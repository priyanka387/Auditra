"""Pure derived metrics for RF-04 treatments and actions (plan §17/§18).

Nothing here touches the database or stores state: progress is computed
from the action rows every time it is read (the 33/66/100 expectations
of plan §56), and overdue is a predicate evaluated against a caller-
supplied ``today`` so tests stay deterministic (Ruling 9: only open
actions and active treatments with a past date are overdue).
"""

from collections.abc import Sequence
from datetime import date

from app.modules.risk_treatment.enums import (
    ACTIVE_TREATMENT_STATUSES,
    TreatmentActionStatus,
)
from app.modules.risk_treatment.models import RiskTreatment, RiskTreatmentAction
from app.modules.risk_treatment.schemas import TreatmentProgress

# The statuses from which an action can still be overdue: terminals
# (completed/cancelled) never count, whatever their dates say (Ruling 9)
OPEN_ACTION_STATUSES: frozenset[str] = frozenset(
    {TreatmentActionStatus.TODO, TreatmentActionStatus.IN_PROGRESS}
)


def treatment_is_overdue(treatment: RiskTreatment, today: date) -> bool:
    """An active treatment whose target completion date has passed."""
    return (
        treatment.status in ACTIVE_TREATMENT_STATUSES
        and treatment.target_completion_date is not None
        and treatment.target_completion_date < today
    )


def action_is_overdue(action: RiskTreatmentAction, today: date) -> bool:
    """An open action whose due date has passed (plan §57)."""
    return (
        action.status in OPEN_ACTION_STATUSES
        and action.due_date is not None
        and action.due_date < today
    )


def compute_progress(actions: Sequence[RiskTreatmentAction]) -> TreatmentProgress:
    """Derived action progress for a treatment (plan §18).

    ``remaining`` excludes cancelled work — abandoned items are not
    outstanding — and the percent floors via integer division (0 when
    there is no work at all, never a division by zero).
    """
    total = len(actions)
    completed = sum(1 for action in actions if action.status == TreatmentActionStatus.COMPLETED)
    in_progress = sum(1 for action in actions if action.status == TreatmentActionStatus.IN_PROGRESS)
    cancelled = sum(1 for action in actions if action.status == TreatmentActionStatus.CANCELLED)
    remaining = total - completed - cancelled
    progress_percent = (completed * 100) // total if total else 0
    return TreatmentProgress(
        total_actions=total,
        completed_actions=completed,
        in_progress_actions=in_progress,
        cancelled_actions=cancelled,
        remaining_actions=remaining,
        progress_percent=progress_percent,
    )
