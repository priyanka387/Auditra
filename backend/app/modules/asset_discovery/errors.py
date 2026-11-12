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
