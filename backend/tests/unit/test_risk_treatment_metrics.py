"""Unit tests: RF-04 derived metrics — progress and overdue (plan §17/§18).

Pure functions over transient ORM objects: progress is computed, never
stored (the 33/66/100 expectations of plan §56), and overdue is a
derived predicate (Ruling 9): only open actions and active treatments
with a past date count — completed/cancelled rows never do.
"""

from datetime import date

from app.modules.risk_treatment.metrics import (
    action_is_overdue,
    compute_progress,
    treatment_is_overdue,
)
from app.modules.risk_treatment.models import RiskTreatment, RiskTreatmentAction

TODAY = date(2026, 11, 20)


def _action(**overrides) -> RiskTreatmentAction:
    fields = {"title": "Restrict production access", "owner": "Security Team", "status": "todo"}
    fields.update(overrides)
    return RiskTreatmentAction(**fields)


def _treatment(**overrides) -> RiskTreatment:
    fields = {
        "tenant_id": "00000000-0000-0000-0000-000000000001",
        "risk_id": "00000000-0000-0000-0000-000000000002",
        "strategy": "mitigate",
        "status": "in_progress",
        "title": "Encrypt training data at rest",
    }
    fields.update(overrides)
    return RiskTreatment(**fields)


# --- compute_progress --------------------------------------------------------


def test_progress_three_actions_one_completed():
    progress = compute_progress(
        [
            _action(status="completed"),
            _action(status="in_progress"),
            _action(status="todo"),
        ]
    )

    assert progress.total_actions == 3
    assert progress.completed_actions == 1
    assert progress.in_progress_actions == 1
    assert progress.remaining_actions == 2
    assert progress.progress_percent == 33


def test_progress_two_completed():
    progress = compute_progress(
        [
            _action(status="completed"),
            _action(status="completed"),
            _action(status="in_progress"),
        ]
    )

    assert progress.progress_percent == 66
    assert progress.remaining_actions == 1


def test_progress_all_completed():
    progress = compute_progress([_action(status="completed") for _ in range(3)])

    assert progress.total_actions == 3
    assert progress.progress_percent == 100
    assert progress.remaining_actions == 0


def test_progress_no_actions_is_zero():
    progress = compute_progress([])

    assert progress.total_actions == 0
    assert progress.progress_percent == 0
    assert progress.remaining_actions == 0


def test_progress_cancelled_actions_do_not_count_as_remaining():
    progress = compute_progress(
        [
            _action(status="completed"),
            _action(status="cancelled"),
            _action(status="todo"),
        ]
    )

    assert progress.total_actions == 3
    assert progress.cancelled_actions == 1
    assert progress.remaining_actions == 1
    assert progress.progress_percent == 33


# --- action_is_overdue -------------------------------------------------------


def test_action_overdue_todo_past_due():
    action = _action(status="todo", due_date=date(2026, 11, 19))

    assert action_is_overdue(action, TODAY) is True


def test_action_overdue_completed_past_due():
    action = _action(status="completed", due_date=date(2026, 11, 19))

    assert action_is_overdue(action, TODAY) is False


def test_action_overdue_cancelled_past_due():
    action = _action(status="cancelled", due_date=date(2026, 11, 19))

    assert action_is_overdue(action, TODAY) is False


def test_action_overdue_future_due():
    action = _action(status="todo", due_date=date(2026, 12, 1))

    assert action_is_overdue(action, TODAY) is False


def test_action_overdue_without_due_date():
    action = _action(status="in_progress", due_date=None)

    assert action_is_overdue(action, TODAY) is False


# --- treatment_is_overdue ----------------------------------------------------


def test_treatment_overdue_only_when_active():
    past = date(2026, 11, 1)
    future = date(2026, 12, 1)

    assert (
        treatment_is_overdue(_treatment(status="in_progress", target_completion_date=past), TODAY)
        is True
    )
    # not_started counts as active for the derived predicate as well
    assert (
        treatment_is_overdue(_treatment(status="not_started", target_completion_date=past), TODAY)
        is True
    )
    assert (
        treatment_is_overdue(_treatment(status="completed", target_completion_date=past), TODAY)
        is False
    )
    assert (
        treatment_is_overdue(_treatment(status="cancelled", target_completion_date=past), TODAY)
        is False
    )
    assert (
        treatment_is_overdue(_treatment(status="planned", target_completion_date=future), TODAY)
        is False
    )
    assert (
        treatment_is_overdue(_treatment(status="in_progress", target_completion_date=None), TODAY)
        is False
    )
