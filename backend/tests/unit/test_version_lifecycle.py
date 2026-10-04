from app.modules.model_inventory.domain.lifecycle import (
    VERSION_ALLOWED_TRANSITIONS,
    VERSION_INITIAL_STATE,
    VersionLifecycleState,
    can_version_transition,
)

STATES = ["DRAFT", "ACTIVE", "DEPRECATED", "RETIRED", "ARCHIVED"]
SPEC_EDGES = {
    ("DRAFT", "ACTIVE"),
    ("DRAFT", "DEPRECATED"),
    ("DRAFT", "ARCHIVED"),
    ("ACTIVE", "DEPRECATED"),
    ("ACTIVE", "RETIRED"),
    ("ACTIVE", "ARCHIVED"),
    ("DEPRECATED", "ACTIVE"),
    ("DEPRECATED", "RETIRED"),
    ("DEPRECATED", "ARCHIVED"),
    ("RETIRED", "ARCHIVED"),
}


def test_version_transitions_match_spec():
    declared = {(c, t) for c, targets in VERSION_ALLOWED_TRANSITIONS.items() for t in targets}
    assert declared == SPEC_EDGES
    assert len(SPEC_EDGES) == 10
    for current in STATES:
        for target in STATES:
            expected = (current, target) in SPEC_EDGES
            assert can_version_transition(current, target) is expected, f"{current} -> {target}"
    assert can_version_transition("FOO", "ACTIVE") is False
    assert can_version_transition("ACTIVE", "FOO") is False


def test_archived_is_terminal():
    for target in STATES:
        assert can_version_transition("ARCHIVED", target) is False


def test_version_initial_state_is_draft():
    assert VERSION_INITIAL_STATE == "DRAFT"
    assert set(VERSION_ALLOWED_TRANSITIONS) == set(STATES)
    assert set(VersionLifecycleState.__args__) == set(STATES)
