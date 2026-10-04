from uuid import uuid4

API = "/api/v1"


def _application(client, name="AI Support Copilot", slug="ai-support-copilot"):
    response = client.post(f"{API}/applications", json={"name": name, "slug": slug})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _agent(client, application_id, name="Support Agent", slug="support-agent", **overrides):
    payload = {"name": name, "slug": slug, "framework": "langgraph", "agent_type": "support"}
    payload.update(overrides)
    response = client.post(f"{API}/applications/{application_id}/agents", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _model(client, **overrides):
    payload = {
        "provider_slug": "openai",
        "model_type_slug": "llm",
        "name": "Support LLM",
        "native_model_id": "support-llm-1",
    }
    payload.update(overrides)
    response = client.post(f"{API}/models", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _error(response):
    return response.json()["error"]


def test_acceptance_scenario(client):
    app_id = _application(client)
    agent_id = _agent(client, app_id)
    model_id = _model(client)

    created = client.post(
        f"{API}/agents/{agent_id}/models",
        json={"model_id": model_id, "role": "PRIMARY", "selection_priority": 1},
    )
    assert created.status_code == 201, created.text
    association = created.json()
    assert association["agent_id"] == agent_id
    assert association["model_id"] == model_id
    assert association["role"] == "PRIMARY"
    assert association["status"] == "ACTIVE"
    assert association["source"] == "manual"

    listed = client.get(f"{API}/agents/{agent_id}/models")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    reverse = client.get(f"{API}/models/{model_id}/agents")
    assert reverse.status_code == 200
    items = reverse.json()["items"]
    assert len(items) == 1
    assert items[0]["agent_id"] == agent_id
    assert items[0]["agent_slug"] == "support-agent"
    assert items[0]["application_id"] == app_id
    assert items[0]["application_slug"] == "ai-support-copilot"

    duplicate = client.post(
        f"{API}/agents/{agent_id}/models",
        json={"model_id": model_id, "role": "PRIMARY", "selection_priority": 1},
    )
    assert duplicate.status_code == 409
    assert _error(duplicate)["code"] == "ASSOCIATION_DUPLICATE"


def test_multiple_roles_same_priority(client):
    app_id = _application(client)
    agent_id = _agent(client, app_id)
    primary = _model(client, name="Primary LLM", native_model_id="primary-1")
    embedding = _model(
        client, name="Embedding", native_model_id="embed-1", model_type_slug="embedding"
    )

    assert (
        client.post(
            f"{API}/agents/{agent_id}/models",
            json={"model_id": primary, "role": "PRIMARY"},
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"{API}/agents/{agent_id}/models",
            json={"model_id": embedding, "role": "EMBEDDING"},
        ).status_code
        == 201
    )
    listed = client.get(f"{API}/agents/{agent_id}/models")
    assert listed.json()["total"] == 2
    assert {item["role"] for item in listed.json()["items"]} == {"PRIMARY", "EMBEDDING"}


def test_association_with_version(client, parent_version):
    app_id = _application(client)
    agent_id = _agent(client, app_id)
    created = client.post(
        f"{API}/agents/{agent_id}/models",
        json={
            "model_id": str(parent_version.model_id),
            "model_version_id": str(parent_version.id),
            "role": "PRIMARY",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["model_version_id"] == str(parent_version.id)

    other_model = _model(client, name="Mismatched", native_model_id="mismatch-1")
    mismatch = client.post(
        f"{API}/agents/{agent_id}/models",
        json={
            "model_id": other_model,
            "model_version_id": str(parent_version.id),
            "role": "FALLBACK",
        },
    )
    assert mismatch.status_code == 409
    assert _error(mismatch)["code"] == "MODEL_VERSION_BELONGS_TO_DIFFERENT_MODEL"


def test_get_association(client):
    app_id = _application(client)
    agent_id = _agent(client, app_id)
    model_id = _model(client)
    created = client.post(
        f"{API}/agents/{agent_id}/models", json={"model_id": model_id, "role": "PRIMARY"}
    ).json()
    response = client.get(f"{API}/agents/{agent_id}/models/{created['id']}")
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_update_association(client):
    app_id = _application(client)
    agent_id = _agent(client, app_id)
    model_id = _model(client)
    created = client.post(
        f"{API}/agents/{agent_id}/models", json={"model_id": model_id, "role": "PRIMARY"}
    ).json()
    response = client.patch(
        f"{API}/agents/{agent_id}/models/{created['id']}",
        json={
            "role": "FALLBACK",
            "selection_priority": 2,
            "configuration": {"temperature": 0.2},
        },
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["role"] == "FALLBACK"
    assert updated["selection_priority"] == 2
    assert updated["configuration"] == {"temperature": 0.2}


def test_delete_disables_and_history_remains(client):
    app_id = _application(client)
    agent_id = _agent(client, app_id)
    model_id = _model(client)
    created = client.post(
        f"{API}/agents/{agent_id}/models", json={"model_id": model_id, "role": "PRIMARY"}
    ).json()

    assert client.delete(f"{API}/agents/{agent_id}/models/{created['id']}").status_code == 204
    assert client.delete(f"{API}/agents/{agent_id}/models/{created['id']}").status_code == 204

    fetched = client.get(f"{API}/agents/{agent_id}/models/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "DISABLED"
    assert fetched.json()["disabled_at"] is not None

    active_only = client.get(f"{API}/agents/{agent_id}/models", params={"active_only": True})
    assert active_only.json()["total"] == 0
    with_disabled = client.get(f"{API}/agents/{agent_id}/models")
    assert with_disabled.json()["total"] == 1


def test_duplicate_after_disable_succeeds(client):
    app_id = _application(client)
    agent_id = _agent(client, app_id)
    model_id = _model(client)
    created = client.post(
        f"{API}/agents/{agent_id}/models", json={"model_id": model_id, "role": "PRIMARY"}
    ).json()
    client.delete(f"{API}/agents/{agent_id}/models/{created['id']}")
    again = client.post(
        f"{API}/agents/{agent_id}/models", json={"model_id": model_id, "role": "PRIMARY"}
    )
    assert again.status_code == 201


def test_unknown_model_is_404(client):
    app_id = _application(client)
    agent_id = _agent(client, app_id)
    response = client.post(
        f"{API}/agents/{agent_id}/models", json={"model_id": str(uuid4()), "role": "PRIMARY"}
    )
    assert response.status_code == 404
    assert _error(response)["code"] == "MODEL_NOT_FOUND"


def test_unknown_agent_is_404(client):
    model_id = _model(client)
    response = client.post(
        f"{API}/agents/{uuid4()}/models", json={"model_id": model_id, "role": "PRIMARY"}
    )
    assert response.status_code == 404
    assert _error(response)["code"] == "AGENT_NOT_FOUND"


def test_association_belongs_to_other_agent_is_404(client):
    app_id = _application(client)
    agent_a = _agent(client, app_id, name="Agent A", slug="agent-a")
    agent_b = _agent(client, app_id, name="Agent B", slug="agent-b")
    model_id = _model(client)
    created = client.post(
        f"{API}/agents/{agent_a}/models", json={"model_id": model_id, "role": "PRIMARY"}
    ).json()
    response = client.get(f"{API}/agents/{agent_b}/models/{created['id']}")
    assert response.status_code == 404
    assert _error(response)["code"] == "ASSOCIATION_NOT_FOUND"


def test_role_filter_sort_and_pagination(client):
    app_id = _application(client)
    agent_id = _agent(client, app_id)
    primary = _model(client, name="P", native_model_id="p-1")
    fallback = _model(client, name="F", native_model_id="f-1")
    client.post(f"{API}/agents/{agent_id}/models", json={"model_id": primary, "role": "PRIMARY"})
    client.post(
        f"{API}/agents/{agent_id}/models",
        json={"model_id": fallback, "role": "FALLBACK", "selection_priority": 2},
    )

    page = client.get(
        f"{API}/agents/{agent_id}/models",
        params={"page": 1, "page_size": 1, "sort_by": "selection_priority", "sort_order": "asc"},
    )
    body = page.json()
    assert body["total"] == 2
    assert body["total_pages"] == 2
    assert body["items"][0]["role"] == "PRIMARY"

    filtered = client.get(f"{API}/agents/{agent_id}/models", params={"role": "FALLBACK"})
    assert filtered.json()["total"] == 1

    invalid_sort = client.get(f"{API}/agents/{agent_id}/models", params={"sort_by": "nope"})
    assert invalid_sort.status_code == 400


def test_retired_model_association_rejected_via_api(client):
    app_id = _application(client)
    agent_id = _agent(client, app_id)
    model_id = _model(client)
    assert (
        client.patch(f"{API}/models/{model_id}", json={"lifecycle_state": "ACTIVE"}).status_code
        == 200
    )
    retired = client.patch(f"{API}/models/{model_id}", json={"lifecycle_state": "RETIRED"})
    assert retired.status_code == 200, retired.text

    response = client.post(
        f"{API}/agents/{agent_id}/models", json={"model_id": model_id, "role": "PRIMARY"}
    )
    assert response.status_code == 409
    assert _error(response)["code"] == "MODEL_NOT_ASSOCIABLE"


def test_reverse_lookup_unknown_model_is_404(client):
    response = client.get(f"{API}/models/{uuid4()}/agents")
    assert response.status_code == 404
    assert _error(response)["code"] == "MODEL_NOT_FOUND"


def test_reverse_lookup_active_only_filter(client):
    app_id = _application(client)
    agent_id = _agent(client, app_id)
    model_id = _model(client)
    created = client.post(
        f"{API}/agents/{agent_id}/models", json={"model_id": model_id, "role": "PRIMARY"}
    ).json()
    client.delete(f"{API}/agents/{agent_id}/models/{created['id']}")

    active = client.get(f"{API}/models/{model_id}/agents", params={"active_only": True})
    assert active.json()["total"] == 0
    all_links = client.get(f"{API}/models/{model_id}/agents")
    assert all_links.json()["total"] == 1
