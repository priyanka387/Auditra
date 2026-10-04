import pytest

from app.core.config import settings
from app.modules.model_inventory.domain.errors import (
    AgentNotFoundError,
    ApplicationArchivedError,
    ApplicationNotFoundError,
    DuplicateAgentError,
    InvalidStatusTransitionError,
)
from app.modules.model_inventory.domain.events import register_handler, reset_handlers
from app.modules.model_inventory.schemas import AgentCreate, AgentUpdate, ApplicationCreate
from app.modules.model_inventory.services import AgentService, ApplicationService

OTHER_TENANT = "22222222-2222-2222-2222-222222222222"


def _application(db, **overrides):
    payload = {"name": "Support Copilot", "slug": "support-copilot"}
    payload.update(overrides)
    return ApplicationService(db, tenant_id=settings.default_tenant_id).create_application(
        ApplicationCreate(**payload)
    )


def _service(db, tenant=None):
    return AgentService(db, tenant_id=tenant or settings.default_tenant_id)


def _payload(**overrides):
    payload = {"name": "Retrieval Agent", "slug": "retrieval-agent"}
    payload.update(overrides)
    return AgentCreate(**payload)


def test_create_agent_under_application(db):
    app = _application(db)
    agent = _service(db).create_agent(app.id, _payload(framework="LangGraph"))
    assert agent.application_id == app.id
    assert agent.framework == "langgraph"
    fetched = _service(db).get_agent(agent.id)
    assert fetched.id == agent.id


def test_create_agent_unknown_application(db):
    from uuid import uuid4

    with pytest.raises(ApplicationNotFoundError):
        _service(db).create_agent(uuid4(), _payload())


def test_create_agent_cross_tenant_application(db):

    app = _application(db)
    other = _service(db, tenant=OTHER_TENANT)
    with pytest.raises(ApplicationNotFoundError):
        other.create_agent(app.id, _payload())


def test_create_agent_under_archived_application(db):
    app = _application(db)
    ApplicationService(db, tenant_id=settings.default_tenant_id).archive_application(app.id)
    with pytest.raises(ApplicationArchivedError):
        _service(db).create_agent(app.id, _payload())


def test_duplicate_slug_within_application(db):
    app = _application(db)
    service = _service(db)
    service.create_agent(app.id, _payload())
    with pytest.raises(DuplicateAgentError):
        service.create_agent(app.id, _payload(name="Renamed"))


def test_list_agents_by_application(db):
    app = _application(db)
    other = _application(db, name="Other", slug="other")
    service = _service(db)
    service.create_agent(app.id, _payload())
    service.create_agent(other.id, _payload())
    items, total = service.list_agents(application_id=app.id)
    assert total == 1
    assert items[0].application_id == app.id
    _, total_all = service.list_agents()
    assert total_all == 2


def test_update_rejects_archived_reactivation(db):
    app = _application(db)
    service = _service(db)
    agent = service.create_agent(app.id, _payload())
    service.archive_agent(agent.id)
    with pytest.raises(InvalidStatusTransitionError):
        service.update_agent(agent.id, AgentUpdate(status="ACTIVE"))


def test_archive_keeps_agent_queryable(db):
    app = _application(db)
    service = _service(db)
    agent = service.create_agent(app.id, _payload())
    service.archive_agent(agent.id)
    service.archive_agent(agent.id)
    fetched = service.get_agent(agent.id)
    assert fetched.status == "ARCHIVED"
    assert fetched.archived_at is not None


def test_get_unknown_agent(db):
    from uuid import uuid4

    with pytest.raises(AgentNotFoundError):
        _service(db).get_agent(uuid4())


def test_create_dispatches_event(db):
    app = _application(db)
    reset_handlers()
    captured = []
    register_handler(captured.append)
    try:
        agent = _service(db).create_agent(app.id, _payload())
        assert len(captured) == 1
        event = captured[0]
        assert event.event_type == "agent.created"
        assert event.agent_id == agent.id
        assert event.application_id == app.id
    finally:
        reset_handlers()
