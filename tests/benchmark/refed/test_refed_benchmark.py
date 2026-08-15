"""Check this engine against ReFED's Impact Calculator, an external benchmark.

Every other correctness check in this repository descends from our own
contract: the golden cases, the fixtures and the engine were written by the
same people from the same document, so they share one ancestor and cannot
catch a mistake that was made in the document.  This test does not.  It runs
one scenario through two independent implementations:

  * ours       -- ``engine.calculate`` over a FactorBundle whose factors are
                  per kilogram, split into upstream and downstream, summed as
                  ``Decimal`` through a stored formula string;
  * ReFED's    -- quantity divided by 907.185 to reach US short tons, then
                  multiplied by a single combined factor per destination and
                  summed as a float.

The two arrive at the same number by different routes, from data published in
two separate files that we obtained separately:

  ``impact_calculator_conversion_factors.csv``
      The "Download factors" file behind ReFED's Impact Calculator,
      https://refed-roadmap.s3-us-west-2.amazonaws.com/csv/public_downloads/
      impact_calculator/impact_calculator_conversion_factors.csv
      This is what ``build_refed_benchmark.py`` turns into our factor set.  It
      carries upstream and downstream as separate columns.

  ``refed_calculator_totals.json``
      The dataset ReFED's own calculator page computes with, lifted verbatim
      from the JSON blob in its client bundle
      (https://insights-engine.refed.org/impact-calculator, webpack module
      36573 of the chunk the page loads; the chunk's hash changes when ReFED
      redeploys, so re-fetch the page and search its chunks for
      ``total_100_year_mtco2e_footprint_per_ton`` rather than the URL below).
      It carries only combined totals, never the split.

That the CSV's upstream + downstream reproduces this file's totals for all 429
cells -- worst relative difference 2.4e-16, i.e. float rounding -- is itself
the check that we read ReFED's split correctly.  ``test_refed_split_sums_to_
refed_totals`` re-runs it here.

WHAT DISAGREEMENT WOULD MEAN
----------------------------
An assertion failure here is a real finding: either our engine computes
differently from ReFED's, or the factor set was derived wrongly.  It is not
noise.  The tolerances below are set at the level of ``float`` accumulation
error and the 10-decimal-place truncation our DECIMAL(20,10) columns impose --
about 1e-9 relative -- not at a level chosen to make the test pass.

WHAT THIS DOES NOT PROVE
------------------------
Only the four metrics ReFED and this calculator have in common are compared.
ReFED's meals-recovered figure is not represented: our metric table is global
and adding a meals metric would change what the New Zealand set reports.
``gwp_horizon`` is not exercised: ReFED's methane mass is identical at 20 and
100 years in all 429 of its rows, and its 20-year CO2e is not in the published
CSV, so no horizon behaviour can be benchmarked against it.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from engine.bundle import FactorBundle
from engine.calculate import calculate
from engine.types import CalculationRequest, EntryInput, ScenarioLine

HERE = Path(__file__).resolve().parent
KG_PER_SHORT_TON = 907.185  # ReFED's own constant; see build_refed_benchmark.py
LITRES_PER_US_GALLON = 3.785411784

# The metrics the two calculators have in common, and the ReFED column in
# refed_calculator_totals.json each one must reproduce, with the factor that
# converts our per-kilogram total back into ReFED's reporting unit.
COMPARABLE = {
    # our metric: (ReFED total column, our unit -> ReFED unit divisor)
    "co2e": ("total_100_year_mtco2e_footprint_per_ton", 1000.0),
    "ch4": ("total_100_year_mtch4_footprint_per_ton", 1000.0),
    "water": ("gallons_water_footprint_per_ton", LITRES_PER_US_GALLON),
    "cost": ("total_100_year_mtco2e_scc_footprint_per_ton", 1.0),
}

# ReFED sector / food type / destination for the recommended scenario, in the
# slugs ReFED's own dataset uses.
REFED_SECTOR = "retail"
REFED_FOOD = "produce"
OUR_FOOD_CATEGORY = "refed_produce"
OUR_SECTOR = "refed_retail"

# The recommended scenario.  Two destinations in `current`, four in
# `alternative`, mass-balanced at 10,000 kg on both sides.  It exercises:
#   * a generic upstream row               (Landfill, Composting)
#   * a destination-specific upstream row  (Donations, at ~27% of the base)
#   * a positive downstream factor         (Landfill)
#   * a negative downstream factor         (Composting, an avoided-emissions
#                                           credit -- charts must render it)
#   * a full 100% offset                   (Prevention, upstream and downstream
#                                           both zero)
#   * summation across four lines, and the current-minus-alternative net benefit
SCENARIO_CURRENT = {"refed_landfill": "8000.000", "refed_composting": "2000.000"}
SCENARIO_ALTERNATIVE = {
    "refed_landfill": "2000.000",
    "refed_composting": "2000.000",
    "refed_donations": "3000.000",
    "refed_prevention": "3000.000",
}
# Same scenario in ReFED's destination slugs, for the independent calculation.
REFED_CURRENT = {"landfill": 8000.0, "composting": 2000.0}
REFED_ALTERNATIVE = {
    "landfill": 2000.0,
    "composting": 2000.0,
    "donations": 3000.0,
    "prevention": 3000.0,
}

# Tolerance is derived, not chosen.  Two error sources, both bounded:
#
#   * Our factors are rounded to ten decimal places on the way into
#     DECIMAL(20,10), so each of `upstream` and `downstream` carries at most
#     5e-11 per kilogram.  A line of `qty` kilograms therefore contributes at
#     most 1e-10 * qty to the metric total, in that metric's own unit.  This
#     dominates for methane, whose factors are around 5e-3 kg/kg, and is
#     invisible for water, whose factors are around 170 L/kg.
#   * ReFED accumulates in double-precision float, worth a few 1e-16 relative.
#
# Nothing here is tuned to make the test pass; widening either term would mean
# our storage precision had changed.
QUANTISATION_PER_KG = 1e-10  # 5e-11 on upstream + 5e-11 on downstream
FLOAT_RELATIVE = 1e-12


def _read(name: str) -> object:
    return json.loads((HERE / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def taxonomy() -> dict:
    return _read("refed-benchmark-taxonomy.json")


@pytest.fixture(scope="module")
def factors() -> dict:
    return _read("refed-benchmark-factors.json")


@pytest.fixture(scope="module")
def refed_totals() -> list[dict]:
    return _read("refed_calculator_totals.json")


@pytest.fixture(scope="module")
def bundle(taxonomy: dict, factors: dict) -> FactorBundle:
    return FactorBundle.from_json(compose_bundle(taxonomy, factors))


def compose_bundle(taxonomy: dict, factors: dict) -> dict:
    """Compose a §10.2 bundle from the two fixture files.

    The re-keying §10.2 describes, with no arithmetic in it. The New Zealand
    `standard_mix` category and `prevention` destination are carried in
    because they exist in any real database this set is loaded into: the
    factor set names `prevention` on its zero upstream rows so that the
    publish-time prevention check can pass, and a bundle must have exactly one
    standard mix for `standard_mix_code()` to be answerable.
    """
    return {
        "version_label": factors["version_label"],
        "is_mock": factors["is_mock"],
        "sectors": [
            {"code": s["code"], "name": s["name"], "sort_order": s["sort_order"]}
            for s in taxonomy["sectors"]
        ],
        "food_categories": [
            {
                "code": "standard_mix",
                "name": "Standard mix",
                "is_standard_mix": True,
                "sort_order": 5,
            }
        ] + [
            {
                "code": f["code"],
                "name": f["name"],
                "is_standard_mix": f["is_standard_mix"],
                "sort_order": f["sort_order"],
            }
            for f in taxonomy["food_categories"]
        ],
        "destination_groups": [
            {"code": "reuse", "name": "Reuse", "is_waste": False, "sort_order": 10},
            {"code": "recycle_recovery", "name": "Recycle and recovery",
             "is_waste": True, "sort_order": 20},
            {"code": "disposal", "name": "Disposal", "is_waste": True,
             "sort_order": 30},
        ],
        "destinations": [
            {"code": "prevention", "name": "Prevention", "group": "reuse",
             "sort_order": 5}
        ] + [
            {
                "code": d["code"],
                "name": d["name"],
                "group": d["group_code"],
                "sort_order": d["sort_order"],
            }
            for d in taxonomy["destinations"]
        ],
        "metrics": [
            {"code": "co2e", "name": "Greenhouse gases", "unit": "kg CO2e",
             "display_unit": "kg CO₂e", "display_precision": 1, "sort_order": 10},
            {"code": "ch4", "name": "Methane", "unit": "kg CH4",
             "display_unit": None, "display_precision": 1, "sort_order": 20},
            {"code": "water", "name": "Water", "unit": "L",
             "display_unit": "L", "display_precision": 0, "sort_order": 30},
            {"code": "cost", "name": "Cost", "unit": "NZD",
             "display_unit": None, "display_precision": 0, "sort_order": 40},
            {"code": "mass", "name": "Mass", "unit": "kg",
             "display_unit": "kg", "display_precision": 1, "sort_order": 50},
        ],
        "constants": factors["constants"],
        "formulas": factors["formulas"],
        "upstream": factors["upstream"],
        "downstream": factors["downstream"],
        "equivalences": factors["equivalences"],
    }


def refed_total(totals: list[dict], column: str, lines: dict[str, float]) -> float:
    """ReFED's own arithmetic, transcribed from their calculator's bundle.

        acc[metric] += quantity / unit.conversion * factor[metric]

    where `unit.conversion` is 907.185 for kilograms and `factor` is looked up
    on (sector, food department, destination).
    """
    index = {
        (r["sector"], r["refed_food_department"], r["destination"]): r
        for r in totals
    }
    accumulator = 0.0
    for destination, quantity in lines.items():
        row = index[(REFED_SECTOR, REFED_FOOD, destination)]
        accumulator += quantity / KG_PER_SHORT_TON * row[column]
    return accumulator


def build_request() -> CalculationRequest:
    def lines(spec: dict[str, str]) -> tuple[ScenarioLine, ...]:
        return tuple(
            ScenarioLine(destination_code=code, qty_kg=Decimal(qty))
            for code, qty in spec.items()
        )

    return CalculationRequest(
        entries=(
            EntryInput(
                sector_code=OUR_SECTOR,
                food_category_code=OUR_FOOD_CATEGORY,
                current=lines(SCENARIO_CURRENT),
                alternative=lines(SCENARIO_ALTERNATIVE),
            ),
        ),
        gwp_horizon=100,
    )


def relative_difference(ours: float, theirs: float) -> float:
    if theirs == 0.0:
        return abs(ours)
    return abs(ours - theirs) / abs(theirs)


def tolerance(qty_kg: float, divisor: float, theirs: float) -> float:
    """The largest disagreement our own storage precision can account for.

    Expressed in ReFED's reporting unit, hence the same `divisor` that
    converts our total into it.
    """
    return QUANTISATION_PER_KG * qty_kg / divisor + FLOAT_RELATIVE * abs(theirs)


def test_the_bundle_is_internally_consistent(bundle: FactorBundle) -> None:
    assert bundle.validate() == []


def test_refed_split_sums_to_refed_totals(refed_totals: list[dict]) -> None:
    """The CSV's upstream + downstream must reproduce ReFED's own totals.

    This is what licenses us to load the split into two tables. If ReFED ever
    republishes the CSV and the totals file out of step, this fails before any
    engine comparison does, which is the difference between a data problem and
    an engine problem.
    """
    import csv as _csv

    text = (HERE / "impact_calculator_conversion_factors.csv").read_text(
        encoding="utf-8-sig"
    )
    rows = [r for r in _csv.DictReader(text.split("\n")[3:]) if r.get("sector")]
    sectors = {"Farm": "farm", "Foodservice": "foodservice",
               "Manufacturing": "manufacturing", "Residential": "residential",
               "Retail": "retail"}
    foods = {"Breads & Bakery": "breads-bakery", "Dairy & Eggs": "dairy-eggs",
             "Dry Goods": "dry-goods",
             "Fresh Meat & Seafood": "fresh-meat-seafood", "Frozen": "frozen",
             "Prepared Foods": "prepared-foods", "Produce": "produce",
             "Ready-To-Drink Beverages": "ready-to-drink-beverages",
             "Standard Mix": "standard-mix"}
    destinations = {"Anaerobic Digestion": "anaerobic-digestion",
                    "Animal Feed": "animal-feed", "Composting": "composting",
                    "Donations": "donations", "Dumping": "refuse-discards",
                    "Incineration": "incineration",
                    "Industrial Uses": "biomaterial-processing",
                    "Land Application": "land-application",
                    "Landfill": "landfill", "Not Harvested": "not-harvested",
                    "Prevention": "prevention", "Sewer": "sewer"}
    index = {
        (r["sector"], r["refed_food_department"], r["destination"]): r
        for r in refed_totals
    }
    pairs = [
        ("upstream_100_year_mtco2e_footprint_per_ton",
         "downstream_100_year_mtco2e_footprint_per_ton",
         "total_100_year_mtco2e_footprint_per_ton"),
        ("upstream_mtch4_footprint_per_ton",
         "downstream_mtch4_footprint_per_ton",
         "total_100_year_mtch4_footprint_per_ton"),
        ("upstream_100_year_mtco2e_scc_footprint_per_ton",
         "downstream_100_year_mtco2e_scc_footprint_per_ton",
         "total_100_year_mtco2e_scc_footprint_per_ton"),
    ]
    compared = 0
    for row in rows:
        key = (sectors[row["sector"]], foods[row["food_type"]],
               destinations[row["destination"]])
        theirs = index.get(key)
        if theirs is None:
            # ReFED publishes three Farm / Industrial Uses cells in the CSV
            # that its own calculator does not offer. All zero; nothing to
            # compare.
            assert all(
                float(row[column]) == 0.0
                for triple in pairs for column in triple[:2]
            ), key
            continue
        for upstream, downstream, total in pairs:
            summed = float(row[upstream]) + float(row[downstream])
            assert relative_difference(summed, theirs[total]) < 1e-12, (
                key, total, summed, theirs[total])
            compared += 1
    assert compared == 429 * len(pairs)


@pytest.mark.parametrize("metric", sorted(COMPARABLE))
@pytest.mark.parametrize("scenario", ["current", "alternative"])
def test_engine_agrees_with_refed(
    bundle: FactorBundle, refed_totals: list[dict], metric: str, scenario: str
) -> None:
    column, divisor = COMPARABLE[metric]
    result = calculate(build_request(), bundle)
    side = getattr(result.totals, scenario)
    ours = float(side.metrics[metric].total) / divisor
    lines = REFED_CURRENT if scenario == "current" else REFED_ALTERNATIVE
    theirs = refed_total(refed_totals, column, lines)
    assert abs(ours - theirs) <= tolerance(sum(lines.values()), divisor, theirs), (
        f"{metric} {scenario}: engine {ours!r} vs ReFED {theirs!r}, "
        f"relative {relative_difference(ours, theirs)!r}")


@pytest.mark.parametrize("metric", sorted(COMPARABLE))
def test_net_benefit_agrees_with_refed(
    bundle: FactorBundle, refed_totals: list[dict], metric: str
) -> None:
    """ReFED's net benefit is current minus alternative, and so is ours."""
    column, divisor = COMPARABLE[metric]
    result = calculate(build_request(), bundle)
    ours = float(result.totals.net_benefit[metric]) / divisor
    theirs = (
        refed_total(refed_totals, column, REFED_CURRENT)
        - refed_total(refed_totals, column, REFED_ALTERNATIVE)
    )
    qty = sum(REFED_CURRENT.values()) + sum(REFED_ALTERNATIVE.values())
    assert abs(ours - theirs) <= tolerance(qty, divisor, theirs), (
        f"net_benefit {metric}: engine {ours!r} vs ReFED {theirs!r}, "
        f"relative {relative_difference(ours, theirs)!r}")


def test_prevention_is_a_complete_offset(bundle: FactorBundle) -> None:
    """The offset must come from the factors, not from a special case."""
    for metric in COMPARABLE:
        assert bundle.upstream(
            OUR_SECTOR, OUR_FOOD_CATEGORY, "refed_prevention", metric
        ) == Decimal("0")
        assert bundle.downstream(
            "refed_prevention", OUR_SECTOR, OUR_FOOD_CATEGORY, metric
        ) == Decimal("0")


def test_composting_carries_a_negative_downstream(bundle: FactorBundle) -> None:
    """Guards the scenario: if this stops being negative it no longer tests
    that a downstream credit survives the summation."""
    assert bundle.downstream(
        "refed_composting", OUR_SECTOR, OUR_FOOD_CATEGORY, "co2e"
    ) < Decimal("0")


# ==========================================================================
# The 5 x 9 shape, and the two scenarios run by hand against ReFED's site
# ==========================================================================
#
# Until contract v1.31 this fixture had one sector row and 39 food categories
# named `refed_farm_dry_goods`, `refed_retail_produce` and so on, because
# `factor_downstream` had no sector column and ReFED's downstream factors
# differ by sector in 82 of their 102 (food type, destination) groups. The
# stage was folded into the food category's code. That was numerically
# lossless -- these very tests agreed with ReFED before and after -- and
# structurally wrong, which is what the tests below now hold in place.
#
# `test_engine_agrees_with_refed` above is the arithmetic check and its
# expectations were **not** adjusted for the rebuild. These add the shape, and
# two scenarios whose expected answers came off ReFED's own screen rather than
# out of any file in this repository.

REFED_SECTOR_CODES = {
    "refed_farm", "refed_manufacturing", "refed_retail",
    "refed_foodservice", "refed_residential",
}
REFED_FOOD_CODES = {
    "refed_breads_bakery", "refed_dairy_eggs", "refed_dry_goods",
    "refed_fresh_meat_seafood", "refed_frozen", "refed_prepared_foods",
    "refed_produce", "refed_ready_to_drink_beverages", "refed_standard_mix",
}
#: The six (sector, food type) pairs ReFED does not publish and its own
#: calculator does not offer. Farm has Dry Goods, Produce and Standard Mix.
UNPUBLISHED_PAIRS = [
    ("refed_farm", food) for food in sorted(
        REFED_FOOD_CODES
        - {"refed_dry_goods", "refed_produce", "refed_standard_mix"}
    )
]


def test_the_fixture_has_refeds_own_shape(taxonomy: dict) -> None:
    """Five sectors and nine food categories, not one and thirty-nine.

    Asserted on the codes rather than on the counts: a fixture that had
    regressed to the folded encoding would still have 39 categories and could
    still count to five if a loop went wrong, but it cannot produce
    `refed_produce` without a food category that is a food.
    """
    assert {s["code"] for s in taxonomy["sectors"]} == REFED_SECTOR_CODES
    assert {f["code"] for f in taxonomy["food_categories"]} == REFED_FOOD_CODES
    #: No code may carry a stage *and* a food. That is the defect itself, it
    #: survives a correct count, so it is asserted directly.
    for food in taxonomy["food_categories"]:
        for stage in ("farm", "retail", "foodservice", "manufacturing",
                      "residential"):
            assert stage not in food["code"], food["code"]


def test_every_downstream_row_states_both_of_its_nullable_dimensions(
    factors: dict,
) -> None:
    """Section 2.2's two nullable dimensions are both filled in on every row.

    Leaving the 20 sector-invariant groups NULL would save rows and would
    answer a Farm / Frozen lookup -- a pair ReFED does not publish -- with
    another sector's number. `test_an_unpublished_pair_prices_at_zero...`
    below is what that would break; this is what makes it true.
    """
    assert factors["downstream"], "no downstream rows at all"
    for row in factors["downstream"]:
        assert row["sector"] in REFED_SECTOR_CODES, row
        assert row["food_category"] in REFED_FOOD_CODES, row


def test_the_sector_dimension_carries_real_information(factors: dict) -> None:
    """The rebuild is only worth doing if the numbers do differ by sector. If
    this ever stops holding, the column is unnecessary here and the fixture is
    hiding a mapping error rather than expressing ReFED's data."""
    by_group: dict = {}
    for row in factors["downstream"]:
        key = (row["destination"], row["food_category"], row["metric"])
        by_group.setdefault(key, set()).add(row["value_per_kg"])
    varying = sum(1 for values in by_group.values() if len(values) > 1)
    #: ReFED's downstream differs by sector in 82 of its 102 (food type,
    #: destination) groups, across the three metrics it splits.
    assert varying >= 82, varying


@pytest.mark.parametrize("sector,food", UNPUBLISHED_PAIRS)
@pytest.mark.parametrize("metric", sorted(COMPARABLE))
def test_an_unpublished_pair_prices_at_zero_rather_than_borrowing(
    bundle: FactorBundle, sector: str, food: str, metric: str
) -> None:
    """A pair ReFED does not publish must return nothing, not something.

    The 1 x 39 encoding made these six unreachable: there was no
    `refed_farm_frozen` category to choose. A 5 x 9 taxonomy offers every
    combination, so the guarantee has to be that the *factors* are silent --
    an obvious zero on screen -- rather than a plausible figure lifted from
    Retail. A NULL-sector row anywhere in this set would break this.
    """
    for destination in ("refed_landfill", "refed_composting", "refed_donations"):
        assert bundle.downstream(destination, sector, food, metric) == Decimal("0")
        assert bundle.upstream(sector, food, destination, metric) == Decimal("0")


# --------------------------------------------------------------------------
# Two scenarios entered by hand on https://insights-engine.refed.org/
# impact-calculator and read off the screen. They are the only expectations in
# this repository that were neither computed by our code nor derived from a
# file we hold, so they are the check that survives a mistake made in the CSV
# reader and the totals reader at once.
#
# The figures below are transcribed at the precision ReFED's interface shows,
# which is why the tolerance is relative and generous compared with
# `QUANTISATION_PER_KG` above: it bounds a transcription, not an arithmetic.
# --------------------------------------------------------------------------

SCREEN_RELATIVE = 1e-8

HAND_RUN_FARM = {
    "our_sector": "refed_farm",
    "our_food": "refed_standard_mix",
    "refed_sector": "farm",
    "refed_food": "standard-mix",
    "lines": {
        "donations": 200.0,
        "animal-feed": 100.0,
        "anaerobic-digestion": 100.0,
        "landfill": 200.0,
        "refuse-discards": 100.0,
        "sewer": 300.0,
    },
    #: Metric tonnes CO2e, metric tonnes CH4, US gallons, US dollars -- ReFED's
    #: own reporting units, exactly as its screen renders them.
    "screen": {
        "co2e": 0.68008173626,
        "ch4": 0.01802778698,
        "water": 36629.3776,
        "cost": 110.18175284,
    },
}
HAND_RUN_RETAIL = {
    "our_sector": "refed_retail",
    "our_food": "refed_standard_mix",
    "refed_sector": "retail",
    "refed_food": "standard-mix",
    "lines": {"donations": 80.0, "animal-feed": 100.0},
    #: Read off the screen at the precision ReFED displays for this scenario:
    #: two decimal places on tonnes, whole gallons, whole dollars. Asserted as
    #: such below rather than pretending to more digits than were shown.
    "screen": {"co2e": 0.52, "water": 47179.0, "cost": 150.0},
    "screen_places": {"co2e": 2, "water": 0, "cost": 0},
}
#: The destination slug ReFED uses -> the code this fixture uses.
OUR_DESTINATION = {
    "donations": "refed_donations",
    "animal-feed": "refed_animal_feed",
    "anaerobic-digestion": "refed_anaerobic_digestion",
    "landfill": "refed_landfill",
    "refuse-discards": "refed_dumping",
    "sewer": "refed_sewer",
}


def _hand_run_request(case: dict) -> CalculationRequest:
    return CalculationRequest(
        entries=(
            EntryInput(
                sector_code=case["our_sector"],
                food_category_code=case["our_food"],
                current=tuple(
                    ScenarioLine(
                        destination_code=OUR_DESTINATION[slug],
                        qty_kg=Decimal(str(qty)),
                    )
                    for slug, qty in case["lines"].items()
                ),
                alternative=(),
            ),
        ),
        gwp_horizon=100,
    )


def _hand_run_ours(bundle: FactorBundle, case: dict, metric: str) -> float:
    _column, divisor = COMPARABLE[metric]
    result = calculate(_hand_run_request(case), bundle)
    return float(result.totals.current.metrics[metric].total) / divisor


@pytest.mark.parametrize("metric", sorted(HAND_RUN_FARM["screen"]))
def test_the_farm_scenario_matches_what_refeds_own_calculator_showed(
    bundle: FactorBundle, metric: str
) -> None:
    """Farm / Standard Mix, 1000 kg over six destinations.

    Six destinations rather than two, four metrics rather than one, and a
    sector that is *not* Retail -- which is the point after the rebuild: under
    the 1 x 39 encoding "Farm" was a food category, and this scenario could
    only be expressed by choosing a category named after a stage.
    """
    ours = _hand_run_ours(bundle, HAND_RUN_FARM, metric)
    theirs = HAND_RUN_FARM["screen"][metric]

    assert relative_difference(ours, theirs) < SCREEN_RELATIVE, (
        f"{metric}: engine {ours!r} vs ReFED's screen {theirs!r}")


@pytest.mark.parametrize("metric", sorted(HAND_RUN_RETAIL["screen"]))
def test_the_retail_scenario_matches_what_refeds_own_calculator_showed(
    bundle: FactorBundle, metric: str
) -> None:
    """Retail / Standard Mix, 180 kg over two destinations, both of them
    diversions rather than disposal routes."""
    ours = _hand_run_ours(bundle, HAND_RUN_RETAIL, metric)
    places = HAND_RUN_RETAIL["screen_places"][metric]
    theirs = HAND_RUN_RETAIL["screen"][metric]

    assert round(ours, places) == theirs, (
        f"{metric}: engine {ours!r} rounds to {round(ours, places)!r}, "
        f"ReFED's screen showed {theirs!r}")


@pytest.mark.parametrize("case", [HAND_RUN_FARM, HAND_RUN_RETAIL],
                         ids=["farm", "retail"])
@pytest.mark.parametrize("metric", sorted(COMPARABLE))
def test_the_hand_run_scenarios_also_match_refeds_published_dataset(
    bundle: FactorBundle, refed_totals: list, case: dict, metric: str
) -> None:
    """The same two scenarios against the full-precision totals file.

    The screen tests above bound a transcription at 1e-8; this bounds the
    arithmetic at the storage precision, which is where a rebuild error would
    actually show. Both are needed: the file could be misread the same way
    twice, and the screen carries only eleven digits.
    """
    column, divisor = COMPARABLE[metric]
    index = {
        (r["sector"], r["refed_food_department"], r["destination"]): r
        for r in refed_totals
    }
    theirs = sum(
        qty / KG_PER_SHORT_TON
        * index[(case["refed_sector"], case["refed_food"], slug)][column]
        for slug, qty in case["lines"].items()
    )
    ours = _hand_run_ours(bundle, case, metric)

    assert abs(ours - theirs) <= tolerance(
        sum(case["lines"].values()), divisor, theirs
    ), (f"{metric}: engine {ours!r} vs ReFED {theirs!r}, "
        f"relative {relative_difference(ours, theirs)!r}")
