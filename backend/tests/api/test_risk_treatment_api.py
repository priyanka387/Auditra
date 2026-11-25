"""API tests: RF-04 treatment, action and residual REST endpoints.

Covers the three routers (nested under risks, treatments, treatment-
actions): the 201/400/404/409/422 envelopes, the overview route winning
the ``/{treatment_id}`` UUID parser, derived ``is_overdue``/``action_stats``
on responses, and the OpenAPI surface (plan §29/Ruling 12: 15 operations).
"""

from uuid import UUID, uuid4

from app.main import app
from app.modules.risk_treatment.models import RiskTreatmentAction

API = "/api/v1/risks"
CATEGORY_API = "/api/v1/risk-categories"
TREATMENTS_API = "/api/v1/treatments"
ACTIONS_API = "/api/v1/treatment-actions"


def _error(response) -> dict:
    return response.json()["error"]


def _category_id(client) -> str:
    categories = client.get(CATEGORY_API, params={"search": "Security"}).json()
    return categories["items"][0]["id"]


def _risk(client, title: str = "Training data unencrypted") -> str:
    response = client.post(API, json={"title": title, "category_id": _category_id(client)})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _treatment_payload(**overrides) -> dict:
    payload = {
        "title": "Encrypt training data at rest",
        "strategy": "mitigate",
        "owner": "Security Team",
        "planned_start_date": "2026-11-01",
        "target_completion_date": "2027-01-15",
    }
    payload.update(overrides)
    return payload


def _treatment(client, risk_id: str, **overrides) -> dict:
    response = client.post(f"{API}/{risk_id}/treatments", json=_treatment_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def _action(client, treatment_id: str, **overrides) -> dict:
    payload = {"title": "Restrict production access", "owner": "Security Team"}
    payload.update(overrides)
    response = client.post(f"{TREATMENTS_API}/{treatment_id}/actions", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _to_in_progress(client, treatment_id: str) -> None:
    assert (
        client.patch(f"{TREATMENTS_API}/{treatment_id}", json={"status": "planned"}).status_code
        == 200
    )
    assert (
        client.patch(f"{TREATMENTS_API}/{treatment_id}", json={"status": "in_progress"}).status_code
        == 200
    )


# --- treatment create ---------------------------------------------------------


def test_create_treatment_returns_201(client):
    risk_id = _risk(client)

    response = client.post(f"{API}/{risk_id}/treatments", json=_treatment_payload())
    assert response.status_code == 201, response.text

    body = response.json()
    assert body["risk_id"] == risk_id
    assert body["strategy"] == "mitigate"
    assert body["status"] == "not_started"
    assert body["owner"] == "Security Team"
    assert body["planned_start_date"] == "2026-11-01"
    assert body["target_completion_date"] == "2027-01-15"
    assert body["is_overdue"] is False
    assert body["action_stats"] is None


def test_create_treatment_unknown_risk_404_envelope(client):
    response = client.post(f"{API}/{uuid4()}/treatments", json=_treatment_payload())

    assert response.status_code == 404
    assert _error(response)["code"] == "RISK_NOT_FOUND"


def test_create_treatment_duplicate_active_409(client):
    risk_id = _risk(client)
    _treatment(client, risk_id)

    response = client.post(f"{API}/{risk_id}/treatments", json=_treatment_payload())

    assert response.status_code == 409
    assert _error(response)["code"] == "RISK_TREATMENT_ACTIVE_EXISTS"


def test_create_accept_without_rationale_returns_400(client):
    risk_id = _risk(client)

    response = client.post(
        f"{API}/{risk_id}/treatments", json=_treatment_payload(strategy="accept", rationale=None)
    )

    assert response.status_code == 400
    assert _error(response)["code"] == "RISK_TREATMENT_ERROR"


def test_create_treatment_inverted_dates_422(client):
    risk_id = _risk(client)

    response = client.post(
        f"{API}/{risk_id}/treatments",
        json=_treatment_payload(
            planned_start_date="2027-01-15", target_completion_date="2026-11-01"
        ),
    )

    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_create_treatment_validation_error_on_extra_field(client):
    risk_id = _risk(client)

    response = client.post(
        f"{API}/{risk_id}/treatments", json=_treatment_payload(completed_at="2026-11-20T00:00:00Z")
    )

    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


# --- treatment read -----------------------------------------------------------


def test_list_treatments_nested_under_risk(client):
    first_risk = _risk(client, title="First risk")
    second_risk = _risk(client, title="Second risk")
    mine = _treatment(client, first_risk)
    _treatment(client, second_risk, title="Other treatment")

    response = client.get(f"{API}/{first_risk}/treatments")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [mine["id"]]

    unknown = client.get(f"{API}/{first_risk}/treatments", params={"nonsense": "1"})
    assert unknown.status_code == 400
    assert _error(unknown)["code"] == "RISK_TREATMENT_QUERY_INVALID"


def test_get_treatment_detail_includes_progress(client):
    risk_id = _risk(client)
    treatment = _treatment(client, risk_id)
    first = _action(client, treatment["id"], title="Step one")
    _action(client, treatment["id"], title="Step two")
    _action(client, treatment["id"], title="Step three")
    client.post(f"{ACTIONS_API}/{first['id']}/complete")

    response = client.get(f"{TREATMENTS_API}/{treatment['id']}")

    assert response.status_code == 200
    body = response.json()
    # 3 actions, 1 completed → 33% with 2 remaining (plan §18/§56)
    assert body["action_stats"]["total_actions"] == 3
    assert body["action_stats"]["completed_actions"] == 1
    assert body["action_stats"]["remaining_actions"] == 2
    assert body["action_stats"]["progress_percent"] == 33
    assert body["is_overdue"] is False


def test_treatment_404_envelope(client):
    response = client.get(f"{TREATMENTS_API}/{uuid4()}")

    assert response.status_code == 404
    assert _error(response)["code"] == "RISK_TREATMENT_NOT_FOUND"


# --- treatment update ---------------------------------------------------------


def test_patch_treatment_updates_owner(client):
    risk_id = _risk(client)
    treatment = _treatment(client, risk_id)

    response = client.patch(f"{TREATMENTS_API}/{treatment['id']}", json={"owner": "AppSec"})

    assert response.status_code == 200
    assert response.json()["owner"] == "AppSec"


def test_patch_treatment_invalid_transition_409(client):
    risk_id = _risk(client)
    treatment = _treatment(client, risk_id)

    # not_started cannot jump to in_progress: planned comes first (§55)
    response = client.patch(f"{TREATMENTS_API}/{treatment['id']}", json={"status": "in_progress"})

    assert response.status_code == 409
    assert _error(response)["code"] == "RISK_TREATMENT_STATUS_INVALID"


def test_patch_treatment_rejects_completed_at_422(client):
    risk_id = _risk(client)
    treatment = _treatment(client, risk_id)

    response = client.patch(
        f"{TREATMENTS_API}/{treatment['id']}",
        json={"completed_at": "2026-11-20T00:00:00Z"},
    )

    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


# --- treatment terminal endpoints ---------------------------------------------


def test_complete_treatment_flow(client):
    risk_id = _risk(client)
    treatment = _treatment(client, risk_id)
    _to_in_progress(client, treatment["id"])

    response = client.post(f"{TREATMENTS_API}/{treatment['id']}/complete")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["completed_at"] is not None

    again = client.post(f"{TREATMENTS_API}/{treatment['id']}/complete")
    assert again.status_code == 409
    assert _error(again)["code"] == "RISK_TREATMENT_STATUS_INVALID"


def test_cancel_treatment_requires_reason_422(client):
    risk_id = _risk(client)
    treatment = _treatment(client, risk_id)

    response = client.post(f"{TREATMENTS_API}/{treatment['id']}/cancel", json={})

    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_cancel_treatment_stores_reason(client):
    risk_id = _risk(client)
    treatment = _treatment(client, risk_id)

    response = client.post(
        f"{TREATMENTS_API}/{treatment['id']}/cancel",
        json={"reason": "Superseded by avoidance"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["cancellation_reason"] == "Superseded by avoidance"
    assert body["cancelled_at"] is not None


# --- actions ------------------------------------------------------------------


def test_create_action_returns_201_and_lists(client):
    risk_id = _risk(client)
    treatment = _treatment(client, risk_id)

    missing_owner = client.post(
        f"{TREATMENTS_API}/{treatment['id']}/actions", json={"title": "No owner"}
    )
    assert missing_owner.status_code == 422
    assert _error(missing_owner)["code"] == "VALIDATION_ERROR"

    created = _action(client, treatment["id"])
    assert created["status"] == "todo"
    assert created["priority"] == "medium"
    assert created["owner"] == "Security Team"
    assert created["is_overdue"] is False

    listed = client.get(f"{TREATMENTS_API}/{treatment['id']}/actions")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [created["id"]]


def test_action_complete_and_cancel_endpoints(client):
    risk_id = _risk(client)
    treatment = _treatment(client, risk_id)
    to_finish = _action(client, treatment["id"], title="Finish me")
    to_cancel = _action(client, treatment["id"], title="Drop me")

    completed = client.post(f"{ACTIONS_API}/{to_finish['id']}/complete")
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["completed_at"] is not None

    cancelled = client.post(
        f"{ACTIONS_API}/{to_cancel['id']}/cancel", json={"reason": "Out of scope"}
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["cancellation_reason"] == "Out of scope"

    # a cancelled action is terminal: completing it is 409
    late = client.post(f"{ACTIONS_API}/{to_cancel['id']}/complete")
    assert late.status_code == 409
    assert _error(late)["code"] == "RISK_TREATMENT_ACTION_STATUS_INVALID"


def test_action_unknown_404_and_cross_tenant_404(client, db):
    unknown = client.get(f"{ACTIONS_API}/{uuid4()}")
    assert unknown.status_code == 404
    assert _error(unknown)["code"] == "RISK_TREATMENT_ACTION_NOT_FOUND"

    # the same row becomes invisible once it belongs to another tenant
    risk_id = _risk(client)
    treatment = _treatment(client, risk_id)
    action = _action(client, treatment["id"])
    db.rollback()
    row = db.get(RiskTreatmentAction, UUID(action["id"]))
    row.tenant_id = uuid4()
    db.commit()

    hidden = client.get(f"{ACTIONS_API}/{action['id']}")
    assert hidden.status_code == 404
    assert _error(hidden)["code"] == "RISK_TREATMENT_ACTION_NOT_FOUND"


# --- residual assessments -----------------------------------------------------


def test_residual_create_returns_201_with_engine_result(client):
    risk_id = _risk(client)
    treatment = _treatment(client, risk_id)

    response = client.post(
        f"{TREATMENTS_API}/{treatment['id']}/residual-assessments",
        json={"likelihood": 2, "impact": 4},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["likelihood"] == 2
    assert body["impact"] == 4
    assert body["calculated_score"] == 8
    assert body["severity"] == "medium"
    assert body["assessment_type"] == "expected"


def test_residual_verified_returns_400(client):
    risk_id = _risk(client)
    treatment = _treatment(client, risk_id)

    response = client.post(
        f"{TREATMENTS_API}/{treatment['id']}/residual-assessments",
        json={"likelihood": 2, "impact": 4, "assessment_type": "verified"},
    )

    assert response.status_code == 400
    assert _error(response)["code"] == "RISK_TREATMENT_RESIDUAL_INVALID"


def test_residual_list_newest_first(client):
    risk_id = _risk(client)
    treatment = _treatment(client, risk_id)
    first = client.post(
        f"{TREATMENTS_API}/{treatment['id']}/residual-assessments",
        json={"likelihood": 1, "impact": 1},
    ).json()
    second = client.post(
        f"{TREATMENTS_API}/{treatment['id']}/residual-assessments",
        json={"likelihood": 5, "impact": 5},
    ).json()

    response = client.get(f"{TREATMENTS_API}/{treatment['id']}/residual-assessments")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert [item["id"] for item in body["items"]] == [second["id"], first["id"]]


# --- overview -----------------------------------------------------------------


def test_overview_returns_distribution_and_untreated(client):
    response = client.get(f"{TREATMENTS_API}/overview")

    assert response.status_code == 200
    body = response.json()
    assert set(body["status_distribution"]) == {
        "not_started",
        "planned",
        "in_progress",
        "completed",
        "cancelled",
    }
    assert set(body["strategy_distribution"]) == {"mitigate", "accept", "avoid", "transfer"}
    assert body["overdue_actions"] == 0
    assert body["overdue_treatments"] == 0
    assert body["untreated_critical"] == 0
    assert body["untreated_high"] == 0
    assert body["risks_with_treatment"] + body["risks_without_treatment"] == body["total_risks"]


def test_overview_route_not_shadowed_by_treatment_id(client):
    # "overview" is declared before /{treatment_id} — it must never be
    # parsed as a UUID and rejected with 422
    response = client.get(f"{TREATMENTS_API}/overview")

    assert response.status_code == 200


# --- OpenAPI surface ----------------------------------------------------------

RF04_PATHS = {
    "/api/v1/risks/{risk_id}/treatments",
    "/api/v1/treatments/overview",
    "/api/v1/treatments/{treatment_id}",
    "/api/v1/treatments/{treatment_id}/complete",
    "/api/v1/treatments/{treatment_id}/cancel",
    "/api/v1/treatments/{treatment_id}/actions",
    "/api/v1/treatments/{treatment_id}/residual-assessments",
    "/api/v1/treatment-actions/{action_id}",
    "/api/v1/treatment-actions/{action_id}/complete",
    "/api/v1/treatment-actions/{action_id}/cancel",
}


def test_openapi_lists_risk_treatment_paths(client):
    paths = app.openapi()["paths"]

    assert RF04_PATHS <= set(paths)
    assert {"get", "post"} <= set(paths["/api/v1/risks/{risk_id}/treatments"])
    assert {"get", "patch"} <= set(paths["/api/v1/treatments/{treatment_id}"])
    assert {"post"} <= set(paths["/api/v1/treatments/{treatment_id}/complete"])
    assert {"post"} <= set(paths["/api/v1/treatments/{treatment_id}/cancel"])
    assert {"get", "post"} <= set(paths["/api/v1/treatments/{treatment_id}/actions"])
    assert {"get", "post"} <= set(paths["/api/v1/treatments/{treatment_id}/residual-assessments"])
    assert {"get", "patch"} <= set(paths["/api/v1/treatment-actions/{action_id}"])
    assert {"post"} <= set(paths["/api/v1/treatment-actions/{action_id}/complete"])
    assert {"post"} <= set(paths["/api/v1/treatment-actions/{action_id}/cancel"])
