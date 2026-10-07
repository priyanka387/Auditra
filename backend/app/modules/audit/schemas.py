from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class AuditActor(BaseModel):
    type: str
    id: str | None = None


class AuditEventResponse(BaseModel):
    id: UUID
    sequence_no: int
    event_type: str
    resource_type: str
    resource_id: str
    actor: AuditActor
    source: str
    occurred_at: datetime
    recorded_at: datetime
    request_id: str | None = None
    correlation_id: str | None = None
    schema_version: int
    changed_fields: list[str] | None = None
    before_state: dict | None = None
    after_state: dict | None = None
    metadata: dict | None = None

    @classmethod
    def from_event(cls, event) -> "AuditEventResponse":
        return cls(
            id=event.id,
            sequence_no=event.sequence_no,
            event_type=event.event_type,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            actor=AuditActor(type=event.actor_type, id=event.actor_id),
            source=event.source,
            occurred_at=event.occurred_at,
            recorded_at=event.recorded_at,
            request_id=event.request_id,
            correlation_id=event.correlation_id,
            schema_version=event.schema_version,
            changed_fields=event.changed_fields,
            before_state=event.before_state,
            after_state=event.after_state,
            metadata=event.metadata_,
        )


class AuditEventListResponse(BaseModel):
    items: list[AuditEventResponse]
    page: int
    page_size: int
    total: int
    total_pages: int
