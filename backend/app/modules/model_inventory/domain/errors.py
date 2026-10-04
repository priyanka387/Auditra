class DomainError(Exception):
    code: str = "INTERNAL_ERROR"
    http_status: int = 500

    def __init__(self, message: str = "") -> None:
        self.message = message or self.code
        super().__init__(self.message)


class ModelNotFoundError(DomainError):
    code = "MODEL_NOT_FOUND"
    http_status = 404


class DuplicateModelError(DomainError):
    code = "MODEL_ALREADY_EXISTS"
    http_status = 409


class IdentityImmutableError(DomainError):
    code = "MODEL_IDENTITY_CONFLICT"
    http_status = 409


class ProviderNotFoundError(DomainError):
    code = "PROVIDER_NOT_FOUND"
    http_status = 404


class ProviderInactiveError(DomainError):
    code = "PROVIDER_INACTIVE"
    http_status = 409


class ModelTypeNotFoundError(DomainError):
    code = "MODEL_TYPE_NOT_FOUND"
    http_status = 404


class ModelTypeInactiveError(DomainError):
    code = "MODEL_TYPE_INACTIVE"
    http_status = 409


class InvalidTransitionError(DomainError):
    code = "INVALID_LIFECYCLE_TRANSITION"
    http_status = 409


class ModelArchivedError(DomainError):
    code = "MODEL_ARCHIVED"
    http_status = 409


class InvalidSortFieldError(DomainError):
    code = "INVALID_SORT_FIELD"
    http_status = 400


class ModelVersionNotFoundError(DomainError):
    code = "MODEL_VERSION_NOT_FOUND"
    http_status = 404


class DuplicateModelVersionError(DomainError):
    code = "MODEL_VERSION_ALREADY_EXISTS"
    http_status = 409


class VersionIdentityImmutableError(DomainError):
    code = "MODEL_VERSION_IDENTITY_IMMUTABLE"
    http_status = 409


class InvalidVersionTransitionError(DomainError):
    code = "INVALID_MODEL_VERSION_LIFECYCLE_TRANSITION"
    http_status = 409


class ModelVersionArchivedError(DomainError):
    code = "MODEL_VERSION_ALREADY_ARCHIVED"
    http_status = 409


class VersionConcurrencyConflictError(DomainError):
    code = "VERSION_CONCURRENCY_CONFLICT"
    http_status = 409


class DeploymentNotFoundError(DomainError):
    code = "DEPLOYMENT_NOT_FOUND"
    http_status = 404


class DeploymentAlreadyArchivedError(DomainError):
    code = "DEPLOYMENT_ALREADY_ARCHIVED"
    http_status = 409


class InvalidDeploymentTransitionError(DomainError):
    code = "DEPLOYMENT_INVALID_TRANSITION"
    http_status = 409


class DuplicateDeploymentError(DomainError):
    code = "DEPLOYMENT_DUPLICATE"
    http_status = 409


class DeploymentConcurrencyConflictError(DomainError):
    code = "DEPLOYMENT_CONCURRENCY_CONFLICT"
    http_status = 409


class ModelVersionNotDeployableError(DomainError):
    code = "MODEL_VERSION_NOT_DEPLOYABLE"
    http_status = 409


class DeploymentEndpointNotFoundError(DomainError):
    code = "ENDPOINT_NOT_FOUND"
    http_status = 404


class DuplicateEndpointError(DomainError):
    code = "ENDPOINT_DUPLICATE"
    http_status = 409


class DuplicatePrimaryEndpointError(DomainError):
    code = "ENDPOINT_DUPLICATE_PRIMARY"
    http_status = 409


class InvalidPrimaryEndpointError(DomainError):
    code = "ENDPOINT_INVALID_PRIMARY"
    http_status = 409


class EndpointArchivedError(DomainError):
    code = "ENDPOINT_ALREADY_ARCHIVED"
    http_status = 409


class InvalidEndpointUrlError(DomainError):
    code = "ENDPOINT_INVALID_URL"
    http_status = 409


class InvalidAuthReferenceError(DomainError):
    code = "INVALID_AUTH_REFERENCE"
    http_status = 409
