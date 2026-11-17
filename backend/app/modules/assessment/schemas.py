"""Pydantic schemas for RF-02 assessment templates and assessments.

Validation follows the RF-02 plan: ``extra="forbid"`` everywhere keeps
out-of-scope fields — scoring knobs belong to RF-03, template section
authoring is out of scope entirely — from silently entering payloads.
``key``/``version`` are never writable after creation (Ruling 4): they
snapshot onto each assessment, so PATCHing them would rewrite history.
"""

import re
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.assessment.enums import AssessmentStatus, AssessmentType, QuestionType

MAX_NAME = 255
MAX_DESCRIPTION = 10000
MAX_ASSIGNEE = 255
MAX_KEY = 100
MAX_REASON = 10000
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def slugify(name: str) -> str:
    """URL/query-safe identity for a template name (mirrors RF-01 slugify)."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _strip(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


def _blank_to_none(value: object) -> object:
    # a blank string means "unset", not an empty label
    if isinstance(value, str):
        stripped = value.strip()
        return None if stripped == "" else stripped
    return value


def _require_timezone(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


# --- template responses ------------------------------------------------------


class AssessmentTemplateRef(BaseModel):
    """Summary of the template an assessment ran against, embedded in responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    version: int
    assessment_type: AssessmentType


class AssessmentQuestionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    section_id: UUID
    key: str
    question_text: str
    description: str | None = None
    question_type: QuestionType
    required: bool
    display_order: int
    options: list[dict] | None = None
    validation_rules: dict | None = None
    help_text: str | None = None
    is_active: bool


class AssessmentSectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    description: str | None = None
    display_order: int
    is_active: bool
    questions: list[AssessmentQuestionResponse] = []


class AssessmentTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    key: str
    name: str
    description: str | None = None
    assessment_type: AssessmentType
    version: int
    is_system: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        return _require_timezone(value)


class AssessmentTemplateDetailResponse(AssessmentTemplateResponse):
    sections: list[AssessmentSectionResponse] = []


class AssessmentTemplateListResponse(BaseModel):
    items: list[AssessmentTemplateResponse]
    page: int
    page_size: int
    total: int
    total_pages: int


# --- template write payloads -------------------------------------------------


class AssessmentTemplateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_NAME)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    assessment_type: AssessmentType = AssessmentType.INITIAL
    is_active: bool = True
    key: str | None = Field(default=None, max_length=MAX_KEY)

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, value: object) -> object:
        return _strip(value)

    @field_validator("description", mode="before")
    @classmethod
    def _blank_description(cls, value: object) -> object:
        return _blank_to_none(value)

    @field_validator("key", mode="before")
    @classmethod
    def _strip_key(cls, value: object) -> object:
        return _blank_to_none(value)

    @field_validator("key")
    @classmethod
    def _check_key(cls, value: str | None) -> str | None:
        if value is not None and not SLUG_PATTERN.match(value):
            raise ValueError("key must be lowercase words separated by single hyphens")
        return value

    @model_validator(mode="after")
    def _derive_key(self) -> "AssessmentTemplateCreate":
        if self.key is None:
            derived = slugify(self.name)
            if not derived:
                raise ValueError("key could not be derived from name")
            self.key = derived
        return self


class AssessmentTemplateUpdate(BaseModel):
    """Partial update; ``key``/``version`` are never PATCHable (Ruling 4)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    assessment_type: AssessmentType | None = None
    is_active: bool | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, value: object) -> object:
        return _strip(value)

    @field_validator("description", mode="before")
    @classmethod
    def _blank_description(cls, value: object) -> object:
        return _blank_to_none(value)


class AssessmentTemplateFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)
    search: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    is_system: bool | None = None
    assessment_type: AssessmentType | None = None
    # the sort whitelist is enforced in the service (400), not here (422)
    sort_by: str = "name"
    sort_order: str = "asc"


# --- assessment write payloads ------------------------------------------------


class RiskAssessmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_id: UUID
    template_id: UUID
    assessment_type: AssessmentType = AssessmentType.INITIAL
    assessor: str | None = Field(default=None, max_length=MAX_ASSIGNEE)

    @field_validator("assessor", mode="before")
    @classmethod
    def _strip_assessor(cls, value: object) -> object:
        return _blank_to_none(value)


class AssessmentResponseUpdate(BaseModel):
    """One saved answer; clearing an answer is not supported (Ruling 10)."""

    model_config = ConfigDict(extra="forbid")

    response: Any
    answered_by: str | None = Field(default=None, max_length=MAX_ASSIGNEE)

    @field_validator("answered_by", mode="before")
    @classmethod
    def _strip_answered_by(cls, value: object) -> object:
        return _blank_to_none(value)

    @model_validator(mode="after")
    def _reject_null_response(self) -> "AssessmentResponseUpdate":
        if self.response is None:
            raise ValueError("response must not be null")
        return self


class AssessmentRejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=MAX_REASON)
    reviewer: str | None = Field(default=None, max_length=MAX_ASSIGNEE)

    @field_validator("reason", mode="before")
    @classmethod
    def _strip_reason(cls, value: object) -> object:
        return _strip(value)

    @field_validator("reviewer", mode="before")
    @classmethod
    def _strip_reviewer(cls, value: object) -> object:
        return _blank_to_none(value)


class AssessmentApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer: str | None = Field(default=None, max_length=MAX_ASSIGNEE)

    @field_validator("reviewer", mode="before")
    @classmethod
    def _strip_reviewer(cls, value: object) -> object:
        return _blank_to_none(value)


class AssessmentRevisionRequest(BaseModel):
    """Empty body allowed — reserved for future revision notes."""

    model_config = ConfigDict(extra="forbid")


# --- assessment responses -----------------------------------------------------


class AssessmentAssetRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str


class AssessmentRiskRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    asset_id: UUID | None = None
    asset: AssessmentAssetRef | None = None


class AssessmentRiskSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str


class AssessmentAnswerState(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    value: Any
    answered_by: str | None = None
    updated_at: datetime

    @field_validator("updated_at", mode="before")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        return _require_timezone(value)


class AssessmentQuestionDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    section_id: UUID
    key: str
    question_text: str
    description: str | None = None
    question_type: QuestionType
    required: bool
    display_order: int
    options: list[dict] | None = None
    validation_rules: dict | None = None
    help_text: str | None = None
    is_active: bool
    response: AssessmentAnswerState | None = None


class AssessmentSectionDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    description: str | None = None
    display_order: int
    questions: list[AssessmentQuestionDetail] = []


class RiskAssessmentDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    risk_id: UUID
    risk: AssessmentRiskRef
    template: AssessmentTemplateRef
    template_version: int
    assessment_type: AssessmentType
    status: AssessmentStatus
    assessor: str | None = None
    reviewer: str | None = None
    revision_number: int
    original_assessment_id: UUID | None = None
    submitted_at: datetime | None = None
    reviewed_at: datetime | None = None
    approved_at: datetime | None = None
    rejected_at: datetime | None = None
    rejection_reason: str | None = None
    completion_percentage: int
    created_at: datetime
    updated_at: datetime
    sections: list[AssessmentSectionDetail] = []

    @field_validator(
        "submitted_at",
        "reviewed_at",
        "approved_at",
        "rejected_at",
        "created_at",
        "updated_at",
        mode="before",
    )
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        return _require_timezone(value)


class RiskAssessmentListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    risk_id: UUID
    risk: AssessmentRiskSummary
    template: AssessmentTemplateRef
    template_version: int
    assessment_type: AssessmentType
    status: AssessmentStatus
    assessor: str | None = None
    reviewer: str | None = None
    revision_number: int
    original_assessment_id: UUID | None = None
    completion_percentage: int
    submitted_at: datetime | None = None
    approved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("submitted_at", "approved_at", "created_at", "updated_at", mode="before")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        return _require_timezone(value)


class RiskAssessmentListResponse(BaseModel):
    items: list[RiskAssessmentListItem]
    page: int
    page_size: int
    total: int
    total_pages: int


class RiskAssessmentFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)
    search: str | None = Field(default=None, max_length=255)
    risk_id: UUID | None = None
    asset_id: UUID | None = None
    template_id: UUID | None = None
    assessment_type: AssessmentType | None = None
    status: AssessmentStatus | None = None
    assessor: str | None = None
    reviewer: str | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    # the sort whitelist is enforced in the service (400), not here (422)
    sort_by: str = "updated_at"
    sort_order: str = "desc"

    @field_validator("created_from", "created_to", mode="before")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        return _require_timezone(value)

    @model_validator(mode="after")
    def _check_date_range(self) -> "RiskAssessmentFilter":
        if (
            self.created_from is not None
            and self.created_to is not None
            and self.created_from > self.created_to
        ):
            raise ValueError("created_from must be earlier than or equal to created_to")
        return self
