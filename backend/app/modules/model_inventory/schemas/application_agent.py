import re
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from app.modules.model_inventory.schemas.model import validate_metadata

ENTITY_STATUSES: set[str] = {"ACTIVE", "INACTIVE", "ARCHIVED"}
CREATE_ENTITY_STATUSES: set[str] = {"ACTIVE", "INACTIVE"}
ASSOCIATION_STATUSES: set[str] = {"ACTIVE", "DISABLED"}
ASSOCIATION_ROLES: list[str] = [
    "PRIMARY",
    "FALLBACK",
    "EMBEDDING",
    "RERANKER",
    "VISION",
    "MULTIMODAL",
    "MODERATION",
    "OTHER",
]
SOURCES: set[str] = {"manual", "api", "connector", "discovery", "system"}

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")


def normalize_slug(value: str) -> str:
    slug = _NON_SLUG_RE.sub("-", value.strip().lower()).strip("-")
    if not slug:
        raise ValueError("slug must contain at least one alphanumeric character")
    if len(slug) > 128:
        raise ValueError("slug must be at most 128 characters after normalization")
    return slug


def _validate_tags(tags: list[str]) -> list[str]:
    cleaned: list[str] = []
    for tag in tags:
        stripped = tag.strip()
        if not stripped:
            raise ValueError("tags must not contain empty entries")
        if len(stripped) > 128:
            raise ValueError("each tag must be at most 128 characters")
        cleaned.append(stripped)
    return cleaned


class ApplicationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    slug: str
    display_name: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    application_type: str | None = Field(default=None, max_length=64)
    owner_name: str | None = Field(default=None, max_length=255)
    team_name: str | None = Field(default=None, max_length=255)
    status: str = "ACTIVE"
    source: str = "manual"
    tags: list[str] = Field(default_factory=list, max_length=50)
    metadata: dict[str, Any] = {}

    @field_validator(
        "name",
        "display_name",
        "description",
        "application_type",
        "owner_name",
        "team_name",
        mode="before",
    )
    @classmethod
    def _strip_strings(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("slug", mode="before")
    @classmethod
    def _normalize_slug(cls, value: Any) -> Any:
        return normalize_slug(value) if isinstance(value, str) else value

    @field_validator("status")
    @classmethod
    def _check_status(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in CREATE_ENTITY_STATUSES:
            raise ValueError(f"status must be one of {sorted(CREATE_ENTITY_STATUSES)}")
        return normalized

    @field_validator("source")
    @classmethod
    def _check_source(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in SOURCES:
            raise ValueError(f"source must be one of {sorted(SOURCES)}")
        return normalized

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, value: list[str]) -> list[str]:
        return _validate_tags(value)

    @field_validator("metadata")
    @classmethod
    def _check_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        validate_metadata(value)
        return value


class ApplicationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = None
    display_name: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    application_type: str | None = Field(default=None, max_length=64)
    owner_name: str | None = Field(default=None, max_length=255)
    team_name: str | None = Field(default=None, max_length=255)
    status: str | None = None
    source: str | None = None
    tags: list[str] | None = Field(default=None, max_length=50)
    metadata: dict[str, Any] | None = None

    @field_validator(
        "name",
        "display_name",
        "description",
        "application_type",
        "owner_name",
        "team_name",
        mode="before",
    )
    @classmethod
    def _strip_strings(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("slug", mode="before")
    @classmethod
    def _normalize_slug(cls, value: Any) -> Any:
        if value is None:
            return None
        return normalize_slug(value) if isinstance(value, str) else value

    @field_validator("status")
    @classmethod
    def _check_status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if normalized not in ENTITY_STATUSES:
            raise ValueError(f"status must be one of {sorted(ENTITY_STATUSES)}")
        return normalized

    @field_validator("source")
    @classmethod
    def _check_source(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in SOURCES:
            raise ValueError(f"source must be one of {sorted(SOURCES)}")
        return normalized

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _validate_tags(value)

    @field_validator("metadata")
    @classmethod
    def _check_metadata(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None:
            validate_metadata(value)
        return value


class ApplicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    name: str
    slug: str
    display_name: str | None
    description: str | None
    application_type: str | None
    owner_name: str | None
    team_name: str | None
    status: str
    source: str
    tags: list[str] | None
    metadata: dict = Field(validation_alias=AliasChoices("metadata_", "metadata"))
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime
    created_by: str | None
    updated_by: str | None


class ApplicationListResponse(BaseModel):
    items: list[ApplicationResponse]
    page: int
    page_size: int
    total: int
    total_pages: int


class AgentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    slug: str
    display_name: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    agent_type: str | None = Field(default=None, max_length=64)
    framework: str | None = Field(default=None, max_length=64)
    framework_version: str | None = Field(default=None, max_length=64)
    runtime_identifier: str | None = Field(default=None, max_length=255)
    owner_name: str | None = Field(default=None, max_length=255)
    team_name: str | None = Field(default=None, max_length=255)
    status: str = "ACTIVE"
    source: str = "manual"
    capabilities: dict[str, Any] | None = None
    tags: list[str] = Field(default_factory=list, max_length=50)
    metadata: dict[str, Any] = {}

    @field_validator(
        "name",
        "display_name",
        "description",
        "agent_type",
        "framework",
        "framework_version",
        "runtime_identifier",
        "owner_name",
        "team_name",
        mode="before",
    )
    @classmethod
    def _strip_strings(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("slug", mode="before")
    @classmethod
    def _normalize_slug(cls, value: Any) -> Any:
        return normalize_slug(value) if isinstance(value, str) else value

    @field_validator("framework", "framework_version", "agent_type")
    @classmethod
    def _lowercase_fields(cls, value: str | None) -> str | None:
        return value.lower() if value is not None else value

    @field_validator("status")
    @classmethod
    def _check_status(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in CREATE_ENTITY_STATUSES:
            raise ValueError(f"status must be one of {sorted(CREATE_ENTITY_STATUSES)}")
        return normalized

    @field_validator("source")
    @classmethod
    def _check_source(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in SOURCES:
            raise ValueError(f"source must be one of {sorted(SOURCES)}")
        return normalized

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, value: list[str]) -> list[str]:
        return _validate_tags(value)

    @field_validator("metadata")
    @classmethod
    def _check_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        validate_metadata(value)
        return value


class AgentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = None
    display_name: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    agent_type: str | None = Field(default=None, max_length=64)
    framework: str | None = Field(default=None, max_length=64)
    framework_version: str | None = Field(default=None, max_length=64)
    runtime_identifier: str | None = Field(default=None, max_length=255)
    owner_name: str | None = Field(default=None, max_length=255)
    team_name: str | None = Field(default=None, max_length=255)
    status: str | None = None
    source: str | None = None
    capabilities: dict[str, Any] | None = None
    tags: list[str] | None = Field(default=None, max_length=50)
    metadata: dict[str, Any] | None = None

    @field_validator(
        "name",
        "display_name",
        "description",
        "agent_type",
        "framework",
        "framework_version",
        "runtime_identifier",
        "owner_name",
        "team_name",
        mode="before",
    )
    @classmethod
    def _strip_strings(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("slug", mode="before")
    @classmethod
    def _normalize_slug(cls, value: Any) -> Any:
        if value is None:
            return None
        return normalize_slug(value) if isinstance(value, str) else value

    @field_validator("framework", "framework_version", "agent_type")
    @classmethod
    def _lowercase_fields(cls, value: str | None) -> str | None:
        return value.lower() if value is not None else value

    @field_validator("status")
    @classmethod
    def _check_status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if normalized not in ENTITY_STATUSES:
            raise ValueError(f"status must be one of {sorted(ENTITY_STATUSES)}")
        return normalized

    @field_validator("source")
    @classmethod
    def _check_source(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in SOURCES:
            raise ValueError(f"source must be one of {sorted(SOURCES)}")
        return normalized

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _validate_tags(value)

    @field_validator("metadata")
    @classmethod
    def _check_metadata(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None:
            validate_metadata(value)
        return value


class AgentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    application_id: UUID
    name: str
    slug: str
    display_name: str | None
    description: str | None
    agent_type: str | None
    framework: str | None
    framework_version: str | None
    runtime_identifier: str | None
    owner_name: str | None
    team_name: str | None
    status: str
    source: str
    capabilities: dict | None
    tags: list[str] | None
    metadata: dict = Field(validation_alias=AliasChoices("metadata_", "metadata"))
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime
    created_by: str | None
    updated_by: str | None


class AgentListResponse(BaseModel):
    items: list[AgentResponse]
    page: int
    page_size: int
    total: int
    total_pages: int


class AgentModelAssociationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: UUID
    model_version_id: UUID | None = None
    role: str
    selection_priority: int = Field(default=1, ge=1)
    status: str = "ACTIVE"
    source: str = "manual"
    configuration: dict[str, Any] = {}
    metadata: dict[str, Any] = {}

    @field_validator("role")
    @classmethod
    def _check_role(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in ASSOCIATION_ROLES:
            raise ValueError(f"role must be one of {ASSOCIATION_ROLES}")
        return normalized

    @field_validator("status")
    @classmethod
    def _check_status(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in ASSOCIATION_STATUSES:
            raise ValueError(f"status must be one of {sorted(ASSOCIATION_STATUSES)}")
        return normalized

    @field_validator("source")
    @classmethod
    def _check_source(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in SOURCES:
            raise ValueError(f"source must be one of {sorted(SOURCES)}")
        return normalized

    @field_validator("configuration", "metadata")
    @classmethod
    def _check_json_fields(cls, value: dict[str, Any]) -> dict[str, Any]:
        validate_metadata(value)
        return value


class AgentModelAssociationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_version_id: UUID | None = None
    role: str | None = None
    selection_priority: int | None = Field(default=None, ge=1)
    status: str | None = None
    configuration: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None

    @field_validator("role")
    @classmethod
    def _check_role(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if normalized not in ASSOCIATION_ROLES:
            raise ValueError(f"role must be one of {ASSOCIATION_ROLES}")
        return normalized

    @field_validator("status")
    @classmethod
    def _check_status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if normalized not in ASSOCIATION_STATUSES:
            raise ValueError(f"status must be one of {sorted(ASSOCIATION_STATUSES)}")
        return normalized

    @field_validator("configuration", "metadata")
    @classmethod
    def _check_json_fields(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None:
            validate_metadata(value)
        return value


class AgentModelAssociationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    agent_id: UUID
    model_id: UUID
    model_version_id: UUID | None
    role: str
    selection_priority: int
    status: str
    source: str
    configuration: dict
    metadata: dict = Field(validation_alias=AliasChoices("metadata_", "metadata"))
    disabled_at: datetime | None
    created_at: datetime
    updated_at: datetime
    created_by: str | None
    updated_by: str | None


class AgentModelAssociationListResponse(BaseModel):
    items: list[AgentModelAssociationResponse]
    page: int
    page_size: int
    total: int
    total_pages: int


class ModelAgentLinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    association_id: UUID
    agent_id: UUID
    agent_name: str
    agent_slug: str
    agent_status: str
    application_id: UUID
    application_name: str
    application_slug: str
    model_id: UUID
    model_version_id: UUID | None
    role: str
    selection_priority: int
    status: str
    source: str
    configuration: dict
    metadata: dict
    created_at: datetime
    updated_at: datetime


class ModelAgentLinkListResponse(BaseModel):
    items: list[ModelAgentLinkResponse]
    page: int
    page_size: int
    total: int
    total_pages: int
