from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.modules.model_inventory.domain.errors import (
    DeploymentConcurrencyConflictError,
    DuplicatePrimaryEndpointError,
)
from app.modules.model_inventory.models import DeploymentEndpoint


def get_endpoint(db: Session, tenant_id: UUID, endpoint_id: UUID) -> DeploymentEndpoint | None:
    return db.scalar(
        select(DeploymentEndpoint).where(
            DeploymentEndpoint.tenant_id == tenant_id,
            DeploymentEndpoint.id == endpoint_id,
        )
    )


def list_endpoints(
    db: Session, tenant_id: UUID, deployment_id: UUID, include_archived: bool = False
) -> list[DeploymentEndpoint]:
    conditions = [
        DeploymentEndpoint.tenant_id == tenant_id,
        DeploymentEndpoint.deployment_id == deployment_id,
    ]
    if not include_archived:
        conditions.append(DeploymentEndpoint.archived_at.is_(None))
    stmt = (
        select(DeploymentEndpoint)
        .where(*conditions)
        .order_by(DeploymentEndpoint.created_at.asc(), DeploymentEndpoint.id.asc())
    )
    return list(db.scalars(stmt).all())


def find_primary_inference(
    db: Session, tenant_id: UUID, deployment_id: UUID, exclude_id: UUID | None = None
) -> DeploymentEndpoint | None:
    conditions = [
        DeploymentEndpoint.tenant_id == tenant_id,
        DeploymentEndpoint.deployment_id == deployment_id,
        DeploymentEndpoint.endpoint_type == "inference",
        DeploymentEndpoint.is_primary.is_(True),
        DeploymentEndpoint.archived_at.is_(None),
    ]
    if exclude_id is not None:
        conditions.append(DeploymentEndpoint.id != exclude_id)
    return db.scalar(select(DeploymentEndpoint).where(*conditions))


def create_endpoint(db: Session, **fields) -> DeploymentEndpoint:
    endpoint = DeploymentEndpoint(**fields)
    db.add(endpoint)
    return endpoint


def commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # The partial unique index on (deployment_id) WHERE is_primary AND
        # endpoint_type='inference' AND archived_at IS NULL is the only
        # constraint an endpoint insert can violate in V1 (FKs are guarded
        # by the service).
        raise DuplicatePrimaryEndpointError(
            "deployment already has an active primary inference endpoint"
        ) from None
    except StaleDataError:
        db.rollback()
        raise DeploymentConcurrencyConflictError(
            "endpoint was modified by another request"
        ) from None
