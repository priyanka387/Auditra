"""risk treatment (RF-04)

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-20 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # RF-04: three brand-new tables, empty on upgrade. A treatment is the
    # decision, its actions are the work, and residual assessments are
    # append-only expected-outcome snapshots. History is never deleted,
    # so every FK is RESTRICT; the partial unique index guarantees at
    # most one active treatment per risk (plan §48).
    op.create_table(
        "risk_treatments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("risk_id", sa.Uuid(), nullable=False),
        sa.Column("strategy", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'not_started'"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("owner", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("planned_start_date", sa.Date(), nullable=True),
        sa.Column("target_completion_date", sa.Date(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
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
            ["risk_id"],
            ["risks.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "strategy IN ('mitigate', 'accept', 'avoid', 'transfer')",
            name="ck_risk_treatments_strategy",
        ),
        sa.CheckConstraint(
            "status IN ('not_started', 'planned', 'in_progress', 'completed', 'cancelled')",
            name="ck_risk_treatments_status",
        ),
        sa.CheckConstraint(
            "planned_start_date IS NULL OR target_completion_date IS NULL "
            "OR planned_start_date <= target_completion_date",
            name="ck_risk_treatments_dates",
        ),
        sa.CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL) "
            "OR (status <> 'completed' AND completed_at IS NULL)",
            name="ck_risk_treatments_completed",
        ),
        sa.CheckConstraint(
            "(status = 'cancelled' AND cancelled_at IS NOT NULL "
            "AND cancellation_reason IS NOT NULL) "
            "OR (status <> 'cancelled' AND cancelled_at IS NULL)",
            name="ck_risk_treatments_cancelled",
        ),
    )
    # one active treatment per risk, whatever the application does
    op.create_index(
        "uq_risk_treatments_risk_active",
        "risk_treatments",
        ["risk_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('not_started', 'planned', 'in_progress')"),
    )
    op.create_index(
        "ix_risk_treatments_tenant_risk", "risk_treatments", ["tenant_id", "risk_id"], unique=False
    )
    op.create_index(
        "ix_risk_treatments_tenant_status",
        "risk_treatments",
        ["tenant_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_risk_treatments_tenant_strategy",
        "risk_treatments",
        ["tenant_id", "strategy"],
        unique=False,
    )
    op.create_index(
        "ix_risk_treatments_tenant_owner", "risk_treatments", ["tenant_id", "owner"], unique=False
    )
    op.create_index(
        "ix_risk_treatments_tenant_target",
        "risk_treatments",
        ["tenant_id", "target_completion_date"],
        unique=False,
    )
    op.create_index(
        "ix_risk_treatments_risk_created",
        "risk_treatments",
        ["risk_id", sa.literal_column("created_at DESC")],
        unique=False,
    )

    op.create_table(
        "risk_treatment_actions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("treatment_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'todo'"),
            nullable=False,
        ),
        sa.Column("owner", sa.String(length=255), nullable=False),
        sa.Column(
            "priority",
            sa.String(length=32),
            server_default=sa.text("'medium'"),
            nullable=False,
        ),
        sa.Column("planned_start_date", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
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
            ["treatment_id"],
            ["risk_treatments.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('todo', 'in_progress', 'completed', 'cancelled')",
            name="ck_risk_treatment_actions_status",
        ),
        sa.CheckConstraint(
            "priority IN ('low', 'medium', 'high', 'critical')",
            name="ck_risk_treatment_actions_priority",
        ),
        sa.CheckConstraint(
            "planned_start_date IS NULL OR due_date IS NULL OR planned_start_date <= due_date",
            name="ck_risk_treatment_actions_dates",
        ),
        sa.CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL) "
            "OR (status <> 'completed' AND completed_at IS NULL)",
            name="ck_risk_treatment_actions_completed",
        ),
        sa.CheckConstraint(
            "(status = 'cancelled' AND cancelled_at IS NOT NULL "
            "AND cancellation_reason IS NOT NULL) "
            "OR (status <> 'cancelled' AND cancelled_at IS NULL)",
            name="ck_risk_treatment_actions_cancelled",
        ),
    )
    op.create_index(
        "ix_risk_treatment_actions_tenant",
        "risk_treatment_actions",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_risk_treatment_actions_treatment",
        "risk_treatment_actions",
        ["treatment_id"],
        unique=False,
    )
    op.create_index(
        "ix_risk_treatment_actions_treatment_status",
        "risk_treatment_actions",
        ["treatment_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_risk_treatment_actions_tenant_owner",
        "risk_treatment_actions",
        ["tenant_id", "owner"],
        unique=False,
    )
    op.create_index(
        "ix_risk_treatment_actions_tenant_due",
        "risk_treatment_actions",
        ["tenant_id", "due_date"],
        unique=False,
    )

    op.create_table(
        "risk_treatment_residual_assessments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("treatment_id", sa.Uuid(), nullable=False),
        sa.Column("likelihood_value", sa.Integer(), nullable=False),
        sa.Column("impact_value", sa.Integer(), nullable=False),
        sa.Column("calculated_score", sa.Integer(), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column(
            "assessment_type",
            sa.String(length=32),
            server_default=sa.text("'expected'"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["treatment_id"],
            ["risk_treatments.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "assessment_type IN ('expected', 'verified')",
            name="ck_risk_treatment_residual_type",
        ),
        sa.CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_risk_treatment_residual_severity",
        ),
        sa.CheckConstraint(
            "likelihood_value >= 1 AND impact_value >= 1 AND calculated_score >= 1",
            name="ck_risk_treatment_residual_values",
        ),
    )
    op.create_index(
        "ix_risk_treatment_residual_tenant",
        "risk_treatment_residual_assessments",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_risk_treatment_residual_treatment",
        "risk_treatment_residual_assessments",
        ["treatment_id"],
        unique=False,
    )
    op.create_index(
        "ix_risk_treatment_residual_treatment_created",
        "risk_treatment_residual_assessments",
        ["treatment_id", sa.literal_column("created_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_risk_treatment_residual_treatment_created",
        table_name="risk_treatment_residual_assessments",
    )
    op.drop_index(
        "ix_risk_treatment_residual_treatment",
        table_name="risk_treatment_residual_assessments",
    )
    op.drop_index(
        "ix_risk_treatment_residual_tenant",
        table_name="risk_treatment_residual_assessments",
    )
    op.drop_table("risk_treatment_residual_assessments")

    op.drop_index("ix_risk_treatment_actions_tenant_due", table_name="risk_treatment_actions")
    op.drop_index("ix_risk_treatment_actions_tenant_owner", table_name="risk_treatment_actions")
    op.drop_index("ix_risk_treatment_actions_treatment_status", table_name="risk_treatment_actions")
    op.drop_index("ix_risk_treatment_actions_treatment", table_name="risk_treatment_actions")
    op.drop_index("ix_risk_treatment_actions_tenant", table_name="risk_treatment_actions")
    op.drop_table("risk_treatment_actions")

    op.drop_index("ix_risk_treatments_risk_created", table_name="risk_treatments")
    op.drop_index("ix_risk_treatments_tenant_target", table_name="risk_treatments")
    op.drop_index("ix_risk_treatments_tenant_owner", table_name="risk_treatments")
    op.drop_index("ix_risk_treatments_tenant_strategy", table_name="risk_treatments")
    op.drop_index("ix_risk_treatments_tenant_status", table_name="risk_treatments")
    op.drop_index("ix_risk_treatments_tenant_risk", table_name="risk_treatments")
    op.drop_index("uq_risk_treatments_risk_active", table_name="risk_treatments")
    op.drop_table("risk_treatments")
