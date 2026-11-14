"""API tests: GET /discovery/assets/{asset_id}/evidence (SF9)."""

import re
from uuid import uuid4

import pytest

from tests.api.test_discovered_assets_api import ITEM_KEYS as ASSET_ITEM_KEYS

SOURCES_API = "/api/v1/discovery/sources"
RUNS_API = "/api/v1/discovery/runs"
ASSETS_API = "/api/v1/discovery/assets"
EXECUTE_API = f"{RUNS_API}/{{run_id}}/execute"

ITEM_KEYS = {
    "id",
    "tenant_id",
    "discovered_asset_id",
    "discovery_source_id",
    "discovery_run_id",
    "evidence_type",
    "detection_rule",
    "file_path",
    "line_number",
    "observed_at",
    "metadata",
    "created_at",
    "updated_at",
}

EVIDENCE_TYPES = {
    "provider_reference",
    "framework_reference",
    "model_reference",
    "agent_reference",
    "workflow_reference",
}

CREDENTIAL_PATTERN = re.compile(r"^(?:sk|pk|ghp|AKIA)[-_]")


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


def _model_asset_id(client) -> str:
    listed = client.get(ASSETS_API, params={"asset_type": "MODEL_REFERENCE"}).json()
    return listed["items"][0]["id"]


def _fixture(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text('import openai\nmodel = "gpt-4o"\n', encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("langchain>=0.1.0\n", encoding="utf-8")
    # content that must never leak through the evidence API
    (tmp_path / "app" / "settings.py").write_text(
        'INTERNAL_TOKEN = "hunter2-do-not-leak"\n', encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def repo(tmp_path):
    return _fixture(tmp_path)


def test_asset_evidence_endpoint_returns_all_rows(client, repo):
    source_id, run_id = _catalog(client, repo)
    asset_id = _model_asset_id(client)

    response = client.get(f"{ASSETS_API}/{asset_id}/evidence")
    assert response.status_code == 200, response.text
    body = response.json()

    assert set(body) == {"items", "page", "page_size", "total", "total_pages"}
    # one evidence row per observation: openai import, gpt-4o assignment,
    # langchain requirement (one row for this asset here, 3 across assets)
    assert body["total"] == 1
    assert len(body["items"]) == 1

    item = body["items"][0]
    assert set(item) == ITEM_KEYS
    assert item["discovered_asset_id"] == asset_id
    assert item["discovery_run_id"] == run_id
    assert item["discovery_source_id"] == source_id
    assert item["evidence_type"] in EVIDENCE_TYPES
    assert item["line_number"] >= 1

    # the other assets expose their own rows: 3 observations -> 3 rows total
    all_rows = 0
    for identity in ("AI_SERVICE|openai", "MODEL_REFERENCE|gpt-4o", "TOOL|langchain"):
        listed = client.get(ASSETS_API, params={"search": identity.split("|")[1]}).json()
        for candidate in listed["items"]:
            if candidate["normalized_identity"] == identity:
                evidence = client.get(f"{ASSETS_API}/{candidate['id']}/evidence").json()
                all_rows += evidence["total"]
    assert all_rows == 3


def test_evidence_filter_by_type_and_rule(client, repo):
    _, run_id = _catalog(client, repo)
    asset_id = _model_asset_id(client)

    by_type = client.get(
        f"{ASSETS_API}/{asset_id}/evidence", params={"evidence_type": "model_reference"}
    )
    assert by_type.status_code == 200, by_type.text
    typed = by_type.json()
    assert typed["total"] == 1
    assert typed["items"][0]["detection_rule"] == "model.assignment"

    openai_asset = client.get(ASSETS_API, params={"asset_type": "AI_SERVICE"}).json()["items"][0][
        "id"
    ]
    by_rule = client.get(
        f"{ASSETS_API}/{openai_asset}/evidence",
        params={"detection_rule": "provider.openai.import"},
    )
    assert by_rule.status_code == 200, by_rule.text
    assert by_rule.json()["total"] == 1

    by_run = client.get(f"{ASSETS_API}/{asset_id}/evidence", params={"discovery_run_id": run_id})
    assert by_run.status_code == 200, by_run.text
    assert by_run.json()["total"] == 1

    invalid = client.get(
        f"{ASSETS_API}/{asset_id}/evidence", params={"evidence_type": "not_a_type"}
    )
    # enum query params are rejected by request validation, like every
    # other discovery list endpoint (e.g. asset_type=SPREADSHEET)
    assert invalid.status_code == 422
    assert _error(invalid)["code"] == "VALIDATION_ERROR"


def test_evidence_sort_and_pagination(client, repo):
    _catalog(client, repo)
    asset_id = _model_asset_id(client)

    paged = client.get(f"{ASSETS_API}/{asset_id}/evidence", params={"page": 1, "page_size": 2})
    assert paged.status_code == 200, paged.text
    body = paged.json()
    assert body["total"] == 1
    assert body["total_pages"] == 1
    assert len(body["items"]) == 1

    # sorting is accepted across the whole catalog vocabulary
    openai_asset = client.get(ASSETS_API, params={"asset_type": "AI_SERVICE"}).json()["items"][0][
        "id"
    ]
    client.post(f"{SOURCES_API}/{_source(client, repo, name='second-service')}/runs")
    sorted_asc = client.get(
        f"{ASSETS_API}/{openai_asset}/evidence",
        params={"sort_by": "created_at", "sort_order": "asc"},
    )
    assert sorted_asc.status_code == 200, sorted_asc.text

    invalid = client.get(f"{ASSETS_API}/{asset_id}/evidence", params={"sort_by": "nope"})
    assert invalid.status_code == 400
    assert _error(invalid)["code"] == "DISCOVERY_EVIDENCE_QUERY_INVALID"


def test_evidence_for_unknown_asset_returns_404(client):
    response = client.get(f"{ASSETS_API}/{uuid4()}/evidence")
    assert response.status_code == 404, response.text
    error = _error(response)
    assert error["code"] == "DISCOVERED_ASSET_NOT_FOUND"
    assert error["http_status"] == 404


def test_evidence_response_leaks_no_secret_content(client, repo):
    _catalog(client, repo)
    asset_id = _model_asset_id(client)

    response = client.get(f"{ASSETS_API}/{asset_id}/evidence")
    assert response.status_code == 200, response.text
    assert "hunter2-do-not-leak" not in response.text
    assert "INTERNAL_TOKEN" not in response.text

    for item in response.json()["items"]:
        assert not item["file_path"].startswith("/")
        assert ":" not in item["file_path"].split("/")[0]  # no drive letter
        for value in item["metadata"].values():
            assert not CREDENTIAL_PATTERN.match(str(value))


def test_asset_detail_response_shape_unchanged(client, repo):
    _catalog(client, repo)
    asset_id = _model_asset_id(client)

    response = client.get(f"{ASSETS_API}/{asset_id}")
    assert response.status_code == 200, response.text
    assert set(response.json()) == ASSET_ITEM_KEYS
    assert "evidence" not in set(response.json())
