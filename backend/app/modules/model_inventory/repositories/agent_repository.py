from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.model_inventory.domain.errors import (
    DuplicateAgentError,
    InvalidSortFieldError,
)
from app.modules.model_inventory.models import Agent

ALLOWED_SORT_FIELDS: dict[str, Any] = {
    "created_at": Agent.created_at,
    "updated_at": Agent.updated_at,
    "name": Agent.name,
    "slug": Agent.slug,
    "status": Agent.status,
    "framework": Agent.framework,
    "agent_type": Agent.agent_type,
}


@dataclass
class AgentFilters:
    search: str | None = None
    status: str | None = None
    framework: str | None = None
    agent_type: str | None = None
    application_id: UUID | None = None
    owner: str | None = None
    team: str | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    updated_after: datetime | None = None
    updated_before: datetime | None = None


def get_agent(db: Session, tenant_id: UUID, agent_id: UUID) -> Agent | None:
    return db.scalar(
        select(Agent).where(
            Agent.tenant_id == tenant_id,
            Agent.id == agent_id,
        )
    )


def find_duplicate_slug(
    db: Session, tenant_id: UUID, application_id: UUID, slug: str
) -> Agent | None:
    return db.scalar(
        select(Agent).where(
            Agent.tenant_id == tenant_id,
            Agent.application_id == application_id,
            Agent.slug == slug,
        )
    )


def create_agent(db: Session, **fields: Any) -> Agent:
    agent = Agent(**fields)
    db.add(agent)
    return agent


def query_agents(
    db: Session,
    tenant_id: UUID,
    filters: AgentFilters | None,
    page: int,
    page_size: int,
    sort_by: str,
    sort_order: str,
    include_archived: bool,
) -> tuple[list[Agent], int]:
    sort_column = ALLOWED_SORT_FIELDS.get(sort_by)
    if sort_column is None:
        raise InvalidSortFieldError(f"sort field '{sort_by}' is not allowed")

    conditions: list[Any] = [Agent.tenant_id == tenant_id]
    if not include_archived and (filters is None or filters.status is None):
        conditions.append(Agent.status != "ARCHIVED")
    if filters is not None:
        if filters.status is not None:
            conditions.append(Agent.status == filters.status)
        if filters.framework is not None:
            conditions.append(Agent.framework == filters.framework.lower())
        if filters.agent_type is not None:
            conditions.append(Agent.agent_type == filters.agent_type)
        if filters.application_id is not None:
            conditions.append(Agent.application_id == filters.application_id)
        if filters.owner is not None:
            conditions.append(Agent.owner_name == filters.owner)
        if filters.team is not None:
            conditions.append(Agent.team_name == filters.team)
        if filters.search is not None:
            term = f"%{filters.search}%"
            conditions.append(
                or_(
                    Agent.name.ilike(term),
                    Agent.slug.ilike(term),
                    Agent.display_name.ilike(term),
                )
            )
        if filters.created_after is not None:
            conditions.append(Agent.created_at >= filters.created_after)
        if filters.created_before is not None:
            conditions.append(Agent.created_at <= filters.created_before)
        if filters.updated_after is not None:
            conditions.append(Agent.updated_at >= filters.updated_after)
        if filters.updated_before is not None:
            conditions.append(Agent.updated_at <= filters.updated_before)

    base = select(Agent).where(*conditions)
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    direction = sort_column.desc() if str(sort_order).lower() == "desc" else sort_column.asc()
    stmt = base.order_by(direction, Agent.id.asc()).offset((page - 1) * page_size).limit(page_size)
    return list(db.scalars(stmt).all()), total


def commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise DuplicateAgentError(
            "agent with this slug already exists in this application"
        ) from None
