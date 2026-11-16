"""Integration tests: idempotent system risk taxonomy seed (RF-01)."""

from uuid import uuid4

from sqlalchemy import select

from app.core.config import settings
from app.modules.risk.models import RiskCategory
from app.modules.risk.seed import RISK_CATEGORIES, seed_risk_categories

# RF-01 plan §27: the default taxonomy, asserted independently of the
# implementation list so a typo in RISK_CATEGORIES fails the test.
EXPECTED_NAMES = {
    "Safety",
    "Security",
    "Privacy",
    "Fairness / Bias",
    "Reliability",
    "Accuracy",
    "Robustness",
    "Explainability",
    "Transparency",
    "Accountability",
    "Data Quality",
    "Data Governance",
    "Legal / Regulatory",
    "Third-Party / Vendor",
    "Operational",
    "Reputational",
    "Financial",
    "Human Oversight",
    "Misuse / Abuse",
    "Model Risk",
    "Other",
}

EXPECTED_SLUGS = {
    "safety",
    "security",
    "privacy",
    "fairness-bias",
    "reliability",
    "accuracy",
    "robustness",
    "explainability",
    "transparency",
    "accountability",
    "data-quality",
    "data-governance",
    "legal-regulatory",
    "third-party-vendor",
    "operational",
    "reputational",
    "financial",
    "human-oversight",
    "misuse-abuse",
    "model-risk",
    "other",
}


def _rows(db, tenant_id) -> list[RiskCategory]:
    return list(
        db.scalars(
            select(RiskCategory)
            .where(RiskCategory.tenant_id == tenant_id)
            .order_by(RiskCategory.slug)
        )
    )


def test_seed_risk_categories_creates_full_taxonomy(db):
    inserted = seed_risk_categories(db, settings.default_tenant_id)
    db.commit()

    assert inserted == 21
    assert len(RISK_CATEGORIES) == 21

    rows = _rows(db, settings.default_tenant_id)
    assert len(rows) == 21
    assert {row.name for row in rows} == EXPECTED_NAMES
    assert {row.slug for row in rows} == EXPECTED_SLUGS
    assert all(row.is_system for row in rows)
    assert all(row.is_active for row in rows)
    assert all(row.tenant_id == settings.default_tenant_id for row in rows)


def test_seed_risk_categories_is_idempotent(db):
    first = seed_risk_categories(db, settings.default_tenant_id)
    db.commit()
    second = seed_risk_categories(db, settings.default_tenant_id)
    db.commit()

    assert first == 21
    assert second == 0
    assert len(_rows(db, settings.default_tenant_id)) == 21


def test_seed_risk_categories_scopes_by_tenant(db):
    default_inserted = seed_risk_categories(db, settings.default_tenant_id)
    other_tenant = uuid4()
    other_inserted = seed_risk_categories(db, other_tenant)
    db.commit()

    assert default_inserted == 21
    assert other_inserted == 21
    assert len(_rows(db, settings.default_tenant_id)) == 21
    assert len(_rows(db, other_tenant)) == 21
