"""API tests: RF-04 treatment integration on the RF-01 risk register.

The register embeds each risk's active treatment summary (null = untreated,
never a fabricated status) and gains treatment-backed filters without
changing RF-01's default ordering or RF-03's score integration (plan §42,
Ruling 13: history lives on the nested treatments endpoint, so a completed
treatment never matches a register filter).
"""

API = "/api/v1/risks"
CATEGORY_API = "/api/v1/risk-categories"
TREATMENTS_API = "/api/v1/treatments"
FRAMEWORK_API = "/api/v1/risk-scoring/frameworks"


def _error(response) -> dict:
    return response.json()["error"]


def _category_id(client) -> str:
    categories = client.get(CATEGORY_API, params={"search": "Security"}).json()
    return categories["items"][0]["id"]


def _risk(client, title: str) -> str:
    response = client.post(API, json={"title": title, "category_id": _category_id(client)})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _treatment(client, risk_id: str, **overrides) -> dict:
    payload = {
        "title": "Encrypt training data at rest",
        "strategy": "mitigate",
        "owner": "Security Team",
    }
    payload.update(overrides)
    response = client.post(f"{API}/{risk_id}/treatments", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _to_in_progress(client, treatment_id: str) -> None:
    planned = client.patch(f"{TREATMENTS_API}/{treatment_id}", json={"status": "planned"})
    assert planned.status_code == 200, planned.text
    started = client.patch(f"{TREATMENTS_API}/{treatment_id}", json={"status": "in_progress"})
    assert started.status_code == 200, started.text


def _complete_treatment(client, treatment_id: str) -> None:
    _to_in_progress(client, treatment_id)
    response = client.post(f"{TREATMENTS_API}/{treatment_id}/complete")
    assert response.status_code == 200, response.text


def _list(client, **params) -> list[dict]:
    response = client.get(API, params=params)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _find(items: list[dict], risk_id: str) -> dict:
    return next(item for item in items if item["id"] == risk_id)


def _framework_id(client) -> str:
    items = client.get(FRAMEWORK_API, params={"page_size": 100}).json()["items"]
    return next(item["id"] for item in items if item["name"] == "Standard AI Risk Matrix")


def _score(client, risk_id: str, likelihood: int, impact: int) -> None:
    response = client.post(
        f"{API}/{risk_id}/scores",
        json={"framework_id": _framework_id(client), "likelihood": likelihood, "impact": impact},
    )
    assert response.status_code == 201, response.text


# --- treatment summary embedding ----------------------------------------------


def test_risk_list_treatment_null_when_untreated(client):
    risk_id = _risk(client, "Untreated risk")

    item = _find(_list(client), risk_id)

    # untreated means null, never a fabricated status (plan §42)
    assert item["treatment"] is None


def test_risk_list_treatment_after_creation(client):
    risk_id = _risk(client, "Treated risk")
    _treatment(client, risk_id, target_completion_date="2027-01-15")

    treatment = _find(_list(client), risk_id)["treatment"]

    assert treatment["strategy"] == "mitigate"
    assert treatment["status"] == "not_started"
    assert treatment["owner"] == "Security Team"
    assert treatment["target_completion_date"] == "2027-01-15"
    assert treatment["is_overdue"] is False


def test_risk_detail_includes_treatment(client):
    risk_id = _risk(client, "Detailed treated risk")
    _treatment(client, risk_id)

    response = client.get(f"{API}/{risk_id}")

    assert response.status_code == 200
    treatment = response.json()["treatment"]
    assert treatment["strategy"] == "mitigate"
    assert treatment["status"] == "not_started"
    assert treatment["is_overdue"] is False


# --- treatment filters ---------------------------------------------------------


def test_treated_true_and_false_filters(client):
    treated = _risk(client, "Has a treatment")
    untreated = _risk(client, "No treatment")
    _treatment(client, treated)

    assert [item["id"] for item in _list(client, treated="true")] == [treated]
    assert [item["id"] for item in _list(client, treated="false")] == [untreated]


def test_filter_by_treatment_strategy_and_status_and_owner(client):
    mitigated = _risk(client, "Mitigated risk")
    _treatment(client, mitigated, strategy="mitigate", owner="Security Team")
    accepted = _risk(client, "Accepted risk")
    _treatment(
        client, accepted, strategy="accept", rationale="Board decision", owner="Risk Committee"
    )

    assert [item["id"] for item in _list(client, treatment_strategy="accept")] == [accepted]
    assert [item["id"] for item in _list(client, treatment_owner="Security Team")] == [mitigated]
    # both treatments are still active (not_started)
    assert {item["id"] for item in _list(client, treatment_status="not_started")} == {
        mitigated,
        accepted,
    }


def test_filter_treatment_overdue(client):
    overdue = _risk(client, "Slipping treatment")
    _treatment(client, overdue, target_completion_date="2026-01-01")
    finished_late = _risk(client, "Finished late")
    late = _treatment(client, finished_late, target_completion_date="2026-01-01")
    _complete_treatment(client, late["id"])

    # only the still-active past-target treatment matches (Ruling 9)
    assert [item["id"] for item in _list(client, treatment_overdue="true")] == [overdue]
    # the completed one joined no active row: it reads as untreated, not
    # overdue — history is not a live state
    not_overdue = {item["id"] for item in _list(client, treatment_overdue="false")}
    assert overdue not in not_overdue
    assert finished_late in not_overdue


def test_treatment_status_completed_returns_empty_on_register(client):
    risk_id = _risk(client, "Finished treatment risk")
    treatment = _treatment(client, risk_id)
    _complete_treatment(client, treatment["id"])

    # Ruling 13: the register only joins ACTIVE treatments — a completed
    # one is history, never a register filter result
    assert _list(client, treatment_status="completed") == []

    # history lives on the nested endpoint under the risk instead
    nested = client.get(f"{API}/{risk_id}/treatments", params={"status": "completed"})
    assert nested.status_code == 200
    assert nested.json()["total"] == 1
    assert nested.json()["items"][0]["status"] == "completed"


# --- regressions against RF-01/RF-03 -------------------------------------------


def test_default_sort_and_scoring_unchanged(client):
    """The treatment join must not disturb RF-01 ordering or RF-03 scores."""
    first = _risk(client, "Created first")
    second = _risk(client, "Created second")
    third = _risk(client, "Created third")
    _score(client, first, likelihood=1, impact=1)  # 2 → low
    _score(client, second, likelihood=4, impact=5)  # 20 → critical
    _score(client, third, likelihood=2, impact=3)  # 6 → medium
    # touch the first risk so it becomes the most recently updated
    assert client.patch(f"{API}/{first}", json={"owner": "priya"}).status_code == 200

    default_ids = [item["id"] for item in _list(client)]
    assert default_ids[0] == first
    assert default_ids.index(third) < default_ids.index(second)

    by_severity = [item["id"] for item in _list(client, sort_by="severity")]
    assert by_severity.index(second) < by_severity.index(third) < by_severity.index(first)

    for item in _list(client):
        assert item["current_score"] is not None
        assert "treatment" in item


def test_unknown_treatment_filter_rejected(client):
    response = client.get(API, params={"treatment_state": "active"})

    assert response.status_code == 400
    assert _error(response)["code"] == "RISK_QUERY_INVALID"


def test_risk_mutations_untreated_by_treatment_join(client):
    """create → patch → archive still work with an active treatment joined."""
    risk_id = _risk(client, "Lifecycle with treatment")
    _treatment(client, risk_id)

    patched = client.patch(f"{API}/{risk_id}", json={"title": "Renamed with treatment"})
    assert patched.status_code == 200
    assert patched.json()["title"] == "Renamed with treatment"
    assert patched.json()["treatment"]["status"] == "not_started"

    assert client.delete(f"{API}/{risk_id}").status_code == 204
    assert risk_id not in [item["id"] for item in _list(client)]
    archived = _find(_list(client, archived="true"), risk_id)
    assert archived["treatment"]["status"] == "not_started"


def test_register_no_fanout_with_history(client):
    risk_id = _risk(client, "Risk with treatment history")
    first = _treatment(client, risk_id, title="First attempt")
    _complete_treatment(client, first["id"])
    second = _treatment(client, risk_id, title="Second attempt")
    assert second["status"] == "not_started"

    items = _list(client)
    rows = [item for item in items if item["id"] == risk_id]

    # completed history + one active row still yields exactly one row,
    # summarizing the ACTIVE treatment (partial unique index, Ruling 5)
    assert len(items) == 1
    assert len(rows) == 1
    assert rows[0]["treatment"]["status"] == "not_started"
    assert rows[0]["treatment"]["is_overdue"] is False
