"""Idempotent seed for the system risk taxonomy (RF-01 §27).

The 21 default categories are data, not code: they live in
``risk_categories`` so organizations can add, rename and disable
entries without an application change. ``seed_risk_categories`` is
safe to run any number of times — existing ``(tenant_id, slug)`` pairs
are skipped in Python and the unique constraint backstops races — which
is what makes ``python -m app.seed`` re-runnable.
"""

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.risk.models import RiskCategory

# (name, slug) — names follow RF-01 plan §27 exactly; slugs are the
# URL/query-safe identity used by the unique (tenant_id, slug) pair.
RISK_CATEGORIES: list[tuple[str, str]] = [
    ("Safety", "safety"),
    ("Security", "security"),
    ("Privacy", "privacy"),
    ("Fairness / Bias", "fairness-bias"),
    ("Reliability", "reliability"),
    ("Accuracy", "accuracy"),
    ("Robustness", "robustness"),
    ("Explainability", "explainability"),
    ("Transparency", "transparency"),
    ("Accountability", "accountability"),
    ("Data Quality", "data-quality"),
    ("Data Governance", "data-governance"),
    ("Legal / Regulatory", "legal-regulatory"),
    ("Third-Party / Vendor", "third-party-vendor"),
    ("Operational", "operational"),
    ("Reputational", "reputational"),
    ("Financial", "financial"),
    ("Human Oversight", "human-oversight"),
    ("Misuse / Abuse", "misuse-abuse"),
    ("Model Risk", "model-risk"),
    ("Other", "other"),
]


def seed_risk_categories(db: Session, tenant_id: UUID) -> int:
    """Insert missing system categories for one tenant; return new rows.

    Idempotent: a category that already exists for this tenant (from an
    earlier seed run or an import) is skipped, never duplicated or
    overwritten — custom edits to seeded rows survive re-seeding.
    """
    existing = set(db.scalars(select(RiskCategory.slug).where(RiskCategory.tenant_id == tenant_id)))
    inserted = 0
    for name, slug in RISK_CATEGORIES:
        if slug in existing:
            continue
        db.add(
            RiskCategory(
                id=uuid4(),
                tenant_id=tenant_id,
                name=name,
                slug=slug,
                is_system=True,
                is_active=True,
            )
        )
        inserted += 1
    db.flush()
    return inserted
