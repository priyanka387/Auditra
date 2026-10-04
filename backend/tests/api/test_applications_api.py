from uuid import uuid4

API = "/api/v1"


def _payload(**overrides):
    payload = {
        "name": "AI Support Copilot",
        "slug": "ai-support-copilot",
        "display_name": "AI Support Copilot",
        "description": "AI support application for enterprise customers",
        "application_type": "copilot",
        "owner_name": "support-team",
        "team_name": "support-ai",
        "source": "manual",
        "tags": ["customer-support", "genai"],
        "metadata": {"business_domain": "support"},
    }
    payload.update(overrides)
    return payload


def _create(client, **overrides):
    response = client.post(f"{API}/applications", json=_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def _error(response):
    return response.json()["error"]


def test_create_application_success(client):
    body = _create(client)
    assert body["slug"] == "ai-support-copilot"
    assert body["status"] == "ACTIVE"
    assert body["source"] == "manual"
    assert body["metadata"] == {"business_domain": "support"}
    assert body["tags"] == ["customer-support", "genai"]
    assert body["tenant_id"]
    assert body["archived_at"] is None


def test_create_application_normalizes_slug(client):
    body = _create(client, name="X", slug="My Support App")
    assert body["slug"] == "my-support-app"


def test_get_application(client):
    body = _create(client)
    response = client.get(f"{API}/applications/{body['id']}")
    assert response.status_code == 200
    assert response.json()["id"] == body["id"]


def test_get_unknown_application_is_404(client):
    response = client.get(f"{API}/applications/{uuid4()}")
    assert response.status_code == 404
    assert _error(response)["code"] == "APPLICATION_NOT_FOUND"


def test_list_applications_pagination_and_filters(client):
    _create(client, name="App One", slug="app-one", application_type="copilot")
    _create(client, name="App Two", slug="app-two", application_type="service")

    response = client.get(f"{API}/applications?page=1&page_size=1")
    assert response.status_code == 200
    body = response.json()
    assert body["page"] == 1
    assert body["page_size"] == 1
    assert body["total"] == 2
    assert body["total_pages"] == 2
    assert len(body["items"]) == 1

    by_type = client.get(f"{API}/applications", params={"application_type": "service"})
    assert by_type.json()["total"] == 1
    assert by_type.json()["items"][0]["slug"] == "app-two"

    search = client.get(f"{API}/applications", params={"search": "app-one"})
    assert search.json()["total"] == 1

    status_filtered = client.get(f"{API}/applications", params={"status": "INACTIVE"})
    assert status_filtered.json()["total"] == 0

    owned = client.get(f"{API}/applications", params={"owner": "support-team"})
    assert owned.json()["total"] == 2
    unknown_owner = client.get(f"{API}/applications", params={"owner": "nobody"})
    assert unknown_owner.json()["total"] == 0


def test_list_applications_sorting(client):
    _create(client, name="Alpha", slug="alpha-app")
    _create(client, name="Beta", slug="beta-app")
    response = client.get(f"{API}/applications", params={"sort_by": "name", "sort_order": "asc"})
    names = [item["name"] for item in response.json()["items"]]
    assert names == ["Alpha", "Beta"]


def test_list_applications_invalid_sort_is_400(client):
    response = client.get(f"{API}/applications", params={"sort_by": "nope"})
    assert response.status_code == 400
    assert _error(response)["code"] == "INVALID_SORT_FIELD"


def test_update_application(client):
    body = _create(client)
    response = client.patch(
        f"{API}/applications/{body['id']}", json={"description": "Updated", "status": "INACTIVE"}
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["description"] == "Updated"
    assert updated["status"] == "INACTIVE"
    assert updated["name"] == body["name"]


def test_update_application_rejects_status_reactivation_from_archived(client):
    body = _create(client)
    assert client.delete(f"{API}/applications/{body['id']}").status_code == 204
    response = client.patch(f"{API}/applications/{body['id']}", json={"status": "ACTIVE"})
    assert response.status_code == 409
    assert _error(response)["code"] == "INVALID_STATUS_TRANSITION"


def test_archive_application_is_idempotent(client):
    body = _create(client)
    assert client.delete(f"{API}/applications/{body['id']}").status_code == 204
    assert client.delete(f"{API}/applications/{body['id']}").status_code == 204
    fetched = client.get(f"{API}/applications/{body['id']}").json()
    assert fetched["status"] == "ARCHIVED"
    assert fetched["archived_at"] is not None
    listed = client.get(f"{API}/applications")
    assert all(item["id"] != body["id"] for item in listed.json()["items"])
    listed_incl = client.get(f"{API}/applications", params={"include_archived": True})
    assert any(item["id"] == body["id"] for item in listed_incl.json()["items"])


def test_duplicate_slug_conflict(client):
    _create(client)
    response = client.post(f"{API}/applications", json=_payload())
    assert response.status_code == 409
    assert _error(response)["code"] == "APPLICATION_ALREADY_EXISTS"


def test_create_application_invalid_body_is_422(client):
    response = client.post(f"{API}/applications", json={"name": "", "slug": "x"})
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_create_application_secret_metadata_is_422(client):
    response = client.post(
        f"{API}/applications", json=_payload(metadata={"api_key": "sk-live-123"})
    )
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_create_application_unknown_source_is_422(client):
    response = client.post(f"{API}/applications", json=_payload(source="magic"))
    assert response.status_code == 422
