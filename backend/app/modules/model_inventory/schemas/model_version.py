from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from app.modules.model_inventory.domain import VERSION_IDENTITY_TYPES, VersionLifecycleState
from app.modules.model_inventory.schemas.model import validate_metadata

VERSION_SOURCE_TYPES: set[str] = {"manual", "import"}


class ModelVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity_type: str
    version_label: str = Field(min_length=1, max_length=255)
    native_version_id: str | None = Field(default=None, min_length=1, max_length=512)
    display_name: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    source_type: str = "manual"
    source_reference: str | None = Field(default=None, max_length=512)
    metadata: dict[str, Any] = {}

    @field_validator(
        "identity_type",
        "version_label",
        "native_version_id",
        "display_name",
        "description",
        "source_type",
        "source_reference",
        mode="before",
    )
    @classmethod
    def _strip_strings(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("identity_type")
    @classmethod
    def _check_identity_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in VERSION_IDENTITY_TYPES:
            raise ValueError(f"identity_type must be one of {sorted(VERSION_IDENTITY_TYPES)}")
        return normalized

    @field_validator("source_type")
    @classmethod
    def _check_source_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in VERSION_SOURCE_TYPES:
            raise ValueError(f"source_type must be one of {sorted(VERSION_SOURCE_TYPES)}")
        return normalized

    @field_validator("metadata")
    @classmethod
    def _check_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        validate_metadata(value)
        return value


class ModelVersionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: UUID | None = None
    identity_type: str | None = None
    version_label: str | None = Field(default=None, min_length=1, max_length=255)
    native_version_id: str | None = Field(default=None, min_length=1, max_length=512)
    canonical_version_key: str | None = Field(default=None, min_length=1, max_length=1024)
    display_name: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    metadata: dict[str, Any] | None = None
    source_reference: str | None = Field(default=None, max_length=512)

    @field_validator(
        "identity_type",
        "version_label",
        "native_version_id",
        "canonical_version_key",
        "display_name",
        "description",
        "source_reference",
        mode="before",
    )
    @classmethod
    def _strip_strings(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("identity_type")
    @classmethod
    def _check_identity_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in VERSION_IDENTITY_TYPES:
            raise ValueError(f"identity_type must be one of {sorted(VERSION_IDENTITY_TYPES)}")
        return normalized

    @field_validator("metadata")
    @classmethod
    def _check_metadata(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None:
            validate_metadata(value)
        return value


class ModelVersionLifecycleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_state: VersionLifecycleState


class ModelVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    model_id: UUID
    identity_type: str
    version_label: str
    native_version_id: str | None
    canonical_version_key: str
    display_name: str | None
    description: str | None
    lifecycle_state: VersionLifecycleState
    source_type: str
    source_reference: str | None
    metadata: dict = Field(validation_alias=AliasChoices("metadata_", "metadata"))
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    created_by: str | None
    updated_by: str | None
    version: int = Field(validation_alias=AliasChoices("version", "record_version"))


class ModelVersionListResponse(BaseModel):
    items: list[ModelVersionResponse]
    page: int
    page_size: int
    total: int
    total_pages: int
