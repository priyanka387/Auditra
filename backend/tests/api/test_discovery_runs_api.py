from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.core.config import settings
from app.modules.asset_discovery.service import DiscoveryRunService

SOURCES_API = "/api/v1/discovery/sources"
RUNS_API = "/api/v1/discovery/runs"


def _source(client, name="payments-service") -> str:
    response = client.post(
        SOURCES_API,
        json={
            "name": name,
            "source_type": "repository",
            "config": {"location": f"/workspace/{name}"},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _create_run(client, source_id: str) -> dict:
    response = client.post(f"{SOURCES_API}/{source_id}/runs")
    assert response.status_code == 201, response.text
    return response.json()


def _error(response):
    return response.json()["error"]


def test_create_run_returns_201_pending_defaults(client):
    source_id = _source(client)
    response = client.post(f"{SOURCES_API}/{source_id}/runs")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "PENDING"
    assert body["source_id"] == source_id
    assert body["started_at"] is None
    assert body["completed_at"] is None
    assert body["assets_detected"] == 0
    assert body["assets_created"] == 0
    assert body["assets_updated"] == 0
    assert body["assets_unchanged"] == 0
    assert body["error_count"] == 0
    assert body["error_summary"] is None
    assert body["created_at"] is not None
    assert body["updated_at"] is not None


def test_create_run_with_empty_body_returns_201(client):
    source_id = _source(client)
    response = client.post(f"{SOURCES_API}/{source_id}/runs", json={})
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "PENDING"


def test_create_run_with_body_field_returns_422(client):
    source_id = _source(client)
    response = client.post(f"{SOURCES_API}/{source_id}/runs", json={"status": "RUNNING"})
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_create_run_missing_source_returns_404_envelope(client):
    response = client.post(f"{SOURCES_API}/{uuid4()}/runs", headers={"X-Request-ID": "req-run-404"})
    assert response.status_code == 404, response.text
    error = _error(response)
    assert error["code"] == "DISCOVERY_SOURCE_NOT_FOUND"
    assert error["http_status"] == 404
    assert error["request_id"] == "req-run-404"


def test_create_run_for_non_uuid_source_returns_422(client):
    response = client.post(f"{SOURCES_API}/not-a-uuid/runs")
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_repeated_create_produces_distinct_runs(client):
    source_id = _source(client)
    first = _create_run(client, source_id)
    second = _create_run(client, source_id)
    assert first["id"] != second["id"]


def test_get_run_returns_run(client):
    source_id = _source(client)
    created = _create_run(client, source_id)

    fetched = client.get(f"{RUNS_API}/{created['id']}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json() == created


def test_get_missing_run_returns_404_envelope(client):
    response = client.get(f"{RUNS_API}/{uuid4()}", headers={"X-Request-ID": "req-run-missing"})
    assert response.status_code == 404
    error = _error(response)
    assert error["code"] == "DISCOVERY_RUN_NOT_FOUND"
    assert error["http_status"] == 404
    assert error["request_id"] == "req-run-missing"


def test_list_runs_filters_by_source(client):
    source_a = _source(client, "source-a")
    source_b = _source(client, "source-b")
    run_a1 = _create_run(client, source_a)
    run_a2 = _create_run(client, source_a)
    _create_run(client, source_b)

    body = client.get(RUNS_API).json()
    assert body["total"] == 3

    by_source = client.get(RUNS_API, params={"source_id": source_a}).json()
    assert by_source["total"] == 2
    assert {item["id"] for item in by_source["items"]} == {run_a1["id"], run_a2["id"]}


def test_list_runs_filters_by_status_and_started_window(client, db):
    source_id = _source(client)
    started_run = _create_run(client, source_id)
    pending_run = _create_run(client, source_id)
    DiscoveryRunService(db, tenant_id=settings.default_tenant_id).start(UUID(started_run["id"]))

    pending = client.get(RUNS_API, params={"status": "PENDING"}).json()
    assert pending["total"] == 1
    assert pending["items"][0]["id"] == pending_run["id"]

    running = client.get(RUNS_API, params={"status": "RUNNING"}).json()
    assert running["total"] == 1
    assert running["items"][0]["id"] == started_run["id"]

    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()

    started_after_past = client.get(RUNS_API, params={"started_from": past}).json()
    assert started_after_past["total"] == 1
    assert started_after_past["items"][0]["id"] == started_run["id"]

    assert client.get(RUNS_API, params={"started_from": future}).json()["total"] == 0
    assert client.get(RUNS_API, params={"started_to": past}).json()["total"] == 0


def test_list_runs_paginates(client):
    source_id = _source(client)
    for _ in range(3):
        _create_run(client, source_id)

    first = client.get(RUNS_API, params={"page": 1, "page_size": 2}).json()
    assert first["total"] == 3
    assert first["total_pages"] == 2
    assert len(first["items"]) == 2

    second = client.get(RUNS_API, params={"page": 2, "page_size": 2}).json()
    assert second["total"] == 3
    assert len(second["items"]) == 1
    assert {item["id"] for item in first["items"]}.isdisjoint(
        item["id"] for item in second["items"]
    )


def test_list_invalid_status_returns_422(client):
    response = client.get(RUNS_API, params={"status": "BOGUS"})
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_list_invalid_sort_returns_400(client):
    response = client.get(RUNS_API, params={"sort_by": "nope"})
    assert response.status_code == 400
    assert _error(response)["code"] == "DISCOVERY_RUN_QUERY_INVALID"


def test_list_malformed_date_returns_422(client):
    response = client.get(RUNS_API, params={"started_from": "not-a-date"})
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_list_naive_date_returns_400(client):
    response = client.get(RUNS_API, params={"started_from": "2026-11-01T00:00:00"})
    assert response.status_code == 400
    assert _error(response)["code"] == "DISCOVERY_RUN_QUERY_INVALID"


def test_openapi_lists_discovery_run_paths(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path in (
        f"{SOURCES_API}/{{source_id}}/runs",
        RUNS_API,
        f"{RUNS_API}/{{run_id}}",
    ):
        assert path in paths, f"missing {path}"
