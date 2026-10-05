import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.modules.model_usage.enums import CostSource, TokenUsageSource, UsageSource, UsageStatus
from app.modules.model_usage.sanitization import (
    ERROR_CODE_MAX,
    ERROR_TYPE_MAX,
    sanitize_error_message,
    truncate,
)
from app.modules.model_inventory.schemas.model import validate_metadata

MAX_TAGS = 50
MAX_TAG_KEY = 128
MAX_TAG_VALUE = 512

_FINGERPRINT_FIELDS = (
    "event_id",
    "model_id",
    "model_version_id",
    "deployment_id",
    "application_id",
    "agent_id",
    "status",
    "started_at",
    "completed_at",
    "duration_ms",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "request_id",
    "trace_id",
    "provider_request_id",
    "source",
)


class UsageEventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=128)
    model_id: UUID
    model_version_id: UUID | None = None
    deployment_id: UUID | None = None
    application_id: UUID | None = None
    agent_id: UUID | None = None

    status: UsageStatus = UsageStatus.SUCCESS
    source: UsageSource

    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    token_usage_source: TokenUsageSource = TokenUsageSource.UNAVAILABLE

    estimated_cost: Decimal | None = Field(default=None, ge=0)
    cost_currency: str | None = Field(default=None, max_length=8)
    cost_source: CostSource = CostSource.UNAVAILABLE

    request_id: str | None = Field(default=None, max_length=128)
    trace_id: str | None = Field(default=None, max_length=128)
    parent_run_id: str | None = Field(default=None, max_length=128)
    provider_request_id: str | None = Field(default=None, max_length=255)

    environment: str | None = Field(default=None, max_length=64)
    region: str | None = Field(default=None, max_length=128)
    host: str | None = Field(default=None, max_length=255)
    runtime: str | None = Field(default=None, max_length=128)
    framework: str | None = Field(default=None, max_length=64)
    framework_version: str | None = Field(default=None, max_length=64)
    sdk_version: str | None = Field(default=None, max_length=64)
    operation_name: str | None = Field(default=None, max_length=255)

    error_type: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retry_count: int | None = Field(default=None, ge=0)
    provider_status_code: int | None = Field(default=None, ge=100, le=599)

    tags: dict[str, str] | None = None
    metadata: dict[str, Any] | None = None

    @field_validator("started_at", "completed_at")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return value

    @field_validator("error_type")
    @classmethod
    def _limit_error_type(cls, value: str | None) -> str | None:
        return truncate(value, ERROR_TYPE_MAX)

    @field_validator("error_code")
    @classmethod
    def _limit_error_code(cls, value: str | None) -> str | None:
        return truncate(value, ERROR_CODE_MAX)

    @field_validator("error_message")
    @classmethod
    def _sanitize_error_message(cls, value: str | None) -> str | None:
        return sanitize_error_message(value)

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        if len(value) > MAX_TAGS:
            raise ValueError(f"tags must have at most {MAX_TAGS} keys")
        for key, tag_value in value.items():
            if len(key) > MAX_TAG_KEY:
                raise ValueError(f"tag key must be at most {MAX_TAG_KEY} characters")
            if len(tag_value) > MAX_TAG_VALUE:
                raise ValueError(f"tag value must be at most {MAX_TAG_VALUE} characters")
        return value

    @field_validator("metadata")
    @classmethod
    def _check_metadata(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None:
            validate_metadata(value)
        return value

    @model_validator(mode="after")
    def _check_consistency(self) -> "UsageEventCreate":
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("completed_at must not be earlier than started_at")
        if self.status == UsageStatus.SUCCESS and (
            self.error_type is not None
            or self.error_code is not None
            or self.error_message is not None
        ):
            raise ValueError("error fields must not be set when status is 'success'")
        return self


class UsageEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_id: str
    model_id: UUID
    model_version_id: UUID | None = None
    deployment_id: UUID | None = None
    application_id: UUID | None = None
    agent_id: UUID | None = None
    status: UsageStatus
    source: UsageSource
    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    token_usage_source: TokenUsageSource
    estimated_cost: Decimal | None = None
    cost_currency: str | None = None
    cost_source: CostSource
    request_id: str | None = None
    trace_id: str | None = None
    parent_run_id: str | None = None
    provider_request_id: str | None = None
    environment: str | None = None
    region: str | None = None
    host: str | None = None
    runtime: str | None = None
    framework: str | None = None
    framework_version: str | None = None
    sdk_version: str | None = None
    operation_name: str | None = None
    error_type: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retry_count: int | None = None
    provider_status_code: int | None = None
    tags: dict[str, str] | None = None
    metadata: dict[str, Any] | None = Field(
        default=None, validation_alias=AliasChoices("metadata_", "metadata")
    )
    created_at: datetime


class UsageEventBatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[UsageEventCreate] = Field(min_length=1)


class BatchItemResult(BaseModel):
    event_id: str
    status: Literal["accepted", "duplicate", "rejected"]
    event_id_persisted: UUID | None = None
    error: str | None = None


class UsageEventBatchResponse(BaseModel):
    accepted: int
    duplicates: int
    rejected: int
    results: list[BatchItemResult]


class UsageEventFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: UUID | None = None
    model_version_id: UUID | None = None
    deployment_id: UUID | None = None
    application_id: UUID | None = None
    agent_id: UUID | None = None
    status: UsageStatus | None = None
    source: UsageSource | None = None
    environment: str | None = None
    started_from: datetime | None = None
    started_to: datetime | None = None
    request_id: str | None = None
    trace_id: str | None = None
    operation_name: str | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)
    sort_by: Literal["started_at", "created_at", "duration_ms"] = "started_at"
    sort_order: Literal["asc", "desc"] = "desc"


class UsageStatsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: UUID | None = None
    model_version_id: UUID | None = None
    deployment_id: UUID | None = None
    application_id: UUID | None = None
    agent_id: UUID | None = None
    environment: str | None = None
    started_from: datetime
    started_to: datetime
    granularity: Literal["total", "hour", "day"] = "total"

    @model_validator(mode="after")
    def _check_range(self) -> "UsageStatsQuery":
        if self.started_to <= self.started_from:
            raise ValueError("started_to must be later than started_from")
        return self


class UsageStatsTotals(BaseModel):
    requests: int
    successful_requests: int
    failed_requests: int
    error_rate: float
    input_tokens: int
    output_tokens: int
    total_tokens: int
    avg_latency_ms: float | None
    min_latency_ms: int | None
    max_latency_ms: int | None


class UsageStatsPeriod(BaseModel):
    from_: datetime = Field(alias="from")
    to: datetime

    model_config = ConfigDict(populate_by_name=True)


class UsageTimeBucket(BaseModel):
    bucket_start: datetime
    totals: UsageStatsTotals


class UsageStatsResponse(BaseModel):
    period: UsageStatsPeriod
    filters: dict[str, Any]
    totals: UsageStatsTotals
    buckets: list[UsageTimeBucket]


def payload_fingerprint(event: UsageEventCreate) -> str:
    payload = event.model_dump(mode="json")
    canonical = {field: payload[field] for field in _FINGERPRINT_FIELDS}
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    return digest.hexdigest()
