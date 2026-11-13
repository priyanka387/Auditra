"""Integration tests: Discovery Source -> Run -> scan -> Discovered Asset Catalog."""

import pytest

from app.core.config import settings
from app.modules.asset_discovery.enums import (
    DiscoveredAssetStatus,
    DiscoveryRunStatus,
    DiscoverySourceType,
)
from app.modules.asset_discovery.errors import DiscoverySourceUnreachableError
from app.modules.asset_discovery.execution import DiscoveryExecutionService
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
def repo(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text('import openai\nmodel = "gpt-4o"\n', encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("langchain>=0.1.0\n", encoding="utf-8")
    return tmp_path


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


def _execute(db, run):
    return DiscoveryExecutionService(db, TENANT).execute(run.id)


def _assets(db, **filters):
    return DiscoveredAssetService(db, TENANT).list(DiscoveredAssetFilter(**filters))


def _refetch_run(db, run_id):
    db.expunge_all()
    return DiscoveryRunService(db, TENANT).get(run_id)


def test_execution_persists_observed_assets_in_catalog(db, repo):
    source = _source(db, repo)
    run = _run(db, source)

    result = _execute(db, run)

    assert result.catalog.created == len(result.observations)
    assert result.catalog.updated == 0
    assert result.catalog.unchanged == 0

    items, total = _assets(db)
    assert total == len(result.observations)
    identities = {asset.normalized_identity for asset in items}
    assert identities == {
        "AI_SERVICE|openai",
        "MODEL_REFERENCE|gpt-4o",
        "TOOL|langchain",
    }

    fetched_run = _refetch_run(db, run.id)
    assert fetched_run.status == DiscoveryRunStatus.COMPLETED
    assert fetched_run.assets_detected == len(result.observations)
    assert fetched_run.assets_created == len(result.observations)
    assert fetched_run.assets_updated == 0
    assert fetched_run.assets_unchanged == 0

    for asset in items:
        assert asset.source_id == source.id
        assert asset.last_seen_run_id == run.id
        assert asset.status == DiscoveredAssetStatus.ACTIVE.value
        assert asset.first_seen_at == asset.last_seen_at
        assert asset.metadata_["file_path"]
        assert asset.metadata_["detection_rule"]


def test_rerun_against_unchanged_source_creates_no_duplicates(db, repo):
    source = _source(db, repo)
    first = _execute(db, _run(db, source))
    _, before_total = _assets(db)
    first_seen = {
        asset.id: asset.first_seen_at
        for asset in _assets(db)[0]
    }

    second = _execute(db, _run(db, source))

    _, after_total = _assets(db)
    assert after_total == before_total == len(second.observations)
    assert second.catalog.created == 0
    assert second.catalog.updated == 0
    assert second.catalog.unchanged == len(second.observations)
    assert first.catalog.created == len(first.observations)

    items, _ = _assets(db)
    for asset in items:
        assert asset.first_seen_at == first_seen[asset.id]  # creation time untouched
        assert asset.last_seen_run_id == second.run.id  # latest run observed it

    fetched_run = _refetch_run(db, second.run.id)
    assert fetched_run.assets_created == 0
    assert fetched_run.assets_unchanged == len(second.observations)
    assert (
        fetched_run.assets_created
        + fetched_run.assets_updated
        + fetched_run.assets_unchanged
        == fetched_run.assets_detected
    )


def test_same_asset_in_multiple_files_maps_to_one_catalog_entry(db, tmp_path):
    (tmp_path / "a.py").write_text('model = "gpt-4o"\n', encoding="utf-8")
    (tmp_path / "b.py").write_text('model = "gpt-4o"\n', encoding="utf-8")
    source = _source(db, tmp_path, name="multi-file-service")

    result = _execute(db, _run(db, source))

    assert len(result.observations) == 2
    items, total = _assets(db, source_id=source.id)
    assert total == 1
    assert items[0].normalized_identity == "MODEL_REFERENCE|gpt-4o"
    assert result.catalog.created == 1
    assert result.catalog.unchanged == 1


def test_partial_scan_still_persists_catalog_entries(db, tmp_path):
    (tmp_path / "code.py").write_text("import anthropic\n", encoding="utf-8")
    (tmp_path / "legacy.txt").write_bytes(b"caf\xe9\n")  # not valid UTF-8
    source = _source(db, tmp_path, name="partial-source")

    result = _execute(db, _run(db, source))

    assert len(result.issues) == 1
    assert result.catalog.created == len(result.observations)
    _, total = _assets(db, source_id=source.id)
    assert total == len(result.observations)

    fetched_run = _refetch_run(db, result.run.id)
    assert fetched_run.status == DiscoveryRunStatus.PARTIAL
    assert fetched_run.assets_created == len(result.observations)


def test_failed_scan_leaves_catalog_empty(db, tmp_path):
    source = _source(db, tmp_path / "missing-repo", name="missing-source")

    with pytest.raises(DiscoverySourceUnreachableError):
        _execute(db, _run(db, source))

    _, total = _assets(db)
    assert total == 0
