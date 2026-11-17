"""Unit tests: RF-02 AI risk assessment domain models and enums."""

import sqlalchemy as sa

from app.modules.assessment.enums import AssessmentStatus, AssessmentType, QuestionType
from app.modules.assessment.models import (
    AssessmentQuestion,
    AssessmentResponse,
    AssessmentSection,
    AssessmentTemplate,
    RiskAssessment,
)


def test_assessment_status_values():
    assert {status.value for status in AssessmentStatus} == {
        "draft",
        "in_progress",
        "submitted",
        "under_review",
        "approved",
        "rejected",
    }
    # RF-02 §5: no completed/resolved/closed yet — those belong to RF-04/RF-08
    assert "completed" not in {status.value for status in AssessmentStatus}
    assert "closed" not in {status.value for status in AssessmentStatus}


def test_assessment_type_values():
    assert {assessment_type.value for assessment_type in AssessmentType} == {
        "initial",
        "pre_deployment",
        "periodic",
        "change",
        "incident_triggered",
        "vendor",
        "other",
    }


def test_question_type_values():
    assert {question_type.value for question_type in QuestionType} == {
        "text",
        "long_text",
        "single_select",
        "multi_select",
        "boolean",
        "number",
    }
    # RF-02 §14: date/file/rating/table/conditional are future additions
    assert "date" not in {question_type.value for question_type in QuestionType}


def test_assessment_table_shape():
    assert RiskAssessment.__tablename__ == "risk_assessments"
    columns = {column.name for column in RiskAssessment.__table__.columns}
    assert {
        "id",
        "tenant_id",
        "risk_id",
        "template_id",
        "template_version",
        "assessment_type",
        "status",
        "assessor",
        "reviewer",
        "submitted_at",
        "reviewed_at",
        "approved_at",
        "rejected_at",
        "rejection_reason",
        "revision_number",
        "original_assessment_id",
        "created_at",
        "updated_at",
    } <= columns

    assert AssessmentTemplate.__tablename__ == "assessment_templates"
    template_columns = {column.name for column in AssessmentTemplate.__table__.columns}
    assert {
        "id",
        "tenant_id",
        "key",
        "name",
        "description",
        "assessment_type",
        "version",
        "is_system",
        "is_active",
        "created_at",
        "updated_at",
    } <= template_columns

    assert AssessmentSection.__tablename__ == "assessment_sections"
    section_columns = {column.name for column in AssessmentSection.__table__.columns}
    assert {"id", "template_id", "title", "description", "display_order", "is_active"} <= (
        section_columns
    )

    assert AssessmentQuestion.__tablename__ == "assessment_questions"
    question_columns = {column.name for column in AssessmentQuestion.__table__.columns}
    assert {
        "id",
        "section_id",
        "key",
        "question_text",
        "description",
        "question_type",
        "required",
        "display_order",
        "options",
        "validation_rules",
        "help_text",
        "is_active",
    } <= question_columns

    assert AssessmentResponse.__tablename__ == "assessment_responses"
    response_columns = {column.name for column in AssessmentResponse.__table__.columns}
    assert {"id", "assessment_id", "question_id", "response_value", "answered_by"} <= (
        response_columns
    )


def _sql_of(constraint: sa.CheckConstraint) -> str:
    return constraint.sqltext if isinstance(constraint.sqltext, str) else constraint.sqltext.text


def test_assessment_check_constraints():
    checks = {
        constraint.name: _sql_of(constraint)
        for constraint in RiskAssessment.__table__.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    status_sql = checks["ck_risk_assessments_status"]
    for value in (
        "draft",
        "in_progress",
        "submitted",
        "under_review",
        "approved",
        "rejected",
    ):
        assert value in status_sql
    assert "completed" not in status_sql

    type_sql = checks["ck_risk_assessments_type"]
    for value in (
        "initial",
        "pre_deployment",
        "periodic",
        "change",
        "incident_triggered",
        "vendor",
        "other",
    ):
        assert value in type_sql

    revision_sql = checks["ck_risk_assessments_revision"]
    assert "revision_number" in revision_sql and ">= 1" in revision_sql

    question_checks = {
        constraint.name: _sql_of(constraint)
        for constraint in AssessmentQuestion.__table__.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    question_type_sql = question_checks["ck_assessment_questions_type"]
    for value in (
        "text",
        "long_text",
        "single_select",
        "multi_select",
        "boolean",
        "number",
    ):
        assert value in question_type_sql

    template_checks = {
        constraint.name: _sql_of(constraint)
        for constraint in AssessmentTemplate.__table__.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    assert "ck_assessment_templates_type" in template_checks
    assert "ck_assessment_templates_version" in template_checks


def test_assessment_unique_constraints():
    response_uniques = {
        constraint.name: tuple(constraint.columns.keys())
        for constraint in AssessmentResponse.__table__.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert response_uniques["uq_assessment_responses_assessment_question"] == (
        "assessment_id",
        "question_id",
    )

    question_uniques = {
        constraint.name: tuple(constraint.columns.keys())
        for constraint in AssessmentQuestion.__table__.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert question_uniques["uq_assessment_questions_section_key"] == ("section_id", "key")

    template_uniques = {
        constraint.name: tuple(constraint.columns.keys())
        for constraint in AssessmentTemplate.__table__.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert template_uniques["uq_assessment_templates_tenant_key"] == ("tenant_id", "key")


def test_assessment_relationships_restrict_delete():
    def fk(model, target: str) -> sa.ForeignKey:
        return next(fk for fk in model.__table__.foreign_keys if fk.target_fullname == target)

    assert fk(RiskAssessment, "risks.id").ondelete == "RESTRICT"
    assert fk(RiskAssessment, "assessment_templates.id").ondelete == "RESTRICT"
    assert fk(RiskAssessment, "risk_assessments.id").ondelete == "RESTRICT"
    assert fk(AssessmentSection, "assessment_templates.id").ondelete == "RESTRICT"
    assert fk(AssessmentQuestion, "assessment_sections.id").ondelete == "RESTRICT"
    assert fk(AssessmentResponse, "risk_assessments.id").ondelete == "RESTRICT"
    assert fk(AssessmentResponse, "assessment_questions.id").ondelete == "RESTRICT"
