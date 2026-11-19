"""End-to-end acceptance scenario: RF-01 Register → RF-02 Assessment → RF-03 Scoring.

Walks the RF-03 acceptance path on top of the two shipped sub-features:
seed the system scoring framework → register an asset and risk the RF-01
way → run an assessment the RF-02 way → score the risk with the assessor's
explicit likelihood/impact → prove register integration, immutable score
history, recalculation, a justified override, the ordered audit trail, the
prioritization overview, and cross-tenant isolation.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa

from app.core.config import settings
from app.modules.assessment.models import (
    AssessmentQuestion,
    AssessmentSection,
    AssessmentTemplate,
)
from app.modules.assessment.schemas import AssessmentResponseUpdate, RiskAssessmentCreate
from app.modules.assessment.seed import (
    GENERAL_AI_RISK_ASSESSMENT_KEY,
    seed_assessment_templates,
)
from app.modules.assessment.service import AssessmentService
from app.modules.asset_discovery.models import DiscoveredAsset, DiscoverySource
from app.modules.audit.models import AuditEvent
from app.modules.risk.errors import RiskNotFoundError
from app.modules.risk.models import RiskCategory
from app.modules.risk.schemas import RiskCreate
from app.modules.risk.seed import seed_risk_categories
from app.modules.risk.service import RiskService
from app.modules.risk_scoring.errors import ScoringFrameworkNotFoundError
from app.modules.risk_scoring.models import RiskScore, RiskScoringFramework
from app.modules.risk_scoring.schemas import (
    RiskScoreCreate,
    RiskScoreFilter,
    RiskScoreOverrideRequest,
)
from app.modules.risk_scoring.seed import (
    STANDARD_AI_RISK_MATRIX_NAME,
    seed_scoring_framework,
)
from app.modules.risk_scoring.service import RiskScoringService, ScoringFrameworkService

TENANT = settings.default_tenant_id
OTHER_TENANT = uuid4()

API = "/api/v1"
SEEDED_FRAMEWORK = STANDARD_AI_RISK_MATRIX_NAME


# --- setup helpers -----------------------------------------------------------


def _asset(db, tenant_id: UUID, name: str) -> DiscoveredAsset:
    source = DiscoverySource(
        tenant_id=tenant_id,
        name=f"src-{uuid4().hex[:8]}",
        source_type="repository",
        config={"location": "/workspace/x"},
    )
    db.add(source)
    db.flush()
    asset = DiscoveredAsset(
        tenant_id=tenant_id,
        source_id=source.id,
        asset_type="MODEL",
        name=name,
        normalized_identity=f"MODEL|{name}|{uuid4().hex[:6]}",
        metadata_={},
        first_seen_at=datetime.now(UTC),
        last_seen_at=datetime.now(UTC),
    )
    db.add(asset)
    db.commit()
    return asset


def _risk(db, tenant_id: UUID, title: str) -> UUID:
    """Risk through the RF-01 path: seeded category + discovered asset."""
    seed_risk_categories(db, tenant_id)
    db.commit()
    category = db.scalar(
        sa.select(RiskCategory).where(
            RiskCategory.tenant_id == tenant_id,
            RiskCategory.slug == "security",
        )
    )
    asset = _asset(db, tenant_id, f"scoring-model-{uuid4().hex[:6]}")
    risk = RiskService(db, tenant_id).create(
        RiskCreate(
            title=title,
            description="Registered through the RF-01 acceptance path",
            category_id=category.id,
            asset_id=asset.id,
            source="asset_discovery",
        )
    )
    return risk.id


def _template(db, tenant_id: UUID) -> AssessmentTemplate:
    return db.scalar(
        sa.select(AssessmentTemplate).where(
            AssessmentTemplate.tenant_id == tenant_id,
            AssessmentTemplate.key == GENERAL_AI_RISK_ASSESSMENT_KEY,
        )
    )


def _questions(db, template_id: UUID) -> dict[str, AssessmentQuestion]:
    rows = db.scalars(
        sa.select(AssessmentQuestion)
        .join(AssessmentSection, AssessmentQuestion.section_id == AssessmentSection.id)
        .where(AssessmentSection.template_id == template_id)
    )
    return {row.key: row for row in rows}


def _value_for(question: AssessmentQuestion):
    kind = question.question_type
    if kind == "boolean":
        return True
    if kind == "number":
        return 7
    if kind == "single_select":
        return question.options[0]["value"]
    if kind == "multi_select":
        return [option["value"] for option in question.options[:2]]
    return f"answer to {question.key}"


def _answer_all_required(db, assessment_id: UUID, template_id: UUID) -> None:
    service = AssessmentService(db, TENANT, request_id="req-e2e-scoring", actor="qa-lead")
    for question in _questions(db, template_id).values():
        if question.required:
            service.save_response(
                assessment_id,
                question.id,
                AssessmentResponseUpdate(response=_value_for(question), answered_by="qa-lead"),
            )


def _framework_id(client) -> str:
    items = client.get(f"{API}/risk-scoring/frameworks", params={"page_size": 100}).json()["items"]
    return next(item["id"] for item in items if item["name"] == SEEDED_FRAMEWORK)


def _score_events(db, risk_id: UUID) -> list[str]:
    """Scoring audit trail for one risk, in commit order."""
    rows = db.scalars(
        sa.select(AuditEvent)
        .where(
            AuditEvent.resource_type == "risk",
            AuditEvent.resource_id == str(risk_id),
        )
        .order_by(AuditEvent.sequence_no)
    )
    return [str(row.event_type) for row in rows if str(row.event_type).startswith("risk.score_")]


# --- the journey -------------------------------------------------------------


def test_full_risk_scoring_journey(db, client):
    # 1. every system seed is provisioned per tenant and idempotent; the
    #    client fixture already seeded categories, templates and the matrix
    assert seed_scoring_framework(db, TENANT) == 0
    assert seed_assessment_templates(db, TENANT) == 0
    db.commit()
    framework_id = _framework_id(client)

    # 2. RF-01 path: discovered asset + registered risk, plus a second risk
    #    that will stay unscored throughout
    risk_id = _risk(db, TENANT, "Undeclared model in production path")
    unscored_id = _risk(db, TENANT, "Vendor model pending intake")

    # 3. RF-02 path: run an assessment on the risk; the assessor records
    #    answers contextually — likelihood/impact are ALWAYS chosen
    #    explicitly at scoring time, never inferred from responses (§50)
    template = _template(db, TENANT)
    assessment = AssessmentService(
        db, TENANT, request_id="req-e2e-scoring", actor="qa-lead"
    ).create(
        RiskAssessmentCreate(
            risk_id=risk_id,
            template_id=template.id,
            assessment_type="initial",
            assessor="qa-lead",
        )
    )
    assessment_id = assessment.id
    _answer_all_required(db, assessment_id, template.id)
    detail = AssessmentService(db, TENANT).get_detail(assessment_id)
    assert detail.assessment.status == "in_progress"
    assert detail.completion_percentage == 100

    # 4. score with the seeded framework: likelihood 4 × impact 5, linked
    #    to the assessment → 20 / critical / priority 1, current
    scored = client.post(
        f"{API}/risks/{risk_id}/scores",
        json={
            "framework_id": framework_id,
            "likelihood": 4,
            "impact": 5,
            "assessment_id": str(assessment_id),
        },
    )
    assert scored.status_code == 201, scored.text
    body = scored.json()
    assert body["calculated_score"] == 20
    assert body["effective_score"] == 20
    assert body["severity"] == "critical"
    assert body["priority"] == 1
    assert body["is_current"] is True
    assert body["is_override"] is False
    assert body["source"] == "assessment"
    assert body["assessment_id"] == str(assessment_id)
    assert body["framework"]["name"] == SEEDED_FRAMEWORK
    assert body["framework_version"] == 1

    # 5. register integration: current_score embedded, unscored stays null,
    #    severity and scored filters select the right subsets
    items = client.get(f"{API}/risks").json()["items"]
    by_id = {item["id"]: item for item in items}
    assert by_id[str(risk_id)]["current_score"] is not None
    assert by_id[str(risk_id)]["current_score"]["severity"] == "critical"
    assert by_id[str(unscored_id)]["current_score"] is None  # unscored ≠ low

    critical = client.get(f"{API}/risks", params={"severity": "critical"}).json()
    assert [item["id"] for item in critical["items"]] == [str(risk_id)]
    unscored = client.get(f"{API}/risks", params={"scored": "false"}).json()
    assert [item["id"] for item in unscored["items"]] == [str(unscored_id)]

    # 6. score history: exactly one row, framework snapshot + labels
    history = client.get(f"{API}/risks/{risk_id}/scores").json()
    assert history["total"] == 1
    first = history["items"][0]
    assert first["framework_version"] == 1
    assert first["likelihood_label"] == "Likely"
    assert first["impact_label"] == "Severe"
    assert first["is_current"] is True

    # 7. recalculate → new row, predecessor keeps its numbers and flips off
    recalculated = client.post(f"{API}/risks/{risk_id}/scores/recalculate")
    assert recalculated.status_code == 200, recalculated.text
    history = client.get(f"{API}/risks/{risk_id}/scores").json()
    assert history["total"] == 2
    assert [item["is_current"] for item in history["items"]] == [True, False]
    previous = history["items"][1]
    assert previous["calculated_score"] == 20
    assert previous["severity"] == "critical"
    assert previous["is_override"] is False

    # 8. override with justification → new row preserving the engine result
    overridden = client.post(
        f"{API}/risks/{risk_id}/scores/override",
        json={"override_score": 24, "reason": "Expanded deployment exposure"},
    )
    assert overridden.status_code == 200, overridden.text
    override_body = overridden.json()
    assert override_body["calculated_score"] == 20  # engine result never discarded
    assert override_body["effective_score"] == 24
    assert override_body["is_override"] is True
    assert override_body["override_reason"] == "Expanded deployment exposure"
    assert override_body["severity"] == "critical"  # re-derived from 24
    assert override_body["priority"] == 1
    history = client.get(f"{API}/risks/{risk_id}/scores").json()
    assert history["total"] == 3
    assert [item["is_current"] for item in history["items"]] == [True, False, False]
    # the middle (recalculated) row is untouched by the override
    assert history["items"][1]["override_score"] is None
    assert history["items"][1]["effective_score"] == 20

    # 9. the audit trail tells the scoring story, in order
    assert _score_events(db, risk_id) == [
        "risk.score_created",
        "risk.score_recalculated",
        "risk.score_overridden",
    ]

    # 10. overview: distribution, heat-map cell and top risks driven by the
    #     configured framework (archived/unscored never folded into low)
    overview = client.get(f"{API}/risk-scoring/overview").json()
    assert overview["framework"]["name"] == SEEDED_FRAMEWORK
    assert overview["distribution"] == {
        "critical": 1,
        "high": 0,
        "medium": 0,
        "low": 0,
        "unscored": 1,
    }
    cell = next(c for c in overview["cells"] if c["likelihood"] == 4 and c["impact"] == 5)
    assert cell["count"] == 1
    assert str(risk_id) in cell["risk_ids"]
    assert [top["risk_id"] for top in overview["top_risks"]] == [str(risk_id)]
    assert overview["top_risks"][0]["priority"] == 1

    # 11. regression: RF-01 mutations and RF-02 reads still work
    patched = client.patch(f"{API}/risks/{risk_id}", json={"owner": "priya"})
    assert patched.status_code == 200
    assert patched.json()["owner"] == "priya"
    assert patched.json()["current_score"]["effective_score"] == 24

    reread = AssessmentService(db, TENANT).get_detail(assessment_id)
    assert reread.completion_percentage == 100
    assert len(reread.responses) == sum(
        1 for q in _questions(db, template.id).values() if q.required
    )
    # archived risks still reject scoring with 409
    assert client.delete(f"{API}/risks/{unscored_id}").status_code == 204
    archived_score = client.post(
        f"{API}/risks/{unscored_id}/scores",
        json={"framework_id": framework_id, "likelihood": 1, "impact": 1},
    )
    assert archived_score.status_code == 409
    assert archived_score.json()["error"]["code"] == "RISK_ARCHIVED"


def test_cross_tenant_scoring_isolation(db, client):
    # tenant B stands up its own framework, risk and score
    seed_risk_categories(db, OTHER_TENANT)
    assert seed_scoring_framework(db, OTHER_TENANT) == 1
    db.commit()
    other_risk_id = _risk(db, OTHER_TENANT, "Tenant B's scored risk")
    other_framework = db.scalar(
        sa.select(RiskScoringFramework).where(
            RiskScoringFramework.tenant_id == OTHER_TENANT,
            RiskScoringFramework.name == SEEDED_FRAMEWORK,
        )
    )
    service_b = RiskScoringService(
        db, OTHER_TENANT, request_id="req-e2e-tenant-b", actor="tenant-b"
    )
    service_b.score_risk(
        other_risk_id,
        RiskScoreCreate(framework_id=other_framework.id, likelihood=4, impact=5),
    )
    assert service_b.get_current_score(other_risk_id).calculated_score == 20

    # tenant A's own world
    own_risk_id = _risk(db, TENANT, "Tenant A's risk")
    own_framework_id = _framework_id(client)

    service_a = RiskScoringService(db, TENANT, request_id="req-e2e-tenant-a", actor="tenant-a")
    a_payload = RiskScoreCreate(framework_id=other_framework.id, likelihood=4, impact=5)
    own_payload = RiskScoreCreate(framework_id=UUID(own_framework_id), likelihood=4, impact=5)

    # tenant A cannot read, score, recalculate, override or list tenant B's
    # risk — every path fails at the tenant-scoped risk lookup (404)
    for call in (
        lambda: service_a.get_current_score(other_risk_id),
        lambda: service_a.score_risk(other_risk_id, own_payload),
        lambda: service_a.score_risk(other_risk_id, a_payload),
        lambda: service_a.recalculate_risk(other_risk_id),
        lambda: service_a.override_score(
            other_risk_id, RiskScoreOverrideRequest(override_score=10, reason="nope")
        ),
        lambda: service_a.get_score_history(other_risk_id, RiskScoreFilter()),
    ):
        try:
            call()
            raise AssertionError("cross-tenant scoring access must be a 404")
        except RiskNotFoundError as exc:
            assert exc.http_status == 404

    # tenant A cannot use tenant B's framework, nor B's overview target
    for call in (
        lambda: ScoringFrameworkService(db, TENANT).get(other_framework.id),
        lambda: service_a.score_risk(own_risk_id, a_payload),
        lambda: service_a.get_overview(other_framework.id),
    ):
        try:
            call()
            raise AssertionError("cross-tenant framework access must be a 404")
        except ScoringFrameworkNotFoundError as exc:
            assert exc.http_status == 404

    # the API is bound to the default tenant: tenant B's rows are 404s
    assert client.get(f"{API}/risks/{other_risk_id}").status_code == 404
    assert client.get(f"{API}/risk-scoring/frameworks/{other_framework.id}").status_code == 404
    assert client.get(f"{API}/risks/{other_risk_id}/scores/current").status_code == 404

    # and tenant A never sees B's data in its own register or overview
    tenant_a_ids = {item["id"] for item in client.get(f"{API}/risks").json()["items"]}
    assert str(other_risk_id) not in tenant_a_ids
    assert str(own_risk_id) in tenant_a_ids

    # tenant A's own scoring path still works with A's own framework
    created = service_a.score_risk(own_risk_id, own_payload)
    assert created.calculated_score == 20

    # tenant A's overview shows A's scored risk and never B's rows
    tenant_a_overview = client.get(f"{API}/risk-scoring/overview").json()
    assert str(own_risk_id) in [top["risk_id"] for top in tenant_a_overview["top_risks"]]
    assert str(other_risk_id) not in [
        risk_id for cell in tenant_a_overview["cells"] for risk_id in cell["risk_ids"]
    ]

    # B's history is unchanged by any of A's attempts
    assert service_b.get_score_history(other_risk_id, RiskScoreFilter())[1] == 1
    assert (
        db.scalar(
            sa.select(sa.func.count())
            .select_from(RiskScore)
            .where(RiskScore.tenant_id == OTHER_TENANT)
        )
        == 1
    )

    # an unknown risk reports 404 rather than a fabricated score
    try:
        RiskScoringService(db, TENANT).get_current_score(uuid4())
        raise AssertionError("unknown risk current score must be a 404")
    except RiskNotFoundError as exc:
        assert exc.http_status == 404


def test_unscored_current_score_is_404(client, db):
    """A tenant-A risk with no score reports 404, never a fabricated low."""
    unscored_id = _risk(db, TENANT, "Never scored")
    response = client.get(f"{API}/risks/{unscored_id}/scores/current")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RISK_SCORE_NOT_FOUND"
