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


#: The two functional COALESCE indexes in the schema. Both are declared on
#: their models *and* created as raw SQL by their migration; neither can be
#: compared meaningfully — see `_include_object` below. Each is proven instead
#: by a behavioural test against the create_all() schema and by an
#: information_schema assertion here against the migrated schema.
_FUNCTIONAL_INDEXES = {
    "uq_factor_downstream_generic",   # §2.2, alembic/versions/0005_factors.py
    "uq_submission_entry_generic",    # §2.3, alembic/versions/0008_submissions.py
    "uq_factor_upstream_generic",     # §2.2, alembic/versions/0009_upstream_destination.py
}


def _include_object(object_, name, type_, reflected, compare_to):
    """Exclude the functional COALESCE indexes from the diff.

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

    `admin/factor_models.py`'s `uq_factor_upstream_generic` (contract §2.2,
    v1.8) is the third instance: `factor_upstream.destination_id` is nullable
    for the same reason `factor_downstream.food_category_id` is, and carries
    the same trap. Excluded for the same reason and pinned by the same
    information_schema assertion below.

    `db/models.py`'s `uq_submission_entry_generic` (contract §2.3) is the same
    construct on `submission_entry` and is excluded for the same reason — B's
    `factor_downstream` defect reproduced exactly on the new table, which is
    why §2.3 points at §2.2 for it. Confirmed empirically: `alembic revision
    --autogenerate` for `0008` logged "Detected added index
    'uq_submission_entry_generic' on ('submission_id', 'sector_id')", having
    dropped the expression key part on the way in.

    This does not weaken what the test proves: every other table, column,
    constraint and plain index in the model modules is still compared. Each
    functional index's own correctness is proven independently by a
    behavioural test —
    tests/admin/test_factor_models.py::test_two_generic_downstream_rows_are_refused
    and tests/db/test_submissions.py::test_two_entries_with_no_food_category_are_refused
    — and its presence in the migration chain by
    `test_the_chain_creates_the_functional_indexes_compare_metadata_cannot_see`
    below.
    """
    return not (type_ == "index" and name in _FUNCTIONAL_INDEXES)


@pytest.mark.db
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


@pytest.mark.db
@pytest.mark.parametrize(
    "table, index, coalesced, consequence",
    [
        (
            "factor_downstream", "uq_factor_downstream_generic",
            #: v1.31: **two** nullable columns, so two COALESCE key parts.
            #: Collapsing only one of them leaves the other's duplicates legal
            #: and the index looks right in every summary of it.
            ("sector_id", "food_category_id"),
            "a second generic downstream row inserts happily and the engine's "
            "factor lookup becomes nondeterministic",
        ),
        (
            "submission_entry", "uq_submission_entry_generic",
            ("food_category_id",),
            "one user's single 'no category breakdown' answer can be stored "
            "twice for the same sector, and §5.4's by_sector aggregation "
            "counts it twice in the public statistics",
        ),
        (
            "factor_upstream", "uq_factor_upstream_generic",
            ("destination_id",),
            "a second generic upstream row inserts happily and the upstream "
            "fallback introduced for O-7 becomes nondeterministic — the same "
            "input returning a different net benefit run to run",
        ),
    ],
    ids=lambda value: value if isinstance(value, str) and value.startswith("uq_") else None,
)
def test_the_chain_creates_the_functional_indexes_compare_metadata_cannot_see(
    migrated_engine, table, index, coalesced, consequence,
):
    """The thing `_include_object` above deliberately stops checking.

    Both COALESCE indexes are excluded from `compare_metadata` because
    SQLAlchemy cannot reflect a MySQL expression index back out in a form that
    compares meaningfully. That exclusion is justified, but it leaves them
    proven by nothing: `tests/admin/test_factor_models.py` and
    `tests/db/test_submissions.py` exercise the same indexes against the
    `create_all()` schema, and that path never runs migration DDL — so
    deleting the `op.execute` from `alembic/versions/0005_factors.py` or
    `0008_submissions.py` leaves the entire suite green while a real
    deployment silently loses the constraint.

    Neither is cosmetic. Without them MySQL admits unlimited duplicate
    `food_category_id IS NULL` rows, because NULLs compare distinct inside a
    UNIQUE key — and in both cases the result is wrong numbers with no error
    and nothing in the logs.

    This asserts against `information_schema` rather than behaviour because
    behaviour is already covered; what was missing is evidence that *the
    migration* produces it.
    """
    with migrated_engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT COLUMN_NAME, EXPRESSION, NON_UNIQUE
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table
              AND INDEX_NAME = :index
            ORDER BY SEQ_IN_INDEX
        """), {"table": table, "index": index}).all()

    assert rows, (
        f"alembic upgrade head did not create {index}. Without it {consequence}."
    )
    assert all(row.NON_UNIQUE == 0 for row in rows), "the index is not unique"

    expressions = [row.EXPRESSION.lower() for row in rows if row.EXPRESSION]
    #: **Every** nullable column of this index must be collapsed, and each is
    #: named rather than counted. `any("coalesce" in ...)` was what this
    #: asserted until v1.31 gave `factor_downstream` a second nullable column,
    #: and it would have passed on an index that collapsed the new column and
    #: dropped the old one — an index that exists, is unique, contains a
    #: COALESCE, and silently stops enforcing half of what it was written for.
    for column in coalesced:
        assert any(
            "coalesce" in expr and column in expr for expr in expressions
        ), (
            f"{index} has no COALESCE({column}, ...) key part, so NULLs in "
            f"{column} still compare distinct and it does not do its job. "
            f"Key parts: {rows}. Without it {consequence}."
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


@pytest.mark.db
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

    #: The two 0008 carries (contract §2.3). Same blind spot, same reason to
    #: pin them here: tests/db/test_submissions.py proves both behaviourally,
    #: but off the create_all() schema, so it would stay green if the
    #: sa.CheckConstraint were dropped from the migration.
    assert "ck_submission_horizon" in clauses, (
        "alembic upgrade head did not create ck_submission_horizon. §6.2 allows "
        "20 and 100 only; the engine binds const_GWP_CH4 off this column, and a "
        f"third value silently resolves to neither. Found: {sorted(clauses)}"
    )
    assert "gwp_horizon" in clauses["ck_submission_horizon"]

    assert "ck_submission_line_qty" in clauses, (
        "alembic upgrade head did not create ck_submission_line_qty. Without it "
        "a negative qty_kg persists and subtracts from every metric total it "
        f"is summed into. Found: {sorted(clauses)}"
    )
    assert "qty_kg" in clauses["ck_submission_line_qty"]


@pytest.mark.db
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
