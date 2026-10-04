from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.model_inventory.domain.errors import (
    DuplicateAssociationError,
    InvalidSortFieldError,
)
from app.modules.model_inventory.models import Agent, AgentModelAssociation, Application

ALLOWED_SORT_FIELDS: dict[str, Any] = {
    "created_at": AgentModelAssociation.created_at,
    "updated_at": AgentModelAssociation.updated_at,
    "role": AgentModelAssociation.role,
    "selection_priority": AgentModelAssociation.selection_priority,
    "status": AgentModelAssociation.status,
}


@dataclass
class AssociationFilters:
    status: str | None = None
    role: str | None = None
    model_id: UUID | None = None
    model_version_id: UUID | None = None
    application_id: UUID | None = None


def get_association(
    db: Session, tenant_id: UUID, association_id: UUID
) -> AgentModelAssociation | None:
    return db.scalar(
        select(AgentModelAssociation).where(
            AgentModelAssociation.tenant_id == tenant_id,
            AgentModelAssociation.id == association_id,
        )
    )


def find_duplicate_role_priority(
    db: Session,
    tenant_id: UUID,
    agent_id: UUID,
    role: str,
    selection_priority: int,
    exclude_id: UUID | None = None,
) -> AgentModelAssociation | None:
    conditions = [
        AgentModelAssociation.tenant_id == tenant_id,
        AgentModelAssociation.agent_id == agent_id,
        AgentModelAssociation.role == role,
        AgentModelAssociation.selection_priority == selection_priority,
        AgentModelAssociation.status == "ACTIVE",
    ]
    if exclude_id is not None:
        conditions.append(AgentModelAssociation.id != exclude_id)
    return db.scalar(select(AgentModelAssociation).where(*conditions))


def find_duplicate_model_role(
    db: Session,
    tenant_id: UUID,
    agent_id: UUID,
    model_id: UUID,
    role: str,
    exclude_id: UUID | None = None,
) -> AgentModelAssociation | None:
    conditions = [
        AgentModelAssociation.tenant_id == tenant_id,
        AgentModelAssociation.agent_id == agent_id,
        AgentModelAssociation.model_id == model_id,
        AgentModelAssociation.role == role,
        AgentModelAssociation.status == "ACTIVE",
    ]
    if exclude_id is not None:
        conditions.append(AgentModelAssociation.id != exclude_id)
    return db.scalar(select(AgentModelAssociation).where(*conditions))


def create_association(db: Session, **fields: Any) -> AgentModelAssociation:
    association = AgentModelAssociation(**fields)
    db.add(association)
    return association


def query_agent_associations(
    db: Session,
    tenant_id: UUID,
    agent_id: UUID,
    filters: AssociationFilters | None,
    page: int,
    page_size: int,
    sort_by: str,
    sort_order: str,
) -> tuple[list[AgentModelAssociation], int]:
    conditions: list[Any] = [
        AgentModelAssociation.tenant_id == tenant_id,
        AgentModelAssociation.agent_id == agent_id,
    ]
    conditions.extend(_filter_conditions(filters))

    return _page(
        db,
        select(AgentModelAssociation).where(*conditions),
        AgentModelAssociation,
        page,
        page_size,
        sort_by,
        sort_order,
    )


def query_model_agents(
    db: Session,
    tenant_id: UUID,
    model_id: UUID,
    filters: AssociationFilters | None,
    page: int,
    page_size: int,
    sort_by: str,
    sort_order: str,
) -> tuple[list[tuple[AgentModelAssociation, Agent, Application]], int]:
    conditions: list[Any] = [
        AgentModelAssociation.tenant_id == tenant_id,
        AgentModelAssociation.model_id == model_id,
    ]
    conditions.extend(_filter_conditions(filters))
    if filters is not None and filters.application_id is not None:
        conditions.append(Agent.application_id == filters.application_id)

    sort_column = ALLOWED_SORT_FIELDS.get(sort_by)
    if sort_column is None:
        raise InvalidSortFieldError(f"sort field '{sort_by}' is not allowed")

    join = (
        select(AgentModelAssociation, Agent, Application)
        .join(Agent, AgentModelAssociation.agent_id == Agent.id)
        .join(Application, Agent.application_id == Application.id)
        .where(*conditions)
    )
    total = db.scalar(
        select(func.count()).select_from(
            select(AgentModelAssociation.id)
            .join(Agent, AgentModelAssociation.agent_id == Agent.id)
            .join(Application, Agent.application_id == Application.id)
            .where(*conditions)
            .subquery()
        )
    )
    direction = sort_column.desc() if str(sort_order).lower() == "desc" else sort_column.asc()
    stmt = (
        join.order_by(direction, AgentModelAssociation.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(db.execute(stmt).all()), total


def commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise DuplicateAssociationError(
            "association violates an active-association uniqueness rule"
        ) from None


def _filter_conditions(filters: AssociationFilters | None) -> list[Any]:
    conditions: list[Any] = []
    if filters is None:
        return conditions
    if filters.status is not None:
        conditions.append(AgentModelAssociation.status == filters.status)
    if filters.role is not None:
        conditions.append(AgentModelAssociation.role == filters.role)
    if filters.model_id is not None:
        conditions.append(AgentModelAssociation.model_id == filters.model_id)
    if filters.model_version_id is not None:
        conditions.append(AgentModelAssociation.model_version_id == filters.model_version_id)
    return conditions


def _page(
    db: Session,
    base: Any,
    entity: type,
    page: int,
    page_size: int,
    sort_by: str,
    sort_order: str,
) -> tuple[list[Any], int]:
    sort_column = ALLOWED_SORT_FIELDS.get(sort_by)
    if sort_column is None:
        raise InvalidSortFieldError(f"sort field '{sort_by}' is not allowed")
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    direction = sort_column.desc() if str(sort_order).lower() == "desc" else sort_column.asc()
    stmt = base.order_by(direction, entity.id.asc()).offset((page - 1) * page_size).limit(page_size)
    return list(db.scalars(stmt).all()), total
