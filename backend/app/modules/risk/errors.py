from app.modules.model_inventory.domain.errors import DomainError


class RiskError(DomainError):
    """Base error for Risk Register (RF-01) operations."""

    code = "RISK_ERROR"
    http_status = 400


class RiskNotFoundError(RiskError):
    code = "RISK_NOT_FOUND"
    http_status = 404


class RiskArchivedError(RiskError):
    """An archived risk is retained but no longer accepts mutations."""

    code = "RISK_ARCHIVED"
    http_status = 409


class InvalidRiskQueryError(RiskError):
    code = "RISK_QUERY_INVALID"
    http_status = 400


class RiskAssetNotFoundError(RiskError):
    """The linked AI asset does not exist in this tenant.

    Unknown and cross-tenant asset ids raise the same 404 so the
    response never leaks whether another organization holds a given
    asset (RF-01 §29).
    """

    code = "RISK_ASSET_NOT_FOUND"
    http_status = 404


class RiskCategoryError(DomainError):
    """Base error for risk taxonomy (RF-01) operations."""

    code = "RISK_CATEGORY_ERROR"
    http_status = 400


class RiskCategoryNotFoundError(RiskCategoryError):
    code = "RISK_CATEGORY_NOT_FOUND"
    http_status = 404


class DuplicateRiskCategoryError(RiskCategoryError):
    code = "RISK_CATEGORY_DUPLICATE"
    http_status = 409


class InvalidRiskCategoryQueryError(RiskCategoryError):
    code = "RISK_CATEGORY_QUERY_INVALID"
    http_status = 400
