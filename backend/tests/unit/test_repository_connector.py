"""Unit tests for the repository/codebase discovery connector."""

from uuid import uuid4

import pytest

from app.core.config import settings
from app.modules.asset_discovery import connectors
from app.modules.asset_discovery.connectors import get_connector
from app.modules.asset_discovery.connectors import repository_connector as rc
from app.modules.asset_discovery.connectors.base import DiscoveryConnector
from app.modules.asset_discovery.connectors.repository_connector import (
    RepositoryDiscoveryConnector,
)
from app.modules.asset_discovery.errors import (
    DiscoveryConnectorUnsupportedError,
    DiscoverySourceUnreachableError,
    InvalidDiscoverySourceConfigError,
)
from app.modules.asset_discovery.models import DiscoverySource

RUN_ID = uuid4()


def _source(location=None, config=None) -> DiscoverySource:
    if config is None:
        config = {"location": str(location)} if location is not None else {}
    return DiscoverySource(
        id=uuid4(),
        tenant_id=settings.default_tenant_id,
        name="scan-target",
        source_type="repository",
        config=config,
        status="active",
    )


def _discover(source, run_id=RUN_ID):
    return RepositoryDiscoveryConnector().discover(source, run_id)


@pytest.fixture
def repo(tmp_path):
    """A small repository exercising scanning, exclusions and file issues."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text(
        'import openai\nclient = openai.OpenAI()\nmodel = "gpt-4o"\n', encoding="utf-8"
    )
    (tmp_path / "README.md").write_text(
        "We use openai for chat. Langchain is optional.\n", encoding="utf-8"
    )
    (tmp_path / "requirements.txt").write_text("langchain>=0.1.0\n", encoding="utf-8")

    # excluded directories: their content must never be observed
    for excluded in ("node_modules", ".git", "__pycache__", "dist"):
        (tmp_path / excluded).mkdir()
        (tmp_path / excluded / "dep.js").write_text(
            'model = "EXCLUDED_MARKER9"\n', encoding="utf-8"
        )

    # binary content with an AI-looking payload: skipped silently
    (tmp_path / "data.txt").write_bytes(b'\x00\x01model = "BINARY_MARKER9"\n')

    # oversized file: recoverable issue, scanning continues
    (tmp_path / "big.txt").write_bytes(b"a" * (rc.MAX_FILE_BYTES + 1))

    # undecodable text: recoverable issue, scanning continues
    (tmp_path / "latin1.txt").write_bytes(b"caf\xe9 openai\n")

    return tmp_path


# --- connector registry -----------------------------------------------------


def test_registry_returns_repository_connector():
    connector = get_connector("repository")
    assert isinstance(connector, RepositoryDiscoveryConnector)
    assert isinstance(connector, DiscoveryConnector)
    assert connector.name == "repository"


def test_registry_rejects_unknown_source_type():
    with pytest.raises(DiscoveryConnectorUnsupportedError) as excinfo:
        get_connector("kubernetes")
    assert excinfo.value.code == "DISCOVERY_CONNECTOR_UNSUPPORTED"
    assert excinfo.value.http_status == 422


def test_repository_connector_registered_in_registry():
    assert "repository" in connectors._CONNECTORS


# --- source validation (fatal errors) ---------------------------------------


def test_missing_location_config_is_fatal():
    with pytest.raises(InvalidDiscoverySourceConfigError):
        _discover(_source(config={}))


def test_blank_location_config_is_fatal():
    with pytest.raises(InvalidDiscoverySourceConfigError):
        _discover(_source(config={"location": "   "}))


def test_nonexistent_location_is_fatal(tmp_path):
    missing = tmp_path / "does-not-exist"
    with pytest.raises(DiscoverySourceUnreachableError) as excinfo:
        _discover(_source(missing))
    # error must not leak the configured path
    assert str(missing) not in str(excinfo.value)


def test_file_location_is_fatal(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("hello", encoding="utf-8")
    with pytest.raises(DiscoverySourceUnreachableError) as excinfo:
        _discover(_source(target))
    assert str(target) not in str(excinfo.value)


def test_relative_traversal_location_resolves(tmp_path):
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    (repo / "code.py").write_text("import anthropic\n", encoding="utf-8")

    def observation_keys(result):
        return [
            (obs.file_path, obs.line_number, obs.detection_rule, obs.name)
            for obs in result.observations
        ]

    direct = _discover(_source(repo))
    traversing = _discover(_source(repo / "sub" / ".."))
    assert observation_keys(direct) == observation_keys(traversing)
    assert direct.observations


# --- traversal and exclusions ------------------------------------------------


def test_scan_produces_observations_from_supported_files(repo):
    result = _discover(_source(repo))
    assert result.connector == "repository"
    assert result.observations

    by_rule = {obs.detection_rule for obs in result.observations}
    assert "provider.openai.import" in by_rule
    assert "provider.openai.usage" in by_rule
    assert "model.assignment" in by_rule
    assert "framework.langchain.manifest" in by_rule

    model = next(obs for obs in result.observations if obs.detection_rule == "model.assignment")
    assert model.name == "gpt-4o"
    assert model.file_path == "app/main.py"
    assert model.line_number == 3
    assert model.source_id is not None and model.run_id == RUN_ID


def test_file_paths_are_source_relative_posix(repo):
    result = _discover(_source(repo))
    for observation in result.observations:
        assert not observation.file_path.startswith("/")
        assert "\\" not in observation.file_path
        assert str(repo) not in observation.file_path


def test_excluded_directories_are_not_scanned(repo):
    result = _discover(_source(repo))
    assert result.observations
    assert all("EXCLUDED_MARKER9" != obs.name for obs in result.observations)
    assert all(
        not any(
            part in obs.file_path.split("/")
            for part in ("node_modules", ".git", "__pycache__", "dist")
        )
        for obs in result.observations
    )


def test_binary_files_are_skipped_silently(repo):
    result = _discover(_source(repo))
    assert all("BINARY_MARKER9" != obs.name for obs in result.observations)
    assert all(issue.file_path != "data.txt" for issue in result.issues)
    assert all(issue.reason != "binary" for issue in result.issues)


def test_oversized_file_yields_recoverable_issue(repo):
    result = _discover(_source(repo))
    issue = next(i for i in result.issues if i.file_path == "big.txt")
    assert issue.reason == "file_too_large"
    # scan continued past the oversized file
    assert result.observations


def test_undecodable_file_yields_recoverable_issue(repo):
    result = _discover(_source(repo))
    issue = next(i for i in result.issues if i.file_path == "latin1.txt")
    assert issue.reason == "unsupported_encoding"
    assert result.observations


def test_unreadable_file_yields_recoverable_issue(repo, monkeypatch):
    original = rc._scan_file

    def flaky(file_path, path):
        if path.name == "main.py":
            raise OSError("permission denied")
        return original(file_path, path)

    monkeypatch.setattr(rc, "_scan_file", flaky)
    result = _discover(_source(repo))

    issue = next(i for i in result.issues if i.file_path == "app/main.py")
    assert issue.reason == "unreadable"
    # other files still scanned
    assert any(obs.detection_rule == "framework.langchain.manifest" for obs in result.observations)


def test_empty_directory_scans_cleanly(tmp_path):
    result = _discover(_source(tmp_path))
    assert result.files_scanned == 0
    assert result.observations == ()
    assert result.issues == ()


# --- determinism --------------------------------------------------------------


def test_scan_is_deterministic(repo):
    source = _source(repo)
    first = _discover(source)
    second = _discover(source)
    assert first.observations == second.observations
    assert first.issues == second.issues
    assert first.files_scanned == second.files_scanned


def test_observations_are_sorted_deterministically(repo):
    result = _discover(_source(repo))
    keys = [
        (obs.file_path, obs.line_number or 0, obs.detection_rule, obs.name)
        for obs in result.observations
    ]
    assert keys == sorted(keys)


def test_same_run_and_source_ids_are_stamped(repo):
    source = _source(repo)
    run_id = uuid4()
    result = _discover(source, run_id)
    assert all(obs.source_id == source.id for obs in result.observations)
    assert all(obs.run_id == run_id for obs in result.observations)


# --- caps ---------------------------------------------------------------------


def test_observation_cap_stops_scan(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("import openai\n", encoding="utf-8")
    (tmp_path / "z.py").write_text("import anthropic\n", encoding="utf-8")

    monkeypatch.setattr(rc, "MAX_OBSERVATIONS", 1)
    result = _discover(_source(tmp_path))
    assert len(result.observations) == 1
    assert any(issue.reason == "observation_limit" for issue in result.issues)


def test_issue_cap_stops_scan(tmp_path, monkeypatch):
    for index in range(5):
        (tmp_path / f"latin{index}.txt").write_bytes(b"caf\xe9\n")

    monkeypatch.setattr(rc, "MAX_ISSUES", 3)
    result = _discover(_source(tmp_path))
    assert len(result.issues) == 3
    assert result.issues[-1].reason == "error_limit"
    assert result.files_scanned == 0


# --- extensible exclusions ----------------------------------------------------


def test_extra_exclusions_are_applied(tmp_path):
    (tmp_path / "keep").mkdir()
    (tmp_path / "keep" / "code.py").write_text("import openai\n", encoding="utf-8")
    (tmp_path / "generated").mkdir()
    (tmp_path / "generated" / "code.py").write_text(
        'model = "generated-marker-9"\n', encoding="utf-8"
    )

    connector = RepositoryDiscoveryConnector(extra_excluded_dirs=("generated",))
    result = connector.discover(_source(tmp_path), RUN_ID)

    assert all("generated-marker-9" != obs.name for obs in result.observations)
    assert any(obs.file_path == "keep/code.py" for obs in result.observations)
