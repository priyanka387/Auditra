"""Unit tests: RF-01 risk and risk-category schemas."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.risk.enums import RiskSource, RiskStatus
from app.modules.risk.schemas import RiskCategoryCreate, RiskCreate, RiskFilter, RiskUpdate

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
