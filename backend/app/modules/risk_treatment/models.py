"""RF-04 risk treatment, action and expected-residual models.

Separates the six concepts the spec insists are not the same thing
(plan §3): risk, treatment decision, treatment plan (actions), status,
and residual expectation. A risk may hold many treatments over time
(plan §49) but at most one *active* treatment — enforced by a partial
unique index, not by application code (plan §48). Residual assessments
are append-only snapshots (no ``updated_at``); the actual residual
number is never claimed here (plan §23).
"""

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    desc,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class RiskTreatment(Base):
    """One treatment decision for a risk (plan §8/§9).

    The treatment answers "what are we going to do about this risk?":
    strategy (mitigate/accept/avoid/transfer), why (rationale), who
    (owner — free text, Auditra has no identity model yet), and when
    (planned start/target completion, stamped ``completed_at`` or
    ``cancelled_at``+reason on terminal transitions).
    """

    __tablename__ = "risk_treatments"
    __table_args__ = (
        CheckConstraint(
            "strategy IN ('mitigate', 'accept', 'avoid', 'transfer')",
            name="ck_risk_treatments_strategy",
        ),
        CheckConstraint(
            "status IN ('not_started', 'planned', 'in_progress', 'completed', 'cancelled')",
            name="ck_risk_treatments_status",
        ),
        CheckConstraint(
            "planned_start_date IS NULL OR target_completion_date IS NULL "
            "OR planned_start_date <= target_completion_date",
            name="ck_risk_treatments_dates",
        ),
        # a completed treatment is stamped; nothing else carries completed_at
        CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL) "
            "OR (status <> 'completed' AND completed_at IS NULL)",
            name="ck_risk_treatments_completed",
        ),
        # a cancelled treatment always keeps when and why it was cancelled
        CheckConstraint(
            "(status = 'cancelled' AND cancelled_at IS NOT NULL "
            "AND cancellation_reason IS NOT NULL) "
            "OR (status <> 'cancelled' AND cancelled_at IS NULL)",
            name="ck_risk_treatments_cancelled",
        ),
        # plan §48: at most one active treatment per risk; completed and
        # cancelled rows stay as history (plan §49) — declared in both the
        # migration and here so metadata-driven tooling sees the invariant
        Index(
            "uq_risk_treatments_risk_active",
            "risk_id",
            unique=True,
            postgresql_where=text("status IN ('not_started', 'planned', 'in_progress')"),
        ),
        Index("ix_risk_treatments_tenant_risk", "tenant_id", "risk_id"),
        Index("ix_risk_treatments_tenant_status", "tenant_id", "status"),
        Index("ix_risk_treatments_tenant_strategy", "tenant_id", "strategy"),
        Index("ix_risk_treatments_tenant_owner", "tenant_id", "owner"),
        Index("ix_risk_treatments_tenant_target", "tenant_id", "target_completion_date"),
        Index("ix_risk_treatments_risk_created", "risk_id", desc("created_at")),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()

    risk_id: Mapped[UUID] = mapped_column(ForeignKey("risks.id", ondelete="RESTRICT"))
    strategy: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(
        String(32), default="not_started", server_default=text("'not_started'")
    )

    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    owner: Mapped[str | None] = mapped_column(String(255))
    created_by: Mapped[str | None] = mapped_column(String(255))

    planned_start_date: Mapped[date | None] = mapped_column(Date)
    target_completion_date: Mapped[date | None] = mapped_column(Date)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_reason: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RiskTreatmentAction(Base):
    """One concrete work item executing a treatment decision (plan §12/§13).

    The treatment is the decision; actions are the actual work — they
    carry their own owner, priority, dates and status, and drive the
    derived progress shown on the treatment (plan §18).
    """

    __tablename__ = "risk_treatment_actions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('todo', 'in_progress', 'completed', 'cancelled')",
            name="ck_risk_treatment_actions_status",
        ),
        CheckConstraint(
            "priority IN ('low', 'medium', 'high', 'critical')",
            name="ck_risk_treatment_actions_priority",
        ),
        CheckConstraint(
            "planned_start_date IS NULL OR due_date IS NULL OR planned_start_date <= due_date",
            name="ck_risk_treatment_actions_dates",
        ),
        CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL) "
            "OR (status <> 'completed' AND completed_at IS NULL)",
            name="ck_risk_treatment_actions_completed",
        ),
        CheckConstraint(
            "(status = 'cancelled' AND cancelled_at IS NOT NULL "
            "AND cancellation_reason IS NOT NULL) "
            "OR (status <> 'cancelled' AND cancelled_at IS NULL)",
            name="ck_risk_treatment_actions_cancelled",
        ),
        Index("ix_risk_treatment_actions_tenant", "tenant_id"),
        Index("ix_risk_treatment_actions_treatment", "treatment_id"),
        Index("ix_risk_treatment_actions_treatment_status", "treatment_id", "status"),
        Index("ix_risk_treatment_actions_tenant_owner", "tenant_id", "owner"),
        Index("ix_risk_treatment_actions_tenant_due", "tenant_id", "due_date"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()

    treatment_id: Mapped[UUID] = mapped_column(
        ForeignKey("risk_treatments.id", ondelete="RESTRICT")
    )
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="todo", server_default=text("'todo'"))
    owner: Mapped[str] = mapped_column(String(255))
    priority: Mapped[str] = mapped_column(
        String(32), default="medium", server_default=text("'medium'")
    )

    planned_start_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_reason: Mapped[str | None] = mapped_column(Text)

    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RiskTreatmentResidualAssessment(Base):
    """Expected residual risk after treatment (plan §22/§23/§24).

    What the organization *expects* once the treatment runs: likelihood,
    impact, score and severity snapshotted through the RF-03 scoring
    engine at creation time. Never a verified outcome — that belongs to
    the later control/evidence workflow (RF-07). Append-only history:
    no ``updated_at``, no edits, no deletes.
    """

    __tablename__ = "risk_treatment_residual_assessments"
    __table_args__ = (
        CheckConstraint(
            "assessment_type IN ('expected', 'verified')",
            name="ck_risk_treatment_residual_type",
        ),
        CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_risk_treatment_residual_severity",
        ),
        CheckConstraint(
            "likelihood_value >= 1 AND impact_value >= 1 AND calculated_score >= 1",
            name="ck_risk_treatment_residual_values",
        ),
        Index("ix_risk_treatment_residual_tenant", "tenant_id"),
        Index("ix_risk_treatment_residual_treatment", "treatment_id"),
        Index(
            "ix_risk_treatment_residual_treatment_created",
            "treatment_id",
            desc("created_at"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column()

    treatment_id: Mapped[UUID] = mapped_column(
        ForeignKey("risk_treatments.id", ondelete="RESTRICT")
    )
    likelihood_value: Mapped[int] = mapped_column()
    impact_value: Mapped[int] = mapped_column()
    calculated_score: Mapped[int] = mapped_column()
    severity: Mapped[str] = mapped_column(String(32))
    assessment_type: Mapped[str] = mapped_column(
        String(32), default="expected", server_default=text("'expected'")
    )
    created_by: Mapped[str | None] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
