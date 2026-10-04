import os

os.environ["AUDITRA_DATABASE_URL"] = (
    "postgresql+psycopg://auditra:auditra@localhost:5433/auditra_test"
)

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command
from app.core.config import settings
from app.core.db import SessionLocal
from app.main import app
from app.modules.model_inventory.services import ModelService
from app.seed import seed_reference_data

TEST_DB_NAME = "auditra_test"
MAINTENANCE_URL = "postgresql+psycopg://auditra:auditra@localhost:5433/postgres"


@pytest.fixture(scope="session")
def database():
    engine = sa.create_engine(MAINTENANCE_URL, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(
            sa.text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :db AND pid <> pg_backend_pid()"
            ),
            {"db": TEST_DB_NAME},
        )
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"'))
        conn.execute(sa.text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    engine.dispose()

    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(cfg, "head")
    yield


@pytest.fixture
def db(database):
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.execute(
            sa.text(
                "TRUNCATE model_tag_links, model_tags, models, model_types, "
                "model_providers RESTART IDENTITY CASCADE"
            )
        )
        session.commit()
        session.close()


@pytest.fixture
def service(db):
    return ModelService(db, tenant_id=settings.default_tenant_id)


@pytest.fixture
def client(db):
    seed_reference_data(db)
    db.commit()
    yield TestClient(app)
