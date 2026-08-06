"""The migration chain must describe exactly what the models declare.

Nothing else in the suite runs migrations: tests/conftest.py builds the
schema with create_all() because it is faster and the tests care about
behaviour, not DDL. That leaves one hole — a model change that nobody wrote
a migration for passes every test and then fails on deploy. This closes it.
"""

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text

import admin.models  # noqa: F401  - registers the tables on Base.metadata
from db.base import Base


@pytest.fixture()
def migrated_engine(database_url_root):
    """A database built by `alembic upgrade head`, dropped afterwards."""
    root = create_engine(database_url_root, future=True)
    with root.connect() as conn:
        conn.execute(text("DROP DATABASE IF EXISTS kaicalc_migrationtest"))
        conn.execute(text("CREATE DATABASE kaicalc_migrationtest"))
        conn.commit()

    url = database_url_root.rsplit("/", 1)[0] + "/kaicalc_migrationtest"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(cfg, "head")

    engine = create_engine(url, future=True)
    yield engine
    engine.dispose()
    with root.connect() as conn:
        conn.execute(text("DROP DATABASE IF EXISTS kaicalc_migrationtest"))
        conn.commit()
    root.dispose()


def test_the_migration_chain_matches_the_models(migrated_engine):
    with migrated_engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        difference = compare_metadata(ctx, Base.metadata)

    assert difference == [], (
        "The migration chain and the models have drifted. Each entry below is "
        "something the models declare that the migrations do not produce, or "
        "the reverse:\n" + "\n".join(repr(d) for d in difference)
    )
