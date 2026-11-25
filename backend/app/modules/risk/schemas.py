"""Pydantic schemas for the RF-01 Risk Register and taxonomy.

Validation follows RF-01 §28: trimmed non-empty title with a reasonable
length bound, length-limited optional description, required category,
and strict enum checks for status/source. ``extra="forbid"`` everywhere
keeps out-of-scope fields (likelihood, residual_score, ...) from
silently entering the registry — scoring belongs to RF-03.
"""

import re
from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.risk.enums import RiskSource, RiskStatus
from app.modules.risk_scoring.enums import RiskSeverity
from app.modules.risk_treatment.enums import RiskTreatmentStatus, RiskTreatmentStrategy

MAX_TITLE = 255
MAX_DESCRIPTION = 10000
MAX_ASSIGNMENT = 255
MAX_CATEGORY_NAME = 100
MAX_CATEGORY_DESCRIPTION = 1000
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def slugify(name: str) -> str:
    """URL/query-safe identity for a category name ("Fairness / Bias" → fairness-bias)."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


class RiskCategoryRef(BaseModel):
    """Summary of the category a risk belongs to, embedded in risk responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str


class RiskAssetRef(BaseModel):
    """Summary of the linked discovered AI asset, embedded in risk responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str


class RiskScoreSummary(BaseModel):
    """Scalar snapshot of a risk's current score for register rows (RF-03 §16).

    Deliberately scalar-only (no framework ref) so ``risk/schemas.py``
    depends on nothing but the scoring enums. ``null`` on ``RiskResponse``
    means unscored — never a fabricated low rating (plan §28).
    """

    model_config = ConfigDict(extra="forbid")

    likelihood: int
    impact: int
    calculated_score: int
    effective_score: int
    severity: RiskSeverity
    priority: int
    is_override: bool
    scored_at: datetime

    @field_validator("scored_at", mode="before")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class TreatmentSummary(BaseModel):
    """Scalar snapshot of a risk's active treatment for register rows (RF-04 §42).

    Deliberately scalar-only (no treatment/risk ref) so ``risk/schemas.py``
    depends on nothing but the treatment enums — mirroring
    ``RiskScoreSummary``. ``null`` on ``RiskResponse`` means untreated —
    never a fabricated status — and only an *active* treatment is ever
    summarized (completed/cancelled rows are history, Ruling 13).
    """

    model_config = ConfigDict(extra="forbid")

    strategy: RiskTreatmentStrategy
    status: RiskTreatmentStatus
    owner: str | None = None
    target_completion_date: date | None = None
    is_overdue: bool


class RiskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_TITLE)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    category_id: UUID
    source: RiskSource = RiskSource.MANUAL
    asset_id: UUID | None = None
    owner: str | None = Field(default=None, max_length=MAX_ASSIGNMENT)
    team: str | None = Field(default=None, max_length=MAX_ASSIGNMENT)
    status: RiskStatus = RiskStatus.OPEN

    @field_validator("title", mode="before")
    @classmethod
    def _strip_title(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("owner", "team", mode="before")
    @classmethod
    def _strip_assignment(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            # blank owner/team means "unassigned", not an empty label
            return None if stripped == "" else stripped
        return value

    @field_validator("description", mode="before")
    @classmethod
    def _blank_description(cls, value: object) -> object:
        if isinstance(value, str) and value.strip() == "":
            return None
        return value


class RiskUpdate(BaseModel):
    """Partial update: only non-None fields are applied (RF-01 §19)."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=MAX_TITLE)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    category_id: UUID | None = None
    source: RiskSource | None = None
    asset_id: UUID | None = None
    owner: str | None = Field(default=None, max_length=MAX_ASSIGNMENT)
    team: str | None = Field(default=None, max_length=MAX_ASSIGNMENT)
    status: RiskStatus | None = None

    @field_validator("title", mode="before")
    @classmethod
    def _strip_title(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("owner", "team", mode="before")
    @classmethod
    def _strip_assignment(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return None if stripped == "" else stripped
        return value

    @field_validator("description", mode="before")
    @classmethod
    def _blank_description(cls, value: object) -> object:
        if isinstance(value, str) and value.strip() == "":
            return None
        return value


class RiskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    title: str
    description: str | None = None
    category_id: UUID
    category: RiskCategoryRef
    source: RiskSource
    asset_id: UUID | None = None
    asset: RiskAssetRef | None = None
    owner: str | None = None
    team: str | None = None
    status: RiskStatus
    created_by: str | None = None
    updated_by: str | None = None
    archived_at: datetime | None = None
    current_score: RiskScoreSummary | None = None
    treatment: TreatmentSummary | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", "archived_at", mode="before")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class RiskListResponse(BaseModel):
    items: list[RiskResponse]
    page: int
    page_size: int
    total: int
    total_pages: int


class RiskFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)
    search: str | None = Field(default=None, max_length=255)
    category_id: UUID | None = None
    status: RiskStatus | None = None
    source: RiskSource | None = None
    owner: str | None = Field(default=None, max_length=MAX_ASSIGNMENT)
    team: str | None = Field(default=None, max_length=MAX_ASSIGNMENT)
    asset_id: UUID | None = None
    # False (default) → active risks only; True → archived risks only,
    # so the register's default view stays clean while archived risks
    # remain queryable (RF-01 §20)
    archived: bool = False
    # RF-03 scoring filters: any score filter implies an inner-filter on
    # the joined current score (unscored risks cannot match)
    severity: RiskSeverity | None = None
    priority: int | None = Field(default=None, ge=1)
    scored: bool | None = None
    likelihood: int | None = None
    impact: int | None = None
    framework_id: UUID | None = None
    # RF-04 treatment filters: run against the LEFT JOINed *active*
    # treatment, so any of them naturally restricts results to treated
    # risks and completed/cancelled history never matches (Ruling 13)
    treatment_strategy: RiskTreatmentStrategy | None = None
    treatment_status: RiskTreatmentStatus | None = None
    treatment_owner: str | None = Field(default=None, max_length=MAX_ASSIGNMENT)
    treatment_overdue: bool | None = None
    treated: bool | None = None
    sort_by: str = "updated_at"
    sort_order: str = "desc"


class RiskCategoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_CATEGORY_NAME)
    description: str | None = Field(default=None, max_length=MAX_CATEGORY_DESCRIPTION)
    slug: str | None = Field(default=None, max_length=MAX_CATEGORY_NAME)

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("slug", mode="before")
    @classmethod
    def _strip_slug(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip().lower()
            return None if value == "" else value
        return value

    @field_validator("slug")
    @classmethod
    def _check_slug(cls, value: str | None) -> str | None:
        if value is not None and not SLUG_PATTERN.match(value):
            raise ValueError("slug must be lowercase words separated by single hyphens")
        return value

    @model_validator(mode="after")
    def _derive_slug(self) -> "RiskCategoryCreate":
        if self.slug is None:
            derived = slugify(self.name)
            if not derived:
                raise ValueError("slug could not be derived from name")
            self.slug = derived
        return self


class RiskCategoryUpdate(BaseModel):
    """Partial update; slug is immutable once created (RF-01 §26)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=MAX_CATEGORY_NAME)
    description: str | None = Field(default=None, max_length=MAX_CATEGORY_DESCRIPTION)
    is_active: bool | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class RiskCategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    name: str
    description: str | None = None
    slug: str
    is_system: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class RiskCategoryListResponse(BaseModel):
    items: list[RiskCategoryResponse]
    page: int
    page_size: int
    total: int
    total_pages: int


class RiskCategoryFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)
    search: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    is_system: bool | None = None
    sort_by: str = "name"
    sort_order: str = "asc"
