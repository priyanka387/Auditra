"""Tenant-scoped persistence for RF-03 scoring frameworks and scores.

Mirrors ``risk/repository.py``: every read is filtered by ``tenant_id``
(or by a framework/risk the caller already tenant-checked), list queries
compose optional filters over whitelisted sort maps, and commit helpers
translate constraint races into the module's domain errors.

Score history is append-only: the only UPDATE ever issued is the
``is_current`` flip in ``clear_current_score`` (RF-03 §37 — no score row
is mutated after insert).
"""

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.risk.models import Risk
from app.modules.risk_scoring.errors import (
    DuplicateScoringFrameworkError,
    InvalidRiskScoreQueryError,
    InvalidScoringFrameworkQueryError,
    RiskScoringError,
)
from app.modules.risk_scoring.models import (
    RiskImpactLevel,
    RiskLikelihoodLevel,
    RiskScore,
    RiskScoringFramework,
    RiskSeverityThreshold,
)
from app.modules.risk_scoring.schemas import RiskScoreFilter, ScoringFrameworkFilter

FRAMEWORK_SORT_COLUMNS = {
    "name": RiskScoringFramework.name,
    "created_at": RiskScoringFramework.created_at,
    "updated_at": RiskScoringFramework.updated_at,
}

SCORE_SORT_COLUMNS = {
    "created_at": RiskScore.created_at,
}


# --- scoring frameworks ------------------------------------------------------


def create_framework(db: Session, **fields) -> RiskScoringFramework:
    framework = RiskScoringFramework(**fields)
    db.add(framework)
    return framework


def get_framework(db: Session, tenant_id: UUID, framework_id: UUID) -> RiskScoringFramework | None:
    return db.scalar(
        select(RiskScoringFramework).where(
            RiskScoringFramework.tenant_id == tenant_id,
            RiskScoringFramework.id == framework_id,
        )
    )


def find_framework_by_name_version(
    db: Session, tenant_id: UUID, name: str, version: int
) -> RiskScoringFramework | None:
    return db.scalar(
        select(RiskScoringFramework).where(
            RiskScoringFramework.tenant_id == tenant_id,
            RiskScoringFramework.name == name,
            RiskScoringFramework.version == version,
        )
    )


def find_default_framework(db: Session, tenant_id: UUID) -> RiskScoringFramework | None:
    """The tenant's system framework: seeded, active, oldest version first."""
    return db.scalar(
        select(RiskScoringFramework)
        .where(
            RiskScoringFramework.tenant_id == tenant_id,
            RiskScoringFramework.is_system.is_(True),
            RiskScoringFramework.is_active.is_(True),
        )
        .order_by(RiskScoringFramework.created_at, RiskScoringFramework.version)
    )


def _framework_conditions(tenant_id: UUID, filters: ScoringFrameworkFilter) -> list:
    conditions = [RiskScoringFramework.tenant_id == tenant_id]
    if filters.is_active is not None:
        conditions.append(RiskScoringFramework.is_active == filters.is_active)
    if filters.is_system is not None:
        conditions.append(RiskScoringFramework.is_system == filters.is_system)
    if filters.search is not None:
        conditions.append(RiskScoringFramework.name.ilike(f"%{filters.search}%"))
    return conditions


def list_frameworks(
    db: Session, tenant_id: UUID, filters: ScoringFrameworkFilter
) -> tuple[list[RiskScoringFramework], int]:
    conditions = _framework_conditions(tenant_id, filters)
    total = (
        db.scalar(select(func.count()).select_from(RiskScoringFramework).where(*conditions)) or 0
    )

    sort_column = FRAMEWORK_SORT_COLUMNS.get(filters.sort_by)
    if sort_column is None:
        raise InvalidScoringFrameworkQueryError(f"unsupported sort field '{filters.sort_by}'")
    ordering = sort_column.desc() if filters.sort_order == "desc" else sort_column.asc()
    items = list(
        db.scalars(
            select(RiskScoringFramework)
            .where(*conditions)
            .order_by(ordering, RiskScoringFramework.id)
            .offset((filters.page - 1) * filters.page_size)
            .limit(filters.page_size)
        )
    )
    return items, total


def get_framework_config(
    db: Session, framework_id: UUID
) -> tuple[list[RiskLikelihoodLevel], list[RiskImpactLevel], list[RiskSeverityThreshold]]:
    """Fetch a framework's full configuration in three ordered queries."""
    likelihoods = list(
        db.scalars(
            select(RiskLikelihoodLevel)
            .where(RiskLikelihoodLevel.framework_id == framework_id)
            .order_by(RiskLikelihoodLevel.display_order, RiskLikelihoodLevel.value)
        )
    )
    impacts = list(
        db.scalars(
            select(RiskImpactLevel)
            .where(RiskImpactLevel.framework_id == framework_id)
            .order_by(RiskImpactLevel.display_order, RiskImpactLevel.value)
        )
    )
    thresholds = list(
        db.scalars(
            select(RiskSeverityThreshold)
            .where(RiskSeverityThreshold.framework_id == framework_id)
            .order_by(RiskSeverityThreshold.minimum_score)
        )
    )
    return likelihoods, impacts, thresholds


def get_likelihood_level(db: Session, framework_id: UUID, value: int) -> RiskLikelihoodLevel | None:
    return db.scalar(
        select(RiskLikelihoodLevel).where(
            RiskLikelihoodLevel.framework_id == framework_id,
            RiskLikelihoodLevel.value == value,
        )
    )


def get_impact_level(db: Session, framework_id: UUID, value: int) -> RiskImpactLevel | None:
    return db.scalar(
        select(RiskImpactLevel).where(
            RiskImpactLevel.framework_id == framework_id,
            RiskImpactLevel.value == value,
        )
    )


def commit_framework(db: Session) -> None:
    """Persist a framework mutation; a race on ``(tenant, name, version)``
    surfaces as a duplicate rather than a raw IntegrityError."""
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DuplicateScoringFrameworkError(
            "scoring framework with this name and version already exists"
        ) from exc


# --- scores ------------------------------------------------------------------


def create_score(db: Session, **fields) -> RiskScore:
    score = RiskScore(**fields)
    db.add(score)
    return score


def get_current_score(db: Session, tenant_id: UUID, risk_id: UUID) -> RiskScore | None:
    return db.scalar(
        select(RiskScore).where(
            RiskScore.tenant_id == tenant_id,
            RiskScore.risk_id == risk_id,
            RiskScore.is_current.is_(True),
        )
    )


def get_current_scores_by_risk_ids(
    db: Session, tenant_id: UUID, risk_ids: set[UUID]
) -> dict[UUID, RiskScore]:
    """Current scores for a page of risks in a single query (Task 8's register)."""
    if not risk_ids:
        return {}
    rows = db.scalars(
        select(RiskScore).where(
            RiskScore.tenant_id == tenant_id,
            RiskScore.risk_id.in_(risk_ids),
            RiskScore.is_current.is_(True),
        )
    )
    return {score.risk_id: score for score in rows}


def clear_current_score(
    db: Session, tenant_id: UUID, risk_id: UUID, keep_id: UUID | None = None
) -> None:
    """Flip ``is_current`` off for a risk's other rows — the only UPDATE
    the append-only history ever performs (RF-03 §16/§37)."""
    stmt = (
        update(RiskScore)
        .where(
            RiskScore.tenant_id == tenant_id,
            RiskScore.risk_id == risk_id,
            RiskScore.is_current.is_(True),
        )
        .values(is_current=False)
        .execution_options(synchronize_session=False)
    )
    if keep_id is not None:
        stmt = stmt.where(RiskScore.id != keep_id)
    db.execute(stmt)


def list_scores(
    db: Session, tenant_id: UUID, risk_id: UUID, filters: RiskScoreFilter
) -> tuple[list[RiskScore], int]:
    conditions = [RiskScore.tenant_id == tenant_id, RiskScore.risk_id == risk_id]
    total = db.scalar(select(func.count()).select_from(RiskScore).where(*conditions)) or 0

    sort_column = SCORE_SORT_COLUMNS.get(filters.sort_by)
    if sort_column is None:
        raise InvalidRiskScoreQueryError(f"unsupported sort field '{filters.sort_by}'")
    ordering = sort_column.desc() if filters.sort_order == "desc" else sort_column.asc()
    items = list(
        db.scalars(
            select(RiskScore)
            .where(*conditions)
            .order_by(ordering, RiskScore.id)
            .offset((filters.page - 1) * filters.page_size)
            .limit(filters.page_size)
        )
    )
    return items, total


def current_scores_with_risk(db: Session, tenant_id: UUID) -> list[tuple[RiskScore, Risk]]:
    """Current scores joined to their ACTIVE risk (archived risks excluded),
    one query — the overview's aggregation source."""
    rows = db.execute(
        select(RiskScore, Risk)
        .join(Risk, RiskScore.risk_id == Risk.id)
        .where(
            RiskScore.tenant_id == tenant_id,
            RiskScore.is_current.is_(True),
            Risk.archived_at.is_(None),
        )
    )
    return [(score, risk) for score, risk in rows]


def count_unscored_active_risks(db: Session, tenant_id: UUID) -> int:
    """Active risks with no current score — reported as ``unscored``, never
    folded into ``low`` (RF-03 §28)."""
    return (
        db.scalar(
            select(func.count())
            .select_from(Risk)
            .outerjoin(
                RiskScore,
                (RiskScore.risk_id == Risk.id) & RiskScore.is_current.is_(True),
            )
            .where(Risk.tenant_id == tenant_id, Risk.archived_at.is_(None), RiskScore.id.is_(None))
        )
        or 0
    )


def commit_score(db: Session) -> None:
    """Persist a scoring transaction; a vanished framework/risk reference
    means the world changed mid-request, so roll back rather than keep a
    half-applied score."""
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise RiskScoringError(
            "score could not be saved: a referenced risk or framework no longer exists"
        ) from exc
