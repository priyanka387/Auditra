from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ModelDiscovery(Base):
    __tablename__ = "model_discovery"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('manual', 'langchain', 'langgraph')",
            name="ck_model_discovery_source_type",
        ),
        CheckConstraint(
            "status IN ('DISCOVERED', 'MATCHED', 'UNRESOLVED', 'REGISTERED', 'IGNORED', 'FAILED')",
            name="ck_model_discovery_status",
        ),
        CheckConstraint("observation_count >= 1", name="ck_model_discovery_observation_count"),
        UniqueConstraint(
            "tenant_id",
            "source_type",
            "source_identifier",
            "canonical_identity",
            name="uq_model_discovery_tenant_source_identity",
            postgresql_nulls_not_distinct=True,
        ),
        Index(
            "ix_model_discovery_tenant_canonical_identity",
            "tenant_id",
            "canonical_identity",
        ),
        Index("ix_model_discovery_tenant_status", "tenant_id", "status"),
        Index("ix_model_discovery_tenant_source_type", "tenant_id", "source_type"),
        Index("ix_model_discovery_tenant_provider", "tenant_id", "provider"),
        Index("ix_model_discovery_matched_model_id", "matched_model_id"),
        Index("ix_model_discovery_tenant_last_seen_at", "tenant_id", desc("last_seen_at")),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()

    source_type: Mapped[str] = mapped_column(String(32))
    source_identifier: Mapped[str | None] = mapped_column(String(255))
    external_identifier: Mapped[str | None] = mapped_column(String(255))

    provider: Mapped[str] = mapped_column(String(100))
    model_identifier: Mapped[str] = mapped_column(String(512))
    model_type: Mapped[str | None] = mapped_column(String(64))
    display_name: Mapped[str | None] = mapped_column(String(255))
    canonical_identity: Mapped[str] = mapped_column(String(1024))

    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSON().with_variant(JSONB, "postgresql"),
        default=dict,
        server_default=text("'{}'"),
    )

    status: Mapped[str] = mapped_column(String(32))
    matched_model_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("models.id", ondelete="RESTRICT")
    )

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    observation_count: Mapped[int] = mapped_column(BigInteger, default=1, server_default=text("1"))

    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
