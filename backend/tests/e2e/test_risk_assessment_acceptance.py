"""End-to-end acceptance scenario: RF-01 Risk Register → RF-02 Assessment.

Walks the RF-02 §47/§48 acceptance path: seed the system questionnaire →
register an asset and risk the RF-01 way → run an assessment through the
full lifecycle (answer → submit → review → approve) → prove resumability,
per-risk history, rejection + revision chains, the ordered audit trail,
list filters, and cross-tenant isolation.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa

from app.core.config import settings
from app.modules.assessment.errors import (
    AssessmentInvalidStateError,
    AssessmentNotFoundError,
    AssessmentTemplateNotFoundError,
)
from app.modules.assessment.models import (
    AssessmentQuestion,
    AssessmentResponse,
    AssessmentSection,
    AssessmentTemplate,
    RiskAssessment,
)
from app.modules.assessment.schemas import (
    AssessmentRejectRequest,
    AssessmentResponseUpdate,
    RiskAssessmentCreate,
    RiskAssessmentFilter,
)
from app.modules.assessment.seed import (
    GENERAL_AI_RISK_ASSESSMENT_KEY,
    seed_assessment_templates,
)
from app.modules.assessment.service import AssessmentService, AssessmentTemplateService
from app.modules.asset_discovery.models import DiscoveredAsset, DiscoverySource
from app.modules.audit.models import AuditEvent
from app.modules.risk.errors import RiskNotFoundError
from app.modules.risk.models import RiskCategory
from app.modules.risk.schemas import RiskCreate
from app.modules.risk.seed import seed_risk_categories
from app.modules.risk.service import RiskService

TENANT = settings.default_tenant_id
OTHER_TENANT = uuid4()


# --- setup helpers -------------------------------------------------------------


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


def _risk(db, tenant_id: UUID) -> UUID:
    """Risk through the RF-01 path: seeded category + discovered asset."""
    seed_risk_categories(db, tenant_id)
    db.commit()
    category = db.scalar(
        sa.select(RiskCategory).where(
            RiskCategory.tenant_id == tenant_id,
            RiskCategory.slug == "security",
        )
    )
    asset = _asset(db, tenant_id, f"acceptance-model-{uuid4().hex[:6]}")
    risk = RiskService(db, tenant_id).create(
        RiskCreate(
            title="Undeclared model in production path",
            description="Found without a registered owner",
            category_id=category.id,
            asset_id=asset.id,
            source="asset_discovery",
        )
    )
    return risk.id


def _service(db, tenant_id: UUID | None = None) -> AssessmentService:
    return AssessmentService(
        db,
        tenant_id or TENANT,
        request_id="req-e2e",
        actor="qa-lead",
    )


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
    service = _service(db)
    for question in _questions(db, template_id).values():
        if question.required:
            service.save_response(
                assessment_id,
                question.id,
                AssessmentResponseUpdate(response=_value_for(question), answered_by="qa-lead"),
            )


def _events_for(db, assessment_id: UUID) -> list[str]:
    rows = db.scalars(
        sa.select(AuditEvent)
        .where(
            AuditEvent.resource_type == "assessment",
            AuditEvent.resource_id == str(assessment_id),
        )
        .order_by(AuditEvent.sequence_no)
    )
    return [str(row.event_type) for row in rows]


# --- the journey ----------------------------------------------------------------


def test_full_risk_assessment_journey(db, client):
    # 1. the system questionnaire is provisioned per tenant (idempotent);
    #    the client fixture already seeded it for the default tenant
    assert seed_assessment_templates(db, TENANT) == 0
    db.commit()
    seed_risk_categories(db, TENANT)
    db.commit()
    template = _template(db, TENANT)
    assert template is not None and template.is_system and template.version == 1

    # 2. RF-01 path: discovered asset + registered risk
    risk_id = _risk(db, TENANT)

    # 3. start an assessment against the risk with the system template
    first = _service(db).create(
        RiskAssessmentCreate(
            risk_id=risk_id,
            template_id=template.id,
            assessment_type="initial",
            assessor="qa-lead",
        )
    )
    first_id = first.id
    assert first.status == "draft"
    assert first.template_version == 1

    # 4. answer every required question (mixed types); progress → 100
    _answer_all_required(db, first_id, template.id)
    detail = _service(db).get_detail(first_id)
    assert detail.assessment.status == "in_progress"
    assert detail.completion_percentage == 100
    assert "assessment.started" in _events_for(db, first_id)

    # 5. leave and resume: a fresh service instance sees every answer
    resumed = AssessmentService(db, TENANT, request_id="req-resume").get_detail(first_id)
    assert resumed.completion_percentage == 100
    assert len(resumed.responses) == sum(
        1 for q in _questions(db, template.id).values() if q.required
    )

    # 6. submit: the door closes on edits
    submitted = _service(db).submit(first_id)
    assert submitted.status == "submitted"
    assert submitted.submitted_at is not None
    purpose = _questions(db, template.id)["purpose"]
    try:
        _service(db).save_response(
            first_id, purpose.id, AssessmentResponseUpdate(response="late edit")
        )
        raise AssertionError("editing a submitted assessment must fail")
    except AssessmentInvalidStateError as exc:
        assert exc.http_status == 409

    # 7. review → approve: terminal state
    assert _service(db).start_review(first_id).status == "under_review"
    approved = _service(db).approve(first_id, None)
    approved_snapshot = (approved.status, approved.reviewer, approved.approved_at)
    assert approved.status == "approved"
    assert approved.reviewed_at is not None and approved.approved_at is not None
    try:
        _service(db).save_response(
            first_id, purpose.id, AssessmentResponseUpdate(response="tamper")
        )
        raise AssertionError("editing an approved assessment must fail")
    except AssessmentInvalidStateError as exc:
        assert exc.http_status == 409

    # 8. a second assessment on the same risk is history, not an overwrite
    second = _service(db).create(
        RiskAssessmentCreate(
            risk_id=risk_id,
            template_id=template.id,
            assessment_type="periodic",
            assessor="qa-lead",
        )
    )
    second_id = second.id
    rows = list(db.scalars(sa.select(RiskAssessment).where(RiskAssessment.risk_id == risk_id)))
    assert len(rows) == 2
    assert {row.id for row in rows} == {first_id, second_id}
    first_after = db.get(RiskAssessment, first_id)
    assert (first_after.status, first_after.reviewer, first_after.approved_at) == (
        approved_snapshot
    )

    # 9. rejection with reason, then a revision chain (§32/§33)
    _answer_all_required(db, second_id, template.id)
    _service(db).submit(second_id)
    _service(db).start_review(second_id)
    _service(db).reject(
        second_id,
        AssessmentRejectRequest(reason="Missing oversight detail"),
    )
    assert db.get(RiskAssessment, second_id).rejection_reason == "Missing oversight detail"

    third = _service(db).create_revision(second_id)  # third row: revision 2
    third_id = third.id
    assert third.status == "draft"
    assert third.revision_number == 2
    assert third.original_assessment_id == second_id
    _service(db).submit(third_id)
    _service(db).start_review(third_id)
    _service(db).reject(third_id, AssessmentRejectRequest(reason="Still no oversight owner"))

    revision = _service(db).create_revision(third_id)  # fourth row: revision 3
    revision_id = revision.id
    assert revision.status == "draft"
    assert revision.revision_number == 3
    assert revision.original_assessment_id == third_id
    assert revision.submitted_at is None and revision.rejection_reason is None

    copied = list(
        db.scalars(
            sa.select(AssessmentResponse).where(AssessmentResponse.assessment_id == revision_id)
        )
    )
    original = list(
        db.scalars(
            sa.select(AssessmentResponse).where(AssessmentResponse.assessment_id == third_id)
        )
    )
    assert len(copied) == len(original) > 0
    assert {(r.question_id, str(r.response_value)) for r in copied} == {
        (r.question_id, str(r.response_value)) for r in original
    }
    untouched = db.get(RiskAssessment, third_id)
    assert untouched.status == "rejected"
    assert untouched.rejection_reason == "Still no oversight owner"

    # 10. the audit trail tells every story, in order — answers append
    #     one response_updated event each, between started and submitted
    def lifecycle(assessment_id):
        return [
            event
            for event in _events_for(db, assessment_id)
            if event != "assessment.response_updated"
        ]

    first_trail = _events_for(db, first_id)
    assert lifecycle(first_id) == [
        "assessment.created",
        "assessment.started",
        "assessment.submitted",
        "assessment.review_started",
        "assessment.approved",
    ]
    assert first_trail.count("assessment.response_updated") == 9
    answer_positions = [
        i for i, event in enumerate(first_trail) if event == "assessment.response_updated"
    ]
    assert all(
        first_trail.index("assessment.started") < i < first_trail.index("assessment.submitted")
        for i in answer_positions
    )

    second_trail = _events_for(db, second_id)
    assert lifecycle(second_id) == [
        "assessment.created",
        "assessment.started",
        "assessment.submitted",
        "assessment.review_started",
        "assessment.rejected",
    ]
    assert second_trail.count("assessment.response_updated") == 9

    # revisions copy answers silently — no response_updated events
    assert _events_for(db, third_id) == [
        "assessment.revised",
        "assessment.submitted",
        "assessment.review_started",
        "assessment.rejected",
    ]
    assert _events_for(db, revision_id) == ["assessment.revised"]

    # 11. list filters return the expected subsets
    service = _service(db)
    on_risk, risk_total = service.list(RiskAssessmentFilter(risk_id=risk_id))
    assert risk_total == 4
    assert {row.id for row in on_risk} == {first_id, second_id, third_id, revision_id}

    approved_rows, approved_total = service.list(RiskAssessmentFilter(status="approved"))
    assert approved_total == 1
    assert approved_rows[0].id == first_id

    draft_rows, draft_total = service.list(RiskAssessmentFilter(status="draft"))
    assert draft_total == 1
    assert draft_rows[0].id == revision_id

    periodic_rows, _ = service.list(RiskAssessmentFilter(assessment_type="periodic"))
    # the periodic lineage: second, its revision and the revision's revision
    assert {row.id for row in periodic_rows} == {second_id, third_id, revision_id}


def test_cross_tenant_assessment_isolation(db):
    # tenant B stands up its own risk + template + assessment
    other_risk_id = _risk(db, OTHER_TENANT)
    seed_assessment_templates(db, OTHER_TENANT)
    db.commit()
    other_template = _template(db, OTHER_TENANT)
    foreign = _service(db, OTHER_TENANT).create(
        RiskAssessmentCreate(
            risk_id=other_risk_id,
            template_id=other_template.id,
            assessor="tenant-b-assessor",
        )
    )
    foreign_id = foreign.id

    # tenant A cannot see any of it (unknown and cross-tenant are equal)
    service_a = _service(db, TENANT)
    for call in (
        lambda: service_a.get(foreign_id),
        lambda: service_a.get_detail(foreign_id),
        lambda: service_a.submit(foreign_id),
        lambda: service_a.save_response(
            foreign_id,
            uuid4(),
            AssessmentResponseUpdate(response="smuggled"),
        ),
        lambda: AssessmentService(db, TENANT).get_detail(foreign_id),
    ):
        try:
            call()
            raise AssertionError("cross-tenant access must be a 404")
        except AssessmentNotFoundError as exc:
            assert exc.http_status == 404

    try:
        AssessmentTemplateService(db, TENANT).get(other_template.id)
        raise AssertionError("cross-tenant template must be a 404")
    except AssessmentTemplateNotFoundError as exc:
        assert exc.http_status == 404

    try:
        RiskService(db, TENANT).get(other_risk_id)
        raise AssertionError("cross-tenant risk must be a 404")
    except RiskNotFoundError as exc:
        assert exc.http_status == 404

    # tenant B's own view still works
    assert _service(db, OTHER_TENANT).get(foreign_id).id == foreign_id
