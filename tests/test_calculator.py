"""The calculation itself (contract §4.2, §4.3).

Every test here is built on `BUNDLE_JSON` below -- a §10.2 bundle document fed
through `FactorBundle.from_json`, not a hand-assembled `FactorBundle`. That is
deliberate: "metrics are data, not code" is a claim about what happens when a
*row* is added, and a test that reaches past the loader to construct the
dataclass directly cannot make that claim. `test_a_metric_added_to_the_bundle_
needs_no_code_change` adds one metric row, one formula row and two factor rows
to a copy of that document and asserts the new metric appears in the result --
which is exactly the INSERT the design promises is the whole cost of a metric.

The three tests this file replaced asserted the walking skeleton's behaviour:
one metric, `lines[0]`, no constants, no summation. Two of their expected
values were only correct while a `compost` line and a `landfill` line drew the
same upstream factor -- the assumption open item O-7 removed.
"""

from __future__ import annotations

import copy
import re
from decimal import Decimal
from pathlib import Path

import pytest

from engine.bundle import FactorBundle
from engine.calculate import calculate, calculate_scenario
from engine.errors import UnknownCodeError
from engine.types import CalculationRequest, EntryInput, ScenarioLine

# --------------------------------------------------------------- the bundle

#: A §10.2 bundle carrying **three** metrics, two of which are not `co2e`.
#: `ch4`'s formula is the only one in the tree that references the special
#: `const_GWP_CH4` binding, and `mass` is the one metric a reader can check by
#: hand. `prevention` carries an upstream row of its own (O-7) for
#: `primary_production`/`vegetables` and deliberately **not** for
#: `processing`/`dairy`, so one test can prove the exact-match branch and
#: another the generic fallback against the same document.
BUNDLE_JSON = {
    "version_label": "TEST-v0",
    "is_mock": True,
    "sectors": [{"code": "processing"}, {"code": "primary_production"}],
    "food_categories": [
        {"code": "standard_mix", "is_standard_mix": True},
        {"code": "dairy"},
        {"code": "vegetables"},
    ],
    "destination_groups": [{"code": "reuse"}, {"code": "disposal"}],
    "destinations": [
        {"code": "landfill", "group": "disposal"},
        {"code": "compost", "group": "disposal"},
        {"code": "animal_feed", "group": "reuse"},
        {"code": "prevention", "group": "reuse"},
    ],
    "metrics": [
        {"code": "co2e", "unit": "kg CO2e", "display_precision": 1, "sort_order": 10},
        {"code": "ch4", "unit": "kg CH4", "display_precision": 2, "sort_order": 20},
        {"code": "mass", "unit": "kg", "display_precision": 1, "sort_order": 30},
    ],
    "constants": [
        {"code": "GWP_CH4_100", "value": "28.0000000000"},
        {"code": "GWP_CH4_20", "value": "84.0000000000"},
        {"code": "FOOD_VALUE_PER_KG", "value": "2.5000000000"},
    ],
    "formulas": [
        {"metric": "co2e", "expression": "qty_kg * (upstream + downstream)"},
        {
            "metric": "ch4",
            "expression": "qty_kg * (upstream + downstream) * const_GWP_CH4",
        },
        {"metric": "mass", "expression": "qty_kg"},
    ],
    "upstream": [
        {
            "sector": "processing",
            "food_category": "dairy",
            "destination": None,
            "metric": "co2e",
            "value_per_kg": "1.9000000000",
        },
        {
            "sector": "primary_production",
            "food_category": "vegetables",
            "destination": None,
            "metric": "co2e",
            "value_per_kg": "0.4500000000",
        },
        #: O-7. Prevented food was never produced, so none of the upstream
        #: burden is attributable to it. This row is what makes `prevention`
        #: a 100% offset rather than a 79% one.
        {
            "sector": "primary_production",
            "food_category": "vegetables",
            "destination": "prevention",
            "metric": "co2e",
            "value_per_kg": "0.0000000000",
        },
        {
            "sector": "processing",
            "food_category": "standard_mix",
            "destination": None,
            "metric": "co2e",
            "value_per_kg": "2.6000000000",
        },
    ],
    "downstream": [
        {
            "destination": "landfill",
            "food_category": "dairy",
            "metric": "co2e",
            "value_per_kg": "0.9900000000",
        },
        {
            "destination": "landfill",
            "food_category": None,
            "metric": "co2e",
            "value_per_kg": "0.7000000000",
        },
        {
            "destination": "landfill",
            "food_category": None,
            "metric": "ch4",
            "value_per_kg": "0.0270000000",
        },
        {
            "destination": "compost",
            "food_category": None,
            "metric": "co2e",
            "value_per_kg": "0.2100000000",
        },
        {
            "destination": "animal_feed",
            "food_category": None,
            "metric": "co2e",
            "value_per_kg": "-0.1500000000",
        },
        {
            "destination": "prevention",
            "food_category": None,
            "metric": "co2e",
            "value_per_kg": "0.0000000000",
        },
    ],
    "equivalences": [
        {
            "code": "km_driven",
            "source_metric": "co2e",
            "value_per_unit": "4.1800000000",
            "label_template": "Equivalent to driving {value} km",
            "sort_order": 10,
        }
    ],
}


@pytest.fixture
def bundle() -> FactorBundle:
    loaded = FactorBundle.from_json(copy.deepcopy(BUNDLE_JSON))
    assert loaded.validate() == []
    return loaded


def line(destination: str, qty: str) -> ScenarioLine:
    return ScenarioLine(destination_code=destination, qty_kg=Decimal(qty))


def dairy_entry(current, alternative=None) -> EntryInput:
    return EntryInput(
        sector_code="processing",
        food_category_code="dairy",
        current=current,
        alternative=alternative,
    )


def scenario(bundle, lines, sector="processing", food_category="dairy", horizon=100):
    return calculate_scenario(lines, sector, food_category, bundle, horizon)


# ------------------------------------------------------- every line counts


def test_every_line_of_a_scenario_is_computed(bundle):
    """The defect this file was rewritten for.

    `lines[0]` alone returns a plausible, correctly typed, wrong number, and
    nothing in a response, a fixture shape check or the old suite could
    expose it. 1200 x (1.9 + 0.99) = 3468, 300 x (1.9 - 0.15) = 525.
    """
    result = scenario(bundle, (line("landfill", "1200.000"), line("animal_feed", "300.000")))

    assert result.total_kg == Decimal("1500.000")
    co2e = result.metrics["co2e"]
    assert [row.destination_code for row in co2e.by_destination] == [
        "landfill",
        "animal_feed",
    ]
    assert [row.value for row in co2e.by_destination] == [
        Decimal("3468.0000000000"),
        Decimal("525.0000000000"),
    ]
    #: Σ line_value, not f(line_0) (§4.3).
    assert co2e.total == Decimal("3993.0000000000")


def test_a_negative_downstream_factor_reduces_its_own_line_only(bundle):
    """`animal_feed` is negative on purpose (§2.2). Summing means the offset
    lands on the total; taking `lines[0]` means it never appears at all."""
    with_feed = scenario(
        bundle, (line("landfill", "1200.000"), line("animal_feed", "300.000"))
    )
    without_feed = scenario(bundle, (line("landfill", "1200.000"),))

    assert with_feed.metrics["co2e"].total > without_feed.metrics["co2e"].total
    assert with_feed.metrics["co2e"].by_destination[1].downstream == Decimal("-0.15")


def test_line_order_is_the_request_order(bundle):
    result = scenario(
        bundle,
        (line("animal_feed", "300.000"), line("compost", "100.000"), line("landfill", "1.000")),
    )
    assert [row.destination_code for row in result.metrics["co2e"].by_destination] == [
        "animal_feed",
        "compost",
        "landfill",
    ]


# ------------------------------------------------- metrics are data, not code


def test_every_metric_in_the_bundle_is_computed(bundle):
    result = scenario(bundle, (line("landfill", "1000.000"),))

    #: Iteration order is `bundle.metrics`, which §4.1 requires sorted by
    #: `sort_order`. The dict is keyed by code, and its order is the wire's.
    assert list(result.metrics) == ["co2e", "ch4", "mass"]
    assert result.metrics["ch4"].unit == "kg CH4"
    assert result.metrics["ch4"].display_precision == 2
    #: 1000 x (0 + 0.027) x 28
    assert result.metrics["ch4"].total == Decimal("756.0000000000")
    assert result.metrics["mass"].total == Decimal("1000.0000000000")


def test_a_metric_added_to_the_bundle_needs_no_code_change(bundle):
    """The real test of "metrics are data".

    Not "the metrics currently in the bundle render" -- that passes against a
    hard-coded list too. A metric is added here as rows, the way a staff
    member adds one, and nothing in `engine/` is touched.
    """
    document = copy.deepcopy(BUNDLE_JSON)
    document["metrics"].append(
        {"code": "cost", "unit": "NZD", "display_precision": 0, "sort_order": 40}
    )
    document["formulas"].append(
        {
            "metric": "cost",
            "expression": "qty_kg * (upstream + downstream + const_FOOD_VALUE_PER_KG)",
        }
    )
    document["downstream"].append(
        {
            "destination": "landfill",
            "food_category": None,
            "metric": "cost",
            "value_per_kg": "0.0600000000",
        }
    )
    widened = FactorBundle.from_json(document)
    assert widened.validate() == []

    before = scenario(bundle, (line("landfill", "1000.000"),))
    after = scenario(widened, (line("landfill", "1000.000"),))

    assert "cost" not in before.metrics
    assert list(after.metrics) == ["co2e", "ch4", "mass", "cost"]
    assert after.metrics["cost"].unit == "NZD"
    #: 1000 x (0 + 0.06 + 2.5)
    assert after.metrics["cost"].total == Decimal("2560.0000000000")
    #: The metrics that were already there are untouched by the new row.
    assert after.metrics["co2e"].total == before.metrics["co2e"].total


#: The five metric codes `admin/seed.py` ships. None of them may appear as a
#: string literal anywhere in `engine/`.
SHIPPED_METRIC_CODES = ("co2e", "ch4", "water", "cost", "mass")


@pytest.mark.parametrize("code", SHIPPED_METRIC_CODES)
def test_no_metric_code_appears_in_the_engine(code):
    """A metric that costs one INSERT cannot also cost a call site.

    Searched as a *quoted literal* rather than as a substring, so that the
    word "mass" in a docstring about mass conservation is not a failure and
    `metrics["mass"]` is.
    """
    literal = re.compile(f"""['"]{re.escape(code)}['"]""")
    for path in sorted(Path(__file__).resolve().parents[1].joinpath("engine").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert not literal.search(source), (
            f"{path.name} names the metric {code!r}. Metrics are data: adding one "
            "must mean inserting a row, never editing a call site."
        )


# ------------------------------------------------------------- constants


def test_every_constant_is_bound_as_const_code(bundle):
    """`const_<CODE>` for every row in the bundle, whether or not any shipped
    formula happens to reference it (§4.3)."""
    document = copy.deepcopy(BUNDLE_JSON)
    document["formulas"] = [{"metric": "mass", "expression": "const_FOOD_VALUE_PER_KG"}]
    document["metrics"] = [
        {"code": "mass", "unit": "kg", "display_precision": 1, "sort_order": 10}
    ]
    document["equivalences"] = []
    only_mass = FactorBundle.from_json(document)

    result = scenario(only_mass, (line("landfill", "3.000"), line("compost", "1.000")))
    #: Two lines, each worth the constant.
    assert result.metrics["mass"].total == Decimal("5.0000000000")


@pytest.mark.parametrize(
    "horizon,expected",
    [(100, Decimal("756.0000000000")), (20, Decimal("2268.0000000000"))],
)
def test_const_gwp_ch4_resolves_through_the_requested_horizon(bundle, horizon, expected):
    """1000 x 0.027 x 28 against 1000 x 0.027 x 84. The formula names
    `const_GWP_CH4` and never a horizon (§4.3)."""
    result = scenario(bundle, (line("landfill", "1000.000"),), horizon=horizon)
    assert result.metrics["ch4"].total == expected


def test_the_horizon_reaches_the_engine_through_calculate(bundle):
    """`gwp_horizon` was used for nothing until this task -- it was carried on
    the request, copied onto the result, and never bound."""
    lines = (line("landfill", "1000.000"),)
    at_100 = calculate(CalculationRequest(entries=(dairy_entry(lines),), gwp_horizon=100), bundle)
    at_20 = calculate(CalculationRequest(entries=(dairy_entry(lines),), gwp_horizon=20), bundle)

    assert at_100.gwp_horizon == 100
    assert at_20.gwp_horizon == 20
    assert at_20.totals.current.metrics["ch4"].total == (
        at_100.totals.current.metrics["ch4"].total * 3
    )


def test_an_impossible_horizon_is_refused(bundle):
    request = CalculationRequest(
        entries=(dairy_entry((line("landfill", "1.000"),)),), gwp_horizon=50
    )
    with pytest.raises(ValueError):
        calculate(request, bundle)


# ---------------------------------------------------------- O-7 at the call site


def test_a_prevention_line_draws_its_own_upstream_factor(bundle):
    """The upstream lookup takes the destination (§4.1, v1.8).

    `primary_production`/`vegetables` carries a generic row at 0.45 and a
    `prevention` row at zero. A prevented line must draw the second.
    """
    result = scenario(
        bundle,
        (line("prevention", "800.000"),),
        sector="primary_production",
        food_category="vegetables",
    )
    row = result.metrics["co2e"].by_destination[0]
    assert row.upstream == Decimal("0.0000000000")
    assert row.downstream == Decimal("0.0000000000")
    assert result.metrics["co2e"].total == Decimal("0.0000000000")


def test_a_prevented_scenario_is_a_whole_offset(bundle):
    """Not 79% of one. Current 800 kg not harvested against 800 kg prevented:
    the alternative's greenhouse-gas total is zero, so net benefit is the
    whole of the current figure."""
    entry = EntryInput(
        sector_code="primary_production",
        food_category_code="vegetables",
        current=(line("compost", "800.000"),),
        alternative=(line("prevention", "800.000"),),
    )
    result = calculate(CalculationRequest(entries=(entry,)), bundle)

    current_total = result.entries[0].current.metrics["co2e"].total
    assert current_total == Decimal("528.0000000000")  # 800 x (0.45 + 0.21)
    assert result.entries[0].alternative.metrics["co2e"].total == Decimal("0.0000000000")
    assert result.entries[0].net_benefit["co2e"] == current_total


def test_a_line_with_no_destination_row_falls_back_to_the_generic_one(bundle):
    """The generic row is the normal case, and it must survive O-7's exact
    match. `processing`/`dairy` has no `prevention` upstream row."""
    result = scenario(bundle, (line("prevention", "500.000"), line("landfill", "500.000")))
    upstreams = [row.upstream for row in result.metrics["co2e"].by_destination]
    assert upstreams == [Decimal("1.9"), Decimal("1.9")]


# ------------------------------------------------------------ code resolution


def test_a_null_food_category_resolves_through_standard_mix(bundle):
    """§6.2: null is treated as `standard_mix`, and it is the engine that
    resolves it (§3)."""
    result = calculate_scenario(
        (line("landfill", "100.000"),), "processing", None, bundle, 100
    )
    #: 2.6 is the standard_mix row; dairy's is 1.9, and the generic landfill
    #: downstream row (0.70) applies because standard_mix has none of its own.
    assert result.metrics["co2e"].by_destination[0].upstream == Decimal("2.6")
    assert result.metrics["co2e"].by_destination[0].downstream == Decimal("0.70")
    assert result.metrics["co2e"].total == Decimal("330.0000000000")


def test_a_null_food_category_is_echoed_on_the_result_not_resolved(bundle):
    """§6.2: "null is treated as standard_mix" is true of the factor lookup
    and of nothing else -- §5.4 keeps `unspecified` and `standard_mix`
    distinct on purpose."""
    entry = EntryInput(
        sector_code="processing",
        food_category_code=None,
        current=(line("landfill", "100.000"),),
        alternative=None,
    )
    result = calculate(CalculationRequest(entries=(entry,)), bundle)
    assert result.entries[0].food_category_code is None


@pytest.mark.parametrize(
    "sector,food_category,destination",
    [
        ("retail", "dairy", "landfill"),
        ("processing", "seafood", "landfill"),
        ("processing", "dairy", "incineration"),
    ],
)
def test_an_unknown_code_raises_rather_than_yielding_zero(
    bundle, sector, food_category, destination
):
    """§4.2, §4.4. Every lookup in `FactorBundle` falls back to
    `Decimal('0')`, which is right for a missing *factor* and catastrophic for
    a missing *code*: an unknown sector would return a calculation of zero
    that looks like a real answer."""
    with pytest.raises(UnknownCodeError):
        scenario(bundle, (line(destination, "100.000"),), sector, food_category)


def test_calculate_raises_on_an_unknown_code_in_the_alternative(bundle):
    entry = dairy_entry((line("landfill", "100.000"),), (line("incineration", "100.000"),))
    with pytest.raises(UnknownCodeError):
        calculate(CalculationRequest(entries=(entry,)), bundle)


# ------------------------------------------------------------------- scale


def test_a_metric_total_carries_the_contracted_ten_places(bundle):
    """§1.2 and the fixtures' scale. `800.000 x 0.4500000000` is thirteen
    places of `Decimal` arithmetic; the wire carries ten."""
    result = scenario(
        bundle,
        (line("compost", "800.000"),),
        sector="primary_production",
        food_category="vegetables",
    )
    assert result.metrics["co2e"].total.as_tuple().exponent == -10
    assert result.metrics["co2e"].by_destination[0].value.as_tuple().exponent == -10


def test_quantisation_happens_on_the_total_not_on_each_line(bundle):
    """Rounding every line and then adding is a different number from adding
    and then rounding once. The engine does the second."""
    document = copy.deepcopy(BUNDLE_JSON)
    document["formulas"] = [{"metric": "mass", "expression": "qty_kg / 3"}]
    document["metrics"] = [
        {"code": "mass", "unit": "kg", "display_precision": 1, "sort_order": 10}
    ]
    document["equivalences"] = []
    thirds = FactorBundle.from_json(document)

    result = calculate_scenario(
        (line("landfill", "1.000"), line("compost", "1.000"), line("animal_feed", "1.000")),
        "processing",
        "dairy",
        thirds,
        100,
    )
    #: Three unrounded thirds sum to 1 well beyond ten places; three rounded
    #: thirds sum to 0.9999999999.
    assert result.metrics["mass"].total == Decimal("1.0000000000")


# ------------------------------------------------- entries, totals, net benefit


def test_entries_are_returned_in_request_order(bundle):
    first = dairy_entry((line("landfill", "100.000"),))
    second = EntryInput(
        sector_code="primary_production",
        food_category_code="vegetables",
        current=(line("compost", "200.000"),),
        alternative=None,
    )
    result = calculate(CalculationRequest(entries=(first, second)), bundle)

    assert [entry.sector_code for entry in result.entries] == [
        "processing",
        "primary_production",
    ]
    assert result.factor_set_version == "TEST-v0"
    assert result.is_mock is True


def test_totals_are_the_sum_across_entries(bundle):
    first = dairy_entry((line("landfill", "1000.000"),))
    second = EntryInput(
        sector_code="primary_production",
        food_category_code="vegetables",
        current=(line("compost", "800.000"),),
        alternative=None,
    )
    result = calculate(CalculationRequest(entries=(first, second)), bundle)

    assert result.totals.current.total_kg == Decimal("1800.000")
    assert result.totals.current.metrics["co2e"].total == (
        result.entries[0].current.metrics["co2e"].total
        + result.entries[1].current.metrics["co2e"].total
    )
    #: §3 rule 2 -- a cross-entry destination breakdown has no single correct
    #: aggregation rule, so there is none.
    assert result.totals.current.metrics["co2e"].by_destination == ()


def test_no_alternative_anywhere_leaves_both_levels_none(bundle):
    result = calculate(
        CalculationRequest(entries=(dairy_entry((line("landfill", "100.000"),)),)), bundle
    )
    assert result.entries[0].alternative is None
    assert result.entries[0].net_benefit is None
    assert result.totals.alternative is None
    assert result.totals.net_benefit is None


def test_an_entry_without_an_alternative_contributes_its_current_to_the_totals(bundle):
    """§3 rule 3. Excluding it would make the rolled-up alternative lighter
    than the current scenario and inflate net benefit -- the precise failure
    the dual-scenario design exists to prevent."""
    improved = dairy_entry(
        (line("landfill", "1000.000"),), (line("compost", "1000.000"),)
    )
    untouched = EntryInput(
        sector_code="primary_production",
        food_category_code="vegetables",
        current=(line("compost", "800.000"),),
        alternative=None,
    )
    result = calculate(CalculationRequest(entries=(improved, untouched)), bundle)

    assert result.entries[1].alternative is None
    assert result.entries[1].net_benefit is None
    #: Mass-conserving: the second entry's 800 kg is on both sides.
    assert result.totals.alternative.total_kg == result.totals.current.total_kg
    #: Its contribution to net benefit is exactly zero, so the rolled-up
    #: figure equals the first entry's.
    assert result.totals.net_benefit["co2e"] == result.entries[0].net_benefit["co2e"]


def test_net_benefit_covers_every_metric(bundle):
    entry = dairy_entry((line("landfill", "1000.000"),), (line("compost", "1000.000"),))
    result = calculate(CalculationRequest(entries=(entry,)), bundle)

    assert set(result.entries[0].net_benefit) == {"co2e", "ch4", "mass"}
    #: 1000 x (1.9 + 0.99) - 1000 x (1.9 + 0.21)
    assert result.entries[0].net_benefit["co2e"] == Decimal("780.0000000000")
    #: Mass is conserved, so its net benefit is zero by construction.
    assert result.entries[0].net_benefit["mass"] == Decimal("0.0000000000")


def test_the_engine_is_a_pure_function(bundle):
    request = CalculationRequest(
        entries=(dairy_entry((line("landfill", "1000.000"),), (line("compost", "1000.000"),)),)
    )
    assert calculate(request, bundle) == calculate(request, bundle)
