from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.modules.model_inventory.models import Agent, AgentModelAssociation, Application

TENANT = settings.default_tenant_id
OTHER_TENANT = UUID("22222222-2222-2222-2222-222222222222")


def _application(tenant=TENANT, slug="test-app", **overrides):
    fields = {
        "id": uuid4(),
        "tenant_id": tenant,
        "name": "Test App",
        "slug": slug,
        "status": "ACTIVE",
        "source": "manual",
    }
    fields.update(overrides)
    return Application(**fields)


def _agent(application_id, tenant=TENANT, slug="test-agent", **overrides):
    fields = {
        "id": uuid4(),
        "tenant_id": tenant,
        "application_id": application_id,
        "name": "Test Agent",
        "slug": slug,
        "status": "ACTIVE",
        "source": "manual",
        "framework": "langgraph",
    }
    fields.update(overrides)
    return Agent(**fields)


def _association(agent_id, model_id, tenant=TENANT, **overrides):
    fields = {
        "id": uuid4(),
        "tenant_id": tenant,
        "agent_id": agent_id,
        "model_id": model_id,
        "role": "PRIMARY",
        "selection_priority": 1,
        "status": "ACTIVE",
        "source": "manual",
    }
    fields.update(overrides)
    return AgentModelAssociation(**fields)


def _commit_expect_integrity_error(db, row):
    db.add(row)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_tables_exist(db):
    inspector = sa.inspect(db.bind)
    for table in ("applications", "agents", "agent_model_associations"):
        assert inspector.has_table(table)


def test_round_trip_application_agent_association(db, parent_model):
    app = _application()
    agent = _agent(app.id)
    association = _association(agent.id, parent_model.id)
    db.add_all([app, agent, association])
    db.commit()

    loaded_app = db.get(Application, app.id)
    loaded_agent = db.get(Agent, agent.id)
    loaded_assoc = db.get(AgentModelAssociation, association.id)
    assert loaded_app is not None and loaded_app.slug == "test-app"
    assert loaded_agent is not None and loaded_agent.application_id == app.id
    assert loaded_agent.framework == "langgraph"
    assert loaded_assoc is not None and loaded_assoc.role == "PRIMARY"
    assert loaded_assoc.model_id == parent_model.id
    assert loaded_assoc.model_version_id is None
    assert loaded_assoc.disabled_at is None


def test_application_slug_unique_per_tenant(db):
    db.add(_application())
    db.commit()
    _commit_expect_integrity_error(db, _application())
    db.rollback()
    db.add(_application(tenant=OTHER_TENANT))
    db.commit()


def test_agent_slug_unique_within_application(db):
    app = _application()
    other_app = _application(slug="other-app")
    db.add_all([app, other_app])
    db.commit()

    db.add(_agent(app.id))
    db.commit()
    _commit_expect_integrity_error(db, _agent(app.id))
    db.add(_agent(other_app.id))
    db.commit()
    _commit_expect_integrity_error(db, _agent(uuid4()))


def test_association_role_priority_unique_for_active(db, parent_model):
    app = _application()
    agent = _agent(app.id)
    db.add_all([app, agent])
    db.commit()

    db.add(_association(agent.id, parent_model.id))
    db.commit()
    other_model = parent_model.__class__(
        tenant_id=TENANT,
        provider_id=parent_model.provider_id,
        model_type_id=parent_model.model_type_id,
        name="Other Model",
        native_model_id="other-model-1",
        canonical_key="openai|other-model-1",
        source_type="MANUAL",
    )
    db.add(other_model)
    db.commit()
    _commit_expect_integrity_error(
        db, _association(agent.id, other_model.id, role="PRIMARY", selection_priority=1)
    )


def test_association_model_role_unique_for_active(db, parent_model):
    app = _application()
    agent = _agent(app.id)
    db.add_all([app, agent])
    db.commit()
    db.add(_association(agent.id, parent_model.id, selection_priority=1))
    db.commit()
    _commit_expect_integrity_error(
        db, _association(agent.id, parent_model.id, selection_priority=2)
    )


def test_same_role_priority_allowed_for_different_role(db, parent_model):
    app = _application()
    agent = _agent(app.id)
    other_model = parent_model.__class__(
        tenant_id=TENANT,
        provider_id=parent_model.provider_id,
        model_type_id=parent_model.model_type_id,
        name="Embedding Model",
        native_model_id="embed-1",
        canonical_key="openai|embed-1",
        source_type="MANUAL",
    )
    db.add_all([app, agent, other_model])
    db.commit()
    db.add(_association(agent.id, parent_model.id, role="PRIMARY", selection_priority=1))
    db.add(_association(agent.id, other_model.id, role="EMBEDDING", selection_priority=1))
    db.commit()
    rows = db.scalars(
        sa.select(AgentModelAssociation).where(AgentModelAssociation.agent_id == agent.id)
    ).all()
    assert len(rows) == 2


def test_reassociate_same_role_priority_after_disable(db, parent_model):
    app = _application()
    agent = _agent(app.id)
    db.add_all([app, agent])
    db.commit()

    first = _association(agent.id, parent_model.id)
    db.add(first)
    db.commit()
    first.status = "DISABLED"
    db.commit()

    second = _association(agent.id, parent_model.id, status="ACTIVE")
    db.add(second)
    db.commit()
    rows = db.scalars(
        sa.select(AgentModelAssociation).where(
            AgentModelAssociation.agent_id == agent.id,
            AgentModelAssociation.role == "PRIMARY",
            AgentModelAssociation.selection_priority == 1,
        )
    ).all()
    assert len(rows) == 2
    assert sorted(r.status for r in rows) == ["ACTIVE", "DISABLED"]


def test_archived_records_remain_queryable(db, parent_model):
    app = _application(status="ARCHIVED")
    agent = _agent(app.id, status="ARCHIVED")
    association = _association(agent.id, parent_model.id, status="DISABLED")
    db.add_all([app, agent, association])
    db.commit()

    archived_apps = db.scalars(sa.select(Application).where(Application.status == "ARCHIVED")).all()
    disabled = db.scalars(
        sa.select(AgentModelAssociation).where(AgentModelAssociation.status == "DISABLED")
    ).all()
    assert app.id in [a.id for a in archived_apps]
    assert association.id in [a.id for a in disabled]


def test_foreign_key_restrict_on_application_with_agents(db):
    app = _application()
    db.add(app)
    db.commit()
    db.add(_agent(app.id))
    db.commit()
    with pytest.raises(IntegrityError):
        db.delete(app)
        db.commit()
    db.rollback()


def test_foreign_key_rejects_bogus_application_id(db):
    _commit_expect_integrity_error(db, _agent(uuid4()))


def test_foreign_key_rejects_bogus_model_id(db, parent_model):
    app = _application()
    agent = _agent(app.id)
    db.add_all([app, agent])
    db.commit()
    _commit_expect_integrity_error(db, _association(agent.id, uuid4()))


def test_check_constraint_rejects_bad_status_and_role(db, parent_model):
    app = _application()
    agent = _agent(app.id)
    db.add_all([app, agent])
    db.commit()
    _commit_expect_integrity_error(db, _application(slug="bad-status-app", status="BOGUS"))
    _commit_expect_integrity_error(db, _association(agent.id, parent_model.id, role="NOT_A_ROLE"))
