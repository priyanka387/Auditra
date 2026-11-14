"""asset reconciliation state

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-14 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # discovered assets gain reconciliation state only: the row stays a
    # discovery-side observation and references its canonical Model by
    # foreign key instead of copying it. 'UNMATCHED' makes every
    # pre-existing row representable as unreconciled.
    op.add_column(
        "discovered_assets",
        sa.Column(
            "reconciliation_status",
            sa.String(length=32),
            server_default=sa.text("'UNMATCHED'"),
            nullable=False,
        ),
    )
    op.add_column(
        "discovered_assets",
        sa.Column("reconciliation_reason", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "discovered_assets",
        sa.Column("reconciled_model_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_discovered_assets_reconciled_model",
        "discovered_assets",
        "models",
        ["reconciled_model_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_discovered_assets_reconciliation_status",
        "discovered_assets",
        "reconciliation_status IN ('UNMATCHED', 'MATCHED', 'AMBIGUOUS', 'IGNORED')",
    )
    op.create_check_constraint(
        "ck_discovered_assets_reconciliation_reason",
        "discovered_assets",
        "reconciliation_reason IS NULL OR reconciliation_reason IN ("
        "'provider_and_model_identifier', 'model_identifier', "
        "'normalized_model_identity', 'multiple_model_identifier_matches', "
        "'multiple_normalized_identity_matches', 'no_canonical_model_match')",
    )
    op.create_check_constraint(
        "ck_discovered_assets_reconciled_model",
        "discovered_assets",
        "(reconciliation_status = 'MATCHED') = (reconciled_model_id IS NOT NULL)",
    )
    op.create_index(
        "ix_discovered_assets_tenant_reconciliation_status",
        "discovered_assets",
        ["tenant_id", "reconciliation_status"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_discovered_assets_tenant_reconciliation_status", table_name="discovered_assets"
    )
    op.drop_constraint("ck_discovered_assets_reconciled_model", "discovered_assets", type_="check")
    op.drop_constraint(
        "ck_discovered_assets_reconciliation_reason", "discovered_assets", type_="check"
    )
    op.drop_constraint(
        "ck_discovered_assets_reconciliation_status", "discovered_assets", type_="check"
    )
    op.drop_constraint(
        "fk_discovered_assets_reconciled_model", "discovered_assets", type_="foreignkey"
    )
    op.drop_column("discovered_assets", "reconciled_model_id")
    op.drop_column("discovered_assets", "reconciliation_reason")
    op.drop_column("discovered_assets", "reconciliation_status")
