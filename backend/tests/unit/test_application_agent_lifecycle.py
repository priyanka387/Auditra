from app.modules.model_inventory.domain import can_status_transition

VALID_PAIRS = [
    ("ACTIVE", "INACTIVE"),
    ("INACTIVE", "ACTIVE"),
    ("ACTIVE", "ARCHIVED"),
    ("INACTIVE", "ARCHIVED"),
]

INVALID_PAIRS = [
    ("ARCHIVED", "ACTIVE"),
    ("ARCHIVED", "INACTIVE"),
    ("ACTIVE", "ACTIVE"),
    ("INACTIVE", "INACTIVE"),
    ("ARCHIVED", "ARCHIVED"),
    ("BOGUS", "ACTIVE"),
    ("ACTIVE", "BOGUS"),
]


def test_valid_status_transitions():
    for current, target in VALID_PAIRS:
        assert can_status_transition(current, target) is True, (current, target)


def test_invalid_status_transitions():
    for current, target in INVALID_PAIRS:
        assert can_status_transition(current, target) is False, (current, target)


def test_archived_is_terminal():
    assert can_status_transition("ARCHIVED", "ACTIVE") is False
    assert can_status_transition("ARCHIVED", "INACTIVE") is False
