import logging
from typing import Protocol

from app.core.config import settings
from app.modules.model_usage.schemas import UsageEventCreate

logger = logging.getLogger("auditra.usage")


class UsageTelemetryCollector(Protocol):
    def emit(self, event: UsageEventCreate) -> None: ...


class ServiceUsageCollector:
    """Synchronous collector that persists events through ModelUsageService.

    BEST_EFFORT (default) swallows telemetry failures so a model invocation
    never fails because of Auditra; STRICT re-raises for tests/debugging.
    """

    def __init__(self, service, mode: str | None = None) -> None:
        self.service = service
        self.mode = mode or settings.usage_mode

    def emit(self, event: UsageEventCreate) -> None:
        if not settings.usage_enabled:
            logger.debug("usage telemetry disabled; dropping event %s", event.event_id)
            return
        try:
            self.service.record_event(event)
        except Exception:
            if self.mode == "strict":
                raise
            logger.exception("usage telemetry failed for event %s", event.event_id)


class NoopCollector:
    def emit(self, event: UsageEventCreate) -> None:
        return None
