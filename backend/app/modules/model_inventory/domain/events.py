from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class ModelEvent:
    event_id: str
    event_type: str
    model_id: UUID
    tenant_id: UUID
    occurred_at: datetime
    actor: str | None
    change_summary: list[str]
    request_id: str | None


_handlers: list[Callable[[ModelEvent], None]] = []


def register_handler(fn: Callable[[ModelEvent], None]) -> None:
    _handlers.append(fn)


def dispatch_event(event: ModelEvent) -> None:
    for fn in _handlers:
        fn(event)


def reset_handlers() -> None:
    _handlers.clear()
