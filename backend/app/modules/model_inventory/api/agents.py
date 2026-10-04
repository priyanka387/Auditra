from datetime import UTC, datetime
from math import ceil
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.modules.model_inventory.repositories import AgentFilters
from app.modules.model_inventory.schemas import (
    AgentCreate,
    AgentListResponse,
    AgentResponse,
    AgentUpdate,
)
from app.modules.model_inventory.services import AgentService

agent_router = APIRouter(prefix="/api/v1")


def _service(request: Request, db: Session) -> AgentService:
    return AgentService(
        db, settings.default_tenant_id, request_id=request.state.request_id, actor=None
    )


def _response(agent) -> AgentResponse:
    return AgentResponse.model_validate(agent, from_attributes=True)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _list_agents(
    request: Request,
    db: Session,
    *,
    application_id: UUID | None,
    page: int,
    page_size: int,
    search: str | None,
    status: str | None,
    framework: str | None,
    agent_type: str | None,
    owner: str | None,
    team: str | None,
    created_after: datetime | None,
    created_before: datetime | None,
    updated_after: datetime | None,
    updated_before: datetime | None,
    include_archived: bool,
    sort_by: str,
    sort_order: str,
) -> AgentListResponse:
    filters = AgentFilters(
        search=search,
        status=status,
        framework=framework,
        agent_type=agent_type,
        owner=owner,
        team=team,
        created_after=_as_utc(created_after),
        created_before=_as_utc(created_before),
        updated_after=_as_utc(updated_after),
        updated_before=_as_utc(updated_before),
    )
    items, total = _service(request, db).list_agents(
        filters,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
        include_archived=include_archived,
        application_id=application_id,
    )
    return AgentListResponse(
        items=[_response(item) for item in items],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=ceil(total / page_size),
    )


@agent_router.post(
    "/applications/{application_id}/agents", status_code=201, response_model=AgentResponse
)
def create_agent(
    application_id: UUID,
    payload: AgentCreate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> AgentResponse:
    agent = _service(request, db).create_agent(application_id, payload)
    return _response(agent)


@agent_router.get("/applications/{application_id}/agents", response_model=AgentListResponse)
def list_application_agents(
    application_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    search: str | None = None,
    status: str | None = None,
    framework: str | None = None,
    agent_type: str | None = None,
    owner: str | None = None,
    team: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    updated_after: datetime | None = None,
    updated_before: datetime | None = None,
    include_archived: bool = False,
    sort_by: str = "created_at",
    sort_order: Literal["asc", "desc"] = "desc",
) -> AgentListResponse:
    return _list_agents(
        request,
        db,
        application_id=application_id,
        page=page,
        page_size=page_size,
        search=search,
        status=status,
        framework=framework,
        agent_type=agent_type,
        owner=owner,
        team=team,
        created_after=created_after,
        created_before=created_before,
        updated_after=updated_after,
        updated_before=updated_before,
        include_archived=include_archived,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@agent_router.get("/agents", response_model=AgentListResponse)
def list_agents(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    search: str | None = None,
    status: str | None = None,
    framework: str | None = None,
    agent_type: str | None = None,
    application_id: UUID | None = None,
    owner: str | None = None,
    team: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    updated_after: datetime | None = None,
    updated_before: datetime | None = None,
    include_archived: bool = False,
    sort_by: str = "created_at",
    sort_order: Literal["asc", "desc"] = "desc",
) -> AgentListResponse:
    return _list_agents(
        request,
        db,
        application_id=application_id,
        page=page,
        page_size=page_size,
        search=search,
        status=status,
        framework=framework,
        agent_type=agent_type,
        owner=owner,
        team=team,
        created_after=created_after,
        created_before=created_before,
        updated_after=updated_after,
        updated_before=updated_before,
        include_archived=include_archived,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@agent_router.get("/agents/{agent_id}", response_model=AgentResponse)
def get_agent(
    agent_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> AgentResponse:
    return _response(_service(request, db).get_agent(agent_id))


@agent_router.patch("/agents/{agent_id}", response_model=AgentResponse)
def update_agent(
    agent_id: UUID,
    payload: AgentUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> AgentResponse:
    return _response(_service(request, db).update_agent(agent_id, payload))


@agent_router.delete("/agents/{agent_id}", status_code=204, response_class=Response)
def archive_agent(
    agent_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    _service(request, db).archive_agent(agent_id)
    return Response(status_code=204)
