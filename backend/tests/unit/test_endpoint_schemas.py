import pytest
from pydantic import ValidationError

from app.modules.model_inventory.domain.errors import (
    InvalidAuthReferenceError,
    InvalidEndpointUrlError,
)
from app.modules.model_inventory.schemas import (
    DeploymentEndpointCreate,
    DeploymentEndpointUpdate,
    validate_auth_reference,
    validate_endpoint_url,
)


def _payload(**overrides):
    payload = {
        "name": "primary",
        "endpoint_type": "inference",
        "protocol": "https",
        "url": "https://fraud.example.com/v1/infer",
        "auth_type": "external_secret",
        "auth_reference": "secret://prod/fraud",
        "is_primary": True,
    }
    payload.update(overrides)
    return payload


def test_create_accepts_valid_payload():
    endpoint = DeploymentEndpointCreate.model_validate(_payload())
    assert endpoint.is_primary is True
    assert endpoint.status == "active"
    assert endpoint.health_status == "unknown"
    assert endpoint.metadata == {}


def test_create_rejects_unknown_enum_values():
    for field, value in (
        ("endpoint_type", "metrics"),
        ("protocol", "ftp"),
        ("auth_type", "oauth"),
        ("status", "degraded"),
        ("health_status", "down"),
    ):
        with pytest.raises(ValidationError):
            DeploymentEndpointCreate.model_validate(_payload(**{field: value}))


def test_create_rejects_immutable_and_unknown_fields():
    for field, value in (
        ("deployment_id", "x"),
        ("endpoint_type_override", "x"),
        ("id", "x"),
    ):
        with pytest.raises(ValidationError):
            DeploymentEndpointCreate.model_validate(_payload(**{field: value}))


def test_valid_urls_accepted():
    cases = [
        ("https", "https://ai.example.com/v1/fraud"),
        ("http", "http://10.0.10.20:8000/infer"),
        ("grpc", "grpc://fraud.internal:9000"),
        ("grpcs", "grpcs://fraud.internal:9001"),
    ]
    for protocol, url in cases:
        assert validate_endpoint_url(protocol, url) == url


def test_rejects_embedded_credentials():
    with pytest.raises(InvalidEndpointUrlError):
        validate_endpoint_url("https", "https://user:pass@fraud.example.com/v1")
    with pytest.raises(InvalidEndpointUrlError):
        validate_endpoint_url("https", "https://user@fraud.example.com/v1")


def test_rejects_fragment():
    with pytest.raises(InvalidEndpointUrlError):
        validate_endpoint_url("https", "https://fraud.example.com/v1#section")


def test_rejects_scheme_protocol_mismatch():
    with pytest.raises(InvalidEndpointUrlError):
        validate_endpoint_url("https", "http://fraud.example.com/v1")
    with pytest.raises(InvalidEndpointUrlError):
        validate_endpoint_url("grpc", "https://fraud.example.com/v1")


def test_rejects_missing_netloc_and_garbage():
    for url in ("not a url", "https://", "://missing-scheme", "/relative/path"):
        with pytest.raises(InvalidEndpointUrlError):
            validate_endpoint_url("https", url)


def test_rejects_oversized_url():
    oversized = "https://fraud.example.com/" + "a" * 2100
    with pytest.raises(InvalidEndpointUrlError):
        validate_endpoint_url("https", oversized)


def test_rejects_empty_url():
    with pytest.raises(InvalidEndpointUrlError):
        validate_endpoint_url("https", "")


def test_auth_reference_rules():
    validate_auth_reference("external_secret", None)
    validate_auth_reference("external_secret", "secret://prod/fraud")
    validate_auth_reference("bearer", "vault://team/llm-token")
    with pytest.raises(InvalidAuthReferenceError):
        validate_auth_reference("none", "secret://prod/fraud")
    with pytest.raises(InvalidAuthReferenceError):
        validate_auth_reference("bearer", "sk-live-abc123")
    with pytest.raises(InvalidAuthReferenceError):
        validate_auth_reference("api_key", "hunter2!")


def test_create_payload_passes_url_and_auth_helpers():
    endpoint = DeploymentEndpointCreate.model_validate(_payload())
    assert validate_endpoint_url(endpoint.protocol, endpoint.url) == endpoint.url
    validate_auth_reference(endpoint.auth_type, endpoint.auth_reference)


def test_update_only_accepts_mutable_fields():
    update = DeploymentEndpointUpdate.model_validate(
        {"name": "renamed", "url": "https://fraud.example.com/v2", "is_primary": False}
    )
    assert update.name == "renamed"
    for immutable in ("deployment_id", "endpoint_type", "id"):
        with pytest.raises(ValidationError):
            DeploymentEndpointUpdate.model_validate({immutable: "x"})

