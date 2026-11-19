"""Integration tests: RF-03 RiskScoringService — scoring workflow, tenancy, audit.

Covers the append-only score history (recalculate/override insert new rows
and flip ``is_current``), tenant isolation on risks/frameworks/assessments,
archived-risk rejection, and the scoring overview aggregation.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from pydantic import ValidationError

from app.core.config import settings
from app.modules.assessment.errors import AssessmentNotFoundError
from app.modules.assessment.models import AssessmentTemplate, RiskAssessment
from app.modules.assessment.seed import seed_assessment_templates
from app.modules.audit.models import AuditEvent
from app.modules.risk.errors import RiskArchivedError, RiskNotFoundError
from app.modules.risk.models import Risk, RiskCategory
from app.modules.risk.seed import seed_risk_categories
from app.modules.risk_scoring.errors import (
    InvalidRiskScoreError,
    RiskScoreNotFoundError,
    ScoringFrameworkInactiveError,
    ScoringFrameworkNotFoundError,
)
from app.modules.risk_scoring.models import RiskScore, RiskScoringFramework
from app.modules.risk_scoring.schemas import (
    RiskScoreCreate,
    RiskScoreFilter,
    RiskScoreOverrideRequest,
    ScoringFrameworkCreate,
)
from app.modules.risk_scoring.seed import STANDARD_AI_RISK_MATRIX_NAME, seed_scoring_framework
from app.modules.risk_scoring.service import RiskScoringService, ScoringFrameworkService

TENANT = settings.default_tenant_id
OTHER_TENANT = uuid4()


# --- fixtures & helpers ------------------------------------------------------


def _service(db, tenant_id: UUID | None = None) -> RiskScoringService:
    return RiskScoringService(
        db,
        tenant_id=tenant_id or TENANT,
        request_id="req-scoring-02",
        actor="user-7",
    )


def _make_risk(
    db, tenant_id: UUID, *, title: str = "Model drifts in production", archived: bool = False
) -> Risk:
    seed_risk_categories(db, tenant_id)
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


def _seed_framework(db, tenant_id: UUID) -> RiskScoringFramework:
    seed_scoring_framework(db, tenant_id)
    db.commit()
    return db.scalar(
        sa.select(RiskScoringFramework).where(
            RiskScoringFramework.tenant_id == tenant_id,
            RiskScoringFramework.name == STANDARD_AI_RISK_MATRIX_NAME,
        )
    )


@pytest.fixture
def framework(db) -> RiskScoringFramework:
    return _seed_framework(db, TENANT)


@pytest.fixture
def risk(db) -> Risk:
    return _make_risk(db, TENANT)


def _payload(
    framework_id: UUID, likelihood: int = 4, impact: int = 5, assessment_id: UUID | None = None
) -> RiskScoreCreate:
    return RiskScoreCreate(
        framework_id=framework_id,
        likelihood=likelihood,
        impact=impact,
        assessment_id=assessment_id,
    )


def _assessment(db, tenant_id: UUID, risk: Risk) -> RiskAssessment:
    seed_assessment_templates(db, tenant_id)
    db.commit()
    template = db.scalar(
        sa.select(AssessmentTemplate).where(
            AssessmentTemplate.tenant_id == tenant_id,
            AssessmentTemplate.key == "general-ai-risk-assessment",
        )
    )
    assessment = RiskAssessment(
        id=uuid4(),
        tenant_id=tenant_id,
        risk_id=risk.id,
        template_id=template.id,
    )
    db.add(assessment)
    db.commit()
    return assessment


def _scores(db, risk_id: UUID) -> list[RiskScore]:
    return list(
        db.scalars(
            sa.select(RiskScore)
            .where(RiskScore.risk_id == risk_id)
            .order_by(RiskScore.created_at, RiskScore.id)
        )
    )


def _events(db, risk_id: UUID) -> list[AuditEvent]:
    return list(
        db.scalars(
            sa.select(AuditEvent)
            .where(
                AuditEvent.resource_type == "risk",
                AuditEvent.resource_id == str(risk_id),
            )
            .order_by(AuditEvent.sequence_no)
        )
    )


# --- scoring ----------------------------------------------------------------


def test_score_risk_persists_calculated_score_severity_priority(db, framework, risk):
    score = _service(db).score_risk(risk.id, _payload(framework.id, likelihood=4, impact=5))

    assert score.calculated_score == 20
    assert score.effective_score == 20
    assert score.severity == "critical"
    assert score.priority == 1
    assert score.is_current is True
    assert score.is_override is False
    assert score.likelihood_label == "Likely"
    assert score.impact_label == "Severe"
    assert score.framework_version == 1
    assert score.source == "manual"
    assert score.scored_by == "user-7"
    assert score.override_score is None

    events = _events(db, risk.id)
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "risk.score_created"
    assert event.resource_type == "risk"
    assert event.after_state["calculated_score"] == 20
    assert event.after_state["severity"] == "critical"
    assert event.metadata_["score_id"] == str(score.id)


def test_score_risk_unknown_risk_returns_404(db, framework):
    with pytest.raises(RiskNotFoundError) as exc:
        _service(db).score_risk(uuid4(), _payload(framework.id))
    assert exc.value.http_status == 404


def test_score_risk_cross_tenant_risk_returns_404(db, framework):
    other_risk = _make_risk(db, OTHER_TENANT, title="Other tenant risk")
    with pytest.raises(RiskNotFoundError):
        _service(db).score_risk(other_risk.id, _payload(framework.id))


def test_score_risk_unknown_framework_returns_404(db, risk):
    with pytest.raises(ScoringFrameworkNotFoundError) as exc:
        _service(db).score_risk(risk.id, _payload(uuid4()))
    assert exc.value.http_status == 404


def test_score_risk_cross_tenant_framework_returns_404(db, risk):
    other_framework = _seed_framework(db, OTHER_TENANT)
    with pytest.raises(ScoringFrameworkNotFoundError):
        _service(db).score_risk(risk.id, _payload(other_framework.id))


def test_score_risk_inactive_framework_returns_409(db, framework, risk):
    framework.is_active = False
    db.commit()
    with pytest.raises(ScoringFrameworkInactiveError) as exc:
        _service(db).score_risk(risk.id, _payload(framework.id))
    assert exc.value.http_status == 409


@pytest.mark.parametrize(
    "likelihood,impact",
    [(6, 5), (0, 5), (4, 6)],
    ids=["likelihood-too-high", "likelihood-zero", "impact-too-high"],
)
def test_score_risk_invalid_values_return_400(db, framework, risk, likelihood, impact):
    with pytest.raises(InvalidRiskScoreError) as exc:
        _service(db).score_risk(
            risk.id, _payload(framework.id, likelihood=likelihood, impact=impact)
        )
    assert exc.value.http_status == 400
    assert exc.value.code == "RISK_SCORE_INVALID"
    bad = likelihood if likelihood in (0, 6) else impact
    assert str(bad) in str(exc.value)


def test_score_risk_value_missing_from_framework_returns_400(db, risk):
    """A 2x2 framework has no value 3 — membership is framework-scoped."""
    created = ScoringFrameworkCreate(
        name="Tiny 2x2",
        likelihood_levels=[{"value": 1, "name": "Unlikely"}, {"value": 2, "name": "Likely"}],
        impact_levels=[{"value": 1, "name": "Minor"}, {"value": 2, "name": "Major"}],
        severity_thresholds=[
            {"severity": "low", "minimum_score": 1, "maximum_score": 2, "priority": 2},
            {"severity": "high", "minimum_score": 3, "maximum_score": 4, "priority": 1},
        ],
    )
    framework = ScoringFrameworkService(db, TENANT).create(created)

    with pytest.raises(InvalidRiskScoreError) as exc:
        _service(db).score_risk(risk.id, _payload(framework.id, likelihood=3, impact=2))
    assert "3" in str(exc.value)


def test_score_risk_on_archived_risk_returns_409(db, framework):
    archived_risk = _make_risk(db, TENANT, title="Retired model", archived=True)
    with pytest.raises(RiskArchivedError) as exc:
        _service(db).score_risk(archived_risk.id, _payload(framework.id))
    assert exc.value.http_status == 409


def test_score_risk_with_foreign_assessment_returns_404(db, framework, risk):
    other_risk = _make_risk(db, OTHER_TENANT, title="Other tenant risk")
    foreign_assessment = _assessment(db, OTHER_TENANT, other_risk)
    with pytest.raises(AssessmentNotFoundError) as exc:
        _service(db).score_risk(
            risk.id, _payload(framework.id, assessment_id=foreign_assessment.id)
        )
    assert exc.value.http_status == 404


def test_score_risk_with_other_risks_assessment_returns_400(db, framework, risk):
    other_risk = _make_risk(db, TENANT, title="Sibling risk")
    sibling_assessment = _assessment(db, TENANT, other_risk)
    with pytest.raises(InvalidRiskScoreError) as exc:
        _service(db).score_risk(
            risk.id, _payload(framework.id, assessment_id=sibling_assessment.id)
        )
    assert exc.value.http_status == 400


def test_score_risk_with_assessment_sets_source(db, framework, risk):
    assessment = _assessment(db, TENANT, risk)
    score = _service(db).score_risk(risk.id, _payload(framework.id, assessment_id=assessment.id))
    assert score.source == "assessment"
    assert score.assessment_id == assessment.id


# --- history: current flips, newest-first -----------------------------------


def test_scoring_a_second_time_flips_current(db, framework, risk):
    risk_id = risk.id
    first = _service(db).score_risk(risk_id, _payload(framework.id, likelihood=4, impact=5))
    second = _service(db).score_risk(risk_id, _payload(framework.id, likelihood=2, impact=3))
    first_id, second_id = first.id, second.id

    db.expunge_all()
    rows = _scores(db, risk_id)
    assert len(rows) == 2
    by_id = {row.id: row for row in rows}
    assert by_id[first_id].is_current is False
    assert by_id[first_id].calculated_score == 20  # history preserved
    assert by_id[second_id].is_current is True
    assert by_id[second_id].calculated_score == 6


def test_get_current_score_returns_newest(db, framework, risk):
    _service(db).score_risk(risk.id, _payload(framework.id, likelihood=4, impact=5))
    second = _service(db).score_risk(risk.id, _payload(framework.id, likelihood=3, impact=4))

    current = _service(db).get_current_score(risk.id)
    assert current.id == second.id
    assert current.calculated_score == 12
    assert current.severity == "high"


def test_get_current_score_unscored_returns_404(db, risk):
    with pytest.raises(RiskScoreNotFoundError) as exc:
        _service(db).get_current_score(risk.id)
    assert exc.value.http_status == 404


def test_get_score_history_newest_first(db, framework, risk):
    scores = [
        _service(db).score_risk(risk.id, _payload(framework.id, likelihood=1, impact=1)),
        _service(db).score_risk(risk.id, _payload(framework.id, likelihood=2, impact=2)),
        _service(db).score_risk(risk.id, _payload(framework.id, likelihood=5, impact=5)),
    ]

    items, total = _service(db).get_score_history(risk.id, RiskScoreFilter())
    assert total == 3
    assert [item.id for item in items] == [scores[2].id, scores[1].id, scores[0].id]
    assert all(item.framework_version == 1 for item in items)


def test_get_score_history_unknown_risk_returns_404(db):
    with pytest.raises(RiskNotFoundError):
        _service(db).get_score_history(uuid4(), RiskScoreFilter())


# --- recalculate -------------------------------------------------------------


def test_recalculate_creates_new_row_and_preserves_old(db, framework, risk):
    risk_id = risk.id
    original = _service(db).score_risk(risk_id, _payload(framework.id, likelihood=4, impact=5))
    original_id = original.id

    recalculated = _service(db).recalculate_risk(risk_id)
    assert recalculated.id != original_id
    assert recalculated.is_current is True
    assert recalculated.calculated_score == 20
    assert recalculated.framework_version == 1

    db.expunge_all()
    rows = _scores(db, risk_id)
    assert len(rows) == 2
    by_id = {row.id: row for row in rows}
    old = by_id[original_id]
    assert old.is_current is False
    assert old.calculated_score == 20
    assert old.severity == "critical"
    assert old.effective_score == 20
    assert old.is_override is False

    events = _events(db, risk_id)
    assert events[-1].event_type == "risk.score_recalculated"
    assert events[-1].metadata_["previous_score_id"] == str(original_id)
    assert events[-1].metadata_["score_id"] == str(recalculated.id)


def test_recalculate_unscored_risk_returns_404(db, risk):
    with pytest.raises(RiskScoreNotFoundError) as exc:
        _service(db).recalculate_risk(risk.id)
    assert exc.value.http_status == 404


# --- override ----------------------------------------------------------------


def test_override_requires_reason_at_schema_level():
    with pytest.raises(ValidationError):
        RiskScoreOverrideRequest(override_score=16, reason="   ")
    with pytest.raises(ValidationError):
        RiskScoreOverrideRequest(override_score=16, reason="")


def test_override_within_range_creates_new_row(db, framework, risk):
    risk_id = risk.id
    current = _service(db).score_risk(risk_id, _payload(framework.id, likelihood=3, impact=4))
    assert current.calculated_score == 12
    assert current.severity == "high"
    current_id = current.id

    overridden = _service(db).override_score(
        risk_id, RiskScoreOverrideRequest(override_score=16, reason="  Business context  ")
    )
    assert overridden.is_override is True
    assert overridden.override_score == 16
    assert overridden.effective_score == 16
    assert overridden.calculated_score == 12  # engine result never replaced
    assert overridden.override_reason == "Business context"
    # severity/priority re-derived from the effective score 16 → high/2
    assert overridden.severity == "high"
    assert overridden.priority == 2
    assert overridden.is_current is True
    assert overridden.likelihood_value == 3
    assert overridden.impact_value == 4

    db.expunge_all()
    rows = _scores(db, risk_id)
    assert len(rows) == 2
    old = next(row for row in rows if row.id == current_id)
    assert old.is_current is False

    events = _events(db, risk_id)
    override_event = events[-1]
    assert override_event.event_type == "risk.score_overridden"
    assert override_event.before_state["effective_score"] == 12
    assert override_event.after_state["effective_score"] == 16
    assert override_event.after_state["is_override"] is True
    assert override_event.metadata_["previous_score_id"] == str(current_id)
    assert override_event.metadata_["score_id"] == str(overridden.id)


@pytest.mark.parametrize("override_score", [26, 0], ids=["above-range", "below-range"])
def test_override_out_of_range_returns_400(db, framework, risk, override_score):
    _service(db).score_risk(risk.id, _payload(framework.id, likelihood=3, impact=4))
    with pytest.raises(InvalidRiskScoreError) as exc:
        _service(db).override_score(
            risk.id, RiskScoreOverrideRequest(override_score=override_score, reason="too far")
        )
    assert exc.value.http_status == 400
    assert "between 1 and 25" in str(exc.value)


def test_override_unscored_risk_returns_404(db, risk):
    with pytest.raises(RiskScoreNotFoundError) as exc:
        _service(db).override_score(
            risk.id, RiskScoreOverrideRequest(override_score=10, reason="no score yet")
        )
    assert exc.value.http_status == 404


def test_override_does_not_mutate_previous_rows(db, framework, risk):
    risk_id = risk.id
    first = _service(db).score_risk(risk_id, _payload(framework.id, likelihood=2, impact=3))
    first_id = first.id
    _service(db).score_risk(risk_id, _payload(framework.id, likelihood=4, impact=5))
    _service(db).override_score(
        risk_id, RiskScoreOverrideRequest(override_score=7, reason="downgrade agreed")
    )

    db.expunge_all()
    rows = _scores(db, risk_id)
    assert len(rows) == 3
    untouched = next(row for row in rows if row.id == first_id)
    assert untouched.override_score is None
    assert untouched.is_override is False
    assert untouched.is_current is False
    assert untouched.effective_score == untouched.calculated_score == 6


# --- overview ----------------------------------------------------------------


def _score(db, framework, risk, likelihood: int, impact: int) -> RiskScore:
    return _service(db).score_risk(
        risk.id, _payload(framework.id, likelihood=likelihood, impact=impact)
    )


def test_overview_distribution_cells_and_top_risks(db, framework):
    risk_a = _make_risk(db, TENANT, title="Minor annoyance")
    risk_b = _make_risk(db, TENANT, title="Severe outage")
    risk_c = _make_risk(db, TENANT, title="Moderate breach")
    _make_risk(db, TENANT, title="Never scored")

    _score(db, framework, risk_a, 1, 1)  # 1 → low, priority 4
    _score(db, framework, risk_b, 5, 5)  # 25 → critical, priority 1
    _score(db, framework, risk_c, 3, 4)  # 12 → high, priority 2

    overview = _service(db).get_overview(None)
    assert overview.framework.id == framework.id
    assert overview.framework.name == STANDARD_AI_RISK_MATRIX_NAME

    distribution = overview.distribution
    assert distribution.critical == 1
    assert distribution.high == 1
    assert distribution.medium == 0
    assert distribution.low == 1
    assert distribution.unscored == 1

    # 5x5 grid: every likelihood/impact pair is a cell
    assert len(overview.cells) == 25
    cell_4_5 = next(cell for cell in overview.cells if cell.likelihood == 4 and cell.impact == 5)
    # no risk scored 4x5; the 5x5 risk sits in its own cell
    assert cell_4_5.count == 0
    cell_5_5 = next(cell for cell in overview.cells if cell.likelihood == 5 and cell.impact == 5)
    assert cell_5_5.count == 1
    assert risk_b.id in cell_5_5.risk_ids
    cell_1_1 = next(cell for cell in overview.cells if cell.likelihood == 1 and cell.impact == 1)
    assert cell_1_1.count == 1
    assert risk_a.id in cell_1_1.risk_ids

    # top risks: priority ASC then effective_score DESC
    assert [top.risk_id for top in overview.top_risks] == [risk_b.id, risk_c.id, risk_a.id]
    assert [top.title for top in overview.top_risks] == [
        "Severe outage",
        "Moderate breach",
        "Minor annoyance",
    ]
    assert [top.priority for top in overview.top_risks] == [1, 2, 4]
    assert len(overview.top_risks) <= 10


def test_overview_excludes_archived_risks(db, framework):
    active = _make_risk(db, TENANT, title="Active risk")
    archived = _make_risk(db, TENANT, title="Archived risk")
    _score(db, framework, active, 2, 3)
    _score(db, framework, archived, 5, 5)
    # archive only after scoring: archived risks reject scoring (RF-03 §30)
    archived.archived_at = datetime.now(UTC)
    db.commit()

    overview = _service(db).get_overview(None)
    distribution = overview.distribution
    # archived risk's current score is out of the aggregation entirely
    assert distribution.critical + distribution.high + distribution.medium + distribution.low == 1
    assert distribution.unscored == 0
    assert [top.risk_id for top in overview.top_risks] == [active.id]
    assert all(risk_id != archived.id for cell in overview.cells for risk_id in cell.risk_ids)


def test_overview_unknown_framework_returns_404(db):
    with pytest.raises(ScoringFrameworkNotFoundError) as exc:
        _service(db).get_overview(uuid4())
    assert exc.value.http_status == 404
