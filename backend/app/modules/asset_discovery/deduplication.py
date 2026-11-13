"""Deterministic discovered-asset deduplication (Sub-feature 6).

Deduplication decides whether two discovery observations represent the
same discovered asset *within one discovery source*. It is deterministic
and explainable: no fuzzy matching, no similarity scoring, no external
signals — the same observations always produce the same decision.

Identity hierarchy (strongest first):

1. **Explicit stable identity** — a connector-reported external id.
   No V1 connector reports one; provider-qualified names already equal
   the normalized name, so this tier collapses into tier 2 today. No
   unreachable branch is implemented for it.
2. **Deterministically normalized identity** —
   ``<asset_type>|<normalized name>``, scoped to ``(tenant, source)``
   and enforced by the database unique constraint
   ``uq_discovered_assets_source_identity``. This is the only accepted
   match.
3. **Location / structured-metadata similarity — deliberately
   rejected.** Two assets sharing a path, filename, framework, provider
   or any other weak signal are *not* proven identical; a false merge
   destroys an observation and cannot be undone, while a missed merge
   merely keeps two rows. Weak similarity is never identity.

Cross-source matching is rejected for the same reason: identical
identity strings in different sources stay separate catalog entries.
Relating them to one canonical entity is Asset Reconciliation
(Sub-feature 7), not deduplication.

Merging follows the same discipline: an existing non-empty metadata
value wins (first observation saw it), empty values are filled in by
later observations, and a later observation never erases a stored value
with an empty one. No database access lives in the pure functions;
``AssetDeduplicationService`` adds the indexed lookup.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.modules.asset_discovery import repository
from app.modules.asset_discovery.catalog import build_normalized_identity
from app.modules.asset_discovery.classification import ClassificationResult
from app.modules.asset_discovery.models import DiscoveredAsset


@dataclass(frozen=True)
class DeduplicationKey:
    """The deterministic identity of one observation within its scope."""

    tenant_id: UUID
    source_id: UUID
    asset_type: str  # AssetType.value
    normalized_identity: str


def resolve_identity(classification: ClassificationResult) -> str:
    """Resolve an observation's identity from its classification (tier 2).

    Delegates to the catalog's validated identity builder: type-checked,
    whitespace-normalized name, length-bounded. Raises
    InvalidDiscoveredAssetError for unusable names/types rather than
    guessing an identity.
    """
    return build_normalized_identity(classification.asset_type, classification.name)


def build_key(
    tenant_id: UUID,
    source_id: UUID,
    classification: ClassificationResult,
) -> DeduplicationKey:
    """Bundle an observation's identity with the scope it is unique in."""
    return DeduplicationKey(
        tenant_id=tenant_id,
        source_id=source_id,
        asset_type=classification.asset_type.value,
        normalized_identity=resolve_identity(classification),
    )


def is_duplicate(asset: DiscoveredAsset | None, key: DeduplicationKey) -> bool:
    """True only when ``asset`` is an exact deterministic match for ``key``.

    Every scope and identity field must match; any mismatch (or a
    missing asset) is not a duplicate. Similar names never qualify.
    """
    if asset is None:
        return False
    return (
        asset.tenant_id == key.tenant_id
        and asset.source_id == key.source_id
        and asset.asset_type == key.asset_type
        and asset.normalized_identity == key.normalized_identity
    )


def _has_value(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def merge_metadata(
    existing: Mapping[str, Any] | None,
    incoming: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    """Deterministically merge a re-observation's metadata into the stored one.

    First observation wins for any non-empty value; later observations
    only fill values the stored metadata left empty and add keys the
    stored metadata does not have. Returns ``(merged, changed)`` where
    ``changed`` is False when merging would not alter the stored
    metadata — re-scanning an unchanged source never churns the row.
    """
    merged = dict(incoming or {})
    for key, value in (existing or {}).items():
        if _has_value(value):
            merged[key] = value  # first-seen non-empty value wins
        else:
            merged.setdefault(key, value)  # keep empties only when incoming lacks the key
    return merged, merged != (existing or {})


class AssetDeduplicationService:
    """Indexed catalog lookup for duplicate detection.

    Read-only: resolves and verifies identity against the catalog using
    the unique-constraint index. Creating and refreshing catalog rows
    stays with ``DiscoveredAssetService``; canonical Model Inventory
    entities are never consulted (that is Reconciliation, SF7).
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

    def build_key(self, source_id: UUID, classification: ClassificationResult) -> DeduplicationKey:
        return build_key(self.tenant_id, source_id, classification)

    def find_duplicate(self, key: DeduplicationKey) -> DiscoveredAsset | None:
        """Return the exact matching catalog asset, or None.

        The lookup is a single equality probe on
        ``(tenant_id, source_id, normalized_identity)`` — the database
        unique-constraint index — so catalog growth adds no per-asset
        cost beyond the index seek.
        """
        asset = repository.find_asset_by_identity(
            self.db, key.tenant_id, key.source_id, key.normalized_identity
        )
        return asset if is_duplicate(asset, key) else None
