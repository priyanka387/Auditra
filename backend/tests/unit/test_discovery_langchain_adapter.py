import json

import pytest

from app.modules.model_discovery.enums import DiscoverySourceType
from app.modules.model_discovery.integrations.base import InsufficientMetadataResult
from app.modules.model_discovery.integrations.langchain import (
    AuditraDiscoveryCallback,
    extract_observation,
)
from app.modules.model_discovery.schemas import DiscoveryCreate

LANGCHAIN = DiscoverySourceType.LANGCHAIN


class RecordingSink:
    def __init__(self):
        self.observations: list[DiscoveryCreate] = []
        self.insufficient: list[InsufficientMetadataResult] = []

    def observe(self, payload: DiscoveryCreate) -> None:
        self.observations.append(payload)

    def insufficient_metadata(self, result: InsufficientMetadataResult) -> None:
        self.insufficient.append(result)


def _extract(metadata, serialized=None, *, source=LANGCHAIN, source_identifier=None):
    return extract_observation(
        serialized or {}, metadata, source=source, source_identifier=source_identifier
    )


def test_extract_from_langsmith_tracing_params():
    payload = _extract(
        {"ls_provider": "openai", "ls_model_name": "gpt-5.x", "ls_model_type": "chat"}
    )
    assert isinstance(payload, DiscoveryCreate)
    assert payload.provider == "openai"
    assert payload.model_identifier == "gpt-5.x"
    assert payload.model_type == "llm"
    assert payload.source_type is LANGCHAIN
    assert payload.metadata["framework"] == "langchain"
    assert payload.metadata["framework_version"]


def test_extract_auditra_metadata_overrides_win():
    payload = _extract(
        {
            "ls_provider": "openai",
            "ls_model_name": "gpt-5.x",
            "ls_model_type": "chat",
            "auditra_provider": "anthropic",
            "auditra_model_identifier": "claude-5",
            "auditra_model_type": "embedding",
            "auditra_display_name": "Claude Five",
            "auditra_environment": "prod",
            "auditra_source_identifier": "batch-job",
        },
        source_identifier="ctor-source",
    )
    assert payload.provider == "anthropic"
    assert payload.model_identifier == "claude-5"
    assert payload.model_type == "embedding"
    assert payload.display_name == "Claude Five"
    assert payload.source_identifier == "batch-job"
    assert payload.metadata["environment"] == "prod"


def test_extract_missing_model_name_returns_insufficient():
    result = _extract({"ls_provider": "x"})
    assert isinstance(result, InsufficientMetadataResult)
    assert result.error_code == "INSUFFICIENT_METADATA"
    assert result.source_type == "langchain"


def test_extract_reads_model_class_not_kwargs():
    payload = _extract(
        {"ls_provider": "openai", "ls_model_name": "m"},
        serialized={"id": ["a", "b", "ChatFoo"], "kwargs": {"api_key": "sk-secret"}},
    )
    assert payload.metadata["model_class"] == "ChatFoo"
    dump = json.dumps(payload.model_dump(mode="json"))
    assert "sk-secret" not in dump
    assert "api_key" not in dump


def test_extract_never_contains_prompt_or_secret_material():
    payload = _extract(
        {
            "ls_provider": "openai",
            "ls_model_name": "m",
            "prompt": "SUPER-SECRET-PROMPT",
        }
    )
    dump = json.dumps(payload.model_dump(mode="json"))
    assert "SUPER-SECRET-PROMPT" not in dump


def test_extract_rejects_secret_config_metadata():
    payload = _extract({"ls_provider": "openai", "ls_model_name": "m", "authorization": "Bearer x"})
    dump = json.dumps(payload.model_dump(mode="json"))
    assert "Bearer x" not in dump
    assert "authorization" not in dump


def test_callback_dispatches_observation_to_sink():
    sink = RecordingSink()
    callback = AuditraDiscoveryCallback(sink, source_identifier="support-service")
    callback.on_chat_model_start(
        {},
        [object()],
        run_id="run-1",
        metadata={"ls_provider": "openai", "ls_model_name": "gpt-5.x"},
    )
    assert len(sink.observations) == 1
    payload = sink.observations[0]
    assert payload.provider == "openai"
    assert payload.source_identifier == "support-service"
    assert sink.insufficient == []


def test_callback_dispatches_insufficient_to_sink():
    sink = RecordingSink()
    callback = AuditraDiscoveryCallback(sink)
    callback.on_llm_start({}, ["prompt"], run_id="run-2", metadata={"ls_provider": "x"})
    assert sink.observations == []
    assert len(sink.insufficient) == 1
    assert sink.insufficient[0].error_code == "INSUFFICIENT_METADATA"


def test_callback_swallows_sink_errors_unless_strict():
    class ExplodingSink(RecordingSink):
        def observe(self, payload):
            raise RuntimeError("sink down")

    loose = AuditraDiscoveryCallback(ExplodingSink())
    loose.on_chat_model_start(
        {}, [], run_id="r1", metadata={"ls_provider": "openai", "ls_model_name": "m"}
    )

    strict = AuditraDiscoveryCallback(ExplodingSink(), strict=True)
    with pytest.raises(RuntimeError, match="sink down"):
        strict.on_chat_model_start(
            {}, [], run_id="r2", metadata={"ls_provider": "openai", "ls_model_name": "m"}
        )


def test_extract_langgraph_source_sets_framework():
    payload = _extract(
        {"ls_provider": "openai", "ls_model_name": "m", "langgraph_node": "agent"},
        source=DiscoverySourceType.LANGGRAPH,
    )
    assert payload.source_type is DiscoverySourceType.LANGGRAPH
    assert payload.metadata["framework"] == "langgraph"
    assert payload.metadata["langgraph_node"] == "agent"
