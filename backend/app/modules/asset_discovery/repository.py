from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.asset_discovery.errors import (
    DiscoverySourceNotFoundError,
    DuplicateDiscoveredAssetError,
    DuplicateDiscoverySourceError,
    InvalidDiscoveredAssetQueryError,
    InvalidDiscoveryRunQueryError,
    InvalidDiscoverySourceQueryError,
)
from app.modules.asset_discovery.models import (
    DiscoveredAsset,
    DiscoveryRun,
    DiscoverySource,
)
from app.modules.asset_discovery.schemas import (
    DiscoveredAssetFilter,
    DiscoveryRunFilter,
    DiscoverySourceFilter,
)

SORT_COLUMNS = {
    "name": DiscoverySource.name,
    "created_at": DiscoverySource.created_at,
    "updated_at": DiscoverySource.updated_at,
    "status": DiscoverySource.status,
}

RUN_SORT_COLUMNS = {
    "started_at": DiscoveryRun.started_at,
    "completed_at": DiscoveryRun.completed_at,
    "created_at": DiscoveryRun.created_at,
    "updated_at": DiscoveryRun.updated_at,
    "status": DiscoveryRun.status,
}

ASSET_SORT_COLUMNS = {
    "name": DiscoveredAsset.name,
    "first_seen_at": DiscoveredAsset.first_seen_at,
    "last_seen_at": DiscoveredAsset.last_seen_at,
    "created_at": DiscoveredAsset.created_at,
    "updated_at": DiscoveredAsset.updated_at,
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


def create_run(db: Session, **fields) -> DiscoveryRun:
    run = DiscoveryRun(**fields)
    db.add(run)
    return run


def get_run(db: Session, tenant_id: UUID, run_id: UUID) -> DiscoveryRun | None:
    return db.scalar(
        select(DiscoveryRun).where(
            DiscoveryRun.tenant_id == tenant_id,
            DiscoveryRun.id == run_id,
        )
    )


def _run_conditions(tenant_id: UUID, filters: DiscoveryRunFilter) -> list:
    conditions = [DiscoveryRun.tenant_id == tenant_id]
    if filters.source_id is not None:
        conditions.append(DiscoveryRun.source_id == filters.source_id)
    if filters.status is not None:
        conditions.append(DiscoveryRun.status == filters.status)
    if filters.started_from is not None:
        conditions.append(DiscoveryRun.started_at >= filters.started_from)
    if filters.started_to is not None:
        conditions.append(DiscoveryRun.started_at <= filters.started_to)
    return conditions


def list_runs(
    db: Session, tenant_id: UUID, filters: DiscoveryRunFilter
) -> tuple[list[DiscoveryRun], int]:
    conditions = _run_conditions(tenant_id, filters)
    total = db.scalar(select(func.count()).select_from(DiscoveryRun).where(*conditions)) or 0

    sort_column = RUN_SORT_COLUMNS.get(filters.sort_by)
    if sort_column is None:
        raise InvalidDiscoveryRunQueryError(f"unsupported sort field '{filters.sort_by}'")
    ordering = sort_column.desc() if filters.sort_order == "desc" else sort_column.asc()
    items = list(
        db.scalars(
            select(DiscoveryRun)
            .where(*conditions)
            .order_by(ordering, DiscoveryRun.id)
            .offset((filters.page - 1) * filters.page_size)
            .limit(filters.page_size)
        )
    )
    return items, total


def commit_run(db: Session, source_id: UUID) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # the only constraint a fresh run insert can violate is the
        # foreign key to its source
        raise DiscoverySourceNotFoundError(f"discovery source '{source_id}' not found") from None


def create_discovered_asset(db: Session, **fields) -> DiscoveredAsset:
    asset = DiscoveredAsset(**fields)
    db.add(asset)
    return asset


def get_discovered_asset(
    db: Session, tenant_id: UUID, asset_id: UUID
) -> DiscoveredAsset | None:
    return db.scalar(
        select(DiscoveredAsset).where(
            DiscoveredAsset.tenant_id == tenant_id,
            DiscoveredAsset.id == asset_id,
        )
    )


def find_asset_by_identity(
    db: Session, tenant_id: UUID, source_id: UUID, normalized_identity: str
) -> DiscoveredAsset | None:
    """Locate an asset within its source scope (catalog-level identity)."""
    return db.scalar(
        select(DiscoveredAsset).where(
            DiscoveredAsset.tenant_id == tenant_id,
            DiscoveredAsset.source_id == source_id,
            DiscoveredAsset.normalized_identity == normalized_identity,
        )
    )


def _asset_conditions(tenant_id: UUID, filters: DiscoveredAssetFilter) -> list:
    conditions = [DiscoveredAsset.tenant_id == tenant_id]
    if filters.asset_type is not None:
        conditions.append(DiscoveredAsset.asset_type == filters.asset_type)
    if filters.source_id is not None:
        conditions.append(DiscoveredAsset.source_id == filters.source_id)
    if filters.status is not None:
        conditions.append(DiscoveredAsset.status == filters.status)
    if filters.search is not None:
        term = f"%{filters.search}%"
        conditions.append(
            or_(
                DiscoveredAsset.name.ilike(term),
                DiscoveredAsset.normalized_identity.ilike(term),
            )
        )
    return conditions


def list_discovered_assets(
    db: Session, tenant_id: UUID, filters: DiscoveredAssetFilter
) -> tuple[list[DiscoveredAsset], int]:
    conditions = _asset_conditions(tenant_id, filters)
    total = db.scalar(select(func.count()).select_from(DiscoveredAsset).where(*conditions)) or 0

    sort_column = ASSET_SORT_COLUMNS.get(filters.sort_by)
    if sort_column is None:
        raise InvalidDiscoveredAssetQueryError(f"unsupported sort field '{filters.sort_by}'")
    ordering = sort_column.desc() if filters.sort_order == "desc" else sort_column.asc()
    items = list(
        db.scalars(
            select(DiscoveredAsset)
            .where(*conditions)
            .order_by(ordering, DiscoveredAsset.id)
            .offset((filters.page - 1) * filters.page_size)
            .limit(filters.page_size)
        )
    )
    return items, total


def commit_asset(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # a fresh insert can only violate the identity uniqueness or the
        # foreign keys; both are pre-validated, so the remaining race is
        # a concurrent observation of the same identity
        raise DuplicateDiscoveredAssetError(
            "discovered asset with this identity already exists"
        ) from None
