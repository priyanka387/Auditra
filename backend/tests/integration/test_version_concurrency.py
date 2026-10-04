from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.core.db import SessionLocal
from app.modules.model_inventory.domain.errors import (
    DuplicateModelVersionError,
    VersionConcurrencyConflictError,
)
from app.modules.model_inventory.models import ModelVersion
from app.modules.model_inventory.schemas import ModelVersionCreate, ModelVersionUpdate
from app.modules.model_inventory.services import ModelVersionService


def _payload():
    return ModelVersionCreate(
        identity_type="native",
        version_label="2025-04-14",
        native_version_id="2025-04-14",
        source_type="manual",
    )


def test_concurrent_duplicate_create_one_wins(db, parent_model, monkeypatch):
    import app.modules.model_inventory.services.model_version_service as mvs

    barrier = Barrier(2, timeout=15)
    real_check = mvs.find_by_canonical_key
    monkeypatch.setattr(
        mvs, "find_by_canonical_key", lambda *a, **k: (barrier.wait(), real_check(*a, **k))[1]
    )
    results = []

    def worker():
        session = SessionLocal()
        try:
            ModelVersionService(session, tenant_id=settings.default_tenant_id).create_version(
                parent_model.id, _payload()
            )
            results.append("created")
        except DuplicateModelVersionError:
            results.append("conflict")
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: worker(), range(2)))

    assert sorted(results) == ["conflict", "created"]
    assert db.scalar(select(func.count()).select_from(ModelVersion)) == 1


def test_stale_metadata_update_conflict(db, parent_model):
    service = ModelVersionService(db, tenant_id=settings.default_tenant_id)
    version = service.create_version(parent_model.id, _payload())
    loaded = service.get_version(parent_model.id, version.id)
    assert loaded.record_version == 1

    other = SessionLocal()
    try:
        theirs = other.get(type(loaded), version.id)
        theirs.display_name = "Other Writer"
        other.commit()
    finally:
        other.close()

    with pytest.raises(VersionConcurrencyConflictError) as exc:
        service.update_version(parent_model.id, version.id, ModelVersionUpdate(description="mine"))
    assert exc.value.code == "VERSION_CONCURRENCY_CONFLICT"
    assert exc.value.http_status == 409
