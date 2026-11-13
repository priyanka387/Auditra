"""Integration tests: Discovered Asset Catalog service and persistence."""

import time
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.core.config import settings
from app.modules.asset_discovery import repository
from app.modules.asset_discovery.connectors.base import DiscoveryObservation
from app.modules.asset_discovery.enums import (
    AssetType,
    DiscoveredAssetStatus,
    DiscoverySourceType,
    ObservationOutcome,
)
from app.modules.asset_discovery.errors import (
    DiscoveredAssetNotFoundError,
    DiscoveryRunNotFoundError,
    DiscoverySourceNotFoundError,
    DuplicateDiscoveredAssetError,
    InvalidDiscoveredAssetError,
    InvalidDiscoveredAssetQueryError,
)
from app.modules.asset_discovery.schemas import (
    DiscoveredAssetFilter,
    DiscoverySourceCreate,
)
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


def test_create_new_discovered_asset(db, source, run):
    asset, outcome = _service(db).record_observation(_observation(source, run))

    assert outcome is ObservationOutcome.CREATED
    assert asset.asset_type == AssetType.MODEL_REFERENCE.value
    assert asset.name == "gpt-4o"
    assert asset.normalized_identity == "MODEL_REFERENCE|gpt-4o"
    assert asset.source_id == source.id
    assert asset.last_seen_run_id == run.id
    assert asset.status == DiscoveredAssetStatus.ACTIVE.value
    assert asset.first_seen_at == asset.last_seen_at
    assert asset.created_at.tzinfo is not None
    assert asset.updated_at.tzinfo is not None
    assert asset.metadata_ == {
        "key": "model",
        "observation_type": "model_reference",
        "file_path": "app/llm.py",
        "detection_rule": "model.assignment",
        "line_number": 2,
    }
    assert _service(db).get(asset.id).id == asset.id


def test_reobservation_preserves_first_seen_and_advances_last_seen(db, source, run):
    asset, _ = _service(db).record_observation(_observation(source, run))
    first_seen = asset.first_seen_at

    time.sleep(0.02)
    second_run = DiscoveryRunService(db, TENANT).create(source.id)
    refreshed, outcome = _service(db).record_observation(
        _observation(source, second_run, file_path="app/other.py", line_number=7)
    )

    assert outcome is ObservationOutcome.UNCHANGED
    assert refreshed.id == asset.id
    assert refreshed.first_seen_at == first_seen  # original creation time kept
    assert refreshed.last_seen_at > first_seen
    assert refreshed.last_seen_run_id == second_run.id
    # first-seen observation wins: unchanged source does not churn metadata
    assert refreshed.metadata_["file_path"] == "app/llm.py"
    assert refreshed.metadata_["line_number"] == 2


def test_reobservation_with_new_metadata_key_reports_updated(db, source, run):
    asset, _ = _service(db).record_observation(
        _observation(source, run, metadata={"framework": "langchain"})
    )
    refreshed, outcome = _service(db).record_observation(
        _observation(
            source,
            run,
            metadata={"framework": "langchain", "env_var": "OPENAI_API_KEY"},
        )
    )
    assert refreshed.id == asset.id
    assert outcome is ObservationOutcome.UPDATED
    assert refreshed.metadata_["env_var"] == "OPENAI_API_KEY"
    assert refreshed.metadata_["framework"] == "langchain"


def test_record_observations_counts_batch_outcomes(db, source, run):
    summary = _service(db).record_observations(
        [
            _observation(source, run),
            _observation(source, run, file_path="app/second.py", line_number=9),
            _observation(source, run, name="langchain", observation_type="framework_reference"),
            _observation(source, run, name="sk-proj-abc123def456"),
        ]
    )
    assert summary.created == 2
    assert summary.unchanged == 1
    assert summary.skipped == 1
    assert summary.updated == 0
    assert summary.recorded == 3


def test_unknown_identity_in_same_source_creates_second_asset(db, source, run):
    _service(db).record_observation(_observation(source, run))
    _service(db).record_observation(_observation(source, run, name="claude-3-opus"))
    _, total = _service(db).list(DiscoveredAssetFilter())
    assert total == 2


def test_same_identity_from_two_sources_stays_separate(db, source, run, tmp_path):
    other = DiscoverySourceService(db, TENANT).create(
        DiscoverySourceCreate(
            name="billing-service",
            source_type=DiscoverySourceType.REPOSITORY,
            config={"location": str(tmp_path)},
        )
    )
    other_run = DiscoveryRunService(db, TENANT).create(other.id)

    first, _ = _service(db).record_observation(_observation(source, run))
    second, _ = _service(db).record_observation(_observation(other, other_run))

    assert first.id != second.id
    assert first.normalized_identity == second.normalized_identity
    _, total = _service(db).list(DiscoveredAssetFilter())
    assert total == 2


def test_invalid_observations_raise(db, source, run):
    service = _service(db)
    with pytest.raises(InvalidDiscoveredAssetError):
        service.record_observation(_observation(source, run, name="   "))
    with pytest.raises(InvalidDiscoveredAssetError):
        service.record_observation(_observation(source, run, observation_type="mystery"))
    with pytest.raises(InvalidDiscoveredAssetError):
        service.record_observation(_observation(source, run, file_path="/etc/passwd"))
    with pytest.raises(InvalidDiscoveredAssetError):
        service.record_observation(_observation(source, run, line_number=0))


def test_missing_source_is_rejected(db, run):
    observation = _observation(None, run, source_id=uuid4())
    with pytest.raises(DiscoverySourceNotFoundError):
        _service(db).record_observation(observation)


def test_missing_run_is_rejected(db, source):
    observation = _observation(source, None, run_id=uuid4())
    with pytest.raises(DiscoveryRunNotFoundError):
        _service(db).record_observation(observation)


def test_recording_is_tenant_scoped(db, source, run):
    with pytest.raises(DiscoverySourceNotFoundError):
        _service(db, tenant_id=uuid4()).record_observation(_observation(source, run))

    asset, _ = _service(db).record_observation(_observation(source, run))
    with pytest.raises(DiscoveredAssetNotFoundError):
        _service(db, tenant_id=uuid4()).get(asset.id)
    _, total = _service(db, tenant_id=uuid4()).list(DiscoveredAssetFilter())
    assert total == 0


def test_secret_shaped_observation_is_skipped_not_persisted(db, source, run):
    asset, outcome = _service(db).record_observation(
        _observation(source, run, name="sk-proj-abc123def456")
    )
    assert outcome is ObservationOutcome.SKIPPED
    assert asset is None
    _, total = _service(db).list(DiscoveredAssetFilter())
    assert total == 0


def test_oversized_name_is_skipped_not_persisted(db, source, run):
    asset, outcome = _service(db).record_observation(
        _observation(source, run, name="a" * 300)
    )
    assert outcome is ObservationOutcome.SKIPPED
    assert asset is None
    _, total = _service(db).list(DiscoveredAssetFilter())
    assert total == 0


def test_uniqueness_constraint_rejects_duplicate_identity(db, source, run):
    _service(db).record_observation(_observation(source, run))

    duplicate = repository.create_discovered_asset(
        db,
        id=uuid4(),
        tenant_id=TENANT,
        source_id=source.id,
        last_seen_run_id=run.id,
        asset_type=AssetType.MODEL_REFERENCE.value,
        name="gpt-4o",
        normalized_identity="MODEL_REFERENCE|gpt-4o",
        status=DiscoveredAssetStatus.ACTIVE.value,
        metadata_={},
        first_seen_at=datetime.now(UTC),
        last_seen_at=datetime.now(UTC),
    )
    db.add(duplicate)
    with pytest.raises(DuplicateDiscoveredAssetError):
        repository.commit_asset(db)

    _, total = _service(db).list(DiscoveredAssetFilter())
    assert total == 1


def test_get_returns_404_for_unknown_asset(db):
    with pytest.raises(DiscoveredAssetNotFoundError):
        _service(db).get(uuid4())


def test_list_filters_by_type_source_status_and_search(db, source, run, tmp_path):
    service = _service(db)
    service.record_observation(_observation(source, run))
    service.record_observation(
        _observation(source, run, name="langchain", observation_type="framework_reference")
    )
    other = DiscoverySourceService(db, TENANT).create(
        DiscoverySourceCreate(
            name="other-service",
            source_type=DiscoverySourceType.REPOSITORY,
            config={"location": str(tmp_path)},
        )
    )
    other_run = DiscoveryRunService(db, TENANT).create(other.id)
    service.record_observation(_observation(other, other_run))

    by_type, total = service.list(DiscoveredAssetFilter(asset_type=AssetType.TOOL))
    assert total == 1
    assert by_type[0].name == "langchain"

    by_source, total = service.list(DiscoveredAssetFilter(source_id=source.id))
    assert total == 2
    assert all(asset.source_id == source.id for asset in by_source)

    _by_status, total = service.list(DiscoveredAssetFilter(status=DiscoveredAssetStatus.ACTIVE))
    assert total == 3

    _searched, total = service.list(DiscoveredAssetFilter(search="gpt"))
    assert total == 2  # the same name exists in both sources

    scoped, total = service.list(DiscoveredAssetFilter(source_id=source.id, search="gpt"))
    assert total == 1
    assert scoped[0].name == "gpt-4o"


def test_list_paginates_and_sorts(db, source, run):
    service = _service(db)
    for index in range(3):
        service.record_observation(_observation(source, run, name=f"model-{index}"))

    first, total = service.list(DiscoveredAssetFilter(page=1, page_size=2))
    second, _ = service.list(DiscoveredAssetFilter(page=2, page_size=2))
    assert total == 3
    assert len(first) == 2
    assert len(second) == 1
    assert {asset.id for asset in first}.isdisjoint(asset.id for asset in second)

    ascending, _ = service.list(DiscoveredAssetFilter(sort_by="name", sort_order="asc"))
    assert [asset.name for asset in ascending] == ["model-0", "model-1", "model-2"]


def test_list_rejects_unsupported_sort_field(db):
    with pytest.raises(InvalidDiscoveredAssetQueryError):
        _service(db).list(DiscoveredAssetFilter(sort_by="relevance"))


def test_deleted_source_still_holds_its_assets(db, source, run):
    # referential integrity: assets keep their source (RESTRICT), and the
    # catalog remains readable after the source is archived
    asset, _ = _service(db).record_observation(_observation(source, run))
    DiscoverySourceService(db, TENANT).delete(source.id)

    fetched = _service(db).get(asset.id)
    assert fetched.source_id == source.id
