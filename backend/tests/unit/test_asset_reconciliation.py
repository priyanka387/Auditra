"""Unit tests: deterministic reconciliation signals, matching precedence, and outcomes.

Reconciliation decides whether a *discovered asset* corresponds to an
existing canonical Model Inventory entity. It never merges discovered
assets (that is deduplication, Sub-feature 6) and never creates models.

Matching precedence under test (strongest first):

1. ``provider_and_model_identifier`` — exact ``canonical_key`` equality
2. ``model_identifier`` — exact ``native_model_id`` equality, only when
   the provider is unknown (ambiguous when several models share it)
3. ``normalized_model_identity`` — case-folded ``native_model_id``,
   scoped to the provider when known (ambiguous when several match)
4. otherwise ``UNMATCHED``

Weak signals — similar names, shared framework, shared provider without
a model identifier — must never reconcile.
"""

from uuid import UUID, uuid4

import pytest

from app.modules.asset_discovery.enums import AssetType, ReconciliationStatus
from app.modules.asset_discovery.models import DiscoveredAsset
from app.modules.asset_discovery.reconciliation import (
    MAX_MODEL_ID_LENGTH,
    CanonicalModelCandidate,
    CanonicalModelIndex,
    ReconciliationOutcome,
    extract_signals,
)

TENANT = uuid4()


def _asset(
    name: str = "gpt-4o",
    asset_type=AssetType.MODEL_REFERENCE,
    metadata=None,
) -> DiscoveredAsset:
    return DiscoveredAsset(
        tenant_id=TENANT,
        source_id=uuid4(),
        asset_type=asset_type.value if isinstance(asset_type, AssetType) else asset_type,
        name=name,
        metadata_=dict(metadata or {}) if metadata is not None else None,
    )


def _candidate(
    canonical_key: str,
    *,
    provider_slug: str | None = None,
    native_model_id: str | None = None,
    model_id: UUID | None = None,
) -> CanonicalModelCandidate:
    slug, _, native = canonical_key.partition("|")
    return CanonicalModelCandidate(
        id=model_id or uuid4(),
        canonical_key=canonical_key,
        native_model_id=native_model_id if native_model_id is not None else native,
        provider_slug=provider_slug if provider_slug is not None else slug,
    )


def _resolve(signals, candidates) -> ReconciliationOutcome:
    return CanonicalModelIndex(candidates).resolve(signals)


# --- signal extraction -----------------------------------------------------


def test_extract_signals_reads_provider_and_model_id_from_metadata():
    signals = extract_signals(_asset(metadata={"provider": "openai", "model_id": "gpt-4o"}))
    assert signals is not None
    assert signals.provider == "openai"
    assert signals.model_id == "gpt-4o"


def test_extract_signals_normalizes_provider_whitespace_and_case():
    signals = extract_signals(_asset(metadata={"provider": "  OpenAI ", "model_id": "gpt-4o"}))
    assert signals is not None
    assert signals.provider == "openai"


def test_extract_signals_treats_empty_provider_as_absent():
    signals = extract_signals(_asset(metadata={"provider": "   ", "model_id": "gpt-4o"}))
    assert signals is not None
    assert signals.provider is None


def test_extract_signals_falls_back_to_name_when_model_id_missing():
    signals = extract_signals(
        _asset(name="gpt-4o", asset_type=AssetType.MODEL, metadata={"provider": "openai"})
    )
    assert signals is not None
    assert signals.provider == "openai"
    assert signals.model_id == "gpt-4o"


def test_extract_signals_falls_back_to_name_when_metadata_empty():
    signals = extract_signals(_asset(name="claude-3-opus", asset_type=AssetType.MODEL))
    assert signals is not None
    assert signals.provider is None
    assert signals.model_id == "claude-3-opus"


def test_extract_signals_falls_back_to_name_for_non_string_model_id():
    signals = extract_signals(_asset(metadata={"model_id": 42}))
    assert signals is not None
    assert signals.model_id == "gpt-4o"


@pytest.mark.parametrize(
    "asset_type",
    [
        AssetType.APPLICATION,
        AssetType.AGENT,
        AssetType.WORKFLOW,
        AssetType.DATASET,
        AssetType.TOOL,
        AssetType.DEPLOYMENT,
        AssetType.ENDPOINT,
        AssetType.AI_SERVICE,
    ],
)
def test_extract_signals_returns_none_for_non_reconcilable_types(asset_type):
    assert extract_signals(_asset(asset_type=asset_type, metadata={"model_id": "gpt-4o"})) is None


def test_extract_signals_rejects_model_id_longer_than_canonical_column():
    long_id = "a" * (MAX_MODEL_ID_LENGTH + 1)
    assert extract_signals(_asset(metadata={"model_id": long_id})) is None
    # exactly at the bound is still usable
    at_bound = extract_signals(_asset(metadata={"model_id": "a" * MAX_MODEL_ID_LENGTH}))
    assert at_bound is not None and at_bound.model_id == "a" * MAX_MODEL_ID_LENGTH


def test_extract_signals_is_deterministic():
    asset = _asset(metadata={"provider": "openai", "model_id": "gpt-4o"})
    assert extract_signals(asset) == extract_signals(asset)


# --- matching precedence ---------------------------------------------------


def test_tier1_provider_and_model_identifier_matches_canonical_key():
    candidate = _candidate("openai|gpt-4o")
    outcome = _resolve(
        extract_signals(_asset(metadata={"provider": "openai", "model_id": "gpt-4o"})),
        [candidate],
    )
    assert outcome == ReconciliationOutcome(
        status=ReconciliationStatus.MATCHED.value,
        reason="provider_and_model_identifier",
        model_id=candidate.id,
    )


def test_tier1_beats_ambiguity_of_lower_tiers():
    # another provider also owns native id gpt-4o; a known provider
    # pins the match to its own canonical identity and never goes
    # ambiguous on a tier it does not need
    openai = _candidate("openai|gpt-4o")
    azure = _candidate("azure|gpt-4o")
    outcome = _resolve(
        extract_signals(_asset(metadata={"provider": "openai", "model_id": "gpt-4o"})),
        [azure, openai],
    )
    assert outcome.status == ReconciliationStatus.MATCHED.value
    assert outcome.reason == "provider_and_model_identifier"
    assert outcome.model_id == openai.id


def test_tier3_case_differing_model_id_matches_within_known_provider():
    candidate = _candidate("openai|gpt-4o")
    outcome = _resolve(
        extract_signals(_asset(metadata={"provider": "openai", "model_id": "GPT-4o"})),
        [candidate],
    )
    assert outcome == ReconciliationOutcome(
        status=ReconciliationStatus.MATCHED.value,
        reason="normalized_model_identity",
        model_id=candidate.id,
    )


def test_known_provider_never_matches_another_providers_model():
    outcome = _resolve(
        extract_signals(_asset(metadata={"provider": "anthropic", "model_id": "gpt-4o"})),
        [_candidate("openai|gpt-4o")],
    )
    assert outcome == ReconciliationOutcome(
        status=ReconciliationStatus.UNMATCHED.value,
        reason="no_canonical_model_match",
        model_id=None,
    )


def test_tier2_unique_exact_model_identifier_matches_without_provider():
    candidate = _candidate("openai|gpt-4o")
    outcome = _resolve(extract_signals(_asset()), [candidate])
    assert outcome == ReconciliationOutcome(
        status=ReconciliationStatus.MATCHED.value,
        reason="model_identifier",
        model_id=candidate.id,
    )


def test_tier2_ambiguous_when_two_providers_share_the_native_id():
    outcome = _resolve(
        extract_signals(_asset()), [_candidate("openai|gpt-4o"), _candidate("azure|gpt-4o")]
    )
    assert outcome == ReconciliationOutcome(
        status=ReconciliationStatus.AMBIGUOUS.value,
        reason="multiple_model_identifier_matches",
        model_id=None,
    )


def test_tier3_ambiguous_when_case_folded_ids_collide():
    # no exact native id "GPT-4O" exists; folding collides two rows
    outcome = _resolve(
        extract_signals(_asset(metadata={"model_id": "GPT-4O"})),
        [_candidate("openai|gpt-4o"), _candidate("azure|gpt-4o")],
    )
    assert outcome == ReconciliationOutcome(
        status=ReconciliationStatus.AMBIGUOUS.value,
        reason="multiple_normalized_identity_matches",
        model_id=None,
    )


def test_tier3_case_folded_unique_match_without_provider():
    candidate = _candidate("openai|gpt-4o")
    outcome = _resolve(extract_signals(_asset(metadata={"model_id": "GPT-4o"})), [candidate])
    assert outcome == ReconciliationOutcome(
        status=ReconciliationStatus.MATCHED.value,
        reason="normalized_model_identity",
        model_id=candidate.id,
    )


def test_tier2_exact_match_wins_over_folded_ambiguity():
    # exact native id "gpt-4o" exists once; folding would also hit
    # azure's "GPT-4o" — the exact tier decides first
    openai = _candidate("openai|gpt-4o")
    azure = _candidate("azure|GPT-4o", native_model_id="GPT-4o")
    outcome = _resolve(extract_signals(_asset()), [azure, openai])
    assert outcome.status == ReconciliationStatus.MATCHED.value
    assert outcome.reason == "model_identifier"
    assert outcome.model_id == openai.id


def test_no_candidates_returns_unmatched():
    outcome = _resolve(extract_signals(_asset()), [])
    assert outcome == ReconciliationOutcome(
        status=ReconciliationStatus.UNMATCHED.value,
        reason="no_canonical_model_match",
        model_id=None,
    )


# --- conservative behavior: weak signals never reconcile -------------------


def test_similar_model_identifiers_do_not_match():
    outcome = _resolve(
        extract_signals(_asset(metadata={"provider": "openai", "model_id": "gpt-4o"})),
        [_candidate("openai|gpt-4o-mini")],
    )
    assert outcome.status == ReconciliationStatus.UNMATCHED.value
    assert outcome.model_id is None


def test_same_provider_different_model_id_does_not_match():
    outcome = _resolve(
        extract_signals(_asset(metadata={"provider": "openai", "model_id": "gpt-4o-mini"})),
        [_candidate("openai|gpt-4o")],
    )
    assert outcome.status == ReconciliationStatus.UNMATCHED.value


def test_name_like_similarity_is_not_identity():
    # exact-equality only: punctuation/case variants are different ids
    outcome = _resolve(
        extract_signals(_asset(metadata={"model_id": "gpt4o"})),
        [_candidate("openai|gpt-4o")],
    )
    assert outcome.status == ReconciliationStatus.UNMATCHED.value


def test_provider_without_model_id_never_reconciles():
    # provider alone is a weak signal: an eligible asset whose identity
    # is only the provider must not link to any model of that provider
    candidate = _candidate("openai|gpt-4o")
    asset = _asset(name="openai", asset_type=AssetType.MODEL, metadata={"provider": "openai"})
    # name fallback yields model_id "openai" — no canonical native id
    outcome = _resolve(extract_signals(asset), [candidate])
    assert outcome.status == ReconciliationStatus.UNMATCHED.value
    assert outcome.model_id is None


def test_resolution_is_deterministic_across_candidate_order():
    candidates = [
        _candidate("openai|gpt-4o"),
        _candidate("azure|gpt-4o"),
        _candidate("anthropic|claude-3-opus"),
    ]
    signals = extract_signals(_asset(metadata={"model_id": "GPT-4o"}))
    baseline = _resolve(signals, candidates)
    assert _resolve(signals, list(reversed(candidates))) == baseline
    assert _resolve(signals, [candidates[1], candidates[2], candidates[0]]) == baseline


def test_reason_codes_pair_with_their_statuses():
    matched = _resolve(
        extract_signals(_asset(metadata={"provider": "openai", "model_id": "gpt-4o"})),
        [_candidate("openai|gpt-4o")],
    )
    ambiguous = _resolve(
        extract_signals(_asset()),
        [_candidate("openai|gpt-4o"), _candidate("azure|gpt-4o")],
    )
    unmatched = _resolve(extract_signals(_asset()), [])

    assert matched.status == ReconciliationStatus.MATCHED.value
    assert matched.reason in {
        "provider_and_model_identifier",
        "model_identifier",
        "normalized_model_identity",
    }
    assert matched.model_id is not None

    assert ambiguous.status == ReconciliationStatus.AMBIGUOUS.value
    assert ambiguous.reason in {
        "multiple_model_identifier_matches",
        "multiple_normalized_identity_matches",
    }
    assert ambiguous.model_id is None

    assert unmatched.status == ReconciliationStatus.UNMATCHED.value
    assert unmatched.reason == "no_canonical_model_match"
    assert unmatched.model_id is None


def test_empty_provider_is_treated_as_unknown_provider():
    # whitespace-only provider normalizes to None -> tier 2 global lookup
    candidate = _candidate("openai|gpt-4o")
    outcome = _resolve(
        extract_signals(_asset(metadata={"provider": "", "model_id": "gpt-4o"})),
        [candidate],
    )
    assert outcome.reason == "model_identifier"
    assert outcome.model_id == candidate.id
