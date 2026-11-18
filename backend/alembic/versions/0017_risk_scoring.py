"""risk scoring (RF-03)

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-18 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # RF-03: five brand-new tables, empty on upgrade. A framework owns
    # its likelihood scale, impact scale and severity thresholds;
    # scores are append-only history rows (never updated) with at most
    # one current row per risk. Historical records are never deleted,
    # so every FK is RESTRICT.
    op.create_table(
        "risk_scoring_frameworks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "method",
            sa.String(length=32),
            server_default=sa.text("'likelihood_impact'"),
            nullable=False,
        ),
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
        # name+version is the stable identity a score snapshots against
        # (RF-03 §9 versioning); re-seeding skips an existing pair
        sa.UniqueConstraint(
            "tenant_id", "name", "version", name="uq_risk_scoring_frameworks_tenant_name_version"
        ),
        sa.CheckConstraint(
            "method IN ('likelihood_impact')",
            name="ck_risk_scoring_frameworks_method",
        ),
        sa.CheckConstraint("version >= 1", name="ck_risk_scoring_frameworks_version"),
    )
    op.create_index(
        "ix_risk_scoring_frameworks_tenant_active",
        "risk_scoring_frameworks",
        ["tenant_id", "is_active"],
        unique=False,
    )

    op.create_table(
        "risk_likelihood_levels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("framework_id", sa.Uuid(), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("display_order", sa.Integer(), server_default=sa.text("1"), nullable=False),
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
            ["framework_id"],
            ["risk_scoring_frameworks.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "framework_id", "value", name="uq_risk_likelihood_levels_framework_value"
        ),
        sa.CheckConstraint("value >= 1", name="ck_risk_likelihood_levels_value"),
    )
    op.create_index(
        "ix_risk_likelihood_levels_framework",
        "risk_likelihood_levels",
        ["framework_id"],
        unique=False,
    )

    op.create_table(
        "risk_impact_levels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("framework_id", sa.Uuid(), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("display_order", sa.Integer(), server_default=sa.text("1"), nullable=False),
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
            ["framework_id"],
            ["risk_scoring_frameworks.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("framework_id", "value", name="uq_risk_impact_levels_framework_value"),
        sa.CheckConstraint("value >= 1", name="ck_risk_impact_levels_value"),
    )
    op.create_index(
        "ix_risk_impact_levels_framework",
        "risk_impact_levels",
        ["framework_id"],
        unique=False,
    )

    op.create_table(
        "risk_severity_thresholds",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("framework_id", sa.Uuid(), nullable=False),
        sa.Column("minimum_score", sa.Integer(), nullable=False),
        sa.Column("maximum_score", sa.Integer(), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("display_order", sa.Integer(), server_default=sa.text("1"), nullable=False),
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
            ["framework_id"],
            ["risk_scoring_frameworks.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "framework_id", "severity", name="uq_risk_severity_thresholds_framework_severity"
        ),
        sa.CheckConstraint(
            "minimum_score >= 1 AND minimum_score <= maximum_score",
            name="ck_risk_severity_thresholds_range",
        ),
        sa.CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_risk_severity_thresholds_severity",
        ),
        sa.CheckConstraint("priority >= 1", name="ck_risk_severity_thresholds_priority"),
    )
    op.create_index(
        "ix_risk_severity_thresholds_framework",
        "risk_severity_thresholds",
        ["framework_id"],
        unique=False,
    )

    op.create_table(
        "risk_scores",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("risk_id", sa.Uuid(), nullable=False),
        sa.Column("framework_id", sa.Uuid(), nullable=False),
        sa.Column("framework_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("likelihood_value", sa.Integer(), nullable=False),
        sa.Column("likelihood_label", sa.String(length=100), nullable=False),
        sa.Column("impact_value", sa.Integer(), nullable=False),
        sa.Column("impact_label", sa.String(length=100), nullable=False),
        sa.Column("calculated_score", sa.Integer(), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("override_score", sa.Integer(), nullable=True),
        sa.Column("effective_score", sa.Integer(), nullable=False),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column(
            "is_inherent",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "is_current",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "is_override",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("assessment_id", sa.Uuid(), nullable=True),
        sa.Column(
            "source",
            sa.String(length=32),
            server_default=sa.text("'manual'"),
            nullable=False,
        ),
        sa.Column("scored_by", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["risk_id"],
            ["risks.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["framework_id"],
            ["risk_scoring_frameworks.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id"],
            ["risk_assessments.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_risk_scores_severity",
        ),
        sa.CheckConstraint(
            "source IN ('manual', 'assessment')",
            name="ck_risk_scores_source",
        ),
        sa.CheckConstraint(
            "(is_override = false AND override_score IS NULL) "
            "OR (is_override = true AND override_score IS NOT NULL)",
            name="ck_risk_scores_override",
        ),
        sa.CheckConstraint("framework_version >= 1", name="ck_risk_scores_framework_version"),
    )
    # the database guarantees the register's "current score" invariant
    # (RF-03 §16): one current row per risk, no matter what the app does
    op.create_index(
        "uq_risk_scores_risk_current",
        "risk_scores",
        ["risk_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )
    op.create_index(
        "ix_risk_scores_tenant_risk", "risk_scores", ["tenant_id", "risk_id"], unique=False
    )
    op.create_index(
        "ix_risk_scores_risk_created", "risk_scores", ["risk_id", "created_at"], unique=False
    )
    op.create_index("ix_risk_scores_framework", "risk_scores", ["framework_id"], unique=False)
    op.create_index(
        "ix_risk_scores_tenant_severity", "risk_scores", ["tenant_id", "severity"], unique=False
    )
    op.create_index(
        "ix_risk_scores_tenant_priority", "risk_scores", ["tenant_id", "priority"], unique=False
    )
    op.create_index(
        "ix_risk_scores_tenant_created", "risk_scores", ["tenant_id", "created_at"], unique=False
    )
    op.create_index("ix_risk_scores_assessment", "risk_scores", ["assessment_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_risk_scores_assessment", table_name="risk_scores")
    op.drop_index("ix_risk_scores_tenant_created", table_name="risk_scores")
    op.drop_index("ix_risk_scores_tenant_priority", table_name="risk_scores")
    op.drop_index("ix_risk_scores_tenant_severity", table_name="risk_scores")
    op.drop_index("ix_risk_scores_framework", table_name="risk_scores")
    op.drop_index("ix_risk_scores_risk_created", table_name="risk_scores")
    op.drop_index("ix_risk_scores_tenant_risk", table_name="risk_scores")
    op.drop_index("uq_risk_scores_risk_current", table_name="risk_scores")
    op.drop_table("risk_scores")
    op.drop_index("ix_risk_severity_thresholds_framework", table_name="risk_severity_thresholds")
    op.drop_table("risk_severity_thresholds")
    op.drop_index("ix_risk_impact_levels_framework", table_name="risk_impact_levels")
    op.drop_table("risk_impact_levels")
    op.drop_index("ix_risk_likelihood_levels_framework", table_name="risk_likelihood_levels")
    op.drop_table("risk_likelihood_levels")
    op.drop_index("ix_risk_scoring_frameworks_tenant_active", table_name="risk_scoring_frameworks")
    op.drop_table("risk_scoring_frameworks")
