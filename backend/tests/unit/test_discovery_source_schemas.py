import pytest
from pydantic import ValidationError

from app.modules.asset_discovery.enums import DiscoverySourceStatus, DiscoverySourceType
from app.modules.asset_discovery.schemas import (
    DiscoverySourceCreate,
    DiscoverySourceUpdate,
)


def _payload(**overrides) -> dict:
    payload = {
        "name": "payments-service",
        "source_type": "repository",
        "config": {"location": "/workspace/payments"},
    }
    payload.update(overrides)
    return payload


def test_create_valid_repository_source():
    source = DiscoverySourceCreate(**_payload())
    assert source.name == "payments-service"
    assert source.source_type == DiscoverySourceType.REPOSITORY
    assert source.config == {"location": "/workspace/payments"}


def test_create_strips_name_whitespace():
    source = DiscoverySourceCreate(**_payload(name="  payments-service  "))
    assert source.name == "payments-service"


def test_create_missing_name_rejected():
    payload = _payload()
    del payload["name"]
    with pytest.raises(ValidationError):
        DiscoverySourceCreate(**payload)


def test_create_blank_name_rejected():
    with pytest.raises(ValidationError):
        DiscoverySourceCreate(**_payload(name="   "))


def test_create_invalid_source_type_rejected():
    with pytest.raises(ValidationError):
        DiscoverySourceCreate(**_payload(source_type="kubernetes"))


def test_create_extra_field_rejected():
    with pytest.raises(ValidationError):
        DiscoverySourceCreate(**_payload(priority=1))


def test_create_config_must_be_object():
    with pytest.raises(ValidationError):
        DiscoverySourceCreate(**_payload(config=["/workspace/payments"]))


def test_create_config_with_secret_key_rejected():
    with pytest.raises(ValidationError) as excinfo:
        DiscoverySourceCreate(
            **_payload(config={"location": "/workspace/payments", "api_key": "sk-secret"})
        )
    assert "looks like a secret" in str(excinfo.value)


def test_create_nested_secret_key_rejected():
    with pytest.raises(ValidationError):
        DiscoverySourceCreate(
            **_payload(config={"location": "/workspace/payments", "auth": {"token": "abc123"}})
        )


def test_create_oversized_config_rejected():
    with pytest.raises(ValidationError):
        DiscoverySourceCreate(
            **_payload(config={"location": "/workspace/payments", "blob": "x" * 20_000})
        )


def test_update_partial_fields():
    update = DiscoverySourceUpdate(name="renamed-service")
    assert update.name == "renamed-service"
    assert update.config is None
    assert update.status is None


def test_update_strips_strings_and_validates_status():
    update = DiscoverySourceUpdate(name="  renamed  ", status="disabled")
    assert update.name == "renamed"
    assert update.status == DiscoverySourceStatus.DISABLED


def test_update_status_archived_rejected():
    with pytest.raises(ValidationError):
        DiscoverySourceUpdate(status="archived")


def test_update_invalid_status_rejected():
    with pytest.raises(ValidationError):
        DiscoverySourceUpdate(status="paused")


def test_update_extra_field_rejected():
    with pytest.raises(ValidationError):
        DiscoverySourceUpdate(source_type="github")


def test_update_config_secret_rejected():
    with pytest.raises(ValidationError):
        DiscoverySourceUpdate(config={"password": "hunter2"})
