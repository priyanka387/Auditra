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

Reconciliation state lives on ``DiscoveredAsset`` (status, reason and a
foreign key to the canonical ``Model``): MATCHED and IGNORED links are
sticky — re-running never re-matches them — while UNMATCHED and
AMBIGUOUS states are re-evaluated so a later canonical registration can
resolve them. Every attempt compares against the stored state first, so
repeated reconciliation writes nothing when the outcome is unchanged.

The service loads the tenant's non-archived canonical models once per
instance and matches through hash indexes, so a batch costs
O(candidates + assets) — never a pairwise scan of every asset against
every model.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.asset_discovery import repository
from app.modules.asset_discovery.enums import AssetType, ReconciliationStatus
from app.modules.asset_discovery.errors import DiscoveredAssetNotFoundError
from app.modules.model_inventory.models import Model, ModelProvider

logger = logging.getLogger("auditra.asset_discovery")

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


@dataclass(frozen=True)
class ReconciliationSummary:
    """Counts for one batch of reconciliation attempts.

    ``matched``/``unmatched``/``ambiguous`` count outcomes (whether just
    written or already stored), ``skipped`` counts assets no attempt was
    made for (non-reconcilable type or an IGNORED decision), and
    ``unchanged`` cross-cuts them: assets whose stored state already
    equaled the outcome, i.e. rows reconciliation did not write to.
    """

    matched: int = 0
    unmatched: int = 0
    ambiguous: int = 0
    skipped: int = 0
    unchanged: int = 0

    @property
    def attempted(self) -> int:
        return self.matched + self.unmatched + self.ambiguous


def load_candidates(db: Session, tenant_id: UUID) -> list[CanonicalModelCandidate]:
    """Load every canonical model candidate for one tenant in one query.

    Archived models are excluded (``lifecycle_state != 'ARCHIVED'`` and
    ``archived_at IS NULL``): an archived record is no longer an active
    canonical identity, so a discovered asset stays UNMATCHED rather
    than linking to it. The tenant predicate rides the models tenant
    index; nothing outside the tenant is ever considered.
    """
    rows = db.execute(
        select(Model.id, Model.canonical_key, Model.native_model_id, ModelProvider.slug)
        .join(ModelProvider, Model.provider_id == ModelProvider.id)
        .where(
            Model.tenant_id == tenant_id,
            Model.lifecycle_state != "ARCHIVED",
            Model.archived_at.is_(None),
        )
    ).all()
    return [
        CanonicalModelCandidate(
            id=row.id,
            canonical_key=row.canonical_key,
            native_model_id=row.native_model_id,
            provider_slug=row.slug,
        )
        for row in rows
    ]


class AssetReconciliationService:
    """Deterministically reconciles discovered assets to canonical models.

    Read-mostly: it consults canonical Model Inventory rows and writes
    only reconciliation state (status, reason, canonical model
    foreign key) back onto the discovered asset. It never creates,
    updates or deletes canonical models, never touches deduplication
    identity, and never links two discovered assets to each other.

    The canonical candidate index loads lazily once per service
    instance, so ``reconcile_many`` runs a single query for the whole
    batch. MATCHED and IGNORED assets short-circuit without any query —
    their stored decision is stable by design.
    """

    def __init__(
        self,
        db: Session,
        tenant_id: UUID,
        request_id: str | None = None,
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.request_id = request_id
        self._index: CanonicalModelIndex | None = None

    @property
    def index(self) -> CanonicalModelIndex:
        if self._index is None:
            self._index = CanonicalModelIndex(load_candidates(self.db, self.tenant_id))
        return self._index

    def reconcile(self, asset) -> ReconciliationOutcome:
        """Reconcile one discovered asset and persist the outcome.

        Idempotent: an already-resolved (MATCHED/IGNORED) asset returns
        its stored decision untouched, and any other outcome is only
        written when it differs from the stored state.
        """
        outcome, changed = self._attempt(asset)
        if changed:
            repository.commit_reconciliation(self.db)
        return outcome

    def reconcile_many(self, assets: Sequence) -> ReconciliationSummary:
        """Reconcile a batch (e.g. every asset one discovery run saw).

        One candidate-index query for the whole batch and one commit at
        the end; counts are returned for observability.
        """
        matched = unmatched = ambiguous = skipped = unchanged = 0
        changed = False
        for asset in assets:
            outcome, wrote = self._attempt(asset)
            changed = changed or wrote
            if outcome.status == ReconciliationStatus.MATCHED.value:
                matched += 1
            elif outcome.status == ReconciliationStatus.AMBIGUOUS.value:
                ambiguous += 1
            elif outcome.reason is None:
                skipped += 1  # not attempted: no canonical target / IGNORED
            else:
                unmatched += 1
            if not wrote:
                unchanged += 1
        if changed:
            repository.commit_reconciliation(self.db)
        summary = ReconciliationSummary(
            matched=matched,
            unmatched=unmatched,
            ambiguous=ambiguous,
            skipped=skipped,
            unchanged=unchanged,
        )
        logger.info(
            "asset reconciliation finished",
            extra={
                "tenant_id": str(self.tenant_id),
                "asset_count": len(assets),
                "assets_matched": summary.matched,
                "assets_unmatched": summary.unmatched,
                "assets_ambiguous": summary.ambiguous,
                "assets_skipped": summary.skipped,
                "assets_unchanged": summary.unchanged,
                "request_id": self.request_id,
            },
        )
        return summary

    def _attempt(self, asset) -> tuple[ReconciliationOutcome, bool]:
        """Resolve one asset's outcome and apply it; returns (outcome, wrote)."""
        if asset.tenant_id != self.tenant_id:
            raise DiscoveredAssetNotFoundError(f"discovered asset '{asset.id}' not found")

        stored = (
            asset.reconciliation_status,
            asset.reconciliation_reason,
            asset.reconciled_model_id,
        )
        if asset.reconciliation_status in (
            ReconciliationStatus.MATCHED.value,
            ReconciliationStatus.IGNORED.value,
        ):
            # stable decision: never re-matched, never churned
            return ReconciliationOutcome(*stored), False

        signals = extract_signals(asset)
        if signals is None:
            # no canonical target for this asset type: stays unreconciled
            outcome = ReconciliationOutcome(
                status=ReconciliationStatus.UNMATCHED.value,
                reason=None,
                model_id=None,
            )
        else:
            outcome = self.index.resolve(signals)

        if (outcome.status, outcome.reason, outcome.model_id) == stored:
            return outcome, False
        asset.reconciliation_status = outcome.status
        asset.reconciliation_reason = outcome.reason
        asset.reconciled_model_id = outcome.model_id
        return outcome, True
