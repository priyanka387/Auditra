"""End-to-end acceptance scenario for AI Model Inventory → 5. Model Usage.

Walks the full path from spec §55: register model → version → deployment →
application/agent association → real LangChain invocation → usage event in
PostgreSQL → query events/stats → LangGraph invocation → second event →
idempotent replay → statistics stay correct.
"""

from datetime import UTC, datetime, timedelta
from typing import TypedDict

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph

from app.core.config import settings
from app.modules.model_usage.integrations.langchain import AuditraUsageCallback
from app.modules.model_usage.integrations.langgraph import langgraph_usage_config
from app.modules.model_usage.service import ModelUsageService
from app.modules.model_usage.telemetry import ServiceUsageCollector

API = "/api/v1"
USAGE = f"{API}/model-usage"
WINDOW_FROM = (datetime.now(UTC) - timedelta(days=1)).isoformat()
WINDOW_TO = (datetime.now(UTC) + timedelta(days=1)).isoformat()


class GraphState(TypedDict):
    messages: list


def test_model_usage_end_to_end_acceptance(client, db):
    # 1. Register a model through the Model Registration API.
    model = client.post(
        f"{API}/models",
        json={
            "provider_slug": "openai",
            "model_type_slug": "llm",
            "name": "GPT-4o Mini",
            "native_model_id": "gpt-4o-mini",
        },
    )
    assert model.status_code == 201, model.text
    model_id = model.json()["id"]

    # 2. Create a version through Model Versioning.
    version = client.post(
        f"{API}/models/{model_id}/versions",
        json={
            "identity_type": "release",
            "version_label": "2026-09",
            "native_version_id": "gpt-4o-mini-2026-09",
        },
    )
    assert version.status_code == 201, version.text
    version_id = version.json()["id"]

    # 3. Create a deployment through Model Deployment.
    deployment = client.post(
        f"{API}/model-versions/{version_id}/deployments",
        json={
            "name": "support-prod",
            "environment": "production",
            "deployment_kind": "online_inference",
            "target_type": "kubernetes",
            "target_name": "prod-cluster",
            "source": "manual",
        },
    )
    assert deployment.status_code == 201, deployment.text
    deployment_id = deployment.json()["id"]

    # 4. Create application + agent and associate the model.
    application = client.post(
        f"{API}/applications",
        json={"name": "Support App", "slug": "support-app"},
    )
    assert application.status_code == 201, application.text
    application_id = application.json()["id"]

    agent = client.post(
        f"{API}/applications/{application_id}/agents",
        json={"name": "Support Agent", "slug": "support-agent", "framework": "langgraph"},
    )
    assert agent.status_code == 201, agent.text
    agent_id = agent.json()["id"]

    association = client.post(
        f"{API}/agents/{agent_id}/models",
        json={"model_id": model_id, "role": "PRIMARY"},
    )
    assert association.status_code == 201, association.text

    # 5-7. Real LangChain invocation with inventory IDs in config metadata.
    collector = ServiceUsageCollector(ModelUsageService(db, settings.default_tenant_id))
    langchain_model = FakeListChatModel(responses=["I can help with that."])
    langchain_result = langchain_model.invoke(
        [HumanMessage("hello")],
        config={
            "callbacks": [AuditraUsageCallback(collector)],
            "metadata": {
                "auditra_model_id": model_id,
                "auditra_model_version_id": version_id,
                "auditra_deployment_id": deployment_id,
                "auditra_application_id": application_id,
                "auditra_agent_id": agent_id,
                "auditra_operation": "support_agent",
                "auditra_environment": "production",
                "auditra_trace_id": "trace-e2e-1",
            },
            "tags": ["auditra", "e2e"],
        },
    )
    assert langchain_result.content == "I can help with that."

    # 9. The event is queryable through GET /model-usage/events.
    events = client.get(f"{USAGE}/events", params={"model_id": model_id})
    assert events.status_code == 200
    listing = events.json()
    assert listing["total"] == 1
    first = listing["items"][0]
    assert first["model_id"] == model_id
    assert first["model_version_id"] == version_id
    assert first["deployment_id"] == deployment_id
    assert first["application_id"] == application_id
    assert first["agent_id"] == agent_id
    assert first["status"] == "success"
    assert first["source"] == "langchain"
    assert first["framework"] == "langchain"
    assert first["operation_name"] == "support_agent"
    assert first["environment"] == "production"
    assert first["trace_id"] == "trace-e2e-1"

    # 10. Statistics include the request.
    stats = client.get(
        f"{USAGE}/stats",
        params={
            "model_id": model_id,
            "started_from": WINDOW_FROM,
            "started_to": WINDOW_TO,
        },
    )
    assert stats.status_code == 200
    assert stats.json()["totals"]["requests"] == 1
    assert stats.json()["totals"]["successful_requests"] == 1

    # 11-12. A LangGraph agent using the same model persists a second event.
    graph = StateGraph(GraphState)

    def support_node(state, config):
        response = langchain_model.invoke(state["messages"], config=config)
        return {"messages": [response]}

    graph.add_node("support", support_node)
    graph.add_edge(START, "support")
    graph.add_edge("support", END)
    compiled = graph.compile()

    graph_result = compiled.invoke(
        {"messages": [HumanMessage("follow up")]},
        config=langgraph_usage_config(
            collector,
            model_id=model_id,
            model_version_id=version_id,
            deployment_id=deployment_id,
            application_id=application_id,
            agent_id=agent_id,
            operation="support_agent",
            environment="production",
            trace_id="trace-e2e-2",
        ),
    )
    assert graph_result["messages"][-1].content == "I can help with that."

    # 13. Statistics show two requests with the expected totals.
    stats = client.get(
        f"{USAGE}/stats",
        params={
            "model_id": model_id,
            "started_from": WINDOW_FROM,
            "started_to": WINDOW_TO,
            "granularity": "day",
        },
    )
    assert stats.status_code == 200
    totals = stats.json()["totals"]
    assert totals["requests"] == 2
    assert totals["successful_requests"] == 2
    assert totals["failed_requests"] == 0
    assert totals["error_rate"] == 0
    assert totals["min_latency_ms"] is not None
    assert len(stats.json()["buckets"]) >= 1

    # 14-15. Repeat delivery of the same event_id does not inflate statistics.
    stored = client.get(f"{USAGE}/events/{first['event_id']}").json()
    replay_payload = {
        key: value for key, value in stored.items() if key not in ("id", "created_at")
    }
    replay = client.post(f"{USAGE}/events", json=replay_payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == stored["id"]

    stats = client.get(
        f"{USAGE}/stats",
        params={
            "model_id": model_id,
            "started_from": WINDOW_FROM,
            "started_to": WINDOW_TO,
        },
    )
    assert stats.json()["totals"]["requests"] == 2
    assert client.get(f"{USAGE}/events", params={"model_id": model_id}).json()["total"] == 2

    # Bonus: conflicting payload with the same event_id is rejected with 409.
    conflict = dict(replay_payload)
    conflict["input_tokens"] = 42
    conflict_response = client.post(f"{USAGE}/events", json=conflict)
    assert conflict_response.status_code == 409
    assert conflict_response.json()["error"]["code"] == "EVENT_ID_CONFLICT"

    # Events remain immutable: no update or delete endpoints exist.
    assert client.delete(f"{USAGE}/events/{first['event_id']}").status_code == 405
    assert client.patch(f"{USAGE}/events/{first['event_id']}", json={}).status_code == 405
