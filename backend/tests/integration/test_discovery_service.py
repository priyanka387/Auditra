from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.core.config import settings
from app.modules.model_discovery import repository
from app.modules.model_discovery.errors import DuplicateDiscoveryError, InvalidDiscoveryQueryError
from app.modules.model_discovery.schemas import DiscoveryFilter


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

