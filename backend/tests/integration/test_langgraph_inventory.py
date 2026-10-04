from app.core.config import settings
from app.modules.model_inventory.models import Model
from app.modules.model_inventory.repositories import get_model_type_by_slug, get_provider_by_slug
from app.modules.model_inventory.schemas import (
    AgentCreate,
    AgentModelAssociationCreate,
    ApplicationCreate,
)
from app.modules.model_inventory.services import (
    AgentAssociationService,
    AgentService,
    ApplicationService,
)


def _model(db, name: str, native_model_id: str, model_type_slug: str = "llm") -> Model:
    tenant_id = settings.default_tenant_id
    model = Model(
        tenant_id=tenant_id,
        provider_id=get_provider_by_slug(db, tenant_id, "openai").id,
        model_type_id=get_model_type_by_slug(db, tenant_id, model_type_slug).id,
        name=name,
        native_model_id=native_model_id,
        canonical_key=f"openai|{native_model_id}",
        source_type="MANUAL",
    )
    db.add(model)
    db.commit()
    return model


def test_langgraph_inventory_end_to_end(db):
    from app.seed import seed_reference_data

    seed_reference_data(db)
    db.commit()

    tenant_id = settings.default_tenant_id
    application = ApplicationService(db, tenant_id).create_application(
        ApplicationCreate(
            name="Enterprise Knowledge Assistant",
            slug="enterprise-knowledge-assistant",
        )
    )
    agent = AgentService(db, tenant_id).create_agent(
        application.id,
        AgentCreate(
            name="Knowledge Research Agent",
            slug="knowledge-research-agent",
            framework="langgraph",
            agent_type="research",
        ),
    )
    assert agent.framework == "langgraph"

    llm = _model(db, "Knowledge LLM", "knowledge-llm-1")
    embedding = _model(db, "Knowledge Embedding", "knowledge-embed-1", "embedding")

    associations = AgentAssociationService(db, tenant_id)
    associations.create_association(
        agent.id, AgentModelAssociationCreate(model_id=llm.id, role="PRIMARY")
    )
    associations.create_association(
        agent.id, AgentModelAssociationCreate(model_id=embedding.id, role="EMBEDDING")
    )

    items, total = associations.list_associations(agent.id)
    assert total == 2
    assert {item.role for item in items} == {"PRIMARY", "EMBEDDING"}
    assert all(item.status == "ACTIVE" for item in items)

    links, links_total = associations.list_model_agents(llm.id, status="ACTIVE")
    assert links_total == 1
    linked_association, linked_agent, linked_application = links[0]
    assert linked_association.role == "PRIMARY"
    assert linked_agent.id == agent.id
    assert linked_application.id == application.id
    assert linked_application.slug == "enterprise-knowledge-assistant"

    AgentService(db, tenant_id).archive_agent(agent.id)
    items, total = associations.list_associations(agent.id)
    assert total == 2
    links, links_total = associations.list_model_agents(llm.id, status="ACTIVE")
    assert links_total == 1
    assert links[0][1].status == "ARCHIVED"
