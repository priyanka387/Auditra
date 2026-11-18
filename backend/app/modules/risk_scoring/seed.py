"""Idempotent seed for RF-03's default scoring framework.

The Standard AI Risk Matrix (5x5, score = likelihood x impact) is
provisioned per tenant exactly like RF-01's system risk categories:
``is_system=True`` rows that the API never lets an organization delete,
identified by ``(tenant_id, name, version)`` so re-running the seed is
duplicate-free. Every range and priority here is configuration stored
on rows — the engine reads them back, never these constants.
"""

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.risk_scoring.enums import RiskSeverity, ScoringMethod
from app.modules.risk_scoring.models import (
    RiskImpactLevel,
    RiskLikelihoodLevel,
    RiskScoringFramework,
    RiskSeverityThreshold,
)

STANDARD_AI_RISK_MATRIX_NAME = "Standard AI Risk Matrix"
STANDARD_AI_RISK_MATRIX_DESCRIPTION = (
    "Standard 5x5 AI risk matrix: score = likelihood x impact (RF-03 default framework)."
)

# (value, name, description) — RF-03 §49 default likelihood scale
LIKELIHOOD_LEVELS: list[tuple[int, str, str]] = [
    (1, "Rare", "The event is not expected to occur."),
    (2, "Unlikely", "The event could occur but is not expected."),
    (3, "Possible", "The event may occur under some circumstances."),
    (4, "Likely", "The risk is expected to occur under normal operating conditions."),
    (5, "Almost Certain", "The event is expected to occur in most circumstances."),
]

# (value, name, description) — RF-03 §49 default impact scale
IMPACT_LEVELS: list[tuple[int, str, str]] = [
    (1, "Negligible", "No meaningful harm to stakeholders or operations."),
    (2, "Minor", "Limited harm that is easily contained."),
    (3, "Moderate", "Noticeable harm requiring remediation."),
    (4, "Major", "Severe harm to stakeholders, operations or compliance."),
    (5, "Severe", "Critical or irreversible harm, including safety or regulatory breaches."),
]

# (severity, min, max, priority, display_order) — RF-03 §49 defaults:
# 1-4 low, 5-9 medium, 10-16 high, 17-25 critical; Critical ranks first
SEVERITY_THRESHOLDS: list[tuple[str, int, int, int, int]] = [
    (RiskSeverity.LOW.value, 1, 4, 4, 1),
    (RiskSeverity.MEDIUM.value, 5, 9, 3, 2),
    (RiskSeverity.HIGH.value, 10, 16, 2, 3),
    (RiskSeverity.CRITICAL.value, 17, 25, 1, 4),
]


def seed_scoring_framework(db: Session, tenant_id: UUID) -> int:
    """Insert the system scoring framework for a tenant if absent.

    Returns 1 when created and 0 when it already exists; the unique
    ``(tenant_id, name, version)`` constraint backs the idempotency.
    """
    existing = db.scalar(
        select(RiskScoringFramework.id).where(
            RiskScoringFramework.tenant_id == tenant_id,
            RiskScoringFramework.name == STANDARD_AI_RISK_MATRIX_NAME,
            RiskScoringFramework.version == 1,
        )
    )
    if existing is not None:
        return 0

    framework = RiskScoringFramework(
        id=uuid4(),
        tenant_id=tenant_id,
        name=STANDARD_AI_RISK_MATRIX_NAME,
        description=STANDARD_AI_RISK_MATRIX_DESCRIPTION,
        version=1,
        method=ScoringMethod.LIKELIHOOD_IMPACT.value,
        is_system=True,
        is_active=True,
    )
    db.add(framework)
    db.flush()

    for position, (value, name, description) in enumerate(LIKELIHOOD_LEVELS, start=1):
        db.add(
            RiskLikelihoodLevel(
                id=uuid4(),
                framework_id=framework.id,
                value=value,
                name=name,
                description=description,
                display_order=position,
                is_active=True,
            )
        )

    for position, (value, name, description) in enumerate(IMPACT_LEVELS, start=1):
        db.add(
            RiskImpactLevel(
                id=uuid4(),
                framework_id=framework.id,
                value=value,
                name=name,
                description=description,
                display_order=position,
                is_active=True,
            )
        )

    for severity, minimum, maximum, priority, display_order in SEVERITY_THRESHOLDS:
        db.add(
            RiskSeverityThreshold(
                id=uuid4(),
                framework_id=framework.id,
                minimum_score=minimum,
                maximum_score=maximum,
                severity=severity,
                priority=priority,
                display_order=display_order,
            )
        )

    db.flush()
    return 1
