"""Unit tests: RF-04 risk treatment domain models and enums."""

import sqlalchemy as sa

from app.modules.risk_treatment.enums import (
    ACTION_TRANSITIONS,
    ACTIVE_TREATMENT_STATUSES,
    TREATMENT_TRANSITIONS,
    ResidualAssessmentType,
    RiskTreatmentStatus,
    RiskTreatmentStrategy,
    TreatmentActionStatus,
    TreatmentPriority,
)
from app.modules.risk_treatment.models import (
    RiskTreatment,
    RiskTreatmentAction,
    RiskTreatmentResidualAssessment,
)


def test_treatment_enum_values():
    assert {strategy.value for strategy in RiskTreatmentStrategy} == {
        "mitigate",
        "accept",
        "avoid",
        "transfer",
    }
    assert {status.value for status in RiskTreatmentStatus} == {
        "not_started",
        "planned",
        "in_progress",
        "completed",
        "cancelled",
    }
    assert {status.value for status in TreatmentActionStatus} == {
        "todo",
        "in_progress",
        "completed",
        "cancelled",
    }
    assert {priority.value for priority in TreatmentPriority} == {
        "low",
        "medium",
        "high",
        "critical",
    }
    assert {kind.value for kind in ResidualAssessmentType} == {"expected", "verified"}


def test_treatment_transition_maps():
    """RF-04 §55 state machine: forward-only, terminals immutable.

    not_started → cancelled is the plan's one gap fill (Ruling 6): a
    never-started treatment must be cancellable.
    """
    assert TREATMENT_TRANSITIONS == {
        "not_started": frozenset({"planned", "cancelled"}),
        "planned": frozenset({"in_progress", "cancelled"}),
        "in_progress": frozenset({"completed", "cancelled"}),
        "completed": frozenset(),
        "cancelled": frozenset(),
    }
    assert ACTION_TRANSITIONS == {
        "todo": frozenset({"in_progress", "completed", "cancelled"}),
        "in_progress": frozenset({"todo", "completed", "cancelled"}),
        "completed": frozenset(),
        "cancelled": frozenset(),
    }
    assert ACTIVE_TREATMENT_STATUSES == frozenset({"not_started", "planned", "in_progress"})


def test_risk_treatment_table_shape():
    assert RiskTreatment.__tablename__ == "risk_treatments"
    treatment_columns = {column.name for column in RiskTreatment.__table__.columns}
    assert {
        "id",
        "tenant_id",
        "risk_id",
        "strategy",
        "status",
        "title",
        "description",
        "rationale",
        "owner",
        "created_by",
        "planned_start_date",
        "target_completion_date",
        "completed_at",
        "cancelled_at",
        "cancellation_reason",
        "created_at",
        "updated_at",
    } <= treatment_columns

    assert RiskTreatmentAction.__tablename__ == "risk_treatment_actions"
    action_columns = {column.name for column in RiskTreatmentAction.__table__.columns}
    assert {
        "id",
        "tenant_id",
        "treatment_id",
        "title",
        "description",
        "status",
        "owner",
        "priority",
        "planned_start_date",
        "due_date",
        "completed_at",
        "cancelled_at",
        "cancellation_reason",
        "created_by",
        "created_at",
        "updated_at",
    } <= action_columns

    assert RiskTreatmentResidualAssessment.__tablename__ == "risk_treatment_residual_assessments"
    residual_columns = {column.name for column in RiskTreatmentResidualAssessment.__table__.columns}
    assert {
        "id",
        "tenant_id",
        "treatment_id",
        "likelihood_value",
        "impact_value",
        "calculated_score",
        "severity",
        "assessment_type",
        "created_by",
        "created_at",
    } <= residual_columns
    # RF-04 §24: residual assessments are append-only history — no updated_at
    assert "updated_at" not in residual_columns


def _sql_of(constraint: sa.CheckConstraint) -> str:
    return constraint.sqltext if isinstance(constraint.sqltext, str) else constraint.sqltext.text


def test_risk_treatment_check_constraints():
    checks = {
        constraint.name: _sql_of(constraint)
        for constraint in RiskTreatment.__table__.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    strategy_sql = checks["ck_risk_treatments_strategy"]
    for value in ("mitigate", "accept", "avoid", "transfer"):
        assert value in strategy_sql

    status_sql = checks["ck_risk_treatments_status"]
    for value in ("not_started", "planned", "in_progress", "completed", "cancelled"):
        assert value in status_sql

    dates_sql = checks["ck_risk_treatments_dates"]
    assert "planned_start_date" in dates_sql and "target_completion_date" in dates_sql

    completed_sql = checks["ck_risk_treatments_completed"]
    assert "completed_at" in completed_sql and "status" in completed_sql

    cancelled_sql = checks["ck_risk_treatments_cancelled"]
    assert "cancellation_reason" in cancelled_sql and "cancelled_at" in cancelled_sql

    action_checks = {
        constraint.name: _sql_of(constraint)
        for constraint in RiskTreatmentAction.__table__.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    action_status_sql = action_checks["ck_risk_treatment_actions_status"]
    for value in ("todo", "in_progress", "completed", "cancelled"):
        assert value in action_status_sql
    priority_sql = action_checks["ck_risk_treatment_actions_priority"]
    for value in ("low", "medium", "high", "critical"):
        assert value in priority_sql
    assert "ck_risk_treatment_actions_dates" in action_checks
    assert "ck_risk_treatment_actions_completed" in action_checks
    assert "ck_risk_treatment_actions_cancelled" in action_checks

    residual_checks = {
        constraint.name: _sql_of(constraint)
        for constraint in RiskTreatmentResidualAssessment.__table__.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    type_sql = residual_checks["ck_risk_treatment_residual_type"]
    assert "expected" in type_sql and "verified" in type_sql
    severity_sql = residual_checks["ck_risk_treatment_residual_severity"]
    for value in ("low", "medium", "high", "critical"):
        assert value in severity_sql
    values_sql = residual_checks["ck_risk_treatment_residual_values"]
    assert "likelihood_value" in values_sql and "impact_value" in values_sql


def test_risk_treatment_single_active_partial_unique_index():
    """At most one active treatment per risk — DB-enforced (RF-04 §48)."""
    indexes = {index.name: index for index in RiskTreatment.__table__.indexes}
    active = indexes["uq_risk_treatments_risk_active"]
    assert active.unique is True
    where = active.dialect_kwargs.get("postgresql_where")
    assert where is not None
    where_text = str(where)
    for value in ("not_started", "planned", "in_progress"):
        assert value in where_text
    assert "completed" not in where_text
    assert "cancelled" not in where_text


def test_risk_treatment_relationships_restrict_delete():
    def fk(model, target: str) -> sa.ForeignKey:
        return next(fk for fk in model.__table__.foreign_keys if fk.target_fullname == target)

    assert fk(RiskTreatment, "risks.id").ondelete == "RESTRICT"
    assert fk(RiskTreatmentAction, "risk_treatments.id").ondelete == "RESTRICT"
    assert fk(RiskTreatmentResidualAssessment, "risk_treatments.id").ondelete == "RESTRICT"
