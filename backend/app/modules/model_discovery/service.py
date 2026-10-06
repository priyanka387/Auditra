import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.model_discovery import repository
from app.modules.model_discovery.domain import (
    can_transition,
    canonical_identity,
    normalize_model_identifier,
    normalize_provider,
)
from app.modules.model_discovery.enums import DiscoveryStatus
from app.modules.model_discovery.errors import (
    DiscoveryError,
    DiscoveryNotFoundError,
    DuplicateDiscoveryError,
    InvalidDiscoveryTransitionError,
)
from app.modules.model_discovery.schemas import (
    DiscoveryCreate,
    DiscoveryFilter,
    DiscoveryResponse,
)
from app.modules.model_inventory.repositories.model_repository import find_by_canonical_key

logger = logging.getLogger("auditra.discovery")

_STICKY_STATUSES = frozenset(
    {
        DiscoveryStatus.IGNORED.value,
        DiscoveryStatus.REGISTERED.value,
        DiscoveryStatus.FAILED.value,
    }
)


class DiscoveryService:
    def __init__(
        self,
        db: Session,
        tenant_id: UUID,
        request_id: str | None = None,
        actor: str | None = None,
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.request_id = request_id
        self.actor = actor

    def ingest(self, payload: DiscoveryCreate) -> tuple[DiscoveryResponse, bool]:
        now = datetime.now(UTC)
        try:
            identity = canonical_identity(payload.provider, payload.model_identifier)
            provider = normalize_provider(payload.provider)
            model_identifier = normalize_model_identifier(payload.model_identifier)
        except ValueError as exc:
            raise DiscoveryError(str(exc)) from None

        source_type = payload.source_type.value
        existing = repository.find_for_ingest(
            self.db, self.tenant_id, source_type, payload.source_identifier, identity
        )
        if existing is not None:
            return self._refresh(existing, payload, now), False

        entity = repository.create_discovery(
            self.db,
            tenant_id=self.tenant_id,
            source_type=source_type,
            source_identifier=payload.source_identifier,
            external_identifier=payload.external_identifier,
            provider=provider,
            model_identifier=model_identifier,
            model_type=payload.model_type,
            display_name=payload.display_name,
            canonical_identity=identity,
            metadata_=payload.metadata or {},
            status=DiscoveryStatus.DISCOVERED.value,
            matched_model_id=None,
            first_seen_at=now,
            last_seen_at=now,
            observation_count=1,
        )
        try:
            self._reconcile(entity)
            repository.commit(self.db)
        except (IntegrityError, DuplicateDiscoveryError):
            # duplicate key fires either at query-time autoflush or at commit
            self.db.rollback()
            raced = repository.find_for_ingest(
                self.db, self.tenant_id, source_type, payload.source_identifier, identity
            )
            if raced is None:
                raise
            return self._refresh(raced, payload, now), False

        self._log("discovery observation ingested", entity)
        return DiscoveryResponse.model_validate(entity, from_attributes=True), True

    def get(self, discovery_id: UUID) -> DiscoveryResponse:
        entity = repository.get_discovery(self.db, self.tenant_id, discovery_id)
        if entity is None:
            raise DiscoveryNotFoundError(f"discovery '{discovery_id}' not found")
        return DiscoveryResponse.model_validate(entity, from_attributes=True)

    def list(self, filters: DiscoveryFilter) -> tuple[list[DiscoveryResponse], int]:
        items, total = repository.list_discoveries(self.db, self.tenant_id, filters)
        return (
            [DiscoveryResponse.model_validate(item, from_attributes=True) for item in items],
            total,
        )

    def _refresh(self, entity, payload: DiscoveryCreate, now: datetime) -> DiscoveryResponse:
        entity.observation_count += 1
        entity.last_seen_at = now
        if payload.metadata:
            entity.metadata_ = {**(entity.metadata_ or {}), **payload.metadata}
        if entity.status not in _STICKY_STATUSES:
            self._reconcile(entity)
        repository.commit(self.db)
        self._log("discovery observation refreshed", entity)
        return DiscoveryResponse.model_validate(entity, from_attributes=True)

    def _reconcile(self, entity) -> None:
        model = find_by_canonical_key(self.db, self.tenant_id, entity.canonical_identity)
        target = (
            DiscoveryStatus.MATCHED.value if model is not None else DiscoveryStatus.UNRESOLVED.value
        )
        if target != entity.status and not can_transition(entity.status, target):
            raise InvalidDiscoveryTransitionError(
                f"cannot transition discovery from {entity.status} to {target}"
            )
        entity.status = target
        entity.matched_model_id = model.id if model is not None else None

    def _log(self, message: str, entity) -> None:
        logger.info(
            message,
            extra={
                "discovery_id": str(entity.id),
                "tenant_id": str(self.tenant_id),
                "source_type": entity.source_type,
                "canonical_identity": entity.canonical_identity,
                "status": entity.status,
                "observation_count": entity.observation_count,
                "matched_model_id": str(entity.matched_model_id)
                if entity.matched_model_id
                else None,
                "request_id": self.request_id,
            },
        )
