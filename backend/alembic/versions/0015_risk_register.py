"""risk register and taxonomy

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-16 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # RF-01: two brand-new tables, empty on upgrade. Risks reference the
    # discovered-asset catalog (Asset Discovery is the upstream source of
    # AI assets) and are archived, never deleted, so every FK is RESTRICT.
    op.create_table(
        "risk_categories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("slug", sa.String(length=100), nullable=False),
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
        # duplicate system categories are prevented per tenant: re-running
        # the seed can only ever match existing rows, never insert twice
        sa.UniqueConstraint("tenant_id", "slug", name="uq_risk_categories_tenant_slug"),
    )
    op.create_index(
        "ix_risk_categories_tenant_active",
        "risk_categories",
        ["tenant_id", "is_active"],
        unique=False,
    )

    op.create_table(
        "risks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column(
            "source",
            sa.String(length=32),
            server_default=sa.text("'manual'"),
            nullable=False,
        ),
        sa.Column("asset_id", sa.Uuid(), nullable=True),
        sa.Column("owner", sa.String(length=255), nullable=True),
        sa.Column("team", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'open'"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
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
            "status IN ('open', 'in_review', 'accepted')",
            name="ck_risks_status",
        ),
        sa.CheckConstraint(
            "source IN ('manual', 'asset_discovery', 'assessment', 'incident', "
            "'audit', 'policy_review', 'vendor_review', 'other')",
            name="ck_risks_source",
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["discovered_assets.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["risk_categories.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_risks_tenant_status", "risks", ["tenant_id", "status"], unique=False)
    op.create_index("ix_risks_tenant_category", "risks", ["tenant_id", "category_id"], unique=False)
    op.create_index("ix_risks_tenant_asset", "risks", ["tenant_id", "asset_id"], unique=False)
    op.create_index("ix_risks_tenant_owner", "risks", ["tenant_id", "owner"], unique=False)
    op.create_index("ix_risks_tenant_archived", "risks", ["tenant_id", "archived_at"], unique=False)
    op.create_index(
        "ix_risks_tenant_updated",
        "risks",
        ["tenant_id", sa.literal_column("updated_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_risks_tenant_updated", table_name="risks")
    op.drop_index("ix_risks_tenant_archived", table_name="risks")
    op.drop_index("ix_risks_tenant_owner", table_name="risks")
    op.drop_index("ix_risks_tenant_asset", table_name="risks")
    op.drop_index("ix_risks_tenant_category", table_name="risks")
    op.drop_index("ix_risks_tenant_status", table_name="risks")
    op.drop_table("risks")
    op.drop_index("ix_risk_categories_tenant_active", table_name="risk_categories")
    op.drop_table("risk_categories")
