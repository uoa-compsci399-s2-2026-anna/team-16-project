"""The item level's schema, inert. Contract §2.1, §2.2, §2.3 (v1.54).

Step 2.5 of `.superpowers/sdd/2026-09-17-food-granularity/design.md` asks a
visitor which *food* they wasted, not only which category. This file proves the
four schema changes that makes possible, and — just as importantly — proves
that none of them changes what the calculator does before a single `food_item`
row exists.

**MySQL, not SQLite**, for the reason `tests/db/test_submissions.py` gives at
length: two of the four changes are functional `COALESCE` unique indexes, which
SQLite has no equivalent of, and a NULL-distinctness test on SQLite proves
nothing. The `session` fixture here is `tests/conftest.py`'s `kaicalc_test`
scratch database, built by `create_all()` off the models.

The migration chain's own copy of the same DDL is a second, independent path to
this schema and is proven separately in `tests/test_migrations.py`. Nothing in
this file runs migration DDL, so deleting the `op.execute` lines from
`alembic/versions/0017_food_item_level.py` would leave every test here green.
"""

from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from db.models import (
    Destination,
    DestinationGroup,
    FactorSet,
    FactorSetStatus,
    FactorUpstream,
    FoodCategory,
    FoodItem,
    Metric,
    Sector,
    Submission,
    SubmissionEntry,
    utcnow,
)

pytestmark = pytest.mark.db


def _taxonomy(session):
    """The rows an entry or an upstream factor has to point at."""
    group = DestinationGroup(code="fi_disposal", name="Disposal", is_waste=True)
    sector = Sector(code="fi_processing", name="Processing")
    dairy = FoodCategory(code="fi_dairy", name="Dairy")
    fruit = FoodCategory(code="fi_fruit", name="Fruit")
    metric = Metric(code="fi_co2e", name="Greenhouse gases", unit="kg CO2e")
    factor_set = FactorSet(version_label="fi-MOCK-v0",
                           status=FactorSetStatus.published, is_mock=True)
    session.add_all([group, sector, dairy, fruit, metric, factor_set])
    session.flush()
    landfill = Destination(group_id=group.id, code="fi_landfill", name="Landfill")
    cheese = FoodItem(code="fi_cheese", name="Cheese", food_category_id=dairy.id)
    butter = FoodItem(code="fi_butter", name="Butter", food_category_id=dairy.id)
    session.add_all([landfill, cheese, butter])
    session.flush()
    return dict(factor_set=factor_set, sector=sector, dairy=dairy, fruit=fruit,
                metric=metric, landfill=landfill, cheese=cheese, butter=butter)


def _submission(session, factor_set, token=None):
    row = Submission(token=token, factor_set_id=factor_set.id, gwp_horizon=100,
                     created_at=utcnow(), updated_at=utcnow())
    session.add(row)
    session.flush()
    return row


# --- food_item, the global taxonomy table (§2.1) -----------------------------


def test_food_item_follows_the_taxonomy_conventions(session):
    """`code` unique, `active` defaulting TRUE, `sort_order` defaulting 0.

    Every table in §2.1 carries these three and the panel relies on all of
    them: `code` is the cross-layer identifier, and a taxonomy row is
    deactivated rather than deleted so that a historical submission stays
    resolvable.
    """
    rows = _taxonomy(session)
    cheese = session.get(FoodItem, rows["cheese"].id)

    assert cheese.active is True
    assert cheese.sort_order == 0
    assert cheese.food_category_id == rows["dairy"].id
    assert str(cheese) == "fi_cheese — Cheese"

    session.add(FoodItem(code="fi_cheese", name="Cheese again",
                         food_category_id=rows["dairy"].id))
    with pytest.raises(IntegrityError):
        session.flush()


def test_a_food_item_must_name_a_category(session):
    """`food_category_id` is NOT NULL. §3.1 of the spec: "Every item belongs to
    exactly one category". An orphan item has no factor to fall back to, which
    is the whole of what makes partial item coverage safe (design §2)."""
    _taxonomy(session)
    session.add(FoodItem(code="fi_orphan", name="Orphan", food_category_id=None))
    with pytest.raises(IntegrityError):
        session.flush()


def test_food_item_is_not_a_child_of_the_factor_set(session):
    """§6.1: *a factor set brings factors, not a vocabulary.*

    The decisive argument is reproducibility. `factor_set_id` carries
    `ondelete="CASCADE"` everywhere it appears, so a set-scoped item row would
    let deleting a draft take the meaning of a stored submission with it — a
    2026 submission must still render "cheese" in 2029, exactly as
    `food_category_id` already does.
    """
    assert "factor_set_id" not in FoodItem.__table__.columns

    from admin.factor_lifecycle import CHILD_MODELS

    assert len(CHILD_MODELS) == 5, (
        "CHILD_MODELS must stay at five: food_item is global taxonomy, not a "
        f"sixth child kind. Found {[m.__tablename__ for m in CHILD_MODELS]}"
    )
    assert FoodItem not in CHILD_MODELS


# --- factor_set.item_level_enabled (§2.2) ------------------------------------


def test_the_item_level_switch_is_off_by_default(session):
    """A set nobody has released step 2.5 on must not release it. FALSE is the
    only safe default: the flag's own guard (a set with no item-level rows may
    not enable it) is a later landing, and a TRUE default would make every
    existing set claim item-level data it does not have."""
    rows = _taxonomy(session)
    assert session.get(FactorSet, rows["factor_set"].id).item_level_enabled is False


# --- factor_upstream.food_item_id (§2.2) -------------------------------------


def _upstream(rows, *, food_item=None, destination=None, value="1.0"):
    return FactorUpstream(
        factor_set_id=rows["factor_set"].id,
        sector_id=rows["sector"].id,
        food_category_id=rows["dairy"].id,
        food_item_id=food_item.id if food_item is not None else None,
        destination_id=destination.id if destination is not None else None,
        metric_id=rows["metric"].id,
        value_per_kg=Decimal(value),
    )


def test_an_item_factor_carries_both_columns(session):
    """`food_category_id` stays NOT NULL. An item row names the item *and* the
    category it refines — that is what lets a missing item fall through to the
    category average rather than to zero (design §2)."""
    rows = _taxonomy(session)
    session.add(_upstream(rows, food_item=rows["cheese"]))
    session.flush()

    row = session.scalar(
        select(FactorUpstream).where(FactorUpstream.food_item_id == rows["cheese"].id)
    )
    assert row.food_category_id == rows["dairy"].id


def test_an_upstream_factor_may_not_name_an_item_without_a_category(session):
    """`food_category_id` is NOT NULL, so this is refused by the column and not
    by a constraint — asserted anyway, because the four-candidate lookup
    (design §2) falls back through the category and a row without one could
    never be reached."""
    _taxonomy(session)
    assert FactorUpstream.__table__.c.food_category_id.nullable is False


def test_an_item_row_and_its_category_row_coexist(session):
    """The whole point. Both must be storable for one
    `(sector, food_category, destination, metric)` or the fallback chain has
    nothing to fall back *to*."""
    rows = _taxonomy(session)
    session.add_all([
        _upstream(rows, food_item=None, value="1.0"),
        _upstream(rows, food_item=rows["cheese"], value="2.0"),
        _upstream(rows, food_item=rows["butter"], value="3.0"),
    ])
    session.flush()

    assert session.scalar(
        select(func.count()).select_from(FactorUpstream)
    ) == 3


def test_two_generic_upstream_rows_are_still_refused(session):
    """The O-7 guard, unchanged. Two rows generic in *both* nullable dimensions
    and the fallback picks one nondeterministically — the same input returning
    a different net benefit run to run, with nothing in the logs."""
    rows = _taxonomy(session)
    session.add(_upstream(rows, value="1.0"))
    session.flush()
    session.add(_upstream(rows, value="2.0"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_two_rows_for_the_same_item_at_the_same_destination_are_refused(session):
    """The new nullable dimension brings the same NULL trap with it, so
    `uq_factor_upstream_generic` has to collapse `food_item_id` as well as
    `destination_id`. Collapsing only one leaves the other's duplicates legal
    — the defect v1.31 recorded on `factor_downstream`, one dimension over."""
    rows = _taxonomy(session)
    session.add(_upstream(rows, food_item=rows["cheese"], value="1.0"))
    session.flush()
    session.add(_upstream(rows, food_item=rows["cheese"], value="2.0"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_the_upstream_functional_index_collapses_both_nullable_columns(session):
    """Names the mechanism, not the behaviour.

    `any("coalesce" in expr)` is what this used to be able to say, and it would
    pass on an index that collapsed the newer column and dropped the older one
    — an index that exists, is unique, contains a COALESCE, and silently stops
    enforcing half of what it was written for. Each column is named.
    """
    _taxonomy(session)
    rows = session.execute(text("""
        SELECT COLUMN_NAME, EXPRESSION, NON_UNIQUE
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'factor_upstream'
          AND INDEX_NAME = 'uq_factor_upstream_generic'
        ORDER BY SEQ_IN_INDEX
    """)).all()

    assert rows, "uq_factor_upstream_generic does not exist"
    assert all(row.NON_UNIQUE == 0 for row in rows), "the index is not unique"
    expressions = [row.EXPRESSION.lower() for row in rows if row.EXPRESSION]
    for column in ("destination_id", "food_item_id"):
        assert any("coalesce" in expr and column in expr for expr in expressions), (
            f"uq_factor_upstream_generic has no COALESCE({column}, ...) key "
            f"part, so NULLs in {column} still compare distinct. Key parts: {rows}"
        )


# --- submission_entry.food_item_id (§2.3) ------------------------------------


def test_an_entry_stores_both_columns_when_an_item_is_named(session):
    """Decision 5, and §5.4 is why. `food_category_id IS NULL` already means
    *the user did not break their waste down by type*; reusing it for *the user
    gave a finer breakdown* would file every submission that named Cheese into
    the statistics page's "not broken down" bucket — the precise opposite of
    what happened."""
    rows = _taxonomy(session)
    submission = _submission(session, rows["factor_set"])
    session.add(SubmissionEntry(
        submission_id=submission.id, sector_id=rows["sector"].id,
        food_category_id=rows["dairy"].id, food_item_id=rows["cheese"].id,
        sort_order=0,
    ))
    session.flush()

    entry = session.scalar(select(SubmissionEntry))
    assert entry.food_category_id == rows["dairy"].id
    assert entry.food_item_id == rows["cheese"].id
    assert entry.food_item.code == "fi_cheese"


def test_an_entry_naming_an_item_without_a_category_is_refused(session):
    """The CHECK constraint. `(food_category_id IS NULL AND food_item_id IS NOT
    NULL)` is the one state that would break `unspecified`: the row would be
    counted as "no breakdown" by §5.4's `by_food_category` while carrying the
    most specific answer the calculator can take.

    `OperationalError`, *not* `IntegrityError`: MySQL reports a CHECK violation
    as error 3819, which SQLAlchemy maps to OperationalError — only uniqueness
    and foreign-key failures arrive as IntegrityError. The constraint is named
    in the assertion so that this cannot pass on some *other* refusal.
    """
    rows = _taxonomy(session)
    submission = _submission(session, rows["factor_set"])
    session.add(SubmissionEntry(
        submission_id=submission.id, sector_id=rows["sector"].id,
        food_category_id=None, food_item_id=rows["cheese"].id, sort_order=0,
    ))
    with pytest.raises(OperationalError) as caught:
        session.flush()

    assert "ck_submission_entry_item_has_category" in str(caught.value)


def test_the_three_legal_states_of_an_entry_all_persist(session):
    """No breakdown, category only, and category-plus-item. The CHECK must
    refuse exactly one of the four combinations and no more."""
    rows = _taxonomy(session)
    submission = _submission(session, rows["factor_set"])
    session.add_all([
        SubmissionEntry(submission_id=submission.id, sector_id=rows["sector"].id,
                        food_category_id=None, food_item_id=None, sort_order=0),
        SubmissionEntry(submission_id=submission.id, sector_id=rows["sector"].id,
                        food_category_id=rows["dairy"].id, food_item_id=None,
                        sort_order=1),
        SubmissionEntry(submission_id=submission.id, sector_id=rows["sector"].id,
                        food_category_id=rows["dairy"].id,
                        food_item_id=rows["cheese"].id, sort_order=2),
    ])
    session.flush()

    assert session.scalar(select(func.count()).select_from(SubmissionEntry)) == 3


def test_two_leaves_under_one_category_coexist(session):
    """The leaf rule (spec §3.3): each ticked item is its own row. Cheese and
    Butter share a sector and a category and must not collide."""
    rows = _taxonomy(session)
    submission = _submission(session, rows["factor_set"])
    session.add_all([
        SubmissionEntry(submission_id=submission.id, sector_id=rows["sector"].id,
                        food_category_id=rows["dairy"].id,
                        food_item_id=rows["cheese"].id, sort_order=0),
        SubmissionEntry(submission_id=submission.id, sector_id=rows["sector"].id,
                        food_category_id=rows["dairy"].id,
                        food_item_id=rows["butter"].id, sort_order=1),
    ])
    session.flush()

    assert session.scalar(select(func.count()).select_from(SubmissionEntry)) == 2


def test_the_same_item_twice_in_one_submission_is_refused(session):
    """Duplicate leaves are the four-column constraint's job, and they are not
    cosmetic: §5.4 aggregates per entry, so one user's single answer stored
    twice is counted twice in the public statistics."""
    rows = _taxonomy(session)
    submission = _submission(session, rows["factor_set"])
    session.add(SubmissionEntry(
        submission_id=submission.id, sector_id=rows["sector"].id,
        food_category_id=rows["dairy"].id, food_item_id=rows["cheese"].id,
        sort_order=0,
    ))
    session.flush()
    session.add(SubmissionEntry(
        submission_id=submission.id, sector_id=rows["sector"].id,
        food_category_id=rows["dairy"].id, food_item_id=rows["cheese"].id,
        sort_order=1,
    ))
    with pytest.raises(IntegrityError):
        session.flush()


def test_a_category_level_leaf_and_an_item_level_leaf_under_it_coexist(session):
    """"Dairy, unspecified item" and "Dairy, cheese" are different answers.
    `COALESCE(food_item_id, 0)` only ever collides with another NULL, because
    an AUTO_INCREMENT `food_item.id` is never 0."""
    rows = _taxonomy(session)
    submission = _submission(session, rows["factor_set"])
    session.add_all([
        SubmissionEntry(submission_id=submission.id, sector_id=rows["sector"].id,
                        food_category_id=rows["dairy"].id, food_item_id=None,
                        sort_order=0),
        SubmissionEntry(submission_id=submission.id, sector_id=rows["sector"].id,
                        food_category_id=rows["dairy"].id,
                        food_item_id=rows["cheese"].id, sort_order=1),
    ])
    session.flush()

    assert session.scalar(select(func.count()).select_from(SubmissionEntry)) == 2


def test_two_category_level_entries_are_still_refused(session):
    """The v1.2 behaviour, unchanged by the new dimension. Adding
    `COALESCE(food_item_id, 0)` to the index must not weaken what it already
    enforced — two "Dairy at processing" rows are still one user's answer
    stored twice."""
    rows = _taxonomy(session)
    submission = _submission(session, rows["factor_set"])
    session.add(SubmissionEntry(submission_id=submission.id,
                                sector_id=rows["sector"].id,
                                food_category_id=rows["dairy"].id, sort_order=0))
    session.flush()
    session.add(SubmissionEntry(submission_id=submission.id,
                                sector_id=rows["sector"].id,
                                food_category_id=rows["dairy"].id, sort_order=1))
    with pytest.raises(IntegrityError):
        session.flush()


def test_the_entry_functional_index_collapses_both_nullable_columns(session):
    """Same assertion as the upstream one above, on the second of the two
    two-nullable-dimension indexes this landing creates. Each column named."""
    _taxonomy(session)
    rows = session.execute(text("""
        SELECT COLUMN_NAME, EXPRESSION, NON_UNIQUE
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'submission_entry'
          AND INDEX_NAME = 'uq_submission_entry_generic'
        ORDER BY SEQ_IN_INDEX
    """)).all()

    assert rows, "uq_submission_entry_generic does not exist"
    assert all(row.NON_UNIQUE == 0 for row in rows), "the index is not unique"
    expressions = [row.EXPRESSION.lower() for row in rows if row.EXPRESSION]
    for column in ("food_category_id", "food_item_id"):
        assert any("coalesce" in expr and column in expr for expr in expressions), (
            f"uq_submission_entry_generic has no COALESCE({column}, ...) key "
            f"part, so NULLs in {column} still compare distinct. Key parts: {rows}"
        )
