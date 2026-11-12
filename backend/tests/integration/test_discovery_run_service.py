from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.modules.asset_discovery.enums import DiscoveryRunStatus, DiscoverySourceType
from app.modules.asset_discovery.errors import (
    DiscoveryRunError,
    DiscoveryRunNotFoundError,
    DiscoverySourceNotFoundError,
    InvalidDiscoveryRunCountsError,
    InvalidDiscoveryRunQueryError,
    InvalidDiscoveryRunTransitionError,
)
from app.modules.asset_discovery.models import DiscoveryRun
from app.modules.asset_discovery.schemas import DiscoveryRunFilter, DiscoverySourceCreate
from app.modules.asset_discovery.service import DiscoveryRunService, DiscoverySourceService


def _source_service(db, tenant_id=None) -> DiscoverySourceService:
    return DiscoverySourceService(db, tenant_id=tenant_id or settings.default_tenant_id)


def _service(db, tenant_id=None) -> DiscoveryRunService:
    return DiscoveryRunService(db, tenant_id=tenant_id or settings.default_tenant_id)


def _source(db, name="payments-service", location="/workspace/payments"):
    return _source_service(db).create(
        DiscoverySourceCreate(
            name=name,
            source_type=DiscoverySourceType.REPOSITORY,
            config={"location": location},
        )
    )


def test_create_run_persists_pending_defaults(db):
    source = _source(db)
    source_id = source.id

    created = _service(db).create(source_id)
    run_id = created.id
    db.expunge_all()

    fetched = _service(db).get(run_id)
    assert fetched.status == DiscoveryRunStatus.PENDING
    assert fetched.source_id == source_id
    assert fetched.tenant_id == settings.default_tenant_id
    assert fetched.started_at is None
    assert fetched.completed_at is None
    assert fetched.assets_detected == 0
    assert fetched.assets_created == 0
    assert fetched.assets_updated == 0
    assert fetched.assets_unchanged == 0
    assert fetched.error_count == 0
    assert fetched.error_summary is None
    assert fetched.created_at is not None
    assert fetched.updated_at is not None


def test_create_run_does_not_execute_discovery(db):
    source = _source(db)
    created = _service(db).create(source.id)

    # creating a run only records the attempt; no scan has run yet
    assert created.status == DiscoveryRunStatus.PENDING
    assert created.started_at is None
    assert created.completed_at is None
    assert created.assets_detected == 0


def test_create_run_missing_source_raises_not_found(db):
    with pytest.raises(DiscoverySourceNotFoundError):
        _service(db).create(uuid4())


def test_get_run_missing_raises_not_found(db):
    with pytest.raises(DiscoveryRunNotFoundError):
        _service(db).get(uuid4())


def test_get_run_is_tenant_scoped(db):
    source = _source(db)
    run_id = _service(db).create(source.id).id

    with pytest.raises(DiscoveryRunNotFoundError):
        _service(db, tenant_id=uuid4()).get(run_id)


def test_source_accepts_multiple_historical_runs(db):
    source = _source(db)
    other = _source(db, name="other-service", location="/workspace/other")
    service = _service(db)

    run_ids = [service.create(source.id).id for _ in range(3)]
    other_run_id = service.create(other.id).id

    items, total = service.list(DiscoveryRunFilter(source_id=source.id))
    assert total == 3
    assert {item.id for item in items} == set(run_ids)
    assert all(item.source_id == source.id for item in items)

    items, total = service.list(DiscoveryRunFilter(source_id=other.id))
    assert total == 1
    assert items[0].id == other_run_id


def test_database_rejects_nonexistent_source_reference(db):
    run = DiscoveryRun(
        id=uuid4(),
        tenant_id=settings.default_tenant_id,
        source_id=uuid4(),
        status=DiscoveryRunStatus.PENDING.value,
    )
    db.add(run)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_database_rejects_outcome_counts_exceeding_detected(db):
    source = _source(db)
    run = DiscoveryRun(
        id=uuid4(),
        tenant_id=settings.default_tenant_id,
        source_id=source.id,
        status=DiscoveryRunStatus.RUNNING.value,
        assets_detected=1,
        assets_created=1,
        assets_updated=1,
        assets_unchanged=0,
    )
    db.add(run)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_database_rejects_negative_counts(db):
    source = _source(db)
    run = DiscoveryRun(
        id=uuid4(),
        tenant_id=settings.default_tenant_id,
        source_id=source.id,
        status=DiscoveryRunStatus.PENDING.value,
        assets_detected=-1,
    )
    db.add(run)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_start_moves_run_to_running(db):
    source = _source(db)
    service = _service(db)
    run_id = service.create(source.id).id

    started = service.start(run_id)
    assert started.status == DiscoveryRunStatus.RUNNING
    db.expunge_all()

    fetched = _service(db).get(run_id)
    assert fetched.status == DiscoveryRunStatus.RUNNING
    assert fetched.started_at is not None
    assert fetched.completed_at is None


def test_start_twice_is_rejected(db):
    source = _source(db)
    service = _service(db)
    run_id = service.create(source.id).id
    service.start(run_id)

    with pytest.raises(InvalidDiscoveryRunTransitionError):
        service.start(run_id)


def test_complete_requires_running_state(db):
    source = _source(db)
    service = _service(db)
    run_id = service.create(source.id).id  # still PENDING

    with pytest.raises(InvalidDiscoveryRunTransitionError):
        service.complete(run_id, assets_detected=1)


def test_complete_records_counts_and_terminal_state(db):
    source = _source(db)
    service = _service(db)
    run_id = service.create(source.id).id
    service.start(run_id)
    service.complete(
        run_id, assets_detected=18, assets_created=12, assets_updated=4, assets_unchanged=2
    )
    db.expunge_all()

    fetched = _service(db).get(run_id)
    assert fetched.status == DiscoveryRunStatus.COMPLETED
    assert fetched.started_at is not None
    assert fetched.completed_at is not None
    assert fetched.completed_at >= fetched.started_at
    assert fetched.assets_detected == 18
    assert fetched.assets_created == 12
    assert fetched.assets_updated == 4
    assert fetched.assets_unchanged == 2
    assert fetched.error_count == 0
    assert fetched.error_summary is None


def test_fail_records_error_information(db):
    source = _source(db)
    service = _service(db)
    run_id = service.create(source.id).id
    service.start(run_id)
    service.fail(run_id, error_summary="connector exited with code 1")
    db.expunge_all()

    fetched = _service(db).get(run_id)
    assert fetched.status == DiscoveryRunStatus.FAILED
    assert fetched.started_at is not None
    assert fetched.completed_at is not None
    assert fetched.error_summary == "connector exited with code 1"
    assert fetched.error_count == 1


def test_fail_requires_non_empty_error_summary_before_mutating(db):
    source = _source(db)
    service = _service(db)
    run_id = service.create(source.id).id
    service.start(run_id)

    with pytest.raises(DiscoveryRunError) as excinfo:
        service.fail(run_id, error_summary="   ")
    assert excinfo.value.code == "DISCOVERY_RUN_ERROR"

    fetched = _service(db).get(run_id)
    assert fetched.status == DiscoveryRunStatus.RUNNING
    assert fetched.completed_at is None


def test_fail_rejects_negative_error_count(db):
    source = _source(db)
    service = _service(db)
    run_id = service.create(source.id).id
    service.start(run_id)

    with pytest.raises(InvalidDiscoveryRunCountsError):
        service.fail(run_id, error_summary="boom", error_count=-1)

    fetched = _service(db).get(run_id)
    assert fetched.status == DiscoveryRunStatus.RUNNING


def test_partial_records_counts_and_errors(db):
    source = _source(db)
    service = _service(db)
    run_id = service.create(source.id).id
    service.start(run_id)
    service.partial(
        run_id,
        assets_detected=10,
        assets_created=6,
        assets_updated=2,
        assets_unchanged=1,
        error_summary="3 files unreadable",
        error_count=3,
    )
    db.expunge_all()

    fetched = _service(db).get(run_id)
    assert fetched.status == DiscoveryRunStatus.PARTIAL
    assert fetched.completed_at is not None
    assert fetched.assets_detected == 10
    assert fetched.assets_created == 6
    assert fetched.assets_updated == 2
    assert fetched.assets_unchanged == 1
    assert fetched.error_count == 3
    assert fetched.error_summary == "3 files unreadable"


def test_partial_requires_error_summary(db):
    source = _source(db)
    service = _service(db)
    run_id = service.create(source.id).id
    service.start(run_id)

    with pytest.raises(DiscoveryRunError) as excinfo:
        service.partial(run_id, assets_detected=1, error_summary="")
    assert excinfo.value.code == "DISCOVERY_RUN_ERROR"

    fetched = _service(db).get(run_id)
    assert fetched.status == DiscoveryRunStatus.RUNNING


def test_terminal_states_are_final(db):
    source = _source(db)
    service = _service(db)
    run_id = service.create(source.id).id
    service.start(run_id)
    service.complete(run_id, assets_detected=1)

    rejected_operations = [
        lambda: service.start(run_id),
        lambda: service.complete(run_id),
        lambda: service.fail(run_id, error_summary="too late"),
        lambda: service.partial(run_id, error_summary="too late"),
    ]
    for operation in rejected_operations:
        with pytest.raises(InvalidDiscoveryRunTransitionError):
            operation()


def test_complete_rejects_negative_counts_before_mutating(db):
    source = _source(db)
    service = _service(db)
    run_id = service.create(source.id).id
    service.start(run_id)

    with pytest.raises(InvalidDiscoveryRunCountsError):
        service.complete(run_id, assets_detected=5, assets_created=-1)

    fetched = _service(db).get(run_id)
    assert fetched.status == DiscoveryRunStatus.RUNNING


def test_complete_rejects_outcome_counts_exceeding_detected(db):
    source = _source(db)
    service = _service(db)
    run_id = service.create(source.id).id
    service.start(run_id)

    with pytest.raises(InvalidDiscoveryRunCountsError):
        service.complete(run_id, assets_detected=5, assets_created=4, assets_updated=2)

    fetched = _service(db).get(run_id)
    assert fetched.status == DiscoveryRunStatus.RUNNING


def test_complete_allows_outcomes_below_detected(db):
    source = _source(db)
    service = _service(db)
    run_id = service.create(source.id).id
    service.start(run_id)
    service.complete(run_id, assets_detected=10, assets_created=4, assets_unchanged=2)

    fetched = _service(db).get(run_id)
    assert fetched.status == DiscoveryRunStatus.COMPLETED
    assert fetched.assets_detected == 10


def test_list_filters_by_source_status_and_started_window(db):
    source_a = _source(db, name="source-a")
    source_b = _source(db, name="source-b", location="/workspace/b")
    service = _service(db)

    run_a1 = service.create(source_a.id)
    run_a2 = service.create(source_a.id)
    run_b1 = service.create(source_b.id)
    service.start(run_a1.id)
    service.complete(run_a1.id, assets_detected=2)
    service.start(run_b1.id)

    items, total = service.list(DiscoveryRunFilter(source_id=source_a.id))
    assert total == 2
    assert {item.id for item in items} == {run_a1.id, run_a2.id}

    items, total = service.list(DiscoveryRunFilter(status=DiscoveryRunStatus.PENDING))
    assert total == 1
    assert items[0].id == run_a2.id

    items, total = service.list(DiscoveryRunFilter(status=DiscoveryRunStatus.RUNNING))
    assert total == 1
    assert items[0].id == run_b1.id

    past = datetime.now(UTC) - timedelta(days=1)
    future = datetime.now(UTC) + timedelta(days=1)

    items, total = service.list(DiscoveryRunFilter(started_from=past))
    assert total == 2
    assert {item.id for item in items} == {run_a1.id, run_b1.id}

    items, total = service.list(DiscoveryRunFilter(started_to=past))
    assert total == 0

    items, total = service.list(DiscoveryRunFilter(started_from=future))
    assert total == 0

    items, total = service.list(
        DiscoveryRunFilter(source_id=source_a.id, status=DiscoveryRunStatus.COMPLETED)
    )
    assert total == 1
    assert items[0].id == run_a1.id


def test_list_paginates(db):
    source = _source(db)
    service = _service(db)
    for _ in range(3):
        service.create(source.id)

    page_one, total = service.list(DiscoveryRunFilter(page=1, page_size=2))
    assert total == 3
    assert len(page_one) == 2

    page_two, total = service.list(DiscoveryRunFilter(page=2, page_size=2))
    assert total == 3
    assert len(page_two) == 1
    assert {item.id for item in page_one}.isdisjoint(item.id for item in page_two)


def test_list_invalid_sort_raises_query_error(db):
    source = _source(db)
    _service(db).create(source.id)

    with pytest.raises(InvalidDiscoveryRunQueryError):
        _service(db).list(DiscoveryRunFilter(sort_by="nope"))
