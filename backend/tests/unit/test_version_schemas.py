import pytest
from pydantic import ValidationError

from app.modules.model_inventory.schemas import (
    ModelVersionCreate,
    ModelVersionLifecycleUpdate,
    ModelVersionUpdate,
)


def _create(**over):
    base = {
        "identity_type": "release",
        "version_label": "v2.1.0",
        "native_version_id": "release-210",
    }
    base.update(over)
    return ModelVersionCreate(**base)


def test_create_minimal_defaults():
    m = _create()
    assert m.source_type == "manual" and m.metadata == {} and m.native_version_id == "release-210"


def test_create_normalizes_identity_type_and_source_type():
    assert (
        _create(identity_type=" Native ", version_label="x", native_version_id=None).identity_type
        == "native"
    )
    assert _create(source_type="MANUAL").source_type == "manual"


def test_unknown_identity_type_rejected():
    with pytest.raises(ValidationError):
        _create(identity_type="semver")


def test_unknown_source_type_rejected():
    with pytest.raises(ValidationError):
        _create(source_type="sdk")


def test_empty_version_label_rejected():
    with pytest.raises(ValidationError):
        _create(version_label="   ", native_version_id=None)


def test_whitespace_native_version_id_rejected():
    with pytest.raises(ValidationError):
        _create(native_version_id="  ")


def test_create_server_owned_fields_forbidden():
    for field in ("lifecycle_state", "canonical_version_key", "record_version", "tenant_id"):
        with pytest.raises(ValidationError):
            _create(**{field: "x"})


def test_secret_and_oversized_metadata_rejected():
    with pytest.raises(ValueError):
        _create(metadata={"api_key": "x"})
    with pytest.raises(ValueError):
        _create(metadata={"blob": "x" * 10_001})


def test_nested_secret_metadata_rejected():
    with pytest.raises(ValueError):
        _create(metadata={"config": {"api_key": "x"}})
    assert ModelVersionCreate(
        identity_type="release",
        version_label="v1",
        metadata={"config": {"region": "eu"}},
    ).metadata == {"config": {"region": "eu"}}


def test_update_accepts_identity_fields_for_explicit_rejection():
    u = ModelVersionUpdate(
        model_id=None,
        identity_type="native",
        version_label="v1",
        native_version_id="n",
        canonical_version_key="native:n",
    )
    assert u.identity_type == "native"


def test_update_mutable_fields_and_extra_forbidden():
    u = ModelVersionUpdate(
        display_name="Friendly", description="d", metadata={"a": 1}, source_reference="ref"
    )
    assert u.display_name == "Friendly"
    with pytest.raises(ValidationError):
        ModelVersionUpdate(lifecycle_state="ACTIVE")
    with pytest.raises(ValidationError):
        ModelVersionUpdate(bogus=1)


def test_lifecycle_target_state_validated():
    assert ModelVersionLifecycleUpdate(target_state="RETIRED").target_state == "RETIRED"
    with pytest.raises(ValidationError):
        ModelVersionLifecycleUpdate(target_state="NOPE")
