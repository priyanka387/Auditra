from datetime import UTC, datetime
from math import ceil
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.modules.model_inventory.repositories import DeploymentFilters
from app.modules.model_inventory.schemas import (
    DeploymentCreate,
    DeploymentEndpointCreate,
    DeploymentEndpointListResponse,
    DeploymentEndpointResponse,
    DeploymentEndpointUpdate,
    DeploymentListResponse,
    DeploymentResponse,
    DeploymentTransitionRequest,
    DeploymentUpdate,
)
from app.modules.model_inventory.services import DeploymentEndpointService, DeploymentService

deployment_router = APIRouter(prefix="/api/v1")


def _service(request: Request, db: Session) -> DeploymentService:
    return DeploymentService(
        db, settings.default_tenant_id, request_id=request.state.request_id, actor=None
    )


def _endpoint_service(request: Request, db: Session) -> DeploymentEndpointService:
    return DeploymentEndpointService(
        db, settings.default_tenant_id, request_id=request.state.request_id, actor=None
    )


def _deployment_response(deployment) -> DeploymentResponse:
    return DeploymentResponse.model_validate(deployment, from_attributes=True)


def _endpoint_response(endpoint) -> DeploymentEndpointResponse:
    return DeploymentEndpointResponse.model_validate(endpoint, from_attributes=True)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


@deployment_router.post(
    "/model-versions/{model_version_id}/deployments",
    status_code=201,
    response_model=DeploymentResponse,
)
def create_deployment(
    model_version_id: UUID,
    payload: DeploymentCreate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> DeploymentResponse:
    deployment = _service(request, db).create_deployment(model_version_id, payload)
    return _deployment_response(deployment)


@deployment_router.get("/deployments", response_model=DeploymentListResponse)
def list_deployments(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    environment: str | None = None,
    status: str | None = None,
    deployment_kind: str | None = None,
    target_type: str | None = None,
    runtime: str | None = None,
    region: str | None = None,
    model_version_id: UUID | None = None,
    model_id: UUID | None = None,
    search: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    updated_after: datetime | None = None,
    updated_before: datetime | None = None,
    last_seen_after: datetime | None = None,
    last_seen_before: datetime | None = None,
    include_archived: bool = False,
    sort_by: str = "created_at",
    sort_order: Literal["asc", "desc"] = "desc",
) -> DeploymentListResponse:
    filters = DeploymentFilters(
        environment=environment,
        status=status,
        deployment_kind=deployment_kind,
        target_type=target_type,
        runtime=runtime,
        region=region,
        model_version_id=model_version_id,
        model_id=model_id,
        search=search,
        created_after=_as_utc(created_after),
        created_before=_as_utc(created_before),
        updated_after=_as_utc(updated_after),
        updated_before=_as_utc(updated_before),
        last_seen_after=_as_utc(last_seen_after),
        last_seen_before=_as_utc(last_seen_before),
    )
    items, total = _service(request, db).list_deployments(
        filters,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
        include_archived=include_archived,
    )
    return DeploymentListResponse(
        items=[_deployment_response(item) for item in items],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=ceil(total / page_size),
    )


@deployment_router.get("/deployments/{deployment_id}", response_model=DeploymentResponse)
def get_deployment(
    deployment_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> DeploymentResponse:
    return _deployment_response(_service(request, db).get_deployment(deployment_id))


@deployment_router.patch("/deployments/{deployment_id}", response_model=DeploymentResponse)
def update_deployment(
    deployment_id: UUID,
    payload: DeploymentUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> DeploymentResponse:
    return _deployment_response(_service(request, db).update_deployment(deployment_id, payload))


@deployment_router.post(
    "/deployments/{deployment_id}/transition", response_model=DeploymentResponse
)
def transition_deployment(
    deployment_id: UUID,
    payload: DeploymentTransitionRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> DeploymentResponse:
    return _deployment_response(_service(request, db).transition_deployment(deployment_id, payload))


@deployment_router.delete(
    "/deployments/{deployment_id}", status_code=204, response_class=Response
)
def archive_deployment(
    deployment_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    _service(request, db).archive_deployment(deployment_id)
    return Response(status_code=204)


@deployment_router.post(
    "/deployments/{deployment_id}/endpoints",
    status_code=201,
    response_model=DeploymentEndpointResponse,
)
def create_endpoint(
    deployment_id: UUID,
    payload: DeploymentEndpointCreate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> DeploymentEndpointResponse:
    endpoint = _endpoint_service(request, db).create_endpoint(deployment_id, payload)
    return _endpoint_response(endpoint)


@deployment_router.get(
    "/deployments/{deployment_id}/endpoints", response_model=DeploymentEndpointListResponse
)
def list_endpoints(
    deployment_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    include_archived: bool = False,
) -> DeploymentEndpointListResponse:
    items = _endpoint_service(request, db).list_endpoints(
        deployment_id, include_archived=include_archived
    )
    return DeploymentEndpointListResponse(
        items=[_endpoint_response(item) for item in items],
        page=1,
        page_size=len(items),
        total=len(items),
        total_pages=1,
    )


@deployment_router.get(
    "/deployment-endpoints/{endpoint_id}", response_model=DeploymentEndpointResponse
)
def get_endpoint(
    endpoint_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> DeploymentEndpointResponse:
    return _endpoint_response(_endpoint_service(request, db).get_endpoint(endpoint_id))


@deployment_router.patch(
    "/deployment-endpoints/{endpoint_id}", response_model=DeploymentEndpointResponse
)
def update_endpoint(
    endpoint_id: UUID,
    payload: DeploymentEndpointUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> DeploymentEndpointResponse:
    return _endpoint_response(
        _endpoint_service(request, db).update_endpoint(endpoint_id, payload)
    )


@deployment_router.delete(
    "/deployment-endpoints/{endpoint_id}", status_code=204, response_class=Response
)
def archive_endpoint(
    endpoint_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    _endpoint_service(request, db).archive_endpoint(endpoint_id)
    return Response(status_code=204)
