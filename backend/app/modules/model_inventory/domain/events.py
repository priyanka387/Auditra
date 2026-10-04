from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class ModelEvent:
    event_id: str
    event_type: str
    model_id: UUID
    tenant_id: UUID
    occurred_at: datetime
    actor: str | None
    change_summary: list[str]
    request_id: str | None


@dataclass(frozen=True)
class ModelVersionEvent:
    event_id: str
    event_type: str
    tenant_id: UUID
    model_id: UUID
    model_version_id: UUID
    occurred_at: datetime
    actor: str | None
    change_summary: list[str]
    request_id: str | None


@dataclass(frozen=True)
class DeploymentEvent:
    event_id: str
    event_type: str
    tenant_id: UUID
    deployment_id: UUID
    model_version_id: UUID
    occurred_at: datetime
    actor: str | None
    change_summary: list[str]
    request_id: str | None


@dataclass(frozen=True)
class DeploymentEndpointEvent:
    event_id: str
    event_type: str
    tenant_id: UUID
    deployment_id: UUID
    endpoint_id: UUID
    occurred_at: datetime
    actor: str | None
    change_summary: list[str]
    request_id: str | None


@dataclass(frozen=True)
class ApplicationEvent:
    event_id: str
    event_type: str
    tenant_id: UUID
    application_id: UUID
    occurred_at: datetime
    actor: str | None
    change_summary: list[str]
    request_id: str | None


@dataclass(frozen=True)
class AgentEvent:
    event_id: str
    event_type: str
    tenant_id: UUID
    application_id: UUID
    agent_id: UUID
    occurred_at: datetime
    actor: str | None
    change_summary: list[str]
    request_id: str | None


@dataclass(frozen=True)
class AgentModelAssociationEvent:
    event_id: str
    event_type: str
    tenant_id: UUID
    agent_id: UUID
    model_id: UUID
    association_id: UUID
    occurred_at: datetime
    actor: str | None
    change_summary: list[str]
    request_id: str | None


Event = (
    ModelEvent
    | ModelVersionEvent
    | DeploymentEvent
    | DeploymentEndpointEvent
    | ApplicationEvent
    | AgentEvent
    | AgentModelAssociationEvent
)

_handlers: list[Callable[[Event], None]] = []


def register_handler(fn: Callable[[Event], None]) -> None:
    _handlers.append(fn)


def dispatch_event(event: Event) -> None:
    for fn in _handlers:
        fn(event)


def reset_handlers() -> None:
    _handlers.clear()
