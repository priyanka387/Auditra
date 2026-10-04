"""model versioning

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "model_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("model_id", sa.Uuid(), nullable=False),
        sa.Column("identity_type", sa.String(length=64), nullable=False),
        sa.Column("version_label", sa.String(length=255), nullable=False),
        sa.Column("native_version_id", sa.String(length=512), nullable=True),
        sa.Column("canonical_version_key", sa.String(length=1024), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("lifecycle_state", sa.String(length=32), server_default="DRAFT", nullable=False),
        sa.Column(
            "metadata",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_reference", sa.String(length=512), nullable=True),
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
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("record_version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "lifecycle_state IN ('DRAFT', 'ACTIVE', 'DEPRECATED', 'RETIRED', 'ARCHIVED')",
            name="ck_model_versions_lifecycle_state",
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["models.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "model_id",
            "canonical_version_key",
            name="uq_model_versions_tenant_model_canonical_key",
        ),
    )
    op.create_index(
        "ix_model_versions_tenant_id_model_id_created_at",
        "model_versions",
        ["tenant_id", "model_id", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_model_versions_tenant_id_model_id_lifecycle_state",
        "model_versions",
        ["tenant_id", "model_id", "lifecycle_state"],
        unique=False,
    )
    op.create_index(
        "ix_model_versions_tenant_id_model_id_updated_at",
        "model_versions",
        ["tenant_id", "model_id", sa.literal_column("updated_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_model_versions_tenant_id_model_id_version_label",
        "model_versions",
        ["tenant_id", "model_id", "version_label"],
        unique=False,
    )
    op.create_index(
        "ix_model_versions_tenant_id_model_id_native_version_id",
        "model_versions",
        ["tenant_id", "model_id", "native_version_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_model_versions_tenant_id_model_id_native_version_id", table_name="model_versions"
    )
    op.drop_index("ix_model_versions_tenant_id_model_id_version_label", table_name="model_versions")
    op.drop_index("ix_model_versions_tenant_id_model_id_updated_at", table_name="model_versions")
    op.drop_index(
        "ix_model_versions_tenant_id_model_id_lifecycle_state", table_name="model_versions"
    )
    op.drop_index("ix_model_versions_tenant_id_model_id_created_at", table_name="model_versions")
    op.drop_table("model_versions")
