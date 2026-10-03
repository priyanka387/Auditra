import math
from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.modules.model_inventory.domain import ModelEvent, register_handler, reset_handlers
from app.modules.model_inventory.domain.errors import (
    DuplicateModelError,
    IdentityImmutableError,
    InvalidSortFieldError,
    InvalidTransitionError,
    ModelArchivedError,
    ModelNotFoundError,
    ModelTypeNotFoundError,
    ProviderInactiveError,
)
from app.modules.model_inventory.models import (
    Model,
    ModelProvider,
    ModelTag,
    ModelTagLink,
    ModelType,
)
from app.modules.model_inventory.repositories import ModelFilters
from app.modules.model_inventory.schemas import ModelCreate, ModelUpdate
from app.modules.model_inventory.services import ModelService
from app.seed import seed_reference_data


@pytest.fixture(autouse=True)
def _reset_event_handlers():
    reset_handlers()
    yield
    reset_handlers()


def _payload(**overrides):
    base = {
        "provider_slug": "openai",
        "model_type_slug": "llm",
        "name": "Test Model",
        "native_model_id": "test-model-1",
    }
    base.update(overrides)
    return ModelCreate(**base)


def _tags_of(db, model_id):
    rows = db.execute(
        select(ModelTag.key, ModelTag.value)
        .join(ModelTagLink, ModelTagLink.tag_id == ModelTag.id)
        .where(ModelTagLink.model_id == model_id)
    )
    return {tuple(row) for row in rows}


def test_register_creates_model_and_dispatches_event(db):
    seed_reference_data(db)
    events: list[ModelEvent] = []
    register_handler(events.append)
    service = ModelService(
        db, tenant_id=settings.default_tenant_id, request_id="req-123", actor="alice"
    )

    model = service.register_model(
        _payload(name="Customer Support LLM", tags=["env=prod", "team=support"])
    )

    assert model.id is not None
    assert model.canonical_key == "openai|test-model-1"
    assert model.lifecycle_state == "REGISTERED"
    assert model.record_version == 1
    assert _tags_of(db, model.id) == {("env", "prod"), ("team", "support")}

    fetched = service.get_model(model.id)
    assert fetched.name == "Customer Support LLM"

    assert len(events) == 1
    event = events[0]
    assert event.event_type == "model.created"
    assert event.model_id == model.id
    assert event.tenant_id == settings.default_tenant_id
    assert event.change_summary == []
    assert event.actor == "alice"
    assert event.request_id == "req-123"
    assert isinstance(event.event_id, str) and event.event_id
    assert isinstance(event.occurred_at, datetime)
    assert event.occurred_at.tzinfo is not None


def test_duplicate_canonical_key_rejected(db):
    seed_reference_data(db)
    service = ModelService(db, tenant_id=settings.default_tenant_id)
    service.register_model(_payload(native_model_id="dup-1"))

    with pytest.raises(DuplicateModelError) as exc:
        service.register_model(_payload(name="Second", native_model_id="dup-1"))

    assert exc.value.code == "MODEL_ALREADY_EXISTS"
    assert exc.value.http_status == 409
    assert db.scalar(select(func.count()).select_from(Model)) == 1


def test_duplicate_race_integrity_error_mapped(db, monkeypatch):
    seed_reference_data(db)
    service = ModelService(db, tenant_id=settings.default_tenant_id)
    service.register_model(_payload(native_model_id="race-1"))

    monkeypatch.setattr(
        "app.modules.model_inventory.services.model_service.find_by_canonical_key",
        lambda *_args, **_kwargs: None,
    )
    with pytest.raises(DuplicateModelError) as exc:
        service.register_model(_payload(name="Second", native_model_id="race-1"))

    assert exc.value.code == "MODEL_ALREADY_EXISTS"
    assert db.scalar(select(func.count()).select_from(Model)) == 1


def test_provider_inactive_rejected(db):
    seed_reference_data(db)
    provider = db.scalar(select(ModelProvider).where(ModelProvider.slug == "openai"))
    provider.is_active = False
    db.commit()
    service = ModelService(db, tenant_id=settings.default_tenant_id)

    with pytest.raises(ProviderInactiveError) as exc:
        service.register_model(_payload())

    assert exc.value.code == "PROVIDER_INACTIVE"
    assert exc.value.http_status == 409


def test_invalid_transition_rejected(db, service):
    seed_reference_data(db)
    model = service.register_model(_payload(native_model_id="trans-1"))

    with pytest.raises(InvalidTransitionError) as exc:
        service.update_model(model.id, ModelUpdate(lifecycle_state="RETIRED"))

    assert exc.value.code == "INVALID_LIFECYCLE_TRANSITION"
    fresh = service.get_model(model.id)
    assert fresh.lifecycle_state == "REGISTERED"
    assert fresh.record_version == 1


def test_archived_is_terminal(db, service):
    seed_reference_data(db)
    model = service.register_model(_payload(native_model_id="term-1"))
    service.archive_model(model.id)

    with pytest.raises(ModelArchivedError) as exc:
        service.update_model(model.id, ModelUpdate(lifecycle_state="ACTIVE"))

    assert exc.value.code == "MODEL_ARCHIVED"
    assert service.get_model(model.id).lifecycle_state == "ARCHIVED"


def test_list_excludes_archived_by_default(db, service):
    seed_reference_data(db)
    kept = service.register_model(_payload(native_model_id="list-1", name="Kept"))
    gone = service.register_model(_payload(native_model_id="list-2", name="Gone"))
    service.archive_model(gone.id)

    items, total = service.list_models(ModelFilters())

    assert total == 1
    assert [m.id for m in items] == [kept.id]


def test_list_include_archived(db, service):
    seed_reference_data(db)
    active = service.register_model(_payload(native_model_id="inc-1", name="Active"))
    archived = service.register_model(_payload(native_model_id="inc-2", name="Archived"))
    service.archive_model(archived.id)

    items, total = service.list_models(ModelFilters(), include_archived=True)

    assert total == 2
    assert {m.id for m in items} == {active.id, archived.id}


def test_list_filters_and_search(db, service):
    seed_reference_data(db)
    support = service.register_model(
        _payload(
            name="Customer Support LLM",
            native_model_id="support-1",
            owner_name="Alice",
            team_name="Support",
            lifecycle_state="ACTIVE",
            tags=["team=support"],
        )
    )
    chat = service.register_model(
        _payload(
            provider_slug="anthropic",
            name="Claude Chat",
            native_model_id="chat-1",
            owner_name="Bob",
            team_name="Core",
            source_type="SDK",
            tags=["team=core"],
        )
    )

    items, _ = service.list_models(ModelFilters(provider="openai"))
    assert [m.id for m in items] == [support.id]

    items, _ = service.list_models(ModelFilters(model_type="llm"))
    assert {m.id for m in items} == {support.id, chat.id}

    items, _ = service.list_models(ModelFilters(lifecycle_state="ACTIVE"))
    assert [m.id for m in items] == [support.id]

    items, _ = service.list_models(ModelFilters(owner="Alice"))
    assert [m.id for m in items] == [support.id]

    items, _ = service.list_models(ModelFilters(team="Core"))
    assert [m.id for m in items] == [chat.id]

    items, _ = service.list_models(ModelFilters(source_type="SDK"))
    assert [m.id for m in items] == [chat.id]

    items, _ = service.list_models(ModelFilters(tag="team=support"))
    assert [m.id for m in items] == [support.id]

    items, _ = service.list_models(ModelFilters(tag="team"))
    assert {m.id for m in items} == {support.id, chat.id}

    items, _ = service.list_models(ModelFilters(search="support"))
    assert [m.id for m in items] == [support.id]

    items, _ = service.list_models(ModelFilters(search="anthropic"))
    assert [m.id for m in items] == [chat.id]


def test_list_sort_allowlist(db, service):
    seed_reference_data(db)
    service.register_model(_payload(provider_slug="openai", name="B Model", native_model_id="sort-1"))
    service.register_model(_payload(provider_slug="anthropic", name="A Model", native_model_id="sort-2"))
    service.register_model(_payload(provider_slug="google", name="C Model", native_model_id="sort-3"))

    items, _ = service.list_models(ModelFilters(), sort_by="provider", sort_order="asc")
    assert [m.name for m in items] == ["A Model", "C Model", "B Model"]

    items, _ = service.list_models(ModelFilters(), sort_by="provider", sort_order="desc")
    assert [m.name for m in items] == ["B Model", "C Model", "A Model"]

    with pytest.raises(InvalidSortFieldError) as exc:
        service.list_models(ModelFilters(), sort_by="name; drop")

    assert exc.value.code == "INVALID_SORT_FIELD"
    assert exc.value.http_status == 400


def test_pagination_total(db, service):
    seed_reference_data(db)
    for i in range(30):
        service.register_model(_payload(name=f"Bulk {i}", native_model_id=f"bulk-{i}"))

    page1, total = service.list_models(ModelFilters(), page=1, page_size=25)
    page2, total2 = service.list_models(ModelFilters(), page=2, page_size=25)

    assert len(page1) == 25
    assert len(page2) == 5
    assert total == 30
    assert total2 == 30
    assert math.ceil(total / 25) == 2
    assert not {m.id for m in page1} & {m.id for m in page2}


def test_update_changes_fields_and_bumps_version(db, service):
    seed_reference_data(db)
    model = service.register_model(
        _payload(name="Original", description="old", metadata={"env": "dev"}, tags=["env=dev"])
    )
    events: list[ModelEvent] = []
    register_handler(events.append)

    updated = service.update_model(
        model.id,
        ModelUpdate(
            name="Renamed",
            description="new",
            owner_name="Alice",
            metadata={"env": "prod"},
            tags=["env=prod", "tier=1"],
        ),
    )

    assert updated.name == "Renamed"
    assert updated.owner_name == "Alice"
    assert updated.metadata_ == {"env": "prod"}
    assert updated.record_version == 2
    assert updated.canonical_key == "openai|test-model-1"
    assert updated.native_model_id == "test-model-1"
    assert _tags_of(db, model.id) == {("env", "prod"), ("tier", "1")}

    assert len(events) == 1
    assert events[0].event_type == "model.updated"
    assert events[0].model_id == model.id
    assert events[0].change_summary == ["description", "metadata", "name", "owner_name", "tags"]

    with pytest.raises(ModelTypeNotFoundError):
        service.update_model(model.id, ModelUpdate(model_type_slug="does-not-exist"))

    retyped = service.update_model(model.id, ModelUpdate(model_type_slug="embedding"))
    embedding_id = db.scalar(select(ModelType.id).where(ModelType.slug == "embedding"))
    assert retyped.model_type_id == embedding_id
    assert retyped.record_version == 3
    assert events[-1].change_summary == ["model_type_slug"]


def test_update_identity_immutable(db, service):
    seed_reference_data(db)
    model = service.register_model(_payload(name="Stable", native_model_id="stable-1"))

    with pytest.raises(IdentityImmutableError) as exc:
        service.update_model(model.id, ModelUpdate(name="Nope", native_model_id="changed"))

    assert exc.value.code == "MODEL_IDENTITY_CONFLICT"
    assert exc.value.http_status == 409

    with pytest.raises(IdentityImmutableError):
        service.update_model(model.id, ModelUpdate(provider_slug="anthropic"))

    fresh = service.get_model(model.id)
    assert fresh.name == "Stable"
    assert fresh.native_model_id == "stable-1"
    assert fresh.record_version == 1

    allowed = service.update_model(model.id, ModelUpdate(provider_slug="openai"))
    assert allowed.provider_id == model.provider_id
    assert allowed.native_model_id == "stable-1"


def test_update_archived_rejected(db, service):
    seed_reference_data(db)
    model = service.register_model(_payload(native_model_id="ua-1"))
    service.archive_model(model.id)

    with pytest.raises(ModelArchivedError) as exc:
        service.update_model(model.id, ModelUpdate(name="Renamed"))

    assert exc.value.code == "MODEL_ARCHIVED"
    assert service.get_model(model.id).name == "Test Model"


def test_tenant_scoping(db, service):
    seed_reference_data(db)
    mine = service.register_model(_payload(native_model_id="mine-1"))
    provider = db.scalar(select(ModelProvider).where(ModelProvider.slug == "openai"))
    model_type = db.scalar(select(ModelType).where(ModelType.slug == "llm"))
    other = Model(
        tenant_id=uuid4(),
        provider_id=provider.id,
        model_type_id=model_type.id,
        name="Other Tenant Model",
        native_model_id="theirs-1",
        canonical_key="openai|theirs-1",
        source_type="MANUAL",
    )
    db.add(other)
    db.flush()
    other_id = other.id

    items, total = service.list_models(ModelFilters(), include_archived=True)
    assert total == 1
    assert [m.id for m in items] == [mine.id]

    with pytest.raises(ModelNotFoundError):
        service.get_model(other_id)
    with pytest.raises(ModelNotFoundError):
        service.update_model(other_id, ModelUpdate(name="Hijack"))
    with pytest.raises(ModelNotFoundError):
        service.archive_model(other_id)


def test_archive_sets_state_and_timestamp(db, service):
    seed_reference_data(db)
    model = service.register_model(_payload(native_model_id="arch-ts-1"))
    events: list[ModelEvent] = []
    register_handler(events.append)

    service.archive_model(model.id)

    archived = service.get_model(model.id)
    assert archived.lifecycle_state == "ARCHIVED"
    assert archived.archived_at is not None
    assert archived.archived_at.tzinfo is not None

    items, total = service.list_models(ModelFilters())
    assert items == []
    assert total == 0

    assert service.archive_model(model.id) is None

    assert len(events) == 1
    assert events[0].event_type == "model.archived"
    assert events[0].model_id == model.id
    assert events[0].change_summary == ["archived"]
