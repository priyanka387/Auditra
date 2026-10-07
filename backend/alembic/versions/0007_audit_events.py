"""audit events

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-07 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_no", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("resource_type", sa.String(length=80), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=False),
        sa.Column("actor_type", sa.String(length=40), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=True),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("request_id", sa.String(length=255), nullable=True),
        sa.Column("correlation_id", sa.String(length=255), nullable=True),
        sa.Column("schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "changed_fields",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "before_state",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "after_state",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "metadata",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.CheckConstraint("schema_version > 0", name="ck_audit_events_schema_version_positive"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sequence_no", name="uq_audit_events_sequence_no"),
    )
    op.create_index(
        "ix_audit_events_tenant_resource_type_resource_id_sequence",
        "audit_events",
        ["tenant_id", "resource_type", "resource_id", sa.literal_column("sequence_no DESC")],
        unique=False,
    )
    op.create_index(
        "ix_audit_events_tenant_event_type_sequence",
        "audit_events",
        ["tenant_id", "event_type", sa.literal_column("sequence_no DESC")],
        unique=False,
    )
    op.create_index(
        "ix_audit_events_tenant_occurred_at_sequence",
        "audit_events",
        ["tenant_id", sa.literal_column("occurred_at DESC"), sa.literal_column("sequence_no DESC")],
        unique=False,
    )
    op.create_index(
        "ix_audit_events_tenant_actor_sequence",
        "audit_events",
        ["tenant_id", "actor_type", "actor_id", sa.literal_column("sequence_no DESC")],
        unique=False,
    )
    op.create_index(
        "ix_audit_events_tenant_source_sequence",
        "audit_events",
        ["tenant_id", "source", sa.literal_column("sequence_no DESC")],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    for index in (
        "ix_audit_events_tenant_source_sequence",
        "ix_audit_events_tenant_actor_sequence",
        "ix_audit_events_tenant_occurred_at_sequence",
        "ix_audit_events_tenant_event_type_sequence",
        "ix_audit_events_tenant_resource_type_resource_id_sequence",
    ):
        op.drop_index(index, table_name="audit_events")
    op.drop_table("audit_events")
