from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.model_discovery.errors import (
    DuplicateDiscoveryError,
    InvalidDiscoveryQueryError,
)
from app.modules.model_discovery.models import ModelDiscovery
from app.modules.model_discovery.schemas import DiscoveryFilter

SORT_COLUMNS = {
    "last_seen_at": ModelDiscovery.last_seen_at,
    "first_seen_at": ModelDiscovery.first_seen_at,
    "observation_count": ModelDiscovery.observation_count,
    "created_at": ModelDiscovery.created_at,
    "updated_at": ModelDiscovery.updated_at,
}


def create_discovery(db: Session, **fields) -> ModelDiscovery:
    discovery = ModelDiscovery(**fields)
    db.add(discovery)
    return discovery


def get_discovery(db: Session, tenant_id: UUID, discovery_id: UUID) -> ModelDiscovery | None:
    return db.scalar(
        select(ModelDiscovery).where(
            ModelDiscovery.tenant_id == tenant_id,
            ModelDiscovery.id == discovery_id,
        )
    )


def find_for_ingest(
    db: Session,
    tenant_id: UUID,
    source_type: str,
    source_identifier: str | None,
    canonical_identity: str,
) -> ModelDiscovery | None:
    identifier = (
        ModelDiscovery.source_identifier.is_(None)
        if source_identifier is None
        else ModelDiscovery.source_identifier == source_identifier
    )
    return db.scalar(
        select(ModelDiscovery).where(
            ModelDiscovery.tenant_id == tenant_id,
            ModelDiscovery.source_type == source_type,
            identifier,
            ModelDiscovery.canonical_identity == canonical_identity,
        )
    )


def _conditions(tenant_id: UUID, filters: DiscoveryFilter) -> list:
    conditions = [ModelDiscovery.tenant_id == tenant_id]
    if filters.source_type is not None:
        conditions.append(ModelDiscovery.source_type == filters.source_type)
    if filters.source_identifier is not None:
        conditions.append(ModelDiscovery.source_identifier == filters.source_identifier)
    if filters.provider is not None:
        conditions.append(ModelDiscovery.provider == filters.provider)
    if filters.model_type is not None:
        conditions.append(ModelDiscovery.model_type == filters.model_type)
    if filters.status is not None:
        conditions.append(ModelDiscovery.status == filters.status)
    if filters.matched_model_id is not None:
        conditions.append(ModelDiscovery.matched_model_id == filters.matched_model_id)
    if filters.canonical_identity is not None:
        conditions.append(ModelDiscovery.canonical_identity == filters.canonical_identity)
    if filters.last_seen_from is not None:
        conditions.append(ModelDiscovery.last_seen_at >= filters.last_seen_from)
    if filters.last_seen_to is not None:
        conditions.append(ModelDiscovery.last_seen_at <= filters.last_seen_to)
    if filters.search is not None:
        term = f"%{filters.search}%"
        conditions.append(
            or_(
                ModelDiscovery.provider.ilike(term),
                ModelDiscovery.model_identifier.ilike(term),
                ModelDiscovery.display_name.ilike(term),
                ModelDiscovery.canonical_identity.ilike(term),
            )
        )
    return conditions


def list_discoveries(
    db: Session, tenant_id: UUID, filters: DiscoveryFilter
) -> tuple[list[ModelDiscovery], int]:
    conditions = _conditions(tenant_id, filters)
    total = db.scalar(select(func.count()).select_from(ModelDiscovery).where(*conditions)) or 0

    sort_column = SORT_COLUMNS.get(filters.sort_by)
    if sort_column is None:
        raise InvalidDiscoveryQueryError(f"unsupported sort field '{filters.sort_by}'")
    ordering = sort_column.desc() if filters.sort_order == "desc" else sort_column.asc()
    items = list(
        db.scalars(
            select(ModelDiscovery)
            .where(*conditions)
            .order_by(ordering, ModelDiscovery.id)
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
        raise DuplicateDiscoveryError("equivalent discovery observation already exists") from None
