import pytest

from app.core.config import settings
from app.modules.model_inventory.domain.errors import (
    ApplicationNotFoundError,
    DuplicateApplicationError,
    InvalidStatusTransitionError,
)
from app.modules.model_inventory.domain.events import register_handler, reset_handlers
from app.modules.model_inventory.repositories import ApplicationFilters
from app.modules.model_inventory.schemas import ApplicationCreate, ApplicationUpdate
from app.modules.model_inventory.services import ApplicationService

OTHER_TENANT = "22222222-2222-2222-2222-222222222222"


def _service(db, tenant=None):
    return ApplicationService(db, tenant_id=tenant or settings.default_tenant_id)


def _payload(**overrides):
    payload = {"name": "Knowledge Assistant", "slug": "knowledge-assistant"}
    payload.update(overrides)
    return ApplicationCreate(**payload)


def test_create_and_get(db):
    service = _service(db)
    app = service.create_application(_payload())
    fetched = service.get_application(app.id)
    assert fetched.id == app.id
    assert fetched.slug == "knowledge-assistant"
    assert fetched.status == "ACTIVE"


def test_duplicate_slug_raises(db):
    service = _service(db)
    service.create_application(_payload())
    with pytest.raises(DuplicateApplicationError):
        service.create_application(_payload(name="Other Name"))


def test_cross_tenant_get_raises_not_found(db):
    service = _service(db)
    app = service.create_application(_payload())
    other = _service(db, tenant=OTHER_TENANT)
    with pytest.raises(ApplicationNotFoundError):
        other.get_application(app.id)


def test_update_rejects_archived_reactivation(db):
    service = _service(db)
    app = service.create_application(_payload())
    service.archive_application(app.id)
    with pytest.raises(InvalidStatusTransitionError):
        service.update_application(app.id, ApplicationUpdate(status="ACTIVE"))


def test_archive_is_idempotent_and_keeps_record(db):
    service = _service(db)
    app = service.create_application(_payload())
    service.archive_application(app.id)
    service.archive_application(app.id)
    fetched = service.get_application(app.id)
    assert fetched.status == "ARCHIVED"
    assert fetched.archived_at is not None


def test_list_filters_and_pagination(db):
    service = _service(db)
    service.create_application(_payload(name="Alpha", slug="alpha", application_type="copilot"))
    service.create_application(_payload(name="Beta", slug="beta", application_type="service"))
    items, total = service.list_applications(page=1, page_size=1, sort_by="name", sort_order="asc")
    assert total == 2
    assert [a.name for a in items] == ["Alpha"]
    filtered, filtered_total = service.list_applications(
        filters=ApplicationFilters(application_type="service"),
        sort_by="name",
        sort_order="asc",
    )
    assert filtered_total == 1
    assert filtered[0].slug == "beta"


def test_create_dispatches_event(db):
    reset_handlers()
    captured = []
    register_handler(captured.append)
    try:
        service = _service(db)
        app = service.create_application(_payload())
        assert len(captured) == 1
        event = captured[0]
        assert event.event_type == "application.created"
        assert event.application_id == app.id
        assert event.tenant_id == settings.default_tenant_id
    finally:
        reset_handlers()
