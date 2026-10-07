from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.modules.audit import repository
from app.modules.audit.enums import EVENT_TYPE_PATTERN
from app.modules.audit.errors import (
    AuditEventNotFoundError,
    InvalidAuditEventError,
    InvalidAuditFilterError,
)
from app.modules.audit.models import AuditEvent
from app.modules.audit.repository import AuditFilters
from app.modules.audit.sanitization import sanitize_state

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ActorContext:
    type: str
    id: str | None


def actor_context(actor: str | None) -> ActorContext:
    return ActorContext(type="user" if actor else "system", id=actor)


def validate_event_type(event_type: str) -> None:
    if not event_type or not EVENT_TYPE_PATTERN.match(event_type):
        raise InvalidAuditEventError(f"invalid event type '{event_type}'")


def validate_resource(resource_type: str, resource_id: str) -> None:
    if not resource_type or not resource_type.strip():
        raise InvalidAuditEventError("resource_type must be non-empty")
    if not resource_id or not resource_id.strip():
        raise InvalidAuditEventError("resource_id must be non-empty")


class AuditService:
    def __init__(
        self,
        db: Session,
        tenant_id: UUID,
        request_id: str | None = None,
        actor: str | None = None,
        source: str = "api",
    ) -> None:
        if not source:
            raise InvalidAuditEventError("source must be non-empty")
        self.db = db
        self.tenant_id = tenant_id
        self.request_id = request_id
        self.actor = actor
        self.source = source

    def record_event(
        self,
        *,
        event_type: str,
        resource_type: str,
        resource_id: str,
        before_state: dict | None = None,
        after_state: dict | None = None,
        changed_fields: list[str] | None = None,
        metadata: dict | None = None,
    ) -> AuditEvent:
        """Stage an audit event on the caller's session; commits are the caller's.

        Runs in the same transaction as the governed mutation (spec §22): the
        caller adds domain changes, calls record_event, then commits once.
        """
        validate_event_type(str(event_type))
        validate_resource(resource_type, str(resource_id))
        actor = actor_context(self.actor)
        now = datetime.now(UTC)
        event = AuditEvent(
            id=uuid4(),
            tenant_id=self.tenant_id,
            event_type=str(event_type),
            resource_type=resource_type,
            resource_id=str(resource_id),
            actor_type=actor.type,
            actor_id=actor.id,
            source=self.source,
            occurred_at=now,
            recorded_at=now,
            request_id=self.request_id,
            schema_version=SCHEMA_VERSION,
            changed_fields=list(changed_fields) if changed_fields is not None else None,
            before_state=sanitize_state(before_state),
            after_state=sanitize_state(after_state),
            metadata_=sanitize_state(metadata),
        )
        repository.append(self.db, event)
        return event

    def get_event(self, event_id: UUID) -> AuditEvent:
        event = repository.get_event(self.db, self.tenant_id, event_id)
        if event is None:
            raise AuditEventNotFoundError(f"audit event '{event_id}' not found")
        return event

    def list_events(
        self, filters: AuditFilters, page: int = 1, page_size: int = 50
    ) -> tuple[list[AuditEvent], int]:
        if (
            filters.occurred_from is not None
            and filters.occurred_to is not None
            and filters.occurred_from > filters.occurred_to
        ):
            raise InvalidAuditFilterError("occurred_from must not be after occurred_to")
        if filters.event_type is not None and not EVENT_TYPE_PATTERN.match(filters.event_type):
            raise InvalidAuditFilterError(f"invalid event type '{filters.event_type}'")
        return repository.list_events(self.db, self.tenant_id, filters, page, page_size)
