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
    ModelVersionFilters,
    list_model_types,
    list_providers,
)
from app.modules.model_inventory.schemas import (
    ModelCreate,
    ModelListResponse,
    ModelResponse,
    ModelTypeSummary,
    ModelUpdate,
    ModelVersionCreate,
    ModelVersionLifecycleUpdate,
    ModelVersionListResponse,
    ModelVersionResponse,
    ModelVersionUpdate,
    ProviderSummary,
)
from app.modules.model_inventory.services import ModelService, ModelVersionService

router = APIRouter(prefix="/api/v1")


def _service(request: Request, db: Session) -> ModelService:
    return ModelService(
        db, settings.default_tenant_id, request_id=request.state.request_id, actor=None
    )


def _version_service(request: Request, db: Session) -> ModelVersionService:
    return ModelVersionService(
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


def _version_response(version) -> ModelVersionResponse:
    return ModelVersionResponse.model_validate(version, from_attributes=True)


@router.post("/models/{model_id}/versions", status_code=201, response_model=ModelVersionResponse)
def create_version(
    model_id: UUID,
    payload: ModelVersionCreate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> ModelVersionResponse:
    return _version_response(_version_service(request, db).create_version(model_id, payload))


@router.get("/models/{model_id}/versions", response_model=ModelVersionListResponse)
def list_versions(
    model_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    search: str | None = None,
    lifecycle_state: str | None = None,
    identity_type: str | None = None,
    source_type: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    updated_after: datetime | None = None,
    updated_before: datetime | None = None,
    include_archived: bool = False,
    sort_by: str = "created_at",
    sort_order: Literal["asc", "desc"] = "desc",
) -> ModelVersionListResponse:
    filters = ModelVersionFilters(
        lifecycle_state=lifecycle_state,
        identity_type=identity_type,
        source_type=source_type,
        search=search,
        created_after=_as_utc(created_after),
        created_before=_as_utc(created_before),
        updated_after=_as_utc(updated_after),
        updated_before=_as_utc(updated_before),
    )
    items, total = _version_service(request, db).list_versions(
        model_id,
        filters,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
        include_archived=include_archived,
    )
    return ModelVersionListResponse(
        items=[_version_response(item) for item in items],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=ceil(total / page_size),
    )


@router.get("/models/{model_id}/versions/{version_id}", response_model=ModelVersionResponse)
def get_version(
    model_id: UUID,
    version_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> ModelVersionResponse:
    return _version_response(_version_service(request, db).get_version(model_id, version_id))


@router.patch(
    "/models/{model_id}/versions/{version_id}",
    response_model=ModelVersionResponse,
    description=(
        "Update mutable version fields (display_name, description, metadata, "
        "source_reference). Identity fields (model_id, identity_type, "
        "native_version_id, canonical_version_key, version_label) are immutable "
        "and rejected with 409 MODEL_VERSION_IDENTITY_IMMUTABLE."
    ),
)
def update_version(
    model_id: UUID,
    version_id: UUID,
    payload: ModelVersionUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> ModelVersionResponse:
    return _version_response(
        _version_service(request, db).update_version(model_id, version_id, payload)
    )


@router.post(
    "/models/{model_id}/versions/{version_id}/lifecycle",
    response_model=ModelVersionResponse,
)
def transition_version(
    model_id: UUID,
    version_id: UUID,
    payload: ModelVersionLifecycleUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> ModelVersionResponse:
    return _version_response(
        _version_service(request, db).transition_version(model_id, version_id, payload)
    )


@router.delete("/models/{model_id}/versions/{version_id}", status_code=204, response_class=Response)
def archive_version(
    model_id: UUID,
    version_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    _version_service(request, db).archive_version(model_id, version_id)
    return Response(status_code=204)


@router.get("/model-providers", response_model=list[ProviderSummary])
def model_providers(db: Annotated[Session, Depends(get_db)]) -> list[ProviderSummary]:
    rows = list_providers(db, settings.default_tenant_id)
    return [ProviderSummary.model_validate(row) for row in rows]


@router.get("/model-types", response_model=list[ModelTypeSummary])
def model_types(db: Annotated[Session, Depends(get_db)]) -> list[ModelTypeSummary]:
    rows = list_model_types(db, settings.default_tenant_id)
    return [ModelTypeSummary.model_validate(row) for row in rows]
