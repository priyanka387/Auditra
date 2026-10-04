from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.modules.model_inventory.models.model import Model
from app.modules.model_inventory.models.model_version import ModelVersion


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_applications_tenant_slug"),
        CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE', 'ARCHIVED')",
            name="ck_applications_status",
        ),
        Index("ix_applications_tenant_id_status", "tenant_id", "status"),
        Index("ix_applications_tenant_id_created_at", "tenant_id", desc("created_at")),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    application_type: Mapped[str | None] = mapped_column(String(64))
    owner_name: Mapped[str | None] = mapped_column(String(255))
    team_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", server_default="ACTIVE")
    source: Mapped[str] = mapped_column(String(32))
    tags: Mapped[list | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
    )
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


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "application_id", "slug", name="uq_agents_tenant_application_slug"
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE', 'ARCHIVED')",
            name="ck_agents_status",
        ),
        Index("ix_agents_application_id", "application_id"),
        Index("ix_agents_tenant_id_status", "tenant_id", "status"),
        Index("ix_agents_framework", "framework"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()
    application_id: Mapped[UUID] = mapped_column(ForeignKey("applications.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    agent_type: Mapped[str | None] = mapped_column(String(64))
    framework: Mapped[str | None] = mapped_column(String(64))
    framework_version: Mapped[str | None] = mapped_column(String(64))
    runtime_identifier: Mapped[str | None] = mapped_column(String(255))
    owner_name: Mapped[str | None] = mapped_column(String(255))
    team_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", server_default="ACTIVE")
    source: Mapped[str] = mapped_column(String(32))
    capabilities: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
    )
    tags: Mapped[list | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
    )
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

    application: Mapped[Application] = relationship()


class AgentModelAssociation(Base):
    __tablename__ = "agent_model_associations"
    __table_args__ = (
        CheckConstraint(
            "role IN ('PRIMARY', 'FALLBACK', 'EMBEDDING', 'RERANKER', 'VISION', "
            "'MULTIMODAL', 'MODERATION', 'OTHER')",
            name="ck_agent_model_associations_role",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'DISABLED')",
            name="ck_agent_model_associations_status",
        ),
        CheckConstraint("selection_priority >= 1", name="ck_agent_model_associations_priority"),
        Index("ix_agent_model_associations_agent_id_status", "agent_id", "status"),
        Index("ix_agent_model_associations_model_id_status", "model_id", "status"),
        Index("ix_agent_model_associations_model_version_id", "model_version_id"),
        Index(
            "ix_agent_model_associations_tenant_id_created_at",
            "tenant_id",
            desc("created_at"),
        ),
        # ponytail: identity excludes nullable model_version_id (NULL semantics)
        # and only ACTIVE rows, so disabled rows never block re-association
        Index(
            "uq_agent_model_associations_agent_role_priority",
            "agent_id",
            "role",
            "selection_priority",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        Index(
            "uq_agent_model_associations_agent_model_role",
            "agent_id",
            "model_id",
            "role",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()
    agent_id: Mapped[UUID] = mapped_column(ForeignKey("agents.id", ondelete="RESTRICT"))
    model_id: Mapped[UUID] = mapped_column(ForeignKey("models.id", ondelete="RESTRICT"))
    model_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("model_versions.id", ondelete="RESTRICT")
    )
    role: Mapped[str] = mapped_column(String(32))
    selection_priority: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", server_default="ACTIVE")
    source: Mapped[str] = mapped_column(String(32))
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
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    created_by: Mapped[str | None] = mapped_column(String(255))
    updated_by: Mapped[str | None] = mapped_column(String(255))

    agent: Mapped[Agent] = relationship()
    model: Mapped[Model] = relationship()
    model_version: Mapped[ModelVersion | None] = relationship()
