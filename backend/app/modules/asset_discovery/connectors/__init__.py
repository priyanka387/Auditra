"""Discovery connector registry.

Mirrors the per-source-type dispatch table used by
``config_validation``: adding a future connector (GitHub, GitLab,
Kubernetes, cloud, model registries) means registering it here without
touching discovery orchestration.
"""

from app.modules.asset_discovery.connectors.base import (
    ConnectorIssue,
    DiscoveryConnector,
    DiscoveryConnectorResult,
    DiscoveryObservation,
)
from app.modules.asset_discovery.connectors.repository_connector import (
    RepositoryDiscoveryConnector,
)
from app.modules.asset_discovery.enums import DiscoverySourceType
from app.modules.asset_discovery.errors import DiscoveryConnectorUnsupportedError

_CONNECTORS: dict[str, type[DiscoveryConnector]] = {
    DiscoverySourceType.REPOSITORY.value: RepositoryDiscoveryConnector,
}


def get_connector(source_type: str) -> DiscoveryConnector:
    """Instantiate the connector for ``source_type``.

    Raises DiscoveryConnectorUnsupportedError when no connector is
    registered — a fatal execution error handled by the execution
    service.
    """
    connector_cls = _CONNECTORS.get(source_type)
    if connector_cls is None:
        raise DiscoveryConnectorUnsupportedError(
            f"no discovery connector registered for source type '{source_type}'"
        )
    return connector_cls()


__all__ = [
    "ConnectorIssue",
    "DiscoveryConnector",
    "DiscoveryConnectorResult",
    "DiscoveryObservation",
    "RepositoryDiscoveryConnector",
    "get_connector",
]
