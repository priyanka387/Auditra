from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Index,
    String,
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
