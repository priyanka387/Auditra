from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.modules.model_inventory.domain import ModelVersionEvent, register_handler, reset_handlers
from app.modules.model_inventory.domain.errors import (
    DuplicateModelVersionError,
    InvalidSortFieldError,
    InvalidVersionTransitionError,
    ModelArchivedError,
    ModelNotFoundError,
    ModelVersionArchivedError,
    ModelVersionNotFoundError,
    VersionIdentityImmutableError,
)
from app.modules.model_inventory.models import Model, ModelVersion
from app.modules.model_inventory.repositories import ModelVersionFilters
from app.modules.model_inventory.schemas import (
    ModelVersionCreate,
    ModelVersionLifecycleUpdate,
    ModelVersionUpdate,
)
from app.modules.model_inventory.services import ModelVersionService


@pytest.fixture(autouse=True)
def _reset_event_handlers():
    reset_handlers()
    yield
    reset_handlers()


def _payload(**overrides):
    base = {
        "identity_type": "release",
        "version_label": "v2.1.0",
        "native_version_id": "release-210",
    }
    base.update(overrides)
    return ModelVersionCreate(**base)


def _svc(db, **overrides):
    return ModelVersionService(db, tenant_id=settings.default_tenant_id, **overrides)


def test_create_version_dispatches_event(db, parent_model):
    events: list[ModelVersionEvent] = []
    register_handler(events.append)
    service = _svc(db, request_id="req-1", actor="alice")

    version = service.create_version(parent_model.id, _payload())

    assert version.lifecycle_state == "DRAFT"
    assert version.canonical_version_key == "release:release-210"
    assert version.record_version == 1

    assert len(events) == 1
    event = events[0]
    assert event.event_type == "model_version.created"
    assert event.model_version_id == version.id
    assert event.actor == "alice"
    assert event.request_id == "req-1"


def test_duplicate_canonical_key_rejected(db, parent_model):
    service = _svc(db)
    service.create_version(parent_model.id, _payload(native_version_id="dup-1"))

    with pytest.raises(DuplicateModelVersionError) as exc:
        service.create_version(
            parent_model.id, _payload(version_label="January Release", native_version_id="dup-1")
        )

    assert exc.value.code == "MODEL_VERSION_ALREADY_EXISTS"
    assert exc.value.http_status == 409
    assert db.scalar(select(func.count()).select_from(ModelVersion)) == 1


def test_duplicate_race_integrity_error_mapped(db, parent_model, monkeypatch):
    service = _svc(db)
    service.create_version(parent_model.id, _payload(native_version_id="race-1"))

    monkeypatch.setattr(
        "app.modules.model_inventory.services.model_version_service.find_by_canonical_key",
        lambda *_args, **_kwargs: None,
    )
    with pytest.raises(DuplicateModelVersionError) as exc:
        service.create_version(parent_model.id, _payload(native_version_id="race-1"))

    assert exc.value.code == "MODEL_VERSION_ALREADY_EXISTS"
    assert db.scalar(select(func.count()).select_from(ModelVersion)) == 1


def test_create_missing_model_rejected(db):
    service = _svc(db)
    with pytest.raises(ModelNotFoundError) as exc:
        service.create_version(uuid4(), _payload())
    assert exc.value.code == "MODEL_NOT_FOUND"


def test_create_under_archived_model_rejected(db, parent_model):
    parent_model.lifecycle_state = "ARCHIVED"
    parent_model.archived_at = datetime.now(UTC)
    db.commit()
    service = _svc(db)

    with pytest.raises(ModelArchivedError) as exc:
        service.create_version(parent_model.id, _payload())

    assert exc.value.code == "MODEL_ARCHIVED"


def test_create_and_read_scoped_by_tenant_and_parent(db, parent_model):
    foreign_id = uuid4()
    db.add(
        ModelVersion(
            id=foreign_id,
            tenant_id=uuid4(),
            model_id=parent_model.id,
            identity_type="native",
            version_label="foreign",
            native_version_id="foreign-1",
            canonical_version_key="native:foreign-1",
            lifecycle_state="DRAFT",
            source_type="manual",
        )
    )
    other_model = Model(
        tenant_id=settings.default_tenant_id,
        provider_id=parent_model.provider_id,
        model_type_id=parent_model.model_type_id,
        name="Other Parent",
        native_model_id="other-parent",
        canonical_key="openai|other-parent",
        source_type="MANUAL",
    )
    db.add(other_model)
    db.flush()
    other_version_id = uuid4()
    db.add(
        ModelVersion(
            id=other_version_id,
            tenant_id=settings.default_tenant_id,
            model_id=other_model.id,
            identity_type="label",
            version_label="other-v1",
            canonical_version_key="label:other-v1",
            lifecycle_state="DRAFT",
            source_type="manual",
        )
    )
    db.commit()
    service = _svc(db)

    with pytest.raises(ModelVersionNotFoundError):
        service.get_version(parent_model.id, foreign_id)
    with pytest.raises(ModelVersionNotFoundError):
        service.get_version(parent_model.id, other_version_id)


def test_update_metadata_and_bumps_record_version(db, parent_model):
    service = _svc(db)
    version = service.create_version(parent_model.id, _payload())
    events: list[ModelVersionEvent] = []
    register_handler(events.append)

    updated = service.update_version(
        parent_model.id,
        version.id,
        ModelVersionUpdate(
            display_name="Friendly",
            description="desc",
            metadata={"env": "prod"},
            source_reference="s3://bucket/model",
        ),
    )

    assert updated.display_name == "Friendly"
    assert updated.description == "desc"
    assert updated.metadata_ == {"env": "prod"}
    assert updated.source_reference == "s3://bucket/model"
    assert updated.record_version == 2
    assert updated.identity_type == "release"
    assert updated.version_label == "v2.1.0"
    assert updated.native_version_id == "release-210"
    assert updated.canonical_version_key == "release:release-210"

    assert len(events) == 1
    assert events[0].event_type == "model_version.updated"
    assert events[0].change_summary == [
        "description",
        "display_name",
        "metadata",
        "source_reference",
    ]


def test_update_identity_fields_rejected(db, parent_model):
    service = _svc(db)
    version = service.create_version(parent_model.id, _payload())

    attempts = (
        {"model_id": uuid4()},
        {"identity_type": "native"},
        {"native_version_id": "changed-1"},
        {"canonical_version_key": "release:changed-1"},
        {"version_label": "v9"},
    )
    for attempt in attempts:
        with pytest.raises(VersionIdentityImmutableError) as exc:
            service.update_version(parent_model.id, version.id, ModelVersionUpdate(**attempt))
        assert exc.value.code == "MODEL_VERSION_IDENTITY_IMMUTABLE"
        assert exc.value.http_status == 409

    fresh = service.get_version(parent_model.id, version.id)
    assert fresh.record_version == 1

    allowed = service.update_version(
        parent_model.id,
        version.id,
        ModelVersionUpdate(
            model_id=parent_model.id,
            identity_type="release",
            version_label="v2.1.0",
            native_version_id="release-210",
            canonical_version_key="release:release-210",
        ),
    )
    assert allowed.record_version == 1


def test_update_archived_rejected(db, parent_model):
    service = _svc(db)
    version = service.create_version(parent_model.id, _payload())
    service.archive_version(parent_model.id, version.id)

    with pytest.raises(ModelVersionArchivedError) as exc:
        service.update_version(parent_model.id, version.id, ModelVersionUpdate(description="nope"))

    assert exc.value.code == "MODEL_VERSION_ALREADY_ARCHIVED"


def test_lifecycle_transitions_allowed(db, parent_model):
    service = _svc(db)
    version = service.create_version(parent_model.id, _payload())
    events: list[ModelVersionEvent] = []
    register_handler(events.append)

    service.transition_version(
        parent_model.id, version.id, ModelVersionLifecycleUpdate(target_state="ACTIVE")
    )
    service.transition_version(
        parent_model.id, version.id, ModelVersionLifecycleUpdate(target_state="DEPRECATED")
    )
    service.transition_version(
        parent_model.id, version.id, ModelVersionLifecycleUpdate(target_state="ACTIVE")
    )
    service.transition_version(
        parent_model.id, version.id, ModelVersionLifecycleUpdate(target_state="RETIRED")
    )

    assert service.get_version(parent_model.id, version.id).lifecycle_state == "RETIRED"
    assert len(events) == 4
    assert events[0].event_type == "model_version.lifecycle_changed"
    assert events[0].change_summary == ["DRAFT->ACTIVE"]


def test_lifecycle_invalid_and_same_state_rejected(db, parent_model):
    service = _svc(db)
    version = service.create_version(parent_model.id, _payload())
    service.transition_version(
        parent_model.id, version.id, ModelVersionLifecycleUpdate(target_state="ACTIVE")
    )
    service.transition_version(
        parent_model.id, version.id, ModelVersionLifecycleUpdate(target_state="RETIRED")
    )

    with pytest.raises(InvalidVersionTransitionError) as exc:
        service.transition_version(
            parent_model.id, version.id, ModelVersionLifecycleUpdate(target_state="ACTIVE")
        )
    assert exc.value.code == "INVALID_MODEL_VERSION_LIFECYCLE_TRANSITION"
    assert exc.value.http_status == 409

    with pytest.raises(InvalidVersionTransitionError):
        service.transition_version(
            parent_model.id, version.id, ModelVersionLifecycleUpdate(target_state="RETIRED")
        )

    archived = service.create_version(parent_model.id, _payload(native_version_id="arch-1"))
    service.archive_version(parent_model.id, archived.id)
    with pytest.raises(ModelVersionArchivedError):
        service.transition_version(
            parent_model.id, archived.id, ModelVersionLifecycleUpdate(target_state="ACTIVE")
        )


def test_lifecycle_to_archived_sets_archived_at(db, parent_model):
    service = _svc(db)
    version = service.create_version(parent_model.id, _payload())
    service.transition_version(
        parent_model.id, version.id, ModelVersionLifecycleUpdate(target_state="ACTIVE")
    )

    service.transition_version(
        parent_model.id, version.id, ModelVersionLifecycleUpdate(target_state="ARCHIVED")
    )

    got = service.get_version(parent_model.id, version.id)
    assert got.lifecycle_state == "ARCHIVED"
    assert got.archived_at is not None
    assert got.archived_at.tzinfo is not None


def test_archive_sets_state_and_timestamp(db, parent_model):
    service = _svc(db)
    version = service.create_version(parent_model.id, _payload())
    events: list[ModelVersionEvent] = []
    register_handler(events.append)

    service.archive_version(parent_model.id, version.id)

    got = service.get_version(parent_model.id, version.id)
    assert got.lifecycle_state == "ARCHIVED"
    assert got.archived_at is not None
    assert got.archived_at.tzinfo is not None

    items, total = service.list_versions(parent_model.id, ModelVersionFilters())
    assert items == [] and total == 0
    items, total = service.list_versions(
        parent_model.id, ModelVersionFilters(), include_archived=True
    )
    assert total == 1 and items[0].id == version.id

    assert service.archive_version(parent_model.id, version.id) is None

    assert len(events) == 1
    assert events[0].event_type == "model_version.archived"
    assert events[0].model_version_id == version.id
    assert events[0].change_summary == ["archived"]


def test_list_filters_search_sort_pagination(db, parent_model):
    service = _svc(db)
    va = service.create_version(
        parent_model.id,
        _payload(identity_type="native", version_label="v1-alpha", native_version_id="n1"),
    )
    vb = service.create_version(
        parent_model.id,
        _payload(
            identity_type="label",
            version_label="v2-beta",
            native_version_id=None,
            display_name="Friendly Bot",
            source_type="import",
        ),
    )
    vc = service.create_version(
        parent_model.id,
        _payload(identity_type="native", version_label="v3-gamma", native_version_id="n3"),
    )
    vd = service.create_version(
        parent_model.id,
        _payload(identity_type="release", version_label="v4-delta", native_version_id="n4"),
    )
    service.transition_version(
        parent_model.id, vb.id, ModelVersionLifecycleUpdate(target_state="ACTIVE")
    )
    service.transition_version(
        parent_model.id, vc.id, ModelVersionLifecycleUpdate(target_state="DEPRECATED")
    )

    items, total = service.list_versions(
        parent_model.id, ModelVersionFilters(lifecycle_state="ACTIVE")
    )
    assert total == 1 and [i.id for i in items] == [vb.id]

    items, total = service.list_versions(
        parent_model.id, ModelVersionFilters(identity_type="native")
    )
    assert total == 2 and {i.id for i in items} == {va.id, vc.id}

    items, total = service.list_versions(parent_model.id, ModelVersionFilters(source_type="import"))
    assert total == 1 and [i.id for i in items] == [vb.id]

    items, total = service.list_versions(parent_model.id, ModelVersionFilters(search="friendly"))
    assert total == 1 and [i.id for i in items] == [vb.id]
    items, total = service.list_versions(parent_model.id, ModelVersionFilters(search="zzz"))
    assert total == 0 and items == []

    items, _ = service.list_versions(
        parent_model.id, ModelVersionFilters(), sort_by="version_label", sort_order="asc"
    )
    assert [i.version_label for i in items] == ["v1-alpha", "v2-beta", "v3-gamma", "v4-delta"]
    items, _ = service.list_versions(
        parent_model.id, ModelVersionFilters(), sort_by="version_label", sort_order="desc"
    )
    assert [i.version_label for i in items] == ["v4-delta", "v3-gamma", "v2-beta", "v1-alpha"]

    page1, total = service.list_versions(parent_model.id, ModelVersionFilters(), page_size=2)
    page2, total2 = service.list_versions(
        parent_model.id, ModelVersionFilters(), page=2, page_size=2
    )
    assert (len(page1), len(page2), total, total2) == (2, 2, 4, 4)
    assert {i.id for i in page1} & {i.id for i in page2} == set()
    assert {i.id for i in page1} | {i.id for i in page2} == {va.id, vb.id, vc.id, vd.id}

    with pytest.raises(InvalidSortFieldError) as exc:
        service.list_versions(parent_model.id, ModelVersionFilters(), sort_by="name; drop")
    assert exc.value.code == "INVALID_SORT_FIELD"
    assert exc.value.http_status == 400


def test_multiple_active_versions_allowed(db, parent_model):
    service = _svc(db)
    first = service.create_version(parent_model.id, _payload(native_version_id="multi-1"))
    second = service.create_version(parent_model.id, _payload(native_version_id="multi-2"))

    for version in (first, second):
        service.transition_version(
            parent_model.id, version.id, ModelVersionLifecycleUpdate(target_state="ACTIVE")
        )

    items, total = service.list_versions(
        parent_model.id, ModelVersionFilters(lifecycle_state="ACTIVE")
    )
    assert total == 2
    assert {i.id for i in items} == {first.id, second.id}
