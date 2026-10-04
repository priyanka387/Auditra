from app.modules.model_inventory.domain.lifecycle import (
    ALLOWED_TRANSITIONS,
    INITIAL_STATES,
    can_transition,
)

STATES = ["REGISTERED", "ACTIVE", "DEPRECATED", "RETIRED", "ARCHIVED"]

SPEC_EDGES = {
    ("REGISTERED", "ACTIVE"),
    ("REGISTERED", "DEPRECATED"),
    ("REGISTERED", "ARCHIVED"),
    ("ACTIVE", "DEPRECATED"),
    ("ACTIVE", "RETIRED"),
    ("ACTIVE", "ARCHIVED"),
    ("DEPRECATED", "ACTIVE"),
    ("DEPRECATED", "RETIRED"),
    ("DEPRECATED", "ARCHIVED"),
    ("RETIRED", "ARCHIVED"),
}


def test_allowed_transitions_match_spec():
    declared = {(c, t) for c, targets in ALLOWED_TRANSITIONS.items() for t in targets}
    assert declared == SPEC_EDGES
    assert len(SPEC_EDGES) == 10
    for current in STATES:
        for target in STATES:
            expected = (current, target) in SPEC_EDGES
            assert can_transition(current, target) is expected, f"{current} -> {target}"
    assert can_transition("FOO", "ACTIVE") is False
    assert can_transition("ACTIVE", "FOO") is False


def test_archived_is_terminal():
    for target in STATES:
        assert can_transition("ARCHIVED", target) is False


def test_initial_states():
    assert INITIAL_STATES == {"REGISTERED", "ACTIVE"}


def test_matrix_keys_match_states():
    assert set(ALLOWED_TRANSITIONS) == set(STATES)
