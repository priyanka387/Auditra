"""End-to-end acceptance scenario: Asset Discovery → RF-01 Risk Register.

Walks the RF-01 §36 acceptance path: seed the taxonomy → discover a real
AI asset through an actual discovery run → register a risk against that
asset → search it → move it to review → archive it → verify the audit
trail and the archived-only views.
"""

from uuid import uuid4

import pytest
import sqlalchemy as sa

from app.core.config import settings
from app.modules.asset_discovery.enums import DiscoverySourceType
from app.modules.asset_discovery.execution import DiscoveryExecutionService
from app.modules.asset_discovery.models import DiscoveredAsset
from app.modules.asset_discovery.schemas import DiscoverySourceCreate
from app.modules.asset_discovery.service import (
    DiscoveryRunService,
    DiscoverySourceService,
)
from app.modules.audit.models import AuditEvent
from app.modules.risk.enums import RiskStatus
from app.modules.risk.schemas import RiskCreate, RiskFilter, RiskUpdate
from app.modules.risk.seed import RISK_CATEGORIES, seed_risk_categories
from app.modules.risk.service import RiskService

TENANT = settings.default_tenant_id

API = "/api/v1"


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "llm.py").write_text('import openai\nmodel = "gpt-4o"\n', encoding="utf-8")
    return tmp_path


def _risk_service(db) -> RiskService:
    return RiskService(db, TENANT, request_id="req-acceptance", actor="qa-lead")


def _risk_events(db) -> list[AuditEvent]:
    stmt = (
        sa.select(AuditEvent)
        .where(AuditEvent.resource_type == "risk")
        .order_by(AuditEvent.sequence_no)
    )
    return list(db.scalars(stmt))


def test_full_risk_register_journey(db, client, repo):
    # 1. the system taxonomy is provisioned per tenant (idempotent)
    fresh_tenant = uuid4()
    assert seed_risk_categories(db, fresh_tenant) == len(RISK_CATEGORIES)
    assert seed_risk_categories(db, fresh_tenant) == 0  # second run inserts nothing
    db.commit()
    categories = client.get(f"{API}/risk-categories").json()
    assert categories["total"] == len(RISK_CATEGORIES)

    # 2. Asset Discovery produces a real AI asset from a real run
    source = DiscoverySourceService(db, TENANT).create(
        DiscoverySourceCreate(
            name="acceptance-repo",
            source_type=DiscoverySourceType.REPOSITORY,
            config={"location": str(repo)},
        )
    )
    run = DiscoveryRunService(db, TENANT).create(source.id)
    result = DiscoveryExecutionService(db, TENANT).execute(run.id)
    assert result.observations, "discovery must observe at least one asset"

    asset = db.scalar(sa.select(DiscoveredAsset).where(DiscoveredAsset.tenant_id == TENANT))
    assert asset is not None
    security = next(
        item
        for item in client.get(f"{API}/risk-categories").json()["items"]
        if item["slug"] == "security"
    )

    # 3. register a risk against that discovered asset
    risk = _risk_service(db).create(
        RiskCreate(
            title="Undeclared model in production path",
            description="Discovery found gpt-4o without a registered owner",
            category_id=security["id"],
            asset_id=asset.id,
            owner="priya",
            team="ml-governance",
            source="asset_discovery",
        )
    )
    risk_id = risk.id

    # 4. visible in the default register view and findable by asset name
    visible = _risk_service(db).list(RiskFilter())
    assert visible[1] == 1
    assert visible[0][0].id == risk_id

    by_asset_name, total = _risk_service(db).list(RiskFilter(search=asset.name))
    assert total == 1
    assert by_asset_name[0].id == risk_id

    # 5. governance review starts: status change is audited
    _risk_service(db).update(risk_id, RiskUpdate(status=RiskStatus.IN_REVIEW))
    assert _risk_service(db).get(risk_id).status == RiskStatus.IN_REVIEW

    # 6. archive: leaves the default view, stays retrievable, filterable
    _risk_service(db).archive(risk_id)
    archived_row = _risk_service(db).get(risk_id)
    assert archived_row.archived_at is not None
    assert str(archived_row.category_id) == security["id"]
    assert archived_row.asset_id == asset.id

    default_items, default_total = _risk_service(db).list(RiskFilter())
    assert default_total == 0  # the archived risk left the default view
    assert all(item.id != risk_id for item in default_items)

    archived_only, archived_total = _risk_service(db).list(RiskFilter(archived=True))
    assert archived_total == 1
    assert archived_only[0].id == risk_id

    # the API serves the archived row too (soft delete, never a 404)
    fetched = client.get(f"{API}/risks/{risk_id}")
    assert fetched.status_code == 200
    assert fetched.json()["archived_at"] is not None
    assert fetched.json()["category"]["slug"] == "security"
    assert fetched.json()["asset"]["id"] == str(asset.id)

    # 7. the audit trail tells the whole story, in order
    events = _risk_events(db)
    assert [event.event_type for event in events] == [
        "risk.created",
        "risk.updated",
        "risk.archived",
    ]
    assert all(event.resource_id == str(risk_id) for event in events)
    assert events[0].after_state["status"] == "open"
    assert sorted(events[1].changed_fields or []) == ["status"]
    assert events[2].before_state["archived_at"] is None
    assert events[2].after_state["archived_at"] is not None
