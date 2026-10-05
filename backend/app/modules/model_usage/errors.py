from app.modules.model_inventory.domain.errors import DomainError


class ModelUsageError(DomainError):
    code = "MODEL_USAGE_ERROR"
    http_status = 400


class DuplicateEventIdError(ModelUsageError):
    code = "EVENT_ID_DUPLICATE"
    http_status = 409


class IdempotencyConflictError(ModelUsageError):
    code = "EVENT_ID_CONFLICT"
    http_status = 409


class UsageRelationshipError(ModelUsageError):
    code = "USAGE_RELATIONSHIP_INVALID"
    http_status = 409


class BatchTooLargeError(ModelUsageError):
    code = "USAGE_BATCH_TOO_LARGE"
    http_status = 422


class UsageContextMissingError(ModelUsageError):
    code = "USAGE_MODEL_ID_MISSING"
    http_status = 422


class ModelUsageNotFoundError(ModelUsageError):
    code = "MODEL_USAGE_EVENT_NOT_FOUND"
    http_status = 404


class InvalidUsageQueryError(ModelUsageError):
    code = "INVALID_USAGE_QUERY"
    http_status = 400
