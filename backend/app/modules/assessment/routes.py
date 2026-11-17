"""REST endpoints for RF-02 AI risk assessments and template catalog.

Both routers mirror ``risk/routes.py``: a single-tenant service factory
bound to ``settings.default_tenant_id``, query filters built through
pydantic (``extra="forbid"`` → unknown parameters and bad sort fields
surface as 400 domain errors), and detail builders that resolve the
embedded risk/asset/template refs and nest sections/questions/answers
without N+1 queries.
"""

from datetime import datetime
from math import ceil
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.modules.assessment.enums import AssessmentStatus, AssessmentType
from app.modules.assessment.errors import (
    InvalidAssessmentQueryError,
    InvalidAssessmentTemplateQueryError,
)
from app.modules.assessment.schemas import (
    AssessmentApproveRequest,
    AssessmentRejectRequest,
    AssessmentResponseUpdate,
    AssessmentRevisionRequest,
    AssessmentTemplateCreate,
    AssessmentTemplateDetailResponse,
    AssessmentTemplateFilter,
    AssessmentTemplateListResponse,
    AssessmentTemplateResponse,
    AssessmentTemplateUpdate,
    RiskAssessmentCreate,
    RiskAssessmentDetailResponse,
    RiskAssessmentFilter,
    RiskAssessmentListItem,
    RiskAssessmentListResponse,
)
from app.modules.assessment.service import (
    AssessmentDetailView,
    AssessmentService,
    AssessmentTemplateService,
)

router = APIRouter(prefix="/api/v1/risk-assessments")
templates_router = APIRouter(prefix="/api/v1/assessment-templates")

# query keys each list endpoint understands; anything else is a client
# mistake (filters are extra="forbid"), not something to silently ignore
ASSESSMENT_QUERY_KEYS = {
    "page",
    "page_size",
    "search",
    "risk_id",
    "asset_id",
    "template_id",
    "assessment_type",
    "status",
    "assessor",
    "reviewer",
    "created_from",
    "created_to",
    "sort_by",
    "sort_order",
}
TEMPLATE_QUERY_KEYS = {
    "page",
    "page_size",
    "search",
    "is_active",
    "is_system",
    "assessment_type",
    "sort_by",
    "sort_order",
}


def _service(request: Request, db: Session) -> AssessmentService:
    return AssessmentService(
        db, settings.default_tenant_id, request_id=request.state.request_id, actor=None
    )


def _template_service(request: Request, db: Session) -> AssessmentTemplateService:
    return AssessmentTemplateService(
        db, settings.default_tenant_id, request_id=request.state.request_id, actor=None
    )


def _reject_unknown_query(request: Request, allowed: set[str], error_cls) -> None:
    unknown = sorted(set(request.query_params) - allowed)
    if unknown:
        raise error_cls(f"unsupported query parameter '{unknown[0]}'")


def _build(**kwargs) -> RiskAssessmentFilter:
    try:
        return RiskAssessmentFilter(**kwargs)
    except ValidationError as exc:
        message = exc.errors()[0].get("msg", "invalid query") if exc.errors() else "invalid query"
        raise InvalidAssessmentQueryError(message) from None


def _build_templates(**kwargs) -> AssessmentTemplateFilter:
    try:
        return AssessmentTemplateFilter(**kwargs)
    except ValidationError as exc:
        message = exc.errors()[0].get("msg", "invalid query") if exc.errors() else "invalid query"
        raise InvalidAssessmentTemplateQueryError(message) from None


# --- response builders ---------------------------------------------------------


def _answer_state(response) -> dict | None:
    if response is None:
        return None
    return {
        "value": response.response_value,
        "answered_by": response.answered_by,
        "updated_at": response.updated_at,
    }


def _question_fields(question) -> dict:
    return {
        "id": question.id,
        "section_id": question.section_id,
        "key": question.key,
        "question_text": question.question_text,
        "description": question.description,
        "question_type": question.question_type,
        "required": question.required,
        "display_order": question.display_order,
        "options": question.options,
        "validation_rules": question.validation_rules,
        "help_text": question.help_text,
        "is_active": question.is_active,
    }


def _detail_response(detail: AssessmentDetailView) -> RiskAssessmentDetailResponse:
    assessment = detail.assessment
    asset = detail.asset
    return RiskAssessmentDetailResponse.model_validate(
        {
            "id": assessment.id,
            "tenant_id": assessment.tenant_id,
            "risk_id": assessment.risk_id,
            "risk": {
                "id": detail.risk.id,
                "title": detail.risk.title,
                "asset_id": detail.risk.asset_id,
                "asset": None if asset is None else {"id": asset.id, "name": asset.name},
            },
            "template": {
                "id": detail.template.id,
                "name": detail.template.name,
                "version": detail.template.version,
                "assessment_type": detail.template.assessment_type,
            },
            "template_version": assessment.template_version,
            "assessment_type": assessment.assessment_type,
            "status": assessment.status,
            "assessor": assessment.assessor,
            "reviewer": assessment.reviewer,
            "revision_number": assessment.revision_number,
            "original_assessment_id": assessment.original_assessment_id,
            "submitted_at": assessment.submitted_at,
            "reviewed_at": assessment.reviewed_at,
            "approved_at": assessment.approved_at,
            "rejected_at": assessment.rejected_at,
            "rejection_reason": assessment.rejection_reason,
            "completion_percentage": detail.completion_percentage,
            "created_at": assessment.created_at,
            "updated_at": assessment.updated_at,
            "sections": [
                {
                    "id": section.id,
                    "title": section.title,
                    "description": section.description,
                    "display_order": section.display_order,
                    "questions": [
                        _question_fields(question)
                        | {"response": _answer_state(detail.responses.get(question.id))}
                        for question in detail.questions_by_section.get(section.id, [])
                    ],
                }
                for section in detail.sections
            ],
        },
        from_attributes=False,
    )


def _list_item(item, risks, templates, completion: int) -> RiskAssessmentListItem:
    risk = risks[item.risk_id]
    template = templates[item.template_id]
    return RiskAssessmentListItem.model_validate(
        {
            "id": item.id,
            "risk_id": item.risk_id,
            "risk": {"id": risk.id, "title": risk.title},
            "template": {
                "id": template.id,
                "name": template.name,
                "version": template.version,
                "assessment_type": template.assessment_type,
            },
            "template_version": item.template_version,
            "assessment_type": item.assessment_type,
            "status": item.status,
            "assessor": item.assessor,
            "reviewer": item.reviewer,
            "revision_number": item.revision_number,
            "original_assessment_id": item.original_assessment_id,
            "completion_percentage": completion,
            "submitted_at": item.submitted_at,
            "approved_at": item.approved_at,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        },
        from_attributes=False,
    )


def _template_response(template) -> AssessmentTemplateResponse:
    return AssessmentTemplateResponse.model_validate(template, from_attributes=True)


def _template_detail_response(
    template, sections, questions_by_section
) -> AssessmentTemplateDetailResponse:
    return AssessmentTemplateDetailResponse.model_validate(
        {
            "id": template.id,
            "tenant_id": template.tenant_id,
            "key": template.key,
            "name": template.name,
            "description": template.description,
            "assessment_type": template.assessment_type,
            "version": template.version,
            "is_system": template.is_system,
            "is_active": template.is_active,
            "created_at": template.created_at,
            "updated_at": template.updated_at,
            "sections": [
                {
                    "id": section.id,
                    "title": section.title,
                    "description": section.description,
                    "display_order": section.display_order,
                    "is_active": section.is_active,
                    "questions": [
                        _question_fields(question)
                        for question in questions_by_section.get(section.id, [])
                    ],
                }
                for section in sections
            ],
        },
        from_attributes=False,
    )


# --- assessments ---------------------------------------------------------------


@router.post("", status_code=201, response_model=RiskAssessmentDetailResponse)
def create_assessment(
    payload: RiskAssessmentCreate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> RiskAssessmentDetailResponse:
    service = _service(request, db)
    created = service.create(payload)
    return _detail_response(service.get_detail(created.id))


@router.get("", response_model=RiskAssessmentListResponse)
def list_assessments(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    search: Annotated[str | None, Query(max_length=255)] = None,
    risk_id: UUID | None = None,
    asset_id: UUID | None = None,
    template_id: UUID | None = None,
    assessment_type: AssessmentType | None = None,
    status: AssessmentStatus | None = None,
    assessor: Annotated[str | None, Query(max_length=255)] = None,
    reviewer: Annotated[str | None, Query(max_length=255)] = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    sort_by: str = "updated_at",
    sort_order: str = "desc",
) -> RiskAssessmentListResponse:
    _reject_unknown_query(request, ASSESSMENT_QUERY_KEYS, InvalidAssessmentQueryError)
    filters = _build(
        page=page,
        page_size=page_size,
        search=search,
        risk_id=risk_id,
        asset_id=asset_id,
        template_id=template_id,
        assessment_type=assessment_type,
        status=status,
        assessor=assessor,
        reviewer=reviewer,
        created_from=created_from,
        created_to=created_to,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    service = _service(request, db)
    items, total = service.list(filters)
    risks, _assets, templates = service.resolve_refs(items)
    completions = service.completion_map(items)
    return RiskAssessmentListResponse(
        items=[_list_item(item, risks, templates, completions[item.id]) for item in items],
        page=filters.page,
        page_size=filters.page_size,
        total=total,
        total_pages=ceil(total / filters.page_size) if total else 0,
    )


@router.get("/{assessment_id}", response_model=RiskAssessmentDetailResponse)
def get_assessment(
    assessment_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> RiskAssessmentDetailResponse:
    service = _service(request, db)
    return _detail_response(service.get_detail(assessment_id))


@router.patch(
    "/{assessment_id}/responses/{question_id}",
    response_model=RiskAssessmentDetailResponse,
)
def save_response(
    assessment_id: UUID,
    question_id: UUID,
    payload: AssessmentResponseUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> RiskAssessmentDetailResponse:
    service = _service(request, db)
    service.save_response(assessment_id, question_id, payload)
    return _detail_response(service.get_detail(assessment_id))


@router.post("/{assessment_id}/submit", response_model=RiskAssessmentDetailResponse)
def submit_assessment(
    assessment_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> RiskAssessmentDetailResponse:
    service = _service(request, db)
    service.submit(assessment_id)
    return _detail_response(service.get_detail(assessment_id))


@router.post("/{assessment_id}/start-review", response_model=RiskAssessmentDetailResponse)
def start_review(
    assessment_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> RiskAssessmentDetailResponse:
    service = _service(request, db)
    service.start_review(assessment_id)
    return _detail_response(service.get_detail(assessment_id))


@router.post("/{assessment_id}/approve", response_model=RiskAssessmentDetailResponse)
def approve_assessment(
    assessment_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    payload: AssessmentApproveRequest | None = None,
) -> RiskAssessmentDetailResponse:
    service = _service(request, db)
    service.approve(assessment_id, payload)
    return _detail_response(service.get_detail(assessment_id))


@router.post("/{assessment_id}/reject", response_model=RiskAssessmentDetailResponse)
def reject_assessment(
    assessment_id: UUID,
    payload: AssessmentRejectRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> RiskAssessmentDetailResponse:
    service = _service(request, db)
    service.reject(assessment_id, payload)
    return _detail_response(service.get_detail(assessment_id))


@router.post(
    "/{assessment_id}/revision",
    status_code=201,
    response_model=RiskAssessmentDetailResponse,
)
def create_revision(
    assessment_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    payload: AssessmentRevisionRequest | None = None,
) -> RiskAssessmentDetailResponse:
    service = _service(request, db)
    revision = service.create_revision(assessment_id)
    return _detail_response(service.get_detail(revision.id))


# --- assessment templates ------------------------------------------------------


@templates_router.post("", status_code=201, response_model=AssessmentTemplateResponse)
def create_template(
    payload: AssessmentTemplateCreate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> AssessmentTemplateResponse:
    return _template_response(_template_service(request, db).create(payload))


@templates_router.get("", response_model=AssessmentTemplateListResponse)
def list_templates(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    search: Annotated[str | None, Query(max_length=255)] = None,
    is_active: bool | None = None,
    is_system: bool | None = None,
    assessment_type: AssessmentType | None = None,
    sort_by: str = "name",
    sort_order: str = "asc",
) -> AssessmentTemplateListResponse:
    _reject_unknown_query(request, TEMPLATE_QUERY_KEYS, InvalidAssessmentTemplateQueryError)
    filters = _build_templates(
        page=page,
        page_size=page_size,
        search=search,
        is_active=is_active,
        is_system=is_system,
        assessment_type=assessment_type,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    items, total = _template_service(request, db).list(filters)
    return AssessmentTemplateListResponse(
        items=[_template_response(item) for item in items],
        page=filters.page,
        page_size=filters.page_size,
        total=total,
        total_pages=ceil(total / filters.page_size) if total else 0,
    )


@templates_router.get("/{template_id}", response_model=AssessmentTemplateDetailResponse)
def get_template(
    template_id: UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> AssessmentTemplateDetailResponse:
    template, sections, questions = _template_service(request, db).get_detail(template_id)
    return _template_detail_response(template, sections, questions)


@templates_router.patch("/{template_id}", response_model=AssessmentTemplateResponse)
def update_template(
    template_id: UUID,
    payload: AssessmentTemplateUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> AssessmentTemplateResponse:
    return _template_response(_template_service(request, db).update(template_id, payload))
