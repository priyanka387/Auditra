import pytest
import sqlalchemy as sa

from app.core.config import settings
from app.modules.audit.models import AuditEvent
from app.modules.audit.service import AuditService
from app.modules.model_discovery.schemas import DiscoveryCreate
from app.modules.model_discovery.service import DiscoveryService
from app.modules.model_inventory.domain.errors import DuplicateModelError
from app.modules.model_inventory.models import Model
from app.modules.model_inventory.schemas import ModelCreate, ModelUpdate
from app.modules.model_inventory.services import ModelService
from app.seed import seed_reference_data

API = "/api/v1"


def _payload(**overrides):
    base = {
        "provider_slug": "openai",
        "model_type_slug": "llm",
        "name": "Support LLM",
        "native_model_id": "support-1",
        "owner_name": "team-a",
        "team_name": "Support",
        "lifecycle_state": "REGISTERED",
        "source_type": "MANUAL",
        "metadata": {"environment": "dev"},
        "tags": ["customer-facing"],
    }
    base.update(overrides)
    return ModelCreate(**base)


def _service(db, **kwargs):
    kwargs.setdefault("request_id", "req-model-01")
    kwargs.setdefault("actor", "user-1")
    return ModelService(db, settings.default_tenant_id, **kwargs)


def _model_events(db, model_id=None):
    stmt = (
        sa.select(AuditEvent)
        .where(
            AuditEvent.resource_type == "model",
            *([AuditEvent.resource_id == str(model_id)] if model_id else []),
        )
        .order_by(AuditEvent.sequence_no)
    )
    return list(db.scalars(stmt))


def test_create_model_emits_model_created(db):
    seed_reference_data(db)
    db.commit()
    svc = _service(db)
    model = svc.register_model(_payload())

    events = _model_events(db, model.id)
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "model.created"
    assert event.actor_type == "user"
    assert event.actor_id == "user-1"
    assert event.source == "api"
    assert event.request_id == "req-model-01"
    assert event.schema_version == 1
    assert event.before_state is None
    assert event.after_state["canonical_key"] == "openai|support-1"
    assert event.after_state["owner_name"] == "team-a"
    assert event.after_state["lifecycle_state"] == "REGISTERED"
    assert event.after_state["tags"] == [{"key": "customer-facing", "value": ""}]


def test_create_model_without_actor_attributes_system(db):
    seed_reference_data(db)
    db.commit()
    svc = ModelService(db, settings.default_tenant_id)
    model = svc.register_model(_payload(native_model_id="system-actor-1"))
    event = _model_events(db, model.id)[0]
    assert event.actor_type == "system"
    assert event.actor_id is None


def test_update_owner_and_tags_emits_model_updated(db):
    seed_reference_data(db)
    db.commit()
    svc = _service(db)
    model = svc.register_model(_payload())

    updated = svc.update_model(
        model.id,
        ModelUpdate(owner_name="team-b", tags=["customer-facing", "production"]),
    )

    events = _model_events(db, model.id)
    assert len(events) == 2
    event = events[-1]
    assert event.event_type == "model.updated"
    assert event.changed_fields == ["owner_name", "tags"]
    assert event.before_state["owner_name"] == "team-a"
    assert event.after_state["owner_name"] == "team-b"
    assert event.after_state["tags"] == [
        {"key": "customer-facing", "value": ""},
        {"key": "production", "value": ""},
    ]
    assert updated.owner_name == "team-b"


def test_noop_update_emits_no_audit_event(db):
    seed_reference_data(db)
    db.commit()
    svc = _service(db)
    model = svc.register_model(_payload())
    assert len(_model_events(db, model.id)) == 1

    svc.update_model(
        model.id,
        ModelUpdate(
            name="Support LLM",
            owner_name="team-a",
            team_name="Support",
            tags=["customer-facing"],
            metadata={"environment": "dev"},
        ),
    )

    assert len(_model_events(db, model.id)) == 1


def test_archive_emits_lifecycle_update(db):
    seed_reference_data(db)
    db.commit()
    svc = _service(db)
    model = svc.register_model(_payload())

    svc.archive_model(model.id)

    events = _model_events(db, model.id)
    assert len(events) == 2
    event = events[-1]
    assert event.event_type == "model.updated"
    assert event.changed_fields == ["lifecycle_state"]
    assert event.before_state["lifecycle_state"] == "REGISTERED"
    assert event.after_state["lifecycle_state"] == "ARCHIVED"

    svc.archive_model(model.id)
    assert len(_model_events(db, model.id)) == 2


def test_duplicate_create_emits_no_second_event(db):
    seed_reference_data(db)
    db.commit()
    svc = _service(db)
    model = svc.register_model(_payload())

    with pytest.raises(DuplicateModelError):
        svc.register_model(_payload())

    assert len(_model_events(db, model.id)) == 1


def test_audit_failure_rolls_back_model_creation(db, monkeypatch):
    seed_reference_data(db)
    db.commit()
    svc = _service(db)

    def boom(*args, **kwargs):
        raise RuntimeError("audit write failed")

    monkeypatch.setattr(AuditService, "record_event", boom)
    with pytest.raises(RuntimeError):
        svc.register_model(_payload(native_model_id="rollback-1"))
    db.rollback()

    model_count = db.scalar(
        sa.select(sa.func.count()).select_from(Model).where(Model.native_model_id == "rollback-1")
    )
    audit_count = db.scalar(sa.select(sa.func.count()).select_from(AuditEvent))
    assert model_count == 0
    assert audit_count == 0


def test_audit_failure_rolls_back_model_update(db, monkeypatch):
    seed_reference_data(db)
    db.commit()
    svc = _service(db)
    model = svc.register_model(_payload())
    assert len(_model_events(db, model.id)) == 1

    def boom(*args, **kwargs):
        raise RuntimeError("audit write failed")

    monkeypatch.setattr(AuditService, "record_event", boom)
    with pytest.raises(RuntimeError):
        svc.update_model(model.id, ModelUpdate(owner_name="team-should-not-persist"))
    db.rollback()
    db.refresh(model)

    assert model.owner_name == "team-a"
    assert len(_model_events(db, model.id)) == 1


def test_secret_like_metadata_value_redacted(db):
    seed_reference_data(db)
    db.commit()
    svc = _service(db)
    model = svc.register_model(
        _payload(metadata={"connection": "password=hunter2", "environment": "dev"})
    )
    event = _model_events(db, model.id)[0]
    assert "hunter2" not in str(event.after_state["metadata"]["connection"])
    assert event.after_state["metadata"]["connection"] == "password=REDACTED"
    assert event.after_state["metadata"]["environment"] == "dev"


def test_create_model_via_api_writes_audit_with_request_id(client, db):
    created = client.post(
        f"{API}/models",
        json={
            "provider_slug": "openai",
            "model_type_slug": "llm",
            "name": "API Audited Model",
            "native_model_id": "api-audit-1",
            "lifecycle_state": "REGISTERED",
            "source_type": "MANUAL",
        },
        headers={"X-Request-ID": "req-flow-01"},
    )
    assert created.status_code == 201

    body = client.get(f"{API}/audit/events", params={"request_id": "req-flow-01"}).json()
    assert body["total"] == 1
    event = body["items"][0]
    assert event["event_type"] == "model.created"
    assert event["resource_type"] == "model"
    assert event["resource_id"] == created.json()["id"]
    assert event["request_id"] == "req-flow-01"


def test_discovery_register_emits_model_registered(db):
    seed_reference_data(db)
    db.commit()
    svc = DiscoveryService(db, settings.default_tenant_id, request_id="req-disc-01", actor="user-7")
    observed, _ = svc.ingest(
        DiscoveryCreate.model_validate(
            {
                "source_type": "manual",
                "provider": "openai",
                "model_identifier": "gpt-discovered",
                "model_type": "llm",
                "display_name": "Discovered GPT",
            }
        )
    )
    response = svc.register(observed.id, None)

    events = _model_events(db, response.matched_model_id)
    assert [e.event_type for e in events] == ["model.created", "model.registered"]
    registered = events[-1]
    assert registered.resource_type == "model"
    assert registered.resource_id == str(response.matched_model_id)
    assert registered.request_id == "req-disc-01"
    assert registered.actor_type == "user"
    assert registered.actor_id == "user-7"
    assert registered.source == "api"
    assert registered.metadata_ == {
        "registration_mode": "discovered_then_registered",
        "discovery_id": str(observed.id),
        "discovery_source_type": "manual",
    }
    assert registered.after_state["canonical_key"] == "openai|gpt-discovered"
