"""API tests for discovery execution (POST /discovery/runs/{id}/execute)."""

import os
from uuid import uuid4

import pytest

SOURCES_API = "/api/v1/discovery/sources"
RUNS_API = "/api/v1/discovery/runs"
EXECUTE_API = f"{RUNS_API}/{{run_id}}/execute"


def _error(response):
    return response.json()["error"]


def _source(client, location, name="payments-service") -> str:
    response = client.post(
        SOURCES_API,
        json={
            "name": name,
            "source_type": "repository",
            "config": {"location": str(location)},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _create_run(client, source_id: str) -> dict:
    response = client.post(f"{SOURCES_API}/{source_id}/runs")
    assert response.status_code == 201, response.text
    return response.json()


def _execute(client, run_id: str, **kwargs):
    return client.post(EXECUTE_API.format(run_id=run_id), **kwargs)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text('import openai\nmodel = "gpt-4o"\n', encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("langchain>=0.1.0\n", encoding="utf-8")
    # non-AI content that must never leak through the API
    (tmp_path / "app" / "settings.py").write_text(
        'INTERNAL_TOKEN = "hunter2-do-not-leak"\n', encoding="utf-8"
    )
    return tmp_path


def test_execute_success_returns_structured_result(client, repo):
    source_id = _source(client, repo)
    run = _create_run(client, source_id)

    response = _execute(client, run["id"])
    assert response.status_code == 200, response.text
    body = response.json()

    assert set(body) == {
        "source",
        "run",
        "status",
        "connector",
        "files_scanned",
        "observation_count",
        "observations",
        "errors",
    }
    assert body["status"] == "COMPLETED"
    assert body["connector"] == "repository"
    assert body["source"] == {
        "id": source_id,
        "name": "payments-service",
        "source_type": "repository",
    }
    assert body["files_scanned"] == 3
    assert body["observation_count"] == len(body["observations"]) >= 1
    assert body["errors"] == []

    rules = {obs["detection_rule"] for obs in body["observations"]}
    assert "provider.openai.import" in rules
    assert "model.assignment" in rules
    assert "framework.langchain.manifest" in rules

    observation = next(o for o in body["observations"] if o["detection_rule"] == "model.assignment")
    assert observation["name"] == "gpt-4o"
    assert observation["observation_type"] == "model_reference"
    assert observation["provider"] is None
    assert observation["file_path"] == "app/main.py"
    assert observation["line_number"] == 2
    assert observation["metadata"] == {"key": "model"}

    assert body["run"]["status"] == "COMPLETED"
    assert body["run"]["assets_detected"] == body["observation_count"]
    assert body["run"]["started_at"] is not None
    assert body["run"]["completed_at"] is not None


def test_execute_does_not_leak_file_contents_or_paths(client, repo):
    source_id = _source(client, repo)
    run = _create_run(client, source_id)

    response = _execute(client, run["id"])
    assert response.status_code == 200, response.text
    assert "hunter2-do-not-leak" not in response.text
    assert str(repo) not in response.text
    assert response.text.count("import openai") == 0  # no raw file dumps


def test_run_reflects_execution_state(client, repo):
    source_id = _source(client, repo)
    run = _create_run(client, source_id)

    executed = _execute(client, run["id"])
    fetched = client.get(f"{RUNS_API}/{run['id']}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["status"] == "COMPLETED"
    assert fetched.json()["assets_detected"] == executed.json()["observation_count"]


def test_execute_unreachable_source_fails_run(client, tmp_path):
    source_id = _source(client, tmp_path / "missing-repo")
    run = _create_run(client, source_id)

    response = _execute(client, run["id"], headers={"X-Request-ID": "req-exec-fail"})
    assert response.status_code == 422, response.text
    error = _error(response)
    assert error["code"] == "DISCOVERY_SOURCE_UNREACHABLE"
    assert error["http_status"] == 422
    assert error["request_id"] == "req-exec-fail"
    assert str(tmp_path) not in response.text

    fetched = client.get(f"{RUNS_API}/{run['id']}")
    assert fetched.json()["status"] == "FAILED"
    assert fetched.json()["error_count"] == 1


def test_execute_partial_returns_errors_and_partial_status(client, tmp_path):
    (tmp_path / "code.py").write_text("import anthropic\n", encoding="utf-8")
    (tmp_path / "legacy.txt").write_bytes(b"caf\xe9\n")
    source_id = _source(client, tmp_path, name="partial-source")
    run = _create_run(client, source_id)

    response = _execute(client, run["id"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "PARTIAL"
    assert body["observation_count"] >= 1
    assert len(body["errors"]) == 1

    issue = body["errors"][0]
    assert set(issue) == {"file_path", "reason", "message"}
    assert issue["file_path"] == "legacy.txt"
    assert issue["reason"] == "unsupported_encoding"
    assert not os.path.isabs(issue["file_path"])
    assert str(tmp_path) not in response.text

    fetched = client.get(f"{RUNS_API}/{run['id']}")
    assert fetched.json()["status"] == "PARTIAL"
    assert fetched.json()["error_count"] == 1


def test_execute_missing_run_returns_404_envelope(client):
    response = _execute(client, str(uuid4()), headers={"X-Request-ID": "req-exec-404"})
    assert response.status_code == 404, response.text
    error = _error(response)
    assert error["code"] == "DISCOVERY_RUN_NOT_FOUND"
    assert error["http_status"] == 404
    assert error["request_id"] == "req-exec-404"


def test_execute_non_uuid_run_returns_422(client):
    response = _execute(client, "not-a-uuid")
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_execute_twice_returns_409(client, repo):
    source_id = _source(client, repo)
    run = _create_run(client, source_id)
    assert _execute(client, run["id"]).status_code == 200

    second = _execute(client, run["id"])
    assert second.status_code == 409
    assert _error(second)["code"] == "DISCOVERY_RUN_TRANSITION_INVALID"


def test_execute_with_body_field_returns_422(client, repo):
    source_id = _source(client, repo)
    run = _create_run(client, source_id)
    response = _execute(client, run["id"], json={"observations": "all"})
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_execute_disabled_source_returns_409_and_run_stays_pending(client, repo):
    source_id = _source(client, repo)
    patched = client.patch(f"{SOURCES_API}/{source_id}", json={"status": "disabled"})
    assert patched.status_code == 200, patched.text
    run = _create_run(client, source_id)

    response = _execute(client, run["id"])
    assert response.status_code == 409
    assert _error(response)["code"] == "DISCOVERY_SOURCE_DISABLED"

    fetched = client.get(f"{RUNS_API}/{run['id']}")
    assert fetched.json()["status"] == "PENDING"


def test_execute_archived_source_returns_409(client, repo):
    source_id = _source(client, repo)
    run = _create_run(client, source_id)
    deleted = client.delete(f"{SOURCES_API}/{source_id}")
    assert deleted.status_code == 204, deleted.text

    response = _execute(client, run["id"])
    assert response.status_code == 409
    assert _error(response)["code"] == "DISCOVERY_SOURCE_ARCHIVED"


def test_create_run_contract_remains_pending(client, repo):
    # Sub-feature 2 contract: creating a run only records the attempt
    source_id = _source(client, repo, name="unchanged-contract")
    created = _create_run(client, source_id)
    assert created["status"] == "PENDING"
    assert created["assets_detected"] == 0


def test_openapi_lists_execute_path(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert EXECUTE_API in paths, f"missing {EXECUTE_API}"
