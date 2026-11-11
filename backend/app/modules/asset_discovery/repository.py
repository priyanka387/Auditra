from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.asset_discovery.errors import (
    DuplicateDiscoverySourceError,
    InvalidDiscoverySourceQueryError,
)
from app.modules.asset_discovery.models import DiscoverySource
from app.modules.asset_discovery.schemas import DiscoverySourceFilter

SORT_COLUMNS = {
    "name": DiscoverySource.name,
    "created_at": DiscoverySource.created_at,
    "updated_at": DiscoverySource.updated_at,
    "status": DiscoverySource.status,
}


def create_source(db: Session, **fields) -> DiscoverySource:
    source = DiscoverySource(**fields)
    db.add(source)
    return source


def get_source(db: Session, tenant_id: UUID, source_id: UUID) -> DiscoverySource | None:
    return db.scalar(
        select(DiscoverySource).where(
            DiscoverySource.tenant_id == tenant_id,
            DiscoverySource.id == source_id,
        )
    )


def find_active_by_name(db: Session, tenant_id: UUID, name: str) -> DiscoverySource | None:
    return db.scalar(
        select(DiscoverySource).where(
            DiscoverySource.tenant_id == tenant_id,
            DiscoverySource.name == name,
            DiscoverySource.archived_at.is_(None),
        )
    )


def _conditions(tenant_id: UUID, filters: DiscoverySourceFilter) -> list:
    conditions = [DiscoverySource.tenant_id == tenant_id]
    if not filters.include_archived:
        conditions.append(DiscoverySource.archived_at.is_(None))
    if filters.source_type is not None:
        conditions.append(DiscoverySource.source_type == filters.source_type)
    if filters.status is not None:
        conditions.append(DiscoverySource.status == filters.status)
    if filters.search is not None:
        term = f"%{filters.search}%"
        conditions.append(
            or_(
                DiscoverySource.name.ilike(term),
                DiscoverySource.source_type.ilike(term),
            )
        )
    return conditions


def list_sources(
    db: Session, tenant_id: UUID, filters: DiscoverySourceFilter
) -> tuple[list[DiscoverySource], int]:
    conditions = _conditions(tenant_id, filters)
    total = db.scalar(select(func.count()).select_from(DiscoverySource).where(*conditions)) or 0

    sort_column = SORT_COLUMNS.get(filters.sort_by)
    if sort_column is None:
        raise InvalidDiscoverySourceQueryError(f"unsupported sort field '{filters.sort_by}'")
    ordering = sort_column.desc() if filters.sort_order == "desc" else sort_column.asc()
    items = list(
        db.scalars(
            select(DiscoverySource)
            .where(*conditions)
            .order_by(ordering, DiscoverySource.id)
            .offset((filters.page - 1) * filters.page_size)
            .limit(filters.page_size)
        )
    )
    return items, total


def commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise DuplicateDiscoverySourceError(
            "discovery source with this name already exists"
        ) from None
