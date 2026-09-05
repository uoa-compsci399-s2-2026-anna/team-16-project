"""`FakeEngineAdapter` (`tests/support/sqlite.py`) against the real engine.

`FakeEngineAdapter.calculate` never calls `engine.calculate.calculate`. It
grew its own `_money()` and its own `rolled_up` branch of `_scenario_result`
so that `tests/api/test_api.py`, `test_api_entries.py` and
`test_fixture_consistency.py` could assert against something without a real
database in front of the engine. Both blocks are hand-copies of
`engine/calculate.py`'s `_money` and `_roll_up` -- `Decimal("0.01")` and
`ROUND_HALF_UP` re-typed as literals rather than imported -- and nothing
before this file checked that the copy still says what the original says.

This is the same situation `admin/expressions.py` and `engine/evaluator.py`
are in, and `tests/test_evaluator.py`'s `test_the_panel_and_the_engine_reach_
the_same_verdict` is the shape this follows: one corpus, both
implementations, one assertion that they agree. It found eight divergences
there; this file exists so the same class of drift here does not go
unnoticed for eight rounds first.

**What "agree" means here.** The fake's single metric (`co2e`) is
deliberately trivial -- its per-line value is the line's own `qty_kg`, so
that the fake never has to re-implement formula evaluation. `bundle.json`
below gives the real engine a `mass` metric whose stored formula is the
literal `qty_kg`, which is the same trivial computation. Comparing the fake's
`co2e` metric against the real bundle's `mass` metric is therefore comparing
the two `_money`/roll-up implementations against each other on equal terms,
not the fake's placeholder arithmetic against the real one's genuine formula.

**What this catches that the shape-only API tests do not.** Deleting
`engine/calculate.py`'s money block and its `_roll_up` amendment leaves the
suites named above at 105 passed, 0 failed, because none of them call the
real engine. Every case below does, and a real/fake mismatch on any of the
four money figures or on a rolled-up `by_destination` row fails here.
"""

from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from engine.bundle import FactorBundle
from engine.calculate import calculate as real_calculate
from engine.types import CalculationRequest, EntryInput, ScenarioLine
from tests.support.sqlite import FakeEngineAdapter

#: A copy of `tests/golden/case_10_money_per_entry_rate_and_prevention/bundle.json`:
#: two destinations (`landfill`, the flagged `prevention`), two sectors, two
#: priced food categories, and -- the reason it is reused rather than
#: invented fresh -- a `mass` metric whose formula is the bare literal
#: `qty_kg`, matching the fake's trivial `co2e` exactly.
BUNDLE_DATA = {
    "version_label": "AGREEMENT-v1",
    "is_mock": True,
    "sectors": [
        {"code": "primary_production", "name": "Primary production", "sort_order": 10},
        {"code": "processing", "name": "Processing / Manufacturing", "sort_order": 20},
    ],
    "food_categories": [
        {"code": "standard_mix", "name": "Standard mix", "is_standard_mix": True, "sort_order": 0},
        {"code": "vegetables", "name": "Vegetables", "is_standard_mix": False, "sort_order": 30},
        {"code": "dairy", "name": "Dairy", "is_standard_mix": False, "sort_order": 60},
    ],
    "destination_groups": [
        {"code": "reuse", "name": "Reuse", "is_waste": False, "sort_order": 10},
        {"code": "disposal", "name": "Disposal", "is_waste": True, "sort_order": 30},
    ],
    "destinations": [
        {"code": "prevention", "name": "Prevention", "group": "reuse", "sort_order": 10, "is_prevention": True},
        {"code": "landfill", "name": "Landfill", "group": "disposal", "sort_order": 120},
    ],
    "metrics": [
        {"code": "co2e", "name": "Greenhouse gases", "unit": "kg CO2e", "display_unit": "kg CO2e", "display_precision": 1, "sort_order": 10},
        {"code": "mass", "name": "Mass", "unit": "kg", "display_unit": "kg", "display_precision": 1, "sort_order": 50},
    ],
    "constants": [],
    "formulas": [
        {"metric": "co2e", "expression": "qty_kg * (upstream + downstream)", "notes": ""},
        {"metric": "mass", "expression": "qty_kg", "notes": ""},
    ],
    "upstream": [
        {"sector": "processing", "food_category": "dairy", "destination": None, "metric": "co2e", "value_per_kg": "1.9000000000"},
        {"sector": "processing", "food_category": "dairy", "destination": "prevention", "metric": "co2e", "value_per_kg": "0.0000000000"},
        {"sector": "primary_production", "food_category": "vegetables", "destination": None, "metric": "co2e", "value_per_kg": "0.4500000000"},
        {"sector": "primary_production", "food_category": "vegetables", "destination": "prevention", "metric": "co2e", "value_per_kg": "0.0000000000"},
    ],
    "downstream": [
        {"destination": "landfill", "sector": None, "food_category": "dairy", "metric": "co2e", "value_per_kg": "0.9900000000"},
        {"destination": "landfill", "sector": None, "food_category": None, "metric": "co2e", "value_per_kg": "0.7000000000"},
        {"destination": "prevention", "sector": None, "food_category": None, "metric": "co2e", "value_per_kg": "0.0000000000"},
    ],
    "equivalences": [
        {"code": "km_driven", "name": "Kilometres driven", "source_metric": "co2e", "value_per_unit": "4.1800000000", "label_template": "Equivalent to driving {value} km", "sort_order": 10},
    ],
}


def _entry(
    sector,
    food_category,
    current,
    alternative=None,
    total_value_nzd=None,
    wasted_value_nzd=None,
    total_input_kg=None,
):
    """One entry, in the plain-dict shape every case below is written in.

    `current`/`alternative` are `(destination_code, qty_kg_str)` pairs.
    `alternative=None` means the entry itself carries none (`entry.alternative
    is None`), not an empty scenario.
    """
    return {
        "sector": sector,
        "food_category": food_category,
        "current": current,
        "alternative": alternative,
        "total_value_nzd": total_value_nzd,
        "wasted_value_nzd": wasted_value_nzd,
        "total_input_kg": total_input_kg,
    }


#: The corpus. Each case is a list of entries handed, unchanged, to both a
#: real `CalculationRequest` and a fake `make_request` payload -- see
#: `_real_request`/`_fake_request` below, which are the only two places that
#: know how to translate this shape into each implementation's own input.
CASES = {
    "all four money figures, a per-entry rate, and prevention": [
        _entry("processing", "dairy", [("landfill", "1200.000")],
               total_value_nzd="60000.00", wasted_value_nzd="9000.00"),
        _entry("primary_production", "vegetables", [("landfill", "1000.000")],
               [("landfill", "600.000"), ("prevention", "400.000")],
               total_value_nzd="15000.00", wasted_value_nzd="6000.00"),
        _entry("processing", "dairy", [("landfill", "500.000")],
               [("landfill", "200.000"), ("prevention", "300.000")]),
    ],
    "the absent case: nobody supplied a money figure": [
        _entry("processing", "dairy", [("landfill", "1200.000")],
               [("landfill", "1200.000")]),
        _entry("primary_production", "vegetables", [("landfill", "800.000")]),
    ],
    "one entry priced, one entirely unpriced, sharing a destination": [
        _entry("processing", "dairy", [("landfill", "900.000")],
               [("landfill", "300.000"), ("prevention", "600.000")],
               total_value_nzd="10000.00", wasted_value_nzd="4000.00"),
        _entry("primary_production", "vegetables", [("landfill", "700.000")],
               [("landfill", "700.000")]),
    ],
    "money present but no entry carries an alternative": [
        _entry("processing", "dairy", [("landfill", "300.000")],
               total_value_nzd="5000.00", wasted_value_nzd="1200.00"),
        _entry("primary_production", "vegetables", [("landfill", "150.000")],
               total_value_nzd="2000.00", wasted_value_nzd="2000.00"),
    ],
    "§4.6: every entry gives a production total -- the share is computable": [
        _entry("processing", "dairy", [("landfill", "1200.000")],
               [("landfill", "1200.000")], total_input_kg="10000.000"),
        _entry("primary_production", "vegetables", [("landfill", "800.000")],
               None, total_input_kg="2000.000"),
    ],
    "§4.6: one entry gives a production total and one does not": [
        _entry("processing", "dairy", [("landfill", "1200.000")],
               [("landfill", "1200.000")], total_input_kg="10000.000"),
        _entry("primary_production", "vegetables", [("landfill", "800.000")]),
    ],
    "wasted value exceeds total value -- the share is left unclamped": [
        _entry("processing", "dairy", [("landfill", "400.000")],
               [("prevention", "400.000")],
               total_value_nzd="1000.00", wasted_value_nzd="3000.00"),
    ],
    "one entry gives a total but not a wasted value": [
        _entry("processing", "dairy", [("landfill", "600.000")],
               [("landfill", "600.000")],
               total_value_nzd="8000.00"),
        _entry("primary_production", "vegetables", [("landfill", "400.000")],
               [("prevention", "400.000")],
               total_value_nzd="3000.00", wasted_value_nzd="3000.00"),
    ],
}


def _decimal_or_none(value):
    return None if value is None else Decimal(value)


def _lines(pairs):
    return None if pairs is None else tuple(pairs)


def _real_request(entries) -> CalculationRequest:
    return CalculationRequest(
        entries=tuple(
            EntryInput(
                sector_code=entry["sector"],
                food_category_code=entry["food_category"],
                current=tuple(
                    ScenarioLine(destination_code=code, qty_kg=Decimal(qty))
                    for code, qty in entry["current"]
                ),
                alternative=(
                    None
                    if entry["alternative"] is None
                    else tuple(
                        ScenarioLine(destination_code=code, qty_kg=Decimal(qty))
                        for code, qty in entry["alternative"]
                    )
                ),
                total_input_kg=_decimal_or_none(entry["total_input_kg"]),
                total_value_nzd=_decimal_or_none(entry["total_value_nzd"]),
                wasted_value_nzd=_decimal_or_none(entry["wasted_value_nzd"]),
            )
            for entry in entries
        ),
        gwp_horizon=100,
    )


def _fake_request(entries):
    """The same entries, in the shape `FakeEngineAdapter.make_request` (and,
    behind it, `api/schemas.py`) expects: `.destination` rather than
    `.destination_code`, `.sector`/`.food_category` rather than the
    `*_code`-suffixed names `EntryInput` uses."""
    payload = SimpleNamespace(
        entries=tuple(
            SimpleNamespace(
                sector=entry["sector"],
                food_category=entry["food_category"],
                current=tuple(
                    SimpleNamespace(destination=code, qty_kg=Decimal(qty))
                    for code, qty in entry["current"]
                ),
                alternative=(
                    None
                    if entry["alternative"] is None
                    else tuple(
                        SimpleNamespace(destination=code, qty_kg=Decimal(qty))
                        for code, qty in entry["alternative"]
                    )
                ),
                total_input_kg=_decimal_or_none(entry["total_input_kg"]),
                total_value_nzd=_decimal_or_none(entry["total_value_nzd"]),
                wasted_value_nzd=_decimal_or_none(entry["wasted_value_nzd"]),
            )
            for entry in entries
        ),
        gwp_horizon=100,
    )
    return FakeEngineAdapter().make_request(payload)


def _money_tuple(money):
    """`None` stays `None`; otherwise the four figures, in a form that
    compares equal whether it came from `engine.types.MoneyResult` or the
    fake's `SimpleNamespace` -- both expose the same four attribute names."""
    if money is None:
        return None
    return (
        money.total_value_nzd,
        money.wasted_value_nzd,
        money.wasted_share_percent,
        money.saving_nzd,
    )


def _state_tuple(totals):
    """§4.6's five states plus the production share itself.

    Compared alongside the money figures rather than instead of them: a copy
    that agreed on every value while disagreeing about whether a partial
    submission was `incomplete` or `not_supplied` would put a different card
    on the results page for the same request, which is precisely the
    distinction §4.6 exists to make."""
    state = totals.data_state
    return (
        totals.production_share_percent,
        state.production_share_percent,
        state.total_value_nzd,
        state.wasted_value_nzd,
        state.wasted_share_percent,
        state.saving_nzd,
    )


def _entry_shares(result):
    return tuple(entry.production_share_percent for entry in result.entries)


def _rollup_tuple(scenario, metric_code):
    """A scenario's rolled-up `(total, by_destination)` for one metric, where
    `by_destination` is reduced to `(destination_code, qty_kg, value)` --
    `upstream`/`downstream` are dropped because they are zeroed identically by
    construction on both sides and carry no information to agree or disagree
    on."""
    metric = scenario.metrics[metric_code]
    return (
        metric.total,
        tuple(
            (row.destination_code, row.qty_kg, row.value)
            for row in metric.by_destination
        ),
    )


@pytest.mark.parametrize("case", CASES, ids=lambda name: name)
def test_the_fake_agrees_with_the_real_engine_on_money(case):
    """The money block: all four figures, computed independently by
    `engine.calculate._money` and the fake's `_money`, must match for every
    case in the corpus -- including the case where every figure is absent."""
    entries = CASES[case]
    real_bundle = FactorBundle.from_json(json.loads(json.dumps(BUNDLE_DATA)))
    fake_bundle = FakeEngineAdapter().bundle_from_json(BUNDLE_DATA)

    real_result = real_calculate(_real_request(entries), real_bundle)
    fake_result = FakeEngineAdapter().calculate(_fake_request(entries), fake_bundle)

    assert _money_tuple(fake_result.totals.money) == _money_tuple(
        real_result.totals.money
    ), case
    assert _state_tuple(fake_result.totals) == _state_tuple(real_result.totals), case
    assert _entry_shares(fake_result) == _entry_shares(real_result), case


@pytest.mark.parametrize("case", CASES, ids=lambda name: name)
def test_the_fake_agrees_with_the_real_engine_on_the_totals_level_rollup(case):
    """The cross-entry roll-up (v1.48, amending §3 rule 2): the fake's
    `rolled_up` branch of `_scenario_result` must merge entries into
    destinations the same way `engine.calculate._roll_up` does, on both the
    current and (when any entry carries one) the alternative side."""
    entries = CASES[case]
    real_bundle = FactorBundle.from_json(json.loads(json.dumps(BUNDLE_DATA)))
    fake_bundle = FakeEngineAdapter().bundle_from_json(BUNDLE_DATA)

    real_result = real_calculate(_real_request(entries), real_bundle)
    fake_result = FakeEngineAdapter().calculate(_fake_request(entries), fake_bundle)

    assert _rollup_tuple(fake_result.totals.current, "co2e") == _rollup_tuple(
        real_result.totals.current, "mass"
    ), case

    has_alternative = any(entry["alternative"] is not None for entry in entries)
    if has_alternative:
        assert _rollup_tuple(fake_result.totals.alternative, "co2e") == _rollup_tuple(
            real_result.totals.alternative, "mass"
        ), case
    else:
        assert fake_result.totals.alternative is None
        assert real_result.totals.alternative is None


def test_the_corpus_exercises_every_money_shape():
    """A guard on the guard, in `test_evaluator.py`'s style
    (`test_the_agreement_set_is_not_all_one_verdict`): this fails if the
    corpus above is ever trimmed down to cases that all land on the same
    branch of `_money`."""
    money_by_case = {}
    for name, entries in CASES.items():
        bundle = FactorBundle.from_json(json.loads(json.dumps(BUNDLE_DATA)))
        money_by_case[name] = real_calculate(_real_request(entries), bundle).totals.money

    assert any(money is None for money in money_by_case.values()), (
        "no case in the corpus leaves money absent"
    )
    assert any(money is not None for money in money_by_case.values()), (
        "no case in the corpus supplies a money figure"
    )
    priced = [money for money in money_by_case.values() if money is not None]
    assert any(money.saving_nzd is not None for money in priced), (
        "no case in the corpus produces a saving"
    )
    assert any(money.saving_nzd is None for money in priced), (
        "no case in the corpus withholds a saving"
    )


def test_the_corpus_reaches_all_three_states_of_the_production_share():
    """§4.6's own guard on the guard. One entry cannot tell a sum from an
    average or a full denominator from a partial one, and a corpus that only
    ever left `total_input_kg` absent would agree with any implementation at
    all -- including one that returned zero."""
    states = {}
    for name, entries in CASES.items():
        bundle = FactorBundle.from_json(json.loads(json.dumps(BUNDLE_DATA)))
        totals = real_calculate(_real_request(entries), bundle).totals
        states[name] = totals.data_state.production_share_percent

    assert "complete" in states.values(), "no case computes a production share"
    assert "incomplete" in states.values(), (
        "no case leaves the production share partly answered -- the state that "
        "cannot be reached with one entry"
    )
    assert "not_supplied" in states.values(), (
        "no case leaves the production share unanswered"
    )
