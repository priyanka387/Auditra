"""Unit tests: Discovered Asset Catalog API schemas."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.asset_discovery.enums import (
    AssetType,
    DiscoveredAssetStatus,
    ReconciliationStatus,
)
from app.modules.asset_discovery.schemas import (
    DiscoveredAssetFilter,
    DiscoveredAssetListResponse,
    DiscoveredAssetResponse,
)


def _response_payload(**overrides) -> dict:
    payload = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "source_id": uuid4(),
        "last_seen_run_id": uuid4(),
        "asset_type": AssetType.MODEL_REFERENCE,
        "name": "gpt-4o",
        "normalized_identity": "MODEL_REFERENCE|gpt-4o",
        "status": DiscoveredAssetStatus.ACTIVE,
        "reconciliation_status": ReconciliationStatus.UNMATCHED,
        "reconciliation_reason": None,
        "reconciled_model_id": None,
        "metadata": {"file_path": "app/llm.py", "detection_rule": "model.assignment"},
        "first_seen_at": datetime(2026, 11, 13, 12, 0, tzinfo=UTC),
        "last_seen_at": datetime(2026, 11, 13, 12, 0, tzinfo=UTC),
        "created_at": datetime(2026, 11, 13, 12, 0, tzinfo=UTC),
        "updated_at": datetime(2026, 11, 13, 12, 0, tzinfo=UTC),
    }
    payload.update(overrides)
    return payload


def test_filter_defaults():
    filters = DiscoveredAssetFilter()
    assert filters.page == 1
    assert filters.page_size == 25
    assert filters.asset_type is None
    assert filters.source_id is None
    assert filters.status is None
    assert filters.reconciliation_status is None
    assert filters.search is None
    assert filters.sort_by == "last_seen_at"
    assert filters.sort_order == "desc"


def test_filter_accepts_supported_filters():
    source_id = uuid4()
    filters = DiscoveredAssetFilter(
        asset_type=AssetType.TOOL,
        source_id=source_id,
        status=DiscoveredAssetStatus.ACTIVE,
        reconciliation_status=ReconciliationStatus.MATCHED,
        search="lang",
    )
    assert filters.asset_type is AssetType.TOOL
    assert filters.source_id == source_id
    assert filters.status is DiscoveredAssetStatus.ACTIVE
    assert filters.reconciliation_status is ReconciliationStatus.MATCHED
    assert filters.search == "lang"


@pytest.mark.parametrize(
    "status",
    [
        DiscoveredAssetStatus.ACTIVE,
        DiscoveredAssetStatus.STALE,
        DiscoveredAssetStatus.DISAPPEARED,
        DiscoveredAssetStatus.IGNORED,
    ],
)
def test_filter_accepts_every_lifecycle_status(status):
    # Asset Lifecycle (Sub-feature 8) extends the catalog vocabulary, so
    # the status filter must accept each lifecycle state
    assert DiscoveredAssetFilter(status=status).status is status


def test_filter_rejects_invalid_asset_type_and_status():
    with pytest.raises(ValidationError):
        DiscoveredAssetFilter(asset_type="SPREADSHEET")
    with pytest.raises(ValidationError):
        DiscoveredAssetFilter(status="VANISHED")
    with pytest.raises(ValidationError):
        DiscoveredAssetFilter(reconciliation_status="SOULMATED")


def test_filter_rejects_invalid_pagination():
    with pytest.raises(ValidationError):
        DiscoveredAssetFilter(page=0)
    with pytest.raises(ValidationError):
        DiscoveredAssetFilter(page_size=1000)


def test_filter_rejects_oversized_search_and_extra_fields():
    with pytest.raises(ValidationError):
        DiscoveredAssetFilter(search="x" * 256)
    with pytest.raises(ValidationError):
        DiscoveredAssetFilter(include_archived=True)


def test_response_accepts_timezone_aware_timestamps():
    response = DiscoveredAssetResponse(**_response_payload())
    assert response.asset_type is AssetType.MODEL_REFERENCE
    assert response.status is DiscoveredAssetStatus.ACTIVE
    assert response.first_seen_at.tzinfo is not None


def test_response_rejects_naive_timestamps():
    with pytest.raises(ValidationError):
        DiscoveredAssetResponse(
            **_response_payload(first_seen_at=datetime(2026, 11, 13, 12, 0))  # noqa: DTZ001
        )
    with pytest.raises(ValidationError):
        DiscoveredAssetResponse(
            **_response_payload(last_seen_at=datetime(2026, 11, 13, 12, 0))  # noqa: DTZ001
        )


def test_response_rejects_unknown_asset_type_and_status():
    with pytest.raises(ValidationError):
        DiscoveredAssetResponse(**_response_payload(asset_type="SOMETHING"))
    with pytest.raises(ValidationError):
        DiscoveredAssetResponse(**_response_payload(status="VANISHED"))


@pytest.mark.parametrize(
    "status",
    [
        DiscoveredAssetStatus.ACTIVE,
        DiscoveredAssetStatus.STALE,
        DiscoveredAssetStatus.DISAPPEARED,
        DiscoveredAssetStatus.IGNORED,
    ],
)
def test_response_accepts_every_lifecycle_status(status):
    response = DiscoveredAssetResponse(**_response_payload(status=status))
    assert response.status is status


def test_response_reads_metadata_from_orm_attribute_name():
    # the ORM column attribute is ``metadata_`` (column name "metadata")
    payload = _response_payload(metadata={"framework": "langchain"})
    payload["metadata_"] = payload.pop("metadata")
    asset = SimpleNamespace(**payload)
    response = DiscoveredAssetResponse.model_validate(asset, from_attributes=True)
    assert response.metadata == {"framework": "langchain"}


def test_list_response_shape():
    listing = DiscoveredAssetListResponse(
        items=[DiscoveredAssetResponse(**_response_payload())],
        page=1,
        page_size=25,
        total=1,
        total_pages=1,
    )
    assert listing.total == 1
    assert listing.items[0].name == "gpt-4o"
