"""discovery runs

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-12 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "discovery_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assets_detected", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("assets_created", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("assets_updated", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("assets_unchanged", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
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
            "status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'PARTIAL')",
            name="ck_discovery_runs_status",
        ),
        sa.CheckConstraint(
            "assets_detected >= 0 AND assets_created >= 0 AND assets_updated >= 0 "
            "AND assets_unchanged >= 0 AND error_count >= 0",
            name="ck_discovery_runs_counts_non_negative",
        ),
        sa.CheckConstraint(
            "assets_created + assets_updated + assets_unchanged <= assets_detected",
            name="ck_discovery_runs_asset_counts",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["discovery_sources.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_discovery_runs_tenant_source_started_at",
        "discovery_runs",
        ["tenant_id", "source_id", sa.literal_column("started_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_discovery_runs_tenant_status",
        "discovery_runs",
        ["tenant_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_discovery_runs_tenant_started_at",
        "discovery_runs",
        ["tenant_id", sa.literal_column("started_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    for index in (
        "ix_discovery_runs_tenant_started_at",
        "ix_discovery_runs_tenant_status",
        "ix_discovery_runs_tenant_source_started_at",
    ):
        op.drop_index(index, table_name="discovery_runs")
    op.drop_table("discovery_runs")
