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
            fk["referred_table"] == "model_versions"
            and fk["options"].get("ondelete") == "RESTRICT"
            for fk in fks
        )
    finally:
        engine.dispose()
