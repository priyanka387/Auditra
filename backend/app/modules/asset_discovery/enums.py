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


class DiscoveryRunStatus(str, Enum):
    """Lifecycle of a single execution against a discovery source.

    States and their transitions follow the master Asset Discovery
    specification: a run is created PENDING, started RUNNING, and ends
    COMPLETED, FAILED or PARTIAL. Terminal states are final.
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"
