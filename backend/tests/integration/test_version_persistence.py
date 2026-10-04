from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.modules.model_inventory.models import ModelVersion


def _mk_version(db, model, key="native:2025-04-14", **over):
    fields = {
        "tenant_id": settings.default_tenant_id,
        "model_id": model.id,
        "identity_type": "native",
        "version_label": "2025-04-14",
        "native_version_id": "2025-04-14",
        "canonical_version_key": key,
        "lifecycle_state": "DRAFT",
        "source_type": "manual",
    }
    fields.update(over)
    db.add(ModelVersion(**fields))
    db.flush()
    return db.scalar(sa.select(ModelVersion).where(ModelVersion.canonical_version_key == key))


def test_canonical_version_key_unique_constraint(db, parent_model):
    _mk_version(db, parent_model, key="native:dup")
    db.add(
        ModelVersion(
            tenant_id=settings.default_tenant_id,
            model_id=parent_model.id,
            identity_type="native",
            version_label="v2",
            canonical_version_key="native:dup",
            lifecycle_state="DRAFT",
            source_type="manual",
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_model_fk_enforced(db, parent_model):
    db.add(
        ModelVersion(
            tenant_id=settings.default_tenant_id,
            model_id=uuid4(),
            identity_type="label",
            version_label="v1",
            canonical_version_key="label:v1",
            lifecycle_state="DRAFT",
            source_type="manual",
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_tenant_model_key_uniqueness_is_scoped(db, parent_model):
    _mk_version(db, parent_model, key="native:scoped")
    db.add(
        ModelVersion(
            tenant_id=uuid4(),
            model_id=parent_model.id,
            identity_type="native",
            version_label="2025-04-14",
            canonical_version_key="native:scoped",
            lifecycle_state="DRAFT",
            source_type="manual",
        )
    )
    db.flush()


def test_timestamps_timezone_aware_and_metadata_jsonb(db, parent_model):
    version = _mk_version(db, parent_model, key="native:ts")
    db.commit()
    fresh = db.get(ModelVersion, version.id)
    assert fresh.created_at.tzinfo is not None and fresh.updated_at.tzinfo is not None
    assert fresh.metadata_ == {} and fresh.record_version == 1
    cols = {
        c["name"]: str(c["type"]) for c in sa.inspect(db.get_bind()).get_columns("model_versions")
    }
    assert cols["metadata"].lower().startswith("jsonb")


def test_record_version_increments_on_flush(db, parent_model):
    version = _mk_version(db, parent_model, key="native:rv")
    version.display_name = "Renamed"
    db.flush()
    assert version.record_version == 2


def test_required_indexes_exist(db, parent_model):
    idx = {i["name"] for i in sa.inspect(db.get_bind()).get_indexes("model_versions")}
    assert {
        "ix_model_versions_tenant_id_model_id_created_at",
        "ix_model_versions_tenant_id_model_id_lifecycle_state",
        "ix_model_versions_tenant_id_model_id_updated_at",
        "ix_model_versions_tenant_id_model_id_version_label",
        "ix_model_versions_tenant_id_model_id_native_version_id",
    } <= idx
    cons = {c["name"] for c in sa.inspect(db.get_bind()).get_unique_constraints("model_versions")}
    assert "uq_model_versions_tenant_model_canonical_key" in cons
