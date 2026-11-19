"""Unit tests: RF-03 scoring and framework schemas."""

import pytest
from pydantic import ValidationError

from app.modules.risk_scoring.schemas import (
    RiskScoreCreate,
    RiskScoreFilter,
    RiskScoreOverrideRequest,
    ScoringFrameworkCreate,
    ScoringFrameworkFilter,
    ScoringFrameworkUpdate,
    SeverityThresholdInput,
)

MINIMAL_LEVELS = [
    {"value": 1, "name": "Low"},
    {"value": 2, "name": "High"},
]
MINIMAL_THRESHOLDS = [
    {"severity": "low", "minimum_score": 1, "maximum_score": 4, "priority": 4},
    {"severity": "critical", "minimum_score": 5, "maximum_score": 25, "priority": 1},
]


def _create_payload(**overrides) -> dict:
    payload = {
        "name": "Custom 4x4",
        "likelihood_levels": MINIMAL_LEVELS,
        "impact_levels": MINIMAL_LEVELS,
        "severity_thresholds": MINIMAL_THRESHOLDS,
    }
    payload.update(overrides)
    return payload


def test_framework_create_strips_name_and_defaults():
    schema = ScoringFrameworkCreate.model_validate(_create_payload(name="  Custom 4x4  "))
    assert schema.name == "Custom 4x4"
    assert schema.method == "likelihood_impact"
    assert schema.version == 1
    assert schema.is_active is True
    assert schema.description is None


def test_framework_create_requires_levels_and_thresholds():
    with pytest.raises(ValidationError):
        ScoringFrameworkCreate.model_validate(_create_payload(likelihood_levels=None))
    with pytest.raises(ValidationError):
        ScoringFrameworkCreate.model_validate(_create_payload(likelihood_levels=[]))
    with pytest.raises(ValidationError):
        ScoringFrameworkCreate.model_validate(_create_payload(impact_levels=[]))
    with pytest.raises(ValidationError):
        ScoringFrameworkCreate.model_validate(_create_payload(severity_thresholds=[]))


def test_framework_create_rejects_extra_fields():
    with pytest.raises(ValidationError):
        ScoringFrameworkCreate.model_validate(_create_payload(max_score=25))


def test_framework_create_rejects_blank_name():
    with pytest.raises(ValidationError):
        ScoringFrameworkCreate.model_validate(_create_payload(name="   "))


def test_framework_update_accepts_partial_payload():
    schema = ScoringFrameworkUpdate.model_validate({"name": " Renamed "})
    assert schema.name == "Renamed"
    assert schema.description is None
    assert schema.is_active is None


def test_framework_update_rejects_immutable_fields():
    # Ruling 4: version/method/levels are never PATCHable
    for field in ("version", "method", "likelihood_levels", "severity_thresholds"):
        with pytest.raises(ValidationError):
            ScoringFrameworkUpdate.model_validate({field: 2 if field == "version" else []})


def test_threshold_input_rejects_blank_severity():
    with pytest.raises(ValidationError):
        SeverityThresholdInput.model_validate(
            {"severity": "bogus", "minimum_score": 1, "maximum_score": 4, "priority": 4}
        )


def test_score_create_requires_framework_and_values():
    with pytest.raises(ValidationError):
        RiskScoreCreate.model_validate({"likelihood": 3, "impact": 4})
    with pytest.raises(ValidationError):
        RiskScoreCreate.model_validate({"framework_id": "00000000-0000-0000-0000-000000000001"})


def test_score_create_rejects_extra_fields():
    # calculated_score is computed server-side only (plan §51)
    with pytest.raises(ValidationError):
        RiskScoreCreate.model_validate(
            {
                "framework_id": "00000000-0000-0000-0000-000000000001",
                "likelihood": 4,
                "impact": 5,
                "calculated_score": 20,
            }
        )


def test_override_request_requires_non_blank_reason():
    base = {"override_score": 20}
    with pytest.raises(ValidationError):
        RiskScoreOverrideRequest.model_validate({**base, "reason": ""})
    with pytest.raises(ValidationError):
        RiskScoreOverrideRequest.model_validate({**base, "reason": "   "})


def test_override_request_strips_reason():
    schema = RiskScoreOverrideRequest.model_validate(
        {"override_score": 12, "reason": "  Business context  "}
    )
    assert schema.reason == "Business context"
    assert schema.override_score == 12


def test_score_filter_defaults():
    schema = RiskScoreFilter.model_validate({})
    assert schema.page == 1
    assert schema.page_size == 25
    assert schema.sort_by == "created_at"
    assert schema.sort_order == "desc"


def test_framework_filter_defaults():
    schema = ScoringFrameworkFilter.model_validate({})
    assert schema.page == 1
    assert schema.page_size == 25
    assert schema.sort_by == "name"
    assert schema.sort_order == "asc"


def test_framework_filter_rejects_unknown_sort():
    with pytest.raises(ValidationError):
        ScoringFrameworkFilter.model_validate({"sort_by": "version"})
