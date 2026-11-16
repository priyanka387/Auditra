from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    desc,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class RiskCategory(Base):
    """Configurable risk-taxonomy entry (RF-01).

    System categories are provisioned per tenant by the idempotent
    seed (``seed.RISK_CATEGORIES``) with ``is_system=True`` and are
    never deletable through the API; organizations add their own
    categories with ``is_system=False``. The unique ``(tenant_id,
    slug)`` pair is what makes re-seeding duplicate-free.
    """

    __tablename__ = "risk_categories"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_risk_categories_tenant_slug"),
        Index("ix_risk_categories_tenant_active", "tenant_id", "is_active"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()

    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    slug: Mapped[str] = mapped_column(String(100))
    is_system: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Risk(Base):
    """One organizational AI risk in the RF-01 register.

    RF-01 answers "what risks do we have, and where do they belong?":
    identity (title, description), placement (category, discovered AI
    asset), accountability (owner/team as free-text — Auditra has no
    identity model yet, so these migrate to user/team FKs later),
    provenance (source) and lifecycle (status plus ``archived_at``).
    Scoring (RF-03) and treatment (RF-04) append their own columns
    later without redesigning this entity.
    """

    __tablename__ = "risks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'in_review', 'accepted')",
            name="ck_risks_status",
        ),
        CheckConstraint(
            "source IN ('manual', 'asset_discovery', 'assessment', 'incident', "
            "'audit', 'policy_review', 'vendor_review', 'other')",
            name="ck_risks_source",
        ),
        Index("ix_risks_tenant_status", "tenant_id", "status"),
        Index("ix_risks_tenant_category", "tenant_id", "category_id"),
        Index("ix_risks_tenant_asset", "tenant_id", "asset_id"),
        Index("ix_risks_tenant_owner", "tenant_id", "owner"),
        Index("ix_risks_tenant_archived", "tenant_id", "archived_at"),
        Index("ix_risks_tenant_updated", "tenant_id", desc("updated_at")),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()

    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    category_id: Mapped[UUID] = mapped_column(ForeignKey("risk_categories.id", ondelete="RESTRICT"))
    source: Mapped[str] = mapped_column(
        String(32), default="manual", server_default=text("'manual'")
    )
    asset_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("discovered_assets.id", ondelete="RESTRICT")
    )
    owner: Mapped[str | None] = mapped_column(String(255))
    team: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="open", server_default=text("'open'"))
    created_by: Mapped[str | None] = mapped_column(String(255))
    updated_by: Mapped[str | None] = mapped_column(String(255))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
