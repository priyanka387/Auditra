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
