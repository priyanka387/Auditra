from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from app.modules.model_inventory.domain import DeploymentStatus
from app.modules.model_inventory.schemas.model import validate_metadata

DEPLOYMENT_ENVIRONMENTS: set[str] = {"development", "staging", "production"}
DEPLOYMENT_KINDS: set[str] = {
    "online_inference",
    "batch",
    "embedded",
    "edge",
    "scheduled",
    "other",
}
DEPLOYMENT_SOURCES: set[str] = {"manual", "api", "connector", "discovery", "system"}
DEPLOYMENT_STATUS_SOURCES: set[str] = set(DEPLOYMENT_SOURCES)


class DeploymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    environment: str
    deployment_kind: str
    target_type: str = Field(min_length=1, max_length=64)
    target_name: str | None = Field(default=None, max_length=255)
    region: str | None = Field(default=None, max_length=128)
    cluster_name: str | None = Field(default=None, max_length=255)
    namespace: str | None = Field(default=None, max_length=255)
    runtime: str | None = Field(default=None, max_length=128)
    serving_framework: str | None = Field(default=None, max_length=128)
    image_uri: str | None = None
    desired_replicas: int | None = Field(default=None, ge=0)
    observed_replicas: int | None = Field(default=None, ge=0)
    configuration: dict[str, Any] = {}
    metadata: dict[str, Any] = {}
    source: str = "manual"
    source_reference: str | None = Field(default=None, max_length=255)

    @field_validator(
        "name",
        "environment",
        "deployment_kind",
        "target_type",
        "target_name",
        "region",
        "cluster_name",
        "namespace",
        "runtime",
        "serving_framework",
        "image_uri",
        "source",
        "source_reference",
        mode="before",
    )
    @classmethod
    def _strip_strings(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("environment")
    @classmethod
    def _check_environment(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in DEPLOYMENT_ENVIRONMENTS:
            raise ValueError(f"environment must be one of {sorted(DEPLOYMENT_ENVIRONMENTS)}")
        return normalized

    @field_validator("deployment_kind")
    @classmethod
    def _check_deployment_kind(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in DEPLOYMENT_KINDS:
            raise ValueError(f"deployment_kind must be one of {sorted(DEPLOYMENT_KINDS)}")
        return normalized

    @field_validator("source")
    @classmethod
    def _check_source(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in DEPLOYMENT_SOURCES:
            raise ValueError(f"source must be one of {sorted(DEPLOYMENT_SOURCES)}")
        return normalized

    @field_validator("target_type", "runtime", "serving_framework")
    @classmethod
    def _lowercase_serving_fields(cls, value: str | None) -> str | None:
        return value.strip().lower() if value is not None else value

    @field_validator("metadata", "configuration")
    @classmethod
    def _check_json_fields(cls, value: dict[str, Any]) -> dict[str, Any]:
        validate_metadata(value)
        return value


class DeploymentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=128)
    target_name: str | None = Field(default=None, max_length=255)
    region: str | None = Field(default=None, max_length=128)
    cluster_name: str | None = Field(default=None, max_length=255)
    namespace: str | None = Field(default=None, max_length=255)
    runtime: str | None = Field(default=None, max_length=128)
    serving_framework: str | None = Field(default=None, max_length=128)
    image_uri: str | None = None
    desired_replicas: int | None = Field(default=None, ge=0)
    observed_replicas: int | None = Field(default=None, ge=0)
    configuration: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    source_reference: str | None = Field(default=None, max_length=255)
    last_seen_at: datetime | None = None

    @field_validator(
        "name",
        "target_name",
        "region",
        "cluster_name",
        "namespace",
        "runtime",
        "serving_framework",
        "image_uri",
        "source_reference",
        mode="before",
    )
    @classmethod
    def _strip_strings(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("runtime", "serving_framework")
    @classmethod
    def _lowercase_serving_fields(cls, value: str | None) -> str | None:
        return value.strip().lower() if value is not None else value

    @field_validator("metadata", "configuration")
    @classmethod
    def _check_json_fields(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None:
            validate_metadata(value)
        return value


class DeploymentTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: DeploymentStatus
    reason: str | None = Field(default=None, max_length=1000)
    observed_at: datetime | None = None

    @field_validator("reason", mode="before")
    @classmethod
    def _strip_reason(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class DeploymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    model_version_id: UUID
    name: str
    environment: str
    deployment_kind: str
    status: DeploymentStatus
    status_source: str
    target_type: str
    target_name: str | None
    region: str | None
    cluster_name: str | None
    namespace: str | None
    runtime: str | None
    serving_framework: str | None
    image_uri: str | None
    desired_replicas: int | None
    observed_replicas: int | None
    configuration: dict
    metadata: dict = Field(validation_alias=AliasChoices("metadata_", "metadata"))
    source: str
    source_reference: str | None
    deployed_at: datetime | None
    last_seen_at: datetime | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime
    created_by: str | None
    updated_by: str | None
    version: int = Field(validation_alias=AliasChoices("version", "record_version"))


class DeploymentListResponse(BaseModel):
    items: list[DeploymentResponse]
    page: int
    page_size: int
    total: int
    total_pages: int
