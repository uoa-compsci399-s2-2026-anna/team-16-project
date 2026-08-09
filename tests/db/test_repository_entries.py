"""Multi-entry persistence and entry-level statistics. Contract §5.3, §5.4.

These use `seeded_session` (SQLite, from tests/support/sqlite.py), like the
rest of `tests/db/test_repository.py`. The one behaviour they cannot prove on
SQLite is the `COALESCE(food_category_id, 0)` unique index, which
`tests/db/test_submissions.py` proves against real MySQL instead.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

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
)
from db.repository import get_public_stats, get_published_factor_set_id, upsert_submission


# --------------------------------------------------------------------------
# §3 request builders. `EntryInput` carries the sector and the food category,
# and its `current` / `alternative` are tuples of `ScenarioLine` directly --
# `ScenarioInput` is gone (§3), so there is no `.lines` to reach through.
# --------------------------------------------------------------------------


def _line(destination, qty):
    return SimpleNamespace(destination_code=destination, qty_kg=Decimal(qty))


def _entry(sector="processing", food_category="dairy", current=(("landfill", "10"),),
           alternative=None):
    return SimpleNamespace(
        sector_code=sector,
        food_category_code=food_category,
        current=tuple(_line(*pair) for pair in current),
        alternative=None if alternative is None
        else tuple(_line(*pair) for pair in alternative),
    )


def _request(*entries, gwp_horizon=100):
    return SimpleNamespace(entries=tuple(entries), gwp_horizon=gwp_horizon)


def _extra_taxonomy(db):
    """The seed carries one sector and one destination; five entries need more."""
    group = db.scalar(select(DestinationGroup))
    db.add_all(
        [Sector(code=code, name=code.title())
         for code in ("retail", "hospitality", "primary_production", "distribution")]
        + [FoodCategory(code=code, name=code.title())
           for code in ("meat", "bakery", "vegetables")]
        + [Destination(group_id=group.id, code=code, name=code.title())
           for code in ("compost", "prevention")]
    )
    db.flush()


def _entries_of(db, submission_id):
    return db.scalars(
        select(SubmissionEntry)
        .where(SubmissionEntry.submission_id == submission_id)
        .order_by(SubmissionEntry.sort_order)
    ).all()


def _bucket(stats_field, code):
    return next((bucket for bucket in stats_field if bucket.code == code), None)


# --------------------------------------------------------------------------
# §5.3 -- one call, one submission, N entries
# --------------------------------------------------------------------------


def test_a_five_entry_calculation_persists_five_entries(seeded_session):
    """The defect this task exists to fix.

    Resolving one sector from the request and deleting lines by submission_id
    persists one row for a five-entry calculation, which is what forced the
    front end to add the per-entry results together in JavaScript (§6.2).
    """
    _extra_taxonomy(seeded_session)
    factor_set_id = get_published_factor_set_id(seeded_session)
    pairs = (
        ("processing", "dairy"),
        ("retail", "meat"),
        ("hospitality", None),
        ("primary_production", "vegetables"),
        ("distribution", "bakery"),
    )
    submission_id, _ = upsert_submission(
        seeded_session,
        None,
        _request(*(_entry(sector, food, current=(("landfill", "10"),))
                   for sector, food in pairs)),
        factor_set_id,
    )

    entries = _entries_of(seeded_session, submission_id)
    assert len(entries) == 5
    assert seeded_session.scalar(select(func.count()).select_from(Submission)) == 1
    # Request order is preserved by sort_order (§2.3), so the response's
    # entries[] can be paired with the rows on the user's screen.
    assert [entry.sort_order for entry in entries] == [0, 1, 2, 3, 4]
    assert [entry.sector.code for entry in entries] == [pair[0] for pair in pairs]
    assert [
        entry.food_category.code if entry.food_category else None for entry in entries
    ] == [pair[1] for pair in pairs]
    assert all(len(entry.lines) == 1 for entry in entries)


def test_each_entry_keeps_its_own_lines_and_scenarios(seeded_session):
    _extra_taxonomy(seeded_session)
    submission_id, _ = upsert_submission(
        seeded_session,
        None,
        _request(
            _entry("processing", "dairy",
                   current=(("landfill", "100"), ("compost", "20")),
                   alternative=(("compost", "120"),)),
            _entry("retail", "meat", current=(("landfill", "7"),)),
        ),
        get_published_factor_set_id(seeded_session),
    )
    first, second = _entries_of(seeded_session, submission_id)

    assert {(line.scenario, line.qty_kg) for line in first.lines} == {
        (Scenario.current, Decimal("100.000")),
        (Scenario.current, Decimal("20.000")),
        (Scenario.alternative, Decimal("120.000")),
    }
    # An entry with no alternative writes current lines only; it is not
    # inferred from, or mixed with, the neighbouring entry's alternative.
    assert [line.scenario for line in second.lines] == [Scenario.current]
    assert second.lines[0].qty_kg == Decimal("7.000")


def test_a_null_food_category_is_stored_as_null_not_resolved(seeded_session):
    """§5.4: the engine resolves NULL to standard_mix, storage does not."""
    submission_id, _ = upsert_submission(
        seeded_session,
        None,
        _request(_entry(food_category=None)),
        get_published_factor_set_id(seeded_session),
    )
    entry = _entries_of(seeded_session, submission_id)[0]
    assert entry.food_category_id is None


def test_resubmitting_with_the_same_token_rebuilds_the_whole_entry_set(seeded_session):
    """§5.3: the entry set is rebuilt, not patched, and leaves no orphan line."""
    _extra_taxonomy(seeded_session)
    factor_set_id = get_published_factor_set_id(seeded_session)
    submission_id, token = upsert_submission(
        seeded_session,
        None,
        _request(
            _entry("processing", "dairy", current=(("landfill", "10"),)),
            _entry("retail", "meat", current=(("landfill", "20"),)),
            _entry("hospitality", "bakery", current=(("landfill", "30"),)),
        ),
        factor_set_id,
    )
    second_id, second_token = upsert_submission(
        seeded_session,
        token,
        _request(_entry("primary_production", "vegetables",
                        current=(("compost", "5"),))),
        factor_set_id,
    )

    assert (second_id, second_token) == (submission_id, token)
    entries = _entries_of(seeded_session, submission_id)
    assert len(entries) == 1
    assert entries[0].sector.code == "primary_production"
    assert seeded_session.scalar(select(func.count()).select_from(Submission)) == 1
    # The removed entries take their lines with them. Counting the whole table
    # rather than the surviving entry's lines is deliberate: a line orphaned by
    # a delete that did not cascade is invisible to every join in §5.4 and
    # would only ever surface as a slow leak.
    assert seeded_session.scalar(select(func.count()).select_from(SubmissionEntry)) == 1
    assert seeded_session.scalar(select(func.count()).select_from(SubmissionLine)) == 1


def test_rebuilding_reuses_the_same_sector_and_food_category_pair(seeded_session):
    """UNIQUE(submission_id, sector_id, food_category_id) is per submission, so
    the rebuild must have flushed its deletes before it inserts the same pair
    again -- the ordinary case of a user changing one number and recalculating.
    """
    factor_set_id = get_published_factor_set_id(seeded_session)
    submission_id, token = upsert_submission(
        seeded_session, None, _request(_entry(current=(("landfill", "10"),))), factor_set_id
    )
    upsert_submission(
        seeded_session, token, _request(_entry(current=(("landfill", "99"),))), factor_set_id
    )
    entries = _entries_of(seeded_session, submission_id)
    assert len(entries) == 1
    assert [line.qty_kg for line in entries[0].lines] == [Decimal("99.000")]


def test_the_submission_stamps_the_factor_set_and_the_horizon(seeded_session):
    factor_set_id = get_published_factor_set_id(seeded_session)
    submission_id, _ = upsert_submission(
        seeded_session, None, _request(_entry(), gwp_horizon=20), factor_set_id
    )
    submission = seeded_session.get(Submission, submission_id)
    assert submission.gwp_horizon == 20
    assert submission.factor_set_id == factor_set_id


# --------------------------------------------------------------------------
# §5.4 -- statistics aggregate over entries
# --------------------------------------------------------------------------


def test_bucket_counts_are_entries_while_total_calculations_counts_submissions(
    seeded_session,
):
    """§5.4/§6.4: the two figures deliberately do not sum to each other."""
    _extra_taxonomy(seeded_session)
    factor_set_id = get_published_factor_set_id(seeded_session)
    upsert_submission(
        seeded_session,
        None,
        _request(
            _entry("processing", "dairy", current=(("landfill", "10"),)),
            _entry("retail", "dairy", current=(("landfill", "20"),)),
            _entry("hospitality", "dairy", current=(("landfill", "30"),)),
        ),
        factor_set_id,
    )
    stats = get_public_stats(seeded_session, threshold=1)

    assert stats.total_calculations == 1
    assert sum(bucket.count for bucket in stats.by_sector) == 3
    assert {bucket.code for bucket in stats.by_sector} == {
        "processing", "retail", "hospitality"
    }
    # A multi-stage business is three sector observations, not one counted as
    # whichever stage it happened to enter first.
    assert {bucket.count for bucket in stats.by_sector} == {1}
    assert _bucket(stats.by_food_category, "dairy").count == 3
    # by_destination counts entries too: three entries used landfill.
    assert _bucket(stats.by_destination, "landfill").count == 3
    assert _bucket(stats.by_destination, "landfill").total_kg == Decimal("60.000")


def test_each_entry_carries_its_own_mass_into_its_own_bucket(seeded_session):
    _extra_taxonomy(seeded_session)
    upsert_submission(
        seeded_session,
        None,
        _request(
            _entry("processing", "dairy", current=(("landfill", "10"),)),
            _entry("retail", "meat", current=(("landfill", "200"),)),
        ),
        get_published_factor_set_id(seeded_session),
    )
    stats = get_public_stats(seeded_session, threshold=1)

    assert _bucket(stats.by_sector, "processing").total_kg == Decimal("10.000")
    assert _bucket(stats.by_sector, "retail").total_kg == Decimal("200.000")
    assert _bucket(stats.by_food_category, "meat").total_kg == Decimal("200.000")


def test_the_alternative_scenario_reaches_no_breakdown_at_all(seeded_session):
    """§5.4's first omission: without WHERE scenario = 'current', `prevention`
    -- waste that by construction did not happen -- becomes a bucket in the
    public destination chart and every total_kg roughly doubles.
    """
    _extra_taxonomy(seeded_session)
    upsert_submission(
        seeded_session,
        None,
        _request(_entry("processing", "dairy",
                        current=(("landfill", "1000"),),
                        alternative=(("prevention", "1000"),))),
        get_published_factor_set_id(seeded_session),
    )
    stats = get_public_stats(seeded_session, threshold=1)

    assert _bucket(stats.by_destination, "prevention") is None
    assert [bucket.code for bucket in stats.by_destination] == ["landfill"]
    assert _bucket(stats.by_destination, "landfill").total_kg == Decimal("1000.000")
    assert _bucket(stats.by_sector, "processing").total_kg == Decimal("1000.000")
    assert _bucket(stats.by_food_category, "dairy").total_kg == Decimal("1000.000")


def test_a_staff_excluded_submission_hides_every_one_of_its_entries(seeded_session):
    """§5.4's second omission: `excluded_from_public` is on `submission`, so a
    breakdown grouped on `submission_entry` has to join one table further than
    its own grouping needs. Stopping at the entry applies staff moderation to
    nothing, and the docstring still says the exclusion happened.
    """
    _extra_taxonomy(seeded_session)
    factor_set_id = get_published_factor_set_id(seeded_session)
    excluded_id, _ = upsert_submission(
        seeded_session,
        None,
        _request(
            _entry("retail", "meat", current=(("landfill", "500"),)),
            _entry("hospitality", "bakery", current=(("compost", "500"),)),
        ),
        factor_set_id,
    )
    seeded_session.get(Submission, excluded_id).excluded_from_public = True
    upsert_submission(
        seeded_session,
        None,
        _request(_entry("processing", "dairy", current=(("landfill", "7"),))),
        factor_set_id,
    )
    seeded_session.flush()

    stats = get_public_stats(seeded_session, threshold=1)
    assert stats.total_calculations == 1
    assert [bucket.code for bucket in stats.by_sector] == ["processing"]
    assert [bucket.code for bucket in stats.by_food_category] == ["dairy"]
    assert [bucket.code for bucket in stats.by_destination] == ["landfill"]
    assert _bucket(stats.by_destination, "landfill").total_kg == Decimal("7.000")


def test_a_null_food_category_becomes_the_unspecified_bucket(seeded_session):
    """§5.4's third omission: NULL is a bucket, not a gap, and it is not
    `standard_mix` -- which is what a user selects deliberately.
    """
    _extra_taxonomy(seeded_session)
    factor_set_id = get_published_factor_set_id(seeded_session)
    upsert_submission(
        seeded_session,
        None,
        _request(
            _entry("processing", None, current=(("landfill", "10"),)),
            _entry("retail", None, current=(("landfill", "20"),)),
        ),
        factor_set_id,
    )
    upsert_submission(
        seeded_session,
        None,
        _request(_entry("hospitality", "standard_mix", current=(("landfill", "5"),))),
        factor_set_id,
    )
    stats = get_public_stats(seeded_session, threshold=1)

    unspecified = _bucket(stats.by_food_category, "unspecified")
    assert unspecified is not None
    assert unspecified.label == "Not broken down by type"
    assert unspecified.count == 2
    assert unspecified.total_kg == Decimal("30.000")
    # The two must stay distinguishable: one is what the user told us, the
    # other is a deliberate choice of the standard mix.
    assert _bucket(stats.by_food_category, "standard_mix").count == 1


def test_shares_are_computed_within_a_breakdown_and_sum_to_one(seeded_session):
    """§6.4: share is computed against its own breakdown's total, not against
    total_calculations -- none of the three breakdowns is a breakdown of it.
    """
    _extra_taxonomy(seeded_session)
    factor_set_id = get_published_factor_set_id(seeded_session)
    upsert_submission(
        seeded_session,
        None,
        _request(
            _entry("processing", "dairy", current=(("landfill", "1"),)),
            _entry("retail", "meat", current=(("landfill", "1"), ("compost", "1"))),
            _entry("hospitality", None, current=(("compost", "1"),)),
        ),
        factor_set_id,
    )
    stats = get_public_stats(seeded_session, threshold=1)

    assert stats.total_calculations == 1
    for breakdown in (stats.by_sector, stats.by_food_category, stats.by_destination):
        assert breakdown
        # Exactly 1, up to the 4-decimal-place quantisation of each share:
        # three buckets of one entry each are three thirds, and 3 x 0.3333 is
        # 0.9999. The tolerance is one ulp per bucket, not a loose epsilon.
        drift = abs(sum(bucket.share for bucket in breakdown) - Decimal("1"))
        assert drift <= Decimal("0.0001") * len(breakdown)
    # Two of the four destination observations are landfill, and that one
    # divides exactly -- the denominator is the breakdown's own 4, not the 1
    # calculation or the 3 entries.
    assert _bucket(stats.by_destination, "landfill").share == Decimal("0.5000")
    assert _bucket(stats.by_sector, "processing").share == Decimal("0.3333")


def test_suppression_still_merges_small_buckets_into_other(seeded_session):
    """The privacy guarantee, re-proved after the unit of counting moved.

    Five submissions of one sector clear a threshold of 5; the single entry in
    another sector does not and merges into `other`, server-side.
    """
    _extra_taxonomy(seeded_session)
    factor_set_id = get_published_factor_set_id(seeded_session)
    for _ in range(5):
        upsert_submission(
            seeded_session,
            None,
            _request(_entry("processing", "dairy", current=(("landfill", "10"),))),
            factor_set_id,
        )
    upsert_submission(
        seeded_session,
        None,
        _request(_entry("retail", "meat", current=(("compost", "3"),))),
        factor_set_id,
    )
    stats = get_public_stats(seeded_session)

    assert stats.suppression_threshold == 5
    assert _bucket(stats.by_sector, "retail") is None
    assert _bucket(stats.by_sector, "processing").count == 5
    other = _bucket(stats.by_sector, "other")
    assert other.count == 1
    assert other.total_kg == Decimal("3.000")
    assert other.label == "Other (sample too small)"
    # The suppressed bucket stays in its breakdown's denominator, so the
    # shares still sum to 1 rather than inflating every surviving share.
    assert sum(bucket.share for bucket in stats.by_sector) == Decimal("1.0000")
    assert _bucket(stats.by_food_category, "unspecified") is None
    assert _bucket(stats.by_destination, "compost") is None


def test_the_unspecified_bucket_is_suppressed_on_the_same_threshold(seeded_session):
    _extra_taxonomy(seeded_session)
    factor_set_id = get_published_factor_set_id(seeded_session)
    for _ in range(5):
        upsert_submission(
            seeded_session,
            None,
            _request(_entry("processing", "dairy", current=(("landfill", "10"),))),
            factor_set_id,
        )
    upsert_submission(
        seeded_session,
        None,
        _request(_entry("retail", None, current=(("landfill", "1"),))),
        factor_set_id,
    )
    stats = get_public_stats(seeded_session)

    assert _bucket(stats.by_food_category, "unspecified") is None
    assert _bucket(stats.by_food_category, "other").count == 1
    assert sum(bucket.share for bucket in stats.by_food_category) == Decimal("1.0000")


def test_statistics_are_decimal_not_float(seeded_session):
    """§1.2. A float reaching the wire would be serialised by `wire()` as a
    JSON number, which is the one thing §1.2 forbids for a decimal.
    """
    upsert_submission(
        seeded_session,
        None,
        _request(_entry(current=(("landfill", "10.125"),))),
        get_published_factor_set_id(seeded_session),
    )
    stats = get_public_stats(seeded_session, threshold=1)
    for breakdown in (stats.by_sector, stats.by_food_category, stats.by_destination):
        for bucket in breakdown:
            assert isinstance(bucket.total_kg, Decimal)
            assert isinstance(bucket.share, Decimal)
    assert _bucket(stats.by_sector, "processing").total_kg == Decimal("10.125")


def test_an_empty_database_produces_empty_breakdowns(seeded_session):
    stats = get_public_stats(seeded_session)
    assert stats.total_calculations == 0
    assert stats.by_sector == ()
    assert stats.by_food_category == ()
    assert stats.by_destination == ()


# --------------------------------------------------------------------------
# The same two behaviours against real MySQL. SQLite has no functional unique
# index and no enforced ON DELETE CASCADE, and it is not the engine that
# ships, so the rebuild's delete/insert ordering and the three breakdowns are
# re-proved here on the database that does. Uses `session` from
# tests/conftest.py (kaicalc_test), not `seeded_session`.
# --------------------------------------------------------------------------


def _mysql_prereqs(session):
    """The taxonomy and factor-set rows an entry has to point at.

    Codes are prefixed so they cannot collide with another test's rows in the
    shared `kaicalc_test` database.
    """
    group = DestinationGroup(code="ent_disposal", name="Disposal", is_waste=True)
    session.add_all(
        [group]
        + [Sector(code=f"ent_sector_{i}", name=f"Sector {i}") for i in range(3)]
        + [FoodCategory(code="ent_dairy", name="Dairy")]
    )
    factor_set = FactorSet(
        version_label="ent-MOCK-v0", status=FactorSetStatus.published, is_mock=True
    )
    session.add(factor_set)
    session.flush()
    session.add_all([
        Destination(group_id=group.id, code="ent_landfill", name="Landfill"),
        Destination(group_id=group.id, code="ent_prevention", name="Prevention"),
    ])
    session.flush()
    return factor_set.id


@pytest.mark.db
def test_the_rebuild_survives_the_real_unique_index(session):
    """On MySQL `uq_submission_entry` and its COALESCE twin are enforced, so a
    rebuild that inserts before its deletes reach the database fails outright
    rather than merely leaving stale rows.
    """
    factor_set_id = _mysql_prereqs(session)
    submission_id, token = upsert_submission(
        session,
        None,
        _request(
            _entry("ent_sector_0", "ent_dairy", current=(("ent_landfill", "10"),)),
            _entry("ent_sector_1", None, current=(("ent_landfill", "20"),)),
        ),
        factor_set_id,
    )
    upsert_submission(
        session,
        token,
        _request(
            _entry("ent_sector_0", "ent_dairy", current=(("ent_landfill", "11"),)),
            _entry("ent_sector_1", None, current=(("ent_landfill", "21"),)),
            _entry("ent_sector_2", None, current=(("ent_landfill", "31"),)),
        ),
        factor_set_id,
    )
    session.flush()

    entries = _entries_of(session, submission_id)
    assert [entry.sort_order for entry in entries] == [0, 1, 2]
    assert session.scalar(select(func.count()).select_from(SubmissionEntry)) == 3
    assert session.scalar(select(func.count()).select_from(SubmissionLine)) == 3


@pytest.mark.db
def test_the_three_breakdowns_run_on_mysql(session):
    """The ENUM predicate, the DECIMAL sums and the COALESCE grouping, on the
    engine that ships. SQLite compares the scenario enum and sums DECIMAL by
    different rules, and neither is the one production uses.
    """
    factor_set_id = _mysql_prereqs(session)
    upsert_submission(
        session,
        None,
        _request(
            _entry("ent_sector_0", "ent_dairy",
                   current=(("ent_landfill", "1000.500"),),
                   alternative=(("ent_prevention", "1000.500"),)),
            _entry("ent_sector_1", None, current=(("ent_landfill", "9.500"),)),
        ),
        factor_set_id,
    )
    session.flush()

    stats = get_public_stats(session, threshold=1)
    assert stats.total_calculations == 1
    assert _bucket(stats.by_destination, "ent_prevention") is None
    assert _bucket(stats.by_destination, "ent_landfill").count == 2
    assert _bucket(stats.by_destination, "ent_landfill").total_kg == Decimal("1010.000")
    assert _bucket(stats.by_sector, "ent_sector_0").total_kg == Decimal("1000.500")
    assert _bucket(stats.by_food_category, "unspecified").count == 1
    assert _bucket(stats.by_food_category, "unspecified").total_kg == Decimal("9.500")
    assert isinstance(_bucket(stats.by_sector, "ent_sector_0").total_kg, Decimal)


@pytest.mark.parametrize("code", ["landfill", "compost"])
def test_a_destination_used_by_two_entries_counts_twice(seeded_session, code):
    _extra_taxonomy(seeded_session)
    upsert_submission(
        seeded_session,
        None,
        _request(
            _entry("processing", "dairy", current=((code, "10"),)),
            _entry("retail", "meat", current=((code, "40"),)),
        ),
        get_published_factor_set_id(seeded_session),
    )
    stats = get_public_stats(seeded_session, threshold=1)
    bucket = _bucket(stats.by_destination, code)
    assert bucket.count == 2
    assert bucket.total_kg == Decimal("50.000")
