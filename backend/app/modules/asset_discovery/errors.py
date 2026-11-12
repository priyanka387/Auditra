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
