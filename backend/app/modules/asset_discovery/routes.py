from math import ceil
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.modules.asset_discovery.enums import DiscoverySourceStatus, DiscoverySourceType
from app.modules.asset_discovery.errors import InvalidDiscoverySourceQueryError
from app.modules.asset_discovery.schemas import (
    DiscoverySourceCreate,
    DiscoverySourceFilter,
    DiscoverySourceListResponse,
    DiscoverySourceResponse,
    DiscoverySourceUpdate,
)
from app.modules.asset_discovery.service import DiscoverySourceService

router = APIRouter(prefix="/api/v1/discovery/sources")


def _service(request: Request, db: Session) -> DiscoverySourceService:
    return DiscoverySourceService(
        db, settings.default_tenant_id, request_id=request.state.request_id, actor=None
    )


def _build(**kwargs) -> DiscoverySourceFilter:
    try:
        return DiscoverySourceFilter(**kwargs)
    except ValidationError as exc:
        message = exc.errors()[0].get("msg", "invalid query") if exc.errors() else "invalid query"
        raise InvalidDiscoverySourceQueryError(message) from None


def _response(source) -> DiscoverySourceResponse:
    return DiscoverySourceResponse.model_validate(source, from_attributes=True)


@router.post("", status_code=201, response_model=DiscoverySourceResponse)
def create_source(
    payload: DiscoverySourceCreate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> DiscoverySourceResponse:
    return _response(_service(request, db).create(payload))


@router.get("", response_model=DiscoverySourceListResponse)
def list_sources(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    source_type: DiscoverySourceType | None = None,
    status: DiscoverySourceStatus | None = None,
    search: str | None = None,
    include_archived: bool = False,
    sort_by: str = "updated_at",
    sort_order: str = "desc",
) -> DiscoverySourceListResponse:
    filters = _build(
        page=page,
        page_size=page_size,
        source_type=source_type,
        status=status,
        search=search,
        include_archived=include_archived,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    items, total = _service(request, db).list(filters)
    return DiscoverySourceListResponse(
        items=[_response(item) for item in items],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=ceil(total / page_size) if total else 0,
    )


@router.get("/{source_id}", response_model=DiscoverySourceResponse)
def get_source(
    source_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> DiscoverySourceResponse:
    return _response(_service(request, db).get(source_id))


@router.patch("/{source_id}", response_model=DiscoverySourceResponse)
def update_source(
    source_id: UUID,
    payload: DiscoverySourceUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> DiscoverySourceResponse:
    return _response(_service(request, db).update(source_id, payload))


@router.delete("/{source_id}", status_code=204, response_class=Response)
def delete_source(
    source_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    _service(request, db).delete(source_id)
    return Response(status_code=204)
