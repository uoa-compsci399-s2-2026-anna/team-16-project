"""The migration chain must describe exactly what the models declare.

Nothing else in the suite runs migrations: tests/conftest.py builds the
schema with create_all() because it is faster and the tests care about
behaviour, not DDL. That leaves one hole — a model change that nobody wrote
a migration for passes every test and then fails on deploy. This closes it.

**Known blind spot:** on this SQLAlchemy/MySQL combination, `compare_metadata`
sees a missing `UNIQUE` constraint but not a missing `CHECK` constraint —
confirmed by stripping each in turn from a migration and re-running
`test_the_migration_chain_matches_the_models` below: the UNIQUE removal
failed the test, the CHECK removal did not. A CHECK constraint declared on a
model (e.g. `comparison_scenario`'s `gwp_horizon IN (20, 100)`, see
`admin/comparison_models.py`) still needs its own `op.execute` proven by a
live-flush behavioural test
(`tests/admin/test_comparison_models.py::
test_the_gwp_horizon_is_one_of_the_two_the_contract_allows`) — this module's
drift gate does not guard it.
"""

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
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

    admin/factor_models.py *does* declare `uq_factor_downstream_generic`
    (contract §2.2) as a SQLAlchemy `Index` — it is a MySQL functional
    index — `(COALESCE(food_category_id, 0))` as a key part — and this
    SQLAlchemy/PyMySQL combination cannot reflect that expression back out
    correctly. Verified directly against this MySQL by
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


def test_the_chain_creates_the_functional_index_compare_metadata_cannot_see(
    migrated_engine,
):
    """The one thing `_include_object` above deliberately stops checking.

    `uq_factor_downstream_generic` is excluded from `compare_metadata` because
    SQLAlchemy cannot reflect a MySQL expression index back out in a form that
    compares meaningfully. That exclusion is justified, but it leaves the index
    proven by nothing: `tests/admin/test_factor_models.py` creates the same
    index by hand against the `create_all()` schema, because that path never
    runs migration DDL — so deleting the `op.execute` from
    `alembic/versions/0005_factors.py` leaves the entire suite green while a
    real deployment silently loses the constraint.

    That constraint is not cosmetic. Without it MySQL admits unlimited
    duplicate `food_category_id IS NULL` rows — the generic "applies to every
    food category" rows — and the engine's fallback lookup then picks one
    nondeterministically: the same input returning different numbers, with
    nothing in the logs to explain it.

    This asserts against `information_schema` rather than behaviour because
    behaviour is already covered; what was missing is evidence that *the
    migration* produces it.
    """
    with migrated_engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT COLUMN_NAME, EXPRESSION, NON_UNIQUE
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'factor_downstream'
              AND INDEX_NAME = 'uq_factor_downstream_generic'
            ORDER BY SEQ_IN_INDEX
        """)).all()

    assert rows, (
        "alembic upgrade head did not create uq_factor_downstream_generic. "
        "Without it a second generic downstream row inserts happily and the "
        "engine's factor lookup becomes nondeterministic."
    )
    assert all(row.NON_UNIQUE == 0 for row in rows), "the index is not unique"

    expressions = [row.EXPRESSION for row in rows if row.EXPRESSION]
    assert any("coalesce" in expr.lower() for expr in expressions), (
        "the index exists but has no COALESCE key part, so NULLs still compare "
        f"distinct and it does not do its job. Key parts: {rows}"
    )


def test_the_chain_has_exactly_one_head_and_exactly_one_root():
    """No second `down_revision = None`, and no fork.

    From B's `tests/test_migration.py`, generalised off her own revision id
    and folded in here when her `migrations/` directory was deleted during
    integration. Her chain and this one were both rooted at
    `down_revision = None`; merging the two `versions/` directories would have
    produced two heads, `alembic upgrade head` would have aborted, and
    `upgrade heads` would have hit `Table 'staff' already exists` — which, MySQL
    DDL being autocommitting, is only recoverable by dropping the database.
    This is the cheap, offline guard against that ever being reintroduced: it
    reads the scripts, so it needs no database and no marker.
    """
    script = ScriptDirectory.from_config(Config("alembic.ini"))

    assert len(script.get_heads()) == 1, (
        f"the migration chain has forked: {script.get_heads()}"
    )
    roots = [
        revision.revision
        for revision in script.walk_revisions()
        if revision.down_revision is None
    ]
    assert len(roots) == 1, f"more than one initial revision: {roots}"


def test_the_chain_creates_the_check_constraints_compare_metadata_cannot_see(
    migrated_engine,
):
    """The other thing this module's blind spot hides.

    Per the "Known blind spot" note at the top of this file, `compare_metadata`
    on this SQLAlchemy/MySQL combination reports a missing UNIQUE but not a
    missing CHECK. `tests/admin/test_taxonomy_models.py` proves both taxonomy
    CHECKs behaviourally, but it builds its schema with `create_all()` off the
    models — it never runs migration DDL, so deleting `sa.CheckConstraint(...)`
    from `alembic/versions/0004_taxonomy.py` leaves that file green while a real
    deployment silently loses the constraint. Exactly the hole
    `test_the_chain_creates_the_functional_index_compare_metadata_cannot_see`
    above closes for the functional index.

    These two arrived with B's models and were nearly lost when her duplicate
    taxonomy classes were replaced by admin/taxonomy_models.py's during the
    integration of her branch. `ck_unit_preset_kg` is the one that matters:
    without it a negative `kg_per_unit` inserts cleanly and `web/units.js`
    turns a unit count into a negative mass.
    """
    with migrated_engine.connect() as conn:
        clauses = dict(conn.execute(text("""
            SELECT CONSTRAINT_NAME, CHECK_CLAUSE
            FROM information_schema.CHECK_CONSTRAINTS
            WHERE CONSTRAINT_SCHEMA = DATABASE()
        """)).all())

    assert "ck_metric_precision" in clauses, (
        "alembic upgrade head did not create ck_metric_precision. "
        f"CHECK constraints found: {sorted(clauses)}"
    )
    assert "display_precision" in clauses["ck_metric_precision"]

    assert "ck_unit_preset_kg" in clauses, (
        "alembic upgrade head did not create ck_unit_preset_kg. Without it a "
        "negative kg_per_unit inserts cleanly and web/units.js produces a "
        f"negative mass. CHECK constraints found: {sorted(clauses)}"
    )
    assert "kg_per_unit" in clauses["ck_unit_preset_kg"]


def test_the_chain_gives_the_factor_tables_bigint_primary_keys(migrated_engine):
    """Contract §2.2 specifies BIGINT for `factor_upstream.id` and
    `factor_downstream.id`, not INT.

    Roughly 270 upstream and 600 downstream rows land per factor set, on every
    version, and old sets are archived rather than deleted — the two tables in
    the schema with a real reason to outgrow an INT.

    `compare_metadata` *would* catch this one, unlike the CHECKs above. It is
    asserted here anyway because it was lost the same way and in the same
    commit: autogenerate emitted `sa.Integer()` for both because the models
    declared a bare `mapped_column(primary_key=True)`, and the drift gate
    agreed with itself.
    """
    with migrated_engine.connect() as conn:
        types = dict(conn.execute(text("""
            SELECT TABLE_NAME, DATA_TYPE
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND COLUMN_NAME = 'id'
              AND TABLE_NAME IN ('factor_upstream', 'factor_downstream',
                                 'factor_set')
        """)).all())

    assert types["factor_upstream"] == "bigint", types
    assert types["factor_downstream"] == "bigint", types
    #: factor_set is INT in the contract and stays INT - one row per published
    #: version, so this is not an oversight in the two rows above.
    assert types["factor_set"] == "int", types
