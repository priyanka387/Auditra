from pathlib import Path
from uuid import uuid4

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
        assert set(fks) == {
            "models",
            "model_versions",
            "model_deployments",
            "applications",
            "agents",
        }
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


def test_audit_events_migration_downgrade_and_upgrade(database):
    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    engine = sa.create_engine(settings.database_url, poolclass=sa.pool.NullPool)
    try:
        insp = sa.inspect(engine)
        assert insp.has_table("audit_events")
        indexes = {i["name"]: i for i in insp.get_indexes("audit_events")}
        for name in (
            "ix_audit_events_tenant_resource_type_resource_id_sequence",
            "ix_audit_events_tenant_event_type_sequence",
            "ix_audit_events_tenant_occurred_at_sequence",
            "ix_audit_events_tenant_actor_sequence",
            "ix_audit_events_tenant_source_sequence",
        ):
            assert name in indexes, name
        unique = {c["name"]: c for c in insp.get_unique_constraints("audit_events")}
        assert "uq_audit_events_sequence_no" in unique
        assert unique["uq_audit_events_sequence_no"]["column_names"] == ["sequence_no"]
        checks = {c["name"] for c in insp.get_check_constraints("audit_events")}
        assert "ck_audit_events_schema_version_positive" in checks
        assert not insp.get_foreign_keys("audit_events")
        try:
            command.downgrade(cfg, "0006")
            assert not sa.inspect(engine).has_table("audit_events")
        finally:
            command.upgrade(cfg, "head")
        assert sa.inspect(engine).has_table("audit_events")
    finally:
        engine.dispose()


def test_model_discovery_migration_downgrade_and_upgrade(database):
    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    engine = sa.create_engine(settings.database_url, poolclass=sa.pool.NullPool)
    try:
        insp = sa.inspect(engine)
        assert insp.has_table("model_discovery")
        indexes = {i["name"]: i for i in insp.get_indexes("model_discovery")}
        for name in (
            "uq_model_discovery_tenant_source_identity",
            "ix_model_discovery_tenant_canonical_identity",
            "ix_model_discovery_tenant_status",
            "ix_model_discovery_tenant_source_type",
            "ix_model_discovery_tenant_provider",
            "ix_model_discovery_matched_model_id",
            "ix_model_discovery_tenant_last_seen_at",
        ):
            assert name in indexes, name
        assert indexes["uq_model_discovery_tenant_source_identity"]["unique"] is True
        fks = insp.get_foreign_keys("model_discovery")
        assert any(
            fk["referred_table"] == "models" and fk["options"].get("ondelete") == "RESTRICT"
            for fk in fks
        )
        checks = {c["name"] for c in insp.get_check_constraints("model_discovery")}
        assert "ck_model_discovery_status" in checks
        assert "ck_model_discovery_source_type" in checks
        assert "ck_model_discovery_observation_count" in checks
        try:
            command.downgrade(cfg, "0005")
            insp = sa.inspect(engine)
            assert not insp.has_table("model_discovery")
            assert insp.has_table("model_usage_events")
        finally:
            command.upgrade(cfg, "head")
        assert sa.inspect(engine).has_table("model_discovery")
    finally:
        engine.dispose()


def test_discovery_sources_migration_downgrade_and_upgrade(database):
    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    engine = sa.create_engine(settings.database_url, poolclass=sa.pool.NullPool)
    try:
        insp = sa.inspect(engine)
        assert insp.has_table("discovery_sources")
        indexes = {i["name"]: i for i in insp.get_indexes("discovery_sources")}
        for name in (
            "uq_discovery_sources_tenant_name",
            "ix_discovery_sources_tenant_status",
            "ix_discovery_sources_tenant_source_type",
        ):
            assert name in indexes, name
        assert indexes["uq_discovery_sources_tenant_name"]["unique"] is True
        checks = {c["name"] for c in insp.get_check_constraints("discovery_sources")}
        assert "ck_discovery_sources_source_type" in checks
        assert "ck_discovery_sources_status" in checks
        assert not insp.get_foreign_keys("discovery_sources")
        try:
            command.downgrade(cfg, "0007")
            assert not sa.inspect(engine).has_table("discovery_sources")
            assert sa.inspect(engine).has_table("model_discovery")
        finally:
            command.upgrade(cfg, "head")
        assert sa.inspect(engine).has_table("discovery_sources")
    finally:
        engine.dispose()


def test_discovery_runs_migration_downgrade_and_upgrade(database):
    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    engine = sa.create_engine(settings.database_url, poolclass=sa.pool.NullPool)
    try:
        insp = sa.inspect(engine)
        assert insp.has_table("discovery_runs")
        indexes = {i["name"] for i in insp.get_indexes("discovery_runs")}
        for name in (
            "ix_discovery_runs_tenant_source_started_at",
            "ix_discovery_runs_tenant_status",
            "ix_discovery_runs_tenant_started_at",
        ):
            assert name in indexes, name
        fks = insp.get_foreign_keys("discovery_runs")
        assert any(
            fk["referred_table"] == "discovery_sources"
            and fk["options"].get("ondelete") == "RESTRICT"
            for fk in fks
        )
        checks = {c["name"] for c in insp.get_check_constraints("discovery_runs")}
        assert "ck_discovery_runs_status" in checks
        assert "ck_discovery_runs_counts_non_negative" in checks
        assert "ck_discovery_runs_asset_counts" in checks
        try:
            command.downgrade(cfg, "0008")
            assert not sa.inspect(engine).has_table("discovery_runs")
            assert sa.inspect(engine).has_table("discovery_sources")
        finally:
            command.upgrade(cfg, "head")
        assert sa.inspect(engine).has_table("discovery_runs")
    finally:
        engine.dispose()


def test_discovered_assets_migration_downgrade_and_upgrade(database):
    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    engine = sa.create_engine(settings.database_url, poolclass=sa.pool.NullPool)
    try:
        insp = sa.inspect(engine)
        assert insp.has_table("discovered_assets")
        indexes = {i["name"] for i in insp.get_indexes("discovered_assets")}
        for name in (
            "ix_discovered_assets_tenant_source",
            "ix_discovered_assets_tenant_asset_type",
            "ix_discovered_assets_tenant_status",
            "ix_discovered_assets_tenant_last_seen_at",
        ):
            assert name in indexes, name
        unique = {c["name"]: c for c in insp.get_unique_constraints("discovered_assets")}
        identity = unique.get("uq_discovered_assets_source_identity")
        assert identity is not None
        assert identity["column_names"] == ["tenant_id", "source_id", "normalized_identity"]
        fks = insp.get_foreign_keys("discovered_assets")
        assert any(
            fk["referred_table"] == "discovery_sources"
            and fk["options"].get("ondelete") == "RESTRICT"
            for fk in fks
        )
        assert any(
            fk["referred_table"] == "discovery_runs" and fk["options"].get("ondelete") == "RESTRICT"
            for fk in fks
        )
        checks = {c["name"] for c in insp.get_check_constraints("discovered_assets")}
        assert "ck_discovered_assets_asset_type" in checks
        assert "ck_discovered_assets_status" in checks
        try:
            command.downgrade(cfg, "0009")
            assert not sa.inspect(engine).has_table("discovered_assets")
            assert sa.inspect(engine).has_table("discovery_runs")
        finally:
            command.upgrade(cfg, "head")
        assert sa.inspect(engine).has_table("discovered_assets")
    finally:
        engine.dispose()


def test_discovered_asset_runs_migration_downgrade_and_upgrade(database):
    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    engine = sa.create_engine(settings.database_url, poolclass=sa.pool.NullPool)
    try:
        insp = sa.inspect(engine)
        assert insp.has_table("discovered_asset_runs")
        indexes = {i["name"]: i for i in insp.get_indexes("discovered_asset_runs")}
        unique = indexes.get("uq_discovered_asset_runs_asset_run")
        assert unique is not None and unique["unique"] is True
        assert unique["column_names"] == ["discovered_asset_id", "discovery_run_id"]
        assert "ix_discovered_asset_runs_tenant_run" in indexes
        fks = {fk["referred_table"]: fk for fk in insp.get_foreign_keys("discovered_asset_runs")}
        assert set(fks) == {"discovered_assets", "discovery_runs"}
        assert all(fk["options"].get("ondelete") == "RESTRICT" for fk in fks.values())
        try:
            command.downgrade(cfg, "0010")
            insp = sa.inspect(engine)
            assert not insp.has_table("discovered_asset_runs")
            assert insp.has_table("discovered_assets")
        finally:
            command.upgrade(cfg, "head")
        assert sa.inspect(engine).has_table("discovered_asset_runs")
    finally:
        engine.dispose()


def test_asset_reconciliation_migration_downgrade_and_upgrade(database):
    """SF7 adds reconciliation state to discovered_assets (columns only)."""
    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    engine = sa.create_engine(settings.database_url, poolclass=sa.pool.NullPool)
    try:
        insp = sa.inspect(engine)
        assert insp.has_table("discovered_assets")
        columns = {c["name"]: c for c in insp.get_columns("discovered_assets")}
        for name in (
            "reconciliation_status",
            "reconciliation_reason",
            "reconciled_model_id",
        ):
            assert name in columns, name
        # pre-existing rows must be representable as unreconciled
        assert "'UNMATCHED'" in (columns["reconciliation_status"].get("default") or "")
        assert columns["reconciliation_reason"].get("default") is None
        assert columns["reconciled_model_id"].get("default") is None

        fks = insp.get_foreign_keys("discovered_assets")
        assert any(
            fk["referred_table"] == "models" and fk["options"].get("ondelete") == "RESTRICT"
            for fk in fks
        ), "reconciled_model_id must reference models RESTRICT"

        checks = {c["name"] for c in insp.get_check_constraints("discovered_assets")}
        assert "ck_discovered_assets_reconciliation_status" in checks
        assert "ck_discovered_assets_reconciliation_reason" in checks
        assert "ck_discovered_assets_reconciled_model" in checks

        indexes = {i["name"] for i in insp.get_indexes("discovered_assets")}
        assert "ix_discovered_assets_tenant_reconciliation_status" in indexes
        try:
            command.downgrade(cfg, "0011")
            insp = sa.inspect(engine)
            assert insp.has_table("discovered_assets")
            dropped_columns = {c["name"] for c in insp.get_columns("discovered_assets")}
            assert "reconciliation_status" not in dropped_columns
            assert "reconciled_model_id" not in dropped_columns
            dropped_checks = {c["name"] for c in insp.get_check_constraints("discovered_assets")}
            assert "ck_discovered_assets_reconciled_model" not in dropped_checks
            dropped_indexes = {i["name"] for i in insp.get_indexes("discovered_assets")}
            assert "ix_discovered_assets_tenant_reconciliation_status" not in dropped_indexes
            assert not any(
                fk["referred_table"] == "models"
                for fk in insp.get_foreign_keys("discovered_assets")
            )
        finally:
            command.upgrade(cfg, "head")
        insp = sa.inspect(engine)
        assert "reconciliation_status" in {c["name"] for c in insp.get_columns("discovered_assets")}
    finally:
        engine.dispose()


def test_asset_lifecycle_migration_downgrade_and_upgrade(database):
    """SF8 widens the discovered asset status vocabulary (constraint only)."""
    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    engine = sa.create_engine(settings.database_url, poolclass=sa.pool.NullPool)
    asset_id, source_id, tenant_id = uuid4(), uuid4(), uuid4()
    try:
        insp = sa.inspect(engine)
        checks = {c["name"]: c for c in insp.get_check_constraints("discovered_assets")}
        constraint = checks["ck_discovered_assets_status"]
        definition = constraint.get("definition") or constraint.get("sqltext") or ""
        for state in ("ACTIVE", "STALE", "DISAPPEARED", "IGNORED"):
            assert state in definition, state

        # a row in a lifecycle state must remain representable, and must
        # collapse back to ACTIVE when the pre-SF8 vocabulary is restored
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO discovery_sources (id, tenant_id, name, source_type, config, status) "
                    "VALUES (:source, :tenant, 'lifecycle-migration-source', 'repository', '{}', 'active')"
                ),
                {"source": source_id, "tenant": tenant_id},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO discovered_assets "
                    "(id, tenant_id, source_id, asset_type, name, normalized_identity, status, "
                    "first_seen_at, last_seen_at) "
                    "VALUES (:asset, :tenant, :source, 'MODEL_REFERENCE', 'gpt-4o', "
                    "'MODEL_REFERENCE|gpt-4o', 'STALE', now(), now())"
                ),
                {"asset": asset_id, "tenant": tenant_id, "source": source_id},
            )

        try:
            command.downgrade(cfg, "0012")
            insp = sa.inspect(engine)
            checks = {c["name"]: c for c in insp.get_check_constraints("discovered_assets")}
            old_definition = (
                checks["ck_discovered_assets_status"].get("definition")
                or checks["ck_discovered_assets_status"].get("sqltext")
                or ""
            )
            for state in ("STALE", "DISAPPEARED", "IGNORED"):
                assert state not in old_definition, state
            with engine.connect() as conn:
                status = conn.execute(
                    sa.text("SELECT status FROM discovered_assets WHERE id = :asset"),
                    {"asset": asset_id},
                ).scalar_one()
            assert status == "ACTIVE"  # rows stay valid under the old constraint
        finally:
            command.upgrade(cfg, "head")
            insp = sa.inspect(engine)
            checks = {c["name"]: c for c in insp.get_check_constraints("discovered_assets")}
            new_definition = (
                checks["ck_discovered_assets_status"].get("definition")
                or checks["ck_discovered_assets_status"].get("sqltext")
                or ""
            )
            assert "DISAPPEARED" in new_definition
    finally:
        with engine.begin() as conn:
            conn.execute(
                sa.text("DELETE FROM discovered_assets WHERE id = :asset"), {"asset": asset_id}
            )
            conn.execute(
                sa.text("DELETE FROM discovery_sources WHERE id = :source"), {"source": source_id}
            )
        engine.dispose()
