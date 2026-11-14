"""Asset Lifecycle domain (Sub-feature 8).

Lifecycle answers one question for a persisted discovered asset:

```text
DiscoveredAsset -- is it still being observed by discovery, or has it
stopped appearing in valid completed discovery runs?
```

It is deliberately separate from the neighbouring concerns:

- discovery *observes* assets (catalog, Sub-feature 4),
- classification decides what they represent (Sub-feature 5),
- deduplication decides whether observations are the same asset
  (Sub-feature 6),
- reconciliation links them to canonical entities (Sub-feature 7),
- **lifecycle decides whether the observed asset is currently observed
  (ACTIVE), aging (STALE), gone (DISAPPEARED) or intentionally ignored
  (IGNORED)** — and it only ever writes the ``status`` column.

## Deterministic policy (run-count based, source scoped)

Evaluation runs for one source when one of its discovery runs reaches a
terminal *successful* state (``COMPLETED`` or ``PARTIAL``) — see
``DiscoveryExecutionService``. A run is a valid observation boundary:

- assets the run observed (``discovered_asset_runs`` link) are
  **ACTIVE** — the observation itself is positive evidence;
- assets of the same source the run did *not* observed age according to
  ``missed`` = number of **COMPLETED** runs of that source that finished
  after the asset's ``last_seen_at`` and were not the run that last
  observed it;
- ``missed >= disappeared_after_missed_runs`` -> ``DISAPPEARED``,
  ``missed >= stale_after_missed_runs`` -> ``STALE``, otherwise
  ``ACTIVE`` (reactivation when a previously absent asset is seen again);
- ``IGNORED`` assets are never transitioned by evaluation.

Only ``COMPLETED`` runs count as absence signals: ``FAILED`` runs carry
no usable scan result, ``PENDING``/``RUNNING`` runs are not finished, and
``PARTIAL`` scans may have skipped files — absence from a partial scan is
not proof of disappearance (its positive observations still re-activate).
This keeps lifecycle conservative: no run can age assets it did not
reliably scan, and evaluation is always scoped to the source of the run.

Policy thresholds come from ``Settings``
(``discovery_lifecycle_stale_after_missed_runs`` /
``discovery_lifecycle_disappeared_after_missed_runs``) so the stale vs
disappeared distinction is an explicit, configurable implementation rule
rather than an inferred one (master spec §11).

## Timestamps, identity and neighbours

Lifecycle never writes ``first_seen_at`` or ``last_seen_at``: those move
only when discovery actually observes the asset. It never creates,
merges or re-identifies assets (deduplication owns identity) and never
touches reconciliation state. Evaluation derives every decision from
persisted data (links, run history, timestamps) — no extra counter
columns, no history tables, no background jobs — so a skipped evaluation
simply self-heals on the next completed run of the source.

The service loads the run's scope with a fixed number of statements and
applies transitions as chunked bulk updates: no per-asset queries.
"""

import logging
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.asset_discovery import repository
from app.modules.asset_discovery.enums import DiscoveredAssetStatus, DiscoveryRunStatus
from app.modules.asset_discovery.errors import (
    DiscoveryRunNotFoundError,
    DiscoverySourceNotFoundError,
    InvalidAssetLifecycleEvaluationError,
    InvalidAssetLifecyclePolicyError,
)

logger = logging.getLogger("auditra.asset_discovery")

# only these terminal run outcomes may drive lifecycle evaluation
EVALUABLE_RUN_STATUSES: frozenset[str] = frozenset(
    {
        DiscoveryRunStatus.COMPLETED.value,
        DiscoveryRunStatus.PARTIAL.value,
    }
)


def is_evaluable_run_status(status: str) -> bool:
    """True when a run's outcome is a valid lifecycle observation boundary."""
    return status in EVALUABLE_RUN_STATUSES


@dataclass(frozen=True)
class LifecyclePolicy:
    """Explicit stale/disappearance thresholds, in missed completed runs."""

    stale_after_missed_runs: int = 1
    disappeared_after_missed_runs: int = 3

    def __post_init__(self) -> None:
        if self.stale_after_missed_runs < 1:
            raise InvalidAssetLifecyclePolicyError(
                "stale threshold must be at least one missed completed run"
            )
        if self.disappeared_after_missed_runs <= self.stale_after_missed_runs:
            # strictly greater keeps both states reachable: STALE must be
            # observable before DISAPPEARED can trigger
            raise InvalidAssetLifecyclePolicyError(
                "disappearance threshold must exceed the stale threshold"
            )

    @classmethod
    def from_settings(cls, source=None) -> "LifecyclePolicy":
        """Build the policy from an application settings object."""
        source = settings if source is None else source
        return cls(
            stale_after_missed_runs=source.discovery_lifecycle_stale_after_missed_runs,
            disappeared_after_missed_runs=source.discovery_lifecycle_disappeared_after_missed_runs,
        )


def decide_lifecycle_status(
    current_status: str,
    missed_completed_runs: int,
    policy: LifecyclePolicy,
) -> str:
    """Pure transition rule: (current status, misses) -> target status.

    Preconditions: ``current_status`` is a stored catalog status (the
    database check constraint guarantees the vocabulary) and
    ``missed_completed_runs`` is non-negative. IGNORED is sticky and
    resolved before any threshold, so no observation or absence can
    overwrite an intentional ignore decision.
    """
    if current_status == DiscoveredAssetStatus.IGNORED.value:
        return DiscoveredAssetStatus.IGNORED.value
    if missed_completed_runs >= policy.disappeared_after_missed_runs:
        return DiscoveredAssetStatus.DISAPPEARED.value
    if missed_completed_runs >= policy.stale_after_missed_runs:
        return DiscoveredAssetStatus.STALE.value
    return DiscoveredAssetStatus.ACTIVE.value


class CompletedRunIndex:
    """Completed runs of one source, ordered by completion time.

    Built once per evaluation so each asset's missed-run count is a
    binary search (``O(log R)``) instead of a rescan of the run history.
    ``missed_since`` counts completed runs that finished strictly after
    ``last_seen_at`` and excludes the run that last observed the asset
    (its own completion follows its observation, but it is positive
    evidence, never a miss).
    """

    __slots__ = ("_positions", "_times")

    def __init__(self, runs: Sequence[tuple[datetime, UUID]]) -> None:
        ordered = sorted(runs, key=lambda item: item[0])
        self._times: tuple[datetime, ...] = tuple(moment for moment, _ in ordered)
        self._positions: dict[UUID, int] = {
            run_id: index for index, (_, run_id) in enumerate(ordered)
        }

    def missed_since(self, last_seen_at: datetime, last_seen_run_id: UUID | None) -> int:
        start = bisect_right(self._times, last_seen_at)  # first run completed after
        missed = len(self._times) - start
        if last_seen_run_id is not None:
            position = self._positions.get(last_seen_run_id)
            if position is not None and position >= start:
                # the observing run completed after the observation: it is
                # the evidence, not a miss
                missed -= 1
        return max(missed, 0)


@dataclass(frozen=True)
class LifecycleSummary:
    """Counts produced by one source-scoped lifecycle evaluation.

    ``scope`` is every asset of the evaluated source; ``observed`` counts
    the ones the evaluated run saw (always promoted/stayed ACTIVE unless
    IGNORED). ``activated``/``stale``/``disappeared`` count transitions
    that were actually written, ``unchanged`` covers assets that already
    held their decided status (IGNORED included, also counted in
    ``ignored``), so ``activated + stale + disappeared + unchanged ==
    scope``.
    """

    source_id: UUID
    run_id: UUID
    scope: int = 0
    observed: int = 0
    activated: int = 0
    stale: int = 0
    disappeared: int = 0
    unchanged: int = 0
    ignored: int = 0

    @property
    def transitioned(self) -> int:
        return self.activated + self.stale + self.disappeared


class AssetLifecycleService:
    """Applies the lifecycle policy to one source after a completed run.

    Stateless and derived: every decision is recomputed from persisted
    data (run history, observation links, timestamps), so evaluation can
    be replayed idempotently and a skipped evaluation self-heals on the
    next completed run of the source. It writes only ``status`` — never
    timestamps, never identity, never reconciliation state — in one
    transaction with a fixed number of statements.
    """

    def __init__(
        self,
        db: Session,
        tenant_id: UUID,
        request_id: str | None = None,
        policy: LifecyclePolicy | None = None,
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.request_id = request_id
        self.policy = policy if policy is not None else LifecyclePolicy.from_settings()

    def evaluate_source_lifecycle(self, source_id: UUID, run_id: UUID) -> LifecycleSummary:
        """Evaluate every asset of ``source_id`` against ``run_id``'s outcome.

        The run must belong to the source and must be terminal-successful
        (COMPLETED or PARTIAL): PENDING, RUNNING and FAILED runs are not
        valid observation boundaries and are rejected before anything is
        read or written.
        """
        run = repository.get_run(self.db, self.tenant_id, run_id)
        if run is None:
            raise DiscoveryRunNotFoundError(f"discovery run '{run_id}' not found")
        if run.source_id != source_id:
            raise InvalidAssetLifecycleEvaluationError(
                f"discovery run '{run_id}' does not belong to source '{source_id}'"
            )
        if repository.get_source(self.db, self.tenant_id, source_id) is None:
            raise DiscoverySourceNotFoundError(f"discovery source '{source_id}' not found")
        if not is_evaluable_run_status(run.status):
            raise InvalidAssetLifecycleEvaluationError(
                f"discovery run '{run_id}' cannot be evaluated from status {run.status}"
            )

        # fixed statement count for any scope size: run history, observed
        # set and asset scope are loaded once, then transitions are
        # applied as chunked bulk updates in a single transaction
        completed = CompletedRunIndex(
            repository.list_source_completed_runs(self.db, self.tenant_id, source_id)
        )
        observed_ids = set(repository.list_run_asset_ids(self.db, self.tenant_id, run_id))
        scope = repository.list_lifecycle_scope(self.db, self.tenant_id, source_id)

        transitions: dict[str, list[UUID]] = {}
        observed = activated = stale = disappeared = unchanged = ignored = 0
        for asset_id, status, last_seen_at, last_seen_run_id in scope:
            is_observed = asset_id in observed_ids
            if is_observed:
                observed += 1
            if status == DiscoveredAssetStatus.IGNORED.value:
                ignored += 1
                unchanged += 1  # sticky: never transitioned, not even observed
                continue

            if is_observed:
                # positive evidence from this very run
                target = DiscoveredAssetStatus.ACTIVE.value
            else:
                missed = completed.missed_since(last_seen_at, last_seen_run_id)
                target = decide_lifecycle_status(status, missed, self.policy)

            if target == status:
                unchanged += 1
                continue
            transitions.setdefault(target, []).append(asset_id)
            if target == DiscoveredAssetStatus.ACTIVE.value:
                activated += 1
            elif target == DiscoveredAssetStatus.STALE.value:
                stale += 1
            else:
                disappeared += 1

        if transitions:
            repository.apply_lifecycle_transitions(self.db, self.tenant_id, transitions)
            repository.commit_lifecycle(self.db)

        summary = LifecycleSummary(
            source_id=source_id,
            run_id=run_id,
            scope=len(scope),
            observed=observed,
            activated=activated,
            stale=stale,
            disappeared=disappeared,
            unchanged=unchanged,
            ignored=ignored,
        )
        logger.info(
            "asset lifecycle evaluation finished",
            extra={
                "tenant_id": str(self.tenant_id),
                "discovery_source_id": str(source_id),
                "discovery_run_id": str(run_id),
                "run_status": run.status,
                "assets_in_scope": summary.scope,
                "assets_observed": summary.observed,
                "assets_activated": summary.activated,
                "assets_stale": summary.stale,
                "assets_disappeared": summary.disappeared,
                "assets_unchanged": summary.unchanged,
                "assets_ignored": summary.ignored,
                "request_id": self.request_id,
            },
        )
        return summary
