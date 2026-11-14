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


class AssetType(str, Enum):
    """What a discovered asset represents.

    Initial types follow the master Asset Discovery specification. The
    enum (and its database check constraint) extend with additional
    types later without changing the catalog model; classification
    itself is owned by Asset Classification & Metadata (Sub-feature 5).
    """

    MODEL = "MODEL"
    MODEL_REFERENCE = "MODEL_REFERENCE"
    APPLICATION = "APPLICATION"
    AGENT = "AGENT"
    WORKFLOW = "WORKFLOW"
    DATASET = "DATASET"
    TOOL = "TOOL"
    DEPLOYMENT = "DEPLOYMENT"
    ENDPOINT = "ENDPOINT"
    AI_SERVICE = "AI_SERVICE"


class DiscoveredAssetStatus(str, Enum):
    """Lifecycle state of a catalog asset (Asset Lifecycle, Sub-feature 8).

    - ACTIVE: observed in the latest applicable completed discovery run.
    - STALE: missed at least the configured number of completed runs of
      its source, but has not yet reached the disappearance threshold.
    - DISAPPEARED: absent from enough completed runs to meet the
      disappearance threshold. It is still the same catalog asset: a
      later observation reactivates it to ACTIVE without creating a
      duplicate (deduplication, Sub-feature 6, owns identity).
    - IGNORED: intentionally excluded from lifecycle processing. Sticky:
      neither discovery nor lifecycle evaluation ever overwrites it (no
      flow produces it yet — it is reserved vocabulary, mirroring how
      ``ReconciliationStatus.IGNORED`` was introduced).

    New observations create assets as ACTIVE and the catalog never
    performs lifecycle transitions itself: transitions are owned by
    ``asset_lifecycle`` (deterministic, source-scoped, run-count based).
    """

    ACTIVE = "ACTIVE"
    STALE = "STALE"
    DISAPPEARED = "DISAPPEARED"
    IGNORED = "IGNORED"


class ObservationOutcome(str, Enum):
    """Result of recording one discovery observation in the catalog."""

    CREATED = "CREATED"  # new catalog asset
    UPDATED = "UPDATED"  # existing asset gained new metadata
    UNCHANGED = "UNCHANGED"  # existing asset re-observed, timestamps refreshed
    SKIPPED = "SKIPPED"  # deliberately not persisted (e.g. secret-shaped)


class ReconciliationStatus(str, Enum):
    """Outcome of reconciling a discovered asset to a canonical entity.

    Vocabulary follows the master Asset Discovery specification (§8):
    UNMATCHED (default — no canonical entity found), MATCHED (one
    canonical entity confidently identified), AMBIGUOUS (several
    candidates — never linked automatically) and IGNORED (reserved for
    explicit future decisions; no flow produces it today). Lifecycle
    states (STALE, DISAPPEARED, ...) are unrelated and belong to Asset
    Lifecycle (Sub-feature 8).
    """

    UNMATCHED = "UNMATCHED"
    MATCHED = "MATCHED"
    AMBIGUOUS = "AMBIGUOUS"
    IGNORED = "IGNORED"
