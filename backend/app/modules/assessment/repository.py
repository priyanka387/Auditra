"""Tenant-scoped persistence for RF-02 templates and assessments.

Mirrors ``risk/repository.py``: every read and write is filtered by
``tenant_id``, list queries compose optional filters over whitelisted
sort maps (unknown sorts raise the module's 400 query errors), and the
commit helper translates constraint races into domain errors.
"""

from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.assessment.errors import (
    AssessmentError,
    DuplicateAssessmentTemplateError,
    InvalidAssessmentQueryError,
    InvalidAssessmentTemplateQueryError,
)
from app.modules.assessment.models import (
    AssessmentQuestion,
    AssessmentResponse,
    AssessmentSection,
    AssessmentTemplate,
    RiskAssessment,
)
from app.modules.assessment.schemas import AssessmentTemplateFilter, RiskAssessmentFilter
from app.modules.asset_discovery.models import DiscoveredAsset
from app.modules.risk.models import Risk

TEMPLATE_SORT_COLUMNS = {
    "name": AssessmentTemplate.name,
    "created_at": AssessmentTemplate.created_at,
    "updated_at": AssessmentTemplate.updated_at,
}

ASSESSMENT_SORT_COLUMNS = {
    "created_at": RiskAssessment.created_at,
    "updated_at": RiskAssessment.updated_at,
    "status": RiskAssessment.status,
}


# --- templates ---------------------------------------------------------------


def create_template(db: Session, **fields) -> AssessmentTemplate:
    template = AssessmentTemplate(**fields)
    db.add(template)
    return template


def get_template(db: Session, tenant_id: UUID, template_id: UUID) -> AssessmentTemplate | None:
    return db.scalar(
        select(AssessmentTemplate).where(
            AssessmentTemplate.tenant_id == tenant_id,
            AssessmentTemplate.id == template_id,
        )
    )


def find_template_by_key(db: Session, tenant_id: UUID, key: str) -> AssessmentTemplate | None:
    return db.scalar(
        select(AssessmentTemplate).where(
            AssessmentTemplate.tenant_id == tenant_id,
            AssessmentTemplate.key == key,
        )
    )


def _template_conditions(tenant_id: UUID, filters: AssessmentTemplateFilter) -> list:
    conditions = [AssessmentTemplate.tenant_id == tenant_id]
    if filters.is_active is not None:
        conditions.append(AssessmentTemplate.is_active == filters.is_active)
    if filters.is_system is not None:
        conditions.append(AssessmentTemplate.is_system == filters.is_system)
    if filters.assessment_type is not None:
        conditions.append(AssessmentTemplate.assessment_type == filters.assessment_type.value)
    if filters.search is not None:
        conditions.append(AssessmentTemplate.name.ilike(f"%{filters.search}%"))
    return conditions


def list_templates(
    db: Session, tenant_id: UUID, filters: AssessmentTemplateFilter
) -> tuple[list[AssessmentTemplate], int]:
    conditions = _template_conditions(tenant_id, filters)
    total = db.scalar(select(func.count()).select_from(AssessmentTemplate).where(*conditions)) or 0

    sort_column = TEMPLATE_SORT_COLUMNS.get(filters.sort_by)
    if sort_column is None:
        raise InvalidAssessmentTemplateQueryError(f"unsupported sort field '{filters.sort_by}'")
    ordering = sort_column.desc() if filters.sort_order == "desc" else sort_column.asc()
    items = list(
        db.scalars(
            select(AssessmentTemplate)
            .where(*conditions)
            .order_by(ordering, AssessmentTemplate.id)
            .offset((filters.page - 1) * filters.page_size)
            .limit(filters.page_size)
        )
    )
    return items, total


def commit_template(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise DuplicateAssessmentTemplateError(
            "assessment template with this key already exists"
        ) from None


# --- template structure ------------------------------------------------------


def get_sections(db: Session, template_id: UUID) -> list[AssessmentSection]:
    return list(
        db.scalars(
            select(AssessmentSection)
            .where(AssessmentSection.template_id == template_id)
            .order_by(AssessmentSection.display_order, AssessmentSection.id)
        )
    )


def get_questions_by_sections(
    db: Session, section_ids: list[UUID]
) -> dict[UUID, list[AssessmentQuestion]]:
    """Questions grouped by section, ordered within each section."""
    if not section_ids:
        return {}
    rows = db.scalars(
        select(AssessmentQuestion)
        .where(AssessmentQuestion.section_id.in_(section_ids))
        .order_by(
            AssessmentQuestion.section_id,
            AssessmentQuestion.display_order,
            AssessmentQuestion.id,
        )
    )
    grouped: dict[UUID, list[AssessmentQuestion]] = {section_id: [] for section_id in section_ids}
    for row in rows:
        grouped.setdefault(row.section_id, []).append(row)
    return grouped


def get_questions_for_template(db: Session, template_id: UUID) -> list[AssessmentQuestion]:
    """Every question of a template in section order (historical rendering)."""
    return list(
        db.scalars(
            select(AssessmentQuestion)
            .join(AssessmentSection, AssessmentQuestion.section_id == AssessmentSection.id)
            .where(AssessmentSection.template_id == template_id)
            .order_by(
                AssessmentSection.display_order,
                AssessmentQuestion.display_order,
                AssessmentQuestion.id,
            )
        )
    )


def get_question_for_template(
    db: Session, template_id: UUID, question_id: UUID
) -> AssessmentQuestion | None:
    return db.scalar(
        select(AssessmentQuestion)
        .join(AssessmentSection, AssessmentQuestion.section_id == AssessmentSection.id)
        .where(
            AssessmentSection.template_id == template_id,
            AssessmentQuestion.id == question_id,
        )
    )


# --- assessments --------------------------------------------------------------


def create_assessment(db: Session, **fields) -> RiskAssessment:
    assessment = RiskAssessment(**fields)
    db.add(assessment)
    return assessment


def get_assessment(db: Session, tenant_id: UUID, assessment_id: UUID) -> RiskAssessment | None:
    return db.scalar(
        select(RiskAssessment).where(
            RiskAssessment.tenant_id == tenant_id,
            RiskAssessment.id == assessment_id,
        )
    )


def _assessment_conditions(tenant_id: UUID, filters: RiskAssessmentFilter) -> list:
    conditions = [RiskAssessment.tenant_id == tenant_id]
    if filters.risk_id is not None:
        conditions.append(RiskAssessment.risk_id == filters.risk_id)
    if filters.asset_id is not None:
        conditions.append(Risk.asset_id == filters.asset_id)
    if filters.template_id is not None:
        conditions.append(RiskAssessment.template_id == filters.template_id)
    if filters.assessment_type is not None:
        conditions.append(RiskAssessment.assessment_type == filters.assessment_type.value)
    if filters.status is not None:
        conditions.append(RiskAssessment.status == filters.status.value)
    if filters.assessor is not None:
        conditions.append(RiskAssessment.assessor == filters.assessor)
    if filters.reviewer is not None:
        conditions.append(RiskAssessment.reviewer == filters.reviewer)
    if filters.created_from is not None:
        conditions.append(RiskAssessment.created_at >= filters.created_from)
    if filters.created_to is not None:
        conditions.append(RiskAssessment.created_at <= filters.created_to)
    if filters.search is not None:
        term = f"%{filters.search}%"
        conditions.append(
            or_(RiskAssessment.assessor.ilike(term), RiskAssessment.reviewer.ilike(term))
        )
    return conditions


def list_assessments(
    db: Session, tenant_id: UUID, filters: RiskAssessmentFilter
) -> tuple[list[RiskAssessment], int]:
    conditions = _assessment_conditions(tenant_id, filters)
    # inner join: every assessment has a risk, and asset filtering runs
    # through risks.asset_id (RF-02 §9 — the asset is derived, never stored)
    total = (
        db.scalar(
            select(func.count())
            .select_from(RiskAssessment)
            .join(Risk, RiskAssessment.risk_id == Risk.id)
            .where(*conditions)
        )
        or 0
    )

    sort_column = ASSESSMENT_SORT_COLUMNS.get(filters.sort_by)
    if sort_column is None:
        raise InvalidAssessmentQueryError(f"unsupported sort field '{filters.sort_by}'")
    ordering = sort_column.desc() if filters.sort_order == "desc" else sort_column.asc()
    items = list(
        db.scalars(
            select(RiskAssessment)
            .join(Risk, RiskAssessment.risk_id == Risk.id)
            .where(*conditions)
            .order_by(ordering, RiskAssessment.id)
            .offset((filters.page - 1) * filters.page_size)
            .limit(filters.page_size)
        )
    )
    return items, total


def commit_assessment(db: Session) -> None:
    """Persist an assessment mutation; a constraint race means a referenced
    row vanished between validation and commit, so roll back rather than
    keep a half-applied change."""
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AssessmentError(
            "assessment references a risk or template that no longer exists"
        ) from exc


# --- responses ----------------------------------------------------------------


def get_responses(db: Session, assessment_id: UUID) -> dict[UUID, AssessmentResponse]:
    """Answers keyed by question_id (one row per question by constraint)."""
    rows = db.scalars(
        select(AssessmentResponse).where(AssessmentResponse.assessment_id == assessment_id)
    )
    return {row.question_id: row for row in rows}


def upsert_response(
    db: Session,
    assessment_id: UUID,
    question_id: UUID,
    value,
    answered_by: str | None,
) -> AssessmentResponse:
    """Insert or overwrite the answer to one question (RF-02 §46)."""
    response = db.scalar(
        select(AssessmentResponse).where(
            AssessmentResponse.assessment_id == assessment_id,
            AssessmentResponse.question_id == question_id,
        )
    )
    if response is not None:
        response.response_value = value
        response.answered_by = answered_by
    else:
        response = AssessmentResponse(
            assessment_id=assessment_id,
            question_id=question_id,
            response_value=value,
            answered_by=answered_by,
        )
        db.add(response)
    db.flush()
    return response


# --- batch reference lookups ---------------------------------------------------


def get_risks_by_ids(db: Session, tenant_id: UUID, risk_ids: set[UUID]) -> dict[UUID, Risk]:
    if not risk_ids:
        return {}
    rows = db.scalars(select(Risk).where(Risk.tenant_id == tenant_id, Risk.id.in_(risk_ids)))
    return {row.id: row for row in rows}


def get_templates_by_ids(
    db: Session, tenant_id: UUID, template_ids: set[UUID]
) -> dict[UUID, AssessmentTemplate]:
    if not template_ids:
        return {}
    rows = db.scalars(
        select(AssessmentTemplate).where(
            AssessmentTemplate.tenant_id == tenant_id,
            AssessmentTemplate.id.in_(template_ids),
        )
    )
    return {row.id: row for row in rows}


def get_assets_by_ids(
    db: Session, tenant_id: UUID, asset_ids: set[UUID]
) -> dict[UUID, DiscoveredAsset]:
    if not asset_ids:
        return {}
    rows = db.scalars(
        select(DiscoveredAsset).where(
            DiscoveredAsset.tenant_id == tenant_id,
            DiscoveredAsset.id.in_(asset_ids),
        )
    )
    return {row.id: row for row in rows}
