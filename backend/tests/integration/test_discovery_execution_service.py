"""Integration tests: Discovery Source -> Discovery Run -> connector execution."""

import pytest

from app.core.config import settings
from app.modules.asset_discovery.enums import (
    DiscoveryRunStatus,
    DiscoverySourceStatus,
    DiscoverySourceType,
)
from app.modules.asset_discovery.errors import (
    DiscoveryRunNotFoundError,
    DiscoverySourceArchivedError,
    DiscoverySourceDisabledError,
    DiscoverySourceUnreachableError,
    InvalidDiscoveryRunTransitionError,
    InvalidDiscoverySourceConfigError,
)
from app.modules.asset_discovery.execution import DiscoveryExecutionService
from app.modules.asset_discovery.schemas import DiscoverySourceCreate, DiscoverySourceUpdate
from app.modules.asset_discovery.service import DiscoveryRunService, DiscoverySourceService


def _execution_service(db, tenant_id=None) -> DiscoveryExecutionService:
    return DiscoveryExecutionService(db, tenant_id=tenant_id or settings.default_tenant_id)


def _source(db, location, name="payments-service") -> "object":
    return DiscoverySourceService(db, settings.default_tenant_id).create(
        DiscoverySourceCreate(
            name=name,
            source_type=DiscoverySourceType.REPOSITORY,
            config={"location": str(location)},
        )
    )


def _run(db, source) -> "object":
    return DiscoveryRunService(db, settings.default_tenant_id).create(source.id)


def _observation_keys(result):
    return [
        (obs.file_path, obs.line_number, obs.detection_rule, obs.name)
        for obs in result.observations
    ]


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text('import openai\nmodel = "gpt-4o"\n', encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("langchain>=0.1.0\n", encoding="utf-8")
    return tmp_path


def _refetch_run(db, run_id):
    db.expunge_all()
    return DiscoveryRunService(db, settings.default_tenant_id).get(run_id)


def test_successful_discovery_completes_run_and_collects_observations(db, repo):
    source = _source(db, repo)
    run = _run(db, source)
    service = _execution_service(db)

    result = service.execute(run.id)

    assert result.connector == "repository"
    assert result.observations
    assert result.issues == ()
    assert result.files_scanned == 2
    rules = {obs.detection_rule for obs in result.observations}
    assert "provider.openai.import" in rules
    assert "model.assignment" in rules
    assert "framework.langchain.manifest" in rules
    assert all(obs.source_id == source.id for obs in result.observations)
    assert all(obs.run_id == run.id for obs in result.observations)

    fetched = _refetch_run(db, run.id)
    assert fetched.status == DiscoveryRunStatus.COMPLETED
    assert fetched.started_at is not None
    assert fetched.completed_at is not None
    assert fetched.assets_detected == len(result.observations)
    # Sub-feature 4: execution persists observations into the catalog
    assert fetched.assets_created == len(result.observations)
    assert fetched.assets_updated == 0
    assert fetched.assets_unchanged == 0
    assert fetched.error_count == 0
    assert fetched.error_summary is None


def test_repeated_discovery_of_unchanged_source_is_deterministic(db, repo):
    source = _source(db, repo)
    service = _execution_service(db)

    first = service.execute(_run(db, source).id)
    second = service.execute(_run(db, source).id)

    assert _observation_keys(first) == _observation_keys(second)
    assert first.files_scanned == second.files_scanned

    fetched = _refetch_run(db, second.run.id)
    assert fetched.status == DiscoveryRunStatus.COMPLETED


def test_unreachable_location_fails_run_without_leaking_paths(db, tmp_path):
    missing = tmp_path / "missing-repo"
    source = _source(db, missing)
    run = _run(db, source)
    service = _execution_service(db)

    with pytest.raises(DiscoverySourceUnreachableError) as excinfo:
        service.execute(run.id)
    assert str(missing) not in str(excinfo.value)

    fetched = _refetch_run(db, run.id)
    assert fetched.status == DiscoveryRunStatus.FAILED
    assert fetched.error_count == 1
    assert "DISCOVERY_SOURCE_UNREACHABLE" in fetched.error_summary
    assert str(missing) not in (fetched.error_summary or "")


def test_missing_location_config_fails_run(db, repo):
    source = _source(db, repo)
    source.config = {}
    db.commit()
    run = _run(db, source)
    service = _execution_service(db)

    with pytest.raises(InvalidDiscoverySourceConfigError):
        service.execute(run.id)

    fetched = _refetch_run(db, run.id)
    assert fetched.status == DiscoveryRunStatus.FAILED
    assert "DISCOVERY_SOURCE_CONFIG_INVALID" in fetched.error_summary


def test_recoverable_file_errors_produce_partial_run(db, tmp_path):
    (tmp_path / "code.py").write_text("import anthropic\n", encoding="utf-8")
    (tmp_path / "legacy.txt").write_bytes(b"caf\xe9\n")  # not valid UTF-8
    source = _source(db, tmp_path)
    run = _run(db, source)
    service = _execution_service(db)

    result = service.execute(run.id)

    assert result.observations
    assert [issue.reason for issue in result.issues] == ["unsupported_encoding"]
    assert result.issues[0].file_path == "legacy.txt"

    fetched = _refetch_run(db, run.id)
    assert fetched.status == DiscoveryRunStatus.PARTIAL
    assert fetched.assets_detected == len(result.observations)
    assert fetched.error_count == 1
    assert "unsupported_encoding" in fetched.error_summary
    # persisted summary is path-free
    assert "legacy.txt" not in (fetched.error_summary or "")
    assert str(tmp_path) not in (fetched.error_summary or "")


def test_archived_source_is_rejected_before_start(db, repo):
    source = _source(db, repo)
    run = _run(db, source)
    DiscoverySourceService(db, settings.default_tenant_id).delete(source.id)

    with pytest.raises(DiscoverySourceArchivedError):
        _execution_service(db).execute(run.id)

    # rejection happens before RUNNING: the run is untouched and retryable
    fetched = _refetch_run(db, run.id)
    assert fetched.status == DiscoveryRunStatus.PENDING
    assert fetched.started_at is None


def test_disabled_source_is_rejected_before_start(db, repo):
    source = _source(db, repo)
    run = _run(db, source)
    DiscoverySourceService(db, settings.default_tenant_id).update(
        source.id, DiscoverySourceUpdate(status=DiscoverySourceStatus.DISABLED)
    )

    with pytest.raises(DiscoverySourceDisabledError):
        _execution_service(db).execute(run.id)

    fetched = _refetch_run(db, run.id)
    assert fetched.status == DiscoveryRunStatus.PENDING


def test_missing_run_raises_not_found(db, repo):
    from uuid import uuid4

    with pytest.raises(DiscoveryRunNotFoundError):
        _execution_service(db).execute(uuid4())


def test_execution_is_tenant_scoped(db, repo):
    from uuid import uuid4

    source = _source(db, repo)
    run = _run(db, source)

    with pytest.raises(DiscoveryRunNotFoundError):
        _execution_service(db, tenant_id=uuid4()).execute(run.id)


def test_executing_terminal_run_is_rejected(db, repo):
    source = _source(db, repo)
    service = _execution_service(db)
    run_id = _run(db, source).id
    service.execute(run_id)

    with pytest.raises(InvalidDiscoveryRunTransitionError):
        service.execute(run_id)

    fetched = _refetch_run(db, run_id)
    assert fetched.status == DiscoveryRunStatus.COMPLETED


def test_run_creation_still_does_not_execute(db, repo):
    source = _source(db, repo)
    created = DiscoveryRunService(db, settings.default_tenant_id).create(source.id)
    assert created.status == DiscoveryRunStatus.PENDING
    assert created.started_at is None
    assert created.assets_detected == 0
