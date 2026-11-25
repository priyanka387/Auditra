"""Treatment domain enums and state machines (RF-04).

Strategy, status, priority and residual-assessment types are stored as
low-case strings behind ``StrEnum`` (repo convention), and the lifecycle
is expressed as explicit transition maps so services validate moves in
one place (plan §55). Nothing here hard-codes treatment *logic* — the
strategy list is data the API surface iterates over (plan §5).
"""

from enum import StrEnum

__all__ = [
    "ACTION_TRANSITIONS",
    "ACTIVE_TREATMENT_STATUSES",
    "TREATMENT_TRANSITIONS",
    "ResidualAssessmentType",
    "RiskTreatmentStatus",
    "RiskTreatmentStrategy",
    "TreatmentActionStatus",
    "TreatmentPriority",
]


class RiskTreatmentStrategy(StrEnum):
    """How the organization decides to respond to a risk (plan §4).

    MITIGATE reduces likelihood/impact, ACCEPT explicitly owns the
    remaining exposure (rationale + audit required), AVOID stops the
    activity, TRANSFER shifts exposure to another party (plan §4).
    """

    MITIGATE = "mitigate"
    ACCEPT = "accept"
    AVOID = "avoid"
    TRANSFER = "transfer"


class RiskTreatmentStatus(StrEnum):
    """Treatment lifecycle (plan §6): not_started → planned → in_progress
    → completed, with cancelled reachable from every non-terminal state."""

    NOT_STARTED = "not_started"
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


# The states that count as an "active" treatment for the one-active-
# treatment-per-risk invariant (plan §48) and the register join (Ruling 5)
ACTIVE_TREATMENT_STATUSES: frozenset[str] = frozenset(
    {
        RiskTreatmentStatus.NOT_STARTED,
        RiskTreatmentStatus.PLANNED,
        RiskTreatmentStatus.IN_PROGRESS,
    }
)

# RF-04 §55 forward-only state machine. not_started → cancelled is the
# plan's one gap fill (Ruling 6); terminal states have no exits (no
# silent reopen — an explicit workflow would be needed for that).
TREATMENT_TRANSITIONS: dict[str, frozenset[str]] = {
    RiskTreatmentStatus.NOT_STARTED: frozenset(
        {RiskTreatmentStatus.PLANNED, RiskTreatmentStatus.CANCELLED}
    ),
    RiskTreatmentStatus.PLANNED: frozenset(
        {RiskTreatmentStatus.IN_PROGRESS, RiskTreatmentStatus.CANCELLED}
    ),
    RiskTreatmentStatus.IN_PROGRESS: frozenset(
        {RiskTreatmentStatus.COMPLETED, RiskTreatmentStatus.CANCELLED}
    ),
    RiskTreatmentStatus.COMPLETED: frozenset(),
    RiskTreatmentStatus.CANCELLED: frozenset(),
}


class TreatmentActionStatus(StrEnum):
    """Treatment-action lifecycle (plan §13)."""

    TODO = "todo"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


# todo ⇄ in_progress freely; completion/cancellation only from a
# non-terminal state; terminals are immutable (Ruling 6)
ACTION_TRANSITIONS: dict[str, frozenset[str]] = {
    TreatmentActionStatus.TODO: frozenset(
        {
            TreatmentActionStatus.IN_PROGRESS,
            TreatmentActionStatus.COMPLETED,
            TreatmentActionStatus.CANCELLED,
        }
    ),
    TreatmentActionStatus.IN_PROGRESS: frozenset(
        {
            TreatmentActionStatus.TODO,
            TreatmentActionStatus.COMPLETED,
            TreatmentActionStatus.CANCELLED,
        }
    ),
    TreatmentActionStatus.COMPLETED: frozenset(),
    TreatmentActionStatus.CANCELLED: frozenset(),
}


class TreatmentPriority(StrEnum):
    """Priority of a treatment action (plan §15) — deliberately separate
    from the RF-03 risk priority: a critical risk can carry a
    medium-priority administrative action."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ResidualAssessmentType(StrEnum):
    """Expected vs actual residual risk (plan §23/§24).

    RF-04 records EXPECTED only — an outcome is never claimed as
    verified until control/evidence assessment exists (RF-07).
    """

    EXPECTED = "expected"
    VERIFIED = "verified"
