from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.modules.model_usage.enums import UsageSource, UsageStatus
from app.modules.model_usage.errors import DuplicateEventIdError
from app.modules.model_usage.repository import (
    aggregate_stats,
    bucket_stats,
    create_event,
    get_by_event_id,
    get_event,
    list_events,
)
from app.modules.model_usage.repository import (
    commit as repository_commit,
)
from app.modules.model_usage.schemas import UsageEventFilter, UsageStatsQuery

DAY1 = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
DAY2 = datetime(2026, 10, 2, 11, 0, tzinfo=UTC)


def _fields(**overrides):
    fields = {
        "tenant_id": settings.default_tenant_id,
        "event_id": "evt-1",
        "payload_hash": "a" * 64,
        "model_id": None,
        "status": UsageStatus.SUCCESS.value,
        "source": UsageSource.API.value,
        "started_at": DAY1,
        "duration_ms": 900,
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 150,
        "token_usage_source": "provider_reported",
        "cost_source": "unavailable",
    }
    fields.update(overrides)
    return fields


def _insert(db, **overrides):
    event = create_event(db, **_fields(**overrides))
    db.commit()
    return event


def test_insert_and_get_by_event_id(db, parent_model):
    event = _insert(db, model_id=parent_model.id)
    assert event.id is not None

    loaded = get_by_event_id(db, settings.default_tenant_id, "evt-1")
    assert loaded is not None
    assert loaded.id == event.id
    assert loaded.model_id == parent_model.id

    by_uuid = get_event(db, settings.default_tenant_id, event.id)
    assert by_uuid is not None and by_uuid.id == event.id


def test_duplicate_event_id_rejected(db, parent_model):
    _insert(db, model_id=parent_model.id)
    duplicate = create_event(db, **_fields(model_id=parent_model.id))
    db.add(duplicate)
    with pytest.raises(DuplicateEventIdError):
        repository_commit(db)


def test_list_filters_and_pagination(db, parent_model):
    other = _insert(
        db,
        event_id="evt-2",
        payload_hash="b" * 64,
        model_id=parent_model.id,
        status=UsageStatus.ERROR.value,
        started_at=DAY2,
    )
    _insert(db, model_id=parent_model.id, started_at=DAY1)

    items, total = list_events(db, settings.default_tenant_id, UsageEventFilter())
    assert total == 2
    assert [item.event_id for item in items] == ["evt-2", "evt-1"]

    items, total = list_events(
        db,
        settings.default_tenant_id,
        UsageEventFilter(status=UsageStatus.SUCCESS),
    )
    assert total == 1 and items[0].event_id == "evt-1"

    items, total = list_events(
        db,
        settings.default_tenant_id,
        UsageEventFilter(started_from=DAY2),
    )
    assert total == 1 and items[0].id == other.id

    page2, total = list_events(
        db, settings.default_tenant_id, UsageEventFilter(page=2, page_size=1)
    )
    assert total == 2 and len(page2) == 1 and page2[0].event_id == "evt-1"


def test_aggregate_stats_totals(db, parent_model):
    _insert(db, model_id=parent_model.id, duration_ms=900)
    _insert(
        db,
        event_id="evt-2",
        payload_hash="b" * 64,
        model_id=parent_model.id,
        status=UsageStatus.ERROR.value,
        duration_ms=2000,
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
        token_usage_source="unavailable",
        error_type="TimeoutError",
    )
    _insert(
        db,
        event_id="evt-3",
        payload_hash="c" * 64,
        model_id=parent_model.id,
        duration_ms=None,
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
        token_usage_source="unavailable",
    )

    query = UsageStatsQuery(
        started_from=datetime(2026, 10, 1, tzinfo=UTC),
        started_to=datetime(2026, 10, 3, tzinfo=UTC),
    )
    totals = aggregate_stats(db, settings.default_tenant_id, query)
    assert totals["requests"] == 3
    assert totals["successful_requests"] == 2
    assert totals["failed_requests"] == 1
    assert totals["error_rate"] == pytest.approx(1 / 3)
    assert totals["input_tokens"] == 100
    assert totals["output_tokens"] == 50
    assert totals["total_tokens"] == 150
    assert totals["avg_latency_ms"] == pytest.approx(1450.0)
    assert totals["min_latency_ms"] == 900
    assert totals["max_latency_ms"] == 2000


def test_aggregate_stats_ignores_events_outside_range(db, parent_model):
    _insert(db, model_id=parent_model.id, started_at=DAY1)
    query = UsageStatsQuery(
        started_from=datetime(2026, 10, 2, tzinfo=UTC),
        started_to=datetime(2026, 10, 3, tzinfo=UTC),
    )
    totals = aggregate_stats(db, settings.default_tenant_id, query)
    assert totals["requests"] == 0
    assert totals["error_rate"] == 0
    assert totals["avg_latency_ms"] is None


def test_bucket_stats_groups_by_day(db, parent_model):
    _insert(db, model_id=parent_model.id, started_at=DAY1, duration_ms=100)
    _insert(
        db,
        event_id="evt-2",
        payload_hash="b" * 64,
        model_id=parent_model.id,
        started_at=DAY2 + timedelta(hours=2),
        duration_ms=300,
    )
    _insert(
        db,
        event_id="evt-3",
        payload_hash="c" * 64,
        model_id=parent_model.id,
        started_at=DAY2 + timedelta(hours=5),
        duration_ms=500,
    )

    query = UsageStatsQuery(
        started_from=datetime(2026, 10, 1, tzinfo=UTC),
        started_to=datetime(2026, 10, 3, tzinfo=UTC),
        granularity="day",
    )
    buckets = bucket_stats(db, settings.default_tenant_id, query)
    assert [b["bucket_start"] for b in buckets] == [
        datetime(2026, 10, 1, tzinfo=UTC),
        datetime(2026, 10, 2, tzinfo=UTC),
    ]
    assert buckets[0]["requests"] == 1
    assert buckets[1]["requests"] == 2
    assert buckets[1]["avg_latency_ms"] == pytest.approx(400.0)
