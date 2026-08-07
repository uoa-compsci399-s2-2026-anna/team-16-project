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


def _include_object(object_, name, type_, reflected, compare_to):
    """Exclude factor_downstream's functional COALESCE index from the diff.

    admin/factor_models.py deliberately does not declare
    `uq_factor_downstream_generic` (contract §2.2) as a SQLAlchemy `Index`:
    it is a MySQL functional index — `(COALESCE(food_category_id, 0))` as a
    key part — and this SQLAlchemy/PyMySQL combination cannot reflect that
    expression back out correctly. Verified directly against this MySQL by
    querying information_schema.STATISTICS: the expression key part comes
    back as a row with `column_name IS NULL` and `expression =
    'coalesce(\\`food_category_id\\`, 0)'`, but SQLAlchemy's MySQL dialect
    turns that into a plain column named `food_category_id` when reflecting
    indexes rather than a text/expression element - producing a shape that
    matches neither "declared as a plain index" (compares unequal against a
    real functional index) nor "not declared at all" (which reports it as
    an index to remove). Either way, comparing this one index is not
    meaningful with the installed SQLAlchemy version, so it is excluded here
    rather than left to produce a permanent false positive.

    This does not weaken what the test proves: every other table, column,
    constraint and plain index in admin/factor_models.py is still compared.
    The functional index's own correctness is proven independently by
    tests/admin/test_factor_models.py::test_two_generic_downstream_rows_are_refused,
    and its presence in the migration chain by the manual `alembic upgrade
    head` / `downgrade` cycle run while developing alembic/versions/0005_factors.py.
    """
    return not (type_ == "index" and name == "uq_factor_downstream_generic")


def test_the_migration_chain_matches_the_models(migrated_engine):
    with migrated_engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn, opts={"include_object": _include_object}
        )
        difference = compare_metadata(ctx, Base.metadata)

    assert difference == [], (
        "The migration chain and the models have drifted. Each entry below is "
        "something the models declare that the migrations do not produce, or "
        "the reverse:\n" + "\n".join(repr(d) for d in difference)
    )
