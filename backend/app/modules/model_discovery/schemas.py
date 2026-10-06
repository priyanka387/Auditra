from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from app.modules.model_discovery.enums import DiscoverySourceType, DiscoveryStatus
from app.modules.model_inventory.schemas.model import validate_metadata

MAX_SOURCE_IDENTIFIER = 255
MAX_EXTERNAL_IDENTIFIER = 255
MAX_PROVIDER = 100
MAX_MODEL_IDENTIFIER = 512
MAX_MODEL_TYPE = 64
MAX_DISPLAY_NAME = 255


class DiscoveryCreate(BaseModel):
    """Common discovery observation input — manual API payload and adapter output share this contract."""

    model_config = ConfigDict(extra="forbid")

    source_type: DiscoverySourceType
    source_identifier: str | None = Field(default=None, max_length=MAX_SOURCE_IDENTIFIER)
    external_identifier: str | None = Field(default=None, max_length=MAX_EXTERNAL_IDENTIFIER)
    provider: str = Field(min_length=1, max_length=MAX_PROVIDER)
    model_identifier: str = Field(min_length=1, max_length=MAX_MODEL_IDENTIFIER)
    model_type: str | None = Field(default=None, max_length=MAX_MODEL_TYPE)
    display_name: str | None = Field(default=None, max_length=MAX_DISPLAY_NAME)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "source_identifier",
        "external_identifier",
        "provider",
        "model_identifier",
        "model_type",
        "display_name",
        mode="before",
    )
    @classmethod
    def _strip_strings(cls, value: Any) -> Any:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("metadata")
    @classmethod
    def _check_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        validate_metadata(value)
        return value


class DiscoveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_type: DiscoverySourceType
    source_identifier: str | None = None
    external_identifier: str | None = None
    provider: str
    model_identifier: str
    model_type: str | None = None
    display_name: str | None = None
    canonical_identity: str
    metadata: dict[str, Any] = Field(
        default_factory=dict, validation_alias=AliasChoices("metadata_", "metadata")
    )
    status: DiscoveryStatus
    matched_model_id: UUID | None = None
    observation_count: int
    first_seen_at: datetime
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime
    error_code: str | None = None
    error_message: str | None = None

    @field_validator("first_seen_at", "last_seen_at", "created_at", "updated_at", mode="before")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class DiscoveryListResponse(BaseModel):
    items: list[DiscoveryResponse]
    page: int
    page_size: int
    total: int
    total_pages: int


class DiscoveryFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)
    source_type: DiscoverySourceType | None = None
    source_identifier: str | None = None
    provider: str | None = None
    model_type: str | None = None
    status: DiscoveryStatus | None = None
    matched_model_id: UUID | None = None
    canonical_identity: str | None = None
    last_seen_from: datetime | None = None
    last_seen_to: datetime | None = None
    search: str | None = Field(default=None, max_length=255)
    sort_by: Literal[
        "last_seen_at", "first_seen_at", "observation_count", "created_at", "updated_at"
    ] = "last_seen_at"
    sort_order: Literal["asc", "desc"] = "desc"


class DiscoveryMatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DiscoveryIgnoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DiscoveryRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    model_type_slug: str | None = Field(default=None, min_length=1, max_length=MAX_MODEL_TYPE)

    @field_validator("name", "model_type_slug", mode="before")
    @classmethod
    def _strip_strings(cls, value: Any) -> Any:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value
