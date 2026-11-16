"""API tests: /api/v1/risks (RF-01 risk register endpoints)."""

from datetime import UTC, datetime
from uuid import uuid4

from app.core.config import settings
from app.modules.asset_discovery.models import DiscoveredAsset, DiscoverySource

API = "/api/v1/risks"
CATEGORY_API = "/api/v1/risk-categories"


def _error(response):
    return response.json()["error"]


def _make_asset(db, name: str) -> str:
    source = DiscoverySource(
        tenant_id=settings.default_tenant_id,
        name=f"src-{uuid4().hex[:8]}",
        source_type="repository",
        config={"location": "/workspace/x"},
    )
    db.add(source)
    db.flush()
    asset = DiscoveredAsset(
        tenant_id=settings.default_tenant_id,
        source_id=source.id,
        asset_type="MODEL",
        name=name,
        normalized_identity=f"MODEL|{name}|{uuid4().hex[:6]}",
        metadata_={},
        first_seen_at=datetime.now(UTC),
        last_seen_at=datetime.now(UTC),
    )
    db.add(asset)
    db.commit()
    return str(asset.id)


def _category_id(client, db, name: str = "Security") -> str:
    """A tenant-visible category: created through the API so the app's own
    session sees it (the test session and the app session differ)."""
    existing = client.get(CATEGORY_API, params={"search": name}).json()
    if existing["total"]:
        return existing["items"][0]["id"]
    response = client.post(CATEGORY_API, json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _payload(category_id: str, **overrides) -> dict:
    payload = {
        "title": "Unreviewed model in payments",
        "description": "Customer-facing model shipped without review",
        "category_id": category_id,
    }
    payload.update(overrides)
    return payload


def test_create_risk_returns_201_and_persists(client, db):
    category_id = _category_id(client, db)
    response = client.post(API, json=_payload(category_id, owner="priya", team="risk-team"))
    assert response.status_code == 201, response.text

    body = response.json()
    assert body["title"] == "Unreviewed model in payments"
    assert body["category_id"] == category_id
    assert body["category"]["slug"] == "security"
    assert body["status"] == "open"
    assert body["source"] == "manual"
    assert body["owner"] == "priya"
    assert body["asset"] is None
    assert body["archived_at"] is None

    fetched = client.get(f"{API}/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["title"] == "Unreviewed model in payments"


def test_create_risk_validation_error_on_blank_title(client, db):
    category_id = _category_id(client, db)
    response = client.post(API, json=_payload(category_id, title="   "))
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_create_risk_rejects_extra_body_fields(client, db):
    category_id = _category_id(client, db)
    payload = _payload(category_id, residual_score=17)
    response = client.post(API, json=payload)
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_create_risk_with_unknown_category_returns_404(client):
    response = client.post(API, json=_payload(str(uuid4())))
    assert response.status_code == 404
    assert _error(response)["code"] == "RISK_CATEGORY_NOT_FOUND"


def test_create_risk_with_unknown_asset_returns_404(client, db):
    category_id = _category_id(client, db)
    response = client.post(API, json=_payload(category_id, asset_id=str(uuid4())))
    assert response.status_code == 404
    assert _error(response)["code"] == "RISK_ASSET_NOT_FOUND"


def test_get_risk_returns_resolved_category_and_asset(client, db):
    category_id = _category_id(client, db)
    asset_id = _make_asset(db, "payments-gpt-4o")
    created = client.post(API, json=_payload(category_id, asset_id=asset_id)).json()

    body = client.get(f"{API}/{created['id']}").json()
    assert body["category"] == {
        "id": category_id,
        "name": "Security",
        "slug": "security",
    }
    assert body["asset"] == {"id": asset_id, "name": "payments-gpt-4o"}


def test_get_unknown_risk_returns_404_envelope(client):
    response = client.get(f"{API}/{uuid4()}")
    assert response.status_code == 404
    error = _error(response)
    assert error["code"] == "RISK_NOT_FOUND"
    assert error["request_id"]
    assert error["http_status"] == 404


def test_patch_risk_updates_fields(client, db):
    category_id = _category_id(client, db)
    created = client.post(API, json=_payload(category_id)).json()

    response = client.patch(
        f"{API}/{created['id']}",
        json={"status": "in_review", "owner": "ram"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "in_review"
    assert body["owner"] == "ram"
    assert body["title"] == created["title"]  # untouched fields stay


def test_patch_unknown_risk_returns_404(client):
    response = client.patch(f"{API}/{uuid4()}", json={"title": "nope"})
    assert response.status_code == 404
    assert _error(response)["code"] == "RISK_NOT_FOUND"


def test_delete_risk_archives_then_get_still_returns_row(client, db):
    category_id = _category_id(client, db)
    created = client.post(API, json=_payload(category_id)).json()

    response = client.delete(f"{API}/{created['id']}")
    assert response.status_code == 204
    assert response.content == b""

    fetched = client.get(f"{API}/{created['id']}")
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["archived_at"] is not None
    # soft archive only: relationships remain intact
    assert body["category_id"] == category_id

    # an archived risk no longer accepts mutations
    patched = client.patch(f"{API}/{created['id']}", json={"title": "too late"})
    assert patched.status_code == 409
    assert _error(patched)["code"] == "RISK_ARCHIVED"


def test_delete_risk_is_idempotent(client, db):
    category_id = _category_id(client, db)
    created = client.post(API, json=_payload(category_id)).json()

    first = client.delete(f"{API}/{created['id']}")
    second = client.delete(f"{API}/{created['id']}")
    assert first.status_code == 204
    assert second.status_code == 204


def test_delete_unknown_risk_returns_404(client):
    response = client.delete(f"{API}/{uuid4()}")
    assert response.status_code == 404
    assert _error(response)["code"] == "RISK_NOT_FOUND"


def test_list_risks_pagination_metadata(client, db):
    category_id = _category_id(client, db)
    for index in range(3):
        created = client.post(API, json=_payload(category_id, title=f"Risk {index}"))
        assert created.status_code == 201, created.text

    body = client.get(API, params={"page": 1, "page_size": 2, "sort_by": "title"}).json()
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert body["total"] == 3
    assert body["total_pages"] == 2
    assert len(body["items"]) == 2

    second = client.get(API, params={"page": 2, "page_size": 2, "sort_by": "title"}).json()
    assert len(second["items"]) == 1


def test_list_risks_filters_by_status_and_category(client, db):
    security_id = _category_id(client, db, "Security")
    privacy_id = _category_id(client, db, "Privacy")
    open_risk = client.post(API, json=_payload(security_id, title="Open one")).json()
    client.post(
        API,
        json=_payload(privacy_id, title="Review one", status="in_review"),
    )

    by_status = client.get(API, params={"status": "in_review"}).json()
    assert by_status["total"] == 1
    assert by_status["items"][0]["title"] == "Review one"

    by_category = client.get(API, params={"category_id": privacy_id}).json()
    assert by_category["total"] == 1
    assert by_category["items"][0]["title"] == "Review one"

    by_owner = client.get(API, params={"owner": "nobody"}).json()
    assert by_owner["total"] == 0
    assert open_risk["owner"] is None


def test_list_risks_search_by_title(client, db):
    category_id = _category_id(client, db)
    client.post(API, json=_payload(category_id, title="Encryption gap"))
    client.post(API, json=_payload(category_id, title="Model drift"))

    body = client.get(API, params={"search": "encryption"}).json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "Encryption gap"


def test_list_risks_archived_filter(client, db):
    category_id = _category_id(client, db)
    active = client.post(API, json=_payload(category_id, title="Active")).json()
    removed = client.post(API, json=_payload(category_id, title="Removed")).json()
    assert client.delete(f"{API}/{removed['id']}").status_code == 204

    default = client.get(API).json()
    assert default["total"] == 1
    assert default["items"][0]["id"] == active["id"]

    archived = client.get(API, params={"archived": "true"}).json()
    assert archived["total"] == 1
    assert archived["items"][0]["id"] == removed["id"]


def test_list_risks_invalid_sort_returns_400(client):
    response = client.get(API, params={"sort_by": "nope"})
    assert response.status_code == 400
    assert _error(response)["code"] == "RISK_QUERY_INVALID"


def test_list_risks_unknown_filter_rejected(client):
    response = client.get(API, params={"residual_score": "17"})
    assert response.status_code == 400
    assert _error(response)["code"] == "RISK_QUERY_INVALID"


def test_list_risks_unknown_status_returns_422(client):
    response = client.get(API, params={"status": "mitigated"})
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_openapi_lists_risk_paths(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path in (API, f"{API}/{{risk_id}}"):
        assert path in paths, f"missing {path}"
