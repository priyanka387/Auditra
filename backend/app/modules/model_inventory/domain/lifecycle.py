from typing import Literal

LifecycleState = Literal["REGISTERED", "ACTIVE", "DEPRECATED", "RETIRED", "ARCHIVED"]

INITIAL_STATES: set[str] = {"REGISTERED", "ACTIVE"}

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "REGISTERED": {"ACTIVE", "DEPRECATED", "ARCHIVED"},
    "ACTIVE": {"DEPRECATED", "RETIRED", "ARCHIVED"},
    "DEPRECATED": {"ACTIVE", "RETIRED", "ARCHIVED"},
    "RETIRED": {"ARCHIVED"},
    "ARCHIVED": set(),
}


def can_transition(current: str, target: str) -> bool:
    if current not in ALLOWED_TRANSITIONS or target not in ALLOWED_TRANSITIONS:
        return False
    if current == target:
        return False
    return target in ALLOWED_TRANSITIONS[current]


VersionLifecycleState = Literal["DRAFT", "ACTIVE", "DEPRECATED", "RETIRED", "ARCHIVED"]

VERSION_INITIAL_STATE = "DRAFT"

VERSION_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"ACTIVE", "DEPRECATED", "ARCHIVED"},
    "ACTIVE": {"DEPRECATED", "RETIRED", "ARCHIVED"},
    "DEPRECATED": {"ACTIVE", "RETIRED", "ARCHIVED"},
    "RETIRED": {"ARCHIVED"},
    "ARCHIVED": set(),
}


def can_version_transition(current: str, target: str) -> bool:
    if current not in VERSION_ALLOWED_TRANSITIONS or target not in VERSION_ALLOWED_TRANSITIONS:
        return False
    if current == target:
        return False
    return target in VERSION_ALLOWED_TRANSITIONS[current]
