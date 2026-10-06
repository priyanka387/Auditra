from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    desc,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ModelUsageEvent(Base):
    __tablename__ = "model_usage_events"
    __table_args__ = (
        CheckConstraint(
            "status IN ('success', 'error', 'cancelled', 'unknown')",
            name="ck_model_usage_events_status",
        ),
        CheckConstraint(
            "source IN ('api', 'sdk', 'langchain', 'langgraph', 'internal', 'connector', 'manual')",
            name="ck_model_usage_events_source",
        ),
        CheckConstraint(
            "token_usage_source IN ('provider_reported', 'calculated', 'unavailable')",
            name="ck_model_usage_events_token_usage_source",
        ),
        CheckConstraint(
            "cost_source IN ('provider_reported', 'local_estimate', 'unavailable')",
            name="ck_model_usage_events_cost_source",
        ),
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND total_tokens >= 0 "
            "AND cached_input_tokens >= 0 AND reasoning_tokens >= 0",
            name="ck_model_usage_events_tokens_non_negative",
        ),
        CheckConstraint(
            "duration_ms >= 0 AND retry_count >= 0 AND estimated_cost >= 0",
            name="ck_model_usage_events_metrics_non_negative",
        ),
        Index("ix_model_usage_events_event_id", "event_id", unique=True),
        Index("ix_model_usage_events_tenant_started_at", "tenant_id", desc("started_at")),
        Index(
            "ix_model_usage_events_tenant_model_started_at",
            "tenant_id",
            "model_id",
            desc("started_at"),
        ),
        Index(
            "ix_model_usage_events_tenant_model_version_started_at",
            "tenant_id",
            "model_version_id",
            desc("started_at"),
        ),
        Index(
            "ix_model_usage_events_tenant_deployment_started_at",
            "tenant_id",
            "deployment_id",
            desc("started_at"),
        ),
        Index(
            "ix_model_usage_events_tenant_application_started_at",
            "tenant_id",
            "application_id",
            desc("started_at"),
        ),
        Index(
            "ix_model_usage_events_tenant_agent_started_at",
            "tenant_id",
            "agent_id",
            desc("started_at"),
        ),
        Index(
            "ix_model_usage_events_tenant_status_started_at",
            "tenant_id",
            "status",
            desc("started_at"),
        ),
        Index(
            "ix_model_usage_events_tenant_source_started_at",
            "tenant_id",
            "source",
            desc("started_at"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()
    event_id: Mapped[str] = mapped_column(String(128))
    payload_hash: Mapped[str] = mapped_column(String(64))

    model_id: Mapped[UUID] = mapped_column(ForeignKey("models.id", ondelete="RESTRICT"))
    model_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("model_versions.id", ondelete="RESTRICT")
    )
    deployment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("model_deployments.id", ondelete="RESTRICT")
    )
    application_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("applications.id", ondelete="RESTRICT")
    )
    agent_id: Mapped[UUID | None] = mapped_column(ForeignKey("agents.id", ondelete="RESTRICT"))

    status: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(32))

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)

    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    total_tokens: Mapped[int | None] = mapped_column(BigInteger)
    cached_input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    reasoning_tokens: Mapped[int | None] = mapped_column(BigInteger)
    token_usage_source: Mapped[str] = mapped_column(String(32))

    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    cost_currency: Mapped[str | None] = mapped_column(String(8))
    cost_source: Mapped[str] = mapped_column(String(32))

    request_id: Mapped[str | None] = mapped_column(String(128))
    trace_id: Mapped[str | None] = mapped_column(String(128))
    parent_run_id: Mapped[str | None] = mapped_column(String(128))
    provider_request_id: Mapped[str | None] = mapped_column(String(255))

    environment: Mapped[str | None] = mapped_column(String(64))
    region: Mapped[str | None] = mapped_column(String(128))
    host: Mapped[str | None] = mapped_column(String(255))
    runtime: Mapped[str | None] = mapped_column(String(128))
    framework: Mapped[str | None] = mapped_column(String(64))
    framework_version: Mapped[str | None] = mapped_column(String(64))
    sdk_version: Mapped[str | None] = mapped_column(String(64))
    operation_name: Mapped[str | None] = mapped_column(String(255))

    error_type: Mapped[str | None] = mapped_column(String(255))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int | None] = mapped_column(Integer)
    provider_status_code: Mapped[int | None] = mapped_column(Integer)

    tags: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    metadata_: Mapped[dict | None] = mapped_column(
        "metadata",
        JSON().with_variant(JSONB, "postgresql"),
        default=dict,
        server_default=text("'{}'"),
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
