"""REST endpoints for RF-04 risk treatment & mitigation (plan §29).

Three routers mirroring ``risk/routes.py``: a single-tenant service
factory bound to ``settings.default_tenant_id``, query filters built
through pydantic (``extra="forbid"`` → unknown parameters and bad sort
fields surface as 400 domain errors), and response builders that attach
the derived fields — ``is_overdue`` everywhere, ``action_stats`` on
treatment detail only (Ruling 14). ``/overview`` is declared before
``/{treatment_id}`` so it never reaches the UUID parser (plan §44).
"""

from datetime import UTC, date, datetime
from math import ceil
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.modules.risk_treatment import metrics, repository
from app.modules.risk_treatment.enums import (
    RiskTreatmentStatus,
    RiskTreatmentStrategy,
    TreatmentActionStatus,
)
from app.modules.risk_treatment.errors import InvalidRiskTreatmentQueryError
from app.modules.risk_treatment.schemas import (
    ResidualAssessmentCreate,
    ResidualAssessmentFilter,
    ResidualAssessmentListResponse,
    ResidualAssessmentResponse,
    TreatmentActionCreate,
    TreatmentActionFilter,
    TreatmentActionListResponse,
    TreatmentActionResponse,
    TreatmentActionUpdate,
    TreatmentCancelRequest,
    TreatmentCreate,
    TreatmentFilter,
    TreatmentListResponse,
    TreatmentOverviewResponse,
    TreatmentResponse,
    TreatmentUpdate,
)
from app.modules.risk_treatment.service import RiskTreatmentActionService, RiskTreatmentService

treatments_nested_router = APIRouter(prefix="/api/v1/risks")
treatments_router = APIRouter(prefix="/api/v1/treatments")
treatment_actions_router = APIRouter(prefix="/api/v1/treatment-actions")

# query keys each list endpoint understands; anything else is a client
# mistake (filters are extra="forbid"), not something to silently ignore
TREATMENT_NESTED_QUERY_KEYS = {
    "page",
    "page_size",
    "strategy",
    "status",
    "owner",
    "overdue",
    "sort_by",
    "sort_order",
}
ACTION_QUERY_KEYS = {"page", "page_size", "status", "owner", "sort_by", "sort_order"}
RESIDUAL_QUERY_KEYS = {"page", "page_size"}


def _service(request: Request, db: Session) -> RiskTreatmentService:
    return RiskTreatmentService(
        db, settings.default_tenant_id, request_id=request.state.request_id, actor=None
    )


def _action_service(request: Request, db: Session) -> RiskTreatmentActionService:
    return RiskTreatmentActionService(
        db, settings.default_tenant_id, request_id=request.state.request_id, actor=None
    )


def _reject_unknown_query(request: Request, allowed: set[str], error_cls) -> None:
    unknown = sorted(set(request.query_params) - allowed)
    if unknown:
        raise error_cls(f"unsupported query parameter '{unknown[0]}'")


def _build_treatments(**kwargs) -> TreatmentFilter:
    try:
        return TreatmentFilter(**kwargs)
    except ValidationError as exc:
        message = exc.errors()[0].get("msg", "invalid query") if exc.errors() else "invalid query"
        raise InvalidRiskTreatmentQueryError(message) from None


def _build_actions(**kwargs) -> TreatmentActionFilter:
    try:
        return TreatmentActionFilter(**kwargs)
    except ValidationError as exc:
        message = exc.errors()[0].get("msg", "invalid query") if exc.errors() else "invalid query"
        raise InvalidRiskTreatmentQueryError(message) from None


def _build_residuals(**kwargs) -> ResidualAssessmentFilter:
    try:
        return ResidualAssessmentFilter(**kwargs)
    except ValidationError as exc:
        message = exc.errors()[0].get("msg", "invalid query") if exc.errors() else "invalid query"
        raise InvalidRiskTreatmentQueryError(message) from None


def _today() -> date:
    return datetime.now(UTC).date()


def _treatment_response(treatment, db: Session | None = None) -> TreatmentResponse:
    """Scalar treatment response with derived ``is_overdue``; passing
    ``db`` additionally attaches ``action_stats`` (detail only, Ruling 14)."""
    response = TreatmentResponse.model_validate(treatment, from_attributes=True)
    update = {"is_overdue": metrics.treatment_is_overdue(treatment, _today())}
    if db is not None:
        actions = repository.get_actions_by_treatment(db, treatment.id)
        update["action_stats"] = metrics.compute_progress(actions)
    return response.model_copy(update=update)


def _action_response(action) -> TreatmentActionResponse:
    response = TreatmentActionResponse.model_validate(action, from_attributes=True)
    return response.model_copy(update={"is_overdue": metrics.action_is_overdue(action, _today())})


def _residual_response(residual) -> ResidualAssessmentResponse:
    # the ORM stores likelihood_value/impact_value; the API contract
    # speaks plain likelihood/impact (plan §23)
    return ResidualAssessmentResponse(
        id=residual.id,
        tenant_id=residual.tenant_id,
        treatment_id=residual.treatment_id,
        likelihood=residual.likelihood_value,
        impact=residual.impact_value,
        calculated_score=residual.calculated_score,
        severity=residual.severity,
        assessment_type=residual.assessment_type,
        created_by=residual.created_by,
        created_at=residual.created_at,
    )


# --- nested under a risk ------------------------------------------------------


@treatments_nested_router.get("/{risk_id}/treatments", response_model=TreatmentListResponse)
def list_treatments_nested(
    risk_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    strategy: RiskTreatmentStrategy | None = None,
    status: RiskTreatmentStatus | None = None,
    owner: Annotated[str | None, Query(max_length=255)] = None,
    overdue: bool | None = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
) -> TreatmentListResponse:
    _reject_unknown_query(request, TREATMENT_NESTED_QUERY_KEYS, InvalidRiskTreatmentQueryError)
    filters = _build_treatments(
        page=page,
        page_size=page_size,
        strategy=strategy,
        status=status,
        owner=owner,
        overdue=overdue,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    items, total = _service(request, db).list_by_risk(risk_id, filters)
    return TreatmentListResponse(
        items=[_treatment_response(item) for item in items],
        page=filters.page,
        page_size=filters.page_size,
        total=total,
        total_pages=ceil(total / filters.page_size) if total else 0,
    )


@treatments_nested_router.post(
    "/{risk_id}/treatments", status_code=201, response_model=TreatmentResponse
)
def create_treatment(
    risk_id: UUID,
    payload: TreatmentCreate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> TreatmentResponse:
    return _treatment_response(_service(request, db).create(risk_id, payload))


# --- treatments ---------------------------------------------------------------


@treatments_router.get("/overview", response_model=TreatmentOverviewResponse)
def get_treatment_overview(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> TreatmentOverviewResponse:
    return _service(request, db).get_overview()


@treatments_router.get("/{treatment_id}", response_model=TreatmentResponse)
def get_treatment(
    treatment_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> TreatmentResponse:
    treatment = _service(request, db).get(treatment_id)
    return _treatment_response(treatment, db)


@treatments_router.patch("/{treatment_id}", response_model=TreatmentResponse)
def update_treatment(
    treatment_id: UUID,
    payload: TreatmentUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> TreatmentResponse:
    return _treatment_response(_service(request, db).update(treatment_id, payload))


@treatments_router.post("/{treatment_id}/complete", response_model=TreatmentResponse)
def complete_treatment(
    treatment_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> TreatmentResponse:
    return _treatment_response(_service(request, db).complete(treatment_id))


@treatments_router.post("/{treatment_id}/cancel", response_model=TreatmentResponse)
def cancel_treatment(
    treatment_id: UUID,
    payload: TreatmentCancelRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> TreatmentResponse:
    return _treatment_response(_service(request, db).cancel(treatment_id, payload))


# --- treatment actions --------------------------------------------------------


@treatments_router.get("/{treatment_id}/actions", response_model=TreatmentActionListResponse)
def list_treatment_actions(
    treatment_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    status: TreatmentActionStatus | None = None,
    owner: Annotated[str | None, Query(max_length=255)] = None,
    sort_by: str = "created_at",
    sort_order: str = "asc",
) -> TreatmentActionListResponse:
    _reject_unknown_query(request, ACTION_QUERY_KEYS, InvalidRiskTreatmentQueryError)
    filters = _build_actions(
        page=page,
        page_size=page_size,
        status=status,
        owner=owner,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    items, total = _action_service(request, db).list(treatment_id, filters)
    return TreatmentActionListResponse(
        items=[_action_response(item) for item in items],
        page=filters.page,
        page_size=filters.page_size,
        total=total,
        total_pages=ceil(total / filters.page_size) if total else 0,
    )


@treatments_router.post(
    "/{treatment_id}/actions", status_code=201, response_model=TreatmentActionResponse
)
def create_treatment_action(
    treatment_id: UUID,
    payload: TreatmentActionCreate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> TreatmentActionResponse:
    return _action_response(_action_service(request, db).create(treatment_id, payload))


# --- expected residual assessments --------------------------------------------


@treatments_router.get(
    "/{treatment_id}/residual-assessments", response_model=ResidualAssessmentListResponse
)
def list_residual_assessments(
    treatment_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> ResidualAssessmentListResponse:
    _reject_unknown_query(request, RESIDUAL_QUERY_KEYS, InvalidRiskTreatmentQueryError)
    filters = _build_residuals(page=page, page_size=page_size)
    items, total = _service(request, db).list_residual(treatment_id, filters)
    return ResidualAssessmentListResponse(
        items=[_residual_response(item) for item in items],
        page=filters.page,
        page_size=filters.page_size,
        total=total,
        total_pages=ceil(total / filters.page_size) if total else 0,
    )


@treatments_router.post(
    "/{treatment_id}/residual-assessments",
    status_code=201,
    response_model=ResidualAssessmentResponse,
)
def create_residual_assessment(
    treatment_id: UUID,
    payload: ResidualAssessmentCreate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> ResidualAssessmentResponse:
    return _residual_response(_service(request, db).create_residual(treatment_id, payload))


# --- standalone action endpoints ----------------------------------------------


@treatment_actions_router.get("/{action_id}", response_model=TreatmentActionResponse)
def get_action(
    action_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> TreatmentActionResponse:
    return _action_response(_action_service(request, db).get(action_id))


@treatment_actions_router.patch("/{action_id}", response_model=TreatmentActionResponse)
def update_action(
    action_id: UUID,
    payload: TreatmentActionUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> TreatmentActionResponse:
    return _action_response(_action_service(request, db).update(action_id, payload))


@treatment_actions_router.post("/{action_id}/complete", response_model=TreatmentActionResponse)
def complete_action(
    action_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> TreatmentActionResponse:
    return _action_response(_action_service(request, db).complete(action_id))


@treatment_actions_router.post("/{action_id}/cancel", response_model=TreatmentActionResponse)
def cancel_action(
    action_id: UUID,
    payload: TreatmentCancelRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> TreatmentActionResponse:
    return _action_response(_action_service(request, db).cancel(action_id, payload))
