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
    #: §4.5, v1.48. NZD figures, not metric ones -- two places, matching
    #: `api/schemas.py`'s `decimal_places=2` and `DECIMAL(14, 2)` in
    #: `db/models.py`, not the ten every metric value above carries.
    "total_input_kg": 3,
    "total_value_nzd": 2,
    "wasted_value_nzd": 2,
    "wasted_share_percent": 2,
    "saving_nzd": 2,
    #: §4.6. A percentage, two places, on `totals` and on every entry. It
    #: fires on `calculate_response.json`'s first entry, which supplies a
    #: production total and therefore carries `"15.00"` -- a `SCALES` entry
    #: every fixture answers with `null` is an entry that never runs, which
    #: is how `total_input_kg` sat here unexercised for a revision.
    "production_share_percent": 2,
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
def export_request_fixture():
    return load("export_pdf_request.json")


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
        #: §4.6's `data_state` is keyed by the figure it describes, so four
        #: of its five keys collide by name with `SCALES` entries above --
        #: and its values are the enum strings `complete` / `incomplete` /
        #: `not_supplied`, not decimals. Skipped by parent, the same way
        #: `net_benefit` is matched by parent: the figures themselves, one
        #: level up, are still checked.
        elif len(parts) > 1 and parts[-2] == "data_state":
            continue
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

    #: v1.58. The key is part of §6.1 whether or not any row is in it, and
    #: `item_level_enabled` is what releases step 2.5 on the front end.
    assert isinstance(taxonomy["food_items"], list)
    assert taxonomy["factor_set"]["item_level_enabled"] is False, (
        "the shipped mock set prices no food individually, so the switch that "
        "releases step 2.5 must be off -- an interface asking a more specific "
        "question than the numbers can answer is what design section 8 warns "
        "about"
    )
    #: Empty today, and the fixture says so honestly rather than inventing a
    #: vocabulary: `admin/seed.py` seeds no `food_item`, so this is what every
    #: deployment returns. Design section 9 leaves "are the ~20 foods in the
    #: client's table 1 the full list or a sample?" open, and a fixture that
    #: answered it for them would put codes in front of C and D that no
    #: database holds. The rule each row must satisfy is asserted anyway, so
    #: the day the rows arrive they are checked rather than merely added.
    for row in taxonomy["food_items"]:
        assert set(row) == {"code", "name", "food_category", "sort_order"}, row
        assert row["food_category"] in {
            item["code"] for item in taxonomy["food_categories"]
        }, f"{row['code']} is filed under a category this response omits"


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
        #: §2.2 (v1.31): null means "every sector", a legal key value rather
        #: than a missing field. `in row` is asserted separately for the reason
        #: given on `upstream[].destination` above — `row.get("sector")` would
        #: pass on a row that omits it, and an omitted sector loads as the
        #: every-sector row and prices every supply-chain stage the same.
        assert "sector" in row
        assert row["sector"] is None or row["sector"] in sectors
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
        #: v1.58. Echoed, never resolved -- a response that answered a named
        #: food with its category would make the results page label a figure
        #: "Dairy" where the visitor typed "Cheese", which is the one thing
        #: this dimension exists to stop.
        assert got["food_item"] == sent.get("food_item"), where
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

    #: v1.58: a **triple**. `dairy/cheese` beside `dairy/butter` is a forked
    #: chain and legal; the same food twice is not. `.get` rather than `[...]`
    #: so that a fixture written before the dimension -- `export_pdf_request.
    #: json` is deliberately still one -- reads as "named no food" rather than
    #: raising, which is exactly what the API does with it.
    seen = {
        (e["sector"], e["food_category"], e.get("food_item"))
        for e in request_fixture["entries"]
    }
    assert len(seen) == len(request_fixture["entries"]), (
        "duplicate (sector, food_category, food_item)"
    )


def test_the_export_fixture_conserves_mass_and_names_no_calculate_only_field(
    export_request_fixture,
):
    """§6.2.3. `export_pdf_request.json` is a `POST /export/pdf` body, not a
    `POST /calculate` one — it must hold §6.2's mass-conservation rule exactly
    as `calculate_request.json` does (the same engine runs either way), and it
    must not carry `token` or `dry_run`, which `ExportPayload` does not
    declare and `extra="forbid"` refuses outright."""
    assert "token" not in export_request_fixture
    assert "dry_run" not in export_request_fixture
    assert export_request_fixture["locale"]

    for index, entry in enumerate(export_request_fixture["entries"]):
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
        assert "prevention" not in {
            line["destination"] for line in entry["current"]
        }, f"entries[{index}].current carries a prevention destination"


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
    "name", ["calculate_response.json", "calculate_response_single.json", "calculate_response_partial_coverage.json", "calculate_response_zero_totals.json"]
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
    "name", ["calculate_response.json", "calculate_response_single.json", "calculate_response_partial_coverage.json", "calculate_response_zero_totals.json"]
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

    def assert_totals_breakdown_is_a_partition(scenario_name, code, metric):
        #: v1.48, amending §3 rule 2: `qty_kg` and `value` roll up across
        #: entries at the totals level too, so this fixture's totals-level
        #: rows must still partition the metric total exactly, the same
        #: property already checked per entry above. The two rate fields
        #: stay at zero here: they are per-kilogram rates that can differ
        #: between the entries sharing a destination.
        summed = sum(
            (Decimal(row["value"]) for row in metric["by_destination"]),
            Decimal("0"),
        )
        assert summed == Decimal(metric["total"]), (
            f"{name} totals.{scenario_name}.{code}: by_destination sums to "
            f"{summed}, total says {metric['total']}"
        )
        for row in metric["by_destination"]:
            assert Decimal(row["upstream"]) == Decimal("0"), (
                f"{name} totals.{scenario_name}.{code}/{row['destination']}: "
                "upstream must be zero at the totals level"
            )
            assert Decimal(row["downstream"]) == Decimal("0"), (
                f"{name} totals.{scenario_name}.{code}/{row['destination']}: "
                "downstream must be zero at the totals level"
            )

    for code, metric in totals["current"]["metrics"].items():
        assert Decimal(metric["total"]) == rolled["current"][code], f"{name} totals {code}"
        assert_totals_breakdown_is_a_partition("current", code, metric)
    if totals["alternative"] is None:
        assert totals["net_benefit"] is None
        assert all(entry["alternative"] is None for entry in fixture["entries"])
        return
    for code, metric in totals["alternative"]["metrics"].items():
        assert Decimal(metric["total"]) == rolled["alternative"][code], f"{name} {code}"
        assert_totals_breakdown_is_a_partition("alternative", code, metric)
    for code, value in totals["net_benefit"].items():
        assert Decimal(value) == Decimal(
            totals["current"]["metrics"][code]["total"]
        ) - Decimal(totals["alternative"]["metrics"][code]["total"]), f"{name} {code}"


@pytest.mark.parametrize(
    "name", ["calculate_response.json", "calculate_response_single.json", "calculate_response_partial_coverage.json", "calculate_response_zero_totals.json"]
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
        #: (destination null), then zero.
        for candidate in (destination, None):
            for row in factors["upstream"]:
                if (row["sector"], row["food_category"], row["destination"],
                        row["metric"]) == (sector, food, candidate, metric):
                    return Decimal(row["value_per_kg"])
        return Decimal("0")

    def downstream(destination, sector, food, metric):
        #: §4.1 (v1.31): both `sector` and `food_category` are nullable, so the
        #: fallback is two-dimensional and the order between the two
        #: one-dimension rows is a decision rather than a derivation — the
        #: sector wins. Written out in full here rather than delegating to
        #: `FactorBundle.downstream`, because the whole value of this module is
        #: that it re-derives the contract independently of the code under
        #: test: sharing the lookup would make the two agree by construction.
        for candidate_sector, candidate_food in (
            (sector, food), (sector, None), (None, food), (None, None),
        ):
            for row in factors["downstream"]:
                if (row["destination"], row["sector"], row["food_category"],
                        row["metric"]) == (
                    destination, candidate_sector, candidate_food, metric
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
                    down = downstream(row["destination"], entry["sector"], food,
                                      code)
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
    "name", ["calculate_response.json", "calculate_response_single.json", "calculate_response_partial_coverage.json", "calculate_response_zero_totals.json"]
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


def test_the_fake_adapters_equivalence_has_every_field_the_engines_does():
    """This repository has already shipped the sibling of what this guards
    against: the engine's money block was once deleted while 105 API tests
    stayed green, because `tests.support.sqlite.FakeEngineAdapter` carried its
    own independent computation of the same figures and never noticed the
    real one was gone.

    The four fields v1.52 added to `EquivalenceResult` are additive, not a
    computation the fake reimplements, so a *deletion* of one is already
    caught loudly — `api/engine_adapter.py` reads `item.<field>` on every
    call, and `AttributeError` fails any test that reaches
    `serialize_result()`, which is most of `test_api.py` and
    `test_api_entries.py`. What that crash does **not** catch is a *value*
    drift: if the real formatting rule for, say, `value_per_unit_display`
    changed, the fake's hardcoded stand-in would keep the field and keep
    passing, silently rendering wire output the fake no longer matches, and
    every assertion resting on "the fake and the real dataclass agree" would
    go untested for exactly the fields nobody re-derives.

    So this test does not re-list the field names -- restating them here
    would be a third copy to keep in step with the other two, which is the
    defect wearing a different hat. It reads `EquivalenceResult`'s own field
    set via `dataclasses.fields` and compares it against the fake's stand-in
    equivalence object's actual attributes, so adding, removing or renaming a
    field on the dataclass makes *this* test fail until the fake is updated
    to match -- at the type level, rather than only when a change to
    `api/engine_adapter.py` happens to be exercised by a test that asserts a
    value.
    """
    import dataclasses
    from decimal import Decimal
    from types import SimpleNamespace

    from engine.types import EquivalenceResult
    from tests.support.sqlite import _scenario_result

    expected = {f.name for f in dataclasses.fields(EquivalenceResult)}

    # `with_breakdown=False` is enough to reach the equivalence stand-in --
    # `_scenario_result` only needs `qty_kg` off each line for that path.
    scenario = _scenario_result(
        [SimpleNamespace(qty_kg=Decimal("10.000"))], with_breakdown=False
    )
    (equivalence,) = scenario.equivalences
    actual = set(vars(equivalence))

    assert actual == expected, (
        "tests.support.sqlite's fake equivalence stand-in has "
        f"{actual - expected or '{}'} that engine.types.EquivalenceResult "
        f"does not, and is missing {expected - actual or '{}'} that it "
        "does -- api/engine_adapter.py serialises every field of the real "
        "dataclass, so the fake must carry the same set for the wider API "
        "test suite to mean what it appears to."
    )


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
    } == {
        code: (label, kg_per_unit)
        for code, label, kg_per_unit, _source_note in UNIT_PRESETS
    }
    #: §6.1 orders the presets smallest first (`get_taxonomy`), and the step-3
    #: container `<select>` renders them in the order the response gives —
    #: `web/js/calculator.js` sorts nothing, because `unit_preset` is the one
    #: taxonomy table with no `sort_order` to sort by. A fixture in some other
    #: order would teach C's form a sequence no deployment serves.
    masses = [Decimal(row["kg_per_unit"]) for row in taxonomy["unit_presets"]]
    assert masses == sorted(masses), (
        "tests/fixtures/taxonomy.json lists the unit presets out of order; "
        "get_taxonomy() orders them by kg_per_unit, and the container select "
        "renders the response order as given"
    )


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
        "calculate_request_partial_coverage.json",
        "calculate_response_partial_coverage.json",
        "calculate_request_zero_totals.json",
        "calculate_response_zero_totals.json",
        "export_pdf_request.json",
        "factors.json",
        "stats.json",
    ):
        for path, value in walk(load(name)):
            key = path.rsplit(".", 1)[-1].split("[")[0]
            if value is None or not isinstance(value, str):
                continue
            #: v1.58: `food_item` is **not** in this list, and that is the
            #: one deliberate hole in it. `admin/seed.py` seeds no
            #: `food_item` row at all -- mapping the client's ~20 foods onto
            #: our categories is a data-authoring task with client-facing
            #: consequences and seven of their rows have no New Zealand
            #: category -- so there is no pool to check against, and adding an
            #: empty one would forbid every food rather than validate it. The
            #: fixtures name no food for exactly that reason (every
            #: `food_item` in them is `null`, and the `is None` guard above
            #: skips those), so nothing is currently unchecked. **Add
            #: `food_item` to this list the moment the seed grows one.**
            if key in {"sector", "destination", "food_category"}:
                pool = {
                    "sector": sectors,
                    "destination": destinations,
                    "food_category": foods,
                }[key]
                assert value in pool, f"{name} {path}: {value!r} is not a seeded code"


@pytest.mark.parametrize(
    "name", ["calculate_response.json", "calculate_response_single.json", "calculate_response_partial_coverage.json", "calculate_response_zero_totals.json"]
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

    items = {row["code"]: row["food_category"] for row in taxonomy["food_items"]}
    for entry in fixture["entries"]:
        assert entry["sector"] in sectors
        assert entry["food_category"] is None or entry["food_category"] in foods
        #: v1.58. `in entry` asserted separately from the value, for the reason
        #: `upstream[].destination` is: `entry.get("food_item")` would pass on
        #: a response that omits the key entirely, and an omitted key is what
        #: makes "named no food" indistinguishable from "this body predates the
        #: dimension".
        assert "food_item" in entry, "the response omits entries[].food_item"
        if entry["food_item"] is not None:
            assert entry["food_item"] in items, (
                f"{name}: {entry['food_item']!r} is not in the taxonomy"
            )
            assert items[entry["food_item"]] == entry["food_category"], (
                f"{name}: {entry['food_item']!r} is filed under "
                f"{items[entry['food_item']]!r}, not {entry['food_category']!r}"
            )
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


# ------------------------------------------ the partial-coverage pair (v1.50)
#
# `calculate_request_partial_coverage.json` / `calculate_response_partial_
# coverage.json` exist because neither existing response fixture reaches the
# two §4.6 states that matter most for the money block's own rewrite:
# `calculate_response.json`'s `totals.money` is `complete` in all four fields
# and its share is `incomplete`; `calculate_response_single.json` is
# `not_supplied` throughout. Nobody's money field was ever `incomplete` and
# nobody's share was ever `complete` — the exact state §4.5's rewrite exists
# for (a submission that priced one entry and not the other) went
# unexercised by every fixture-driven test in the tree, which is how a PDF
# and two on-screen surfaces could disagree about `saving_nzd` and nothing
# here noticed.
#
# This pair is not hand-typed: it is `engine.calculate.calculate`'s own
# output for a two-entry request in which entry 2 supplies a production
# total but no money figures — the same discipline `calculate_response.json`
# and `calculate_response_single.json` were built with, recorded in
# `docs/interfaces.md` §10's fixture table.


def test_the_partial_coverage_pair_describes_the_same_calculation():
    """The same correspondence `test_the_request_and_the_response_describe_
    the_same_calculation` checks for the canonical pair, checked here for
    this one too — a saved fixture is exactly the kind of file that can
    silently drift from the request that was supposed to produce it."""
    request_fixture = load("calculate_request_partial_coverage.json")
    response_fixture = load("calculate_response_partial_coverage.json")
    assert response_fixture["gwp_horizon"] == request_fixture["gwp_horizon"]
    assert len(response_fixture["entries"]) == len(request_fixture["entries"])
    for index, (sent, got) in enumerate(
        zip(request_fixture["entries"], response_fixture["entries"])
    ):
        where = f"entries[{index}]"
        assert got["sector"] == sent["sector"], where
        assert got["food_category"] == sent["food_category"], where
        for scenario in ("current", "alternative"):
            expected = [
                (line["destination"], Decimal(line["qty_kg"])) for line in sent[scenario]
            ]
            for metric_code, metric in got[scenario]["metrics"].items():
                assert lines_of({"metrics": {metric_code: metric}}) == expected, (
                    f"{where}.{scenario}.metrics.{metric_code}.by_destination "
                    "does not match the lines the request carried"
                )


def test_the_partial_coverage_pair_is_what_its_name_promises():
    """Not a shape check — the two specific facts this fixture exists to
    supply. A regeneration that (say) also priced entry 2 would still be a
    valid response and would silently stop testing what this file is for."""
    response = load("calculate_response_partial_coverage.json")
    totals = response["totals"]
    assert totals["data_state"]["production_share_percent"] == "complete"
    assert totals["production_share_percent"] is not None
    for field in (
        "total_value_nzd", "wasted_value_nzd", "wasted_share_percent", "saving_nzd",
    ):
        assert totals["data_state"][field] == "incomplete", field
        assert totals["money"][field] is None, field


def test_the_zero_totals_pair_is_what_its_name_promises():
    """v1.51's fixture for the fourth `data_state` value. Both entries answer
    `total_input_kg`, `total_value_nzd` and `wasted_value_nzd` as zero — every
    entry answered, and both ratios built from what they answered are still
    undefined. Not the same shape `calculate_response_single.json` (nobody
    answered) or `_partial_coverage.json` (some did, some did not) give:
    `complete` figures whose value is still `None`, which before v1.51 was
    indistinguishable on the wire from `not_supplied`."""
    response = load("calculate_response_zero_totals.json")
    totals = response["totals"]
    assert totals["production_share_percent"] is None
    assert totals["data_state"]["production_share_percent"] == "undefined"
    assert totals["money"]["wasted_share_percent"] is None
    assert totals["data_state"]["wasted_share_percent"] == "undefined"
    # The two sums either side of that ratio are real, present zeros — not
    # withheld, and not the thing that is undefined here.
    assert totals["money"]["total_value_nzd"] == "0.00"
    assert totals["data_state"]["total_value_nzd"] == "complete"
    assert totals["money"]["wasted_value_nzd"] == "0.00"
    assert totals["data_state"]["wasted_value_nzd"] == "complete"


_DATA_STATE_FIELDS = (
    "production_share_percent",
    "total_value_nzd",
    "wasted_value_nzd",
    "wasted_share_percent",
    "saving_nzd",
)

#: v1.51's fourth state, `undefined`, is reachable only by the two ratio
#: fields — a division over a sum every entry supplied. `total_value_nzd` and
#: `wasted_value_nzd` are themselves sums, and a sum of zero is a real zero,
#: never undefined; `saving_nzd`'s own zero-denominator case is a different,
#: rarer arithmetic gap `_share_state` does not cover (see
#: `engine/calculate.py::_money`). Asserting the same four-value set against
#: every field would therefore fail honestly for the three that cannot reach
#: the fourth state, which is why this is per field rather than one constant.
_UNDEFINED_CAPABLE_FIELDS = {"production_share_percent", "wasted_share_percent"}


def test_every_data_state_value_is_exercised_somewhere_in_the_fixtures():
    """§4.6's rule is four states (three until v1.51), and a suite that only
    ever fixtures some of them is not testing the rule — it is testing the
    ones that happen to be convenient. This asserts the property directly,
    across whichever `calculate_response*.json` files exist, so it fails
    again on its own if a future edit narrows the coverage back down rather
    than only when someone remembers to check by hand."""
    seen = {field: set() for field in _DATA_STATE_FIELDS}
    for name in (
        "calculate_response.json",
        "calculate_response_single.json",
        "calculate_response_partial_coverage.json",
        "calculate_response_zero_totals.json",
    ):
        state = load(name)["totals"]["data_state"]
        for field in _DATA_STATE_FIELDS:
            seen[field].add(state[field])
    for field in _DATA_STATE_FIELDS:
        expected = {"complete", "incomplete", "not_supplied"} | (
            {"undefined"} if field in _UNDEFINED_CAPABLE_FIELDS else set()
        )
        assert seen[field] == expected, (
            f"{field}: only {sorted(seen[field])} appear across the fixtures, "
            f"expected {sorted(expected)}"
        )


#: Every request-shaped fixture, and what §6.2's period rules say about it.
_REQUEST_FIXTURES = (
    "calculate_request.json",
    "calculate_request_partial_coverage.json",
    "calculate_request_zero_totals.json",
    "export_pdf_request.json",
)


def test_the_request_fixtures_exercise_the_period_present_and_absent():
    """§6.2, v1.67. The fixtures are the executable contract, so the shapes
    they carry are the shapes a front end builds against.

    All three legal states must appear across the four request fixtures, or
    a front end (and a backend contract test) develops against whichever
    happened to be convenient:

    * **a preset with an interval** -- v1.67's designed normal case, since
      the presets became templates that fill the picker;
    * **`custom` with an interval** -- the visitor choosing dates themselves,
      and the only fixture appearance of the vocabulary's fifth member;
    * **a preset with no interval** -- the shape of every row written before
      v1.67, and the shape the deployed front end still sends until WP3
      lands. A suite in which it disappeared would stop noticing if a later
      revision made the old shape a 400.

    The *illegal* states are not fixtured, deliberately: a fixture is an
    example of a valid document, and `tests/api/test_api.py` and
    `tests/test_schemas.py` hold the refusals instead. What is asserted here
    is that none of the four has drifted into one of them.
    """
    seen = set()
    for name in _REQUEST_FIXTURES:
        body = load(name)
        time_frame = body.get("time_frame")
        start, end = body.get("period_start"), body.get("period_end")

        assert (start is None) == (end is None), (
            f"{name} carries half an interval, which §6.2 refuses outright"
        )
        if start is not None:
            assert time_frame is not None, (
                f"{name} carries an interval and no time_frame, which §6.2 "
                "refuses: 'Not stated' cannot be carrying dates"
            )
            assert start <= end, f"{name}'s period runs backwards"
        else:
            assert time_frame != "custom", (
                f"{name} says time_frame 'custom' and names no dates, which "
                "§6.2 refuses: 'custom' means the visitor chose these dates"
            )
        seen.add(
            (time_frame == "custom", start is not None) if time_frame else None
        )

    assert {(False, True), (True, True), (False, False)} <= seen, (
        "the request fixtures no longer cover all three legal period shapes "
        "(preset+interval, custom+interval, preset alone); they cover "
        f"{sorted(x for x in seen if x is not None)}"
    )


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
