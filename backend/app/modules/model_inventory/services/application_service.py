from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.modules.model_inventory.domain import (
    ApplicationEvent,
    can_status_transition,
    dispatch_event,
)
from app.modules.model_inventory.domain.errors import (
    ApplicationNotFoundError,
    DuplicateApplicationError,
    InvalidStatusTransitionError,
)
from app.modules.model_inventory.models import Application
from app.modules.model_inventory.repositories.application_repository import (
    ApplicationFilters,
    commit,
    create_application,
    find_duplicate_slug,
    get_application,
    query_applications,
)
from app.modules.model_inventory.schemas import ApplicationCreate, ApplicationUpdate

_MUTABLE_FIELDS = (
    "name",
    "slug",
    "display_name",
    "description",
    "application_type",
    "owner_name",
    "team_name",
    "source",
)


class ApplicationService:
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

    def create_application(self, payload: ApplicationCreate) -> Application:
        if find_duplicate_slug(self.db, self.tenant_id, payload.slug) is not None:
            raise DuplicateApplicationError(
                f"application with slug '{payload.slug}' already exists"
            )
        application = create_application(
            self.db,
            id=uuid4(),
            tenant_id=self.tenant_id,
            name=payload.name,
            slug=payload.slug,
            display_name=payload.display_name,
            description=payload.description,
            application_type=payload.application_type,
            owner_name=payload.owner_name,
            team_name=payload.team_name,
            status=payload.status,
            source=payload.source,
            tags=payload.tags,
            metadata_=payload.metadata,
            created_by=self.actor,
        )
        application_id = application.id
        commit(self.db)
        self._dispatch("application.created", application_id, ["created"])
        return application

    def get_application(self, application_id: UUID) -> Application:
        application = get_application(self.db, self.tenant_id, application_id)
        if application is None:
            raise ApplicationNotFoundError(f"application '{application_id}' not found")
        return application

    def list_applications(
        self,
        filters: ApplicationFilters | None = None,
        page: int = 1,
        page_size: int = 25,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        include_archived: bool = False,
    ) -> tuple[list[Application], int]:
        return query_applications(
            self.db,
            self.tenant_id,
            filters,
            page,
            page_size,
            sort_by,
            sort_order,
            include_archived,
        )

    def update_application(self, application_id: UUID, payload: ApplicationUpdate) -> Application:
        application = self.get_application(application_id)

        changed: list[str] = []
        if payload.status is not None and payload.status != application.status:
            if not can_status_transition(application.status, payload.status):
                raise InvalidStatusTransitionError(
                    f"cannot transition application from {application.status} to {payload.status}"
                )
            changed.append("status")
            application.status = payload.status
            if payload.status == "ARCHIVED":
                application.archived_at = datetime.now(UTC)
        if (
            payload.slug is not None
            and payload.slug != application.slug
            and find_duplicate_slug(self.db, self.tenant_id, payload.slug) is not None
        ):
            raise DuplicateApplicationError(
                f"application with slug '{payload.slug}' already exists"
            )
        for field in _MUTABLE_FIELDS:
            value = getattr(payload, field)
            if value is not None and value != getattr(application, field):
                changed.append(field)
                setattr(application, field, value)
        if payload.tags is not None and payload.tags != application.tags:
            changed.append("tags")
            application.tags = payload.tags
        if payload.metadata is not None and payload.metadata != application.metadata_:
            changed.append("metadata")
            application.metadata_ = payload.metadata

        if changed:
            if self.actor is not None:
                application.updated_by = self.actor
            commit(self.db)
            self._dispatch("application.updated", application_id, changed)
        return application

    def archive_application(self, application_id: UUID) -> None:
        application = self.get_application(application_id)
        if application.status == "ARCHIVED":
            return
        application.status = "ARCHIVED"
        application.archived_at = datetime.now(UTC)
        if self.actor is not None:
            application.updated_by = self.actor
        commit(self.db)
        self._dispatch("application.archived", application_id, ["archived"])

    def _dispatch(self, event_type: str, application_id: UUID, summary: list[str]) -> None:
        dispatch_event(
            ApplicationEvent(
                event_id=str(uuid4()),
                event_type=event_type,
                tenant_id=self.tenant_id,
                application_id=application_id,
                occurred_at=datetime.now(UTC),
                actor=self.actor,
                change_summary=summary,
                request_id=self.request_id,
            )
        )
