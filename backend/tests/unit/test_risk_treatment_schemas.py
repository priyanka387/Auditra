"""Unit tests: RF-04 treatment/action/residual pydantic schemas (Task 3 draft — copy to backend/tests/unit/ in Task 3)."""

from datetime import date

import pytest
from pydantic import ValidationError

from app.modules.risk_treatment.enums import ResidualAssessmentType
from app.modules.risk_treatment.schemas import (
    ResidualAssessmentCreate,
    TreatmentActionCreate,
    TreatmentActionUpdate,
    TreatmentCancelRequest,
    TreatmentCreate,
    TreatmentFilter,
    TreatmentSummary,
    TreatmentUpdate,
)


def test_treatment_create_strips_title_and_defaults():
    payload = TreatmentCreate(
        title="  Data Exposure Mitigation  ",
        strategy="mitigate",
    )
    assert payload.title == "Data Exposure Mitigation"
    assert payload.description is None
    assert payload.rationale is None
    assert payload.owner is None
    assert payload.planned_start_date is None
    assert payload.target_completion_date is None
    # status is not settable on create — always not_started (Ruling 6)
    assert "status" not in TreatmentCreate.model_fields


def test_treatment_create_rejects_extra_fields():
    with pytest.raises(ValidationError):
        TreatmentCreate(title="t", strategy="mitigate", risk_id="x")
    with pytest.raises(ValidationError):
        TreatmentCreate(title="t", strategy="mitigate", completed_at="2027-01-01")
    with pytest.raises(ValidationError):
        TreatmentCreate(title="t", strategy="mitigate", status="planned")


def test_treatment_create_rejects_inverted_dates():
    with pytest.raises(ValidationError):
        TreatmentCreate(
            title="t",
            strategy="mitigate",
            planned_start_date=date(2027, 1, 15),
            target_completion_date=date(2026, 12, 1),
        )


def test_treatment_create_accepts_single_date():
    only_target = TreatmentCreate(
        title="t", strategy="mitigate", target_completion_date=date(2027, 1, 15)
    )
    assert only_target.target_completion_date == date(2027, 1, 15)
    only_start = TreatmentCreate(
        title="t", strategy="mitigate", planned_start_date=date(2026, 12, 1)
    )
    assert only_start.planned_start_date == date(2026, 12, 1)
    both = TreatmentCreate(
        title="t",
        strategy="mitigate",
        planned_start_date=date(2026, 12, 1),
        target_completion_date=date(2027, 1, 15),
    )
    assert both.planned_start_date <= both.target_completion_date


def test_treatment_update_accepts_partial_payload():
    payload = TreatmentUpdate(owner="Security Team")
    assert payload.owner == "Security Team"
    assert payload.title is None
    assert payload.status is None


def test_treatment_update_rejects_completed_at():
    with pytest.raises(ValidationError):
        TreatmentUpdate(completed_at="2027-01-01")
    with pytest.raises(ValidationError):
        TreatmentUpdate(cancelled_at="2027-01-01")
    with pytest.raises(ValidationError):
        TreatmentUpdate(cancellation_reason="nope")


def test_treatment_cancel_requires_non_blank_reason():
    with pytest.raises(ValidationError):
        TreatmentCancelRequest(reason="")
    with pytest.raises(ValidationError):
        TreatmentCancelRequest(reason="   ")


def test_treatment_cancel_strips_reason():
    payload = TreatmentCancelRequest(reason="  Risk retired after decommission.  ")
    assert payload.reason == "Risk retired after decommission."


def test_action_create_requires_owner():
    with pytest.raises(ValidationError):
        TreatmentActionCreate(title="Add PII filtering")
    with pytest.raises(ValidationError):
        TreatmentActionCreate(title="Add PII filtering", owner="   ")


def test_action_create_defaults_priority_to_medium():
    payload = TreatmentActionCreate(title="Add PII filtering", owner="Security Team")
    assert payload.priority == "medium"


def test_action_create_rejects_bogus_priority():
    with pytest.raises(ValidationError):
        TreatmentActionCreate(title="t", owner="o", priority="urgent")


def test_action_create_rejects_inverted_dates():
    with pytest.raises(ValidationError):
        TreatmentActionCreate(
            title="t",
            owner="o",
            planned_start_date=date(2026, 12, 20),
            due_date=date(2026, 12, 1),
        )


def test_action_update_accepts_status_and_rejects_completed_at():
    payload = TreatmentActionUpdate(status="in_progress")
    assert payload.status == "in_progress"
    with pytest.raises(ValidationError):
        TreatmentActionUpdate(completed_at="2027-01-01")
    with pytest.raises(ValidationError):
        TreatmentActionUpdate(priority="urgent")


def test_residual_create_defaults_to_expected():
    payload = ResidualAssessmentCreate(likelihood=2, impact=4)
    assert payload.assessment_type == ResidualAssessmentType.EXPECTED


def test_residual_create_type_boundary():
    # "verified" is in the enum (schema accepts it) — the *service*
    # rejects it until RF-07 verification exists (Ruling 10)
    verified = ResidualAssessmentCreate(likelihood=2, impact=4, assessment_type="verified")
    assert verified.assessment_type == ResidualAssessmentType.VERIFIED
    with pytest.raises(ValidationError):
        ResidualAssessmentCreate(likelihood=2, impact=4, assessment_type="bogus")
    # score/severity are computed server-side only
    with pytest.raises(ValidationError):
        ResidualAssessmentCreate(likelihood=2, impact=4, calculated_score=8)


def test_treatment_filter_defaults():
    filters = TreatmentFilter()
    assert filters.page == 1
    assert filters.page_size == 25
    assert filters.sort_by == "created_at"
    assert filters.sort_order == "desc"
    assert filters.strategy is None
    assert filters.status is None
    assert filters.overdue is None


def test_treatment_summary_is_scalar_only():
    assert set(TreatmentSummary.model_fields) == {
        "strategy",
        "status",
        "owner",
        "target_completion_date",
        "is_overdue",
    }
