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
    UniqueConstraint,
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


class DiscoveredAsset(Base):
    """An AI-related asset observed by the discovery system.

    A discovered asset is an *observation*, not a canonical Auditra
    inventory entity: mapping it to e.g. a Model Inventory record is
    owned by Asset Reconciliation (Sub-feature 7). Identity is scoped
    to the discovery source it was observed from so the same asset seen
    from another source stays a separate entry: deduplication
    (Sub-feature 6) preserves source boundaries, and reconciliation
    (Sub-feature 7) is what later relates such entries.

    ``last_seen_run_id`` records the discovery execution that last
    observed the asset; full per-observation evidence belongs to
    Discovery Evidence (Sub-feature 9).
    """

    __tablename__ = "discovered_assets"
    __table_args__ = (
        CheckConstraint(
            "asset_type IN ('MODEL', 'MODEL_REFERENCE', 'APPLICATION', 'AGENT', "
            "'WORKFLOW', 'DATASET', 'TOOL', 'DEPLOYMENT', 'ENDPOINT', 'AI_SERVICE')",
            name="ck_discovered_assets_asset_type",
        ),
        # lifecycle vocabulary (Asset Lifecycle, Sub-feature 8): ACTIVE is
        # the observed default, STALE/DISAPPEARED age as completed runs of
        # the source miss the asset, IGNORED is a sticky intentional
        # decision that lifecycle evaluation never overwrites
        CheckConstraint(
            "status IN ('ACTIVE', 'STALE', 'DISAPPEARED', 'IGNORED')",
            name="ck_discovered_assets_status",
        ),
        UniqueConstraint(
            "tenant_id",
            "source_id",
            "normalized_identity",
            name="uq_discovered_assets_source_identity",
        ),
        # reconciliation state vocabulary follows master spec §8; the
        # reason vocabulary is the deterministic rule that decided the
        # outcome (see reconciliation.py) — a MATCHED row always carries
        # its canonical model and vice versa
        CheckConstraint(
            "reconciliation_status IN ('UNMATCHED', 'MATCHED', 'AMBIGUOUS', 'IGNORED')",
            name="ck_discovered_assets_reconciliation_status",
        ),
        CheckConstraint(
            "reconciliation_reason IS NULL OR reconciliation_reason IN ("
            "'provider_and_model_identifier', 'model_identifier', "
            "'normalized_model_identity', 'multiple_model_identifier_matches', "
            "'multiple_normalized_identity_matches', 'no_canonical_model_match')",
            name="ck_discovered_assets_reconciliation_reason",
        ),
        CheckConstraint(
            "(reconciliation_status = 'MATCHED') = (reconciled_model_id IS NOT NULL)",
            name="ck_discovered_assets_reconciled_model",
        ),
        Index("ix_discovered_assets_tenant_source", "tenant_id", "source_id"),
        Index("ix_discovered_assets_tenant_asset_type", "tenant_id", "asset_type"),
        Index("ix_discovered_assets_tenant_status", "tenant_id", "status"),
        Index(
            "ix_discovered_assets_tenant_reconciliation_status",
            "tenant_id",
            "reconciliation_status",
        ),
        Index("ix_discovered_assets_tenant_last_seen_at", "tenant_id", desc("last_seen_at")),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()
    source_id: Mapped[UUID] = mapped_column(ForeignKey("discovery_sources.id", ondelete="RESTRICT"))
    last_seen_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("discovery_runs.id", ondelete="RESTRICT")
    )

    asset_type: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(255))
    normalized_identity: Mapped[str] = mapped_column(String(1024))
    status: Mapped[str] = mapped_column(
        String(32), default="ACTIVE", server_default=text("'ACTIVE'")
    )

    # discovery-produced extras only (provider, framework, file path,
    # detection rule, ...); secret-like keys are rejected before persist
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSON().with_variant(JSONB, "postgresql"),
        default=dict,
        server_default=text("'{}'"),
    )

    # Asset Reconciliation (Sub-feature 7): link to the canonical Model
    # Inventory entity when a deterministic identity match exists. The
    # discovery-side row stays an observation — only the canonical model
    # id, the outcome status and the deciding rule's reason are stored;
    # default 'UNMATCHED' keeps every pre-existing row representable as
    # unreconciled. MATCHED <-> reconciled_model_id is enforced by
    # ck_discovered_assets_reconciled_model.
    reconciliation_status: Mapped[str] = mapped_column(
        String(32), default="UNMATCHED", server_default=text("'UNMATCHED'")
    )
    reconciliation_reason: Mapped[str | None] = mapped_column(String(64))
    reconciled_model_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("models.id", ondelete="RESTRICT")
    )

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DiscoveredAssetRun(Base):
    """Link recording that one discovery run observed one discovered asset.

    Rows are historical traceability: they are never mutated in place, so
    an asset seen in Run 1, Run 2 and Run 3 keeps all three links even as
    ``DiscoveredAsset.last_seen_run_id`` advances. Evidence content itself
    belongs to Discovery Evidence (Sub-feature 9).
    """

    __tablename__ = "discovered_asset_runs"
    __table_args__ = (
        Index(
            "uq_discovered_asset_runs_asset_run",
            "discovered_asset_id",
            "discovery_run_id",
            unique=True,
        ),
        Index("ix_discovered_asset_runs_tenant_run", "tenant_id", "discovery_run_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()
    discovered_asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("discovered_assets.id", ondelete="RESTRICT")
    )
    discovery_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("discovery_runs.id", ondelete="RESTRICT")
    )

    first_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
