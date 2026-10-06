from typing import Any

from app.modules.model_discovery.enums import DiscoverySourceType
from app.modules.model_discovery.integrations.langchain import AuditraDiscoveryCallback


def langgraph_discovery_config(
    sink: Any,
    *,
    source_identifier: str | None = None,
    environment: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Build a LangGraph/LangChain RunnableConfig that reports model discovery.

    Pass the returned dict as ``config`` to ``graph.invoke(...)``; every model
    call inside the graph emits one discovery observation through the sink.
    Identity overrides (``auditra_provider``/``auditra_model_identifier``) can
    be added to ``config["metadata"]`` by the caller.
    """
    metadata: dict[str, Any] = {}
    if environment is not None:
        metadata["auditra_environment"] = environment
    if source_identifier is not None:
        metadata["auditra_source_identifier"] = source_identifier
    return {
        "tags": ["auditra", *(tags or [])],
        "metadata": metadata,
        "callbacks": [
            AuditraDiscoveryCallback(
                sink,
                source=DiscoverySourceType.LANGGRAPH,
                source_identifier=source_identifier,
            )
        ],
    }
