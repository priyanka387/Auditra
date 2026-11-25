"""Integration tests: RF-04 expected residual risk — RF-03 engine, append-only history.

Residual assessments record what the organization *expects* after the
treatment runs (plan §23/§24): likelihood/impact validated against the
tenant's active scoring framework, score and severity computed through
the RF-03 engine (never literals — plan Task 6 Step 3), only
``expected`` accepted (Ruling 10), history kept append-only, and every
lookup tenant-scoped (plan §51).
"""

from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from app.core.config import settings
from app.modules.audit.models import AuditEvent
from app.modules.risk.models import RiskCategory
from app.modules.risk.schemas import RiskCreate
from app.modules.risk.seed import seed_risk_categories
from app.modules.risk.service import RiskService
from app.modules.risk_scoring.engine import ScoreCalculator, SeverityClassifier
from app.modules.risk_scoring.errors import ScoringFrameworkNotFoundError
from app.modules.risk_scoring.models import RiskScoringFramework
from app.modules.risk_scoring.repository import get_framework_config
from app.modules.risk_scoring.seed import seed_scoring_framework
from app.modules.risk_treatment.enums import RiskTreatmentStrategy
from app.modules.risk_treatment.errors import (
    InvalidRiskTreatmentResidualError,
    RiskTreatmentNotFoundError,
)
from app.modules.risk_treatment.models import RiskTreatment, RiskTreatmentResidualAssessment
from app.modules.risk_treatment.schemas import (
    ResidualAssessmentCreate,
    ResidualAssessmentFilter,
    TreatmentCreate,
)
from app.modules.risk_treatment.service import RiskTreatmentService

TENANT = settings.default_tenant_id
OTHER_TENANT = uuid4()


# --- fixtures & helpers ------------------------------------------------------


def _service(db, tenant_id: UUID | None = None) -> RiskTreatmentService:
    return RiskTreatmentService(
        db,
        tenant_id=tenant_id or TENANT,
        request_id="req-residual-01",
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


def _make_treatment(db, tenant_id: UUID | None = None) -> RiskTreatment:
    """A risk (RF-01 path) plus a fresh mitigate treatment (RF-04 path)."""
    tenant = tenant_id or TENANT
    seed_risk_categories(db, tenant)
    db.commit()
    category = db.scalar(
        sa.select(RiskCategory).where(
            RiskCategory.tenant_id == tenant,
            RiskCategory.slug == "security",
        )
    )
    risk = RiskService(db, tenant_id=tenant, actor=None).create(
        RiskCreate(title="Training data unencrypted", category_id=category.id)
    )
    return RiskTreatmentService(db, tenant_id=tenant, actor=None).create(
        risk.id,
        TreatmentCreate(
            title="Encrypt training data at rest",
            strategy=RiskTreatmentStrategy.MITIGATE,
            owner="Security Team",
        ),
    )


def _payload(**overrides) -> ResidualAssessmentCreate:
    payload = {"likelihood": 2, "impact": 4}
    payload.update(overrides)
    return ResidualAssessmentCreate(**payload)


def _events(db, treatment_id: UUID, event_type: str | None = None) -> list[AuditEvent]:
    stmt = sa.select(AuditEvent).where(
        AuditEvent.resource_type == "treatment",
        AuditEvent.resource_id == str(treatment_id),
    )
    if event_type is not None:
        stmt = stmt.where(AuditEvent.event_type == event_type)
    return list(db.scalars(stmt.order_by(AuditEvent.sequence_no)))


# --- create: engine-computed expected residual --------------------------------


def test_create_expected_residual_computes_via_rf03_engine(db):
    framework = _seed_framework(db, TENANT)
    treatment = _make_treatment(db)

    residual = _service(db).create_residual(treatment.id, _payload(likelihood=2, impact=4))

    # 2 x 4 = 8 falls in the seeded 5-9 band → medium (never a literal)
    assert residual.likelihood_value == 2
    assert residual.impact_value == 4
    assert residual.calculated_score == 8
    assert residual.severity == "medium"
    assert residual.assessment_type == "expected"
    assert residual.tenant_id == TENANT
    assert residual.treatment_id == treatment.id
    assert residual.created_by is None

    events = _events(db, treatment.id, "risk.treatment_residual_assessment_created")
    assert len(events) == 1
    assert events[0].resource_type == "treatment"
    assert events[0].metadata_["framework_id"] == str(framework.id)
    assert events[0].metadata_["risk_id"] == str(treatment.risk_id)


def test_create_residual_unknown_treatment_404(db):
    _seed_framework(db, TENANT)

    with pytest.raises(RiskTreatmentNotFoundError):
        _service(db).create_residual(uuid4(), _payload())


def test_create_residual_cross_tenant_404(db):
    _seed_framework(db, TENANT)
    treatment = _make_treatment(db)

    with pytest.raises(RiskTreatmentNotFoundError):
        _service(db, OTHER_TENANT).create_residual(treatment.id, _payload())


def test_create_residual_verified_rejected(db):
    _seed_framework(db, TENANT)
    treatment = _make_treatment(db)

    with pytest.raises(InvalidRiskTreatmentResidualError) as exc:
        _service(db).create_residual(treatment.id, _payload(assessment_type="verified"))

    assert exc.value.code == "RISK_TREATMENT_RESIDUAL_INVALID"
    assert exc.value.http_status == 400


def test_create_residual_invalid_likelihood_400(db):
    _seed_framework(db, TENANT)
    treatment = _make_treatment(db)

    with pytest.raises(InvalidRiskTreatmentResidualError) as exc:
        _service(db).create_residual(treatment.id, _payload(likelihood=6))

    # the message names the offending value (mirrors RF-03)
    assert "6" in exc.value.message
    assert exc.value.code == "RISK_TREATMENT_RESIDUAL_INVALID"


def test_create_residual_without_framework_404(db):
    framework = _seed_framework(db, TENANT)
    framework.is_active = False
    db.commit()
    treatment = _make_treatment(db)

    with pytest.raises(ScoringFrameworkNotFoundError) as exc:
        _service(db).create_residual(treatment.id, _payload())

    assert exc.value.code == "SCORING_FRAMEWORK_NOT_FOUND"


def test_create_residual_never_stores_literals(db):
    framework = _seed_framework(db, TENANT)
    treatment = _make_treatment(db)

    residual = _service(db).create_residual(treatment.id, _payload(likelihood=2, impact=4))

    # recompute through the engine + this framework's threshold rows: the
    # stored values must be whatever the configuration says (plan Step 6)
    _, _, thresholds = get_framework_config(db, framework.id)
    assert residual.calculated_score == ScoreCalculator.calculate(2, 4, framework)
    assert residual.severity == SeverityClassifier.classify(residual.calculated_score, thresholds)


# --- list: newest first, tenant-scoped, append-only ---------------------------


def test_list_residual_newest_first_and_tenant_scoped(db):
    _seed_framework(db, TENANT)
    treatment = _make_treatment(db)
    service = _service(db)
    first = service.create_residual(treatment.id, _payload(likelihood=1, impact=1))
    second = service.create_residual(treatment.id, _payload(likelihood=5, impact=5))

    items, total = service.list_residual(treatment.id, ResidualAssessmentFilter())

    assert total == 2
    assert [item.id for item in items] == [second.id, first.id]

    with pytest.raises(RiskTreatmentNotFoundError):
        _service(db, OTHER_TENANT).list_residual(treatment.id, ResidualAssessmentFilter())


def test_residual_history_preserved(db):
    _seed_framework(db, TENANT)
    treatment = _make_treatment(db)
    service = _service(db)
    first = service.create_residual(treatment.id, _payload(likelihood=2, impact=4))
    second = service.create_residual(treatment.id, _payload(likelihood=3, impact=3))

    rows, total = service.list_residual(treatment.id, ResidualAssessmentFilter())

    # append-only: both snapshots stay, nothing is edited in place
    assert total == 2
    assert {row.id for row in rows} == {first.id, second.id}
    assert "updated_at" not in RiskTreatmentResidualAssessment.__table__.columns
    assert first.calculated_score == 8
    assert second.calculated_score == 9
