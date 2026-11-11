from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.modules.audit.enums import AuditEventType
from app.modules.audit.service import AuditService
from app.modules.model_inventory.domain import (
    DEPLOYABLE_VERSION_STATES,
    DeploymentEvent,
    can_deployment_transition,
    dispatch_event,
)
from app.modules.model_inventory.domain.errors import (
    DeploymentAlreadyArchivedError,
    DeploymentNotFoundError,
    DuplicateDeploymentError,
    InvalidDeploymentTransitionError,
    ModelVersionNotDeployableError,
    ModelVersionNotFoundError,
)
from app.modules.model_inventory.models import ModelDeployment, ModelVersion
from app.modules.model_inventory.repositories.deployment_repository import (
    DeploymentFilters,
    commit,
    create_deployment,
    find_duplicate,
    get_deployment,
    query_deployments,
)
from app.modules.model_inventory.schemas import (
    DeploymentCreate,
    DeploymentTransitionRequest,
    DeploymentUpdate,
)

_MUTABLE_FIELDS = (
    "name",
    "target_name",
    "region",
    "cluster_name",
    "namespace",
    "runtime",
    "serving_framework",
    "image_uri",
    "desired_replicas",
    "observed_replicas",
    "source_reference",
    "last_seen_at",
)


def deployment_audit_state(deployment: ModelDeployment) -> dict:
    """Governance-relevant projection of a deployment for audit before/after state."""
    return {
        "name": deployment.name,
        "environment": deployment.environment,
        "deployment_kind": deployment.deployment_kind,
        "status": deployment.status,
        "status_source": deployment.status_source,
        "target_type": deployment.target_type,
        "target_name": deployment.target_name,
        "region": deployment.region,
        "cluster_name": deployment.cluster_name,
        "namespace": deployment.namespace,
        "runtime": deployment.runtime,
        "serving_framework": deployment.serving_framework,
        "image_uri": deployment.image_uri,
        "desired_replicas": deployment.desired_replicas,
        "observed_replicas": deployment.observed_replicas,
        "model_version_id": str(deployment.model_version_id),
        "configuration": dict(deployment.configuration or {}),
        "metadata": dict(deployment.metadata_ or {}),
        "source": deployment.source,
    }


class DeploymentService:
    def __init__(
        self,
        db: Session,
        tenant_id: UUID,
        request_id: str | None = None,
        actor: str | None = None,
        source: str = "api",
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.request_id = request_id
        self.actor = actor
        self.audit = AuditService(db, tenant_id, request_id=request_id, actor=actor, source=source)

    def create_deployment(
        self, model_version_id: UUID, payload: DeploymentCreate
    ) -> ModelDeployment:
        version = self._get_version_or_404(model_version_id)
        if version.lifecycle_state not in DEPLOYABLE_VERSION_STATES:
            raise ModelVersionNotDeployableError(
                f"model version '{model_version_id}' in state "
                f"{version.lifecycle_state} is not deployable"
            )
        if (
            find_duplicate(
                self.db,
                self.tenant_id,
                model_version_id,
                payload.environment,
                payload.target_name,
                payload.namespace,
            )
            is not None
        ):
            raise DuplicateDeploymentError(
                f"deployment '{payload.name}' already exists for this version, "
                "environment, target and namespace"
            )

        deployment = create_deployment(
            self.db,
            id=uuid4(),
            tenant_id=self.tenant_id,
            model_version_id=model_version_id,
            name=payload.name,
            environment=payload.environment,
            deployment_kind=payload.deployment_kind,
            status="planned",
            status_source=payload.source,
            target_type=payload.target_type,
            target_name=payload.target_name,
            region=payload.region,
            cluster_name=payload.cluster_name,
            namespace=payload.namespace,
            runtime=payload.runtime,
            serving_framework=payload.serving_framework,
            image_uri=payload.image_uri,
            desired_replicas=payload.desired_replicas,
            observed_replicas=payload.observed_replicas,
            configuration=payload.configuration,
            metadata_=payload.metadata,
            source=payload.source,
            source_reference=payload.source_reference,
            created_by=self.actor,
        )
        deployment_id = deployment.id
        self.audit.record_event(
            event_type=AuditEventType.DEPLOYMENT_CREATED,
            resource_type="deployment",
            resource_id=str(deployment_id),
            after_state=deployment_audit_state(deployment),
            metadata={"model_version_id": str(model_version_id)},
        )
        commit(self.db)
        self._dispatch("deployment.created", deployment_id, model_version_id, [])
        return deployment

    def get_deployment(self, deployment_id: UUID) -> ModelDeployment:
        return self._get_or_404(deployment_id)

    def list_deployments(
        self,
        filters: DeploymentFilters,
        page: int = 1,
        page_size: int = 20,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        include_archived: bool = False,
    ) -> tuple[list[ModelDeployment], int]:
        return query_deployments(
            self.db,
            self.tenant_id,
            filters,
            page,
            page_size,
            sort_by,
            sort_order,
            include_archived,
        )

    def update_deployment(self, deployment_id: UUID, payload: DeploymentUpdate) -> ModelDeployment:
        deployment = self._get_or_404(deployment_id)
        self._assert_not_archived(deployment)
        before_state = deployment_audit_state(deployment)

        changed: list[str] = []
        for field in _MUTABLE_FIELDS:
            value = getattr(payload, field)
            if value is not None and value != getattr(deployment, field):
                changed.append(field)
                setattr(deployment, field, value)
        if payload.configuration is not None and payload.configuration != deployment.configuration:
            changed.append("configuration")
            deployment.configuration = payload.configuration
        if payload.metadata is not None and payload.metadata != deployment.metadata_:
            changed.append("metadata")
            deployment.metadata_ = payload.metadata

        if changed and self.actor is not None:
            deployment.updated_by = self.actor
        if changed:
            self.audit.record_event(
                event_type=AuditEventType.DEPLOYMENT_UPDATED,
                resource_type="deployment",
                resource_id=str(deployment_id),
                before_state=before_state,
                after_state=deployment_audit_state(deployment),
                changed_fields=sorted(changed),
            )
        commit(self.db)
        self._dispatch("deployment.updated", deployment_id, deployment.model_version_id, changed)
        return deployment

    def transition_deployment(
        self, deployment_id: UUID, payload: DeploymentTransitionRequest
    ) -> ModelDeployment:
        deployment = self._get_or_404(deployment_id)
        self._assert_not_archived(deployment)

        current = deployment.status
        target = payload.status
        if not can_deployment_transition(current, target):
            raise InvalidDeploymentTransitionError(
                f"cannot transition deployment from {current} to {target}"
            )

        deployment.status = target
        deployment.status_source = "api"
        if payload.observed_at is not None:
            deployment.last_seen_at = payload.observed_at
        summary = [f"{current}->{target}"]
        if payload.reason:
            summary.append(payload.reason)
        self.audit.record_event(
            event_type=AuditEventType.DEPLOYMENT_STATUS_CHANGED,
            resource_type="deployment",
            resource_id=str(deployment_id),
            before_state={"status": current},
            after_state={"status": target},
            changed_fields=["status"],
        )
        commit(self.db)
        self._dispatch(
            "deployment.status_changed", deployment_id, deployment.model_version_id, summary
        )
        return deployment

    def archive_deployment(self, deployment_id: UUID) -> None:
        deployment = self._get_or_404(deployment_id)
        if deployment.archived_at is not None:
            return
        before_state = deployment_audit_state(deployment)
        deployment.status = "deprecated"
        deployment.archived_at = datetime.now(UTC)
        self.audit.record_event(
            event_type=AuditEventType.DEPLOYMENT_ARCHIVED,
            resource_type="deployment",
            resource_id=str(deployment_id),
            before_state=before_state,
            after_state=deployment_audit_state(deployment),
            changed_fields=["archived_at", "status"],
        )
        commit(self.db)
        self._dispatch(
            "deployment.archived", deployment_id, deployment.model_version_id, ["archived"]
        )

    def _get_or_404(self, deployment_id: UUID) -> ModelDeployment:
        deployment = get_deployment(self.db, self.tenant_id, deployment_id)
        if deployment is None:
            raise DeploymentNotFoundError(f"deployment '{deployment_id}' not found")
        return deployment

    def _get_version_or_404(self, model_version_id: UUID) -> ModelVersion:
        version = self.db.get(ModelVersion, model_version_id)
        if version is None or version.tenant_id != self.tenant_id:
            raise ModelVersionNotFoundError(f"model version '{model_version_id}' not found")
        return version

    def _assert_not_archived(self, deployment: ModelDeployment) -> None:
        if deployment.archived_at is not None:
            raise DeploymentAlreadyArchivedError(f"deployment '{deployment.id}' is archived")

    def _dispatch(
        self, event_type: str, deployment_id: UUID, model_version_id: UUID, summary: list[str]
    ) -> None:
        dispatch_event(
            DeploymentEvent(
                event_id=str(uuid4()),
                event_type=event_type,
                tenant_id=self.tenant_id,
                deployment_id=deployment_id,
                model_version_id=model_version_id,
                occurred_at=datetime.now(UTC),
                actor=self.actor,
                change_summary=summary,
                request_id=self.request_id,
            )
        )
