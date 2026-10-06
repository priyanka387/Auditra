import pytest

from app.modules.model_discovery.domain import (
    can_transition,
    canonical_identity,
    normalize_model_identifier,
    normalize_provider,
)
from app.modules.model_discovery.enums import DiscoverySourceType, DiscoveryStatus
from app.modules.model_discovery.errors import (
    DiscoveryNotFoundError,
    DiscoveryError,
    DuplicateDiscoveryError,
    InvalidDiscoveryQueryError,
    InvalidDiscoveryTransitionError,
)


def test_source_types_are_lowercase_strings():
    assert DiscoverySourceType.MANUAL.value == "manual"
    assert DiscoverySourceType.LANGCHAIN.value == "langchain"
    assert DiscoverySourceType.LANGGRAPH.value == "langgraph"


def test_statuses_are_uppercase_strings():
    assert {s.value for s in DiscoveryStatus} == {
        "DISCOVERED",
        "MATCHED",
        "UNRESOLVED",
        "REGISTERED",
        "IGNORED",
        "FAILED",
    }


def test_normalize_provider_lowercases_and_trims():
    assert normalize_provider("  OpenAI ") == "openai"


def test_normalize_provider_alias_open_ai():
    assert normalize_provider("open_ai") == "openai"


def test_normalize_provider_blank_raises():
    with pytest.raises(ValueError):
        normalize_provider("   ")
    with pytest.raises(ValueError):
        normalize_provider("")


def test_normalize_model_identifier_preserves_case():
    assert normalize_model_identifier(" GPT-5.X ") == "GPT-5.X"


def test_normalize_model_identifier_blank_raises():
    with pytest.raises(ValueError):
        normalize_model_identifier("  ")


def test_canonical_identity_matches_registration_format():
    assert canonical_identity("OpenAI", "gpt-5.x") == "openai|gpt-5.x"
    assert canonical_identity("ollama", "llama3") == "ollama|llama3"


def test_canonical_identity_equivalent_inputs_match():
    assert canonical_identity(" openai ", "gpt-5.x") == canonical_identity(
        "OpenAI", "gpt-5.x"
    )


def test_canonical_identity_blank_raises():
    with pytest.raises(ValueError):
        canonical_identity("", "gpt-5.x")
    with pytest.raises(ValueError):
        canonical_identity("openai", " ")


VALID_EDGES = [
    ("DISCOVERED", "MATCHED"),
    ("DISCOVERED", "UNRESOLVED"),
    ("DISCOVERED", "FAILED"),
    ("UNRESOLVED", "MATCHED"),
    ("UNRESOLVED", "REGISTERED"),
    ("UNRESOLVED", "IGNORED"),
    ("MATCHED", "REGISTERED"),
    ("MATCHED", "IGNORED"),
    ("FAILED", "DISCOVERED"),
    ("FAILED", "IGNORED"),
]


@pytest.mark.parametrize("current,target", VALID_EDGES)
def test_valid_transitions_allowed(current, target):
    assert can_transition(current, target) is True


@pytest.mark.parametrize(
    "current,target",
    [
        ("DISCOVERED", "REGISTERED"),
        ("DISCOVERED", "IGNORED"),
        ("MATCHED", "UNRESOLVED"),
        ("MATCHED", "DISCOVERED"),
        ("IGNORED", "MATCHED"),
        ("IGNORED", "UNRESOLVED"),
        ("REGISTERED", "IGNORED"),
        ("REGISTERED", "MATCHED"),
        ("FAILED", "MATCHED"),
        ("FAILED", "REGISTERED"),
        ("UNRESOLVED", "DISCOVERED"),
        ("UNRESOLVED", "FAILED"),
    ],
)
def test_invalid_transitions_rejected(current, target):
    assert can_transition(current, target) is False


@pytest.mark.parametrize("status", [s.value for s in DiscoveryStatus])
def test_same_state_refresh_is_not_a_transition(status):
    assert can_transition(status, status) is True


def test_error_codes_and_statuses():
    assert DiscoveryError.code == "DISCOVERY_ERROR" and DiscoveryError.http_status == 400
    assert (
        DiscoveryNotFoundError.code == "DISCOVERY_NOT_FOUND"
        and DiscoveryNotFoundError.http_status == 404
    )
    assert (
        InvalidDiscoveryTransitionError.code == "DISCOVERY_TRANSITION_INVALID"
        and InvalidDiscoveryTransitionError.http_status == 409
    )
    assert (
        InvalidDiscoveryQueryError.code == "DISCOVERY_QUERY_INVALID"
        and InvalidDiscoveryQueryError.http_status == 400
    )
    assert DuplicateDiscoveryError.code == "DISCOVERY_DUPLICATE"
    assert DuplicateDiscoveryError.http_status == 409
