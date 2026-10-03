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


class InvalidTransitionError(DomainError):
    code = "INVALID_LIFECYCLE_TRANSITION"
    http_status = 409


class ModelArchivedError(DomainError):
    code = "MODEL_ARCHIVED"
    http_status = 409


class InvalidSortFieldError(DomainError):
    code = "INVALID_SORT_FIELD"
    http_status = 400
