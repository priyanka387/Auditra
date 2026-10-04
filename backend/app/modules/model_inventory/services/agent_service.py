from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.modules.model_inventory.domain import (
    AgentEvent,
    can_status_transition,
    dispatch_event,
)
from app.modules.model_inventory.domain.errors import (
    AgentNotFoundError,
    ApplicationArchivedError,
    ApplicationNotFoundError,
    DuplicateAgentError,
    InvalidStatusTransitionError,
)
from app.modules.model_inventory.models import Agent, Application
from app.modules.model_inventory.repositories.agent_repository import (
    AgentFilters,
    commit,
    create_agent,
    find_duplicate_slug,
    get_agent,
    query_agents,
)
from app.modules.model_inventory.repositories.application_repository import (
    get_application as get_application_row,
)
from app.modules.model_inventory.schemas import AgentCreate, AgentUpdate

_MUTABLE_FIELDS = (
    "name",
    "slug",
    "display_name",
    "description",
    "agent_type",
    "framework",
    "framework_version",
    "runtime_identifier",
    "owner_name",
    "team_name",
    "source",
)


class AgentService:
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

    def create_agent(self, application_id: UUID, payload: AgentCreate) -> Agent:
        application = self._get_application_or_404(application_id)
        if application.status == "ARCHIVED":
            raise ApplicationArchivedError(
                f"application '{application_id}' is archived and cannot accept new agents"
            )
        if find_duplicate_slug(self.db, self.tenant_id, application_id, payload.slug) is not None:
            raise DuplicateAgentError(
                f"agent with slug '{payload.slug}' already exists in this application"
            )
        agent = create_agent(
            self.db,
            id=uuid4(),
            tenant_id=self.tenant_id,
            application_id=application_id,
            name=payload.name,
            slug=payload.slug,
            display_name=payload.display_name,
            description=payload.description,
            agent_type=payload.agent_type,
            framework=payload.framework,
            framework_version=payload.framework_version,
            runtime_identifier=payload.runtime_identifier,
            owner_name=payload.owner_name,
            team_name=payload.team_name,
            status=payload.status,
            source=payload.source,
            capabilities=payload.capabilities,
            tags=payload.tags,
            metadata_=payload.metadata,
            created_by=self.actor,
        )
        agent_id = agent.id
        commit(self.db)
        self._dispatch("agent.created", application_id, agent_id, ["created"])
        return agent

    def get_agent(self, agent_id: UUID) -> Agent:
        agent = get_agent(self.db, self.tenant_id, agent_id)
        if agent is None:
            raise AgentNotFoundError(f"agent '{agent_id}' not found")
        return agent

    def list_agents(
        self,
        filters: AgentFilters | None = None,
        page: int = 1,
        page_size: int = 25,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        include_archived: bool = False,
        application_id: UUID | None = None,
    ) -> tuple[list[Agent], int]:
        if application_id is not None:
            self._get_application_or_404(application_id)
            if filters is None:
                filters = AgentFilters()
            filters.application_id = application_id
        return query_agents(
            self.db,
            self.tenant_id,
            filters,
            page,
            page_size,
            sort_by,
            sort_order,
            include_archived,
        )

    def update_agent(self, agent_id: UUID, payload: AgentUpdate) -> Agent:
        agent = self.get_agent(agent_id)

        changed: list[str] = []
        if payload.status is not None and payload.status != agent.status:
            if not can_status_transition(agent.status, payload.status):
                raise InvalidStatusTransitionError(
                    f"cannot transition agent from {agent.status} to {payload.status}"
                )
            changed.append("status")
            agent.status = payload.status
            if payload.status == "ARCHIVED":
                agent.archived_at = datetime.now(UTC)
        if (
            payload.slug is not None
            and payload.slug != agent.slug
            and find_duplicate_slug(self.db, self.tenant_id, agent.application_id, payload.slug)
            is not None
        ):
            raise DuplicateAgentError(
                f"agent with slug '{payload.slug}' already exists in this application"
            )
        for field in _MUTABLE_FIELDS:
            value = getattr(payload, field)
            if value is not None and value != getattr(agent, field):
                changed.append(field)
                setattr(agent, field, value)
        if payload.capabilities is not None and payload.capabilities != agent.capabilities:
            changed.append("capabilities")
            agent.capabilities = payload.capabilities
        if payload.tags is not None and payload.tags != agent.tags:
            changed.append("tags")
            agent.tags = payload.tags
        if payload.metadata is not None and payload.metadata != agent.metadata_:
            changed.append("metadata")
            agent.metadata_ = payload.metadata

        if changed:
            if self.actor is not None:
                agent.updated_by = self.actor
            commit(self.db)
            self._dispatch("agent.updated", agent.application_id, agent_id, changed)
        return agent

    def archive_agent(self, agent_id: UUID) -> None:
        agent = self.get_agent(agent_id)
        if agent.status == "ARCHIVED":
            return
        agent.status = "ARCHIVED"
        agent.archived_at = datetime.now(UTC)
        if self.actor is not None:
            agent.updated_by = self.actor
        commit(self.db)
        self._dispatch("agent.archived", agent.application_id, agent_id, ["archived"])

    def _get_application_or_404(self, application_id: UUID) -> Application:
        application = get_application_row(self.db, self.tenant_id, application_id)
        if application is None:
            raise ApplicationNotFoundError(f"application '{application_id}' not found")
        return application

    def _dispatch(
        self, event_type: str, application_id: UUID, agent_id: UUID, summary: list[str]
    ) -> None:
        dispatch_event(
            AgentEvent(
                event_id=str(uuid4()),
                event_type=event_type,
                tenant_id=self.tenant_id,
                application_id=application_id,
                agent_id=agent_id,
                occurred_at=datetime.now(UTC),
                actor=self.actor,
                change_summary=summary,
                request_id=self.request_id,
            )
        )
