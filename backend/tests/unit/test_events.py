from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.modules.model_inventory.domain.events import (
    ModelEvent,
    ModelVersionEvent,
    dispatch_event,
    register_handler,
    reset_handlers,
)


def _event():
    return ModelEvent(
        event_id=str(uuid4()),
        event_type="model.created",
        model_id=uuid4(),
        tenant_id=uuid4(),
        occurred_at=datetime.now(UTC),
        actor="tester",
        change_summary=["created"],
        request_id="req-1",
    )


def _version_event():
    return ModelVersionEvent(
        event_id=str(uuid4()),
        event_type="model_version.created",
        tenant_id=uuid4(),
        model_id=uuid4(),
        model_version_id=uuid4(),
        occurred_at=datetime.now(UTC),
        actor="tester",
        change_summary=[],
        request_id="req-1",
    )


def test_version_event_dispatch_reaches_handler():
    reset_handlers()
    captured = []
    register_handler(captured.append)

    event = _version_event()
    dispatch_event(event)

    assert captured == [event]
    reset_handlers()


def test_event_dispatch_reaches_handler():
    reset_handlers()
    captured = []
    order = []

    def first(event):
        order.append("first")
        captured.append(event)

    def second(event):
        order.append("second")

    register_handler(first)
    register_handler(second)

    event = _event()
    dispatch_event(event)

    assert captured == [event]
    assert order == ["first", "second"]

    reset_handlers()

    def boom(_event):
        raise RuntimeError("handler exploded")

    register_handler(boom)
    with pytest.raises(RuntimeError, match="handler exploded"):
        dispatch_event(event)
    reset_handlers()


def _application_event():
    from app.modules.model_inventory.domain.events import ApplicationEvent

    return ApplicationEvent(
        event_id=str(uuid4()),
        event_type="application.created",
        tenant_id=uuid4(),
        application_id=uuid4(),
        occurred_at=datetime.now(UTC),
        actor="tester",
        change_summary=["created"],
        request_id="req-1",
    )


def _agent_event():
    from app.modules.model_inventory.domain.events import AgentEvent

    return AgentEvent(
        event_id=str(uuid4()),
        event_type="agent.created",
        tenant_id=uuid4(),
        application_id=uuid4(),
        agent_id=uuid4(),
        occurred_at=datetime.now(UTC),
        actor="tester",
        change_summary=["created"],
        request_id="req-1",
    )


def _association_event():
    from app.modules.model_inventory.domain.events import AgentModelAssociationEvent

    return AgentModelAssociationEvent(
        event_id=str(uuid4()),
        event_type="agent.model_associated",
        tenant_id=uuid4(),
        agent_id=uuid4(),
        model_id=uuid4(),
        association_id=uuid4(),
        occurred_at=datetime.now(UTC),
        actor="tester",
        change_summary=["created"],
        request_id="req-1",
    )


def test_application_event_dispatch_reaches_handler():
    reset_handlers()
    captured = []
    register_handler(captured.append)
    event = _application_event()
    dispatch_event(event)
    assert captured == [event]
    assert captured[0].application_id == event.application_id
    reset_handlers()


def test_agent_event_dispatch_reaches_handler():
    reset_handlers()
    captured = []
    register_handler(captured.append)
    event = _agent_event()
    dispatch_event(event)
    assert captured == [event]
    assert captured[0].agent_id == event.agent_id
    reset_handlers()


def test_association_event_dispatch_reaches_handler():
    reset_handlers()
    captured = []
    register_handler(captured.append)
    event = _association_event()
    dispatch_event(event)
    assert captured == [event]
    assert captured[0].event_type == "agent.model_associated"
    reset_handlers()
