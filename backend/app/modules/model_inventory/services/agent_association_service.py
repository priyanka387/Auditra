from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.modules.model_inventory.domain import (
    AgentModelAssociationEvent,
    dispatch_event,
)
from app.modules.model_inventory.domain.errors import (
    AgentNotActiveError,
    AgentNotFoundError,
    AssociationNotFoundError,
    DuplicateAssociationError,
    ModelNotAssociableError,
    ModelNotFoundError,
    ModelVersionMismatchError,
    ModelVersionNotFoundError,
)
from app.modules.model_inventory.models import (
    Agent,
    AgentModelAssociation,
    Application,
    Model,
    ModelVersion,
)
from app.modules.model_inventory.repositories.agent_model_association_repository import (
    AssociationFilters,
    commit,
    create_association,
    find_duplicate_model_role,
    find_duplicate_role_priority,
    get_association,
    query_agent_associations,
    query_model_agents,
)
from app.modules.model_inventory.repositories.agent_repository import get_agent
from app.modules.model_inventory.repositories.model_repository import get_model
from app.modules.model_inventory.schemas import (
    AgentModelAssociationCreate,
    AgentModelAssociationUpdate,
)

_MUTABLE_FIELDS = ("role", "selection_priority", "configuration")


class AgentAssociationService:
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

    def create_association(
        self, agent_id: UUID, payload: AgentModelAssociationCreate
    ) -> AgentModelAssociation:
        agent = self._get_agent_or_404(agent_id)
        if payload.status == "ACTIVE" and agent.status != "ACTIVE":
            raise AgentNotActiveError(
                f"agent '{agent_id}' is {agent.status} and cannot receive new associations"
            )
        model = self._get_model_or_404(payload.model_id)
        if payload.status == "ACTIVE" and model.lifecycle_state == "RETIRED":
            raise ModelNotAssociableError(
                f"model '{payload.model_id}' is retired and cannot be associated"
            )
        if payload.model_version_id is not None:
            self._validate_version(payload.model_id, payload.model_version_id)
        if payload.status == "ACTIVE":
            self._ensure_not_duplicate(
                agent_id, payload.model_id, payload.role, payload.selection_priority
            )

        association = create_association(
            self.db,
            id=uuid4(),
            tenant_id=self.tenant_id,
            agent_id=agent_id,
            model_id=payload.model_id,
            model_version_id=payload.model_version_id,
            role=payload.role,
            selection_priority=payload.selection_priority,
            status=payload.status,
            source=payload.source,
            configuration=payload.configuration,
            metadata_=payload.metadata,
            created_by=self.actor,
        )
        association_id = association.id
        commit(self.db)
        self._dispatch(
            "agent_model_association.created",
            agent_id,
            payload.model_id,
            association_id,
            ["created"],
        )
        return association

    def get_association(self, agent_id: UUID, association_id: UUID) -> AgentModelAssociation:
        association = get_association(self.db, self.tenant_id, association_id)
        if association is None or association.agent_id != agent_id:
            raise AssociationNotFoundError(f"association '{association_id}' not found")
        return association

    def list_associations(
        self,
        agent_id: UUID,
        filters: AssociationFilters | None = None,
        page: int = 1,
        page_size: int = 25,
        sort_by: str = "selection_priority",
        sort_order: str = "asc",
    ) -> tuple[list[AgentModelAssociation], int]:
        self._get_agent_or_404(agent_id)
        return query_agent_associations(
            self.db, self.tenant_id, agent_id, filters, page, page_size, sort_by, sort_order
        )

    def update_association(
        self,
        agent_id: UUID,
        association_id: UUID,
        payload: AgentModelAssociationUpdate,
    ) -> AgentModelAssociation:
        association = self.get_association(agent_id, association_id)

        proposed_status = payload.status if payload.status is not None else association.status
        proposed_role = payload.role if payload.role is not None else association.role
        proposed_priority = (
            payload.selection_priority
            if payload.selection_priority is not None
            else association.selection_priority
        )
        if proposed_status == "ACTIVE":
            self._ensure_not_duplicate(
                association.agent_id,
                association.model_id,
                proposed_role,
                proposed_priority,
                exclude_id=association.id,
            )

        changed: list[str] = []
        if payload.status is not None and payload.status != association.status:
            changed.append("status")
            association.status = payload.status
            association.disabled_at = datetime.now(UTC) if payload.status == "DISABLED" else None
        if (
            payload.model_version_id is not None
            and payload.model_version_id != association.model_version_id
        ):
            self._validate_version(association.model_id, payload.model_version_id)
            changed.append("model_version_id")
            association.model_version_id = payload.model_version_id
        for field in _MUTABLE_FIELDS:
            value = getattr(payload, field)
            if value is not None and value != getattr(association, field):
                changed.append(field)
                setattr(association, field, value)
        if payload.metadata is not None and payload.metadata != association.metadata_:
            changed.append("metadata")
            association.metadata_ = payload.metadata

        if changed:
            if self.actor is not None:
                association.updated_by = self.actor
            commit(self.db)
            self._dispatch(
                "agent_model_association.updated",
                association.agent_id,
                association.model_id,
                association.id,
                changed,
            )
        return association

    def disable_association(self, agent_id: UUID, association_id: UUID) -> None:
        association = self.get_association(agent_id, association_id)
        if association.status == "DISABLED":
            return
        association.status = "DISABLED"
        association.disabled_at = datetime.now(UTC)
        if self.actor is not None:
            association.updated_by = self.actor
        commit(self.db)
        self._dispatch(
            "agent_model_association.disabled",
            association.agent_id,
            association.model_id,
            association.id,
            ["disabled"],
        )

    def list_model_agents(
        self,
        model_id: UUID,
        *,
        role: str | None = None,
        status: str | None = None,
        application_id: UUID | None = None,
        page: int = 1,
        page_size: int = 25,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> tuple[list[tuple[AgentModelAssociation, Agent, Application]], int]:
        if get_model(self.db, self.tenant_id, model_id) is None:
            raise ModelNotFoundError(f"model '{model_id}' not found")
        filters = AssociationFilters(status=status, role=role, application_id=application_id)
        return query_model_agents(
            self.db, self.tenant_id, model_id, filters, page, page_size, sort_by, sort_order
        )

    def _ensure_not_duplicate(
        self,
        agent_id: UUID,
        model_id: UUID,
        role: str,
        selection_priority: int,
        exclude_id: UUID | None = None,
    ) -> None:
        if (
            find_duplicate_role_priority(
                self.db, self.tenant_id, agent_id, role, selection_priority, exclude_id
            )
            is not None
            or find_duplicate_model_role(
                self.db, self.tenant_id, agent_id, model_id, role, exclude_id
            )
            is not None
        ):
            raise DuplicateAssociationError(
                f"active association with role '{role}' and priority "
                f"{selection_priority} already exists for this agent"
            )

    def _validate_version(self, model_id: UUID, version_id: UUID) -> None:
        version = self.db.get(ModelVersion, version_id)
        if version is None or version.tenant_id != self.tenant_id:
            raise ModelVersionNotFoundError(f"model version '{version_id}' not found")
        if version.model_id != model_id:
            raise ModelVersionMismatchError(
                f"model version '{version_id}' belongs to a different model"
            )

    def _get_agent_or_404(self, agent_id: UUID) -> Agent:
        agent = get_agent(self.db, self.tenant_id, agent_id)
        if agent is None:
            raise AgentNotFoundError(f"agent '{agent_id}' not found")
        return agent

    def _get_model_or_404(self, model_id: UUID) -> Model:
        model = get_model(self.db, self.tenant_id, model_id)
        if model is None:
            raise ModelNotFoundError(f"model '{model_id}' not found")
        return model

    def _dispatch(
        self,
        event_type: str,
        agent_id: UUID,
        model_id: UUID,
        association_id: UUID,
        summary: list[str],
    ) -> None:
        dispatch_event(
            AgentModelAssociationEvent(
                event_id=str(uuid4()),
                event_type=event_type,
                tenant_id=self.tenant_id,
                agent_id=agent_id,
                model_id=model_id,
                association_id=association_id,
                occurred_at=datetime.now(UTC),
                actor=self.actor,
                change_summary=summary,
                request_id=self.request_id,
            )
        )
