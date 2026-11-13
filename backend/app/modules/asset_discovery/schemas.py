from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from app.modules.asset_discovery.enums import (
    AssetType,
    DiscoveredAssetStatus,
    DiscoveryRunStatus,
    DiscoverySourceStatus,
    DiscoverySourceType,
)
from app.modules.model_inventory.schemas.model import validate_metadata

MAX_NAME = 255


class DiscoverySourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_NAME)
    source_type: DiscoverySourceType
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("config")
    @classmethod
    def _check_config(cls, value: dict[str, Any]) -> dict[str, Any]:
        # size cap + secret-like key rejection (never store plaintext credentials)
        validate_metadata(value)
        return value


class DiscoverySourceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME)
    config: dict[str, Any] | None = None
    status: DiscoverySourceStatus | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("config")
    @classmethod
    def _check_config(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None:
            validate_metadata(value)
        return value


class DiscoverySourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    name: str
    source_type: DiscoverySourceType
    config: dict[str, Any]
    status: DiscoverySourceStatus
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", "archived_at", mode="before")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class DiscoverySourceListResponse(BaseModel):
    items: list[DiscoverySourceResponse]
    page: int
    page_size: int
    total: int
    total_pages: int


class DiscoverySourceFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)
    source_type: DiscoverySourceType | None = None
    status: DiscoverySourceStatus | None = None
    search: str | None = Field(default=None, max_length=255)
    include_archived: bool = False
    sort_by: str = "updated_at"
    sort_order: str = "desc"


class DiscoveryRunCreate(BaseModel):
    """Create-run request body.

    A run is always created PENDING for the source in the path; the body
    carries no fields yet exists so unexpected fields (e.g. attempts to
    force an initial status) are rejected.
    """

    model_config = ConfigDict(extra="forbid")


class DiscoveryRunExecuteRequest(BaseModel):
    """Execute-run request body.

    Execution takes no parameters; the empty body exists so unexpected
    fields (e.g. attempts to inject scan options) are rejected.
    """

    model_config = ConfigDict(extra="forbid")


class DiscoveryRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    source_id: UUID
    status: DiscoveryRunStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    assets_detected: int
    assets_created: int
    assets_updated: int
    assets_unchanged: int
    error_count: int
    error_summary: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("started_at", "completed_at", "created_at", "updated_at", mode="before")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class DiscoveryRunListResponse(BaseModel):
    items: list[DiscoveryRunResponse]
    page: int
    page_size: int
    total: int
    total_pages: int


class DiscoveryRunFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)
    source_id: UUID | None = None
    status: DiscoveryRunStatus | None = None
    started_from: datetime | None = None
    started_to: datetime | None = None
    sort_by: str = "started_at"
    sort_order: str = "desc"

    @field_validator("started_from", "started_to", mode="after")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        # tz-aware bounds only: naive values cannot be compared against
        # the stored timestamps without risking a database error
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class DiscoveryObservationResponse(BaseModel):
    """One raw observation detected during execution.

    An intermediate discovery result, not a persisted DiscoveredAsset;
    ``file_path`` is source-relative and file contents are never returned.
    """

    model_config = ConfigDict(extra="forbid")

    observation_type: str
    name: str
    provider: str | None = None
    file_path: str
    line_number: int | None = None
    detection_rule: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class DiscoveryExecutionIssueResponse(BaseModel):
    """A recoverable problem encountered while scanning."""

    model_config = ConfigDict(extra="forbid")

    file_path: str | None = None
    reason: str
    message: str


class DiscoveryExecutionSourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    source_type: DiscoverySourceType


class DiscoveryExecutionResponse(BaseModel):
    """Result of executing a discovery run against its source."""

    model_config = ConfigDict(extra="forbid")

    source: DiscoveryExecutionSourceResponse
    run: DiscoveryRunResponse
    status: DiscoveryRunStatus
    connector: str
    files_scanned: int = Field(ge=0)
    observation_count: int = Field(ge=0)
    observations: list[DiscoveryObservationResponse]
    errors: list[DiscoveryExecutionIssueResponse]


class DiscoveredAssetResponse(BaseModel):
    """One persisted discovered asset — an observation, not a canonical entity.

    Exposes catalog state only: never file contents, raw repository
    data or credentials.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    source_id: UUID
    last_seen_run_id: UUID | None = None
    asset_type: AssetType
    name: str
    normalized_identity: str
    status: DiscoveredAssetStatus
    metadata: dict[str, Any] = Field(
        default_factory=dict, validation_alias=AliasChoices("metadata_", "metadata")
    )
    first_seen_at: datetime
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime

    @field_validator("first_seen_at", "last_seen_at", "created_at", "updated_at", mode="before")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class DiscoveredAssetListResponse(BaseModel):
    items: list[DiscoveredAssetResponse]
    page: int
    page_size: int
    total: int
    total_pages: int


class DiscoveredAssetFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)
    asset_type: AssetType | None = None
    source_id: UUID | None = None
    status: DiscoveredAssetStatus | None = None
    search: str | None = Field(default=None, max_length=255)
    sort_by: str = "last_seen_at"
    sort_order: str = "desc"
