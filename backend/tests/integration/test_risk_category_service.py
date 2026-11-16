"""Integration tests: risk taxonomy (RiskCategory) service and audit."""

from uuid import uuid4

import pytest
import sqlalchemy as sa

from app.core.config import settings
from app.modules.audit.models import AuditEvent
from app.modules.risk.errors import (
    DuplicateRiskCategoryError,
    InvalidRiskCategoryQueryError,
    RiskCategoryNotFoundError,
)
from app.modules.risk.schemas import (
    RiskCategoryCreate,
    RiskCategoryFilter,
    RiskCategoryUpdate,
)
from app.modules.risk.service import RiskCategoryService


def _service(db, tenant_id=None) -> RiskCategoryService:
    return RiskCategoryService(
        db,
        tenant_id=tenant_id or settings.default_tenant_id,
        request_id="req-risk-01",
        actor="user-7",
    )


def _payload(**overrides) -> RiskCategoryCreate:
    payload = {"name": "Fairness / Bias"}
    payload.update(overrides)
    return RiskCategoryCreate(**payload)


def _events(db, resource_id) -> list[AuditEvent]:
    stmt = (
        sa.select(AuditEvent)
        .where(
            AuditEvent.resource_type == "risk_category",
            AuditEvent.resource_id == str(resource_id),
        )
        .order_by(AuditEvent.sequence_no)
    )
    return list(db.scalars(stmt))


def test_create_category_persists_and_generates_slug(db):
    created = _service(db).create(_payload())
    created_id = created.id
    db.expunge_all()

    fetched = _service(db).get(created_id)
    assert fetched.name == "Fairness / Bias"
    assert fetched.slug == "fairness-bias"
    assert fetched.is_system is False
    assert fetched.is_active is True
    assert fetched.tenant_id == settings.default_tenant_id
    assert fetched.created_at is not None


def test_create_duplicate_slug_rejected(db):
    _service(db).create(_payload())
    with pytest.raises(DuplicateRiskCategoryError):
        _service(db).create(_payload(name="Fairness Bias"))  # slugifies to the same slug


def test_create_explicit_duplicate_slug_rejected(db):
    _service(db).create(_payload(slug="vendor-risk"))
    with pytest.raises(DuplicateRiskCategoryError):
        _service(db).create(_payload(name="Vendor Risk", slug="vendor-risk"))


def test_update_category_changes_name_and_is_active(db):
    service = _service(db)
    created = service.create(_payload())

    updated = service.update(
        created.id,
        RiskCategoryUpdate(name="Bias & Fairness", is_active=False),
    )
    updated_id = updated.id
    db.expunge_all()

    fetched = _service(db).get(updated_id)
    assert fetched.name == "Bias & Fairness"
    assert fetched.is_active is False
    # slug is immutable once created (RF-01 §26)
    assert fetched.slug == "fairness-bias"

    events = [e for e in _events(db, updated_id) if e.event_type == "risk_category.updated"]
    assert len(events) == 1
    assert sorted(events[0].changed_fields or []) == ["is_active", "name"]
    assert events[0].before_state["name"] == "Fairness / Bias"
    assert events[0].after_state["name"] == "Bias & Fairness"


def test_get_unknown_category_returns_404(db):
    with pytest.raises(RiskCategoryNotFoundError):
        _service(db).get(uuid4())


def test_get_category_is_tenant_scoped(db):
    created = _service(db).create(_payload())
    with pytest.raises(RiskCategoryNotFoundError):
        _service(db, tenant_id=uuid4()).get(created.id)


def test_list_categories_search_and_pagination(db):
    service = _service(db)
    for name in ("Model Risk", "Financial", "Third-Party / Vendor"):
        service.create(_payload(name=name))

    items, total = service.list(RiskCategoryFilter(search="risk"))
    assert total == 1
    assert [item.name for item in items] == ["Model Risk"]

    page1, total = service.list(RiskCategoryFilter(page=1, page_size=2))
    page2, _ = service.list(RiskCategoryFilter(page=2, page_size=2))
    assert total == 3
    assert len(page1) == 2
    assert len(page2) == 1


def test_list_categories_is_tenant_scoped(db):
    _service(db).create(_payload(name="Mine Only"))
    items, total = _service(db, tenant_id=uuid4()).list(RiskCategoryFilter())
    assert total == 0
    assert items == []


def test_list_categories_invalid_sort_returns_400(db):
    with pytest.raises(InvalidRiskCategoryQueryError):
        _service(db).list(RiskCategoryFilter(sort_by="nope"))
