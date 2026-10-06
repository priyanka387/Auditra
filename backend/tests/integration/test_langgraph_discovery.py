import json
from operator import add
from typing import Annotated, TypedDict

import sqlalchemy as sa
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph

from app.core.config import settings
from app.modules.model_discovery.integrations.langgraph import langgraph_discovery_config
from app.modules.model_discovery.models import ModelDiscovery
from app.modules.model_discovery.service import DiscoveryService, ServiceDiscoverySink


class GraphState(TypedDict):
    messages: Annotated[list, add]


def _sink(db):
    return ServiceDiscoverySink(DiscoveryService(db, settings.default_tenant_id), strict=True)


def _graph(model):
    graph = StateGraph(GraphState)

    def call_model(state, config):
        return {"messages": [model.invoke(state["messages"], config=config)]}

    graph.add_node("call_model", call_model)
    graph.add_edge(START, "call_model")
    graph.add_edge("call_model", END)
    return graph.compile()


def _rows(db):
    return list(db.scalars(sa.select(ModelDiscovery)).all())


def _config(db, source_identifier="support-graph", environment="test"):
    config = langgraph_discovery_config(
        _sink(db), source_identifier=source_identifier, environment=environment
    )
    config["metadata"].update({"auditra_provider": "openai", "auditra_model_identifier": "gpt-5.x"})
    return config


def _model(messages):
    return GenericFakeChatModel(messages=iter(messages))


def test_langgraph_run_persists_observation_with_node_context(db, parent_model):
    graph = _graph(_model([AIMessage(content="hi")]))

    graph.invoke({"messages": [HumanMessage("hello")]}, config=_config(db))

    rows = _rows(db)
    assert len(rows) == 1
    row = rows[0]
    assert row.source_type == "langgraph"
    assert row.source_identifier == "support-graph"
    assert row.canonical_identity == "openai|gpt-5.x"
    assert row.metadata_["framework"] == "langgraph"
    assert row.metadata_["langgraph_node"] == "call_model"
    assert row.metadata_["environment"] == "test"


def test_langgraph_repeated_runs_are_idempotent(db, parent_model):
    graph = _graph(_model([AIMessage(content="one"), AIMessage(content="two")]))
    config = _config(db)

    graph.invoke({"messages": [HumanMessage("hello")]}, config=config)
    graph.invoke({"messages": [HumanMessage("hello again")]}, config=config)

    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0].observation_count == 2


def test_langgraph_invocation_persists_no_prompt_content(db, parent_model):
    graph = _graph(_model([AIMessage(content="GRAPH-SECRET-RESPONSE")]))

    graph.invoke({"messages": [HumanMessage("SUPER-SECRET-PROMPT")]}, config=_config(db))

    rows = _rows(db)
    assert len(rows) == 1
    dump = json.dumps(rows[0].metadata_)
    assert "SUPER-SECRET-PROMPT" not in dump
    assert "GRAPH-SECRET-RESPONSE" not in dump
