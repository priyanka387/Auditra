"""Unit tests: RF-02 assessment and template schemas."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.assessment.enums import AssessmentType
from app.modules.assessment.schemas import (
    AssessmentApproveRequest,
    AssessmentRejectRequest,
    AssessmentResponseUpdate,
    AssessmentTemplateCreate,
    AssessmentTemplateUpdate,
    RiskAssessmentCreate,
    RiskAssessmentFilter,
)

# --- templates ---------------------------------------------------------------


def test_template_create_strips_name_and_autosizes_key():
    template = AssessmentTemplateCreate(name="  General AI Risk Assessment  ")
    assert template.name == "General AI Risk Assessment"
    assert template.key == "general-ai-risk-assessment"
    assert template.assessment_type == AssessmentType.INITIAL
    assert template.is_active is True
    assert template.description is None


def test_template_create_rejects_blank_name():
    with pytest.raises(ValidationError):
        AssessmentTemplateCreate(name="   ")


def test_template_create_rejects_bad_key():
    with pytest.raises(ValidationError):
        AssessmentTemplateCreate(name="Custom Assessment", key="Bad Key!")


def test_template_create_rejects_extra_fields():
    # template authoring of sections/questions is out of scope (plan §22)
    with pytest.raises(ValidationError):
        AssessmentTemplateCreate(name="Custom Assessment", sections=[])


def test_template_update_accepts_partial_payload():
    update = AssessmentTemplateUpdate(name="Renamed Template")
    assert update.name == "Renamed Template"
    assert update.description is None
    assert update.assessment_type is None
    assert update.is_active is None


def test_template_update_rejects_extra_fields():
    # version is never PATCHable: historical assessments snapshot it (§11)
    with pytest.raises(ValidationError):
        AssessmentTemplateUpdate(version=2)
    with pytest.raises(ValidationError):
        AssessmentTemplateUpdate(key="sneaky-key")


# --- assessments -------------------------------------------------------------


def test_assessment_create_requires_risk_and_template():
    with pytest.raises(ValidationError):
        RiskAssessmentCreate(template_id=uuid4())
    with pytest.raises(ValidationError):
        RiskAssessmentCreate(risk_id=uuid4())


def test_assessment_create_strips_assessor():
    payload = RiskAssessmentCreate(risk_id=uuid4(), template_id=uuid4(), assessor="   ")
    assert payload.assessor is None
    assert payload.assessment_type == AssessmentType.INITIAL

    named = RiskAssessmentCreate(risk_id=uuid4(), template_id=uuid4(), assessor="  Priya  ")
    assert named.assessor == "Priya"


def test_assessment_create_rejects_unknown_assessment_type():
    with pytest.raises(ValidationError):
        RiskAssessmentCreate(risk_id=uuid4(), template_id=uuid4(), assessment_type="ad_hoc")


def test_assessment_create_rejects_extra_fields():
    # RF-03 boundary: scoring fields must not sneak into RF-02 (§4)
    with pytest.raises(ValidationError):
        RiskAssessmentCreate(risk_id=uuid4(), template_id=uuid4(), score=10)
    with pytest.raises(ValidationError):
        RiskAssessmentCreate(risk_id=uuid4(), template_id=uuid4(), likelihood=3, impact=4)


# --- responses & review ------------------------------------------------------


def test_response_update_requires_response_value():
    with pytest.raises(ValidationError):
        AssessmentResponseUpdate()
    # no clearing answers in MVP (plan Ruling 10)
    with pytest.raises(ValidationError):
        AssessmentResponseUpdate(response=None)


def test_response_update_accepts_answered_by():
    payload = AssessmentResponseUpdate(response="yes", answered_by=" Priya ")
    assert payload.answered_by == "Priya"


def test_reject_request_requires_non_blank_reason():
    # RF-02 §32: a rejection must carry a reason
    with pytest.raises(ValidationError):
        AssessmentRejectRequest(reason="   ")
    with pytest.raises(ValidationError):
        AssessmentRejectRequest()


def test_reject_request_strips_reason_and_reviewer():
    payload = AssessmentRejectRequest(
        reason="  Human oversight section requires additional information.  ",
        reviewer="  Reviewer One  ",
    )
    assert payload.reason == "Human oversight section requires additional information."
    assert payload.reviewer == "Reviewer One"


def test_approve_request_accepts_empty_body():
    assert AssessmentApproveRequest().reviewer is None
    assert AssessmentApproveRequest(reviewer=" Reviewer One ").reviewer == "Reviewer One"


# --- filters -----------------------------------------------------------------


def test_filter_defaults():
    filters = RiskAssessmentFilter()
    assert filters.page == 1
    assert filters.page_size == 25
    assert filters.search is None
    assert filters.risk_id is None
    assert filters.status is None
    assert filters.sort_by == "updated_at"
    assert filters.sort_order == "desc"


def test_filter_rejects_inverted_date_range():
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        RiskAssessmentFilter(created_from=now, created_to=now - timedelta(days=1))


def test_filter_accepts_ordered_date_range():
    now = datetime.now(UTC)
    filters = RiskAssessmentFilter(created_from=now - timedelta(days=1), created_to=now)
    assert filters.created_from < filters.created_to
