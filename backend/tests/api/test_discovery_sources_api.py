from uuid import uuid4

API = "/api/v1/discovery/sources"


def _payload(**overrides) -> dict:
    payload = {
        "name": "payments-service",
        "source_type": "repository",
        "config": {"location": "/workspace/payments"},
    }
    payload.update(overrides)
    return payload


def _error(response):
    return response.json()["error"]


def test_create_returns_201_with_defaults(client):
    response = client.post(API, json=_payload())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "payments-service"
    assert body["source_type"] == "repository"
    assert body["config"] == {"location": "/workspace/payments"}
    assert body["status"] == "active"
    assert body["archived_at"] is None
    assert body["created_at"] is not None
    assert body["updated_at"] is not None


def test_create_duplicate_name_returns_409_envelope(client):
    created = client.post(API, json=_payload())
    assert created.status_code == 201, created.text

    duplicate = client.post(API, json=_payload(config={"location": "/workspace/other"}))
    assert duplicate.status_code == 409, duplicate.text
    error = _error(duplicate)
    assert error["code"] == "DISCOVERY_SOURCE_DUPLICATE"
    assert error["http_status"] == 409


def test_create_invalid_source_type_returns_422(client):
    response = client.post(API, json=_payload(source_type="kubernetes"))
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_create_missing_name_returns_422(client):
    payload = _payload()
    del payload["name"]
    response = client.post(API, json=payload)
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_create_blank_name_returns_422(client):
    response = client.post(API, json=_payload(name="   "))
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_create_secret_config_returns_422(client):
    response = client.post(
        API, json=_payload(config={"location": "/workspace/payments", "token": "abc123"})
    )
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_create_repository_without_location_returns_422(client):
    response = client.post(API, json=_payload(config={}))
    assert response.status_code == 422
    assert _error(response)["code"] == "DISCOVERY_SOURCE_CONFIG_INVALID"


def test_create_extra_field_returns_422(client):
    response = client.post(API, json=_payload(schedule="*/5 * * * *"))
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_get_returns_source(client):
    created = client.post(API, json=_payload())
    assert created.status_code == 201, created.text

    fetched = client.get(f"{API}/{created.json()['id']}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json() == created.json()


def test_get_missing_returns_404_envelope(client):
    response = client.get(f"{API}/{uuid4()}", headers={"X-Request-ID": "req-src-404"})
    assert response.status_code == 404
    error = _error(response)
    assert error["code"] == "DISCOVERY_SOURCE_NOT_FOUND"
    assert error["http_status"] == 404
    assert error["request_id"] == "req-src-404"


def test_list_filters_and_paginates(client):
    client.post(API, json=_payload(name="repo-one"))
    client.post(API, json=_payload(name="repo-two", config={"location": "/workspace/two"}))

    filtered = client.get(API, params={"search": "repo-two"})
    assert filtered.status_code == 200, filtered.text
    body = filtered.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "repo-two"

    first = client.get(API, params={"page": 1, "page_size": 1}).json()
    assert first["total"] == 2
    assert first["total_pages"] == 2
    assert len(first["items"]) == 1

    second = client.get(API, params={"page": 2, "page_size": 1}).json()
    assert len(second["items"]) == 1
    assert second["items"][0]["id"] != first["items"][0]["id"]


def test_list_invalid_sort_returns_400(client):
    response = client.get(API, params={"sort_by": "nope"})
    assert response.status_code == 400
    assert _error(response)["code"] == "DISCOVERY_SOURCE_QUERY_INVALID"


def test_list_excludes_archived_by_default(client):
    kept = client.post(API, json=_payload(name="kept-source"))
    removed = client.post(API, json=_payload(name="removed-source"))
    assert client.delete(f"{API}/{removed.json()['id']}").status_code == 204

    default = client.get(API).json()
    assert default["total"] == 1
    assert default["items"][0]["id"] == kept.json()["id"]

    with_archived = client.get(API, params={"include_archived": True}).json()
    assert with_archived["total"] == 2
    assert {item["name"] for item in with_archived["items"]} == {
        "kept-source",
        "removed-source",
    }


def test_patch_updates_fields(client):
    created = client.post(API, json=_payload())
    source_id = created.json()["id"]

    response = client.patch(
        f"{API}/{source_id}",
        json={
            "name": "payments-service-2",
            "config": {"location": "/workspace/payments-v2"},
            "status": "disabled",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "payments-service-2"
    assert body["config"] == {"location": "/workspace/payments-v2"}
    assert body["status"] == "disabled"

    fetched = client.get(f"{API}/{source_id}").json()
    assert fetched["name"] == "payments-service-2"
    assert fetched["status"] == "disabled"


def test_patch_missing_returns_404(client):
    response = client.patch(f"{API}/{uuid4()}", json={"name": "ghost"})
    assert response.status_code == 404
    assert _error(response)["code"] == "DISCOVERY_SOURCE_NOT_FOUND"


def test_patch_archived_returns_409(client):
    created = client.post(API, json=_payload())
    source_id = created.json()["id"]
    assert client.delete(f"{API}/{source_id}").status_code == 204

    response = client.patch(f"{API}/{source_id}", json={"name": "too-late"})
    assert response.status_code == 409
    assert _error(response)["code"] == "DISCOVERY_SOURCE_ARCHIVED"


def test_patch_invalid_status_returns_422(client):
    created = client.post(API, json=_payload())
    response = client.patch(f"{API}/{created.json()['id']}", json={"status": "archived"})
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_patch_to_duplicate_name_returns_409(client):
    client.post(API, json=_payload(name="source-a"))
    other = client.post(API, json=_payload(name="source-b"))

    response = client.patch(f"{API}/{other.json()['id']}", json={"name": "source-a"})
    assert response.status_code == 409
    assert _error(response)["code"] == "DISCOVERY_SOURCE_DUPLICATE"


def test_delete_returns_204_and_is_idempotent(client):
    created = client.post(API, json=_payload())
    source_id = created.json()["id"]

    assert client.delete(f"{API}/{source_id}").status_code == 204
    assert client.delete(f"{API}/{source_id}").status_code == 204

    fetched = client.get(f"{API}/{source_id}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["archived_at"] is not None


def test_delete_missing_returns_404(client):
    response = client.delete(f"{API}/{uuid4()}")
    assert response.status_code == 404
    assert _error(response)["code"] == "DISCOVERY_SOURCE_NOT_FOUND"


def test_archived_name_can_be_reused(client):
    created = client.post(API, json=_payload())
    assert client.delete(f"{API}/{created.json()['id']}").status_code == 204

    recreated = client.post(API, json=_payload())
    assert recreated.status_code == 201, recreated.text
    assert recreated.json()["name"] == "payments-service"


def test_source_lifecycle_records_audit_events(client):
    created = client.post(API, json=_payload(name="audited-source"))
    source_id = created.json()["id"]
    client.patch(f"{API}/{source_id}", json={"status": "disabled"})
    client.delete(f"{API}/{source_id}")

    response = client.get(
        "/api/v1/audit/events",
        params={"resource_type": "discovery_source", "resource_id": source_id},
    )
    assert response.status_code == 200, response.text
    events = response.json()["items"]
    # audit listing returns newest first (sequence_no DESC)
    assert [event["event_type"] for event in events] == [
        "discovery_source.archived",
        "discovery_source.updated",
        "discovery_source.created",
    ]


def test_openapi_lists_discovery_source_paths(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path in (
        API,
        f"{API}/{{source_id}}",
    ):
        assert path in paths, f"missing {path}"
