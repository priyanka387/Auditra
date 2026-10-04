from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.modules.model_inventory.domain import DeploymentEndpointEvent, dispatch_event
from app.modules.model_inventory.domain.errors import (
    DeploymentAlreadyArchivedError,
    DeploymentEndpointNotFoundError,
    DeploymentNotFoundError,
    DuplicatePrimaryEndpointError,
    EndpointArchivedError,
    InvalidPrimaryEndpointError,
)
from app.modules.model_inventory.models import DeploymentEndpoint, ModelDeployment
from app.modules.model_inventory.repositories.deployment_endpoint_repository import (
    commit,
    create_endpoint,
    find_primary_inference,
    get_endpoint,
    list_endpoints,
)
from app.modules.model_inventory.repositories.deployment_repository import (
    get_deployment as get_deployment_row,
)
from app.modules.model_inventory.schemas import (
    DeploymentEndpointCreate,
    DeploymentEndpointUpdate,
    validate_auth_reference,
    validate_endpoint_url,
)

_MUTABLE_FIELDS = (
    "name",
    "url",
    "route",
    "is_primary",
    "status",
    "health_status",
    "last_health_check_at",
)


class DeploymentEndpointService:
    def __init__(
        self,
        db: Session,
        tenant_id: UUID,
        request_id: str | None = None,
        actor: str | None = None,
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.request_id = request_id
        self.actor = actor

    def create_endpoint(
        self, deployment_id: UUID, payload: DeploymentEndpointCreate
    ) -> DeploymentEndpoint:
        deployment = self._get_deployment_or_404(deployment_id)
        if deployment.archived_at is not None:
            raise DeploymentAlreadyArchivedError(f"deployment '{deployment_id}' is archived")
        self._validate(payload.endpoint_type, payload.protocol, payload.url, payload)
        if payload.is_primary:
            self._assert_primary_available(deployment_id)

        endpoint = create_endpoint(
            self.db,
            id=uuid4(),
            tenant_id=self.tenant_id,
            deployment_id=deployment_id,
            name=payload.name,
            endpoint_type=payload.endpoint_type,
            protocol=payload.protocol,
            url=payload.url,
            route=payload.route,
            auth_type=payload.auth_type,
            auth_reference=payload.auth_reference,
            is_primary=payload.is_primary,
            status=payload.status,
            health_status=payload.health_status,
            metadata_=payload.metadata,
            created_by=self.actor,
        )
        endpoint_id = endpoint.id
        commit(self.db)
        self._dispatch("deployment_endpoint.created", deployment_id, endpoint_id, [])
        return endpoint

    def get_endpoint(self, endpoint_id: UUID) -> DeploymentEndpoint:
        endpoint = get_endpoint(self.db, self.tenant_id, endpoint_id)
        if endpoint is None:
            raise DeploymentEndpointNotFoundError(f"endpoint '{endpoint_id}' not found")
        return endpoint

    def list_endpoints(
        self, deployment_id: UUID, include_archived: bool = False
    ) -> list[DeploymentEndpoint]:
        self._get_deployment_or_404(deployment_id)
        return list_endpoints(self.db, self.tenant_id, deployment_id, include_archived)

    def update_endpoint(
        self, endpoint_id: UUID, payload: DeploymentEndpointUpdate
    ) -> DeploymentEndpoint:
        endpoint = self.get_endpoint(endpoint_id)
        if endpoint.archived_at is not None:
            raise EndpointArchivedError(f"endpoint '{endpoint_id}' is archived")

        protocol = payload.protocol if payload.protocol is not None else endpoint.protocol
        url = payload.url if payload.url is not None else endpoint.url
        validate_endpoint_url(protocol, url)

        changed: list[str] = []

        if payload.auth_type is not None or payload.auth_reference is not None:
            # A new auth_type without a reference clears the old pairing, so
            # validation always runs against exactly what will be persisted.
            new_type = payload.auth_type if payload.auth_type is not None else endpoint.auth_type
            new_reference = payload.auth_reference
            validate_auth_reference(new_type, new_reference)
            if endpoint.auth_type != new_type:
                changed.append("auth_type")
                endpoint.auth_type = new_type
            if endpoint.auth_reference != new_reference:
                changed.append("auth_reference")
                endpoint.auth_reference = new_reference

        if payload.is_primary is True and endpoint.endpoint_type != "inference":
            raise InvalidPrimaryEndpointError("only inference endpoints can be marked as primary")
        if payload.is_primary is True:
            self._assert_primary_available(endpoint.deployment_id, exclude_id=endpoint.id)

        for field in _MUTABLE_FIELDS:
            value = getattr(payload, field)
            if value is not None and value != getattr(endpoint, field):
                changed.append(field)
                setattr(endpoint, field, value)
        if payload.metadata is not None and payload.metadata != endpoint.metadata_:
            changed.append("metadata")
            endpoint.metadata_ = payload.metadata

        if changed and self.actor is not None:
            endpoint.updated_by = self.actor
        commit(self.db)
        self._dispatch("deployment_endpoint.updated", endpoint.deployment_id, endpoint.id, changed)
        return endpoint

    def archive_endpoint(self, endpoint_id: UUID) -> None:
        endpoint = self.get_endpoint(endpoint_id)
        if endpoint.archived_at is not None:
            return
        endpoint.archived_at = datetime.now(UTC)
        commit(self.db)
        self._dispatch(
            "deployment_endpoint.archived", endpoint.deployment_id, endpoint.id, ["archived"]
        )

    def _validate(
        self, endpoint_type: str, protocol: str, url: str, payload: DeploymentEndpointCreate
    ) -> None:
        if endpoint_type != "inference" and payload.is_primary:
            raise InvalidPrimaryEndpointError("only inference endpoints can be marked as primary")
        validate_endpoint_url(protocol, url)
        validate_auth_reference(payload.auth_type, payload.auth_reference)

    def _assert_primary_available(
        self, deployment_id: UUID, exclude_id: UUID | None = None
    ) -> None:
        existing = find_primary_inference(self.db, self.tenant_id, deployment_id, exclude_id)
        if existing is not None:
            raise DuplicatePrimaryEndpointError(
                f"deployment '{deployment_id}' already has primary inference endpoint "
                f"'{existing.name}'"
            )

    def _get_deployment_or_404(self, deployment_id: UUID) -> ModelDeployment:
        deployment = get_deployment_row(self.db, self.tenant_id, deployment_id)
        if deployment is None:
            raise DeploymentNotFoundError(f"deployment '{deployment_id}' not found")
        return deployment

    def _dispatch(
        self, event_type: str, deployment_id: UUID, endpoint_id: UUID, summary: list[str]
    ) -> None:
        dispatch_event(
            DeploymentEndpointEvent(
                event_id=str(uuid4()),
                event_type=event_type,
                tenant_id=self.tenant_id,
                deployment_id=deployment_id,
                endpoint_id=endpoint_id,
                occurred_at=datetime.now(UTC),
                actor=self.actor,
                change_summary=summary,
                request_id=self.request_id,
            )
        )
