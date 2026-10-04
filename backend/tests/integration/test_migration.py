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
