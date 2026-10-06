from dataclasses import dataclass
from typing import Protocol

from app.modules.model_discovery.schemas import DiscoveryCreate


@dataclass(frozen=True)
class InsufficientMetadataResult:
    error_code: str  # "INSUFFICIENT_METADATA" | "OBSERVATION_REJECTED"
    reason: str
    source_type: str
    source_identifier: str | None
    model_class: str | None


class DiscoverySink(Protocol):
    def observe(self, payload: DiscoveryCreate) -> None: ...

    def insufficient_metadata(self, result: InsufficientMetadataResult) -> None: ...
