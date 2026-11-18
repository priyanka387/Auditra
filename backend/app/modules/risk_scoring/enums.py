"""Scoring domain enums (RF-03).

The scoring methodology, the severity ratings, and a score row's input
provenance. Severity labels are the four fixed ratings from RF-03 §13;
which score ranges map to them (and what priority each carries) lives on
``RiskSeverityThreshold`` rows so it stays configurable per framework.
"""

from enum import StrEnum


class ScoringMethod(StrEnum):
    """How a framework turns scoring inputs into a numeric score (RF-03 §8).

    The initial implementation multiplies likelihood by impact; the enum
    exists so weighted or multi-dimensional methods can be added without
    touching stored data.
    """

    LIKELIHOOD_IMPACT = "likelihood_impact"


class RiskSeverity(StrEnum):
    """Risk rating a calculated score is classified into (RF-03 §5)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ScoreSource(StrEnum):
    """Where a score row's likelihood/impact inputs came from (RF-03 §19).

    ``assessment`` records keep the RF-02 assessment reference; the
    values themselves are always entered explicitly — never inferred
    from questionnaire answers (RF-03 §50).
    """

    MANUAL = "manual"
    ASSESSMENT = "assessment"
