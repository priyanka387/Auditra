from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.asset_discovery.enums import DiscoveryRunStatus
from app.modules.asset_discovery.schemas import (
    DiscoveryRunCreate,
    DiscoveryRunFilter,
    DiscoveryRunResponse,
)


def _response_payload(**overrides) -> dict:
    payload = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "source_id": uuid4(),
        "status": DiscoveryRunStatus.PENDING,
        "started_at": None,
        "completed_at": None,
        "assets_detected": 0,
        "assets_created": 0,
        "assets_updated": 0,
        "assets_unchanged": 0,
        "error_count": 0,
        "error_summary": None,
        "created_at": datetime(2026, 11, 12, 12, 0, tzinfo=UTC),
        "updated_at": datetime(2026, 11, 12, 12, 0, tzinfo=UTC),
    }
    payload.update(overrides)
    return payload


def test_create_accepts_empty_payload():
    payload = DiscoveryRunCreate()
    assert payload.model_dump() == {}


def test_create_rejects_extra_fields():
    with pytest.raises(ValidationError):
        DiscoveryRunCreate(schedule="*/5 * * * *")


def test_filter_defaults():
    filters = DiscoveryRunFilter()
    assert filters.page == 1
    assert filters.page_size == 25
    assert filters.source_id is None
    assert filters.status is None
    assert filters.started_from is None
    assert filters.started_to is None
    assert filters.sort_by == "started_at"
    assert filters.sort_order == "desc"


def test_filter_accepts_source_status_and_date_window():
    source_id = uuid4()
    filters = DiscoveryRunFilter(
        source_id=source_id,
        status=DiscoveryRunStatus.RUNNING,
        started_from=datetime(2026, 11, 1, tzinfo=UTC),
        started_to=datetime(2026, 11, 12, tzinfo=UTC),
    )
    assert filters.source_id == source_id
    assert filters.status == DiscoveryRunStatus.RUNNING
    assert filters.started_from == datetime(2026, 11, 1, tzinfo=UTC)
    assert filters.started_to == datetime(2026, 11, 12, tzinfo=UTC)


def test_filter_rejects_invalid_status():
    with pytest.raises(ValidationError):
        DiscoveryRunFilter(status="BOGUS")


def test_filter_rejects_invalid_page():
    with pytest.raises(ValidationError):
        DiscoveryRunFilter(page=0)


def test_filter_rejects_oversized_page_size():
    with pytest.raises(ValidationError):
        DiscoveryRunFilter(page_size=1000)


def test_filter_rejects_extra_fields():
    with pytest.raises(ValidationError):
        DiscoveryRunFilter(include_archived=True)


def test_filter_rejects_naive_date_bounds():
    with pytest.raises(ValidationError):
        DiscoveryRunFilter(started_from=datetime(2026, 11, 1))  # noqa: DTZ001 - naive is rejected
    with pytest.raises(ValidationError):
        DiscoveryRunFilter(started_to=datetime(2026, 11, 1))  # noqa: DTZ001 - naive is rejected


def test_response_accepts_timezone_aware_timestamps():
    response = DiscoveryRunResponse(**_response_payload())
    assert response.status == DiscoveryRunStatus.PENDING
    assert response.created_at.tzinfo is not None


def test_response_rejects_naive_timestamps():
    with pytest.raises(ValidationError):
        DiscoveryRunResponse(
            **_response_payload(created_at=datetime(2026, 11, 12, 12, 0))  # noqa: DTZ001
        )
    with pytest.raises(ValidationError):
        DiscoveryRunResponse(
            **_response_payload(started_at=datetime(2026, 11, 12, 12, 0))  # noqa: DTZ001
        )


def test_response_rejects_invalid_status():
    with pytest.raises(ValidationError):
        DiscoveryRunResponse(**_response_payload(status="BOGUS"))
