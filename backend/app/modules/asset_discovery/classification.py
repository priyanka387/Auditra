"""Deterministic asset classification and metadata normalization (Sub-feature 5).

Discovery decides *where* an observation came from; classification
decides *what it represents* and normalizes its metadata. Every rule is
a static, explainable predicate over signals the connector already
produced (observation type, detection rule, provider, framework, model
identifier, file location) — no LLM, no network calls, no execution of
scanned content, and no fuzzy matching.

The rule list is ordered most-specific-first: the first matching rule
wins, so a later rule can never silently change an earlier outcome.
Observation types no rule knows about raise
InvalidDiscoveredAssetError — classification never guesses a type.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.modules.asset_discovery.connectors.base import (
    AGENT_REFERENCE,
    FRAMEWORK_REFERENCE,
    MODEL_REFERENCE,
    PROVIDER_REFERENCE,
    WORKFLOW_REFERENCE,
    DiscoveryObservation,
)
from app.modules.asset_discovery.connectors.detection import PROVIDER_IDS
from app.modules.asset_discovery.enums import AssetType
from app.modules.asset_discovery.errors import InvalidDiscoveredAssetError


def normalize_asset_name(raw: str) -> str:
    """Deterministic display/identity name: trimmed, whitespace collapsed."""
    return " ".join((raw or "").split())


def normalize_provider_slug(raw: str | None) -> str | None:
    """Normalized provider id (trimmed, lowercase); None when absent."""
    slug = (raw or "").strip().lower()
    return slug or None


def normalize_framework(raw: str | None) -> str | None:
    """Normalized framework/library id (trimmed, lowercase); None when absent."""
    framework = (raw or "").strip().lower()
    return framework or None


def split_provider_prefix(name: str) -> tuple[str | None, str]:
    """Split ``provider:model`` when the prefix is a known provider id.

    Deterministic only: an unknown prefix, an empty remainder or no
    delimiter at all leaves the identifier untouched. The remainder
    keeps its case — model identifiers are case-sensitive.
    """
    prefix, separator, rest = name.partition(":")
    if not separator:
        return None, name
    slug = prefix.strip().lower()
    remainder = rest.strip()
    if slug in PROVIDER_IDS and remainder:
        return slug, remainder
    return None, name


@dataclass(frozen=True)
class ClassificationResult:
    """Explainable outcome of classifying one discovery observation."""

    asset_type: AssetType  # what the observation represents
    name: str  # normalized asset name
    metadata: dict[str, Any]  # normalized, secret-free classification extras
    rule_id: str  # the rule that decided the type (explainability)


@dataclass(frozen=True)
class ClassificationRule:
    """One deterministic classification rule.

    ``matches`` inspects the raw observation; ``extras`` contributes
    normalized metadata for whatever the rule recognised. Rules are
    evaluated in list order and the first match wins.
    """

    rule_id: str
    asset_type: AssetType
    matches: Callable[[DiscoveryObservation], bool]
    extras: Callable[[DiscoveryObservation], dict[str, Any]] | None = None


def _is(observation_type: str) -> Callable[[DiscoveryObservation], bool]:
    return lambda observation: observation.observation_type == observation_type


def _framework_extras(observation: DiscoveryObservation) -> dict[str, Any]:
    framework = normalize_framework((observation.metadata or {}).get("framework"))
    return {"framework": framework} if framework else {}


def _model_reference_extras(observation: DiscoveryObservation) -> dict[str, Any]:
    provider, model_id = split_provider_prefix(normalize_asset_name(observation.name))
    extras: dict[str, Any] = {"model_id": model_id}
    known_provider = provider or normalize_provider_slug(observation.provider)
    if known_provider:
        extras["provider"] = known_provider
    return extras


def _provider_extras(observation: DiscoveryObservation) -> dict[str, Any]:
    provider = normalize_provider_slug(observation.provider)
    return {"provider": provider} if provider else {}


# Ordered most-specific-first (agent/workflow are structural signals,
# model/framework/provider are library signals). Types without a
# detection signal yet — MODEL, APPLICATION, DATASET, DEPLOYMENT,
# ENDPOINT — deliberately have no rule: classification does not invent
# evidence the discovery pass never produced.
CLASSIFICATION_RULES: tuple[ClassificationRule, ...] = (
    ClassificationRule(
        "type.agent_reference",
        AssetType.AGENT,
        _is(AGENT_REFERENCE),
        _framework_extras,
    ),
    ClassificationRule(
        "type.workflow_reference",
        AssetType.WORKFLOW,
        _is(WORKFLOW_REFERENCE),
        _framework_extras,
    ),
    ClassificationRule(
        "type.model_reference",
        AssetType.MODEL_REFERENCE,
        _is(MODEL_REFERENCE),
        _model_reference_extras,
    ),
    ClassificationRule(
        "type.framework_reference",
        AssetType.TOOL,
        _is(FRAMEWORK_REFERENCE),
        _framework_extras,
    ),
    ClassificationRule(
        "type.provider_reference",
        AssetType.AI_SERVICE,
        _is(PROVIDER_REFERENCE),
        _provider_extras,
    ),
)


def classify(observation: DiscoveryObservation) -> ClassificationResult:
    """Classify one observation deterministically.

    Returns the asset type the first matching rule decides, the
    normalized name and the normalized metadata that rule extracted.
    Raises InvalidDiscoveredAssetError for observation types no rule
    knows about — classification never guesses a type.
    """
    for rule in CLASSIFICATION_RULES:
        if not rule.matches(observation):
            continue
        extras = rule.extras(observation) if rule.extras else {}
        return ClassificationResult(
            asset_type=rule.asset_type,
            name=normalize_asset_name(observation.name),
            metadata=dict(extras),
            rule_id=rule.rule_id,
        )
    raise InvalidDiscoveredAssetError(
        f"unsupported observation type '{observation.observation_type}'"
    )
