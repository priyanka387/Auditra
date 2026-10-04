"""application agent association inventory

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-05 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "applications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("application_type", sa.String(length=64), nullable=True),
        sa.Column("owner_name", sa.String(length=255), nullable=True),
        sa.Column("team_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="ACTIVE", nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column(
            "tags",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "metadata",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            server_default=sa.text("'{}'"),
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
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE', 'ARCHIVED')",
            name="ck_applications_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_applications_tenant_slug"),
    )
    op.create_index("ix_applications_tenant_id_status", "applications", ["tenant_id", "status"])
    op.create_index(
        "ix_applications_tenant_id_created_at",
        "applications",
        ["tenant_id", sa.literal_column("created_at DESC")],
        unique=False,
    )

    op.create_table(
        "agents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("agent_type", sa.String(length=64), nullable=True),
        sa.Column("framework", sa.String(length=64), nullable=True),
        sa.Column("framework_version", sa.String(length=64), nullable=True),
        sa.Column("runtime_identifier", sa.String(length=255), nullable=True),
        sa.Column("owner_name", sa.String(length=255), nullable=True),
        sa.Column("team_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="ACTIVE", nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column(
            "capabilities",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "tags",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "metadata",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            server_default=sa.text("'{}'"),
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
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE', 'ARCHIVED')",
            name="ck_agents_status",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "application_id", "slug", name="uq_agents_tenant_application_slug"
        ),
    )
    op.create_index("ix_agents_application_id", "agents", ["application_id"])
    op.create_index("ix_agents_framework", "agents", ["framework"])
    op.create_index("ix_agents_tenant_id_status", "agents", ["tenant_id", "status"])

    op.create_table(
        "agent_model_associations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("model_id", sa.Uuid(), nullable=False),
        sa.Column("model_version_id", sa.Uuid(), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("selection_priority", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="ACTIVE", nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column(
            "configuration",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "metadata",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.CheckConstraint(
            "role IN ('PRIMARY', 'FALLBACK', 'EMBEDDING', 'RERANKER', 'VISION', "
            "'MULTIMODAL', 'MODERATION', 'OTHER')",
            name="ck_agent_model_associations_role",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'DISABLED')",
            name="ck_agent_model_associations_status",
        ),
        sa.CheckConstraint(
            "selection_priority >= 1",
            name="ck_agent_model_associations_priority",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["models.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["model_version_id"],
            ["model_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_model_associations_agent_id_status",
        "agent_model_associations",
        ["agent_id", "status"],
    )
    op.create_index(
        "ix_agent_model_associations_model_id_status",
        "agent_model_associations",
        ["model_id", "status"],
    )
    op.create_index(
        "ix_agent_model_associations_model_version_id",
        "agent_model_associations",
        ["model_version_id"],
    )
    op.create_index(
        "ix_agent_model_associations_tenant_id_created_at",
        "agent_model_associations",
        ["tenant_id", sa.literal_column("created_at DESC")],
        unique=False,
    )
    # ponytail: identity excludes nullable model_version_id (Postgres NULL
    # semantics) and only covers ACTIVE rows so a disabled association never
    # blocks re-associating the same role/priority
    op.create_index(
        "uq_agent_model_associations_agent_role_priority",
        "agent_model_associations",
        ["agent_id", "role", "selection_priority"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index(
        "uq_agent_model_associations_agent_model_role",
        "agent_model_associations",
        ["agent_id", "model_id", "role"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX IF EXISTS uq_agent_model_associations_agent_model_role")
    op.execute("DROP INDEX IF EXISTS uq_agent_model_associations_agent_role_priority")
    op.drop_index(
        "ix_agent_model_associations_tenant_id_created_at",
        table_name="agent_model_associations",
    )
    op.drop_index(
        "ix_agent_model_associations_model_version_id",
        table_name="agent_model_associations",
    )
    op.drop_index(
        "ix_agent_model_associations_model_id_status",
        table_name="agent_model_associations",
    )
    op.drop_index(
        "ix_agent_model_associations_agent_id_status",
        table_name="agent_model_associations",
    )
    op.drop_table("agent_model_associations")
    op.drop_index("ix_agents_tenant_id_status", table_name="agents")
    op.drop_index("ix_agents_framework", table_name="agents")
    op.drop_index("ix_agents_application_id", table_name="agents")
    op.drop_table("agents")
    op.drop_index("ix_applications_tenant_id_created_at", table_name="applications")
    op.drop_index("ix_applications_tenant_id_status", table_name="applications")
    op.drop_table("applications")
