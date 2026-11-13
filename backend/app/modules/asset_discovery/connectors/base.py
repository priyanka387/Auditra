"""Discovery connector abstraction.

Connectors inspect a configured discovery source and return raw
observations. Classification, normalization, persistence, deduplication,
reconciliation and evidence belong to later sub-features.

The dataclasses below are intentionally minimal intermediate results —
they are NOT the future ``DiscoveredAsset`` entity.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.modules.asset_discovery.models import DiscoverySource

# Stable observation types produced by detection rules. They are the
# contract between connector rules and the Discovered Asset Catalog
# (Sub-feature 4), which maps them to catalog asset types.
PROVIDER_REFERENCE = "provider_reference"
FRAMEWORK_REFERENCE = "framework_reference"
MODEL_REFERENCE = "model_reference"
AGENT_REFERENCE = "agent_reference"
WORKFLOW_REFERENCE = "workflow_reference"


@dataclass(frozen=True)
class DiscoveryObservation:
    """One raw signal detected in a source, before classification."""

    source_id: UUID
    run_id: UUID
    observation_type: str  # provider_reference | framework_reference | model_reference | ...
    name: str  # identifier as detected (provider, package, model id, ...)
    provider: str | None  # known only when the rule itself identifies a provider
    file_path: str  # path relative to the source root, posix separators
    line_number: int | None  # 1-based, when safely available
    detection_rule: str  # stable rule id explaining why this was observed
    metadata: dict[str, Any]  # small, rule-generated, secret-free extras


@dataclass(frozen=True)
class ConnectorIssue:
    """A recoverable problem that did not stop discovery."""

    file_path: str | None  # relative path; None for whole-scan issues
    reason: str  # machine-readable code, e.g. "file_too_large"
    message: str  # short human text; never absolute filesystem paths


@dataclass(frozen=True)
class DiscoveryConnectorResult:
    connector: str
    files_scanned: int
    observations: tuple[DiscoveryObservation, ...]
    issues: tuple[ConnectorIssue, ...]


class DiscoveryConnector(ABC):
    """Knows how to inspect one family of discovery sources.

    The core domain depends only on this interface so future connectors
    (GitHub, GitLab, Kubernetes, cloud, model registries) plug in without
    touching discovery orchestration.
    """

    name: str = ""

    @abstractmethod
    def discover(self, source: DiscoverySource, run_id: UUID) -> DiscoveryConnectorResult:
        """Statically inspect ``source`` and return normalized observations.

        Raises a DomainError subclass for fatal problems (invalid config,
        unreachable location); recoverable per-file problems are returned
        as issues. Must never execute content from the scanned source.
        """
