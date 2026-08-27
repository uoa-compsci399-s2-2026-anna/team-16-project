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
            "sector": None,
            "food_category": "dairy",
            "metric": "co2e",
            "value_per_kg": "0.9900000000",
        },
        {
            "destination": "landfill",
            "sector": None,
            "food_category": None,
            "metric": "co2e",
            "value_per_kg": "0.7000000000",
        },
        {
            "destination": "landfill",
            "sector": None,
            "food_category": None,
            "metric": "ch4",
            "value_per_kg": "0.0270000000",
        },
        {
            "destination": "compost",
            "sector": None,
            "food_category": None,
            "metric": "co2e",
            "value_per_kg": "0.2100000000",
        },
        {
            "destination": "animal_feed",
            "sector": None,
            "food_category": None,
            "metric": "co2e",
            "value_per_kg": "-0.1500000000",
        },
        {
            "destination": "prevention",
            "sector": None,
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
            "sector": None,
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
    #: v1.48, amending §3 rule 2: the two entries land on different
    #: destinations here, so this is a same-metric sanity check that the
    #: totals-level breakdown reproduces each entry's own row rather than a
    #: test of the cross-entry summing itself -- that is
    #: `test_the_totals_carry_a_destination_breakdown_across_entries`'s job.
    #: 1000 x (1.9 + 0.99) = 2890; 800 x (0.45 + 0.21) = 528.
    rows = {row.destination_code: row for row in result.totals.current.metrics["co2e"].by_destination}
    assert rows["landfill"].qty_kg == Decimal("1000.000")
    assert rows["landfill"].value == Decimal("2890.0000000000")
    assert rows["compost"].qty_kg == Decimal("800.000")
    assert rows["compost"].value == Decimal("528.0000000000")
    assert rows["landfill"].upstream == Decimal("0")
    assert rows["landfill"].downstream == Decimal("0")


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


# ---------------------------------------------------------- equivalences
#
# Two things have to be right, and they fail in different ways. The **value**
# is §4.2's roll-up rule: the totals-level figure is derived from the
# rolled-up metric total, never summed from the per-entry ones. The **label**
# is §3 rule 5, a format the fixture set invented to match §6.2's samples and
# which nothing in the tree re-derived until this section -- `_assert_shape`
# compares types, so a label reading "Equivalent to driving 18596.8200000000
# km" is a well-formed string of the right type in the right key.


def one_metric_bundle(equivalences, formula="qty_kg"):
    """A bundle whose only metric is the mass in kilograms, so that a test can
    choose the exact `value` an equivalence has to convert and format. The
    factor rows in `BUNDLE_JSON` name metrics this document does not carry;
    the engine iterates `bundle.metrics` and never reads them."""
    document = copy.deepcopy(BUNDLE_JSON)
    document["metrics"] = [
        {"code": "mass", "unit": "kg", "display_precision": 1, "sort_order": 10}
    ]
    document["formulas"] = [{"metric": "mass", "expression": formula}]
    document["equivalences"] = equivalences
    return FactorBundle.from_json(document)


def equivalence_for(qty, value_per_unit="1.0000000000", template="{value}", formula="qty_kg"):
    """The single `EquivalenceResult` a one-line scenario of `qty` produces."""
    loaded = one_metric_bundle(
        [
            {
                "code": "eq",
                "source_metric": "mass",
                "value_per_unit": value_per_unit,
                "label_template": template,
                "sort_order": 10,
            }
        ],
        formula=formula,
    )
    return scenario(loaded, (line("landfill", qty),)).equivalences[0]


def test_an_equivalence_is_converted_from_the_metric_total_it_names(bundle):
    """§2.2: `value = metric total x value_per_unit`. 1000 x (1.9 + 0.99) is
    2890 kg CO2e, and 2890 x 4.18 is 12,080.2 km."""
    result = scenario(bundle, (line("landfill", "1000.000"),))

    assert len(result.equivalences) == 1
    item = result.equivalences[0]
    assert item.code == "km_driven"
    assert item.source_metric_code == "co2e"
    assert item.value == Decimal("12080.2000000000")
    assert item.label == "Equivalent to driving 12,080 km"


def test_an_equivalence_added_to_the_bundle_needs_no_code_change(bundle):
    """The real test of "equivalences are data", written the same way as the
    metric one: a *row* is added, the way a staff member adds one, and
    nothing in `engine/` is touched between the two calls below."""
    document = copy.deepcopy(BUNDLE_JSON)
    document["equivalences"].append(
        {
            "code": "meals",
            "source_metric": "ch4",
            "value_per_unit": "2.0000000000",
            "label_template": "About {value} meals",
            "sort_order": 20,
        }
    )
    widened = FactorBundle.from_json(document)
    assert widened.validate() == []

    before = scenario(bundle, (line("landfill", "1000.000"),))
    after = scenario(widened, (line("landfill", "1000.000"),))

    assert [item.code for item in before.equivalences] == ["km_driven"]
    #: Present, in `sort_order`, converting from a metric the first
    #: equivalence does not name, with its own template.
    assert [item.code for item in after.equivalences] == ["km_driven", "meals"]
    added = after.equivalences[1]
    assert added.source_metric_code == "ch4"
    #: 1000 x 0.027 x 28 = 756 kg CH4, doubled.
    assert added.value == Decimal("1512.0000000000")
    assert added.label == "About 1,512 meals"
    #: The equivalence that was already there is untouched by the new row.
    assert after.equivalences[0] == before.equivalences[0]


#: The three equivalence codes §2.2 names and `admin/seed.py` ships.
SHIPPED_EQUIVALENCE_CODES = ("km_driven", "meals", "showers")


@pytest.mark.parametrize("code", SHIPPED_EQUIVALENCE_CODES)
def test_no_equivalence_code_appears_in_the_engine(code):
    """Equivalences are data on exactly the same terms as metrics."""
    literal = re.compile(f"""['"]{re.escape(code)}['"]""")
    for path in sorted(Path(__file__).resolve().parents[1].joinpath("engine").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert not literal.search(source), (
            f"{path.name} names the equivalence {code!r}. Equivalences are data: "
            "adding one must mean inserting a row, never editing a call site."
        )


def test_an_equivalence_naming_a_metric_the_bundle_does_not_compute_is_skipped():
    """`validate()` (§4.1) reports a dangling `source_metric`, which is where
    that belongs. A calculation drops the equivalence rather than raising
    `KeyError` -- the same rule `net_benefit` follows for a metric present on
    only one side."""
    loaded = one_metric_bundle(
        [
            {
                "code": "eq",
                "source_metric": "co2e",
                "value_per_unit": "1.0000000000",
                "label_template": "{value}",
                "sort_order": 10,
            }
        ]
    )
    assert loaded.validate() != []
    assert scenario(loaded, (line("landfill", "1000.000"),)).equivalences == ()


# --------------------------------------- the value: §4.2's roll-up rule


def test_the_totals_equivalence_is_derived_from_the_rolled_up_metric_total(bundle):
    result = calculate(
        CalculationRequest(
            entries=(
                dairy_entry((line("landfill", "1000.000"),)),
                dairy_entry((line("compost", "500.000"),)),
            )
        ),
        bundle,
    )
    rolled_up = result.totals.current.metrics["co2e"].total
    assert result.totals.current.equivalences[0].value == (
        rolled_up * Decimal("4.1800000000")
    )


def test_the_totals_equivalence_is_not_summed_from_the_per_entry_ones():
    """§4.2. The conversion is linear, so the two agree mathematically -- but
    `Decimal` has finite precision and one computation is one rounding.

    Two entries of 1e-10 kg against a factor of 0.5. Each entry's product is
    5e-11, which is half of the last place the wire carries and quantises
    away to zero; the rolled-up total of 2e-10 converts to a clean 1e-10.
    Summing the per-entry values gives **zero** where the correct answer is
    0.0000000001. The gap is one unit in the last place here because that is
    what a contrived case can isolate; on real figures it is the same
    arithmetic.
    """
    loaded = one_metric_bundle(
        [
            {
                "code": "eq",
                "source_metric": "mass",
                "value_per_unit": "0.5000000000",
                "label_template": "{value}",
                "sort_order": 10,
            }
        ]
    )
    tiny = (line("landfill", "0.0000000001"),)
    result = calculate(
        CalculationRequest(entries=(dairy_entry(tiny), dairy_entry(tiny))), loaded
    )

    per_entry = [entry.current.equivalences[0].value for entry in result.entries]
    assert per_entry == [Decimal("0.0000000000"), Decimal("0.0000000000")]

    totals = result.totals.current.equivalences[0].value
    assert totals == result.totals.current.metrics["mass"].total * Decimal("0.5")
    assert totals == Decimal("0.0000000001")
    #: The implementation this pins out: summing what the engine already
    #: computed, one level down.
    assert totals != sum(per_entry, Decimal("0"))


def test_an_equivalence_value_carries_the_contracted_ten_places(bundle):
    """§1.2. `total x value_per_unit` is a twenty-place product; the wire
    carries ten, and an exact zero must still carry them -- `wire()` renders
    `Decimal("0E-10")` as `"0.0000000000"` only because the scale is there."""
    result = scenario(
        bundle,
        (line("prevention", "800.000"),),
        sector="primary_production",
        food_category="vegetables",
    )
    zero = result.equivalences[0]
    assert zero.value == Decimal("0")
    assert zero.value.as_tuple().exponent == -10
    assert zero.label == "Equivalent to driving 0 km"

    nonzero = scenario(bundle, (line("landfill", "1000.000"),)).equivalences[0]
    assert nonzero.value.as_tuple().exponent == -10


# ------------------------------------------- the label: §3's rule 5


@pytest.mark.parametrize(
    "qty,expected",
    [
        #: Half-even -- `Decimal`'s default, and what `quantize(Decimal("1"))`
        #: would silently apply -- rounds both of these DOWN, to 2 and 4.
        ("5.000", "3"),
        ("9.000", "5"),
        #: Above and below the half, where the two modes agree.
        ("4.800", "2"),
        ("5.200", "3"),
    ],
)
def test_the_label_rounds_half_up_and_not_half_even(qty, expected):
    """§3 rule 5, and the one part of it **no fixture pins**: no value in the
    set lands on a half, so both rounding modes pass the fixture comparison
    and only one of them is the contract. Halved quantities put the value
    exactly on 2.5 and 4.5, which is where the modes part company."""
    item = equivalence_for(qty, value_per_unit="0.5000000000")
    assert item.label == expected


@pytest.mark.parametrize(
    "qty,expected",
    [
        ("0.000", "0"),
        ("999.000", "999"),
        ("1000.000", "1,000"),
        ("18596.820", "18,597"),
        ("1234567.500", "1,234,568"),
    ],
)
def test_the_label_groups_thousands_with_commas(qty, expected):
    """§3 rule 5: a comma every three digits, and no decimal point -- there is
    no fractional part to separate."""
    assert equivalence_for(qty).label == expected


@pytest.mark.parametrize(
    "qty,expected",
    [
        #: A metric total can be negative when a downstream offset dominates.
        ("1234567.500", "-1,234,568"),
        ("2.500", "-3"),
    ],
)
def test_a_negative_value_keeps_its_sign_and_its_grouping(qty, expected):
    assert equivalence_for(qty, formula="0 - qty_kg").label == expected


def test_a_value_that_rounds_to_zero_from_below_is_not_minus_zero():
    """`Decimal("-0.4")` rounds to `Decimal("-0")`, and "-0 km" reads as a bug
    on a results page. The sign belongs to values that are actually negative."""
    item = equivalence_for("0.400", formula="0 - qty_kg")
    assert item.value == Decimal("-0.4000000000")
    assert item.label == "0"


def test_everything_but_the_placeholder_is_copied_verbatim():
    """§2.2 and §3 rule 5. `label_template` is staff-authored (§8.1) and must
    never behave as a format string: `{value}` is the only substitution, and
    any other brace sequence is literal text. `str.format` would raise on the
    unmatched brace below, and `"{home}"` would raise `KeyError` -- both in
    front of a user, from a field a staff member typed."""
    item = equivalence_for("3.000", template="~ {value} km/yr {home} {not_a_field} { }")
    assert item.label == "~ 3 km/yr {home} {not_a_field} { }"


def test_a_template_with_no_placeholder_is_left_alone():
    assert equivalence_for("3.000", template="A fixed sentence").label == (
        "A fixed sentence"
    )


def test_the_placeholder_is_substituted_wherever_it_appears():
    assert equivalence_for("3.000", template="{value} and {value}").label == "3 and 3"


def test_the_engine_is_a_pure_function(bundle):
    request = CalculationRequest(
        entries=(dairy_entry((line("landfill", "1000.000"),), (line("compost", "1000.000"),)),)
    )
    assert calculate(request, bundle) == calculate(request, bundle)


# --------------------------------------------- the cross-entry roll-up


def _bundle_with_two_sectors_sharing_a_destination() -> FactorBundle:
    """A two-metric bundle, built the same way `one_metric_bundle` is, except
    it deliberately carries a second metric. `co2e`'s formula uses `upstream`
    and `downstream`; `mass`'s formula is bare `qty_kg`. A test reading
    `metrics["co2e"].total` is reading the one number the roll-up could
    plausibly be confused with, and a test comparing `metrics["co2e"]` and
    `metrics["mass"]`'s `by_destination` rows is checking the property that
    makes the mass partition readable off either metric: `qty_kg` does not
    depend on which metric computed it, so the two must agree row for row.

    `farm` and `retail` draw genuinely different upstream factors for
    `standard_mix` -- 2.0 against 5.0 per kg. That is deliberate: two equal
    factors, or a 0/1 pair, would let a wrong roll-up (summing or averaging
    the *rate* instead of leaving it at zero) land on a number that happens
    to look right. `downstream` at `landfill` is the same for both sectors,
    so a defect that mixed the two rates together is visible in `upstream`
    alone rather than smeared across both.

    Also carries a `prevention` destination, flagged `is_prevention` rather
    than recognised by its literal code, for the money block's saving figure
    (§4.5) -- it has no factor rows of its own, since none of the tests that
    use it read a metric total.
    """
    document = {
        "version_label": "TEST-v0-shared-destination",
        "is_mock": True,
        "sectors": [{"code": "farm"}, {"code": "retail"}],
        "food_categories": [{"code": "standard_mix", "is_standard_mix": True}],
        "destination_groups": [{"code": "disposal"}, {"code": "reuse"}],
        "destinations": [
            {"code": "landfill", "group": "disposal"},
            {"code": "prevention", "group": "reuse", "is_prevention": True},
        ],
        "metrics": [
            {"code": "co2e", "unit": "kg CO2e", "display_precision": 1, "sort_order": 10},
            {"code": "mass", "unit": "kg", "display_precision": 1, "sort_order": 20},
        ],
        "constants": [],
        "formulas": [
            {"metric": "co2e", "expression": "qty_kg * (upstream + downstream)"},
            {"metric": "mass", "expression": "qty_kg"},
        ],
        "upstream": [
            {
                "sector": "farm",
                "food_category": "standard_mix",
                "destination": None,
                "metric": "co2e",
                "value_per_kg": "2.0000000000",
            },
            {
                "sector": "retail",
                "food_category": "standard_mix",
                "destination": None,
                "metric": "co2e",
                "value_per_kg": "5.0000000000",
            },
        ],
        "downstream": [
            {
                "destination": "landfill",
                "sector": None,
                "food_category": None,
                "metric": "co2e",
                "value_per_kg": "0.5000000000",
            }
        ],
        "equivalences": [],
    }
    loaded = FactorBundle.from_json(document)
    assert loaded.validate() == []
    return loaded


def test_the_totals_carry_a_destination_breakdown_across_entries():
    """**§3 rule 2 said this could not be done, and it was half right.**

    The engine's own comment: "the same destination can appear under several
    entries drawing different upstream factors, so a cross-entry destination
    breakdown has no single correct aggregation rule." That is true of
    `upstream` and `downstream`, which are per-KILOGRAM rates - averaging two
    different factors is meaningless.

    It is not true of the other two. `qty_kg` is a mass, and `value` is
    defined as "this line's contribution to the metric total" - and the
    metric total is itself `running + metric.total` summed across entries.
    Summing contributions per destination therefore produces an exact
    partition of a number the engine already computes by summing.

    So the roll-up carries the two additive fields and leaves the two rates
    at zero, and v1.48 rewrites the rule to say which is which. It stays a
    per-metric field -- `MetricResult.by_destination`, not a new field on
    `ScenarioResult` -- because `value` is in that metric's own unit, and a
    field that merged `co2e` and `mass` together would mix kg CO2e with kg,
    which would be worse than either metric alone.
    """
    bundle = _bundle_with_two_sectors_sharing_a_destination()
    request = CalculationRequest(entries=(
        EntryInput(sector_code="farm", food_category_code=None,
                   current=(ScenarioLine(destination_code="landfill",
                                         qty_kg=Decimal("100.000")),),
                   alternative=None),
        EntryInput(sector_code="retail", food_category_code=None,
                   current=(ScenarioLine(destination_code="landfill",
                                         qty_kg=Decimal("300.000")),),
                   alternative=None),
    ))

    result = calculate(request, bundle)

    rows = result.totals.current.metrics["co2e"].by_destination
    landfill = next(row for row in rows if row.destination_code == "landfill")

    #: The mass is the plain sum.
    assert landfill.qty_kg == Decimal("400.000")

    #: And the value is a partition of the metric total, exactly - which is
    #: the assertion that makes this a roll-up rather than a second, parallel
    #: computation that could drift from it.
    co2e_total = result.totals.current.metrics["co2e"].total
    assert sum(
        (row.value for row in rows), Decimal("0")
    ) == co2e_total

    #: The same destination, read off a *different* metric, is a genuinely
    #: different number -- unlike `qty_kg`, which is identical across every
    #: metric's rows and so cannot expose a roll-up that built one metric's
    #: rows from another metric's accumulator. `mass`'s formula is bare
    #: `qty_kg`, so its own rolled-up value has to equal its own qty_kg
    #: exactly; a roll-up that keyed `mass`'s destination to `co2e`'s bucket
    #: (or mixed the two together) would put co2e's value here instead, and
    #: both assertions below would fail.
    mass_landfill = next(
        row for row in result.totals.current.metrics["mass"].by_destination
        if row.destination_code == "landfill"
    )
    assert mass_landfill.value == mass_landfill.qty_kg
    assert mass_landfill.value != landfill.value


def test_the_rolled_up_rows_do_not_claim_a_per_kilogram_rate():
    """The half of §3 rule 2 that still stands.

    `upstream` and `downstream` are rates per kilogram. The two entries above
    draw different upstream factors for the same destination, so there is no
    figure to report - and reporting either one, or their mean, would be a
    number that looks authoritative and is not derived from anything.

    Zero, and the contract says why. A consumer that renders these is
    rendering the wrong thing, which is what the v1.48 note warns about.
    """
    bundle = _bundle_with_two_sectors_sharing_a_destination()
    request = CalculationRequest(entries=(
        EntryInput(sector_code="farm", food_category_code=None,
                   current=(ScenarioLine(destination_code="landfill",
                                         qty_kg=Decimal("100.000")),),
                   alternative=None),
        EntryInput(sector_code="retail", food_category_code=None,
                   current=(ScenarioLine(destination_code="landfill",
                                         qty_kg=Decimal("300.000")),),
                   alternative=None),
    ))

    result = calculate(request, bundle)
    row = next(r for r in result.totals.current.metrics["co2e"].by_destination
               if r.destination_code == "landfill")

    assert row.upstream == Decimal("0")
    assert row.downstream == Decimal("0")


def test_a_single_entry_rolls_up_to_the_same_rows_it_already_had():
    """The affirmative half. With one entry there is nothing to combine, so
    the roll-up must reproduce that entry's own breakdown - masses and values
    both. A roll-up that returned an empty tuple would satisfy the sum
    assertion above whenever the total happened to be zero."""
    bundle = _bundle_with_two_sectors_sharing_a_destination()
    request = CalculationRequest(entries=(
        EntryInput(sector_code="farm", food_category_code=None,
                   current=(ScenarioLine(destination_code="landfill",
                                         qty_kg=Decimal("100.000")),),
                   alternative=None),
    ))

    result = calculate(request, bundle)
    entry_rows = result.entries[0].current.metrics["co2e"].by_destination
    total_rows = result.totals.current.metrics["co2e"].by_destination

    assert {r.destination_code for r in total_rows} == {
        r.destination_code for r in entry_rows}
    assert sum((r.qty_kg for r in total_rows), Decimal("0")) == Decimal("100.000")


# ------------------------------------------------------------------ the money


def test_the_money_block_states_the_share_of_value_wasted():
    """The client's ask: "浪费的金额占总金额的多少百分比".

    Computed HERE and not in the browser, because §7.6.1 gives the front end
    exactly one calculation - unit conversion in `units.js` - and every other
    number on the page comes from the API.

    It is NOT a metric. `metric` rows are evaluated by the formula engine,
    whose language is per-LINE and takes (qty_kg, upstream, downstream,
    const_*); an entry-level figure a visitor typed cannot be expressed in it,
    and inventing a per-kilogram money factor is exactly the modelling the
    client's O-2 ruling avoided.
    """
    bundle = _bundle_with_two_sectors_sharing_a_destination()
    request = CalculationRequest(entries=(
        EntryInput(sector_code="farm", food_category_code=None,
                   total_value_nzd=Decimal("120000.00"),
                   wasted_value_nzd=Decimal("4500.00"),
                   current=(ScenarioLine(destination_code="landfill",
                                         qty_kg=Decimal("1000.000")),),
                   alternative=None),
    ))

    money = calculate(request, bundle).totals.money

    assert money.total_value_nzd == Decimal("120000.00")
    assert money.wasted_value_nzd == Decimal("4500.00")
    assert money.wasted_share_percent == Decimal("3.75")


def test_the_saving_is_value_per_kilogram_times_the_mass_diverted():
    """The client's ruling: "节省额按每公斤均匀价值计算".

    1,000 kg wasted at $4,500 is $4.50/kg. An alternative that sends 300 kg
    to a prevention destination diverts 300 kg, so the saving is $1,350.

    **Uniform value per kilogram is an assumption, and it is the client's.**
    Milk and mixed waste are not worth the same per kilogram; this figure is
    only as good as that. The contract note in v1.48 says so, because this is
    the number most likely to be screenshotted.
    """
    bundle = _bundle_with_two_sectors_sharing_a_destination()
    request = CalculationRequest(entries=(
        EntryInput(
            sector_code="farm", food_category_code=None,
            total_value_nzd=Decimal("120000.00"),
            wasted_value_nzd=Decimal("4500.00"),
            current=(ScenarioLine(destination_code="landfill",
                                  qty_kg=Decimal("1000.000")),),
            alternative=(
                ScenarioLine(destination_code="landfill", qty_kg=Decimal("700.000")),
                ScenarioLine(destination_code="prevention", qty_kg=Decimal("300.000")),
            ),
        ),
    ))

    money = calculate(request, bundle).totals.money

    assert money.saving_nzd == Decimal("1350.00")


def test_the_money_block_is_absent_when_nobody_typed_a_value():
    """The common case. Every field is optional, and absent must stay absent
    rather than becoming zero - "$0 wasted" is a claim, and "0% of value
    wasted" is a different and much stronger one."""
    bundle = _bundle_with_two_sectors_sharing_a_destination()
    request = CalculationRequest(entries=(
        EntryInput(sector_code="farm", food_category_code=None,
                   current=(ScenarioLine(destination_code="landfill",
                                         qty_kg=Decimal("1000.000")),),
                   alternative=None),
    ))

    assert calculate(request, bundle).totals.money is None


def test_a_share_needs_a_total_and_a_saving_needs_an_alternative():
    """Each figure appears only when what it is derived from is there.

    A visitor who typed the wasted value but not the total gets the wasted
    value and no share - dividing by an absent total is not zero and not
    infinity, it is a question nobody answered.
    """
    bundle = _bundle_with_two_sectors_sharing_a_destination()
    request = CalculationRequest(entries=(
        EntryInput(sector_code="farm", food_category_code=None,
                   wasted_value_nzd=Decimal("4500.00"),
                   current=(ScenarioLine(destination_code="landfill",
                                         qty_kg=Decimal("1000.000")),),
                   alternative=None),
    ))

    money = calculate(request, bundle).totals.money

    assert money.wasted_value_nzd == Decimal("4500.00")
    assert money.wasted_share_percent is None
    assert money.saving_nzd is None, "no alternative scenario, so nothing is saved"
