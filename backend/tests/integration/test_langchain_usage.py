from uuid import uuid4

import pytest
import sqlalchemy as sa
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.core.config import settings
from app.modules.model_usage.enums import UsageSource, UsageStatus
from app.modules.model_usage.errors import UsageContextMissingError
from app.modules.model_usage.integrations.langchain import AuditraUsageCallback
from app.modules.model_usage.models import ModelUsageEvent
from app.modules.model_usage.service import ModelUsageService
from app.modules.model_usage.telemetry import ServiceUsageCollector

METADATA = {
    "auditra_model_id": None,  # filled per test
    "auditra_operation": "support_agent",
    "auditra_environment": "production",
    "auditra_trace_id": "trace-123",
}


class _TimeoutChatModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "timeout"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise TimeoutError("provider timeout")


class _TokenChatModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "token"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        message = AIMessage(
            content="ok",
            usage_metadata={
                "input_tokens": 843,
                "output_tokens": 392,
                "total_tokens": 1235,
            },
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


class _BrokenService:
    def record_event(self, payload):
        raise RuntimeError("telemetry backend down")


def _collector(db, mode="best_effort", service=None):
    service = service or ModelUsageService(db, settings.default_tenant_id)
    return ServiceUsageCollector(service, mode=mode)


def _config(collector, model_id=None, source=UsageSource.LANGCHAIN, **metadata_overrides):
    metadata = {k: v for k, v in METADATA.items() if v is not None}
    metadata.update(metadata_overrides)
    if model_id is not None:
        metadata["auditra_model_id"] = str(model_id)
    return {
        "callbacks": [AuditraUsageCallback(collector, source=source)],
        "metadata": metadata,
        "tags": ["auditra", "test"],
    }


def _events(db):
    return list(db.scalars(sa.select(ModelUsageEvent).order_by(ModelUsageEvent.created_at)).all())


def test_langchain_success_creates_usage_event(db, parent_model):
    collector = _collector(db)
    model = FakeListChatModel(responses=["hello"])

    result = model.invoke([HumanMessage("hi")], config=_config(collector, parent_model.id))

    assert result.content == "hello"
    events = _events(db)
    assert len(events) == 1
    event = events[0]
    assert event.status == UsageStatus.SUCCESS.value
    assert event.source == UsageSource.LANGCHAIN.value
    assert event.model_id == parent_model.id
    assert event.framework == "langchain"
    assert event.framework_version is not None
    assert event.operation_name == "support_agent"
    assert event.environment == "production"
    assert event.trace_id == "trace-123"
    assert event.request_id is not None
    assert event.completed_at is not None
    assert event.duration_ms is not None and event.duration_ms >= 0
    assert event.total_tokens is None
    assert event.token_usage_source == "unavailable"
    assert "hi" not in (event.error_message or "")


def test_langchain_error_creates_error_event(db, parent_model):
    collector = _collector(db)
    model = _TimeoutChatModel()

    with pytest.raises(TimeoutError, match="provider timeout"):
        model.invoke("hi", config=_config(collector, parent_model.id))

    events = _events(db)
    assert len(events) == 1
    event = events[0]
    assert event.status == UsageStatus.ERROR.value
    assert event.error_type == "TimeoutError"
    assert event.error_message is not None
    assert "provider timeout" in event.error_message
    assert event.completed_at is not None


def test_langchain_extracts_provider_reported_token_usage(db, parent_model):
    collector = _collector(db)
    model = _TokenChatModel()

    model.invoke("hi", config=_config(collector, parent_model.id))

    event = _events(db)[0]
    assert event.input_tokens == 843
    assert event.output_tokens == 392
    assert event.total_tokens == 1235
    assert event.token_usage_source == "provider_reported"


def test_missing_model_id_is_dropped_in_best_effort_mode(db, parent_model):
    collector = _collector(db)
    model = FakeListChatModel(responses=["hello"])

    result = model.invoke("hi", config=_config(collector))

    assert result.content == "hello"
    assert _events(db) == []


def test_missing_model_id_raises_in_strict_mode(db, parent_model):
    collector = _collector(db, mode="strict")
    model = FakeListChatModel(responses=["hello"])

    with pytest.raises(UsageContextMissingError):
        model.invoke("hi", config=_config(collector))


def test_best_effort_swallows_telemetry_failures(db, parent_model):
    collector = _collector(db, service=_BrokenService())
    model = FakeListChatModel(responses=["hello"])

    result = model.invoke("hi", config=_config(collector, parent_model.id))

    assert result.content == "hello"
    assert _events(db) == []


def test_strict_mode_propagates_telemetry_failures(db, parent_model):
    collector = _collector(db, mode="strict", service=_BrokenService())
    model = FakeListChatModel(responses=["hello"])

    with pytest.raises(RuntimeError, match="telemetry backend down"):
        model.invoke("hi", config=_config(collector, parent_model.id))


def test_disabled_usage_flag_makes_collector_noop(db, parent_model, monkeypatch):
    monkeypatch.setattr(settings, "usage_enabled", False)
    collector = _collector(db)
    model = FakeListChatModel(responses=["hello"])

    result = model.invoke("hi", config=_config(collector, parent_model.id))

    assert result.content == "hello"
    assert _events(db) == []


def test_concurrent_runs_emit_independent_events(db, parent_model):
    collector = _collector(db)
    model = FakeListChatModel(responses=["a", "b"])

    first = model.invoke("one", config=_config(collector, parent_model.id))
    second = model.invoke("two", config=_config(collector, parent_model.id))

    assert first.content == "a"
    assert second.content == "b"
    events = _events(db)
    assert len(events) == 2
    assert len({event.event_id for event in events}) == 2
    assert len({event.id for event in events}) == 2


def test_replayed_event_id_never_double_counts(db, parent_model):
    collector = _collector(db)
    event_id = str(uuid4())
    model = FakeListChatModel(responses=["hello"])
    config = _config(collector, parent_model.id, auditra_event_id=event_id)

    model.invoke("hi", config=config)
    model.invoke("hi", config=config)

    assert len(_events(db)) == 1
