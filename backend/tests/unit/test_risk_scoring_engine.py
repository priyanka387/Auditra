"""Unit tests: RF-03 scoring engine (calculator + classifiers).

The engine must read every range and priority from the threshold rows —
never from literals (plan §3/§55) — so these tests build transient ORM
objects that stand in for a framework's configuration.
"""

import pytest

from app.modules.risk_scoring.engine import (
    PriorityClassifier,
    ScoreCalculator,
    SeverityClassifier,
)
from app.modules.risk_scoring.errors import (
    InvalidScoringFrameworkError,
    UnsupportedScoringMethodError,
)
from app.modules.risk_scoring.models import (
    RiskScoringFramework,
    RiskSeverityThreshold,
)

# (severity, min, max, priority) — RF-03 §49 default bands
DEFAULT_BANDS: list[tuple[str, int, int, int]] = [
    ("low", 1, 4, 4),
    ("medium", 5, 9, 3),
    ("high", 10, 16, 2),
    ("critical", 17, 25, 1),
]


def _framework(method: str = "likelihood_impact") -> RiskScoringFramework:
    return RiskScoringFramework(
        tenant_id=None,
        name="test",
        version=1,
        method=method,  # transient: never flushed
    )


def _thresholds(bands: list[tuple[str, int, int, int]]) -> list[RiskSeverityThreshold]:
    return [
        RiskSeverityThreshold(
            minimum_score=minimum,
            maximum_score=maximum,
            severity=severity,
            priority=priority,
        )
        for severity, minimum, maximum, priority in bands
    ]


def test_calculate_multiplies_likelihood_by_impact():
    framework = _framework()
    assert ScoreCalculator.calculate(4, 5, framework) == 20
    assert ScoreCalculator.calculate(3, 4, framework) == 12
    assert ScoreCalculator.calculate(2, 3, framework) == 6
    assert ScoreCalculator.calculate(1, 2, framework) == 2


def test_calculate_rejects_unsupported_method():
    framework = _framework(method="weighted")
    with pytest.raises(UnsupportedScoringMethodError):
        ScoreCalculator.calculate(3, 4, framework)


def test_classify_default_bands():
    thresholds = _thresholds(DEFAULT_BANDS)
    assert SeverityClassifier.classify(20, thresholds) == "critical"
    assert SeverityClassifier.classify(12, thresholds) == "high"
    assert SeverityClassifier.classify(6, thresholds) == "medium"
    assert SeverityClassifier.classify(2, thresholds) == "low"


def test_classify_band_boundaries():
    thresholds = _thresholds(DEFAULT_BANDS)
    assert SeverityClassifier.classify(4, thresholds) == "low"
    assert SeverityClassifier.classify(5, thresholds) == "medium"
    assert SeverityClassifier.classify(9, thresholds) == "medium"
    assert SeverityClassifier.classify(10, thresholds) == "high"
    assert SeverityClassifier.classify(16, thresholds) == "high"
    assert SeverityClassifier.classify(17, thresholds) == "critical"
    assert SeverityClassifier.classify(25, thresholds) == "critical"


def test_classify_uses_framework_thresholds_not_literals():
    """The same score classifies differently under different configs —
    proof the engine reads the rows and hard-codes nothing (plan §55)."""
    framework_a = _thresholds(DEFAULT_BANDS)
    framework_b = _thresholds(
        [
            ("low", 1, 4, 4),
            ("medium", 5, 11, 3),
            ("high", 12, 20, 2),
            ("critical", 21, 25, 1),
        ]
    )
    assert SeverityClassifier.classify(11, framework_a) == "high"
    assert SeverityClassifier.classify(11, framework_b) == "medium"
    assert SeverityClassifier.classify(12, framework_b) == "high"


def test_classify_score_outside_all_bands_raises():
    thresholds = _thresholds([("critical", 17, 25, 1)])
    with pytest.raises(InvalidScoringFrameworkError):
        SeverityClassifier.classify(10, thresholds)


def test_priority_comes_from_threshold_rows():
    thresholds = _thresholds(DEFAULT_BANDS)
    assert PriorityClassifier.assign("critical", thresholds) == 1
    assert PriorityClassifier.assign("high", thresholds) == 2
    assert PriorityClassifier.assign("medium", thresholds) == 3
    assert PriorityClassifier.assign("low", thresholds) == 4

    swapped = _thresholds(
        [
            ("low", 1, 4, 9),
            ("medium", 5, 9, 3),
            ("high", 10, 16, 2),
            ("critical", 17, 25, 1),
        ]
    )
    assert PriorityClassifier.assign("low", swapped) == 9


def test_assign_unknown_severity_raises():
    thresholds = _thresholds(DEFAULT_BANDS)
    with pytest.raises(InvalidScoringFrameworkError):
        PriorityClassifier.assign("catastrophic", thresholds)
