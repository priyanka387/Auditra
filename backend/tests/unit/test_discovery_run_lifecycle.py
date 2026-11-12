import pytest

from app.modules.asset_discovery.run_lifecycle import can_run_transition

VALID_TRANSITIONS = [
    ("PENDING", "RUNNING"),
    ("RUNNING", "COMPLETED"),
    ("RUNNING", "FAILED"),
    ("RUNNING", "PARTIAL"),
]

INVALID_TRANSITIONS = [
    # skipping the start step entirely
    ("PENDING", "COMPLETED"),
    ("PENDING", "FAILED"),
    ("PENDING", "PARTIAL"),
    # no-op transitions
    ("PENDING", "PENDING"),
    ("RUNNING", "RUNNING"),
    ("COMPLETED", "COMPLETED"),
    ("FAILED", "FAILED"),
    ("PARTIAL", "PARTIAL"),
    # backwards transitions
    ("RUNNING", "PENDING"),
    ("COMPLETED", "PENDING"),
    ("FAILED", "PENDING"),
    ("PARTIAL", "PENDING"),
    # terminal states are final
    ("COMPLETED", "RUNNING"),
    ("COMPLETED", "FAILED"),
    ("COMPLETED", "PARTIAL"),
    ("FAILED", "RUNNING"),
    ("FAILED", "COMPLETED"),
    ("FAILED", "PARTIAL"),
    ("PARTIAL", "RUNNING"),
    ("PARTIAL", "COMPLETED"),
    ("PARTIAL", "FAILED"),
    # unknown states
    ("UNKNOWN", "RUNNING"),
    ("RUNNING", "BOGUS"),
    ("", "RUNNING"),
]


@pytest.mark.parametrize(("current", "target"), VALID_TRANSITIONS)
def test_valid_run_transitions_are_allowed(current, target):
    assert can_run_transition(current, target) is True


@pytest.mark.parametrize(("current", "target"), INVALID_TRANSITIONS)
def test_invalid_run_transitions_are_rejected(current, target):
    assert can_run_transition(current, target) is False
