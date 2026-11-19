"""API tests: RF-03 risk-scoring frameworks, scores and overview endpoints."""

from uuid import uuid4

from app.main import app
from app.modules.risk_scoring.schemas import ScoringFrameworkCreate
from app.modules.risk_scoring.service import ScoringFrameworkService

FRAMEWORK_API = "/api/v1/risk-scoring/frameworks"
OVERVIEW_API = "/api/v1/risk-scoring/overview"
RISK_API = "/api/v1/risks"
CATEGORY_API = "/api/v1/risk-categories"
SEEDED_NAME = "Standard AI Risk Matrix"


def _error(response) -> dict:
    return response.json()["error"]


def _risk(client, title: str = "Scoring target model") -> str:
    """A tenant-visible risk created through RF-01's API."""
    categories = client.get(CATEGORY_API, params={"search": "Security"}).json()
    category_id = categories["items"][0]["id"]
    response = client.post(RISK_API, json={"title": title, "category_id": category_id})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _framework_id(client) -> str:
    """The seeded Standard AI Risk Matrix (the client fixture seeds it)."""
    items = client.get(FRAMEWORK_API, params={"page_size": 100}).json()["items"]
    return next(item["id"] for item in items if item["name"] == SEEDED_NAME)


def _scores_api(risk_id: str) -> str:
    return f"{RISK_API}/{risk_id}/scores"


def _custom_payload(**overrides) -> dict:
    payload = {
        "name": "Custom 2x2",
        "likelihood_levels": [
            {"value": 1, "name": "Unlikely"},
            {"value": 2, "name": "Likely"},
        ],
        "impact_levels": [
            {"value": 1, "name": "Minor"},
            {"value": 2, "name": "Major"},
        ],
        "severity_thresholds": [
            {"severity": "low", "minimum_score": 1, "maximum_score": 2, "priority": 2},
            {"severity": "high", "minimum_score": 3, "maximum_score": 4, "priority": 1},
        ],
    }
    payload.update(overrides)
    return payload


def _five_by_five_payload(**overrides) -> dict:
    levels = [{"value": value, "name": f"L{value}"} for value in range(1, 6)]
    payload = {
        "name": "Big 5x5",
        "likelihood_levels": levels,
        "impact_levels": levels,
        "severity_thresholds": [
            {"severity": "low", "minimum_score": 1, "maximum_score": 4, "priority": 4},
            {"severity": "medium", "minimum_score": 5, "maximum_score": 9, "priority": 3},
            {"severity": "high", "minimum_score": 10, "maximum_score": 16, "priority": 2},
            {"severity": "critical", "minimum_score": 17, "maximum_score": 25, "priority": 1},
        ],
    }
    payload.update(overrides)
    return payload


def _score(client, risk_id: str, **overrides) -> dict:
    payload = {"framework_id": _framework_id(client), "likelihood": 4, "impact": 5}
    payload.update(overrides)
    response = client.post(_scores_api(risk_id), json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# --- frameworks --------------------------------------------------------------


def test_list_frameworks_includes_seeded_standard_matrix(client):
    response = client.get(FRAMEWORK_API)
    assert response.status_code == 200
    items = response.json()["items"]
    seeded = next(item for item in items if item["name"] == SEEDED_NAME)
    assert seeded["is_system"] is True
    assert seeded["version"] == 1
    assert seeded["method"] == "likelihood_impact"
    assert seeded["is_active"] is True


def test_get_framework_detail_returns_levels_and_thresholds(client):
    detail = client.get(f"{FRAMEWORK_API}/{_framework_id(client)}")
    assert detail.status_code == 200
    body = detail.json()
    assert len(body["likelihood_levels"]) == 5
    assert [level["value"] for level in body["likelihood_levels"]] == [1, 2, 3, 4, 5]
    assert len(body["impact_levels"]) == 5
    assert len(body["severity_thresholds"]) == 4
    assert [t["minimum_score"] for t in body["severity_thresholds"]] == [1, 5, 10, 17]
    assert [t["severity"] for t in body["severity_thresholds"]] == [
        "low",
        "medium",
        "high",
        "critical",
    ]


def test_create_custom_framework_returns_201_with_detail(client):
    response = client.post(FRAMEWORK_API, json=_custom_payload())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "Custom 2x2"
    assert body["version"] == 1
    assert body["is_system"] is False
    assert len(body["likelihood_levels"]) == 2
    assert len(body["impact_levels"]) == 2
    assert len(body["severity_thresholds"]) == 2


def test_create_duplicate_framework_returns_409_envelope(client):
    assert client.post(FRAMEWORK_API, json=_custom_payload()).status_code == 201
    duplicate = client.post(FRAMEWORK_API, json=_custom_payload())
    assert duplicate.status_code == 409
    assert _error(duplicate)["code"] == "SCORING_FRAMEWORK_DUPLICATE"


def test_create_framework_overlapping_thresholds_returns_400(client):
    payload = _five_by_five_payload(
        severity_thresholds=[
            {"severity": "low", "minimum_score": 1, "maximum_score": 6, "priority": 4},
            {"severity": "medium", "minimum_score": 5, "maximum_score": 9, "priority": 3},
            {"severity": "high", "minimum_score": 10, "maximum_score": 16, "priority": 2},
            {"severity": "critical", "minimum_score": 17, "maximum_score": 25, "priority": 1},
        ]
    )
    response = client.post(FRAMEWORK_API, json=payload)
    assert response.status_code == 400
    assert _error(response)["code"] == "SCORING_FRAMEWORK_INVALID"


def test_patch_framework_updates_name(client):
    framework_id = _framework_id(client)
    response = client.patch(f"{FRAMEWORK_API}/{framework_id}", json={"name": "Renamed Matrix"})
    assert response.status_code == 200
    assert response.json()["name"] == "Renamed Matrix"
    assert response.json()["version"] == 1  # PATCH never bumps version


def test_patch_framework_rejects_version_field(client):
    response = client.patch(f"{FRAMEWORK_API}/{_framework_id(client)}", json={"version": 2})
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_get_unknown_framework_returns_404_envelope(client):
    response = client.get(f"{FRAMEWORK_API}/{uuid4()}")
    assert response.status_code == 404
    assert _error(response)["code"] == "SCORING_FRAMEWORK_NOT_FOUND"


def test_list_frameworks_unknown_filter_rejected(client):
    response = client.get(FRAMEWORK_API, params={"nonsense": "1"})
    assert response.status_code == 400
    assert _error(response)["code"] == "SCORING_FRAMEWORK_QUERY_INVALID"


def test_list_frameworks_invalid_sort_returns_400(client):
    response = client.get(FRAMEWORK_API, params={"sort_by": "version"})
    assert response.status_code == 400
    assert _error(response)["code"] == "SCORING_FRAMEWORK_QUERY_INVALID"


def test_cross_tenant_framework_get_returns_404(client, db):
    """A framework that exists for another tenant is a 404, not a leak."""
    other_tenant = uuid4()
    created = ScoringFrameworkService(db, other_tenant).create(
        ScoringFrameworkCreate(**_custom_payload(name="Other Tenant Matrix"))
    )
    db.commit()

    response = client.get(f"{FRAMEWORK_API}/{created.id}")
    assert response.status_code == 404
    assert _error(response)["code"] == "SCORING_FRAMEWORK_NOT_FOUND"


# --- scores ------------------------------------------------------------------


def test_create_score_returns_201_with_calculated_result(client):
    risk_id = _risk(client)
    response = client.post(
        _scores_api(risk_id),
        json={"framework_id": _framework_id(client), "likelihood": 4, "impact": 5},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["calculated_score"] == 20
    assert body["effective_score"] == 20
    assert body["severity"] == "critical"
    assert body["priority"] == 1
    assert body["is_current"] is True
    assert body["is_override"] is False
    assert body["likelihood_label"] == "Likely"
    assert body["impact_label"] == "Severe"
    assert body["framework"]["name"] == SEEDED_NAME
    assert body["framework_version"] == 1


def test_create_score_unknown_risk_404_envelope(client):
    response = client.post(
        _scores_api(uuid4()),
        json={"framework_id": _framework_id(client), "likelihood": 4, "impact": 5},
    )
    assert response.status_code == 404
    assert _error(response)["code"] == "RISK_NOT_FOUND"


def test_create_score_unknown_framework_404(client):
    risk_id = _risk(client)
    response = client.post(
        _scores_api(risk_id),
        json={"framework_id": str(uuid4()), "likelihood": 4, "impact": 5},
    )
    assert response.status_code == 404
    assert _error(response)["code"] == "SCORING_FRAMEWORK_NOT_FOUND"


def test_create_score_inactive_framework_409(client):
    risk_id = _risk(client)
    created = client.post(FRAMEWORK_API, json=_custom_payload()).json()
    assert (
        client.patch(f"{FRAMEWORK_API}/{created['id']}", json={"is_active": False}).status_code
        == 200
    )
    response = client.post(
        _scores_api(risk_id),
        json={"framework_id": created["id"], "likelihood": 1, "impact": 1},
    )
    assert response.status_code == 409
    assert _error(response)["code"] == "SCORING_FRAMEWORK_INACTIVE"


def test_create_score_invalid_likelihood_returns_400(client):
    risk_id = _risk(client)
    response = client.post(
        _scores_api(risk_id),
        json={"framework_id": _framework_id(client), "likelihood": 6, "impact": 5},
    )
    assert response.status_code == 400
    assert _error(response)["code"] == "RISK_SCORE_INVALID"


def test_create_score_validation_error_on_extra_field(client):
    risk_id = _risk(client)
    response = client.post(
        _scores_api(risk_id),
        json={
            "framework_id": _framework_id(client),
            "likelihood": 4,
            "impact": 5,
            "calculated_score": 20,
        },
    )
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_get_current_score_returns_latest(client):
    risk_id = _risk(client)
    _score(client, risk_id, likelihood=4, impact=5)
    second = _score(client, risk_id, likelihood=3, impact=4)

    response = client.get(f"{_scores_api(risk_id)}/current")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == second["id"]
    assert body["calculated_score"] == 12
    assert body["severity"] == "high"


def test_get_current_score_unscored_404(client):
    risk_id = _risk(client)
    response = client.get(f"{_scores_api(risk_id)}/current")
    assert response.status_code == 404
    assert _error(response)["code"] == "RISK_SCORE_NOT_FOUND"


def test_score_history_lists_newest_first_with_framework_snapshot(client):
    risk_id = _risk(client)
    first = _score(client, risk_id, likelihood=1, impact=1)
    second = _score(client, risk_id, likelihood=2, impact=2)
    third = _score(client, risk_id, likelihood=5, impact=5)

    response = client.get(_scores_api(risk_id))
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert [item["id"] for item in body["items"]] == [third["id"], second["id"], first["id"]]
    assert all(item["framework"]["name"] == SEEDED_NAME for item in body["items"])
    assert all(item["framework_version"] == 1 for item in body["items"])
    # only the newest row is current
    assert [item["is_current"] for item in body["items"]] == [True, False, False]


def test_recalculate_creates_new_record(client):
    risk_id = _risk(client)
    _score(client, risk_id, likelihood=4, impact=5)

    response = client.post(f"{_scores_api(risk_id)}/recalculate")
    assert response.status_code == 200, response.text
    assert response.json()["is_current"] is True
    assert response.json()["calculated_score"] == 20

    history = client.get(_scores_api(risk_id)).json()
    assert history["total"] == 2
    assert [item["is_current"] for item in history["items"]] == [True, False]


def test_override_without_reason_returns_422(client):
    risk_id = _risk(client)
    _score(client, risk_id, likelihood=3, impact=4)
    response = client.post(f"{_scores_api(risk_id)}/override", json={"override_score": 16})
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_override_with_reason_preserves_calculated_score(client):
    risk_id = _risk(client)
    _score(client, risk_id, likelihood=3, impact=4)

    response = client.post(
        f"{_scores_api(risk_id)}/override",
        json={"override_score": 16, "reason": " Business context "},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["calculated_score"] == 12  # engine result preserved
    assert body["effective_score"] == 16
    assert body["is_override"] is True
    assert body["override_score"] == 16
    assert body["override_reason"] == "Business context"
    assert body["severity"] == "high"
    assert body["priority"] == 2


def test_override_out_of_range_returns_400(client):
    risk_id = _risk(client)
    _score(client, risk_id, likelihood=3, impact=4)
    response = client.post(
        f"{_scores_api(risk_id)}/override",
        json={"override_score": 26, "reason": "wishful thinking"},
    )
    assert response.status_code == 400
    assert _error(response)["code"] == "RISK_SCORE_INVALID"


def test_score_archived_risk_returns_409(client):
    risk_id = _risk(client)
    assert client.delete(f"{RISK_API}/{risk_id}").status_code == 204
    response = client.post(
        _scores_api(risk_id),
        json={"framework_id": _framework_id(client), "likelihood": 4, "impact": 5},
    )
    assert response.status_code == 409
    assert _error(response)["code"] == "RISK_ARCHIVED"


# --- overview ----------------------------------------------------------------


def test_overview_returns_distribution_cells_and_top_risks(client):
    scored = _risk(client, title="Scored risk")
    unscored = _risk(client, title="Unscored risk")
    _score(client, scored, likelihood=2, impact=3)  # 6 → medium, priority 3

    response = client.get(OVERVIEW_API)
    assert response.status_code == 200
    body = response.json()
    assert body["framework"]["name"] == SEEDED_NAME
    assert body["distribution"] == {
        "critical": 0,
        "high": 0,
        "medium": 1,
        "low": 0,
        "unscored": 1,
    }
    assert len(body["cells"]) == 25
    cell = next(c for c in body["cells"] if c["likelihood"] == 2 and c["impact"] == 3)
    assert cell["count"] == 1
    assert scored in cell["risk_ids"]
    assert [top["title"] for top in body["top_risks"]] == ["Scored risk"]
    assert unscored not in [top["risk_id"] for top in body["top_risks"]]


def test_openapi_lists_risk_scoring_paths():
    paths = app.openapi()["paths"]
    assert {
        "/api/v1/risk-scoring/frameworks",
        "/api/v1/risk-scoring/frameworks/{framework_id}",
        "/api/v1/risk-scoring/overview",
        "/api/v1/risks/{risk_id}/scores",
        "/api/v1/risks/{risk_id}/scores/current",
        "/api/v1/risks/{risk_id}/scores/recalculate",
        "/api/v1/risks/{risk_id}/scores/override",
    } <= set(paths)
