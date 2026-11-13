"""Unit tests: Discovered Asset Catalog identity, mapping and metadata."""

from uuid import uuid4

import pytest

from app.modules.asset_discovery.catalog import (
    ASSET_TYPE_BY_OBSERVATION,
    asset_type_for_observation,
    build_asset_metadata,
    build_normalized_identity,
    has_secret_shaped_value,
    normalize_asset_name,
    validate_file_path,
)
from app.modules.asset_discovery.connectors.base import DiscoveryObservation
from app.modules.asset_discovery.enums import AssetType
from app.modules.asset_discovery.errors import InvalidDiscoveredAssetError


def _observation(**overrides) -> DiscoveryObservation:
    payload = {
        "source_id": uuid4(),
        "run_id": uuid4(),
        "observation_type": "model_reference",
        "name": "gpt-4o",
        "provider": None,
        "file_path": "app/llm.py",
        "line_number": 2,
        "detection_rule": "model.assignment",
        "metadata": {"key": "model"},
    }
    payload.update(overrides)
    return DiscoveryObservation(**payload)


def test_initial_asset_types_match_specification():
    assert {asset.value for asset in AssetType} == {
        "MODEL",
        "MODEL_REFERENCE",
        "APPLICATION",
        "AGENT",
        "WORKFLOW",
        "DATASET",
        "TOOL",
        "DEPLOYMENT",
        "ENDPOINT",
        "AI_SERVICE",
    }


def test_observation_mapping_covers_connector_observation_types():
    assert ASSET_TYPE_BY_OBSERVATION["model_reference"] is AssetType.MODEL_REFERENCE
    assert ASSET_TYPE_BY_OBSERVATION["provider_reference"] is AssetType.AI_SERVICE
    assert ASSET_TYPE_BY_OBSERVATION["framework_reference"] is AssetType.TOOL
    assert ASSET_TYPE_BY_OBSERVATION["agent_reference"] is AssetType.AGENT
    assert ASSET_TYPE_BY_OBSERVATION["workflow_reference"] is AssetType.WORKFLOW


def test_unknown_observation_type_raises_instead_of_guessing():
    with pytest.raises(InvalidDiscoveredAssetError):
        asset_type_for_observation("model_deployment")
    assert asset_type_for_observation("model_reference") is AssetType.MODEL_REFERENCE


def test_identity_is_deterministic():
    first = build_normalized_identity(AssetType.MODEL_REFERENCE, "gpt-4o")
    second = build_normalized_identity("MODEL_REFERENCE", "gpt-4o")
    assert first == second == "MODEL_REFERENCE|gpt-4o"


def test_identity_separates_asset_types():
    model = build_normalized_identity(AssetType.MODEL, "gpt-4o")
    reference = build_normalized_identity(AssetType.MODEL_REFERENCE, "gpt-4o")
    assert model != reference


def test_identity_normalizes_whitespace_but_preserves_case():
    assert build_normalized_identity(AssetType.MODEL, "  gpt   4o ") == "MODEL|gpt 4o"
    # case-folding would pre-empt Sub-feature 6 deduplication rules
    assert build_normalized_identity(AssetType.MODEL, "GPT-4o") != build_normalized_identity(
        AssetType.MODEL, "gpt-4o"
    )


def test_identity_rejects_empty_name_and_unknown_type():
    with pytest.raises(InvalidDiscoveredAssetError):
        build_normalized_identity(AssetType.MODEL, "   ")
    with pytest.raises(InvalidDiscoveredAssetError):
        build_normalized_identity("NOT_A_TYPE", "gpt-4o")


def test_normalize_asset_name_collapses_whitespace():
    assert normalize_asset_name("  a \t b  ") == "a b"
    assert normalize_asset_name("") == ""


def test_validate_file_path_accepts_relative_posix_path():
    assert validate_file_path("app/llm/client.py") == "app/llm/client.py"


@pytest.mark.parametrize(
    "file_path",
    ["", "   ", "/etc/passwd", "C:\\win\\path", "..\\escape", "a/../../b", "app\x00.py"],
)
def test_validate_file_path_rejects_hostile_paths(file_path):
    with pytest.raises(InvalidDiscoveredAssetError):
        validate_file_path(file_path)


def test_validate_file_path_rejects_oversized_path():
    with pytest.raises(InvalidDiscoveredAssetError):
        validate_file_path("a/" * 600 + "b.py")


def test_metadata_combines_structural_and_rule_fields():
    metadata = build_asset_metadata(
        _observation(provider="openai", metadata={"framework": "langchain"})
    )
    assert metadata == {
        "framework": "langchain",
        "observation_type": "model_reference",
        "file_path": "app/llm.py",
        "detection_rule": "model.assignment",
        "line_number": 2,
        "provider": "openai",
    }


def test_metadata_omits_provider_and_line_when_absent():
    metadata = build_asset_metadata(_observation(provider=None, line_number=None))
    assert "provider" not in metadata
    assert "line_number" not in metadata


def test_metadata_structural_keys_cannot_be_overridden_by_rules():
    metadata = build_asset_metadata(
        _observation(metadata={"file_path": "bogus/path", "detection_rule": "bogus"})
    )
    assert metadata["file_path"] == "app/llm.py"
    assert metadata["detection_rule"] == "model.assignment"


@pytest.mark.parametrize("line_number", [0, -1, True, "3", 10_000_001])
def test_metadata_rejects_invalid_line_numbers(line_number):
    with pytest.raises(InvalidDiscoveredAssetError):
        build_asset_metadata(_observation(line_number=line_number))


def test_metadata_rejects_secret_like_rule_keys():
    with pytest.raises(InvalidDiscoveredAssetError):
        build_asset_metadata(_observation(metadata={"api_key": "hunter2"}))


def test_metadata_rejects_oversized_payload():
    with pytest.raises(InvalidDiscoveredAssetError):
        build_asset_metadata(_observation(metadata={"blob": "x" * 11_000}))


def test_metadata_rejects_unserializable_values():
    with pytest.raises(InvalidDiscoveredAssetError):
        build_asset_metadata(_observation(metadata={"when": object()}))


def test_secret_shaped_names_are_detected():
    assert has_secret_shaped_value("sk-proj-abc123def456", _observation())
    assert has_secret_shaped_value("OPENAI_API_KEY", _observation())
    assert has_secret_shaped_value("ghp_AbCdEf123456", _observation())
    assert not has_secret_shaped_value("gpt-4o", _observation())
    assert not has_secret_shaped_value("langchain", _observation())


def test_env_var_name_metadata_value_is_not_treated_as_secret():
    observation = _observation(metadata={"env_var": "OPENAI_API_KEY"})
    assert not has_secret_shaped_value("openai", observation)


def test_secret_shaped_rule_metadata_value_is_detected():
    observation = _observation(metadata={"model": "sk-proj-abc123def456"})
    assert has_secret_shaped_value("openai", observation)


def test_file_path_and_rule_are_never_secret_scanned():
    # a path segment must not trigger the guard (it is validated, not a value)
    observation = _observation(file_path="sk-upload/README.md")
    assert not has_secret_shaped_value("readme", observation)
