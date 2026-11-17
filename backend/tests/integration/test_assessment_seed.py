"""Integration tests: RF-02 system assessment template seed."""

from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.core.config import settings
from app.modules.assessment.enums import QuestionType
from app.modules.assessment.models import (
    AssessmentQuestion,
    AssessmentSection,
    AssessmentTemplate,
)
from app.modules.assessment.seed import (
    GENERAL_AI_RISK_ASSESSMENT_KEY,
    seed_assessment_templates,
)

SECTION_TITLES = [
    "System Purpose & Context",
    "Data & Privacy",
    "Stakeholder Impact",
    "Human Oversight",
    "Safety & Reliability",
    "Security & Robustness",
    "Governance & Accountability",
]

REQUIRED_KEYS = {
    "purpose",
    "data-types",
    "processes_personal_data",
    "affected-stakeholders",
    "human-oversight",
    "incorrect-output",
    "impact-severity",
    "misuse-potential",
    "accountable-owner",
}

OPTIONAL_KEYS = {
    "influenced-decisions",
    "oversight-mechanism",
    "security-threats",
    "regulatory-scope",
    "governance-review",
}


def _get_template(db, tenant_id: UUID) -> AssessmentTemplate | None:
    return db.scalar(
        select(AssessmentTemplate).where(
            AssessmentTemplate.tenant_id == tenant_id,
            AssessmentTemplate.key == GENERAL_AI_RISK_ASSESSMENT_KEY,
        )
    )


def _get_sections(db, template: AssessmentTemplate) -> list[AssessmentSection]:
    return list(
        db.scalars(
            select(AssessmentSection)
            .where(AssessmentSection.template_id == template.id)
            .order_by(AssessmentSection.display_order, AssessmentSection.id)
        )
    )


def _get_questions(db, template: AssessmentTemplate) -> list[AssessmentQuestion]:
    section_ids = [section.id for section in _get_sections(db, template)]
    if not section_ids:
        return []
    return list(
        db.scalars(
            select(AssessmentQuestion)
            .where(AssessmentQuestion.section_id.in_(section_ids))
            .order_by(AssessmentQuestion.display_order, AssessmentQuestion.id)
        )
    )


def test_seed_creates_general_template_with_sections_and_questions(db):
    tenant_id = settings.default_tenant_id
    inserted = seed_assessment_templates(db, tenant_id)
    db.commit()

    assert inserted == 1
    template = _get_template(db, tenant_id)
    assert template is not None
    assert template.name == "General AI Risk Assessment"
    assert template.is_system is True
    assert template.is_active is True
    assert template.version == 1
    assert template.assessment_type == "initial"
    assert template.description

    sections = _get_sections(db, template)
    assert [section.title for section in sections] == SECTION_TITLES
    assert all(section.is_active for section in sections)
    assert [section.display_order for section in sections] == list(range(1, 8))

    questions = _get_questions(db, template)
    assert len(questions) == 14
    # keys are unique across the whole template, not just per section
    assert len({question.key for question in questions}) == 14
    # every supported question type is exercised by the seeded content
    assert {question.question_type for question in questions} == {
        question_type.value for question_type in QuestionType
    }
    assert {question.key for question in questions if question.required} == REQUIRED_KEYS
    assert {question.key for question in questions if not question.required} == OPTIONAL_KEYS
    assert all(question.is_active for question in questions)
    assert all(question.question_text for question in questions)

    # select questions carry structured options with value/label pairs
    select_questions = [
        question
        for question in questions
        if question.question_type in {"single_select", "multi_select"}
    ]
    assert len(select_questions) == 4
    for question in select_questions:
        assert question.options, question.key
        assert all({"value", "label"} <= set(option) for option in question.options), question.key

    # only the number question declares validation rules (RF-02 §13)
    governed = [question for question in questions if question.validation_rules]
    assert [question.key for question in governed] == ["governance-review"]


def test_seed_is_idempotent(db):
    tenant_id = settings.default_tenant_id
    first = seed_assessment_templates(db, tenant_id)
    db.commit()
    second = seed_assessment_templates(db, tenant_id)
    db.commit()

    assert first == 1
    assert second == 0

    template = _get_template(db, tenant_id)
    assert template is not None
    section_count = db.scalar(
        select(func.count())
        .select_from(AssessmentSection)
        .where(AssessmentSection.template_id == template.id)
    )
    question_count = db.scalar(
        select(func.count())
        .select_from(AssessmentQuestion)
        .join(AssessmentSection, AssessmentQuestion.section_id == AssessmentSection.id)
        .where(AssessmentSection.template_id == template.id)
    )
    assert section_count == 7
    assert question_count == 14


def test_seed_scopes_by_tenant(db):
    default_tenant = settings.default_tenant_id
    other_tenant = uuid4()

    assert seed_assessment_templates(db, default_tenant) == 1
    assert seed_assessment_templates(db, other_tenant) == 1
    db.commit()

    assert _get_template(db, default_tenant) is not None
    assert _get_template(db, other_tenant) is not None

    default_template = _get_template(db, default_tenant)
    other_template = _get_template(db, other_tenant)
    assert default_template.id != other_template.id
    assert len(_get_sections(db, default_template)) == 7
    assert len(_get_sections(db, other_template)) == 7
