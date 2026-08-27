"""The three submission tables of contract §2.3, against real MySQL.

`submission` / `submission_entry` / `submission_line`. One submission carries
several `(sector, food_category)` entries, because a food business has waste
at more than one point in the supply chain and each point draws a different
upstream factor — so they cannot share one set of lines.

**This file runs against MySQL, not SQLite.** It uses the `session` fixture
from `tests/conftest.py` (the `kaicalc_test` scratch database), not
`seeded_session` from `tests/support/sqlite.py`. That is deliberate and it is
the whole point of the `uq_submission_entry_generic` tests below: SQLite
treats NULLs as distinct in a UNIQUE index *and* has no MySQL functional
index, so on SQLite those tests would prove nothing at all. See
`tests/support/sqlite.py`'s module docstring — in this suite a file's
directory does not decide which engine it runs against, its fixtures do.

The migration chain's own copy of the same index and the same CHECK
constraints is proven separately, in `tests/test_migrations.py`: this file
builds its schema with `create_all()` off the models and never runs migration
DDL, so deleting the `op.execute` from `alembic/versions/0008_submissions.py`
would leave every test here green.
"""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from db.models import (
    Destination,
    DestinationGroup,
    FactorSet,
    FactorSetStatus,
    FoodCategory,
    Scenario,
    Sector,
    Submission,
    SubmissionEntry,
    SubmissionLine,
    utcnow,
)


def _prereqs(session):
    """The taxonomy and factor-set rows a submission has to point at."""
    group = DestinationGroup(code="sub_disposal", name="Disposal", is_waste=True)
    sector = Sector(code="sub_processing", name="Processing")
    dairy = FoodCategory(code="sub_dairy", name="Dairy")
    factor_set = FactorSet(
        version_label="sub-MOCK-v0", status=FactorSetStatus.published, is_mock=True
    )
    session.add_all([group, sector, dairy, factor_set])
    session.flush()
    landfill = Destination(group_id=group.id, code="sub_landfill", name="Landfill")
    session.add(landfill)
    session.flush()
    return factor_set, sector, dairy, landfill


def _submission(session, factor_set, token="4d0f8e1e-0000-4000-8000-000000000000"):
    now = utcnow()
    row = Submission(
        token=token,
        token_expires_at=now + timedelta(hours=1),
        created_at=now,
        updated_at=now,
        factor_set_id=factor_set.id,
        gwp_horizon=100,
    )
    session.add(row)
    session.flush()
    return row


# --- shape ------------------------------------------------------------------


def test_the_sector_and_category_live_on_the_entry_not_the_submission():
    """Contract §2.3: "`sector_id` and `food_category_id` live on
    `submission_entry`, not here: one submission carries several, each with
    its own factors."

    Needs no database — it reads the mapped tables. The old single-entry
    shape had both columns on `submission`, which silently caps a submission
    at one supply-chain stage.
    """
    assert "sector_id" not in Submission.__table__.columns
    assert "food_category_id" not in Submission.__table__.columns
    assert "sector_id" in SubmissionEntry.__table__.columns
    assert "food_category_id" in SubmissionEntry.__table__.columns


def test_the_gwp_horizon_stays_on_the_submission():
    """Contract §2.3: "Applies to the whole submission" — §6.2 sends it once,
    outside `entries[]`, so it must not be per-entry."""
    assert "gwp_horizon" in Submission.__table__.columns
    assert "gwp_horizon" not in SubmissionEntry.__table__.columns


def test_a_line_hangs_off_an_entry_not_a_submission():
    """Contract §2.3: `submission_line.submission_entry_id`. Lines keyed
    directly on the submission cannot say which sector they belong to."""
    assert "submission_entry_id" in SubmissionLine.__table__.columns
    assert "submission_id" not in SubmissionLine.__table__.columns


def test_no_column_in_the_three_tables_is_a_float():
    """Global rule: DECIMAL everywhere; FLOAT and DOUBLE are prohibited.
    `qty_kg` is the mass every impact number is multiplied by."""
    for table in (Submission.__table__, SubmissionEntry.__table__,
                  SubmissionLine.__table__):
        for column in table.columns:
            assert "FLOAT" not in repr(column.type).upper(), (table.name, column.name)
            assert "DOUBLE" not in repr(column.type).upper(), (table.name, column.name)


# --- the entry uniqueness rules ---------------------------------------------


@pytest.mark.db
def test_the_same_sector_and_category_cannot_appear_twice_in_one_submission(session):
    """Contract §2.3 UNIQUE(submission_id, sector_id, food_category_id), and
    §6.2's "No duplicate (sector, food_category) across entries"."""
    factor_set, sector, dairy, _ = _prereqs(session)
    submission = _submission(session, factor_set)
    session.add(SubmissionEntry(submission_id=submission.id, sector_id=sector.id,
                                food_category_id=dairy.id, sort_order=0))
    session.flush()

    session.add(SubmissionEntry(submission_id=submission.id, sector_id=sector.id,
                                food_category_id=dairy.id, sort_order=1))

    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.db
def test_two_entries_with_no_food_category_are_refused(session):
    """**The test this table exists to have.** Contract §2.3: "The uniqueness
    constraint has the same MySQL NULL caveat as `factor_downstream` (§2.2):
    a nullable column in a UNIQUE key does not prevent duplicates, because
    NULLs compare distinct. Use a functional index over
    COALESCE(food_category_id, 0)."

    B found this trap in `factor_downstream` and §2.2 credits it to her by
    name. `submission_entry` has the same shape and the same failure: without
    the functional index MySQL admits unlimited "no category breakdown"
    entries for one sector, `POST /calculate` writes them all, and §5.4's
    `by_sector` aggregation then counts one user's single answer several
    times — wrong numbers in the public statistics, no error, nothing in the
    logs.

    On SQLite this passes for the wrong reason and on SQLite without the
    index it fails for the wrong reason, which is why this file runs on
    MySQL.
    """
    factor_set, sector, _, _ = _prereqs(session)
    submission = _submission(session, factor_set)
    session.add(SubmissionEntry(submission_id=submission.id, sector_id=sector.id,
                                food_category_id=None, sort_order=0))
    session.flush()

    session.add(SubmissionEntry(submission_id=submission.id, sector_id=sector.id,
                                food_category_id=None, sort_order=1))

    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.db
def test_a_specific_and_a_generic_entry_coexist(session):
    """The functional index must not overshoot: "dairy at processing" and
    "processing, no breakdown" are different answers and both are legal.
    COALESCE(food_category_id, 0) only collides with another NULL, because
    an AUTO_INCREMENT `food_category.id` is never 0.
    """
    factor_set, sector, dairy, _ = _prereqs(session)
    submission = _submission(session, factor_set)

    session.add_all([
        SubmissionEntry(submission_id=submission.id, sector_id=sector.id,
                        food_category_id=None, sort_order=0),
        SubmissionEntry(submission_id=submission.id, sector_id=sector.id,
                        food_category_id=dairy.id, sort_order=1),
    ])
    session.flush()

    assert session.scalar(
        select(func.count()).select_from(SubmissionEntry)
        .where(SubmissionEntry.submission_id == submission.id)
    ) == 2


@pytest.mark.db
def test_two_submissions_may_each_carry_the_same_generic_entry(session):
    """The index is scoped to one submission. Two different users answering
    "processing, no breakdown" must not collide with each other — that would
    make the second calculation on the site fail outright."""
    factor_set, sector, _, _ = _prereqs(session)
    first = _submission(session, factor_set, token="a" * 8 + "-0000-4000-8000-000000000000")
    second = _submission(session, factor_set, token="b" * 8 + "-0000-4000-8000-000000000000")

    session.add_all([
        SubmissionEntry(submission_id=first.id, sector_id=sector.id,
                        food_category_id=None, sort_order=0),
        SubmissionEntry(submission_id=second.id, sector_id=sector.id,
                        food_category_id=None, sort_order=0),
    ])
    session.flush()

    assert session.scalar(select(func.count()).select_from(SubmissionEntry)) == 2


@pytest.mark.db
def test_the_functional_index_is_what_the_create_all_schema_actually_has(session):
    """Names the mechanism, not just the behaviour.

    Without this, replacing the functional index with a plain
    UNIQUE(submission_id, sector_id, food_category_id) would still fail
    `test_two_entries_with_no_food_category_are_refused` on some future
    engine that treats NULLs as equal, and pass here — the reverse of what
    is wanted. Asserted against `information_schema` for the same reason
    `tests/test_migrations.py` does it: a COALESCE key part comes back with
    `COLUMN_NAME IS NULL` and the expression in `EXPRESSION`.
    """
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
    expressions = [row.EXPRESSION for row in rows if row.EXPRESSION]
    assert any("coalesce" in expr.lower() for expr in expressions), (
        f"the index exists but has no COALESCE key part. Key parts: {rows}"
    )


# --- lines ------------------------------------------------------------------


@pytest.mark.db
def test_a_destination_appears_at_most_once_per_scenario_per_entry(session):
    """Contract §2.3 UNIQUE(submission_entry_id, scenario, destination_id),
    and §6.2's "No duplicate destination within one entry's scenario"."""
    factor_set, sector, dairy, landfill = _prereqs(session)
    submission = _submission(session, factor_set)
    entry = SubmissionEntry(submission_id=submission.id, sector_id=sector.id,
                            food_category_id=dairy.id, sort_order=0)
    session.add(entry)
    session.flush()

    session.add(SubmissionLine(submission_entry_id=entry.id,
                               scenario=Scenario.current,
                               destination_id=landfill.id,
                               qty_kg=Decimal("1200.500")))
    session.flush()
    session.add(SubmissionLine(submission_entry_id=entry.id,
                               scenario=Scenario.current,
                               destination_id=landfill.id,
                               qty_kg=Decimal("1.000")))

    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.db
def test_the_two_scenarios_of_one_entry_may_use_the_same_destination(session):
    """`scenario` is part of the key precisely so that "1200 kg to landfill
    now, 300 kg to landfill under the alternative" is expressible."""
    factor_set, sector, dairy, landfill = _prereqs(session)
    submission = _submission(session, factor_set)
    entry = SubmissionEntry(submission_id=submission.id, sector_id=sector.id,
                            food_category_id=dairy.id, sort_order=0)
    session.add(entry)
    session.flush()

    session.add_all([
        SubmissionLine(submission_entry_id=entry.id, scenario=Scenario.current,
                       destination_id=landfill.id, qty_kg=Decimal("1200.000")),
        SubmissionLine(submission_entry_id=entry.id, scenario=Scenario.alternative,
                       destination_id=landfill.id, qty_kg=Decimal("300.000")),
    ])
    session.flush()

    assert session.scalar(
        select(func.count()).select_from(SubmissionLine)
        .where(SubmissionLine.submission_entry_id == entry.id)
    ) == 2


@pytest.mark.db
def test_the_same_line_may_belong_to_two_entries_of_one_submission(session):
    """The uniqueness scope is the entry, not the submission — the direct
    consequence of moving the FK. Waste sent to landfill at processing and
    waste sent to landfill at retail are two separate figures with two
    separate upstream factors.
    """
    factor_set, sector, dairy, landfill = _prereqs(session)
    submission = _submission(session, factor_set)
    first = SubmissionEntry(submission_id=submission.id, sector_id=sector.id,
                            food_category_id=dairy.id, sort_order=0)
    second = SubmissionEntry(submission_id=submission.id, sector_id=sector.id,
                             food_category_id=None, sort_order=1)
    session.add_all([first, second])
    session.flush()

    session.add_all([
        SubmissionLine(submission_entry_id=first.id, scenario=Scenario.current,
                       destination_id=landfill.id, qty_kg=Decimal("10.000")),
        SubmissionLine(submission_entry_id=second.id, scenario=Scenario.current,
                       destination_id=landfill.id, qty_kg=Decimal("20.000")),
    ])
    session.flush()

    assert session.scalar(select(func.count()).select_from(SubmissionLine)) == 2


@pytest.mark.db
def test_a_negative_quantity_is_refused(session):
    """CHECK qty_kg >= 0 (contract §2.3). `compare_metadata` cannot see a
    missing CHECK on this SQLAlchemy/MySQL combination, so the constraint
    needs a behavioural test of its own — see tests/test_migrations.py's
    "Known blind spot". A negative mass would subtract from every metric
    total it is summed into.

    OperationalError, *not* IntegrityError: MySQL reports a CHECK violation as
    error 3819, which SQLAlchemy maps to OperationalError — only uniqueness
    and foreign-key violations arrive as IntegrityError. Same reasoning as
    tests/admin/test_taxonomy_models.py.
    """
    factor_set, sector, dairy, landfill = _prereqs(session)
    submission = _submission(session, factor_set)
    entry = SubmissionEntry(submission_id=submission.id, sector_id=sector.id,
                            food_category_id=dairy.id, sort_order=0)
    session.add(entry)
    session.flush()

    session.add(SubmissionLine(submission_entry_id=entry.id,
                               scenario=Scenario.current,
                               destination_id=landfill.id,
                               qty_kg=Decimal("-1.000")))

    with pytest.raises(OperationalError) as caught:
        session.flush()

    assert "ck_submission_line_qty" in str(caught.value)


@pytest.mark.db
def test_the_quantity_keeps_three_decimal_places_exactly(session):
    """DECIMAL(16,3), not a float: §1.2 transmits quantities as strings
    precisely so no double ever touches them."""
    factor_set, sector, dairy, landfill = _prereqs(session)
    submission = _submission(session, factor_set)
    entry = SubmissionEntry(submission_id=submission.id, sector_id=sector.id,
                            food_category_id=dairy.id, sort_order=0)
    session.add(entry)
    session.flush()
    line = SubmissionLine(submission_entry_id=entry.id, scenario=Scenario.current,
                          destination_id=landfill.id, qty_kg=Decimal("1200.123"))
    session.add(line)
    session.flush()
    session.expire(line)

    assert line.qty_kg == Decimal("1200.123")


@pytest.mark.db
def test_the_gwp_horizon_is_one_of_the_two_the_contract_allows(session):
    """CHECK gwp_horizon IN (20, 100) — §6.2's only two legal values. The
    same constraint `admin/comparison_models.py` carries, and the same blind
    spot: `compare_metadata` will not report it missing. The engine binds
    `const_GWP_CH4` to `GWP_CH4_20` or `GWP_CH4_100` off this column (§4.3),
    so a third value resolves to neither.

    OperationalError rather than IntegrityError, per MySQL error 3819 — see
    `test_a_negative_quantity_is_refused` above.
    """
    factor_set, _, _, _ = _prereqs(session)
    now = utcnow()
    session.add(Submission(token=None, created_at=now, updated_at=now,
                           factor_set_id=factor_set.id, gwp_horizon=50))

    with pytest.raises(OperationalError) as caught:
        session.flush()

    assert "ck_submission_horizon" in str(caught.value)


# --- cascade ----------------------------------------------------------------


@pytest.mark.db
def test_deleting_a_submission_takes_its_entries_and_their_lines(session):
    """Both FKs are ON DELETE CASCADE (§2.3). §5.3 relies on it: an upsert
    "delete[s] every submission_entry for this submission (ON DELETE CASCADE
    takes the lines with it)". Without the second cascade, rebuilding an
    entry set orphans every line of the previous one — invisible to the API
    and counted by §5.4."""
    factor_set, sector, dairy, landfill = _prereqs(session)
    submission = _submission(session, factor_set)
    entry = SubmissionEntry(submission_id=submission.id, sector_id=sector.id,
                            food_category_id=dairy.id, sort_order=0)
    session.add(entry)
    session.flush()
    session.add(SubmissionLine(submission_entry_id=entry.id,
                               scenario=Scenario.current,
                               destination_id=landfill.id,
                               qty_kg=Decimal("5.000")))
    session.flush()

    session.execute(
        text("DELETE FROM submission WHERE id = :id"), {"id": submission.id}
    )

    assert session.scalar(select(func.count()).select_from(SubmissionEntry)) == 0
    assert session.scalar(select(func.count()).select_from(SubmissionLine)) == 0


@pytest.mark.db
def test_deleting_one_entry_takes_only_its_own_lines(session):
    """The rebuild path of §5.3 again, from the other side."""
    factor_set, sector, dairy, landfill = _prereqs(session)
    submission = _submission(session, factor_set)
    kept = SubmissionEntry(submission_id=submission.id, sector_id=sector.id,
                           food_category_id=dairy.id, sort_order=0)
    dropped = SubmissionEntry(submission_id=submission.id, sector_id=sector.id,
                              food_category_id=None, sort_order=1)
    session.add_all([kept, dropped])
    session.flush()
    session.add_all([
        SubmissionLine(submission_entry_id=kept.id, scenario=Scenario.current,
                       destination_id=landfill.id, qty_kg=Decimal("1.000")),
        SubmissionLine(submission_entry_id=dropped.id, scenario=Scenario.current,
                       destination_id=landfill.id, qty_kg=Decimal("2.000")),
    ])
    session.flush()

    session.execute(
        text("DELETE FROM submission_entry WHERE id = :id"), {"id": dropped.id}
    )

    remaining = session.scalars(select(SubmissionLine)).all()
    assert [line.submission_entry_id for line in remaining] == [kept.id]


# --- ordering and the token -------------------------------------------------


@pytest.mark.db
def test_sort_order_preserves_the_order_the_user_entered(session):
    """Contract §2.3: sort_order "[p]reserves the order the user entered
    them, so `entries[]` in the §6.2 response can be paired with the rows on
    screen". Nothing else in the row records it — id order is an
    implementation accident once an upsert has rebuilt the set."""
    factor_set, sector, dairy, _ = _prereqs(session)
    other = Sector(code="sub_retail", name="Retail")
    session.add(other)
    session.flush()
    submission = _submission(session, factor_set)
    session.add_all([
        SubmissionEntry(submission_id=submission.id, sector_id=other.id,
                        food_category_id=dairy.id, sort_order=1),
        SubmissionEntry(submission_id=submission.id, sector_id=sector.id,
                        food_category_id=dairy.id, sort_order=0),
    ])
    session.flush()

    ordered = session.scalars(
        select(SubmissionEntry)
        .where(SubmissionEntry.submission_id == submission.id)
        .order_by(SubmissionEntry.sort_order)
    ).all()

    assert [e.sector_id for e in ordered] == [sector.id, other.id]


@pytest.mark.db
def test_the_token_is_unique_and_nullable(session):
    """§2.3: UNIQUE and NULL. Nullable because `expire_tokens` (§5.3) nulls
    the column an hour on, keeping the data and severing the linkage; unique
    because it is what keys the upsert."""
    factor_set, _, _, _ = _prereqs(session)
    now = utcnow()
    session.add_all([
        Submission(token=None, created_at=now, updated_at=now,
                   factor_set_id=factor_set.id, gwp_horizon=100),
        Submission(token=None, created_at=now, updated_at=now,
                   factor_set_id=factor_set.id, gwp_horizon=100),
    ])
    session.flush()

    session.add(Submission(token="dup", created_at=now, updated_at=now,
                           factor_set_id=factor_set.id, gwp_horizon=100))
    session.flush()
    session.add(Submission(token="dup", created_at=now, updated_at=now,
                           factor_set_id=factor_set.id, gwp_horizon=100))

    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.db
def test_a_submission_defaults_to_the_hundred_year_horizon_and_is_public(session):
    """The two server-side defaults §2.3 specifies. They are server defaults,
    not Python ones, so a row written by a migration, a fixture or raw SQL
    gets them too — and `excluded_from_public` defaulting to FALSE is what
    makes it staff moderation rather than user consent (Decision 8)."""
    factor_set, _, _, _ = _prereqs(session)
    now = datetime(2026, 1, 1, 12, 0)
    session.execute(
        text("INSERT INTO submission (token, created_at, updated_at, factor_set_id) "
             "VALUES (NULL, :now, :now, :fs)"),
        {"now": now, "fs": factor_set.id},
    )

    row = session.scalars(select(Submission)).one()
    assert row.gwp_horizon == 100
    assert row.excluded_from_public is False


@pytest.mark.db
def test_the_submission_carries_its_time_frame_and_consent(session):
    """§2.3, v1.48. Two fields about the submission as a whole.

    `time_frame` is a label and nothing computes with it (the client's own
    ruling: "不用进 engine"), so it is a short string rather than a pair of
    dates - a period somebody chose from a list, carried to the results page
    and into the download.

    `is_public_contributed` defaults FALSE, and that default is the whole
    change: until v1.48 every calculation reached the public statistics
    because §2.3 said "there is no consent checkbox". There is one now.
    """
    factor_set, _, _, _ = _prereqs(session)
    now = utcnow()
    row = Submission(
        token=None, created_at=now, updated_at=now,
        factor_set_id=factor_set.id, gwp_horizon=100,
        time_frame="one_month",
    )
    session.add(row)
    session.flush()

    assert row.time_frame == "one_month"
    assert row.is_public_contributed is False, (
        "consent must default to withheld - a default of True would opt every "
        "visitor in and make the column decorative"
    )


@pytest.mark.db
def test_an_entry_carries_its_input_total_and_its_money(session):
    """§2.3, v1.48. Three optional numbers per (sector, food category).

    They are on the ENTRY and not the submission because the client asked for
    the total input "按 sector" - a business with waste at three points in the
    supply chain has three different production totals, and one figure on the
    submission could not say which stage it belonged to.

    All three are nullable: §6.2 makes them optional, and a visitor who does
    not know their production total still gets every other figure.
    """
    factor_set, sector, dairy, _ = _prereqs(session)
    submission = _submission(session, factor_set)
    entry = SubmissionEntry(
        submission_id=submission.id, sector_id=sector.id,
        food_category_id=dairy.id, sort_order=0,
        total_input_kg=Decimal("50000.000"),
        total_value_nzd=Decimal("120000.00"),
        wasted_value_nzd=Decimal("4500.00"),
    )
    session.add(entry)
    session.flush()

    assert entry.total_input_kg == Decimal("50000.000")
    assert entry.total_value_nzd == Decimal("120000.00")
    assert entry.wasted_value_nzd == Decimal("4500.00")

    blank = SubmissionEntry(
        submission_id=submission.id, sector_id=sector.id,
        food_category_id=None, sort_order=1,
    )
    session.add(blank)
    session.flush()
    assert blank.total_input_kg is None
    assert blank.total_value_nzd is None
    assert blank.wasted_value_nzd is None
