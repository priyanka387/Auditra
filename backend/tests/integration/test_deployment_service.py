import uuid

import pytest

from app.core.config import settings
from app.modules.model_inventory.domain import (
    DeploymentEndpointEvent,
    DeploymentEvent,
    dispatch_event,
    register_handler,
    reset_handlers,
)
from app.modules.model_inventory.domain.errors import (
    DeploymentAlreadyArchivedError,
    DeploymentEndpointNotFoundError,
    DeploymentNotFoundError,
    DuplicateDeploymentError,
    DuplicatePrimaryEndpointError,
    EndpointArchivedError,
    InvalidAuthReferenceError,
    InvalidDeploymentTransitionError,
    InvalidEndpointUrlError,
    InvalidPrimaryEndpointError,
    ModelVersionNotDeployableError,
    ModelVersionNotFoundError,
)
from app.modules.model_inventory.models import ModelVersion
from app.modules.model_inventory.repositories import DeploymentFilters
from app.modules.model_inventory.schemas import (
    DeploymentCreate,
    DeploymentEndpointCreate,
    DeploymentEndpointUpdate,
    DeploymentTransitionRequest,
    DeploymentUpdate,
)
from app.modules.model_inventory.services import DeploymentEndpointService, DeploymentService


def _service(db):
    return DeploymentService(db, tenant_id=settings.default_tenant_id)


def _endpoint_service(db):
    return DeploymentEndpointService(db, tenant_id=settings.default_tenant_id)


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


def test_create_sets_planned_and_initial_status_source(db, parent_version):
    deployment = _service(db).create_deployment(parent_version.id, _payload())
    assert deployment.status == "planned"
    assert deployment.status_source == "manual"
    assert deployment.model_version_id == parent_version.id
    assert deployment.record_version == 1
    assert deployment.archived_at is None
    assert deployment.configuration == {"tensor_parallel_size": 2}
    assert deployment.metadata_ == {"deployment_ticket": "DEP-1209"}


def test_create_rejects_unknown_version(db):
    with pytest.raises(ModelVersionNotFoundError):
        _service(db).create_deployment(uuid.uuid4(), _payload())


def test_create_rejects_non_deployable_versions(db, parent_version):
    version = db.get(ModelVersion, parent_version.id)
    version.lifecycle_state = "RETIRED"
    db.commit()
    with pytest.raises(ModelVersionNotDeployableError) as retired:
        _service(db).create_deployment(parent_version.id, _payload())
    assert retired.value.code == "MODEL_VERSION_NOT_DEPLOYABLE"

    version.lifecycle_state = "ARCHIVED"
    db.commit()
    with pytest.raises(ModelVersionNotDeployableError):
        _service(db).create_deployment(parent_version.id, _payload())

    version.lifecycle_state = "DRAFT"
    db.commit()
    created = _service(db).create_deployment(parent_version.id, _payload())
    assert created.status == "planned"

    version.lifecycle_state = "ACTIVE"
    db.commit()
    created = _service(db).create_deployment(
        parent_version.id, _payload(name="fraud-detection-stage", target_name="stage-cluster")
    )
    assert created.status == "planned"


def test_create_rejects_duplicate_identity(db, parent_version):
    _service(db).create_deployment(parent_version.id, _payload())
    with pytest.raises(DuplicateDeploymentError):
        _service(db).create_deployment(parent_version.id, _payload())


def test_get_and_list(db, parent_version):
    service = _service(db)
    deployment = service.create_deployment(parent_version.id, _payload())
    fetched = service.get_deployment(deployment.id)
    assert fetched.id == deployment.id
    items, total = service.list_deployments(DeploymentFilters())
    assert total == 1 and items[0].id == deployment.id
    with pytest.raises(DeploymentNotFoundError):
        service.get_deployment(uuid.uuid4())


def test_transition_happy_path_and_guards(db, parent_version):
    service = _service(db)
    deployment = service.create_deployment(parent_version.id, _payload())

    def transition(target, **kwargs):
        return service.transition_deployment(
            deployment.id, DeploymentTransitionRequest(status=target, **kwargs)
        )

    assert transition("deploying").status == "deploying"
    active = transition(
        "active",
        reason="External deployment verified",
        observed_at="2026-10-05T11:15:00Z",
    )
    assert active.status == "active"
    assert active.status_source == "api"
    assert active.last_seen_at is not None

    with pytest.raises(InvalidDeploymentTransitionError):
        transition("stopped")

    with pytest.raises(InvalidDeploymentTransitionError):
        service.transition_deployment(deployment.id, DeploymentTransitionRequest(status="active"))

    stopping = transition("stopping")
    assert stopping.status == "stopping"
    assert transition("stopped").status == "stopped"

    service.archive_deployment(deployment.id)
    with pytest.raises(DeploymentAlreadyArchivedError):
        transition("deploying")


def test_update_applies_mutable_fields(db, parent_version):
    service = _service(db)
    deployment = service.create_deployment(parent_version.id, _payload())
    updated = service.update_deployment(
        deployment.id,
        DeploymentUpdate.model_validate(
            {
                "name": "fraud-detection-prod-renamed",
                "region": "eu-west-1",
                "desired_replicas": 5,
                "metadata": {"cost_center": "AI-01"},
            }
        ),
    )
    assert updated.name == "fraud-detection-prod-renamed"
    assert updated.region == "eu-west-1"
    assert updated.desired_replicas == 5
    assert updated.metadata_ == {"cost_center": "AI-01"}
    assert updated.record_version == 2
    assert updated.model_version_id == parent_version.id


def test_archive_is_idempotent_and_sets_state(db, parent_version):
    service = _service(db)
    deployment = service.create_deployment(parent_version.id, _payload())
    service.archive_deployment(deployment.id)
    archived = service.get_deployment(deployment.id)
    assert archived.archived_at is not None
    assert archived.status == "deprecated"
    service.archive_deployment(deployment.id)
    assert service.get_deployment(deployment.id).archived_at == archived.archived_at

    with pytest.raises(DeploymentAlreadyArchivedError):
        service.update_deployment(deployment.id, DeploymentUpdate.model_validate({"name": "nope"}))


def test_endpoint_crud_and_primary_rules(db, parent_version):
    deployment = _service(db).create_deployment(parent_version.id, _payload())
    service = _endpoint_service(db)

    primary = service.create_endpoint(deployment.id, _endpoint_payload())
    assert primary.is_primary is True
    assert primary.health_status == "unknown"
    assert primary.status == "active"

    with pytest.raises(DuplicatePrimaryEndpointError):
        service.create_endpoint(deployment.id, _endpoint_payload(name="primary-2"))

    health = service.create_endpoint(
        deployment.id,
        _endpoint_payload(
            name="health",
            is_primary=False,
            endpoint_type="health",
            url="https://fraud.example.com/healthz",
        ),
    )
    assert health.endpoint_type == "health"

    with pytest.raises(InvalidPrimaryEndpointError):
        service.create_endpoint(
            deployment.id,
            _endpoint_payload(
                name="health-primary",
                endpoint_type="health",
                is_primary=True,
                url="https://fraud.example.com/healthz",
            ),
        )

    listed = service.list_endpoints(deployment.id)
    assert [ep.name for ep in listed] == ["primary", "health"]

    service.archive_endpoint(primary.id)
    remaining = service.list_endpoints(deployment.id)
    assert [ep.name for ep in remaining] == ["health"]
    with_archived = service.list_endpoints(deployment.id, include_archived=True)
    assert len(with_archived) == 2

    replacement = service.create_endpoint(deployment.id, _endpoint_payload(name="primary-v2"))
    assert replacement.is_primary is True

    with pytest.raises(DeploymentEndpointNotFoundError):
        service.get_endpoint(uuid.uuid4())


def test_endpoint_update_rules(db, parent_version):
    deployment = _service(db).create_deployment(parent_version.id, _payload())
    service = _endpoint_service(db)
    endpoint = service.create_endpoint(deployment.id, _endpoint_payload())

    updated = service.update_endpoint(
        endpoint.id,
        DeploymentEndpointUpdate.model_validate(
            {
                "name": "primary-renamed",
                "url": "https://fraud.example.com/v2/infer",
                "health_status": "healthy",
                "last_health_check_at": "2026-10-05T12:00:00Z",
            }
        ),
    )
    assert updated.name == "primary-renamed"
    assert updated.url == "https://fraud.example.com/v2/infer"
    assert updated.health_status == "healthy"
    assert updated.record_version == 2

    with pytest.raises(InvalidEndpointUrlError):
        service.update_endpoint(
            endpoint.id,
            DeploymentEndpointUpdate.model_validate({"url": "http://fraud.example.com/v2"}),
        )

    with pytest.raises(InvalidEndpointUrlError):
        service.create_endpoint(
            deployment.id,
            _endpoint_payload(
                name="credentialed", is_primary=False, url="https://user:pass@fraud.example.com/v1"
            ),
        )

    with pytest.raises(InvalidAuthReferenceError):
        service.create_endpoint(
            deployment.id,
            _endpoint_payload(name="leaky", is_primary=False, auth_reference="sk-live-abc123"),
        )

    service.archive_endpoint(endpoint.id)
    with pytest.raises(EndpointArchivedError):
        service.update_endpoint(
            endpoint.id, DeploymentEndpointUpdate.model_validate({"name": "nope"})
        )


def test_endpoint_on_archived_deployment_rejected(db, parent_version):
    deployment = _service(db).create_deployment(parent_version.id, _payload())
    _endpoint_service(db).create_endpoint(deployment.id, _endpoint_payload())
    _service(db).archive_deployment(deployment.id)

    with pytest.raises(DeploymentAlreadyArchivedError):
        _endpoint_service(db).create_endpoint(
            deployment.id, _endpoint_payload(name="second", is_primary=False)
        )


def test_events_dispatched(db, parent_version):
    seen: list[str] = []

    def handler(event):
        seen.append(event.event_type)

    register_handler(handler)
    try:
        deployment = _service(db).create_deployment(parent_version.id, _payload())
        endpoint_service = _endpoint_service(db)
        endpoint = endpoint_service.create_endpoint(deployment.id, _endpoint_payload())
        _service(db).transition_deployment(
            deployment.id, DeploymentTransitionRequest(status="deploying")
        )
        _service(db).update_deployment(
            deployment.id, DeploymentUpdate.model_validate({"region": "eu-west-1"})
        )
        endpoint_service.update_endpoint(
            endpoint.id, DeploymentEndpointUpdate.model_validate({"name": "renamed"})
        )
        endpoint_service.archive_endpoint(endpoint.id)
        _service(db).archive_deployment(deployment.id)
    finally:
        reset_handlers()

    assert seen == [
        "deployment.created",
        "deployment_endpoint.created",
        "deployment.status_changed",
        "deployment.updated",
        "deployment_endpoint.updated",
        "deployment_endpoint.archived",
        "deployment.archived",
    ]
    assert isinstance(seen, list)


def test_event_payload_shape(db, parent_version):
    events = []
    register_handler(events.append)
    try:
        deployment = _service(db).create_deployment(parent_version.id, _payload())
    finally:
        reset_handlers()
    event = events[0]
    assert isinstance(event, DeploymentEvent)
    assert event.tenant_id == settings.default_tenant_id
    assert event.deployment_id == deployment.id
    assert event.model_version_id == parent_version.id
    assert event.event_type == "deployment.created"

    endpoint_events = []
    register_handler(endpoint_events.append)
    try:
        _endpoint_service(db).create_endpoint(deployment.id, _endpoint_payload())
    finally:
        reset_handlers()
    assert isinstance(endpoint_events[0], DeploymentEndpointEvent)
    assert endpoint_events[0].endpoint_id is not None

    assert callable(dispatch_event)
