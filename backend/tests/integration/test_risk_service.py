"""Integration tests: Risk service — business rules, tenant isolation, audit."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from app.core.config import settings
from app.modules.asset_discovery.models import DiscoveredAsset, DiscoverySource
from app.modules.audit.models import AuditEvent
from app.modules.risk.enums import RiskSource, RiskStatus
from app.modules.risk.errors import (
    InvalidRiskQueryError,
    RiskArchivedError,
    RiskAssetNotFoundError,
    RiskCategoryNotFoundError,
    RiskNotFoundError,
)
from app.modules.risk.models import RiskCategory
from app.modules.risk.schemas import RiskCreate, RiskFilter, RiskUpdate
from app.modules.risk.seed import seed_risk_categories
from app.modules.risk.service import RiskService

TENANT = settings.default_tenant_id
OTHER_TENANT = uuid4()


def _category_for(db, tenant_id: UUID, slug: str) -> RiskCategory:
    seed_risk_categories(db, tenant_id)
    db.commit()
    return db.scalar(
        sa.select(RiskCategory).where(
            RiskCategory.tenant_id == tenant_id,
            RiskCategory.slug == slug,
        )
    )


@pytest.fixture
def category(db) -> RiskCategory:
    return _category_for(db, TENANT, "security")


@pytest.fixture
def other_tenant_category(db) -> RiskCategory:
    return _category_for(db, OTHER_TENANT, "privacy")


def _make_asset(db, tenant_id: UUID, name: str) -> DiscoveredAsset:
    source = DiscoverySource(
        tenant_id=tenant_id,
        name=f"src-{uuid4().hex[:8]}",
        source_type="repository",
        config={"location": "/workspace/x"},
    )
    db.add(source)
    db.flush()
    asset = DiscoveredAsset(
        tenant_id=tenant_id,
        source_id=source.id,
        asset_type="MODEL",
        name=name,
        normalized_identity=f"MODEL|{name}|{uuid4().hex[:6]}",
        metadata_={},
        first_seen_at=datetime.now(UTC),
        last_seen_at=datetime.now(UTC),
    )
    db.add(asset)
    db.commit()
    return asset


@pytest.fixture
def asset(db) -> DiscoveredAsset:
    return _make_asset(db, TENANT, "payments-gpt-4o")


@pytest.fixture
def other_tenant_asset(db) -> DiscoveredAsset:
    return _make_asset(db, OTHER_TENANT, "other-tenant-model")


def _service(db, tenant_id=None) -> RiskService:
    return RiskService(
        db,
        tenant_id=tenant_id or TENANT,
        request_id="req-risk-01",
        actor="user-7",
    )


def _payload(**overrides) -> RiskCreate:
    payload = {
        "title": "Unreviewed model in payments",
        "description": "A customer-facing model shipped without review",
    }
    payload.update(overrides)
    return RiskCreate(**payload)


def _events(db, resource_id) -> list[AuditEvent]:
    stmt = (
        sa.select(AuditEvent)
        .where(
            AuditEvent.resource_type == "risk",
            AuditEvent.resource_id == str(resource_id),
        )
        .order_by(AuditEvent.sequence_no)
    )
    return list(db.scalars(stmt))


# --- create -----------------------------------------------------------------


def test_create_risk_persists_full_record(db, category, asset):
    category_id, asset_id = category.id, asset.id
    created = _service(db).create(
        _payload(
            category_id=category_id,
            asset_id=asset_id,
            owner="priya",
            team="risk-team",
            source=RiskSource.ASSET_DISCOVERY,
        )
    )
    created_id = created.id
    db.expunge_all()

    fetched = _service(db).get(created_id)
    assert fetched.title == "Unreviewed model in payments"
    assert fetched.category_id == category_id
    assert fetched.asset_id == asset_id
    assert fetched.owner == "priya"
    assert fetched.team == "risk-team"
    assert fetched.source == RiskSource.ASSET_DISCOVERY
    assert fetched.status == RiskStatus.OPEN
    assert fetched.created_by == "user-7"
    assert fetched.archived_at is None
    assert fetched.tenant_id == TENANT

    events = _events(db, created_id)
    assert len(events) == 1
    assert events[0].event_type == "risk.created"
    assert events[0].after_state["title"] == "Unreviewed model in payments"
    assert events[0].after_state["status"] == "open"
    assert events[0].before_state is None


def test_create_risk_trims_title_and_blanks(db, category):
    created = _service(db).create(
        _payload(title="  padded title  ", owner="   ", description="   ", category_id=category.id)
    )
    created_id = created.id
    db.expunge_all()

    fetched = _service(db).get(created_id)
    assert fetched.title == "padded title"
    assert fetched.owner is None
    assert fetched.description is None


def test_create_risk_requires_existing_category(db, asset):
    with pytest.raises(RiskCategoryNotFoundError):
        _service(db).create(_payload(category_id=uuid4(), asset_id=asset.id))


def test_create_risk_with_cross_tenant_category_returns_404(db, other_tenant_category, asset):
    with pytest.raises(RiskCategoryNotFoundError):
        _service(db).create(_payload(category_id=other_tenant_category.id, asset_id=asset.id))


def test_create_risk_with_unknown_asset_returns_404(db, category):
    with pytest.raises(RiskAssetNotFoundError):
        _service(db).create(_payload(category_id=category.id, asset_id=uuid4()))


def test_create_risk_with_cross_tenant_asset_returns_404(db, category, other_tenant_asset):
    with pytest.raises(RiskAssetNotFoundError):
        _service(db).create(_payload(category_id=category.id, asset_id=other_tenant_asset.id))


def test_create_risk_with_valid_asset_links_discovered_asset(db, category, asset):
    asset_id = asset.id
    created = _service(db).create(_payload(category_id=category.id, asset_id=asset_id))
    created_id = created.id
    db.expunge_all()

    fetched = _service(db).get(created_id)
    assert fetched.asset_id == asset_id
    # asset summary is built via resolve_refs (no per-row relationship)
    _, assets = _service(db).resolve_refs([fetched])
    assert assets[asset_id].name == "payments-gpt-4o"


# --- read -------------------------------------------------------------------


def test_get_unknown_risk_returns_404(db):
    with pytest.raises(RiskNotFoundError):
        _service(db).get(uuid4())


def test_get_risk_from_other_tenant_returns_404(db, category, asset):
    created = _service(db).create(_payload(category_id=category.id, asset_id=asset.id))
    with pytest.raises(RiskNotFoundError):
        _service(db, tenant_id=OTHER_TENANT).get(created.id)


# --- update -----------------------------------------------------------------


def test_update_risk_applies_fields_and_records_changed_fields(db, category):
    created = _service(db).create(_payload(category_id=category.id))
    updated = _service(db).update(
        created.id,
        RiskUpdate(status=RiskStatus.IN_REVIEW, owner="ram", title="Renamed risk"),
    )
    updated_id = updated.id
    db.expunge_all()

    fetched = _service(db).get(updated_id)
    assert fetched.status == RiskStatus.IN_REVIEW
    assert fetched.owner == "ram"
    assert fetched.title == "Renamed risk"
    assert fetched.updated_by == "user-7"

    events = [e for e in _events(db, updated_id) if e.event_type == "risk.updated"]
    assert len(events) == 1
    assert sorted(events[0].changed_fields or []) == ["owner", "status", "title"]


def test_update_risk_no_change_records_no_audit(db, category):
    created = _service(db).create(_payload(category_id=category.id))
    _service(db).update(created.id, RiskUpdate())
    updated_events = [e for e in _events(db, created.id) if e.event_type == "risk.updated"]
    assert updated_events == []


def test_update_risk_with_unknown_category_returns_404(db, category):
    created = _service(db).create(_payload(category_id=category.id))
    with pytest.raises(RiskCategoryNotFoundError):
        _service(db).update(created.id, RiskUpdate(category_id=uuid4()))


def test_update_risk_with_cross_tenant_asset_returns_404(db, category, other_tenant_asset):
    created = _service(db).create(_payload(category_id=category.id))
    with pytest.raises(RiskAssetNotFoundError):
        _service(db).update(created.id, RiskUpdate(asset_id=other_tenant_asset.id))


def test_update_unknown_risk_returns_404(db):
    with pytest.raises(RiskNotFoundError):
        _service(db).update(uuid4(), RiskUpdate(title="nope"))


def test_update_archived_risk_returns_409(db, category):
    created = _service(db).create(_payload(category_id=category.id))
    _service(db).archive(created.id)
    with pytest.raises(RiskArchivedError):
        _service(db).update(created.id, RiskUpdate(title="too late"))


# --- archive ----------------------------------------------------------------


def test_delete_risk_archives_and_preserves_relationships(db, category, asset):
    category_id, asset_id = category.id, asset.id
    created = _service(db).create(_payload(category_id=category_id, asset_id=asset_id))
    archived = _service(db).archive(created.id)
    archived_id = archived.id
    archived_at = archived.archived_at
    assert archived_at is not None

    db.expunge_all()
    fetched = _service(db).get(archived_id)
    assert fetched.archived_at is not None
    # soft archive only: the row and its FKs survive
    assert fetched.category_id == category_id
    assert fetched.asset_id == asset_id

    events = [e for e in _events(db, archived_id) if e.event_type == "risk.archived"]
    assert len(events) == 1
    assert events[0].before_state["archived_at"] is None
    assert events[0].after_state["archived_at"] is not None


def test_delete_archived_risk_is_idempotent(db, category):
    created = _service(db).create(_payload(category_id=category.id))
    first = _service(db).archive(created.id)
    first_at = first.archived_at

    second = _service(db).archive(created.id)
    assert second.archived_at == first_at
    archived_events = [e for e in _events(db, created.id) if e.event_type == "risk.archived"]
    assert len(archived_events) == 1  # no second audit event


# --- list -------------------------------------------------------------------


def _create(db, category, **overrides) -> UUID:
    return _service(db).create(_payload(category_id=category.id, **overrides)).id


def test_list_risks_filters_by_status_category_owner_asset(db, category, asset):
    other_category = _category_for(db, TENANT, "privacy")
    _create(db, category, title="Alpha", status=RiskStatus.OPEN, owner="priya")
    _create(
        db,
        other_category,
        title="Beta",
        status=RiskStatus.IN_REVIEW,
        owner="ram",
        asset_id=asset.id,
    )

    items, total = _service(db).list(RiskFilter(status=RiskStatus.IN_REVIEW))
    assert total == 1
    assert items[0].title == "Beta"

    items, total = _service(db).list(RiskFilter(category_id=other_category.id))
    assert total == 1
    assert items[0].title == "Beta"

    items, total = _service(db).list(RiskFilter(owner="priya"))
    assert total == 1
    assert items[0].title == "Alpha"

    items, total = _service(db).list(RiskFilter(asset_id=asset.id))
    assert total == 1
    assert items[0].title == "Beta"

    items, total = _service(db).list(RiskFilter(source=RiskSource.MANUAL))
    assert total == 2


def test_list_risks_search_covers_title_description_owner_and_asset_name(db, category, asset):
    _create(db, category, title="Encryption gap", owner="alice")
    _create(db, category, title="Other", description="customer PII leak", owner="bob")
    _create(
        db,
        category,
        title="Linked one",
        owner="carol",
        asset_id=asset.id,
    )

    for term, expected in [
        ("encryption", "Encryption gap"),
        ("PII leak", "Other"),
        ("alice", "Encryption gap"),
        ("payments-gpt-4o", "Linked one"),
    ]:
        items, total = _service(db).list(RiskFilter(search=term))
        assert total == 1, term
        assert items[0].title == expected, term


def test_list_risks_excludes_archived_by_default_and_includes_when_archived(db, category):
    active = _create(db, category, title="Still open")
    gone = _create(db, category, title="Archived one")
    _service(db).archive(gone)

    items, total = _service(db).list(RiskFilter())
    assert total == 1
    assert [item.id for item in items] == [active]

    items, total = _service(db).list(RiskFilter(archived=True))
    assert total == 1
    assert [item.id for item in items] == [gone]


def test_list_risks_pagination_and_total(db, category):
    for index in range(3):
        _create(db, category, title=f"Risk {index}")

    page1, total = _service(db).list(
        RiskFilter(page=1, page_size=2, sort_by="title", sort_order="asc")
    )
    page2, _ = _service(db).list(RiskFilter(page=2, page_size=2, sort_by="title", sort_order="asc"))
    assert total == 3
    assert [item.title for item in page1] == ["Risk 0", "Risk 1"]
    assert [item.title for item in page2] == ["Risk 2"]


def test_list_risks_is_tenant_scoped(db, category):
    _create(db, category, title="Secret")
    items, total = _service(db, tenant_id=OTHER_TENANT).list(RiskFilter())
    assert total == 0
    assert items == []


def test_list_risks_invalid_sort_returns_400(db):
    with pytest.raises(InvalidRiskQueryError):
        _service(db).list(RiskFilter(sort_by="nope"))
