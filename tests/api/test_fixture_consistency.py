"""The fixtures checked against the contract and against each other.

`tests/api/test_api.py` asserts that a live response has a fixture's *shape*.
That is a check between two artefacts, and it says nothing about whether the
fixture is internally possible: the pair it guarded for two revisions carried
a request of two entries totalling 15 kg against a response of one entry
totalling 10 kg, in which the same `landfill` destination held a downstream
factor of 0.99 under `current` and 0.00 under `alternative`. No factor set can
produce that body, and a key-set comparison cannot see it.

Nothing here touches the API. These tests read `tests/fixtures/` and assert the
properties §1.2, §5.4, §6.2, §6.3, §6.4, §9 and §10 state — the ones C and D
will rely on when they build against these files instead of a running backend.
"""

from __future__ import annotations

import ast
import json
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

#: §6.2's own derivation: 20 lines x 0.0005 kg of independent 3-dp rounding.
MASS_TOLERANCE_KG = Decimal("0.010")

#: §9's error codes. `NOT_FOUND` and `METHOD_NOT_ALLOWED` are framework
#: envelopes rather than contract codes and have no fixture.
ERROR_CODES = {
    "VALIDATION_ERROR",
    "UNKNOWN_CODE",
    "UNAUTHORIZED",
    "BLOCKED",
    "RATE_LIMITED",
    "FORMULA_ERROR",
    "NO_PUBLISHED_FACTOR_SET",
}

#: §1.2 and §2.2. A JSON decimal is a string, and its scale is part of the
#: contract: `"qty_kg": "1200.500"` is three places, not "about three".
SCALES = {
    "qty_kg": 3,
    "total_kg": 3,
    "kg_per_unit": 4,
    "share": 4,
    "value": 10,
    "total": 10,
    "upstream": 10,
    "downstream": 10,
    "value_per_kg": 10,
    "value_per_unit": 10,
}


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def taxonomy():
    return load("taxonomy.json")


@pytest.fixture(scope="module")
def factors():
    return load("factors.json")


@pytest.fixture(scope="module")
def request_fixture():
    return load("calculate_request.json")


@pytest.fixture(scope="module")
def response_fixture():
    return load("calculate_response.json")


@pytest.fixture(scope="module")
def single_fixture():
    return load("calculate_response_single.json")


# --------------------------------------------------------------- primitives


def walk(node, path="$"):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk(value, f"{path}[{index}]")
    else:
        yield path, node


ALL_FIXTURES = sorted(
    str(path.relative_to(FIXTURES)).replace("\\", "/")
    for path in FIXTURES.rglob("*.json")
)


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_every_decimal_is_a_string_at_the_contracted_scale(name):
    """§1.2. JavaScript's Number is a double, so decimals travel as strings."""
    for path, value in walk(load(name)):
        parts = path.split(".")
        key = parts[-1].split("[")[0]
        #: `net_benefit` is keyed by metric code, so its values cannot be
        #: reached by name the way `total` and `qty_kg` can. They are metric
        #: values and carry the same ten places.
        if len(parts) > 1 and parts[-2] == "net_benefit":
            required = 10
        elif key in SCALES:
            required = SCALES[key]
        else:
            continue
        if value is None:
            continue
        assert isinstance(value, str), f"{name} {path}: {value!r} is not a string"
        assert "e" not in value.lower(), f"{name} {path}: {value!r} is exponential"
        Decimal(value)  # raises if it is not a decimal at all
        assert "." in value, f"{name} {path}: {value!r} carries no decimal point"
        places = len(value.split(".")[1])
        assert places == required, (
            f"{name} {path}: {value!r} has {places} decimal places, "
            f"the contract requires {required}"
        )


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_no_fixture_leaks_a_primary_key(name):
    """§1.1. `code` is the cross-layer identifier; the front end never learns
    an auto-increment id, so a fixture must not teach it one."""
    for path, _ in walk(load(name)):
        key = path.rsplit(".", 1)[-1].split("[")[0]
        assert key not in {"id", "factor_set_id", "submission_id"}, (
            f"{name} {path}: a primary key reached a fixture"
        )


# ----------------------------------------------------------------- taxonomy


def test_taxonomy_is_internally_consistent(taxonomy):
    groups = {row["code"]: row for row in taxonomy["destination_groups"]}
    destinations = {row["code"]: row for row in taxonomy["destinations"]}
    for row in taxonomy["destinations"]:
        assert row["group"] in groups, f"{row['code']} points at a missing group"

    # §2.1: exactly one standard mix, enforced in admin/taxonomy_rules.py.
    assert sum(1 for row in taxonomy["food_categories"] if row["is_standard_mix"]) == 1

    # §10: without these two the demo cannot show the mass-conserving offset
    # or the non-waste half of the MfE taxonomy.
    assert "prevention" in destinations
    assert groups[destinations["prevention"]["group"]]["is_waste"] is False
    non_waste = [
        row for row in taxonomy["destinations"] if not groups[row["group"]]["is_waste"]
    ]
    assert len(non_waste) > 1, "the reuse group needs destinations other than prevention"

    # v1.3 §2.1: one description field, not two. C's expander falls back to
    # `description`, so a surviving `details` shows every sector twice.
    for row in taxonomy["sectors"]:
        assert "details" not in row, f"{row['code']} still carries `details`"
        assert row["description"], f"{row['code']} has no description"

    for code in ("code", "name", "unit", "display_precision"):
        assert all(code in row for row in taxonomy["metrics"])
    for row in taxonomy["unit_presets"]:
        assert row["food_category"] is None or row["food_category"] in {
            item["code"] for item in taxonomy["food_categories"]
        }


# ------------------------------------------------------------------ factors


def test_factor_rows_reference_the_taxonomy(taxonomy, factors):
    sectors = {row["code"] for row in taxonomy["sectors"]}
    foods = {row["code"] for row in taxonomy["food_categories"]}
    destinations = {row["code"] for row in taxonomy["destinations"]}
    metrics = {row["code"] for row in taxonomy["metrics"]}

    assert factors["formulas"], "an empty formulas array renders the methodology page blank"
    assert factors["constants"] and factors["upstream"] and factors["downstream"]
    assert factors["equivalences"]

    for row in factors["formulas"]:
        assert row["metric"] in metrics
    for row in factors["upstream"]:
        assert row["sector"] in sectors
        assert row["food_category"] in foods
        #: §2.2 (v1.8): null means "every destination", and it is a legal key
        #: value rather than a missing field — `in row` is asserted separately
        #: because `row.get("destination")` would pass on a row that omits it.
        assert "destination" in row
        assert row["destination"] is None or row["destination"] in destinations
        assert row["metric"] in metrics
        assert "source_note" in row and "data_quality" in row  # §6.3
    for row in factors["downstream"]:
        assert row["destination"] in destinations
        assert row["food_category"] is None or row["food_category"] in foods
        assert row["metric"] in metrics
        assert "source_note" in row and "data_quality" in row  # §6.3
    for row in factors["equivalences"]:
        assert row["source_metric"] in metrics
        assert "{value}" in row["label_template"]
        assert "source_note" in row  # §6.3; equivalence has no data_quality

    # §2.1: prevention contributes no downstream impact, in every metric that
    # names it at all.
    for row in factors["downstream"]:
        if row["destination"] == "prevention":
            assert Decimal(row["value_per_kg"]) == 0

    assert factors["factor_set"]["is_mock"] is True
    assert "PLACEHOLDER" in factors["factor_set"]["version_label"]


# --------------------------------------------------------- the calculate pair

_OPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
}


def evaluate(expression, variables):
    """§4.3's expression language: no arrays, no loops, no aggregates."""

    def walk_node(node):
        if isinstance(node, ast.Expression):
            return walk_node(node.body)
        if isinstance(node, ast.BinOp):
            return _OPS[type(node.op)](walk_node(node.left), walk_node(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -walk_node(node.operand)
        if isinstance(node, ast.Name):
            return variables[node.id]
        if isinstance(node, ast.Constant):
            return Decimal(str(node.value))
        raise AssertionError(f"unsupported node {type(node).__name__}")

    return walk_node(ast.parse(expression, mode="eval"))


def lines_of(scenario):
    """A scenario's request lines, read back off any one metric's breakdown."""
    metric = next(iter(scenario["metrics"].values()))
    return [(row["destination"], Decimal(row["qty_kg"])) for row in metric["by_destination"]]


def test_the_request_and_the_response_describe_the_same_calculation(
    request_fixture, response_fixture
):
    """The pair must correspond. The set this replaces did not.

    A request of two entries and 1,500 kg answered by a response of one entry
    and 10 kg is the failure this asserts against, and it survived because
    nothing compared the two files.
    """
    assert response_fixture["gwp_horizon"] == request_fixture["gwp_horizon"]
    assert len(response_fixture["entries"]) == len(request_fixture["entries"])

    for index, (sent, got) in enumerate(
        zip(request_fixture["entries"], response_fixture["entries"])
    ):
        where = f"entries[{index}]"
        # §3 rule 1: entries preserve request order.
        assert got["sector"] == sent["sector"], where
        assert got["food_category"] == sent["food_category"], where
        for scenario in ("current", "alternative"):
            if sent[scenario] is None:
                assert got[scenario] is None, f"{where}.{scenario}"
                assert got["net_benefit"] is None, where
                continue
            expected = [
                (line["destination"], Decimal(line["qty_kg"])) for line in sent[scenario]
            ]
            for metric_code, metric in got[scenario]["metrics"].items():
                assert lines_of({"metrics": {metric_code: metric}}) == expected, (
                    f"{where}.{scenario}.metrics.{metric_code}.by_destination "
                    "does not match the lines the request carried"
                )
            mass = sum((qty for _, qty in expected), Decimal("0"))
            assert Decimal(got[scenario]["total_kg"]) == mass, f"{where}.{scenario}"


def test_every_entry_conserves_mass_between_its_scenarios(request_fixture):
    """§6.2. Wasting less is expressed by moving mass to `prevention`, not by
    sending less of it — otherwise `net_benefit` is free."""
    for index, entry in enumerate(request_fixture["entries"]):
        if entry["alternative"] is None:
            continue
        current = sum((Decimal(l["qty_kg"]) for l in entry["current"]), Decimal("0"))
        alternative = sum(
            (Decimal(l["qty_kg"]) for l in entry["alternative"]), Decimal("0")
        )
        assert abs(alternative - current) <= MASS_TOLERANCE_KG, (
            f"entries[{index}].alternative describes {alternative} kg against a "
            f"current scenario of {current} kg"
        )
        assert len({l["destination"] for l in entry["alternative"]}) == len(
            entry["alternative"]
        ), f"entries[{index}].alternative repeats a destination"

    seen = {(e["sector"], e["food_category"]) for e in request_fixture["entries"]}
    assert len(seen) == len(request_fixture["entries"]), "duplicate (sector, food_category)"


def test_prevention_is_a_whole_offset_upstream_as_well_as_down(factors):
    """Open item O-7, and the reason `factor_upstream.destination_id` exists.

    `docs/architecture.md` §4.1 says `prevention`'s factors are all zero — a
    100% offset — and that this is what stops `net_benefit` being inflated by
    simply assuming less waste. Until v1.8 only the *downstream* half of that
    was expressible: upstream was keyed on (sector, food_category, metric) and
    could not see the destination, so a line moved to `prevention` kept the
    entry's full upstream factor. On this fixture set, 800 kg of
    `not_harvested` moved to `prevention` yielded a `net_benefit.co2e` of
    96.000 where a true offset yields 456.000 — 78.9% of the benefit missing,
    always in the same direction, on the client's headline claim.

    The schema alone does not close it; the rows do. Every general upstream row
    needs a `prevention` counterpart at zero, and this is what says so.
    """
    general = {(row["sector"], row["food_category"], row["metric"])
               for row in factors["upstream"] if row["destination"] is None}
    prevention = {(row["sector"], row["food_category"], row["metric"]): row
                  for row in factors["upstream"]
                  if row["destination"] == "prevention"}

    assert general, "no general upstream rows at all"
    missing = general - set(prevention)
    assert not missing, (
        "these (sector, food_category, metric) tuples have a general upstream "
        "factor but no `prevention` row, so a line moved to `prevention` still "
        f"carries their full upstream burden: {sorted(missing)}"
    )
    for key, row in prevention.items():
        assert Decimal(row["value_per_kg"]) == 0, f"{key} prevents at {row['value_per_kg']}"
        #: §2.2's rationale for source_note: a zero is the number most likely
        #: to be read as missing data rather than as a decision.
        assert row["source_note"], f"{key} states no basis for its zero"

    for row in factors["downstream"]:
        if row["destination"] == "prevention":
            assert Decimal(row["value_per_kg"]) == 0, row


def test_the_prevention_lines_of_the_response_draw_no_upstream(response_fixture):
    """The other half of O-7, at the surface C and D build against.

    A `prevention` line whose `upstream` is not zero is the defect back, and it
    would be visible in the results table before it was visible anywhere else.
    """
    seen = 0
    for entry in response_fixture["entries"]:
        for scenario in ("current", "alternative"):
            if entry[scenario] is None:
                continue
            for code, metric in entry[scenario]["metrics"].items():
                for row in metric["by_destination"]:
                    if row["destination"] != "prevention":
                        continue
                    seen += 1
                    assert Decimal(row["upstream"]) == 0, (
                        f"entries[].{scenario}.{code}: a prevention line draws "
                        f"upstream {row['upstream']}, so it is not the 100% "
                        "offset architecture.md 4.1 describes"
                    )
                    #: The *line value* is deliberately not asserted to be
                    #: zero. `mass`'s formula is `qty_kg`, so a prevention line
                    #: still weighs 800 kg — as it must, or the two scenarios
                    #: would stop conserving mass and the offset would be a
                    #: disappearance instead. Zero factors, not zero mass.
    assert seen, "the canonical response has no prevention line to check"


@pytest.mark.parametrize(
    "name", ["calculate_response.json", "calculate_response_single.json"]
)
def test_a_destinations_factors_do_not_change_between_scenarios(name):
    """The defect that made the old response body impossible.

    `landfill` carried `downstream: "0.9900000000"` under `current` and
    `"0.0000000000"` under `alternative` in the same entry. A downstream factor
    is a property of `(destination, food_category, metric)` (§2.2) and an
    upstream factor of `(sector, food_category, destination, metric)`; neither
    knows which scenario it is being read for.

    **The destination joined the upstream key in v1.8** (open item O-7). Before
    it did, this test asserted that one entry reported a single upstream factor
    across every destination in both of its scenarios — which was true of the
    schema and was exactly the assumption that made `prevention` impossible to
    express, since a 100% offset means `prevention` drawing a *different*
    upstream factor from the rest of the entry. The check keeps its teeth
    because a destination appearing in both scenarios of one entry — the
    canonical fixture's `animal_feed` does — must still report the same
    upstream in both.
    """
    fixture = load(name)
    downstream_seen = {}
    for entry in fixture["entries"]:
        upstream_seen = {}
        for scenario in ("current", "alternative"):
            if entry[scenario] is None:
                continue
            for metric_code, metric in entry[scenario]["metrics"].items():
                for row in metric["by_destination"]:
                    up_key = (metric_code, entry["sector"], entry["food_category"],
                              row["destination"])
                    assert upstream_seen.setdefault(up_key, row["upstream"]) == row[
                        "upstream"
                    ], f"{name}: upstream differs within one entry for {up_key}"
                    down_key = (metric_code, row["destination"], entry["food_category"])
                    assert downstream_seen.setdefault(
                        down_key, row["downstream"]
                    ) == row["downstream"], (
                        f"{name}: downstream differs between scenarios for {down_key}"
                    )


@pytest.mark.parametrize(
    "name", ["calculate_response.json", "calculate_response_single.json"]
)
def test_the_response_arithmetic_closes(name):
    """Lines sum to metric totals, entries sum to the roll-up, and
    `net_benefit` is `current - alternative` at both levels (§4.2)."""
    fixture = load(name)
    totals = fixture["totals"]
    rolled = {"current": {}, "alternative": {}}
    mass = Decimal("0")

    for index, entry in enumerate(fixture["entries"]):
        where = f"{name} entries[{index}]"
        mass += Decimal(entry["current"]["total_kg"])
        for scenario in ("current", "alternative"):
            source = entry[scenario] or entry["current"]  # §3 rule 3
            if scenario == "alternative" and totals["alternative"] is None:
                continue
            for code, metric in source["metrics"].items():
                lines = sum(
                    (Decimal(row["value"]) for row in metric["by_destination"]),
                    Decimal("0"),
                )
                assert lines == Decimal(metric["total"]), (
                    f"{where}.{scenario}.metrics.{code}: by_destination sums to "
                    f"{lines}, total says {metric['total']}"
                )
                rolled[scenario][code] = rolled[scenario].get(code, Decimal("0")) + lines
        if entry["net_benefit"] is not None:
            for code, value in entry["net_benefit"].items():
                assert Decimal(value) == Decimal(
                    entry["current"]["metrics"][code]["total"]
                ) - Decimal(entry["alternative"]["metrics"][code]["total"]), where

    # §3: the wire's `totals.total_kg` is the current scenario's mass.
    assert Decimal(totals["total_kg"]) == mass, f"{name}: totals.total_kg"
    for code, metric in totals["current"]["metrics"].items():
        assert Decimal(metric["total"]) == rolled["current"][code], f"{name} totals {code}"
        assert "by_destination" not in metric, "§3 rule 2: empty at the totals level"
    if totals["alternative"] is None:
        assert totals["net_benefit"] is None
        assert all(entry["alternative"] is None for entry in fixture["entries"])
        return
    for code, metric in totals["alternative"]["metrics"].items():
        assert Decimal(metric["total"]) == rolled["alternative"][code], f"{name} {code}"
    for code, value in totals["net_benefit"].items():
        assert Decimal(value) == Decimal(
            totals["current"]["metrics"][code]["total"]
        ) - Decimal(totals["alternative"]["metrics"][code]["total"]), f"{name} {code}"


@pytest.mark.parametrize(
    "name", ["calculate_response.json", "calculate_response_single.json"]
)
def test_every_line_is_its_formula_applied_to_the_published_factors(
    name, taxonomy, factors
):
    """The fixtures are one factor set, not three files that resemble one.

    Every `by_destination` row is re-derived from `factors.json` — the same
    rows `GET /factors` publishes — through that metric's stored expression.
    A number nudged by hand in any of the three files fails here.
    """
    fixture = load(name)
    formulas = {row["metric"]: row["expression"] for row in factors["formulas"]}
    constants = {
        f"const_{row['code']}": Decimal(row["value"]) for row in factors["constants"]
    }
    standard_mix = next(
        row["code"] for row in taxonomy["food_categories"] if row["is_standard_mix"]
    )

    def upstream(sector, food, destination, metric):
        #: §2.2 (v1.8): exact destination first, then the generic row
        #: (destination null), then zero — the same three-step
        #: `downstream` below has always used for food_category.
        for candidate in (destination, None):
            for row in factors["upstream"]:
                if (row["sector"], row["food_category"], row["destination"],
                        row["metric"]) == (sector, food, candidate, metric):
                    return Decimal(row["value_per_kg"])
        return Decimal("0")

    def downstream(destination, food, metric):
        for candidate in (food, None):  # §2.2: exact match, then the null row
            for row in factors["downstream"]:
                if (row["destination"], row["food_category"], row["metric"]) == (
                    destination, candidate, metric
                ):
                    return Decimal(row["value_per_kg"])
        return Decimal("0")

    for entry in fixture["entries"]:
        # §6.2: a null food_category is the standard mix to the engine.
        food = entry["food_category"] or standard_mix
        for scenario in ("current", "alternative"):
            if entry[scenario] is None:
                continue
            for code, metric in entry[scenario]["metrics"].items():
                assert code in formulas, f"{code} has no published formula"
                for row in metric["by_destination"]:
                    up = upstream(entry["sector"], food, row["destination"], code)
                    down = downstream(row["destination"], food, code)
                    assert Decimal(row["upstream"]) == up, (
                        f"{name}: {code}/{row['destination']} reports upstream "
                        f"{row['upstream']}, factors.json publishes {up}"
                    )
                    assert Decimal(row["downstream"]) == down, (
                        f"{name}: {code}/{row['destination']} reports downstream "
                        f"{row['downstream']}, factors.json publishes {down}"
                    )
                    variables = {
                        "qty_kg": Decimal(row["qty_kg"]),
                        "upstream": up,
                        "downstream": down,
                        **constants,
                    }
                    assert Decimal(row["value"]) == evaluate(formulas[code], variables), (
                        f"{name}: {code}/{row['destination']} does not equal "
                        f"`{formulas[code]}` applied to the published factors"
                    )


@pytest.mark.parametrize(
    "name", ["calculate_response.json", "calculate_response_single.json"]
)
def test_every_equivalence_is_derived_from_the_metric_total_it_names(name, factors):
    """§4.2. An equivalence is computed **from the rolled-up metric total**,
    not summed from the per-entry values — the conversion is linear so the two
    agree mathematically, but one computation is one rounding.

    Until this existed, equivalences were the one class of number in the set
    that nothing re-derived: `value` and `label` could be changed to anything
    and the whole suite still passed. That is the same shape of defect as the
    impossible response body this file was written to catch.

    **The label format is now §3's rule 5**, not the undocumented convention
    this fixture set invented to match §6.2's samples: `{value}` interpolates
    to the value rounded to a whole unit, `ROUND_HALF_UP`, with a comma
    thousands separator ("18,597 km"), and everything else in the template is
    copied verbatim.

    `quantize(Decimal("1"))` alone is **not** that rule — `Decimal`'s default
    context rounds half to even, so it would turn 2.5 into 2 and pin the
    wrong rule the first time a fixture value landed on a half. No value in
    this set does, which is exactly why the rounding mode has to be written
    out rather than left to the default: both modes pass today, and only one
    of them is what A has been asked to build.
    """
    fixture = load(name)
    specs = {row["code"]: row for row in factors["equivalences"]}

    def check(scenario, where):
        if scenario is None:
            return
        assert scenario["equivalences"], f"{where}: no equivalences at all"
        assert [item["code"] for item in scenario["equivalences"]] == [
            row["code"] for row in sorted(specs.values(), key=lambda r: r["sort_order"])
        ], f"{where}: equivalences are not the published set, in sort_order"
        for item in scenario["equivalences"]:
            spec = specs[item["code"]]
            source = item["source_metric"]
            assert source == spec["source_metric"], f"{where}.{item['code']}"
            total = Decimal(scenario["metrics"][source]["total"])
            expected = total * Decimal(spec["value_per_unit"])
            assert Decimal(item["value"]) == expected, (
                f"{where}.{item['code']}: value is {item['value']}, but "
                f"{total} x {spec['value_per_unit']} is {expected}"
            )
            shown = f"{expected.quantize(Decimal('1'), rounding=ROUND_HALF_UP):,}"
            assert item["label"] == spec["label_template"].replace("{value}", shown), (
                f"{where}.{item['code']}: label is {item['label']!r}, the "
                f"template interpolates to "
                f"{spec['label_template'].replace('{value}', shown)!r}"
            )

    for scenario in ("current", "alternative"):
        check(fixture["totals"][scenario], f"{name} totals.{scenario}")
    for index, entry in enumerate(fixture["entries"]):
        for scenario in ("current", "alternative"):
            check(entry[scenario], f"{name} entries[{index}].{scenario}")


def test_taxonomy_codes_and_names_are_the_shipped_seeds(taxonomy):
    """`taxonomy.json` must be `admin/seed.py`, not a plausible neighbour.

    Every other check in this file is internal to `tests/fixtures/`, and the
    live shape test runs against the abridged seed in
    `tests/support/sqlite.py`. So a *coordinated* rename — `compost` changed
    to `compost_aerobic_digestion` in the taxonomy, the request, the response
    and the factors together — passed the entire suite while reintroducing
    exactly the defect this fixture set was rebuilt to remove: `code` is the
    cross-layer identifier (§1.1), and a fixture that spells a destination
    differently from the seed teaches the front end a code no deployment
    holds. This is the only assertion that reaches outside `tests/fixtures/`.
    """
    from admin.seed import (
        DESTINATION_GROUPS,
        DESTINATIONS,
        FOOD_CATEGORIES,
        METRICS,
        SECTORS,
        UNIT_PRESETS,
    )

    assert {row["code"]: (row["name"], row["sort_order"]) for row in taxonomy["sectors"]} == {
        code: (name, sort_order) for code, name, sort_order in SECTORS
    }
    assert {
        row["code"]: (row["name"], row["is_standard_mix"], row["sort_order"])
        for row in taxonomy["food_categories"]
    } == {
        code: (name, is_standard_mix, sort_order)
        for code, name, is_standard_mix, sort_order in FOOD_CATEGORIES
    }
    assert {
        row["code"]: (row["name"], row["is_waste"], row["sort_order"])
        for row in taxonomy["destination_groups"]
    } == {
        code: (name, is_waste, sort_order)
        for code, name, is_waste, sort_order in DESTINATION_GROUPS
    }
    #: `is_prevention` is in the tuple because §6.1 puts it on the wire and
    #: `web/js/calculator.js` keeps a flagged destination off the current-waste
    #: list by reading it. A fixture that dropped the key, or set it on the
    #: wrong row, would offer a destination the server answers 400 for.
    assert {
        row["code"]: (
            row["group"], row["name"], row["is_prevention"], row["sort_order"]
        )
        for row in taxonomy["destinations"]
    } == {
        code: (group_code, name, is_prevention, sort_order)
        for group_code, code, name, is_prevention, sort_order in DESTINATIONS
    }
    assert {
        row["code"]: (
            row["name"],
            row["unit"],
            row["display_unit"],
            row["display_precision"],
            row["sort_order"],
        )
        for row in taxonomy["metrics"]
    } == {
        code: (name, unit, display_unit, precision, sort_order)
        for code, name, unit, display_unit, precision, sort_order in METRICS
    }
    assert {
        row["code"]: (row["label"], Decimal(row["kg_per_unit"]))
        for row in taxonomy["unit_presets"]
    } == {code: (label, kg_per_unit) for code, label, kg_per_unit in UNIT_PRESETS}


def test_the_codes_the_fixtures_calculate_with_are_shipped_codes():
    """The same binding, one layer down.

    The taxonomy check above is set equality against the seed; this one covers
    the request, the response and the factor rows, which name codes directly
    and would otherwise only ever be checked against the taxonomy file that
    was renamed alongside them.
    """
    from admin.seed import DESTINATIONS, FOOD_CATEGORIES, SECTORS

    sectors = {code for code, _, _ in SECTORS}
    foods = {code for code, _, _, _ in FOOD_CATEGORIES}
    destinations = {code for _, code, _, _, _ in DESTINATIONS}

    for name in (
        "calculate_request.json",
        "calculate_response.json",
        "calculate_response_single.json",
        "factors.json",
        "stats.json",
    ):
        for path, value in walk(load(name)):
            key = path.rsplit(".", 1)[-1].split("[")[0]
            if value is None or not isinstance(value, str):
                continue
            if key in {"sector", "destination", "food_category"}:
                pool = {
                    "sector": sectors,
                    "destination": destinations,
                    "food_category": foods,
                }[key]
                assert value in pool, f"{name} {path}: {value!r} is not a seeded code"


@pytest.mark.parametrize(
    "name", ["calculate_response.json", "calculate_response_single.json"]
)
def test_the_response_only_names_codes_the_taxonomy_defines(name, taxonomy):
    fixture = load(name)
    sectors = {row["code"] for row in taxonomy["sectors"]}
    foods = {row["code"] for row in taxonomy["food_categories"]}
    destinations = {row["code"] for row in taxonomy["destinations"]}
    metric_specs = {row["code"]: row for row in taxonomy["metrics"]}

    assert fixture["factor_set"]["is_mock"] is True
    assert fixture["factor_source"] in {"published", "inline"} or fixture[
        "factor_source"
    ].startswith("version:")

    for entry in fixture["entries"]:
        assert entry["sector"] in sectors
        assert entry["food_category"] is None or entry["food_category"] in foods
        for scenario in ("current", "alternative"):
            if entry[scenario] is None:
                continue
            for code, metric in entry[scenario]["metrics"].items():
                assert code in metric_specs, f"{name}: metric {code} is not in the taxonomy"
                assert metric["unit"] == metric_specs[code]["unit"]
                assert (
                    metric["display_precision"] == metric_specs[code]["display_precision"]
                )
                for row in metric["by_destination"]:
                    assert row["destination"] in destinations
            for item in entry[scenario]["equivalences"]:
                assert item["source_metric"] in metric_specs


def test_prevention_is_only_ever_an_alternative_destination(request_fixture):
    """§7.3a. Offering `prevention` in the current scenario would let a user
    claim to be already preventing the waste they are about to describe."""
    for entry in request_fixture["entries"]:
        assert "prevention" not in {line["destination"] for line in entry["current"]}
    used = {
        line["destination"]
        for entry in request_fixture["entries"]
        for line in (entry["alternative"] or [])
    }
    assert "prevention" in used, "the canonical request must demonstrate the offset"


# ---------------------------------------------------------------- statistics


def test_stats_fixture_gives_the_statistics_page_something_to_render(taxonomy):
    stats = load("stats.json")
    threshold = stats["suppression_threshold"]
    codes = {row["code"] for row in taxonomy["destinations"]}
    codes |= {row["code"] for row in taxonomy["sectors"]}
    codes |= {row["code"] for row in taxonomy["food_categories"]}
    codes |= {"other", "unspecified"}

    masses = []
    for breakdown in ("by_destination", "by_sector", "by_food_category"):
        buckets = stats[breakdown]
        assert buckets, f"{breakdown} is empty; D has nothing to build against"
        assert {b["code"] for b in buckets} <= codes, f"{breakdown} names an unknown code"

        # §5.4: suppression happens server-side, and a suppressed bucket is
        # merged rather than dropped so the remaining shares are not inflated.
        merged = [b for b in buckets if b["code"] == "other"]
        assert len(merged) == 1, f"{breakdown} has no `other` bucket to render"
        for bucket in buckets:
            if bucket["code"] != "other":
                assert bucket["count"] >= threshold, (
                    f"{breakdown}: {bucket['code']} is below the threshold and "
                    "should have been merged into `other`"
                )
        assert buckets[-1]["code"] == "other", f"{breakdown}: `other` sorts last"
        assert buckets == sorted(
            buckets[:-1], key=lambda b: (-b["count"], b["code"])
        ) + [buckets[-1]], f"{breakdown} is not in _bucketise order"

        # §6.4: share is computed within its own breakdown and sums to 1.
        assert sum(Decimal(b["share"]) for b in buckets) == Decimal("1.0000"), breakdown
        masses.append(sum(Decimal(b["total_kg"]) for b in buckets))

    # Each breakdown partitions the same current-scenario mass — by_sector and
    # by_food_category over entries, by_destination over the lines those
    # entries are made of — so all three totals are the same number.
    assert len(set(masses)) == 1, f"the three breakdowns disagree on total mass: {masses}"

    # §5.4 and §6.4: `total_calculations` counts submissions, bucket counts
    # count entries, and the fixture must make the gap visible rather than
    # hide it, because D's copy has to survive it.
    entries = sum(b["count"] for b in stats["by_sector"])
    assert entries == sum(b["count"] for b in stats["by_food_category"])
    assert entries > stats["total_calculations"], (
        "the fixture should show a multi-entry population; equal figures teach "
        "D that the two numbers are the same thing"
    )
    assert sum(b["count"] for b in stats["by_destination"]) > entries

    # `prevention` is the destination for waste that did not happen. It lives
    # in the alternative scenario and must never reach a public statistic.
    assert "prevention" not in {b["code"] for b in stats["by_destination"]}

    assert stats["generated_at"].endswith("Z")  # §1.3


# -------------------------------------------------------------------- errors


def test_every_contract_error_code_has_a_fixture():
    """§10. `blocked.json` was the one code with no sample, and it is the one
    error C and D must handle without offering a retry."""
    files = sorted(path.name for path in (FIXTURES / "errors").glob("*.json"))
    found = {}
    for name in files:
        body = load(f"errors/{name}")
        assert set(body) == {"error"}, name
        error = body["error"]
        assert set(error) == {"code", "message", "details"}, name
        assert error["message"], name
        found[error["code"]] = error
    assert ERROR_CODES <= set(found), f"missing: {sorted(ERROR_CODES - set(found))}"


def test_blocked_is_the_one_envelope_whose_details_is_null():
    """§9.2. `details` is always null and `message` never varies: the refusal
    must not say which rule fired, when it expires, or that a list exists."""
    blocked = load("errors/blocked.json")["error"]
    assert blocked["details"] is None
    assert "block" not in blocked["message"].lower()
    for path in sorted((FIXTURES / "errors").glob("*.json")):
        if path.name == "blocked.json":
            continue
        assert isinstance(load(f"errors/{path.name}")["error"]["details"], list), path.name


def test_validation_details_use_the_bracket_path_form():
    """§9. Pydantic's `loc` renders `entries.0.current.1.qty_kg`; a front end
    building a lookup key writes `entries[0].current[1].qty_kg`. If the API
    emits one and the client looks up the other, field highlighting silently
    never binds."""
    details = load("errors/validation_error.json")["error"]["details"]
    assert details
    for item in details:
        assert set(item) >= {"field", "issue"}
        assert ".0." not in item["field"] and not item["field"].endswith(".0")
        assert item["field"].startswith("entries[")
    # §6.2: a mass-conservation failure points at the whole array, because no
    # single line is at fault.
    assert any(
        item["field"].endswith(".alternative") and item["issue"] == "mass_not_conserved"
        for item in details
    )
