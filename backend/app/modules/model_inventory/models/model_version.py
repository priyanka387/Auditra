from datetime import datetime
from typing import ClassVar
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


class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "model_id",
            "canonical_version_key",
            name="uq_model_versions_tenant_model_canonical_key",
        ),
        CheckConstraint(
            "lifecycle_state IN ('DRAFT', 'ACTIVE', 'DEPRECATED', 'RETIRED', 'ARCHIVED')",
            name="ck_model_versions_lifecycle_state",
        ),
        Index(
            "ix_model_versions_tenant_id_model_id_created_at",
            "tenant_id",
            "model_id",
            desc("created_at"),
        ),
        Index(
            "ix_model_versions_tenant_id_model_id_lifecycle_state",
            "tenant_id",
            "model_id",
            "lifecycle_state",
        ),
        Index(
            "ix_model_versions_tenant_id_model_id_updated_at",
            "tenant_id",
            "model_id",
            desc("updated_at"),
        ),
        Index(
            "ix_model_versions_tenant_id_model_id_version_label",
            "tenant_id",
            "model_id",
            "version_label",
        ),
        Index(
            "ix_model_versions_tenant_id_model_id_native_version_id",
            "tenant_id",
            "model_id",
            "native_version_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()
    model_id: Mapped[UUID] = mapped_column(ForeignKey("models.id"))
    identity_type: Mapped[str] = mapped_column(String(64))
    version_label: Mapped[str] = mapped_column(String(255))
    native_version_id: Mapped[str | None] = mapped_column(String(512))
    canonical_version_key: Mapped[str] = mapped_column(String(1024))
    display_name: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    lifecycle_state: Mapped[str] = mapped_column(
        String(32), default="DRAFT", server_default="DRAFT"
    )
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSON().with_variant(JSONB, "postgresql"),
        default=dict,
        server_default=text("'{}'"),
    )
    source_type: Mapped[str] = mapped_column(String(64))
    source_reference: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(255))
    updated_by: Mapped[str | None] = mapped_column(String(255))
    record_version: Mapped[int] = mapped_column(BigInteger, default=1, server_default=text("1"))

    __mapper_args__: ClassVar[dict] = {"version_id_col": record_version}
