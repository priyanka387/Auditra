"""Discovery execution: drives one discovery run to a terminal state.

Orchestrates the flow defined by the master specification:

```text
Discovery Source -> Run (PENDING) -> RUNNING -> connector scan
    -> Discovered Asset Catalog -> COMPLETED | PARTIAL | FAILED
```

Run state transitions are delegated to the existing
``DiscoveryRunService`` (Sub-feature 2) — never duplicated here — and
observations are persisted by ``DiscoveredAssetService`` (Sub-feature
4). Fatal errors (invalid configuration, unreachable location,
unsupported connector, catalog validation) fail the run and surface as
domain errors; recoverable per-file issues complete the run as PARTIAL.
"""

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.asset_discovery import repository
from app.modules.asset_discovery.config_validation import validate_source_config
from app.modules.asset_discovery.connectors import get_connector
from app.modules.asset_discovery.connectors.base import (
    ConnectorIssue,
    DiscoveryObservation,
)
from app.modules.asset_discovery.enums import DiscoverySourceStatus
from app.modules.asset_discovery.errors import (
    DiscoveryExecutionError,
    DiscoverySourceArchivedError,
    DiscoverySourceDisabledError,
    DiscoverySourceNotFoundError,
)
from app.modules.asset_discovery.models import DiscoveryRun, DiscoverySource
from app.modules.asset_discovery.service import (
    CatalogSummary,
    DiscoveredAssetService,
    DiscoveryRunService,
)
from app.modules.model_inventory.domain.errors import DomainError

logger = logging.getLogger("auditra.asset_discovery")


@dataclass(frozen=True)
class DiscoveryExecutionResult:
    """Structured outcome of executing one discovery run.

    Observations are the raw connector results and issues are
    recoverable per-file problems; file contents are never included.
    ``catalog`` reports how those observations mapped to persisted
    Discovered Assets.
    """

    source: DiscoverySource
    run: DiscoveryRun
    connector: str
    files_scanned: int
    observations: tuple[DiscoveryObservation, ...]
    issues: tuple[ConnectorIssue, ...]
    catalog: CatalogSummary


def _issue_summary(issues: Sequence[ConnectorIssue]) -> str:
    """Persistable, path-free summary: counts grouped by reason.

    Relative file paths stay in the API response only; the run's stored
    error_summary never carries filesystem detail.
    """
    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue.reason] = counts.get(issue.reason, 0) + 1
    breakdown = ", ".join(f"{reason}={count}" for reason, count in sorted(counts.items()))
    return f"{len(issues)} recoverable error(s): {breakdown}"


class DiscoveryExecutionService:
    """Executes discovery runs synchronously against their source."""

    def __init__(
        self,
        db: Session,
        tenant_id: UUID,
        request_id: str | None = None,
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.request_id = request_id
        self.runs = DiscoveryRunService(db, tenant_id, request_id=request_id)
        self.catalog = DiscoveredAssetService(db, tenant_id, request_id=request_id)

    def execute(self, run_id: UUID) -> DiscoveryExecutionResult:
        run = self.runs.get(run_id)  # tenant-scoped; 404 when missing
        source = repository.get_source(self.db, self.tenant_id, run.source_id)
        if source is None:
            raise DiscoverySourceNotFoundError(f"discovery source '{run.source_id}' not found")

        # preconditions are checked before the run is consumed: a rejected
        # execution leaves the run PENDING so it can be retried
        self._validate_executable(source)
        self.runs.start(run_id)  # PENDING -> RUNNING (409 when not pending)

        started = time.monotonic()
        try:
            validate_source_config(source.source_type, source.config)
            connector = get_connector(source.source_type)
            result = connector.discover(source, run.id)
        except DomainError as exc:
            self.runs.fail(run_id, error_summary=f"discovery aborted: {exc.code}")
            self._log_finished(
                source,
                run,
                connector=None,
                files_scanned=0,
                issue_count=0,
                started=started,
                error_code=exc.code,
            )
            raise
        except Exception:
            logger.exception(
                "discovery execution raised unexpectedly",
                extra=self._log_context(source, run),
            )
            self.runs.fail(run_id, error_summary="unexpected error during discovery")
            raise DiscoveryExecutionError("discovery failed unexpectedly") from None

        # persist observations into the Discovered Asset Catalog before
        # the run reports its outcome; a catalog failure fails the run
        # (the scan can be re-run — identity makes it idempotent)
        try:
            catalog = self.catalog.record_observations(result.observations)
        except DomainError as exc:
            self.runs.fail(run_id, error_summary=f"catalog persistence failed: {exc.code}")
            self._log_finished(
                source,
                run,
                connector=result.connector,
                files_scanned=result.files_scanned,
                issue_count=len(result.issues),
                started=started,
                observation_count=len(result.observations),
                error_code=exc.code,
            )
            raise
        except Exception:
            logger.exception(
                "catalog persistence raised unexpectedly",
                extra=self._log_context(source, run),
            )
            self.runs.fail(run_id, error_summary="unexpected error persisting discovered assets")
            raise DiscoveryExecutionError("discovery failed unexpectedly") from None

        if result.issues:
            self.runs.partial(
                run_id,
                assets_detected=len(result.observations),
                assets_created=catalog.created,
                assets_updated=catalog.updated,
                assets_unchanged=catalog.unchanged,
                error_summary=_issue_summary(result.issues),
                error_count=len(result.issues),
            )
        else:
            self.runs.complete(
                run_id,
                assets_detected=len(result.observations),
                assets_created=catalog.created,
                assets_updated=catalog.updated,
                assets_unchanged=catalog.unchanged,
            )

        self._log_finished(
            source,
            run,
            connector=result.connector,
            files_scanned=result.files_scanned,
            issue_count=len(result.issues),
            started=started,
            observation_count=len(result.observations),
        )
        return DiscoveryExecutionResult(
            source=source,
            run=run,
            connector=result.connector,
            files_scanned=result.files_scanned,
            observations=result.observations,
            issues=result.issues,
            catalog=catalog,
        )

    @staticmethod
    def _validate_executable(source: DiscoverySource) -> None:
        if source.archived_at is not None:
            raise DiscoverySourceArchivedError(f"discovery source '{source.id}' is archived")
        if source.status == DiscoverySourceStatus.DISABLED.value:
            raise DiscoverySourceDisabledError(f"discovery source '{source.id}' is disabled")

    def _log_context(self, source: DiscoverySource, run: DiscoveryRun) -> dict:
        return {
            "discovery_source_id": str(source.id),
            "discovery_run_id": str(run.id),
            "tenant_id": str(self.tenant_id),
            "request_id": self.request_id,
        }

    def _log_finished(
        self,
        source: DiscoverySource,
        run: DiscoveryRun,
        *,
        connector: str | None,
        files_scanned: int,
        issue_count: int,
        started: float,
        observation_count: int = 0,
        error_code: str | None = None,
    ) -> None:
        logger.info(
            "discovery execution finished",
            extra={
                **self._log_context(source, run),
                "connector": connector,
                "status": run.status,
                "files_scanned": files_scanned,
                "observation_count": observation_count,
                "error_count": issue_count,
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
                "error_code": error_code,
            },
        )
