"""Integration tests: RF-04 RiskTreatmentService — state machine, acceptance, tenancy, audit.

Covers the one-active-treatment invariant (partial unique index), the
forward-only status machine (plan §55 + Ruling 6's not_started→cancelled
gap fill), the ACCEPT rationale guard with its dedicated audit event,
derived overdue filtering, and tenant isolation on every mutation
(plan §51). Completion snapshots action counts into the audit metadata
and never touches the risk row (plan §20/§21, Ruling 16).
"""

from datetime import date
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from pydantic import ValidationError

from app.core.config import settings
from app.modules.audit.models import AuditEvent
from app.modules.risk.errors import RiskArchivedError, RiskNotFoundError
from app.modules.risk.models import Risk, RiskCategory
from app.modules.risk.schemas import RiskCreate
from app.modules.risk.seed import seed_risk_categories
from app.modules.risk.service import RiskService
from app.modules.risk_treatment.enums import RiskTreatmentStrategy
from app.modules.risk_treatment.errors import (
    InvalidRiskTreatmentQueryError,
    InvalidRiskTreatmentStatusError,
    RiskTreatmentActiveExistsError,
    RiskTreatmentError,
    RiskTreatmentNotFoundError,
)
from app.modules.risk_treatment.schemas import (
    TreatmentCancelRequest,
    TreatmentCreate,
    TreatmentFilter,
    TreatmentUpdate,
)
from app.modules.risk_treatment.service import RiskTreatmentService

TENANT = settings.default_tenant_id
OTHER_TENANT = uuid4()


# --- fixtures & helpers ------------------------------------------------------


def _service(db, tenant_id: UUID | None = None) -> RiskTreatmentService:
    return RiskTreatmentService(
        db,
        tenant_id=tenant_id or TENANT,
        request_id="req-treatment-01",
        actor=None,
    )


def _make_risk(db, tenant_id: UUID, *, title: str, archived: bool = False) -> Risk:
    """Create a real risk through RF-01's RiskService (plan Task 4 fixture)."""
    seed_risk_categories(db, tenant_id)
    db.commit()
    category = db.scalar(
        sa.select(RiskCategory).where(
            RiskCategory.tenant_id == tenant_id,
            RiskCategory.slug == "security",
        )
    )
    risk_service = RiskService(db, tenant_id=tenant_id, actor=None)
    risk = risk_service.create(RiskCreate(title=title, category_id=category.id))
    if archived:
        risk_service.archive(risk.id)
    return risk


def _payload(**overrides) -> TreatmentCreate:
    payload = {
        "title": "Encrypt training data at rest",
        "strategy": RiskTreatmentStrategy.MITIGATE,
        "owner": "Security Team",
        "planned_start_date": date(2026, 11, 1),
        "target_completion_date": date(2026, 12, 31),
    }
    payload.update(overrides)
    return TreatmentCreate(**payload)


def _events(db, resource_id: UUID) -> list[AuditEvent]:
    stmt = (
        sa.select(AuditEvent)
        .where(
            AuditEvent.resource_type == "treatment",
            AuditEvent.resource_id == str(resource_id),
        )
        .order_by(AuditEvent.sequence_no)
    )
    return list(db.scalars(stmt))


def _event_types(db, resource_id: UUID) -> list[str]:
    return [event.event_type for event in _events(db, resource_id)]


def _to_in_progress(service: RiskTreatmentService, treatment_id: UUID) -> None:
    service.update(treatment_id, TreatmentUpdate(status="planned"))
    service.update(treatment_id, TreatmentUpdate(status="in_progress"))


# --- create ------------------------------------------------------------------


def test_create_treatment_persists_not_started_with_audit(db):
    risk = _make_risk(db, TENANT, title="Training data unencrypted")
    treatment = _service(db).create(risk.id, _payload())

    assert treatment.status == "not_started"
    assert treatment.strategy == RiskTreatmentStrategy.MITIGATE
    assert treatment.owner == "Security Team"
    assert treatment.planned_start_date == date(2026, 11, 1)
    assert treatment.target_completion_date == date(2026, 12, 31)
    assert treatment.risk_id == risk.id
    assert treatment.tenant_id == TENANT
    assert treatment.created_by is None
    assert treatment.completed_at is None
    assert treatment.cancelled_at is None

    events = _events(db, treatment.id)
    assert [event.event_type for event in events] == ["risk.treatment_created"]
    assert events[0].metadata_["risk_id"] == str(risk.id)


def test_create_treatment_unknown_risk_returns_404(db):
    with pytest.raises(RiskNotFoundError):
        _service(db).create(uuid4(), _payload())


def test_create_treatment_cross_tenant_risk_returns_404(db):
    risk = _make_risk(db, OTHER_TENANT, title="Other tenant risk")
    with pytest.raises(RiskNotFoundError):
        _service(db).create(risk.id, _payload())


def test_create_treatment_on_archived_risk_returns_409(db):
    risk = _make_risk(db, TENANT, title="Archived risk", archived=True)
    with pytest.raises(RiskArchivedError):
        _service(db).create(risk.id, _payload())


def test_create_second_active_treatment_rejected(db):
    risk = _make_risk(db, TENANT, title="Risk with active treatment")
    service = _service(db)
    service.create(risk.id, _payload())

    with pytest.raises(RiskTreatmentActiveExistsError) as exc:
        service.create(risk.id, _payload(title="Parallel strategy"))
    assert exc.value.code == "RISK_TREATMENT_ACTIVE_EXISTS"
    assert exc.value.http_status == 409


def test_completed_treatment_allows_a_new_treatment(db):
    risk = _make_risk(db, TENANT, title="Risk with completed history")
    service = _service(db)
    first = service.create(risk.id, _payload())
    _to_in_progress(service, first.id)
    service.complete(first.id)

    second = service.create(risk.id, _payload(title="Follow-up hardening"))
    assert second.status == "not_started"
    assert second.id != first.id

    items, total = service.list(TreatmentFilter(risk_id=risk.id))
    assert total == 2
    assert {item.status for item in items} == {"completed", "not_started"}


def test_create_accept_requires_rationale(db):
    risk = _make_risk(db, TENANT, title="Accepted exposure risk")
    service = _service(db)

    with pytest.raises(RiskTreatmentError) as exc:
        service.create(risk.id, _payload(strategy="accept", rationale="   "))
    assert exc.value.http_status == 400
    assert exc.value.code == "RISK_TREATMENT_ERROR"

    accepted = service.create(
        risk.id, _payload(strategy="accept", rationale="Board accepted the residual exposure")
    )
    assert accepted.strategy == RiskTreatmentStrategy.ACCEPT
    assert accepted.rationale == "Board accepted the residual exposure"

    types = _event_types(db, accepted.id)
    assert types[0] == "risk.treatment_created"
    assert types.count("risk.treatment_acceptance_recorded") == 1


def test_create_all_four_strategies(db):
    service = _service(db)
    for index, strategy in enumerate(RiskTreatmentStrategy):
        risk = _make_risk(db, TENANT, title=f"Risk for strategy {strategy}")
        overrides: dict = {"strategy": strategy}
        if strategy == RiskTreatmentStrategy.ACCEPT:
            overrides["rationale"] = "Documented acceptance"
        treatment = service.create(risk.id, _payload(**overrides))
        assert treatment.strategy == strategy
        assert treatment.status == "not_started"
        assert treatment.risk_id == risk.id
        assert index >= 0  # all four strategies iterate to completion


# --- read --------------------------------------------------------------------


def test_get_treatment_tenant_scoped(db):
    risk = _make_risk(db, OTHER_TENANT, title="Tenant B risk")
    treatment = _service(db, OTHER_TENANT).create(risk.id, _payload())

    with pytest.raises(RiskTreatmentNotFoundError):
        _service(db).get(treatment.id)
    assert _service(db, OTHER_TENANT).get(treatment.id).id == treatment.id


def test_list_treatments_filters_and_pagination(db):
    service = _service(db)
    risk_one = _make_risk(db, TENANT, title="Filter risk one")
    risk_two = _make_risk(db, TENANT, title="Filter risk two")
    risk_three = _make_risk(db, TENANT, title="Filter risk three")
    first = service.create(risk_one.id, _payload(strategy="mitigate"))
    second = service.create(
        risk_two.id, _payload(strategy="accept", rationale="Accepted", owner="Legal")
    )
    third = service.create(risk_three.id, _payload(strategy="avoid", owner="Security Team"))
    service.update(third.id, TreatmentUpdate(status="planned"))

    items, total = service.list(TreatmentFilter(strategy="mitigate"))
    assert total == 1
    assert items[0].id == first.id

    items, total = service.list(TreatmentFilter(status="planned"))
    assert total == 1
    assert items[0].id == third.id

    items, total = service.list(TreatmentFilter(owner="Security Team"))
    assert total == 2
    assert {item.id for item in items} == {first.id, third.id}

    items, total = service.list(TreatmentFilter(risk_id=risk_two.id))
    assert total == 1
    assert items[0].id == second.id

    items, total = service.list(TreatmentFilter(page=1, page_size=2))
    assert total == 3
    assert len(items) == 2
    items, total = service.list(TreatmentFilter(page=2, page_size=2))
    assert total == 3
    assert len(items) == 1

    items, total = service.list(TreatmentFilter(sort_by="target_completion_date"))
    assert total == 3

    with pytest.raises(InvalidRiskTreatmentQueryError) as exc:
        service.list(TreatmentFilter(sort_by="title"))
    assert exc.value.http_status == 400

    items, total = service.list_by_risk(risk_one.id, TreatmentFilter())
    assert total == 1
    assert items[0].id == first.id
    with pytest.raises(RiskNotFoundError):
        service.list_by_risk(uuid4(), TreatmentFilter())


def test_list_treatments_overdue_filter(db):
    service = _service(db)
    past_start = date(2025, 12, 1)
    past_target = date(2026, 1, 1)
    future_target = date(2027, 6, 1)

    risk_overdue = _make_risk(db, TENANT, title="Overdue risk")
    risk_done = _make_risk(db, TENANT, title="Done risk")
    risk_future = _make_risk(db, TENANT, title="Future risk")

    overdue = service.create(
        risk_overdue.id,
        _payload(planned_start_date=past_start, target_completion_date=past_target),
    )
    done = service.create(
        risk_done.id,
        _payload(planned_start_date=past_start, target_completion_date=past_target),
    )
    future = service.create(
        risk_future.id,
        _payload(planned_start_date=date(2026, 11, 1), target_completion_date=future_target),
    )
    _to_in_progress(service, done.id)
    service.complete(done.id)

    items, total = service.list(TreatmentFilter(overdue=True))
    assert total == 1
    assert items[0].id == overdue.id

    items, total = service.list(TreatmentFilter(overdue=False))
    assert total == 2
    assert {item.id for item in items} == {done.id, future.id}


# --- update ------------------------------------------------------------------


def test_update_treatment_changes_owner_and_strategy_with_audit(db):
    risk = _make_risk(db, TENANT, title="Reassigned risk")
    service = _service(db)
    treatment = service.create(risk.id, _payload(strategy="mitigate", owner="Security Team"))

    updated = service.update(treatment.id, TreatmentUpdate(owner="Platform Team", strategy="avoid"))
    assert updated.owner == "Platform Team"
    assert updated.strategy == RiskTreatmentStrategy.AVOID

    events = _events(db, treatment.id)
    types = [event.event_type for event in events]
    assert types[0] == "risk.treatment_created"
    assert "risk.treatment_updated" in types
    assert "risk.treatment_strategy_changed" in types
    assert "risk.treatment_owner_changed" in types
    assert "risk.treatment_acceptance_recorded" not in types

    updated_event = next(e for e in events if e.event_type == "risk.treatment_updated")
    assert set(updated_event.changed_fields) == {"owner", "strategy"}
    assert updated_event.before_state["owner"] == "Security Team"
    assert updated_event.after_state["owner"] == "Platform Team"

    strategy_event = next(e for e in events if e.event_type == "risk.treatment_strategy_changed")
    assert strategy_event.before_state["strategy"] == "mitigate"
    assert strategy_event.after_state["strategy"] == "avoid"

    owner_event = next(e for e in events if e.event_type == "risk.treatment_owner_changed")
    assert owner_event.before_state["owner"] == "Security Team"
    assert owner_event.after_state["owner"] == "Platform Team"


def test_update_treatment_status_forward_only(db):
    risk = _make_risk(db, TENANT, title="Forward-only risk")
    service = _service(db)
    treatment = service.create(risk.id, _payload())

    moved = service.update(treatment.id, TreatmentUpdate(status="planned"))
    assert moved.status == "planned"

    with pytest.raises(InvalidRiskTreatmentStatusError) as exc:
        service.update(treatment.id, TreatmentUpdate(status="not_started"))
    assert exc.value.code == "RISK_TREATMENT_STATUS_INVALID"
    assert exc.value.http_status == 409
    assert service.get(treatment.id).status == "planned"

    advanced = service.update(treatment.id, TreatmentUpdate(status="in_progress"))
    assert advanced.status == "in_progress"

    risk_two = _make_risk(db, TENANT, title="Skipped-state risk")
    second = service.create(risk_two.id, _payload())
    with pytest.raises(InvalidRiskTreatmentStatusError):
        service.update(second.id, TreatmentUpdate(status="in_progress"))
    assert service.get(second.id).status == "not_started"


def test_update_terminal_treatment_rejected(db):
    risk = _make_risk(db, TENANT, title="Terminal risk")
    service = _service(db)
    treatment = service.create(risk.id, _payload())
    _to_in_progress(service, treatment.id)
    service.complete(treatment.id)

    with pytest.raises(InvalidRiskTreatmentStatusError) as exc:
        service.update(treatment.id, TreatmentUpdate(title="Sneaky rename"))
    assert exc.value.http_status == 409
    assert service.get(treatment.id).title == "Encrypt training data at rest"


def test_update_strategy_to_accept_requires_rationale(db):
    risk = _make_risk(db, TENANT, title="Switch to accept risk")
    service = _service(db)
    treatment = service.create(risk.id, _payload(strategy="mitigate"))

    with pytest.raises(RiskTreatmentError) as exc:
        service.update(treatment.id, TreatmentUpdate(strategy="accept"))
    assert exc.value.http_status == 400
    assert service.get(treatment.id).strategy == RiskTreatmentStrategy.MITIGATE

    switched = service.update(
        treatment.id,
        TreatmentUpdate(strategy="accept", rationale="Residual exposure approved"),
    )
    assert switched.strategy == RiskTreatmentStrategy.ACCEPT
    assert switched.rationale == "Residual exposure approved"

    types = _event_types(db, treatment.id)
    assert "risk.treatment_updated" in types
    assert "risk.treatment_strategy_changed" in types
    assert types.count("risk.treatment_acceptance_recorded") == 1


# --- complete ----------------------------------------------------------------


def test_complete_treatment_from_in_progress(db):
    risk = _make_risk(db, TENANT, title="Completable risk")
    service = _service(db)
    treatment = service.create(risk.id, _payload())
    _to_in_progress(service, treatment.id)

    done = service.complete(treatment.id)
    assert done.status == "completed"
    assert done.completed_at is not None
    assert done.completed_at.tzinfo is not None
    assert done.cancelled_at is None

    completed_events = [
        event
        for event in _events(db, treatment.id)
        if event.event_type == "risk.treatment_completed"
    ]
    assert len(completed_events) == 1
    assert completed_events[0].metadata_["risk_id"] == str(risk.id)
    assert completed_events[0].metadata_["total_actions"] == 0
    assert completed_events[0].metadata_["completed_actions"] == 0

    # plan §20/§21: completing a treatment never touches the risk row
    assert risk.status == "open"
    assert risk.archived_at is None


def test_complete_treatment_rejects_wrong_state(db):
    risk = _make_risk(db, TENANT, title="Wrong-state completion risk")
    service = _service(db)
    treatment = service.create(risk.id, _payload())

    with pytest.raises(InvalidRiskTreatmentStatusError):
        service.complete(treatment.id)

    service.update(treatment.id, TreatmentUpdate(status="planned"))
    with pytest.raises(InvalidRiskTreatmentStatusError):
        service.complete(treatment.id)

    service.update(treatment.id, TreatmentUpdate(status="in_progress"))
    service.complete(treatment.id)

    # no silent reopen (plan §55)
    with pytest.raises(InvalidRiskTreatmentStatusError):
        service.complete(treatment.id)
    assert service.get(treatment.id).status == "completed"


# --- cancel ------------------------------------------------------------------


def test_cancel_treatment_requires_reason(db):
    risk = _make_risk(db, TENANT, title="Cancellable risk")
    service = _service(db)
    treatment = service.create(risk.id, _payload())

    with pytest.raises(ValidationError):
        TreatmentCancelRequest(reason="   ")

    cancelled = service.cancel(
        treatment.id, TreatmentCancelRequest(reason="Superseded by avoidance")
    )
    assert cancelled.status == "cancelled"
    assert cancelled.cancelled_at is not None
    assert cancelled.cancelled_at.tzinfo is not None
    assert cancelled.cancellation_reason == "Superseded by avoidance"
    assert cancelled.completed_at is None

    cancel_events = [
        event
        for event in _events(db, treatment.id)
        if event.event_type == "risk.treatment_cancelled"
    ]
    assert len(cancel_events) == 1
    assert cancel_events[0].metadata_["reason"] == "Superseded by avoidance"
    assert cancel_events[0].metadata_["risk_id"] == str(risk.id)


def test_cancel_not_started_treatment_allowed(db):
    # Ruling 6 gap fill: a never-started treatment must be cancellable
    risk = _make_risk(db, TENANT, title="Never-started risk")
    service = _service(db)
    treatment = service.create(risk.id, _payload())

    cancelled = service.cancel(treatment.id, TreatmentCancelRequest(reason="Plan changed"))
    assert cancelled.status == "cancelled"
    assert cancelled.cancellation_reason == "Plan changed"


def test_cancel_completed_treatment_rejected(db):
    risk = _make_risk(db, TENANT, title="Completed risk")
    service = _service(db)
    treatment = service.create(risk.id, _payload())
    _to_in_progress(service, treatment.id)
    service.complete(treatment.id)

    with pytest.raises(InvalidRiskTreatmentStatusError) as exc:
        service.cancel(treatment.id, TreatmentCancelRequest(reason="Too late"))
    assert exc.value.http_status == 409
    assert service.get(treatment.id).status == "completed"


# --- tenant isolation --------------------------------------------------------


def test_cross_tenant_mutations_rejected(db):
    risk = _make_risk(db, OTHER_TENANT, title="Tenant B treatment risk")
    victim = _service(db, OTHER_TENANT).create(risk.id, _payload())
    attacker = _service(db)

    with pytest.raises(RiskTreatmentNotFoundError):
        attacker.update(victim.id, TreatmentUpdate(title="Hijacked"))
    with pytest.raises(RiskTreatmentNotFoundError):
        attacker.complete(victim.id)
    with pytest.raises(RiskTreatmentNotFoundError):
        attacker.cancel(victim.id, TreatmentCancelRequest(reason="Hostile cancel"))

    survivor = _service(db, OTHER_TENANT).get(victim.id)
    assert survivor.title == "Encrypt training data at rest"
    assert survivor.status == "not_started"
