from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.model_discovery.enums import DiscoverySourceType, DiscoveryStatus
from app.modules.model_discovery.schemas import (
    DiscoveryCreate,
    DiscoveryFilter,
    DiscoveryIgnoreRequest,
    DiscoveryListResponse,
    DiscoveryMatchRequest,
    DiscoveryRegisterRequest,
    DiscoveryResponse,
)


def _create(**overrides) -> DiscoveryCreate:
    payload = {
        "source_type": "manual",
        "provider": "openai",
        "model_identifier": "gpt-5.x",
    }
    payload.update(overrides)
    return DiscoveryCreate.model_validate(payload)


def test_create_strips_and_accepts_valid_payload():
    payload = _create(
        source_identifier="  platform-team ",
        provider="  OpenAI ",
        model_identifier=" gpt-5.x ",
        model_type="llm",
        display_name="OpenAI GPT model",
        metadata={"environment": "development"},
    )
    assert payload.source_type is DiscoverySourceType.MANUAL
    assert payload.source_identifier == "platform-team"
    assert payload.provider == "OpenAI"
    assert payload.model_identifier == "gpt-5.x"
    assert payload.metadata == {"environment": "development"}


def test_create_blank_provider_rejected():
    with pytest.raises(ValidationError):
        _create(provider="   ")
    with pytest.raises(ValidationError):
        _create(provider="")


def test_create_blank_model_identifier_rejected():
    with pytest.raises(ValidationError):
        _create(model_identifier="  ")


def test_create_secret_metadata_key_rejected():
    with pytest.raises(ValidationError):
        _create(metadata={"api_key": "sk-123"})
    with pytest.raises(ValidationError):
        _create(metadata={"nested": {"password": "hunter2"}})


def test_create_oversized_metadata_rejected():
    with pytest.raises(ValidationError):
        _create(metadata={"blob": "x" * 11_000})


def test_create_forbids_extra_fields():
    with pytest.raises(ValidationError):
        DiscoveryCreate.model_validate(
            {"source_type": "manual", "provider": "openai", "model_identifier": "m", "status": "MATCHED"}
        )


def test_create_invalid_source_type_rejected():
    with pytest.raises(ValidationError):
        _create(source_type="sweep")


def _stub(**overrides):
    base = {
        "id": uuid4(),
        "source_type": "manual",
        "source_identifier": "platform-team",
        "external_identifier": None,
        "provider": "openai",
        "model_identifier": "gpt-5.x",
        "model_type": "llm",
        "display_name": "OpenAI GPT model",
        "canonical_identity": "openai|gpt-5.x",
        "metadata_": {"environment": "development"},
        "status": "UNRESOLVED",
        "matched_model_id": None,
        "observation_count": 1,
        "first_seen_at": datetime(2026, 10, 7, tzinfo=UTC),
        "last_seen_at": datetime(2026, 10, 7, tzinfo=UTC),
        "created_at": datetime(2026, 10, 7, tzinfo=UTC),
        "updated_at": datetime(2026, 10, 7, tzinfo=UTC),
        "error_code": None,
        "error_message": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_response_parses_orm_attributes():
    response = DiscoveryResponse.model_validate(_stub(), from_attributes=True)
    assert response.status is DiscoveryStatus.UNRESOLVED
    assert response.source_type is DiscoverySourceType.MANUAL
    assert response.metadata == {"environment": "development"}
    assert response.canonical_identity == "openai|gpt-5.x"
    assert response.matched_model_id is None


def test_response_rejects_naive_timestamps():
    with pytest.raises(ValidationError):
        DiscoveryResponse.model_validate(
            _stub(first_seen_at=datetime(2026, 10, 7)), from_attributes=True
        )


def test_list_response_computes_pagination_fields():
    listing = DiscoveryListResponse(
        items=[], page=2, page_size=25, total=30, total_pages=2
    )
    assert listing.total_pages == 2 and listing.page == 2


def test_filter_defaults_and_sort_allow_list():
    filters = DiscoveryFilter()
    assert filters.page == 1 and filters.page_size == 25
    assert filters.sort_by == "last_seen_at" and filters.sort_order == "desc"
    with pytest.raises(ValidationError):
        DiscoveryFilter(sort_by="canonical_identity")


def test_empty_action_request_bodies_forbid_extra_fields():
    for request_cls in (DiscoveryMatchRequest, DiscoveryIgnoreRequest):
        with pytest.raises(ValidationError):
            request_cls.model_validate({"status": "IGNORED"})
    assert DiscoveryRegisterRequest().model_type_slug is None
    assert DiscoveryRegisterRequest(name="Custom").name == "Custom"
