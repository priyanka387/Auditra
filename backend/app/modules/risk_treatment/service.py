"""Risk Treatment services (RF-04): business rules, tenant isolation, audit.

Follows the RF-01/RF-03 pattern: tenant-scoped entity lookups (unknown
and cross-tenant ids are indistinguishable 404s), the state machine and
ACCEPT guard validated *before* any mutation, governance events staged
with ``AuditService.record_event`` in the same transaction, then a
single commit. Completion and cancellation stamp the row and snapshot
their context into audit metadata; neither ever touches the risk row
(plan §20/§21).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.enums import AuditEventType
from app.modules.audit.service import AuditService
from app.modules.risk import repository as risk_repository
from app.modules.risk.errors import RiskArchivedError, RiskNotFoundError
from app.modules.risk.models import Risk
from app.modules.risk_treatment import repository
from app.modules.risk_treatment.enums import (
    ACTION_TRANSITIONS,
    ACTIVE_TREATMENT_STATUSES,
    TREATMENT_TRANSITIONS,
    RiskTreatmentStatus,
    RiskTreatmentStrategy,
    TreatmentActionStatus,
)
from app.modules.risk_treatment.errors import (
    InvalidRiskTreatmentActionStatusError,
    InvalidRiskTreatmentStatusError,
    RiskTreatmentActionNotFoundError,
    RiskTreatmentActiveExistsError,
    RiskTreatmentError,
    RiskTreatmentNotFoundError,
)
from app.modules.risk_treatment.models import RiskTreatment, RiskTreatmentAction
from app.modules.risk_treatment.schemas import (
    TreatmentActionCreate,
    TreatmentActionFilter,
    TreatmentActionUpdate,
    TreatmentCancelRequest,
    TreatmentCreate,
    TreatmentFilter,
    TreatmentUpdate,
)

logger = logging.getLogger("auditra.risk_treatment")

# Ruling 6: PATCH may move status only to planned/in_progress — the
# terminal states are reachable exclusively through the dedicated
# complete/cancel endpoints
PATCH_STATUS_TARGETS: frozenset[str] = frozenset(
    {RiskTreatmentStatus.PLANNED, RiskTreatmentStatus.IN_PROGRESS}
)

# Ruling 6 for actions: PATCH interconverts the two open states only;
# completed/cancelled come from the dedicated endpoints
PATCH_ACTION_STATUS_TARGETS: frozenset[str] = frozenset(
    {TreatmentActionStatus.TODO, TreatmentActionStatus.IN_PROGRESS}
)


def treatment_audit_state(treatment: RiskTreatment) -> dict:
    """Governance-relevant projection of a treatment for audit state."""
    return {
        "risk_id": str(treatment.risk_id),
        "strategy": treatment.strategy,
        "status": treatment.status,
        "title": treatment.title,
        "owner": treatment.owner,
        "planned_start_date": (
            treatment.planned_start_date.isoformat() if treatment.planned_start_date else None
        ),
        "target_completion_date": (
            treatment.target_completion_date.isoformat()
            if treatment.target_completion_date
            else None
        ),
        "completed_at": treatment.completed_at.isoformat() if treatment.completed_at else None,
        "cancelled_at": treatment.cancelled_at.isoformat() if treatment.cancelled_at else None,
        "cancellation_reason": treatment.cancellation_reason,
    }


def action_audit_state(action: RiskTreatmentAction) -> dict:
    """Governance-relevant projection of a treatment action for audit state."""
    return {
        "treatment_id": str(action.treatment_id),
        "title": action.title,
        "status": action.status,
        "owner": action.owner,
        "priority": action.priority,
        "planned_start_date": (
            action.planned_start_date.isoformat() if action.planned_start_date else None
        ),
        "due_date": action.due_date.isoformat() if action.due_date else None,
        "completed_at": action.completed_at.isoformat() if action.completed_at else None,
        "cancelled_at": action.cancelled_at.isoformat() if action.cancelled_at else None,
        "cancellation_reason": action.cancellation_reason,
    }


class RiskTreatmentService:
    """The RF-04 treatment decision workflow on a risk.

    Authorization is tenant isolation plus validation (Ruling 4): there
    are no roles yet, so every lookup is tenant-scoped and the ACCEPT
    rationale guard runs regardless of who is calling.
    """

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

    # --- create ---------------------------------------------------------------

    def create(self, risk_id: UUID, payload: TreatmentCreate) -> RiskTreatment:
        risk = risk_repository.get_risk(self.db, self.tenant_id, risk_id)
        if risk is None:
            raise RiskNotFoundError(f"risk '{risk_id}' not found")
        if risk.archived_at is not None:
            raise RiskArchivedError(f"risk '{risk_id}' is archived")
        if repository.get_active_treatment(self.db, self.tenant_id, risk_id) is not None:
            raise RiskTreatmentActiveExistsError(
                f"risk '{risk_id}' already has an active treatment"
            )
        self._require_acceptance_rationale(payload.strategy, payload.rationale)

        treatment = repository.create_treatment(
            self.db,
            id=uuid4(),
            tenant_id=self.tenant_id,
            risk_id=risk_id,
            strategy=payload.strategy.value,
            status=RiskTreatmentStatus.NOT_STARTED.value,
            title=payload.title,
            description=payload.description,
            rationale=payload.rationale,
            owner=payload.owner,
            planned_start_date=payload.planned_start_date,
            target_completion_date=payload.target_completion_date,
            created_by=self.actor,
        )
        metadata = {"risk_id": str(risk_id)}
        self.audit.record_event(
            event_type=AuditEventType.RISK_TREATMENT_CREATED,
            resource_type="treatment",
            resource_id=str(treatment.id),
            after_state=treatment_audit_state(treatment),
            metadata=metadata,
        )
        if payload.strategy == RiskTreatmentStrategy.ACCEPT:
            self.audit.record_event(
                event_type=AuditEventType.RISK_TREATMENT_ACCEPTANCE_RECORDED,
                resource_type="treatment",
                resource_id=str(treatment.id),
                after_state=treatment_audit_state(treatment),
                metadata=metadata,
            )
        repository.commit_treatment(self.db)
        self._log("risk treatment created", treatment, ["created"])
        return treatment

    # --- read -----------------------------------------------------------------

    def get(self, treatment_id: UUID) -> RiskTreatment:
        return self._entity(treatment_id)

    def list(self, filters: TreatmentFilter) -> tuple[list[RiskTreatment], int]:
        return repository.list_treatments(self.db, self.tenant_id, filters)

    def list_by_risk(
        self, risk_id: UUID, filters: TreatmentFilter
    ) -> tuple[list[RiskTreatment], int]:
        """Nested list under a risk: the path risk must exist first (404),
        then the page is scoped to that risk regardless of query params."""
        if risk_repository.get_risk(self.db, self.tenant_id, risk_id) is None:
            raise RiskNotFoundError(f"risk '{risk_id}' not found")
        scoped = filters.model_copy(update={"risk_id": risk_id})
        return repository.list_treatments(self.db, self.tenant_id, scoped)

    def resolve_active(self, risks: Sequence[Risk]) -> dict[UUID, RiskTreatment]:
        """Active treatments for a page of risks in one query (plan §42).

        Routes attach these as the register's ``treatment`` summary; a
        missing entry means the risk is untreated. Ruling 5 guarantees at
        most one active row per risk, so the map never collapses rows.
        """
        risk_ids = {risk.id for risk in risks}
        if not risk_ids:
            return {}
        rows = self.db.scalars(
            select(RiskTreatment).where(
                RiskTreatment.tenant_id == self.tenant_id,
                RiskTreatment.risk_id.in_(risk_ids),
                RiskTreatment.status.in_(tuple(ACTIVE_TREATMENT_STATUSES)),
            )
        )
        return {row.risk_id: row for row in rows}

    # --- update ---------------------------------------------------------------

    def update(self, treatment_id: UUID, payload: TreatmentUpdate) -> RiskTreatment:
        treatment = self._entity(treatment_id)
        if treatment.status in (
            RiskTreatmentStatus.COMPLETED.value,
            RiskTreatmentStatus.CANCELLED.value,
        ):
            raise InvalidRiskTreatmentStatusError(
                f"treatment '{treatment_id}' is {treatment.status} and cannot be modified"
            )
        if payload.status is not None:
            allowed = TREATMENT_TRANSITIONS[treatment.status] & PATCH_STATUS_TARGETS
            if payload.status not in allowed:
                raise InvalidRiskTreatmentStatusError(
                    f"treatment '{treatment_id}' cannot move to '{payload.status}'"
                )
        if (
            payload.strategy is not None
            and payload.strategy != treatment.strategy
            and payload.strategy == RiskTreatmentStrategy.ACCEPT
        ):
            effective = payload.rationale if payload.rationale is not None else treatment.rationale
            self._require_acceptance_rationale(payload.strategy, effective)

        before_state = treatment_audit_state(treatment)
        changed: list[str] = []
        if payload.title is not None and payload.title != treatment.title:
            treatment.title = payload.title
            changed.append("title")
        if payload.description is not None and payload.description != treatment.description:
            treatment.description = payload.description
            changed.append("description")
        if payload.rationale is not None and payload.rationale != treatment.rationale:
            treatment.rationale = payload.rationale
            changed.append("rationale")
        if payload.owner is not None and payload.owner != treatment.owner:
            treatment.owner = payload.owner
            changed.append("owner")
        if payload.strategy is not None and payload.strategy.value != treatment.strategy:
            treatment.strategy = payload.strategy.value
            changed.append("strategy")
        if (
            payload.planned_start_date is not None
            and payload.planned_start_date != treatment.planned_start_date
        ):
            treatment.planned_start_date = payload.planned_start_date
            changed.append("planned_start_date")
        if (
            payload.target_completion_date is not None
            and payload.target_completion_date != treatment.target_completion_date
        ):
            treatment.target_completion_date = payload.target_completion_date
            changed.append("target_completion_date")
        if payload.status is not None and payload.status.value != treatment.status:
            treatment.status = payload.status.value
            changed.append("status")

        if changed:
            after_state = treatment_audit_state(treatment)
            metadata = {"risk_id": str(treatment.risk_id)}
            self.audit.record_event(
                event_type=AuditEventType.RISK_TREATMENT_UPDATED,
                resource_type="treatment",
                resource_id=str(treatment.id),
                before_state=before_state,
                after_state=after_state,
                changed_fields=sorted(changed),
                metadata=metadata,
            )
            # §37 wants both granularities: the aggregate PATCH event plus
            # a dedicated before/after event for the governance columns
            if "strategy" in changed:
                self.audit.record_event(
                    event_type=AuditEventType.RISK_TREATMENT_STRATEGY_CHANGED,
                    resource_type="treatment",
                    resource_id=str(treatment.id),
                    before_state=before_state,
                    after_state=after_state,
                    metadata=metadata,
                )
            if "owner" in changed:
                self.audit.record_event(
                    event_type=AuditEventType.RISK_TREATMENT_OWNER_CHANGED,
                    resource_type="treatment",
                    resource_id=str(treatment.id),
                    before_state=before_state,
                    after_state=after_state,
                    metadata=metadata,
                )
            if treatment.strategy == RiskTreatmentStrategy.ACCEPT.value and "strategy" in changed:
                self.audit.record_event(
                    event_type=AuditEventType.RISK_TREATMENT_ACCEPTANCE_RECORDED,
                    resource_type="treatment",
                    resource_id=str(treatment.id),
                    before_state=before_state,
                    after_state=after_state,
                    metadata=metadata,
                )
        repository.commit_treatment(self.db)
        self._log("risk treatment updated", treatment, changed)
        return treatment

    def complete(self, treatment_id: UUID) -> RiskTreatment:
        treatment = self._entity(treatment_id)
        # in_progress is the only state whose transition map contains
        # completed — terminals cannot be completed (or reopened, §55)
        if RiskTreatmentStatus.COMPLETED not in TREATMENT_TRANSITIONS[treatment.status]:
            raise InvalidRiskTreatmentStatusError(
                f"treatment '{treatment_id}' cannot be completed from '{treatment.status}'"
            )

        before_state = treatment_audit_state(treatment)
        treatment.status = RiskTreatmentStatus.COMPLETED.value
        treatment.completed_at = datetime.now(UTC)
        # Ruling 16: completion never gates on action state — it only
        # snapshots the counts for the audit trail (one query)
        total_actions, completed_actions = repository.count_actions_for_completion(
            self.db, treatment.id
        )
        self.audit.record_event(
            event_type=AuditEventType.RISK_TREATMENT_COMPLETED,
            resource_type="treatment",
            resource_id=str(treatment.id),
            before_state=before_state,
            after_state=treatment_audit_state(treatment),
            changed_fields=["completed_at", "status"],
            metadata={
                "risk_id": str(treatment.risk_id),
                "total_actions": total_actions,
                "completed_actions": completed_actions,
            },
        )
        repository.commit_treatment(self.db)
        self._log("risk treatment completed", treatment, ["status", "completed_at"])
        return treatment

    def cancel(self, treatment_id: UUID, payload: TreatmentCancelRequest) -> RiskTreatment:
        treatment = self._entity(treatment_id)
        # reachable from every non-terminal state (Ruling 6 fills the
        # not_started gap); terminals reject with 409
        if RiskTreatmentStatus.CANCELLED not in TREATMENT_TRANSITIONS[treatment.status]:
            raise InvalidRiskTreatmentStatusError(
                f"treatment '{treatment_id}' cannot be cancelled from '{treatment.status}'"
            )

        before_state = treatment_audit_state(treatment)
        treatment.status = RiskTreatmentStatus.CANCELLED.value
        treatment.cancelled_at = datetime.now(UTC)
        treatment.cancellation_reason = payload.reason
        self.audit.record_event(
            event_type=AuditEventType.RISK_TREATMENT_CANCELLED,
            resource_type="treatment",
            resource_id=str(treatment.id),
            before_state=before_state,
            after_state=treatment_audit_state(treatment),
            changed_fields=["cancelled_at", "cancellation_reason", "status"],
            metadata={"risk_id": str(treatment.risk_id), "reason": payload.reason},
        )
        repository.commit_treatment(self.db)
        self._log("risk treatment cancelled", treatment, ["status", "cancelled_at"])
        return treatment

    # --- internals ------------------------------------------------------------

    @staticmethod
    def _require_acceptance_rationale(
        strategy: RiskTreatmentStrategy | str, rationale: str | None
    ) -> None:
        """Ruling 7: ACCEPT owns the remaining exposure explicitly —
        create/PATCH-to-accept without a non-blank rationale is 400."""
        if strategy == RiskTreatmentStrategy.ACCEPT and not (rationale and rationale.strip()):
            raise RiskTreatmentError("ACCEPT strategy requires a rationale")

    def _entity(self, treatment_id: UUID) -> RiskTreatment:
        treatment = repository.get_treatment(self.db, self.tenant_id, treatment_id)
        if treatment is None:
            raise RiskTreatmentNotFoundError(f"treatment '{treatment_id}' not found")
        return treatment

    def _log(self, message: str, treatment: RiskTreatment, changed: Sequence[str]) -> None:
        logger.info(
            message,
            extra={
                "risk_treatment_id": str(treatment.id),
                "tenant_id": str(self.tenant_id),
                "risk_id": str(treatment.risk_id),
                "strategy": treatment.strategy,
                "status": treatment.status,
                "changed_fields": changed,
                "request_id": self.request_id,
            },
        )


class RiskTreatmentActionService:
    """The work items executing a treatment decision (plan §12/§13).

    Same authorization model as ``RiskTreatmentService`` (Ruling 4):
    tenant-scoped lookups everywhere, validation before mutation, audit
    events staged in the same transaction, single commit per operation.
    Progress is never written here — it stays derived from these rows
    (plan §18).
    """

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

    # --- create ---------------------------------------------------------------

    def create(self, treatment_id: UUID, payload: TreatmentActionCreate) -> RiskTreatmentAction:
        treatment = self._treatment(treatment_id)
        # a closed decision takes no new work (plan §31): terminals are
        # immutable, so nothing may be appended to them either
        if treatment.status in (
            RiskTreatmentStatus.COMPLETED.value,
            RiskTreatmentStatus.CANCELLED.value,
        ):
            raise InvalidRiskTreatmentStatusError(
                f"treatment '{treatment_id}' is {treatment.status} and cannot take new actions"
            )

        action = repository.create_action(
            self.db,
            id=uuid4(),
            tenant_id=self.tenant_id,
            treatment_id=treatment_id,
            title=payload.title,
            description=payload.description,
            status=TreatmentActionStatus.TODO.value,
            owner=payload.owner,
            priority=payload.priority.value,
            planned_start_date=payload.planned_start_date,
            due_date=payload.due_date,
            created_by=self.actor,
        )
        self.audit.record_event(
            event_type=AuditEventType.RISK_TREATMENT_ACTION_CREATED,
            resource_type="treatment_action",
            resource_id=str(action.id),
            after_state=action_audit_state(action),
            metadata=self._metadata(action),
        )
        repository.commit_action(self.db)
        self._log("treatment action created", action, ["created"])
        return action

    # --- read -----------------------------------------------------------------

    def get(self, action_id: UUID) -> RiskTreatmentAction:
        return self._entity(action_id)

    def list(
        self, treatment_id: UUID, filters: TreatmentActionFilter
    ) -> tuple[list[RiskTreatmentAction], int]:
        """Nested list under a treatment: the path treatment must exist
        first (404), then the page is scoped to it in this tenant."""
        self._treatment(treatment_id)
        return repository.list_actions(self.db, self.tenant_id, treatment_id, filters)

    # --- update ---------------------------------------------------------------

    def update(self, action_id: UUID, payload: TreatmentActionUpdate) -> RiskTreatmentAction:
        action = self._entity(action_id)
        if action.status in (
            TreatmentActionStatus.COMPLETED.value,
            TreatmentActionStatus.CANCELLED.value,
        ):
            raise InvalidRiskTreatmentActionStatusError(
                f"action '{action_id}' is {action.status} and cannot be modified"
            )
        if payload.status is not None:
            # Ruling 6: PATCH only interconverts the open states —
            # completed/cancelled need the dedicated endpoints
            allowed = ACTION_TRANSITIONS[action.status] & PATCH_ACTION_STATUS_TARGETS
            if payload.status not in allowed:
                raise InvalidRiskTreatmentActionStatusError(
                    f"action '{action_id}' cannot move to '{payload.status}'"
                )

        before_state = action_audit_state(action)
        changed: list[str] = []
        if payload.title is not None and payload.title != action.title:
            action.title = payload.title
            changed.append("title")
        if payload.description is not None and payload.description != action.description:
            action.description = payload.description
            changed.append("description")
        if payload.owner is not None and payload.owner != action.owner:
            action.owner = payload.owner
            changed.append("owner")
        if payload.priority is not None and payload.priority.value != action.priority:
            action.priority = payload.priority.value
            changed.append("priority")
        if payload.status is not None and payload.status.value != action.status:
            action.status = payload.status.value
            changed.append("status")
        if (
            payload.planned_start_date is not None
            and payload.planned_start_date != action.planned_start_date
        ):
            action.planned_start_date = payload.planned_start_date
            changed.append("planned_start_date")
        if payload.due_date is not None and payload.due_date != action.due_date:
            action.due_date = payload.due_date
            changed.append("due_date")

        if changed:
            self.audit.record_event(
                event_type=AuditEventType.RISK_TREATMENT_ACTION_UPDATED,
                resource_type="treatment_action",
                resource_id=str(action.id),
                before_state=before_state,
                after_state=action_audit_state(action),
                changed_fields=sorted(changed),
                metadata=self._metadata(action),
            )
        repository.commit_action(self.db)
        self._log("treatment action updated", action, changed)
        return action

    # --- terminal transitions -------------------------------------------------

    def complete(self, action_id: UUID) -> RiskTreatmentAction:
        action = self._entity(action_id)
        if TreatmentActionStatus.COMPLETED not in ACTION_TRANSITIONS[action.status]:
            raise InvalidRiskTreatmentActionStatusError(
                f"action '{action_id}' cannot be completed from '{action.status}'"
            )

        before_state = action_audit_state(action)
        action.status = TreatmentActionStatus.COMPLETED.value
        action.completed_at = datetime.now(UTC)
        self.audit.record_event(
            event_type=AuditEventType.RISK_TREATMENT_ACTION_COMPLETED,
            resource_type="treatment_action",
            resource_id=str(action.id),
            before_state=before_state,
            after_state=action_audit_state(action),
            changed_fields=["completed_at", "status"],
            metadata=self._metadata(action),
        )
        repository.commit_action(self.db)
        self._log("treatment action completed", action, ["status", "completed_at"])
        return action

    def cancel(self, action_id: UUID, payload: TreatmentCancelRequest) -> RiskTreatmentAction:
        action = self._entity(action_id)
        if TreatmentActionStatus.CANCELLED not in ACTION_TRANSITIONS[action.status]:
            raise InvalidRiskTreatmentActionStatusError(
                f"action '{action_id}' cannot be cancelled from '{action.status}'"
            )

        before_state = action_audit_state(action)
        action.status = TreatmentActionStatus.CANCELLED.value
        action.cancelled_at = datetime.now(UTC)
        action.cancellation_reason = payload.reason
        self.audit.record_event(
            event_type=AuditEventType.RISK_TREATMENT_ACTION_CANCELLED,
            resource_type="treatment_action",
            resource_id=str(action.id),
            before_state=before_state,
            after_state=action_audit_state(action),
            changed_fields=["cancelled_at", "cancellation_reason", "status"],
            metadata={**self._metadata(action), "reason": payload.reason},
        )
        repository.commit_action(self.db)
        self._log("treatment action cancelled", action, ["status", "cancelled_at"])
        return action

    # --- internals ------------------------------------------------------------

    def _entity(self, action_id: UUID) -> RiskTreatmentAction:
        action = repository.get_action(self.db, self.tenant_id, action_id)
        if action is None:
            raise RiskTreatmentActionNotFoundError(f"treatment action '{action_id}' not found")
        return action

    def _treatment(self, treatment_id: UUID) -> RiskTreatment:
        treatment = repository.get_treatment(self.db, self.tenant_id, treatment_id)
        if treatment is None:
            raise RiskTreatmentNotFoundError(f"treatment '{treatment_id}' not found")
        return treatment

    def _metadata(self, action: RiskTreatmentAction) -> dict:
        """Ruling 11: action events carry ``risk_id``/``treatment_id``.

        The parent treatment is resolved in this tenant (the action row
        is tenant-scoped and the FK keeps the treatment alive), which
        also yields the risk the action ultimately serves.
        """
        treatment = repository.get_treatment(self.db, self.tenant_id, action.treatment_id)
        risk_id = treatment.risk_id if treatment is not None else None
        return {
            "risk_id": str(risk_id) if risk_id is not None else None,
            "treatment_id": str(action.treatment_id),
        }

    def _log(self, message: str, action: RiskTreatmentAction, changed: Sequence[str]) -> None:
        logger.info(
            message,
            extra={
                "risk_treatment_action_id": str(action.id),
                "tenant_id": str(self.tenant_id),
                "treatment_id": str(action.treatment_id),
                "status": action.status,
                "priority": action.priority,
                "changed_fields": changed,
                "request_id": self.request_id,
            },
        )
