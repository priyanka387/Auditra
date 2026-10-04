from datetime import datetime
from typing import ClassVar
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
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


class ModelDeployment(Base):
    __tablename__ = "model_deployments"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "model_version_id",
            "environment",
            "target_name",
            "namespace",
            name="uq_model_deployments_tenant_version_env_target_ns",
        ),
        CheckConstraint(
            "environment IN ('development', 'staging', 'production')",
            name="ck_model_deployments_environment",
        ),
        CheckConstraint(
            "deployment_kind IN ('online_inference', 'batch', 'embedded', 'edge', "
            "'scheduled', 'other')",
            name="ck_model_deployments_deployment_kind",
        ),
        CheckConstraint(
            "status IN ('planned', 'deploying', 'active', 'degraded', 'failed', "
            "'stopping', 'stopped', 'deprecated')",
            name="ck_model_deployments_status",
        ),
        CheckConstraint(
            "desired_replicas >= 0 AND observed_replicas >= 0",
            name="ck_model_deployments_replicas",
        ),
        Index(
            "ix_model_deployments_tenant_model_version_env",
            "tenant_id",
            "model_version_id",
            "environment",
        ),
        Index("ix_model_deployments_tenant_status", "tenant_id", "status"),
        Index(
            "ix_model_deployments_tenant_environment_status",
            "tenant_id",
            "environment",
            "status",
        ),
        Index("ix_model_deployments_tenant_target_type", "tenant_id", "target_type"),
        Index("ix_model_deployments_tenant_last_seen_at", "tenant_id", "last_seen_at"),
        Index("ix_model_deployments_tenant_created_at", "tenant_id", desc("created_at")),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()
    model_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("model_versions.id", ondelete="RESTRICT")
    )
    name: Mapped[str] = mapped_column(String(128))
    environment: Mapped[str] = mapped_column(String(32))
    deployment_kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="planned", server_default="planned")
    status_source: Mapped[str] = mapped_column(String(32))
    target_type: Mapped[str] = mapped_column(String(64))
    target_name: Mapped[str | None] = mapped_column(String(255))
    region: Mapped[str | None] = mapped_column(String(128))
    cluster_name: Mapped[str | None] = mapped_column(String(255))
    namespace: Mapped[str | None] = mapped_column(String(255))
    runtime: Mapped[str | None] = mapped_column(String(128))
    serving_framework: Mapped[str | None] = mapped_column(String(128))
    image_uri: Mapped[str | None] = mapped_column(Text)
    desired_replicas: Mapped[int | None] = mapped_column()
    observed_replicas: Mapped[int | None] = mapped_column()
    configuration: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        default=dict,
        server_default=text("'{}'"),
    )
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSON().with_variant(JSONB, "postgresql"),
        default=dict,
        server_default=text("'{}'"),
    )
    source: Mapped[str] = mapped_column(String(32))
    source_reference: Mapped[str | None] = mapped_column(String(255))
    deployed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    created_by: Mapped[str | None] = mapped_column(String(255))
    updated_by: Mapped[str | None] = mapped_column(String(255))
    record_version: Mapped[int] = mapped_column(BigInteger, default=1, server_default=text("1"))

    __mapper_args__: ClassVar[dict] = {"version_id_col": record_version}


class DeploymentEndpoint(Base):
    __tablename__ = "deployment_endpoints"
    __table_args__ = (
        CheckConstraint(
            "endpoint_type IN ('inference', 'health')",
            name="ck_deployment_endpoints_endpoint_type",
        ),
        CheckConstraint(
            "protocol IN ('http', 'https', 'grpc', 'grpcs')",
            name="ck_deployment_endpoints_protocol",
        ),
        CheckConstraint(
            "status IN ('active', 'inactive')",
            name="ck_deployment_endpoints_status",
        ),
        CheckConstraint(
            "health_status IN ('unknown', 'healthy', 'unhealthy')",
            name="ck_deployment_endpoints_health_status",
        ),
        Index("ix_deployment_endpoints_deployment_id", "deployment_id"),
        Index("ix_deployment_endpoints_endpoint_type", "endpoint_type"),
        Index("ix_deployment_endpoints_tenant_status", "tenant_id", "status"),
        Index(
            "uq_deployment_endpoints_primary_inference",
            "deployment_id",
            unique=True,
            postgresql_where=text(
                "is_primary AND endpoint_type = 'inference' AND archived_at IS NULL"
            ),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()
    deployment_id: Mapped[UUID] = mapped_column(
        ForeignKey("model_deployments.id", ondelete="RESTRICT")
    )
    name: Mapped[str] = mapped_column(String(128))
    endpoint_type: Mapped[str] = mapped_column(String(32))
    protocol: Mapped[str] = mapped_column(String(16))
    url: Mapped[str] = mapped_column(Text)
    route: Mapped[str | None] = mapped_column(String(512))
    auth_type: Mapped[str] = mapped_column(String(32))
    auth_reference: Mapped[str | None] = mapped_column(String(255))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    status: Mapped[str] = mapped_column(String(32), default="active", server_default="active")
    health_status: Mapped[str] = mapped_column(
        String(32), default="unknown", server_default="unknown"
    )
    last_health_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSON().with_variant(JSONB, "postgresql"),
        default=dict,
        server_default=text("'{}'"),
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    created_by: Mapped[str | None] = mapped_column(String(255))
    updated_by: Mapped[str | None] = mapped_column(String(255))
    record_version: Mapped[int] = mapped_column(BigInteger, default=1, server_default=text("1"))

    __mapper_args__: ClassVar[dict] = {"version_id_col": record_version}
