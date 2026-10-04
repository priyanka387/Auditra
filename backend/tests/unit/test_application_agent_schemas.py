from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.model_inventory.schemas import (
    AgentCreate,
    AgentModelAssociationCreate,
    AgentModelAssociationUpdate,
    AgentUpdate,
    ApplicationCreate,
    ApplicationUpdate,
    normalize_slug,
)


def test_normalize_slug_basic():
    assert normalize_slug("My App!") == "my-app"
    assert normalize_slug("  Hello--World  ") == "hello-world"
    assert normalize_slug("Already-Slug") == "already-slug"
    assert normalize_slug("support_agent.v2") == "support-agent-v2"


def test_normalize_slug_rejects_empty_result():
    with pytest.raises(ValueError, match="slug"):
        normalize_slug("!!!")


def test_application_create_minimal_uses_defaults():
    app = ApplicationCreate(name="AI Support Copilot", slug="ai-support-copilot")
    assert app.slug == "ai-support-copilot"
    assert app.source == "manual"
    assert app.status == "ACTIVE"
    assert app.metadata == {}
    assert app.tags == []


def test_application_create_normalizes_slug():
    app = ApplicationCreate(name="X", slug="  My Support App  ")
    assert app.slug == "my-support-app"


def test_application_create_rejects_empty_name():
    with pytest.raises(ValidationError):
        ApplicationCreate(name="   ", slug="a")


def test_application_create_rejects_long_name():
    with pytest.raises(ValidationError):
        ApplicationCreate(name="x" * 256, slug="a")


def test_application_create_rejects_empty_slug():
    with pytest.raises(ValidationError):
        ApplicationCreate(name="X", slug="!!!")


def test_application_create_rejects_secret_metadata():
    with pytest.raises(ValidationError, match="secret"):
        ApplicationCreate(name="X", slug="x", metadata={"api_key": "sk-123"})


def test_application_create_rejects_archived_status():
    with pytest.raises(ValidationError):
        ApplicationCreate(name="X", slug="x", status="ARCHIVED")


def test_application_create_allows_inactive_status():
    app = ApplicationCreate(name="X", slug="x", status="INACTIVE")
    assert app.status == "INACTIVE"


def test_application_create_rejects_unknown_source():
    with pytest.raises(ValidationError, match="source"):
        ApplicationCreate(name="X", slug="x", source="telepathy")


def test_application_update_is_partial_and_extra_forbidden():
    update = ApplicationUpdate(name="Renamed")
    assert update.name == "Renamed"
    assert update.slug is None
    with pytest.raises(ValidationError):
        ApplicationUpdate(id=uuid4())
    with pytest.raises(ValidationError):
        ApplicationUpdate(tenant_id=uuid4())


def test_agent_create_has_no_application_id_field():
    assert "application_id" not in AgentCreate.model_fields


def test_agent_create_minimal_uses_defaults():
    agent = AgentCreate(name="Support Agent", slug="support-agent")
    assert agent.source == "manual"
    assert agent.status == "ACTIVE"
    assert agent.framework is None
    assert agent.capabilities is None


def test_agent_create_normalizes_framework():
    agent = AgentCreate(name="A", slug="a", framework=" LangGraph ")
    assert agent.framework == "langgraph"


def test_agent_create_capabilities_must_be_structured():
    agent = AgentCreate(name="A", slug="a", capabilities={"retrieval": True})
    assert agent.capabilities == {"retrieval": True}
    with pytest.raises(ValidationError):
        AgentCreate(name="A", slug="a", capabilities="retrieval")


def test_agent_create_rejects_secret_metadata():
    with pytest.raises(ValidationError, match="secret"):
        AgentCreate(name="A", slug="a", metadata={"password": "hunter2"})


def test_agent_update_extra_forbidden():
    with pytest.raises(ValidationError):
        AgentUpdate(application_id=uuid4())


def test_association_create_minimal():
    assoc = AgentModelAssociationCreate(model_id=uuid4(), role="PRIMARY")
    assert assoc.selection_priority == 1
    assert assoc.status == "ACTIVE"
    assert assoc.source == "manual"
    assert assoc.configuration == {}
    assert assoc.model_version_id is None


def test_association_role_normalized_to_uppercase():
    assoc = AgentModelAssociationCreate(model_id=uuid4(), role="primary")
    assert assoc.role == "PRIMARY"


def test_association_rejects_invalid_role():
    with pytest.raises(ValidationError, match="role"):
        AgentModelAssociationCreate(model_id=uuid4(), role="LEAD")


def test_association_priority_must_be_positive():
    with pytest.raises(ValidationError):
        AgentModelAssociationCreate(model_id=uuid4(), role="PRIMARY", selection_priority=0)
    with pytest.raises(ValidationError):
        AgentModelAssociationCreate(model_id=uuid4(), role="PRIMARY", selection_priority=-2)


def test_association_rejects_secret_configuration():
    with pytest.raises(ValidationError, match="secret"):
        AgentModelAssociationCreate(
            model_id=uuid4(), role="PRIMARY", configuration={"token": "abc"}
        )


def test_association_create_status_must_be_known():
    assert (
        AgentModelAssociationCreate(model_id=uuid4(), role="PRIMARY", status="DISABLED").status
        == "DISABLED"
    )
    with pytest.raises(ValidationError):
        AgentModelAssociationCreate(model_id=uuid4(), role="PRIMARY", status="ARCHIVED")


def test_association_update_partial_and_extra_forbidden():
    update = AgentModelAssociationUpdate(role="FALLBACK")
    assert update.role == "FALLBACK"
    assert update.selection_priority is None
    with pytest.raises(ValidationError):
        AgentModelAssociationUpdate(agent_id=uuid4())
    with pytest.raises(ValidationError):
        AgentModelAssociationUpdate(id=uuid4())
