from datetime import UTC, datetime
from math import ceil
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.modules.model_inventory.repositories import (
    ModelFilters,
    list_model_types,
    list_providers,
)
from app.modules.model_inventory.schemas import (
    ModelCreate,
    ModelListResponse,
    ModelResponse,
    ModelTypeSummary,
    ModelUpdate,
    ProviderSummary,
)
from app.modules.model_inventory.services import ModelService

router = APIRouter(prefix="/api/v1")


def _service(request: Request, db: Session) -> ModelService:
    return ModelService(
        db, settings.default_tenant_id, request_id=request.state.request_id, actor=None
    )


def _model_response(model) -> ModelResponse:
    return ModelResponse.model_validate(model, from_attributes=True)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


@router.post("/models", status_code=201, response_model=ModelResponse)
def create_model(
    payload: ModelCreate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> ModelResponse:
    model = _service(request, db).register_model(payload)
    return _model_response(model)


@router.get("/models", response_model=ModelListResponse)
def list_models(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    provider: str | None = None,
    model_type: str | None = None,
    lifecycle_state: str | None = None,
    owner: str | None = None,
    team: str | None = None,
    source_type: str | None = None,
    tag: str | None = None,
    search: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    updated_after: datetime | None = None,
    updated_before: datetime | None = None,
    include_archived: bool = False,
    sort_by: str = "updated_at",
    sort_order: Literal["asc", "desc"] = "desc",
) -> ModelListResponse:
    filters = ModelFilters(
        provider=provider,
        model_type=model_type,
        lifecycle_state=lifecycle_state,
        owner=owner,
        team=team,
        source_type=source_type,
        tag=tag,
        search=search,
        created_after=_as_utc(created_after),
        created_before=_as_utc(created_before),
        updated_after=_as_utc(updated_after),
        updated_before=_as_utc(updated_before),
    )
    items, total = _service(request, db).list_models(
        filters,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
        include_archived=include_archived,
    )
    return ModelListResponse(
        items=[_model_response(item) for item in items],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=ceil(total / page_size),
    )


@router.get("/models/{model_id}", response_model=ModelResponse)
def get_model(
    model_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> ModelResponse:
    return _model_response(_service(request, db).get_model(model_id))


@router.patch("/models/{model_id}", response_model=ModelResponse)
def update_model(
    model_id: UUID,
    payload: ModelUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> ModelResponse:
    return _model_response(_service(request, db).update_model(model_id, payload))


@router.delete("/models/{model_id}", status_code=204, response_class=Response)
def archive_model(
    model_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    _service(request, db).archive_model(model_id)
    return Response(status_code=204)


@router.get("/model-providers", response_model=list[ProviderSummary])
def model_providers(db: Annotated[Session, Depends(get_db)]) -> list[ProviderSummary]:
    rows = list_providers(db, settings.default_tenant_id)
    return [ProviderSummary.model_validate(row) for row in rows]


@router.get("/model-types", response_model=list[ModelTypeSummary])
def model_types(db: Annotated[Session, Depends(get_db)]) -> list[ModelTypeSummary]:
    rows = list_model_types(db, settings.default_tenant_id)
    return [ModelTypeSummary.model_validate(row) for row in rows]
