"""Integration tests: deduplication within the Discovered Asset Catalog."""

import time
from uuid import uuid4

import pytest

from app.core.config import settings
from app.modules.asset_discovery import repository
from app.modules.asset_discovery.classification import classify
from app.modules.asset_discovery.connectors.base import DiscoveryObservation
from app.modules.asset_discovery.deduplication import AssetDeduplicationService
from app.modules.asset_discovery.enums import (
    DiscoverySourceType,
    ObservationOutcome,
)
from app.modules.asset_discovery.errors import (
    DiscoveredAssetNotFoundError,
    InvalidDiscoveredAssetError,
)
from app.modules.asset_discovery.execution import DiscoveryExecutionService
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


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text('import openai\nmodel = "gpt-4o"\n', encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("langchain>=0.1.0\n", encoding="utf-8")
    return tmp_path


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


def test_create_records_run_link(db, source, run):
    asset, _ = _service(db).record_observation(_observation(source, run))

    assert _service(db).observed_run_ids(asset.id) == [run.id]


def test_reobservation_in_same_run_records_single_link(db, source, run):
    asset, _ = _service(db).record_observation(_observation(source, run))
    refreshed, _ = _service(db).record_observation(
        _observation(source, run, file_path="app/other.py", line_number=9)
    )

    assert refreshed.id == asset.id
    assert _service(db).observed_run_ids(asset.id) == [run.id]


def test_same_asset_across_three_runs_records_all_runs(db, source, run):
    service = _service(db)
    first, _ = service.record_observation(_observation(source, run))
    first_seen = first.first_seen_at

    time.sleep(0.02)
    run2 = DiscoveryRunService(db, TENANT).create(source.id)
    second, _ = service.record_observation(_observation(source, run2))

    time.sleep(0.02)
    run3 = DiscoveryRunService(db, TENANT).create(source.id)
    third, _ = service.record_observation(_observation(source, run3))

    assert second.id == first.id
    assert third.id == first.id
    assert third.first_seen_at == first_seen
    assert third.last_seen_at > first_seen
    assert third.last_seen_run_id == run3.id
    assert service.observed_run_ids(first.id) == [run.id, run2.id, run3.id]


def test_run_side_lookup_returns_observed_assets(db, source, run):
    asset, _ = _service(db).record_observation(_observation(source, run))
    run2 = DiscoveryRunService(db, TENANT).create(source.id)
    _service(db).record_observation(_observation(source, run2))

    assert asset.id in repository.list_run_asset_ids(db, TENANT, run2.id)
    assert asset.id in repository.list_run_asset_ids(db, TENANT, run.id)


def test_observed_run_ids_rejects_unknown_asset(db, source, run):
    with pytest.raises(DiscoveredAssetNotFoundError):
        _service(db).observed_run_ids(uuid4())


def test_batch_across_two_sources_prefetches_per_source(db, source, run, tmp_path):
    other = DiscoverySourceService(db, TENANT).create(
        DiscoverySourceCreate(
            name="billing-service",
            source_type=DiscoverySourceType.REPOSITORY,
            config={"location": str(tmp_path)},
        )
    )
    other_run = DiscoveryRunService(db, TENANT).create(other.id)

    summary = _service(db).record_observations(
        [
            _observation(source, run),
            _observation(source, run, file_path="app/second.py", line_number=9),
            _observation(other, other_run),
            _observation(other, other_run, file_path="app/second.py", line_number=9),
        ]
    )

    # identity is scoped per source: two sources x one identity = 2 assets
    assert summary.created == 2
    assert summary.unchanged == 2
    assert summary.updated == 0
    _, total = _service(db).list(DiscoveredAssetFilter())
    assert total == 2


def test_batch_with_invalid_observation_persists_nothing(db, source, run):
    with pytest.raises(InvalidDiscoveredAssetError):
        _service(db).record_observations(
            [
                _observation(source, run),
                _observation(source, run, observation_type="mystery"),
            ]
        )

    # the batch is validated before anything persists
    _, total = _service(db).list(DiscoveredAssetFilter())
    assert total == 0


def _source(db, location, name="payments-service"):
    return DiscoverySourceService(db, TENANT).create(
        DiscoverySourceCreate(
            name=name,
            source_type=DiscoverySourceType.REPOSITORY,
            config={"location": str(location)},
        )
    )


def _run(db, source):
    return DiscoveryRunService(db, TENANT).create(source.id)


def _execute(db, source):
    run = _run(db, source)
    result = DiscoveryExecutionService(db, TENANT).execute(run.id)
    return run, result


def _refetch_run(db, run_id):
    db.expunge_all()
    return DiscoveryRunService(db, TENANT).get(run_id)


def _catalog_ids(db):
    items, _ = _service(db).list(DiscoveredAssetFilter())
    return {asset.id for asset in items}


def test_three_runs_against_unchanged_repo_yield_identical_catalog(db, repo):
    source = _source(db, repo)
    run1, first = _execute(db, source)
    assert first.catalog.created == 3
    assert first.catalog.updated == 0
    assert first.catalog.unchanged == 0
    fetched = _refetch_run(db, run1.id)
    assert (fetched.assets_created, fetched.assets_updated, fetched.assets_unchanged) == (3, 0, 0)

    ids_after_first = _catalog_ids(db)
    first_seen = {
        asset.id: asset.first_seen_at for asset in _service(db).list(DiscoveredAssetFilter())[0]
    }

    run2, second = _execute(db, source)
    assert (second.catalog.created, second.catalog.updated, second.catalog.unchanged) == (0, 0, 3)
    assert _catalog_ids(db) == ids_after_first
    fetched = _refetch_run(db, run2.id)
    assert (fetched.assets_created, fetched.assets_updated, fetched.assets_unchanged) == (0, 0, 3)

    run3, third = _execute(db, source)
    assert (third.catalog.created, third.catalog.updated, third.catalog.unchanged) == (0, 0, 3)
    assert _catalog_ids(db) == ids_after_first
    fetched = _refetch_run(db, run3.id)
    assert (fetched.assets_created, fetched.assets_updated, fetched.assets_unchanged) == (0, 0, 3)

    items, total = _service(db).list(DiscoveredAssetFilter())
    assert total == len(ids_after_first) == 3
    for asset in items:
        assert asset.first_seen_at == first_seen[asset.id]  # creation time frozen at run 1
        assert asset.last_seen_run_id == run3.id
        assert _service(db).observed_run_ids(asset.id) == [run1.id, run2.id, run3.id]


def test_same_path_and_name_in_two_sources_never_merges(db, source, run, tmp_path):
    other = _source(db, tmp_path, name="billing-service")
    other_run = DiscoveryRunService(db, TENANT).create(other.id)

    first, _ = _service(db).record_observation(
        _observation(source, run, file_path="app/llm.py", line_number=5)
    )
    second, _ = _service(db).record_observation(
        _observation(other, other_run, file_path="app/llm.py", line_number=5)
    )

    assert first.id != second.id
    assert first.normalized_identity == second.normalized_identity
    assert _service(db).observed_run_ids(first.id) == [run.id]
    assert _service(db).observed_run_ids(second.id) == [other_run.id]


def test_cross_source_shared_identity_stays_separate(db, source, run, tmp_path):
    other = _source(db, tmp_path, name="billing-service")
    classification = classify(_observation(source, run))
    dedup = AssetDeduplicationService(db, TENANT)

    asset, _ = _service(db).record_observation(_observation(source, run))

    # source B's identical classification key never matches source A's asset
    assert dedup.find_duplicate(dedup.build_key(source.id, classification)).id == asset.id
    assert dedup.find_duplicate(dedup.build_key(other.id, classification)) is None


def test_similar_names_do_not_merge_in_one_source(db, source, run):
    service = _service(db)
    created = [
        service.record_observation(_observation(source, run, name=name))[0]
        for name in ("gpt-4o", "gpt-4o-mini", "GPT-4o")
    ]

    assert len({asset.id for asset in created}) == 3
    _, total = service.list(DiscoveredAssetFilter())
    assert total == 3


def test_same_framework_in_two_sources_stays_separate(db, source, run, tmp_path):
    other = _source(db, tmp_path, name="billing-service")
    other_run = DiscoveryRunService(db, TENANT).create(other.id)
    service = _service(db)

    first, _ = service.record_observation(
        _observation(source, run, name="langchain", observation_type="framework_reference")
    )
    second, _ = service.record_observation(
        _observation(other, other_run, name="langchain", observation_type="framework_reference")
    )

    assert first.id != second.id
    assert first.normalized_identity == second.normalized_identity == "TOOL|langchain"
    _, total = service.list(DiscoveredAssetFilter())
    assert total == 2
