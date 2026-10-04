"""model deployment inventory

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-05 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "model_deployments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("model_version_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column("deployment_kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="planned", nullable=False),
        sa.Column("status_source", sa.String(length=32), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_name", sa.String(length=255), nullable=True),
        sa.Column("region", sa.String(length=128), nullable=True),
        sa.Column("cluster_name", sa.String(length=255), nullable=True),
        sa.Column("namespace", sa.String(length=255), nullable=True),
        sa.Column("runtime", sa.String(length=128), nullable=True),
        sa.Column("serving_framework", sa.String(length=128), nullable=True),
        sa.Column("image_uri", sa.Text(), nullable=True),
        sa.Column("desired_replicas", sa.Integer(), nullable=True),
        sa.Column("observed_replicas", sa.Integer(), nullable=True),
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
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_reference", sa.String(length=255), nullable=True),
        sa.Column("deployed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("record_version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "deployment_kind IN ('online_inference', 'batch', 'embedded', 'edge', "
            "'scheduled', 'other')",
            name="ck_model_deployments_deployment_kind",
        ),
        sa.CheckConstraint(
            "environment IN ('development', 'staging', 'production')",
            name="ck_model_deployments_environment",
        ),
        sa.CheckConstraint(
            "desired_replicas >= 0 AND observed_replicas >= 0",
            name="ck_model_deployments_replicas",
        ),
        sa.CheckConstraint(
            "status IN ('planned', 'deploying', 'active', 'degraded', 'failed', "
            "'stopping', 'stopped', 'deprecated')",
            name="ck_model_deployments_status",
        ),
        sa.ForeignKeyConstraint(
            ["model_version_id"],
            ["model_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_model_deployments_tenant_version_env_target_ns",
        "model_deployments",
        ["tenant_id", "model_version_id", "environment", "target_name", "namespace"],
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )
    op.create_index(
        "ix_model_deployments_tenant_created_at",
        "model_deployments",
        ["tenant_id", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_model_deployments_tenant_environment_status",
        "model_deployments",
        ["tenant_id", "environment", "status"],
        unique=False,
    )
    op.create_index(
        "ix_model_deployments_tenant_last_seen_at",
        "model_deployments",
        ["tenant_id", "last_seen_at"],
        unique=False,
    )
    op.create_index(
        "ix_model_deployments_tenant_model_version_env",
        "model_deployments",
        ["tenant_id", "model_version_id", "environment"],
        unique=False,
    )
    op.create_index(
        "ix_model_deployments_tenant_status",
        "model_deployments",
        ["tenant_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_model_deployments_tenant_target_type",
        "model_deployments",
        ["tenant_id", "target_type"],
        unique=False,
    )

    op.create_table(
        "deployment_endpoints",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("deployment_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("endpoint_type", sa.String(length=32), nullable=False),
        sa.Column("protocol", sa.String(length=16), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("route", sa.String(length=512), nullable=True),
        sa.Column("auth_type", sa.String(length=32), nullable=False),
        sa.Column("auth_reference", sa.String(length=255), nullable=True),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("health_status", sa.String(length=32), server_default="unknown", nullable=False),
        sa.Column("last_health_check_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("record_version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "endpoint_type IN ('inference', 'health')",
            name="ck_deployment_endpoints_endpoint_type",
        ),
        sa.CheckConstraint(
            "health_status IN ('unknown', 'healthy', 'unhealthy')",
            name="ck_deployment_endpoints_health_status",
        ),
        sa.CheckConstraint(
            "protocol IN ('http', 'https', 'grpc', 'grpcs')",
            name="ck_deployment_endpoints_protocol",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'inactive')",
            name="ck_deployment_endpoints_status",
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id"],
            ["model_deployments.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_deployment_endpoints_deployment_id", "deployment_endpoints", ["deployment_id"])
    op.create_index("ix_deployment_endpoints_endpoint_type", "deployment_endpoints", ["endpoint_type"])
    op.create_index("ix_deployment_endpoints_tenant_status", "deployment_endpoints", ["tenant_id", "status"])
    op.create_index(
        "uq_deployment_endpoints_primary_inference",
        "deployment_endpoints",
        ["deployment_id"],
        unique=True,
        postgresql_where=sa.text(
            "is_primary AND endpoint_type = 'inference' AND archived_at IS NULL"
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_deployment_endpoints_primary_inference", table_name="deployment_endpoints")
    op.drop_index("ix_deployment_endpoints_tenant_status", table_name="deployment_endpoints")
    op.drop_index("ix_deployment_endpoints_endpoint_type", table_name="deployment_endpoints")
    op.drop_index("ix_deployment_endpoints_deployment_id", table_name="deployment_endpoints")
    op.drop_table("deployment_endpoints")
    op.drop_index("ix_model_deployments_tenant_target_type", table_name="model_deployments")
    op.drop_index("ix_model_deployments_tenant_status", table_name="model_deployments")
    op.drop_index("ix_model_deployments_tenant_model_version_env", table_name="model_deployments")
    op.drop_index("ix_model_deployments_tenant_last_seen_at", table_name="model_deployments")
    op.drop_index("ix_model_deployments_tenant_environment_status", table_name="model_deployments")
    op.drop_index("ix_model_deployments_tenant_created_at", table_name="model_deployments")
    op.execute("DROP INDEX IF EXISTS uq_model_deployments_tenant_version_env_target_ns")
    op.drop_table("model_deployments")
