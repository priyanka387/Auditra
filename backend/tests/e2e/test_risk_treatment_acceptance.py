"""End-to-end acceptance scenario: RF-01 → RF-02 → RF-03 → RF-04.

Walks the RF-04 acceptance path on top of the three shipped sub-features:
register a risk the RF-01 way, run an assessment the RF-02 way, score it
critical the RF-03 way, choose a mitigation strategy, drive its actions
through 33/66/100% progress, complete the treatment without ever touching
the risk row or its score, record the *expected* residual through the
RF-03 engine, prove the acceptance guard and its dedicated audit event,
and verify the register/overview integration, the ordered audit trail,
cross-tenant isolation, and a full cancellation journey (plan §63–§65).
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
from app.modules.risk_scoring.seed import seed_scoring_framework
from app.modules.risk_treatment.enums import RiskTreatmentStrategy
from app.modules.risk_treatment.errors import RiskTreatmentError
from app.modules.risk_treatment.models import RiskTreatment
from app.modules.risk_treatment.schemas import (
    ResidualAssessmentCreate,
    ResidualAssessmentFilter,
    TreatmentActionCreate,
    TreatmentActionUpdate,
    TreatmentCancelRequest,
    TreatmentCreate,
    TreatmentFilter,
    TreatmentUpdate,
)
from app.modules.risk_treatment.service import (
    RiskTreatmentActionService,
    RiskTreatmentService,
)

TENANT = settings.default_tenant_id
OTHER_TENANT = uuid4()

API = "/api/v1"
RISKS = f"{API}/risks"
TREATMENTS = f"{API}/treatments"
ACTIONS = f"{API}/treatment-actions"


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
    asset = _asset(db, tenant_id, f"treatment-model-{uuid4().hex[:6]}")
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
    service = AssessmentService(db, TENANT, request_id="req-e2e-rf04", actor="qa-lead")
    for question in _questions(db, template_id).values():
        if question.required:
            service.save_response(
                assessment_id,
                question.id,
                AssessmentResponseUpdate(response=_value_for(question), answered_by="qa-lead"),
            )


def _framework_id(client) -> str:
    items = client.get(f"{API}/risk-scoring/frameworks", params={"page_size": 100}).json()["items"]
    return next(item["id"] for item in items if item["name"] == "Standard AI Risk Matrix")


def _error(response) -> dict:
    return response.json()["error"]


def _treatment(client, risk_id: str, **overrides) -> dict:
    payload = {
        "title": "Encrypt training data at rest",
        "strategy": "mitigate",
        "owner": "Security Team",
    }
    payload.update(overrides)
    response = client.post(f"{RISKS}/{risk_id}/treatments", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _action(client, treatment_id: str, **overrides) -> dict:
    payload = {"title": "Restrict production access", "owner": "Security Team"}
    payload.update(overrides)
    response = client.post(f"{TREATMENTS}/{treatment_id}/actions", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _stats(client, treatment_id: str) -> dict:
    response = client.get(f"{TREATMENTS}/{treatment_id}")
    assert response.status_code == 200, response.text
    return response.json()["action_stats"]


def _treatment_service(db, tenant_id: UUID, request_id: str) -> RiskTreatmentService:
    return RiskTreatmentService(db, tenant_id, request_id=request_id, actor=None)


def _action_service(db, tenant_id: UUID, request_id: str) -> RiskTreatmentActionService:
    return RiskTreatmentActionService(db, tenant_id, request_id=request_id, actor=None)


# --- the journey -------------------------------------------------------------


def test_full_risk_treatment_journey(db, client):
    # 1. every system seed is provisioned per tenant and idempotent; the
    #    client fixture already seeded categories, templates and the matrix
    assert seed_scoring_framework(db, TENANT) == 0
    assert seed_assessment_templates(db, TENANT) == 0
    db.commit()
    framework_id = _framework_id(client)

    # 2. RF-01 path: discovered asset + registered risk
    risk_id = _risk(db, TENANT, "Undeclared model in production path")
    risk_str = str(risk_id)

    # 3. RF-02 path (assessment) + RF-03 path (score): likelihood 4 x
    #    impact 5 -> calculated 20, critical, priority 1
    template = _template(db, TENANT)
    assessment = AssessmentService(db, TENANT, request_id="req-e2e-rf04", actor="qa-lead").create(
        RiskAssessmentCreate(
            risk_id=risk_id,
            template_id=template.id,
            assessment_type="initial",
            assessor="qa-lead",
        )
    )
    _answer_all_required(db, assessment.id, template.id)
    scored = client.post(
        f"{RISKS}/{risk_str}/scores",
        json={
            "framework_id": framework_id,
            "likelihood": 4,
            "impact": 5,
            "assessment_id": str(assessment.id),
        },
    )
    assert scored.status_code == 201, scored.text
    assert scored.json()["calculated_score"] == 20
    assert scored.json()["severity"] == "critical"
    assert scored.json()["priority"] == 1

    # 4. RF-04: mitigation strategy with rationale, owner and dates
    treatment = _treatment(
        client,
        risk_str,
        rationale="Reduce exposure before the launch window",
        planned_start_date="2026-12-01",
        target_completion_date="2027-01-15",
    )
    assert treatment["status"] == "not_started"
    treatment_id = treatment["id"]

    # 5. not_started -> planned -> in_progress, three actions, 33% with
    #    one completed (plan §18/§56; remaining = total - completed - cancelled)
    assert (
        client.patch(f"{TREATMENTS}/{treatment_id}", json={"status": "planned"}).json()["status"]
        == "planned"
    )
    assert (
        client.patch(f"{TREATMENTS}/{treatment_id}", json={"status": "in_progress"}).json()[
            "status"
        ]
        == "in_progress"
    )
    restrict = _action(client, treatment_id, title="Restrict production access")
    pii = _action(client, treatment_id, title="Add PII filtering", owner="AppSec")
    review = _action(client, treatment_id, title="Review access logs", owner="AppSec")
    started = client.patch(f"{ACTIONS}/{pii['id']}", json={"status": "in_progress"})
    assert started.status_code == 200, started.text
    assert client.post(f"{ACTIONS}/{restrict['id']}/complete").status_code == 200

    assert _stats(client, treatment_id) == {
        "total_actions": 3,
        "completed_actions": 1,
        "in_progress_actions": 1,
        "cancelled_actions": 0,
        "remaining_actions": 2,
        "progress_percent": 33,
    }

    # 6. finish the rest: 66% then 100%; completion never auto-completes
    #    the treatment (plan §19 — state is not inferred from progress)
    assert client.post(f"{ACTIONS}/{pii['id']}/complete").status_code == 200
    assert _stats(client, treatment_id)["progress_percent"] == 66
    assert _stats(client, treatment_id)["remaining_actions"] == 1
    assert client.post(f"{ACTIONS}/{review['id']}/complete").status_code == 200
    assert _stats(client, treatment_id)["progress_percent"] == 100
    assert _stats(client, treatment_id)["remaining_actions"] == 0
    assert client.get(f"{TREATMENTS}/{treatment_id}").json()["status"] == "in_progress"

    # 7. complete the treatment: risk row and score stay untouched
    #    (plan §20/§21) and completed history leaves the register
    completed = client.post(f"{TREATMENTS}/{treatment_id}/complete")
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    assert completed.json()["completed_at"] is not None

    risk_after = client.get(f"{RISKS}/{risk_str}").json()
    assert risk_after["status"] == "open"
    assert risk_after["current_score"]["calculated_score"] == 20
    assert risk_after["current_score"]["severity"] == "critical"
    assert risk_after["treatment"] is None  # active-treatment summary only

    # 8. expected residual 2 x 4 -> 8 medium, type expected — an
    #    expectation, never a claim about the actual risk (plan §22)
    residual = client.post(
        f"{TREATMENTS}/{treatment_id}/residual-assessments",
        json={"likelihood": 2, "impact": 4},
    )
    assert residual.status_code == 201, residual.text
    assert residual.json()["calculated_score"] == 8
    assert residual.json()["severity"] == "medium"
    assert residual.json()["assessment_type"] == "expected"

    # 9. second risk, scored critical; ACCEPT without a rationale is 400
    #    (plan §25/Ruling 7), with one it is created and audited (§26)
    second_id = _risk(db, TENANT, "Vendor model without contractual safeguards")
    second_str = str(second_id)
    client.post(
        f"{RISKS}/{second_str}/scores",
        json={"framework_id": framework_id, "likelihood": 4, "impact": 5},
    )
    refused = client.post(
        f"{RISKS}/{second_str}/treatments",
        json={"title": "Accept vendor exposure", "strategy": "accept", "owner": "Risk Committee"},
    )
    assert refused.status_code == 400
    assert _error(refused)["code"] == "RISK_TREATMENT_ERROR"

    accepted = client.post(
        f"{RISKS}/{second_str}/treatments",
        json={
            "title": "Accept vendor exposure",
            "strategy": "accept",
            "rationale": "Contractual exposure accepted by the board",
            "owner": "Risk Committee",
        },
    )
    assert accepted.status_code == 201, accepted.text
    accept_id = accepted.json()["id"]
    acceptance_events = list(
        db.scalars(
            sa.select(AuditEvent).where(
                AuditEvent.event_type == "risk.treatment_acceptance_recorded",
                AuditEvent.resource_id == accept_id,
            )
        )
    )
    assert len(acceptance_events) == 1
    assert acceptance_events[0].metadata_["risk_id"] == second_str

    # 10. register integration: summaries on rows, treatment filters, and
    #     the RF-03 severity filter still intact
    items = client.get(RISKS).json()["items"]
    by_id = {item["id"]: item for item in items}
    assert by_id[second_str]["treatment"]["strategy"] == "accept"
    assert by_id[second_str]["treatment"]["status"] == "not_started"
    assert by_id[second_str]["treatment"]["is_overdue"] is False
    assert by_id[risk_str]["treatment"] is None  # completed = history

    untreated = client.get(RISKS, params={"treated": "false"}).json()
    assert [item["id"] for item in untreated["items"]] == [risk_str]
    accept_only = client.get(RISKS, params={"treatment_strategy": "accept"}).json()
    assert [item["id"] for item in accept_only["items"]] == [second_str]
    critical = client.get(RISKS, params={"severity": "critical"}).json()
    assert {item["id"] for item in critical["items"]} == {risk_str, second_str}

    # 11. overview: only ACTIVE treatments count as coverage (Ruling 20) —
    #     the completed mitigation leaves risk 1 untreated+critical
    overview = client.get(f"{TREATMENTS}/overview").json()
    assert overview["total_risks"] == 2
    assert overview["risks_with_treatment"] == 1
    assert overview["risks_without_treatment"] == 1
    assert overview["status_distribution"] == {
        "not_started": 1,
        "planned": 0,
        "in_progress": 0,
        "completed": 1,
        "cancelled": 0,
    }
    assert overview["strategy_distribution"] == {
        "mitigate": 1,
        "accept": 1,
        "avoid": 0,
        "transfer": 0,
    }
    assert overview["overdue_actions"] == 0
    assert overview["overdue_treatments"] == 0
    assert overview["untreated_critical"] == 1
    assert overview["untreated_high"] == 0

    # 12. the ordered audit trail tells the whole treatment story
    #     (action rows log under their own resource, Ruling 11)
    trail = [
        str(event.event_type)
        for event in db.scalars(
            sa.select(AuditEvent)
            .where(AuditEvent.event_type.like("risk.treatment%"))
            .order_by(AuditEvent.sequence_no)
        )
    ]
    assert trail == [
        "risk.treatment_created",
        "risk.treatment_updated",  # PATCH planned
        "risk.treatment_updated",  # PATCH in_progress
        "risk.treatment_action_created",
        "risk.treatment_action_created",
        "risk.treatment_action_created",
        "risk.treatment_action_updated",  # PII filtering starts
        "risk.treatment_action_completed",
        "risk.treatment_action_completed",
        "risk.treatment_action_completed",
        "risk.treatment_completed",
        "risk.treatment_residual_assessment_created",
        "risk.treatment_created",  # the accept decision
        "risk.treatment_acceptance_recorded",
    ]

    # 13. regression: RF-01 mutations, RF-02 reads and RF-03 recalculate
    #     all still work with treatments in the picture
    patched = client.patch(f"{RISKS}/{risk_str}", json={"owner": "priya"})
    assert patched.status_code == 200
    assert patched.json()["owner"] == "priya"

    reread = AssessmentService(db, TENANT).get_detail(assessment.id)
    assert reread.completion_percentage == 100

    recalculated = client.post(f"{RISKS}/{risk_str}/scores/recalculate")
    assert recalculated.status_code == 200, recalculated.text
    assert recalculated.json()["effective_score"] == 20
    assert recalculated.json()["severity"] == "critical"


def test_cross_tenant_treatment_isolation(db, client):
    # tenant B stands up its own risk, treatment, action and residual
    seed_risk_categories(db, OTHER_TENANT)
    assert seed_scoring_framework(db, OTHER_TENANT) == 1
    db.commit()
    b_risk = _risk(db, OTHER_TENANT, "Tenant B's risk")
    b_treatment = _treatment_service(db, OTHER_TENANT, "req-e2e-tenant-b").create(
        b_risk,
        TreatmentCreate(
            title="B's mitigation", strategy=RiskTreatmentStrategy.MITIGATE, owner="B Security"
        ),
    )
    b_action = _action_service(db, OTHER_TENANT, "req-e2e-tenant-b").create(
        b_treatment.id, TreatmentActionCreate(title="B's action", owner="B Security")
    )
    b_residual = _treatment_service(db, OTHER_TENANT, "req-e2e-tenant-b").create_residual(
        b_treatment.id, ResidualAssessmentCreate(likelihood=2, impact=4)
    )
    assert b_residual.severity == "medium"

    # tenant A cannot read, mutate, complete, cancel or list tenant B's
    # treatments/actions/residuals — every path is a tenant-scoped 404
    service_a = _treatment_service(db, TENANT, "req-e2e-tenant-a")
    actions_a = _action_service(db, TENANT, "req-e2e-tenant-a")
    for call in (
        lambda: service_a.get(b_treatment.id),
        lambda: service_a.update(b_treatment.id, TreatmentUpdate(owner="A")),
        lambda: service_a.complete(b_treatment.id),
        lambda: service_a.cancel(b_treatment.id, TreatmentCancelRequest(reason="nope")),
        lambda: service_a.list_residual(b_treatment.id, ResidualAssessmentFilter()),
        lambda: service_a.create_residual(
            b_treatment.id, ResidualAssessmentCreate(likelihood=1, impact=1)
        ),
        lambda: actions_a.get(b_action.id),
        lambda: actions_a.update(b_action.id, TreatmentActionUpdate(owner="A")),
        lambda: actions_a.complete(b_action.id),
        lambda: actions_a.cancel(b_action.id, TreatmentCancelRequest(reason="nope")),
    ):
        try:
            call()
            raise AssertionError("cross-tenant treatment access must be a 404")
        except RiskTreatmentError as exc:
            assert exc.http_status == 404

    # and tenant A cannot create a treatment on tenant B's risk at all
    for call in (
        lambda: service_a.list_by_risk(b_risk, TreatmentFilter()),
        lambda: service_a.create(
            b_risk,
            TreatmentCreate(title="hijack", strategy=RiskTreatmentStrategy.MITIGATE, owner="A"),
        ),
    ):
        try:
            call()
            raise AssertionError("cross-tenant risk access must be a 404")
        except RiskNotFoundError as exc:
            assert exc.http_status == 404

    # the API is bound to the default tenant: B's rows are 404s
    assert client.get(f"{TREATMENTS}/{b_treatment.id}").status_code == 404
    assert client.get(f"{ACTIONS}/{b_action.id}").status_code == 404
    refused = client.post(
        f"{RISKS}/{b_risk}/treatments",
        json={"title": "hijack", "strategy": "mitigate", "owner": "A"},
    )
    assert refused.status_code == 404

    # tenant A's register and overview never see B's world...
    assert client.get(RISKS).json()["items"] == []
    overview = client.get(f"{TREATMENTS}/overview").json()
    assert overview["total_risks"] == 0
    assert overview["risks_with_treatment"] == 0

    # ...while B's rows stay intact and untouched by every A attempt
    assert _treatment_service(db, OTHER_TENANT, "req-e2e-check").get(b_treatment.id).status == (
        "not_started"
    )
    b_treatment_count = db.scalar(
        sa.select(sa.func.count())
        .select_from(RiskTreatment)
        .where(RiskTreatment.tenant_id == OTHER_TENANT)
    )
    assert b_treatment_count == 1


def test_treatment_cancellation_journey(db, client):
    risk_id = _risk(db, TENANT, "Programme cancelled upstream")
    risk_str = str(risk_id)

    treatment = _treatment(client, risk_str, strategy="mitigate", owner="Security Team")
    treatment_id = treatment["id"]
    action = _action(client, treatment_id)

    cancelled_action = client.post(
        f"{ACTIONS}/{action['id']}/cancel", json={"reason": "Scope moved to platform team"}
    )
    assert cancelled_action.status_code == 200, cancelled_action.text
    assert cancelled_action.json()["status"] == "cancelled"
    assert cancelled_action.json()["cancellation_reason"] == "Scope moved to platform team"
    assert cancelled_action.json()["cancelled_at"] is not None

    cancelled = client.post(
        f"{TREATMENTS}/{treatment_id}/cancel",
        json={"reason": "Risk will be accepted at board level instead"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["cancellation_reason"] == "Risk will be accepted at board level instead"
    assert cancelled.json()["cancelled_at"] is not None

    # both reasons are stored on the rows and carried in the audit events
    action_trail = list(
        db.scalars(
            sa.select(AuditEvent.event_type)
            .where(AuditEvent.resource_id == str(action["id"]))
            .order_by(AuditEvent.sequence_no)
        )
    )
    assert [str(event) for event in action_trail] == [
        "risk.treatment_action_created",
        "risk.treatment_action_cancelled",
    ]
    treatment_trail = list(
        db.scalars(
            sa.select(AuditEvent.event_type)
            .where(AuditEvent.resource_id == treatment_id)
            .order_by(AuditEvent.sequence_no)
        )
    )
    assert [str(event) for event in treatment_trail] == [
        "risk.treatment_created",
        "risk.treatment_cancelled",
    ]
    cancel_event = db.scalar(
        sa.select(AuditEvent).where(AuditEvent.event_type == "risk.treatment_cancelled")
    )
    assert cancel_event.metadata_["reason"] == "Risk will be accepted at board level instead"

    # the risk stays register-visible and reads as untreated: cancellation
    # is history, not coverage (plan §21 — the risk row is never touched)
    items = client.get(RISKS).json()["items"]
    assert [item["id"] for item in items] == [risk_str]
    assert items[0]["treatment"] is None
    assert items[0]["status"] == "open"
    untreated = client.get(RISKS, params={"treated": "false"}).json()
    assert [item["id"] for item in untreated["items"]] == [risk_str]
    assert client.get(f"{TREATMENTS}/{treatment_id}").json()["status"] == "cancelled"
