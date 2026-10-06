from datetime import datetime
from math import ceil
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.modules.model_usage.enums import UsageSource, UsageStatus
from app.modules.model_usage.errors import InvalidUsageQueryError
from app.modules.model_usage.schemas import (
    UsageEventBatchCreate,
    UsageEventBatchResponse,
    UsageEventCreate,
    UsageEventFilter,
    UsageEventListResponse,
    UsageEventResponse,
    UsageStatsQuery,
    UsageStatsResponse,
)
from app.modules.model_usage.service import ModelUsageService

router = APIRouter(prefix="/api/v1/model-usage")

_CREATED_DOC = "201 for a new event, 200 when an identical event_id is replayed (idempotent)."


def _service(request: Request, db: Session) -> ModelUsageService:
    return ModelUsageService(
        db, settings.default_tenant_id, request_id=request.state.request_id, actor=None
    )


def _build(model_cls, **kwargs):
    try:
        return model_cls(**kwargs)
    except ValidationError as exc:
        message = exc.errors()[0].get("msg", "invalid query") if exc.errors() else "invalid query"
        raise InvalidUsageQueryError(message) from None


@router.post(
    "/events",
    status_code=201,
    response_model=UsageEventResponse,
    responses={200: {"model": UsageEventResponse, "description": "Duplicate event replayed"}},
    description=_CREATED_DOC,
)
def create_event(
    payload: UsageEventCreate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    response: Response,
) -> UsageEventResponse:
    result, created = _service(request, db).record_event(payload)
    response.status_code = 201 if created else 200
    return result


@router.post(
    "/events/batch",
    response_model=UsageEventBatchResponse,
    summary="Batch-ingest usage events",
    description=(
        "Each event is processed independently: valid events are accepted, replayed "
        "event_ids are reported as duplicates, invalid events are rejected with an error."
    ),
)
def create_event_batch(
    payload: UsageEventBatchCreate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> UsageEventBatchResponse:
    return _service(request, db).record_batch(payload.events)


@router.get("/events/{event_id}", response_model=UsageEventResponse)
def get_event(
    event_id: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> UsageEventResponse:
    return _service(request, db).get_event(event_id)


@router.get("/events", response_model=UsageEventListResponse)
def list_events(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    model_id: UUID | None = None,
    model_version_id: UUID | None = None,
    deployment_id: UUID | None = None,
    application_id: UUID | None = None,
    agent_id: UUID | None = None,
    status: UsageStatus | None = None,
    source: UsageSource | None = None,
    environment: str | None = None,
    started_from: datetime | None = None,
    started_to: datetime | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
    operation_name: str | None = None,
    sort_by: Literal["started_at", "created_at", "duration_ms"] = "started_at",
    sort_order: Literal["asc", "desc"] = "desc",
) -> UsageEventListResponse:
    filters = _build(
        UsageEventFilter,
        model_id=model_id,
        model_version_id=model_version_id,
        deployment_id=deployment_id,
        application_id=application_id,
        agent_id=agent_id,
        status=status,
        source=source,
        environment=environment,
        started_from=started_from,
        started_to=started_to,
        request_id=request_id,
        trace_id=trace_id,
        operation_name=operation_name,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    items, total = _service(request, db).list_events(filters)
    return UsageEventListResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        total_pages=ceil(total / page_size) if total else 0,
    )


@router.get("/stats", response_model=UsageStatsResponse)
def usage_stats(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    started_from: Annotated[datetime, Query()],
    started_to: Annotated[datetime, Query()],
    model_id: UUID | None = None,
    model_version_id: UUID | None = None,
    deployment_id: UUID | None = None,
    application_id: UUID | None = None,
    agent_id: UUID | None = None,
    environment: str | None = None,
    granularity: Literal["total", "hour", "day"] = "total",
) -> UsageStatsResponse:
    query = _build(
        UsageStatsQuery,
        model_id=model_id,
        model_version_id=model_version_id,
        deployment_id=deployment_id,
        application_id=application_id,
        agent_id=agent_id,
        environment=environment,
        started_from=started_from,
        started_to=started_to,
        granularity=granularity,
    )
    return _service(request, db).get_stats(query)
