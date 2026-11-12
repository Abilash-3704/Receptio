"""Schema management. Postgres is migrated with Alembic; the in-memory SQLite used by tests
without TEST_DATABASE_URL is built from the models with create_all (the migrations use
Postgres-only constraints, so they are exercised by the Postgres test run)."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine

from backend.app.db import Base

ROOT = Path(__file__).resolve().parents[2]


def upgrade(engine: Engine) -> None:
    if engine.dialect.name == "sqlite":
        Base.metadata.create_all(engine)
        return
    config = Config()  # alembic.ini is only for the CLI; nothing here depends on the file
    config.set_main_option("script_location", str(ROOT / "backend" / "migrations"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
