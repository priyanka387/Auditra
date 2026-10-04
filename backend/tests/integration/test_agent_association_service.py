import pytest

from app.core.config import settings
from app.modules.model_inventory.domain.errors import (
    AgentNotActiveError,
    AgentNotFoundError,
    AssociationNotFoundError,
    DuplicateAssociationError,
    ModelNotAssociableError,
    ModelNotFoundError,
    ModelVersionMismatchError,
)
from app.modules.model_inventory.schemas import (
    AgentCreate,
    AgentModelAssociationCreate,
    AgentModelAssociationUpdate,
    ApplicationCreate,
)
from app.modules.model_inventory.services import (
    AgentAssociationService,
    AgentService,
    ApplicationService,
)


def _application(db):
    from uuid import uuid4

    return ApplicationService(db, tenant_id=settings.default_tenant_id).create_application(
        ApplicationCreate(name="Support Copilot", slug=f"support-copilot-{uuid4().hex[:8]}")
    )


def _agent(db, **overrides):
    app = _application(db)
    payload = AgentCreate(name="Support Agent", slug="support-agent")
    return AgentService(db, tenant_id=settings.default_tenant_id).create_agent(app.id, payload)


def _service(db):
    return AgentAssociationService(db, tenant_id=settings.default_tenant_id)


def test_create_primary_association(db, parent_model):
    agent = _agent(db)
    assoc = _service(db).create_association(
        agent.id,
        AgentModelAssociationCreate(model_id=parent_model.id, role="PRIMARY", selection_priority=1),
    )
    assert assoc.role == "PRIMARY"
    assert assoc.status == "ACTIVE"
    assert assoc.model_id == parent_model.id
    assert assoc.model_version_id is None


def test_fallback_and_embedding_same_priority(db, parent_model, parent_version):

    from app.modules.model_inventory.models import Model

    second = Model(
        tenant_id=settings.default_tenant_id,
        provider_id=parent_model.provider_id,
        model_type_id=parent_model.model_type_id,
        name="Embedding Model",
        native_model_id="embed-9",
        canonical_key="openai|embed-9",
        source_type="MANUAL",
    )
    db.add(second)
    db.commit()

    agent = _agent(db)
    service = _service(db)
    service.create_association(
        agent.id,
        AgentModelAssociationCreate(model_id=parent_model.id, role="PRIMARY"),
    )
    service.create_association(
        agent.id,
        AgentModelAssociationCreate(model_id=second.id, role="EMBEDDING"),
    )
    items, total = service.list_associations(agent.id)
    assert total == 2
    assert {item.role for item in items} == {"PRIMARY", "EMBEDDING"}


def test_duplicate_role_priority_rejected(db, parent_model):
    agent = _agent(db)
    service = _service(db)
    service.create_association(
        agent.id, AgentModelAssociationCreate(model_id=parent_model.id, role="PRIMARY")
    )

    from app.modules.model_inventory.models import Model

    other = Model(
        tenant_id=settings.default_tenant_id,
        provider_id=parent_model.provider_id,
        model_type_id=parent_model.model_type_id,
        name="Other",
        native_model_id="other-9",
        canonical_key="openai|other-9",
        source_type="MANUAL",
    )
    db.add(other)
    db.commit()
    with pytest.raises(DuplicateAssociationError):
        service.create_association(
            agent.id,
            AgentModelAssociationCreate(model_id=other.id, role="PRIMARY"),
        )


def test_version_mismatch_rejected(db, parent_model, parent_version):

    from app.modules.model_inventory.models import Model

    other = Model(
        tenant_id=settings.default_tenant_id,
        provider_id=parent_model.provider_id,
        model_type_id=parent_model.model_type_id,
        name="Mismatched",
        native_model_id="mismatch-1",
        canonical_key="openai|mismatch-1",
        source_type="MANUAL",
    )
    db.add(other)
    db.commit()
    agent = _agent(db)
    with pytest.raises(ModelVersionMismatchError):
        _service(db).create_association(
            agent.id,
            AgentModelAssociationCreate(
                model_id=other.id,
                model_version_id=parent_version.id,
                role="PRIMARY",
            ),
        )


def test_valid_version_reference(db, parent_model, parent_version):
    agent = _agent(db)
    assoc = _service(db).create_association(
        agent.id,
        AgentModelAssociationCreate(
            model_id=parent_model.id,
            model_version_id=parent_version.id,
            role="PRIMARY",
        ),
    )
    assert assoc.model_version_id == parent_version.id


def test_retired_model_rejected_for_new_active_association(db, parent_model):
    parent_model.lifecycle_state = "RETIRED"
    db.commit()
    agent = _agent(db)
    with pytest.raises(ModelNotAssociableError):
        _service(db).create_association(
            agent.id, AgentModelAssociationCreate(model_id=parent_model.id, role="PRIMARY")
        )


def test_deprecated_model_still_associable(db, parent_model):
    parent_model.lifecycle_state = "DEPRECATED"
    db.commit()
    agent = _agent(db)
    assoc = _service(db).create_association(
        agent.id, AgentModelAssociationCreate(model_id=parent_model.id, role="PRIMARY")
    )
    assert assoc.model_id == parent_model.id


def test_archived_agent_rejects_new_association(db, parent_model):
    agent = _agent(db)
    service = _service(db)
    from app.modules.model_inventory.schemas import AgentUpdate

    service_agent = AgentService(db, tenant_id=settings.default_tenant_id)
    service_agent.update_agent(agent.id, AgentUpdate(status="ARCHIVED"))
    with pytest.raises(AgentNotActiveError):
        service.create_association(
            agent.id, AgentModelAssociationCreate(model_id=parent_model.id, role="PRIMARY")
        )


def test_inactive_agent_rejects_new_active_association(db, parent_model):
    from app.modules.model_inventory.schemas import AgentUpdate

    agent = _agent(db)
    AgentService(db, tenant_id=settings.default_tenant_id).update_agent(
        agent.id, AgentUpdate(status="INACTIVE")
    )
    with pytest.raises(AgentNotActiveError):
        _service(db).create_association(
            agent.id, AgentModelAssociationCreate(model_id=parent_model.id, role="PRIMARY")
        )


def test_unknown_agent_and_model_rejected(db, parent_model):
    from uuid import uuid4

    agent = _agent(db)
    with pytest.raises(AgentNotFoundError):
        _service(db).create_association(
            uuid4(), AgentModelAssociationCreate(model_id=parent_model.id, role="PRIMARY")
        )
    with pytest.raises(ModelNotFoundError):
        _service(db).create_association(
            agent.id, AgentModelAssociationCreate(model_id=uuid4(), role="PRIMARY")
        )


def test_cross_tenant_model_rejected(db, parent_model):
    from uuid import uuid4

    from app.modules.model_inventory.models import Model

    foreign = Model(
        tenant_id=uuid4(),
        provider_id=parent_model.provider_id,
        model_type_id=parent_model.model_type_id,
        name="Foreign",
        native_model_id="foreign-1",
        canonical_key="openai|foreign-1",
        source_type="MANUAL",
    )
    db.add(foreign)
    db.commit()
    agent = _agent(db)
    with pytest.raises(ModelNotFoundError):
        _service(db).create_association(
            agent.id, AgentModelAssociationCreate(model_id=foreign.id, role="PRIMARY")
        )


def test_disable_then_reassociate(db, parent_model):
    agent = _agent(db)
    service = _service(db)
    first = service.create_association(
        agent.id, AgentModelAssociationCreate(model_id=parent_model.id, role="PRIMARY")
    )
    service.disable_association(agent.id, first.id)
    second = service.create_association(
        agent.id, AgentModelAssociationCreate(model_id=parent_model.id, role="PRIMARY")
    )
    assert second.id != first.id
    items, total = service.list_associations(agent.id)
    assert total == 2
    statuses = {item.status for item in items}
    assert statuses == {"ACTIVE", "DISABLED"}
    disabled = service.get_association(agent.id, first.id)
    assert disabled.status == "DISABLED"
    assert disabled.disabled_at is not None


def test_disable_is_idempotent_and_keeps_history(db, parent_model):
    agent = _agent(db)
    service = _service(db)
    assoc = service.create_association(
        agent.id, AgentModelAssociationCreate(model_id=parent_model.id, role="PRIMARY")
    )
    service.disable_association(agent.id, assoc.id)
    service.disable_association(agent.id, assoc.id)
    assert service.get_association(agent.id, assoc.id).status == "DISABLED"


def test_update_role_and_priority(db, parent_model, parent_version):
    agent = _agent(db)
    service = _service(db)
    assoc = service.create_association(
        agent.id, AgentModelAssociationCreate(model_id=parent_model.id, role="PRIMARY")
    )
    updated = service.update_association(
        agent.id,
        assoc.id,
        AgentModelAssociationUpdate(
            role="FALLBACK",
            selection_priority=2,
            model_version_id=parent_version.id,
            configuration={"temperature": 0.2},
        ),
    )
    assert updated.role == "FALLBACK"
    assert updated.selection_priority == 2
    assert updated.model_version_id == parent_version.id
    assert updated.configuration == {"temperature": 0.2}


def test_update_duplicate_role_priority_rejected(db, parent_model):
    from app.modules.model_inventory.models import Model

    agent = _agent(db)
    service = _service(db)
    service.create_association(
        agent.id, AgentModelAssociationCreate(model_id=parent_model.id, role="PRIMARY")
    )
    second_model = Model(
        tenant_id=settings.default_tenant_id,
        provider_id=parent_model.provider_id,
        model_type_id=parent_model.model_type_id,
        name="Second",
        native_model_id="second-1",
        canonical_key="openai|second-1",
        source_type="MANUAL",
    )
    db.add(second_model)
    db.commit()
    second = service.create_association(
        agent.id,
        AgentModelAssociationCreate(
            model_id=second_model.id, role="FALLBACK", selection_priority=2
        ),
    )
    with pytest.raises(DuplicateAssociationError):
        service.update_association(
            agent.id,
            second.id,
            AgentModelAssociationUpdate(role="PRIMARY", selection_priority=1),
        )


def test_get_association_wrong_agent_is_not_found(db, parent_model):
    agent_a = _agent(db)
    agent_b = _agent(db)
    service = _service(db)
    assoc = service.create_association(
        agent_a.id, AgentModelAssociationCreate(model_id=parent_model.id, role="PRIMARY")
    )
    with pytest.raises(AssociationNotFoundError):
        service.get_association(agent_b.id, assoc.id)


def test_associations_only_returned_for_requested_agent(db, parent_model):
    agent_a = _agent(db)
    agent_b = _agent(db)
    service = _service(db)
    service.create_association(
        agent_a.id, AgentModelAssociationCreate(model_id=parent_model.id, role="PRIMARY")
    )
    items, total = service.list_associations(agent_b.id)
    assert total == 0
    assert items == []


def test_reverse_lookup_returns_agent_and_application(db, parent_model, parent_version):
    app = _application(db)
    agent = AgentService(db, tenant_id=settings.default_tenant_id).create_agent(
        app.id, AgentCreate(name="Research Agent", slug="research-agent")
    )
    service = _service(db)
    service.create_association(
        agent.id,
        AgentModelAssociationCreate(
            model_id=parent_model.id, model_version_id=parent_version.id, role="PRIMARY"
        ),
    )
    links, total = service.list_model_agents(parent_model.id)
    assert total == 1
    assoc, linked_agent, linked_app = links[0]
    assert linked_agent.id == agent.id
    assert linked_app.id == app.id
    assert assoc.role == "PRIMARY"


def test_reverse_lookup_filter_by_role(db, parent_model):
    app = _application(db)
    agent = AgentService(db, tenant_id=settings.default_tenant_id).create_agent(
        app.id, AgentCreate(name="A", slug="agent-a")
    )
    service = _service(db)
    service.create_association(
        agent.id, AgentModelAssociationCreate(model_id=parent_model.id, role="EMBEDDING")
    )
    _, total = service.list_model_agents(parent_model.id, role="PRIMARY")
    assert total == 0
    _, total = service.list_model_agents(parent_model.id, role="EMBEDDING")
    assert total == 1


def test_reverse_lookup_unknown_model(db, parent_model):
    from uuid import uuid4

    with pytest.raises(ModelNotFoundError):
        _service(db).list_model_agents(uuid4())
