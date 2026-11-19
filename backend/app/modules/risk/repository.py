"""Tenant-scoped persistence for the RF-01 risk register and taxonomy.

Mirrors ``asset_discovery/repository.py``: every read and write is
filtered by ``tenant_id``, list queries compose optional filters over a
whitelisted sort map, and commit helpers translate constraint races into
the module's domain errors.
"""

from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.asset_discovery.models import DiscoveredAsset
from app.modules.risk.errors import (
    DuplicateRiskCategoryError,
    InvalidRiskCategoryQueryError,
    InvalidRiskQueryError,
    RiskError,
)
from app.modules.risk.models import Risk, RiskCategory
from app.modules.risk.schemas import RiskCategoryFilter, RiskFilter
from app.modules.risk_scoring.models import RiskScore

CATEGORY_SORT_COLUMNS = {
    "name": RiskCategory.name,
    "slug": RiskCategory.slug,
    "created_at": RiskCategory.created_at,
    "updated_at": RiskCategory.updated_at,
    "is_active": RiskCategory.is_active,
}

RISK_SORT_COLUMNS = {
    "title": Risk.title,
    "status": Risk.status,
    "source": Risk.source,
    "owner": Risk.owner,
    "created_at": Risk.created_at,
    "updated_at": Risk.updated_at,
}

# RF-03: score-backed sort fields; each honors sort_order with NULLS LAST
# so unscored risks never float to the top of an ascending list (Ruling 10)
SCORE_SORT_COLUMNS = {
    "score": RiskScore.effective_score,
    "likelihood": RiskScore.likelihood_value,
    "impact": RiskScore.impact_value,
    "scored_at": RiskScore.created_at,
}

# severity/priority sorts are fixed: highest risk first, ties broken by
# effective score — sort_order is ignored (Ruling 10)
PRIORITY_SORT_FIELDS = {"severity", "priority"}


# --- risk categories --------------------------------------------------------


def create_category(db: Session, **fields) -> RiskCategory:
    category = RiskCategory(**fields)
    db.add(category)
    return category


def get_category(db: Session, tenant_id: UUID, category_id: UUID) -> RiskCategory | None:
    return db.scalar(
        select(RiskCategory).where(
            RiskCategory.tenant_id == tenant_id,
            RiskCategory.id == category_id,
        )
    )


def find_category_by_slug(db: Session, tenant_id: UUID, slug: str) -> RiskCategory | None:
    return db.scalar(
        select(RiskCategory).where(
            RiskCategory.tenant_id == tenant_id,
            RiskCategory.slug == slug,
        )
    )


def get_categories_by_ids(
    db: Session, tenant_id: UUID, category_ids: set[UUID]
) -> dict[UUID, RiskCategory]:
    """Prefetch every referenced category of one page in a single query."""
    if not category_ids:
        return {}
    rows = db.scalars(
        select(RiskCategory).where(
            RiskCategory.tenant_id == tenant_id,
            RiskCategory.id.in_(category_ids),
        )
    )
    return {category.id: category for category in rows}


def _category_conditions(tenant_id: UUID, filters: RiskCategoryFilter) -> list:
    conditions = [RiskCategory.tenant_id == tenant_id]
    if filters.is_active is not None:
        conditions.append(RiskCategory.is_active == filters.is_active)
    if filters.is_system is not None:
        conditions.append(RiskCategory.is_system == filters.is_system)
    if filters.search is not None:
        term = f"%{filters.search}%"
        conditions.append(
            or_(
                RiskCategory.name.ilike(term),
                RiskCategory.slug.ilike(term),
            )
        )
    return conditions


def list_categories(
    db: Session, tenant_id: UUID, filters: RiskCategoryFilter
) -> tuple[list[RiskCategory], int]:
    conditions = _category_conditions(tenant_id, filters)
    total = db.scalar(select(func.count()).select_from(RiskCategory).where(*conditions)) or 0

    sort_column = CATEGORY_SORT_COLUMNS.get(filters.sort_by)
    if sort_column is None:
        raise InvalidRiskCategoryQueryError(f"unsupported sort field '{filters.sort_by}'")
    ordering = sort_column.desc() if filters.sort_order == "desc" else sort_column.asc()
    items = list(
        db.scalars(
            select(RiskCategory)
            .where(*conditions)
            .order_by(ordering, RiskCategory.id)
            .offset((filters.page - 1) * filters.page_size)
            .limit(filters.page_size)
        )
    )
    return items, total


def commit_category(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise DuplicateRiskCategoryError("risk category with this slug already exists") from None


# --- risks ------------------------------------------------------------------


def create_risk(db: Session, **fields) -> Risk:
    risk = Risk(**fields)
    db.add(risk)
    return risk


def get_risk(db: Session, tenant_id: UUID, risk_id: UUID) -> Risk | None:
    return db.scalar(
        select(Risk).where(
            Risk.tenant_id == tenant_id,
            Risk.id == risk_id,
        )
    )


def get_assets_by_ids(
    db: Session, tenant_id: UUID, asset_ids: set[UUID]
) -> dict[UUID, DiscoveredAsset]:
    """Prefetch every referenced discovered asset of one page (one query)."""
    if not asset_ids:
        return {}
    rows = db.scalars(
        select(DiscoveredAsset).where(
            DiscoveredAsset.tenant_id == tenant_id,
            DiscoveredAsset.id.in_(asset_ids),
        )
    )
    return {asset.id: asset for asset in rows}


def _risk_conditions(tenant_id: UUID, filters: RiskFilter) -> list:
    conditions = [Risk.tenant_id == tenant_id]
    # False (default) -> active only, True -> archived only, so archived
    # risks stay queryable but never leak into the default register view
    if filters.archived:
        conditions.append(Risk.archived_at.is_not(None))
    else:
        conditions.append(Risk.archived_at.is_(None))
    if filters.category_id is not None:
        conditions.append(Risk.category_id == filters.category_id)
    if filters.status is not None:
        conditions.append(Risk.status == filters.status)
    if filters.source is not None:
        conditions.append(Risk.source == filters.source)
    if filters.owner is not None:
        conditions.append(Risk.owner == filters.owner)
    if filters.team is not None:
        conditions.append(Risk.team == filters.team)
    if filters.asset_id is not None:
        conditions.append(Risk.asset_id == filters.asset_id)
    # RF-03 score filters run against the LEFT JOINed current score, so
    # any of them naturally restricts results to scored risks
    if filters.scored is True:
        conditions.append(RiskScore.id.is_not(None))
    elif filters.scored is False:
        conditions.append(RiskScore.id.is_(None))
    if filters.severity is not None:
        conditions.append(RiskScore.severity == filters.severity)
    if filters.priority is not None:
        conditions.append(RiskScore.priority == filters.priority)
    if filters.likelihood is not None:
        conditions.append(RiskScore.likelihood_value == filters.likelihood)
    if filters.impact is not None:
        conditions.append(RiskScore.impact_value == filters.impact)
    if filters.framework_id is not None:
        conditions.append(RiskScore.framework_id == filters.framework_id)
    if filters.search is not None:
        term = f"%{filters.search}%"
        conditions.append(
            or_(
                Risk.title.ilike(term),
                Risk.description.ilike(term),
                Risk.owner.ilike(term),
                DiscoveredAsset.name.ilike(term),
            )
        )
    return conditions


def _risk_list_joins(statement):
    """Asset + current-score outer joins shared by count and page queries."""
    return statement.join(DiscoveredAsset, Risk.asset_id == DiscoveredAsset.id, isouter=True).join(
        RiskScore,
        (RiskScore.risk_id == Risk.id) & RiskScore.is_current.is_(True),
        isouter=True,
    )


def _risk_ordering(filters: RiskFilter) -> list:
    if filters.sort_by in PRIORITY_SORT_FIELDS:
        # severity/priority: highest risk first, effective score breaks ties
        return [RiskScore.priority.asc().nulls_last(), RiskScore.effective_score.desc()]
    if filters.sort_by in SCORE_SORT_COLUMNS:
        column = SCORE_SORT_COLUMNS[filters.sort_by]
        ordering = column.desc() if filters.sort_order == "desc" else column.asc()
        return [ordering.nulls_last()]
    sort_column = RISK_SORT_COLUMNS.get(filters.sort_by)
    if sort_column is None:
        raise InvalidRiskQueryError(f"unsupported sort field '{filters.sort_by}'")
    return [sort_column.desc() if filters.sort_order == "desc" else sort_column.asc()]


def list_risks(db: Session, tenant_id: UUID, filters: RiskFilter) -> tuple[list[Risk], int]:
    conditions = _risk_conditions(tenant_id, filters)
    # outer joins: a risk without an asset must still match title/
    # description terms, and a risk without a score must still be listed
    total = (
        db.scalar(_risk_list_joins(select(func.count()).select_from(Risk)).where(*conditions)) or 0
    )

    ordering = _risk_ordering(filters)
    items = list(
        db.scalars(
            _risk_list_joins(select(Risk))
            .where(*conditions)
            .order_by(*ordering, Risk.id)
            .offset((filters.page - 1) * filters.page_size)
            .limit(filters.page_size)
        )
    )
    return items, total


def commit_risk(db: Session) -> None:
    """Persist a risk mutation; a constraint race means a referenced row
    vanished between validation and commit, so roll back rather than keep
    a half-applied change."""
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise RiskError("risk references a category or asset that no longer exists") from exc
