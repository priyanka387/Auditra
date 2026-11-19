"""Pydantic schemas for RF-03 Risk Scoring & Prioritization.

Two groups: framework configuration (create/update/filter/responses for
``risk_scoring_frameworks`` plus their levels and thresholds) and score
history (create/override/filter/responses for ``risk_scores``), plus the
scoring-overview payloads. Validation mirrors RF-01/RF-02 style:
``extra="forbid"`` everywhere, trimmed names, blank descriptions become
``None``, and timestamps must be timezone-aware.

Deliberately absent from update/create payloads: ``version``/``method``
never change after creation (Ruling 4 — a new version is a new row),
and ``calculated_score`` is always computed by the engine, never
accepted from a client.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from app.modules.risk_scoring.enums import RiskSeverity, ScoringMethod

MAX_NAME = 100
MAX_DESCRIPTION = 10000
MAX_REASON = 10000
MAX_SEARCH = 255


class LikelihoodLevelResponse(BaseModel):
    """One point on a framework's likelihood scale (RF-03 §10)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    value: int
    name: str
    description: str | None = None
    display_order: int
    is_active: bool


class ImpactLevelResponse(BaseModel):
    """One point on a framework's impact scale (RF-03 §11)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    value: int
    name: str
    description: str | None = None
    display_order: int
    is_active: bool


class SeverityThresholdResponse(BaseModel):
    """A score band mapped to a severity rating and priority (RF-03 §13)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    minimum_score: int
    maximum_score: int
    severity: RiskSeverity
    priority: int
    display_order: int


class ScoringFrameworkRef(BaseModel):
    """Which framework (and version) a score or overview refers to."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    version: int
    method: ScoringMethod


class ScoringFrameworkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    name: str
    description: str | None = None
    version: int
    method: ScoringMethod
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


class ScoringFrameworkDetailResponse(ScoringFrameworkResponse):
    """A framework with its full configuration (RF-03 §12)."""

    likelihood_levels: list[LikelihoodLevelResponse]
    impact_levels: list[ImpactLevelResponse]
    severity_thresholds: list[SeverityThresholdResponse]


class ScoringFrameworkListResponse(BaseModel):
    items: list[ScoringFrameworkResponse]
    page: int
    page_size: int
    total: int
    total_pages: int


class ScoringFrameworkFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)
    search: str | None = Field(default=None, max_length=MAX_SEARCH)
    is_active: bool | None = None
    is_system: bool | None = None
    sort_by: Literal["name", "created_at", "updated_at"] = "name"
    sort_order: Literal["asc", "desc"] = "asc"


class LikelihoodLevelInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=MAX_NAME)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    display_order: int | None = Field(default=None, ge=1)
    is_active: bool = True

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("description", mode="before")
    @classmethod
    def _blank_description(cls, value: object) -> object:
        if isinstance(value, str) and value.strip() == "":
            return None
        return value


class ImpactLevelInput(LikelihoodLevelInput):
    """Same shape as a likelihood level (RF-03 §11)."""


class SeverityThresholdInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: RiskSeverity
    minimum_score: int = Field(ge=1)
    maximum_score: int = Field(ge=1)
    priority: int = Field(ge=1)
    display_order: int | None = Field(default=None, ge=1)


class ScoringFrameworkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_NAME)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    method: ScoringMethod = ScoringMethod.LIKELIHOOD_IMPACT
    version: int = Field(default=1, ge=1)
    likelihood_levels: list[LikelihoodLevelInput] = Field(min_length=1)
    impact_levels: list[ImpactLevelInput] = Field(min_length=1)
    severity_thresholds: list[SeverityThresholdInput] = Field(min_length=1)
    is_active: bool = True

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("description", mode="before")
    @classmethod
    def _blank_description(cls, value: object) -> object:
        if isinstance(value, str) and value.strip() == "":
            return None
        return value


class ScoringFrameworkUpdate(BaseModel):
    """Partial update. Version, method, levels and thresholds are never
    PATCHable (Ruling 4) — a changed methodology is a new version."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    is_active: bool | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("description", mode="before")
    @classmethod
    def _blank_description(cls, value: object) -> object:
        if isinstance(value, str) and value.strip() == "":
            return None
        return value


class RiskScoreCreate(BaseModel):
    """Score inputs; membership and range checks happen in the service
    against the selected framework (plan §51)."""

    model_config = ConfigDict(extra="forbid")

    framework_id: UUID
    likelihood: int
    impact: int
    assessment_id: UUID | None = None


class RiskScoreOverrideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    override_score: int
    reason: str = Field(min_length=1, max_length=MAX_REASON)

    @field_validator("reason", mode="before")
    @classmethod
    def _strip_reason(cls, value: object) -> object:
        if isinstance(value, str):
            # whitespace-only reasons are blank → fail min_length (plan §36)
            return value.strip()
        return value


class RiskScoreResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    risk_id: UUID
    framework: ScoringFrameworkRef
    framework_version: int
    likelihood: int = Field(validation_alias=AliasChoices("likelihood", "likelihood_value"))
    likelihood_label: str
    impact: int = Field(validation_alias=AliasChoices("impact", "impact_value"))
    impact_label: str
    calculated_score: int
    effective_score: int
    severity: RiskSeverity
    priority: int
    override_score: int | None = None
    override_reason: str | None = None
    is_override: bool
    is_inherent: bool
    is_current: bool
    assessment_id: UUID | None = None
    source: str
    scored_by: str | None = None
    created_at: datetime

    @field_validator("created_at", mode="before")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class RiskScoreListResponse(BaseModel):
    items: list[RiskScoreResponse]
    page: int
    page_size: int
    total: int
    total_pages: int


class RiskScoreFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)
    sort_by: Literal["created_at"] = "created_at"
    sort_order: Literal["asc", "desc"] = "desc"


class RiskDistributionResponse(BaseModel):
    """Severity counts across a framework's current scores. ``unscored``
    risks are never folded into ``low`` (RF-03 §28)."""

    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    unscored: int = 0


class MatrixCell(BaseModel):
    """One heatmap cell: how many risks sit at a likelihood/impact pair."""

    likelihood: int
    impact: int
    count: int
    risk_ids: list[UUID]


class OverviewTopRisk(BaseModel):
    """Highest-priority current score, for the overview's top list."""

    risk_id: UUID
    title: str
    severity: RiskSeverity
    effective_score: int
    priority: int


class ScoringOverviewResponse(BaseModel):
    framework: ScoringFrameworkRef
    distribution: RiskDistributionResponse
    likelihood_levels: list[LikelihoodLevelResponse]
    impact_levels: list[ImpactLevelResponse]
    cells: list[MatrixCell]
    top_risks: list[OverviewTopRisk]
