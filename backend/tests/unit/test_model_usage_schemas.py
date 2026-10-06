from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.model_usage.enums import (
    CostSource,
    TokenUsageSource,
    UsageSource,
    UsageStatus,
)
from app.modules.model_usage.sanitization import sanitize_error_message
from app.modules.model_usage.schemas import (
    UsageEventBatchCreate,
    UsageEventCreate,
    payload_fingerprint,
)

NOW = datetime(2026, 10, 5, 10, 0, 0, tzinfo=UTC)
MODEL_ID = uuid4()


def _payload(**overrides):
    payload = {
        "event_id": "evt-1",
        "model_id": MODEL_ID,
        "source": UsageSource.API,
        "started_at": NOW,
    }
    payload.update(overrides)
    return payload


def test_enum_values():
    assert UsageStatus.SUCCESS.value == "success"
    assert UsageStatus.ERROR.value == "error"
    assert UsageStatus.CANCELLED.value == "cancelled"
    assert UsageStatus.UNKNOWN.value == "unknown"
    assert UsageSource.LANGCHAIN.value == "langchain"
    assert UsageSource.LANGGRAPH.value == "langgraph"
    assert TokenUsageSource.PROVIDER_REPORTED.value == "provider_reported"
    assert TokenUsageSource.CALCULATED.value == "calculated"
    assert TokenUsageSource.UNAVAILABLE.value == "unavailable"
    assert CostSource.UNAVAILABLE.value == "unavailable"


def test_minimal_event_defaults():
    event = UsageEventCreate(**_payload())
    assert event.status == UsageStatus.SUCCESS
    assert event.token_usage_source == TokenUsageSource.UNAVAILABLE
    assert event.cost_source == CostSource.UNAVAILABLE
    assert event.duration_ms is None
    assert event.model_version_id is None
    assert event.metadata is None
    assert event.tags is None


def test_invalid_enum_rejected():
    with pytest.raises(ValidationError):
        UsageEventCreate(**_payload(status="ok"))
    with pytest.raises(ValidationError):
        UsageEventCreate(**_payload(source="spreadsheet"))


def test_naive_started_at_rejected():
    with pytest.raises(ValidationError):
        UsageEventCreate(**_payload(started_at=datetime(2026, 10, 5, 10, 0, 0)))  # noqa: DTZ001 - naive timestamp is the point


def test_completed_before_started_rejected():
    with pytest.raises(ValidationError):
        UsageEventCreate(**_payload(completed_at=NOW - timedelta(milliseconds=1), duration_ms=1))


def test_negative_metrics_rejected():
    with pytest.raises(ValidationError):
        UsageEventCreate(**_payload(duration_ms=-1))
    with pytest.raises(ValidationError):
        UsageEventCreate(**_payload(input_tokens=-1))
    with pytest.raises(ValidationError):
        UsageEventCreate(**_payload(estimated_cost=-1))
    with pytest.raises(ValidationError):
        UsageEventCreate(**_payload(retry_count=-1))
    with pytest.raises(ValidationError):
        UsageEventCreate(**_payload(provider_status_code=99))


def test_error_fields_rejected_on_success():
    with pytest.raises(ValidationError):
        UsageEventCreate(**_payload(error_type="TimeoutError"))
    with pytest.raises(ValidationError):
        UsageEventCreate(**_payload(error_message="boom"))


def test_error_status_allows_missing_detail():
    event = UsageEventCreate(**_payload(status=UsageStatus.ERROR))
    assert event.error_type is None


def test_error_status_with_detail_accepted():
    event = UsageEventCreate(
        **_payload(status=UsageStatus.ERROR, error_type="TimeoutError", error_code="ETIMEDOUT")
    )
    assert event.error_type == "TimeoutError"


def test_event_id_length_limit():
    with pytest.raises(ValidationError):
        UsageEventCreate(**_payload(event_id="x" * 129))
    with pytest.raises(ValidationError):
        UsageEventCreate(**_payload(event_id=""))


def test_metadata_secret_key_rejected():
    with pytest.raises(ValidationError):
        UsageEventCreate(**_payload(metadata={"api_key": "sk-123"}))


def test_metadata_size_limit():
    with pytest.raises(ValidationError):
        UsageEventCreate(**_payload(metadata={"blob": "x" * 11_000}))


def test_tags_key_count_limit():
    with pytest.raises(ValidationError):
        UsageEventCreate(**_payload(tags={f"k{i}": "v" for i in range(51)}))


def test_tags_must_be_string_values():
    with pytest.raises(ValidationError):
        UsageEventCreate(**_payload(tags={"count": 3}))


def test_batch_requires_at_least_one_event():
    with pytest.raises(ValidationError):
        UsageEventBatchCreate(events=[])


def test_sanitize_truncates_long_error_message():
    message = sanitize_error_message("boom " * 1000)
    assert len(message) <= 2000


def test_sanitize_redacts_bearer_token_and_api_key():
    raw = "401 from https://api with Authorization: Bearer sk-abc123 and api_key=sk-secret42"
    message = sanitize_error_message(raw)
    assert "sk-abc123" not in message
    assert "sk-secret42" not in message
    assert "REDACTED" in message


def test_sanitize_drops_stack_trace():
    raw = "ValueError: bad input\nTraceback (most recent call last):\n  File x, line 1"
    message = sanitize_error_message(raw)
    assert "Traceback" not in message
    assert "bad input" in message


def test_fingerprint_deterministic_and_payload_sensitive():
    a = UsageEventCreate(**_payload(input_tokens=10))
    b = UsageEventCreate(**_payload(input_tokens=10))
    c = UsageEventCreate(**_payload(input_tokens=11))
    assert payload_fingerprint(a) == payload_fingerprint(b)
    assert payload_fingerprint(a) != payload_fingerprint(c)
    assert len(payload_fingerprint(a)) == 64
