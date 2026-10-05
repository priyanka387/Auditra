from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from app.core.config import settings


def test_model_versioning_migration_downgrade_and_upgrade(database):
    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    engine = sa.create_engine(settings.database_url, poolclass=sa.pool.NullPool)
    try:
        assert sa.inspect(engine).has_table("model_versions")
        try:
            command.downgrade(cfg, "0001")
            insp = sa.inspect(engine)
            assert not insp.has_table("model_versions")
            assert insp.has_table("models")
        finally:
            command.upgrade(cfg, "head")
        assert sa.inspect(engine).has_table("model_versions")
    finally:
        engine.dispose()


def test_model_deployment_migration_downgrade_and_upgrade(database):
    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    engine = sa.create_engine(settings.database_url, poolclass=sa.pool.NullPool)
    try:
        insp = sa.inspect(engine)
        assert insp.has_table("model_deployments")
        assert insp.has_table("deployment_endpoints")
        try:
            command.downgrade(cfg, "0002")
            insp = sa.inspect(engine)
            assert not insp.has_table("model_deployments")
            assert not insp.has_table("deployment_endpoints")
            assert insp.has_table("model_versions")
        finally:
            command.upgrade(cfg, "head")
        insp = sa.inspect(engine)
        assert insp.has_table("model_deployments")
        assert insp.has_table("deployment_endpoints")
        deployment_indexes = {i["name"]: i for i in insp.get_indexes("model_deployments")}
        for expected in (
            "ix_model_deployments_tenant_model_version_env",
            "ix_model_deployments_tenant_status",
            "ix_model_deployments_tenant_environment_status",
            "ix_model_deployments_tenant_target_type",
            "ix_model_deployments_tenant_last_seen_at",
            "ix_model_deployments_tenant_created_at",
        ):
            assert expected in deployment_indexes
        endpoint_indexes = {i["name"]: i for i in insp.get_indexes("deployment_endpoints")}
        primary = endpoint_indexes.get("uq_deployment_endpoints_primary_inference")
        assert primary is not None and primary["unique"] is True
        fks = insp.get_foreign_keys("model_deployments")
        assert any(
            fk["referred_table"] == "model_versions" and fk["options"].get("ondelete") == "RESTRICT"
            for fk in fks
        )
    finally:
        engine.dispose()


def test_application_agent_association_migration_downgrade_and_upgrade(database):
    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    engine = sa.create_engine(settings.database_url, poolclass=sa.pool.NullPool)
    try:
        insp = sa.inspect(engine)
        for table in ("applications", "agents", "agent_model_associations"):
            assert insp.has_table(table)
        indexes = {i["name"]: i for i in insp.get_indexes("agent_model_associations")}
        for name in (
            "uq_agent_model_associations_agent_role_priority",
            "uq_agent_model_associations_agent_model_role",
        ):
            assert name in indexes and indexes[name]["unique"] is True
        agent_fks = insp.get_foreign_keys("agents")
        assert any(
            fk["referred_table"] == "applications" and fk["options"].get("ondelete") == "RESTRICT"
            for fk in agent_fks
        )
        association_fks = insp.get_foreign_keys("agent_model_associations")
        assert any(
            fk["referred_table"] == "agents" and fk["options"].get("ondelete") == "RESTRICT"
            for fk in association_fks
        )
        try:
            command.downgrade(cfg, "0003")
            insp = sa.inspect(engine)
            for table in ("applications", "agents", "agent_model_associations"):
                assert not insp.has_table(table)
            assert insp.has_table("model_deployments")
        finally:
            command.upgrade(cfg, "head")
        insp = sa.inspect(engine)
        for table in ("applications", "agents", "agent_model_associations"):
            assert insp.has_table(table)
        indexes = {i["name"]: i for i in insp.get_indexes("agent_model_associations")}
        assert indexes["uq_agent_model_associations_agent_role_priority"]["unique"] is True
    finally:
        engine.dispose()


def test_model_usage_migration_downgrade_and_upgrade(database):
    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    engine = sa.create_engine(settings.database_url, poolclass=sa.pool.NullPool)
    try:
        insp = sa.inspect(engine)
        assert insp.has_table("model_usage_events")
        indexes = {i["name"]: i for i in insp.get_indexes("model_usage_events")}
        for name in (
            "ix_model_usage_events_tenant_started_at",
            "ix_model_usage_events_tenant_model_started_at",
            "ix_model_usage_events_tenant_status_started_at",
            "ix_model_usage_events_event_id",
        ):
            assert name in indexes, name
        assert indexes["ix_model_usage_events_event_id"]["unique"] is True
        fks = {fk["referred_table"]: fk for fk in insp.get_foreign_keys("model_usage_events")}
        assert set(fks) == {"models", "model_versions", "model_deployments", "applications", "agents"}
        assert all(fk["options"].get("ondelete") == "RESTRICT" for fk in fks.values())
        checks = {c["name"] for c in insp.get_check_constraints("model_usage_events")}
        assert "ck_model_usage_events_status" in checks
        assert "ck_model_usage_events_source" in checks
        assert "ck_model_usage_events_tokens_non_negative" in checks
        try:
            command.downgrade(cfg, "0004")
            assert not sa.inspect(engine).has_table("model_usage_events")
        finally:
            command.upgrade(cfg, "head")
        assert sa.inspect(engine).has_table("model_usage_events")
    finally:
        engine.dispose()
