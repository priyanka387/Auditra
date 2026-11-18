"""Risk scoring domain models (RF-03).

A ``RiskScoringFramework`` owns its configuration: a likelihood scale,
an impact scale, and severity thresholds that map score ranges to the
four fixed ratings with a configurable priority. Scoring itself is
append-only history — a ``RiskScore`` row snapshots the framework
version, the input labels, and both the calculated and effective
scores, and is never updated after insert (RF-03 §37). At most one row
per risk carries ``is_current`` (partial unique index), which is how
the register reads a risk's current rating without denormalizing
anything onto ``risks`` (RF-03 §16).
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class RiskScoringFramework(Base):
    """One version of a tenant's risk-scoring methodology (RF-03 §8).

    ``version`` is immutable after creation: a v2 is a new row with the
    same name, and every score snapshots the version it was computed
    under, so re-scoring under newer configuration never rewrites
    history (RF-03 §9). System frameworks arrive through the
    idempotent seed with ``is_system=True``.
    """

    __tablename__ = "risk_scoring_frameworks"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "name", "version", name="uq_risk_scoring_frameworks_tenant_name_version"
        ),
        CheckConstraint(
            "method IN ('likelihood_impact')",
            name="ck_risk_scoring_frameworks_method",
        ),
        CheckConstraint("version >= 1", name="ck_risk_scoring_frameworks_version"),
        Index("ix_risk_scoring_frameworks_tenant_active", "tenant_id", "is_active"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()

    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    method: Mapped[str] = mapped_column(
        String(32), default="likelihood_impact", server_default=text("'likelihood_impact'")
    )
    is_system: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RiskLikelihoodLevel(Base):
    """One point on a framework's likelihood scale (RF-03 §10)."""

    __tablename__ = "risk_likelihood_levels"
    __table_args__ = (
        UniqueConstraint("framework_id", "value", name="uq_risk_likelihood_levels_framework_value"),
        CheckConstraint("value >= 1", name="ck_risk_likelihood_levels_value"),
        Index("ix_risk_likelihood_levels_framework", "framework_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    framework_id: Mapped[UUID] = mapped_column(
        ForeignKey("risk_scoring_frameworks.id", ondelete="RESTRICT")
    )

    value: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    display_order: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RiskImpactLevel(Base):
    """One point on a framework's impact scale (RF-03 §11)."""

    __tablename__ = "risk_impact_levels"
    __table_args__ = (
        UniqueConstraint("framework_id", "value", name="uq_risk_impact_levels_framework_value"),
        CheckConstraint("value >= 1", name="ck_risk_impact_levels_value"),
        Index("ix_risk_impact_levels_framework", "framework_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    framework_id: Mapped[UUID] = mapped_column(
        ForeignKey("risk_scoring_frameworks.id", ondelete="RESTRICT")
    )

    value: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    display_order: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RiskSeverityThreshold(Base):
    """A score band classified into a severity rating with a priority
    (RF-03 §13/§23).

    Ranges, ordering, and the priority each rating carries are
    configuration — the engine reads them from these rows and never
    hard-codes ``17–25 → critical`` anywhere else.
    """

    __tablename__ = "risk_severity_thresholds"
    __table_args__ = (
        UniqueConstraint(
            "framework_id", "severity", name="uq_risk_severity_thresholds_framework_severity"
        ),
        CheckConstraint(
            "minimum_score >= 1 AND minimum_score <= maximum_score",
            name="ck_risk_severity_thresholds_range",
        ),
        CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_risk_severity_thresholds_severity",
        ),
        CheckConstraint("priority >= 1", name="ck_risk_severity_thresholds_priority"),
        Index("ix_risk_severity_thresholds_framework", "framework_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    framework_id: Mapped[UUID] = mapped_column(
        ForeignKey("risk_scoring_frameworks.id", ondelete="RESTRICT")
    )

    minimum_score: Mapped[int] = mapped_column(Integer)
    maximum_score: Mapped[int] = mapped_column(Integer)
    severity: Mapped[str] = mapped_column(String(32))
    priority: Mapped[int] = mapped_column(Integer)
    display_order: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RiskScore(Base):
    """One immutable scoring event for a risk (RF-03 §14/§37).

    The row preserves enough to reproduce the calculation: framework
    id + version, the input values with their labels, the calculated
    score, the severity/priority classification, and — when a rating
    was overridden — the override value, the reason, and the effective
    score that the register displays. ``calculated_score`` is never
    replaced by ``override_score`` (RF-03 §21).
    """

    __tablename__ = "risk_scores"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_risk_scores_severity",
        ),
        CheckConstraint(
            "source IN ('manual', 'assessment')",
            name="ck_risk_scores_source",
        ),
        CheckConstraint(
            "(is_override = false AND override_score IS NULL) "
            "OR (is_override = true AND override_score IS NOT NULL)",
            name="ck_risk_scores_override",
        ),
        CheckConstraint("framework_version >= 1", name="ck_risk_scores_framework_version"),
        # one current score per risk, enforced by the database
        Index(
            "uq_risk_scores_risk_current",
            "risk_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
        Index("ix_risk_scores_tenant_risk", "tenant_id", "risk_id"),
        Index("ix_risk_scores_risk_created", "risk_id", "created_at"),
        Index("ix_risk_scores_framework", "framework_id"),
        Index("ix_risk_scores_tenant_severity", "tenant_id", "severity"),
        Index("ix_risk_scores_tenant_priority", "tenant_id", "priority"),
        Index("ix_risk_scores_tenant_created", "tenant_id", "created_at"),
        Index("ix_risk_scores_assessment", "assessment_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()
    risk_id: Mapped[UUID] = mapped_column(ForeignKey("risks.id", ondelete="RESTRICT"))
    framework_id: Mapped[UUID] = mapped_column(
        ForeignKey("risk_scoring_frameworks.id", ondelete="RESTRICT")
    )
    framework_version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))

    likelihood_value: Mapped[int] = mapped_column(Integer)
    likelihood_label: Mapped[str] = mapped_column(String(100))
    impact_value: Mapped[int] = mapped_column(Integer)
    impact_label: Mapped[str] = mapped_column(String(100))

    calculated_score: Mapped[int] = mapped_column(Integer)
    severity: Mapped[str] = mapped_column(String(32))
    priority: Mapped[int] = mapped_column(Integer)

    override_score: Mapped[int | None] = mapped_column(Integer)
    effective_score: Mapped[int] = mapped_column(Integer)
    override_reason: Mapped[str | None] = mapped_column(Text)

    is_inherent: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    is_current: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    is_override: Mapped[bool] = mapped_column(default=False, server_default=text("false"))

    assessment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("risk_assessments.id", ondelete="RESTRICT")
    )
    source: Mapped[str] = mapped_column(
        String(32), default="manual", server_default=text("'manual'")
    )
    scored_by: Mapped[str | None] = mapped_column(String(255))

    # immutable history: no updated_at by design (RF-03 §37)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
