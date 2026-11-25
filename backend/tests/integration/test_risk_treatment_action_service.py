"""Integration tests: RF-04 RiskTreatmentActionService — work items, state machine, audit.

Actions execute a treatment decision (plan §12): owners are required,
``todo``⇄``in_progress`` interconvert via PATCH while completed/cancelled
are reachable only through the dedicated endpoints (Ruling 6), terminal
states are immutable, progress stays derived from the rows (plan §18),
and every mutation is tenant-scoped with a single-commit audit event
(resource_type ``treatment_action``, Ruling 11).
"""

from datetime import date
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from pydantic import ValidationError

from app.core.config import settings
from app.modules.audit.models import AuditEvent
from app.modules.risk.models import RiskCategory
from app.modules.risk.schemas import RiskCreate
from app.modules.risk.seed import seed_risk_categories
from app.modules.risk.service import RiskService
from app.modules.risk_treatment import metrics, repository
from app.modules.risk_treatment.enums import RiskTreatmentStrategy, TreatmentPriority
from app.modules.risk_treatment.errors import (
    InvalidRiskTreatmentActionStatusError,
    InvalidRiskTreatmentQueryError,
    InvalidRiskTreatmentStatusError,
    RiskTreatmentActionNotFoundError,
    RiskTreatmentNotFoundError,
)
from app.modules.risk_treatment.models import RiskTreatment
from app.modules.risk_treatment.schemas import (
    TreatmentActionCreate,
    TreatmentActionFilter,
    TreatmentActionUpdate,
    TreatmentCancelRequest,
    TreatmentCreate,
    TreatmentUpdate,
)
from app.modules.risk_treatment.service import (
    RiskTreatmentActionService,
    RiskTreatmentService,
)

TENANT = settings.default_tenant_id
OTHER_TENANT = uuid4()


# --- fixtures & helpers ------------------------------------------------------


def _action_service(db, tenant_id: UUID | None = None) -> RiskTreatmentActionService:
    return RiskTreatmentActionService(
        db,
        tenant_id=tenant_id or TENANT,
        request_id="req-action-01",
        actor=None,
    )


def _treatment_service(db, tenant_id: UUID | None = None) -> RiskTreatmentService:
    return RiskTreatmentService(
        db,
        tenant_id=tenant_id or TENANT,
        request_id="req-treatment-01",
        actor=None,
    )


def _make_treatment(db, tenant_id: UUID | None = None) -> RiskTreatment:
    """A risk (RF-01 path) plus a fresh mitigate treatment (RF-04 path)."""
    tenant = tenant_id or TENANT
    seed_risk_categories(db, tenant)
    db.commit()
    category = db.scalar(
        sa.select(RiskCategory).where(
            RiskCategory.tenant_id == tenant,
            RiskCategory.slug == "security",
        )
    )
    risk = RiskService(db, tenant_id=tenant, actor=None).create(
        RiskCreate(title="Training data unencrypted", category_id=category.id)
    )
    treatment = _treatment_service(db, tenant).create(
        risk.id,
        TreatmentCreate(
            title="Encrypt training data at rest",
            strategy=RiskTreatmentStrategy.MITIGATE,
            owner="Security Team",
        ),
    )
    return treatment


def _action_payload(**overrides) -> TreatmentActionCreate:
    payload = {
        "title": "Restrict production access",
        "owner": "Security Team",
        "planned_start_date": date(2026, 11, 1),
        "due_date": date(2026, 12, 15),
    }
    payload.update(overrides)
    return TreatmentActionCreate(**payload)


def _events(db, action_id: UUID) -> list[AuditEvent]:
    stmt = (
        sa.select(AuditEvent)
        .where(
            AuditEvent.resource_type == "treatment_action",
            AuditEvent.resource_id == str(action_id),
        )
        .order_by(AuditEvent.sequence_no)
    )
    return list(db.scalars(stmt))


def _to_completed_treatment(db) -> RiskTreatment:
    treatment = _make_treatment(db)
    service = _treatment_service(db)
    service.update(treatment.id, TreatmentUpdate(status="planned"))
    service.update(treatment.id, TreatmentUpdate(status="in_progress"))
    service.complete(treatment.id)
    return treatment


# --- create ------------------------------------------------------------------


def test_create_action_persists_with_defaults(db):
    treatment = _make_treatment(db)

    action = _action_service(db).create(treatment.id, _action_payload())

    assert action.status == "todo"
    assert action.priority == TreatmentPriority.MEDIUM
    assert action.owner == "Security Team"
    assert action.tenant_id == TENANT
    assert action.treatment_id == treatment.id
    assert action.completed_at is None
    assert action.cancelled_at is None

    events = _events(db, action.id)
    assert [event.event_type for event in events] == ["risk.treatment_action_created"]
    assert events[0].resource_type == "treatment_action"
    assert events[0].metadata_["treatment_id"] == str(treatment.id)
    assert events[0].metadata_["risk_id"] == str(treatment.risk_id)


def test_create_action_requires_owner(db):
    with pytest.raises(ValidationError):
        TreatmentActionCreate(title="Review access logs", owner="   ")

    treatment = _make_treatment(db)
    action = _action_service(db).create(
        treatment.id, TreatmentActionCreate(title="Review access logs", owner="AppSec")
    )
    assert action.owner == "AppSec"


def test_create_action_unknown_treatment_404(db):
    with pytest.raises(RiskTreatmentNotFoundError):
        _action_service(db).create(uuid4(), _action_payload())


def test_create_action_cross_tenant_404(db):
    treatment = _make_treatment(db)

    with pytest.raises(RiskTreatmentNotFoundError):
        _action_service(db, OTHER_TENANT).create(treatment.id, _action_payload())


def test_create_action_on_terminal_treatment_409(db):
    completed = _to_completed_treatment(db)
    with pytest.raises(InvalidRiskTreatmentStatusError):
        _action_service(db).create(completed.id, _action_payload())

    cancelled_treatment = _make_treatment(db)
    _treatment_service(db).cancel(
        cancelled_treatment.id, TreatmentCancelRequest(reason="Superseded by avoidance")
    )
    with pytest.raises(InvalidRiskTreatmentStatusError):
        _action_service(db).create(cancelled_treatment.id, _action_payload())


# --- list --------------------------------------------------------------------


def test_list_actions_tenant_scoped_and_sorted(db):
    treatment = _make_treatment(db)
    service = _action_service(db)
    first = service.create(treatment.id, _action_payload(title="A", due_date=date(2026, 11, 30)))
    second = service.create(treatment.id, _action_payload(title="B", due_date=date(2026, 11, 15)))

    items, total = service.list(
        treatment.id, TreatmentActionFilter(sort_by="due_date", sort_order="asc")
    )
    assert total == 2
    assert [item.id for item in items] == [second.id, first.id]

    with pytest.raises(InvalidRiskTreatmentQueryError):
        service.list(treatment.id, TreatmentActionFilter(sort_by="title"))

    with pytest.raises(RiskTreatmentNotFoundError):
        _action_service(db, OTHER_TENANT).list(treatment.id, TreatmentActionFilter())


# --- update ------------------------------------------------------------------


def test_update_action_owner_priority_due_date(db):
    treatment = _make_treatment(db)
    service = _action_service(db)
    action = service.create(treatment.id, _action_payload())

    updated = service.update(
        action.id,
        TreatmentActionUpdate(
            owner="AppSec", priority=TreatmentPriority.HIGH, due_date=date(2026, 12, 31)
        ),
    )

    assert updated.owner == "AppSec"
    assert updated.priority == TreatmentPriority.HIGH
    assert updated.due_date == date(2026, 12, 31)

    events = _events(db, action.id)
    assert events[-1].event_type == "risk.treatment_action_updated"
    assert set(events[-1].changed_fields) == {"due_date", "owner", "priority"}
    assert events[-1].before_state["owner"] == "Security Team"


def test_update_action_status_forward_and_back(db):
    treatment = _make_treatment(db)
    service = _action_service(db)
    action = service.create(treatment.id, _action_payload())

    assert (
        service.update(action.id, TreatmentActionUpdate(status="in_progress")).status
        == "in_progress"
    )
    assert service.update(action.id, TreatmentActionUpdate(status="todo")).status == "todo"

    # completed/cancelled are reachable only through the dedicated
    # endpoints (Ruling 6) — PATCH to a terminal state is 409
    with pytest.raises(InvalidRiskTreatmentActionStatusError):
        service.update(action.id, TreatmentActionUpdate(status="completed"))

    service.complete(action.id)
    with pytest.raises(InvalidRiskTreatmentActionStatusError):
        service.update(action.id, TreatmentActionUpdate(status="todo"))


def test_update_action_unknown_and_cross_tenant_404(db):
    treatment = _make_treatment(db)
    action = _action_service(db).create(treatment.id, _action_payload())

    with pytest.raises(RiskTreatmentActionNotFoundError):
        _action_service(db).update(uuid4(), TreatmentActionUpdate(owner="Nobody"))
    with pytest.raises(RiskTreatmentActionNotFoundError):
        _action_service(db, OTHER_TENANT).update(action.id, TreatmentActionUpdate(owner="Nobody"))


# --- complete ----------------------------------------------------------------


def test_complete_action_sets_completed_at_and_audits(db):
    treatment = _make_treatment(db)
    service = _action_service(db)
    action = service.create(treatment.id, _action_payload())
    service.update(action.id, TreatmentActionUpdate(status="in_progress"))

    completed = service.complete(action.id)

    assert completed.status == "completed"
    assert completed.completed_at is not None
    assert completed.completed_at.tzinfo is not None

    events = _events(db, action.id)
    assert events[-1].event_type == "risk.treatment_action_completed"
    assert events[-1].metadata_["treatment_id"] == str(treatment.id)


def test_complete_terminal_action_409(db):
    treatment = _make_treatment(db)
    service = _action_service(db)
    action = service.create(treatment.id, _action_payload())
    service.complete(action.id)

    with pytest.raises(InvalidRiskTreatmentActionStatusError):
        service.complete(action.id)


# --- cancel ------------------------------------------------------------------


def test_cancel_action_requires_reason_and_audits(db):
    with pytest.raises(ValidationError):
        TreatmentCancelRequest(reason="   ")

    treatment = _make_treatment(db)
    service = _action_service(db)
    action = service.create(treatment.id, _action_payload())

    cancelled = service.cancel(action.id, TreatmentCancelRequest(reason="Out of scope now"))

    assert cancelled.status == "cancelled"
    assert cancelled.cancellation_reason == "Out of scope now"
    assert cancelled.cancelled_at is not None

    events = _events(db, action.id)
    assert events[-1].event_type == "risk.treatment_action_cancelled"
    assert events[-1].metadata_["reason"] == "Out of scope now"
    assert events[-1].after_state["cancellation_reason"] == "Out of scope now"


def test_cancel_completed_action_409(db):
    treatment = _make_treatment(db)
    service = _action_service(db)
    action = service.create(treatment.id, _action_payload())
    service.complete(action.id)

    with pytest.raises(InvalidRiskTreatmentActionStatusError):
        service.cancel(action.id, TreatmentCancelRequest(reason="Too late"))


# --- derived progress --------------------------------------------------------


def test_treatment_progress_reflects_actions(db):
    treatment = _make_treatment(db)
    service = _action_service(db)
    actions = [
        service.create(treatment.id, _action_payload(title=f"Step {index}")) for index in range(3)
    ]

    def progress():
        rows = repository.get_actions_by_treatment(db, treatment.id)
        return metrics.compute_progress(rows)

    # RiskTreatmentService.get proves the treatment exists (404-checked
    # path) while progress itself stays derived from the action rows
    _treatment_service(db).get(treatment.id)

    service.complete(actions[0].id)
    assert progress().progress_percent == 33
    assert progress().remaining_actions == 2
    assert progress().in_progress_actions == 0

    service.complete(actions[1].id)
    assert progress().progress_percent == 66
    assert progress().remaining_actions == 1

    service.complete(actions[2].id)
    assert progress().progress_percent == 100
    assert progress().remaining_actions == 0


# --- tenancy -----------------------------------------------------------------


def test_cross_tenant_action_mutations_rejected(db):
    treatment = _make_treatment(db)
    action = _action_service(db).create(treatment.id, _action_payload())
    intruder = _action_service(db, OTHER_TENANT)

    with pytest.raises(RiskTreatmentActionNotFoundError):
        intruder.get(action.id)
    with pytest.raises(RiskTreatmentActionNotFoundError):
        intruder.update(action.id, TreatmentActionUpdate(owner="Nobody"))
    with pytest.raises(RiskTreatmentActionNotFoundError):
        intruder.complete(action.id)
    with pytest.raises(RiskTreatmentActionNotFoundError):
        intruder.cancel(action.id, TreatmentCancelRequest(reason="Not mine"))
