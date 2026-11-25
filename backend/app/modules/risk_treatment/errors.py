"""Domain errors for RF-04 risk treatment operations."""

from app.modules.model_inventory.domain.errors import DomainError


class RiskTreatmentError(DomainError):
    """Base error for Risk Treatment (RF-04) operations."""

    code = "RISK_TREATMENT_ERROR"
    http_status = 400


class RiskTreatmentNotFoundError(RiskTreatmentError):
    """Unknown and cross-tenant treatment ids raise the same 404 so the
    response never leaks whether another organization holds a treatment
    (plan §51)."""

    code = "RISK_TREATMENT_NOT_FOUND"
    http_status = 404


class RiskTreatmentActiveExistsError(RiskTreatmentError):
    """A risk already has an active (not_started/planned/in_progress)
    treatment — at most one, DB-enforced by the partial unique index
    (plan §48)."""

    code = "RISK_TREATMENT_ACTIVE_EXISTS"
    http_status = 409


class InvalidRiskTreatmentStatusError(RiskTreatmentError):
    """The requested status transition is not in the state machine
    (plan §55): terminals are immutable and PATCH cannot reach
    completed/cancelled without the dedicated endpoints."""

    code = "RISK_TREATMENT_STATUS_INVALID"
    http_status = 409


class InvalidRiskTreatmentQueryError(RiskTreatmentError):
    code = "RISK_TREATMENT_QUERY_INVALID"
    http_status = 400


class RiskTreatmentActionNotFoundError(RiskTreatmentError):
    code = "RISK_TREATMENT_ACTION_NOT_FOUND"
    http_status = 404


class InvalidRiskTreatmentActionStatusError(RiskTreatmentError):
    """Action status transition rejected (terminal states immutable)."""

    code = "RISK_TREATMENT_ACTION_STATUS_INVALID"
    http_status = 409


class InvalidRiskTreatmentResidualError(RiskTreatmentError):
    """Residual assessment rejected: non-expected type, or likelihood/
    impact outside the active scoring framework's levels (plan §23)."""

    code = "RISK_TREATMENT_RESIDUAL_INVALID"
    http_status = 400
