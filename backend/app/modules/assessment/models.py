"""RF-02 domain models: template → sections → questions → assessment → responses.

The architecture is deliberately data-driven (RF-02 §3): questions are
rows, never code, so new questionnaires ship as seed/template data
without touching the workflow engine. An assessment snapshots the
template version it started against and keeps its own response rows, so
later template edits never rewrite historical answers (RF-02 §11).
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# JSON-compatible payloads (question options, validation rules, answer
# values) — JSONB on PostgreSQL for real querying, JSON elsewhere
JSONVariant = JSON().with_variant(JSONB, "postgresql")


class AssessmentTemplate(Base):
    """A configurable questionnaire definition (RF-02 §10).

    System templates are provisioned per tenant by the idempotent seed
    (``seed.GENERAL_AI_RISK_ASSESSMENT_KEY``) with ``is_system=True``;
    the immutable ``key`` is what makes re-seeding duplicate-free, the
    way RF-01's ``slug`` does for categories. ``version`` is snapshotted
    onto each assessment (RF-02 §11): PATCH edits metadata only and
    never bumps it, so a historical assessment always resolves against
    the version it was created with.
    """

    __tablename__ = "assessment_templates"
    __table_args__ = (
        UniqueConstraint("tenant_id", "key", name="uq_assessment_templates_tenant_key"),
        CheckConstraint(
            "assessment_type IN ('initial', 'pre_deployment', 'periodic', 'change', "
            "'incident_triggered', 'vendor', 'other')",
            name="ck_assessment_templates_type",
        ),
        CheckConstraint("version >= 1", name="ck_assessment_templates_version"),
        Index("ix_assessment_templates_tenant_active", "tenant_id", "is_active"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()

    key: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    assessment_type: Mapped[str] = mapped_column(
        String(32), default="initial", server_default=text("'initial'")
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    is_system: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AssessmentSection(Base):
    """Ordered grouping of questions inside a template (RF-02 §12)."""

    __tablename__ = "assessment_sections"
    __table_args__ = (
        Index("ix_assessment_sections_template_order", "template_id", "display_order"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    template_id: Mapped[UUID] = mapped_column(
        ForeignKey("assessment_templates.id", ondelete="RESTRICT")
    )

    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    display_order: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AssessmentQuestion(Base):
    """One data-driven questionnaire item (RF-02 §13).

    ``options`` holds the structured ``[{"value", "label"}]`` choices of
    select questions (RF-02 §15) and ``validation_rules`` is reserved
    for per-question bounds; both are template data. Responses reference
    this row by id, so an answer survives any future template revision.
    """

    __tablename__ = "assessment_questions"
    __table_args__ = (
        UniqueConstraint("section_id", "key", name="uq_assessment_questions_section_key"),
        CheckConstraint(
            "question_type IN ('text', 'long_text', 'single_select', 'multi_select', "
            "'boolean', 'number')",
            name="ck_assessment_questions_type",
        ),
        Index("ix_assessment_questions_section_order", "section_id", "display_order"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    section_id: Mapped[UUID] = mapped_column(
        ForeignKey("assessment_sections.id", ondelete="RESTRICT")
    )

    key: Mapped[str] = mapped_column(String(100))
    question_text: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    question_type: Mapped[str] = mapped_column(String(32))
    required: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    display_order: Mapped[int] = mapped_column(Integer)
    options: Mapped[list | None] = mapped_column(JSONVariant)
    validation_rules: Mapped[dict | None] = mapped_column(JSONVariant)
    help_text: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RiskAssessment(Base):
    """One assessment of one RF-01 risk (RF-02 §17).

    A risk accumulates assessments over its lifecycle — this row is a
    historical record, never an overwrite of an earlier one (RF-02 §8).
    The AI asset is reached through ``risk`` rather than stored here
    (RF-02 §9), keeping a single source of truth. ``template_version``
    snapshots the template at creation (RF-02 §11), ``revision_number``
    + ``original_assessment_id`` chain rejected-and-revised attempts so
    audit history is never lost (RF-02 §33). ``assessor``/``reviewer``
    are free-text until an identity model exists (see plan Ruling 3).
    """

    __tablename__ = "risk_assessments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'in_progress', 'submitted', 'under_review', "
            "'approved', 'rejected')",
            name="ck_risk_assessments_status",
        ),
        CheckConstraint(
            "assessment_type IN ('initial', 'pre_deployment', 'periodic', 'change', "
            "'incident_triggered', 'vendor', 'other')",
            name="ck_risk_assessments_type",
        ),
        CheckConstraint("revision_number >= 1", name="ck_risk_assessments_revision"),
        Index("ix_risk_assessments_tenant_risk", "tenant_id", "risk_id"),
        Index("ix_risk_assessments_tenant_status", "tenant_id", "status"),
        Index("ix_risk_assessments_tenant_template", "tenant_id", "template_id"),
        Index("ix_risk_assessments_tenant_assessor", "tenant_id", "assessor"),
        Index("ix_risk_assessments_tenant_reviewer", "tenant_id", "reviewer"),
        Index("ix_risk_assessments_tenant_created", "tenant_id", "created_at"),
        Index("ix_risk_assessments_tenant_type", "tenant_id", "assessment_type"),
        Index("ix_risk_assessments_original", "original_assessment_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()

    risk_id: Mapped[UUID] = mapped_column(ForeignKey("risks.id", ondelete="RESTRICT"))
    template_id: Mapped[UUID] = mapped_column(
        ForeignKey("assessment_templates.id", ondelete="RESTRICT")
    )
    template_version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    assessment_type: Mapped[str] = mapped_column(
        String(32), default="initial", server_default=text("'initial'")
    )
    status: Mapped[str] = mapped_column(String(32), default="draft", server_default=text("'draft'"))

    assessor: Mapped[str | None] = mapped_column(String(255))
    reviewer: Mapped[str | None] = mapped_column(String(255))

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)

    revision_number: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    original_assessment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("risk_assessments.id", ondelete="RESTRICT")
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AssessmentResponse(Base):
    """One saved answer to one question (RF-02 §18).

    Responses live in their own table — never as a JSON blob on the
    assessment — so answers validate against their question, stay
    queryable for future reporting, and carry answer-level history.
    The unique ``(assessment_id, question_id)`` pair makes re-saving an
    answer an update instead of a duplicate row (RF-02 §46).
    """

    __tablename__ = "assessment_responses"
    __table_args__ = (
        UniqueConstraint(
            "assessment_id", "question_id", name="uq_assessment_responses_assessment_question"
        ),
        Index("ix_assessment_responses_question", "question_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    assessment_id: Mapped[UUID] = mapped_column(
        ForeignKey("risk_assessments.id", ondelete="RESTRICT")
    )
    question_id: Mapped[UUID] = mapped_column(
        ForeignKey("assessment_questions.id", ondelete="RESTRICT")
    )

    response_value: Mapped[dict | list | str | int | float | bool] = mapped_column(JSONVariant)
    answered_by: Mapped[str | None] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
