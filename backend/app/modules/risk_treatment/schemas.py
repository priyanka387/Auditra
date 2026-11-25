"""Pydantic schemas for RF-04 risk treatment & mitigation.

Validation follows the plan: trimmed non-empty title, length-limited
optional text, required strategy, required action owner, cross-field
date ordering, and ``extra="forbid"`` everywhere so out-of-scope fields
(compliance, controls, verified residual claims) cannot sneak in.
Acceptance rationale and cancellation reasons are business rules enforced
by the service/schema boundary described in the plan's rulings; blank
values fail here as ``ValidationError`` → 422.
"""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.risk_scoring.enums import RiskSeverity
from app.modules.risk_treatment.enums import (
    ResidualAssessmentType,
    RiskTreatmentStatus,
    RiskTreatmentStrategy,
    TreatmentActionStatus,
    TreatmentPriority,
)

MAX_TITLE = 255
MAX_DESCRIPTION = 10000
MAX_OWNER = 255
MAX_REASON = 10000


def _strip_or_none(value: object) -> object:
    """Blank assignment/reason text means 'unset', not an empty label."""
    if isinstance(value, str):
        stripped = value.strip()
        return None if stripped == "" else stripped
    return value


class TreatmentCreate(BaseModel):
    """Create a treatment decision on a risk (plan §30).

    ``status`` is intentionally absent: every treatment starts at
    ``not_started`` and moves through the state machine (Ruling 6).
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_TITLE)
    strategy: RiskTreatmentStrategy
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    rationale: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    owner: str | None = Field(default=None, max_length=MAX_OWNER)
    planned_start_date: date | None = None
    target_completion_date: date | None = None

    @field_validator("title", mode="before")
    @classmethod
    def _strip_title(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("description", "rationale", "owner", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        return _strip_or_none(value)

    @model_validator(mode="after")
    def _check_dates(self) -> "TreatmentCreate":
        if (
            self.planned_start_date is not None
            and self.target_completion_date is not None
            and self.planned_start_date > self.target_completion_date
        ):
            raise ValueError("planned_start_date must be on or before target_completion_date")
        return self


class TreatmentUpdate(BaseModel):
    """Partial update: only non-None fields are applied.

    ``status`` moves only through the forward transitions (plan §55);
    completed/cancelled are reachable exclusively through the dedicated
    endpoints (Ruling 6). Terminal stamps are never settable here.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=MAX_TITLE)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    rationale: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    owner: str | None = Field(default=None, max_length=MAX_OWNER)
    strategy: RiskTreatmentStrategy | None = None
    planned_start_date: date | None = None
    target_completion_date: date | None = None
    status: RiskTreatmentStatus | None = None

    @field_validator("title", mode="before")
    @classmethod
    def _strip_title(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("description", "rationale", "owner", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        return _strip_or_none(value)

    @model_validator(mode="after")
    def _check_dates(self) -> "TreatmentUpdate":
        if (
            self.planned_start_date is not None
            and self.target_completion_date is not None
            and self.planned_start_date > self.target_completion_date
        ):
            raise ValueError("planned_start_date must be on or before target_completion_date")
        return self


class TreatmentCancelRequest(BaseModel):
    """Cancellation always says why (plan §35)."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=MAX_REASON)

    @field_validator("reason", mode="before")
    @classmethod
    def _strip_reason(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class TreatmentProgress(BaseModel):
    """Derived action progress (plan §18) — computed, never stored."""

    model_config = ConfigDict(extra="forbid")

    total_actions: int = 0
    completed_actions: int = 0
    in_progress_actions: int = 0
    cancelled_actions: int = 0
    remaining_actions: int = 0
    progress_percent: int = 0


class TreatmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    risk_id: UUID
    strategy: RiskTreatmentStrategy
    status: RiskTreatmentStatus
    title: str
    description: str | None = None
    rationale: str | None = None
    owner: str | None = None
    created_by: str | None = None
    planned_start_date: date | None = None
    target_completion_date: date | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancellation_reason: str | None = None
    is_overdue: bool = False
    # populated on detail responses only (Ruling 14)
    action_stats: TreatmentProgress | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", "completed_at", "cancelled_at", mode="before")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class TreatmentListResponse(BaseModel):
    items: list[TreatmentResponse]
    page: int
    page_size: int
    total: int
    total_pages: int


class TreatmentFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)
    risk_id: UUID | None = None
    strategy: RiskTreatmentStrategy | None = None
    status: RiskTreatmentStatus | None = None
    owner: str | None = Field(default=None, max_length=MAX_OWNER)
    overdue: bool | None = None
    sort_by: str = "created_at"
    sort_order: str = "desc"


class TreatmentSummary(BaseModel):
    """Scalar active-treatment embed for register rows (plan §38/§42).

    Deliberately scalar-only so ``risk/schemas.py`` depends on nothing
    but the treatment enums (mirrors RF-03's ``RiskScoreSummary``).
    ``null`` on ``RiskResponse`` means no active treatment.
    """

    model_config = ConfigDict(extra="forbid")

    strategy: RiskTreatmentStrategy
    status: RiskTreatmentStatus
    owner: str | None = None
    target_completion_date: date | None = None
    is_overdue: bool = False


# --- treatment actions -------------------------------------------------------


class TreatmentActionCreate(BaseModel):
    """One work item executing the treatment (plan §31).

    Owner is required (plan §41's action form); priority defaults to
    medium (Ruling 15).
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_TITLE)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    owner: str = Field(min_length=1, max_length=MAX_OWNER)
    priority: TreatmentPriority = TreatmentPriority.MEDIUM
    planned_start_date: date | None = None
    due_date: date | None = None

    @field_validator("title", "owner", mode="before")
    @classmethod
    def _strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("description", mode="before")
    @classmethod
    def _blank_description(cls, value: object) -> object:
        return _strip_or_none(value)

    @model_validator(mode="after")
    def _check_dates(self) -> "TreatmentActionCreate":
        if (
            self.planned_start_date is not None
            and self.due_date is not None
            and self.planned_start_date > self.due_date
        ):
            raise ValueError("planned_start_date must be on or before due_date")
        return self


class TreatmentActionUpdate(BaseModel):
    """Partial update (plan §32): owner, status, priority, description,
    due date — never the terminal stamps."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=MAX_TITLE)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    owner: str | None = Field(default=None, min_length=1, max_length=MAX_OWNER)
    priority: TreatmentPriority | None = None
    status: TreatmentActionStatus | None = None
    planned_start_date: date | None = None
    due_date: date | None = None

    @field_validator("title", "owner", mode="before")
    @classmethod
    def _strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("description", mode="before")
    @classmethod
    def _blank_description(cls, value: object) -> object:
        return _strip_or_none(value)

    @model_validator(mode="after")
    def _check_dates(self) -> "TreatmentActionUpdate":
        if (
            self.planned_start_date is not None
            and self.due_date is not None
            and self.planned_start_date > self.due_date
        ):
            raise ValueError("planned_start_date must be on or before due_date")
        return self


class TreatmentActionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    treatment_id: UUID
    title: str
    description: str | None = None
    status: TreatmentActionStatus
    owner: str
    priority: TreatmentPriority
    planned_start_date: date | None = None
    due_date: date | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancellation_reason: str | None = None
    is_overdue: bool = False
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", "completed_at", "cancelled_at", mode="before")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class TreatmentActionListResponse(BaseModel):
    items: list[TreatmentActionResponse]
    page: int
    page_size: int
    total: int
    total_pages: int


class TreatmentActionFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)
    status: TreatmentActionStatus | None = None
    owner: str | None = Field(default=None, max_length=MAX_OWNER)
    sort_by: str = "created_at"
    sort_order: str = "asc"


# --- expected residual risk --------------------------------------------------


class ResidualAssessmentCreate(BaseModel):
    """Expected residual likelihood/impact (plan §23/§24).

    Score and severity are computed through the RF-03 engine server-side
    — never accepted from the client. Only ``expected`` is accepted by
    the service until verification exists (Ruling 10).
    """

    model_config = ConfigDict(extra="forbid")

    likelihood: int = Field(ge=1)
    impact: int = Field(ge=1)
    assessment_type: ResidualAssessmentType = ResidualAssessmentType.EXPECTED


class ResidualAssessmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    treatment_id: UUID
    likelihood: int
    impact: int
    calculated_score: int
    severity: RiskSeverity
    assessment_type: ResidualAssessmentType
    created_by: str | None = None
    created_at: datetime

    @field_validator("created_at", mode="before")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class ResidualAssessmentListResponse(BaseModel):
    items: list[ResidualAssessmentResponse]
    page: int
    page_size: int
    total: int
    total_pages: int


class ResidualAssessmentFilter(BaseModel):
    """Pagination only: residual history is always newest-first and has
    no user-facing columns to filter or sort (plan §36)."""

    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)


# --- treatment overview (dashboard) ------------------------------------------


class TreatmentStatusDistribution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    not_started: int = 0
    planned: int = 0
    in_progress: int = 0
    completed: int = 0
    cancelled: int = 0


class TreatmentStrategyDistribution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mitigate: int = 0
    accept: int = 0
    avoid: int = 0
    transfer: int = 0


class TreatmentOverviewResponse(BaseModel):
    """Treatment dashboard numbers (plan §44/§45) — data only, no UI."""

    model_config = ConfigDict(extra="forbid")

    total_risks: int = 0
    risks_with_treatment: int = 0
    risks_without_treatment: int = 0
    status_distribution: TreatmentStatusDistribution = Field(
        default_factory=TreatmentStatusDistribution
    )
    strategy_distribution: TreatmentStrategyDistribution = Field(
        default_factory=TreatmentStrategyDistribution
    )
    overdue_actions: int = 0
    overdue_treatments: int = 0
    untreated_critical: int = 0
    untreated_high: int = 0
