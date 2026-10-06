from typing import Any
from uuid import UUID

from app.modules.model_usage.enums import UsageSource
from app.modules.model_usage.integrations.langchain import AuditraUsageCallback
from app.modules.model_usage.telemetry import UsageTelemetryCollector


def langgraph_usage_config(
    collector: UsageTelemetryCollector,
    *,
    model_id: UUID | str,
    model_version_id: UUID | str | None = None,
    deployment_id: UUID | str | None = None,
    application_id: UUID | str | None = None,
    agent_id: UUID | str | None = None,
    operation: str | None = None,
    environment: str | None = None,
    region: str | None = None,
    trace_id: str | None = None,
    event_id: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Build a LangGraph/LangChain RunnableConfig that records model usage.

    Pass the returned dict as ``config`` to ``graph.invoke(...)``; every model
    call inside the graph emits one Auditra usage event through the collector.
    """
    metadata: dict[str, Any] = {"auditra_model_id": str(model_id)}
    optional = {
        "auditra_model_version_id": model_version_id,
        "auditra_deployment_id": deployment_id,
        "auditra_application_id": application_id,
        "auditra_agent_id": agent_id,
        "auditra_operation": operation,
        "auditra_environment": environment,
        "auditra_region": region,
        "auditra_trace_id": trace_id,
        "auditra_event_id": event_id,
    }
    metadata.update({key: value for key, value in optional.items() if value is not None})
    return {
        "tags": ["auditra", *(tags or [])],
        "metadata": metadata,
        "callbacks": [AuditraUsageCallback(collector, source=UsageSource.LANGGRAPH)],
    }
