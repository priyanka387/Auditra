"""discovered asset run traceability

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-14 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # link rows are historical traceability, never mutated in place: an
    # asset observed across runs keeps every (asset, run) pair
    op.create_table(
        "discovered_asset_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("discovered_asset_id", sa.Uuid(), nullable=False),
        sa.Column("discovery_run_id", sa.Uuid(), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
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
            ["discovered_asset_id"],
            ["discovered_assets.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["discovery_run_id"],
            ["discovery_runs.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_discovered_asset_runs_asset_run",
        "discovered_asset_runs",
        ["discovered_asset_id", "discovery_run_id"],
        unique=True,
    )
    op.create_index(
        "ix_discovered_asset_runs_tenant_run",
        "discovered_asset_runs",
        ["tenant_id", "discovery_run_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_discovered_asset_runs_tenant_run", table_name="discovered_asset_runs")
    op.drop_index("uq_discovered_asset_runs_asset_run", table_name="discovered_asset_runs")
    op.drop_table("discovered_asset_runs")
