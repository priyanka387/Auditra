from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa

from app.main import app
from app.modules.model_inventory.models import Model, ModelProvider, ModelType
from app.modules.model_inventory.services import ModelService

API = "/api/v1"


def _payload(**overrides):
    base = {
        "provider_slug": "openai",
        "model_type_slug": "llm",
        "name": "Customer Support LLM",
        "native_model_id": "provider-specific-model-id",
        "description": "Answers customer questions",
        "owner_name": "AI Team",
        "owner_contact": "ai-team@example.com",
        "team_name": "Support",
        "lifecycle_state": "REGISTERED",
        "source_type": "MANUAL",
        "metadata": {"environment": "Development"},
        "tags": ["customer-support", "development"],
    }
    base.update(overrides)
    return base


def _error(response):
    return response.json()["error"]


def test_e2e_registration_workflow(client):
    created = client.post(f"{API}/models", json=_payload())
    assert created.status_code == 201
    body = created.json()
    assert body["canonical_key"] == "openai|provider-specific-model-id"
    assert body["lifecycle_state"] == "REGISTERED"
    assert body["provider"] == {
        "id": body["provider"]["id"],
        "slug": "openai",
        "name": "OpenAI",
    }
    assert body["model_type"]["slug"] == "llm"
    assert [tag["key"] for tag in body["tags"]] == ["customer-support", "development"]
    assert body["metadata"] == {"environment": "Development"}
    assert body["version"] == 1
    assert body["created_by"] is None
    assert body["updated_by"] is None
    assert created.headers["X-Request-ID"]

    listed = client.get(f"{API}/models")
    assert listed.status_code == 200
    page = listed.json()
    assert page["total"] == 1
    assert [item["id"] for item in page["items"]] == [body["id"]]

    fetched = client.get(f"{API}/models/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["canonical_key"] == "openai|provider-specific-model-id"

    patched = client.patch(f"{API}/models/{body['id']}", json={"name": "Customer Support LLM v2"})
    assert patched.status_code == 200
    updated = patched.json()
    assert updated["name"] == "Customer Support LLM v2"
    assert updated["updated_at"] != body["updated_at"]
    assert updated["version"] == 2

    duplicate = client.post(f"{API}/models", json=_payload())
    assert duplicate.status_code == 409
    assert _error(duplicate)["code"] == "MODEL_ALREADY_EXISTS"

    archived = client.delete(f"{API}/models/{body['id']}")
    assert archived.status_code == 204
    assert archived.text == ""


def test_description_and_ownership_roundtrip(client):
    created = client.post(f"{API}/models", json=_payload()).json()
    fetched = client.get(f"{API}/models/{created['id']}").json()
    assert fetched["description"] == "Answers customer questions"
    assert fetched["owner_name"] == "AI Team"
    assert fetched["owner_contact"] == "ai-team@example.com"
    assert fetched["team_name"] == "Support"


def test_get_missing_returns_404_envelope(client):
    response = client.get(f"{API}/models/{uuid4()}", headers={"X-Request-ID": "req-missing-1"})
    assert response.status_code == 404
    error = _error(response)
    assert error["code"] == "MODEL_NOT_FOUND"
    assert error["http_status"] == 404
    assert error["details"] == {}
    assert error["request_id"] == "req-missing-1"
    assert response.headers["X-Request-ID"] == "req-missing-1"
    assert "Traceback" not in response.text


def test_patch_identity_fields_rejected(client):
    model_id = client.post(f"{API}/models", json=_payload()).json()["id"]

    patched = client.patch(f"{API}/models/{model_id}", json={"native_model_id": "renamed-identity"})
    assert patched.status_code == 409
    assert _error(patched)["code"] == "MODEL_IDENTITY_CONFLICT"

    fetched = client.get(f"{API}/models/{model_id}").json()
    assert fetched["native_model_id"] == "provider-specific-model-id"
    assert fetched["canonical_key"] == "openai|provider-specific-model-id"


def test_patch_invalid_lifecycle_returns_409(client):
    model_id = client.post(f"{API}/models", json=_payload()).json()["id"]

    patched = client.patch(f"{API}/models/{model_id}", json={"lifecycle_state": "RETIRED"})
    assert patched.status_code == 409
    assert _error(patched)["code"] == "INVALID_LIFECYCLE_TRANSITION"

    fetched = client.get(f"{API}/models/{model_id}").json()
    assert fetched["lifecycle_state"] == "REGISTERED"


def test_archive_workflow(client):
    model_id = client.post(f"{API}/models", json=_payload()).json()["id"]

    assert client.delete(f"{API}/models/{model_id}").status_code == 204

    default_page = client.get(f"{API}/models").json()
    assert default_page["total"] == 0
    assert default_page["items"] == []

    archived_page = client.get(f"{API}/models", params={"include_archived": "true"}).json()
    assert archived_page["total"] == 1
    assert archived_page["items"][0]["id"] == model_id
    assert archived_page["items"][0]["lifecycle_state"] == "ARCHIVED"

    explicit_archived = client.get(f"{API}/models", params={"lifecycle_state": "ARCHIVED"}).json()
    assert explicit_archived["total"] == 0

    assert client.get(f"{API}/models/{model_id}").status_code == 200

    patched = client.patch(f"{API}/models/{model_id}", json={"name": "Nope"})
    assert patched.status_code == 409
    assert _error(patched)["code"] == "MODEL_ARCHIVED"

    assert client.delete(f"{API}/models/{model_id}").status_code == 204


def test_search_filter_sort_pagination(client):
    client.post(
        f"{API}/models",
        json=_payload(name="Alpha Search Target", native_model_id="alpha-1"),
    )
    client.post(
        f"{API}/models",
        json=_payload(name="Beta", native_model_id="beta-1", provider_slug="anthropic"),
    )
    client.post(f"{API}/models", json=_payload(name="Gamma", native_model_id="gamma-1"))

    search_hit = client.get(f"{API}/models", params={"search": "Alpha"}).json()
    assert search_hit["total"] == 1
    assert search_hit["items"][0]["name"] == "Alpha Search Target"

    search_miss = client.get(f"{API}/models", params={"search": "no-such-model"}).json()
    assert search_miss["total"] == 0

    by_provider = client.get(f"{API}/models", params={"provider": "anthropic"}).json()
    assert by_provider["total"] == 1
    assert by_provider["items"][0]["name"] == "Beta"

    ascending = client.get(f"{API}/models", params={"sort_by": "name", "sort_order": "asc"}).json()
    assert [item["name"] for item in ascending["items"]] == [
        "Alpha Search Target",
        "Beta",
        "Gamma",
    ]

    page_two = client.get(
        f"{API}/models",
        params={"sort_by": "name", "sort_order": "asc", "page_size": 2, "page": 2},
    ).json()
    assert page_two["page"] == 2
    assert page_two["page_size"] == 2
    assert page_two["total"] == 3
    assert page_two["total_pages"] == 2
    assert [item["name"] for item in page_two["items"]] == ["Gamma"]

    oversized = client.get(f"{API}/models", params={"page_size": 500})
    assert oversized.status_code == 422
    assert _error(oversized)["code"] == "VALIDATION_ERROR"


def test_invalid_sort_field_returns_400(client):
    response = client.get(f"{API}/models", params={"sort_by": "nope"})
    assert response.status_code == 400
    error = _error(response)
    assert error["code"] == "INVALID_SORT_FIELD"
    assert error["http_status"] == 400
    assert error["details"] == {}
    assert error["request_id"] == response.headers["X-Request-ID"]


def test_secret_metadata_returns_422(client):
    response = client.post(f"{API}/models", json=_payload(metadata={"api_key": "x"}))
    assert response.status_code == 422
    error = _error(response)
    assert error["code"] == "VALIDATION_ERROR"
    assert error["http_status"] == 422
    assert ["body", "metadata"] in [detail["loc"] for detail in error["details"]]
    assert set(error["details"][0]) == {"loc", "msg", "type"}


def test_oversized_metadata_returns_422(client):
    response = client.post(f"{API}/models", json=_payload(metadata={"blob": "x" * 20_000}))
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_provider_inactive_returns_409(client, db):
    provider = db.scalar(sa.select(ModelProvider).where(ModelProvider.slug == "openai"))
    provider.is_active = False
    db.commit()

    response = client.post(f"{API}/models", json=_payload())
    assert response.status_code == 409
    assert _error(response)["code"] == "PROVIDER_INACTIVE"


def test_lookup_endpoints(client, db):
    meta = db.scalar(sa.select(ModelProvider).where(ModelProvider.slug == "meta"))
    meta.is_active = False
    db.commit()

    providers = client.get(f"{API}/model-providers")
    assert providers.status_code == 200
    provider_body = providers.json()
    assert len(provider_body) >= 13
    assert set(provider_body[0]) == {"id", "slug", "name"}
    provider_names = [entry["name"] for entry in provider_body]
    assert provider_names == sorted(provider_names)
    assert any(entry["slug"] == "meta" for entry in provider_body)

    types = client.get(f"{API}/model-types")
    assert types.status_code == 200
    type_body = types.json()
    assert len(type_body) >= 15
    type_names = [entry["name"] for entry in type_body]
    assert type_names == sorted(type_names)


def test_custom_provider_and_type_accepted(client, db):
    db.add(ModelProvider(slug="acme", name="Acme AI"))
    db.add(ModelType(slug="audio", name="Audio Model", is_active=True, is_system=False))
    db.commit()

    created = client.post(
        f"{API}/models", json=_payload(provider_slug="acme", model_type_slug="audio")
    )
    assert created.status_code == 201
    body = created.json()
    assert body["provider"]["slug"] == "acme"
    assert body["model_type"]["slug"] == "audio"


def test_openapi_contract(client):
    paths = app.openapi()["paths"]
    assert {
        "/api/v1/models",
        "/api/v1/models/{model_id}",
        "/api/v1/models/{model_id}/versions",
        "/api/v1/models/{model_id}/versions/{version_id}",
        "/api/v1/models/{model_id}/versions/{version_id}/lifecycle",
        "/api/v1/model-providers",
        "/api/v1/model-types",
        "/health",
    } <= set(paths)
    assert {"post", "get"} <= set(paths["/api/v1/models"])
    assert {"get", "patch", "delete"} <= set(paths["/api/v1/models/{model_id}"])
    assert {"post", "get"} <= set(paths["/api/v1/models/{model_id}/versions"])
    assert {"get", "patch", "delete"} <= set(
        paths["/api/v1/models/{model_id}/versions/{version_id}"]
    )
    assert {"post"} <= set(paths["/api/v1/models/{model_id}/versions/{version_id}/lifecycle"])
    assert sum(len(methods) for methods in paths.values()) == 14

    schemas = app.openapi()["components"]["schemas"]
    assert {"ModelVersionCreate", "ModelVersionResponse"} <= set(schemas)

    patch_doc = paths["/api/v1/models/{model_id}/versions/{version_id}"]["patch"]
    assert "immutable" in (patch_doc.get("description") or "").lower()


def test_error_body_has_no_traceback(client, monkeypatch):
    missing = client.get(f"{API}/models/{uuid4()}")
    assert missing.status_code == 404
    assert "traceback" not in missing.text.lower()

    def _boom(*_args, **_kwargs):
        raise RuntimeError("boom internals")

    monkeypatch.setattr(ModelService, "get_model", _boom)
    broken = client.get(f"{API}/models/{uuid4()}")
    assert broken.status_code == 500
    error = _error(broken)
    assert error["code"] == "INTERNAL_ERROR"
    assert error["message"] == "Internal server error"
    assert error["details"] == {}
    assert error["http_status"] == 500
    assert error["request_id"] == broken.headers["X-Request-ID"]
    assert "traceback" not in broken.text.lower()
    assert "boom internals" not in broken.text


def test_malformed_tag_returns_422(client):
    response = client.post(f"{API}/models", json=_payload(tags=["=x"]))
    assert response.status_code == 422
    error = _error(response)
    assert error["code"] == "VALIDATION_ERROR"
    assert ["body", "tags"] in [detail["loc"] for detail in error["details"]]


def test_inactive_model_type_returns_409(client, db):
    model_type = db.scalar(sa.select(ModelType).where(ModelType.slug == "llm"))
    model_type.is_active = False
    db.commit()

    response = client.post(f"{API}/models", json=_payload())
    assert response.status_code == 409
    assert _error(response)["code"] == "MODEL_TYPE_INACTIVE"


def test_oversized_tag_returns_422(client):
    key_response = client.post(f"{API}/models", json=_payload(tags=["k" * 200]))
    assert key_response.status_code == 422
    key_error = _error(key_response)
    assert key_error["code"] == "VALIDATION_ERROR"
    assert ["body", "tags"] in [detail["loc"] for detail in key_error["details"]]

    value_response = client.post(f"{API}/models", json=_payload(tags=["k=" + "v" * 600]))
    assert value_response.status_code == 422
    assert _error(value_response)["code"] == "VALIDATION_ERROR"


def test_patch_lifecycle_archived_sets_archived_at(client):
    model_id = client.post(f"{API}/models", json=_payload()).json()["id"]

    patched = client.patch(f"{API}/models/{model_id}", json={"lifecycle_state": "ARCHIVED"})
    assert patched.status_code == 200
    body = patched.json()
    assert body["lifecycle_state"] == "ARCHIVED"
    assert body["archived_at"] is not None

    default_page = client.get(f"{API}/models").json()
    assert default_page["total"] == 0
    assert default_page["items"] == []

    assert client.delete(f"{API}/models/{model_id}").status_code == 204


def test_unknown_provider_returns_404(client):
    response = client.post(f"{API}/models", json=_payload(provider_slug="does-not-exist"))
    assert response.status_code == 404
    assert _error(response)["code"] == "PROVIDER_NOT_FOUND"


def test_unknown_model_type_returns_404(client):
    response = client.post(f"{API}/models", json=_payload(model_type_slug="does-not-exist"))
    assert response.status_code == 404
    assert _error(response)["code"] == "MODEL_TYPE_NOT_FOUND"


def test_date_range_filters(client, db):
    first = client.post(
        f"{API}/models", json=_payload(name="Early", native_model_id="early-1")
    ).json()
    second = client.post(
        f"{API}/models", json=_payload(name="Late", native_model_id="late-1")
    ).json()

    db.execute(
        sa.update(Model)
        .where(Model.id == first["id"])
        .values(created_at=datetime(2026, 1, 1, tzinfo=UTC))
    )
    db.execute(
        sa.update(Model)
        .where(Model.id == second["id"])
        .values(created_at=datetime(2026, 6, 1, tzinfo=UTC))
    )
    db.commit()

    after = client.get(f"{API}/models", params={"created_after": "2026-03-01T00:00:00Z"}).json()
    assert after["total"] == 1
    assert after["items"][0]["id"] == second["id"]

    before = client.get(f"{API}/models", params={"created_before": "2026-03-01T00:00:00Z"}).json()
    assert before["total"] == 1
    assert before["items"][0]["id"] == first["id"]
