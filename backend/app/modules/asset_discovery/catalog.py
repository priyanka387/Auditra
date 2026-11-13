"""Discovered Asset Catalog domain logic.

Pure functions that turn a raw discovery observation into the catalog
representation: deterministic identity, validated metadata and
secret-shaped value guards. No database access lives here (persistence
orchestration is ``DiscoveredAssetService``).

Classification semantics and metadata normalization live in
``classification`` (Sub-feature 5); its result is merged into the
persisted payload by ``build_asset_metadata``. Deciding whether two
observations describe the same real-world asset belongs to Asset
Deduplication (Sub-feature 6).
"""

import re
from typing import Any

from app.modules.asset_discovery.classification import (
    ClassificationResult,
    normalize_asset_name,
)
from app.modules.asset_discovery.connectors.base import DiscoveryObservation
from app.modules.asset_discovery.enums import AssetType
from app.modules.asset_discovery.errors import InvalidDiscoveredAssetError
from app.modules.model_inventory.schemas.model import SECRET_KEY_RE, validate_metadata

MAX_NAME_LENGTH = 255
MAX_IDENTITY_LENGTH = 1024
MAX_FILE_PATH_LENGTH = 1024
MAX_LINE_NUMBER = 10_000_000

# Credential-shaped values (sk-..., ghp_..., AKIA..., ...) surfaced as a
# name or rule-generated metadata value are never persisted. Matching is
# intentionally narrow: metadata *keys* are already screened by
# ``validate_metadata``, and legitimate values such as the environment
# variable *name* ``OPENAI_API_KEY`` must be kept.
_CREDENTIAL_VALUE_RE = re.compile(
    r"^(?:sk|pk|ghp|gho|ghs|ghu|github_pat|xox[abpes]|AKIA|AIza|glpat|npm_)[-_]",
    re.IGNORECASE,
)

# observation metadata keys that never carry credential-shaped content
# (they are structural values produced by the connector itself)
_STRUCTURAL_METADATA_KEYS = frozenset({"file_path", "detection_rule", "observation_type"})


def build_normalized_identity(asset_type: AssetType | str, name: str) -> str:
    """Deterministic identity of an asset within one discovery source.

    Format: ``<asset_type>|<normalized name>``. Names keep their case —
    case-folding would pre-empt the deduplication rules Sub-feature 6
    owns. Uniqueness is enforced per (tenant, source) at the database
    level, so observations from different sources never collide here and
    cross-source reconciliation keeps room to relate them later.
    """
    try:
        kind = AssetType(asset_type).value
    except ValueError:
        raise InvalidDiscoveredAssetError(f"unsupported asset type '{asset_type}'") from None
    normalized = normalize_asset_name(name)
    if not normalized:
        raise InvalidDiscoveredAssetError("asset name must not be empty")
    identity = f"{kind}|{normalized}"
    if len(identity) > MAX_IDENTITY_LENGTH:
        raise InvalidDiscoveredAssetError("asset identity exceeds maximum length")
    return identity


def validate_file_path(file_path: str) -> str:
    """Validate an observation path as relative to the source root."""
    if not isinstance(file_path, str) or not file_path.strip():
        raise InvalidDiscoveredAssetError("observation file_path must be a non-empty string")
    if len(file_path) > MAX_FILE_PATH_LENGTH or "\x00" in file_path:
        raise InvalidDiscoveredAssetError("observation file_path is invalid")
    if file_path.startswith("/") or "\\" in file_path or re.match(r"^[A-Za-z]:", file_path):
        raise InvalidDiscoveredAssetError(
            "observation file_path must be relative to the source root"
        )
    if any(part == ".." for part in file_path.split("/")):
        raise InvalidDiscoveredAssetError(
            "observation file_path must not escape the source root"
        )
    return file_path


def build_asset_metadata(
    observation: DiscoveryObservation,
    classification: ClassificationResult | None = None,
) -> dict[str, Any]:
    """Validated, secret-free metadata persisted for one observation.

    Merges three layers: the rule-generated extras, the normalized
    classification extras (Sub-feature 5) and the structural fields
    (rule, path, location) which always win. Classification extras win
    over the raw value of the same signal because they are that signal
    normalized. ``classification_rule`` records which rule decided the
    asset type so the stored asset stays explainable.
    """
    classification_metadata = dict(classification.metadata) if classification else {}
    metadata: dict[str, Any] = {
        # rule-generated extras first, classification normalization
        # second, structural keys below always win
        **(observation.metadata or {}),
        **classification_metadata,
        "observation_type": observation.observation_type,
        "file_path": validate_file_path(observation.file_path),
        "detection_rule": observation.detection_rule,
    }
    if classification is not None:
        metadata["classification_rule"] = classification.rule_id
    provider = classification_metadata.get("provider") or observation.provider
    if provider:
        metadata["provider"] = provider
    if observation.line_number is not None:
        if (
            isinstance(observation.line_number, bool)
            or not isinstance(observation.line_number, int)
            or observation.line_number < 1
            or observation.line_number > MAX_LINE_NUMBER
        ):
            raise InvalidDiscoveredAssetError("observation line_number is invalid")
        metadata["line_number"] = observation.line_number
    try:
        validate_metadata(metadata)
    except (TypeError, ValueError) as exc:
        raise InvalidDiscoveredAssetError(f"observation metadata is invalid: {exc}") from None
    return metadata


def has_secret_shaped_value(name: str, observation: DiscoveryObservation) -> bool:
    """True when an observation looks like it carries a credential.

    Such observations are skipped rather than persisted: raw secrets
    never enter the catalog. Only the asset name and rule-generated
    metadata values are screened (structural paths are validated
    separately and are not secret material).
    """
    if SECRET_KEY_RE.search(name) or _CREDENTIAL_VALUE_RE.match(name):
        return True
    values = [observation.provider, *(observation.metadata or {}).values()]
    return any(
        isinstance(value, str) and bool(_CREDENTIAL_VALUE_RE.match(value))
        for value in values
    )
