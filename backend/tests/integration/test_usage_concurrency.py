from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, local
from uuid import uuid4

import sqlalchemy as sa

from app.core.config import settings
from app.core.db import SessionLocal
from app.modules.model_usage.models import ModelUsageEvent
from app.modules.model_usage.repository import aggregate_stats
from app.modules.model_usage.schemas import UsageEventCreate, UsageStatsQuery
from app.modules.model_usage.service import ModelUsageService

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def _payload(parent_model, event_id=None):
    return UsageEventCreate(
        event_id=event_id or f"evt-{uuid4()}",
        model_id=parent_model.id,
        source="api",
        started_at=NOW,
        duration_ms=42,
    )


def _run_in_sessions(worker, payloads):
    def wrapped(payload):
        session = SessionLocal()
        try:
            return worker(session, payload)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=min(4, len(payloads))) as pool:
        return list(pool.map(wrapped, payloads))


def test_concurrent_same_event_id_persists_single_row(db, parent_model, monkeypatch):
    import app.modules.model_usage.repository as repo

    barrier = Barrier(2, timeout=15)
    tls = local()
    real_get = repo.get_by_event_id

    def gated_get(*args, **kwargs):
        result = real_get(*args, **kwargs)
        if not getattr(tls, "waited", False):
            tls.waited = True
            barrier.wait()
        return result

    monkeypatch.setattr(repo, "get_by_event_id", gated_get)

    payload = _payload(parent_model, event_id="race-evt-1")

    def worker(session, _):
        return ModelUsageService(session, tenant_id=settings.default_tenant_id).record_event(
            payload
        )

    results = _run_in_sessions(worker, [None, None])

    assert sorted(created for _, created in results) == [False, True]
    rows = db.scalars(
        sa.select(ModelUsageEvent).where(ModelUsageEvent.event_id == "race-evt-1")
    ).all()
    assert len(rows) == 1


def test_concurrent_distinct_events_all_persist(db, parent_model):
    payloads = [_payload(parent_model) for _ in range(8)]

    def worker(session, payload):
        _, created = ModelUsageService(session, tenant_id=settings.default_tenant_id).record_event(
            payload
        )
        assert created is True

    _run_in_sessions(worker, payloads)

    assert db.scalar(sa.select(sa.func.count()).select_from(ModelUsageEvent)) == 8


def test_batch_concurrent_with_single_ingestion(db, parent_model):
    batch_payloads = [_payload(parent_model) for _ in range(5)]
    single_payloads = [_payload(parent_model) for _ in range(3)]

    def batch_worker(session, _):
        response = ModelUsageService(session, tenant_id=settings.default_tenant_id).record_batch(
            batch_payloads
        )
        assert response.rejected == 0
        assert response.accepted == 5

    def single_worker(session, payload):
        _, created = ModelUsageService(session, tenant_id=settings.default_tenant_id).record_event(
            payload
        )
        assert created is True

    def run(worker, payload):
        session = SessionLocal()
        try:
            return worker(session, payload)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(run, batch_worker, None),
            *[pool.submit(run, single_worker, p) for p in single_payloads],
        ]
        for future in futures:
            future.result()

    assert db.scalar(sa.select(sa.func.count()).select_from(ModelUsageEvent)) == 8


def test_stats_query_while_events_are_inserted(db, parent_model):
    payloads = [_payload(parent_model) for _ in range(20)]
    started = Barrier(2, timeout=15)
    stats_errors = []

    def writer():
        session = SessionLocal()
        try:
            service = ModelUsageService(session, tenant_id=settings.default_tenant_id)
            started.wait()
            for payload in payloads:
                service.record_event(payload)
        finally:
            session.close()

    def reader():
        started.wait()
        try:
            for _ in range(50):
                aggregate_stats(
                    db,
                    settings.default_tenant_id,
                    UsageStatsQuery(
                        model_id=parent_model.id,
                        started_from=NOW,
                        started_to=NOW + timedelta(days=1),
                    ),
                )
        except Exception as exc:  # noqa: BLE001 - capture any failure for assertion
            stats_errors.append(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer_future = pool.submit(writer)
        reader_future = pool.submit(reader)
        writer_future.result()
        reader_future.result()

    assert stats_errors == []
    assert db.scalar(sa.select(sa.func.count()).select_from(ModelUsageEvent)) == 20
    totals = aggregate_stats(
        db,
        settings.default_tenant_id,
        UsageStatsQuery(
            model_id=parent_model.id,
            started_from=NOW,
            started_to=NOW + timedelta(days=1),
        ),
    )
    assert totals["requests"] == 20
