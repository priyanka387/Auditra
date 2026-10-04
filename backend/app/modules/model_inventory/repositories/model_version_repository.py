from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.modules.model_inventory.domain.errors import (
    DuplicateModelVersionError,
    InvalidSortFieldError,
    VersionConcurrencyConflictError,
)
from app.modules.model_inventory.models import ModelVersion

ALLOWED_SORT_FIELDS: dict[str, Any] = {
    "created_at": ModelVersion.created_at,
    "updated_at": ModelVersion.updated_at,
    "version_label": ModelVersion.version_label,
    "lifecycle_state": ModelVersion.lifecycle_state,
}


@dataclass
class ModelVersionFilters:
    lifecycle_state: str | None = None
    identity_type: str | None = None
    source_type: str | None = None
    search: str | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    updated_after: datetime | None = None
    updated_before: datetime | None = None


def get_model_version(
    db: Session, tenant_id: UUID, model_id: UUID, version_id: UUID
) -> ModelVersion | None:
    return db.scalar(
        select(ModelVersion).where(
            ModelVersion.tenant_id == tenant_id,
            ModelVersion.model_id == model_id,
            ModelVersion.id == version_id,
        )
    )


def find_by_canonical_key(
    db: Session, tenant_id: UUID, model_id: UUID, canonical_key: str
) -> ModelVersion | None:
    return db.scalar(
        select(ModelVersion).where(
            ModelVersion.tenant_id == tenant_id,
            ModelVersion.model_id == model_id,
            ModelVersion.canonical_version_key == canonical_key,
        )
    )


def create_model_version(db: Session, **fields: Any) -> ModelVersion:
    version = ModelVersion(**fields)
    db.add(version)
    return version


def query_model_versions(
    db: Session,
    tenant_id: UUID,
    model_id: UUID,
    filters: ModelVersionFilters,
    page: int,
    page_size: int,
    sort_by: str,
    sort_order: str,
    include_archived: bool,
) -> tuple[list[ModelVersion], int]:
    sort_column = ALLOWED_SORT_FIELDS.get(sort_by)
    if sort_column is None:
        raise InvalidSortFieldError(f"sort field '{sort_by}' is not allowed")

    conditions: list[Any] = [
        ModelVersion.tenant_id == tenant_id,
        ModelVersion.model_id == model_id,
    ]
    if not include_archived:
        conditions.append(ModelVersion.lifecycle_state != "ARCHIVED")
    if filters.lifecycle_state is not None:
        conditions.append(ModelVersion.lifecycle_state == filters.lifecycle_state)
    if filters.identity_type is not None:
        conditions.append(ModelVersion.identity_type == filters.identity_type)
    if filters.source_type is not None:
        conditions.append(ModelVersion.source_type == filters.source_type)
    if filters.search is not None:
        term = f"%{filters.search}%"
        conditions.append(
            or_(
                ModelVersion.version_label.ilike(term),
                ModelVersion.display_name.ilike(term),
                ModelVersion.native_version_id.ilike(term),
                ModelVersion.canonical_version_key.ilike(term),
            )
        )
    if filters.created_after is not None:
        conditions.append(ModelVersion.created_at >= filters.created_after)
    if filters.created_before is not None:
        conditions.append(ModelVersion.created_at <= filters.created_before)
    if filters.updated_after is not None:
        conditions.append(ModelVersion.updated_at >= filters.updated_after)
    if filters.updated_before is not None:
        conditions.append(ModelVersion.updated_at <= filters.updated_before)

    base = select(ModelVersion).where(*conditions)
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    direction = sort_column.desc() if str(sort_order).lower() == "desc" else sort_column.asc()
    stmt = (
        base.order_by(direction, ModelVersion.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(db.scalars(stmt).all()), total


def commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise DuplicateModelVersionError(
            "model version with this identity already exists"
        ) from None
    except StaleDataError:
        db.rollback()
        raise VersionConcurrencyConflictError(
            "model version was modified by another request"
        ) from None
