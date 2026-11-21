"""Tenant-scoped persistence for RF-04 risk treatments.

Mirrors ``risk/repository.py``: every read and write is filtered by
``tenant_id``, list queries compose optional filters over a whitelisted
sort map, and the commit helper translates the partial-active-index race
into the module's domain error (plan §48 — the DB, not the service,
enforces one active treatment per risk).
"""

from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.risk_treatment.enums import ACTIVE_TREATMENT_STATUSES
from app.modules.risk_treatment.errors import (
    InvalidRiskTreatmentQueryError,
    RiskTreatmentActiveExistsError,
    RiskTreatmentError,
)
from app.modules.risk_treatment.models import RiskTreatment, RiskTreatmentAction
from app.modules.risk_treatment.schemas import TreatmentFilter

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
