import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.asset_discovery import repository
from app.modules.asset_discovery.config_validation import validate_source_config
from app.modules.asset_discovery.enums import DiscoveryRunStatus, DiscoverySourceStatus
from app.modules.asset_discovery.errors import (
    DiscoveryRunError,
    DiscoveryRunNotFoundError,
    DiscoverySourceArchivedError,
    DiscoverySourceNotFoundError,
    DuplicateDiscoverySourceError,
    InvalidDiscoveryRunCountsError,
    InvalidDiscoveryRunTransitionError,
)
from app.modules.asset_discovery.models import DiscoveryRun, DiscoverySource
from app.modules.asset_discovery.run_lifecycle import RUN_INITIAL_STATUS, can_run_transition
from app.modules.asset_discovery.schemas import (
    DiscoveryRunFilter,
    DiscoverySourceCreate,
    DiscoverySourceFilter,
    DiscoverySourceUpdate,
)
from app.modules.audit.enums import AuditEventType
from app.modules.audit.service import AuditService

logger = logging.getLogger("auditra.asset_discovery")


def source_audit_state(source: DiscoverySource) -> dict:
    """Governance-relevant projection of a discovery source for audit state."""
    return {
        "name": source.name,
        "source_type": source.source_type,
        "config": dict(source.config or {}),
        "status": source.status,
        "archived_at": source.archived_at.isoformat() if source.archived_at else None,
    }


class DiscoverySourceService:
    def __init__(
        self,
        db: Session,
        tenant_id: UUID,
        request_id: str | None = None,
        actor: str | None = None,
        source: str = "api",
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.request_id = request_id
        self.actor = actor
        self.audit = AuditService(db, tenant_id, request_id=request_id, actor=actor, source=source)

    def create(self, payload: DiscoverySourceCreate) -> DiscoverySource:
        if repository.find_active_by_name(self.db, self.tenant_id, payload.name) is not None:
            raise DuplicateDiscoverySourceError(f"discovery source '{payload.name}' already exists")
        validate_source_config(payload.source_type, payload.config)

        source = repository.create_source(
            self.db,
            id=uuid4(),
            tenant_id=self.tenant_id,
            name=payload.name,
            source_type=payload.source_type.value,
            config=payload.config,
            status=DiscoverySourceStatus.ACTIVE.value,
        )
        try:
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            raise DuplicateDiscoverySourceError(
                f"discovery source '{payload.name}' already exists"
            ) from None

        self.audit.record_event(
            event_type=AuditEventType.DISCOVERY_SOURCE_CREATED,
            resource_type="discovery_source",
            resource_id=str(source.id),
            after_state=source_audit_state(source),
        )
        repository.commit(self.db)
        self._log("discovery source created", source, ["created"])
        return source

    def get(self, source_id: UUID) -> DiscoverySource:
        return self._entity(source_id)

    def list(self, filters: DiscoverySourceFilter) -> tuple[list[DiscoverySource], int]:
        return repository.list_sources(self.db, self.tenant_id, filters)

    def update(self, source_id: UUID, payload: DiscoverySourceUpdate) -> DiscoverySource:
        source = self._entity(source_id)
        if source.archived_at is not None:
            raise DiscoverySourceArchivedError(f"discovery source '{source_id}' is archived")

        if payload.name is not None and payload.name != source.name:
            existing = repository.find_active_by_name(self.db, self.tenant_id, payload.name)
            if existing is not None and existing.id != source.id:
                raise DuplicateDiscoverySourceError(
                    f"discovery source '{payload.name}' already exists"
                )
        if payload.config is not None:
            validate_source_config(source.source_type, payload.config)

        before_state = source_audit_state(source)
        changed: list[str] = []
        if payload.name is not None and payload.name != source.name:
            source.name = payload.name
            changed.append("name")
        if payload.config is not None and payload.config != source.config:
            source.config = payload.config
            changed.append("config")
        if payload.status is not None and payload.status.value != source.status:
            source.status = payload.status.value
            changed.append("status")

        if changed:
            self.audit.record_event(
                event_type=AuditEventType.DISCOVERY_SOURCE_UPDATED,
                resource_type="discovery_source",
                resource_id=str(source.id),
                before_state=before_state,
                after_state=source_audit_state(source),
                changed_fields=sorted(changed),
            )
        repository.commit(self.db)
        self._log("discovery source updated", source, changed)
        return source

    def delete(self, source_id: UUID) -> None:
        source = self._entity(source_id)
        if source.archived_at is not None:
            return  # idempotent archive, mirrors existing DELETE conventions
        before_state = source_audit_state(source)
        source.archived_at = datetime.now(UTC)
        self.audit.record_event(
            event_type=AuditEventType.DISCOVERY_SOURCE_ARCHIVED,
            resource_type="discovery_source",
            resource_id=str(source.id),
            before_state=before_state,
            after_state=source_audit_state(source),
            changed_fields=["archived_at"],
        )
        repository.commit(self.db)
        self._log("discovery source archived", source, ["archived"])

    def _entity(self, source_id: UUID) -> DiscoverySource:
        source = repository.get_source(self.db, self.tenant_id, source_id)
        if source is None:
            raise DiscoverySourceNotFoundError(f"discovery source '{source_id}' not found")
        return source

    def _log(self, message: str, source: DiscoverySource, changed: Sequence[str]) -> None:
        logger.info(
            message,
            extra={
                "discovery_source_id": str(source.id),
                "tenant_id": str(self.tenant_id),
                "source_type": source.source_type,
                "status": source.status,
                "changed_fields": changed,
                "request_id": self.request_id,
            },
        )


class DiscoveryRunService:
    """Tracks discovery executions against discovery sources.

    Creating a run records the attempt only — it never performs a scan.
    The transition methods (start/complete/fail/partial) are the
    foundation the future Asset Discovery sub-feature drives while
    executing a run.
    """

    def __init__(
        self,
        db: Session,
        tenant_id: UUID,
        request_id: str | None = None,
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.request_id = request_id

    def create(self, source_id: UUID) -> DiscoveryRun:
        if repository.get_source(self.db, self.tenant_id, source_id) is None:
            raise DiscoverySourceNotFoundError(f"discovery source '{source_id}' not found")

        run = repository.create_run(
            self.db,
            id=uuid4(),
            tenant_id=self.tenant_id,
            source_id=source_id,
            status=RUN_INITIAL_STATUS.value,
        )
        repository.commit_run(self.db, source_id)
        self._log("discovery run created", run)
        return run

    def get(self, run_id: UUID) -> DiscoveryRun:
        return self._entity(run_id)

    def list(self, filters: DiscoveryRunFilter) -> tuple[list[DiscoveryRun], int]:
        return repository.list_runs(self.db, self.tenant_id, filters)

    def start(self, run_id: UUID) -> DiscoveryRun:
        run = self._entity(run_id)
        self._transition(run, DiscoveryRunStatus.RUNNING)
        run.started_at = datetime.now(UTC)
        self.db.commit()
        self._log("discovery run started", run)
        return run

    def complete(
        self,
        run_id: UUID,
        *,
        assets_detected: int = 0,
        assets_created: int = 0,
        assets_updated: int = 0,
        assets_unchanged: int = 0,
    ) -> DiscoveryRun:
        run = self._entity(run_id)
        self._validate_counts(assets_detected, assets_created, assets_updated, assets_unchanged)
        self._transition(run, DiscoveryRunStatus.COMPLETED)
        run.completed_at = datetime.now(UTC)
        run.assets_detected = assets_detected
        run.assets_created = assets_created
        run.assets_updated = assets_updated
        run.assets_unchanged = assets_unchanged
        self.db.commit()
        self._log("discovery run completed", run)
        return run

    def fail(self, run_id: UUID, *, error_summary: str, error_count: int = 1) -> DiscoveryRun:
        run = self._entity(run_id)
        summary = self._validate_error_summary(error_summary)
        self._validate_error_count(error_count)
        self._transition(run, DiscoveryRunStatus.FAILED)
        run.completed_at = datetime.now(UTC)
        run.error_summary = summary
        run.error_count = error_count
        self.db.commit()
        self._log("discovery run failed", run)
        return run

    def partial(
        self,
        run_id: UUID,
        *,
        assets_detected: int = 0,
        assets_created: int = 0,
        assets_updated: int = 0,
        assets_unchanged: int = 0,
        error_summary: str,
        error_count: int = 1,
    ) -> DiscoveryRun:
        run = self._entity(run_id)
        summary = self._validate_error_summary(error_summary)
        self._validate_error_count(error_count)
        self._validate_counts(assets_detected, assets_created, assets_updated, assets_unchanged)
        self._transition(run, DiscoveryRunStatus.PARTIAL)
        run.completed_at = datetime.now(UTC)
        run.assets_detected = assets_detected
        run.assets_created = assets_created
        run.assets_updated = assets_updated
        run.assets_unchanged = assets_unchanged
        run.error_summary = summary
        run.error_count = error_count
        self.db.commit()
        self._log("discovery run partially completed", run)
        return run

    def _transition(self, run: DiscoveryRun, target: DiscoveryRunStatus) -> None:
        if not can_run_transition(run.status, target.value):
            raise InvalidDiscoveryRunTransitionError(
                f"discovery run '{run.id}' cannot transition from {run.status} to {target.value}"
            )
        run.status = target.value

    @staticmethod
    def _validate_counts(
        assets_detected: int,
        assets_created: int,
        assets_updated: int,
        assets_unchanged: int,
    ) -> None:
        counts = {
            "assets_detected": assets_detected,
            "assets_created": assets_created,
            "assets_updated": assets_updated,
            "assets_unchanged": assets_unchanged,
        }
        for name, value in counts.items():
            if value < 0:
                raise InvalidDiscoveryRunCountsError(f"{name} must be non-negative")
        if assets_created + assets_updated + assets_unchanged > assets_detected:
            raise InvalidDiscoveryRunCountsError(
                "asset outcome counts cannot exceed assets detected"
            )

    @staticmethod
    def _validate_error_count(error_count: int) -> None:
        if error_count < 0:
            raise InvalidDiscoveryRunCountsError("error_count must be non-negative")

    @staticmethod
    def _validate_error_summary(error_summary: str) -> str:
        summary = (error_summary or "").strip()
        if not summary:
            raise DiscoveryRunError("error summary must not be empty")
        return summary

    def _entity(self, run_id: UUID) -> DiscoveryRun:
        run = repository.get_run(self.db, self.tenant_id, run_id)
        if run is None:
            raise DiscoveryRunNotFoundError(f"discovery run '{run_id}' not found")
        return run

    def _log(self, message: str, run: DiscoveryRun) -> None:
        logger.info(
            message,
            extra={
                "discovery_run_id": str(run.id),
                "discovery_source_id": str(run.source_id),
                "tenant_id": str(self.tenant_id),
                "status": run.status,
                "assets_detected": run.assets_detected,
                "error_count": run.error_count,
                "request_id": self.request_id,
            },
        )
