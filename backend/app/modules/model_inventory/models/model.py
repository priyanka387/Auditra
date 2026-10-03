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


class Model(Base):
    __tablename__ = "models"
    __table_args__ = (
        UniqueConstraint("tenant_id", "canonical_key", name="uq_models_tenant_canonical_key"),
        CheckConstraint(
            "lifecycle_state IN ('REGISTERED', 'ACTIVE', 'DEPRECATED', 'RETIRED', 'ARCHIVED')",
            name="ck_models_lifecycle_state",
        ),
        Index("ix_models_tenant_id_lifecycle_state", "tenant_id", "lifecycle_state"),
        Index("ix_models_tenant_id_provider_id", "tenant_id", "provider_id"),
        Index("ix_models_tenant_id_model_type_id", "tenant_id", "model_type_id"),
        Index("ix_models_tenant_id_created_at", "tenant_id", desc("created_at")),
        Index("ix_models_tenant_id_updated_at", "tenant_id", desc("updated_at")),
        Index("ix_models_tenant_id_native_model_id", "tenant_id", "native_model_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(index=True)
    provider_id: Mapped[UUID] = mapped_column(ForeignKey("model_providers.id"))
    model_type_id: Mapped[UUID] = mapped_column(ForeignKey("model_types.id"))
    name: Mapped[str] = mapped_column(String(255))
    native_model_id: Mapped[str] = mapped_column(String(512))
    canonical_key: Mapped[str] = mapped_column(String(1024))
    description: Mapped[str | None] = mapped_column(Text)
    owner_name: Mapped[str | None] = mapped_column(String(255))
    owner_contact: Mapped[str | None] = mapped_column(String(512))
    team_name: Mapped[str | None] = mapped_column(String(255))
    hosting_mode: Mapped[str | None] = mapped_column(String(64))
    runtime_hint: Mapped[str | None] = mapped_column(String(64))
    lifecycle_state: Mapped[str] = mapped_column(
        String(32), default="REGISTERED", server_default="REGISTERED"
    )
    source_type: Mapped[str] = mapped_column(String(64))
    source_reference: Mapped[str | None] = mapped_column(String(512))
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSON().with_variant(JSONB, "postgresql"),
        default=dict,
        server_default=text("'{}'"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(255))
    updated_by: Mapped[str | None] = mapped_column(String(255))
    record_version: Mapped[int] = mapped_column(BigInteger, default=1, server_default=text("1"))


class ModelTag(Base):
    __tablename__ = "model_tags"
    __table_args__ = (
        UniqueConstraint("tenant_id", "key", "value", name="uq_model_tags_tenant_key_value"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()
    key: Mapped[str] = mapped_column(String(128))
    value: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModelTagLink(Base):
    __tablename__ = "model_tag_links"

    model_id: Mapped[UUID] = mapped_column(ForeignKey("models.id"), primary_key=True)
    tag_id: Mapped[UUID] = mapped_column(ForeignKey("model_tags.id"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
