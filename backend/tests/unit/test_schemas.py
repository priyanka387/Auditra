import pytest
from pydantic import ValidationError

from app.modules.model_inventory.schemas import (
    ModelCreate,
    ModelUpdate,
    parse_tags,
    validate_metadata,
)


def test_create_minimal_ok():
    model = ModelCreate(
        provider_slug="openai",
        model_type_slug="llm",
        name="Customer Support LLM",
        native_model_id="gpt-4o",
    )
    assert model.lifecycle_state == "REGISTERED"
    assert model.source_type == "MANUAL"
    assert model.tags == []
    assert model.metadata == {}


def test_empty_name_rejected():
    with pytest.raises(ValidationError):
        ModelCreate(
            provider_slug="openai",
            model_type_slug="llm",
            name="",
            native_model_id="gpt-4o",
        )


def test_secret_metadata_key_rejected():
    with pytest.raises(ValueError):
        validate_metadata({"api_key": "x"})
    with pytest.raises(ValueError):
        ModelCreate(
            provider_slug="openai",
            model_type_slug="llm",
            name="Customer Support LLM",
            native_model_id="gpt-4o",
            metadata={"api_key": "x"},
        )


def test_nested_secret_metadata_rejected():
    with pytest.raises(ValueError):
        validate_metadata({"config": {"api_key": "x"}})
    with pytest.raises(ValueError):
        validate_metadata({"servers": [{"secret_token": "x"}]})
    with pytest.raises(ValueError):
        ModelCreate(
            provider_slug="openai",
            model_type_slug="llm",
            name="Customer Support LLM",
            native_model_id="gpt-4o",
            metadata={"config": {"api_key": "x"}},
        )
    assert validate_metadata({"config": {"region": "eu"}}) is None


def test_oversized_metadata_rejected():
    padded = {"pad": "x" * 10_001}
    with pytest.raises(ValueError):
        validate_metadata(padded)
    with pytest.raises(ValueError):
        ModelCreate(
            provider_slug="openai",
            model_type_slug="llm",
            name="Customer Support LLM",
            native_model_id="gpt-4o",
            metadata=padded,
        )


def test_bad_source_type_rejected():
    with pytest.raises(ValidationError):
        ModelCreate(
            provider_slug="openai",
            model_type_slug="llm",
            name="Customer Support LLM",
            native_model_id="gpt-4o",
            source_type="WHATEVER",
        )


def test_create_archived_rejected():
    with pytest.raises(ValidationError):
        ModelCreate(
            provider_slug="openai",
            model_type_slug="llm",
            name="Customer Support LLM",
            native_model_id="gpt-4o",
            lifecycle_state="ARCHIVED",
        )


def test_parse_tags_key_value():
    assert parse_tags(["a", "b=c"]) == [("a", ""), ("b", "c")]


def test_parse_tags_empty_key_rejected():
    with pytest.raises(ValueError):
        parse_tags(["=x"])


def test_parse_tags_oversized_key_rejected():
    with pytest.raises(ValueError):
        parse_tags(["k" * 129])


def test_parse_tags_oversized_value_rejected():
    with pytest.raises(ValueError):
        parse_tags(["k=" + "v" * 513])


def test_create_oversized_tag_rejected():
    with pytest.raises(ValidationError):
        ModelCreate(
            provider_slug="openai",
            model_type_slug="llm",
            name="Customer Support LLM",
            native_model_id="gpt-4o",
            tags=["k" * 200],
        )


def test_update_accepts_identity_fields():
    update = ModelUpdate(provider_slug="openai", native_model_id="x")
    assert update.provider_slug == "openai"
    assert update.native_model_id == "x"


def test_extra_field_forbidden():
    with pytest.raises(ValidationError):
        ModelCreate(
            provider_slug="openai",
            model_type_slug="llm",
            name="Customer Support LLM",
            native_model_id="gpt-4o",
            bogus=1,
        )


def test_update_extra_field_forbidden():
    with pytest.raises(ValidationError):
        ModelUpdate(bogus=1)
