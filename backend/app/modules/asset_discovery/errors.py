from app.modules.model_inventory.domain.errors import DomainError


class DiscoverySourceError(DomainError):
    code = "DISCOVERY_SOURCE_ERROR"
    http_status = 400


class DiscoverySourceNotFoundError(DiscoverySourceError):
    code = "DISCOVERY_SOURCE_NOT_FOUND"
    http_status = 404


class DuplicateDiscoverySourceError(DiscoverySourceError):
    code = "DISCOVERY_SOURCE_DUPLICATE"
    http_status = 409


class DiscoverySourceArchivedError(DiscoverySourceError):
    code = "DISCOVERY_SOURCE_ARCHIVED"
    http_status = 409


class InvalidDiscoverySourceConfigError(DiscoverySourceError):
    code = "DISCOVERY_SOURCE_CONFIG_INVALID"
    http_status = 422


class InvalidDiscoverySourceQueryError(DiscoverySourceError):
    code = "DISCOVERY_SOURCE_QUERY_INVALID"
    http_status = 400


class DiscoverySourceDisabledError(DiscoverySourceError):
    code = "DISCOVERY_SOURCE_DISABLED"
    http_status = 409


class DiscoveryRunError(DomainError):
    code = "DISCOVERY_RUN_ERROR"
    http_status = 400


class DiscoveryRunNotFoundError(DiscoveryRunError):
    code = "DISCOVERY_RUN_NOT_FOUND"
    http_status = 404


class InvalidDiscoveryRunTransitionError(DiscoveryRunError):
    code = "DISCOVERY_RUN_TRANSITION_INVALID"
    http_status = 409


class InvalidDiscoveryRunCountsError(DiscoveryRunError):
    code = "DISCOVERY_RUN_COUNTS_INVALID"
    http_status = 422


class InvalidDiscoveryRunQueryError(DiscoveryRunError):
    code = "DISCOVERY_RUN_QUERY_INVALID"
    http_status = 400


class DiscoveryExecutionError(DomainError):
    """A discovery execution failed before or while scanning."""

    code = "DISCOVERY_EXECUTION_ERROR"
    http_status = 400


class DiscoverySourceUnreachableError(DiscoveryExecutionError):
    """The configured location cannot be resolved to a readable directory.

    The message intentionally omits the configured path so filesystem
    layout is not leaked through API responses or persisted run state.
    """

    code = "DISCOVERY_SOURCE_UNREACHABLE"
    http_status = 422


class DiscoveryConnectorUnsupportedError(DiscoveryExecutionError):
    """No discovery connector is registered for the source type."""

    code = "DISCOVERY_CONNECTOR_UNSUPPORTED"
    http_status = 422


class DiscoveredAssetError(DomainError):
    """Base error for Discovered Asset Catalog operations."""

    code = "DISCOVERED_ASSET_ERROR"
    http_status = 400


class DiscoveredAssetNotFoundError(DiscoveredAssetError):
    code = "DISCOVERED_ASSET_NOT_FOUND"
    http_status = 404


class InvalidDiscoveredAssetError(DiscoveredAssetError):
    """An observation cannot be represented as a catalog asset."""

    code = "DISCOVERED_ASSET_INVALID"
    http_status = 422


class DuplicateDiscoveredAssetError(DiscoveredAssetError):
    code = "DISCOVERED_ASSET_DUPLICATE"
    http_status = 409


class InvalidDiscoveredAssetQueryError(DiscoveredAssetError):
    code = "DISCOVERED_ASSET_QUERY_INVALID"
    http_status = 400


class AssetLifecycleError(DomainError):
    """Base error for Asset Lifecycle (Sub-feature 8) operations."""

    code = "ASSET_LIFECYCLE_ERROR"
    http_status = 400


class InvalidAssetLifecycleEvaluationError(AssetLifecycleError):
    """Lifecycle evaluation was requested outside a valid completed-run scope.

    Raised when the run does not belong to the given source or when its
    status is not one that permits evaluation (only COMPLETED and PARTIAL
    runs carry a valid observation boundary; PENDING, RUNNING and FAILED
    runs must never age assets out of the catalog).
    """

    code = "ASSET_LIFECYCLE_EVALUATION_INVALID"
    http_status = 409


class InvalidAssetLifecyclePolicyError(AssetLifecycleError):
    """The configured stale/disappearance thresholds are inconsistent."""

    code = "ASSET_LIFECYCLE_POLICY_INVALID"
    http_status = 422
