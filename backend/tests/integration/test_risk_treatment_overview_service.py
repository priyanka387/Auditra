"""Integration tests: RF-04 treatment overview — dashboard numbers only (plan §44/§45).

The overview aggregates what the register already knows: treated vs
untreated active risks, status/strategy distributions across *all*
treatments (history included), derived overdue counts (Ruling 9), and
untreated critical/high risks scored by RF-03. Archived risks never
count, and an empty tenant returns zeros everywhere — no division by
zero, no fabricated rows.
"""

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa

from app.core.config import settings
from app.modules.risk.models import Risk, RiskCategory
from app.modules.risk.seed import seed_risk_categories
from app.modules.risk_scoring.models import RiskScoringFramework
from app.modules.risk_scoring.schemas import RiskScoreCreate
from app.modules.risk_scoring.seed import seed_scoring_framework
from app.modules.risk_scoring.service import RiskScoringService
from app.modules.risk_treatment.enums import RiskTreatmentStrategy
from app.modules.risk_treatment.models import RiskTreatment
from app.modules.risk_treatment.schemas import (
    TreatmentActionCreate,
    TreatmentCancelRequest,
    TreatmentCreate,
    TreatmentUpdate,
)
from app.modules.risk_treatment.service import (
    RiskTreatmentActionService,
    RiskTreatmentService,
)

TENANT = settings.default_tenant_id
OTHER_TENANT = uuid4()


# --- fixtures & helpers ------------------------------------------------------


def _treatment_service(db, tenant_id: UUID | None = None) -> RiskTreatmentService:
    return RiskTreatmentService(
        db,
        tenant_id=tenant_id or TENANT,
        request_id="req-overview-01",
        actor=None,
    )


def _action_service(db, tenant_id: UUID | None = None) -> RiskTreatmentActionService:
    return RiskTreatmentActionService(
        db,
        tenant_id=tenant_id or TENANT,
        request_id="req-overview-01",
        actor=None,
    )


def _seed_framework(db, tenant_id: UUID) -> RiskScoringFramework:
    seed_scoring_framework(db, tenant_id)
    db.commit()
    return db.scalar(
        sa.select(RiskScoringFramework).where(
            RiskScoringFramework.tenant_id == tenant_id,
            RiskScoringFramework.name == "Standard AI Risk Matrix",
        )
    )


def _make_risk(db, tenant_id: UUID, *, title: str, archived: bool = False) -> Risk:
    seed_risk_categories(db, tenant_id)
    db.commit()
    category = db.scalar(
        sa.select(RiskCategory).where(
            RiskCategory.tenant_id == tenant_id,
            RiskCategory.slug == "security",
        )
    )
    risk = Risk(
        id=uuid4(),
        tenant_id=tenant_id,
        title=title,
        category_id=category.id,
        source="manual",
        status="open",
    )
    if archived:
        risk.archived_at = datetime.now(UTC)
    db.add(risk)
    db.commit()
    return risk


def _score(db, tenant_id: UUID, risk: Risk, likelihood: int, impact: int) -> None:
    framework = db.scalar(
        sa.select(RiskScoringFramework).where(
            RiskScoringFramework.tenant_id == tenant_id,
            RiskScoringFramework.is_system.is_(True),
            RiskScoringFramework.is_active.is_(True),
        )
    )
    RiskScoringService(db, tenant_id=tenant_id, actor=None).score_risk(
        risk.id, RiskScoreCreate(framework_id=framework.id, likelihood=likelihood, impact=impact)
    )


def _make_treatment(
    db,
    risk: Risk,
    *,
    strategy: RiskTreatmentStrategy = RiskTreatmentStrategy.MITIGATE,
    rationale: str | None = None,
    target_completion_date: date | None = None,
) -> RiskTreatment:
    return _treatment_service(db, risk.tenant_id).create(
        risk.id,
        TreatmentCreate(
            title="Encrypt training data at rest",
            strategy=strategy,
            rationale=rationale,
            owner="Security Team",
            target_completion_date=target_completion_date,
        ),
    )


def _to_in_progress(service: RiskTreatmentService, treatment_id: UUID) -> None:
    service.update(treatment_id, TreatmentUpdate(status="planned"))
    service.update(treatment_id, TreatmentUpdate(status="in_progress"))


# --- coverage of treated vs untreated risks -----------------------------------


def test_overview_counts_treated_and_untreated_risks(db):
    _seed_framework(db, TENANT)
    first = _make_risk(db, TENANT, title="Risk one")
    _make_risk(db, TENANT, title="Risk two")
    _make_risk(db, TENANT, title="Risk three")
    _make_treatment(db, first)

    overview = _treatment_service(db).get_overview()

    assert overview.total_risks == 3
    assert overview.risks_with_treatment == 1
    assert overview.risks_without_treatment == 2


def test_overview_status_and_strategy_distributions(db):
    _seed_framework(db, TENANT)
    running = _make_risk(db, TENANT, title="Running treatment")
    done = _make_risk(db, TENANT, title="Finished treatment")
    dropped = _make_risk(db, TENANT, title="Dropped treatment")
    service = _treatment_service(db)

    in_progress = _make_treatment(db, running)
    _to_in_progress(service, in_progress.id)

    finished = _make_treatment(db, done)
    _to_in_progress(service, finished.id)
    service.complete(finished.id)

    accepted = _make_treatment(
        db, dropped, strategy=RiskTreatmentStrategy.ACCEPT, rationale="Board decision"
    )
    service.cancel(accepted.id, TreatmentCancelRequest(reason="Avoid instead"))

    overview = service.get_overview()

    # distributions cover ALL treatments, history included (plan §44)
    assert overview.status_distribution.not_started == 0
    assert overview.status_distribution.planned == 0
    assert overview.status_distribution.in_progress == 1
    assert overview.status_distribution.completed == 1
    assert overview.status_distribution.cancelled == 1
    assert overview.strategy_distribution.mitigate == 2
    assert overview.strategy_distribution.accept == 1
    assert overview.strategy_distribution.avoid == 0
    assert overview.strategy_distribution.transfer == 0


# --- derived overdue counts ---------------------------------------------------


def test_overview_counts_overdue_actions_and_treatments(db):
    _seed_framework(db, TENANT)
    risk = _make_risk(db, TENANT, title="Slipping programme")
    # active treatment whose target already passed
    treatment = _make_treatment(db, risk, target_completion_date=date(2026, 11, 1))
    actions = _action_service(db)
    overdue = actions.create(
        treatment.id,
        TreatmentActionCreate(
            title="Restrict access", owner="Security Team", due_date=date(2026, 11, 10)
        ),
    )
    finished_late = actions.create(
        treatment.id,
        TreatmentActionCreate(
            title="Add PII filtering", owner="AppSec", due_date=date(2026, 11, 12)
        ),
    )
    actions.complete(finished_late.id)

    overview = _treatment_service(db).get_overview()

    # the open past-due action counts; the completed one never does
    # (Ruling 9), and only active treatments are overdue
    assert overview.overdue_actions == 1
    assert overview.overdue_treatments == 1
    assert overdue.status == "todo"


def test_overview_untreated_critical_and_high(db):
    _seed_framework(db, TENANT)
    critical = _make_risk(db, TENANT, title="Critical untreated")
    _score(db, TENANT, critical, 4, 5)  # 20 → critical
    high = _make_risk(db, TENANT, title="High untreated")
    _score(db, TENANT, high, 2, 5)  # 10 → high
    treated = _make_risk(db, TENANT, title="Critical but treated")
    _score(db, TENANT, treated, 4, 5)  # 20 → critical
    _make_treatment(db, treated)
    _make_risk(db, TENANT, title="Never scored")

    overview = _treatment_service(db).get_overview()

    assert overview.untreated_critical == 1
    assert overview.untreated_high == 1


# --- boundary cases ------------------------------------------------------------


def test_overview_excludes_archived_risks(db):
    _seed_framework(db, TENANT)
    active = _make_risk(db, TENANT, title="Still in scope")
    archived = _make_risk(db, TENANT, title="Retired model")
    # score before archiving (RF-03 refuses archived risks), then
    # archive: neither its critical score nor its absence of treatment
    # may leak into the overview
    _score(db, TENANT, archived, 4, 5)
    archived.archived_at = datetime.now(UTC)
    db.commit()

    overview = _treatment_service(db).get_overview()

    assert overview.total_risks == 1
    assert overview.risks_with_treatment == 0
    assert overview.risks_without_treatment == 1
    assert overview.untreated_critical == 0
    assert overview.untreated_high == 0
    assert active.archived_at is None


def test_overview_empty_tenant(db):
    overview = _treatment_service(db, OTHER_TENANT).get_overview()

    assert overview.total_risks == 0
    assert overview.risks_with_treatment == 0
    assert overview.risks_without_treatment == 0
    assert overview.overdue_actions == 0
    assert overview.overdue_treatments == 0
    assert overview.untreated_critical == 0
    assert overview.untreated_high == 0
    assert overview.status_distribution.not_started == 0
    assert overview.status_distribution.completed == 0
    assert overview.strategy_distribution.mitigate == 0
    assert overview.strategy_distribution.accept == 0
