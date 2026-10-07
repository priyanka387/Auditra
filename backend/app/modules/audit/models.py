from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Identity,
    Index,
    String,
    UniqueConstraint,
    desc,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("sequence_no", name="uq_audit_events_sequence_no"),
        CheckConstraint("schema_version > 0", name="ck_audit_events_schema_version_positive"),
        Index(
            "ix_audit_events_tenant_resource_type_resource_id_sequence",
            "tenant_id",
            "resource_type",
            "resource_id",
            desc("sequence_no"),
        ),
        Index(
            "ix_audit_events_tenant_event_type_sequence",
            "tenant_id",
            "event_type",
            desc("sequence_no"),
        ),
        Index(
            "ix_audit_events_tenant_occurred_at_sequence",
            "tenant_id",
            desc("occurred_at"),
            desc("sequence_no"),
        ),
        Index(
            "ix_audit_events_tenant_actor_sequence",
            "tenant_id",
            "actor_type",
            "actor_id",
            desc("sequence_no"),
        ),
        Index(
            "ix_audit_events_tenant_source_sequence",
            "tenant_id",
            "source",
            desc("sequence_no"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()
    sequence_no: Mapped[int] = mapped_column(BigInteger, Identity())
    event_type: Mapped[str] = mapped_column(String(120))
    resource_type: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[str] = mapped_column(String(255))
    actor_type: Mapped[str] = mapped_column(String(40))
    actor_id: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(80))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    request_id: Mapped[str | None] = mapped_column(String(255))
    correlation_id: Mapped[str | None] = mapped_column(String(255))
    schema_version: Mapped[int] = mapped_column(default=1, server_default=text("1"))
    changed_fields: Mapped[list | None] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    before_state: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    after_state: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    metadata_: Mapped[dict | None] = mapped_column(
        "metadata", JSON().with_variant(JSONB, "postgresql")
    )
