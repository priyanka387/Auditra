from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.model_inventory.domain.errors import (
    DuplicateApplicationError,
    InvalidSortFieldError,
)
from app.modules.model_inventory.models import Application

ALLOWED_SORT_FIELDS: dict[str, Any] = {
    "created_at": Application.created_at,
    "updated_at": Application.updated_at,
    "name": Application.name,
    "slug": Application.slug,
    "status": Application.status,
    "application_type": Application.application_type,
}


@dataclass
class ApplicationFilters:
    search: str | None = None
    status: str | None = None
    application_type: str | None = None
    owner: str | None = None
    team: str | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    updated_after: datetime | None = None
    updated_before: datetime | None = None


def get_application(db: Session, tenant_id: UUID, application_id: UUID) -> Application | None:
    return db.scalar(
        select(Application).where(
            Application.tenant_id == tenant_id,
            Application.id == application_id,
        )
    )


def find_duplicate_slug(db: Session, tenant_id: UUID, slug: str) -> Application | None:
    return db.scalar(
        select(Application).where(
            Application.tenant_id == tenant_id,
            Application.slug == slug,
        )
    )


def create_application(db: Session, **fields: Any) -> Application:
    application = Application(**fields)
    db.add(application)
    return application


def query_applications(
    db: Session,
    tenant_id: UUID,
    filters: ApplicationFilters | None,
    page: int,
    page_size: int,
    sort_by: str,
    sort_order: str,
    include_archived: bool,
) -> tuple[list[Application], int]:
    sort_column = ALLOWED_SORT_FIELDS.get(sort_by)
    if sort_column is None:
        raise InvalidSortFieldError(f"sort field '{sort_by}' is not allowed")

    conditions: list[Any] = [Application.tenant_id == tenant_id]
    if not include_archived and (filters is None or filters.status is None):
        conditions.append(Application.status != "ARCHIVED")
    if filters is not None:
        if filters.status is not None:
            conditions.append(Application.status == filters.status)
        if filters.application_type is not None:
            conditions.append(Application.application_type == filters.application_type)
        if filters.owner is not None:
            conditions.append(Application.owner_name == filters.owner)
        if filters.team is not None:
            conditions.append(Application.team_name == filters.team)
        if filters.search is not None:
            term = f"%{filters.search}%"
            conditions.append(
                or_(
                    Application.name.ilike(term),
                    Application.slug.ilike(term),
                    Application.display_name.ilike(term),
                )
            )
        if filters.created_after is not None:
            conditions.append(Application.created_at >= filters.created_after)
        if filters.created_before is not None:
            conditions.append(Application.created_at <= filters.created_before)
        if filters.updated_after is not None:
            conditions.append(Application.updated_at >= filters.updated_after)
        if filters.updated_before is not None:
            conditions.append(Application.updated_at <= filters.updated_before)

    base = select(Application).where(*conditions)
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    direction = sort_column.desc() if str(sort_order).lower() == "desc" else sort_column.asc()
    stmt = (
        base.order_by(direction, Application.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(db.scalars(stmt).all()), total


def commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise DuplicateApplicationError(
            "application with this slug already exists for this tenant"
        ) from None
