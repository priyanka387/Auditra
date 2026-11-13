"""discovered assets

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-13 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "discovered_assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("last_seen_run_id", sa.Uuid(), nullable=True),
        sa.Column("asset_type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("normalized_identity", sa.String(length=1024), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'ACTIVE'"),
            nullable=False,
        ),
        sa.Column(
            "metadata",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
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
            "asset_type IN ('MODEL', 'MODEL_REFERENCE', 'APPLICATION', 'AGENT', "
            "'WORKFLOW', 'DATASET', 'TOOL', 'DEPLOYMENT', 'ENDPOINT', 'AI_SERVICE')",
            name="ck_discovered_assets_asset_type",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE')",
            name="ck_discovered_assets_status",
        ),
        # identity is scoped to the source it was observed from; cross-
        # source reconciliation (later sub-features) relates entries
        sa.UniqueConstraint(
            "tenant_id",
            "source_id",
            "normalized_identity",
            name="uq_discovered_assets_source_identity",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["discovery_sources.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["last_seen_run_id"],
            ["discovery_runs.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_discovered_assets_tenant_source",
        "discovered_assets",
        ["tenant_id", "source_id"],
        unique=False,
    )
    op.create_index(
        "ix_discovered_assets_tenant_asset_type",
        "discovered_assets",
        ["tenant_id", "asset_type"],
        unique=False,
    )
    op.create_index(
        "ix_discovered_assets_tenant_status",
        "discovered_assets",
        ["tenant_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_discovered_assets_tenant_last_seen_at",
        "discovered_assets",
        ["tenant_id", sa.literal_column("last_seen_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    for index in (
        "ix_discovered_assets_tenant_last_seen_at",
        "ix_discovered_assets_tenant_status",
        "ix_discovered_assets_tenant_asset_type",
        "ix_discovered_assets_tenant_source",
    ):
        op.drop_index(index, table_name="discovered_assets")
    op.drop_table("discovered_assets")
