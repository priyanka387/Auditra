from datetime import datetime
from math import ceil
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.modules.model_discovery.enums import DiscoverySourceType, DiscoveryStatus
from app.modules.model_discovery.errors import InvalidDiscoveryQueryError
from app.modules.model_discovery.schemas import (
    DiscoveryCreate,
    DiscoveryFilter,
    DiscoveryListResponse,
    DiscoveryRegisterRequest,
    DiscoveryResponse,
)
from app.modules.model_discovery.service import DiscoveryService

router = APIRouter(prefix="/api/v1/model-discovery")

_CREATED_DOC = (
    "201 for a new observation, 200 when an identical observation is replayed (idempotent)."
)


def _service(request: Request, db: Session) -> DiscoveryService:
    return DiscoveryService(
        db, settings.default_tenant_id, request_id=request.state.request_id, actor=None
    )


def _build(model_cls, **kwargs):
    try:
        return model_cls(**kwargs)
    except ValidationError as exc:
        message = exc.errors()[0].get("msg", "invalid query") if exc.errors() else "invalid query"
        raise InvalidDiscoveryQueryError(message) from None


@router.post(
    "",
    status_code=201,
    response_model=DiscoveryResponse,
    responses={200: {"model": DiscoveryResponse, "description": "Observation replayed"}},
    description=_CREATED_DOC,
)
def create_observation(
    payload: DiscoveryCreate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    response: Response,
) -> DiscoveryResponse:
    result, created = _service(request, db).ingest(payload)
    response.status_code = 201 if created else 200
    return result


@router.get("/{discovery_id}", response_model=DiscoveryResponse)
def get_discovery(
    discovery_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> DiscoveryResponse:
    return _service(request, db).get(discovery_id)


@router.post("/{discovery_id}/match", response_model=DiscoveryResponse)
def match_discovery(
    discovery_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> DiscoveryResponse:
    return _service(request, db).match(discovery_id)


@router.post("/{discovery_id}/ignore", response_model=DiscoveryResponse)
def ignore_discovery(
    discovery_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> DiscoveryResponse:
    return _service(request, db).ignore(discovery_id)


@router.post(
    "/{discovery_id}/register",
    status_code=201,
    response_model=DiscoveryResponse,
)
def register_discovery(
    discovery_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    payload: DiscoveryRegisterRequest | None = None,
) -> DiscoveryResponse:
    return _service(request, db).register(discovery_id, payload)


@router.get("", response_model=DiscoveryListResponse)
def list_discoveries(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    source_type: DiscoverySourceType | None = None,
    source_identifier: str | None = None,
    provider: str | None = None,
    model_type: str | None = None,
    status: DiscoveryStatus | None = None,
    matched_model_id: UUID | None = None,
    canonical_identity: str | None = None,
    last_seen_from: datetime | None = None,
    last_seen_to: datetime | None = None,
    search: str | None = None,
    sort_by: str = "last_seen_at",
    sort_order: str = "desc",
) -> DiscoveryListResponse:
    filters = _build(
        DiscoveryFilter,
        page=page,
        page_size=page_size,
        source_type=source_type,
        source_identifier=source_identifier,
        provider=provider,
        model_type=model_type,
        status=status,
        matched_model_id=matched_model_id,
        canonical_identity=canonical_identity,
        last_seen_from=last_seen_from,
        last_seen_to=last_seen_to,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    items, total = _service(request, db).list(filters)
    return DiscoveryListResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        total_pages=ceil(total / page_size) if total else 0,
    )
