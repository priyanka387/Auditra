"""Risk Register services (RF-01): business rules, tenant isolation, audit.

Both services follow the asset_discovery pattern: tenant-scoped entity
lookups (unknown and cross-tenant ids are indistinguishable 404s),
governance mutations staged with ``AuditService.record_event`` in the
same transaction, then a single commit.
"""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.asset_discovery.models import DiscoveredAsset
from app.modules.audit.enums import AuditEventType
from app.modules.audit.service import AuditService
from app.modules.risk import repository
from app.modules.risk.errors import (
    DuplicateRiskCategoryError,
    RiskArchivedError,
    RiskAssetNotFoundError,
    RiskCategoryNotFoundError,
    RiskError,
    RiskNotFoundError,
)
from app.modules.risk.models import Risk, RiskCategory
from app.modules.risk.schemas import (
    RiskCategoryCreate,
    RiskCategoryFilter,
    RiskCategoryUpdate,
    RiskCreate,
    RiskFilter,
    RiskUpdate,
)

logger = logging.getLogger("auditra.risk")


def risk_audit_state(risk: Risk) -> dict:
    """Governance-relevant projection of a risk for audit state."""
    return {
        "title": risk.title,
        "description": risk.description,
        "category_id": str(risk.category_id),
        "source": risk.source,
        "asset_id": str(risk.asset_id) if risk.asset_id else None,
        "owner": risk.owner,
        "team": risk.team,
        "status": risk.status,
        "archived_at": risk.archived_at.isoformat() if risk.archived_at else None,
    }


def category_audit_state(category: RiskCategory) -> dict:
    """Governance-relevant projection of a risk category for audit state."""
    return {
        "name": category.name,
        "description": category.description,
        "slug": category.slug,
        "is_system": category.is_system,
        "is_active": category.is_active,
    }


class RiskCategoryService:
    """CRUD over the per-tenant risk taxonomy.

    System categories arrive through the idempotent seed with
    ``is_system=True``; categories created here are always
    ``is_system=False`` and slugs are immutable after creation.
    """

    def __init__(
        self,
        db: Session,
        tenant_id: UUID,
        request_id: str | None = None,
        actor: str | None = None,
        source: str = "api",
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.request_id = request_id
        self.actor = actor
        self.audit = AuditService(db, tenant_id, request_id=request_id, actor=actor, source=source)

    def create(self, payload: RiskCategoryCreate) -> RiskCategory:
        if repository.find_category_by_slug(self.db, self.tenant_id, payload.slug) is not None:
            raise DuplicateRiskCategoryError(f"risk category '{payload.slug}' already exists")
        category = repository.create_category(
            self.db,
            id=uuid4(),
            tenant_id=self.tenant_id,
            name=payload.name,
            description=payload.description,
            slug=payload.slug,
            is_system=False,
        )
        try:
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            raise DuplicateRiskCategoryError(
                f"risk category '{payload.slug}' already exists"
            ) from None

        self.audit.record_event(
            event_type=AuditEventType.RISK_CATEGORY_CREATED,
            resource_type="risk_category",
            resource_id=str(category.id),
            after_state=category_audit_state(category),
        )
        repository.commit_category(self.db)
        self._log("risk category created", category, ["created"])
        return category

    def get(self, category_id: UUID) -> RiskCategory:
        return self._entity(category_id)

    def list(self, filters: RiskCategoryFilter) -> tuple[list[RiskCategory], int]:
        return repository.list_categories(self.db, self.tenant_id, filters)

    def update(self, category_id: UUID, payload: RiskCategoryUpdate) -> RiskCategory:
        category = self._entity(category_id)

        before_state = category_audit_state(category)
        changed: list[str] = []
        # slug is immutable once created (RF-01 §26): only name,
        # description and is_active move
        if payload.name is not None and payload.name != category.name:
            category.name = payload.name
            changed.append("name")
        if payload.description is not None and payload.description != category.description:
            category.description = payload.description
            changed.append("description")
        if payload.is_active is not None and payload.is_active != category.is_active:
            category.is_active = payload.is_active
            changed.append("is_active")

        if changed:
            self.audit.record_event(
                event_type=AuditEventType.RISK_CATEGORY_UPDATED,
                resource_type="risk_category",
                resource_id=str(category.id),
                before_state=before_state,
                after_state=category_audit_state(category),
                changed_fields=sorted(changed),
            )
        repository.commit_category(self.db)
        self._log("risk category updated", category, changed)
        return category

    def _entity(self, category_id: UUID) -> RiskCategory:
        category = repository.get_category(self.db, self.tenant_id, category_id)
        if category is None:
            raise RiskCategoryNotFoundError(f"risk category '{category_id}' not found")
        return category

    def _log(self, message: str, category: RiskCategory, changed: Sequence[str]) -> None:
        logger.info(
            message,
            extra={
                "risk_category_id": str(category.id),
                "tenant_id": str(self.tenant_id),
                "slug": category.slug,
                "is_system": category.is_system,
                "is_active": category.is_active,
                "changed_fields": changed,
                "request_id": self.request_id,
            },
        )


class RiskService:
    """The RF-01 register: create/read/update risks and soft-archive them.

    Archive is the only removal path (RF-01 §20): ``archived_at`` is set,
    the row and its category/asset relationships stay intact, and an
    archived risk rejects further mutations with a 409 while remaining
    queryable through ``RiskFilter(archived=True)``.
    """

    def __init__(
        self,
        db: Session,
        tenant_id: UUID,
        request_id: str | None = None,
        actor: str | None = None,
        source: str = "api",
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.request_id = request_id
        self.actor = actor
        self.audit = AuditService(db, tenant_id, request_id=request_id, actor=actor, source=source)

    def create(self, payload: RiskCreate) -> Risk:
        self._require_category(payload.category_id)
        if payload.asset_id is not None:
            self._require_asset(payload.asset_id)

        risk = repository.create_risk(
            self.db,
            id=uuid4(),
            tenant_id=self.tenant_id,
            title=payload.title,
            description=payload.description,
            category_id=payload.category_id,
            source=payload.source.value,
            asset_id=payload.asset_id,
            owner=payload.owner,
            team=payload.team,
            status=payload.status.value,
            created_by=self.actor,
        )
        try:
            self.db.flush()
        except IntegrityError as exc:
            self.db.rollback()
            raise RiskError("risk references a category or asset that no longer exists") from exc
        self.audit.record_event(
            event_type=AuditEventType.RISK_CREATED,
            resource_type="risk",
            resource_id=str(risk.id),
            after_state=risk_audit_state(risk),
        )
        repository.commit_risk(self.db)
        self._log("risk created", risk, ["created"])
        return risk

    def get(self, risk_id: UUID) -> Risk:
        return self._entity(risk_id)

    def list(self, filters: RiskFilter) -> tuple[list[Risk], int]:
        return repository.list_risks(self.db, self.tenant_id, filters)

    def update(self, risk_id: UUID, payload: RiskUpdate) -> Risk:
        risk = self._entity(risk_id)
        if risk.archived_at is not None:
            raise RiskArchivedError(f"risk '{risk_id}' is archived")

        if payload.category_id is not None and payload.category_id != risk.category_id:
            self._require_category(payload.category_id)
        if payload.asset_id is not None and payload.asset_id != risk.asset_id:
            self._require_asset(payload.asset_id)

        before_state = risk_audit_state(risk)
        changed: list[str] = []
        if payload.title is not None and payload.title != risk.title:
            risk.title = payload.title
            changed.append("title")
        if payload.description is not None and payload.description != risk.description:
            risk.description = payload.description
            changed.append("description")
        if payload.category_id is not None and payload.category_id != risk.category_id:
            risk.category_id = payload.category_id
            changed.append("category_id")
        if payload.source is not None and payload.source.value != risk.source:
            risk.source = payload.source.value
            changed.append("source")
        if payload.asset_id is not None and payload.asset_id != risk.asset_id:
            risk.asset_id = payload.asset_id
            changed.append("asset_id")
        if payload.owner is not None and payload.owner != risk.owner:
            risk.owner = payload.owner
            changed.append("owner")
        if payload.team is not None and payload.team != risk.team:
            risk.team = payload.team
            changed.append("team")
        if payload.status is not None and payload.status.value != risk.status:
            risk.status = payload.status.value
            changed.append("status")
        if changed:
            risk.updated_by = self.actor

        if changed:
            self.audit.record_event(
                event_type=AuditEventType.RISK_UPDATED,
                resource_type="risk",
                resource_id=str(risk.id),
                before_state=before_state,
                after_state=risk_audit_state(risk),
                changed_fields=sorted(changed),
            )
        repository.commit_risk(self.db)
        self._log("risk updated", risk, changed)
        return risk

    def archive(self, risk_id: UUID) -> Risk:
        """Idempotent soft archive: the second call is a no-op (no audit)."""
        risk = self._entity(risk_id)
        if risk.archived_at is not None:
            return risk

        before_state = risk_audit_state(risk)
        risk.archived_at = datetime.now(UTC)
        risk.updated_by = self.actor
        self.audit.record_event(
            event_type=AuditEventType.RISK_ARCHIVED,
            resource_type="risk",
            resource_id=str(risk.id),
            before_state=before_state,
            after_state=risk_audit_state(risk),
            changed_fields=["archived_at"],
        )
        repository.commit_risk(self.db)
        self._log("risk archived", risk, ["archived"])
        return risk

    def resolve_refs(
        self, risks: Sequence[Risk]
    ) -> tuple[dict[UUID, RiskCategory], dict[UUID, DiscoveredAsset]]:
        """Categories and assets for a page of risks in at most two queries.

        Routes build their embedded ``category``/``asset`` summaries from
        these maps instead of touching the ORM relationships per row.
        """
        category_ids = {risk.category_id for risk in risks}
        asset_ids = {risk.asset_id for risk in risks if risk.asset_id is not None}
        categories = repository.get_categories_by_ids(self.db, self.tenant_id, category_ids)
        assets = repository.get_assets_by_ids(self.db, self.tenant_id, asset_ids)
        return categories, assets

    def _entity(self, risk_id: UUID) -> Risk:
        risk = repository.get_risk(self.db, self.tenant_id, risk_id)
        if risk is None:
            raise RiskNotFoundError(f"risk '{risk_id}' not found")
        return risk

    def _require_category(self, category_id: UUID) -> None:
        if repository.get_category(self.db, self.tenant_id, category_id) is None:
            raise RiskCategoryNotFoundError(f"risk category '{category_id}' not found")

    def _require_asset(self, asset_id: UUID) -> None:
        if repository.get_assets_by_ids(self.db, self.tenant_id, {asset_id}) == {}:
            raise RiskAssetNotFoundError(f"discovered asset '{asset_id}' not found")

    def _log(self, message: str, risk: Risk, changed: Sequence[str]) -> None:
        logger.info(
            message,
            extra={
                "risk_id": str(risk.id),
                "tenant_id": str(self.tenant_id),
                "category_id": str(risk.category_id),
                "asset_id": str(risk.asset_id) if risk.asset_id else None,
                "status": risk.status,
                "source": risk.source,
                "changed_fields": changed,
                "request_id": self.request_id,
            },
        )
