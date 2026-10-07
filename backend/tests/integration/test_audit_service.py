from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.config import settings
from app.modules.audit.errors import AuditEventNotFoundError, InvalidAuditFilterError
from app.modules.audit.repository import AuditFilters
from app.modules.audit.service import AuditService

MODEL_ID = "11111111-1111-1111-1111-111111111111"


def _service(db, **kwargs):
    kwargs.setdefault("request_id", "req-test-01")
    kwargs.setdefault("actor", "user-test-01")
    return AuditService(db, settings.default_tenant_id, **kwargs)


def test_record_event_persists_row(db):
    svc = _service(db)
    event = svc.record_event(
        event_type="model.created",
        resource_type="model",
        resource_id=MODEL_ID,
        before_state=None,
        after_state={"owner_name": "AI Team", "created_at": datetime(2026, 10, 7, tzinfo=UTC)},
        metadata={"operation": "register_model"},
    )
    db.commit()

    assert event.id is not None
    assert event.sequence_no is not None
    assert event.tenant_id == settings.default_tenant_id
    assert event.schema_version == 1
    assert event.actor_type == "user"
    assert event.actor_id == "user-test-01"
    assert event.source == "api"
    assert event.request_id == "req-test-01"
    assert event.correlation_id is None
    assert event.occurred_at.tzinfo is not None
    assert event.recorded_at.tzinfo is not None
    assert event.occurred_at.utcoffset() == timedelta(0)
    assert event.before_state is None
    assert event.after_state["owner_name"] == "AI Team"
    assert event.after_state["created_at"] == "2026-10-07T00:00:00+00:00"
    assert event.metadata_ == {"operation": "register_model"}


def test_list_events_filters_and_orders(db):
    svc = _service(db)
    other_id = str(uuid4())
    first = svc.record_event(
        event_type="model.created", resource_type="model", resource_id=MODEL_ID
    )
    second = svc.record_event(
        event_type="model.updated", resource_type="model", resource_id=MODEL_ID
    )
    svc.record_event(event_type="model.created", resource_type="model", resource_id=other_id)
    db.commit()

    items, total = svc.list_events(AuditFilters(resource_type="model", resource_id=MODEL_ID))
    assert total == 2
    assert [e.id for e in items] == [second.id, first.id]
    assert items[0].sequence_no > items[1].sequence_no

    items, total = svc.list_events(AuditFilters(event_type="model.updated"))
    assert total == 1
    assert items[0].id == second.id

    items, total = svc.list_events(AuditFilters(actor_id="user-test-01"))
    assert total == 3

    now = datetime.now(UTC)
    items, total = svc.list_events(
        AuditFilters(occurred_from=now - timedelta(hours=1), occurred_to=now + timedelta(hours=1))
    )
    assert total == 3
    items, total = svc.list_events(AuditFilters(occurred_from=now + timedelta(hours=1))
    )
    assert total == 0


def test_list_events_rejects_inverted_date_range(db):
    svc = _service(db)
    now = datetime.now(UTC)
    with pytest.raises(InvalidAuditFilterError):
        svc.list_events(
            AuditFilters(occurred_from=now, occurred_to=now - timedelta(days=1))
        )


def test_list_events_rejects_bad_event_type_filter(db):
    svc = _service(db)
    with pytest.raises(InvalidAuditFilterError):
        svc.list_events(AuditFilters(event_type="MODEL.CREATED"))


def test_get_event_missing_raises_not_found(db):
    svc = _service(db)
    with pytest.raises(AuditEventNotFoundError):
        svc.get_event(uuid4())


def test_get_event_returns_recorded_event(db):
    svc = _service(db)
    recorded = svc.record_event(
        event_type="deployment.created", resource_type="deployment", resource_id="dep-1"
    )
    db.commit()
    fetched = svc.get_event(recorded.id)
    assert fetched.id == recorded.id
    assert fetched.event_type == "deployment.created"


def test_pagination_deterministic(db):
    svc = _service(db)
    ids = []
    for i in range(5):
        event = svc.record_event(
            event_type="model.updated",
            resource_type="model",
            resource_id=f"m-{i}",
        )
        ids.append(event.id)
    db.commit()

    page1, total = svc.list_events(AuditFilters(), page=1, page_size=2)
    page2, _ = svc.list_events(AuditFilters(), page=2, page_size=2)
    page3, _ = svc.list_events(AuditFilters(), page=3, page_size=2)
    assert total == 5
    seen = [e.id for page in (page1, page2, page3) for e in page]
    assert seen == list(reversed(ids))
    assert len(set(seen)) == 5
