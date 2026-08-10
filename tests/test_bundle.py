import copy
import json
from decimal import Decimal
from pathlib import Path

from engine.bundle import FactorBundle
import pytest
from engine.errors import BundleFormatError, UnknownConstantError
from engine.types import MetricSpec, EquivalenceSpec

constants = {
    "GWP_CH4_100": Decimal("28")
}
upstream_factors = {
    # v1.8 §4.1: the key gained a destination dimension (open item O-7).
    # `None` is the generic row; the `prevention` row at zero is what makes
    # a prevented line a real 100% offset.
    ("processing", "dairy", None, "co2e") : Decimal("1.9"),
    ("processing", "dairy", "prevention", "co2e") : Decimal("0")
}
downstream_factors = {
    ("landfill", "dairy", "co2e") : Decimal("0.99"),
    ("compost", None, "co2e") : Decimal("0.25")
}
formulas = {
    "co2e" : "qty_kg * (upstream + downstream)",
    "water" : "qty_kg * upstream"
}
sectors = {
    "processing"
}
categories = {
    "dairy"
}
destinations = {"landfill", "compost", "animal_feed"}
standard_mix = "standard_mix"
version_label = "MOCK-v0"
is_mock = True
metric = MetricSpec("co2e", "kg CO2e", 1)
equivalence_specs = (
    EquivalenceSpec(
        "km_driven",
        "co2e",
        Decimal("0.5"),
        "equals to drive {value} km"
    ),
)
bundle = FactorBundle(upstream_factors, downstream_factors, constants, formulas, destinations, sectors, categories, standard_mix, version_label, is_mock, (metric,), equivalence_specs)

def test_upstream():
    assert bundle.upstream("processing", "dairy", "landfill", "co2e") == Decimal("1.9")
    assert bundle.upstream("processing", "bakery", "landfill", "co2e") == Decimal("0")


def test_upstream_falls_back_to_the_generic_row_then_to_zero():
    """§4.1's three steps, in order: exact destination, generic row, zero."""
    # Exact wins over the generic row -- this is O-7's whole point.
    assert bundle.upstream("processing", "dairy", "prevention", "co2e") == Decimal("0")
    # A destination with no row of its own falls through to the generic row.
    assert bundle.upstream("processing", "dairy", "compost", "co2e") == Decimal("1.9")
    # `None` asks for the generic row directly.
    assert bundle.upstream("processing", "dairy", None, "co2e") == Decimal("1.9")
    # Neither present.
    assert bundle.upstream("processing", "dairy", "compost", "water") == Decimal("0")

def test_downstream():
    assert bundle.downstream("landfill", "dairy", "co2e") == Decimal("0.99")
    assert bundle.downstream("landfill", None, "co2e") == Decimal("0")
    assert bundle.downstream("compost", "dairy", "co2e") == Decimal("0.25")

def test_constant_returns_existing_constant():
    assert bundle.constant("GWP_CH4_100") == Decimal("28")
    with pytest.raises(UnknownConstantError): bundle.constant("NOT_EXIST")

def test_formula():
    assert bundle.formula("water") == "qty_kg * upstream"
    assert bundle.formula("unknown_metric") == "qty_kg * (upstream + downstream)"

def test_has_destination():
    assert bundle.has_destination("landfill") ==  True
    assert bundle.has_destination("moon") == False

def test_has_sector():
    assert bundle.has_sector("processing") == True
    assert bundle.has_sector("moon") == False

def test_has_food_category():
    assert bundle.has_food_category("dairy") == True
    assert bundle.has_food_category("unknown_food") == False

def test_standard_mix():
    assert bundle.standard_mix_code() == "standard_mix"

def test_version_label():
    assert bundle.version_label == "MOCK-v0"

def test_is_mock():
    assert bundle.is_mock

def test_metric():
    assert bundle.metrics[0].code == "co2e"

def test_equivalences():
    result = bundle.equivalences()

    assert len(result) == 1
    assert result[0].code == "km_driven"
    assert result[0].source_metric_code == "co2e"
    assert result[0].value_per_unit == Decimal("0.5")
    assert result[0].label_template == "equals to drive {value} km"


# --------------------------------------------------------------------------
# from_json() and validate() -- contract SS4.1, SS10.2
# --------------------------------------------------------------------------

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def canonical_bundle_json():
    """SS10.2's `bundle.json`, assembled from the two canonical fixtures.

    `tests/fixtures/factors.json` is a **SS6.3 `GET /factors` export**, not a
    bundle: it wraps `version_label` and `is_mock` in a `factor_set` object
    and carries no taxonomy at all. SS10.2's shape is the taxonomy *plus* the
    factors with those two hoisted to the top level -- which is exactly what
    `db/repository.build_bundle_data` emits, and `get_factor_export` is that
    dictionary with the taxonomy sections dropped.

    Composing them here rather than adding a thirteenth fixture keeps one
    canonical set (SS10): these are the same numbers
    `tests/api/test_fixture_consistency.py` re-derives from the published
    formulas, so a golden case built on this in Task 7 is built on numbers
    that already have a test behind them.
    """
    taxonomy = _fixture("taxonomy.json")
    factors = _fixture("factors.json")
    return {
        "version_label": factors["factor_set"]["version_label"],
        "is_mock": factors["factor_set"]["is_mock"],
        "sectors": taxonomy["sectors"],
        "food_categories": taxonomy["food_categories"],
        "destination_groups": taxonomy["destination_groups"],
        "destinations": taxonomy["destinations"],
        "metrics": taxonomy["metrics"],
        "constants": factors["constants"],
        "formulas": factors["formulas"],
        "upstream": factors["upstream"],
        "downstream": factors["downstream"],
        "equivalences": factors["equivalences"],
    }


def test_the_canonical_fixtures_load_and_validate_clean():
    data = canonical_bundle_json()
    loaded = FactorBundle.from_json(data)

    assert loaded.validate() == []
    assert loaded.version_label == data["version_label"]
    assert loaded.is_mock is True
    assert loaded.standard_mix_code() == "standard_mix"
    assert loaded.has_sector("processing")
    assert loaded.has_food_category("dairy")
    assert loaded.has_destination("prevention")
    assert not loaded.has_destination("moon")


def test_every_canonical_number_round_trips_at_full_scale():
    """SS1.2: decimals travel as strings and are read with `Decimal()`. The
    trailing zeros matter -- `Decimal("0.4500000000")` and `Decimal("0.45")`
    compare equal, so the scale is asserted through `str()`."""
    loaded = FactorBundle.from_json(canonical_bundle_json())

    assert str(loaded.upstream("primary_production", "vegetables", "landfill", "co2e")) == (
        "0.4500000000"
    )
    assert str(loaded.constant("GWP_CH4_20")) == "84.0000000000"
    assert str(loaded.equivalences()[0].value_per_unit) == "4.1800000000"
    # A negative downstream factor is an offset (SS2.2) and must survive.
    assert loaded.downstream("animal_feed", "dairy", "co2e") == Decimal("-0.1500000000")


def test_the_canonical_generic_and_specific_downstream_rows_both_survive():
    """`food_category: null` is a key, not an absent field (SS10.2)."""
    loaded = FactorBundle.from_json(canonical_bundle_json())

    assert loaded.downstream("landfill", "dairy", "co2e") == Decimal("0.9900000000")
    assert loaded.downstream("landfill", "vegetables", "co2e") == Decimal("0.7000000000")
    # The waste levy: a per-tonne charge carried on the generic row alone.
    assert loaded.downstream("landfill", "dairy", "cost") == Decimal("0.0600000000")
    assert loaded.downstream("compost", "dairy", "mass") == Decimal("0")


def test_the_canonical_prevention_upstream_rows_survive_as_a_whole_offset():
    """O-7. The destination dimension is the newest part of the key and the
    one a loader is most likely to drop; dropping it is silent, because the
    generic row is still there to fall back to."""
    loaded = FactorBundle.from_json(canonical_bundle_json())

    for sector, food in (
        ("processing", "dairy"),
        ("primary_production", "vegetables"),
        ("consumer_hospitality", "standard_mix"),
    ):
        for metric in ("co2e", "water"):
            assert loaded.upstream(sector, food, "prevention", metric) == Decimal("0")
            assert loaded.upstream(sector, food, "landfill", metric) > Decimal("0")


def test_source_note_and_data_quality_are_accepted_and_ignored():
    """SS10.2's accept-and-ignore rule. Every canonical factor row carries
    both, and `data_quality` is `null` on three of them."""
    data = canonical_bundle_json()
    assert any(row["data_quality"] is None for row in data["downstream"])
    assert all("source_note" in row for row in data["upstream"])

    loaded = FactorBundle.from_json(data)

    assert loaded.validate() == []
    assert not any(
        "source_note" in problem or "data_quality" in problem
        for problem in loaded.validate()
    )


def test_metrics_and_equivalences_arrive_sorted_by_sort_order():
    """SS4.1 promises both tuples are sorted; the engine iterates them as
    given and never re-sorts, so the promise is made here."""
    data = canonical_bundle_json()
    data["metrics"] = list(reversed(data["metrics"]))

    loaded = FactorBundle.from_json(data)

    assert [metric.code for metric in loaded.metrics] == [
        "co2e",
        "ch4",
        "water",
        "cost",
        "mass",
    ]
    assert loaded.metrics[0].unit == "kg CO2e"
    assert loaded.metrics[0].display_precision == 1


def test_from_json_accepts_the_json_text_and_never_opens_a_file():
    """The engine is pure (SS4). A caller that has a file opens it."""
    text = json.dumps(canonical_bundle_json())

    assert FactorBundle.from_json(text).validate() == []


# ---------- malformed input raises BundleFormatError, never KeyError ----------


def _minimal():
    """The smallest bundle that loads and validates clean."""
    return {
        "version_label": "TEST-v0",
        "is_mock": True,
        "sectors": [{"code": "processing", "name": "Processing", "sort_order": 1}],
        "food_categories": [
            {"code": "standard_mix", "name": "Mixed", "is_standard_mix": True,
             "sort_order": 0},
            {"code": "dairy", "name": "Dairy", "is_standard_mix": False, "sort_order": 1},
        ],
        "destination_groups": [
            {"code": "disposal", "name": "Disposal", "is_waste": True, "sort_order": 1}
        ],
        "destinations": [
            {"code": "landfill", "name": "Landfill", "group": "disposal", "sort_order": 1},
            {"code": "prevention", "name": "Prevented", "group": "disposal",
             "sort_order": 0},
        ],
        "metrics": [
            {"code": "co2e", "name": "Greenhouse gas", "unit": "kg CO2e",
             "display_unit": "kg CO2e", "display_precision": 1, "sort_order": 1}
        ],
        "constants": [{"code": "GWP_CH4_100", "value": "28.0000000000", "unit": "",
                       "note": ""}],
        "formulas": [{"metric": "co2e", "expression": "qty_kg * (upstream + downstream)",
                      "notes": ""}],
        "upstream": [
            {"sector": "processing", "food_category": "dairy", "destination": None,
             "metric": "co2e", "value_per_kg": "1.9000000000"},
            {"sector": "processing", "food_category": "dairy",
             "destination": "prevention", "metric": "co2e",
             "value_per_kg": "0.0000000000"},
        ],
        "downstream": [
            {"destination": "landfill", "food_category": None, "metric": "co2e",
             "value_per_kg": "0.7000000000"}
        ],
        "equivalences": [
            {"code": "km_driven", "name": "Kilometres driven", "source_metric": "co2e",
             "value_per_unit": "4.1800000000",
             "label_template": "Equivalent to driving {value} km", "sort_order": 1}
        ],
    }


def test_the_minimal_bundle_is_itself_clean():
    assert FactorBundle.from_json(_minimal()).validate() == []


@pytest.mark.parametrize("key", [
    "version_label", "is_mock", "sectors", "food_categories", "destination_groups",
    "destinations", "metrics", "constants", "formulas", "upstream", "downstream",
    "equivalences",
])
def test_every_missing_top_level_key_is_a_bundle_format_error(key):
    data = _minimal()
    del data[key]

    with pytest.raises(BundleFormatError) as raised:
        FactorBundle.from_json(data)

    assert key in str(raised.value)


def test_a_get_factors_response_is_refused_with_a_message_naming_what_is_missing():
    """SS10.2 calls a `GET /factors` body the natural thing to paste into the
    dry-run box, and it is not a bundle: it has the factors and none of the
    taxonomy. The refusal has to say so."""
    with pytest.raises(BundleFormatError) as raised:
        FactorBundle.from_json(_fixture("factors.json"))

    message = str(raised.value)
    for missing in ("sectors", "food_categories", "destinations", "metrics"):
        assert missing in message


def test_a_missing_upstream_destination_key_is_malformed_not_null():
    """SS10.2, in as many words. `null` means "every destination"; a row that
    lost the key would compute a plausible, wrong answer."""
    data = _minimal()
    del data["upstream"][0]["destination"]

    with pytest.raises(BundleFormatError) as raised:
        FactorBundle.from_json(data)

    assert "destination" in str(raised.value)


def test_a_missing_downstream_food_category_key_is_malformed_not_null():
    data = _minimal()
    del data["downstream"][0]["food_category"]

    with pytest.raises(BundleFormatError):
        FactorBundle.from_json(data)


def test_a_json_number_where_a_decimal_string_belongs_is_refused():
    """SS1.2: `float` is never an intermediate. `json.loads` has already made
    this a float by the time the loader sees it."""
    data = _minimal()
    data["upstream"][0]["value_per_kg"] = 1.9

    with pytest.raises(BundleFormatError) as raised:
        FactorBundle.from_json(data)

    assert "value_per_kg" in str(raised.value)


@pytest.mark.parametrize("mutate", [
    lambda d: d.__setitem__("upstream", {"sector": "processing"}),
    lambda d: d["upstream"].append("not a row"),
    lambda d: d["destinations"][0].pop("group"),
    lambda d: d["metrics"][0].pop("display_precision"),
    lambda d: d["constants"][0].__setitem__("value", "not a number"),
    lambda d: d["formulas"][0].pop("expression"),
    lambda d: d["equivalences"][0].pop("value_per_unit"),
    lambda d: d["food_categories"][0].__setitem__("is_standard_mix", "yes"),
    lambda d: d.__setitem__("is_mock", "true"),
    lambda d: d.__setitem__("version_label", 7),
    lambda d: d["sectors"][0].__setitem__("code", ""),
])
def test_malformed_rows_raise_bundle_format_error_rather_than_key_error(mutate):
    """The dry-run view (SS6.2.1) shows this to a staff member. A bare
    `KeyError` there is a 500 with nothing they can act on."""
    data = _minimal()
    mutate(data)

    with pytest.raises(BundleFormatError):
        FactorBundle.from_json(data)


@pytest.mark.parametrize("payload", ["", "{", "[]", "null", '"a string"', "12"])
def test_input_that_is_not_a_json_object_raises_bundle_format_error(payload):
    with pytest.raises(BundleFormatError):
        FactorBundle.from_json(payload)


# ---------- validate() ----------


def test_validate_reports_an_upstream_row_whose_destination_does_not_resolve():
    """The v1.8 dimension, and the check most easily forgotten: nothing else
    catches it. `upstream()` falls back to the generic row, so the calculator
    returns a number -- the *pre-O-7* number, charging a prevented line its
    full upstream burden."""
    data = _minimal()
    data["upstream"][1]["destination"] = "preventoin"

    problems = FactorBundle.from_json(data).validate()

    assert len(problems) == 1
    assert "preventoin" in problems[0]
    assert "destination" in problems[0]


@pytest.mark.parametrize("field_name,value", [
    ("sector", "manufacturing"),
    ("food_category", "diary"),
    ("metric", "co2"),
])
def test_validate_reports_an_upstream_row_naming_anything_absent(field_name, value):
    data = _minimal()
    data["upstream"][0][field_name] = value

    problems = FactorBundle.from_json(data).validate()

    assert any(value in problem for problem in problems)


@pytest.mark.parametrize("field_name,value", [
    ("destination", "incinerator"),
    ("food_category", "diary"),
    ("metric", "co2"),
])
def test_validate_reports_a_downstream_row_naming_anything_absent(field_name, value):
    data = _minimal()
    data["downstream"][0][field_name] = value

    problems = FactorBundle.from_json(data).validate()

    assert any(value in problem for problem in problems)


def test_validate_accepts_the_two_legal_nulls():
    """`upstream[].destination` and `downstream[].food_category` are `null` on
    the rows that carry the general case, and neither is a problem."""
    data = _minimal()
    assert data["upstream"][0]["destination"] is None
    assert data["downstream"][0]["food_category"] is None

    assert FactorBundle.from_json(data).validate() == []


def test_validate_reports_a_destination_whose_group_does_not_exist():
    data = _minimal()
    data["destinations"][0]["group"] = "recycle_recovery"

    problems = FactorBundle.from_json(data).validate()

    assert len(problems) == 1
    assert "recycle_recovery" in problems[0]


def test_validate_reports_no_standard_mix_and_two_standard_mixes():
    none_flagged = _minimal()
    for row in none_flagged["food_categories"]:
        row["is_standard_mix"] = False
    two_flagged = _minimal()
    for row in two_flagged["food_categories"]:
        row["is_standard_mix"] = True

    assert len(FactorBundle.from_json(none_flagged).validate()) == 1
    problems = FactorBundle.from_json(two_flagged).validate()
    assert len(problems) == 1
    assert "dairy" in problems[0]


def test_validate_reports_a_formula_and_an_equivalence_naming_an_absent_metric():
    data = _minimal()
    data["formulas"][0]["metric"] = "ch4"
    data["equivalences"][0]["source_metric"] = "ch4"

    problems = FactorBundle.from_json(data).validate()

    assert len(problems) == 2
    assert any("formula" in problem for problem in problems)
    assert any("km_driven" in problem for problem in problems)


def test_validate_reports_a_duplicate_factor_row():
    """Not in SS4.1's list, and the same silent-wrong-number failure as a
    dangling destination: the later row wins and nothing raises. Unreachable
    through the repository, which has a unique index for it; reachable in
    every hand-assembled bundle."""
    data = _minimal()
    duplicate = copy.deepcopy(data["upstream"][0])
    duplicate["value_per_kg"] = "9.9900000000"
    data["upstream"].append(duplicate)

    loaded = FactorBundle.from_json(data)
    problems = loaded.validate()

    assert len(problems) == 1
    assert "more than once" in problems[0]
    assert loaded.upstream("processing", "dairy", None, "co2e") == Decimal("9.99")


def test_validate_never_raises_on_a_bundle_that_is_wrong_in_every_way():
    """SS4.1: it returns problems, and the API decides how to present them."""
    data = _minimal()
    data["upstream"][0].update(sector="x", food_category="y", destination="z", metric="m")
    data["downstream"][0].update(destination="p", food_category="q", metric="r")
    data["destinations"][0]["group"] = "s"
    data["food_categories"][0]["is_standard_mix"] = False
    data["formulas"][0]["metric"] = "t"
    data["equivalences"][0]["source_metric"] = "u"

    problems = FactorBundle.from_json(data).validate()

    assert len(problems) == 11
    assert all(isinstance(problem, str) for problem in problems)


def test_validate_invents_no_problems_on_a_hand_built_bundle():
    """The four fields `from_json` adds are defaulted, so the twelve-field
    constructor still works and `validate()` reports only real problems --
    it does not turn absent taxonomy metadata into invented ones.

    The module-level `bundle` above genuinely carries two: a `prevention`
    upstream row against a destination set that has no `prevention`, and a
    `water` formula against a metric tuple that has only `co2e`. Both are
    exactly what this method exists to find. What must *not* appear is a
    complaint about `is_standard_mix` (the code is carried in `standard_mix`
    rather than in the flag list) or about a destination group (a hand-built
    bundle names no groups at all)."""
    problems = bundle.validate()

    assert len(problems) == 2
    assert any("prevention" in problem for problem in problems)
    assert any("water" in problem for problem in problems)
    assert not any("is_standard_mix" in problem for problem in problems)
    assert not any("group" in problem for problem in problems)