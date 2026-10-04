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
            #: v1.54: a second nullable column, `food_item_id`, for exactly the
            #: reason `factor_downstream` grew one in v1.31 — and the same
            #: two-COALESCE rule applies. Collapsing only `food_item_id` would
            #: let two "no category breakdown" entries back in; collapsing only
            #: `food_category_id` would let one visitor's Cheese be stored
            #: twice. Both, or the index has stopped doing half its job.
            ("food_category_id", "food_item_id"),
            "one user's single 'no category breakdown' answer can be stored "
            "twice for the same sector, and §5.4's by_sector aggregation "
            "counts it twice in the public statistics",
        ),
        (
            "factor_upstream", "uq_factor_upstream_generic",
            #: v1.54: `food_item_id` joins `destination_id` here on the same
            #: terms. An item row and the category row it refines must coexist
            #: (that is the whole of the four-candidate lookup), so the item
            #: column has to be a key part — and being nullable, it has to be
            #: a COALESCE one.
            ("destination_id", "food_item_id"),
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


@pytest.mark.db
def test_the_head_revision_round_trips(database_url_root):
    """`upgrade head` → `downgrade -1` → `upgrade head` lands on the same
    schema the models declare.

    **A downgrade nobody ran is a downgrade that does not work.** Every test
    above builds the chain forwards only, so a `downgrade()` that drops the
    wrong index, sequences two MySQL operations in an order errno 1553 refuses,
    or simply forgets a column is invisible to all of them — and it is the half
    of a revision that runs on the day a deployment has gone wrong and nobody
    wants surprises.

    Two of this revision's operations are the kind that only fail on the way
    back: `uq_submission_entry` and `uq_factor_upstream` both lead with the
    column MySQL is using to back a foreign key, so whichever of the two
    same-prefixed indexes is dropped first, the other must already exist.
    0009's upgrade documents that trap and sequences its downgrade as the
    mirror image; nothing until now proved either half.

    Its own scratch database rather than the `migrated_engine` fixture, so a
    chain left half-downgraded by a failure here cannot reach another test.
    """
    root = create_engine(database_url_root, future=True)
    with root.connect() as conn:
        conn.execute(text("DROP DATABASE IF EXISTS kaicalc_roundtriptest"))
        conn.execute(text("CREATE DATABASE kaicalc_roundtriptest"))
        conn.commit()

    url = database_url_root.rsplit("/", 1)[0] + "/kaicalc_roundtriptest"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))

    engine = create_engine(url, future=True)
    try:
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "-1")
        command.upgrade(cfg, "head")

        with engine.connect() as conn:
            ctx = MigrationContext.configure(
                conn, opts={"include_object": _include_object}
            )
            difference = compare_metadata(ctx, Base.metadata)
    finally:
        engine.dispose()
        with root.connect() as conn:
            conn.execute(text("DROP DATABASE IF EXISTS kaicalc_roundtriptest"))
            conn.commit()
        root.dispose()

    assert difference == [], (
        "the head revision does not round-trip: after upgrade → downgrade → "
        "upgrade the schema no longer matches the models. Each entry below is "
        "something the downgrade removed and the upgrade did not put back, or "
        "the reverse:\n" + "\n".join(repr(d) for d in difference)
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

    #: 0017's one CHECK (contract §2.3, v1.54). Same blind spot again:
    #: tests/db/test_food_item_schema.py proves it behaviourally off the
    #: create_all() schema and would stay green if the op.execute here were
    #: dropped. `food_category_id IS NULL AND food_item_id IS NOT NULL` is the
    #: state that would file the calculator's most specific answer into §5.4's
    #: "not broken down by type" bucket.
    assert "ck_submission_entry_item_has_category" in clauses, (
        "alembic upgrade head did not create "
        "ck_submission_entry_item_has_category. Without it an entry may name a "
        "food item and no food category, which §5.4 forbids outright. "
        f"Found: {sorted(clauses)}"
    )
    assert "food_item_id" in clauses["ck_submission_entry_item_has_category"]
    assert "food_category_id" in clauses["ck_submission_entry_item_has_category"]

    #: 0018's one CHECK (contract §2.3, v1.67), and the blind spot again:
    #: tests/db/test_submissions.py proves all six refusals behaviourally off
    #: the create_all() schema and would stay green if the op.execute here
    #: were dropped. This constraint is the half of v1.67's contradiction rule
    #: that holds against a writer which is not the API -- the panel, a CLI, a
    #: correction made by hand -- so losing it in a deployment loses exactly
    #: the guarantee it was added for, and losing it silently.
    assert "ck_submission_period" in clauses, (
        "alembic upgrade head did not create ck_submission_period. Without it "
        "a row may carry half an interval, an interval that runs backwards, "
        "time_frame='custom' with no dates, or dates with no time_frame at "
        f"all. Found: {sorted(clauses)}"
    )
    for column in ("period_start", "period_end", "time_frame"):
        assert column in clauses["ck_submission_period"], (
            f"ck_submission_period no longer mentions {column}; the "
            "contradiction rule spans all three columns and a clause that "
            "dropped one of them would still be a constraint that exists, is "
            "named right and enforces less than it says. "
            f"Clause: {clauses['ck_submission_period']}"
        )

    #: 0019's two CHECKs (contract §2.2, v1.71), and the blind spot a third
    #: time: tests/db/test_equivalence_bands.py proves both refusals
    #: behaviourally off the create_all() schema and would stay green if
    #: either op.execute in 0019 were dropped.
    assert "ck_equivalence_band_needs_family" in clauses, (
        "alembic upgrade head did not create ck_equivalence_band_needs_family. "
        "Without it a staff member can set a band on a row with no family, "
        "where selection never looks at it -- a minimum that is stored, "
        f"audited and can never fire. Found: {sorted(clauses)}"
    )
    for column in ("family", "min_value", "max_value"):
        assert column in clauses["ck_equivalence_band_needs_family"], (
            f"ck_equivalence_band_needs_family no longer mentions {column}; "
            "the rule spans all three and a clause that dropped one would "
            "still be a constraint that exists, is named right and enforces "
            f"less than it says. Clause: "
            f"{clauses['ck_equivalence_band_needs_family']}"
        )
    assert "ck_equivalence_band_ordered" in clauses, (
        "alembic upgrade head did not create ck_equivalence_band_ordered. "
        "Without it a band may be inverted or empty, which makes a rung that "
        f"can never be chosen look exactly like one nobody added. Found: "
        f"{sorted(clauses)}"
    )
    for column in ("min_value", "max_value"):
        assert column in clauses["ck_equivalence_band_ordered"], (
            f"ck_equivalence_band_ordered no longer mentions {column}. "
            f"Clause: {clauses['ck_equivalence_band_ordered']}"
        )


@pytest.mark.db
def test_the_chain_gives_the_submission_its_period_columns(migrated_engine):
    """Contract §2.3, v1.67. `0018`'s two columns, read back out of MySQL.

    `compare_metadata` would catch a missing column, so this is not closing a
    blind spot -- it is pinning the two things about these columns that a
    diff would happily agree with and that the design depends on:

    * **`DATETIME`, never a `FLOAT` or a `DOUBLE`.** §1.2 prohibits both
      outright. The temptation is not the instants themselves but the
      *duration* between them, which is the natural shape for a float and
      which this schema deliberately does not store -- nothing derives a
      length from the period, because §2.3 forbids computing with it at all.
    * **Nullable.** Absence is how "no period was given" is represented, for
      every row written before this revision and for every visitor who leaves
      step 5 at "Not stated". A NOT NULL column here would have needed a
      backfill, and there is no instant that could be back-filled honestly.
    """
    with migrated_engine.connect() as conn:
        columns = {
            row[0]: (row[1], row[2], row[3])
            for row in conn.execute(text("""
                SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, DATETIME_PRECISION
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'submission'
            """)).all()
        }

    for column in ("period_start", "period_end"):
        assert column in columns, (
            f"alembic upgrade head did not add submission.{column}. "
            f"Columns found: {sorted(columns)}"
        )
        data_type, nullable, precision = columns[column]
        assert data_type == "datetime", (
            f"submission.{column} is {data_type}, not datetime. §1.2 "
            "prohibits FLOAT and DOUBLE, and a period is two instants rather "
            "than a duration for that reason among others"
        )
        assert nullable == "YES", (
            f"submission.{column} is NOT NULL. Absence is how 'no period was "
            "given' is stored (§2.3); a NOT NULL column would demand a "
            "backfill nobody can supply honestly"
        )
        assert precision == 0, (
            f"submission.{column} carries {precision} digits of "
            "fractional-seconds precision. The wire drops microseconds "
            "before the value is written (api.schemas.PricingOptions) "
            "precisely because the column does not keep them"
        )


def test_sector_descriptions_are_the_shipped_seeds():
    """One wording for a supply-chain stage, held in three places.

    Contract v1.96. Step 1's cards are client-facing copy, and it lives in
    `admin/seed.py` for a fresh deployment, in `0021` for the deployments that
    already exist, and in `tests/fixtures/taxonomy.json` for C's and D's
    development. Three copies of a sentence drift, and this one has drifted
    already in a worse form: the fixture carried a `details` string per sector
    that no version of the contract ever defined, so the panel repeated the
    description against the real API and printed *Additional details have not
    been supplied.* against a live database for three weeks.

    `tests/api/test_fixture_consistency.py` ties the fixture to the seed. This
    ties the migration to both, and it is deliberately an exact string
    comparison rather than a similarity: the point of failure is somebody
    improving one copy.

    Needs no database -- it reads three Python objects.
    """
    import importlib.util
    import json
    import pathlib

    from admin.seed import SECTORS

    root = pathlib.Path(__file__).resolve().parents[1]
    fixture = json.loads((root / "tests/fixtures/taxonomy.json").read_text(encoding="utf-8"))
    spec = importlib.util.spec_from_file_location(
        "_m0021", root / "alembic/versions/0021_sector_description.py"
    )
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)

    seed = {code: description for code, _name, _order, description in SECTORS}
    migration = dict(revision.SECTOR_DESCRIPTIONS)
    fixtured = {row["code"]: row["description"] for row in fixture["sectors"]}

    assert set(seed) == set(migration) == set(fixtured), (
        "the three copies do not even cover the same sectors: "
        f"seed={sorted(seed)} migration={sorted(migration)} fixture={sorted(fixtured)}"
    )
    disagree = {
        code: {"seed": seed[code], "migration": migration[code], "fixture": fixtured[code]}
        for code in sorted(seed)
        if not seed[code] == migration[code] == fixtured[code]
    }
    assert not disagree, (
        "a sector's description has been improved in one place and not the "
        f"others, so a deployment and a fixture now say different things: {disagree}"
    )

    #: Every one of them must survive `sectorCopy`'s split with something left
    #: for the panel, or the card carries the whole paragraph and the *Details*
    #: button is not drawn -- which is legal behaviour and not what any of
    #: these six are written for.
    import re

    for code, text_value in sorted(seed.items()):
        split = re.match(r"^([\s\S]*?[.!?])\s+([\s\S]+)$", text_value)
        assert split, (
            f"{code}'s description is one sentence, so step 1 draws no Details "
            f"panel for it: {text_value!r}"
        )
        assert len(split.group(1)) <= 120, (
            f"{code}'s first sentence is {len(split.group(1))} characters and the "
            "card has room for one line of it at 1280px: "
            f"{split.group(1)!r}"
        )


@pytest.mark.db
def test_0021_seeds_an_existing_sector_and_never_corrects_one(database_url_root):
    """Contract v1.96. **The half of `0021` that is data**, on 0020's shape.

    Every other migration test in this module runs the chain against an EMPTY
    database, where an UPDATE over `sector` matches no rows and a dropped one
    is indistinguishable from a working one. So this runs the chain in two
    halves with rows in between, which is the only arrangement in which the
    seed is observable at all.

    Three properties:

    * a sector that existed before `0021` comes out carrying its description,
      rather than leaving step 1 with an empty line beside every title and
      *Additional details have not been supplied.* in every panel -- which is
      the state the owner reported and the reason this revision exists;
    * the description is the shipped wording, not a placeholder, checked by
      the sentence the panel is supposed to open onto;
    * a description somebody had already written is **left alone**. The UPDATE
      tests `description IS NULL`, so it seeds and never corrects, which is
      0015's rule for `unit_preset` and 0020's for `equivalence` applied
      again: an operator who re-runs the chain must not lose work.

    Its own scratch database, like the tests above, so a chain left part-way by
    a failure here cannot reach another test.
    """
    root = create_engine(database_url_root, future=True)
    with root.connect() as conn:
        conn.execute(text("DROP DATABASE IF EXISTS kaicalc_sectortest"))
        conn.execute(text("CREATE DATABASE kaicalc_sectortest"))
        conn.commit()

    url = database_url_root.rsplit("/", 1)[0] + "/kaicalc_sectortest"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    engine = create_engine(url, future=True)
    mine = "Our own wording for this stage, which nothing may overwrite."
    try:
        #: Stop one revision short, so the rows below are written by a chain
        #: that has not yet run the UPDATE -- which is every deployed database
        #: on the day this lands. The column itself is as old as 0004.
        command.upgrade(cfg, "0020")
        with engine.begin() as conn:
            for code, name, order in (
                ("primary_production", "Primary production", 10),
                ("consumer_household", "Households", 40),
            ):
                conn.execute(
                    text(
                        "INSERT INTO sector (code, name, description, sort_order, "
                        "active) VALUES (:c, :n, NULL, :o, 1)"
                    ),
                    {"c": code, "n": name, "o": order},
                )

        command.upgrade(cfg, "head")

        #: A description written by hand AFTER the seed ran, then the chain
        #: re-run: `alembic upgrade head` is idempotent and an operator may do
        #: it twice.
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE sector SET description = :d WHERE code = 'consumer_household'"),
                {"d": mine},
            )
        command.upgrade(cfg, "head")

        with engine.connect() as conn:
            rows = dict(conn.execute(text("SELECT code, description FROM sector")).all())
    finally:
        engine.dispose()
        with root.connect() as conn:
            conn.execute(text("DROP DATABASE IF EXISTS kaicalc_sectortest"))
            conn.commit()
        root.dispose()

    seeded = rows["primary_production"]
    assert seeded, (
        "0021 seeded nothing, so step 1 on a deployed site shows an empty line "
        "beside every sector title and `Additional details have not been "
        "supplied.` in every Details panel"
    )
    assert "Typical losses" in seeded, (
        "the seeded description is not the shipped wording -- the panel is "
        f"supposed to open onto what is typically lost at this stage: {seeded!r}"
    )

    kept = rows["consumer_household"]
    assert kept == mine, (
        "re-running the chain overwrote a description somebody had written. "
        f"The UPDATE must be conditional on `description IS NULL`: {kept!r}"
    )


@pytest.mark.db
def test_0020_seeds_an_existing_row_and_never_corrects_one(database_url_root):
    """Contract v1.80, issue #127. **The half of `0020` that is data.**

    `compare_metadata` sees the column; nothing in this module sees the
    UPDATE beside it, and nothing else in the suite could -- every other
    migration test runs the chain against an EMPTY database, where an UPDATE
    over `equivalence` matches no rows and a dropped one is indistinguishable
    from a working one. So this runs the chain in two halves with rows in
    between, which is the only arrangement in which the seed is observable at
    all.

    Three properties, and the third is why the UPDATE is conditional:

    * a row that existed before `0020` comes out carrying a sentence, rather
      than showing an empty disclosure on every card of a deployed site;
    * the sentence is **not** the row's `source_note` -- the two are different
      claims, and the long one is what #127 took off the results page;
    * a row whose sentence somebody had already written is **left alone**. The
      UPDATE tests `description IS NULL`, so it seeds and never corrects, which
      is 0015's rule for `unit_preset` applied again: an operator who re-runs
      the chain must not lose work.

    Its own scratch database, like `test_the_head_revision_round_trips`, so a
    chain left part-way by a failure here cannot reach another test.
    """
    root = create_engine(database_url_root, future=True)
    with root.connect() as conn:
        conn.execute(text("DROP DATABASE IF EXISTS kaicalc_seedtest"))
        conn.execute(text("CREATE DATABASE kaicalc_seedtest"))
        conn.commit()

    url = database_url_root.rsplit("/", 1)[0] + "/kaicalc_seedtest"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    engine = create_engine(url, future=True)
    note = "PLACEHOLDER. Open item O-3; the basis for a shower is not settled."
    mine = "Our own sentence, which nothing may overwrite."
    try:
        #: Stop one revision short, so the rows below are written by a schema
        #: that has never heard of the column -- which is every deployed
        #: database on the day this lands.
        command.upgrade(cfg, "0019")
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO factor_set (version_label, status, is_mock, "
                "item_level_enabled) VALUES ('SEEDTEST', 'draft', 1, 0)"
            ))
            conn.execute(text(
                "INSERT INTO destination_group (code, name, is_waste, "
                "sort_order, active) VALUES ('seedtest', 'Seed test', 1, 1, 1)"
            ))
            conn.execute(text(
                "INSERT INTO metric (code, name, unit, display_precision, "
                "sort_order, active) VALUES ('water', 'Water', 'L', 1, 1, 1)"
            ))
            set_id = conn.execute(
                text("SELECT id FROM factor_set WHERE version_label = 'SEEDTEST'")
            ).scalar_one()
            metric_id = conn.execute(
                text("SELECT id FROM metric WHERE code = 'water'")
            ).scalar_one()
            for code, label in (("showers", "Equivalent to {value} showers"),
                                ("meals", "Equivalent to {value} meals")):
                conn.execute(
                    text(
                        "INSERT INTO equivalence (factor_set_id, code, name, "
                        "source_metric_id, value_per_unit, label_template, "
                        "source_note, sort_order, active) VALUES "
                        "(:s, :c, :c, :m, 1.0, :l, :n, 1, 1)"
                    ),
                    {"s": set_id, "c": code, "m": metric_id, "l": label, "n": note},
                )

        command.upgrade(cfg, "head")

        #: A sentence written by hand AFTER the column exists, then the chain
        #: re-run: `alembic upgrade head` is idempotent and an operator may do
        #: it twice.
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE equivalence SET description = :d WHERE code = 'meals'"),
                {"d": mine},
            )
        command.upgrade(cfg, "head")

        with engine.connect() as conn:
            rows = {
                row[0]: (row[1], row[2])
                for row in conn.execute(text(
                    "SELECT code, description, source_note FROM equivalence"
                )).all()
            }
    finally:
        engine.dispose()
        with root.connect() as conn:
            conn.execute(text("DROP DATABASE IF EXISTS kaicalc_seedtest"))
            conn.commit()
        root.dispose()

    seeded, seeded_note = rows["showers"]
    assert seeded, (
        "0020 added the column and seeded nothing, so every tangible-"
        "equivalence card on a deployed site opens onto its figures and no "
        "sentence"
    )
    assert seeded != seeded_note, (
        "0020 seeded the row's own source_note as its description. They are "
        f"different claims and the long one is the one #127 removed: {seeded!r}"
    )
    assert "ten-minute" in seeded and "nine litres" in seeded, (
        "the `showers` sentence has lost the assumption its source_note "
        f"carries. That caveat is the whole reason this row needed one: {seeded!r}"
    )

    kept, _ = rows["meals"]
    assert kept == mine, (
        "re-running the chain overwrote a sentence somebody had written. The "
        f"UPDATE must be conditional on `description IS NULL`: {kept!r}"
    )


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
