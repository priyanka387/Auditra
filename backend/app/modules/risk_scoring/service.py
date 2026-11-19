"""Risk Scoring services (RF-03): configurable frameworks, scoring workflow,
append-only history, overview aggregation, tenant isolation, audit.

Follows the RF-01/RF-02 pattern: tenant-scoped entity lookups (unknown and
cross-tenant ids are indistinguishable 404s), governance mutations staged
with ``AuditService.record_event`` in the same transaction, then a single
commit. All arithmetic delegates to the pure ``engine`` — nothing here
hard-codes a band, range, or priority (plan §3).
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Sequence
from itertools import pairwise
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.assessment.errors import AssessmentNotFoundError
from app.modules.assessment.models import RiskAssessment
from app.modules.audit.enums import AuditEventType
from app.modules.audit.service import AuditService
from app.modules.risk import repository as risk_repository
from app.modules.risk.errors import RiskArchivedError, RiskNotFoundError
from app.modules.risk.models import Risk
from app.modules.risk_scoring import repository
from app.modules.risk_scoring.engine import (
    PriorityClassifier,
    ScoreCalculator,
    SeverityClassifier,
)
from app.modules.risk_scoring.enums import ScoringMethod
from app.modules.risk_scoring.errors import (
    DuplicateScoringFrameworkError,
    InvalidRiskScoreError,
    InvalidScoringFrameworkError,
    RiskScoreNotFoundError,
    ScoringFrameworkInactiveError,
    ScoringFrameworkNotFoundError,
    UnsupportedScoringMethodError,
)
from app.modules.risk_scoring.models import (
    RiskImpactLevel,
    RiskLikelihoodLevel,
    RiskScore,
    RiskScoringFramework,
    RiskSeverityThreshold,
)
from app.modules.risk_scoring.schemas import (
    MatrixCell,
    OverviewTopRisk,
    RiskDistributionResponse,
    RiskScoreCreate,
    RiskScoreFilter,
    RiskScoreOverrideRequest,
    RiskScoreResponse,
    ScoringFrameworkCreate,
    ScoringFrameworkDetailResponse,
    ScoringFrameworkFilter,
    ScoringFrameworkRef,
    ScoringFrameworkUpdate,
    ScoringOverviewResponse,
    SeverityThresholdInput,
)

logger = logging.getLogger("auditra.risk_scoring")


def score_audit_state(score: RiskScore) -> dict:
    """Governance projection of a score row for audit state (RF-03 §37)."""
    return {
        "risk_id": str(score.risk_id),
        "framework_id": str(score.framework_id),
        "framework_version": score.framework_version,
        "likelihood": score.likelihood_value,
        "impact": score.impact_value,
        "calculated_score": score.calculated_score,
        "effective_score": score.effective_score,
        "severity": score.severity,
        "priority": score.priority,
        "override_score": score.override_score,
        "override_reason": score.override_reason,
        "is_override": score.is_override,
        "is_current": score.is_current,
        "assessment_id": str(score.assessment_id) if score.assessment_id else None,
        "source": score.source,
    }


def framework_audit_state(framework: RiskScoringFramework) -> dict:
    """Governance projection of a framework for audit state."""
    return {
        "name": framework.name,
        "description": framework.description,
        "version": framework.version,
        "method": framework.method,
        "is_system": framework.is_system,
        "is_active": framework.is_active,
    }


class ScoringFrameworkService:
    """CRUD over per-tenant scoring configuration.

    Frameworks created here are always ``is_system=False``; the seeded
    Standard AI Risk Matrix is the only system framework. Configuration
    is validated as a whole (contiguous bands covering exactly the
    achievable score range, unique severities and priorities) so the
    engine can always classify any score it can calculate (RF-03 §13).
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

    def create(self, payload: ScoringFrameworkCreate) -> RiskScoringFramework:
        if (
            repository.find_framework_by_name_version(
                self.db, self.tenant_id, payload.name, payload.version
            )
            is not None
        ):
            raise DuplicateScoringFrameworkError(
                f"scoring framework '{payload.name}' version {payload.version} already exists"
            )
        self.validate_config(
            payload.method,
            [level.value for level in payload.likelihood_levels],
            [level.value for level in payload.impact_levels],
            payload.severity_thresholds,
        )

        framework = repository.create_framework(
            self.db,
            id=uuid4(),
            tenant_id=self.tenant_id,
            name=payload.name,
            description=payload.description,
            version=payload.version,
            method=payload.method.value,
            is_system=False,
            is_active=payload.is_active,
        )
        try:
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            raise DuplicateScoringFrameworkError(
                f"scoring framework '{payload.name}' version {payload.version} already exists"
            ) from None

        for position, level in enumerate(payload.likelihood_levels, start=1):
            self.db.add(
                RiskLikelihoodLevel(
                    id=uuid4(),
                    framework_id=framework.id,
                    value=level.value,
                    name=level.name,
                    description=level.description,
                    display_order=level.display_order or position,
                    is_active=level.is_active,
                )
            )
        for position, level in enumerate(payload.impact_levels, start=1):
            self.db.add(
                RiskImpactLevel(
                    id=uuid4(),
                    framework_id=framework.id,
                    value=level.value,
                    name=level.name,
                    description=level.description,
                    display_order=level.display_order or position,
                    is_active=level.is_active,
                )
            )
        for position, threshold in enumerate(payload.severity_thresholds, start=1):
            self.db.add(
                RiskSeverityThreshold(
                    id=uuid4(),
                    framework_id=framework.id,
                    minimum_score=threshold.minimum_score,
                    maximum_score=threshold.maximum_score,
                    severity=threshold.severity.value,
                    priority=threshold.priority,
                    display_order=threshold.display_order or position,
                )
            )

        self.audit.record_event(
            event_type=AuditEventType.SCORING_FRAMEWORK_CREATED,
            resource_type="scoring_framework",
            resource_id=str(framework.id),
            after_state=framework_audit_state(framework),
        )
        repository.commit_framework(self.db)
        logger.info(
            "scoring framework created",
            extra={
                "framework_id": str(framework.id),
                "tenant_id": str(self.tenant_id),
                "framework_name": framework.name,
                "version": framework.version,
                "request_id": self.request_id,
            },
        )
        return framework

    def get(self, framework_id: UUID) -> RiskScoringFramework:
        return self._entity(framework_id)

    def get_detail(
        self, framework_id: UUID
    ) -> tuple[
        RiskScoringFramework,
        list,
        list,
        list[RiskSeverityThreshold],
    ]:
        framework = self._entity(framework_id)
        likelihoods, impacts, thresholds = repository.get_framework_config(self.db, framework.id)
        return framework, likelihoods, impacts, thresholds

    def list(self, filters: ScoringFrameworkFilter) -> tuple[list[RiskScoringFramework], int]:
        return repository.list_frameworks(self.db, self.tenant_id, filters)

    def update(self, framework_id: UUID, payload: ScoringFrameworkUpdate) -> RiskScoringFramework:
        """Partial update. Version, method, levels and thresholds are never
        touched (Ruling 4) — a changed methodology is a new version row."""
        framework = self._entity(framework_id)

        before_state = framework_audit_state(framework)
        changed: list[str] = []
        if payload.name is not None and payload.name != framework.name:
            framework.name = payload.name
            changed.append("name")
        if payload.description is not None and payload.description != framework.description:
            framework.description = payload.description
            changed.append("description")
        if payload.is_active is not None and payload.is_active != framework.is_active:
            framework.is_active = payload.is_active
            changed.append("is_active")

        if changed:
            self.audit.record_event(
                event_type=AuditEventType.SCORING_FRAMEWORK_UPDATED,
                resource_type="scoring_framework",
                resource_id=str(framework.id),
                before_state=before_state,
                after_state=framework_audit_state(framework),
                changed_fields=sorted(changed),
            )
        repository.commit_framework(self.db)
        return framework

    @staticmethod
    def validate_config(
        method: str,
        likelihoods: list[int],
        impacts: list[int],
        thresholds: list[SeverityThresholdInput],
    ) -> None:
        """Reject configurations the engine could not classify (RF-03 §13/§51).

        Bands must be contiguous, unique, and cover exactly the score
        range the configured scales can produce — no overlaps, no gaps,
        no dead zones where a calculated score has no severity.
        """
        if method != ScoringMethod.LIKELIHOOD_IMPACT:
            raise UnsupportedScoringMethodError(
                f"scoring method '{method}' has no engine implementation"
            )
        if not likelihoods or not impacts:
            raise InvalidScoringFrameworkError("likelihood and impact scales must not be empty")
        for label, values in (("likelihood", likelihoods), ("impact", impacts)):
            if any(value < 1 for value in values):
                raise InvalidScoringFrameworkError(f"{label} values must be >= 1")
            if len(set(values)) != len(values):
                raise InvalidScoringFrameworkError(f"{label} values must be unique")
        if not thresholds:
            raise InvalidScoringFrameworkError("at least one severity threshold is required")

        severities = [threshold.severity for threshold in thresholds]
        if len(set(severities)) != len(severities):
            raise InvalidScoringFrameworkError("severity ratings must be unique")
        priorities = [threshold.priority for threshold in thresholds]
        if len(set(priorities)) != len(priorities):
            raise InvalidScoringFrameworkError("threshold priorities must be unique")

        ordered = sorted(thresholds, key=lambda threshold: threshold.minimum_score)
        for threshold in ordered:
            if threshold.minimum_score > threshold.maximum_score:
                raise InvalidScoringFrameworkError(
                    f"threshold '{threshold.severity}' has minimum_score "
                    f"{threshold.minimum_score} above maximum_score {threshold.maximum_score}"
                )

        min_possible = min(likelihoods) * min(impacts)
        max_possible = max(likelihoods) * max(impacts)
        first, last = ordered[0], ordered[-1]
        if first.minimum_score != min_possible:
            raise InvalidScoringFrameworkError(
                f"severity thresholds must start at {min_possible} "
                f"(the minimum achievable score), got {first.minimum_score}"
            )
        if last.maximum_score != max_possible:
            raise InvalidScoringFrameworkError(
                f"severity thresholds must cover up to {max_possible} "
                f"(the maximum achievable score), got {last.maximum_score}"
            )
        for previous, following in pairwise(ordered):
            if following.minimum_score != previous.maximum_score + 1:
                raise InvalidScoringFrameworkError(
                    f"severity thresholds must be contiguous: '{previous.severity}' ends at "
                    f"{previous.maximum_score} but '{following.severity}' starts at "
                    f"{following.minimum_score}"
                )

    def framework_detail_response(
        self,
        framework: RiskScoringFramework,
        likelihoods: list,
        impacts: list,
        thresholds: list[RiskSeverityThreshold],
    ) -> ScoringFrameworkDetailResponse:
        """Assemble the detail payload from the already-fetched config."""
        return ScoringFrameworkDetailResponse(
            id=framework.id,
            tenant_id=framework.tenant_id,
            name=framework.name,
            description=framework.description,
            version=framework.version,
            method=framework.method,
            is_system=framework.is_system,
            is_active=framework.is_active,
            created_at=framework.created_at,
            updated_at=framework.updated_at,
            likelihood_levels=likelihoods,
            impact_levels=impacts,
            severity_thresholds=thresholds,
        )

    def _entity(self, framework_id: UUID) -> RiskScoringFramework:
        framework = repository.get_framework(self.db, self.tenant_id, framework_id)
        if framework is None:
            raise ScoringFrameworkNotFoundError(f"scoring framework '{framework_id}' not found")
        return framework


class RiskScoringService:
    """The RF-03 scoring workflow: score, re-score, override, aggregate.

    Every mutation appends a new ``RiskScore`` row and flips ``is_current``
    — history is never rewritten (RF-03 §37), the calculated score is
    never replaced by an override (§21), and archived risks reject scoring
    with a 409 (§30).
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

    # --- engine delegation ---------------------------------------------------

    @staticmethod
    def calculate_score(likelihood: int, impact: int, framework: RiskScoringFramework) -> int:
        return ScoreCalculator.calculate(likelihood, impact, framework)

    @staticmethod
    def classify_severity(score: int, thresholds: Sequence[RiskSeverityThreshold]) -> str:
        return SeverityClassifier.classify(score, thresholds)

    @staticmethod
    def calculate_priority(severity: str, thresholds: Sequence[RiskSeverityThreshold]) -> int:
        return PriorityClassifier.assign(severity, thresholds)

    # --- scoring workflow ----------------------------------------------------

    def score_risk(self, risk_id: UUID, payload: RiskScoreCreate) -> RiskScore:
        risk = self._risk(risk_id)
        if risk.archived_at is not None:
            raise RiskArchivedError(f"risk '{risk_id}' is archived")

        framework = repository.get_framework(self.db, self.tenant_id, payload.framework_id)
        if framework is None:
            raise ScoringFrameworkNotFoundError(
                f"scoring framework '{payload.framework_id}' not found"
            )
        if not framework.is_active:
            raise ScoringFrameworkInactiveError(f"scoring framework '{framework.name}' is inactive")

        likelihood_level = repository.get_likelihood_level(
            self.db, framework.id, payload.likelihood
        )
        if likelihood_level is None or not likelihood_level.is_active:
            raise InvalidRiskScoreError(
                f"likelihood value {payload.likelihood} is not valid "
                f"for framework '{framework.name}'"
            )
        impact_level = repository.get_impact_level(self.db, framework.id, payload.impact)
        if impact_level is None or not impact_level.is_active:
            raise InvalidRiskScoreError(
                f"impact value {payload.impact} is not valid for framework '{framework.name}'"
            )

        assessment_id = self._assessment(risk_id, payload.assessment_id)

        _, _, thresholds = repository.get_framework_config(self.db, framework.id)
        calculated = self.calculate_score(payload.likelihood, payload.impact, framework)
        severity = self.classify_severity(calculated, thresholds)
        priority = self.calculate_priority(severity, thresholds)

        repository.clear_current_score(self.db, self.tenant_id, risk_id)
        score = repository.create_score(
            self.db,
            id=uuid4(),
            tenant_id=self.tenant_id,
            risk_id=risk_id,
            framework_id=framework.id,
            framework_version=framework.version,
            likelihood_value=payload.likelihood,
            likelihood_label=likelihood_level.name,
            impact_value=payload.impact,
            impact_label=impact_level.name,
            calculated_score=calculated,
            severity=severity,
            priority=priority,
            override_score=None,
            effective_score=calculated,
            override_reason=None,
            is_inherent=True,
            is_current=True,
            is_override=False,
            assessment_id=assessment_id,
            source="assessment" if assessment_id is not None else "manual",
            scored_by=self.actor,
        )
        self.db.flush()

        self.audit.record_event(
            event_type=AuditEventType.RISK_SCORE_CREATED,
            resource_type="risk",
            resource_id=str(risk_id),
            after_state=score_audit_state(score),
            metadata={"score_id": str(score.id)},
        )
        repository.commit_score(self.db)
        logger.info(
            "risk scored",
            extra={
                "risk_id": str(risk_id),
                "score_id": str(score.id),
                "tenant_id": str(self.tenant_id),
                "calculated_score": calculated,
                "severity": severity,
                "request_id": self.request_id,
            },
        )
        return score

    def get_current_score(self, risk_id: UUID) -> RiskScore:
        self._risk(risk_id)
        score = repository.get_current_score(self.db, self.tenant_id, risk_id)
        if score is None:
            raise RiskScoreNotFoundError(f"risk '{risk_id}' has no score yet")
        return score

    def get_score_history(
        self, risk_id: UUID, filters: RiskScoreFilter
    ) -> tuple[list[RiskScore], int]:
        self._risk(risk_id)
        return repository.list_scores(self.db, self.tenant_id, risk_id, filters)

    def recalculate_risk(self, risk_id: UUID) -> RiskScore:
        """Re-run the engine for a risk's current inputs under its current
        framework — a NEW row; the predecessor keeps its numbers (plan §35)."""
        self._risk(risk_id)
        previous = repository.get_current_score(self.db, self.tenant_id, risk_id)
        if previous is None:
            raise RiskScoreNotFoundError(f"risk '{risk_id}' has no score yet")

        framework = repository.get_framework(self.db, self.tenant_id, previous.framework_id)
        if framework is None:  # FK guarantees existence; defensive
            raise ScoringFrameworkNotFoundError(
                f"scoring framework '{previous.framework_id}' not found"
            )
        _, _, thresholds = repository.get_framework_config(self.db, framework.id)
        calculated = self.calculate_score(
            previous.likelihood_value, previous.impact_value, framework
        )
        severity = self.classify_severity(calculated, thresholds)
        priority = self.calculate_priority(severity, thresholds)

        repository.clear_current_score(self.db, self.tenant_id, risk_id)
        score = repository.create_score(
            self.db,
            id=uuid4(),
            tenant_id=self.tenant_id,
            risk_id=risk_id,
            framework_id=previous.framework_id,
            framework_version=previous.framework_version,
            likelihood_value=previous.likelihood_value,
            likelihood_label=previous.likelihood_label,
            impact_value=previous.impact_value,
            impact_label=previous.impact_label,
            calculated_score=calculated,
            severity=severity,
            priority=priority,
            override_score=None,
            effective_score=calculated,
            override_reason=None,
            is_inherent=previous.is_inherent,
            is_current=True,
            is_override=False,
            assessment_id=previous.assessment_id,
            source=previous.source,
            scored_by=previous.scored_by,
        )
        self.db.flush()

        self.audit.record_event(
            event_type=AuditEventType.RISK_SCORE_RECALCULATED,
            resource_type="risk",
            resource_id=str(risk_id),
            before_state=score_audit_state(previous),
            after_state=score_audit_state(score),
            metadata={"score_id": str(score.id), "previous_score_id": str(previous.id)},
        )
        repository.commit_score(self.db)
        return score

    def override_score(self, risk_id: UUID, payload: RiskScoreOverrideRequest) -> RiskScore:
        """Record a justified manual rating: a new row that preserves the
        engine's result and carries the effective score (RF-03 §21)."""
        self._risk(risk_id)
        previous = repository.get_current_score(self.db, self.tenant_id, risk_id)
        if previous is None:
            raise RiskScoreNotFoundError(f"risk '{risk_id}' has no score yet")

        framework = repository.get_framework(self.db, self.tenant_id, previous.framework_id)
        if framework is None:  # FK guarantees existence; defensive
            raise ScoringFrameworkNotFoundError(
                f"scoring framework '{previous.framework_id}' not found"
            )
        likelihoods, impacts, thresholds = repository.get_framework_config(self.db, framework.id)
        min_possible = min(level.value for level in likelihoods) * min(
            level.value for level in impacts
        )
        max_possible = max(level.value for level in likelihoods) * max(
            level.value for level in impacts
        )
        if not min_possible <= payload.override_score <= max_possible:
            raise InvalidRiskScoreError(
                f"override score must be between {min_possible} and {max_possible} "
                f"for framework '{framework.name}'"
            )

        severity = self.classify_severity(payload.override_score, thresholds)
        priority = self.calculate_priority(severity, thresholds)

        repository.clear_current_score(self.db, self.tenant_id, risk_id)
        score = repository.create_score(
            self.db,
            id=uuid4(),
            tenant_id=self.tenant_id,
            risk_id=risk_id,
            framework_id=previous.framework_id,
            framework_version=previous.framework_version,
            likelihood_value=previous.likelihood_value,
            likelihood_label=previous.likelihood_label,
            impact_value=previous.impact_value,
            impact_label=previous.impact_label,
            calculated_score=previous.calculated_score,
            severity=severity,
            priority=priority,
            override_score=payload.override_score,
            effective_score=payload.override_score,
            override_reason=payload.reason,
            is_inherent=previous.is_inherent,
            is_current=True,
            is_override=True,
            assessment_id=previous.assessment_id,
            source=previous.source,
            scored_by=self.actor,
        )
        self.db.flush()

        self.audit.record_event(
            event_type=AuditEventType.RISK_SCORE_OVERRIDDEN,
            resource_type="risk",
            resource_id=str(risk_id),
            before_state=score_audit_state(previous),
            after_state=score_audit_state(score),
            metadata={"score_id": str(score.id), "previous_score_id": str(previous.id)},
        )
        repository.commit_score(self.db)
        logger.info(
            "risk score overridden",
            extra={
                "risk_id": str(risk_id),
                "score_id": str(score.id),
                "tenant_id": str(self.tenant_id),
                "override_score": payload.override_score,
                "request_id": self.request_id,
            },
        )
        return score

    # --- overview -------------------------------------------------------------

    def get_overview(self, framework_id: UUID | None) -> ScoringOverviewResponse:
        if framework_id is not None:
            framework = repository.get_framework(self.db, self.tenant_id, framework_id)
            if framework is None:
                raise ScoringFrameworkNotFoundError(f"scoring framework '{framework_id}' not found")
        else:
            framework = repository.find_default_framework(self.db, self.tenant_id)
            if framework is None:
                raise ScoringFrameworkNotFoundError(
                    "no active scoring framework configured for this tenant"
                )

        likelihoods, impacts, _thresholds = repository.get_framework_config(self.db, framework.id)
        active_likelihoods = [level for level in likelihoods if level.is_active]
        active_impacts = [level for level in impacts if level.is_active]

        rows = repository.current_scores_with_risk(self.db, self.tenant_id)
        distribution_counts = Counter(score.severity for score, _ in rows)
        distribution = RiskDistributionResponse(
            critical=distribution_counts.get("critical", 0),
            high=distribution_counts.get("high", 0),
            medium=distribution_counts.get("medium", 0),
            low=distribution_counts.get("low", 0),
            unscored=repository.count_unscored_active_risks(self.db, self.tenant_id),
        )

        # full cartesian grid so the heatmap renders empty cells too;
        # scores whose inputs fall outside the active grid stay in the
        # distribution but are not placed in any cell
        cell_risk_ids: dict[tuple[int, int], list[UUID]] = {
            (level.value, impact.value): []
            for level in active_likelihoods
            for impact in active_impacts
        }
        for score, _ in rows:
            key = (score.likelihood_value, score.impact_value)
            if key in cell_risk_ids:
                cell_risk_ids[key].append(score.risk_id)
        cells = [
            MatrixCell(
                likelihood=likelihood_value,
                impact=impact_value,
                count=len(risk_ids),
                risk_ids=risk_ids,
            )
            for (likelihood_value, impact_value), risk_ids in cell_risk_ids.items()
        ]

        ranked = sorted(rows, key=lambda row: (row[0].priority, -row[0].effective_score))
        top_risks = [
            OverviewTopRisk(
                risk_id=score.risk_id,
                title=risk.title,
                severity=score.severity,
                effective_score=score.effective_score,
                priority=score.priority,
            )
            for score, risk in ranked[:10]
        ]

        return ScoringOverviewResponse(
            framework=framework,
            distribution=distribution,
            likelihood_levels=active_likelihoods,
            impact_levels=active_impacts,
            cells=cells,
            top_risks=top_risks,
        )

    # --- response helpers -----------------------------------------------------

    def score_response(self, score: RiskScore) -> RiskScoreResponse:
        """Build an API response with the embedded framework ref — the ORM
        row alone has no nested ``framework`` attribute to map onto."""
        framework = repository.get_framework(self.db, self.tenant_id, score.framework_id)
        if framework is None:  # FK guarantees existence; defensive
            raise ScoringFrameworkNotFoundError(
                f"scoring framework '{score.framework_id}' not found"
            )
        ref = ScoringFrameworkRef(
            id=framework.id,
            name=framework.name,
            version=framework.version,
            method=framework.method,
        )
        return RiskScoreResponse(
            id=score.id,
            risk_id=score.risk_id,
            framework=ref,
            framework_version=score.framework_version,
            likelihood=score.likelihood_value,
            likelihood_label=score.likelihood_label,
            impact=score.impact_value,
            impact_label=score.impact_label,
            calculated_score=score.calculated_score,
            effective_score=score.effective_score,
            severity=score.severity,
            priority=score.priority,
            override_score=score.override_score,
            override_reason=score.override_reason,
            is_override=score.is_override,
            is_inherent=score.is_inherent,
            is_current=score.is_current,
            assessment_id=score.assessment_id,
            source=score.source,
            scored_by=score.scored_by,
            created_at=score.created_at,
        )

    # --- internals -------------------------------------------------------------

    def _risk(self, risk_id: UUID) -> Risk:
        risk = risk_repository.get_risk(self.db, self.tenant_id, risk_id)
        if risk is None:
            raise RiskNotFoundError(f"risk '{risk_id}' not found")
        return risk

    def _assessment(self, risk_id: UUID, assessment_id: UUID | None) -> UUID | None:
        if assessment_id is None:
            return None
        assessment = self.db.scalar(
            select(RiskAssessment).where(
                RiskAssessment.tenant_id == self.tenant_id,
                RiskAssessment.id == assessment_id,
            )
        )
        if assessment is None:
            raise AssessmentNotFoundError(f"assessment '{assessment_id}' not found")
        if assessment.risk_id != risk_id:
            raise InvalidRiskScoreError(f"assessment '{assessment_id}' belongs to a different risk")
        return assessment.id
