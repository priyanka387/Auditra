import pytest
from pydantic import ValidationError

from app.modules.model_inventory.schemas import (
    DeploymentCreate,
    DeploymentTransitionRequest,
    DeploymentUpdate,
)


def _payload(**overrides):
    payload = {
        "name": "fraud-detection-prod",
        "environment": "production",
        "deployment_kind": "online_inference",
        "target_type": "kubernetes",
        "target_name": "prod-cluster",
        "runtime": "vllm",
        "source": "manual",
    }
    payload.update(overrides)
    return payload


def test_create_normalizes_and_accepts_valid():
    deployment = DeploymentCreate.model_validate(
        _payload(environment=" Production ", source="MANUAL")
    )
    assert deployment.environment == "production"
    assert deployment.source == "manual"
    assert deployment.name == "fraud-detection-prod"
    assert deployment.configuration == {}
    assert deployment.metadata == {}
    assert not hasattr(deployment, "status")


def test_create_rejects_bad_environment_kind_source():
    for field, value in (
        ("environment", "qa"),
        ("deployment_kind", "streaming"),
        ("source", "spreadsheet"),
    ):
        with pytest.raises(ValidationError):
            DeploymentCreate.model_validate(_payload(**{field: value}))


def test_create_rejects_status_field():
    with pytest.raises(ValidationError):
        DeploymentCreate.model_validate(_payload(status="active"))


def test_create_rejects_model_version_id_field():
    with pytest.raises(ValidationError):
        DeploymentCreate.model_validate(_payload(model_version_id="x"))


def test_negative_replicas_rejected():
    with pytest.raises(ValidationError):
        DeploymentCreate.model_validate(_payload(desired_replicas=-1))
    with pytest.raises(ValidationError):
        DeploymentCreate.model_validate(_payload(observed_replicas=-2))
    accepted = DeploymentCreate.model_validate(_payload(desired_replicas=0))
    assert accepted.desired_replicas == 0


def test_name_rules():
    with pytest.raises(ValidationError):
        DeploymentCreate.model_validate(_payload(name=""))
    with pytest.raises(ValidationError):
        DeploymentCreate.model_validate(_payload(name="   "))
    with pytest.raises(ValidationError):
        DeploymentCreate.model_validate(_payload(name="x" * 129))
    padded = DeploymentCreate.model_validate(_payload(name="  padded-name  "))
    assert padded.name == "padded-name"


def test_runtime_and_target_type_lowercase():
    deployment = DeploymentCreate.model_validate(_payload(runtime="Triton", target_type="K8S"))
    assert deployment.runtime == "triton"
    assert deployment.target_type == "k8s"


def test_metadata_and_configuration_reject_secret_keys():
    with pytest.raises(ValidationError):
        DeploymentCreate.model_validate(_payload(metadata={"api_key": "x"}))
    with pytest.raises(ValidationError):
        DeploymentCreate.model_validate(_payload(configuration={"password": "x"}))
    with pytest.raises(ValidationError):
        DeploymentCreate.model_validate(_payload(metadata={"blob": "x" * 20_000}))
    ok = DeploymentCreate.model_validate(
        _payload(configuration={"tensor_parallel_size": 2, "max_model_len": 32768})
    )
    assert ok.configuration == {"tensor_parallel_size": 2, "max_model_len": 32768}


def test_update_only_accepts_mutable_fields():
    update = DeploymentUpdate.model_validate(
        {
            "name": "renamed",
            "region": "eu-west-1",
            "desired_replicas": 5,
            "metadata": {"ticket": "DEP-1"},
            "last_seen_at": "2026-10-05T10:00:00Z",
        }
    )
    assert update.name == "renamed"
    for immutable in (
        "model_version_id",
        "status",
        "environment",
        "deployment_kind",
        "target_type",
        "source",
        "status_source",
    ):
        with pytest.raises(ValidationError):
            DeploymentUpdate.model_validate({immutable: "x"})


def test_update_negative_replicas_rejected():
    with pytest.raises(ValidationError):
        DeploymentUpdate.model_validate({"desired_replicas": -1})


def test_transition_request_validation():
    ok = DeploymentTransitionRequest.model_validate(
        {"status": "active", "reason": "verified", "observed_at": "2026-10-05T11:15:00Z"}
    )
    assert ok.status == "active"
    assert ok.reason == "verified"
    with pytest.raises(ValidationError):
        DeploymentTransitionRequest.model_validate({"status": "archived"})
    with pytest.raises(ValidationError):
        DeploymentTransitionRequest.model_validate({})
    with pytest.raises(ValidationError):
        DeploymentTransitionRequest.model_validate({"status": "active", "nope": 1})
