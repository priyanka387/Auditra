"""discovery sources

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-12 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "discovery_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column(
            "config",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
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
            "source_type IN ('repository')",
            name="ck_discovery_sources_source_type",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')",
            name="ck_discovery_sources_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # uniqueness excludes archived rows so an archived source never
    # blocks re-creating the same name
    op.create_index(
        "uq_discovery_sources_tenant_name",
        "discovery_sources",
        ["tenant_id", "name"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )
    op.create_index(
        "ix_discovery_sources_tenant_status",
        "discovery_sources",
        ["tenant_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_discovery_sources_tenant_source_type",
        "discovery_sources",
        ["tenant_id", "source_type"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    for index in (
        "ix_discovery_sources_tenant_source_type",
        "ix_discovery_sources_tenant_status",
        "uq_discovery_sources_tenant_name",
    ):
        op.drop_index(index, table_name="discovery_sources")
    op.drop_table("discovery_sources")
