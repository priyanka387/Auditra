from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.modules.model_inventory.domain import (
    ModelEvent,
    build_canonical_key,
    can_transition,
    dispatch_event,
)
from app.modules.model_inventory.domain.errors import (
    DuplicateModelError,
    IdentityImmutableError,
    InvalidTransitionError,
    ModelArchivedError,
    ModelNotFoundError,
    ModelTypeNotFoundError,
    ProviderInactiveError,
    ProviderNotFoundError,
)
from app.modules.model_inventory.models import Model, ModelProvider, ModelTag, ModelTagLink
from app.modules.model_inventory.repositories.model_repository import (
    ModelFilters,
    commit,
    create_model,
    find_by_canonical_key,
    get_model,
    get_model_type_by_slug,
    get_provider_by_slug,
    query_models,
)
from app.modules.model_inventory.schemas import ModelCreate, ModelUpdate, parse_tags

_UPDATE_FIELDS = (
    "name",
    "description",
    "owner_name",
    "owner_contact",
    "team_name",
    "hosting_mode",
    "runtime_hint",
    "lifecycle_state",
    "source_reference",
)


class ModelService:
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

    def register_model(self, payload: ModelCreate) -> Model:
        provider = get_provider_by_slug(self.db, self.tenant_id, payload.provider_slug)
        if provider is None:
            raise ProviderNotFoundError(f"provider '{payload.provider_slug}' not found")
        if not provider.is_active:
            raise ProviderInactiveError(f"provider '{payload.provider_slug}' is not active")
        model_type = get_model_type_by_slug(self.db, self.tenant_id, payload.model_type_slug)
        if model_type is None:
            raise ModelTypeNotFoundError(f"model type '{payload.model_type_slug}' not found")

        canonical_key = build_canonical_key(payload.provider_slug, payload.native_model_id)
        if find_by_canonical_key(self.db, self.tenant_id, canonical_key) is not None:
            raise DuplicateModelError(f"model '{canonical_key}' is already registered")

        tag_ids = [
            self._resolve_tag(key, value)
            for key, value in dict.fromkeys(parse_tags(payload.tags))
        ]
        model = create_model(
            self.db,
            id=uuid4(),
            tenant_id=self.tenant_id,
            provider_id=provider.id,
            model_type_id=model_type.id,
            name=payload.name,
            native_model_id=payload.native_model_id,
            canonical_key=canonical_key,
            description=payload.description,
            owner_name=payload.owner_name,
            owner_contact=payload.owner_contact,
            team_name=payload.team_name,
            hosting_mode=payload.hosting_mode,
            runtime_hint=payload.runtime_hint,
            lifecycle_state=payload.lifecycle_state,
            source_type=payload.source_type,
            source_reference=payload.source_reference,
            metadata_=payload.metadata,
        )
        for tag_id in tag_ids:
            self.db.add(ModelTagLink(model_id=model.id, tag_id=tag_id))

        model_id = model.id
        commit(self.db)
        self._dispatch("model.created", model_id, [])
        return model

    def get_model(self, model_id: UUID) -> Model:
        return self._get_or_404(model_id)

    def list_models(
        self,
        filters: ModelFilters,
        page: int = 1,
        page_size: int = 25,
        sort_by: str = "updated_at",
        sort_order: str = "desc",
        include_archived: bool = False,
    ) -> tuple[list[Model], int]:
        return query_models(
            self.db, self.tenant_id, filters, page, page_size, sort_by, sort_order, include_archived
        )

    def update_model(self, model_id: UUID, payload: ModelUpdate) -> Model:
        model = self._get_or_404(model_id)
        if model.lifecycle_state == "ARCHIVED":
            raise ModelArchivedError(f"model '{model_id}' is archived")

        if payload.native_model_id is not None and payload.native_model_id != model.native_model_id:
            raise IdentityImmutableError("native_model_id cannot be changed")
        if payload.provider_slug is not None:
            current_slug = self.db.scalar(
                select(ModelProvider.slug).where(ModelProvider.id == model.provider_id)
            )
            if payload.provider_slug != current_slug:
                raise IdentityImmutableError("provider_slug cannot be changed")

        if payload.lifecycle_state is not None and not can_transition(
            model.lifecycle_state, payload.lifecycle_state
        ):
            raise InvalidTransitionError(
                f"cannot transition from {model.lifecycle_state} to {payload.lifecycle_state}"
            )

        changed: list[str] = []
        for field in _UPDATE_FIELDS:
            value = getattr(payload, field)
            if value is not None and value != getattr(model, field):
                changed.append(field)

        new_type_id = None
        if payload.model_type_slug is not None:
            model_type = get_model_type_by_slug(self.db, self.tenant_id, payload.model_type_slug)
            if model_type is None:
                raise ModelTypeNotFoundError(f"model type '{payload.model_type_slug}' not found")
            if model_type.id != model.model_type_id:
                new_type_id = model_type.id
                changed.append("model_type_slug")

        if payload.metadata is not None and payload.metadata != model.metadata_:
            changed.append("metadata")

        new_tag_pairs = None
        if payload.tags is not None:
            new_tag_pairs = list(dict.fromkeys(parse_tags(payload.tags)))
            if set(new_tag_pairs) != self._current_tag_pairs(model.id):
                changed.append("tags")

        for field in _UPDATE_FIELDS:
            value = getattr(payload, field)
            if value is not None:
                setattr(model, field, value)
        if new_type_id is not None:
            model.model_type_id = new_type_id
        if payload.metadata is not None:
            model.metadata_ = payload.metadata
        if new_tag_pairs is not None:
            self.db.execute(delete(ModelTagLink).where(ModelTagLink.model_id == model.id))
            for key, value in new_tag_pairs:
                self.db.add(ModelTagLink(model_id=model.id, tag_id=self._resolve_tag(key, value)))

        model.record_version += 1
        commit(self.db)
        self._dispatch("model.updated", model_id, sorted(changed))
        return model

    def archive_model(self, model_id: UUID) -> None:
        model = self._get_or_404(model_id)
        if model.lifecycle_state == "ARCHIVED":
            return
        model.lifecycle_state = "ARCHIVED"
        model.archived_at = datetime.now(UTC)
        archived_id = model.id
        commit(self.db)
        self._dispatch("model.archived", archived_id, ["archived"])

    def _get_or_404(self, model_id: UUID) -> Model:
        model = get_model(self.db, self.tenant_id, model_id)
        if model is None:
            raise ModelNotFoundError(f"model '{model_id}' not found")
        return model

    def _resolve_tag(self, key: str, value: str) -> UUID:
        tag_id = self.db.scalar(
            select(ModelTag.id).where(
                ModelTag.tenant_id == self.tenant_id,
                ModelTag.key == key,
                ModelTag.value == value,
            )
        )
        if tag_id is not None:
            return tag_id
        tag = ModelTag(id=uuid4(), tenant_id=self.tenant_id, key=key, value=value)
        self.db.add(tag)
        return tag.id

    def _current_tag_pairs(self, model_id: UUID) -> set[tuple[str, str]]:
        rows = self.db.execute(
            select(ModelTag.key, ModelTag.value)
            .join(ModelTagLink, ModelTagLink.tag_id == ModelTag.id)
            .where(ModelTagLink.model_id == model_id)
        )
        return {tuple(row) for row in rows}

    def _dispatch(self, event_type: str, model_id: UUID, change_summary: list[str]) -> None:
        dispatch_event(
            ModelEvent(
                event_id=str(uuid4()),
                event_type=event_type,
                model_id=model_id,
                tenant_id=self.tenant_id,
                occurred_at=datetime.now(UTC),
                actor=self.actor,
                change_summary=change_summary,
                request_id=self.request_id,
            )
        )
