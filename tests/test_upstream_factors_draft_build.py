"""``data/upstream-factors-draft/build_upstream_factors_draft.py``'s own
guard rails, under test for the first time.

That script carries two mechanical checks that exist to stop a silent
defect, and until now nothing ran either of them except the script itself:

* ``_assert_only_table2_co2_moved()`` re-runs the measurement the 2026-09-21
  client revision was made on -- table 1 unchanged, table 2's labels,
  life-cycle column and water column unchanged, only its CO2-eq column
  replaced. It is the successor to the ``TABLE2_CO2_COPY_SOURCE`` check,
  which asserted the copy-paste defect the client has now fixed and which
  would fail on correct data.
* ``_assert_completeness()`` refuses to write a factor set that is missing a
  row a submission could need, because a missing row prices at zero and a
  zero on screen is indistinguishable from a real measurement of none.
* ``_assert_ladders_are_well_formed()`` (v1.71) refuses an equivalence ladder
  that is upside down or has no bottom rung. Neither is malformed data --
  both load, validate and compute -- and both show the wrong sentence to
  every visitor, which is why the build is where they are caught.

A check nothing exercises is a check nobody knows still works. Each test
below breaks exactly one thing and asserts the failure names it, rather than
asserting only that *something* failed -- ``SystemExit`` is raised from half
a dozen places in that script and "it raised" would pass for the wrong
reason.

The module is loaded by file path: ``data/upstream-factors-draft/`` has no
``__init__.py`` and its name is not a valid Python identifier, the same
reason ``tests/db/test_load_upstream_factors_draft.py`` does it this way.
Nothing here touches a database; the build script reads two committed JSON
fixtures and writes nothing unless ``main()`` is called, which it is not.
"""

from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_BUILD_PATH = (
    REPO_ROOT / "data" / "upstream-factors-draft" / "build_upstream_factors_draft.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "build_upstream_factors_draft", _BUILD_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: Loaded once: importing it only defines names and reads two path constants.
build_module = _load_module()


@pytest.fixture
def restore_tables():
    """Put ``TABLE1`` and ``TABLE2`` back however the test leaves them.

    The tests below mutate the transcribed client data in place, which is
    module-level state shared with every other test in the session. Copying
    the lists and restoring their contents (rather than rebinding the names)
    means the restoration reaches the build script too, which imported the
    same list objects.
    """
    table1 = build_module.TABLE1
    table2 = build_module.TABLE2
    saved1 = list(table1)
    saved2 = list(table2)
    yield
    table1[:] = saved1
    table2[:] = saved2


def test_the_committed_transcription_passes_its_own_check():
    """The negative case for every test below: as committed, the client's
    two tables satisfy the measurement the 2026-09-21 revision was made on.
    Without this, a check that raised unconditionally would pass all three
    of the tests that follow."""
    build_module._assert_only_table2_co2_moved()


def test_a_table1_cell_that_moved_stops_the_build(restore_tables):
    """Table 1 was measured unchanged between the client's two revisions,
    and the provenance in `rawtec_source_data.py` and
    `docs/upstream-factors-draft.md` says so. If a later edit moves a table 1
    cell, that prose becomes false, so the build must stop rather than write
    a factor set whose documentation no longer describes it."""
    original = build_module.TABLE1[2]
    assert original.food == "Cheese", "table 1's row order has changed; fix this test"
    build_module.TABLE1[2] = original._replace(
        water_l_per_kg=original.water_l_per_kg + Decimal("1")
    )

    with pytest.raises(SystemExit) as caught:
        build_module._assert_only_table2_co2_moved()

    message = str(caught.value)
    assert "TABLE1 row 2 (Cheese) water_l_per_kg" in message
    assert "3969.00" in message and "3968.00" in message


def test_a_table2_water_cell_that_moved_stops_the_build(restore_tables):
    """Table 2's *water* column was measured identical across both client
    revisions. It is the column most likely to be edited by accident while
    editing the CO2-eq column beside it, which is exactly why it is frozen."""
    original = build_module.TABLE2[4]
    assert original.destination == "Landfill", "table 2's row order has changed; fix this test"
    build_module.TABLE2[4] = original._replace(water_l_per_kg=Decimal("0.07"))

    with pytest.raises(SystemExit) as caught:
        build_module._assert_only_table2_co2_moved()

    message = str(caught.value)
    assert "TABLE2 row 4 (Landfill) water_l_per_kg" in message
    assert "0.07" in message and "0.06" in message


def test_a_row_still_carrying_the_withdrawn_co2_column_stops_the_build(restore_tables):
    """What a half-applied revision looks like: one destination left holding
    the 2026-09-05 value, which was a table 1 food figure and not a
    destination figure at all. Compost is the row that made the defect
    visible -- 10.13 against landfill's 4.95 priced composting as twice as
    bad as landfilling."""
    original = build_module.TABLE2[2]
    assert original.destination == "Compost", "table 2's row order has changed; fix this test"
    build_module.TABLE2[2] = original._replace(co2e_per_kg=Decimal("10.13"))

    with pytest.raises(SystemExit) as caught:
        build_module._assert_only_table2_co2_moved()

    message = str(caught.value)
    assert "'Compost' still carries the withdrawn" in message
    assert "10.13" in message


def test_the_corrected_column_reaches_the_built_factor_set():
    """The transcription is only half the job: the client's corrected values
    have to survive the mapping into New Zealand destinations. These four are
    one-to-one in `NZ_DESTINATION_SOURCES` (one client row each), so the
    built figure must equal the client's own printed number exactly -- no
    aggregation stands between them.

    `compost` against `landfill` is the pair the correction inverted, and is
    checked as an ordering as well as two values: under the withdrawn column
    compost was priced *worse* than landfill, which is the defect this
    revision removes."""
    data = build_module.build()
    built = {
        row["destination"]: Decimal(row["value_per_kg"])
        for row in data["downstream"]
        if row["metric"] == "co2e"
    }

    assert built["compost"] == Decimal("-0.11")
    assert built["landfill"] == Decimal("0.60")
    assert built["anaerobic_digestion"] == Decimal("-0.04")
    assert built["upcycling"] == Decimal("-0.15")

    assert built["compost"] < built["landfill"], (
        "composting must price better than landfill under the client's "
        "corrected column; the withdrawn 2026-09-05 column had it the other "
        "way round"
    )


def test_a_dropped_downstream_row_stops_the_build():
    """`_assert_completeness()` still does its job after this revision.

    A destination missing a metric's row does not fail anywhere else: the
    documented lookup order resolves it to zero, and a zero on screen reads
    as a real measurement of none. This is the check that turns that into a
    refusal, and it must keep catching the case after the table 2 rewrite.
    """
    data = build_module.build()
    before = len(data["downstream"])
    data["downstream"] = [
        row for row in data["downstream"]
        if not (row["destination"] == "compost" and row["metric"] == "co2e")
    ]
    assert len(data["downstream"]) == before - 1, "nothing was dropped; fix this test"

    with pytest.raises(SystemExit) as caught:
        build_module._assert_completeness(data)

    message = str(caught.value)
    assert "downstream compost/co2e: found 0, expected 1" in message


# ------------------------------------------------------------------- `land`


def test_the_yield_column_is_inverted_rather_than_used_as_printed():
    """The one conversion nobody may skip, checked on its own arithmetic.

    The client publishes land as **t/ha**, a yield. Used as a factor exactly
    as printed it is upside down -- a bigger number would mean more land. The
    metric reports land occupation per kilogram, so the figure is
    ``10 / yield``, and the direction is the whole point: a high-yielding crop
    must come out with a *small* footprint.

    Three assertions, and each would survive without the other two. The
    identity ``10/Y`` pins the value; ``Poultry`` at 57.48 t/ha against
    ``Red Meat`` at 0.22 pins the *direction*, which an inverted conversion
    (``Y/10``) would reverse while still producing plausible-looking numbers;
    and the exactness check pins that it is done on ``Decimal`` -- 10/0.22 has
    no finite binary representation, so a float round trip lands somewhere
    near 45.454545454545453 rather than on the repeating decimal.
    """
    invert = build_module.land_m2_per_kg_from_yield

    assert invert(Decimal("0.22")) == Decimal(10) / Decimal("0.22")
    assert invert(Decimal("57.48")) == Decimal(10) / Decimal("57.48")

    #: 57.48 t/ha is a high yield and must price LOW per kilogram; 0.22 t/ha
    #: is a low yield and must price HIGH. `Y / 10` gets this backwards.
    assert invert(Decimal("57.48")) < invert(Decimal("0.22"))

    #: Exact, not merely close: `Decimal` arithmetic throughout (contract 1.2).
    assert invert(Decimal("0.22")) == Decimal("45.45454545454545454545454545")
    assert invert(Decimal("100")) == Decimal("0.1")


def test_a_yield_of_zero_is_refused_rather_than_divided_by():
    """An infinity here would propagate through the unweighted mean into a
    stored factor and read on screen as a very large but ordinary number."""
    with pytest.raises(SystemExit) as caught:
        build_module.land_m2_per_kg_from_yield(Decimal("0"))
    assert "cannot be inverted" in str(caught.value)


def test_the_stated_rule_takes_the_public_figure_only_past_ten_times():
    """The rule the owner set, on the four rows that cross it and one that
    does not.

    `Poultry` is the row the whole check exists for: 57.48 t/ha inverts to
    0.17 m2/kg, a vegetable's footprint rather than a chicken's, and the
    public figure is seventy times larger. `Pork` is the control -- the two
    sources agree to within one percent, so nothing is substituted.

    `Nuts and seeds` is reported rather than fixed, and this test says so in
    executable form: its land cell is a known duplicate of Red Meat's, but it
    sits 4.1x from the public figure, which is inside the stated threshold, so
    the client's figure is kept. The rule is one rule; it is not re-argued per
    row.
    """
    poultry = build_module.land_comparison("Poultry")
    assert poultry.taken == "public"
    assert poultry.public_m2_per_kg == Decimal("12.22")
    assert poultry.ratio > Decimal(10)

    pork = build_module.land_comparison("Pork")
    assert pork.taken == "client"
    assert pork.ratio < Decimal(10)

    nuts = build_module.land_comparison("Nuts and seeds")
    assert nuts.taken == "client", (
        "the stated rule keeps the client's figure here; if this is to change "
        "it is the threshold that changes, not this one row"
    )
    assert build_module.land_cell_duplicates("Nuts and seeds") == ("Red Meat",)

    substituted = sorted(
        row.food for row in build_module.TABLE1
        if build_module.land_comparison(row.food).taken == "public"
    )
    assert substituted == ["Eggs", "Other meat", "Poultry", "Sweeteners"]


def test_every_land_row_says_which_of_the_two_figures_it_is():
    """"Do not quietly average them, and do not silently keep an implausible
    client figure." Both are hidden by an unannotated number, so every row's
    note has to name the decision.

    Checked on the built set rather than on the note-building function, so a
    row that reached the file by some other path is covered too.
    """
    data = build_module.build()
    land_rows = [
        row for row in data["upstream"]
        if row["metric"] == "land" and row["destination"] is None
    ]
    assert len(land_rows) == 60, "ten food categories x six sectors"

    for row in land_rows:
        note = row["source_note"]
        where = f"{row['food_category']}/{row['sector']}"
        assert "YIELD, not a footprint" in note, where
        assert "x 10,000 m2/ha" in note, where
        assert (
            "THE CLIENT'S FIGURE IS KEPT" in note
            or "THE PUBLIC FIGURE IS TAKEN" in note
            or "KEPT, UNCHECKED" in note
        ), f"{where} does not say which of the two figures it carries"


def test_the_client_and_public_figures_reach_the_built_categories():
    """The comparison is only half the job: the chosen figure has to survive
    the unweighted mean into a New Zealand category.

    `vegetables` and `nuts_seeds` are one-to-one, so the built figure must
    equal the inverted client cell exactly. `meat` is the mixed case -- two
    client figures and two public substitutions in one mean -- and is the row
    a change to either side would move.
    """
    data = build_module.build()
    built = {
        row["food_category"]: Decimal(row["value_per_kg"])
        for row in data["upstream"]
        if row["metric"] == "land" and row["destination"] is None
    }

    assert built["vegetables"] == Decimal("0.1683501684")   # 10 / 59.40
    assert built["nuts_seeds"] == Decimal("45.4545454545")  # 10 / 0.22
    #: mean(10/0.22, 10/0.58, 12.22 public, 181.40 public)
    assert built["meat"] == Decimal("64.0789811912")

    #: Flat across all six sectors -- land is occupied at the farm and the
    #: same kilogram carries it wherever it is wasted.
    for food in built:
        values = {
            row["value_per_kg"] for row in data["upstream"]
            if row["metric"] == "land"
            and row["destination"] is None
            and row["food_category"] == food
        }
        assert len(values) == 1, f"{food} varies by sector: {sorted(values)}"


def test_every_land_row_has_a_prevention_offset_at_zero():
    """O-7's machinery, for the new metric.

    `find_missing_prevention_upstream` refuses to publish a set in which any
    (sector, food_category, metric) has a general upstream row and no
    prevention row at zero. A land row without one would charge a prevented
    line its full land footprint and understate the benefit of wasting less --
    and the refusal would arrive at publication, long after the build.
    """
    data = build_module.build()
    general = {
        (row["sector"], row["food_category"])
        for row in data["upstream"]
        if row["metric"] == "land" and row["destination"] is None
    }
    offsets = {
        (row["sector"], row["food_category"]): row["value_per_kg"]
        for row in data["upstream"]
        if row["metric"] == "land" and row["destination"] == "prevention"
    }

    assert general, "no general land rows at all; fix this test"
    missing = sorted(general - set(offsets))
    assert not missing, f"land rows with no prevention offset: {missing}"
    assert set(offsets.values()) == {"0.0000000000"}


def test_a_missing_land_prevention_row_stops_the_build():
    """The completeness check reaches the new metric, not only the old three.

    Dropping a prevention row is the mutation that matters here: nothing else
    in the build notices it, and the failure it causes arrives at publication
    time with a message about O-7 rather than at build time with a message
    about a missing row.
    """
    data = build_module.build()
    before = len(data["upstream"])
    data["upstream"] = [
        row for row in data["upstream"]
        if not (
            row["metric"] == "land"
            and row["destination"] == "prevention"
            and row["food_category"] == "dairy"
            and row["sector"] == "consumer_household"
        )
    ]
    assert len(data["upstream"]) == before - 1, "nothing was dropped; fix this test"

    with pytest.raises(SystemExit) as caught:
        build_module._assert_completeness(data)

    assert (
        "upstream prevention-override dairy/consumer_household/land: found 0"
        in str(caught.value)
    )


def test_a_downstream_land_row_stops_the_build():
    """The absence of a downstream land row is a decision, so it is checked.

    Table 2 has no land column and should not have one -- sending a kilogram
    to landfill returns no land and occupies none -- and an absent row already
    resolves to zero through the documented lookup order. Without this check
    the difference between "deliberately absent" and "somebody forgot" is a
    paragraph in a docstring.
    """
    data = build_module.build()
    assert not [row for row in data["downstream"] if row["metric"] == "land"]

    data["downstream"].append({
        "destination": "landfill", "sector": None, "food_category": None,
        "metric": "land", "value_per_kg": "0.5000000000",
        "source_note": "invented", "data_quality": "invented",
    })

    with pytest.raises(SystemExit) as caught:
        build_module._assert_no_land_downstream_rows(data)

    assert "downstream `land` rows for ['landfill']" in str(caught.value)


def test_a_data_quality_tag_too_long_for_its_column_stops_the_build():
    """A valid JSON file the loader cannot load is still a broken build.

    `factor_upstream.data_quality` is `VARCHAR(32)`. This check exists because
    a thirty-three-character tag got as far as a MySQL `Data too long` error
    inside `tests/db/test_load_upstream_factors_draft.py`, several minutes into
    a load, with nothing between the build and that point saying anything was
    wrong. The build is where it can be said cheaply.
    """
    data = build_module.build()
    data["upstream"][0]["data_quality"] = "x" * 33

    with pytest.raises(SystemExit) as caught:
        build_module._assert_completeness(data)

    message = str(caught.value)
    assert "is 33 characters" in message
    assert "VARCHAR(32)" in message


# ------------------------------------------- the equivalence ladders (v1.71)


def _equivalences():
    return {row["code"]: row for row in build_module.build()["equivalences"]}


def test_the_built_ladders_pass_their_own_check():
    """The negative case for every test below. Without it a check that raised
    unconditionally would pass all five."""
    build_module._assert_ladders_are_well_formed(build_module.build())


def test_the_clients_own_units_are_still_at_the_top_of_each_ladder():
    """The promise this package was given: *do not replace the client's
    units*. Both of the client's own conversions keep their factor, their
    wording and the lowest `sort_order` in their family, which is the position
    the selection tries first and the one it falls back to.

    Asserted on the factor as well as on the position, because "still at the
    top" is not worth much if the number under it has moved."""
    rows = _equivalences()

    assert rows["vehicles_year"]["value_per_unit"] == "0.0004149378"  # 1 / 2410
    assert rows["olympic_pools"]["value_per_unit"] == "4.000E-7"      # 1 / 2,500,000
    assert rows["meals"]["value_per_unit"] == "2.2222222222"          # 1 / 0.45

    for code, family in (("vehicles_year", "vehicles"),
                         ("olympic_pools", "water_volume")):
        family_rows = [row for row in rows.values() if row.get("family") == family]
        assert rows[code]["sort_order"] == min(
            row["sort_order"] for row in family_rows
        ), f"{code} is no longer the first rung its family tries"


def test_the_vehicle_day_is_the_clients_own_year_divided_by_365():
    """It needs no new source, which is the whole reason it is the rung that
    was added: it is the client's own 2.41 t CO2e per vehicle per year spread
    over that year's days. Checked as exact `Decimal` arithmetic on the
    client's own divisor rather than against a transcribed constant, so that a
    figure typed in by hand cannot drift from the row above it."""
    rows = _equivalences()

    assert Decimal(rows["vehicles_day"]["value_per_unit"]) == (
        Decimal(365) / Decimal(2410)
    ).quantize(Decimal("1E-10"))
    #: 6.60 kg CO2e a day, which is what the source_note says it is.
    assert (Decimal(1) / Decimal(rows["vehicles_day"]["value_per_unit"])).quantize(
        Decimal("0.01")
    ) == Decimal("6.60")


def test_every_rung_a_ladder_added_states_where_its_number_came_from():
    """"Be creative" licenses the choice of rung, not an unsourced number.
    Every row added here either derives from the client's own figure or states
    the arithmetic it was built from, and the two rows that are assumptions
    rather than measurements say `PLACEHOLDER` in the same way `km_driven`
    always did."""
    rows = _equivalences()

    assert "divided by 365" in rows["vehicles_day"]["source_note"]
    assert "8 m x 4 m x 1.5 m" in rows["backyard_pools"]["source_note"]
    assert "48,000 litres" in rows["backyard_pools"]["source_note"]
    assert rows["showers"]["source_note"].startswith("PLACEHOLDER")
    assert "9 litres a minute" in rows["showers"]["source_note"]
    assert "O-3" in rows["showers"]["source_note"]


def test_every_rung_has_a_singular_sentence():
    """A ladder drives the displayed number toward 1 by design, so `1 Olympic
    swimming pools of water` stops being an occasional embarrassment and
    becomes the normal case. Asserted on every equivalence in the set, not
    only the rungs: `meals` is not a rung and still prints `1 meals` without
    one."""
    for code, row in _equivalences().items():
        assert row.get("label_template_one"), f"{code} has no singular sentence"
        assert "{value}" in row["label_template_one"], code


def test_a_ladder_in_the_wrong_order_stops_the_build():
    """The failure mode that has no other alarm: rungs are tried in
    `sort_order` and the first that reaches one wins, so a ladder numbered
    smallest-unit-first never climbs. Every submission ever made would read in
    showers, and the data would be perfectly valid."""
    data = build_module.build()
    rows = {row["code"]: row for row in data["equivalences"]}
    rows["olympic_pools"]["sort_order"], rows["showers"]["sort_order"] = (
        rows["showers"]["sort_order"], rows["olympic_pools"]["sort_order"],
    )

    with pytest.raises(SystemExit) as caught:
        build_module._assert_ladders_are_well_formed(data)

    message = str(caught.value)
    assert "LARGEST UNIT (smallest value_per_unit) FIRST" in message
    assert "water_volume" in message


def test_a_banded_bottom_rung_stops_the_build():
    """The other half of the same rule, and the one that reintroduces the
    original defect exactly: with every rung banded, a value below all of them
    falls back to the family's FIRST row -- the largest unit, which is the one
    that reads `0`."""
    data = build_module.build()
    rows = {row["code"]: row for row in data["equivalences"]}
    rows["showers"]["min_value"] = "1.0000000000"

    with pytest.raises(SystemExit) as caught:
        build_module._assert_ladders_are_well_formed(data)

    message = str(caught.value)
    assert "its last rung (showers) carries a min_value" in message


def test_a_ladder_drawing_on_two_metrics_stops_the_build():
    """A ladder is one quantity at several sizes. Two metrics in one family
    are two different facts wearing one name, and which one the reader is
    shown would be decided by magnitude."""
    data = build_module.build()
    rows = {row["code"]: row for row in data["equivalences"]}
    rows["showers"]["source_metric"] = "co2e"

    with pytest.raises(SystemExit) as caught:
        build_module._assert_ladders_are_well_formed(data)

    assert "draws on more than one metric" in str(caught.value)


def test_a_band_on_a_row_with_no_family_stops_the_build():
    """`meals` is deliberately not a rung of anything. A band on it is a rule
    that can never fire -- selection only ever happens within a family -- and
    the database refuses the row outright, so the build must refuse it first
    rather than at INSERT time."""
    data = build_module.build()
    rows = {row["code"]: row for row in data["equivalences"]}
    rows["meals"]["min_value"] = "1.0000000000"

    with pytest.raises(SystemExit) as caught:
        build_module._assert_ladders_are_well_formed(data)

    assert "carries a band and no family" in str(caught.value)
