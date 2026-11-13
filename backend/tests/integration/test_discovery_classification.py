"""Integration tests: observation -> catalog asset -> classification -> normalized metadata."""

import pytest

from app.core.config import settings
from app.modules.asset_discovery.connectors.base import DiscoveryObservation
from app.modules.asset_discovery.enums import DiscoverySourceType
from app.modules.asset_discovery.errors import InvalidDiscoveredAssetError
from app.modules.asset_discovery.execution import DiscoveryExecutionService
from app.modules.asset_discovery.schemas import (
    DiscoveredAssetFilter,
    DiscoverySourceCreate,
)
from app.modules.asset_discovery.service import (
    DiscoveredAssetService,
    DiscoveryRunService,
    DiscoverySourceService,
)

TENANT = settings.default_tenant_id


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text(
        'import openai\nmodel = "openai:gpt-4o"\n', encoding="utf-8"
    )
    (tmp_path / "agent.py").write_text(
        "from langgraph.graph import StateGraph\n"
        "graph = StateGraph(G)\n"
        "agent = create_react_agent(model, tools)\n",
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text("langchain>=0.1.0\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def source(db, tmp_path):
    return DiscoverySourceService(db, TENANT).create(
        DiscoverySourceCreate(
            name="payments-service",
            source_type=DiscoverySourceType.REPOSITORY,
            config={"location": str(tmp_path)},
        )
    )


@pytest.fixture
def run(db, source):
    return DiscoveryRunService(db, TENANT).create(source.id)


def _source_for(db, location, name="payments-service"):
    return DiscoverySourceService(db, TENANT).create(
        DiscoverySourceCreate(
            name=name,
            source_type=DiscoverySourceType.REPOSITORY,
            config={"location": str(location)},
        )
    )


def _execute(db, source):
    run = DiscoveryRunService(db, TENANT).create(source.id)
    return DiscoveryExecutionService(db, TENANT).execute(run.id)


def _assets(db, **filters):
    items, _total = DiscoveredAssetService(db, TENANT).list(DiscoveredAssetFilter(**filters))
    return {asset.normalized_identity: asset for asset in items}


def test_execution_classifies_and_persists_normalized_metadata(db, repo):
    result = _execute(db, _source_for(db, repo))

    assert result.catalog.created == len(result.observations) == 7
    by_identity = _assets(db)

    model = by_identity["MODEL_REFERENCE|openai:gpt-4o"]
    assert model.asset_type == "MODEL_REFERENCE"
    assert model.metadata_["classification_rule"] == "type.model_reference"
    assert model.metadata_["model_id"] == "gpt-4o"
    assert model.metadata_["provider"] == "openai"
    assert model.metadata_["file_path"] == "app/main.py"
    assert model.metadata_["detection_rule"] == "model.assignment"
    assert model.metadata_["line_number"] == 2
    assert model.metadata_["key"] == "model"

    ai_service = by_identity["AI_SERVICE|openai"]
    assert ai_service.asset_type == "AI_SERVICE"
    assert ai_service.metadata_["classification_rule"] == "type.provider_reference"
    assert ai_service.metadata_["provider"] == "openai"
    assert ai_service.metadata_["file_path"] == "app/main.py"

    agent = by_identity["AGENT|create_react_agent"]
    assert agent.asset_type == "AGENT"
    assert agent.metadata_["classification_rule"] == "type.agent_reference"
    assert agent.metadata_["framework"] == "langchain"

    workflow = by_identity["WORKFLOW|StateGraph"]
    assert workflow.asset_type == "WORKFLOW"
    assert workflow.metadata_["classification_rule"] == "type.workflow_reference"
    assert workflow.metadata_["framework"] == "langgraph"

    framework_tool = by_identity["TOOL|langchain"]
    assert framework_tool.asset_type == "TOOL"
    assert framework_tool.metadata_["classification_rule"] == "type.framework_reference"
    assert framework_tool.metadata_["framework"] == "langchain"

    for asset in by_identity.values():
        assert asset.status == "ACTIVE"
        assert asset.metadata_["observation_type"]


def test_repeated_discovery_keeps_classification_stable_and_first_seen(db, repo):
    source = _source_for(db, repo, name="stable-source")
    first = _execute(db, source)
    before = _assets(db)
    first_seen = {identity: asset.first_seen_at for identity, asset in before.items()}

    second = _execute(db, source)

    assert second.catalog.created == 0
    assert second.catalog.unchanged == len(second.observations)
    after = _assets(db)
    assert set(after) == set(before)
    for identity, asset in after.items():
        # classification and normalized metadata survive re-scanning
        assert asset.id == before[identity].id
        assert asset.first_seen_at == first_seen[identity]
        assert asset.metadata_ == before[identity].metadata_
        assert asset.metadata_["classification_rule"].startswith("type.")

    run = DiscoveryRunService(db, TENANT).get(second.run.id)
    assert run.assets_created == 0
    assert run.assets_unchanged == len(second.observations)
    assert first.catalog.created == len(first.observations)


def test_unknown_observation_type_persists_nothing(db, source, run):
    observation = DiscoveryObservation(
        source_id=source.id,
        run_id=run.id,
        observation_type="mystery_signal",
        name="thing",
        provider=None,
        file_path="app/thing.py",
        line_number=1,
        detection_rule="future.rule",
        metadata={},
    )

    with pytest.raises(InvalidDiscoveredAssetError):
        DiscoveredAssetService(db, TENANT).record_observation(observation)

    _items, total = DiscoveredAssetService(db, TENANT).list(DiscoveredAssetFilter())
    assert total == 0
