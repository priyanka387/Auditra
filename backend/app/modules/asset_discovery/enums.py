from enum import Enum


class DiscoverySourceType(str, Enum):
    """Where Auditra looks for assets.

    V1 supports repository/codebase sources only; the enum (and the
    config-validation dispatch table) extend to GitHub, Kubernetes,
    cloud accounts, model registries, etc. without touching the core
    source model.
    """

    REPOSITORY = "repository"


class DiscoverySourceStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
