"""API tests: RF-03 scoring integration on the RF-01 risk register.

The register embeds each risk's current score (null = unscored, never
fabricated as low) and gains score-backed filters and sorts without
changing RF-01's default ordering.
"""

API = "/api/v1/risks"
CATEGORY_API = "/api/v1/risk-categories"
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


def _framework_id(client) -> str:
    items = client.get(FRAMEWORK_API, params={"page_size": 100}).json()["items"]
    return next(item["id"] for item in items if item["name"] == "Standard AI Risk Matrix")


def _score(client, risk_id: str, likelihood: int, impact: int, **overrides) -> dict:
    payload = {
        "framework_id": _framework_id(client),
        "likelihood": likelihood,
        "impact": impact,
    }
    payload.update(overrides)
    response = client.post(f"{API}/{risk_id}/scores", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _list(client, **params) -> list[dict]:
    response = client.get(API, params=params)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _find(items: list[dict], risk_id: str) -> dict:
    return next(item for item in items if item["id"] == risk_id)


# --- current_score embedding -------------------------------------------------


def test_risk_list_includes_null_current_score_when_unscored(client):
    risk_id = _risk(client, "Unscored risk")
    item = _find(_list(client), risk_id)
    assert item["current_score"] is None  # unscored ≠ low (plan §28)


def test_risk_list_current_score_after_scoring(client):
    risk_id = _risk(client, "Scored risk")
    _score(client, risk_id, likelihood=4, impact=5)

    item = _find(_list(client), risk_id)
    score = item["current_score"]
    assert score is not None
    assert score["likelihood"] == 4
    assert score["impact"] == 5
    assert score["calculated_score"] == 20
    assert score["effective_score"] == 20
    assert score["severity"] == "critical"
    assert score["priority"] == 1
    assert score["is_override"] is False
    assert score["scored_at"] is not None


def test_risk_detail_includes_current_score(client):
    risk_id = _risk(client, "Detailed risk")
    _score(client, risk_id, likelihood=1, impact=2)

    response = client.get(f"{API}/{risk_id}")
    assert response.status_code == 200
    score = response.json()["current_score"]
    assert score["severity"] == "low"
    assert score["effective_score"] == 2


# --- scoring filters ---------------------------------------------------------


def test_filter_scored_true_and_false(client):
    scored = _risk(client, "Has a score")
    unscored = _risk(client, "No score yet")
    _score(client, scored, likelihood=3, impact=4)

    only_scored = _list(client, scored="true")
    assert [item["id"] for item in only_scored] == [scored]

    only_unscored = _list(client, scored="false")
    assert [item["id"] for item in only_unscored] == [unscored]


def test_filter_by_severity(client):
    critical = _risk(client, "Critical risk")
    low = _risk(client, "Low risk")
    _score(client, critical, likelihood=5, impact=5)
    _score(client, low, likelihood=1, impact=1)

    items = _list(client, severity="critical")
    assert [item["id"] for item in items] == [critical]


def test_filter_by_priority(client):
    critical = _risk(client, "Priority one")
    medium = _risk(client, "Priority three")
    _score(client, critical, likelihood=5, impact=5)  # 25 → critical, priority 1
    _score(client, medium, likelihood=2, impact=3)  # 6 → medium, priority 3

    items = _list(client, priority="1")
    assert [item["id"] for item in items] == [critical]


def test_filter_by_likelihood_impact_and_framework_id(client):
    target = _risk(client, "Grid position 4x5")
    _risk(client, "Unscored neighbour")
    _score(client, target, likelihood=4, impact=5)

    assert [item["id"] for item in _list(client, likelihood="4")] == [target]
    assert [item["id"] for item in _list(client, impact="5")] == [target]
    assert [item["id"] for item in _list(client, framework_id=_framework_id(client))] == [target]


# --- scoring sorts -----------------------------------------------------------


def test_sort_by_score_desc_puts_highest_first_and_unscored_last(client):
    highest = _risk(client, "Twenty")
    middle = _risk(client, "Twelve")
    lowest = _risk(client, "Six")
    unscored = _risk(client, "Nothing")
    _score(client, highest, likelihood=4, impact=5)  # 20
    _score(client, middle, likelihood=3, impact=4)  # 12
    _score(client, lowest, likelihood=2, impact=3)  # 6

    items = _list(client, sort_by="score", sort_order="desc")
    ids = [item["id"] for item in items]
    assert ids.index(highest) < ids.index(middle) < ids.index(lowest) < ids.index(unscored)


def test_sort_by_severity_puts_critical_first(client):
    critical = _risk(client, "Critical")
    high = _risk(client, "High")
    medium = _risk(client, "Medium")
    _score(client, critical, likelihood=5, impact=5)  # 25 → critical
    _score(client, high, likelihood=4, impact=4)  # 16 → high
    _score(client, medium, likelihood=2, impact=3)  # 6 → medium

    items = _list(client, sort_by="severity")
    ids = [item["id"] for item in items]
    assert ids.index(critical) < ids.index(high) < ids.index(medium)


def test_sort_by_scored_at_and_likelihood(client):
    first = _risk(client, "Scored first")
    second = _risk(client, "Scored second")
    unscored = _risk(client, "Never scored")
    _score(client, first, likelihood=2, impact=3)
    _score(client, second, likelihood=5, impact=1)

    by_scored_at = _list(client, sort_by="scored_at", sort_order="asc")
    scored_ids = [item["id"] for item in by_scored_at if item["current_score"]]
    assert scored_ids == [first, second]
    assert by_scored_at[-1]["id"] == unscored  # nulls last in either direction

    by_likelihood = _list(client, sort_by="likelihood", sort_order="desc")
    scored_ids = [item["id"] for item in by_likelihood if item["current_score"]]
    assert scored_ids == [second, first]
    assert by_likelihood[-1]["id"] == unscored


def test_default_sort_unchanged(client):
    """No sort params → RF-01's updated_at desc ordering still applies."""
    first = _risk(client, "Created first")
    second = _risk(client, "Created second")
    # touch the first risk so it becomes the most recently updated
    assert client.patch(f"{API}/{first}", json={"owner": "priya"}).status_code == 200

    items = _list(client)
    ids = [item["id"] for item in items]
    assert ids.index(first) < ids.index(second)


def test_unknown_scoring_filter_rejected(client):
    response = client.get(API, params={"nonsense": "1"})
    assert response.status_code == 400
    assert _error(response)["code"] == "RISK_QUERY_INVALID"


def test_invalid_scoring_sort_rejected(client):
    response = client.get(API, params={"sort_by": "calculated_score"})
    assert response.status_code == 400
    assert _error(response)["code"] == "RISK_QUERY_INVALID"


# --- archived interplay ------------------------------------------------------


def test_archived_risk_hidden_by_default_with_score(client):
    risk_id = _risk(client, "Retired but scored")
    _score(client, risk_id, likelihood=4, impact=5)
    assert client.delete(f"{API}/{risk_id}").status_code == 204

    default_ids = [item["id"] for item in _list(client)]
    assert risk_id not in default_ids

    archived_ids = [item["id"] for item in _list(client, archived="true")]
    assert risk_id in archived_ids

    # and an archived risk still rejects scoring with 409
    fresh = _risk(client, "Archived before scoring")
    assert client.delete(f"{API}/{fresh}").status_code == 204
    response = client.post(
        f"{API}/{fresh}/scores",
        json={"framework_id": _framework_id(client), "likelihood": 4, "impact": 5},
    )
    assert response.status_code == 409
    assert _error(response)["code"] == "RISK_ARCHIVED"


def test_scoring_flow_does_not_break_rf01_mutations(client):
    """The score join must not disturb create → patch → archive (RF-01)."""
    risk_id = _risk(client, "Lifecycle target")
    _score(client, risk_id, likelihood=3, impact=4)

    patched = client.patch(f"{API}/{risk_id}", json={"title": "Renamed target"})
    assert patched.status_code == 200
    assert patched.json()["title"] == "Renamed target"
    assert patched.json()["current_score"]["calculated_score"] == 12

    assert client.delete(f"{API}/{risk_id}").status_code == 204
    archived = _list(client, archived="true")
    item = _find(archived, risk_id)
    assert item["current_score"]["severity"] == "high"
