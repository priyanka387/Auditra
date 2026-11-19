"""REST endpoints for the RF-01 risk register and taxonomy.

Both routers mirror ``asset_discovery/routes.py``: a single-tenant
service factory bound to ``settings.default_tenant_id``, query filters
built through pydantic (``extra="forbid"`` → unknown parameters and bad
sort fields surface as 400 domain errors), and response builders that
resolve the embedded category/asset references without N+1 queries.
"""

from math import ceil
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.modules.risk.enums import RiskSource, RiskStatus
from app.modules.risk.errors import (
    InvalidRiskCategoryQueryError,
    InvalidRiskQueryError,
)
from app.modules.risk.schemas import (
    RiskCategoryCreate,
    RiskCategoryFilter,
    RiskCategoryListResponse,
    RiskCategoryResponse,
    RiskCategoryUpdate,
    RiskCreate,
    RiskFilter,
    RiskListResponse,
    RiskResponse,
    RiskScoreSummary,
    RiskUpdate,
)
from app.modules.risk.service import RiskCategoryService, RiskService
from app.modules.risk_scoring.enums import RiskSeverity

router = APIRouter(prefix="/api/v1/risks")
categories_router = APIRouter(prefix="/api/v1/risk-categories")

# query keys each list endpoint understands; anything else is a client
# mistake (filters are extra="forbid"), not something to silently ignore
RISK_QUERY_KEYS = {
    "page",
    "page_size",
    "search",
    "category_id",
    "status",
    "source",
    "owner",
    "team",
    "asset_id",
    "archived",
    "severity",
    "priority",
    "scored",
    "likelihood",
    "impact",
    "framework_id",
    "sort_by",
    "sort_order",
}
CATEGORY_QUERY_KEYS = {
    "page",
    "page_size",
    "search",
    "is_active",
    "is_system",
    "sort_by",
    "sort_order",
}


def _service(request: Request, db: Session) -> RiskService:
    return RiskService(
        db, settings.default_tenant_id, request_id=request.state.request_id, actor=None
    )


def _category_service(request: Request, db: Session) -> RiskCategoryService:
    return RiskCategoryService(
        db, settings.default_tenant_id, request_id=request.state.request_id, actor=None
    )


def _reject_unknown_query(request: Request, allowed: set[str], error_cls) -> None:
    unknown = sorted(set(request.query_params) - allowed)
    if unknown:
        raise error_cls(f"unsupported query parameter '{unknown[0]}'")


def _build(**kwargs) -> RiskFilter:
    try:
        return RiskFilter(**kwargs)
    except ValidationError as exc:
        message = exc.errors()[0].get("msg", "invalid query") if exc.errors() else "invalid query"
        raise InvalidRiskQueryError(message) from None


def _build_categories(**kwargs) -> RiskCategoryFilter:
    try:
        return RiskCategoryFilter(**kwargs)
    except ValidationError as exc:
        message = exc.errors()[0].get("msg", "invalid query") if exc.errors() else "invalid query"
        raise InvalidRiskCategoryQueryError(message) from None


def _risk_fields(risk) -> dict:
    """Scalar columns of a risk row (refs are attached separately)."""
    return {
        "id": risk.id,
        "tenant_id": risk.tenant_id,
        "title": risk.title,
        "description": risk.description,
        "category_id": risk.category_id,
        "source": risk.source,
        "asset_id": risk.asset_id,
        "owner": risk.owner,
        "team": risk.team,
        "status": risk.status,
        "created_by": risk.created_by,
        "updated_by": risk.updated_by,
        "archived_at": risk.archived_at,
        "created_at": risk.created_at,
        "updated_at": risk.updated_at,
    }


def _score_summary(score) -> RiskScoreSummary | None:
    """Scalar summary of a current score; None means unscored (never low)."""
    if score is None:
        return None
    return RiskScoreSummary(
        likelihood=score.likelihood_value,
        impact=score.impact_value,
        calculated_score=score.calculated_score,
        effective_score=score.effective_score,
        severity=score.severity,
        priority=score.priority,
        is_override=score.is_override,
        scored_at=score.created_at,
    )


def _risk_response(risk, categories, assets, scores) -> RiskResponse:
    """Build one risk response with its resolved refs (no per-row queries)."""
    asset = assets.get(risk.asset_id) if risk.asset_id else None
    return RiskResponse.model_validate(
        _risk_fields(risk)
        | {
            "category": {
                "id": categories[risk.category_id].id,
                "name": categories[risk.category_id].name,
                "slug": categories[risk.category_id].slug,
            },
            "asset": None if asset is None else {"id": asset.id, "name": asset.name},
            "current_score": _score_summary(scores.get(risk.id)),
        },
        from_attributes=False,
    )


def _category_response(category) -> RiskCategoryResponse:
    return RiskCategoryResponse.model_validate(category, from_attributes=True)


def _page_response(service: RiskService, filters) -> RiskListResponse:
    items, total = service.list(filters)
    categories, assets = service.resolve_refs(items)
    scores = service.resolve_scores(items)
    return RiskListResponse(
        items=[_risk_response(item, categories, assets, scores) for item in items],
        page=filters.page,
        page_size=filters.page_size,
        total=total,
        total_pages=ceil(total / filters.page_size) if total else 0,
    )


# --- risks ------------------------------------------------------------------


@router.post("", status_code=201, response_model=RiskResponse)
def create_risk(
    payload: RiskCreate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> RiskResponse:
    service = _service(request, db)
    risk = service.create(payload)
    categories, assets = service.resolve_refs([risk])
    scores = service.resolve_scores([risk])
    return _risk_response(risk, categories, assets, scores)


@router.get("", response_model=RiskListResponse)
def list_risks(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    search: Annotated[str | None, Query(max_length=255)] = None,
    category_id: UUID | None = None,
    status: RiskStatus | None = None,
    source: RiskSource | None = None,
    owner: Annotated[str | None, Query(max_length=255)] = None,
    team: Annotated[str | None, Query(max_length=255)] = None,
    asset_id: UUID | None = None,
    archived: bool = False,
    severity: RiskSeverity | None = None,
    priority: Annotated[int | None, Query(ge=1)] = None,
    scored: bool | None = None,
    likelihood: int | None = None,
    impact: int | None = None,
    framework_id: UUID | None = None,
    sort_by: str = "updated_at",
    sort_order: str = "desc",
) -> RiskListResponse:
    _reject_unknown_query(request, RISK_QUERY_KEYS, InvalidRiskQueryError)
    filters = _build(
        page=page,
        page_size=page_size,
        search=search,
        category_id=category_id,
        status=status,
        source=source,
        owner=owner,
        team=team,
        asset_id=asset_id,
        archived=archived,
        severity=severity,
        priority=priority,
        scored=scored,
        likelihood=likelihood,
        impact=impact,
        framework_id=framework_id,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    return _page_response(_service(request, db), filters)


@router.get("/{risk_id}", response_model=RiskResponse)
def get_risk(
    risk_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> RiskResponse:
    service = _service(request, db)
    risk = service.get(risk_id)
    categories, assets = service.resolve_refs([risk])
    scores = service.resolve_scores([risk])
    return _risk_response(risk, categories, assets, scores)


@router.patch("/{risk_id}", response_model=RiskResponse)
def update_risk(
    risk_id: UUID,
    payload: RiskUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> RiskResponse:
    service = _service(request, db)
    risk = service.update(risk_id, payload)
    categories, assets = service.resolve_refs([risk])
    scores = service.resolve_scores([risk])
    return _risk_response(risk, categories, assets, scores)


@router.delete("/{risk_id}", status_code=204, response_class=Response)
def archive_risk(
    risk_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    _service(request, db).archive(risk_id)
    return Response(status_code=204)


# --- risk categories --------------------------------------------------------


@categories_router.post("", status_code=201, response_model=RiskCategoryResponse)
def create_category(
    payload: RiskCategoryCreate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> RiskCategoryResponse:
    return _category_response(_category_service(request, db).create(payload))


@categories_router.get("", response_model=RiskCategoryListResponse)
def list_categories(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    search: Annotated[str | None, Query(max_length=255)] = None,
    is_active: bool | None = None,
    is_system: bool | None = None,
    sort_by: str = "name",
    sort_order: str = "asc",
) -> RiskCategoryListResponse:
    _reject_unknown_query(request, CATEGORY_QUERY_KEYS, InvalidRiskCategoryQueryError)
    filters = _build_categories(
        page=page,
        page_size=page_size,
        search=search,
        is_active=is_active,
        is_system=is_system,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    items, total = _category_service(request, db).list(filters)
    return RiskCategoryListResponse(
        items=[_category_response(item) for item in items],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=ceil(total / page_size) if total else 0,
    )


@categories_router.get("/{category_id}", response_model=RiskCategoryResponse)
def get_category(
    category_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> RiskCategoryResponse:
    return _category_response(_category_service(request, db).get(category_id))


@categories_router.patch("/{category_id}", response_model=RiskCategoryResponse)
def update_category(
    category_id: UUID,
    payload: RiskCategoryUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> RiskCategoryResponse:
    return _category_response(_category_service(request, db).update(category_id, payload))
