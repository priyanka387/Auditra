from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.modules.model_inventory.models import Model, ModelProvider, ModelTag, ModelType
from app.seed import seed_reference_data

TENANT = UUID("11111111-1111-1111-1111-111111111111")


def _mk_model(db, key="openai|gpt-test", tenant=TENANT):
    seed_reference_data(db)
    provider = db.scalar(select(ModelProvider).where(ModelProvider.slug == "openai"))
    model_type = db.scalar(select(ModelType).where(ModelType.slug == "llm"))
    model = Model(
        tenant_id=tenant,
        provider_id=provider.id,
        model_type_id=model_type.id,
        name="GPT Test",
        native_model_id="gpt-test",
        canonical_key=key,
        source_type="MANUAL",
    )
    db.add(model)
    db.flush()
    return model


def test_canonical_key_unique_constraint(db):
    _mk_model(db, key="openai|dup")
    with pytest.raises(IntegrityError):
        _mk_model(db, key="openai|dup")
        db.flush()


def test_provider_slug_unique(db):
    seed_reference_data(db)
    db.add(ModelProvider(slug="openai", name="Duplicate OpenAI"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_tag_unique_per_tenant(db):
    db.add(ModelTag(tenant_id=TENANT, key="env", value="prod"))
    db.flush()
    db.add(ModelTag(tenant_id=TENANT, key="env", value="prod"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_provider_fk_enforced(db):
    seed_reference_data(db)
    model_type = db.scalar(select(ModelType).where(ModelType.slug == "llm"))
    db.add(
        Model(
            tenant_id=TENANT,
            provider_id=uuid4(),
            model_type_id=model_type.id,
            name="Orphan",
            native_model_id="orphan",
            canonical_key="orphan|model",
            source_type="MANUAL",
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_seed_idempotent(db):
    seed_reference_data(db)
    seed_reference_data(db)
    assert db.scalar(select(func.count()).select_from(ModelProvider)) == 13
    assert db.scalar(select(func.count()).select_from(ModelType)) == 15
