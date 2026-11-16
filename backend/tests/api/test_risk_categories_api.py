"""API tests: /api/v1/risk-categories (RF-01 risk taxonomy endpoints)."""

from uuid import uuid4

API = "/api/v1/risk-categories"

# RF-01 §27: the 21 system categories the idempotent seed provisions
SYSTEM_TAXONOMY_SIZE = 21


def _error(response):
    return response.json()["error"]


def test_list_categories_includes_seeded_system_taxonomy(client):
    body = client.get(API).json()
    assert body["total"] == SYSTEM_TAXONOMY_SIZE
    assert all(item["is_system"] for item in body["items"])
    assert all(item["is_active"] for item in body["items"])
    slugs = {item["slug"] for item in body["items"]}
    assert {"security", "privacy", "fairness-bias", "model-risk"} <= slugs


def test_list_categories_search_and_pagination(client):
    body = client.get(API, params={"search": "fairness"}).json()
    assert body["total"] == 1
    assert body["items"][0]["slug"] == "fairness-bias"

    page1 = client.get(API, params={"page": 1, "page_size": 5}).json()
    assert page1["total"] == SYSTEM_TAXONOMY_SIZE
    assert page1["total_pages"] == 5
    assert len(page1["items"]) == 5


def test_list_categories_invalid_sort_returns_400(client):
    response = client.get(API, params={"sort_by": "nope"})
    assert response.status_code == 400
    assert _error(response)["code"] == "RISK_CATEGORY_QUERY_INVALID"


def test_create_custom_category_returns_201(client):
    response = client.post(API, json={"name": "Model Governance", "description": "Internal"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "Model Governance"
    assert body["slug"] == "model-governance"
    assert body["is_system"] is False
    assert body["is_active"] is True

    fetched = client.get(f"{API}/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["slug"] == "model-governance"


def test_create_duplicate_category_returns_409(client):
    created = client.post(API, json={"name": "Vendor Watch"})
    assert created.status_code == 201, created.text

    duplicate = client.post(API, json={"name": "Vendor Watch"})
    assert duplicate.status_code == 409
    assert _error(duplicate)["code"] == "RISK_CATEGORY_DUPLICATE"


def test_create_blank_name_returns_422(client):
    response = client.post(API, json={"name": "   "})
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_patch_category_disables_it(client):
    created = client.post(API, json={"name": "Retiring Soon"}).json()

    response = client.patch(f"{API}/{created['id']}", json={"is_active": False})
    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is False
    # slug is immutable
    assert response.json()["slug"] == "retiring-soon"

    inactive = client.get(API, params={"is_active": "false"}).json()
    assert [item["id"] for item in inactive["items"]] == [created["id"]]


def test_patch_unknown_category_returns_404(client):
    response = client.patch(f"{API}/{uuid4()}", json={"name": "nope"})
    assert response.status_code == 404
    assert _error(response)["code"] == "RISK_CATEGORY_NOT_FOUND"


def test_get_unknown_category_returns_404(client):
    response = client.get(f"{API}/{uuid4()}")
    assert response.status_code == 404
    assert _error(response)["code"] == "RISK_CATEGORY_NOT_FOUND"
