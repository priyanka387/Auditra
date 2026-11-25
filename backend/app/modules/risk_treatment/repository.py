"""Tenant-scoped persistence for RF-04 risk treatments.

Mirrors ``risk/repository.py``: every read and write is filtered by
``tenant_id``, list queries compose optional filters over a whitelisted
sort map, and the commit helper translates the partial-active-index race
into the module's domain error (plan §48 — the DB, not the service,
enforces one active treatment per risk).
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.risk.models import Risk
from app.modules.risk_scoring.models import RiskScore
from app.modules.risk_treatment.enums import ACTIVE_TREATMENT_STATUSES
from app.modules.risk_treatment.errors import (
    InvalidRiskTreatmentQueryError,
    RiskTreatmentActiveExistsError,
    RiskTreatmentError,
)
from app.modules.risk_treatment.metrics import OPEN_ACTION_STATUSES
from app.modules.risk_treatment.models import (
    RiskTreatment,
    RiskTreatmentAction,
    RiskTreatmentResidualAssessment,
)
from app.modules.risk_treatment.schemas import (
    ResidualAssessmentFilter,
    TreatmentActionFilter,
    TreatmentFilter,
)

# RF-04 sorts are date/audit oriented: when it was decided, when it last
# moved, and when it is due to finish (title/owner sorts are deliberately
# not offered — plan §36 keeps the list column set small)
TREATMENT_SORT_COLUMNS = {
    "created_at": RiskTreatment.created_at,
    "updated_at": RiskTreatment.updated_at,
    "target_completion_date": RiskTreatment.target_completion_date,
}


def create_treatment(db: Session, **fields) -> RiskTreatment:
    treatment = RiskTreatment(**fields)
    db.add(treatment)
    return treatment


def get_treatment(db: Session, tenant_id: UUID, treatment_id: UUID) -> RiskTreatment | None:
    return db.scalar(
        select(RiskTreatment).where(
            RiskTreatment.tenant_id == tenant_id,
            RiskTreatment.id == treatment_id,
        )
    )


def get_active_treatment(db: Session, tenant_id: UUID, risk_id: UUID) -> RiskTreatment | None:
    """The risk's current active treatment, oldest first (plan §48).

    The partial unique index guarantees at most one row, but the explicit
    ordering keeps the lookup deterministic if the invariant ever widens.
    """
    return db.scalar(
        select(RiskTreatment)
        .where(
            RiskTreatment.tenant_id == tenant_id,
            RiskTreatment.risk_id == risk_id,
            RiskTreatment.status.in_(tuple(ACTIVE_TREATMENT_STATUSES)),
        )
        .order_by(RiskTreatment.created_at.asc(), RiskTreatment.id.asc())
        .limit(1)
    )


def count_treatments_by_risk(db: Session, tenant_id: UUID, risk_id: UUID) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(RiskTreatment)
            .where(
                RiskTreatment.tenant_id == tenant_id,
                RiskTreatment.risk_id == risk_id,
            )
        )
        or 0
    )


def count_actions_for_completion(db: Session, treatment_id: UUID) -> tuple[int, int]:
    """``(total, completed)`` action counts in one query (Ruling 16).

    The complete endpoint snapshots these into audit metadata; it never
    gates on them. Lives here (not in Task 5's action repository block)
    because ``RiskTreatmentService.complete`` needs it first.
    """
    total, completed = db.execute(
        select(
            func.count(RiskTreatmentAction.id),
            func.count(RiskTreatmentAction.id).filter(RiskTreatmentAction.status == "completed"),
        ).where(RiskTreatmentAction.treatment_id == treatment_id)
    ).one()
    return int(total), int(completed)


def _treatment_conditions(tenant_id: UUID, filters: TreatmentFilter, today: date) -> list:
    conditions = [RiskTreatment.tenant_id == tenant_id]
    if filters.risk_id is not None:
        conditions.append(RiskTreatment.risk_id == filters.risk_id)
    if filters.strategy is not None:
        conditions.append(RiskTreatment.strategy == filters.strategy)
    if filters.status is not None:
        conditions.append(RiskTreatment.status == filters.status)
    if filters.owner is not None:
        conditions.append(RiskTreatment.owner == filters.owner)
    # overdue is derived, never stored (Ruling 9): active status AND a
    # target date in the past; the False branch is its exact negation so
    # completed/cancelled rows and open-ended treatments are included
    if filters.overdue is True:
        conditions.append(RiskTreatment.status.in_(tuple(ACTIVE_TREATMENT_STATUSES)))
        conditions.append(RiskTreatment.target_completion_date < today)
    elif filters.overdue is False:
        conditions.append(
            or_(
                RiskTreatment.status.notin_(tuple(ACTIVE_TREATMENT_STATUSES)),
                RiskTreatment.target_completion_date.is_(None),
                RiskTreatment.target_completion_date >= today,
            )
        )
    return conditions


def list_treatments(
    db: Session, tenant_id: UUID, filters: TreatmentFilter
) -> tuple[list[RiskTreatment], int]:
    today = datetime.now(UTC).date()
    conditions = _treatment_conditions(tenant_id, filters, today)
    total = db.scalar(select(func.count()).select_from(RiskTreatment).where(*conditions)) or 0

    sort_column = TREATMENT_SORT_COLUMNS.get(filters.sort_by)
    if sort_column is None:
        raise InvalidRiskTreatmentQueryError(f"unsupported sort field '{filters.sort_by}'")
    ordering = sort_column.desc() if filters.sort_order == "desc" else sort_column.asc()
    items = list(
        db.scalars(
            select(RiskTreatment)
            .where(*conditions)
            .order_by(ordering, RiskTreatment.id)
            .offset((filters.page - 1) * filters.page_size)
            .limit(filters.page_size)
        )
    )
    return items, total


def commit_treatment(db: Session) -> None:
    """Persist a treatment mutation.

    The partial unique index (plan §48) may reject a second *active*
    treatment when two requests race past the service's pre-check — that
    is the invariant working, so it maps to 409. Any other integrity
    failure means a referenced row vanished; roll back rather than keep a
    half-applied change.
    """
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if "uq_risk_treatments_risk_active" in str(exc.orig):
            raise RiskTreatmentActiveExistsError("risk already has an active treatment") from None
        raise RiskTreatmentError("treatment could not be saved") from exc


# --- treatment actions -------------------------------------------------------

# Actions sort on when the work was logged and when it is due (plan §36):
# the same small-whitelist approach as treatments
ACTION_SORT_COLUMNS = {
    "created_at": RiskTreatmentAction.created_at,
    "due_date": RiskTreatmentAction.due_date,
}


def create_action(db: Session, **fields) -> RiskTreatmentAction:
    action = RiskTreatmentAction(**fields)
    db.add(action)
    return action


def get_action(db: Session, tenant_id: UUID, action_id: UUID) -> RiskTreatmentAction | None:
    return db.scalar(
        select(RiskTreatmentAction).where(
            RiskTreatmentAction.tenant_id == tenant_id,
            RiskTreatmentAction.id == action_id,
        )
    )


def get_actions_by_treatment(db: Session, treatment_id: UUID) -> list[RiskTreatmentAction]:
    """Every action of a treatment in one query (progress + audit counts).

    The caller (route/service) has already resolved the treatment in its
    tenant, so this lookup keys only on the treatment id.
    """
    return list(
        db.scalars(
            select(RiskTreatmentAction)
            .where(RiskTreatmentAction.treatment_id == treatment_id)
            .order_by(RiskTreatmentAction.created_at, RiskTreatmentAction.id)
        )
    )


def count_actions_by_treatment(db: Session, treatment_id: UUID) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(RiskTreatmentAction)
            .where(RiskTreatmentAction.treatment_id == treatment_id)
        )
        or 0
    )


def list_actions(
    db: Session, tenant_id: UUID, treatment_id: UUID, filters: TreatmentActionFilter
) -> tuple[list[RiskTreatmentAction], int]:
    conditions = [
        RiskTreatmentAction.tenant_id == tenant_id,
        RiskTreatmentAction.treatment_id == treatment_id,
    ]
    if filters.status is not None:
        conditions.append(RiskTreatmentAction.status == filters.status)
    if filters.owner is not None:
        conditions.append(RiskTreatmentAction.owner == filters.owner)

    total = db.scalar(select(func.count()).select_from(RiskTreatmentAction).where(*conditions)) or 0

    sort_column = ACTION_SORT_COLUMNS.get(filters.sort_by)
    if sort_column is None:
        raise InvalidRiskTreatmentQueryError(f"unsupported sort field '{filters.sort_by}'")
    ordering = sort_column.desc() if filters.sort_order == "desc" else sort_column.asc()
    items = list(
        db.scalars(
            select(RiskTreatmentAction)
            .where(*conditions)
            .order_by(ordering, RiskTreatmentAction.id)
            .offset((filters.page - 1) * filters.page_size)
            .limit(filters.page_size)
        )
    )
    return items, total


def commit_action(db: Session) -> None:
    """Persist an action mutation.

    Actions carry no unique constraint of their own; an integrity
    failure here means the parent treatment vanished mid-request, so the
    transaction rolls back instead of half-applying the change.
    """
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise RiskTreatmentError("treatment action could not be saved") from exc


# --- expected residual assessments --------------------------------------------


def create_residual(db: Session, **fields) -> RiskTreatmentResidualAssessment:
    residual = RiskTreatmentResidualAssessment(**fields)
    db.add(residual)
    return residual


def list_residuals(
    db: Session, tenant_id: UUID, treatment_id: UUID, filters: ResidualAssessmentFilter
) -> tuple[list[RiskTreatmentResidualAssessment], int]:
    """Append-only history, newest snapshot first (plan §23)."""
    conditions = [
        RiskTreatmentResidualAssessment.tenant_id == tenant_id,
        RiskTreatmentResidualAssessment.treatment_id == treatment_id,
    ]
    total = (
        db.scalar(
            select(func.count()).select_from(RiskTreatmentResidualAssessment).where(*conditions)
        )
        or 0
    )
    items = list(
        db.scalars(
            select(RiskTreatmentResidualAssessment)
            .where(*conditions)
            .order_by(
                RiskTreatmentResidualAssessment.created_at.desc(),
                RiskTreatmentResidualAssessment.id.desc(),
            )
            .offset((filters.page - 1) * filters.page_size)
            .limit(filters.page_size)
        )
    )
    return items, total


def commit_residual(db: Session) -> None:
    """Persist a residual snapshot (append-only, no unique constraints)."""
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise RiskTreatmentError("residual assessment could not be saved") from exc


# --- treatment overview (plan §44/§45) ----------------------------------------


def count_active_risks(db: Session, tenant_id: UUID) -> int:
    """The register's live population: non-archived risks only."""
    return (
        db.scalar(
            select(func.count())
            .select_from(Risk)
            .where(Risk.tenant_id == tenant_id, Risk.archived_at.is_(None))
        )
        or 0
    )


def count_risks_with_active_treatment(db: Session, tenant_id: UUID) -> int:
    """Risks currently covered by an active treatment.

    Ruling 5's partial unique index keeps the join to at most one row
    per risk, so counting joined risks cannot fan out.
    """
    return (
        db.scalar(
            select(func.count(func.distinct(Risk.id)))
            .select_from(Risk)
            .join(
                RiskTreatment,
                and_(
                    RiskTreatment.risk_id == Risk.id,
                    RiskTreatment.status.in_(tuple(ACTIVE_TREATMENT_STATUSES)),
                ),
            )
            .where(Risk.tenant_id == tenant_id, Risk.archived_at.is_(None))
        )
        or 0
    )


def treatment_status_distribution(db: Session, tenant_id: UUID) -> dict[str, int]:
    """Status counts across ALL treatments — history included (plan §44)."""
    rows = db.execute(
        select(RiskTreatment.status, func.count())
        .where(RiskTreatment.tenant_id == tenant_id)
        .group_by(RiskTreatment.status)
    ).all()
    return {status: int(count) for status, count in rows}


def treatment_strategy_distribution(db: Session, tenant_id: UUID) -> dict[str, int]:
    rows = db.execute(
        select(RiskTreatment.strategy, func.count())
        .where(RiskTreatment.tenant_id == tenant_id)
        .group_by(RiskTreatment.strategy)
    ).all()
    return {strategy: int(count) for strategy, count in rows}


def count_overdue_actions(db: Session, tenant_id: UUID, today: date) -> int:
    """Open actions past due — the SQL twin of ``metrics.action_is_overdue``
    (Ruling 9: terminals never count)."""
    return (
        db.scalar(
            select(func.count())
            .select_from(RiskTreatmentAction)
            .where(
                RiskTreatmentAction.tenant_id == tenant_id,
                RiskTreatmentAction.status.in_(tuple(OPEN_ACTION_STATUSES)),
                RiskTreatmentAction.due_date < today,
            )
        )
        or 0
    )


def count_overdue_treatments(db: Session, tenant_id: UUID, today: date) -> int:
    """Active treatments past target — SQL twin of ``metrics.treatment_is_overdue``."""
    return (
        db.scalar(
            select(func.count())
            .select_from(RiskTreatment)
            .where(
                RiskTreatment.tenant_id == tenant_id,
                RiskTreatment.status.in_(tuple(ACTIVE_TREATMENT_STATUSES)),
                RiskTreatment.target_completion_date < today,
            )
        )
        or 0
    )


def count_untreated_risks_by_severity(
    db: Session, tenant_id: UUID, severities: Sequence[str]
) -> dict[str, int]:
    """Active risks whose *current* RF-03 score is in ``severities`` and
    that have no active treatment (plan §45).

    An EXISTS anti-join against the partial active-treatment index does
    the filtering in SQL; archived risks and stale scores never match.
    """
    if not severities:
        return {}
    rows = db.execute(
        select(RiskScore.severity, func.count())
        .join(Risk, Risk.id == RiskScore.risk_id)
        .where(
            RiskScore.tenant_id == tenant_id,
            RiskScore.is_current.is_(True),
            RiskScore.severity.in_(tuple(severities)),
            Risk.archived_at.is_(None),
            ~exists(
                select(RiskTreatment.id).where(
                    RiskTreatment.risk_id == Risk.id,
                    RiskTreatment.status.in_(tuple(ACTIVE_TREATMENT_STATUSES)),
                )
            ),
        )
        .group_by(RiskScore.severity)
    ).all()
    return {severity: int(count) for severity, count in rows}
