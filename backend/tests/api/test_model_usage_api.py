from datetime import UTC, datetime, timedelta
from uuid import uuid4

API = "/api/v1/model-usage"

STARTED = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)


def _model(client):
    response = client.post(
        "/api/v1/models",
        json={
            "provider_slug": "openai",
            "model_type_slug": "llm",
            "name": "Usage Model",
            "native_model_id": "usage-model-1",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _payload(model_id, **overrides):
    payload = {
        "event_id": f"evt-{uuid4()}",
        "model_id": model_id,
        "source": "api",
        "started_at": STARTED.isoformat(),
        "completed_at": (STARTED + timedelta(milliseconds=900)).isoformat(),
        "input_tokens": 100,
        "output_tokens": 50,
        "environment": "production",
        "operation_name": "support_agent",
    }
    payload.update(overrides)
    return payload


def _error(response):
    return response.json()["error"]


def test_create_event_created_then_duplicate_then_conflict(client):
    model_id = _model(client)
    payload = _payload(model_id)

    created = client.post(f"{API}/events", json=payload)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["event_id"] == payload["event_id"]
    assert body["total_tokens"] == 150
    assert body["duration_ms"] == 900
    assert body["created_at"] is not None

    duplicate = client.post(f"{API}/events", json=payload)
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["id"] == body["id"]

    conflicting = client.post(
        f"{API}/events", json=_payload(model_id, event_id=payload["event_id"], input_tokens=999)
    )
    assert conflicting.status_code == 409
    assert _error(conflicting)["code"] == "EVENT_ID_CONFLICT"


def test_create_event_rejects_unknown_model(client):
    response = client.post(f"{API}/events", json=_payload(str(uuid4())))
    assert response.status_code == 404
    assert _error(response)["code"] == "MODEL_NOT_FOUND"


def test_create_event_validation_errors(client):
    model_id = _model(client)

    naive = client.post(f"{API}/events", json=_payload(model_id, started_at="2026-10-05T10:00:00"))
    assert naive.status_code == 422

    negative = client.post(f"{API}/events", json=_payload(model_id, input_tokens=-1))
    assert negative.status_code == 422

    bad_status = client.post(
        f"{API}/events", json=_payload(model_id, status="fine")
    )
    assert bad_status.status_code == 422

    success_with_error = client.post(
        f"{API}/events", json=_payload(model_id, error_type="TimeoutError")
    )
    assert success_with_error.status_code == 422

    extra_field = client.post(
        f"{API}/events", json=_payload(model_id, prompt="secret prompt")
    )
    assert extra_field.status_code == 422


def test_batch_ingestion_partial_success_and_idempotent_replay(client):
    model_id = _model(client)
    valid_a = _payload(model_id)
    valid_b = _payload(model_id, status="error", error_type="TimeoutError")
    invalid = _payload(str(uuid4()))

    response = client.post(f"{API}/events/batch", json={"events": [valid_a, valid_b, invalid]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accepted"] == 2
    assert body["rejected"] == 1
    assert body["duplicates"] == 0
    assert body["results"][2]["status"] == "rejected"
    assert "not found" in body["results"][2]["error"]

    replay = client.post(f"{API}/events/batch", json={"events": [valid_a]})
    assert replay.status_code == 200
    assert replay.json()["duplicates"] == 1
    assert replay.json()["results"][0]["status"] == "duplicate"

    empty = client.post(f"{API}/events/batch", json={"events": []})
    assert empty.status_code == 422


def test_batch_size_limit(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "usage_batch_size", 2)
    model_id = _model(client)
    events = [_payload(model_id) for _ in range(3)]
    response = client.post(f"{API}/events/batch", json={"events": events})
    assert response.status_code == 422
    assert _error(response)["code"] == "USAGE_BATCH_TOO_LARGE"


def test_get_event_by_external_id_and_uuid(client):
    model_id = _model(client)
    payload = _payload(model_id)
    created = client.post(f"{API}/events", json=payload).json()

    by_external = client.get(f"{API}/events/{payload['event_id']}")
    assert by_external.status_code == 200
    assert by_external.json()["id"] == created["id"]

    by_uuid = client.get(f"{API}/events/{created['id']}")
    assert by_uuid.status_code == 200
    assert by_uuid.json()["event_id"] == payload["event_id"]

    missing = client.get(f"{API}/events/{uuid4()}")
    assert missing.status_code == 404
    assert _error(missing)["code"] == "MODEL_USAGE_EVENT_NOT_FOUND"


def test_list_events_filters_and_pagination(client):
    model_id = _model(client)
    first = _payload(model_id, started_at=STARTED.isoformat())
    second_started = STARTED + timedelta(minutes=5)
    second = _payload(
        model_id,
        started_at=second_started.isoformat(),
        completed_at=(second_started + timedelta(milliseconds=900)).isoformat(),
        status="error",
        error_type="TimeoutError",
    )
    posted_first = client.post(f"{API}/events", json=first)
    posted_second = client.post(f"{API}/events", json=second)
    assert posted_first.status_code == 201, posted_first.text
    assert posted_second.status_code == 201, posted_second.text

    all_events = client.get(f"{API}/events")
    assert all_events.status_code == 200
    body = all_events.json()
    assert body["total"] == 2
    assert body["items"][0]["event_id"] == second["event_id"]

    successes = client.get(f"{API}/events", params={"status": "success"})
    assert successes.json()["total"] == 1
    assert successes.json()["items"][0]["event_id"] == first["event_id"]

    by_model = client.get(f"{API}/events", params={"model_id": str(uuid4())})
    assert by_model.json()["total"] == 0

    page2 = client.get(f"{API}/events", params={"page": 2, "page_size": 1})
    assert page2.status_code == 200
    assert len(page2.json()["items"]) == 1
    assert page2.json()["total_pages"] == 2

    bad_size = client.get(f"{API}/events", params={"page_size": 1000})
    assert bad_size.status_code == 422


def test_stats_endpoint(client):
    model_id = _model(client)
    client.post(
        f"{API}/events",
        json=_payload(model_id, input_tokens=100, output_tokens=50),
    )
    client.post(
        f"{API}/events",
        json=_payload(
            model_id,
            status="error",
            error_type="TimeoutError",
            input_tokens=None,
            output_tokens=None,
            completed_at=None,
            duration_ms=2000,
        ),
    )

    params = {
        "model_id": model_id,
        "started_from": "2026-10-01T00:00:00Z",
        "started_to": "2026-10-06T00:00:00Z",
        "granularity": "day",
    }
    response = client.get(f"{API}/stats", params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    assert datetime.fromisoformat(body["period"]["from"]) == datetime(2026, 10, 1, tzinfo=UTC)
    assert body["filters"] == {"model_id": model_id}
    assert body["totals"]["requests"] == 2
    assert body["totals"]["successful_requests"] == 1
    assert body["totals"]["failed_requests"] == 1
    assert body["totals"]["error_rate"] == 0.5
    assert body["totals"]["total_tokens"] == 150
    assert body["totals"]["min_latency_ms"] == 900
    assert len(body["buckets"]) == 1

    unbounded = client.get(
        f"{API}/stats",
        params={"model_id": model_id},
    )
    assert unbounded.status_code == 422

    inverted = client.get(
        f"{API}/stats",
        params={
            "started_from": "2026-10-06T00:00:00Z",
            "started_to": "2026-10-01T00:00:00Z",
        },
    )
    assert inverted.status_code == 400
    assert _error(inverted)["code"] == "INVALID_USAGE_QUERY"


def test_usage_events_are_immutable(client):
    model_id = _model(client)
    created = client.post(f"{API}/events", json=_payload(model_id)).json()

    assert client.patch(f"{API}/events/{created['event_id']}", json={}).status_code == 405
    assert client.delete(f"{API}/events/{created['event_id']}").status_code == 405
    assert client.put(f"{API}/events/{created['event_id']}", json={}).status_code == 405


def test_privacy_no_prompt_content_stored(client):
    model_id = _model(client)
    payload = _payload(
        model_id,
        metadata={"retrieved_docs": ["doc-1"]},
    )
    created = client.post(f"{API}/events", json=payload).json()
    assert "prompt" not in created
    assert "completion" not in created
    stored = client.get(f"{API}/events/{created['event_id']}").json()
    assert stored["metadata"] == {"retrieved_docs": ["doc-1"]}
