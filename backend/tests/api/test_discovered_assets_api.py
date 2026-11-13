"""API tests for the Discovered Asset Catalog (GET /discovery/assets)."""

from uuid import uuid4

import pytest

SOURCES_API = "/api/v1/discovery/sources"
RUNS_API = "/api/v1/discovery/runs"
ASSETS_API = "/api/v1/discovery/assets"
EXECUTE_API = f"{RUNS_API}/{{run_id}}/execute"

ITEM_KEYS = {
    "id",
    "tenant_id",
    "source_id",
    "last_seen_run_id",
    "asset_type",
    "name",
    "normalized_identity",
    "status",
    "metadata",
    "first_seen_at",
    "last_seen_at",
    "created_at",
    "updated_at",
}


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


def _catalog(client, repo, name="payments-service") -> tuple[str, str]:
    """Create a source and execute one run so the catalog is populated."""
    source_id = _source(client, repo, name=name)
    run = client.post(f"{SOURCES_API}/{source_id}/runs").json()
    executed = client.post(EXECUTE_API.format(run_id=run["id"]))
    assert executed.status_code == 200, executed.text
    return source_id, run["id"]


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text('import openai\nmodel = "gpt-4o"\n', encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("langchain>=0.1.0\n", encoding="utf-8")
    # content that must never leak through the catalog API
    (tmp_path / "app" / "settings.py").write_text(
        'INTERNAL_TOKEN = "hunter2-do-not-leak"\n', encoding="utf-8"
    )
    return tmp_path


def test_list_returns_persisted_catalog_entries(client, repo):
    source_id, run_id = _catalog(client, repo)

    response = client.get(ASSETS_API)
    assert response.status_code == 200, response.text
    body = response.json()

    assert set(body) == {"items", "page", "page_size", "total", "total_pages"}
    assert body["page"] == 1
    assert body["page_size"] == 25
    assert body["total"] == 3
    assert body["total_pages"] == 1

    by_identity = {item["normalized_identity"]: item for item in body["items"]}
    assert set(by_identity) == {
        "AI_SERVICE|openai",
        "MODEL_REFERENCE|gpt-4o",
        "TOOL|langchain",
    }

    model = by_identity["MODEL_REFERENCE|gpt-4o"]
    assert set(model) == ITEM_KEYS
    assert model["asset_type"] == "MODEL_REFERENCE"
    assert model["name"] == "gpt-4o"
    assert model["source_id"] == source_id
    assert model["last_seen_run_id"] == run_id
    assert model["status"] == "ACTIVE"
    assert model["first_seen_at"] == model["last_seen_at"]
    assert model["metadata"]["file_path"] == "app/main.py"
    assert model["metadata"]["detection_rule"] == "model.assignment"
    assert model["metadata"]["line_number"] == 2


def test_list_filters_by_asset_type_source_status_and_search(client, repo):
    source_id, _ = _catalog(client, repo)

    by_type = client.get(ASSETS_API, params={"asset_type": "MODEL_REFERENCE"}).json()
    assert by_type["total"] == 1
    assert by_type["items"][0]["name"] == "gpt-4o"

    by_source = client.get(ASSETS_API, params={"source_id": source_id}).json()
    assert by_source["total"] == 3

    by_foreign_source = client.get(ASSETS_API, params={"source_id": str(uuid4())}).json()
    assert by_foreign_source["total"] == 0

    by_status = client.get(ASSETS_API, params={"status": "ACTIVE"}).json()
    assert by_status["total"] == 3

    searched = client.get(ASSETS_API, params={"search": "gpt"}).json()
    assert searched["total"] == 1
    assert searched["items"][0]["name"] == "gpt-4o"


def test_list_paginates(client, repo):
    _catalog(client, repo)

    first = client.get(ASSETS_API, params={"page": 1, "page_size": 2}).json()
    second = client.get(ASSETS_API, params={"page": 2, "page_size": 2}).json()
    assert first["total"] == 3
    assert first["total_pages"] == 2
    assert len(first["items"]) == 2
    assert len(second["items"]) == 1
    assert {item["id"] for item in first["items"]}.isdisjoint(
        item["id"] for item in second["items"]
    )


def test_get_by_id_returns_asset(client, repo):
    _, run_id = _catalog(client, repo)
    listed = client.get(ASSETS_API, params={"asset_type": "MODEL_REFERENCE"}).json()
    asset_id = listed["items"][0]["id"]

    response = client.get(f"{ASSETS_API}/{asset_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == ITEM_KEYS
    assert body["id"] == asset_id
    assert body["last_seen_run_id"] == run_id


def test_get_unknown_asset_returns_404_envelope(client):
    response = client.get(f"{ASSETS_API}/{uuid4()}", headers={"X-Request-ID": "req-asset-404"})
    assert response.status_code == 404, response.text
    error = _error(response)
    assert error["code"] == "DISCOVERED_ASSET_NOT_FOUND"
    assert error["http_status"] == 404
    assert error["request_id"] == "req-asset-404"


def test_get_non_uuid_returns_422(client):
    response = client.get(f"{ASSETS_API}/not-a-uuid")
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_list_invalid_asset_type_returns_422(client):
    response = client.get(ASSETS_API, params={"asset_type": "SPREADSHEET"})
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_list_invalid_sort_returns_400(client):
    response = client.get(ASSETS_API, params={"sort_by": "nope"})
    assert response.status_code == 400
    assert _error(response)["code"] == "DISCOVERED_ASSET_QUERY_INVALID"


def test_list_invalid_page_returns_422(client):
    response = client.get(ASSETS_API, params={"page": 0})
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_catalog_does_not_leak_secrets_or_file_contents(client, repo):
    _catalog(client, repo)

    response = client.get(ASSETS_API, params={"page_size": 100})
    assert response.status_code == 200, response.text
    assert "hunter2-do-not-leak" not in response.text
    assert str(repo) not in response.text
    assert "INTERNAL_TOKEN" not in response.text
    assert response.text.count("import openai") == 0  # no raw file dumps

    item = response.json()["items"][0]
    assert set(item["metadata"]) <= {
        "observation_type",
        "file_path",
        "detection_rule",
        "line_number",
        "provider",
        "key",
        "framework",
        "classification_rule",
        "model_id",
    }


def test_catalog_survives_rerun_without_duplicates(client, repo):
    source_id, _ = _catalog(client, repo)
    before = client.get(ASSETS_API).json()

    second_run = client.post(f"{SOURCES_API}/{source_id}/runs").json()
    assert client.post(EXECUTE_API.format(run_id=second_run["id"])).status_code == 200

    after = client.get(ASSETS_API).json()
    assert after["total"] == before["total"] == 3
    # every asset was re-observed by the second run
    assert all(item["last_seen_run_id"] == second_run["id"] for item in after["items"])
    # first-seen timestamps are preserved across runs
    first_seen = {item["id"]: item["first_seen_at"] for item in before["items"]}
    assert {item["id"]: item["first_seen_at"] for item in after["items"]} == first_seen


def test_openapi_lists_discovered_asset_paths(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert ASSETS_API in paths, f"missing {ASSETS_API}"
    assert f"{ASSETS_API}/{{asset_id}}" in paths, "missing asset detail path"
