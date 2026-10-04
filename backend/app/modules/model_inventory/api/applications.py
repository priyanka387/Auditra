from datetime import UTC, datetime
from math import ceil
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.modules.model_inventory.repositories import ApplicationFilters
from app.modules.model_inventory.schemas import (
    ApplicationCreate,
    ApplicationListResponse,
    ApplicationResponse,
    ApplicationUpdate,
)
from app.modules.model_inventory.services import ApplicationService

application_router = APIRouter(prefix="/api/v1")


def _service(request: Request, db: Session) -> ApplicationService:
    return ApplicationService(
        db, settings.default_tenant_id, request_id=request.state.request_id, actor=None
    )


def _response(application) -> ApplicationResponse:
    return ApplicationResponse.model_validate(application, from_attributes=True)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


@application_router.post("/applications", status_code=201, response_model=ApplicationResponse)
def create_application(
    payload: ApplicationCreate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> ApplicationResponse:
    application = _service(request, db).create_application(payload)
    return _response(application)


@application_router.get("/applications", response_model=ApplicationListResponse)
def list_applications(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    search: str | None = None,
    status: str | None = None,
    application_type: str | None = None,
    owner: str | None = None,
    team: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    updated_after: datetime | None = None,
    updated_before: datetime | None = None,
    include_archived: bool = False,
    sort_by: str = "created_at",
    sort_order: Literal["asc", "desc"] = "desc",
) -> ApplicationListResponse:
    filters = ApplicationFilters(
        search=search,
        status=status,
        application_type=application_type,
        owner=owner,
        team=team,
        created_after=_as_utc(created_after),
        created_before=_as_utc(created_before),
        updated_after=_as_utc(updated_after),
        updated_before=_as_utc(updated_before),
    )
    items, total = _service(request, db).list_applications(
        filters,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
        include_archived=include_archived,
    )
    return ApplicationListResponse(
        items=[_response(item) for item in items],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=ceil(total / page_size),
    )


@application_router.get("/applications/{application_id}", response_model=ApplicationResponse)
def get_application(
    application_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> ApplicationResponse:
    return _response(_service(request, db).get_application(application_id))


@application_router.patch("/applications/{application_id}", response_model=ApplicationResponse)
def update_application(
    application_id: UUID,
    payload: ApplicationUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> ApplicationResponse:
    return _response(_service(request, db).update_application(application_id, payload))


@application_router.delete(
    "/applications/{application_id}", status_code=204, response_class=Response
)
def archive_application(
    application_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    _service(request, db).archive_application(application_id)
    return Response(status_code=204)
