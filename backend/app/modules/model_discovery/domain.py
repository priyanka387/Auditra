from app.modules.model_discovery.enums import DiscoveryStatus
from app.modules.model_inventory.domain.identity import build_canonical_key

PROVIDER_ALIASES = {
    "open_ai": "openai",
}

_TRANSITIONS: dict[DiscoveryStatus, frozenset[DiscoveryStatus]] = {
    DiscoveryStatus.DISCOVERED: frozenset(
        {DiscoveryStatus.MATCHED, DiscoveryStatus.UNRESOLVED, DiscoveryStatus.FAILED}
    ),
    DiscoveryStatus.UNRESOLVED: frozenset(
        {DiscoveryStatus.MATCHED, DiscoveryStatus.REGISTERED, DiscoveryStatus.IGNORED}
    ),
    DiscoveryStatus.MATCHED: frozenset({DiscoveryStatus.REGISTERED, DiscoveryStatus.IGNORED}),
    DiscoveryStatus.FAILED: frozenset({DiscoveryStatus.DISCOVERED, DiscoveryStatus.IGNORED}),
    DiscoveryStatus.REGISTERED: frozenset(),
    DiscoveryStatus.IGNORED: frozenset(),
}


def normalize_provider(raw: str) -> str:
    provider = (raw or "").strip().lower()
    if not provider:
        raise ValueError("provider must be non-empty")
    return PROVIDER_ALIASES.get(provider, provider)


def normalize_model_identifier(raw: str) -> str:
    model_identifier = (raw or "").strip()
    if not model_identifier:
        raise ValueError("model_identifier must be non-empty")
    return model_identifier


def canonical_identity(provider: str, model_identifier: str) -> str:
    return build_canonical_key(normalize_provider(provider), normalize_model_identifier(model_identifier))


def can_transition(current: DiscoveryStatus | str, target: DiscoveryStatus | str) -> bool:
    current_status = DiscoveryStatus(current)
    target_status = DiscoveryStatus(target)
    if current_status is target_status:
        return True
    return target_status in _TRANSITIONS[current_status]
