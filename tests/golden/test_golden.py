"""The golden suite runner. Contract §10.1.

Three files per case under `tests/golden/case_*/`:

    bundle.json     a §10.2 factor-set snapshot
    request.json    a §3 `CalculationRequest`
    expected.json   the §3 `CalculationResult` it must produce

**This suite is the only evidence that the calculator computes correctly**,
and it is what the team presents at handover. Four properties of this file are
what make that claim worth anything.

**It compares the domain object, not the wire body.** `expected.json` mirrors
§3 field for field — `food_category_code`, `source_metric_code`,
`by_destination` populated at both the entry and the totals level (v1.48;
the totals-level rows carry only `qty_kg` and `value`, the two additive
fields, with `upstream` and `downstream` left at zero), `total_kg` on
`totals.alternative` where §6.2 has no room for it. A golden case that
compared §6.2's body would certify `api/engine_adapter.py` as well as the
engine, and a hoist or an omitted key there would read as an engine defect.

**A failure names the case, the path, the metric and both values.** A golden
failure that says only `assert False` wastes the debugging session it exists
to shorten, so `_diff` walks both documents in both directions and reports
every leaf that moved, with the path that located it.

**Decimals are compared as text at their own scale.** `format(value, "f")` is
what `api/serialization.wire()` does, so `"0.0000000000"` and `"0"` are
different answers here exactly as they are on the wire. That is deliberate:
§1.2's ten places are part of the contract, and three of the defects this
integration found were scale defects that every value-equality comparison in
the tree passed.

**Nothing here writes an expected file.** A runner with a `--regenerate` flag
is a runner that certifies whatever the engine currently does; the provenance
of every case's numbers is recorded in `_PROVENANCE` below and in the task-7
report.

**This module lives beside the cases, not in `tests/`.** It was
`tests/test_golden.py` for one commit, and `pytest tests/golden` then collected
zero tests and reported green — so `test_the_suite_is_not_empty`, the guard
against a renamed case directory, sat in a module the obvious command never
loaded. A suite whose whole job is to be run has to be found by the path people
will type.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from engine.bundle import FactorBundle
from engine.calculate import calculate
from engine.types import (
    CalculationRequest,
    CalculationResult,
    EntryInput,
    ScenarioLine,
)

GOLDEN = Path(__file__).resolve().parent

#: What each case is evidence *of*. Kept beside the runner rather than only in
#: the report so that a case whose purpose nobody can state is visible: the
#: parametrisation below fails if a `case_*` directory has no entry here.
_PROVENANCE = {
    "case_01_canonical_two_entry": (
        "Derived from tests/fixtures/calculate_request.json and "
        "calculate_response.json — numbers produced on B's line from the "
        "published formulas, independently of this engine. Multi-entry; a "
        "negative downstream factor (animal_feed, -0.15); prevention as a "
        "whole offset; a category-specific downstream row winning over the "
        "generic one; a generic (food_category: null) row; a formula using a "
        "constant; five metrics in sort_order; equivalences at both levels."
    ),
    "case_02_no_alternative_standard_mix": (
        "Derived from tests/fixtures/calculate_response_single.json, the "
        "second independently-produced fixture. food_category: null "
        "resolving to standard_mix; §3 rule 4 (no entry carries an "
        "alternative, so totals.alternative and totals.net_benefit are both "
        "null); fractional quantities."
    ),
    "case_03_prevention_whole_offset": (
        "Hand-computed; see the task-7 report for the arithmetic. **The O-7 "
        "case.** 800 kg moved from not_harvested to prevention yields "
        "net_benefit.co2e = 456.000, the figure contract v1.8 recomputed "
        "independently. It fails with 96.000 if factor_upstream's destination "
        "dimension is removed — pinned by "
        "test_case_03_fails_if_the_upstream_destination_dimension_is_removed."
    ),
    "case_04_gwp_horizon_100": (
        "Hand-computed. const_GWP_CH4 bound to GWP_CH4_100 = 28."
    ),
    "case_05_gwp_horizon_20": (
        "Hand-computed. The same bundle and the same lines as case_04 with "
        "gwp_horizon 20, so const_GWP_CH4 binds GWP_CH4_20 = 84 and ch4 "
        "triples while co2e — whose formula names no constant — does not "
        "move. Neither case alone pins the binding; the pair does."
    ),
    "case_06_constant_in_formula": (
        "Hand-computed. A metric whose formula reads const_LEVY_PER_KG at a "
        "non-zero value, over a null food_category that must resolve to "
        "standard_mix rather than to the other category in the bundle."
    ),
    "case_07_negative_total_and_zero_label": (
        "Hand-computed. A negative downstream factor large enough to make the "
        "metric total itself negative, and two equivalences off it: one "
        "genuinely negative (-209 km) and one that rounds to zero from below "
        "(-0.4), which §3 rule 5 requires to print `0` and not `-0`."
    ),
    "case_09_function_and_division": (
        "Hand-computed. The only case exercising §4.3's language beyond the "
        "five default formula shapes: a function call (`round(x, 2)`, which is "
        "what the admin panel advertises and therefore the first thing staff "
        "will type), a second function whose cap actually binds "
        "(`min(qty_kg * upstream, 500000)` against an uncapped 1259190), and a "
        "division inside a parenthesised sub-expression."
    ),
    "case_10_money_per_entry_rate_and_prevention": (
        "Hand-computed. §4.5, v1.48 (fix round 3): the only case exercising "
        "the money block. Three entries share case_08's bundle (a copy, with "
        "is_prevention added to the prevention row): one priced at $60,000/$9,000 "
        "(7.50/kg) with no alternative, so it diverts nothing regardless of its own "
        "price; one priced at $15,000/$6,000 (6.00/kg) diverting 400 of its 1,000 kg "
        "to prevention, contributing 6.00 x 400 = 2400.00; one entirely unpriced, "
        "diverting 300 of its own 500 kg to prevention and contributing nothing. "
        "total_value_nzd 75000.00, wasted_value_nzd 15000.00, wasted_share_percent "
        "20.00 (exact), saving_nzd 2400.00 (0 + 2400.00 + 0). A blended whole-form "
        "rate answers 15000/2700 x 700 = 3888.89 instead (all three entries current "
        "mass, all 700 kg diverted to prevention across the whole request) -- the "
        "number this case is confirmed to reject when the per-entry rate is reverted "
        "(see the task-5 report, fix round 3). "
        "**\u00a74.6 changed what it is evidence of.** Its third entry is unpriced, "
        "so `total_value_nzd` is a roll-up of an input only two of the three "
        "entries supplied: every money figure is now withheld and "
        "`data_state` says `incomplete` four times. It is the golden evidence "
        "for the incomplete state -- that a partial submission reports a gap "
        "and not 75000.00, which is two entries' money presented as three "
        "entries' total. case_11 carries the same three entries fully "
        "answered and keeps the per-entry-rate arithmetic above under test."
    ),
    "case_11_money_and_share_every_entry_answered": (
        "case_10's bundle and request with the third entry priced "
        "($25,000 / $2,500 = 5.00/kg, diverting 300 of its 500 kg to "
        "prevention for 1500.00) and a production total on all three. Every "
        "metric figure is case_10's unchanged -- the money and production "
        "fields reach no formula -- so the only hand arithmetic is \u00a74.5's "
        "and \u00a74.6's: total_value_nzd 100000.00, wasted_value_nzd 17500.00, "
        "wasted_share_percent 17.50, saving_nzd 3900.00 (0 + 2400.00 + "
        "1500.00; a blended whole-form rate answers 17500/2700 x 700 = "
        "4537.04 instead). production_share_percent is 2700 / 19500 = 13.85, "
        "and the three entries' own shares are 10.00, 20.00 and 20.00 -- "
        "whose mean is 16.67, so this case rejects an averaged share as well "
        "as a summed-over-answerers one. **This is the complete state**, and "
        "the only golden case in which any of the five figures carries a "
        "number."
    ),
    "case_12_disagreeing_data_states": (
        "Hand-designed, engine-computed (the same discipline case_10/11 were "
        "built with): pins the precedence `_combined_state` decides between "
        "two non-complete states, which no case before it exercised -- every "
        "existing money case moved `total_value_nzd` and `wasted_value_nzd` "
        "together. Three entries share case_10's bundle: the first two "
        "answer `total_input_kg` and only the second prices `total_value_nzd`; "
        "no entry answers `wasted_value_nzd` at all. `production_share_percent`"
        "'s coverage lands on `incomplete` (two of three entries answered) "
        "and `wasted_share_percent`'s on `not_supplied` (`total_value_nzd` is "
        "`incomplete`, `wasted_value_nzd` is `not_supplied`, and the correct "
        "precedence is `not_supplied` wins) -- one figure `incomplete` and "
        "another `not_supplied` in the same response, from entries that "
        "disagree in exactly that way. Reversing `_combined_state`'s two "
        "guards changes `wasted_share_percent`'s state to `incomplete` and "
        "this case catches it; see `engine/calculate.py::_combined_state`'s "
        "own docstring and `tests/test_calculator.py::"
        "test_incomplete_and_not_supplied_together_favour_not_supplied`."
    ),
    "case_08_mixed_alternative_rollup": (
        "Hand-computed. §3 rule 3: one entry with an alternative and one "
        "without. The entry without contributes its *current* figures to "
        "totals.alternative, so totals.net_benefit equals the first entry's "
        "net benefit exactly and nothing is inflated. Its equivalence value "
        "lands on 7210.5, which ROUND_HALF_UP and ROUND_HALF_EVEN answer "
        "differently."
    ),
    "case_13_equivalences_across_metrics": (
        "Hand-computed. case_01's bundle and request with the single "
        "km_driven equivalence (source_metric co2e) replaced by the "
        "client's three real equivalences from Task 4: vehicles_year "
        "(co2e, factor 1/2410), olympic_pools (water, factor "
        "1/2,500,000) and meals (mass, factor 1/0.45). Confirms an "
        "equivalence off water and one off mass are wired correctly and "
        "not silently skipped or mixed up with co2e -- at the request "
        "totals level, current co2e 4449.0000000000 x 0.0004149378 = "
        "1.8460582722 vehicles, water 1787600.0000000000 x 0.0000004 = "
        "0.7150400000 pools, and mass 2300.0000000000 x 2.2222222222 = "
        "5111.1111110600 meals -- the last of which is only reachable "
        "from the mass total (the co2e total would instead give "
        "9886.666666...)."
    ),
}


def _cases() -> list[Path]:
    return sorted(path for path in GOLDEN.glob("case_*") if path.is_dir())


def _read(case: Path, name: str):
    return json.loads((case / name).read_text(encoding="utf-8"))


# --------------------------------------------------------------- the loaders


def _lines(rows) -> tuple[ScenarioLine, ...]:
    return tuple(
        ScenarioLine(
            destination_code=row["destination_code"], qty_kg=Decimal(row["qty_kg"])
        )
        for row in rows
    )


def _entry_decimal(entry: dict, key: str) -> Decimal | None:
    """A money field is optional in a request (§4.5, v1.48); a case that
    omits the key must produce None, not KeyError, so an unpriced entry can be
    expressed at all."""
    value = entry.get(key)
    return None if value is None else Decimal(value)


def request_from_json(data) -> CalculationRequest:
    """§3's CalculationRequest from request.json.

    Lives here rather than in engine/ on purpose: §3 and §4 specify no
    reader for a request, api/schemas.py already owns the §6.2 wire shape,
    and a third parser inside the engine would be a second definition of the
    request with no contract behind it.

    The three money fields (§4.5, v1.48) are read the same optional way
    entry["alternative"] already is: absent in nine of the ten cases, and case_10
    is the one case that needs them to reach the engine at all.
    """
    return CalculationRequest(
        entries=tuple(
            EntryInput(
                sector_code=entry["sector_code"],
                food_category_code=entry["food_category_code"],
                current=_lines(entry["current"]),
                alternative=(
                    None if entry["alternative"] is None else _lines(entry["alternative"])
                ),
                total_input_kg=_entry_decimal(entry, "total_input_kg"),
                total_value_nzd=_entry_decimal(entry, "total_value_nzd"),
                wasted_value_nzd=_entry_decimal(entry, "wasted_value_nzd"),
            )
            for entry in data["entries"]
        ),
        gwp_horizon=data.get("gwp_horizon", 100),
    )


# ------------------------------------------------------------- the rendering


def _decimal(value: Decimal) -> str:
    """`format(value, "f")`, which is what `api/serialization.wire()` does.
    `str(Decimal("0E-10"))` is `"0E-10"`; the wire carries `"0.0000000000"`,
    and so does a golden file."""
    return format(value, "f")


def _breakdown_rows(rows) -> list[dict]:
    return [
        {
            "destination_code": row.destination_code,
            "qty_kg": _decimal(row.qty_kg),
            "upstream": _decimal(row.upstream),
            "downstream": _decimal(row.downstream),
            "value": _decimal(row.value),
        }
        for row in rows
    ]


def _metric(metric) -> dict:
    return {
        "metric_code": metric.metric_code,
        "unit": metric.unit,
        "display_precision": metric.display_precision,
        "total": _decimal(metric.total),
        "by_destination": _breakdown_rows(metric.by_destination),
    }


def _scenario(scenario) -> dict | None:
    if scenario is None:
        return None
    return {
        "total_kg": _decimal(scenario.total_kg),
        "metrics": {code: _metric(metric) for code, metric in scenario.metrics.items()},
        "equivalences": [
            {
                "code": equivalence.code,
                "label": equivalence.label,
                "value": _decimal(equivalence.value),
                "source_metric_code": equivalence.source_metric_code,
                "name": equivalence.name,
                "value_per_unit": _decimal(equivalence.value_per_unit),
                "value_per_unit_display": equivalence.value_per_unit_display,
                "source_note": equivalence.source_note,
            }
            for equivalence in scenario.equivalences
        ],
    }


def _benefit(benefit) -> dict | None:
    if benefit is None:
        return None
    return {code: _decimal(value) for code, value in benefit.items()}


def _optional_decimal(value: Decimal | None) -> str | None:
    """Unlike `_decimal`, this may legitimately receive `None` -- every
    `MoneyResult` field is optional (§4.5), and `None` there means "nobody
    supplied it", not zero."""
    return None if value is None else _decimal(value)


def _money(money) -> dict | None:
    """§4.5, v1.48. `None` when no entry supplied a money figure at all; not
    a metric, so it carries none of `_scenario`'s shape."""
    if money is None:
        return None
    return {
        "total_value_nzd": _optional_decimal(money.total_value_nzd),
        "wasted_value_nzd": _optional_decimal(money.wasted_value_nzd),
        "wasted_share_percent": _optional_decimal(money.wasted_share_percent),
        "saving_nzd": _optional_decimal(money.saving_nzd),
    }


def render(result: CalculationResult) -> dict:
    """§3's `CalculationResult` as `expected.json`'s shape. Field for field —
    this function performs no arithmetic and drops nothing."""
    return {
        "factor_set_version": result.factor_set_version,
        "is_mock": result.is_mock,
        "gwp_horizon": result.gwp_horizon,
        "totals": {
            "current": _scenario(result.totals.current),
            "alternative": _scenario(result.totals.alternative),
            "net_benefit": _benefit(result.totals.net_benefit),
            "money": _money(result.totals.money),
            "production_share_percent": _optional_decimal(
                result.totals.production_share_percent
            ),
            "data_state": {
                "production_share_percent":
                    result.totals.data_state.production_share_percent,
                "total_value_nzd": result.totals.data_state.total_value_nzd,
                "wasted_value_nzd": result.totals.data_state.wasted_value_nzd,
                "wasted_share_percent":
                    result.totals.data_state.wasted_share_percent,
                "saving_nzd": result.totals.data_state.saving_nzd,
            },
        },
        "entries": [
            {
                "sector_code": entry.sector_code,
                "food_category_code": entry.food_category_code,
                "current": _scenario(entry.current),
                "alternative": _scenario(entry.alternative),
                "net_benefit": _benefit(entry.net_benefit),
                "production_share_percent": _optional_decimal(
                    entry.production_share_percent
                ),
            }
            for entry in result.entries
        ],
    }


# ------------------------------------------------------------- the comparison

_MISSING = object()


def _flatten(node, path: str, out: dict[str, object]) -> None:
    if isinstance(node, dict):
        if not node:
            out[path] = "{}"
            return
        for key, value in node.items():
            _flatten(value, f"{path}.{key}", out)
    elif isinstance(node, list):
        if not node:
            out[path] = "[]"
            return
        for index, value in enumerate(node):
            _flatten(value, f"{path}[{index}]", out)
    else:
        out[path] = node


def _diff(expected, actual) -> list[str]:
    """Every leaf that moved, in both directions, each with its own path.

    Both directions matter: a metric the engine stopped producing and one it
    produced for the first time are different defects, and a comparison that
    only walked `expected` would report the first and miss the second.
    """
    left: dict[str, object] = {}
    right: dict[str, object] = {}
    _flatten(expected, "$", left)
    _flatten(actual, "$", right)

    problems = []
    for path in sorted(set(left) | set(right)):
        want = left.get(path, _MISSING)
        got = right.get(path, _MISSING)
        if want is _MISSING:
            problems.append(f"{path}: not in expected.json, engine produced {got!r}")
        elif got is _MISSING:
            problems.append(f"{path}: expected {want!r}, engine produced nothing")
        elif want != got:
            problems.append(f"{path}: expected {want!r}, engine produced {got!r}")
    return problems


# ------------------------------------------------------------------- the runs


def _load(case: Path):
    bundle = FactorBundle.from_json(_read(case, "bundle.json"))
    request = request_from_json(_read(case, "request.json"))
    return bundle, request


@pytest.mark.parametrize("case", _cases(), ids=lambda path: path.name)
def test_golden_case(case: Path):
    """The suite. One assertion per case, and a failure message that names the
    case, every leaf that moved, and both values."""
    bundle, request = _load(case)

    problems = bundle.validate()
    assert not problems, "\n".join(
        [f"{case.name}: bundle.json is not internally consistent:"] + problems
    )

    differences = _diff(_read(case, "expected.json"), render(calculate(request, bundle)))
    assert not differences, "\n".join(
        [f"{case.name}: {len(differences)} difference(s) from expected.json:"]
        + differences
    )


# ------------------------------------------------- the suite's own guardrails


def test_the_suite_is_not_empty():
    """A discovery bug turns this whole file into zero assertions, and a run
    of zero golden cases is green. `_cases()` is a glob, so this is the only
    thing standing between a renamed directory and a suite that certifies
    nothing."""
    assert len(_cases()) >= 9


@pytest.mark.parametrize("case", _cases(), ids=lambda path: path.name)
def test_every_case_has_its_three_files_and_a_stated_purpose(case: Path):
    for name in ("bundle.json", "request.json", "expected.json"):
        assert (case / name).is_file(), f"{case.name} has no {name} (§10.1)"
    assert case.name in _PROVENANCE, (
        f"{case.name} has no entry in _PROVENANCE. A case nobody can say what "
        "it certifies is a case that will be deleted the first time it fails."
    )


@pytest.mark.parametrize("case", _cases(), ids=lambda path: path.name)
def test_every_case_is_reproducible(case: Path):
    """§4.2: the engine is a pure function. Two calls over the same two
    documents must produce the same object — no clock, no environment, no
    dictionary-ordering luck."""
    bundle, request = _load(case)
    assert calculate(request, bundle) == calculate(request, bundle)


def test_the_golden_runner_would_notice_a_wrong_number():
    """**The test the suite is worthless without.** `_diff` compares
    documents, and a comparison with a bug in it passes everything. One leaf
    is perturbed by a single unit in the last place the contract carries, and
    the diff must find it and must name it."""
    expected = _read(GOLDEN / "case_03_prevention_whole_offset", "expected.json")
    tampered = json.loads(json.dumps(expected))
    tampered["entries"][0]["current"]["metrics"]["co2e"]["total"] = "456.0000000001"

    differences = _diff(expected, tampered)

    assert differences == [
        "$.entries[0].current.metrics.co2e.total: expected '456.0000000000', "
        "engine produced '456.0000000001'"
    ]


def test_the_golden_runner_would_notice_a_missing_metric():
    expected = _read(GOLDEN / "case_03_prevention_whole_offset", "expected.json")
    tampered = json.loads(json.dumps(expected))
    del tampered["entries"][0]["current"]["metrics"]["mass"]

    differences = _diff(expected, tampered)

    assert differences and all("mass" in problem for problem in differences)
    assert all("produced nothing" in problem for problem in differences)


def test_case_03_fails_if_the_upstream_destination_dimension_is_removed(monkeypatch):
    """**This is what makes case_03 evidence that O-7 is closed** rather than
    evidence that today's engine agrees with today's expected file.

    `FactorBundle.upstream()` is reverted to its pre-v1.8 behaviour — the
    signature keeps the destination argument and ignores it, which is exactly
    what the column's removal would amount to — and the case must then fail.
    It is not enough that it fails: it must fail with **96.000**, the figure
    contract v1.8 measured, so that a future change which broke the case some
    other way could not be mistaken for this one.
    """
    case = GOLDEN / "case_03_prevention_whole_offset"
    bundle, request = _load(case)

    def blind_to_destination(self, sector, food_cat, destination, metric):
        return self.upstream_factors.get((sector, food_cat, None, metric), Decimal("0"))

    monkeypatch.setattr(FactorBundle, "upstream", blind_to_destination)

    result = calculate(request, bundle)
    differences = _diff(_read(case, "expected.json"), render(result))

    assert differences, (
        "case_03 passes with the upstream destination dimension removed, so it "
        "is not evidence that O-7 is closed"
    )
    assert result.entries[0].net_benefit["co2e"] == Decimal("96.0000000000")
    assert any(
        "$.entries[0].net_benefit.co2e: expected '456.0000000000', "
        "engine produced '96.0000000000'" == problem
        for problem in differences
    ), differences
