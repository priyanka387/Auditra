import json

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from sqlalchemy import select

from app.core.config import settings
from app.modules.model_discovery.integrations import (
    AuditraDiscoveryCallback,
    InsufficientMetadataResult,
)
from app.modules.model_discovery.models import ModelDiscovery
from app.modules.model_discovery.service import DiscoveryService, ServiceDiscoverySink
from app.modules.model_inventory.schemas.model import ModelCreate
from app.modules.model_inventory.services import ModelService

AUDITRA_METADATA = {
    "auditra_provider": "openai",
    "auditra_model_identifier": "gpt-5.x",
    "auditra_environment": "test",
}


class RecordingSink:
    def __init__(self):
        self.observations = []
        self.insufficient = []

    def observe(self, payload) -> None:
        self.observations.append(payload)

    def insufficient_metadata(self, result: InsufficientMetadataResult) -> None:
        self.insufficient.append(result)


def _model(messages):
    return GenericFakeChatModel(messages=iter(messages))


def _sink(db):
    return ServiceDiscoverySink(DiscoveryService(db, settings.default_tenant_id), strict=True)


def _invoke(model, db, metadata=AUDITRA_METADATA, source_identifier="support-service"):
    callback = AuditraDiscoveryCallback(_sink(db), source_identifier=source_identifier)
    return model.invoke("hello", config={"callbacks": [callback], "metadata": dict(metadata)})


def _rows(db):
    return db.execute(select(ModelDiscovery)).scalars().all()


def _register_gpt(db):
    return ModelService(db, settings.default_tenant_id).register_model(
        ModelCreate(
            provider_slug="openai",
            model_type_slug="llm",
            name="GPT-5.X",
            native_model_id="gpt-5.x",
        )
    )


def test_langchain_invocation_persists_observation_matched(db, parent_model):
    model = _register_gpt(db)

    _invoke(_model([AIMessage(content="hi")]), db)

    rows = _rows(db)
    assert len(rows) == 1
    row = rows[0]
    assert row.source_type == "langchain"
    assert row.source_identifier == "support-service"
    assert row.status == "MATCHED"
    assert row.matched_model_id == model.id
    assert row.canonical_identity == "openai|gpt-5.x"
    assert row.metadata_["framework"] == "langchain"
    assert row.metadata_["model_class"] == "GenericFakeChatModel"


def test_langchain_repeated_invocation_increments_count(db, parent_model):
    _register_gpt(db)
    model = _model([AIMessage(content="one"), AIMessage(content="two")])

    _invoke(model, db)
    _invoke(model, db)

    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0].observation_count == 2


def test_langchain_invocation_persists_no_prompt_content(db, parent_model):
    _register_gpt(db)
    model = _model([AIMessage(content="RESPONSE-SECRET")])

    callback = AuditraDiscoveryCallback(_sink(db), source_identifier="support-service")
    model.invoke(
        "SUPER-SECRET-PROMPT",
        config={"callbacks": [callback], "metadata": dict(AUDITRA_METADATA)},
    )

    rows = _rows(db)
    assert len(rows) == 1
    dump = json.dumps(rows[0].metadata_)
    assert "SUPER-SECRET-PROMPT" not in dump
    assert "RESPONSE-SECRET" not in dump


def test_langchain_insufficient_metadata_not_persisted(db, parent_model):
    sink = RecordingSink()
    callback = AuditraDiscoveryCallback(sink)
    _model([AIMessage(content="hi")]).invoke(
        "hello", config={"callbacks": [callback], "metadata": {}}
    )

    assert _rows(db) == []
    assert sink.observations == []
    assert len(sink.insufficient) == 1
    assert sink.insufficient[0].error_code == "INSUFFICIENT_METADATA"
