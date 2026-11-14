"""discovery evidence provenance

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-15 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Discovery Evidence (SF9): a brand-new append-only table, so no data
    # migration is needed — it is empty on upgrade. Downgrade drops the
    # evidence history only, which is recoverable because evidence is
    # derived from re-runnable scans.
    op.create_table(
        "discovery_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("discovered_asset_id", sa.Uuid(), nullable=False),
        sa.Column("discovery_source_id", sa.Uuid(), nullable=False),
        sa.Column("discovery_run_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_type", sa.String(length=32), nullable=False),
        sa.Column("detection_rule", sa.String(length=255), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "metadata",
            sa.JSON().with_variant(JSONB, "postgresql"),
            server_default=sa.text("'{}'"),
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
        sa.CheckConstraint(
            "evidence_type IN ('provider_reference', 'framework_reference', "
            "'model_reference', 'agent_reference', 'workflow_reference')",
            name="ck_discovery_evidence_evidence_type",
        ),
        sa.CheckConstraint(
            "line_number IS NULL OR (line_number >= 1 AND line_number <= 10000000)",
            name="ck_discovery_evidence_line_number",
        ),
        sa.ForeignKeyConstraint(
            ["discovered_asset_id"],
            ["discovered_assets.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["discovery_source_id"],
            ["discovery_sources.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["discovery_run_id"],
            ["discovery_runs.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # one row per observation per run — ON CONFLICT DO NOTHING target
    op.create_index(
        "uq_discovery_evidence_observation",
        "discovery_evidence",
        ["discovered_asset_id", "discovery_run_id", "detection_rule", "file_path", "line_number"],
        unique=True,
    )
    op.create_index(
        "ix_discovery_evidence_tenant_asset_observed_at",
        "discovery_evidence",
        ["tenant_id", "discovered_asset_id", "observed_at"],
        unique=False,
    )
    op.create_index(
        "ix_discovery_evidence_tenant_run",
        "discovery_evidence",
        ["tenant_id", "discovery_run_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_discovery_evidence_tenant_run", table_name="discovery_evidence")
    op.drop_index(
        "ix_discovery_evidence_tenant_asset_observed_at", table_name="discovery_evidence"
    )
    op.drop_index("uq_discovery_evidence_observation", table_name="discovery_evidence")
    op.drop_table("discovery_evidence")
