"""RF-02 assessment services: template CRUD + assessment workflow.

Both services follow the RF-01 pattern: tenant-scoped entity lookups
(unknown and cross-tenant ids are indistinguishable 404s), governance
mutations staged with ``AuditService.record_event`` in the same
transaction, then a single commit. The workflow itself is a strict
state machine (Ruling 6): draft → in_progress → submitted →
under_review → approved | rejected, with rejection spawning a new
revision row instead of mutating history.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.assessment import repository
from app.modules.assessment.enums import AssessmentStatus, QuestionType
from app.modules.assessment.errors import (
    AssessmentError,
    AssessmentIncompleteError,
    AssessmentInvalidStateError,
    AssessmentNotFoundError,
    AssessmentQuestionNotFoundError,
    AssessmentTemplateInactiveError,
    AssessmentTemplateNotFoundError,
    DuplicateAssessmentTemplateError,
    InvalidAssessmentResponseError,
)
from app.modules.assessment.models import (
    AssessmentQuestion,
    AssessmentResponse,
    AssessmentSection,
    AssessmentTemplate,
    RiskAssessment,
)
from app.modules.assessment.schemas import (
    AssessmentApproveRequest,
    AssessmentRejectRequest,
    AssessmentResponseUpdate,
    AssessmentTemplateCreate,
    AssessmentTemplateFilter,
    AssessmentTemplateUpdate,
    RiskAssessmentCreate,
    RiskAssessmentFilter,
)
from app.modules.asset_discovery.models import DiscoveredAsset
from app.modules.audit.enums import AuditEventType
from app.modules.audit.service import AuditService
from app.modules.risk.errors import RiskArchivedError, RiskNotFoundError
from app.modules.risk.models import Risk

logger = logging.getLogger("auditra.assessment")

EDITABLE_STATUSES = frozenset({AssessmentStatus.DRAFT, AssessmentStatus.IN_PROGRESS})

MAX_TEXT = 1000
MAX_LONG_TEXT = 10000


# --- audit projections --------------------------------------------------------


def template_audit_state(template: AssessmentTemplate) -> dict:
    """Governance-relevant projection of an assessment template."""
    return {
        "key": template.key,
        "name": template.name,
        "description": template.description,
        "assessment_type": template.assessment_type,
        "version": template.version,
        "is_system": template.is_system,
        "is_active": template.is_active,
    }


def assessment_audit_state(assessment: RiskAssessment) -> dict:
    """Governance-relevant projection of an assessment's lifecycle state."""
    return {
        "risk_id": str(assessment.risk_id),
        "template_id": str(assessment.template_id),
        "template_version": assessment.template_version,
        "assessment_type": assessment.assessment_type,
        "status": assessment.status,
        "assessor": assessment.assessor,
        "reviewer": assessment.reviewer,
        "revision_number": assessment.revision_number,
        "original_assessment_id": (
            str(assessment.original_assessment_id) if assessment.original_assessment_id else None
        ),
        "submitted_at": assessment.submitted_at.isoformat() if assessment.submitted_at else None,
        "reviewed_at": assessment.reviewed_at.isoformat() if assessment.reviewed_at else None,
        "approved_at": assessment.approved_at.isoformat() if assessment.approved_at else None,
        "rejected_at": assessment.rejected_at.isoformat() if assessment.rejected_at else None,
        "rejection_reason": assessment.rejection_reason,
    }


# --- response validation (Ruling 9) -------------------------------------------


def _option_values(question: AssessmentQuestion) -> set[str]:
    return {
        option["value"]
        for option in question.options or []
        if isinstance(option, dict) and "value" in option
    }


def _validate_response(question: AssessmentQuestion, value) -> None:
    """Reject a value that does not match the question's declared type."""
    kind = question.question_type
    valid = False
    if kind == QuestionType.TEXT.value:
        valid = isinstance(value, str) and len(value) <= MAX_TEXT
    elif kind == QuestionType.LONG_TEXT.value:
        valid = isinstance(value, str) and len(value) <= MAX_LONG_TEXT
    elif kind == QuestionType.SINGLE_SELECT.value:
        valid = isinstance(value, str) and value in _option_values(question)
    elif kind == QuestionType.MULTI_SELECT.value:
        valid = (
            isinstance(value, list)
            and all(isinstance(item, str) for item in value)
            and set(value) <= _option_values(question)
        )
    elif kind == QuestionType.BOOLEAN.value:
        # strict bool: an int must never pass as an answer (1 != True here)
        valid = isinstance(value, bool)
    elif kind == QuestionType.NUMBER.value:
        valid = isinstance(value, int | float) and not isinstance(value, bool)
    if not valid:
        raise InvalidAssessmentResponseError(
            f"response does not match question '{question.key}' of type '{kind}'"
        )


def _is_answered(response: AssessmentResponse | None) -> bool:
    """Answered = a row exists with a non-empty value (Ruling 9).

    ``False`` and ``0`` are real answers; only blanks, empty lists and
    missing rows count as unanswered.
    """
    if response is None:
        return False
    value = response.response_value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, list | dict):
        return len(value) > 0
    return True


# --- detail view ----------------------------------------------------------------


@dataclass
class AssessmentDetailView:
    """Everything one assessment detail payload needs, fetched in ≤6 queries."""

    assessment: RiskAssessment
    risk: Risk
    asset: DiscoveredAsset | None
    template: AssessmentTemplate
    sections: list[AssessmentSection]
    questions_by_section: dict[UUID, list[AssessmentQuestion]]
    responses: dict[UUID, AssessmentResponse]
    completion_percentage: int


class AssessmentTemplateService:
    """CRUD over the per-tenant template catalog (metadata only)."""

    def __init__(
        self,
        db: Session,
        tenant_id: UUID,
        request_id: str | None = None,
        actor: str | None = None,
        source: str = "api",
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.request_id = request_id
        self.actor = actor
        self.audit = AuditService(db, tenant_id, request_id=request_id, actor=actor, source=source)

    def create(self, payload: AssessmentTemplateCreate) -> AssessmentTemplate:
        if repository.find_template_by_key(self.db, self.tenant_id, payload.key) is not None:
            raise DuplicateAssessmentTemplateError(
                f"assessment template '{payload.key}' already exists"
            )
        template = repository.create_template(
            self.db,
            id=uuid4(),
            tenant_id=self.tenant_id,
            key=payload.key,
            name=payload.name,
            description=payload.description,
            assessment_type=payload.assessment_type.value,
            version=1,
            is_system=False,
            is_active=payload.is_active,
        )
        try:
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            raise DuplicateAssessmentTemplateError(
                f"assessment template '{payload.key}' already exists"
            ) from None

        self.audit.record_event(
            event_type=AuditEventType.ASSESSMENT_TEMPLATE_CREATED,
            resource_type="assessment_template",
            resource_id=str(template.id),
            after_state=template_audit_state(template),
        )
        repository.commit_template(self.db)
        self._log("assessment template created", template, ["created"])
        return template

    def get(self, template_id: UUID) -> AssessmentTemplate:
        return self._entity(template_id)

    def get_detail(
        self, template_id: UUID
    ) -> tuple[AssessmentTemplate, list[AssessmentSection], dict[UUID, list[AssessmentQuestion]]]:
        template = self._entity(template_id)
        sections = repository.get_sections(self.db, template_id)
        questions = repository.get_questions_by_sections(
            self.db, [section.id for section in sections]
        )
        return template, sections, questions

    def list(self, filters: AssessmentTemplateFilter) -> tuple[list[AssessmentTemplate], int]:
        return repository.list_templates(self.db, self.tenant_id, filters)

    def update(self, template_id: UUID, payload: AssessmentTemplateUpdate) -> AssessmentTemplate:
        template = self._entity(template_id)

        before_state = template_audit_state(template)
        changed: list[str] = []
        # key/version are immutable (Ruling 4): PATCH moves metadata only,
        # so historical assessment snapshots never change meaning
        if payload.name is not None and payload.name != template.name:
            template.name = payload.name
            changed.append("name")
        if payload.description is not None and payload.description != template.description:
            template.description = payload.description
            changed.append("description")
        if (
            payload.assessment_type is not None
            and payload.assessment_type.value != template.assessment_type
        ):
            template.assessment_type = payload.assessment_type.value
            changed.append("assessment_type")
        if payload.is_active is not None and payload.is_active != template.is_active:
            template.is_active = payload.is_active
            changed.append("is_active")

        if changed:
            self.audit.record_event(
                event_type=AuditEventType.ASSESSMENT_TEMPLATE_UPDATED,
                resource_type="assessment_template",
                resource_id=str(template.id),
                before_state=before_state,
                after_state=template_audit_state(template),
                changed_fields=sorted(changed),
            )
        repository.commit_template(self.db)
        self._log("assessment template updated", template, changed)
        return template

    def _entity(self, template_id: UUID) -> AssessmentTemplate:
        template = repository.get_template(self.db, self.tenant_id, template_id)
        if template is None:
            raise AssessmentTemplateNotFoundError(f"assessment template '{template_id}' not found")
        return template

    def _log(self, message: str, template: AssessmentTemplate, changed: Sequence[str]) -> None:
        logger.info(
            message,
            extra={
                "assessment_template_id": str(template.id),
                "tenant_id": str(self.tenant_id),
                "key": template.key,
                "version": template.version,
                "is_system": template.is_system,
                "is_active": template.is_active,
                "changed_fields": changed,
                "request_id": self.request_id,
            },
        )


class AssessmentService:
    """The RF-02 workflow: answer questions, run the review state machine.

    An approved assessment is terminal — edits must go through rejection
    plus ``create_revision``, which forks a new row and copies the
    answers so audit history is never rewritten (RF-02 §32/§33).
    """

    def __init__(
        self,
        db: Session,
        tenant_id: UUID,
        request_id: str | None = None,
        actor: str | None = None,
        source: str = "api",
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.request_id = request_id
        self.actor = actor
        self.audit = AuditService(db, tenant_id, request_id=request_id, actor=actor, source=source)

    # --- create / read --------------------------------------------------------

    def create(self, payload: RiskAssessmentCreate) -> RiskAssessment:
        risk = self._risk(payload.risk_id)
        if risk.archived_at is not None:
            raise RiskArchivedError(f"risk '{payload.risk_id}' is archived")

        template = repository.get_template(self.db, self.tenant_id, payload.template_id)
        if template is None:
            raise AssessmentTemplateNotFoundError(
                f"assessment template '{payload.template_id}' not found"
            )
        if not template.is_active:
            raise AssessmentTemplateInactiveError(
                f"assessment template '{payload.template_id}' is inactive"
            )

        assessment = repository.create_assessment(
            self.db,
            id=uuid4(),
            tenant_id=self.tenant_id,
            risk_id=risk.id,
            template_id=template.id,
            template_version=template.version,
            assessment_type=payload.assessment_type.value,
            status=AssessmentStatus.DRAFT.value,
            assessor=payload.assessor,
            revision_number=1,
        )
        try:
            self.db.flush()
        except IntegrityError as exc:
            self.db.rollback()
            raise AssessmentError(
                "assessment references a risk or template that no longer exists"
            ) from exc

        self.audit.record_event(
            event_type=AuditEventType.ASSESSMENT_CREATED,
            resource_type="assessment",
            resource_id=str(assessment.id),
            after_state=assessment_audit_state(assessment),
        )
        repository.commit_assessment(self.db)
        self._log("assessment created", assessment, ["created"])
        return assessment

    def get(self, assessment_id: UUID) -> RiskAssessment:
        return self._entity(assessment_id)

    def list(self, filters: RiskAssessmentFilter) -> tuple[list[RiskAssessment], int]:
        return repository.list_assessments(self.db, self.tenant_id, filters)

    def get_detail(self, assessment_id: UUID) -> AssessmentDetailView:
        """Assessment + refs + full questionnaire + answers, ≤6 queries.

        Every question is rendered (not just active ones) so a historical
        assessment keeps displaying exactly what it asked.
        """
        assessment = self._entity(assessment_id)
        risk = self._risk(assessment.risk_id)
        asset = None
        if risk.asset_id is not None:
            asset = self._asset(risk.asset_id)
        template = repository.get_template(self.db, self.tenant_id, assessment.template_id)
        if template is None:
            raise AssessmentTemplateNotFoundError(
                f"assessment template '{assessment.template_id}' not found"
            )
        sections = repository.get_sections(self.db, template.id)
        questions_by_section = repository.get_questions_by_sections(
            self.db, [section.id for section in sections]
        )
        responses = repository.get_responses(self.db, assessment.id)
        all_questions = [question for group in questions_by_section.values() for question in group]
        return AssessmentDetailView(
            assessment=assessment,
            risk=risk,
            asset=asset,
            template=template,
            sections=sections,
            questions_by_section=questions_by_section,
            responses=responses,
            completion_percentage=self.completion_percentage(assessment, all_questions, responses),
        )

    def resolve_refs(
        self, assessments: Sequence[RiskAssessment]
    ) -> tuple[dict[UUID, Risk], dict[UUID, DiscoveredAsset], dict[UUID, AssessmentTemplate]]:
        """Risks, assets and templates for a page in at most three queries."""
        risk_ids = {assessment.risk_id for assessment in assessments}
        template_ids = {assessment.template_id for assessment in assessments}
        risks = repository.get_risks_by_ids(self.db, self.tenant_id, risk_ids)
        assets = repository.get_assets_by_ids(
            self.db,
            self.tenant_id,
            {risk.asset_id for risk in risks.values() if risk.asset_id is not None},
        )
        templates = repository.get_templates_by_ids(self.db, self.tenant_id, template_ids)
        return risks, assets, templates

    # --- answering ------------------------------------------------------------

    def save_response(
        self,
        assessment_id: UUID,
        question_id: UUID,
        payload: AssessmentResponseUpdate,
    ) -> AssessmentResponse:
        assessment = self._entity(assessment_id)
        self._require_status(assessment, EDITABLE_STATUSES, "edit")

        question = repository.get_question_for_template(
            self.db, assessment.template_id, question_id
        )
        if question is None:
            raise AssessmentQuestionNotFoundError(f"question '{question_id}' not found")
        _validate_response(question, payload.response)

        if assessment.status == AssessmentStatus.DRAFT.value:
            before_state = assessment_audit_state(assessment)
            assessment.status = AssessmentStatus.IN_PROGRESS.value
            self.audit.record_event(
                event_type=AuditEventType.ASSESSMENT_STARTED,
                resource_type="assessment",
                resource_id=str(assessment.id),
                before_state=before_state,
                after_state=assessment_audit_state(assessment),
                changed_fields=["status"],
            )

        response = repository.upsert_response(
            self.db,
            assessment.id,
            question.id,
            payload.response,
            payload.answered_by,
        )
        self.audit.record_event(
            event_type=AuditEventType.ASSESSMENT_RESPONSE_UPDATED,
            resource_type="assessment",
            resource_id=str(assessment.id),
            after_state=assessment_audit_state(assessment),
            metadata={"question_key": question.key},
            changed_fields=["responses"],
        )
        repository.commit_assessment(self.db)
        self._log("assessment response updated", assessment, ["responses"])
        return response

    # --- lifecycle ------------------------------------------------------------

    def submit(self, assessment_id: UUID) -> RiskAssessment:
        assessment = self._entity(assessment_id)
        self._require_status(assessment, EDITABLE_STATUSES, "submit")

        questions = repository.get_questions_for_template(self.db, assessment.template_id)
        responses = repository.get_responses(self.db, assessment.id)
        missing = [
            question.key
            for question in questions
            if question.required and not _is_answered(responses.get(question.id))
        ]
        if missing:
            raise AssessmentIncompleteError(
                "assessment cannot be submitted: unanswered required questions: "
                + ", ".join(missing[:5])
            )

        before_state = assessment_audit_state(assessment)
        assessment.status = AssessmentStatus.SUBMITTED.value
        assessment.submitted_at = datetime.now(UTC)
        self.audit.record_event(
            event_type=AuditEventType.ASSESSMENT_SUBMITTED,
            resource_type="assessment",
            resource_id=str(assessment.id),
            before_state=before_state,
            after_state=assessment_audit_state(assessment),
            changed_fields=["status", "submitted_at"],
        )
        repository.commit_assessment(self.db)
        self._log("assessment submitted", assessment, ["submitted"])
        return assessment

    def start_review(self, assessment_id: UUID) -> RiskAssessment:
        assessment = self._entity(assessment_id)
        self._require_status(assessment, {AssessmentStatus.SUBMITTED}, "start review of")

        before_state = assessment_audit_state(assessment)
        assessment.status = AssessmentStatus.UNDER_REVIEW.value
        self.audit.record_event(
            event_type=AuditEventType.ASSESSMENT_REVIEW_STARTED,
            resource_type="assessment",
            resource_id=str(assessment.id),
            before_state=before_state,
            after_state=assessment_audit_state(assessment),
            changed_fields=["status"],
        )
        repository.commit_assessment(self.db)
        self._log("assessment review started", assessment, ["under_review"])
        return assessment

    def approve(
        self, assessment_id: UUID, payload: AssessmentApproveRequest | None
    ) -> RiskAssessment:
        assessment = self._entity(assessment_id)
        self._require_status(assessment, {AssessmentStatus.UNDER_REVIEW}, "approve")

        before_state = assessment_audit_state(assessment)
        if payload is not None and payload.reviewer is not None:
            assessment.reviewer = payload.reviewer
        now = datetime.now(UTC)
        assessment.status = AssessmentStatus.APPROVED.value
        assessment.reviewed_at = now
        assessment.approved_at = now
        self.audit.record_event(
            event_type=AuditEventType.ASSESSMENT_APPROVED,
            resource_type="assessment",
            resource_id=str(assessment.id),
            before_state=before_state,
            after_state=assessment_audit_state(assessment),
            changed_fields=["status", "reviewer", "reviewed_at", "approved_at"],
        )
        repository.commit_assessment(self.db)
        self._log("assessment approved", assessment, ["approved"])
        return assessment

    def reject(self, assessment_id: UUID, payload: AssessmentRejectRequest) -> RiskAssessment:
        assessment = self._entity(assessment_id)
        self._require_status(assessment, {AssessmentStatus.UNDER_REVIEW}, "reject")

        before_state = assessment_audit_state(assessment)
        if payload.reviewer is not None:
            assessment.reviewer = payload.reviewer
        assessment.status = AssessmentStatus.REJECTED.value
        assessment.rejection_reason = payload.reason
        assessment.rejected_at = datetime.now(UTC)
        self.audit.record_event(
            event_type=AuditEventType.ASSESSMENT_REJECTED,
            resource_type="assessment",
            resource_id=str(assessment.id),
            before_state=before_state,
            after_state=assessment_audit_state(assessment),
            changed_fields=["status", "rejection_reason", "rejected_at"],
        )
        repository.commit_assessment(self.db)
        self._log("assessment rejected", assessment, ["rejected"])
        return assessment

    def create_revision(self, assessment_id: UUID) -> RiskAssessment:
        """Fork a rejected assessment into a fresh draft (Ruling 7).

        The predecessor row is untouched history; the new row inherits
        its answers so the assessor revises instead of restarting.
        """
        predecessor = self._entity(assessment_id)
        self._require_status(predecessor, {AssessmentStatus.REJECTED}, "revise")

        revision = repository.create_assessment(
            self.db,
            id=uuid4(),
            tenant_id=predecessor.tenant_id,
            risk_id=predecessor.risk_id,
            template_id=predecessor.template_id,
            template_version=predecessor.template_version,
            assessment_type=predecessor.assessment_type,
            status=AssessmentStatus.DRAFT.value,
            assessor=predecessor.assessor,
            revision_number=predecessor.revision_number + 1,
            original_assessment_id=predecessor.id,
        )
        try:
            self.db.flush()
        except IntegrityError as exc:
            self.db.rollback()
            raise AssessmentError(
                "revision references a risk or template that no longer exists"
            ) from exc

        for response in repository.get_responses(self.db, predecessor.id).values():
            self.db.add(
                AssessmentResponse(
                    assessment_id=revision.id,
                    question_id=response.question_id,
                    response_value=response.response_value,
                    answered_by=response.answered_by,
                )
            )

        self.audit.record_event(
            event_type=AuditEventType.ASSESSMENT_REVISED,
            resource_type="assessment",
            resource_id=str(revision.id),
            after_state=assessment_audit_state(revision),
            metadata={"original_assessment_id": str(predecessor.id)},
        )
        repository.commit_assessment(self.db)
        self._log("assessment revised", revision, ["revision"])
        return revision

    # --- helpers ----------------------------------------------------------------

    @staticmethod
    def completion_percentage(
        assessment: RiskAssessment | None,
        questions: Sequence[AssessmentQuestion],
        responses: dict[UUID, AssessmentResponse],
    ) -> int:
        """Answered required / total required × 100 (Ruling 8).

        A progress metric only — never a score (RF-03 boundary). Optional
        questions never enter the denominator; a template without required
        questions reports 100.
        """
        required = [question for question in questions if question.required]
        if not required:
            return 100
        answered = sum(1 for question in required if _is_answered(responses.get(question.id)))
        return round(answered * 100 / len(required))

    def completion_map(self, assessments: Sequence[RiskAssessment]) -> dict[UUID, int]:
        """Completion % for a whole list page in two batched queries."""
        if not assessments:
            return {}
        questions_by_template = repository.get_questions_for_templates(
            self.db, {a.template_id for a in assessments}
        )
        responses_by_assessment = repository.get_responses_for_assessments(
            self.db, {a.id for a in assessments}
        )
        return {
            assessment.id: self.completion_percentage(
                assessment,
                questions_by_template.get(assessment.template_id, []),
                responses_by_assessment.get(assessment.id, {}),
            )
            for assessment in assessments
        }

    def _entity(self, assessment_id: UUID) -> RiskAssessment:
        assessment = repository.get_assessment(self.db, self.tenant_id, assessment_id)
        if assessment is None:
            raise AssessmentNotFoundError(f"assessment '{assessment_id}' not found")
        return assessment

    def _risk(self, risk_id: UUID) -> Risk:
        risk = self.db.scalar(
            select(Risk).where(Risk.tenant_id == self.tenant_id, Risk.id == risk_id)
        )
        if risk is None:
            raise RiskNotFoundError(f"risk '{risk_id}' not found")
        return risk

    def _asset(self, asset_id: UUID) -> DiscoveredAsset | None:
        asset = repository.get_assets_by_ids(self.db, self.tenant_id, {asset_id})
        return asset.get(asset_id)

    def _require_status(
        self, assessment: RiskAssessment, allowed: frozenset | set, action: str
    ) -> None:
        if assessment.status not in allowed:
            raise AssessmentInvalidStateError(
                f"Cannot {action} an assessment with status '{assessment.status}'."
            )

    def _log(self, message: str, assessment: RiskAssessment, changed: Sequence[str]) -> None:
        logger.info(
            message,
            extra={
                "risk_assessment_id": str(assessment.id),
                "tenant_id": str(self.tenant_id),
                "risk_id": str(assessment.risk_id),
                "template_id": str(assessment.template_id),
                "status": assessment.status,
                "revision_number": assessment.revision_number,
                "changed_fields": changed,
                "request_id": self.request_id,
            },
        )
