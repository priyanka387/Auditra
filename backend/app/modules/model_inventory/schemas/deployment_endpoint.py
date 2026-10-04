from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from app.modules.model_inventory.domain.errors import (
    InvalidAuthReferenceError,
    InvalidEndpointUrlError,
)
from app.modules.model_inventory.schemas.model import validate_metadata

ENDPOINT_TYPES: set[str] = {"inference", "health"}
ENDPOINT_PROTOCOLS: set[str] = {"http", "https", "grpc", "grpcs"}
ENDPOINT_STATUSES: set[str] = {"active", "inactive"}
HEALTH_STATUSES: set[str] = {"unknown", "healthy", "unhealthy"}
AUTH_TYPES: set[str] = {"none", "api_key", "bearer", "basic", "mtls", "custom", "external_secret"}
MAX_ENDPOINT_URL_LENGTH = 2048


def validate_endpoint_url(protocol: str, url: str) -> str:
    if not url or len(url) > MAX_ENDPOINT_URL_LENGTH:
        raise InvalidEndpointUrlError(
            f"endpoint url must be 1-{MAX_ENDPOINT_URL_LENGTH} characters"
        )
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise InvalidEndpointUrlError(f"endpoint url is not parseable: {exc}") from None
    if parts.scheme != protocol:
        raise InvalidEndpointUrlError(f"endpoint url scheme must match protocol '{protocol}'")
    if not parts.netloc:
        raise InvalidEndpointUrlError("endpoint url must include a host")
    if parts.username is not None or parts.password is not None:
        raise InvalidEndpointUrlError("endpoint url must not contain embedded credentials")
    if parts.fragment:
        raise InvalidEndpointUrlError("endpoint url must not contain a fragment")
    return url


def validate_auth_reference(auth_type: str, auth_reference: str | None) -> None:
    if auth_reference is None:
        return
    if auth_type == "none":
        raise InvalidAuthReferenceError(
            "auth_reference must not be set when auth_type is 'none'"
        )
    if "://" not in auth_reference:
        raise InvalidAuthReferenceError(
            "auth_reference must be a reference URI such as secret://..., never a raw secret"
        )


class DeploymentEndpointCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    endpoint_type: str
    protocol: str
    url: str = Field(min_length=1, max_length=MAX_ENDPOINT_URL_LENGTH)
    route: str | None = Field(default=None, max_length=512)
    auth_type: str
    auth_reference: str | None = Field(default=None, max_length=255)
    is_primary: bool = False
    status: str = "active"
    health_status: str = "unknown"
    metadata: dict[str, Any] = {}

    @field_validator(
        "name",
        "endpoint_type",
        "protocol",
        "url",
        "route",
        "auth_type",
        "auth_reference",
        mode="before",
    )
    @classmethod
    def _strip_strings(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("endpoint_type")
    @classmethod
    def _check_endpoint_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ENDPOINT_TYPES:
            raise ValueError(f"endpoint_type must be one of {sorted(ENDPOINT_TYPES)}")
        return normalized

    @field_validator("protocol")
    @classmethod
    def _check_protocol(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ENDPOINT_PROTOCOLS:
            raise ValueError(f"protocol must be one of {sorted(ENDPOINT_PROTOCOLS)}")
        return normalized

    @field_validator("auth_type")
    @classmethod
    def _check_auth_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in AUTH_TYPES:
            raise ValueError(f"auth_type must be one of {sorted(AUTH_TYPES)}")
        return normalized

    @field_validator("status")
    @classmethod
    def _check_status(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ENDPOINT_STATUSES:
            raise ValueError(f"status must be one of {sorted(ENDPOINT_STATUSES)}")
        return normalized

    @field_validator("health_status")
    @classmethod
    def _check_health_status(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in HEALTH_STATUSES:
            raise ValueError(f"health_status must be one of {sorted(HEALTH_STATUSES)}")
        return normalized

    @field_validator("metadata")
    @classmethod
    def _check_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        validate_metadata(value)
        return value


class DeploymentEndpointUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=128)
    protocol: str | None = None
    url: str | None = Field(default=None, min_length=1, max_length=MAX_ENDPOINT_URL_LENGTH)
    route: str | None = Field(default=None, max_length=512)
    auth_type: str | None = None
    auth_reference: str | None = Field(default=None, max_length=255)
    is_primary: bool | None = None
    status: str | None = None
    health_status: str | None = None
    last_health_check_at: datetime | None = None
    metadata: dict[str, Any] | None = None

    @field_validator(
        "name",
        "protocol",
        "url",
        "route",
        "auth_type",
        "auth_reference",
        mode="before",
    )
    @classmethod
    def _strip_strings(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("protocol")
    @classmethod
    def _check_protocol(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in ENDPOINT_PROTOCOLS:
            raise ValueError(f"protocol must be one of {sorted(ENDPOINT_PROTOCOLS)}")
        return normalized

    @field_validator("auth_type")
    @classmethod
    def _check_auth_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in AUTH_TYPES:
            raise ValueError(f"auth_type must be one of {sorted(AUTH_TYPES)}")
        return normalized

    @field_validator("status")
    @classmethod
    def _check_status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in ENDPOINT_STATUSES:
            raise ValueError(f"status must be one of {sorted(ENDPOINT_STATUSES)}")
        return normalized

    @field_validator("health_status")
    @classmethod
    def _check_health_status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in HEALTH_STATUSES:
            raise ValueError(f"health_status must be one of {sorted(HEALTH_STATUSES)}")
        return normalized

    @field_validator("metadata")
    @classmethod
    def _check_metadata(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None:
            validate_metadata(value)
        return value


class DeploymentEndpointResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    deployment_id: UUID
    name: str
    endpoint_type: str
    protocol: str
    url: str
    route: str | None
    auth_type: str
    auth_reference: str | None
    is_primary: bool
    status: str
    health_status: str
    last_health_check_at: datetime | None
    metadata: dict = Field(validation_alias=AliasChoices("metadata_", "metadata"))
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime
    created_by: str | None
    updated_by: str | None
    version: int = Field(validation_alias=AliasChoices("version", "record_version"))


class DeploymentEndpointListResponse(BaseModel):
    items: list[DeploymentEndpointResponse]
    page: int
    page_size: int
    total: int
    total_pages: int
