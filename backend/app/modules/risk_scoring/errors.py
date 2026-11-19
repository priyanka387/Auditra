from app.modules.model_inventory.domain.errors import DomainError


class RiskScoringError(DomainError):
    """Base error for Risk Scoring (RF-03) operations."""

    code = "RISK_SCORING_ERROR"
    http_status = 400


class ScoringFrameworkNotFoundError(RiskScoringError):
    """Unknown and cross-tenant framework ids raise the same 404 so the
    response never leaks whether another organization holds a given
    framework (RF-03 §47)."""

    code = "SCORING_FRAMEWORK_NOT_FOUND"
    http_status = 404


class ScoringFrameworkInactiveError(RiskScoringError):
    """A disabled framework is retained but no longer accepts scoring."""

    code = "SCORING_FRAMEWORK_INACTIVE"
    http_status = 409


class DuplicateScoringFrameworkError(RiskScoringError):
    code = "SCORING_FRAMEWORK_DUPLICATE"
    http_status = 409


class InvalidScoringFrameworkError(RiskScoringError):
    """The framework's configured levels/thresholds violate RF-03 §13:
    overlapping bands, gaps, inverted ranges, duplicate severities."""

    code = "SCORING_FRAMEWORK_INVALID"
    http_status = 400


class InvalidScoringFrameworkQueryError(RiskScoringError):
    code = "SCORING_FRAMEWORK_QUERY_INVALID"
    http_status = 400


class UnsupportedScoringMethodError(RiskScoringError):
    """The requested scoring method has no engine implementation yet."""

    code = "SCORING_METHOD_UNSUPPORTED"
    http_status = 400


class RiskScoreNotFoundError(RiskScoringError):
    """No score exists yet for the risk — scoring status is UNSCORED
    (RF-03 §28), which is never treated as Low."""

    code = "RISK_SCORE_NOT_FOUND"
    http_status = 404


class InvalidRiskScoreError(RiskScoringError):
    """Likelihood/impact not valid for the selected framework, an
    override outside the framework's score range, or a mismatched
    assessment reference (RF-03 §52)."""

    code = "RISK_SCORE_INVALID"
    http_status = 400


class InvalidRiskScoreQueryError(RiskScoringError):
    code = "RISK_SCORE_QUERY_INVALID"
    http_status = 400
