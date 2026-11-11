from uuid import uuid4

import pytest

from app.core.config import settings
from app.modules.asset_discovery.enums import DiscoverySourceStatus, DiscoverySourceType
from app.modules.asset_discovery.errors import (
    DiscoverySourceArchivedError,
    DiscoverySourceNotFoundError,
    DuplicateDiscoverySourceError,
    InvalidDiscoverySourceConfigError,
)
from app.modules.asset_discovery.schemas import (
    DiscoverySourceCreate,
    DiscoverySourceFilter,
    DiscoverySourceUpdate,
)
from app.modules.asset_discovery.service import DiscoverySourceService


def _service(db, tenant_id=None) -> DiscoverySourceService:
    return DiscoverySourceService(db, tenant_id=tenant_id or settings.default_tenant_id)


def _payload(**overrides) -> DiscoverySourceCreate:
    payload = {
        "name": "payments-service",
        "source_type": DiscoverySourceType.REPOSITORY,
        "config": {"location": "/workspace/payments"},
    }
    payload.update(overrides)
    return DiscoverySourceCreate(**payload)


def test_create_persists_with_defaults(db):
    created = _service(db).create(_payload())
    created_id = created.id
    db.expunge_all()

    fetched = _service(db).get(created_id)
    assert fetched.name == "payments-service"
    assert fetched.source_type == DiscoverySourceType.REPOSITORY
    assert fetched.config == {"location": "/workspace/payments"}
    assert fetched.status == DiscoverySourceStatus.ACTIVE
    assert fetched.tenant_id == settings.default_tenant_id
    assert fetched.archived_at is None
    assert fetched.created_at is not None
    assert fetched.updated_at is not None


def test_get_missing_raises_not_found(db):
    with pytest.raises(DiscoverySourceNotFoundError):
        _service(db).get(uuid4())


def test_get_is_tenant_scoped(db):
    created = _service(db).create(_payload(name="tenant-scoped"))
    with pytest.raises(DiscoverySourceNotFoundError):
        _service(db, tenant_id=uuid4()).get(created.id)


def test_create_duplicate_name_raises(db):
    _service(db).create(_payload())
    with pytest.raises(DuplicateDiscoverySourceError):
        _service(db).create(_payload(config={"location": "/workspace/other"}))


def test_create_missing_location_raises_config_error(db):
    with pytest.raises(InvalidDiscoverySourceConfigError):
        _service(db).create(_payload(config={}))


def test_create_blank_location_raises_config_error(db):
    with pytest.raises(InvalidDiscoverySourceConfigError):
        _service(db).create(_payload(config={"location": "   "}))


def test_update_changes_name_config_and_status(db):
    service = _service(db)
    created = service.create(_payload())
    updated = service.update(
        created.id,
        DiscoverySourceUpdate(
            name="payments-service-2",
            config={"location": "/workspace/payments-v2"},
            status=DiscoverySourceStatus.DISABLED,
        ),
    )
    updated_id = updated.id
    db.expunge_all()

    fetched = _service(db).get(updated_id)
    assert fetched.name == "payments-service-2"
    assert fetched.config == {"location": "/workspace/payments-v2"}
    assert fetched.status == DiscoverySourceStatus.DISABLED


def test_update_missing_raises_not_found(db):
    with pytest.raises(DiscoverySourceNotFoundError):
        _service(db).update(uuid4(), DiscoverySourceUpdate(name="nope"))


def test_update_to_duplicate_name_raises(db):
    service = _service(db)
    service.create(_payload(name="source-a"))
    other = service.create(_payload(name="source-b"))
    with pytest.raises(DuplicateDiscoverySourceError):
        service.update(other.id, DiscoverySourceUpdate(name="source-a"))


def test_update_with_invalid_config_raises(db):
    service = _service(db)
    created = service.create(_payload())
    with pytest.raises(InvalidDiscoverySourceConfigError):
        service.update(created.id, DiscoverySourceUpdate(config={}))


def test_delete_archives_and_is_idempotent(db):
    service = _service(db)
    created = service.create(_payload())
    created_id = created.id

    service.delete(created_id)
    db.expunge_all()

    archived = _service(db).get(created_id)
    assert archived.archived_at is not None

    _service(db).delete(created_id)
    db.expunge_all()
    assert _service(db).get(created_id).archived_at is not None


def test_delete_missing_raises_not_found(db):
    with pytest.raises(DiscoverySourceNotFoundError):
        _service(db).delete(uuid4())


def test_update_archived_source_raises_conflict(db):
    service = _service(db)
    created = service.create(_payload())
    service.delete(created.id)
    with pytest.raises(DiscoverySourceArchivedError):
        service.update(created.id, DiscoverySourceUpdate(name="too-late"))


def test_archived_name_can_be_reused(db):
    service = _service(db)
    created = service.create(_payload())
    service.delete(created.id)

    recreated = service.create(_payload())
    assert recreated.name == "payments-service"
    assert recreated.archived_at is None


def test_list_filters_and_pagination(db):
    service = _service(db)
    service.create(_payload(name="repo-one"))
    service.create(_payload(name="repo-two", config={"location": "/workspace/two"}))
    service.create(
        DiscoverySourceCreate(
            name="repo-three",
            source_type=DiscoverySourceType.REPOSITORY,
            config={"location": "/workspace/three"},
        )
    )

    items, total = service.list(DiscoverySourceFilter())
    assert total == 3
    assert len(items) == 3

    page_one, page_total = service.list(DiscoverySourceFilter(page=1, page_size=2))
    assert page_total == 3
    assert len(page_one) == 2

    items, total = service.list(DiscoverySourceFilter(search="repo-two"))
    assert total == 1
    assert items[0].name == "repo-two"

    items, total = service.list(DiscoverySourceFilter(source_type=DiscoverySourceType.REPOSITORY))
    assert total == 3


def test_list_excludes_archived_unless_requested(db):
    service = _service(db)
    kept = service.create(_payload(name="kept"))
    removed = service.create(_payload(name="removed"))
    service.delete(removed.id)

    items, total = service.list(DiscoverySourceFilter())
    assert total == 1
    assert items[0].id == kept.id

    items, total = service.list(DiscoverySourceFilter(include_archived=True))
    assert total == 2
    assert {item.name for item in items} == {"kept", "removed"}


def test_list_invalid_sort_raises_query_error(db):
    from app.modules.asset_discovery.errors import InvalidDiscoverySourceQueryError

    with pytest.raises(InvalidDiscoverySourceQueryError):
        _service(db).list(DiscoverySourceFilter(sort_by="nope"))


def test_service_records_audit_events(db):
    service = _service(db)
    created = service.create(_payload(name="audited-source"))
    service.update(created.id, DiscoverySourceUpdate(status=DiscoverySourceStatus.DISABLED))
    service.delete(created.id)

    from sqlalchemy import select

    from app.modules.audit.models import AuditEvent

    events = list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.resource_id == str(created.id))
            .order_by(AuditEvent.sequence_no)
        ).all()
    )
    assert [event.event_type for event in events] == [
        "discovery_source.created",
        "discovery_source.updated",
        "discovery_source.archived",
    ]
