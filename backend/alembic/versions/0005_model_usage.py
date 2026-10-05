"""model usage events

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-05 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "model_usage_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("model_id", sa.Uuid(), nullable=False),
        sa.Column("model_version_id", sa.Uuid(), nullable=True),
        sa.Column("deployment_id", sa.Uuid(), nullable=True),
        sa.Column("application_id", sa.Uuid(), nullable=True),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("total_tokens", sa.BigInteger(), nullable=True),
        sa.Column("cached_input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("reasoning_tokens", sa.BigInteger(), nullable=True),
        sa.Column("token_usage_source", sa.String(length=32), nullable=False),
        sa.Column("estimated_cost", sa.Numeric(20, 8), nullable=True),
        sa.Column("cost_currency", sa.String(length=8), nullable=True),
        sa.Column("cost_source", sa.String(length=32), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=True),
        sa.Column("parent_run_id", sa.String(length=128), nullable=True),
        sa.Column("provider_request_id", sa.String(length=255), nullable=True),
        sa.Column("environment", sa.String(length=64), nullable=True),
        sa.Column("region", sa.String(length=128), nullable=True),
        sa.Column("host", sa.String(length=255), nullable=True),
        sa.Column("runtime", sa.String(length=128), nullable=True),
        sa.Column("framework", sa.String(length=64), nullable=True),
        sa.Column("framework_version", sa.String(length=64), nullable=True),
        sa.Column("sdk_version", sa.String(length=64), nullable=True),
        sa.Column("operation_name", sa.String(length=255), nullable=True),
        sa.Column("error_type", sa.String(length=255), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=True),
        sa.Column("provider_status_code", sa.Integer(), nullable=True),
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
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "cost_source IN ('provider_reported', 'local_estimate', 'unavailable')",
            name="ck_model_usage_events_cost_source",
        ),
        sa.CheckConstraint(
            "duration_ms >= 0 AND retry_count >= 0 AND estimated_cost >= 0",
            name="ck_model_usage_events_metrics_non_negative",
        ),
        sa.CheckConstraint(
            "source IN ('api', 'sdk', 'langchain', 'langgraph', 'internal', 'connector', "
            "'manual')",
            name="ck_model_usage_events_source",
        ),
        sa.CheckConstraint(
            "status IN ('success', 'error', 'cancelled', 'unknown')",
            name="ck_model_usage_events_status",
        ),
        sa.CheckConstraint(
            "token_usage_source IN ('provider_reported', 'calculated', 'unavailable')",
            name="ck_model_usage_events_token_usage_source",
        ),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND total_tokens >= 0 "
            "AND cached_input_tokens >= 0 AND reasoning_tokens >= 0",
            name="ck_model_usage_events_tokens_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"], ["applications.id"], ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id"], ["model_deployments.id"], ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["model_id"], ["models.id"], ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["model_version_id"], ["model_versions.id"], ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_model_usage_events_event_id",
        "model_usage_events",
        ["event_id"],
        unique=True,
    )
    op.create_index(
        "ix_model_usage_events_tenant_started_at",
        "model_usage_events",
        ["tenant_id", sa.literal_column("started_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_model_usage_events_tenant_model_started_at",
        "model_usage_events",
        ["tenant_id", "model_id", sa.literal_column("started_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_model_usage_events_tenant_model_version_started_at",
        "model_usage_events",
        ["tenant_id", "model_version_id", sa.literal_column("started_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_model_usage_events_tenant_deployment_started_at",
        "model_usage_events",
        ["tenant_id", "deployment_id", sa.literal_column("started_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_model_usage_events_tenant_application_started_at",
        "model_usage_events",
        ["tenant_id", "application_id", sa.literal_column("started_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_model_usage_events_tenant_agent_started_at",
        "model_usage_events",
        ["tenant_id", "agent_id", sa.literal_column("started_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_model_usage_events_tenant_status_started_at",
        "model_usage_events",
        ["tenant_id", "status", sa.literal_column("started_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_model_usage_events_tenant_source_started_at",
        "model_usage_events",
        ["tenant_id", "source", sa.literal_column("started_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    for index in (
        "ix_model_usage_events_tenant_source_started_at",
        "ix_model_usage_events_tenant_status_started_at",
        "ix_model_usage_events_tenant_agent_started_at",
        "ix_model_usage_events_tenant_application_started_at",
        "ix_model_usage_events_tenant_deployment_started_at",
        "ix_model_usage_events_tenant_model_version_started_at",
        "ix_model_usage_events_tenant_model_started_at",
        "ix_model_usage_events_tenant_started_at",
        "ix_model_usage_events_event_id",
    ):
        op.drop_index(index, table_name="model_usage_events")
    op.drop_table("model_usage_events")
