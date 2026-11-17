"""API tests: /api/v1/assessment-templates (RF-02 template catalog)."""

from uuid import uuid4

API = "/api/v1/assessment-templates"

SECTION_TITLES = [
    "System Purpose & Context",
    "Data & Privacy",
    "Stakeholder Impact",
    "Human Oversight",
    "Safety & Reliability",
    "Security & Robustness",
    "Governance & Accountability",
]


def _error(response):
    return response.json()["error"]


def test_list_templates_includes_seeded_general_template(client):
    body = client.get(API).json()
    seeded = next(item for item in body["items"] if item["key"] == "general-ai-risk-assessment")
    assert seeded["name"] == "General AI Risk Assessment"
    assert seeded["is_system"] is True
    assert seeded["version"] == 1
    assert seeded["is_active"] is True
    assert body["total"] >= 1


def test_get_template_detail_returns_sections_with_questions(client):
    listed = client.get(API, params={"search": "General"}).json()
    template_id = next(
        item["id"] for item in listed["items"] if item["key"] == "general-ai-risk-assessment"
    )

    body = client.get(f"{API}/{template_id}").json()
    assert body["key"] == "general-ai-risk-assessment"
    assert [section["title"] for section in body["sections"]] == SECTION_TITLES
    assert [section["display_order"] for section in body["sections"]] == list(range(1, 8))
    questions = [question for section in body["sections"] for question in section["questions"]]
    assert len(questions) == 14
    purpose = next(question for question in questions if question["key"] == "purpose")
    assert purpose["question_type"] == "text"
    assert purpose["required"] is True
    assert purpose["section_id"] is not None


def test_create_custom_template_returns_201(client):
    response = client.post(API, json={"name": "  Clinical Trial Assessment  "})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "Clinical Trial Assessment"
    assert body["key"] == "clinical-trial-assessment"
    assert body["is_system"] is False
    assert body["version"] == 1
    assert body["assessment_type"] == "initial"


def test_create_duplicate_template_key_returns_409(client):
    first = client.post(API, json={"name": "Vendor Risk Review"})
    assert first.status_code == 201

    second = client.post(API, json={"name": "Vendor Risk Review"})
    assert second.status_code == 409
    assert _error(second)["code"] == "ASSESSMENT_TEMPLATE_DUPLICATE"


def test_patch_template_updates_name(client):
    created = client.post(API, json={"name": "Patch Me"}).json()

    response = client.patch(f"{API}/{created['id']}", json={"name": "Patched Name"})
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Patched Name"
    assert response.json()["key"] == created["key"]
    assert response.json()["version"] == created["version"]


def test_patch_template_unknown_returns_404(client):
    response = client.patch(f"{API}/{uuid4()}", json={"name": "Nope"})
    assert response.status_code == 404
    assert _error(response)["code"] == "ASSESSMENT_TEMPLATE_NOT_FOUND"


def test_patch_template_rejects_version_field(client):
    created = client.post(API, json={"name": "Version Guard"}).json()
    response = client.patch(f"{API}/{created['id']}", json={"version": 2})
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"
