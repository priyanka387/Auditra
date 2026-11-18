"""Integration tests: RF-03 default scoring-framework seed."""

from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.core.config import settings
from app.modules.risk_scoring.models import (
    RiskImpactLevel,
    RiskLikelihoodLevel,
    RiskScoringFramework,
    RiskSeverityThreshold,
)
from app.modules.risk_scoring.seed import (
    STANDARD_AI_RISK_MATRIX_NAME,
    seed_scoring_framework,
)

LIKELIHOOD_NAMES = ["Rare", "Unlikely", "Possible", "Likely", "Almost Certain"]
IMPACT_NAMES = ["Negligible", "Minor", "Moderate", "Major", "Severe"]
# (severity, min, max, priority, display_order) — RF-03 §49 defaults
THRESHOLDS = [
    ("low", 1, 4, 4, 1),
    ("medium", 5, 9, 3, 2),
    ("high", 10, 16, 2, 3),
    ("critical", 17, 25, 1, 4),
]


def _get_framework(db, tenant_id: UUID) -> RiskScoringFramework | None:
    return db.scalar(
        select(RiskScoringFramework).where(
            RiskScoringFramework.tenant_id == tenant_id,
            RiskScoringFramework.name == STANDARD_AI_RISK_MATRIX_NAME,
            RiskScoringFramework.version == 1,
        )
    )


def _levels(db, model, framework) -> list:
    return list(
        db.scalars(
            select(model)
            .where(model.framework_id == framework.id)
            .order_by(model.display_order, model.value)
        )
    )


def _thresholds(db, framework) -> list[RiskSeverityThreshold]:
    return list(
        db.scalars(
            select(RiskSeverityThreshold)
            .where(RiskSeverityThreshold.framework_id == framework.id)
            .order_by(RiskSeverityThreshold.minimum_score)
        )
    )


def test_seed_creates_standard_framework_with_levels_and_thresholds(db):
    tenant_id = settings.default_tenant_id
    inserted = seed_scoring_framework(db, tenant_id)
    db.commit()

    assert inserted == 1
    framework = _get_framework(db, tenant_id)
    assert framework is not None
    assert framework.name == STANDARD_AI_RISK_MATRIX_NAME
    assert framework.version == 1
    assert framework.method == "likelihood_impact"
    assert framework.is_system is True
    assert framework.is_active is True
    assert framework.description

    likelihoods = _levels(db, RiskLikelihoodLevel, framework)
    assert [level.value for level in likelihoods] == [1, 2, 3, 4, 5]
    assert [level.name for level in likelihoods] == LIKELIHOOD_NAMES
    assert all(level.description for level in likelihoods)
    assert all(level.is_active for level in likelihoods)

    impacts = _levels(db, RiskImpactLevel, framework)
    assert [level.value for level in impacts] == [1, 2, 3, 4, 5]
    assert [level.name for level in impacts] == IMPACT_NAMES
    assert all(level.description for level in impacts)
    assert all(level.is_active for level in impacts)

    thresholds = _thresholds(db, framework)
    assert [
        (t.severity, t.minimum_score, t.maximum_score, t.priority, t.display_order)
        for t in thresholds
    ] == THRESHOLDS


def test_seed_is_idempotent(db):
    tenant_id = settings.default_tenant_id
    first = seed_scoring_framework(db, tenant_id)
    db.commit()
    second = seed_scoring_framework(db, tenant_id)
    db.commit()

    assert first == 1
    assert second == 0

    framework = _get_framework(db, tenant_id)
    assert framework is not None
    likelihood_count = db.scalar(
        select(func.count())
        .select_from(RiskLikelihoodLevel)
        .where(RiskLikelihoodLevel.framework_id == framework.id)
    )
    impact_count = db.scalar(
        select(func.count())
        .select_from(RiskImpactLevel)
        .where(RiskImpactLevel.framework_id == framework.id)
    )
    threshold_count = db.scalar(
        select(func.count())
        .select_from(RiskSeverityThreshold)
        .where(RiskSeverityThreshold.framework_id == framework.id)
    )
    framework_count = db.scalar(
        select(func.count())
        .select_from(RiskScoringFramework)
        .where(RiskScoringFramework.tenant_id == tenant_id)
    )
    assert framework_count == 1
    assert likelihood_count == 5
    assert impact_count == 5
    assert threshold_count == 4


def test_seed_scopes_by_tenant(db):
    default_tenant = settings.default_tenant_id
    other_tenant = uuid4()

    assert seed_scoring_framework(db, default_tenant) == 1
    assert seed_scoring_framework(db, other_tenant) == 1
    db.commit()

    default_framework = _get_framework(db, default_tenant)
    other_framework = _get_framework(db, other_tenant)
    assert default_framework is not None
    assert other_framework is not None
    assert default_framework.id != other_framework.id
    assert len(_levels(db, RiskLikelihoodLevel, other_framework)) == 5
    assert len(_thresholds(db, other_framework)) == 4
