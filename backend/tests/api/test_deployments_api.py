import time
from uuid import uuid4

API = "/api/v1"


def _model(client, **overrides):
    payload = {
        "provider_slug": "openai",
        "model_type_slug": "llm",
        "name": "Fraud Detection",
        "native_model_id": "fraud-det-1",
    }
    payload.update(overrides)
    response = client.post(f"{API}/models", json=payload)
    assert response.status_code == 201
    return response.json()["id"]


def _version(client, model_id, **overrides):
    payload = {
        "identity_type": "release",
        "version_label": "v3",
        "native_version_id": "fraud-v3",
    }
    payload.update(overrides)
    response = client.post(f"{API}/models/{model_id}/versions", json=payload)
    assert response.status_code == 201
    return response.json()


def _deployment_payload(**overrides):
    payload = {
        "name": "fraud-detection-prod",
        "environment": "production",
        "deployment_kind": "online_inference",
        "target_type": "kubernetes",
        "target_name": "prod-cluster",
        "region": "us-east-1",
        "cluster_name": "fraud-prod",
        "namespace": "ai-serving",
        "runtime": "vllm",
        "serving_framework": "vllm",
        "desired_replicas": 3,
        "configuration": {"tensor_parallel_size": 2, "max_model_len": 32768},
        "metadata": {"deployment_ticket": "DEP-1209"},
        "source": "manual",
    }
    payload.update(overrides)
    return payload


def _create_deployment(client, model_version_id, **overrides):
    response = client.post(
        f"{API}/model-versions/{model_version_id}/deployments",
        json=_deployment_payload(**overrides),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _endpoint_payload(**overrides):
    payload = {
        "name": "primary",
        "endpoint_type": "inference",
        "protocol": "https",
        "url": "https://fraud.example.com/v1/infer",
        "auth_type": "external_secret",
        "auth_reference": "secret://prod/fraud",
        "is_primary": True,
    }
    payload.update(overrides)
    return payload


def _error(response):
    return response.json()["error"]


def test_acceptance_scenario(client):
    model_id = _model(client)
    version = _version(client, model_id)

    created = client.post(
        f"{API}/model-versions/{version['id']}/deployments",
        json=_deployment_payload(),
    )
    assert created.status_code == 201
    deployment = created.json()
    assert deployment["status"] == "planned"
    assert deployment["status_source"] == "manual"
    assert deployment["environment"] == "production"
    assert deployment["runtime"] == "vllm"
    assert deployment["model_version_id"] == version["id"]
    assert deployment["configuration"]["tensor_parallel_size"] == 2

    endpoint = client.post(
        f"{API}/deployments/{deployment['id']}/endpoints", json=_endpoint_payload()
    )
    assert endpoint.status_code == 201, endpoint.text
    assert endpoint.json()["is_primary"] is True

    activated = client.post(
        f"{API}/deployments/{deployment['id']}/transition",
        json={"status": "active", "reason": "Deployment verified"},
    )
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"
    assert activated.json()["status_source"] == "api"

    inventory = client.get(f"{API}/deployments").json()
    assert inventory["total"] == 1
    item = inventory["items"][0]
    assert item["name"] == "fraud-detection-prod"
    assert item["status"] == "active"
    assert item["environment"] == "production"

    invalid = client.post(
        f"{API}/deployments/{deployment['id']}/transition", json={"status": "stopped"}
    )
    assert invalid.status_code == 409
    assert _error(invalid)["code"] == "DEPLOYMENT_INVALID_TRANSITION"


def test_deployment_not_deployable_version_returns_409(client):
    model_id = _model(client, native_model_id="retired-model", name="Retired")
    version = _version(client, model_id, native_version_id="retired-v1")
    lifecycle = client.post(
        f"{API}/models/{model_id}/versions/{version['id']}/lifecycle",
        json={"target_state": "DEPRECATED"},
    )
    assert lifecycle.status_code == 200

    response = client.post(
        f"{API}/model-versions/{version['id']}/deployments", json=_deployment_payload()
    )
    assert response.status_code == 409
    assert _error(response)["code"] == "MODEL_VERSION_NOT_DEPLOYABLE"


def test_unknown_model_version_returns_404_envelope(client, db):
    response = client.post(
        f"{API}/model-versions/{uuid4()}/deployments",
        json=_deployment_payload(),
        headers={"X-Request-ID": "req-dep-missing"},
    )
    assert response.status_code == 404
    error = _error(response)
    assert error["code"] == "MODEL_VERSION_NOT_FOUND"
    assert error["request_id"] == "req-dep-missing"
    assert "Traceback" not in response.text


def test_list_filters_search_sort_pagination(client, db):
    from datetime import UTC, datetime

    import sqlalchemy as sa

    from app.modules.model_inventory.models import ModelDeployment

    model_id = _model(client)
    version = _version(client, model_id)
    other_model = _model(client, native_model_id="other-det", name="Other")
    other_version = _version(client, other_model, native_version_id="other-v1")

    prod = _create_deployment(client, version["id"], name="aaa-prod")
    staging = _create_deployment(
        client,
        version["id"],
        name="bbb-staging",
        environment="staging",
        target_type="vm",
        target_name="stage-vm",
        runtime="triton",
        namespace="ai-staging",
        source_reference="ticket-77",
    )
    other = _create_deployment(client, other_version["id"], name="ccc-elsewhere")
    archived = _create_deployment(
        client, version["id"], name="ddd-archived", target_name="archived-vm"
    )
    client.post(
        f"{API}/deployments/{archived['id']}/transition", json={"status": "deploying"}
    )
    assert client.delete(f"{API}/deployments/{archived['id']}").status_code == 204

    by_env = client.get(f"{API}/deployments", params={"environment": "production"}).json()
    assert by_env["total"] == 2

    by_status = client.get(f"{API}/deployments", params={"status": "planned"}).json()
    assert by_status["total"] == 3

    combined = client.get(
        f"{API}/deployments", params={"environment": "staging", "status": "planned"}
    ).json()
    assert combined["total"] == 1 and combined["items"][0]["id"] == staging["id"]

    by_target = client.get(f"{API}/deployments", params={"target_type": "vm"}).json()
    assert by_target["total"] == 1 and by_target["items"][0]["id"] == staging["id"]

    by_runtime = client.get(f"{API}/deployments", params={"runtime": "triton"}).json()
    assert by_runtime["total"] == 1

    by_version = client.get(
        f"{API}/deployments", params={"model_version_id": version["id"]}
    ).json()
    assert by_version["total"] == 2

    by_model = client.get(f"{API}/deployments", params={"model_id": other_model}).json()
    assert by_model["total"] == 1 and by_model["items"][0]["id"] == other["id"]

    search_cluster = client.get(f"{API}/deployments", params={"search": "prod-cluster"}).json()
    assert search_cluster["total"] >= 1
    assert prod["id"] in [item["id"] for item in search_cluster["items"]]

    search_namespace = client.get(
        f"{API}/deployments", params={"search": "ai-staging"}
    ).json()
    assert search_namespace["total"] == 1 and search_namespace["items"][0]["id"] == staging["id"]

    search_source_ref = client.get(
        f"{API}/deployments", params={"search": "ticket-77"}
    ).json()
    assert search_source_ref["total"] == 1

    archived_hidden = client.get(f"{API}/deployments").json()
    assert archived["id"] not in [item["id"] for item in archived_hidden["items"]]
    archived_shown = client.get(
        f"{API}/deployments", params={"include_archived": "true"}
    ).json()
    assert archived["id"] in [item["id"] for item in archived_shown["items"]]

    ascending = client.get(
        f"{API}/deployments", params={"sort_by": "name", "sort_order": "asc"}
    ).json()
    assert [item["name"] for item in ascending["items"]] == [
        "aaa-prod",
        "bbb-staging",
        "ccc-elsewhere",
    ]

    page_two = client.get(
        f"{API}/deployments",
        params={"sort_by": "name", "sort_order": "asc", "page_size": 2, "page": 2},
    ).json()
    assert page_two["page"] == 2
    assert page_two["total"] == 3
    assert page_two["total_pages"] == 2
    assert [item["name"] for item in page_two["items"]] == ["ccc-elsewhere"]

    oversized = client.get(f"{API}/deployments", params={"page_size": 500})
    assert oversized.status_code == 422
    assert _error(oversized)["code"] == "VALIDATION_ERROR"

    bad_sort = client.get(f"{API}/deployments", params={"sort_by": "name; DROP TABLE"})
    assert bad_sort.status_code == 400
    assert _error(bad_sort)["code"] == "INVALID_SORT_FIELD"

    assert other["id"] != prod["id"]

    db.execute(
        sa.update(ModelDeployment)
        .where(ModelDeployment.id == staging["id"])
        .values(last_seen_at=datetime(2026, 6, 1, tzinfo=UTC))
    )
    db.commit()
    seen = client.get(
        f"{API}/deployments", params={"last_seen_after": "2026-03-01T00:00:00Z"}
    ).json()
    assert seen["total"] == 1 and seen["items"][0]["id"] == staging["id"]


def test_get_patch_delete_workflow(client):
    model_id = _model(client)
    version = _version(client, model_id)
    deployment = _create_deployment(client, version["id"])
    path = f"{API}/deployments/{deployment['id']}"

    fetched = client.get(path)
    assert fetched.status_code == 200
    assert fetched.json()["id"] == deployment["id"]

    patched = client.patch(
        path,
        json={
            "name": "fraud-detection-prod-renamed",
            "desired_replicas": 5,
            "metadata": {"cost_center": "AI-01"},
        },
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["name"] == "fraud-detection-prod-renamed"
    assert body["desired_replicas"] == 5
    assert body["metadata"] == {"cost_center": "AI-01"}
    assert body["version"] == 2
    assert body["model_version_id"] == deployment["model_version_id"]

    for immutable in (
        {"status": "active"},
        {"model_version_id": str(uuid4())},
        {"environment": "staging"},
        {"deployment_kind": "batch"},
        {"source": "api"},
    ):
        rejected = client.patch(path, json=immutable)
        assert rejected.status_code == 422, rejected.text
        assert _error(rejected)["code"] == "VALIDATION_ERROR"

    assert client.get(path).json()["status"] == "planned"

    deleted = client.delete(path)
    assert deleted.status_code == 204
    assert deleted.text == ""

    assert client.get(f"{API}/deployments").json()["total"] == 0
    archived = client.get(f"{API}/deployments", params={"include_archived": "true"}).json()
    assert archived["total"] == 1 and archived["items"][0]["id"] == deployment["id"]
    assert client.get(path).status_code == 200

    patched_archived = client.patch(path, json={"name": "nope"})
    assert patched_archived.status_code == 409
    assert _error(patched_archived)["code"] == "DEPLOYMENT_ALREADY_ARCHIVED"

    transition_archived = client.post(
        f"{path}/transition", json={"status": "deploying"}
    )
    assert transition_archived.status_code == 409
    assert _error(transition_archived)["code"] == "DEPLOYMENT_ALREADY_ARCHIVED"

    assert client.delete(path).status_code == 204


def test_missing_deployments_return_404_envelope(client):
    unknown = str(uuid4())
    cases = (
        ("GET", f"{API}/deployments/{unknown}", None),
        ("PATCH", f"{API}/deployments/{unknown}", {"name": "nope"}),
        ("DELETE", f"{API}/deployments/{unknown}", None),
        ("POST", f"{API}/deployments/{unknown}/transition", {"status": "deploying"}),
        ("POST", f"{API}/deployments/{unknown}/endpoints", _endpoint_payload()),
        ("GET", f"{API}/deployments/{unknown}/endpoints", None),
        ("GET", f"{API}/deployment-endpoints/{unknown}", None),
        ("PATCH", f"{API}/deployment-endpoints/{unknown}", {"name": "nope"}),
        ("DELETE", f"{API}/deployment-endpoints/{unknown}", None),
    )
    for method, url, body in cases:
        kwargs = {"json": body} if body is not None else {}
        response = client.request(method, url, **kwargs)
        assert response.status_code == 404, (method, url, response.text)
        error = _error(response)
        assert error["code"] in ("DEPLOYMENT_NOT_FOUND", "ENDPOINT_NOT_FOUND")
        assert error["http_status"] == 404
        assert "traceback" not in response.text.lower()


def test_invalid_transition_returns_409(client):
    model_id = _model(client)
    version = _version(client, model_id)
    deployment = _create_deployment(client, version["id"])
    path = f"{API}/deployments/{deployment['id']}/transition"

    response = client.post(path, json={"status": "stopped"})
    assert response.status_code == 409
    assert _error(response)["code"] == "DEPLOYMENT_INVALID_TRANSITION"

    unknown_state = client.post(path, json={"status": "archived"})
    assert unknown_state.status_code == 422

    client.post(path, json={"status": "deploying"})
    assert client.post(path, json={"status": "active"}).status_code == 200
    assert client.post(path, json={"status": "stopped"}).status_code == 409


def test_duplicate_primary_returns_409(client):
    model_id = _model(client)
    version = _version(client, model_id)
    deployment = _create_deployment(client, version["id"])
    first = client.post(
        f"{API}/deployments/{deployment['id']}/endpoints", json=_endpoint_payload()
    )
    assert first.status_code == 201

    second = client.post(
        f"{API}/deployments/{deployment['id']}/endpoints",
        json=_endpoint_payload(name="primary-2"),
    )
    assert second.status_code == 409
    assert _error(second)["code"] == "ENDPOINT_DUPLICATE_PRIMARY"

    health_primary = client.post(
        f"{API}/deployments/{deployment['id']}/endpoints",
        json=_endpoint_payload(
            name="health",
            endpoint_type="health",
            is_primary=True,
            url="https://fraud.example.com/healthz",
        ),
    )
    assert health_primary.status_code == 409
    assert _error(health_primary)["code"] == "ENDPOINT_INVALID_PRIMARY"


def test_endpoint_crud_workflow(client):
    model_id = _model(client)
    version = _version(client, model_id)
    deployment = _create_deployment(client, version["id"])
    base = f"{API}/deployments/{deployment['id']}/endpoints"

    created = client.post(base, json=_endpoint_payload())
    assert created.status_code == 201
    endpoint = created.json()
    assert endpoint["health_status"] == "unknown"
    assert endpoint["status"] == "active"

    health = client.post(
        base,
        json=_endpoint_payload(
            name="health",
            endpoint_type="health",
            is_primary=False,
            url="https://fraud.example.com/healthz",
        ),
    )
    assert health.status_code == 201

    listed = client.get(base).json()
    assert listed["total"] == 2

    fetched = client.get(f"{API}/deployment-endpoints/{endpoint['id']}")
    assert fetched.status_code == 200

    patched = client.patch(
        f"{API}/deployment-endpoints/{endpoint['id']}",
        json={"name": "primary-renamed", "health_status": "healthy"},
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "primary-renamed"
    assert patched.json()["health_status"] == "healthy"
    assert patched.json()["version"] == 2

    immovable = client.patch(
        f"{API}/deployment-endpoints/{endpoint['id']}",
        json={"deployment_id": str(uuid4()), "endpoint_type": "health"},
    )
    assert immovable.status_code == 422

    deleted = client.delete(f"{API}/deployment-endpoints/{endpoint['id']}")
    assert deleted.status_code == 204
    assert client.get(base).json()["total"] == 1
    assert client.get(f"{API}/deployment-endpoints/{endpoint['id']}").status_code == 200

    patched_archived = client.patch(
        f"{API}/deployment-endpoints/{endpoint['id']}", json={"name": "nope"}
    )
    assert patched_archived.status_code == 409
    assert _error(patched_archived)["code"] == "ENDPOINT_ALREADY_ARCHIVED"
    assert client.delete(f"{API}/deployment-endpoints/{endpoint['id']}").status_code == 204


def test_hostile_endpoint_urls_rejected(client):
    model_id = _model(client)
    version = _version(client, model_id)
    deployment = _create_deployment(client, version["id"])
    base = f"{API}/deployments/{deployment['id']}/endpoints"

    hostile = (
        "https://user:pass@fraud.example.com/v1",
        "https://user@fraud.example.com/v1",
        "https://fraud.example.com/v1#fragment",
        "http://fraud.example.com/v1",
        "not-a-url",
    )
    for url in hostile:
        started = time.monotonic()
        response = client.post(base, json=_endpoint_payload(url=url, name=url[:20]))
        elapsed = time.monotonic() - started
        assert response.status_code == 409, (url, response.text)
        assert _error(response)["code"] == "ENDPOINT_INVALID_URL"
        assert elapsed < 1.0

    oversized = client.post(
        base, json=_endpoint_payload(url="https://fraud.example.com/" + "a" * 2100)
    )
    assert oversized.status_code == 422

    bad_auth = client.post(
        base, json=_endpoint_payload(name="leaky", auth_reference="sk-live-abc123")
    )
    assert bad_auth.status_code == 409
    assert _error(bad_auth)["code"] == "INVALID_AUTH_REFERENCE"


def test_create_endpoint_on_archived_deployment_returns_409(client):
    model_id = _model(client)
    version = _version(client, model_id)
    deployment = _create_deployment(client, version["id"])
    assert client.delete(f"{API}/deployments/{deployment['id']}").status_code == 204

    response = client.post(
        f"{API}/deployments/{deployment['id']}/endpoints", json=_endpoint_payload()
    )
    assert response.status_code == 409
    assert _error(response)["code"] == "DEPLOYMENT_ALREADY_ARCHIVED"


def test_duplicate_deployment_identity_returns_409(client):
    model_id = _model(client)
    version = _version(client, model_id)
    _create_deployment(client, version["id"])
    duplicate = client.post(
        f"{API}/model-versions/{version['id']}/deployments", json=_deployment_payload()
    )
    assert duplicate.status_code == 409
    assert _error(duplicate)["code"] == "DEPLOYMENT_DUPLICATE"


def test_validation_error_envelope(client):
    model_id = _model(client)
    version = _version(client, model_id)

    missing = client.post(
        f"{API}/model-versions/{version['id']}/deployments",
        json={"name": "no-environment"},
    )
    assert missing.status_code == 422
    error = _error(missing)
    assert error["code"] == "VALIDATION_ERROR"
    assert ["body", "environment"] in [detail["loc"] for detail in error["details"]]

    bad_env = client.post(
        f"{API}/model-versions/{version['id']}/deployments",
        json=_deployment_payload(environment="qa"),
    )
    assert bad_env.status_code == 422

    negative = client.post(
        f"{API}/model-versions/{version['id']}/deployments",
        json=_deployment_payload(desired_replicas=-1),
    )
    assert negative.status_code == 422

    secret_metadata = client.post(
        f"{API}/model-versions/{version['id']}/deployments",
        json=_deployment_payload(metadata={"api_key": "x"}),
    )
    assert secret_metadata.status_code == 422

    status_field = client.post(
        f"{API}/model-versions/{version['id']}/deployments",
        json=_deployment_payload(status="active"),
    )
    assert status_field.status_code == 422


def test_invalid_sort_field_returns_400(client):
    response = client.get(f"{API}/deployments", params={"sort_by": "nope"})
    assert response.status_code == 400
    error = _error(response)
    assert error["code"] == "INVALID_SORT_FIELD"
    assert error["http_status"] == 400
    assert "traceback" not in response.text.lower()
