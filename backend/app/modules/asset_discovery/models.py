from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    desc,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class DiscoverySource(Base):
    """A location/system Auditra is configured to inspect for assets."""

    __tablename__ = "discovery_sources"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('repository')",
            name="ck_discovery_sources_source_type",
        ),
        CheckConstraint(
            "status IN ('active', 'disabled')",
            name="ck_discovery_sources_status",
        ),
        # uniqueness excludes archived rows so an archived source never
        # blocks re-creating the same name
        Index(
            "uq_discovery_sources_tenant_name",
            "tenant_id",
            "name",
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
        Index("ix_discovery_sources_tenant_status", "tenant_id", "status"),
        Index("ix_discovery_sources_tenant_source_type", "tenant_id", "source_type"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()

    name: Mapped[str] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(String(32))
    # non-secret configuration only; secret-like keys are rejected at the
    # schema layer and future connectors reference credentials by URI
    config: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        default=dict,
        server_default=text("'{}'"),
    )
    status: Mapped[str] = mapped_column(
        String(32), default="active", server_default=text("'active'")
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DiscoveryRun(Base):
    """One execution/attempt to inspect a discovery source.

    A run only tracks the execution (state, timing, asset counts and
    errors); performing discovery itself belongs to a later sub-feature.
    """

    __tablename__ = "discovery_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'PARTIAL')",
            name="ck_discovery_runs_status",
        ),
        CheckConstraint(
            "assets_detected >= 0 AND assets_created >= 0 AND assets_updated >= 0 "
            "AND assets_unchanged >= 0 AND error_count >= 0",
            name="ck_discovery_runs_counts_non_negative",
        ),
        # a run can leave detected assets unprocessed (partial failure)
        # but can never report more outcomes than it detected
        CheckConstraint(
            "assets_created + assets_updated + assets_unchanged <= assets_detected",
            name="ck_discovery_runs_asset_counts",
        ),
        Index(
            "ix_discovery_runs_tenant_source_started_at",
            "tenant_id",
            "source_id",
            desc("started_at"),
        ),
        Index("ix_discovery_runs_tenant_status", "tenant_id", "status"),
        Index("ix_discovery_runs_tenant_started_at", "tenant_id", desc("started_at")),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()
    source_id: Mapped[UUID] = mapped_column(ForeignKey("discovery_sources.id", ondelete="RESTRICT"))

    status: Mapped[str] = mapped_column(
        String(32), default="PENDING", server_default=text("'PENDING'")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    assets_detected: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    assets_created: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    assets_updated: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    assets_unchanged: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    error_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    error_summary: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
