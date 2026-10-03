import json
import re
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from app.modules.model_inventory.domain import INITIAL_STATES, LifecycleState

SOURCE_TYPES: set[str] = {"MANUAL", "SDK", "API", "IMPORT"}
HOSTING_MODES: set[str] = {
    "CLOUD_API",
    "SAAS_API",
    "SELF_HOSTED",
    "LOCAL",
    "ON_PREMISE",
    "EDGE",
    "EMBEDDED",
    "CUSTOM",
}
SECRET_KEY_RE = re.compile(r"(?i)(api[_-]?key|secret|token|password|credential|private[_-]?key)")


def parse_tags(raw: list[str]) -> list[tuple[str, str]]:
    parsed: list[tuple[str, str]] = []
    for entry in raw:
        stripped = entry.strip()
        if "=" in stripped:
            key, value = stripped.split("=", 1)
        else:
            key, value = stripped, ""
        key, value = key.strip(), value.strip()
        if not key:
            raise ValueError("tag key must not be empty")
        if len(key) > 128:
            raise ValueError("tag key must be at most 128 characters")
        if len(value) > 512:
            raise ValueError("tag value must be at most 512 characters")
        parsed.append((key, value))
    return parsed


def validate_metadata(md: dict) -> None:
    if len(json.dumps(md).encode("utf-8")) > 10_000:
        raise ValueError("metadata exceeds 10000 bytes when serialized")
    for key in md:
        if SECRET_KEY_RE.search(str(key)):
            raise ValueError(f"metadata key {key!r} looks like a secret")


class ProviderSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    name: str


class ModelTypeSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    name: str


class TagResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    value: str


class ModelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_slug: str = Field(min_length=1)
    model_type_slug: str
    name: str = Field(min_length=1, max_length=255)
    native_model_id: str = Field(min_length=1, max_length=512)
    description: str | None = Field(default=None, max_length=4000)
    owner_name: str | None = Field(default=None, max_length=255)
    owner_contact: str | None = Field(default=None, max_length=512)
    team_name: str | None = Field(default=None, max_length=255)
    hosting_mode: str | None = None
    runtime_hint: str | None = Field(default=None, max_length=64)
    lifecycle_state: LifecycleState = "REGISTERED"
    source_type: str = "MANUAL"
    source_reference: str | None = Field(default=None, max_length=512)
    tags: list[str] = Field(default_factory=list, max_length=50)
    metadata: dict[str, Any] = {}

    @field_validator(
        "provider_slug",
        "model_type_slug",
        "name",
        "native_model_id",
        "description",
        "owner_name",
        "owner_contact",
        "team_name",
        "hosting_mode",
        "runtime_hint",
        "lifecycle_state",
        "source_type",
        "source_reference",
        mode="before",
    )
    @classmethod
    def _strip_strings(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("source_type")
    @classmethod
    def _check_source_type(cls, value: str) -> str:
        if value not in SOURCE_TYPES:
            raise ValueError(f"source_type must be one of {sorted(SOURCE_TYPES)}")
        return value

    @field_validator("hosting_mode")
    @classmethod
    def _check_hosting_mode(cls, value: str | None) -> str | None:
        if value is not None and value not in HOSTING_MODES:
            raise ValueError(f"hosting_mode must be one of {sorted(HOSTING_MODES)}")
        return value

    @field_validator("lifecycle_state")
    @classmethod
    def _check_initial_state(cls, value: str) -> str:
        if value not in INITIAL_STATES:
            raise ValueError(f"lifecycle_state must be one of {sorted(INITIAL_STATES)}")
        return value

    @field_validator("metadata")
    @classmethod
    def _check_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        validate_metadata(value)
        return value

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, value: list[str]) -> list[str]:
        parse_tags(value)
        return value


class ModelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_slug: str | None = Field(default=None, min_length=1)
    native_model_id: str | None = Field(default=None, min_length=1, max_length=512)
    model_type_slug: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    owner_name: str | None = Field(default=None, max_length=255)
    owner_contact: str | None = Field(default=None, max_length=512)
    team_name: str | None = Field(default=None, max_length=255)
    hosting_mode: str | None = None
    runtime_hint: str | None = Field(default=None, max_length=64)
    lifecycle_state: LifecycleState | None = None
    source_reference: str | None = Field(default=None, max_length=512)
    tags: list[str] | None = Field(default=None, max_length=50)
    metadata: dict[str, Any] | None = None

    @field_validator(
        "provider_slug",
        "native_model_id",
        "model_type_slug",
        "name",
        "description",
        "owner_name",
        "owner_contact",
        "team_name",
        "hosting_mode",
        "runtime_hint",
        "lifecycle_state",
        "source_reference",
        mode="before",
    )
    @classmethod
    def _strip_strings(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("hosting_mode")
    @classmethod
    def _check_hosting_mode(cls, value: str | None) -> str | None:
        if value is not None and value not in HOSTING_MODES:
            raise ValueError(f"hosting_mode must be one of {sorted(HOSTING_MODES)}")
        return value

    @field_validator("metadata")
    @classmethod
    def _check_metadata(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None:
            validate_metadata(value)
        return value

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is not None:
            parse_tags(value)
        return value


class ModelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    name: str
    native_model_id: str
    canonical_key: str
    description: str | None
    provider: ProviderSummary
    model_type: ModelTypeSummary
    hosting_mode: str | None
    runtime_hint: str | None
    lifecycle_state: LifecycleState
    source_type: str
    source_reference: str | None
    owner_name: str | None
    owner_contact: str | None
    team_name: str | None
    tags: list[TagResponse]
    metadata: dict = Field(validation_alias=AliasChoices("metadata_", "metadata"))
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    created_by: UUID | None
    updated_by: UUID | None
    version: int = Field(validation_alias=AliasChoices("version", "record_version"))


class ModelListResponse(BaseModel):
    items: list[ModelResponse]
    page: int
    page_size: int
    total: int
    total_pages: int
