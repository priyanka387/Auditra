from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.modules.model_inventory.domain.errors import DuplicateModelError, InvalidSortFieldError
from app.modules.model_inventory.models import (
    Model,
    ModelProvider,
    ModelTag,
    ModelTagLink,
    ModelType,
)

ALLOWED_SORT_FIELDS: dict[str, Any] = {
    "name": Model.name,
    "created_at": Model.created_at,
    "updated_at": Model.updated_at,
    "lifecycle_state": Model.lifecycle_state,
    "provider": ModelProvider.name,
    "model_type": ModelType.name,
}


@dataclass
class ModelFilters:
    provider: str | None = None
    model_type: str | None = None
    lifecycle_state: str | None = None
    owner: str | None = None
    team: str | None = None
    source_type: str | None = None
    tag: str | None = None
    search: str | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    updated_after: datetime | None = None
    updated_before: datetime | None = None


def get_provider_by_slug(db: Session, tenant_id: UUID, slug: str) -> ModelProvider | None:
    return db.scalar(select(ModelProvider).where(ModelProvider.slug == slug))


def list_providers(db: Session, tenant_id: UUID) -> list[ModelProvider]:
    return list(db.scalars(select(ModelProvider).order_by(ModelProvider.name)))


def get_model_type_by_slug(db: Session, tenant_id: UUID, slug: str) -> ModelType | None:
    return db.scalar(select(ModelType).where(ModelType.slug == slug))


def list_model_types(db: Session, tenant_id: UUID) -> list[ModelType]:
    return list(db.scalars(select(ModelType).order_by(ModelType.name)))


def find_by_canonical_key(db: Session, tenant_id: UUID, canonical_key: str) -> Model | None:
    return db.scalar(
        select(Model).where(Model.tenant_id == tenant_id, Model.canonical_key == canonical_key)
    )


def create_model(db: Session, **fields: Any) -> Model:
    model = Model(**fields)
    db.add(model)
    return model


def get_model(db: Session, tenant_id: UUID, model_id: UUID) -> Model | None:
    stmt = (
        select(Model)
        .options(
            selectinload(Model.tags),
            joinedload(Model.provider),
            joinedload(Model.model_type),
        )
        .where(Model.tenant_id == tenant_id, Model.id == model_id)
    )
    return db.scalar(stmt)


def _filtered_models(tenant_id: UUID, filters: ModelFilters, include_archived: bool) -> Select:
    conditions: list[Any] = [Model.tenant_id == tenant_id]
    if not include_archived:
        conditions.append(Model.lifecycle_state != "ARCHIVED")
    if filters.provider is not None:
        conditions.append(ModelProvider.slug == filters.provider)
    if filters.model_type is not None:
        conditions.append(ModelType.slug == filters.model_type)
    if filters.lifecycle_state is not None:
        conditions.append(Model.lifecycle_state == filters.lifecycle_state)
    if filters.owner is not None:
        conditions.append(Model.owner_name == filters.owner)
    if filters.team is not None:
        conditions.append(Model.team_name == filters.team)
    if filters.source_type is not None:
        conditions.append(Model.source_type == filters.source_type)
    if filters.tag is not None:
        conditions.append(
            select(ModelTagLink.model_id)
            .join(ModelTag, ModelTag.id == ModelTagLink.tag_id)
            .where(
                ModelTagLink.model_id == Model.id,
                ModelTag.tenant_id == tenant_id,
                or_(
                    ModelTag.key == filters.tag,
                    func.concat(ModelTag.key, "=", ModelTag.value) == filters.tag,
                ),
            )
            .exists()
        )
    if filters.search is not None:
        term = f"%{filters.search}%"
        conditions.append(
            or_(
                Model.name.ilike(term),
                Model.native_model_id.ilike(term),
                Model.description.ilike(term),
                Model.canonical_key.ilike(term),
                ModelProvider.name.ilike(term),
                ModelProvider.slug.ilike(term),
            )
        )
    if filters.created_after is not None:
        conditions.append(Model.created_at >= filters.created_after)
    if filters.created_before is not None:
        conditions.append(Model.created_at <= filters.created_before)
    if filters.updated_after is not None:
        conditions.append(Model.updated_at >= filters.updated_after)
    if filters.updated_before is not None:
        conditions.append(Model.updated_at <= filters.updated_before)

    return (
        select(Model)
        .outerjoin(ModelProvider, Model.provider_id == ModelProvider.id)
        .outerjoin(ModelType, Model.model_type_id == ModelType.id)
        .where(*conditions)
    )


def query_models(
    db: Session,
    tenant_id: UUID,
    filters: ModelFilters,
    page: int,
    page_size: int,
    sort_by: str,
    sort_order: str,
    include_archived: bool,
) -> tuple[list[Model], int]:
    sort_column = ALLOWED_SORT_FIELDS.get(sort_by)
    if sort_column is None:
        raise InvalidSortFieldError(f"sort field '{sort_by}' is not allowed")

    base = _filtered_models(tenant_id, filters, include_archived)
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    direction = sort_column.desc() if str(sort_order).lower() == "desc" else sort_column.asc()
    stmt = (
        base.options(
            selectinload(Model.tags),
            joinedload(Model.provider),
            joinedload(Model.model_type),
        )
        .order_by(direction, Model.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(db.scalars(stmt).all()), total


def commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise DuplicateModelError("model with this identity already exists") from None
