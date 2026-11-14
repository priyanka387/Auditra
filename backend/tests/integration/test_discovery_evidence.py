"""Integration tests: evidence rows produced by discovery execution (SF9)."""

import pytest

from app.core.config import settings
from app.modules.asset_discovery.enums import DiscoverySourceType, EvidenceType
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
    (tmp_path / "app" / "main.py").write_text(
        'import openai\nmodel = "gpt-4o"\n', encoding="utf-8"
    )
    (tmp_path / "app" / "client.py").write_text("import openai\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("langchain>=0.1.0\n", encoding="utf-8")
    return tmp_path


def _source(db, location, name="payments-service"):
    return DiscoverySourceService(db, TENANT).create(
        DiscoverySourceCreate(
            name=name, source_type=DiscoverySourceType.REPOSITORY,
            config={"location": str(location)},
        )
    )


def _execute(db, source):
    run = DiscoveryRunService(db, TENANT).create(source.id)
    return DiscoveryExecutionService(db, TENANT).execute(run.id)


def _evidence_rows(db, asset_id):
    from sqlalchemy import select
    from app.modules.asset_discovery.models import DiscoveryEvidence

    return list(
        db.scalars(
            select(DiscoveryEvidence)
            .where(DiscoveryEvidence.tenant_id == TENANT,
                   DiscoveryEvidence.discovered_asset_id == asset_id)
            .order_by(DiscoveryEvidence.file_path, DiscoveryEvidence.line_number)
        )
    )


def _all_evidence_rows(db):
    from sqlalchemy import select
    from app.modules.asset_discovery.models import DiscoveryEvidence

    return list(
        db.scalars(
            select(DiscoveryEvidence)
            .where(DiscoveryEvidence.tenant_id == TENANT)
            .order_by(DiscoveryEvidence.file_path, DiscoveryEvidence.line_number)
        )
    )


def _assets_by_identity(db):
    items, _ = DiscoveredAssetService(db, TENANT).list(DiscoveredAssetFilter())
    return {asset.normalized_identity: asset for asset in items}


def test_execution_records_one_evidence_row_per_observation(db, repo):
    source = _source(db, repo)

    result = _execute(db, source)

    assets = _assets_by_identity(db)
    assert set(assets) == {
        "AI_SERVICE|openai",
        "MODEL_REFERENCE|gpt-4o",
        "TOOL|langchain",
    }

    openai_rows = _evidence_rows(db, assets["AI_SERVICE|openai"].id)
    model_rows = _evidence_rows(db, assets["MODEL_REFERENCE|gpt-4o"].id)
    tool_rows = _evidence_rows(db, assets["TOOL|langchain"].id)
    assert len(openai_rows) == 2
    assert len(model_rows) == 1
    assert len(tool_rows) == 1

    all_rows = _all_evidence_rows(db)
    assert len(all_rows) == len(result.observations)

    run = DiscoveryRunService(db, TENANT).get(result.run.id)
    valid_types = {member.value for member in EvidenceType}
    for row in all_rows:
        assert row.evidence_type in valid_types
        assert row.detection_rule
        assert not row.file_path.startswith("/")
        assert run.started_at <= row.observed_at <= run.completed_at
        assert row.discovery_run_id == run.id
        assert row.discovery_source_id == source.id

    model_row = model_rows[0]
    assert model_row.discovered_asset_id == assets["MODEL_REFERENCE|gpt-4o"].id
    assert model_row.file_path == "app/main.py"
    assert model_row.line_number == 2
    assert model_row.detection_rule == "model.assignment"
    assert model_row.metadata_ == {"key": "model"}


def test_multiple_observations_of_one_asset_produce_multiple_evidence_rows(db, repo):
    source = _source(db, repo)

    _execute(db, source)

    assets = _assets_by_identity(db)
    openai_asset = assets["AI_SERVICE|openai"]
    rows = _evidence_rows(db, openai_asset.id)
    assert {(row.file_path, row.line_number, row.detection_rule) for row in rows} == {
        ("app/client.py", 1, "provider.openai.import"),
        ("app/main.py", 1, "provider.openai.import"),
    }


def test_second_run_appends_evidence_without_mutating_previous_run_rows(db, repo):
    source = _source(db, repo)

    first = _execute(db, source)
    first_run_id = first.run.id
    first_seen = {
        asset.id: asset.first_seen_at for asset in _assets_by_identity(db).values()
    }
    snapshot = {
        (row.id, row.observed_at, row.file_path) for row in _all_evidence_rows(db)
    }
    assert snapshot  # run 1 produced evidence

    second = _execute(db, source)

    after = _all_evidence_rows(db)
    snapshot_after = {(row.id, row.observed_at, row.file_path) for row in after}
    assert snapshot <= snapshot_after  # run-1 rows untouched
    for row in after:
        if (row.id, row.observed_at, row.file_path) not in snapshot:
            assert row.discovery_run_id == second.run.id
        else:
            assert row.discovery_run_id == first_run_id
    assert len(after) == 2 * len(snapshot)

    for asset in _assets_by_identity(db).values():
        assert asset.first_seen_at == first_seen[asset.id]


def test_skipped_secret_shaped_observation_produces_no_asset_and_no_evidence(db, tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text(
        'model = "sk-proj-abcdef123"\n', encoding="utf-8"
    )
    source = _source(db, tmp_path, name="secret-shaped-service")

    result = _execute(db, source)

    for asset in _assets_by_identity(db).values():
        assert "sk-proj" not in asset.name
        assert "sk-proj" not in asset.normalized_identity
    for row in _all_evidence_rows(db):
        assert "sk-proj" not in str(row.metadata_)
        assert "sk-proj" not in row.file_path
    assert result.catalog.skipped >= 1


def test_evidence_survives_lifecycle_status_change(db, repo):
    source = _source(db, repo)

    _execute(db, source)
    asset = _assets_by_identity(db)["MODEL_REFERENCE|gpt-4o"]
    before = [(row.id, row.observed_at) for row in _evidence_rows(db, asset.id)]
    assert before

    asset.status = "DISAPPEARED"
    db.commit()

    after = [(row.id, row.observed_at) for row in _evidence_rows(db, asset.id)]
    assert after == before


def test_evidence_recording_does_not_touch_reconciliation_or_identity(db, repo):
    source = _source(db, repo)

    result = _execute(db, source)

    asset = _assets_by_identity(db)["MODEL_REFERENCE|gpt-4o"]
    assert asset.reconciliation_status == "UNMATCHED"
    assert asset.normalized_identity == "MODEL_REFERENCE|gpt-4o"
    assert asset.first_seen_at == asset.last_seen_at
    assert asset.last_seen_run_id == result.run.id
    assert asset.reconciled_model_id is None
