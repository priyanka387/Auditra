"""Per-source-type configuration rules for discovery sources.

V1 validates only the repository source. The dispatch table keeps
connector-specific rules isolated so future source types (GitHub,
GitLab, Kubernetes, cloud accounts, model registries) add their own
validators without changing the core source model.
"""

from app.modules.asset_discovery.enums import DiscoverySourceType
from app.modules.asset_discovery.errors import InvalidDiscoverySourceConfigError

MAX_LOCATION_LENGTH = 1024


def _validate_repository_config(config: dict) -> None:
    location = config.get("location")
    if not isinstance(location, str) or not location.strip():
        raise InvalidDiscoverySourceConfigError(
            "config.location is required and must be a non-empty string "
            "for source type 'repository'"
        )
    if len(location) > MAX_LOCATION_LENGTH:
        raise InvalidDiscoverySourceConfigError(
            f"config.location must be at most {MAX_LOCATION_LENGTH} characters"
        )
    if "\x00" in location:
        raise InvalidDiscoverySourceConfigError("config.location must not contain NUL characters")


_CONFIG_VALIDATORS = {
    DiscoverySourceType.REPOSITORY: _validate_repository_config,
}


def validate_source_config(source_type: DiscoverySourceType | str, config: dict) -> None:
    """Raise InvalidDiscoverySourceConfigError when config breaks the type's rules."""
    validator = _CONFIG_VALIDATORS.get(DiscoverySourceType(source_type))
    if validator is not None:
        validator(config)
