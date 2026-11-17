"""Integration tests: RF-02 assessment service — state machine, validation, tenancy, audit."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from pydantic import ValidationError

from app.core.config import settings
from app.modules.assessment.enums import AssessmentStatus, AssessmentType
from app.modules.assessment.errors import (
    AssessmentIncompleteError,
    AssessmentInvalidStateError,
    AssessmentNotFoundError,
    AssessmentQuestionNotFoundError,
    AssessmentTemplateInactiveError,
    AssessmentTemplateNotFoundError,
    InvalidAssessmentQueryError,
    InvalidAssessmentResponseError,
)
from app.modules.assessment.models import (
    AssessmentQuestion,
    AssessmentResponse,
    AssessmentSection,
    AssessmentTemplate,
    RiskAssessment,
)
from app.modules.assessment.schemas import (
    AssessmentApproveRequest,
    AssessmentRejectRequest,
    AssessmentResponseUpdate,
    AssessmentTemplateUpdate,
    RiskAssessmentCreate,
    RiskAssessmentFilter,
)
from app.modules.assessment.seed import seed_assessment_templates
from app.modules.assessment.service import AssessmentService, AssessmentTemplateService
from app.modules.asset_discovery.models import DiscoveredAsset, DiscoverySource
from app.modules.audit.models import AuditEvent
from app.modules.risk.errors import RiskArchivedError, RiskNotFoundError
from app.modules.risk.models import Risk, RiskCategory
from app.modules.risk.seed import seed_risk_categories

TENANT = settings.default_tenant_id
OTHER_TENANT = uuid4()


# --- fixtures & helpers ------------------------------------------------------


def _category(db, tenant_id: UUID) -> RiskCategory:
    seed_risk_categories(db, tenant_id)
    db.commit()
    return db.scalar(
        sa.select(RiskCategory).where(
            RiskCategory.tenant_id == tenant_id,
            RiskCategory.slug == "security",
        )
    )


def _make_asset(db, tenant_id: UUID, name: str) -> DiscoveredAsset:
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


def _make_risk(
    db,
    tenant_id: UUID,
    *,
    title: str = "Unreviewed model in payments",
    asset_id: UUID | None = None,
    archived: bool = False,
) -> Risk:
    category = _category(db, tenant_id)
    risk = Risk(
        id=uuid4(),
        tenant_id=tenant_id,
        title=title,
        category_id=category.id,
        source="manual",
        asset_id=asset_id,
        status="open",
    )
    if archived:
        risk.archived_at = datetime.now(UTC)
    db.add(risk)
    db.commit()
    return risk


def _template_for(db, tenant_id: UUID) -> AssessmentTemplate:
    seed_assessment_templates(db, tenant_id)
    db.commit()
    return db.scalar(
        sa.select(AssessmentTemplate).where(
            AssessmentTemplate.tenant_id == tenant_id,
            AssessmentTemplate.key == "general-ai-risk-assessment",
        )
    )


@pytest.fixture
def template(db) -> AssessmentTemplate:
    return _template_for(db, TENANT)


@pytest.fixture
def other_tenant_template(db) -> AssessmentTemplate:
    return _template_for(db, OTHER_TENANT)


@pytest.fixture
def risk(db) -> Risk:
    asset = _make_asset(db, TENANT, "payments-gpt-4o")
    return _make_risk(db, TENANT, asset_id=asset.id)


@pytest.fixture
def other_risk(db) -> Risk:
    asset = _make_asset(db, TENANT, "support-claude")
    return _make_risk(db, TENANT, title="Support assistant exposure", asset_id=asset.id)


@pytest.fixture
def other_tenant_risk(db) -> Risk:
    asset = _make_asset(db, OTHER_TENANT, "other-tenant-model")
    return _make_risk(db, OTHER_TENANT, asset_id=asset.id)


def _service(db, tenant_id=None) -> AssessmentService:
    return AssessmentService(
        db,
        tenant_id=tenant_id or TENANT,
        request_id="req-asmt-01",
        actor="user-7",
    )


def _create(db, risk_row, template_row, **overrides) -> RiskAssessment:
    payload = {"risk_id": risk_row.id, "template_id": template_row.id}
    payload.update(overrides)
    return _service(db).create(RiskAssessmentCreate(**payload))


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
        return False
    if kind == "number":
        return 3
    if kind == "single_select":
        return question.options[0]["value"]
    if kind == "multi_select":
        return [question.options[0]["value"]]
    return "answer"


def _answer_required(db, assessment: RiskAssessment) -> dict[str, AssessmentQuestion]:
    """Answer every required question through the service (draft → in_progress)."""
    service = _service(db)
    questions = _questions(db, assessment.template_id)
    for question in questions.values():
        if question.required:
            service.save_response(
                assessment.id,
                question.id,
                AssessmentResponseUpdate(response=_value_for(question), answered_by="priya"),
            )
    return questions


def _submitted(db, risk_row, template_row) -> RiskAssessment:
    assessment = _create(db, risk_row, template_row)
    _answer_required(db, assessment)
    return _service(db).submit(assessment.id)


def _under_review(db, risk_row, template_row) -> RiskAssessment:
    assessment = _submitted(db, risk_row, template_row)
    return _service(db).start_review(assessment.id)


def _approved(db, risk_row, template_row) -> RiskAssessment:
    assessment = _under_review(db, risk_row, template_row)
    return _service(db).approve(assessment.id, AssessmentApproveRequest(reviewer="reviewer-1"))


def _events(db, resource_id) -> list[AuditEvent]:
    stmt = (
        sa.select(AuditEvent)
        .where(
            AuditEvent.resource_type == "assessment",
            AuditEvent.resource_id == str(resource_id),
        )
        .order_by(AuditEvent.sequence_no)
    )
    return list(db.scalars(stmt))


def _event_types(db, resource_id) -> list[str]:
    return [str(event.event_type) for event in _events(db, resource_id)]


# --- create -----------------------------------------------------------------


def test_create_assessment_persists_and_snapshots_template_version(db, risk, template):
    created = _create(db, risk, template, assessor="  priya  ")
    created_id = created.id
    assert created.tenant_id == TENANT
    assert created.risk_id == risk.id
    assert created.template_id == template.id
    assert created.template_version == template.version
    assert created.status == "draft"
    assert created.revision_number == 1
    assert created.assessor == "priya"
    assert created.original_assessment_id is None
    db.expunge_all()

    row = db.get(RiskAssessment, created_id)
    assert row.status == "draft"
    events = _events(db, created_id)
    assert [str(e.event_type) for e in events] == ["assessment.created"]
    assert events[0].resource_type == "assessment"
    assert events[0].after_state["status"] == "draft"


def test_create_assessment_with_unknown_risk_returns_404(db, template):
    with pytest.raises(RiskNotFoundError) as exc:
        _service(db).create(RiskAssessmentCreate(risk_id=uuid4(), template_id=template.id))
    assert exc.value.http_status == 404


def test_create_assessment_with_cross_tenant_risk_returns_404(db, other_tenant_risk, template):
    # tenant A referencing tenant B's risk is indistinguishable from unknown
    with pytest.raises(RiskNotFoundError):
        _service(db).create(
            RiskAssessmentCreate(risk_id=other_tenant_risk.id, template_id=template.id)
        )


def test_create_assessment_with_unknown_template_returns_404(db, risk):
    with pytest.raises(AssessmentTemplateNotFoundError):
        _service(db).create(RiskAssessmentCreate(risk_id=risk.id, template_id=uuid4()))


def test_create_assessment_with_cross_tenant_template_returns_404(db, risk, other_tenant_template):
    with pytest.raises(AssessmentTemplateNotFoundError):
        _service(db).create(
            RiskAssessmentCreate(risk_id=risk.id, template_id=other_tenant_template.id)
        )


def test_create_assessment_with_inactive_template_returns_409(db, risk, template):
    template_id = template.id
    AssessmentTemplateService(db, TENANT).update(
        template_id, AssessmentTemplateUpdate(is_active=False)
    )
    with pytest.raises(AssessmentTemplateInactiveError) as exc:
        _service(db).create(RiskAssessmentCreate(risk_id=risk.id, template_id=template_id))
    assert exc.value.http_status == 409


def test_create_assessment_on_archived_risk_returns_409(db, template):
    asset = _make_asset(db, TENANT, "archived-model")
    archived = _make_risk(db, TENANT, title="Archived risk", asset_id=asset.id, archived=True)
    with pytest.raises(RiskArchivedError) as exc:
        _service(db).create(RiskAssessmentCreate(risk_id=archived.id, template_id=template.id))
    assert exc.value.http_status == 409


def test_create_multiple_assessments_on_one_risk_preserved(db, risk, template):
    first = _create(db, risk, template)
    second = _create(db, risk, template)
    third = _create(db, risk, template)
    ids = {first.id, second.id, third.id}
    assert len(ids) == 3
    rows = db.scalars(
        sa.select(RiskAssessment).where(
            RiskAssessment.tenant_id == TENANT,
            RiskAssessment.risk_id == risk.id,
        )
    )
    assert len(list(rows)) == 3


# --- read -------------------------------------------------------------------


def test_get_assessment_from_other_tenant_returns_404(db, other_tenant_risk, other_tenant_template):
    foreign_service = _service(db, tenant_id=OTHER_TENANT)
    created = foreign_service.create(
        RiskAssessmentCreate(risk_id=other_tenant_risk.id, template_id=other_tenant_template.id)
    )
    foreign_id = created.id

    with pytest.raises(AssessmentNotFoundError) as exc:
        _service(db).get(foreign_id)
    assert exc.value.http_status == 404


def test_get_detail_returns_sections_questions_and_completion(db, risk, template):
    assessment = _create(db, risk, template)
    questions = _questions(db, template.id)
    _service(db).save_response(
        assessment.id,
        questions["purpose"].id,
        AssessmentResponseUpdate(response="Fraud triage assistant"),
    )

    detail = _service(db).get_detail(assessment.id)
    assert detail.assessment.id == assessment.id
    assert detail.risk.id == risk.id
    assert detail.template.id == template.id
    assert [section.display_order for section in detail.sections] == list(range(1, 8))
    all_questions = [q for group in detail.questions_by_section.values() for q in group]
    assert len(all_questions) == 14
    purpose = next(q for q in all_questions if q.key == "purpose")
    assert purpose.id in detail.responses
    assert detail.responses[purpose.id].response_value == "Fraud triage assistant"
    assert detail.completion_percentage == round(100 / 9)


# --- save_response -----------------------------------------------------------


def test_save_response_moves_draft_to_in_progress(db, risk, template):
    assessment = _create(db, risk, template)
    assert db.get(RiskAssessment, assessment.id).status == "draft"
    question = _questions(db, template.id)["purpose"]

    _service(db).save_response(
        assessment.id,
        question.id,
        AssessmentResponseUpdate(response="Triage", answered_by="priya"),
    )

    assert db.get(RiskAssessment, assessment.id).status == "in_progress"
    types = _event_types(db, assessment.id)
    assert "assessment.started" in types
    assert "assessment.response_updated" in types

    started = next(
        e for e in _events(db, assessment.id) if str(e.event_type) == "assessment.started"
    )
    assert started.before_state["status"] == "draft"
    assert started.after_state["status"] == "in_progress"

    updated = next(
        e for e in _events(db, assessment.id) if str(e.event_type) == "assessment.response_updated"
    )
    assert updated.changed_fields == ["responses"]
    assert updated.metadata_["question_key"] == "purpose"


def test_save_response_upserts_and_bumps_updated_at(db, risk, template):
    assessment = _create(db, risk, template)
    question = _questions(db, template.id)["purpose"]
    service = _service(db)

    service.save_response(
        assessment.id, question.id, AssessmentResponseUpdate(response="first answer")
    )
    first = db.scalar(
        sa.select(AssessmentResponse).where(
            AssessmentResponse.assessment_id == assessment.id,
            AssessmentResponse.question_id == question.id,
        )
    )
    first_updated_at = first.updated_at

    service.save_response(
        assessment.id, question.id, AssessmentResponseUpdate(response="second answer")
    )
    rows = list(
        db.scalars(
            sa.select(AssessmentResponse).where(AssessmentResponse.assessment_id == assessment.id)
        )
    )
    assert len(rows) == 1
    assert rows[0].response_value == "second answer"
    assert rows[0].updated_at >= first_updated_at


def test_save_response_with_wrong_type_rejected(db, risk, template):
    assessment = _create(db, risk, template)
    questions = _questions(db, template.id)
    service = _service(db)

    wrong_values = {
        "governance-review": "3",  # NUMBER rejects a numeric string
        "human-oversight": 1,  # BOOLEAN rejects an int (strict bool)
        "impact-severity": "bogus",  # SINGLE_SELECT rejects unknown option
        "purpose": "x" * 1001,  # TEXT rejects oversize input
        "data-types": ["not_an_option"],  # MULTI_SELECT rejects unknown option
    }
    for key, value in wrong_values.items():
        with pytest.raises(InvalidAssessmentResponseError) as exc:
            service.save_response(
                assessment.id,
                questions[key].id,
                AssessmentResponseUpdate(response=value),
            )
        assert exc.value.http_status == 400

    # nothing was persisted for any rejected answer
    assert (
        db.scalar(
            sa.select(sa.func.count())
            .select_from(AssessmentResponse)
            .where(AssessmentResponse.assessment_id == assessment.id)
        )
        == 0
    )


def test_save_response_for_foreign_question_returns_404(db, risk, template, other_tenant_template):
    assessment = _create(db, risk, template)
    foreign_question = next(iter(_questions(db, other_tenant_template.id).values()))

    with pytest.raises(AssessmentQuestionNotFoundError) as exc:
        _service(db).save_response(
            assessment.id,
            foreign_question.id,
            AssessmentResponseUpdate(response="smuggled answer"),
        )
    assert exc.value.http_status == 404


def test_save_response_on_submitted_assessment_returns_409(db, risk, template):
    assessment = _submitted(db, risk, template)
    question = _questions(db, template.id)["purpose"]

    with pytest.raises(AssessmentInvalidStateError) as exc:
        _service(db).save_response(
            assessment.id, question.id, AssessmentResponseUpdate(response="late edit")
        )
    assert exc.value.http_status == 409
    assert "submitted" in str(exc.value)


def test_save_response_on_approved_assessment_returns_409(db, risk, template):
    assessment = _approved(db, risk, template)
    question = _questions(db, template.id)["purpose"]

    with pytest.raises(AssessmentInvalidStateError) as exc:
        _service(db).save_response(
            assessment.id, question.id, AssessmentResponseUpdate(response="tamper")
        )
    assert exc.value.http_status == 409
    assert "approved" in str(exc.value)


# --- submit -----------------------------------------------------------------


def test_submit_requires_all_required_questions(db, risk, template):
    assessment = _create(db, risk, template)
    questions = _questions(db, template.id)
    _service(db).save_response(
        assessment.id, questions["purpose"].id, AssessmentResponseUpdate(response="Triage")
    )

    with pytest.raises(AssessmentIncompleteError) as exc:
        _service(db).submit(assessment.id)
    assert exc.value.http_status == 409
    message = str(exc.value)
    assert "purpose" not in message  # answered questions are not "missing"
    assert "data-types" in message  # a missing required key is named
    assert db.get(RiskAssessment, assessment.id).status == "in_progress"


def test_submit_sets_submitted_timestamp_and_status(db, risk, template):
    assessment = _create(db, risk, template)
    _answer_required(db, assessment)

    submitted = _service(db).submit(assessment.id)
    assert submitted.status == "submitted"
    assert submitted.submitted_at is not None

    fresh = db.get(RiskAssessment, assessment.id)
    assert fresh.status == "submitted"
    assert fresh.submitted_at is not None
    assert "assessment.submitted" in _event_types(db, assessment.id)


def test_submit_allowed_from_draft_when_complete(db, risk, template):
    assessment = _create(db, risk, template)
    _answer_required(db, assessment)
    # every required answer exists; flip the status back to draft to prove
    # submission does not depend on having passed through in_progress
    row = db.get(RiskAssessment, assessment.id)
    row.status = "draft"
    db.commit()

    submitted = _service(db).submit(assessment.id)
    assert submitted.status == "submitted"


def test_submit_twice_returns_409(db, risk, template):
    assessment = _submitted(db, risk, template)
    with pytest.raises(AssessmentInvalidStateError):
        _service(db).submit(assessment.id)


# --- completion --------------------------------------------------------------


def test_completion_percentage_counts_required_only():
    required = [SimpleNamespace(id=f"q{i}", required=True) for i in range(10)]
    optional = [SimpleNamespace(id=f"o{i}", required=False) for i in range(5)]
    questions = required + optional
    answered = {f"q{i}" for i in range(5)} | {"o0", "o1", "o2"}
    responses = {qid: SimpleNamespace(response_value="yes") for qid in answered}

    assert AssessmentService.completion_percentage(None, questions, responses) == 50
    # optional answers never inflate the metric
    assert AssessmentService.completion_percentage(None, optional, responses) == 100
    # an unanswered required set reports zero, not a crash
    assert AssessmentService.completion_percentage(None, required, {}) == 0


def test_completion_percentage_treats_false_and_zero_as_answered():
    questions = [
        SimpleNamespace(id="flag", required=True),
        SimpleNamespace(id="count", required=True),
    ]
    responses = {
        "flag": SimpleNamespace(response_value=False),
        "count": SimpleNamespace(response_value=0),
    }
    assert AssessmentService.completion_percentage(None, questions, responses) == 100


# --- review workflow ---------------------------------------------------------


def test_start_review_transitions_submitted_to_under_review(db, risk, template):
    assessment = _submitted(db, risk, template)

    reviewed = _service(db).start_review(assessment.id)
    assert reviewed.status == "under_review"
    assert "assessment.review_started" in _event_types(db, assessment.id)


def test_start_review_on_draft_returns_409(db, risk, template):
    assessment = _create(db, risk, template)
    with pytest.raises(AssessmentInvalidStateError) as exc:
        _service(db).start_review(assessment.id)
    assert exc.value.http_status == 409
    assert "draft" in str(exc.value)


def test_approve_requires_under_review(db, risk, template):
    draft = _create(db, risk, template)
    submitted = _submitted(db, risk, template)

    for assessment_id in (draft.id, submitted.id):
        with pytest.raises(AssessmentInvalidStateError):
            _service(db).approve(assessment_id, None)


def test_approve_sets_reviewer_and_timestamps_and_is_immutable(db, risk, template):
    assessment = _under_review(db, risk, template)

    approved = _service(db).approve(
        assessment.id, AssessmentApproveRequest(reviewer="  Reviewer One  ")
    )
    assert approved.status == "approved"
    assert approved.reviewer == "Reviewer One"
    assert approved.reviewed_at is not None
    assert approved.approved_at is not None
    assert "assessment.approved" in _event_types(db, assessment.id)

    # approved is terminal: no edits, no resubmission, no second approve
    question = _questions(db, template.id)["purpose"]
    with pytest.raises(AssessmentInvalidStateError):
        _service(db).save_response(
            assessment.id, question.id, AssessmentResponseUpdate(response="edit")
        )
    with pytest.raises(AssessmentInvalidStateError):
        _service(db).submit(assessment.id)
    with pytest.raises(AssessmentInvalidStateError):
        _service(db).approve(assessment.id, None)


def test_approve_without_reviewer_keeps_existing_reviewer(db, risk, template):
    assessment = _under_review(db, risk, template)
    _service(db).approve(assessment.id, AssessmentApproveRequest(reviewer="Reviewer One"))
    fresh = db.get(RiskAssessment, assessment.id)
    assert fresh.reviewer == "Reviewer One"


def test_reject_requires_reason_and_persists_it(db, risk, template):
    # schema-level: a blank or missing reason never reaches the service
    with pytest.raises(ValidationError):
        AssessmentRejectRequest(reason="   ")
    with pytest.raises(ValidationError):
        AssessmentRejectRequest()

    assessment = _under_review(db, risk, template)
    rejected = _service(db).reject(
        assessment.id,
        AssessmentRejectRequest(
            reason="  Oversight section incomplete.  ", reviewer="Reviewer One"
        ),
    )
    assert rejected.status == "rejected"
    assert rejected.rejection_reason == "Oversight section incomplete."
    assert rejected.reviewer == "Reviewer One"
    assert rejected.rejected_at is not None


def test_reject_sets_reason_and_timestamp(db, risk, template):
    assessment = _under_review(db, risk, template)
    rejected = _service(db).reject(
        assessment.id, AssessmentRejectRequest(reason="Needs a second data-source review")
    )
    assert rejected.status == "rejected"
    assert rejected.rejection_reason == "Needs a second data-source review"
    assert rejected.rejected_at is not None
    assert "assessment.rejected" in _event_types(db, assessment.id)


def test_reject_without_under_review_returns_409(db, risk, template):
    draft = _create(db, risk, template)
    submitted = _submitted(db, risk, template)

    for assessment_id in (draft.id, submitted.id):
        with pytest.raises(AssessmentInvalidStateError):
            _service(db).reject(assessment_id, AssessmentRejectRequest(reason="nope"))


# --- revision ---------------------------------------------------------------


def test_create_revision_from_rejected_copies_responses(db, risk, template):
    assessment = _create(db, risk, template)
    _answer_required(db, assessment)
    _service(db).submit(assessment.id)
    _service(db).start_review(assessment.id)
    _service(db).reject(assessment.id, AssessmentRejectRequest(reason="Missing oversight detail"))
    predecessor_id = assessment.id
    predecessor_before = {
        (
            r.question_id,
            tuple(r.response_value) if isinstance(r.response_value, list) else r.response_value,
        )
        for r in db.scalars(
            sa.select(AssessmentResponse).where(AssessmentResponse.assessment_id == predecessor_id)
        )
    }
    assert predecessor_before  # some answers were carried in

    revision = _service(db).create_revision(predecessor_id)

    assert revision.id != predecessor_id
    assert revision.revision_number == 2
    assert revision.original_assessment_id == predecessor_id
    assert revision.status == "draft"
    assert revision.tenant_id == TENANT
    assert revision.risk_id == assessment.risk_id
    assert revision.template_id == assessment.template_id
    assert revision.template_version == assessment.template_version
    assert revision.assessment_type == assessment.assessment_type
    assert revision.submitted_at is None
    assert revision.rejected_at is None
    assert revision.rejection_reason is None

    copied = {
        (
            r.question_id,
            tuple(r.response_value) if isinstance(r.response_value, list) else r.response_value,
        )
        for r in db.scalars(
            sa.select(AssessmentResponse).where(AssessmentResponse.assessment_id == revision.id)
        )
    }
    assert copied == predecessor_before

    # the rejected predecessor is untouched history
    predecessor = db.get(RiskAssessment, predecessor_id)
    assert predecessor.status == "rejected"
    assert predecessor.rejected_at is not None
    assert predecessor.rejection_reason == "Missing oversight detail"

    revised_events = _events(db, revision.id)
    assert [str(e.event_type) for e in revised_events] == ["assessment.revised"]
    assert revised_events[0].metadata_["original_assessment_id"] == str(predecessor_id)


def test_create_revision_from_approved_returns_409(db, risk, template):
    assessment = _approved(db, risk, template)
    with pytest.raises(AssessmentInvalidStateError):
        _service(db).create_revision(assessment.id)


# --- list -------------------------------------------------------------------


def test_list_assessments_filters_by_risk_status_type_assessor_and_asset(
    db, risk, other_risk, template
):
    initial = _create(db, risk, template, assessor="priya", assessment_type=AssessmentType.INITIAL)
    periodic = _create(
        db,
        other_risk,
        template,
        assessor="sam",
        assessment_type=AssessmentType.PERIODIC,
    )
    _answer_required(db, periodic)
    _service(db).submit(periodic.id)

    service = _service(db)

    items, total = service.list(RiskAssessmentFilter(risk_id=risk.id))
    assert total == 1
    assert [item.id for item in items] == [initial.id]

    items, total = service.list(RiskAssessmentFilter(status=AssessmentStatus.SUBMITTED))
    assert total == 1
    assert [item.id for item in items] == [periodic.id]

    items, total = service.list(RiskAssessmentFilter(assessment_type=AssessmentType.PERIODIC))
    assert total == 1
    assert [item.id for item in items] == [periodic.id]

    items, total = service.list(RiskAssessmentFilter(assessor="sam"))
    assert total == 1
    assert [item.id for item in items] == [periodic.id]

    # asset filter resolves through the risk join
    items, total = service.list(RiskAssessmentFilter(asset_id=other_risk.asset_id))
    assert total == 1
    assert [item.id for item in items] == [periodic.id]

    items, total = service.list(RiskAssessmentFilter())
    assert total == 2


def test_list_assessments_search_covers_assessor_and_reviewer(db, risk, template):
    by_assessor = _create(db, risk, template, assessor="Alice Smith")
    reviewer_journey = _create(db, risk, template, assessor="Bob Jones")
    _answer_required(db, reviewer_journey)
    service = _service(db)
    service.submit(reviewer_journey.id)
    service.start_review(reviewer_journey.id)
    service.approve(reviewer_journey.id, AssessmentApproveRequest(reviewer="Carol Reviewer"))

    items, _ = service.list(RiskAssessmentFilter(search="alice"))
    assert [item.id for item in items] == [by_assessor.id]

    items, _ = service.list(RiskAssessmentFilter(search="carol"))
    assert [item.id for item in items] == [reviewer_journey.id]

    items, _ = service.list(RiskAssessmentFilter(search="SMITH"))
    assert [item.id for item in items] == [by_assessor.id]


def test_list_assessments_created_range_filter(db, risk, template):
    _create(db, risk, template)
    service = _service(db)
    now = datetime.now(UTC)

    items, total = service.list(
        RiskAssessmentFilter(
            created_from=now - timedelta(days=1), created_to=now + timedelta(days=1)
        )
    )
    assert total == 1

    items, total = service.list(
        RiskAssessmentFilter(
            created_from=now + timedelta(hours=1), created_to=now + timedelta(hours=2)
        )
    )
    assert total == 0
    assert items == []


def test_list_assessments_invalid_sort_returns_400(db, risk, template):
    _create(db, risk, template)
    with pytest.raises(InvalidAssessmentQueryError) as exc:
        _service(db).list(RiskAssessmentFilter(sort_by="assessor"))
    assert exc.value.http_status == 400


def test_list_assessments_is_tenant_scoped(
    db, risk, template, other_tenant_risk, other_tenant_template
):
    _create(db, risk, template)
    _service(db, tenant_id=OTHER_TENANT).create(
        RiskAssessmentCreate(risk_id=other_tenant_risk.id, template_id=other_tenant_template.id)
    )

    _, total = _service(db).list(RiskAssessmentFilter())
    assert total == 1
    _, total = _service(db, tenant_id=OTHER_TENANT).list(RiskAssessmentFilter())
    assert total == 1
