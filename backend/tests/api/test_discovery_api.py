from uuid import uuid4

API = "/api/v1/model-discovery"


def _payload(**overrides):
    payload = {
        "source_type": "manual",
        "provider": "openai",
        "model_identifier": "gpt-5.x",
        "model_type": "llm",
        "display_name": "GPT-5",
        "metadata": {"environment": "development"},
    }
    payload.update(overrides)
    return payload


def _error(response):
    return response.json()["error"]


def test_create_returns_201_with_canonical_identity(client):
    response = client.post(API, json=_payload())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "UNRESOLVED"
    assert body["canonical_identity"] == "openai|gpt-5.x"
    assert body["observation_count"] == 1
    assert body["first_seen_at"] == body["last_seen_at"]


def test_create_replay_returns_200_with_incremented_count(client):
    created = client.post(API, json=_payload())
    assert created.status_code == 201, created.text

    replay = client.post(API, json=_payload())
    assert replay.status_code == 200, replay.text
    body = replay.json()
    assert body["id"] == created.json()["id"]
    assert body["observation_count"] == 2
    assert body["first_seen_at"] == created.json()["first_seen_at"]


def test_create_blank_provider_returns_422(client):
    response = client.post(API, json=_payload(provider="  "))
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_create_secret_metadata_returns_422(client):
    response = client.post(API, json=_payload(metadata={"api_key": "sk-secret"}))
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_get_missing_returns_404_envelope(client):
    response = client.get(f"{API}/{uuid4()}", headers={"X-Request-ID": "req-disc-404"})
    assert response.status_code == 404
    error = _error(response)
    assert error["code"] == "DISCOVERY_NOT_FOUND"
    assert error["http_status"] == 404
    assert error["request_id"] == "req-disc-404"


def test_list_filters_by_status_and_provider_and_paginates(client):
    client.post(API, json=_payload(model_identifier="gpt-list-1"))
    client.post(API, json=_payload(provider="anthropic", model_identifier="claude-list-1"))

    filtered = client.get(API, params={"status": "UNRESOLVED", "provider": "anthropic"})
    assert filtered.status_code == 200, filtered.text
    body = filtered.json()
    assert body["total"] == 1
    assert body["items"][0]["canonical_identity"] == "anthropic|claude-list-1"

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
    assert _error(response)["code"] == "DISCOVERY_QUERY_INVALID"


def test_match_endpoint_reruns_reconciliation(client):
    created = client.post(API, json=_payload(model_identifier="late-1"))
    assert created.status_code == 201, created.text
    discovery_id = created.json()["id"]
    assert created.json()["status"] == "UNRESOLVED"

    model = client.post(
        "/api/v1/models",
        json={
            "provider_slug": "openai",
            "model_type_slug": "llm",
            "name": "Late Model",
            "native_model_id": "late-1",
        },
    )
    assert model.status_code == 201, model.text

    matched = client.post(f"{API}/{discovery_id}/match")
    assert matched.status_code == 200, matched.text
    body = matched.json()
    assert body["status"] == "MATCHED"
    assert body["matched_model_id"] == model.json()["id"]


def test_ignore_endpoint_sets_ignored(client):
    created = client.post(API, json=_payload())
    response = client.post(f"{API}/{created.json()['id']}/ignore")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "IGNORED"


def test_register_endpoint_creates_model_and_returns_registered(client):
    created = client.post(API, json=_payload(model_identifier="via-register"))
    response = client.post(f"{API}/{created.json()['id']}/register", json={})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "REGISTERED"
    assert body["matched_model_id"] is not None

    model = client.get(f"/api/v1/models/{body['matched_model_id']}")
    assert model.status_code == 200, model.text
    assert model.json()["canonical_key"] == "openai|via-register"
    assert model.json()["source_reference"] == f"model-discovery:{created.json()['id']}"


def test_invalid_transition_returns_409(client):
    created = client.post(API, json=_payload(model_identifier="twice-api"))
    first = client.post(f"{API}/{created.json()['id']}/register", json={})
    assert first.status_code == 201, first.text

    second = client.post(f"{API}/{created.json()['id']}/register", json={})
    assert second.status_code == 409
    assert _error(second)["code"] == "DISCOVERY_TRANSITION_INVALID"


def test_openapi_lists_discovery_paths(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path in (
        API,
        f"{API}/{{discovery_id}}",
        f"{API}/{{discovery_id}}/match",
        f"{API}/{{discovery_id}}/ignore",
        f"{API}/{{discovery_id}}/register",
    ):
        assert path in paths, f"missing {path}"
