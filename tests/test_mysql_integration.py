"""MySQL 8 checks that SQLite cannot stand in for.

`DECIMAL(20,10)` round-tripping, `ON DELETE CASCADE` and the functional
COALESCE unique index (§2.2) all behave differently — or, in the index's
case, do not exist at all — on the in-memory SQLite the `tests/db` and
`tests/api` suites run against. This module is the only place they are
proven against the engine that ships.

**Runs by default**, on the same `docker compose up -d` instance the rest of
the suite uses, and skips with `python -m pytest -m "not db"`. It used to be
skipped unless a `TEST_DATABASE_URL` environment variable was set, which made
the one file that genuinely needed MySQL the one file nobody ran — B's own
review (`docs/ToB_v2.0.md` S10) flagged that as backwards, since the rest of
`tests/conftest.py` had settled on the opposite convention months earlier.

It builds its own scratch database with `alembic upgrade head` and drops it
afterwards, rather than borrowing `tests/conftest.py`'s `engine`: that
fixture builds the schema with `create_all()`, so the DDL these assertions
are about would never be exercised. Dropping the database also removes the
whole reason the earlier version ended with forty lines of hand-written
row-by-row cleanup, which had to stay in step with the foreign keys by hand.
"""

import uuid
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.models import (
    Destination,
    DestinationGroup,
    FactorDownstream,
    FactorSet,
    FactorSetStatus,
    FoodCategory,
    Metric,
)

pytestmark = [pytest.mark.db]

SCRATCH_DB = "kaicalc_mysqltest"


@pytest.fixture()
def mysql_engine(database_url_root, monkeypatch):
    """A scratch MySQL database built by `alembic upgrade head`."""
    root = create_engine(database_url_root, future=True)
    with root.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH_DB}"))
        conn.execute(
            text(
                f"CREATE DATABASE {SCRATCH_DB} "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        )
        conn.commit()

    url = database_url_root.rsplit("/", 1)[0] + f"/{SCRATCH_DB}"
    # alembic/env.py falls back to DATABASE_URL when the config carries no
    # url of its own; set both so neither ordering can point the upgrade at
    # the developer's own `kaicalc`.
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(cfg, "head")

    engine = create_engine(url, future=True)
    yield engine
    engine.dispose()
    with root.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH_DB}"))
        conn.commit()
    root.dispose()


def test_mysql_upgrade_constraints_decimal_and_cascade(mysql_engine):
    assert {
        "factor_set",
        "factor_downstream",
        "submission",
        "submission_entry",
        "submission_line",
        "audit_log",
        "ip_block",
        "staff",
        "staff_recovery_code",
    }.issubset(inspect(mysql_engine).get_table_names())

    suffix = uuid.uuid4().hex[:10]
    with Session(mysql_engine) as session:
        group = DestinationGroup(
            code=f"group_{suffix}", name="Integration group", is_waste=True
        )
        food = FoodCategory(
            code=f"food_{suffix}", name="Integration food", is_standard_mix=False
        )
        metric = Metric(
            code=f"metric_{suffix}",
            name="Integration metric",
            unit="unit",
            display_precision=2,
        )
        factor_set = FactorSet(
            version_label=f"integration-{suffix}",
            status=FactorSetStatus.draft,
            is_mock=True,
        )
        session.add_all([group, food, metric, factor_set])
        session.flush()
        destination = Destination(
            group_id=group.id,
            code=f"destination_{suffix}",
            name="Integration destination",
        )
        session.add(destination)
        session.flush()
        row = FactorDownstream(
            factor_set_id=factor_set.id,
            destination_id=destination.id,
            food_category_id=None,
            metric_id=metric.id,
            value_per_kg=Decimal("-0.1234567890"),
        )
        session.add(row)
        session.flush()
        # §1.2: ten decimal places survive the round trip, and a downstream
        # factor may be negative (§4.2 — an offset).
        assert session.get(FactorDownstream, row.id).value_per_kg == Decimal(
            "-0.1234567890"
        )
        factor_set_id = factor_set.id
        destination_id = destination.id
        metric_id = metric.id
        session.commit()

    # §2.2: a second generic row (food_category_id IS NULL) for the same
    # (factor_set, destination, metric). MySQL's plain UNIQUE does not stop
    # this because NULLs compare distinct; the COALESCE functional index
    # does, and SQLite cannot demonstrate either behaviour.
    with Session(mysql_engine) as session:
        session.add(
            FactorDownstream(
                factor_set_id=factor_set_id,
                destination_id=destination_id,
                food_category_id=None,
                metric_id=metric_id,
                value_per_kg=Decimal("1"),
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    # ON DELETE CASCADE from factor_set to its factor rows. Asserted here
    # rather than in tests/db because pysqlite leaves PRAGMA foreign_keys
    # off, which makes the cascade inert on the engine most of the suite
    # uses — every child row would orphan invisibly and nothing would fail.
    with Session(mysql_engine) as session:
        session.delete(session.get(FactorSet, factor_set_id))
        session.commit()
        assert (
            session.scalar(
                select(func.count())
                .select_from(FactorDownstream)
                .where(FactorDownstream.factor_set_id == factor_set_id)
            )
            == 0
        )
