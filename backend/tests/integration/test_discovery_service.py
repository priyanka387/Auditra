from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.core.config import settings
from app.modules.model_discovery import repository
from app.modules.model_discovery.enums import DiscoverySourceType, DiscoveryStatus
from app.modules.model_discovery.errors import DuplicateDiscoveryError, InvalidDiscoveryQueryError
from app.modules.model_discovery.schemas import DiscoveryCreate, DiscoveryFilter, DiscoveryResponse
from app.modules.model_discovery.service import DiscoveryService


def _fields(**overrides) -> dict:
    now = datetime.now(UTC)
    fields = {
        "tenant_id": settings.default_tenant_id,
        "source_type": "manual",
        "source_identifier": None,
        "external_identifier": None,
        "provider": "openai",
        "model_identifier": "gpt-5.x",
        "model_type": "llm",
        "display_name": "OpenAI GPT model",
        "canonical_identity": "openai|gpt-5.x",
        "metadata_": {"environment": "development"},
        "status": "UNRESOLVED",
        "matched_model_id": None,
        "first_seen_at": now,
        "last_seen_at": now,
        "observation_count": 1,
    }
    fields.update(overrides)
    return fields


def test_repository_roundtrip_and_filters(db, parent_model):
    parent_id = parent_model.id
    manual = repository.create_discovery(db, **_fields())
    linked = repository.create_discovery(
        db,
        **_fields(
            source_type="langchain",
            source_identifier="support-service",
            provider="anthropic",
            model_identifier="claude-x",
            canonical_identity="anthropic|claude-x",
            display_name="Claude X",
            status="MATCHED",
            matched_model_id=parent_id,
            metadata_={"framework": "langchain"},
        ),
    )
    repository.commit(db)
    manual_id, linked_id = manual.id, linked.id
    db.expunge_all()

    fetched = repository.get_discovery(db, settings.default_tenant_id, manual_id)
    assert fetched is not None and fetched.canonical_identity == "openai|gpt-5.x"
    assert repository.get_discovery(db, uuid4(), manual_id) is None

    hit = repository.find_for_ingest(db, settings.default_tenant_id, "manual", None, "openai|gpt-5.x")
    assert hit is not None and hit.id == manual_id
    assert (
        repository.find_for_ingest(
            db, settings.default_tenant_id, "manual", "platform-team", "openai|gpt-5.x"
        )
        is None
    )
    linked_hit = repository.find_for_ingest(
        db, settings.default_tenant_id, "langchain", "support-service", "anthropic|claude-x"
    )
    assert linked_hit is not None and linked_hit.id == linked_id

    items, total = repository.list_discoveries(db, settings.default_tenant_id, DiscoveryFilter())
    assert total == 2 and len(items) == 2

    items, total = repository.list_discoveries(
        db, settings.default_tenant_id, DiscoveryFilter(status="MATCHED")
    )
    assert total == 1 and items[0].id == linked_id

    items, total = repository.list_discoveries(
        db, settings.default_tenant_id, DiscoveryFilter(provider="anthropic")
    )
    assert total == 1 and items[0].id == linked_id

    items, total = repository.list_discoveries(
        db, settings.default_tenant_id, DiscoveryFilter(search="claude")
    )
    assert total == 1 and items[0].id == linked_id

    items, total = repository.list_discoveries(
        db, settings.default_tenant_id, DiscoveryFilter(search="no-such-model")
    )
    assert total == 0 and items == []

    items, total = repository.list_discoveries(
        db, settings.default_tenant_id, DiscoveryFilter(matched_model_id=parent_id)
    )
    assert total == 1 and items[0].id == linked_id

    items, total = repository.list_discoveries(
        db, settings.default_tenant_id, DiscoveryFilter(page=2, page_size=1)
    )
    assert total == 2 and len(items) == 1

    items, total = repository.list_discoveries(
        db, settings.default_tenant_id, DiscoveryFilter(sort_by="observation_count", sort_order="asc")
    )
    assert total == 2


def test_repository_raises_for_unknown_sort_field(db):
    filters = DiscoveryFilter.model_construct(
        sort_by="not_a_column", sort_order="desc", page=1, page_size=25
    )
    with pytest.raises(InvalidDiscoveryQueryError):
        repository.list_discoveries(db, settings.default_tenant_id, filters)


def test_commit_maps_integrity_error_to_duplicate(db):
    repository.create_discovery(db, **_fields())
    repository.commit(db)
    repository.create_discovery(db, **_fields())
    with pytest.raises(DuplicateDiscoveryError):
        repository.commit(db)
    db.rollback()



def _payload(**overrides):
    payload = {
        "source_type": "manual",
        "source_identifier": None,
        "provider": "openai",
        "model_identifier": "gpt-5.x",
        "model_type": "llm",
        "metadata": {"environment": "development"},
    }
    payload.update(overrides)
    return DiscoveryCreate.model_validate(payload)


def test_ingest_creates_unresolved_observation(db):
    svc = DiscoveryService(db, settings.default_tenant_id)
    response, created = svc.ingest(_payload())
    assert created is True
    assert response.status is DiscoveryStatus.UNRESOLVED
    assert response.matched_model_id is None
    assert response.observation_count == 1
    assert response.canonical_identity == "openai|gpt-5.x"
    assert response.source_type is DiscoverySourceType.MANUAL


def test_ingest_matches_existing_canonical_model(db, parent_model):
    from sqlalchemy import func, select

    from app.modules.model_inventory.models import Model

    svc = DiscoveryService(db, settings.default_tenant_id)
    before = db.scalar(select(func.count()).select_from(Model))
    response, created = svc.ingest(
        _payload(provider="OpenAI ", model_identifier="versioned-1")
    )
    assert created is True
    assert response.status is DiscoveryStatus.MATCHED
    assert response.matched_model_id == parent_model.id
    after = db.scalar(select(func.count()).select_from(Model))
    assert after == before


def test_repeated_observation_updates_count_and_preserves_first_seen(db):
    svc = DiscoveryService(db, settings.default_tenant_id)
    first, created_first = svc.ingest(_payload())
    second, created_second = svc.ingest(_payload())
    assert created_first is True and created_second is False
    assert second.observation_count == 2
    assert second.first_seen_at == first.first_seen_at
    assert second.last_seen_at >= first.last_seen_at


def test_manual_discovery_with_null_source_identifier_is_idempotent(db):
    svc = DiscoveryService(db, settings.default_tenant_id)
    _, created_first = svc.ingest(_payload(source_type="manual", source_identifier=None))
    _, created_second = svc.ingest(_payload(source_type="manual", source_identifier=None))
    assert (created_first, created_second) == (True, False)
    items, total = svc.list(DiscoveryFilter())
    assert total == 1 and items[0].observation_count == 2


def test_repeat_ingest_does_not_reset_ignored_status(db):
    svc = DiscoveryService(db, settings.default_tenant_id)
    response, _ = svc.ingest(_payload())
    row = repository.find_for_ingest(
        db, settings.default_tenant_id, "manual", None, response.canonical_identity
    )
    row.status = "IGNORED"
    db.commit()

    replayed, created = svc.ingest(_payload())
    assert created is False
    assert replayed.status is DiscoveryStatus.IGNORED
    assert replayed.observation_count == 2


def test_duplicate_insert_race_recovers_as_repeat(db, monkeypatch):
    now = datetime.now(UTC)
    repository.create_discovery(
        db,
        tenant_id=settings.default_tenant_id,
        source_type="manual",
        source_identifier=None,
        provider="openai",
        model_identifier="gpt-5.x",
        canonical_identity="openai|gpt-5.x",
        status="UNRESOLVED",
        metadata_={},
        first_seen_at=now,
        last_seen_at=now,
        observation_count=1,
    )
    repository.commit(db)

    original = repository.find_for_ingest
    calls = {"count": 0}

    def flaky(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return None
        return original(*args, **kwargs)

    monkeypatch.setattr(repository, "find_for_ingest", flaky)

    svc = DiscoveryService(db, settings.default_tenant_id)
    response, created = svc.ingest(_payload())
    assert created is False
    assert response.observation_count == 2
    assert calls["count"] == 2


def test_service_list_returns_responses_and_passes_unknown_sort(db):
    svc = DiscoveryService(db, settings.default_tenant_id)
    svc.ingest(_payload(provider="openai", model_identifier="gpt-5.x"))
    svc.ingest(_payload(provider="anthropic", model_identifier="claude-x"))
    items, total = svc.list(DiscoveryFilter(provider="anthropic"))
    assert total == 1
    assert isinstance(items[0], DiscoveryResponse)
    assert items[0].provider == "anthropic"
    with pytest.raises(InvalidDiscoveryQueryError):
        svc.list(DiscoveryFilter.model_construct(sort_by="bogus", page=1, page_size=25))

