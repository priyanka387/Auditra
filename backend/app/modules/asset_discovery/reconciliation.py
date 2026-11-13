"""Deterministic asset reconciliation to canonical entities (Sub-feature 7).

Reconciliation answers one question for a persisted discovered asset:

```text
DiscoveredAsset -- does it correspond to an existing canonical Auditra entity?
```

The only canonical target today is the Model Inventory ``Model``. Asset
discovery discovers, classification classifies, deduplication (Sub-feature
6) decides whether two *observations* are the same discovered asset —
reconciliation relates a discovered asset to a *canonical* model and
never merges discovered assets, never creates canonical models and never
fuzzy-matches.

Matching is deterministic, explainable and conservative. Precedence
(strongest first, adapted to the actual ``models`` schema where
``canonical_key = "<provider_slug>|<native_model_id>"`` is the canonical
stable identity, unique per tenant):

1. ``provider_and_model_identifier`` — the signals' provider + model
   identifier compose the exact canonical key (database-unique, so a hit
   can never be ambiguous).
2. ``model_identifier`` — exact ``native_model_id`` equality when the
   provider is *unknown*. Several models can share a native id across
   providers: 1 hit matches, more than one is AMBIGUOUS. A known
   provider never falls back to a cross-provider lookup — matching a
   different provider's model would be a false reconciliation.
3. ``normalized_model_identity`` — case-folded native id equality,
   scoped to the provider when known, again ambiguity-checked.
4. otherwise ``UNMATCHED`` (``no_canonical_model_match``).

Deliberately rejected signals: display-name similarity, filename or
framework affinity, shared repository, provider without a model
identifier, and any fuzzy/textual similarity. False positives are worse
than leaving an asset unresolved. Signals come from already-persisted,
validated discovery metadata only: no source content is read here, no
code or shell is executed, and nothing leaves the database.

The service loads the tenant's non-archived canonical models once per
instance and matches through hash indexes, so a batch costs
O(candidates + assets) — never a pairwise scan of every asset against
every model.
"""

from dataclasses import dataclass
from uuid import UUID

from app.modules.asset_discovery.enums import AssetType, ReconciliationStatus

# only these discovered asset types have a canonical Model Inventory
# target; every other type stays unreconciled until a canonical entity
# for it exists (the framework extends by adding targets, not by
# inventing canonical rows)
RECONCILABLE_ASSET_TYPES: frozenset[str] = frozenset(
    {AssetType.MODEL.value, AssetType.MODEL_REFERENCE.value}
)

# == Model.native_model_id column width; identifiers that cannot fit
# the canonical column are unusable as a match signal
MAX_MODEL_ID_LENGTH = 512

REASON_PROVIDER_AND_MODEL_IDENTIFIER = "provider_and_model_identifier"
REASON_MODEL_IDENTIFIER = "model_identifier"
REASON_NORMALIZED_MODEL_IDENTITY = "normalized_model_identity"
REASON_MULTIPLE_MODEL_IDENTIFIER_MATCHES = "multiple_model_identifier_matches"
REASON_MULTIPLE_NORMALIZED_IDENTITY_MATCHES = "multiple_normalized_identity_matches"
REASON_NO_CANONICAL_MODEL_MATCH = "no_canonical_model_match"

MATCH_REASONS: frozenset[str] = frozenset(
    {
        REASON_PROVIDER_AND_MODEL_IDENTIFIER,
        REASON_MODEL_IDENTIFIER,
        REASON_NORMALIZED_MODEL_IDENTITY,
    }
)
AMBIGUOUS_REASONS: frozenset[str] = frozenset(
    {
        REASON_MULTIPLE_MODEL_IDENTIFIER_MATCHES,
        REASON_MULTIPLE_NORMALIZED_IDENTITY_MATCHES,
    }
)
ALL_REASONS: frozenset[str] = MATCH_REASONS | AMBIGUOUS_REASONS | {REASON_NO_CANONICAL_MODEL_MATCH}


@dataclass(frozen=True)
class ReconciliationSignals:
    """Normalized identity signals extracted from a discovered asset."""

    provider: str | None  # lowercase provider slug when known
    model_id: str  # case-preserved model identifier


@dataclass(frozen=True)
class ReconciliationOutcome:
    """Explainable result of one reconciliation attempt."""

    status: str  # ReconciliationStatus.value
    reason: str | None  # the deterministic rule that decided (None = not attempted)
    model_id: UUID | None  # canonical Model.id when MATCHED, else None


@dataclass(frozen=True)
class CanonicalModelCandidate:
    """One non-archived canonical Model row, reduced to matching signals."""

    id: UUID
    canonical_key: str
    native_model_id: str
    provider_slug: str


def extract_signals(asset) -> ReconciliationSignals | None:
    """Extract canonical-matching signals from a discovered asset.

    Returns None when the asset type has no canonical target, or when no
    usable model identifier exists (nothing to match on — reconciliation
    never guesses). ``metadata`` supplies the classification-normalized
    provider/model_id (Sub-feature 5); the asset name is the identifier
    fallback for MODEL assets and for rows persisted before those keys
    existed. Signals are stripped/length-bounded before use.
    """
    if asset.asset_type not in RECONCILABLE_ASSET_TYPES:
        return None
    metadata = asset.metadata_ or {}

    provider: str | None = None
    raw_provider = metadata.get("provider")
    if isinstance(raw_provider, str) and raw_provider.strip():
        provider = raw_provider.strip().lower()

    model_id = metadata.get("model_id")
    if not isinstance(model_id, str) or not model_id.strip():
        model_id = asset.name or ""
    model_id = model_id.strip()
    if not model_id or len(model_id) > MAX_MODEL_ID_LENGTH:
        return None
    return ReconciliationSignals(provider=provider, model_id=model_id)


class CanonicalModelIndex:
    """Hash indexes over canonical model candidates for O(1) resolution.

    Built once per reconciliation batch from the tenant's non-archived
    models; ``resolve`` then decides a single asset's outcome without
    touching the database.
    """

    def __init__(self, candidates) -> None:
        self._by_canonical_key: dict[str, UUID] = {}
        self._by_native_id: dict[str, list[UUID]] = {}
        self._by_native_folded: dict[str, list[UUID]] = {}
        self._by_native_folded_scoped: dict[tuple[str, str], list[UUID]] = {}
        for candidate in candidates:
            # canonical_key is unique per (tenant, canonical_key) in the
            # database; setdefault keeps the first row defensively
            self._by_canonical_key.setdefault(candidate.canonical_key, candidate.id)
            self._by_native_id.setdefault(candidate.native_model_id, []).append(candidate.id)
            folded = candidate.native_model_id.lower()
            self._by_native_folded.setdefault(folded, []).append(candidate.id)
            scope = (candidate.provider_slug.lower(), folded)
            self._by_native_folded_scoped.setdefault(scope, []).append(candidate.id)

    def resolve(self, signals: ReconciliationSignals) -> ReconciliationOutcome:
        """Apply the documented precedence to one asset's signals."""
        if signals.provider is not None:
            # tier 1: exact canonical identity (provider + model id);
            # a known provider pins every weaker tier to itself, so a
            # different provider's identical native id is never visible
            canonical_key = f"{signals.provider.lower()}|{signals.model_id}"
            model_id = self._by_canonical_key.get(canonical_key)
            if model_id is not None:
                return ReconciliationOutcome(
                    status=ReconciliationStatus.MATCHED.value,
                    reason=REASON_PROVIDER_AND_MODEL_IDENTIFIER,
                    model_id=model_id,
                )
            hits = self._by_native_folded_scoped.get(
                (signals.provider.lower(), signals.model_id.lower()), []
            )
            folded_reason = REASON_MULTIPLE_NORMALIZED_IDENTITY_MATCHES
        else:
            # tier 2: exact native id while the provider is unknown
            hits = self._by_native_id.get(signals.model_id, [])
            if len(hits) == 1:
                return ReconciliationOutcome(
                    status=ReconciliationStatus.MATCHED.value,
                    reason=REASON_MODEL_IDENTIFIER,
                    model_id=hits[0],
                )
            if len(hits) > 1:
                return ReconciliationOutcome(
                    status=ReconciliationStatus.AMBIGUOUS.value,
                    reason=REASON_MULTIPLE_MODEL_IDENTIFIER_MATCHES,
                    model_id=None,
                )
            # tier 3: case-folded native id, still provider-agnostic
            hits = self._by_native_folded.get(signals.model_id.lower(), [])
            folded_reason = REASON_MULTIPLE_NORMALIZED_IDENTITY_MATCHES

        # tier 3 for a known provider (scoped) or the unknown-provider
        # fallback: one hit is a confident identity, several are ambiguous
        if len(hits) == 1:
            return ReconciliationOutcome(
                status=ReconciliationStatus.MATCHED.value,
                reason=REASON_NORMALIZED_MODEL_IDENTITY,
                model_id=hits[0],
            )
        if len(hits) > 1:
            return ReconciliationOutcome(
                status=ReconciliationStatus.AMBIGUOUS.value,
                reason=folded_reason,
                model_id=None,
            )
        return ReconciliationOutcome(
            status=ReconciliationStatus.UNMATCHED.value,
            reason=REASON_NO_CANONICAL_MODEL_MATCH,
            model_id=None,
        )
