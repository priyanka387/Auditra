"""Pure scoring engine (RF-03 §8/§13/§23).

Three deterministic, DB-free functions turn a framework's configuration
into a score, a severity rating, and a priority. Every range, band, and
priority comes from the caller's ``RiskSeverityThreshold`` rows — this
module contains no score literals, so a tenant that reconfigures its
matrix gets different answers without any code change (plan §3/§55).
"""

from collections.abc import Sequence

from app.modules.risk_scoring.enums import ScoringMethod
from app.modules.risk_scoring.errors import (
    InvalidScoringFrameworkError,
    UnsupportedScoringMethodError,
)
from app.modules.risk_scoring.models import (
    RiskScoringFramework,
    RiskSeverityThreshold,
)


class ScoreCalculator:
    """Turns likelihood/impact inputs into a numeric score (RF-03 §8)."""

    @staticmethod
    def calculate(likelihood: int, impact: int, framework: RiskScoringFramework) -> int:
        """Dispatch on the framework's configured scoring method."""
        if framework.method == ScoringMethod.LIKELIHOOD_IMPACT:
            return likelihood * impact
        raise UnsupportedScoringMethodError(
            f"scoring method '{framework.method}' has no engine implementation"
        )


class SeverityClassifier:
    """Maps a score onto the severity band that covers it (RF-03 §13)."""

    @staticmethod
    def classify(score: int, thresholds: Sequence[RiskSeverityThreshold]) -> str:
        """Return the severity of the band whose range contains ``score``.

        Bands are scanned in ascending ``minimum_score`` order so the
        result does not depend on how the caller sorted the rows.
        """
        ordered = sorted(thresholds, key=lambda threshold: threshold.minimum_score)
        for threshold in ordered:
            if threshold.minimum_score <= score <= threshold.maximum_score:
                return str(threshold.severity)
        raise InvalidScoringFrameworkError(f"no severity threshold covers score {score}")


class PriorityClassifier:
    """Maps a severity rating onto its configured priority (RF-03 §23)."""

    @staticmethod
    def assign(severity: str, thresholds: Sequence[RiskSeverityThreshold]) -> int:
        """Return the priority stored on the matching threshold row."""
        for threshold in thresholds:
            if threshold.severity == severity:
                return int(threshold.priority)
        raise InvalidScoringFrameworkError(f"no severity threshold for '{severity}'")
