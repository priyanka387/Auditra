from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.modules.model_inventory.domain.errors import (
    DeploymentConcurrencyConflictError,
    DuplicateDeploymentError,
    InvalidSortFieldError,
)
from app.modules.model_inventory.models import ModelDeployment, ModelVersion

ALLOWED_SORT_FIELDS: dict[str, Any] = {
    "created_at": ModelDeployment.created_at,
    "updated_at": ModelDeployment.updated_at,
    "name": ModelDeployment.name,
    "environment": ModelDeployment.environment,
    "status": ModelDeployment.status,
    "deployed_at": ModelDeployment.deployed_at,
    "last_seen_at": ModelDeployment.last_seen_at,
}


@dataclass
class DeploymentFilters:
    environment: str | None = None
    status: str | None = None
    deployment_kind: str | None = None
    target_type: str | None = None
    runtime: str | None = None
    region: str | None = None
    model_version_id: UUID | None = None
    model_id: UUID | None = None
    search: str | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    updated_after: datetime | None = None
    updated_before: datetime | None = None
    last_seen_after: datetime | None = None
    last_seen_before: datetime | None = None


def get_deployment(db: Session, tenant_id: UUID, deployment_id: UUID) -> ModelDeployment | None:
    return db.scalar(
        select(ModelDeployment).where(
            ModelDeployment.tenant_id == tenant_id,
            ModelDeployment.id == deployment_id,
        )
    )


def find_duplicate(
    db: Session,
    tenant_id: UUID,
    model_version_id: UUID,
    environment: str,
    target_name: str | None,
    namespace: str | None,
) -> ModelDeployment | None:
    conditions = [
        ModelDeployment.tenant_id == tenant_id,
        ModelDeployment.model_version_id == model_version_id,
        ModelDeployment.environment == environment,
        ModelDeployment.target_name == target_name
        if target_name is not None
        else ModelDeployment.target_name.is_(None),
        ModelDeployment.namespace == namespace
        if namespace is not None
        else ModelDeployment.namespace.is_(None),
        ModelDeployment.archived_at.is_(None),
    ]
    return db.scalar(select(ModelDeployment).where(*conditions))


def create_deployment(db: Session, **fields: Any) -> ModelDeployment:
    deployment = ModelDeployment(**fields)
    db.add(deployment)
    return deployment


def query_deployments(
    db: Session,
    tenant_id: UUID,
    filters: DeploymentFilters,
    page: int,
    page_size: int,
    sort_by: str,
    sort_order: str,
    include_archived: bool,
) -> tuple[list[ModelDeployment], int]:
    sort_column = ALLOWED_SORT_FIELDS.get(sort_by)
    if sort_column is None:
        raise InvalidSortFieldError(f"sort field '{sort_by}' is not allowed")

    conditions: list[Any] = [ModelDeployment.tenant_id == tenant_id]
    if not include_archived:
        conditions.append(ModelDeployment.archived_at.is_(None))
    if filters.environment is not None:
        conditions.append(ModelDeployment.environment == filters.environment)
    if filters.status is not None:
        conditions.append(ModelDeployment.status == filters.status)
    if filters.deployment_kind is not None:
        conditions.append(ModelDeployment.deployment_kind == filters.deployment_kind)
    if filters.target_type is not None:
        conditions.append(ModelDeployment.target_type == filters.target_type)
    if filters.runtime is not None:
        conditions.append(ModelDeployment.runtime == filters.runtime)
    if filters.region is not None:
        conditions.append(ModelDeployment.region == filters.region)
    if filters.model_version_id is not None:
        conditions.append(ModelDeployment.model_version_id == filters.model_version_id)
    if filters.search is not None:
        term = f"%{filters.search}%"
        conditions.append(
            or_(
                ModelDeployment.name.ilike(term),
                ModelDeployment.target_name.ilike(term),
                ModelDeployment.cluster_name.ilike(term),
                ModelDeployment.namespace.ilike(term),
                ModelDeployment.runtime.ilike(term),
                ModelDeployment.serving_framework.ilike(term),
                ModelDeployment.source_reference.ilike(term),
            )
        )
    if filters.created_after is not None:
        conditions.append(ModelDeployment.created_at >= filters.created_after)
    if filters.created_before is not None:
        conditions.append(ModelDeployment.created_at <= filters.created_before)
    if filters.updated_after is not None:
        conditions.append(ModelDeployment.updated_at >= filters.updated_after)
    if filters.updated_before is not None:
        conditions.append(ModelDeployment.updated_at <= filters.updated_before)
    if filters.last_seen_after is not None:
        conditions.append(ModelDeployment.last_seen_at >= filters.last_seen_after)
    if filters.last_seen_before is not None:
        conditions.append(ModelDeployment.last_seen_at <= filters.last_seen_before)

    base = select(ModelDeployment).where(*conditions)
    if filters.model_id is not None:
        base = base.join(ModelVersion, ModelDeployment.model_version_id == ModelVersion.id).where(
            ModelVersion.model_id == filters.model_id
        )

    total = db.scalar(select(func.count()).select_from(base.subquery()))
    direction = sort_column.desc() if str(sort_order).lower() == "desc" else sort_column.asc()
    stmt = (
        base.order_by(direction, ModelDeployment.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(db.scalars(stmt).all()), total


def commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise DuplicateDeploymentError(
            "deployment with this version, environment, target and namespace already exists"
        ) from None
    except StaleDataError:
        db.rollback()
        raise DeploymentConcurrencyConflictError(
            "deployment was modified by another request"
        ) from None
