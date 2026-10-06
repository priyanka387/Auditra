from datetime import UTC, datetime
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult, LLMResult

from app.core.config import settings
from app.modules.model_usage.errors import UsageContextMissingError
from app.modules.model_usage.integrations.langchain import extract_token_usage
from app.modules.model_usage.schemas import UsageEventCreate
from app.modules.model_usage.telemetry import NoopCollector, ServiceUsageCollector

NOW = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)


def _event():
    return UsageEventCreate(
        event_id="evt-1",
        model_id=uuid4(),
        source="api",
        started_at=NOW,
    )


def test_extract_from_message_usage_metadata():
    message = AIMessage(
        content="ok",
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )
    assert extract_token_usage(message) == (10, 5, 15)


def test_extract_from_chat_generation_message():
    message = AIMessage(
        content="ok",
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )
    result = ChatResult(generations=[ChatGeneration(message=message)])
    assert extract_token_usage(result) == (10, 5, 15)


def test_extract_from_llm_output_token_usage():
    from types import SimpleNamespace

    fake = SimpleNamespace(
        llm_output={
            "token_usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
        },
        generations=[],
    )
    assert extract_token_usage(fake) == (7, 3, 10)


def test_extract_from_nested_generations():
    message = AIMessage(
        content="ok",
        usage_metadata={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
    )
    result = LLMResult(generations=[[ChatGeneration(message=message)]])
    assert extract_token_usage(result) == (1, 2, 3)


def test_extract_returns_none_when_usage_absent():
    assert extract_token_usage(AIMessage(content="plain")) is None
    assert extract_token_usage(LLMResult(generations=[])) is None


class _RecordingService:
    def __init__(self):
        self.events = []

    def record_event(self, event):
        self.events.append(event)


class _FailingService:
    def record_event(self, event):
        raise RuntimeError("backend down")


def test_collector_emits_through_service():
    service = _RecordingService()
    ServiceUsageCollector(service).emit(_event())
    assert len(service.events) == 1


def test_collector_best_effort_swallows_failures():
    ServiceUsageCollector(_FailingService(), mode="best_effort").emit(_event())


def test_collector_strict_propagates_failures():
    with pytest.raises(RuntimeError, match="backend down"):
        ServiceUsageCollector(_FailingService(), mode="strict").emit(_event())


def test_collector_disabled_flag_drops_events(monkeypatch):
    monkeypatch.setattr(settings, "usage_enabled", False)
    service = _RecordingService()
    ServiceUsageCollector(service).emit(_event())
    assert service.events == []


def test_noop_collector_drops_events():
    service = _RecordingService()
    NoopCollector().emit(_event())
    assert service.events == []


def test_context_missing_error_is_domain_error():
    error = UsageContextMissingError("missing")
    assert error.code == "USAGE_MODEL_ID_MISSING"
    assert error.http_status == 422
