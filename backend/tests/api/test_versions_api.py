from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa

from app.modules.model_inventory.models import ModelVersion
from app.modules.model_inventory.services import ModelVersionService

API = "/api/v1"


def _model(client, **overrides):
    payload = {
        "provider_slug": "openai",
        "model_type_slug": "llm",
        "name": "Customer Support LLM",
        "native_model_id": "provider-specific-model-id",
    }
    payload.update(overrides)
    response = client.post(f"{API}/models", json=payload)
    assert response.status_code == 201
    return response.json()["id"]


def _version(**overrides):
    payload = {
        "identity_type": "release",
        "version_label": "v2.1.0",
        "native_version_id": "release-210",
    }
    payload.update(overrides)
    return payload


def _create_version(client, model_id, **overrides):
    response = client.post(f"{API}/models/{model_id}/versions", json=_version(**overrides))
    assert response.status_code == 201
    return response.json()


def _error(response):
    return response.json()["error"]


def test_e2e_version_workflow(client):
    model_id = _model(client)
    created = client.post(
        f"{API}/models/{model_id}/versions", json=_version(metadata={"env": "dev"})
    )
    assert created.status_code == 201
    body = created.json()
    assert body["lifecycle_state"] == "DRAFT"
    assert body["canonical_version_key"] == "release:release-210"
    assert body["version"] == 1
    assert body["tenant_id"] and body["model_id"] == model_id
    assert body["metadata"] == {"env": "dev"}

    fetched = client.get(f"{API}/models/{model_id}/versions/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]

    collection = client.get(f"{API}/models/{model_id}/versions").json()
    assert collection["total"] == 1
    assert [item["id"] for item in collection["items"]] == [body["id"]]

    patched = client.patch(
        f"{API}/models/{model_id}/versions/{body['id']}",
        json={"description": "d2", "metadata": {"framework": "pytorch"}},
    )
    assert patched.status_code == 200
    assert patched.json()["version"] == 2
    assert patched.json()["description"] == "d2"

    transitioned = client.post(
        f"{API}/models/{model_id}/versions/{body['id']}/lifecycle",
        json={"target_state": "ACTIVE"},
    )
    assert transitioned.status_code == 200
    assert transitioned.json()["lifecycle_state"] == "ACTIVE"

    deleted = client.delete(f"{API}/models/{model_id}/versions/{body['id']}")
    assert deleted.status_code == 204
    assert deleted.text == ""

    assert client.get(f"{API}/models/{model_id}/versions").json()["total"] == 0
    archived_page = client.get(
        f"{API}/models/{model_id}/versions", params={"include_archived": "true"}
    ).json()
    assert archived_page["total"] == 1
    assert archived_page["items"][0]["id"] == body["id"]


def test_duplicate_version_returns_409_envelope(client):
    model_id = _model(client)
    _create_version(client, model_id)

    duplicate = client.post(
        f"{API}/models/{model_id}/versions",
        json=_version(version_label="January Release"),
    )
    assert duplicate.status_code == 409
    error = _error(duplicate)
    assert error["code"] == "MODEL_VERSION_ALREADY_EXISTS"
    assert error["http_status"] == 409
    for key in ("message", "details", "request_id", "http_status"):
        assert key in error


def test_get_missing_returns_404_envelope(client):
    model_id = _model(client)
    response = client.get(
        f"{API}/models/{model_id}/versions/{uuid4()}",
        headers={"X-Request-ID": "req-ver-missing"},
    )
    assert response.status_code == 404
    error = _error(response)
    assert error["code"] == "MODEL_VERSION_NOT_FOUND"
    assert error["http_status"] == 404
    assert error["details"] == {}
    assert error["request_id"] == "req-ver-missing"
    assert response.headers["X-Request-ID"] == "req-ver-missing"
    assert "Traceback" not in response.text


def test_get_version_of_other_model_returns_404(client):
    first = _model(client)
    second = _model(client, native_model_id="other-model-id", name="Other")
    version = _create_version(client, first)

    response = client.get(f"{API}/models/{second}/versions/{version['id']}")
    assert response.status_code == 404
    assert _error(response)["code"] == "MODEL_VERSION_NOT_FOUND"


def test_patch_identity_fields_rejected(client):
    model_id = _model(client)
    version = _create_version(client, model_id)

    attempts = (
        {"identity_type": "native"},
        {"native_version_id": "changed-1"},
        {"version_label": "v9"},
        {"canonical_version_key": "release:changed-1"},
        {"model_id": str(uuid4())},
    )
    for attempt in attempts:
        patched = client.patch(f"{API}/models/{model_id}/versions/{version['id']}", json=attempt)
        assert patched.status_code == 409
        assert _error(patched)["code"] == "MODEL_VERSION_IDENTITY_IMMUTABLE"

    fetched = client.get(f"{API}/models/{model_id}/versions/{version['id']}").json()
    assert fetched["identity_type"] == "release"
    assert fetched["version_label"] == "v2.1.0"
    assert fetched["native_version_id"] == "release-210"
    assert fetched["canonical_version_key"] == "release:release-210"
    assert fetched["version"] == 1


def test_patch_lifecycle_via_patch_rejected(client):
    model_id = _model(client)
    version = _create_version(client, model_id)

    patched = client.patch(
        f"{API}/models/{model_id}/versions/{version['id']}",
        json={"lifecycle_state": "ACTIVE"},
    )
    assert patched.status_code == 422
    assert _error(patched)["code"] == "VALIDATION_ERROR"


def test_lifecycle_transitions_via_api(client):
    model_id = _model(client)
    version = _create_version(client, model_id)
    path = f"{API}/models/{model_id}/versions/{version['id']}/lifecycle"

    for target in ("ACTIVE", "DEPRECATED", "ACTIVE", "RETIRED"):
        response = client.post(path, json={"target_state": target})
        assert response.status_code == 200
        assert response.json()["lifecycle_state"] == target

    invalid = client.post(path, json={"target_state": "ACTIVE"})
    assert invalid.status_code == 409
    assert _error(invalid)["code"] == "INVALID_MODEL_VERSION_LIFECYCLE_TRANSITION"

    assert client.delete(f"{API}/models/{model_id}/versions/{version['id']}").status_code == 204
    archived = client.post(path, json={"target_state": "ACTIVE"})
    assert archived.status_code == 409
    assert _error(archived)["code"] == "MODEL_VERSION_ALREADY_ARCHIVED"


def test_create_under_archived_model_returns_409(client):
    model_id = _model(client)
    assert client.delete(f"{API}/models/{model_id}").status_code == 204

    response = client.post(f"{API}/models/{model_id}/versions", json=_version())
    assert response.status_code == 409
    assert _error(response)["code"] == "MODEL_ARCHIVED"


def test_create_missing_model_returns_404(client):
    response = client.post(f"{API}/models/{uuid4()}/versions", json=_version())
    assert response.status_code == 404
    assert _error(response)["code"] == "MODEL_NOT_FOUND"


def test_archive_workflow(client):
    model_id = _model(client)
    version = _create_version(client, model_id)
    path = f"{API}/models/{model_id}/versions/{version['id']}"

    assert client.delete(path).status_code == 204

    patched = client.patch(path, json={"description": "nope"})
    assert patched.status_code == 409
    assert _error(patched)["code"] == "MODEL_VERSION_ALREADY_ARCHIVED"

    transitioned = client.post(f"{path}/lifecycle", json={"target_state": "ACTIVE"})
    assert transitioned.status_code == 409
    assert _error(transitioned)["code"] == "MODEL_VERSION_ALREADY_ARCHIVED"

    assert client.delete(path).status_code == 204

    explicit = client.get(
        f"{API}/models/{model_id}/versions", params={"lifecycle_state": "ARCHIVED"}
    ).json()
    assert explicit["total"] == 0
    assert client.get(path).status_code == 200


def test_search_filter_sort_pagination(client):
    model_id = _model(client)
    alpha = _create_version(
        client,
        model_id,
        identity_type="native",
        version_label="v1-alpha",
        native_version_id="n1",
    )
    beta = _create_version(client, model_id, version_label="v2-beta", native_version_id="n2")
    gamma = _create_version(
        client,
        model_id,
        identity_type="label",
        version_label="v3-gamma",
        native_version_id=None,
        source_type="import",
    )
    client.post(
        f"{API}/models/{model_id}/versions/{beta['id']}/lifecycle",
        json={"target_state": "ACTIVE"},
    )

    by_label = client.get(f"{API}/models/{model_id}/versions", params={"search": "beta"}).json()
    assert by_label["total"] == 1
    assert by_label["items"][0]["id"] == beta["id"]

    by_native = client.get(f"{API}/models/{model_id}/versions", params={"search": "n1"}).json()
    assert by_native["total"] == 1
    assert by_native["items"][0]["id"] == alpha["id"]

    by_state = client.get(
        f"{API}/models/{model_id}/versions", params={"lifecycle_state": "ACTIVE"}
    ).json()
    assert by_state["total"] == 1
    assert by_state["items"][0]["id"] == beta["id"]

    by_identity = client.get(
        f"{API}/models/{model_id}/versions", params={"identity_type": "native"}
    ).json()
    assert by_identity["total"] == 1
    assert by_identity["items"][0]["id"] == alpha["id"]

    by_source = client.get(
        f"{API}/models/{model_id}/versions", params={"source_type": "import"}
    ).json()
    assert by_source["total"] == 1
    assert by_source["items"][0]["id"] == gamma["id"]

    ascending = client.get(
        f"{API}/models/{model_id}/versions",
        params={"sort_by": "version_label", "sort_order": "asc"},
    ).json()
    assert [item["version_label"] for item in ascending["items"]] == [
        "v1-alpha",
        "v2-beta",
        "v3-gamma",
    ]

    page_two = client.get(
        f"{API}/models/{model_id}/versions",
        params={"sort_by": "version_label", "sort_order": "asc", "page_size": 2, "page": 2},
    ).json()
    assert page_two["page"] == 2
    assert page_two["total"] == 3
    assert page_two["total_pages"] == 2
    assert [item["version_label"] for item in page_two["items"]] == ["v3-gamma"]

    oversized = client.get(f"{API}/models/{model_id}/versions", params={"page_size": 500})
    assert oversized.status_code == 422
    assert _error(oversized)["code"] == "VALIDATION_ERROR"


def test_date_range_filters(client, db):
    model_id = _model(client)
    first = _create_version(client, model_id, native_version_id="range-1")
    second = _create_version(client, model_id, native_version_id="range-2")

    db.execute(
        sa.update(ModelVersion)
        .where(ModelVersion.id == first["id"])
        .values(created_at=datetime(2026, 1, 1, tzinfo=UTC))
    )
    db.execute(
        sa.update(ModelVersion)
        .where(ModelVersion.id == second["id"])
        .values(created_at=datetime(2026, 6, 1, tzinfo=UTC))
    )
    db.commit()

    after = client.get(
        f"{API}/models/{model_id}/versions", params={"created_after": "2026-03-01T00:00:00Z"}
    ).json()
    assert after["total"] == 1
    assert after["items"][0]["id"] == second["id"]

    before = client.get(
        f"{API}/models/{model_id}/versions", params={"created_before": "2026-03-01T00:00:00Z"}
    ).json()
    assert before["total"] == 1
    assert before["items"][0]["id"] == first["id"]


def test_invalid_sort_field_returns_400(client):
    model_id = _model(client)
    response = client.get(f"{API}/models/{model_id}/versions", params={"sort_by": "nope"})
    assert response.status_code == 400
    error = _error(response)
    assert error["code"] == "INVALID_SORT_FIELD"
    assert error["http_status"] == 400
    assert error["details"] == {}
    assert error["request_id"] == response.headers["X-Request-ID"]


def test_validation_errors(client):
    model_id = _model(client)

    secret = client.post(
        f"{API}/models/{model_id}/versions", json=_version(metadata={"api_key": "x"})
    )
    assert secret.status_code == 422
    error = _error(secret)
    assert error["code"] == "VALIDATION_ERROR"
    assert ["body", "metadata"] in [detail["loc"] for detail in error["details"]]

    oversized = client.post(
        f"{API}/models/{model_id}/versions", json=_version(metadata={"blob": "x" * 20_000})
    )
    assert oversized.status_code == 422
    assert _error(oversized)["code"] == "VALIDATION_ERROR"

    bad_identity = client.post(
        f"{API}/models/{model_id}/versions", json=_version(identity_type="semver")
    )
    assert bad_identity.status_code == 422
    assert _error(bad_identity)["code"] == "VALIDATION_ERROR"

    bad_source = client.post(f"{API}/models/{model_id}/versions", json=_version(source_type="sdk"))
    assert bad_source.status_code == 422
    assert _error(bad_source)["code"] == "VALIDATION_ERROR"


def test_cross_tenant_version_returns_404(client, db):
    model_id = _model(client)
    foreign_id = uuid4()
    db.add(
        ModelVersion(
            id=foreign_id,
            tenant_id=uuid4(),
            model_id=model_id,
            identity_type="native",
            version_label="foreign",
            native_version_id="foreign-1",
            canonical_version_key="native:foreign-1",
            lifecycle_state="DRAFT",
            source_type="manual",
        )
    )
    db.commit()

    response = client.get(f"{API}/models/{model_id}/versions/{foreign_id}")
    assert response.status_code == 404
    assert _error(response)["code"] == "MODEL_VERSION_NOT_FOUND"


def test_error_body_has_no_traceback(client, monkeypatch):
    def _boom(*_args, **_kwargs):
        raise RuntimeError("boom internals")

    monkeypatch.setattr(ModelVersionService, "get_version", _boom)
    broken = client.get(f"{API}/models/{uuid4()}/versions/{uuid4()}")
    assert broken.status_code == 500
    error = _error(broken)
    assert error["code"] == "INTERNAL_ERROR"
    assert error["message"] == "Internal server error"
    assert error["details"] == {}
    assert error["http_status"] == 500
    assert error["request_id"] == broken.headers["X-Request-ID"]
    assert "traceback" not in broken.text.lower()
    assert "boom internals" not in broken.text
