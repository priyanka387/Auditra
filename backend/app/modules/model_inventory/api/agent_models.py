from math import ceil
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.modules.model_inventory.repositories import AssociationFilters
from app.modules.model_inventory.schemas import (
    AgentModelAssociationCreate,
    AgentModelAssociationListResponse,
    AgentModelAssociationResponse,
    AgentModelAssociationUpdate,
    ModelAgentLinkListResponse,
    ModelAgentLinkResponse,
)
from app.modules.model_inventory.services import AgentAssociationService

agent_model_router = APIRouter(prefix="/api/v1")


def _service(request: Request, db: Session) -> AgentAssociationService:
    return AgentAssociationService(
        db, settings.default_tenant_id, request_id=request.state.request_id, actor=None
    )


def _response(association) -> AgentModelAssociationResponse:
    return AgentModelAssociationResponse.model_validate(association, from_attributes=True)


def _filters(status: str | None, active_only: bool, role: str | None) -> AssociationFilters:
    return AssociationFilters(
        status="ACTIVE" if active_only else status,
        role=role,
    )


@agent_model_router.post(
    "/agents/{agent_id}/models", status_code=201, response_model=AgentModelAssociationResponse
)
def create_association(
    agent_id: UUID,
    payload: AgentModelAssociationCreate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> AgentModelAssociationResponse:
    association = _service(request, db).create_association(agent_id, payload)
    return _response(association)


@agent_model_router.get(
    "/agents/{agent_id}/models", response_model=AgentModelAssociationListResponse
)
def list_associations(
    agent_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    status: str | None = None,
    active_only: bool = False,
    role: str | None = None,
    model_id: UUID | None = None,
    model_version_id: UUID | None = None,
    sort_by: str = "selection_priority",
    sort_order: Literal["asc", "desc"] = "asc",
) -> AgentModelAssociationListResponse:
    filters = _filters(status, active_only, role)
    filters.model_id = model_id
    filters.model_version_id = model_version_id
    items, total = _service(request, db).list_associations(
        agent_id,
        filters,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    return AgentModelAssociationListResponse(
        items=[_response(item) for item in items],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=ceil(total / page_size),
    )


@agent_model_router.get(
    "/agents/{agent_id}/models/{association_id}", response_model=AgentModelAssociationResponse
)
def get_association(
    agent_id: UUID,
    association_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> AgentModelAssociationResponse:
    association = _service(request, db).get_association(agent_id, association_id)
    return _response(association)


@agent_model_router.patch(
    "/agents/{agent_id}/models/{association_id}", response_model=AgentModelAssociationResponse
)
def update_association(
    agent_id: UUID,
    association_id: UUID,
    payload: AgentModelAssociationUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> AgentModelAssociationResponse:
    association = _service(request, db).update_association(agent_id, association_id, payload)
    return _response(association)


@agent_model_router.delete(
    "/agents/{agent_id}/models/{association_id}", status_code=204, response_class=Response
)
def disable_association(
    agent_id: UUID,
    association_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    _service(request, db).disable_association(agent_id, association_id)
    return Response(status_code=204)


@agent_model_router.get("/models/{model_id}/agents", response_model=ModelAgentLinkListResponse)
def list_model_agents(
    model_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    status: str | None = None,
    active_only: bool = False,
    role: str | None = None,
    application_id: UUID | None = None,
    sort_by: str = "created_at",
    sort_order: Literal["asc", "desc"] = "desc",
) -> ModelAgentLinkListResponse:
    filters = _filters(status, active_only, role)
    items, total = _service(request, db).list_model_agents(
        model_id,
        role=filters.role,
        status=filters.status,
        application_id=application_id,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    return ModelAgentLinkListResponse(
        items=[
            ModelAgentLinkResponse(
                association_id=association.id,
                agent_id=agent.id,
                agent_name=agent.name,
                agent_slug=agent.slug,
                agent_status=agent.status,
                application_id=application.id,
                application_name=application.name,
                application_slug=application.slug,
                model_id=association.model_id,
                model_version_id=association.model_version_id,
                role=association.role,
                selection_priority=association.selection_priority,
                status=association.status,
                source=association.source,
                configuration=association.configuration,
                metadata=association.metadata_,
                created_at=association.created_at,
                updated_at=association.updated_at,
            )
            for association, agent, application in items
        ],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=ceil(total / page_size),
    )
