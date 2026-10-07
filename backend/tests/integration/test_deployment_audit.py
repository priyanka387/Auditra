import sqlalchemy as sa

from app.core.config import settings
from app.modules.audit.models import AuditEvent
from app.modules.model_inventory.schemas import (
    DeploymentCreate,
    DeploymentEndpointCreate,
    DeploymentEndpointUpdate,
    DeploymentTransitionRequest,
    DeploymentUpdate,
)
from app.modules.model_inventory.services import DeploymentEndpointService, DeploymentService


def _service(db):
    return DeploymentService(
        db,
        tenant_id=settings.default_tenant_id,
        request_id="req-dep-01",
        actor="user-3",
    )


def _endpoint_service(db):
    return DeploymentEndpointService(
        db,
        tenant_id=settings.default_tenant_id,
        request_id="req-dep-01",
        actor="user-3",
    )


def _payload(**overrides):
    payload = {
        "name": "fraud-detection-prod",
        "environment": "production",
        "deployment_kind": "online_inference",
        "target_type": "kubernetes",
        "target_name": "prod-cluster",
        "region": "us-east-1",
        "namespace": "ai-serving",
        "runtime": "vllm",
        "desired_replicas": 3,
        "configuration": {"tensor_parallel_size": 2},
        "metadata": {"deployment_ticket": "DEP-1209"},
        "source": "manual",
    }
    payload.update(overrides)
    return DeploymentCreate.model_validate(payload)


def _endpoint_payload(**overrides):
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
    return DeploymentEndpointCreate.model_validate(payload)


def _events(db, resource_type, resource_id=None):
    stmt = (
        sa.select(AuditEvent)
        .where(
            AuditEvent.resource_type == resource_type,
            *([AuditEvent.resource_id == str(resource_id)] if resource_id else []),
        )
        .order_by(AuditEvent.sequence_no)
    )
    return list(db.scalars(stmt))


def test_create_deployment_emits_deployment_created(db, parent_version):
    deployment = _service(db).create_deployment(parent_version.id, _payload())

    events = _events(db, "deployment", deployment.id)
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "deployment.created"
    assert event.changed_fields is None
    assert event.before_state is None
    assert event.after_state["name"] == "fraud-detection-prod"
    assert event.after_state["status"] == "planned"
    assert event.after_state["environment"] == "production"
    assert event.after_state["model_version_id"] == str(parent_version.id)
    assert event.metadata_ == {"model_version_id": str(parent_version.id)}
    assert event.actor_type == "user"
    assert event.actor_id == "user-3"
    assert event.request_id == "req-dep-01"


def test_update_deployment_emits_deployment_updated(db, parent_version):
    deployment = _service(db).create_deployment(parent_version.id, _payload())
    _service(db).update_deployment(
        deployment.id, DeploymentUpdate(name="renamed-dep", namespace="other-ns")
    )

    events = _events(db, "deployment", deployment.id)
    assert len(events) == 2
    event = events[-1]
    assert event.event_type == "deployment.updated"
    assert event.changed_fields == ["name", "namespace"]
    assert event.before_state["name"] == "fraud-detection-prod"
    assert event.after_state["name"] == "renamed-dep"
    assert event.before_state["namespace"] == "ai-serving"
    assert event.after_state["namespace"] == "other-ns"


def test_deployment_noop_update_emits_no_event(db, parent_version):
    deployment = _service(db).create_deployment(parent_version.id, _payload())

    _service(db).update_deployment(deployment.id, DeploymentUpdate())
    _service(db).update_deployment(deployment.id, DeploymentUpdate(name="fraud-detection-prod"))

    assert len(_events(db, "deployment", deployment.id)) == 1


def test_transition_emits_status_changed(db, parent_version):
    deployment = _service(db).create_deployment(parent_version.id, _payload())
    _service(db).transition_deployment(
        deployment.id, DeploymentTransitionRequest(status="deploying")
    )

    events = _events(db, "deployment", deployment.id)
    assert len(events) == 2
    event = events[-1]
    assert event.event_type == "deployment.status_changed"
    assert event.changed_fields == ["status"]
    assert event.before_state == {"status": "planned"}
    assert event.after_state == {"status": "deploying"}


def test_archive_emits_deployment_archived(db, parent_version):
    deployment = _service(db).create_deployment(parent_version.id, _payload())
    _service(db).archive_deployment(deployment.id)

    events = _events(db, "deployment", deployment.id)
    assert len(events) == 2
    event = events[-1]
    assert event.event_type == "deployment.archived"
    assert event.changed_fields == ["archived_at", "status"]
    assert event.before_state["status"] == "planned"
    assert event.after_state["status"] == "deprecated"

    _service(db).archive_deployment(deployment.id)
    assert len(_events(db, "deployment", deployment.id)) == 2


def test_create_endpoint_emits_endpoint_created(db, parent_version):
    deployment = _service(db).create_deployment(parent_version.id, _payload())
    endpoint = _endpoint_service(db).create_endpoint(deployment.id, _endpoint_payload())

    events = _events(db, "deployment_endpoint", endpoint.id)
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "deployment_endpoint.created"
    assert event.resource_type == "deployment_endpoint"
    assert event.changed_fields is None
    assert event.metadata_ == {"deployment_id": str(deployment.id)}
    assert event.after_state["url"] == "https://fraud.example.com/v1/infer"
    assert event.after_state["auth_type"] == "external_secret"
    assert event.after_state["is_primary"] is True


def test_update_endpoint_emits_endpoint_updated(db, parent_version):
    deployment = _service(db).create_deployment(parent_version.id, _payload())
    endpoint = _endpoint_service(db).create_endpoint(deployment.id, _endpoint_payload())
    _endpoint_service(db).update_endpoint(
        endpoint.id, DeploymentEndpointUpdate(name="primary-renamed")
    )

    events = _events(db, "deployment_endpoint", endpoint.id)
    assert len(events) == 2
    event = events[-1]
    assert event.event_type == "deployment_endpoint.updated"
    assert event.changed_fields == ["name"]
    assert event.before_state["name"] == "primary"
    assert event.after_state["name"] == "primary-renamed"


def test_endpoint_noop_update_emits_no_event(db, parent_version):
    deployment = _service(db).create_deployment(parent_version.id, _payload())
    endpoint = _endpoint_service(db).create_endpoint(deployment.id, _endpoint_payload())

    _endpoint_service(db).update_endpoint(endpoint.id, DeploymentEndpointUpdate())

    assert len(_events(db, "deployment_endpoint", endpoint.id)) == 1


def test_archive_endpoint_emits_endpoint_archived(db, parent_version):
    deployment = _service(db).create_deployment(parent_version.id, _payload())
    endpoint = _endpoint_service(db).create_endpoint(deployment.id, _endpoint_payload())
    _endpoint_service(db).archive_endpoint(endpoint.id)

    events = _events(db, "deployment_endpoint", endpoint.id)
    assert len(events) == 2
    event = events[-1]
    assert event.event_type == "deployment_endpoint.archived"
    assert event.changed_fields == ["archived_at"]

    _endpoint_service(db).archive_endpoint(endpoint.id)
    assert len(_events(db, "deployment_endpoint", endpoint.id)) == 2


def test_endpoint_auth_reference_redacted_in_audit_state(db, parent_version):
    deployment = _service(db).create_deployment(parent_version.id, _payload())
    endpoint = _endpoint_service(db).create_endpoint(
        deployment.id, _endpoint_payload(auth_reference="secret://vault/x")
    )
    _endpoint_service(db).update_endpoint(
        endpoint.id, DeploymentEndpointUpdate(auth_reference="secret://vault/y")
    )

    events = _events(db, "deployment_endpoint", endpoint.id)
    created, updated = events[0], events[-1]
    assert created.after_state["auth_reference"] == "REDACTED"
    assert updated.before_state["auth_reference"] == "REDACTED"
    assert updated.after_state["auth_reference"] == "REDACTED"
    assert "vault" not in str(events)
