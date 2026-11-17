"""API tests: /api/v1/risk-assessments (RF-02 assessment workflow)."""

from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa

from app.core.config import settings
from app.modules.assessment.models import AssessmentTemplate, RiskAssessment
from app.modules.assessment.seed import seed_assessment_templates
from app.modules.asset_discovery.models import DiscoveredAsset, DiscoverySource
from app.modules.risk.models import Risk, RiskCategory
from app.modules.risk.seed import seed_risk_categories

API = "/api/v1/risk-assessments"
RISK_API = "/api/v1/risks"
CATEGORY_API = "/api/v1/risk-categories"
TEMPLATE_API = "/api/v1/assessment-templates"


def _error(response):
    return response.json()["error"]


def _make_asset(db, tenant_id, name: str) -> str:
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
    return str(asset.id)


def _risk(client, db, title: str = "Unreviewed model in payments") -> str:
    """A tenant-visible risk: category + asset + risk through RF-01's API."""
    categories = client.get(CATEGORY_API, params={"search": "Security"}).json()
    category_id = categories["items"][0]["id"]
    asset_id = _make_asset(db, settings.default_tenant_id, "payments-gpt-4o")
    response = client.post(
        RISK_API,
        json={"title": title, "category_id": category_id, "asset_id": asset_id},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _template(client) -> str:
    """The seeded system template (the client fixture seeds it per tenant)."""
    items = client.get(TEMPLATE_API, params={"page_size": 100}).json()["items"]
    return next(item["id"] for item in items if item["key"] == "general-ai-risk-assessment")


def _create(client, db, **overrides) -> dict:
    payload = {
        "risk_id": _risk(client, db),
        "template_id": _template(client),
        "assessment_type": "initial",
        "assessor": "priya",
    }
    payload.update(overrides)
    response = client.post(API, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _value_for(question: dict):
    kind = question["question_type"]
    if kind == "boolean":
        return False
    if kind == "number":
        return 3
    if kind == "single_select":
        return question["options"][0]["value"]
    if kind == "multi_select":
        return [question["options"][0]["value"]]
    return "answer"


def _answer_required(client, assessment_id: str) -> dict:
    """Answer every required question through the API; return fresh detail."""
    detail = client.get(f"{API}/{assessment_id}").json()
    for section in detail["sections"]:
        for question in section["questions"]:
            if question["required"]:
                response = client.patch(
                    f"{API}/{assessment_id}/responses/{question['id']}",
                    json={"response": _value_for(question)},
                )
                assert response.status_code == 200, response.text
    return client.get(f"{API}/{assessment_id}").json()


# --- create -------------------------------------------------------------------


def test_create_assessment_returns_201_with_detail_shape(client, db):
    risk_id = _risk(client, db)
    template_id = _template(client)

    response = client.post(
        API,
        json={
            "risk_id": risk_id,
            "template_id": template_id,
            "assessment_type": "initial",
            "assessor": "  priya  ",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["status"] == "draft"
    assert body["completion_percentage"] == 0
    assert body["template_version"] == 1
    assert body["assessor"] == "priya"
    assert body["revision_number"] == 1
    assert body["risk"]["id"] == risk_id
    assert body["risk"]["asset"]["name"] == "payments-gpt-4o"
    assert body["template"]["id"] == template_id
    assert len(body["sections"]) == 7
    assert sum(len(section["questions"]) for section in body["sections"]) == 14
    assert all(
        question["response"] is None
        for section in body["sections"]
        for question in section["questions"]
    )


def test_create_assessment_unknown_risk_404_envelope(client):
    response = client.post(API, json={"risk_id": str(uuid4()), "template_id": str(uuid4())})
    assert response.status_code == 404
    error = _error(response)
    assert error["code"] == "RISK_NOT_FOUND"
    assert error["request_id"]
    assert error["http_status"] == 404


def test_create_assessment_unknown_template_404(client, db):
    response = client.post(API, json={"risk_id": _risk(client, db), "template_id": str(uuid4())})
    assert response.status_code == 404
    assert _error(response)["code"] == "ASSESSMENT_TEMPLATE_NOT_FOUND"


def test_create_assessment_validation_error_on_extra_field(client, db):
    # RF-03 boundary: scoring fields must not sneak into RF-02
    response = client.post(
        API,
        json={
            "risk_id": _risk(client, db),
            "template_id": _template(client),
            "likelihood": 3,
        },
    )
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


# --- read ---------------------------------------------------------------------


def test_get_assessment_returns_sections_questions_and_responses(client, db):
    created = _create(client, db)
    detail = _answer_required(client, created["id"])

    purpose = next(
        question
        for section in detail["sections"]
        for question in section["questions"]
        if question["key"] == "purpose"
    )
    assert purpose["response"] is not None
    assert purpose["response"]["value"] == "answer"
    assert detail["completion_percentage"] == 100
    assert detail["status"] == "in_progress"


def test_get_unknown_assessment_returns_404_envelope(client):
    response = client.get(f"{API}/{uuid4()}")
    assert response.status_code == 404
    error = _error(response)
    assert error["code"] == "ASSESSMENT_NOT_FOUND"
    assert error["request_id"]
    assert error["http_status"] == 404


def test_cross_tenant_assessment_get_returns_404(client, db):
    other_tenant = uuid4()
    asset_id = _make_asset(db, other_tenant, "other-tenant-model")
    seed_risk_categories(db, other_tenant)
    seed_assessment_templates(db, other_tenant)
    db.flush()
    category = db.scalar(
        sa.select(RiskCategory).where(
            RiskCategory.tenant_id == other_tenant, RiskCategory.slug == "security"
        )
    )
    risk = Risk(
        id=uuid4(),
        tenant_id=other_tenant,
        title="Other tenant risk",
        category_id=category.id,
        source="manual",
        asset_id=asset_id,
    )
    db.add(risk)
    db.flush()
    template = db.scalar(
        sa.select(AssessmentTemplate).where(
            AssessmentTemplate.tenant_id == other_tenant,
            AssessmentTemplate.key == "general-ai-risk-assessment",
        )
    )
    foreign = RiskAssessment(
        id=uuid4(),
        tenant_id=other_tenant,
        risk_id=risk.id,
        template_id=template.id,
    )
    db.add(foreign)
    db.commit()
    foreign_id = str(foreign.id)

    response = client.get(f"{API}/{foreign_id}")
    assert response.status_code == 404
    assert _error(response)["code"] == "ASSESSMENT_NOT_FOUND"


# --- responses -----------------------------------------------------------------


def test_patch_response_updates_answer_and_completion(client, db):
    created = _create(client, db)
    question = next(
        question
        for section in created["sections"]
        for question in section["questions"]
        if question["key"] == "purpose"
    )

    response = client.patch(
        f"{API}/{created['id']}/responses/{question['id']}",
        json={"response": "Fraud triage", "answered_by": "priya"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "in_progress"
    assert body["completion_percentage"] == round(100 / 9)
    echoed = next(
        q for section in body["sections"] for q in section["questions"] if q["id"] == question["id"]
    )
    assert echoed["response"]["value"] == "Fraud triage"
    assert echoed["response"]["answered_by"] == "priya"


def test_patch_response_wrong_type_returns_400(client, db):
    created = _create(client, db)
    question = next(
        question
        for section in created["sections"]
        for question in section["questions"]
        if question["key"] == "governance-review"
    )

    response = client.patch(
        f"{API}/{created['id']}/responses/{question['id']}",
        json={"response": "not-a-number"},
    )
    assert response.status_code == 400
    assert _error(response)["code"] == "ASSESSMENT_RESPONSE_INVALID"


def test_patch_response_unknown_question_404(client, db):
    created = _create(client, db)
    response = client.patch(
        f"{API}/{created['id']}/responses/{uuid4()}",
        json={"response": "answer"},
    )
    assert response.status_code == 404
    assert _error(response)["code"] == "ASSESSMENT_QUESTION_NOT_FOUND"


# --- lifecycle ------------------------------------------------------------------


def test_submit_incomplete_returns_409(client, db):
    created = _create(client, db)
    response = client.post(f"{API}/{created['id']}/submit")
    assert response.status_code == 409
    error = _error(response)
    assert error["code"] == "ASSESSMENT_INCOMPLETE"
    assert "data-types" in error["message"]


def test_submit_complete_then_edit_returns_409(client, db):
    created = _create(client, db)
    _answer_required(client, created["id"])

    submitted = client.post(f"{API}/{created['id']}/submit")
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "submitted"

    question = next(
        question
        for section in submitted.json()["sections"]
        for question in section["questions"]
        if question["key"] == "purpose"
    )
    edit = client.patch(
        f"{API}/{created['id']}/responses/{question['id']}",
        json={"response": "late edit"},
    )
    assert edit.status_code == 409
    assert _error(edit)["code"] == "ASSESSMENT_INVALID_STATE"


def test_full_review_journey_via_api(client, db):
    created = _create(client, db)
    assessment_id = created["id"]
    _answer_required(client, assessment_id)

    assert client.post(f"{API}/{assessment_id}/submit").json()["status"] == "submitted"
    assert client.post(f"{API}/{assessment_id}/start-review").json()["status"] == "under_review"
    approved = client.post(f"{API}/{assessment_id}/approve", json={"reviewer": "Reviewer One"})
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "approved"
    assert body["reviewer"] == "Reviewer One"
    assert body["approved_at"] is not None
    assert body["reviewed_at"] is not None

    # approved is terminal
    question = next(
        question
        for section in body["sections"]
        for question in section["questions"]
        if question["key"] == "purpose"
    )
    edit = client.patch(
        f"{API}/{assessment_id}/responses/{question['id']}",
        json={"response": "tamper"},
    )
    assert edit.status_code == 409
    assert _error(edit)["code"] == "ASSESSMENT_INVALID_STATE"


def test_reject_without_reason_returns_422(client, db):
    created = _create(client, db)
    response = client.post(f"{API}/{created['id']}/reject", json={})
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_revision_extra_body_field_returns_422(client, db):
    # the revision body is empty-by-design; stray fields must 422 instead
    # of being silently dropped
    created = _create(client, db)
    _answer_required(client, created["id"])
    client.post(f"{API}/{created['id']}/submit")
    client.post(f"{API}/{created['id']}/start-review")
    client.post(
        f"{API}/{created['id']}/reject",
        json={"reason": "needs work"},
    )

    response = client.post(f"{API}/{created['id']}/revision", json={"score": 3})
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_reject_with_reason_then_revision_201(client, db):
    created = _create(client, db)
    assessment_id = created["id"]
    detail = _answer_required(client, assessment_id)
    client.post(f"{API}/{assessment_id}/submit")
    client.post(f"{API}/{assessment_id}/start-review")

    rejected = client.post(
        f"{API}/{assessment_id}/reject",
        json={"reason": "Oversight section needs detail", "reviewer": "Reviewer One"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["rejection_reason"] == "Oversight section needs detail"

    answered_before = sum(
        1
        for section in detail["sections"]
        for question in section["questions"]
        if question["response"] is not None
    )
    assert answered_before > 0

    revision = client.post(f"{API}/{assessment_id}/revision")
    assert revision.status_code == 201, revision.text
    body = revision.json()
    assert body["id"] != assessment_id
    assert body["status"] == "draft"
    assert body["revision_number"] == 2
    assert body["original_assessment_id"] == assessment_id
    assert body["rejection_reason"] is None
    answered_after = sum(
        1
        for section in body["sections"]
        for question in section["questions"]
        if question["response"] is not None
    )
    assert answered_after == answered_before

    original = client.get(f"{API}/{assessment_id}").json()
    assert original["status"] == "rejected"
    assert original["rejection_reason"] == "Oversight section needs detail"


# --- list ------------------------------------------------------------------------


def test_list_assessments_pagination_metadata(client, db):
    ids = {_create(client, db)["id"] for _ in range(3)}
    assert len(ids) == 3

    page_one = client.get(API, params={"page_size": 2}).json()
    assert page_one["page"] == 1
    assert page_one["page_size"] == 2
    assert page_one["total"] == 3
    assert page_one["total_pages"] == 2
    assert len(page_one["items"]) == 2

    page_two = client.get(API, params={"page": 2, "page_size": 2}).json()
    assert len(page_two["items"]) == 1
    combined = {item["id"] for item in page_one["items"]} | {
        item["id"] for item in page_two["items"]
    }
    assert combined == ids


def test_list_assessments_filters_by_status_and_risk(client, db):
    first = _create(client, db)
    second = _create(client, db, risk_id=_risk(client, db, title="Second risk"))
    _answer_required(client, second["id"])
    client.post(f"{API}/{second['id']}/submit")

    by_status = client.get(API, params={"status": "submitted"}).json()
    assert [item["id"] for item in by_status["items"]] == [second["id"]]

    by_risk = client.get(API, params={"risk_id": first["risk_id"]}).json()
    assert [item["id"] for item in by_risk["items"]] == [first["id"]]

    by_draft = client.get(API, params={"status": "draft"}).json()
    assert [item["id"] for item in by_draft["items"]] == [first["id"]]


def test_list_assessments_unknown_filter_rejected(client, db):
    response = client.get(API, params={"bogus_filter": "1"})
    assert response.status_code == 400
    assert _error(response)["code"] == "ASSESSMENT_QUERY_INVALID"


def test_list_assessments_invalid_sort_returns_400(client, db):
    response = client.get(API, params={"sort_by": "assessor"})
    assert response.status_code == 400
    assert _error(response)["code"] == "ASSESSMENT_QUERY_INVALID"
