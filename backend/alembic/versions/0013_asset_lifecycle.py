"""asset lifecycle status vocabulary

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-14 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Asset Lifecycle (SF8) widens the existing status vocabulary only:
    # no new columns, no history tables. Every pre-existing row is
    # ACTIVE, which stays valid, so no data migration is required.
    op.drop_constraint("ck_discovered_assets_status", "discovered_assets", type_="check")
    op.create_check_constraint(
        "ck_discovered_assets_status",
        "discovered_assets",
        "status IN ('ACTIVE', 'STALE', 'DISAPPEARED', 'IGNORED')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    # restore the pre-SF8 vocabulary: collapse lifecycle states back to
    # ACTIVE so no row violates the narrower constraint
    op.execute("UPDATE discovered_assets SET status = 'ACTIVE' WHERE status <> 'ACTIVE'")
    op.drop_constraint("ck_discovered_assets_status", "discovered_assets", type_="check")
    op.create_check_constraint(
        "ck_discovered_assets_status",
        "discovered_assets",
        "status IN ('ACTIVE')",
    )
