import logging
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from pydantic import ValidationError

from app.modules.model_discovery.enums import DiscoverySourceType
from app.modules.model_discovery.integrations.base import InsufficientMetadataResult
from app.modules.model_discovery.schemas import DiscoveryCreate

logger = logging.getLogger("auditra.discovery")


def _package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _model_class(serialized: Any) -> str | None:
    """Model class name from serialized['id'] only — kwargs are never read."""
    if not isinstance(serialized, dict):
        return None
    identifiers = serialized.get("id")
    if isinstance(identifiers, list) and identifiers:
        last = identifiers[-1]
        if isinstance(last, str) and last:
            return last
    return None


def extract_observation(
    serialized: Any,
    metadata: dict[str, Any] | None,
    *,
    source: DiscoverySourceType,
    source_identifier: str | None = None,
) -> DiscoveryCreate | InsufficientMetadataResult:
    """Build a discovery observation from LangChain run data.

    Public-API-only: reads ``ls_*``/``auditra_*`` tracing metadata and
    ``serialized['id']``. Prompts, messages, kwargs, and any metadata key
    outside the allowlist are never persisted.
    """
    metadata = metadata or {}
    model_class = _model_class(serialized)
    source_type = source.value

    provider = metadata.get("auditra_provider") or metadata.get("ls_provider")
    model_identifier = metadata.get("auditra_model_identifier") or metadata.get("ls_model_name")
    resolved_source_identifier = metadata.get("auditra_source_identifier") or source_identifier

    if not provider or not model_identifier:
        return InsufficientMetadataResult(
            error_code="INSUFFICIENT_METADATA",
            reason="missing provider or model identifier in run metadata",
            source_type=source_type,
            source_identifier=resolved_source_identifier,
            model_class=model_class,
        )

    observed: dict[str, Any] = {"framework": source_type}
    framework_version = _package_version("langchain-core")
    if framework_version is not None:
        observed["framework_version"] = framework_version
    if model_class is not None:
        observed["model_class"] = model_class
    environment = metadata.get("auditra_environment")
    if environment is not None:
        observed["environment"] = environment
    langgraph_node = metadata.get("langgraph_node")
    if langgraph_node is not None:
        observed["langgraph_node"] = langgraph_node

    model_type = metadata.get("auditra_model_type") or {"chat": "llm"}.get(
        metadata.get("ls_model_type"), metadata.get("ls_model_type")
    )
    display_name = metadata.get("auditra_display_name") or model_class

    try:
        return DiscoveryCreate.model_validate(
            {
                "source_type": source,
                "source_identifier": resolved_source_identifier,
                "provider": provider,
                "model_identifier": model_identifier,
                "model_type": model_type,
                "display_name": display_name,
                "metadata": observed,
            }
        )
    except ValidationError as exc:
        errors = exc.errors()
        return InsufficientMetadataResult(
            error_code="OBSERVATION_REJECTED",
            reason=errors[0].get("msg", "invalid observation") if errors else str(exc),
            source_type=source_type,
            source_identifier=resolved_source_identifier,
            model_class=model_class,
        )


class AuditraDiscoveryCallback(BaseCallbackHandler):
    """LangChain callback that reports one discovery observation per model start.

    Never reads ``messages``/``prompts``/``kwargs``. Sink failures are swallowed
    unless ``strict=True`` so instrumentation cannot break a model invocation.
    """

    def __init__(
        self,
        sink: Any,
        *,
        source: DiscoverySourceType = DiscoverySourceType.LANGCHAIN,
        source_identifier: str | None = None,
        strict: bool = False,
    ) -> None:
        self.sink = sink
        self.source = source
        self.source_identifier = source_identifier
        self.strict = strict

    def on_chat_model_start(
        self,
        serialized,
        messages,
        *,
        run_id,
        parent_run_id=None,
        tags=None,
        metadata=None,
        **kwargs,
    ) -> None:
        self._observe(serialized, metadata)

    def on_llm_start(
        self, serialized, prompts, *, run_id, parent_run_id=None, tags=None, metadata=None, **kwargs
    ) -> None:
        self._observe(serialized, metadata)

    def _observe(self, serialized, metadata) -> None:
        try:
            result = extract_observation(
                serialized,
                metadata,
                source=self.source,
                source_identifier=self.source_identifier,
            )
            if isinstance(result, DiscoveryCreate):
                self.sink.observe(result)
            else:
                self.sink.insufficient_metadata(result)
        except Exception:
            if self.strict:
                raise
            logger.exception("discovery observation failed")
