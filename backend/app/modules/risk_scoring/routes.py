"""REST endpoints for RF-03 risk scoring & prioritization.

Two routers mirror ``risk/routes.py``: a single-tenant service factory
bound to ``settings.default_tenant_id``, query filters built through
pydantic (``extra="forbid"`` → unknown parameters and bad sort fields
surface as 400 domain errors), and responses assembled from the service
helpers so the embedded framework refs never trigger N+1 queries.
"""

from math import ceil
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.modules.risk_scoring.errors import (
    InvalidRiskScoreQueryError,
    InvalidScoringFrameworkQueryError,
)
from app.modules.risk_scoring.schemas import (
    RiskScoreCreate,
    RiskScoreFilter,
    RiskScoreListResponse,
    RiskScoreOverrideRequest,
    RiskScoreResponse,
    ScoringFrameworkCreate,
    ScoringFrameworkDetailResponse,
    ScoringFrameworkFilter,
    ScoringFrameworkListResponse,
    ScoringFrameworkResponse,
    ScoringFrameworkUpdate,
    ScoringOverviewResponse,
)
from app.modules.risk_scoring.service import RiskScoringService, ScoringFrameworkService

router = APIRouter(prefix="/api/v1/risk-scoring")
scores_router = APIRouter(prefix="/api/v1/risks")

# query keys each list endpoint understands; anything else is a client
# mistake (filters are extra="forbid"), not something to silently ignore
FRAMEWORK_QUERY_KEYS = {
    "page",
    "page_size",
    "search",
    "is_active",
    "is_system",
    "sort_by",
    "sort_order",
}
OVERVIEW_QUERY_KEYS = {"framework_id"}
SCORE_QUERY_KEYS = {"page", "page_size", "sort_by", "sort_order"}


def _framework_service(request: Request, db: Session) -> ScoringFrameworkService:
    return ScoringFrameworkService(
        db, settings.default_tenant_id, request_id=request.state.request_id, actor=None
    )


def _score_service(request: Request, db: Session) -> RiskScoringService:
    return RiskScoringService(
        db, settings.default_tenant_id, request_id=request.state.request_id, actor=None
    )


def _reject_unknown_query(request: Request, allowed: set[str], error_cls) -> None:
    unknown = sorted(set(request.query_params) - allowed)
    if unknown:
        raise error_cls(f"unsupported query parameter '{unknown[0]}'")


def _build_framework_filters(**kwargs) -> ScoringFrameworkFilter:
    try:
        return ScoringFrameworkFilter(**kwargs)
    except ValidationError as exc:
        message = exc.errors()[0].get("msg", "invalid query") if exc.errors() else "invalid query"
        raise InvalidScoringFrameworkQueryError(message) from None


def _build_score_filters(**kwargs) -> RiskScoreFilter:
    try:
        return RiskScoreFilter(**kwargs)
    except ValidationError as exc:
        message = exc.errors()[0].get("msg", "invalid query") if exc.errors() else "invalid query"
        raise InvalidRiskScoreQueryError(message) from None


# --- scoring frameworks ------------------------------------------------------


@router.get("/frameworks", response_model=ScoringFrameworkListResponse)
def list_frameworks(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    search: Annotated[str | None, Query(max_length=255)] = None,
    is_active: bool | None = None,
    is_system: bool | None = None,
    sort_by: str = "name",
    sort_order: str = "asc",
) -> ScoringFrameworkListResponse:
    _reject_unknown_query(request, FRAMEWORK_QUERY_KEYS, InvalidScoringFrameworkQueryError)
    filters = _build_framework_filters(
        page=page,
        page_size=page_size,
        search=search,
        is_active=is_active,
        is_system=is_system,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    items, total = _framework_service(request, db).list(filters)
    return ScoringFrameworkListResponse(
        items=[
            ScoringFrameworkResponse.model_validate(item, from_attributes=True) for item in items
        ],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=ceil(total / page_size) if total else 0,
    )


@router.post("/frameworks", status_code=201, response_model=ScoringFrameworkDetailResponse)
def create_framework(
    payload: ScoringFrameworkCreate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> ScoringFrameworkDetailResponse:
    service = _framework_service(request, db)
    created = service.create(payload)
    framework, likelihoods, impacts, thresholds = service.get_detail(created.id)
    return service.framework_detail_response(framework, likelihoods, impacts, thresholds)


@router.get("/overview", response_model=ScoringOverviewResponse)
def get_scoring_overview(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    framework_id: UUID | None = None,
) -> ScoringOverviewResponse:
    _reject_unknown_query(request, OVERVIEW_QUERY_KEYS, InvalidScoringFrameworkQueryError)
    return _score_service(request, db).get_overview(framework_id)


@router.get("/frameworks/{framework_id}", response_model=ScoringFrameworkDetailResponse)
def get_framework(
    framework_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> ScoringFrameworkDetailResponse:
    service = _framework_service(request, db)
    framework, likelihoods, impacts, thresholds = service.get_detail(framework_id)
    return service.framework_detail_response(framework, likelihoods, impacts, thresholds)


@router.patch("/frameworks/{framework_id}", response_model=ScoringFrameworkResponse)
def update_framework(
    framework_id: UUID,
    payload: ScoringFrameworkUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> ScoringFrameworkResponse:
    updated = _framework_service(request, db).update(framework_id, payload)
    return ScoringFrameworkResponse.model_validate(updated, from_attributes=True)


# --- risk scores (nested under risks) ----------------------------------------


@scores_router.get("/{risk_id}/scores", response_model=RiskScoreListResponse)
def list_scores(
    risk_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    sort_by: str = "created_at",
    sort_order: str = "desc",
) -> RiskScoreListResponse:
    _reject_unknown_query(request, SCORE_QUERY_KEYS, InvalidRiskScoreQueryError)
    filters = _build_score_filters(
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    service = _score_service(request, db)
    items, total = service.get_score_history(risk_id, filters)
    return RiskScoreListResponse(
        items=[service.score_response(item) for item in items],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=ceil(total / page_size) if total else 0,
    )


@scores_router.post("/{risk_id}/scores", status_code=201, response_model=RiskScoreResponse)
def create_score(
    risk_id: UUID,
    payload: RiskScoreCreate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> RiskScoreResponse:
    service = _score_service(request, db)
    return service.score_response(service.score_risk(risk_id, payload))


@scores_router.get("/{risk_id}/scores/current", response_model=RiskScoreResponse)
def get_current_score(
    risk_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> RiskScoreResponse:
    service = _score_service(request, db)
    return service.score_response(service.get_current_score(risk_id))


@scores_router.post("/{risk_id}/scores/recalculate", response_model=RiskScoreResponse)
def recalculate_score(
    risk_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> RiskScoreResponse:
    service = _score_service(request, db)
    return service.score_response(service.recalculate_risk(risk_id))


@scores_router.post("/{risk_id}/scores/override", response_model=RiskScoreResponse)
def override_score(
    risk_id: UUID,
    payload: RiskScoreOverrideRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> RiskScoreResponse:
    service = _score_service(request, db)
    return service.score_response(service.override_score(risk_id, payload))
