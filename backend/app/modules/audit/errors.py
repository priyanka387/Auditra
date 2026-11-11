from app.modules.model_inventory.domain.errors import DomainError


class AuditEventNotFoundError(DomainError):
    code = "AUDIT_EVENT_NOT_FOUND"
    http_status = 404


class InvalidAuditFilterError(DomainError):
    code = "INVALID_AUDIT_FILTER"
    http_status = 400


class InvalidAuditEventError(DomainError):
    code = "INVALID_AUDIT_EVENT"
    http_status = 500
