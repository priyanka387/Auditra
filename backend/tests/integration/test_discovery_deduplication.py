"""Integration tests: deduplication within the Discovered Asset Catalog."""

import pytest

from app.core.config import settings
from app.modules.asset_discovery.classification import classify
from app.modules.asset_discovery.connectors.base import DiscoveryObservation
from app.modules.asset_discovery.deduplication import AssetDeduplicationService
from app.modules.asset_discovery.enums import (
    DiscoverySourceType,
    ObservationOutcome,
)
from app.modules.asset_discovery.schemas import DiscoveredAssetFilter, DiscoverySourceCreate
from app.modules.asset_discovery.service import (
    DiscoveredAssetService,
    DiscoveryRunService,
    DiscoverySourceService,
)

TENANT = settings.default_tenant_id


@pytest.fixture
def source(db, tmp_path):
    return DiscoverySourceService(db, TENANT).create(
        DiscoverySourceCreate(
            name="payments-service",
            source_type=DiscoverySourceType.REPOSITORY,
            config={"location": str(tmp_path)},
        )
    )


@pytest.fixture
def run(db, source):
    return DiscoveryRunService(db, TENANT).create(source.id)


def _service(db, tenant_id=None) -> DiscoveredAssetService:
    return DiscoveredAssetService(db, tenant_id or TENANT)


def _observation(source, run, **overrides) -> DiscoveryObservation:
    payload = {
        "source_id": source.id if source is not None else None,
        "run_id": run.id if run is not None else None,
        "observation_type": "model_reference",
        "name": "gpt-4o",
        "provider": None,
        "file_path": "app/llm.py",
        "line_number": 2,
        "detection_rule": "model.assignment",
        "metadata": {"key": "model"},
    }
    payload.update(overrides)
    return DiscoveryObservation(**payload)


def test_dedup_service_returns_exact_match_and_none(db, source, run, tmp_path):
    asset, _ = _service(db).record_observation(_observation(source, run))
    observation = _observation(source, run)
    classification = classify(observation)

    dedup = AssetDeduplicationService(db, TENANT)
    match = dedup.find_duplicate(dedup.build_key(source.id, classification))
    assert match is not None
    assert match.id == asset.id

    # the same identity in another source is not a duplicate of it
    other = DiscoverySourceService(db, TENANT).create(
        DiscoverySourceCreate(
            name="billing-service",
            source_type=DiscoverySourceType.REPOSITORY,
            config={"location": str(tmp_path)},
        )
    )
    assert dedup.find_duplicate(dedup.build_key(other.id, classification)) is None


def test_reobservation_fills_previously_empty_metadata_value(db, source, run):
    first, _ = _service(db).record_observation(
        _observation(source, run, metadata={"framework": ""})
    )

    refreshed, outcome = _service(db).record_observation(
        _observation(source, run, metadata={"framework": "langchain"})
    )

    assert refreshed.id == first.id
    assert outcome is ObservationOutcome.UPDATED
    assert refreshed.metadata_["framework"] == "langchain"


def test_reobservation_preserves_non_empty_metadata_over_new_value(db, source, run):
    first, _ = _service(db).record_observation(
        _observation(source, run, metadata={"framework": "langchain"})
    )

    refreshed, outcome = _service(db).record_observation(
        _observation(source, run, metadata={"framework": "llamaindex"})
    )

    assert refreshed.id == first.id
    assert outcome is ObservationOutcome.UNCHANGED
    assert refreshed.metadata_["framework"] == "langchain"


def test_same_name_with_different_asset_types_stays_separate(db, source, run):
    service = _service(db)
    service.record_observation(
        _observation(source, run, name="openai", observation_type="provider_reference")
    )
    service.record_observation(
        _observation(source, run, name="openai", observation_type="framework_reference")
    )

    items, total = _service(db).list(DiscoveredAssetFilter())
    assert total == 2
    assert {asset.normalized_identity for asset in items} == {"AI_SERVICE|openai", "TOOL|openai"}
