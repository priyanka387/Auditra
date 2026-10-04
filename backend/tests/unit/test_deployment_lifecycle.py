import dataclasses
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.modules.model_inventory.domain import (
    DEPLOYABLE_VERSION_STATES,
    DEPLOYMENT_ALLOWED_TRANSITIONS,
    DeploymentEndpointEvent,
    DeploymentEvent,
    Event,
    can_deployment_transition,
)
from app.modules.model_inventory.domain.errors import (
    DeploymentAlreadyArchivedError,
    DeploymentConcurrencyConflictError,
    DeploymentEndpointNotFoundError,
    DeploymentNotFoundError,
    DuplicateDeploymentError,
    DuplicateEndpointError,
    DuplicatePrimaryEndpointError,
    EndpointArchivedError,
    InvalidAuthReferenceError,
    InvalidDeploymentTransitionError,
    InvalidEndpointUrlError,
    InvalidPrimaryEndpointError,
    ModelVersionNotDeployableError,
)

VALID_EDGES = [
    ("planned", "deploying"),
    ("planned", "active"),
    ("planned", "failed"),
    ("deploying", "active"),
    ("deploying", "degraded"),
    ("deploying", "failed"),
    ("active", "degraded"),
    ("active", "stopping"),
    ("active", "deprecated"),
    ("degraded", "active"),
    ("degraded", "stopping"),
    ("degraded", "failed"),
    ("degraded", "deprecated"),
    ("failed", "deploying"),
    ("failed", "stopping"),
    ("failed", "deprecated"),
    ("stopping", "stopped"),
    ("stopping", "failed"),
    ("stopped", "deploying"),
    ("stopped", "deprecated"),
]

INVALID_EDGES = [
    ("active", "stopped"),
    ("planned", "stopped"),
    ("stopped", "active"),
    ("deprecated", "active"),
    ("deprecated", "stopped"),
    ("active", "active"),
    ("planned", "planned"),
    ("archived", "active"),
    ("", "active"),
    ("active", "archived"),
]


def test_transition_matrix_matches_spec():
    for source, target in VALID_EDGES:
        assert can_deployment_transition(source, target) is True, f"{source}->{target}"
        assert (target in DEPLOYMENT_ALLOWED_TRANSITIONS[source]) is True


def test_invalid_transitions_rejected():
    for source, target in INVALID_EDGES:
        assert can_deployment_transition(source, target) is False, f"{source}->{target}"


def test_deprecated_is_terminal():
    assert DEPLOYMENT_ALLOWED_TRANSITIONS["deprecated"] == set()


def test_deployable_version_states():
    assert DEPLOYABLE_VERSION_STATES == frozenset({"DRAFT", "ACTIVE"})


@pytest.mark.parametrize(
    ("cls", "code", "status"),
    [
        (DeploymentNotFoundError, "DEPLOYMENT_NOT_FOUND", 404),
        (DeploymentAlreadyArchivedError, "DEPLOYMENT_ALREADY_ARCHIVED", 409),
        (InvalidDeploymentTransitionError, "DEPLOYMENT_INVALID_TRANSITION", 409),
        (DuplicateDeploymentError, "DEPLOYMENT_DUPLICATE", 409),
        (DeploymentConcurrencyConflictError, "DEPLOYMENT_CONCURRENCY_CONFLICT", 409),
        (ModelVersionNotDeployableError, "MODEL_VERSION_NOT_DEPLOYABLE", 409),
        (DeploymentEndpointNotFoundError, "ENDPOINT_NOT_FOUND", 404),
        (DuplicateEndpointError, "ENDPOINT_DUPLICATE", 409),
        (DuplicatePrimaryEndpointError, "ENDPOINT_DUPLICATE_PRIMARY", 409),
        (InvalidPrimaryEndpointError, "ENDPOINT_INVALID_PRIMARY", 409),
        (EndpointArchivedError, "ENDPOINT_ALREADY_ARCHIVED", 409),
        (InvalidEndpointUrlError, "ENDPOINT_INVALID_URL", 409),
        (InvalidAuthReferenceError, "INVALID_AUTH_REFERENCE", 409),
    ],
)
def test_error_codes_and_statuses(cls, code, status):
    error = cls("boom")
    assert error.code == code
    assert error.http_status == status
    assert error.message == "boom"
    assert isinstance(error, Exception)


def test_event_dataclasses_are_frozen():
    common = {
        "event_id": "e1",
        "event_type": "deployment.created",
        "tenant_id": uuid4(),
        "occurred_at": datetime.now(UTC),
        "actor": None,
        "change_summary": [],
        "request_id": None,
    }
    deployment_event = DeploymentEvent(
        deployment_id=uuid4(), model_version_id=uuid4(), **common
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        deployment_event.event_type = "mutated"

    endpoint_event = DeploymentEndpointEvent(
        deployment_id=uuid4(), endpoint_id=uuid4(), **common
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        endpoint_event.event_type = "mutated"


def test_event_union_includes_deployment_events():
    assert DeploymentEvent in Event.__args__
    assert DeploymentEndpointEvent in Event.__args__
