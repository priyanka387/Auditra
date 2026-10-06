from app.modules.model_inventory.domain.errors import DomainError


class DiscoveryError(DomainError):
    code = "DISCOVERY_ERROR"
    http_status = 400


class DiscoveryNotFoundError(DiscoveryError):
    code = "DISCOVERY_NOT_FOUND"
    http_status = 404


class InvalidDiscoveryTransitionError(DiscoveryError):
    code = "DISCOVERY_TRANSITION_INVALID"
    http_status = 409


class InvalidDiscoveryQueryError(DiscoveryError):
    code = "DISCOVERY_QUERY_INVALID"
    http_status = 400


class DuplicateDiscoveryError(DiscoveryError):
    code = "DISCOVERY_DUPLICATE"
    http_status = 409
