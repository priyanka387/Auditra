"""Unit tests: RF-01 risk and risk-category schemas."""

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.risk.enums import RiskSource, RiskStatus
from app.modules.risk.schemas import (
    RiskCategoryCreate,
    RiskCreate,
    RiskFilter,
    RiskResponse,
    RiskScoreSummary,
    RiskUpdate,
    TreatmentSummary,
)
from app.modules.risk_scoring.enums import RiskSeverity
from app.modules.risk_treatment.enums import RiskTreatmentStatus, RiskTreatmentStrategy

CATEGORY_ID = uuid4()


def test_risk_create_strips_title_and_owner():
    payload = RiskCreate(
        title="  Candidate ranking bias  ",
        category_id=CATEGORY_ID,
        owner="  Alice  ",
        team="  AI Governance  ",
    )
    assert payload.title == "Candidate ranking bias"
    assert payload.owner == "Alice"
    assert payload.team == "AI Governance"


def test_risk_create_rejects_blank_title():
    with pytest.raises(ValidationError):
        RiskCreate(title="   ", category_id=CATEGORY_ID)


def test_risk_create_rejects_oversized_title():
    with pytest.raises(ValidationError):
        RiskCreate(title="x" * 256, category_id=CATEGORY_ID)


def test_risk_create_requires_category_id():
    with pytest.raises(ValidationError):
        RiskCreate(title="Missing category")


def test_risk_create_rejects_unknown_status_and_source():
    with pytest.raises(ValidationError):
        RiskCreate(title="Bad status", category_id=CATEGORY_ID, status="mitigated")
    with pytest.raises(ValidationError):
        RiskCreate(title="Bad source", category_id=CATEGORY_ID, source="questionnaire")


def test_risk_create_rejects_extra_fields():
    # RF-01 is the registry, not the scoring engine: out-of-scope fields
    # like likelihood must be rejected, not silently stored
    with pytest.raises(ValidationError):
        RiskCreate(title="Scored risk", category_id=CATEGORY_ID, likelihood=3)


def test_risk_create_defaults():
    payload = RiskCreate(title="Defaulted", category_id=CATEGORY_ID)
    assert payload.status == RiskStatus.OPEN
    assert payload.source == RiskSource.MANUAL
    assert payload.description is None
    assert payload.asset_id is None
    assert payload.owner is None
    assert payload.team is None


def test_risk_create_blank_owner_becomes_none():
    payload = RiskCreate(title="No owner", category_id=CATEGORY_ID, owner="   ")
    assert payload.owner is None


def test_risk_update_accepts_partial_payload():
    payload = RiskUpdate(status=RiskStatus.IN_REVIEW)
    assert payload.status == RiskStatus.IN_REVIEW
    assert payload.title is None
    assert payload.category_id is None
    assert payload.source is None
    assert payload.asset_id is None
    assert payload.owner is None
    assert payload.team is None
    assert payload.description is None


def test_risk_update_rejects_blank_title():
    with pytest.raises(ValidationError):
        RiskUpdate(title="   ")


def test_risk_update_rejects_extra_fields():
    with pytest.raises(ValidationError):
        RiskUpdate(residual_score=12)


def test_risk_filter_defaults():
    filters = RiskFilter()
    assert filters.page == 1
    assert filters.page_size == 25
    assert filters.archived is False
    assert filters.sort_by == "updated_at"
    assert filters.sort_order == "desc"
    assert filters.search is None
    assert filters.status is None


def test_risk_filter_rejects_extra_fields():
    with pytest.raises(ValidationError):
        RiskFilter(include_archived=True)


def test_risk_category_create_autoslug():
    payload = RiskCategoryCreate(name="Fairness / Bias")
    assert payload.slug == "fairness-bias"
    assert payload.name == "Fairness / Bias"


def test_risk_category_create_keeps_explicit_slug():
    payload = RiskCategoryCreate(name="Industry Risk", slug="industry-risk")
    assert payload.slug == "industry-risk"


def test_risk_category_create_rejects_bad_slug():
    with pytest.raises(ValidationError):
        RiskCategoryCreate(name="Bad slug", slug="Bad Slug!")


def test_risk_category_create_requires_name():
    with pytest.raises(ValidationError):
        RiskCategoryCreate()


# --- RF-03 score summary -----------------------------------------------------


def test_risk_score_summary_rejects_naive_timestamps():
    with pytest.raises(ValidationError):
        RiskScoreSummary(
            likelihood=4,
            impact=5,
            calculated_score=20,
            effective_score=20,
            severity="critical",
            priority=1,
            is_override=False,
            scored_at=datetime(2026, 11, 18, 12, 0, 0),  # noqa: DTZ001 - naive is the point
        )


def test_risk_score_summary_accepts_aware_timestamp_and_enum():
    summary = RiskScoreSummary(
        likelihood=4,
        impact=5,
        calculated_score=20,
        effective_score=20,
        severity="critical",
        priority=1,
        is_override=False,
        scored_at=datetime.now(UTC),
    )
    assert summary.severity == RiskSeverity.CRITICAL
    assert summary.effective_score == 20


def test_risk_response_defaults_current_score_to_none():
    response = RiskResponse.model_validate(
        {
            "id": uuid4(),
            "tenant_id": uuid4(),
            "title": "Unscored risk",
            "category_id": CATEGORY_ID,
            "category": {"id": CATEGORY_ID, "name": "Security", "slug": "security"},
            "source": "manual",
            "status": "open",
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
    )
    # unscored is null, never a fabricated low (plan §28)
    assert response.current_score is None


def test_risk_filter_accepts_scoring_fields():
    filters = RiskFilter(
        severity="critical",
        priority=1,
        scored=True,
        likelihood=4,
        impact=5,
        framework_id=uuid4(),
    )
    assert filters.severity == RiskSeverity.CRITICAL
    assert filters.priority == 1
    assert filters.scored is True


def test_risk_filter_rejects_invalid_priority():
    with pytest.raises(ValidationError):
        RiskFilter(priority=0)


# --- RF-04 treatment summary ---------------------------------------------------


def test_treatment_summary_scalar_fields():
    summary = TreatmentSummary(
        strategy="mitigate",
        status="in_progress",
        owner="Security Team",
        target_completion_date=date(2027, 1, 15),
        is_overdue=False,
    )

    assert summary.strategy == RiskTreatmentStrategy.MITIGATE
    assert summary.status == RiskTreatmentStatus.IN_PROGRESS
    assert summary.owner == "Security Team"
    assert summary.target_completion_date == date(2027, 1, 15)
    assert summary.is_overdue is False


def test_treatment_summary_is_scalar_only():
    # scalar-only like RiskScoreSummary: no nested refs may sneak in, and
    # the five documented fields are the entire contract (plan §42)
    with pytest.raises(ValidationError):
        TreatmentSummary(
            strategy="mitigate",
            status="not_started",
            owner=None,
            target_completion_date=None,
            is_overdue=False,
            risk_id=uuid4(),
        )


def test_treatment_summary_rejects_unknown_strategy():
    with pytest.raises(ValidationError):
        TreatmentSummary(
            strategy="ignore",
            status="not_started",
            owner=None,
            target_completion_date=None,
            is_overdue=False,
        )


def test_treatment_summary_target_date_type_enforced():
    with pytest.raises(ValidationError):
        TreatmentSummary(
            strategy="mitigate",
            status="not_started",
            owner=None,
            target_completion_date="not-a-date",
            is_overdue=False,
        )


def test_risk_response_defaults_treatment_to_none():
    response = RiskResponse.model_validate(
        {
            "id": uuid4(),
            "tenant_id": uuid4(),
            "title": "Untreated risk",
            "category_id": CATEGORY_ID,
            "category": {"id": CATEGORY_ID, "name": "Security", "slug": "security"},
            "source": "manual",
            "status": "open",
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
    )
    # no active treatment is null, never a fabricated status (plan §42)
    assert response.treatment is None


def test_risk_filter_accepts_treatment_fields():
    filters = RiskFilter(
        treatment_strategy="accept",
        treatment_status="in_progress",
        treatment_owner="Risk Committee",
        treatment_overdue=True,
        treated=False,
    )

    assert filters.treatment_strategy == RiskTreatmentStrategy.ACCEPT
    assert filters.treatment_status == RiskTreatmentStatus.IN_PROGRESS
    assert filters.treatment_owner == "Risk Committee"
    assert filters.treatment_overdue is True
    assert filters.treated is False


def test_risk_filter_treatment_defaults_are_none():
    filters = RiskFilter()

    assert filters.treatment_strategy is None
    assert filters.treatment_status is None
    assert filters.treatment_owner is None
    assert filters.treatment_overdue is None
    assert filters.treated is None
