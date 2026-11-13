"""Unit tests: deterministic asset classification and metadata normalization (Sub-feature 5)."""

from uuid import uuid4

import pytest

from app.modules.asset_discovery.classification import (
    CLASSIFICATION_RULES,
    ClassificationResult,
    ClassificationRule,
    classify,
    normalize_asset_name,
    normalize_framework,
    normalize_provider_slug,
    split_provider_prefix,
)
from app.modules.asset_discovery.connectors.base import (
    AGENT_REFERENCE,
    FRAMEWORK_REFERENCE,
    MODEL_REFERENCE,
    PROVIDER_REFERENCE,
    WORKFLOW_REFERENCE,
    DiscoveryObservation,
)
from app.modules.asset_discovery.enums import AssetType
from app.modules.asset_discovery.errors import InvalidDiscoveredAssetError


def _observation(**overrides) -> DiscoveryObservation:
    payload = {
        "source_id": uuid4(),
        "run_id": uuid4(),
        "observation_type": MODEL_REFERENCE,
        "name": "gpt-4o",
        "provider": None,
        "file_path": "app/llm.py",
        "line_number": 2,
        "detection_rule": "model.assignment",
        "metadata": {"key": "model"},
    }
    payload.update(overrides)
    return DiscoveryObservation(**payload)


# --- classification of the observable asset types ---------------------------


def test_classifies_model_reference():
    result = classify(_observation())
    assert result.asset_type is AssetType.MODEL_REFERENCE
    assert result.name == "gpt-4o"
    assert result.rule_id == "type.model_reference"
    assert result.metadata["model_id"] == "gpt-4o"
    assert "provider" not in result.metadata


def test_classifies_provider_reference_as_ai_service():
    result = classify(
        _observation(
            observation_type=PROVIDER_REFERENCE,
            name="openai",
            provider="openai",
            detection_rule="provider.openai.import",
            metadata={},
        )
    )
    assert result.asset_type is AssetType.AI_SERVICE
    assert result.rule_id == "type.provider_reference"
    assert result.metadata["provider"] == "openai"


def test_classifies_framework_reference_as_tool():
    result = classify(
        _observation(
            observation_type=FRAMEWORK_REFERENCE,
            name="langchain_openai",
            detection_rule="framework.langchain.import",
            metadata={"framework": "langchain"},
        )
    )
    assert result.asset_type is AssetType.TOOL
    assert result.rule_id == "type.framework_reference"
    assert result.metadata["framework"] == "langchain"


def test_classifies_agent_reference():
    result = classify(
        _observation(
            observation_type=AGENT_REFERENCE,
            name="create_react_agent",
            detection_rule="agent.langchain.create_react_agent",
            metadata={"framework": "langchain"},
        )
    )
    assert result.asset_type is AssetType.AGENT
    assert result.rule_id == "type.agent_reference"
    assert result.metadata["framework"] == "langchain"


def test_classifies_workflow_reference():
    result = classify(
        _observation(
            observation_type=WORKFLOW_REFERENCE,
            name="StateGraph",
            detection_rule="workflow.langgraph.state_graph",
            metadata={"framework": "langgraph"},
        )
    )
    assert result.asset_type is AssetType.WORKFLOW
    assert result.rule_id == "type.workflow_reference"
    assert result.metadata["framework"] == "langgraph"


def test_rules_cover_exactly_the_types_current_signals_produce():
    # MODEL, APPLICATION, DATASET, DEPLOYMENT and ENDPOINT have no
    # detection signal yet; classification must not invent them
    assert {rule.asset_type for rule in CLASSIFICATION_RULES} == {
        AssetType.MODEL_REFERENCE,
        AssetType.AI_SERVICE,
        AssetType.TOOL,
        AssetType.AGENT,
        AssetType.WORKFLOW,
    }


def test_unknown_observation_type_raises_instead_of_guessing():
    with pytest.raises(InvalidDiscoveredAssetError):
        classify(_observation(observation_type="mystery_signal"))


def test_rules_are_ordered_most_specific_first():
    # precedence contract: structural signals (agent, workflow) are
    # evaluated before generic library/provider signals
    assert [rule.rule_id for rule in CLASSIFICATION_RULES] == [
        "type.agent_reference",
        "type.workflow_reference",
        "type.model_reference",
        "type.framework_reference",
        "type.provider_reference",
    ]


def test_rule_ids_and_asset_types_are_valid_and_unique():
    rule_ids = [rule.rule_id for rule in CLASSIFICATION_RULES]
    assert len(rule_ids) == len(set(rule_ids))
    for rule in CLASSIFICATION_RULES:
        assert isinstance(rule, ClassificationRule)
        assert isinstance(rule.asset_type, AssetType)
        assert rule.rule_id.startswith("type.")


# --- conflicting signals and deterministic precedence -----------------------


def test_agent_signal_wins_over_framework_signal():
    # metadata suggesting a library must not reclassify an agent
    result = classify(
        _observation(
            observation_type=AGENT_REFERENCE,
            name="create_react_agent",
            metadata={"framework": "langchain"},
        )
    )
    assert result.asset_type is AssetType.AGENT


def test_workflow_signal_wins_over_framework_signal():
    result = classify(
        _observation(
            observation_type=WORKFLOW_REFERENCE,
            name="StateGraph",
            metadata={"framework": "langgraph"},
        )
    )
    assert result.asset_type is AssetType.WORKFLOW


def test_provider_signal_wins_over_stray_framework_metadata():
    result = classify(
        _observation(
            observation_type=PROVIDER_REFERENCE,
            name="openai",
            provider="openai",
            metadata={"framework": "langchain"},
        )
    )
    # the stray framework value does not reclassify the observation; it
    # is preserved verbatim by catalog metadata merging instead
    assert result.asset_type is AssetType.AI_SERVICE
    assert "framework" not in result.metadata


def test_classification_is_deterministic():
    observation = _observation(
        observation_type=FRAMEWORK_REFERENCE,
        name="langgraph",
        metadata={"framework": "langgraph"},
    )
    assert classify(observation) == classify(observation)


# --- name and metadata normalization ---------------------------------------


def test_name_is_normalized():
    assert classify(_observation(name="  gpt   4o  ")).name == "gpt 4o"


def test_model_id_preserves_case():
    assert classify(_observation(name="GPT-4o")).metadata["model_id"] == "GPT-4o"


def test_provider_qualified_model_id_is_split():
    result = classify(_observation(name="openai:gpt-4o"))
    assert result.metadata == {"model_id": "gpt-4o", "provider": "openai"}


def test_provider_qualified_split_is_case_insensitive_on_prefix():
    result = classify(_observation(name="OpenAI:gpt-4o"))
    assert result.metadata == {"model_id": "gpt-4o", "provider": "openai"}


def test_unknown_provider_prefix_is_not_split():
    result = classify(_observation(name="acme:gpt-4o"))
    assert result.metadata == {"model_id": "acme:gpt-4o"}
    assert "provider" not in result.metadata


def test_display_name_is_not_fuzzily_split():
    # "OpenAI GPT-4o" carries no unambiguous delimiter; preserve it whole
    result = classify(_observation(name="OpenAI GPT-4o"))
    assert result.metadata == {"model_id": "OpenAI GPT-4o"}
    assert "provider" not in result.metadata


def test_provider_metadata_is_normalized():
    result = classify(
        _observation(
            observation_type=PROVIDER_REFERENCE,
            name="openai",
            provider="  OpenAI ",
            metadata={},
        )
    )
    assert result.metadata["provider"] == "openai"


def test_framework_metadata_is_normalized():
    result = classify(
        _observation(
            observation_type=FRAMEWORK_REFERENCE,
            name="langchain",
            metadata={"framework": " LangChain "},
        )
    )
    assert result.metadata["framework"] == "langchain"


def test_classification_metadata_keys_are_allowlisted():
    # classification must never persist arbitrary rule content or source
    # material: only normalized identity/location signals are produced
    allowed = {"model_id", "provider", "framework"}
    observations = [
        _observation(),
        _observation(observation_type=PROVIDER_REFERENCE, provider="openai", metadata={}),
        _observation(observation_type=FRAMEWORK_REFERENCE, metadata={"framework": "langchain"}),
        _observation(observation_type=AGENT_REFERENCE, metadata={"framework": "langchain"}),
        _observation(observation_type=WORKFLOW_REFERENCE, metadata={"framework": "langgraph"}),
    ]
    for observation in observations:
        assert set(classify(observation).metadata) <= allowed


# --- normalization helpers --------------------------------------------------


def test_normalize_asset_name_collapses_whitespace():
    assert normalize_asset_name("  a \t b  ") == "a b"
    assert normalize_asset_name("") == ""


def test_normalize_provider_slug():
    assert normalize_provider_slug(" OpenAI ") == "openai"
    assert normalize_provider_slug(None) is None
    assert normalize_provider_slug("   ") is None


def test_normalize_framework():
    assert normalize_framework(" LangChain ") == "langchain"
    assert normalize_framework(None) is None
    assert normalize_framework("") is None


def test_split_provider_prefix():
    assert split_provider_prefix("openai:gpt-4o") == ("openai", "gpt-4o")
    assert split_provider_prefix("OPENAI:gpt-4o") == ("openai", "gpt-4o")
    assert split_provider_prefix("gpt-4o") == (None, "gpt-4o")
    assert split_provider_prefix("acme:gpt-4o") == (None, "acme:gpt-4o")
    assert split_provider_prefix("openai:") == (None, "openai:")
    assert split_provider_prefix("anthropic:claude:3") == ("anthropic", "claude:3")


def test_classify_result_is_a_dataclass_result():
    result = classify(_observation())
    assert isinstance(result, ClassificationResult)
    assert isinstance(result.asset_type, AssetType)
    assert isinstance(result.metadata, dict)
    assert isinstance(result.rule_id, str)
