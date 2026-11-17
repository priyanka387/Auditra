"""ai risk assessment (RF-02)

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-17 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_VARIANT = sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    """Upgrade schema."""
    # RF-02: five brand-new tables, empty on upgrade. Questionnaires are
    # data (template → sections → questions), assessments reference an
    # RF-01 risk and snapshot the template version, and answers live in
    # their own table so history is never rewritten. Assessments are
    # historical records (never deleted), so every FK is RESTRICT.
    op.create_table(
        "assessment_templates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "assessment_type",
            sa.String(length=32),
            server_default=sa.text("'initial'"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "is_system",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        # the immutable per-tenant key is what makes re-running the
        # template seed duplicate-free
        sa.UniqueConstraint("tenant_id", "key", name="uq_assessment_templates_tenant_key"),
        sa.CheckConstraint(
            "assessment_type IN ('initial', 'pre_deployment', 'periodic', 'change', "
            "'incident_triggered', 'vendor', 'other')",
            name="ck_assessment_templates_type",
        ),
        sa.CheckConstraint("version >= 1", name="ck_assessment_templates_version"),
    )
    op.create_index(
        "ix_assessment_templates_tenant_active",
        "assessment_templates",
        ["tenant_id", "is_active"],
        unique=False,
    )

    op.create_table(
        "assessment_sections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["assessment_templates.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assessment_sections_template_order",
        "assessment_sections",
        ["template_id", "display_order"],
        unique=False,
    )

    op.create_table(
        "assessment_questions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("section_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("question_type", sa.String(length=32), nullable=False),
        sa.Column(
            "required",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("options", JSON_VARIANT, nullable=True),
        sa.Column("validation_rules", JSON_VARIANT, nullable=True),
        sa.Column("help_text", sa.Text(), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["section_id"],
            ["assessment_sections.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("section_id", "key", name="uq_assessment_questions_section_key"),
        sa.CheckConstraint(
            "question_type IN ('text', 'long_text', 'single_select', 'multi_select', "
            "'boolean', 'number')",
            name="ck_assessment_questions_type",
        ),
    )
    op.create_index(
        "ix_assessment_questions_section_order",
        "assessment_questions",
        ["section_id", "display_order"],
        unique=False,
    )

    op.create_table(
        "risk_assessments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("risk_id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("template_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "assessment_type",
            sa.String(length=32),
            server_default=sa.text("'initial'"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        sa.Column("assessor", sa.String(length=255), nullable=True),
        sa.Column("reviewer", sa.String(length=255), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("revision_number", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("original_assessment_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["original_assessment_id"],
            ["risk_assessments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["risk_id"],
            ["risks.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["assessment_templates.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('draft', 'in_progress', 'submitted', 'under_review', "
            "'approved', 'rejected')",
            name="ck_risk_assessments_status",
        ),
        sa.CheckConstraint(
            "assessment_type IN ('initial', 'pre_deployment', 'periodic', 'change', "
            "'incident_triggered', 'vendor', 'other')",
            name="ck_risk_assessments_type",
        ),
        sa.CheckConstraint("revision_number >= 1", name="ck_risk_assessments_revision"),
    )
    op.create_index(
        "ix_risk_assessments_tenant_risk",
        "risk_assessments",
        ["tenant_id", "risk_id"],
        unique=False,
    )
    op.create_index(
        "ix_risk_assessments_tenant_status",
        "risk_assessments",
        ["tenant_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_risk_assessments_tenant_template",
        "risk_assessments",
        ["tenant_id", "template_id"],
        unique=False,
    )
    op.create_index(
        "ix_risk_assessments_tenant_assessor",
        "risk_assessments",
        ["tenant_id", "assessor"],
        unique=False,
    )
    op.create_index(
        "ix_risk_assessments_tenant_reviewer",
        "risk_assessments",
        ["tenant_id", "reviewer"],
        unique=False,
    )
    op.create_index(
        "ix_risk_assessments_tenant_created",
        "risk_assessments",
        ["tenant_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_risk_assessments_tenant_type",
        "risk_assessments",
        ["tenant_id", "assessment_type"],
        unique=False,
    )
    op.create_index(
        "ix_risk_assessments_original",
        "risk_assessments",
        ["original_assessment_id"],
        unique=False,
    )

    op.create_table(
        "assessment_responses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("assessment_id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.Uuid(), nullable=False),
        sa.Column("response_value", JSON_VARIANT, nullable=False),
        sa.Column("answered_by", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id"],
            ["risk_assessments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["question_id"],
            ["assessment_questions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        # one answer per question per assessment: re-saving is an update,
        # never a duplicate row (RF-02 §46)
        sa.UniqueConstraint(
            "assessment_id", "question_id", name="uq_assessment_responses_assessment_question"
        ),
    )
    op.create_index(
        "ix_assessment_responses_question",
        "assessment_responses",
        ["question_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_assessment_responses_question", table_name="assessment_responses")
    op.drop_table("assessment_responses")
    op.drop_index("ix_risk_assessments_original", table_name="risk_assessments")
    op.drop_index("ix_risk_assessments_tenant_type", table_name="risk_assessments")
    op.drop_index("ix_risk_assessments_tenant_created", table_name="risk_assessments")
    op.drop_index("ix_risk_assessments_tenant_reviewer", table_name="risk_assessments")
    op.drop_index("ix_risk_assessments_tenant_assessor", table_name="risk_assessments")
    op.drop_index("ix_risk_assessments_tenant_template", table_name="risk_assessments")
    op.drop_index("ix_risk_assessments_tenant_status", table_name="risk_assessments")
    op.drop_index("ix_risk_assessments_tenant_risk", table_name="risk_assessments")
    op.drop_table("risk_assessments")
    op.drop_index("ix_assessment_questions_section_order", table_name="assessment_questions")
    op.drop_table("assessment_questions")
    op.drop_index("ix_assessment_sections_template_order", table_name="assessment_sections")
    op.drop_table("assessment_sections")
    op.drop_index("ix_assessment_templates_tenant_active", table_name="assessment_templates")
    op.drop_table("assessment_templates")
