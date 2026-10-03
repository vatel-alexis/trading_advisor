import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.config import get_settings

BACKEND_DIR = os.path.dirname(os.path.dirname(__file__))


@pytest.fixture(scope="session")
def migrated_db() -> str:
    """Run the migrations down and up on the test database, then hand back its URL."""
    url = get_settings().database_url
    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "migrations"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return url


@pytest.fixture
def db_inspector(migrated_db: str):
    engine = create_engine(migrated_db)
    try:
        yield inspect(engine)
    finally:
        engine.dispose()
