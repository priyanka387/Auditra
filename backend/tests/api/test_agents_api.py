from uuid import uuid4

API = "/api/v1"


def _application(client, **overrides):
    payload = {"name": "AI Support Copilot", "slug": "ai-support-copilot"}
    payload.update(overrides)
    response = client.post(f"{API}/applications", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _payload(**overrides):
    payload = {
        "name": "Support Research Agent",
        "slug": "support-research-agent",
        "display_name": "Support Research Agent",
        "description": "Researches customer issues and prepares response context",
        "agent_type": "research",
        "framework": "langgraph",
        "framework_version": "0.2.0",
        "runtime_identifier": "support-research-v1",
        "owner_name": "team-support",
        "team_name": "support-ai",
        "source": "manual",
        "capabilities": {"tool_use": True, "retrieval": True, "planning": True},
        "tags": ["langgraph", "support"],
        "metadata": {},
    }
    payload.update(overrides)
    return payload


def _create(client, application_id, **overrides):
    response = client.post(
        f"{API}/applications/{application_id}/agents", json=_payload(**overrides)
    )
    assert response.status_code == 201, response.text
    return response.json()


def _error(response):
    return response.json()["error"]


def test_create_agent_success(client):
    app_id = _application(client)
    body = _create(client, app_id)
    assert body["application_id"] == app_id
    assert body["slug"] == "support-research-agent"
    assert body["framework"] == "langgraph"
    assert body["framework_version"] == "0.2.0"
    assert body["capabilities"]["retrieval"] is True
    assert body["status"] == "ACTIVE"
    assert body["source"] == "manual"


def test_create_agent_normalizes_framework(client):
    app_id = _application(client)
    body = _create(client, app_id, framework="LangChain")
    assert body["framework"] == "langchain"


def test_get_agent(client):
    app_id = _application(client)
    body = _create(client, app_id)
    response = client.get(f"{API}/agents/{body['id']}")
    assert response.status_code == 200
    assert response.json()["id"] == body["id"]


def test_get_unknown_agent_is_404(client):
    response = client.get(f"{API}/agents/{uuid4()}")
    assert response.status_code == 404
    assert _error(response)["code"] == "AGENT_NOT_FOUND"


def test_list_agents_for_application(client):
    app_id = _application(client)
    other_app_id = _application(client, name="Other App", slug="other-app")
    _create(client, app_id)
    _create(client, app_id, name="Second Agent", slug="second-agent")
    _create(client, other_app_id, name="Foreign Agent", slug="foreign-agent")

    response = client.get(f"{API}/applications/{app_id}/agents")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert all(item["application_id"] == app_id for item in body["items"])


def test_list_all_agents_search_and_filters(client):
    app_id = _application(client)
    other_app_id = _application(client, name="Other App", slug="other-app")
    _create(client, app_id)
    _create(
        client,
        other_app_id,
        name="Extractor",
        slug="extractor",
        framework="custom",
        agent_type="extraction",
    )

    all_agents = client.get(f"{API}/agents")
    assert all_agents.json()["total"] == 2

    by_framework = client.get(f"{API}/agents", params={"framework": "custom"})
    assert by_framework.json()["total"] == 1
    assert by_framework.json()["items"][0]["slug"] == "extractor"

    by_type = client.get(f"{API}/agents", params={"agent_type": "research"})
    assert by_type.json()["total"] == 1

    searched = client.get(f"{API}/agents", params={"search": "extractor"})
    assert searched.json()["total"] == 1

    by_app = client.get(f"{API}/agents", params={"application_id": str(app_id)})
    assert by_app.json()["total"] == 1


def test_agent_list_pagination(client):
    app_id = _application(client)
    _create(client, app_id, name="A", slug="agent-a")
    _create(client, app_id, name="B", slug="agent-b")
    response = client.get(
        f"{API}/applications/{app_id}/agents",
        params={"page": 1, "page_size": 1, "sort_by": "name", "sort_order": "asc"},
    )
    body = response.json()
    assert body["total"] == 2
    assert body["total_pages"] == 2
    assert [item["name"] for item in body["items"]] == ["A"]


def test_update_agent(client):
    app_id = _application(client)
    body = _create(client, app_id)
    response = client.patch(
        f"{API}/agents/{body['id']}",
        json={"description": "Updated purpose", "status": "INACTIVE"},
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["description"] == "Updated purpose"
    assert updated["status"] == "INACTIVE"
    assert updated["framework"] == "langgraph"


def test_archive_agent_is_idempotent_and_archived_application_blocks_new_agents(client):
    app_id = _application(client)
    body = _create(client, app_id)
    assert client.delete(f"{API}/agents/{body['id']}").status_code == 204
    assert client.delete(f"{API}/agents/{body['id']}").status_code == 204
    fetched = client.get(f"{API}/agents/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "ARCHIVED"

    assert client.delete(f"{API}/applications/{app_id}").status_code == 204
    blocked = client.post(f"{API}/applications/{app_id}/agents", json=_payload())
    assert blocked.status_code == 409
    assert _error(blocked)["code"] == "APPLICATION_ARCHIVED"


def test_duplicate_agent_slug_in_same_application_is_409(client):
    app_id = _application(client)
    _create(client, app_id)
    response = client.post(f"{API}/applications/{app_id}/agents", json=_payload(name="Renamed"))
    assert response.status_code == 409
    assert _error(response)["code"] == "AGENT_ALREADY_EXISTS"


def test_same_agent_slug_allowed_in_different_application(client):
    app_id = _application(client)
    other_app_id = _application(client, name="Other App", slug="other-app")
    _create(client, app_id)
    body = _create(client, other_app_id)
    assert body["slug"] == "support-research-agent"


def test_create_agent_under_unknown_application_is_404(client):
    response = client.post(f"{API}/applications/{uuid4()}/agents", json=_payload())
    assert response.status_code == 404
    assert _error(response)["code"] == "APPLICATION_NOT_FOUND"


def test_create_agent_invalid_body_is_422(client):
    app_id = _application(client)
    response = client.post(f"{API}/applications/{app_id}/agents", json={"name": "", "slug": "x"})
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_create_agent_secret_metadata_is_422(client):
    app_id = _application(client)
    response = client.post(
        f"{API}/applications/{app_id}/agents",
        json=_payload(metadata={"client_secret": "shh"}),
    )
    assert response.status_code == 422
