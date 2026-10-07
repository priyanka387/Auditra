from datetime import UTC, datetime
from math import ceil
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.modules.audit.repository import AuditFilters
from app.modules.audit.schemas import AuditEventListResponse, AuditEventResponse
from app.modules.audit.service import AuditService

router = APIRouter(prefix="/api/v1")


def get_audit_reader() -> None:
    """Authorization extension point for audit reads.

    No IAM exists yet, so this is a pass-through. A future RBAC system
    enforces ``ai_inventory.audit.read`` here (401/403) without touching
    audit storage or query logic.
    """
    return


def _service(request: Request, db: Session) -> AuditService:
    return AuditService(db, settings.default_tenant_id, request_id=request.state.request_id)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


@router.get(
    "/audit/events",
    response_model=AuditEventListResponse,
    dependencies=[Depends(get_audit_reader)],
)
def list_events(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    resource_type: str | None = None,
    resource_id: str | None = None,
    event_type: str | None = None,
    actor_type: str | None = None,
    actor_id: str | None = None,
    source: str | None = None,
    request_id: str | None = None,
    correlation_id: str | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditEventListResponse:
    filters = AuditFilters(
        resource_type=resource_type,
        resource_id=resource_id,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        source=source,
        request_id=request_id,
        correlation_id=correlation_id,
        occurred_from=_as_utc(occurred_from),
        occurred_to=_as_utc(occurred_to),
    )
    items, total = _service(request, db).list_events(filters, page=page, page_size=page_size)
    return AuditEventListResponse(
        items=[AuditEventResponse.from_event(item) for item in items],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=ceil(total / page_size),
    )


@router.get(
    "/audit/events/{event_id}",
    response_model=AuditEventResponse,
    dependencies=[Depends(get_audit_reader)],
)
def get_event(
    event_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> AuditEventResponse:
    return AuditEventResponse.from_event(_service(request, db).get_event(event_id))
