import logging
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Any
from uuid import UUID, uuid4

from langchain_core.callbacks import BaseCallbackHandler

from app.modules.model_usage.enums import TokenUsageSource, UsageSource, UsageStatus
from app.modules.model_usage.errors import UsageContextMissingError
from app.modules.model_usage.sanitization import sanitize_error_message
from app.modules.model_usage.schemas import UsageEventCreate
from app.modules.model_usage.telemetry import UsageTelemetryCollector

logger = logging.getLogger("auditra.usage")


def _package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def extract_token_usage(response: Any) -> tuple[int, int, int | None] | None:
    """Provider-neutral token extraction from LangChain model responses."""
    usage = getattr(response, "usage_metadata", None)
    found = _tokens_from_usage(usage)
    if found is not None:
        return found

    message = getattr(response, "message", None)
    found = _tokens_from_usage(getattr(message, "usage_metadata", None))
    if found is not None:
        return found

    llm_output = getattr(response, "llm_output", None)
    if isinstance(llm_output, dict):
        token_usage = llm_output.get("token_usage") or llm_output.get("usage")
        found = _tokens_from_usage(token_usage)
        if found is not None:
            return found

    for generation in _iter_generations(response):
        found = _tokens_from_usage(
            getattr(getattr(generation, "message", None), "usage_metadata", None)
        )
        if found is not None:
            return found
    return None


def _iter_generations(response: Any) -> list[Any]:
    generations = getattr(response, "generations", None) or []
    flat: list[Any] = []
    for item in generations:
        if isinstance(item, list):
            flat.extend(item)
        else:
            flat.append(item)
    return flat


def _tokens_from_usage(usage: Any) -> tuple[int, int, int | None] | None:
    if not isinstance(usage, dict):
        return None
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
    if input_tokens is None or output_tokens is None:
        return None
    total_tokens = usage.get("total_tokens")
    return (
        int(input_tokens),
        int(output_tokens),
        (int(total_tokens) if total_tokens is not None else None),
    )


def _optional_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    return UUID(str(value))


class AuditraUsageCallback(BaseCallbackHandler):
    """LangChain callback that emits one Auditra usage event per model run.

    Pass it via RunnableConfig together with ``auditra_*`` metadata keys; the
    inventory model id is required (``auditra_model_id``).
    """

    def __init__(
        self,
        collector: UsageTelemetryCollector,
        source: UsageSource = UsageSource.LANGCHAIN,
    ) -> None:
        self.collector = collector
        self.source = source
        self.raise_error = getattr(collector, "mode", "best_effort") == "strict"
        self._runs: dict[Any, dict[str, Any]] = {}

    @property
    def framework_name(self) -> str:
        return "langgraph" if self.source == UsageSource.LANGGRAPH else "langchain"

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
        self._start(run_id, parent_run_id, metadata)

    def on_llm_start(
        self, serialized, prompts, *, run_id, parent_run_id=None, tags=None, metadata=None, **kwargs
    ) -> None:
        self._start(run_id, parent_run_id, metadata)

    def on_chat_model_end(self, response, *, run_id, parent_run_id=None, **kwargs) -> None:
        self._end(run_id, response)

    def on_llm_end(self, response, *, run_id, parent_run_id=None, **kwargs) -> None:
        self._end(run_id, response)

    def on_chat_model_error(self, error, *, run_id, parent_run_id=None, **kwargs) -> None:
        self._error(run_id, error)

    def on_llm_error(self, error, *, run_id, parent_run_id=None, **kwargs) -> None:
        self._error(run_id, error)

    def _start(self, run_id, parent_run_id, metadata) -> None:
        metadata = metadata or {}
        state: dict[str, Any] = {
            "started_at": datetime.now(UTC),
            "parent_run_id": str(parent_run_id) if parent_run_id is not None else None,
            "run_id": str(run_id),
            "context": None,
        }
        self._runs[run_id] = state

        model_id = metadata.get("auditra_model_id")
        if not model_id:
            message = "Auditra usage telemetry requires metadata key 'auditra_model_id'"
            if self.raise_error:
                raise UsageContextMissingError(message)
            logger.warning("%s; event dropped", message)
            return

        try:
            state["context"] = {
                "model_id": UUID(str(model_id)),
                "model_version_id": _optional_uuid(metadata.get("auditra_model_version_id")),
                "deployment_id": _optional_uuid(metadata.get("auditra_deployment_id")),
                "application_id": _optional_uuid(metadata.get("auditra_application_id")),
                "agent_id": _optional_uuid(metadata.get("auditra_agent_id")),
                "event_id": str(metadata.get("auditra_event_id") or uuid4()),
                "request_id": str(metadata.get("auditra_request_id") or run_id),
                "trace_id": metadata.get("auditra_trace_id"),
                "operation_name": metadata.get("auditra_operation"),
                "environment": metadata.get("auditra_environment"),
                "region": metadata.get("auditra_region"),
                "tags": metadata.get("auditra_tags"),
            }
        except ValueError as exc:
            if self.raise_error:
                raise UsageContextMissingError(f"invalid auditra metadata: {exc}") from None
            logger.warning("invalid auditra metadata; event dropped: %s", exc)
            state["context"] = None

    def _end(self, run_id, response) -> None:
        state = self._runs.pop(run_id, None)
        context = state and state.get("context")
        if not context:
            return
        usage = extract_token_usage(response)
        event = self._build_event(
            state,
            context,
            status=UsageStatus.SUCCESS,
            input_tokens=usage[0] if usage else None,
            output_tokens=usage[1] if usage else None,
            total_tokens=usage[2] if usage else None,
            token_usage_source=(
                TokenUsageSource.PROVIDER_REPORTED if usage else TokenUsageSource.UNAVAILABLE
            ),
        )
        try:
            self.collector.emit(event)
        except Exception:
            if self.raise_error:
                raise
            logger.exception("usage telemetry failed for event %s", context["event_id"])

    def _error(self, run_id, error) -> None:
        state = self._runs.pop(run_id, None)
        context = state and state.get("context")
        if not context:
            return
        event = self._build_event(
            state,
            context,
            status=UsageStatus.ERROR,
            error_type=type(error).__name__,
            error_message=sanitize_error_message(str(error)),
        )
        try:
            self.collector.emit(event)
        except Exception:
            if self.raise_error:
                raise
            logger.exception("usage telemetry failed for event %s", context["event_id"])

    def _build_event(
        self, state, context, *, status, error_type=None, error_message=None, **metrics
    ):
        return UsageEventCreate(
            event_id=context["event_id"],
            model_id=context["model_id"],
            model_version_id=context["model_version_id"],
            deployment_id=context["deployment_id"],
            application_id=context["application_id"],
            agent_id=context["agent_id"],
            status=status,
            source=self.source,
            started_at=state["started_at"],
            completed_at=datetime.now(UTC),
            request_id=context["request_id"],
            trace_id=context["trace_id"],
            parent_run_id=state["parent_run_id"],
            framework=self.framework_name,
            framework_version=_package_version("langchain-core"),
            operation_name=context["operation_name"],
            environment=context["environment"],
            region=context["region"],
            tags=context["tags"],
            error_type=error_type,
            error_message=error_message,
            **metrics,
        )
