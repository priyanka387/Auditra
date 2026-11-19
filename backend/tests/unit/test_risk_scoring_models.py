"""Unit tests: RF-03 risk scoring domain models and enums."""

import sqlalchemy as sa

from app.modules.risk_scoring.enums import RiskSeverity, ScoreSource, ScoringMethod
from app.modules.risk_scoring.models import (
    RiskImpactLevel,
    RiskLikelihoodLevel,
    RiskScore,
    RiskScoringFramework,
    RiskSeverityThreshold,
)


def test_scoring_enum_values():
    assert {method.value for method in ScoringMethod} == {"likelihood_impact"}
    # RF-03 §13/§49: severity labels are the four fixed ratings; ranges
    # and priority are what stay configurable on threshold rows.
    assert {severity.value for severity in RiskSeverity} == {
        "low",
        "medium",
        "high",
        "critical",
    }
    assert {source.value for source in ScoreSource} == {"manual", "assessment"}


def test_risk_scoring_table_shape():
    assert RiskScoringFramework.__tablename__ == "risk_scoring_frameworks"
    framework_columns = {column.name for column in RiskScoringFramework.__table__.columns}
    assert {
        "id",
        "tenant_id",
        "name",
        "description",
        "version",
        "method",
        "is_system",
        "is_active",
        "created_at",
        "updated_at",
    } <= framework_columns

    assert RiskLikelihoodLevel.__tablename__ == "risk_likelihood_levels"
    likelihood_columns = {column.name for column in RiskLikelihoodLevel.__table__.columns}
    assert {
        "id",
        "framework_id",
        "value",
        "name",
        "description",
        "display_order",
        "is_active",
    } <= likelihood_columns

    assert RiskImpactLevel.__tablename__ == "risk_impact_levels"
    impact_columns = {column.name for column in RiskImpactLevel.__table__.columns}
    assert {
        "id",
        "framework_id",
        "value",
        "name",
        "description",
        "display_order",
        "is_active",
    } <= impact_columns

    assert RiskSeverityThreshold.__tablename__ == "risk_severity_thresholds"
    threshold_columns = {column.name for column in RiskSeverityThreshold.__table__.columns}
    assert {
        "id",
        "framework_id",
        "minimum_score",
        "maximum_score",
        "severity",
        "priority",
        "display_order",
    } <= threshold_columns

    assert RiskScore.__tablename__ == "risk_scores"
    score_columns = {column.name for column in RiskScore.__table__.columns}
    assert {
        "id",
        "tenant_id",
        "risk_id",
        "framework_id",
        "framework_version",
        "likelihood_value",
        "likelihood_label",
        "impact_value",
        "impact_label",
        "calculated_score",
        "severity",
        "priority",
        "override_score",
        "effective_score",
        "override_reason",
        "is_inherent",
        "is_current",
        "is_override",
        "assessment_id",
        "source",
        "scored_by",
        "created_at",
    } <= score_columns
    # RF-03 §37: score rows are immutable history — no updated_at
    assert "updated_at" not in score_columns


def _sql_of(constraint: sa.CheckConstraint) -> str:
    return constraint.sqltext if isinstance(constraint.sqltext, str) else constraint.sqltext.text


def test_risk_score_check_constraints():
    checks = {
        constraint.name: _sql_of(constraint)
        for constraint in RiskScore.__table__.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    severity_sql = checks["ck_risk_scores_severity"]
    for value in ("low", "medium", "high", "critical"):
        assert value in severity_sql

    source_sql = checks["ck_risk_scores_source"]
    assert "manual" in source_sql and "assessment" in source_sql

    override_sql = checks["ck_risk_scores_override"]
    assert "is_override" in override_sql and "override_score" in override_sql

    version_sql = checks["ck_risk_scores_framework_version"]
    assert "framework_version" in version_sql and ">= 1" in version_sql

    threshold_checks = {
        constraint.name: _sql_of(constraint)
        for constraint in RiskSeverityThreshold.__table__.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    range_sql = threshold_checks["ck_risk_severity_thresholds_range"]
    assert "minimum_score" in range_sql and "maximum_score" in range_sql
    priority_sql = threshold_checks["ck_risk_severity_thresholds_priority"]
    assert "priority" in priority_sql and ">= 1" in priority_sql
    severity_sql = threshold_checks["ck_risk_severity_thresholds_severity"]
    for value in ("low", "medium", "high", "critical"):
        assert value in severity_sql

    framework_checks = {
        constraint.name: _sql_of(constraint)
        for constraint in RiskScoringFramework.__table__.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    assert "ck_risk_scoring_frameworks_method" in framework_checks
    assert "ck_risk_scoring_frameworks_version" in framework_checks


def test_risk_score_unique_constraints():
    framework_uniques = {
        constraint.name: tuple(constraint.columns.keys())
        for constraint in RiskScoringFramework.__table__.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert framework_uniques["uq_risk_scoring_frameworks_tenant_name_version"] == (
        "tenant_id",
        "name",
        "version",
    )

    likelihood_uniques = {
        constraint.name: tuple(constraint.columns.keys())
        for constraint in RiskLikelihoodLevel.__table__.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert likelihood_uniques["uq_risk_likelihood_levels_framework_value"] == (
        "framework_id",
        "value",
    )

    threshold_uniques = {
        constraint.name: tuple(constraint.columns.keys())
        for constraint in RiskSeverityThreshold.__table__.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert threshold_uniques["uq_risk_severity_thresholds_framework_severity"] == (
        "framework_id",
        "severity",
    )


def test_risk_score_single_current_partial_unique_index():
    """At most one current score per risk — DB-enforced (RF-03 §16)."""
    indexes = {index.name: index for index in RiskScore.__table__.indexes}
    current = indexes["uq_risk_scores_risk_current"]
    assert current.unique is True
    where = current.dialect_kwargs.get("postgresql_where")
    assert where is not None
    assert "is_current" in str(where)


def test_risk_scoring_relationships_restrict_delete():
    def fk(model, target: str) -> sa.ForeignKey:
        return next(fk for fk in model.__table__.foreign_keys if fk.target_fullname == target)

    assert fk(RiskLikelihoodLevel, "risk_scoring_frameworks.id").ondelete == "RESTRICT"
    assert fk(RiskImpactLevel, "risk_scoring_frameworks.id").ondelete == "RESTRICT"
    assert fk(RiskSeverityThreshold, "risk_scoring_frameworks.id").ondelete == "RESTRICT"
    assert fk(RiskScore, "risks.id").ondelete == "RESTRICT"
    assert fk(RiskScore, "risk_scoring_frameworks.id").ondelete == "RESTRICT"
    assert fk(RiskScore, "risk_assessments.id").ondelete == "RESTRICT"
