from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.modules.model_inventory.domain import (
    ModelVersionEvent,
    build_canonical_version_key,
    can_version_transition,
    dispatch_event,
)
from app.modules.model_inventory.domain.errors import (
    DuplicateModelVersionError,
    InvalidVersionTransitionError,
    ModelArchivedError,
    ModelNotFoundError,
    ModelVersionArchivedError,
    ModelVersionNotFoundError,
    VersionIdentityImmutableError,
)
from app.modules.model_inventory.domain.lifecycle import VERSION_INITIAL_STATE
from app.modules.model_inventory.models import Model, ModelVersion
from app.modules.model_inventory.repositories.model_repository import get_model
from app.modules.model_inventory.repositories.model_version_repository import (
    ModelVersionFilters,
    commit,
    create_model_version,
    find_by_canonical_key,
    get_model_version,
    query_model_versions,
)
from app.modules.model_inventory.schemas import (
    ModelVersionCreate,
    ModelVersionLifecycleUpdate,
    ModelVersionUpdate,
)

_IDENTITY_FIELDS = (
    "model_id",
    "identity_type",
    "native_version_id",
    "canonical_version_key",
    "version_label",
)
_MUTABLE_FIELDS = ("display_name", "description", "source_reference")


class ModelVersionService:
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

    def create_version(self, model_id: UUID, payload: ModelVersionCreate) -> ModelVersion:
        model = self._get_model_or_404(model_id)
        if model.lifecycle_state == "ARCHIVED":
            raise ModelArchivedError(f"model '{model_id}' is archived")

        key = build_canonical_version_key(
            payload.identity_type, payload.version_label, payload.native_version_id
        )
        if find_by_canonical_key(self.db, self.tenant_id, model_id, key) is not None:
            raise DuplicateModelVersionError(f"model version '{key}' already exists")

        version = create_model_version(
            self.db,
            id=uuid4(),
            tenant_id=self.tenant_id,
            model_id=model_id,
            identity_type=payload.identity_type,
            version_label=payload.version_label,
            native_version_id=payload.native_version_id,
            canonical_version_key=key,
            display_name=payload.display_name,
            description=payload.description,
            lifecycle_state=VERSION_INITIAL_STATE,
            metadata_=payload.metadata,
            source_type=payload.source_type,
            source_reference=payload.source_reference,
            created_by=self.actor,
        )
        version_id = version.id
        commit(self.db)
        self._dispatch("model_version.created", model_id, version_id, [])
        return version

    def get_version(self, model_id: UUID, version_id: UUID) -> ModelVersion:
        self._get_model_or_404(model_id)
        return self._get_or_404(model_id, version_id)

    def list_versions(
        self,
        model_id: UUID,
        filters: ModelVersionFilters,
        page: int = 1,
        page_size: int = 20,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        include_archived: bool = False,
    ) -> tuple[list[ModelVersion], int]:
        self._get_model_or_404(model_id)
        return query_model_versions(
            self.db,
            self.tenant_id,
            model_id,
            filters,
            page,
            page_size,
            sort_by,
            sort_order,
            include_archived,
        )

    def update_version(
        self, model_id: UUID, version_id: UUID, payload: ModelVersionUpdate
    ) -> ModelVersion:
        self._get_model_or_404(model_id)
        version = self._get_or_404(model_id, version_id)
        if version.lifecycle_state == "ARCHIVED":
            raise ModelVersionArchivedError(f"model version '{version_id}' is archived")

        for field in _IDENTITY_FIELDS:
            value = getattr(payload, field)
            if value is not None and value != getattr(version, field):
                raise VersionIdentityImmutableError(f"{field} cannot be changed")

        changed: list[str] = []
        for field in _MUTABLE_FIELDS:
            value = getattr(payload, field)
            if value is not None and value != getattr(version, field):
                changed.append(field)
        if payload.metadata is not None and payload.metadata != version.metadata_:
            changed.append("metadata")

        for field in _MUTABLE_FIELDS:
            value = getattr(payload, field)
            if value is not None:
                setattr(version, field, value)
        if payload.metadata is not None:
            version.metadata_ = payload.metadata

        commit(self.db)
        self._dispatch("model_version.updated", model_id, version_id, sorted(changed))
        return version

    def transition_version(
        self, model_id: UUID, version_id: UUID, payload: ModelVersionLifecycleUpdate
    ) -> ModelVersion:
        self._get_model_or_404(model_id)
        version = self._get_or_404(model_id, version_id)
        if version.lifecycle_state == "ARCHIVED":
            raise ModelVersionArchivedError(f"model version '{version_id}' is archived")

        current = version.lifecycle_state
        target = payload.target_state
        if not can_version_transition(current, target):
            raise InvalidVersionTransitionError(f"cannot transition from {current} to {target}")

        version.lifecycle_state = target
        if target == "ARCHIVED":
            version.archived_at = datetime.now(UTC)
        commit(self.db)
        self._dispatch(
            "model_version.lifecycle_changed", model_id, version_id, [f"{current}->{target}"]
        )
        return version

    def archive_version(self, model_id: UUID, version_id: UUID) -> None:
        self._get_model_or_404(model_id)
        version = self._get_or_404(model_id, version_id)
        if version.lifecycle_state == "ARCHIVED":
            return
        version.lifecycle_state = "ARCHIVED"
        version.archived_at = datetime.now(UTC)
        commit(self.db)
        self._dispatch("model_version.archived", model_id, version_id, ["archived"])

    def _get_model_or_404(self, model_id: UUID) -> Model:
        model = get_model(self.db, self.tenant_id, model_id)
        if model is None:
            raise ModelNotFoundError(f"model '{model_id}' not found")
        return model

    def _get_or_404(self, model_id: UUID, version_id: UUID) -> ModelVersion:
        version = get_model_version(self.db, self.tenant_id, model_id, version_id)
        if version is None:
            raise ModelVersionNotFoundError(f"model version '{version_id}' not found")
        return version

    def _dispatch(
        self, event_type: str, model_id: UUID, version_id: UUID, change_summary: list[str]
    ) -> None:
        dispatch_event(
            ModelVersionEvent(
                event_id=str(uuid4()),
                event_type=event_type,
                tenant_id=self.tenant_id,
                model_id=model_id,
                model_version_id=version_id,
                occurred_at=datetime.now(UTC),
                actor=self.actor,
                change_summary=change_summary,
                request_id=self.request_id,
            )
        )
