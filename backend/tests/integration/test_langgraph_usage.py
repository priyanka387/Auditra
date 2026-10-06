from operator import add
from typing import Annotated, TypedDict

import pytest
import sqlalchemy as sa
from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph

from app.core.config import settings
from app.modules.model_inventory.schemas import ApplicationCreate
from app.modules.model_inventory.services import ApplicationService
from app.modules.model_usage.enums import UsageSource, UsageStatus
from app.modules.model_usage.integrations.langchain import AuditraUsageCallback
from app.modules.model_usage.integrations.langgraph import langgraph_usage_config
from app.modules.model_usage.models import ModelUsageEvent
from app.modules.model_usage.schemas import UsageStatsQuery
from app.modules.model_usage.service import ModelUsageService
from app.modules.model_usage.telemetry import ServiceUsageCollector
from app.seed import seed_reference_data


class GraphState(TypedDict):
    messages: Annotated[list, add]


def _collector(db):
    return ServiceUsageCollector(ModelUsageService(db, settings.default_tenant_id))


def _graph(model):
    graph = StateGraph(GraphState)

    def chat_node(state, config):
        response = model.invoke(state["messages"], config=config)
        return {"messages": [response]}

    graph.add_node("chat", chat_node)
    graph.add_edge(START, "chat")
    graph.add_edge("chat", END)
    return graph.compile()


def _events(db):
    return list(db.scalars(sa.select(ModelUsageEvent)).all())


def test_langgraph_invocation_emits_usage_event(db, parent_model):
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    seed_reference_data(db)
    db.commit()
    application = ApplicationService(db, settings.default_tenant_id).create_application(
        ApplicationCreate(name="Graph App", slug="graph-app")
    )

    collector = _collector(db)
    model = FakeListChatModel(responses=["graph says hi"])
    graph = _graph(model)

    config = langgraph_usage_config(
        collector,
        model_id=parent_model.id,
        application_id=application.id,
        operation="research_agent",
        environment="staging",
    )
    result = graph.invoke(
        {"messages": [HumanMessage("hello")]},
        config=config,
    )

    assert result["messages"][-1].content == "graph says hi"
    events = _events(db)
    assert len(events) == 1
    event = events[0]
    assert event.status == UsageStatus.SUCCESS.value
    assert event.source == UsageSource.LANGGRAPH.value
    assert event.framework == "langgraph"
    assert event.model_id == parent_model.id
    assert event.application_id == application.id
    assert event.operation_name == "research_agent"
    assert event.environment == "staging"
    assert event.parent_run_id is not None


def test_langgraph_multiple_model_calls_emit_multiple_events(db, parent_model):
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    collector = _collector(db)
    model = FakeListChatModel(responses=["first", "second"])

    graph = StateGraph(GraphState)

    def node_a(state, config):
        return {"messages": [model.invoke(state["messages"], config=config)]}

    def node_b(state, config):
        return {"messages": [model.invoke(state["messages"], config=config)]}

    graph.add_node("a", node_a)
    graph.add_node("b", node_b)
    graph.add_edge(START, "a")
    graph.add_edge("a", "b")
    graph.add_edge("b", END)
    compiled = graph.compile()

    config = langgraph_usage_config(collector, model_id=parent_model.id, operation="two_step")
    compiled.invoke({"messages": [HumanMessage("go")]}, config=config)

    events = _events(db)
    assert len(events) == 2
    assert all(event.model_id == parent_model.id for event in events)
    assert all(event.operation_name == "two_step" for event in events)

    stats = ModelUsageService(db, settings.default_tenant_id).get_stats(
        UsageStatsQuery(
            model_id=parent_model.id,
            started_from=event_started(events),
            started_to=event_ended(events),
        )
    )
    assert stats.totals.requests == 2


def event_started(events):
    from datetime import timedelta

    return min(e.started_at for e in events) - timedelta(days=1)


def event_ended(events):
    from datetime import timedelta

    return max(e.started_at for e in events) + timedelta(days=1)


def test_langgraph_model_error_emits_error_event(db, parent_model):
    from langchain_core.language_models.chat_models import BaseChatModel

    class _FailingModel(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "failing"

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            raise TimeoutError("graph provider timeout")

    collector = _collector(db)
    graph = _graph(_FailingModel())
    config = langgraph_usage_config(collector, model_id=parent_model.id, operation="failing_agent")

    with pytest.raises(TimeoutError, match="graph provider timeout"):
        graph.invoke({"messages": [HumanMessage("go")]}, config=config)

    events = _events(db)
    assert len(events) == 1
    assert events[0].status == UsageStatus.ERROR.value
    assert events[0].error_type == "TimeoutError"


def test_langgraph_config_carries_inventory_metadata():
    class _RecordingCollector:
        mode = "best_effort"

        def __init__(self):
            self.events = []

        def emit(self, event):
            self.events.append(event)

    from uuid import uuid4

    collector = _RecordingCollector()
    model_id = uuid4()
    config = langgraph_usage_config(
        collector,
        model_id=model_id,
        operation="support",
        environment="production",
        trace_id="trace-9",
    )
    assert config["metadata"]["auditra_model_id"] == str(model_id)
    assert config["metadata"]["auditra_operation"] == "support"
    assert config["metadata"]["auditra_environment"] == "production"
    assert config["metadata"]["auditra_trace_id"] == "trace-9"
    assert len(config["callbacks"]) == 1
    assert isinstance(config["callbacks"][0], AuditraUsageCallback)
    assert config["callbacks"][0].source == UsageSource.LANGGRAPH
