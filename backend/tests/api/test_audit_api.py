from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.core.config import settings
from app.modules.audit.service import AuditService

API = "/api/v1"


def _seed(
    db,
    *,
    event_type="model.created",
    resource_type="model",
    resource_id=None,
    before_state=None,
    after_state=None,
    changed_fields=None,
    metadata=None,
    actor="user-99",
    actor_type=None,
    request_id="req-seed-01",
    source="api",
):
    service = AuditService(
        db,
        settings.default_tenant_id,
        request_id=request_id,
        actor=actor,
        actor_type=actor_type,
        source=source,
    )
    event = service.record_event(
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id or str(uuid4()),
        before_state=before_state,
        after_state=after_state,
        changed_fields=changed_fields,
        metadata=metadata,
    )
    db.commit()
    return event


def _error(response):
    return response.json()["error"]


def test_list_and_get_event(client, db):
    seeded = _seed(db, resource_id="model-1", after_state={"name": "Support LLM"})

    listed = client.get(f"{API}/audit/events")
    assert listed.status_code == 200
    page = listed.json()
    assert page["total"] == 1
    item = page["items"][0]
    assert item["id"] == str(seeded.id)
    assert item["sequence_no"] == seeded.sequence_no
    assert item["event_type"] == "model.created"
    assert item["resource_type"] == "model"
    assert item["resource_id"] == "model-1"
    assert item["actor"] == {"type": "user", "id": "user-99"}
    assert item["source"] == "api"
    assert item["schema_version"] == 1
    assert item["request_id"] == "req-seed-01"
    assert item["after_state"] == {"name": "Support LLM"}
    assert item["before_state"] is None

    fetched = client.get(f"{API}/audit/events/{seeded.id}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == str(seeded.id)

    missing = client.get(f"{API}/audit/events/{uuid4()}")
    assert missing.status_code == 404
    assert _error(missing)["code"] == "AUDIT_EVENT_NOT_FOUND"


def test_filters(client, db):
    other_resource = str(uuid4())
    _seed(db, resource_id="model-1", event_type="model.created")
    _seed(db, resource_id="model-1", event_type="model.updated", changed_fields=["owner_name"])
    _seed(
        db,
        resource_type="deployment",
        resource_id=other_resource,
        event_type="deployment.created",
        actor="service-deployer",
        actor_type="service",
        source="sdk",
    )

    body = client.get(
        f"{API}/audit/events", params={"resource_type": "model", "resource_id": "model-1"}
    ).json()
    assert body["total"] == 2
    assert {i["event_type"] for i in body["items"]} == {"model.created", "model.updated"}

    body = client.get(f"{API}/audit/events", params={"event_type": "deployment.created"}).json()
    assert body["total"] == 1
    assert body["items"][0]["resource_id"] == other_resource

    body = client.get(
        f"{API}/audit/events", params={"actor_type": "service", "actor_id": "service-deployer"}
    ).json()
    assert body["total"] == 1

    body = client.get(f"{API}/audit/events", params={"source": "sdk"}).json()
    assert body["total"] == 1
    assert body["items"][0]["source"] == "sdk"

    body = client.get(
        f"{API}/audit/events",
        params={
            "occurred_from": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "occurred_to": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    ).json()
    assert body["total"] == 3

    body = client.get(
        f"{API}/audit/events",
        params={"occurred_from": (datetime.now(UTC) + timedelta(days=1)).isoformat()},
    ).json()
    assert body["total"] == 0


def test_request_id_filter(client, db):
    _seed(db, request_id="req-abc-123")
    _seed(db, request_id="req-other")

    body = client.get(f"{API}/audit/events", params={"request_id": "req-abc-123"}).json()
    assert body["total"] == 1
    assert body["items"][0]["request_id"] == "req-abc-123"


def test_pagination_deterministic(client, db):
    ids = [str(_seed(db, resource_id=f"m-{i}").id) for i in range(5)]

    page1 = client.get(f"{API}/audit/events", params={"page": 1, "page_size": 2}).json()
    page2 = client.get(f"{API}/audit/events", params={"page": 2, "page_size": 2}).json()
    page3 = client.get(f"{API}/audit/events", params={"page": 3, "page_size": 2}).json()

    assert page1["total"] == 5
    assert page1["total_pages"] == 3
    seen = [i["id"] for page in (page1, page2, page3) for i in page["items"]]
    assert seen == list(reversed(ids))
    assert len(set(seen)) == 5
    sequences = [i["sequence_no"] for page in (page1, page2, page3) for i in page["items"]]
    assert sequences == sorted(sequences, reverse=True)


def test_invalid_filter_range_400(client, db):
    inverted = client.get(
        f"{API}/audit/events",
        params={
            "occurred_from": "2026-10-07T00:00:00Z",
            "occurred_to": "2026-10-01T00:00:00Z",
        },
    )
    assert inverted.status_code == 400
    assert _error(inverted)["code"] == "INVALID_AUDIT_FILTER"

    bad_type = client.get(f"{API}/audit/events", params={"event_type": "MODEL.CREATED"})
    assert bad_type.status_code == 400
    assert _error(bad_type)["code"] == "INVALID_AUDIT_FILTER"


def test_page_size_bounds(client, db):
    assert client.get(f"{API}/audit/events", params={"page_size": 0}).status_code == 422
    assert client.get(f"{API}/audit/events", params={"page_size": 201}).status_code == 422
    assert client.get(f"{API}/audit/events", params={"page": 0}).status_code == 422


def test_audit_mutation_methods_not_allowed(client, db):
    event_id = _seed(db).id
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)(f"{API}/audit/events").status_code == 405
        assert getattr(client, method)(f"{API}/audit/events/{event_id}").status_code == 405
