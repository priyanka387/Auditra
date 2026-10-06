"""model discovery observations

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-07 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "model_discovery",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_identifier", sa.String(length=255), nullable=True),
        sa.Column("external_identifier", sa.String(length=255), nullable=True),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("model_identifier", sa.String(length=512), nullable=False),
        sa.Column("model_type", sa.String(length=64), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("canonical_identity", sa.String(length=1024), nullable=False),
        sa.Column(
            "metadata",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("matched_model_id", sa.Uuid(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "observation_count",
            sa.BigInteger(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
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
            "observation_count >= 1",
            name="ck_model_discovery_observation_count",
        ),
        sa.CheckConstraint(
            "source_type IN ('manual', 'langchain', 'langgraph')",
            name="ck_model_discovery_source_type",
        ),
        sa.CheckConstraint(
            "status IN ('DISCOVERED', 'MATCHED', 'UNRESOLVED', 'REGISTERED', 'IGNORED', 'FAILED')",
            name="ck_model_discovery_status",
        ),
        sa.ForeignKeyConstraint(
            ["matched_model_id"],
            ["models.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_type",
            "source_identifier",
            "canonical_identity",
            name="uq_model_discovery_tenant_source_identity",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        "ix_model_discovery_tenant_canonical_identity",
        "model_discovery",
        ["tenant_id", "canonical_identity"],
        unique=False,
    )
    op.create_index(
        "ix_model_discovery_tenant_status",
        "model_discovery",
        ["tenant_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_model_discovery_tenant_source_type",
        "model_discovery",
        ["tenant_id", "source_type"],
        unique=False,
    )
    op.create_index(
        "ix_model_discovery_tenant_provider",
        "model_discovery",
        ["tenant_id", "provider"],
        unique=False,
    )
    op.create_index(
        "ix_model_discovery_matched_model_id",
        "model_discovery",
        ["matched_model_id"],
        unique=False,
    )
    op.create_index(
        "ix_model_discovery_tenant_last_seen_at",
        "model_discovery",
        ["tenant_id", sa.literal_column("last_seen_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    for index in (
        "ix_model_discovery_tenant_last_seen_at",
        "ix_model_discovery_matched_model_id",
        "ix_model_discovery_tenant_provider",
        "ix_model_discovery_tenant_source_type",
        "ix_model_discovery_tenant_status",
        "ix_model_discovery_tenant_canonical_identity",
    ):
        op.drop_index(index, table_name="model_discovery")
    op.drop_table("model_discovery")
