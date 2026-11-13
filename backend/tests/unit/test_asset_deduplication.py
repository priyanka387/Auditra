"""Unit tests: deterministic deduplication identity, matching, and merging."""

from dataclasses import replace
from itertools import combinations
from uuid import uuid4

import pytest

from app.modules.asset_discovery.classification import ClassificationResult
from app.modules.asset_discovery.deduplication import (
    build_key,
    is_duplicate,
    merge_metadata,
    resolve_identity,
)
from app.modules.asset_discovery.enums import AssetType
from app.modules.asset_discovery.errors import InvalidDiscoveredAssetError
from app.modules.asset_discovery.models import DiscoveredAsset

TENANT = uuid4()
OTHER_TENANT = uuid4()
SOURCE = uuid4()
OTHER_SOURCE = uuid4()


def _classification(
    name: str = "gpt-4o",
    asset_type=AssetType.MODEL_REFERENCE,
    metadata=None,
) -> ClassificationResult:
    return ClassificationResult(
        asset_type=asset_type,
        name=name,
        metadata=dict(metadata or {}),
        rule_id="type.model_reference",
    )


def _asset_for(key):
    """A detached catalog asset that matches ``key`` exactly."""
    return DiscoveredAsset(
        tenant_id=key.tenant_id,
        source_id=key.source_id,
        asset_type=key.asset_type,
        normalized_identity=key.normalized_identity,
    )


def test_resolve_identity_is_deterministic():
    classification = _classification()
    assert resolve_identity(classification) == "MODEL_REFERENCE|gpt-4o"
    assert resolve_identity(classification) == resolve_identity(classification)


def test_resolve_identity_includes_asset_type():
    model = resolve_identity(_classification(asset_type=AssetType.MODEL))
    reference = resolve_identity(_classification(asset_type=AssetType.MODEL_REFERENCE))
    assert model == "MODEL|gpt-4o"
    assert model != reference


def test_resolve_identity_normalizes_whitespace():
    assert resolve_identity(_classification(name="  gpt-4o  ")) == "MODEL_REFERENCE|gpt-4o"


def test_resolve_identity_rejects_empty_name():
    with pytest.raises(InvalidDiscoveredAssetError):
        resolve_identity(_classification(name="   "))


def test_resolve_identity_rejects_unknown_type():
    with pytest.raises(InvalidDiscoveredAssetError):
        resolve_identity(_classification(asset_type="THING"))


def test_identity_ignores_location_and_metadata():
    # identity is derived from type + normalized name only: where the
    # asset was observed and what else classification extracted never
    # change whether two observations are the same asset
    at_a = resolve_identity(
        _classification(metadata={"file_path": "app/a.py", "framework": "langchain"})
    )
    at_b = resolve_identity(
        _classification(metadata={"file_path": "app/b.py", "framework": "llamaindex"})
    )
    assert at_a == at_b == "MODEL_REFERENCE|gpt-4o"


def test_different_names_sharing_a_path_get_different_identities():
    # location never merges two different names into one identity
    first = resolve_identity(_classification(name="model-a"))
    second = resolve_identity(_classification(name="model-b"))
    assert first != second


def test_build_key_scopes_to_source_and_tenant():
    classification = _classification()
    key = build_key(TENANT, SOURCE, classification)

    assert key == build_key(TENANT, SOURCE, _classification())
    assert key.tenant_id == TENANT
    assert key.source_id == SOURCE
    assert key.asset_type == AssetType.MODEL_REFERENCE.value
    assert key.normalized_identity == "MODEL_REFERENCE|gpt-4o"
    # same identity string in another source or tenant is a different key
    assert build_key(TENANT, OTHER_SOURCE, classification) != key
    assert build_key(OTHER_TENANT, SOURCE, classification) != key


def test_is_duplicate_requires_exact_match():
    key = build_key(TENANT, SOURCE, _classification())
    asset = _asset_for(key)

    assert is_duplicate(asset, key) is True
    assert is_duplicate(None, key) is False
    assert is_duplicate(asset, replace(key, tenant_id=uuid4())) is False
    assert is_duplicate(asset, replace(key, source_id=uuid4())) is False
    assert is_duplicate(asset, replace(key, asset_type=AssetType.TOOL.value)) is False
    assert is_duplicate(asset, replace(key, normalized_identity="MODEL_REFERENCE|other")) is False
    # mismatches on the asset side are rejected symmetrically
    assert is_duplicate(_asset_for(replace(key, source_id=OTHER_SOURCE)), key) is False


def test_is_duplicate_rejects_similar_names():
    names = ("gpt-4o", "gpt-4o-mini", "GPT-4o")
    keys = [build_key(TENANT, SOURCE, _classification(name)) for name in names]
    assets = [_asset_for(key) for key in keys]

    for i, j in combinations(range(len(keys)), 2):
        assert is_duplicate(assets[i], keys[j]) is False


def test_merge_metadata_preserves_existing_non_empty():
    merged, changed = merge_metadata({"a": "1"}, {"a": "2"})
    assert merged == {"a": "1"}
    assert changed is False


def test_merge_metadata_fills_empty_existing_values():
    merged, changed = merge_metadata({"a": ""}, {"a": "x"})
    assert merged == {"a": "x"}
    assert changed is True

    merged, changed = merge_metadata({"a": None}, {"a": "x"})
    assert merged == {"a": "x"}
    assert changed is True


def test_merge_metadata_adds_new_keys():
    merged, changed = merge_metadata({"a": "1"}, {"b": 2})
    assert merged == {"a": "1", "b": 2}
    assert changed is True


def test_merge_metadata_incoming_empty_never_erases():
    merged, changed = merge_metadata({"a": "1"}, {"a": ""})
    assert merged == {"a": "1"}
    assert changed is False


def test_merge_metadata_handles_none_arguments():
    assert merge_metadata(None, None) == ({}, False)
    assert merge_metadata(None, {"a": 1}) == ({"a": 1}, True)
    assert merge_metadata({"a": 1}, None) == ({"a": 1}, False)


def test_merge_metadata_does_not_mutate_inputs():
    existing = {"a": "", "b": "keep"}
    incoming = {"a": "new", "c": "extra"}
    existing_copy = dict(existing)
    incoming_copy = dict(incoming)

    merged, _ = merge_metadata(existing, incoming)

    assert existing == existing_copy
    assert incoming == incoming_copy
    assert merged == {"a": "new", "b": "keep", "c": "extra"}
