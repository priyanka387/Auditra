from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditEvent


@dataclass
class AuditFilters:
    resource_type: str | None = None
    resource_id: str | None = None
    event_type: str | None = None
    actor_type: str | None = None
    actor_id: str | None = None
    source: str | None = None
    request_id: str | None = None
    correlation_id: str | None = None
    occurred_from: datetime | None = None
    occurred_to: datetime | None = None


def _conditions(tenant_id: UUID, filters: AuditFilters) -> list:
    conditions = [AuditEvent.tenant_id == tenant_id]
    for column, value in (
        (AuditEvent.resource_type, filters.resource_type),
        (AuditEvent.resource_id, filters.resource_id),
        (AuditEvent.event_type, filters.event_type),
        (AuditEvent.actor_type, filters.actor_type),
        (AuditEvent.actor_id, filters.actor_id),
        (AuditEvent.source, filters.source),
        (AuditEvent.request_id, filters.request_id),
        (AuditEvent.correlation_id, filters.correlation_id),
    ):
        if value is not None:
            conditions.append(column == value)
    if filters.occurred_from is not None:
        conditions.append(AuditEvent.occurred_at >= filters.occurred_from)
    if filters.occurred_to is not None:
        conditions.append(AuditEvent.occurred_at <= filters.occurred_to)
    return conditions


def append(db: Session, event: AuditEvent) -> None:
    db.add(event)


def get_event(db: Session, tenant_id: UUID, event_id: UUID) -> AuditEvent | None:
    return db.scalar(
        select(AuditEvent).where(AuditEvent.tenant_id == tenant_id, AuditEvent.id == event_id)
    )


def list_events(
    db: Session,
    tenant_id: UUID,
    filters: AuditFilters,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[AuditEvent], int]:
    conditions = _conditions(tenant_id, filters)
    total = db.scalar(select(func.count()).select_from(AuditEvent).where(*conditions)) or 0
    rows = db.scalars(
        select(AuditEvent)
        .where(*conditions)
        .order_by(AuditEvent.sequence_no.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return list(rows), total
