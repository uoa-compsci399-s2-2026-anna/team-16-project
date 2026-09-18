import copy
import json
from decimal import Decimal
from pathlib import Path

from engine.bundle import (
    FOOD_ITEMS_KEY,
    REQUIRED_KEYS,
    FactorBundle,
    UpstreamBasis,
)
import pytest
from engine.errors import BundleFormatError, UnknownCodeError, UnknownConstantError
from engine.types import MetricSpec, EquivalenceSpec

constants = {
    "GWP_CH4_100": Decimal("28")
}
upstream_factors = {
    # v1.8 §4.1: the key gained a destination dimension (open item O-7).
    # `None` is the generic row; the `prevention` row at zero is what makes
    # a prevented line a real 100% offset.
    #
    # v1.54 §4.1: and a food_item dimension *before* the destination, keyed as
    # `(sector, food_category, food_item | None, destination | None, metric)`.
    # Both rows here are category rows -- `None` in the item slot -- which is
    # what every bundle in the tree carries and why the dimension is inert.
    # See `test_the_upstream_lookup_matrix_*` below for the order between the
    # two nullable slots.
    ("processing", "dairy", None, None, "co2e") : Decimal("1.9"),
    ("processing", "dairy", None, "prevention", "co2e") : Decimal("0")
}
downstream_factors = {
    # v1.31 §4.1: the key gained a sector dimension between the destination
    # and the food category. `None` in either slot means "every value of that
    # dimension"; see `test_the_downstream_lookup_matrix_*` below for the
    # order between them.
    ("landfill", None, "dairy", "co2e") : Decimal("0.99"),
    ("compost", None, None, "co2e") : Decimal("0.25")
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
    assert bundle.upstream("processing", "dairy", None, "landfill", "co2e") == Decimal("1.9")
    assert bundle.upstream("processing", "bakery", None, "landfill", "co2e") == Decimal("0")


def test_upstream_falls_back_to_the_generic_row_then_to_zero():
    """§4.1's three steps, in order: exact destination, generic row, zero."""
    # Exact wins over the generic row -- this is O-7's whole point.
    assert bundle.upstream("processing", "dairy", None, "prevention", "co2e") == Decimal("0")
    # A destination with no row of its own falls through to the generic row.
    assert bundle.upstream("processing", "dairy", None, "compost", "co2e") == Decimal("1.9")
    # `None` asks for the generic row directly.
    assert bundle.upstream("processing", "dairy", None, None, "co2e") == Decimal("1.9")
    # Neither present.
    assert bundle.upstream("processing", "dairy", None, "compost", "water") == Decimal("0")

def test_downstream():
    assert bundle.downstream("landfill", "processing", "dairy", "co2e") == Decimal("0.99")
    assert bundle.downstream("landfill", "processing", None, "co2e") == Decimal("0")
    assert bundle.downstream("compost", "processing", "dairy", "co2e") == Decimal("0.25")

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
# from_json() and validate() -- contract §4.1, §10.2
# --------------------------------------------------------------------------

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def canonical_bundle_json():
    """§10.2's `bundle.json`, assembled from the two canonical fixtures.

    `tests/fixtures/factors.json` is a **§6.3 `GET /factors` export**, not a
    bundle: it wraps `version_label` and `is_mock` in a `factor_set` object
    and carries no taxonomy at all. §10.2's shape is the taxonomy *plus* the
    factors with those two hoisted to the top level -- which is exactly what
    `db/repository.build_bundle_data` emits, and `get_factor_export` is that
    dictionary with the taxonomy sections dropped.

    Composing them here rather than adding a thirteenth fixture keeps one
    canonical set (§10): these are the same numbers
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
    """§1.2: decimals travel as strings and are read with `Decimal()`. The
    trailing zeros matter -- `Decimal("0.4500000000")` and `Decimal("0.45")`
    compare equal, so the scale is asserted through `str()`."""
    loaded = FactorBundle.from_json(canonical_bundle_json())

    assert str(loaded.upstream("primary_production", "vegetables", None, "landfill", "co2e")) == (
        "0.4500000000"
    )
    assert str(loaded.constant("GWP_CH4_20")) == "84.0000000000"
    assert str(loaded.equivalences()[0].value_per_unit) == "4.1800000000"
    # A negative downstream factor is an offset (§2.2) and must survive.
    assert loaded.downstream(
        "animal_feed", "processing", "dairy", "co2e"
    ) == Decimal("-0.1500000000")


def test_the_canonical_generic_and_specific_downstream_rows_both_survive():
    """`food_category: null` is a key, not an absent field (§10.2)."""
    loaded = FactorBundle.from_json(canonical_bundle_json())

    assert loaded.downstream(
        "landfill", "processing", "dairy", "co2e"
    ) == Decimal("0.9900000000")
    assert loaded.downstream(
        "landfill", "processing", "vegetables", "co2e"
    ) == Decimal("0.7000000000")
    # The waste levy: a per-tonne charge carried on the row that names neither
    # a sector nor a category.
    assert loaded.downstream(
        "landfill", "processing", "dairy", "cost"
    ) == Decimal("0.0600000000")
    assert loaded.downstream("compost", "processing", "dairy", "mass") == Decimal("0")
    # The New Zealand set does not vary by sector, so the sector must make no
    # difference to any of the above. Asserted rather than assumed: this is the
    # whole of the claim that v1.31 left the live set alone.
    for sector in ("processing", "primary_production", "consumer_hospitality"):
        assert loaded.downstream(
            "landfill", sector, "dairy", "co2e"
        ) == Decimal("0.9900000000")
        assert loaded.downstream(
            "landfill", sector, "dairy", "cost"
        ) == Decimal("0.0600000000")


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
            assert loaded.upstream(sector, food, None, "prevention", metric) == Decimal("0")
            assert loaded.upstream(sector, food, None, "landfill", metric) > Decimal("0")


def test_source_note_and_data_quality_are_accepted_and_ignored():
    """§10.2's accept-and-ignore rule. Every canonical factor row carries
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
    """§4.1 promises both tuples are sorted; the engine iterates them as
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
    """The engine is pure (§4). A caller that has a file opens it."""
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
            {"destination": "landfill", "sector": None, "food_category": None,
             "metric": "co2e", "value_per_kg": "0.7000000000"}
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
    """§10.2 calls a `GET /factors` body the natural thing to paste into the
    dry-run box, and it is not a bundle: it has the factors and none of the
    taxonomy. The refusal has to say so."""
    with pytest.raises(BundleFormatError) as raised:
        FactorBundle.from_json(_fixture("factors.json"))

    message = str(raised.value)
    for missing in ("sectors", "food_categories", "destinations", "metrics"):
        assert missing in message


def test_a_missing_upstream_destination_key_is_malformed_not_null():
    """§10.2, in as many words. `null` means "every destination"; a row that
    lost the key would compute a plausible, wrong answer."""
    data = _minimal()
    del data["upstream"][0]["destination"]

    with pytest.raises(BundleFormatError) as raised:
        FactorBundle.from_json(data)

    assert "destination" in str(raised.value)


@pytest.mark.parametrize("key", ["food_category", "sector"])
def test_a_missing_downstream_nullable_key_is_malformed_not_null(key):
    """Both nullable dimensions, on §10.2's terms: `null` is a value, an absent
    key is a malformed row. A downstream row that had lost its `sector` would
    load as the every-sector row and price every stage the same, which is a
    plausible answer rather than an error."""
    data = _minimal()
    del data["downstream"][0][key]

    with pytest.raises(BundleFormatError) as raised:
        FactorBundle.from_json(data)

    assert key in str(raised.value)


def test_a_json_number_where_a_decimal_string_belongs_is_refused():
    """§1.2: `float` is never an intermediate. `json.loads` has already made
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
    """The dry-run view (§6.2.1) shows this to a staff member. A bare
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
    ("sector", "manufacturing"),
    ("food_category", "diary"),
    ("metric", "co2"),
])
def test_validate_reports_a_downstream_row_naming_anything_absent(field_name, value):
    data = _minimal()
    data["downstream"][0][field_name] = value

    problems = FactorBundle.from_json(data).validate()

    assert any(value in problem for problem in problems)


def test_validate_accepts_the_three_legal_nulls():
    """`upstream[].destination`, `downstream[].sector` and
    `downstream[].food_category` are `null` on the rows that carry the general
    case, and none of the three is a problem."""
    data = _minimal()
    assert data["upstream"][0]["destination"] is None
    assert data["downstream"][0]["sector"] is None
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
    """Not in §4.1's list, and the same silent-wrong-number failure as a
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
    assert loaded.upstream("processing", "dairy", None, None, "co2e") == Decimal("9.99")


def test_validate_never_raises_on_a_bundle_that_is_wrong_in_every_way():
    """§4.1: it returns problems, and the API decides how to present them."""
    data = _minimal()
    data["upstream"][0].update(sector="x", food_category="y", destination="z", metric="m")
    data["downstream"][0].update(destination="p", sector="s2", food_category="q",
                                 metric="r")
    data["destinations"][0]["group"] = "s"
    data["food_categories"][0]["is_standard_mix"] = False
    data["formulas"][0]["metric"] = "t"
    data["equivalences"][0]["source_metric"] = "u"

    problems = FactorBundle.from_json(data).validate()

    assert len(problems) == 12
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

# ==========================================================================
# The two-dimensional downstream lookup (contract v1.31, §4.1)
# ==========================================================================
#
# `factor_downstream` has two nullable dimensions, so four rows may legally
# exist for one (destination, metric) and exactly one of them must win:
#
#     1. (sector, food_category)   -- both stated
#     2. (sector, NULL)            -- this sector, every food category
#     3. (NULL, food_category)     -- every sector, this food category
#     4. (NULL, NULL)              -- every sector, every food category
#     5. Decimal('0')
#
# Steps 2 and 3 both name one dimension, so specificity cannot separate them.
# The sector wins; §2.2 carries the reasoning.
#
# **Every cell is asserted on the value, not on the presence of a value.** A
# wrong precedence here returns a plausible number rather than an error, so a
# test that only checked "some row came back" would pass against every one of
# the twenty-four orderings of these four candidates. Each constant below is
# distinct and each assertion names which row it expects by name.

#: One value per candidate row, chosen so that no two are equal and no sum or
#: difference of two of them equals a third -- an off-by-one in the candidate
#: list cannot coincide with a right answer.
BOTH = Decimal("11.0000000000")        # (sector, food_category)
SECTOR_ONLY = Decimal("22.0000000000")  # (sector, NULL)
FOOD_ONLY = Decimal("33.0000000000")    # (NULL, food_category)
NEITHER = Decimal("44.0000000000")      # (NULL, NULL)

#: The four rows keyed exactly as `FactorBundle.downstream_factors` is.
ALL_FOUR = {
    ("landfill", "processing", "dairy", "co2e"): BOTH,
    ("landfill", "processing", None, "co2e"): SECTOR_ONLY,
    ("landfill", None, "dairy", "co2e"): FOOD_ONLY,
    ("landfill", None, None, "co2e"): NEITHER,
}


def _matrix_bundle(present):
    """A bundle carrying only the named subset of the four candidate rows.

    Built by hand rather than through `from_json` so that the test is about the
    lookup and nothing else; `test_the_downstream_sector_survives_from_json`
    below covers the loader.
    """
    return FactorBundle(
        upstream_factors={},
        downstream_factors={key: ALL_FOUR[key] for key in present},
        constants={},
        formulas={},
        destinations={"landfill"},
        sectors={"processing", "retail"},
        food_categories={"dairy", "vegetables"},
        standard_mix="standard_mix",
        version_label="MATRIX",
        is_mock=True,
        metrics=(MetricSpec("co2e", "kg CO2e", 1),),
        equivalence_specs=(),
    )


#: Every subset of the four rows, paired with what a lookup for
#: (landfill, processing, dairy, co2e) must return from it. Sixteen cases: the
#: full power set, so no combination is left to an assumption. The empty set
#: is the documented fall to zero.
FULL_MATRIX = [
    ((), Decimal("0")),

    # One row present: whichever one it is, it applies.
    ((("landfill", "processing", "dairy", "co2e"),), BOTH),
    ((("landfill", "processing", None, "co2e"),), SECTOR_ONLY),
    ((("landfill", None, "dairy", "co2e"),), FOOD_ONLY),
    ((("landfill", None, None, "co2e"),), NEITHER),

    # Two rows present. The pair that matters is the third of these: a row
    # naming only the sector against a row naming only the food category,
    # neither more specific than the other by any count of stated dimensions.
    ((("landfill", "processing", "dairy", "co2e"),
      ("landfill", "processing", None, "co2e")), BOTH),
    ((("landfill", "processing", "dairy", "co2e"),
      ("landfill", None, "dairy", "co2e")), BOTH),
    ((("landfill", "processing", None, "co2e"),
      ("landfill", None, "dairy", "co2e")), SECTOR_ONLY),
    ((("landfill", "processing", None, "co2e"),
      ("landfill", None, None, "co2e")), SECTOR_ONLY),
    ((("landfill", None, "dairy", "co2e"),
      ("landfill", None, None, "co2e")), FOOD_ONLY),
    ((("landfill", "processing", "dairy", "co2e"),
      ("landfill", None, None, "co2e")), BOTH),

    # Three rows present.
    ((("landfill", "processing", "dairy", "co2e"),
      ("landfill", "processing", None, "co2e"),
      ("landfill", None, "dairy", "co2e")), BOTH),
    ((("landfill", "processing", "dairy", "co2e"),
      ("landfill", "processing", None, "co2e"),
      ("landfill", None, None, "co2e")), BOTH),
    ((("landfill", "processing", "dairy", "co2e"),
      ("landfill", None, "dairy", "co2e"),
      ("landfill", None, None, "co2e")), BOTH),
    ((("landfill", "processing", None, "co2e"),
      ("landfill", None, "dairy", "co2e"),
      ("landfill", None, None, "co2e")), SECTOR_ONLY),

    # All four.
    (tuple(ALL_FOUR), BOTH),
]


@pytest.mark.parametrize("present,expected", FULL_MATRIX,
                         ids=lambda x: None)
def test_the_downstream_lookup_matrix_returns_the_right_row(present, expected):
    """All sixteen subsets of the four candidate rows, by value.

    The assertion is on which row came back, never on whether one did: three
    of the four candidates are non-zero, so `!= 0` would pass on a bundle that
    returned the wrong row every time.
    """
    bundle = _matrix_bundle(present)

    assert bundle.downstream("landfill", "processing", "dairy", "co2e") == expected


def test_the_sector_beats_the_food_category_when_only_one_of_each_exists():
    """The precedence decision itself, stated once on its own.

    Called out separately from the matrix above because it is the only cell
    where the answer was chosen rather than derived, and because a reader
    coming to this file after a wrong number in the field will look for it by
    name.
    """
    bundle = _matrix_bundle((
        ("landfill", "processing", None, "co2e"),
        ("landfill", None, "dairy", "co2e"),
    ))

    assert bundle.downstream("landfill", "processing", "dairy", "co2e") == SECTOR_ONLY
    assert bundle.downstream("landfill", "processing", "dairy", "co2e") != FOOD_ONLY


def test_a_different_sector_falls_past_the_sector_row_to_the_food_row():
    """The other half of the same decision: a sector row applies to *its*
    sector and to no other. Without this, a test suite could pass with a
    lookup that ignored the sector value and always preferred slot two."""
    bundle = _matrix_bundle((
        ("landfill", "processing", None, "co2e"),
        ("landfill", None, "dairy", "co2e"),
    ))

    assert bundle.downstream("landfill", "retail", "dairy", "co2e") == FOOD_ONLY


def test_a_different_food_category_falls_past_the_food_row_to_the_general_row():
    bundle = _matrix_bundle((
        ("landfill", None, "dairy", "co2e"),
        ("landfill", None, None, "co2e"),
    ))

    assert bundle.downstream("landfill", "processing", "vegetables", "co2e") == NEITHER


def test_asking_for_the_general_rows_directly_does_not_skip_them():
    """§4.1: when `sector` is None, step 1 *is* step 3 and step 2 *is* step 4,
    so the fallbacks have to be looked up rather than assumed absent. A lookup
    written as `if exact not in ...: return fallback` would return the wrong
    row here, or nothing."""
    bundle = _matrix_bundle(tuple(ALL_FOUR))

    assert bundle.downstream("landfill", None, "dairy", "co2e") == FOOD_ONLY
    assert bundle.downstream("landfill", None, None, "co2e") == NEITHER
    assert bundle.downstream("landfill", "processing", None, "co2e") == SECTOR_ONLY


def test_an_unknown_sector_and_category_reach_the_row_that_names_neither():
    """A destination priced only by the row naming neither dimension -- the
    waste levy shape -- must answer for every sector and every category,
    including ones the bundle has never heard of."""
    bundle = _matrix_bundle((("landfill", None, None, "co2e"),))

    assert bundle.downstream("landfill", "moon", "moon_cheese", "co2e") == NEITHER


def test_the_downstream_sector_survives_from_json_in_both_states():
    """§10.2: `sector` is a key whose value may be null, and both the null and
    the named form must round-trip. A loader that dropped the field entirely
    would still pass every matrix test above, because those build the dict
    directly."""
    data = _minimal()
    data["downstream"] = [
        {"destination": "landfill", "sector": None, "food_category": None,
         "metric": "co2e", "value_per_kg": "0.7000000000"},
        {"destination": "landfill", "sector": "processing", "food_category": None,
         "metric": "co2e", "value_per_kg": "1.2500000000"},
    ]

    loaded = FactorBundle.from_json(data)

    assert loaded.validate() == []
    assert loaded.downstream(
        "landfill", "processing", "dairy", "co2e"
    ) == Decimal("1.2500000000")
    assert loaded.downstream(
        "landfill", "consumer", "dairy", "co2e"
    ) == Decimal("0.7000000000")


def test_two_downstream_rows_differing_only_in_sector_are_not_duplicates():
    """The sector is part of the key, so these are two rows and not one.

    `validate()` reports a duplicate key as a problem, and if the loader built
    its key without the sector one of these would silently overwrite the other
    -- the later row winning, which is precisely the failure the duplicate
    check exists to name. Asserting the *absence* of a problem is weak on its
    own, so both values are asserted too.
    """
    data = _minimal()
    data["downstream"] = [
        {"destination": "landfill", "sector": "processing", "food_category": "dairy",
         "metric": "co2e", "value_per_kg": "0.7000000000"},
        {"destination": "landfill", "sector": None, "food_category": "dairy",
         "metric": "co2e", "value_per_kg": "1.2500000000"},
    ]

    loaded = FactorBundle.from_json(data)

    assert loaded.validate() == []
    assert loaded.downstream(
        "landfill", "processing", "dairy", "co2e"
    ) == Decimal("0.7000000000")
    assert loaded.downstream(
        "landfill", "retail", "dairy", "co2e"
    ) == Decimal("1.2500000000")


def test_validate_reports_a_downstream_row_naming_a_sector_that_is_absent():
    """The mirror of the upstream destination check, and silent in the same
    way: a misspelled sector here does not raise, it falls through to the
    every-sector row and the calculator prices the wrong stage."""
    data = _minimal()
    data["downstream"][0]["sector"] = "prcoessing"

    problems = FactorBundle.from_json(data).validate()

    assert len(problems) == 1
    assert "prcoessing" in problems[0]
    assert "sector" in problems[0]


# ==========================================================================
# The two-dimensional upstream lookup (contract v1.54, §2.2, §4.1)
# ==========================================================================
#
# `factor_upstream` has two nullable dimensions since v1.54, so four rows may
# legally exist for one (sector, food_category, metric) and exactly one of
# them must win:
#
#     1. (item, destination)   -- this food, at this destination
#     2. (NULL, destination)   -- every food in this category, here
#     3. (item, NULL)          -- this food, at every destination
#     4. (NULL, NULL)          -- the category average, everywhere
#     5. Decimal('0')
#
# Steps 2 and 3 both name one dimension, so specificity cannot separate them.
# **The destination wins**, because item-first re-opens O-7: the prevention
# offset is a shape-2 row at zero, and an item's shape-3 row placed above it
# would price a prevented line at the item's ordinary factor.
# `test_an_item_generic_row_does_not_outrank_the_prevention_zero` below is
# that consequence stated as a test, in the same terms §2.2 measured.
#
# **Every cell is asserted on the value, not on the presence of a value** --
# the same discipline the downstream matrix above is written to, and for the
# same reason: a wrong precedence here returns a plausible number rather than
# an error, so "a row came back" would pass under all twenty-four orderings.

#: One value per candidate row, chosen so that no two are equal and no sum or
#: difference of two equals a third.
ITEM_HERE = Decimal("111.0000000000")            # (item, destination)
CATEGORY_HERE = Decimal("222.0000000000")        # (NULL, destination)
ITEM_EVERYWHERE = Decimal("333.0000000000")      # (item, NULL)
CATEGORY_EVERYWHERE = Decimal("444.0000000000")  # (NULL, NULL)

#: The four rows keyed exactly as `FactorBundle.upstream_factors` is.
ALL_FOUR_UPSTREAM = {
    ("processing", "dairy", "cheese", "landfill", "co2e"): ITEM_HERE,
    ("processing", "dairy", None, "landfill", "co2e"): CATEGORY_HERE,
    ("processing", "dairy", "cheese", None, "co2e"): ITEM_EVERYWHERE,
    ("processing", "dairy", None, None, "co2e"): CATEGORY_EVERYWHERE,
}


def _item_matrix_bundle(present, *, items=None):
    """A bundle carrying only the named subset of the four candidate rows.

    Built by hand rather than through `from_json` so that the test is about
    the lookup and nothing else; the loader has its own tests below.
    """
    return FactorBundle(
        upstream_factors={key: ALL_FOUR_UPSTREAM[key] for key in present},
        downstream_factors={},
        constants={},
        formulas={},
        destinations={"landfill", "compost", "prevention"},
        sectors={"processing", "retail"},
        food_categories={"dairy", "fruit"},
        standard_mix="standard_mix",
        version_label="ITEM-MATRIX",
        is_mock=True,
        metrics=(MetricSpec("co2e", "kg CO2e", 1),),
        equivalence_specs=(),
        food_item_category_of=(
            {"cheese": "dairy", "butter": "dairy", "feijoa": "fruit"}
            if items is None else items
        ),
    )


#: Every subset of the four rows, paired with what a lookup for
#: (processing, dairy, cheese, landfill, co2e) must return from it. Sixteen
#: cases: the full power set, so no combination is left to an assumption. The
#: empty set is the documented fall to zero.
FULL_UPSTREAM_MATRIX = [
    ((), Decimal("0")),

    # One row present: whichever one it is, it applies.
    ((("processing", "dairy", "cheese", "landfill", "co2e"),), ITEM_HERE),
    ((("processing", "dairy", None, "landfill", "co2e"),), CATEGORY_HERE),
    ((("processing", "dairy", "cheese", None, "co2e"),), ITEM_EVERYWHERE),
    ((("processing", "dairy", None, None, "co2e"),), CATEGORY_EVERYWHERE),

    # Two rows present. The pair that matters is the third of these: a row
    # naming only the item against a row naming only the destination, neither
    # more specific than the other by any count of stated dimensions.
    ((("processing", "dairy", "cheese", "landfill", "co2e"),
      ("processing", "dairy", None, "landfill", "co2e")), ITEM_HERE),
    ((("processing", "dairy", "cheese", "landfill", "co2e"),
      ("processing", "dairy", "cheese", None, "co2e")), ITEM_HERE),
    ((("processing", "dairy", None, "landfill", "co2e"),
      ("processing", "dairy", "cheese", None, "co2e")), CATEGORY_HERE),
    ((("processing", "dairy", None, "landfill", "co2e"),
      ("processing", "dairy", None, None, "co2e")), CATEGORY_HERE),
    ((("processing", "dairy", "cheese", None, "co2e"),
      ("processing", "dairy", None, None, "co2e")), ITEM_EVERYWHERE),
    ((("processing", "dairy", "cheese", "landfill", "co2e"),
      ("processing", "dairy", None, None, "co2e")), ITEM_HERE),

    # Three rows present.
    ((("processing", "dairy", "cheese", "landfill", "co2e"),
      ("processing", "dairy", None, "landfill", "co2e"),
      ("processing", "dairy", "cheese", None, "co2e")), ITEM_HERE),
    ((("processing", "dairy", "cheese", "landfill", "co2e"),
      ("processing", "dairy", None, "landfill", "co2e"),
      ("processing", "dairy", None, None, "co2e")), ITEM_HERE),
    ((("processing", "dairy", "cheese", "landfill", "co2e"),
      ("processing", "dairy", "cheese", None, "co2e"),
      ("processing", "dairy", None, None, "co2e")), ITEM_HERE),
    ((("processing", "dairy", None, "landfill", "co2e"),
      ("processing", "dairy", "cheese", None, "co2e"),
      ("processing", "dairy", None, None, "co2e")), CATEGORY_HERE),

    # All four.
    (tuple(ALL_FOUR_UPSTREAM), ITEM_HERE),
]


@pytest.mark.parametrize("present,expected", FULL_UPSTREAM_MATRIX,
                         ids=lambda x: None)
def test_the_upstream_lookup_matrix_returns_the_right_row(present, expected):
    """All sixteen subsets of the four candidate rows, by value."""
    bundle = _item_matrix_bundle(present)

    assert bundle.upstream(
        "processing", "dairy", "cheese", "landfill", "co2e"
    ) == expected


def test_the_destination_beats_the_item_when_only_one_of_each_exists():
    """The precedence decision itself, stated once on its own.

    Called out separately from the matrix above because it is the only cell
    where the answer was chosen rather than derived, and because a reader
    coming to this file after a wrong number in the field will look for it by
    name.
    """
    bundle = _item_matrix_bundle((
        ("processing", "dairy", None, "landfill", "co2e"),
        ("processing", "dairy", "cheese", None, "co2e"),
    ))

    assert bundle.upstream(
        "processing", "dairy", "cheese", "landfill", "co2e"
    ) == CATEGORY_HERE
    assert bundle.upstream(
        "processing", "dairy", "cheese", "landfill", "co2e"
    ) != ITEM_EVERYWHERE


def test_an_item_generic_row_does_not_outrank_the_prevention_zero():
    """**Why the destination wins, in the shape §2.2 measured.**

    The prevention offset is a category-level, destination-specific row at
    zero -- candidate 2. Cheese has an ordinary generic factor -- candidate 3.
    Order the item first and a line moved to `prevention` is priced at 21.0
    per kg instead of 0, which is O-7 exactly: the benefit understated,
    one-directionally, on the client's "wasting less" story. Nothing else in
    the tree would fail -- `find_missing_prevention_upstream` and
    `refuse_nonzero_prevention_factors` both look at rows, and every row here
    is correct.
    """
    bundle = FactorBundle(
        upstream_factors={
            # The category's prevention zero -- one row, covering every food
            # under dairy, and no item-level prevention row anywhere.
            ("processing", "dairy", None, "prevention", "co2e"): Decimal("0"),
            ("processing", "dairy", None, None, "co2e"): Decimal("19.0"),
            # Cheese is heavier than the dairy average and says so.
            ("processing", "dairy", "cheese", None, "co2e"): Decimal("21.0"),
        },
        downstream_factors={},
        constants={},
        formulas={},
        destinations={"landfill", "prevention"},
        sectors={"processing"},
        food_categories={"dairy"},
        standard_mix="standard_mix",
        version_label="O7",
        is_mock=True,
        metrics=(MetricSpec("co2e", "kg CO2e", 1),),
        equivalence_specs=(),
        food_item_category_of={"cheese": "dairy"},
    )

    assert bundle.upstream(
        "processing", "dairy", "cheese", "prevention", "co2e"
    ) == Decimal("0")
    assert bundle.upstream(
        "processing", "dairy", "cheese", "landfill", "co2e"
    ) == Decimal("21.0")


def test_a_different_item_falls_past_the_item_row_to_the_category_row():
    """The other half of the same decision: an item row applies to *its* item
    and to no other. Without this, a suite could pass with a lookup that
    ignored the item value and always preferred the item slot."""
    bundle = _item_matrix_bundle((
        ("processing", "dairy", "cheese", None, "co2e"),
        ("processing", "dairy", None, None, "co2e"),
    ))

    assert bundle.upstream(
        "processing", "dairy", "butter", "landfill", "co2e"
    ) == CATEGORY_EVERYWHERE


def test_a_different_destination_falls_past_the_here_row_to_the_generic_row():
    """And the mirror for the destination slot."""
    bundle = _item_matrix_bundle((
        ("processing", "dairy", "cheese", "landfill", "co2e"),
        ("processing", "dairy", "cheese", None, "co2e"),
    ))

    assert bundle.upstream(
        "processing", "dairy", "cheese", "compost", "co2e"
    ) == ITEM_EVERYWHERE


def test_asking_for_the_category_rows_directly_does_not_skip_them():
    """§4.1: when `food_item` is None, candidate 1 *is* candidate 2 and
    candidate 3 *is* candidate 4; when `destination` is None, candidate 1 *is*
    candidate 3. So the later steps must be looked up, not assumed absent."""
    bundle = _item_matrix_bundle(tuple(ALL_FOUR_UPSTREAM))

    assert bundle.upstream(
        "processing", "dairy", None, "landfill", "co2e"
    ) == CATEGORY_HERE
    assert bundle.upstream(
        "processing", "dairy", None, None, "co2e"
    ) == CATEGORY_EVERYWHERE
    assert bundle.upstream(
        "processing", "dairy", "cheese", None, "co2e"
    ) == ITEM_EVERYWHERE


def test_an_item_with_no_row_of_its_own_gets_the_category_average_not_zero():
    """The claim that makes the destination-first tie affordable. An item the
    factor set says nothing about is the *normal* state -- one item row
    releases the level and the other eighteen foods have none -- and it must
    answer with the category average, which is a defined, meaningful number
    and is literally the average of those same foods."""
    bundle = _item_matrix_bundle((
        ("processing", "dairy", None, None, "co2e"),
    ))

    assert bundle.upstream(
        "processing", "dairy", "butter", "landfill", "co2e"
    ) == CATEGORY_EVERYWHERE
    assert bundle.upstream(
        "processing", "dairy", "butter", "landfill", "co2e"
    ) != Decimal("0")


# ---------- upstream_with_basis (groundwork for the disclosure) ----------


@pytest.mark.parametrize("present,expected", FULL_UPSTREAM_MATRIX,
                         ids=lambda x: None)
def test_the_basis_lookup_agrees_with_the_value_lookup_everywhere(present, expected):
    """One chain, two entry points, and they may never disagree.

    The same sixteen subsets the matrix above asserts by value: whatever
    `upstream()` returns, `upstream_with_basis()` must return the same number.
    A second copy of the candidate list is the way this drifts.
    """
    bundle = _item_matrix_bundle(present)

    value, _ = bundle.upstream_with_basis(
        "processing", "dairy", "cheese", "landfill", "co2e"
    )

    assert value == expected
    assert value == bundle.upstream(
        "processing", "dairy", "cheese", "landfill", "co2e"
    )


@pytest.mark.parametrize("row,expected_value,expected_basis", [
    (("processing", "dairy", "cheese", "landfill", "co2e"),
     ITEM_HERE, UpstreamBasis.ITEM_AT_DESTINATION),
    (("processing", "dairy", None, "landfill", "co2e"),
     CATEGORY_HERE, UpstreamBasis.CATEGORY_AT_DESTINATION),
    (("processing", "dairy", "cheese", None, "co2e"),
     ITEM_EVERYWHERE, UpstreamBasis.ITEM_EVERY_DESTINATION),
    (("processing", "dairy", None, None, "co2e"),
     CATEGORY_EVERYWHERE, UpstreamBasis.CATEGORY_EVERY_DESTINATION),
])
def test_each_candidate_names_itself(row, expected_value, expected_basis):
    """Each of the four rows, alone, reports which one it is.

    Asserted by member and not merely by "something came back": the fallback
    disclosure branches on this, so a basis that named the wrong row would
    print "this figure is the Dairy average" over a figure that is cheese's
    own -- or, worse, stay silent over one that is not.
    """
    bundle = _item_matrix_bundle((row,))

    assert bundle.upstream_with_basis(
        "processing", "dairy", "cheese", "landfill", "co2e"
    ) == (expected_value, expected_basis)


def test_the_basis_of_nothing_at_all_is_absent():
    bundle = _item_matrix_bundle(())

    assert bundle.upstream_with_basis(
        "processing", "dairy", "cheese", "landfill", "co2e"
    ) == (Decimal("0"), UpstreamBasis.ABSENT)


def test_a_lookup_that_named_no_item_is_never_reported_as_item_level():
    """The degenerate case the basis has to survive. With `food_item=None`
    candidate 1 and candidate 2 are the *same key*, so a basis taken from the
    candidate's position in the list rather than from the winning row would
    label a category row `ITEM_AT_DESTINATION` -- and step 7 would suppress a
    disclosure it owes the reader."""
    bundle = _item_matrix_bundle(tuple(ALL_FOUR_UPSTREAM))

    _, basis = bundle.upstream_with_basis(
        "processing", "dairy", None, "landfill", "co2e"
    )

    assert basis is UpstreamBasis.CATEGORY_AT_DESTINATION
    assert not basis.is_item_level


def test_a_lookup_that_named_no_destination_is_never_reported_as_here_only():
    """The same degeneracy in the other slot: with `destination=None`,
    candidate 1 is candidate 3."""
    bundle = _item_matrix_bundle(tuple(ALL_FOUR_UPSTREAM))

    _, basis = bundle.upstream_with_basis(
        "processing", "dairy", "cheese", None, "co2e"
    )

    assert basis is UpstreamBasis.ITEM_EVERY_DESTINATION
    assert basis.is_item_level


def test_the_disclosure_branch_is_a_value_and_not_a_sentence():
    """What landing step 7 will read. The engine says *which row*; the
    sentence ("this figure is the Fruit average, not Feijoas") is the
    caller's, because it is copy and it is translated."""
    bundle = _item_matrix_bundle((
        ("processing", "dairy", None, None, "co2e"),
    ))

    _, basis = bundle.upstream_with_basis(
        "processing", "dairy", "cheese", "landfill", "co2e"
    )

    assert isinstance(basis, UpstreamBasis)
    assert basis is UpstreamBasis.CATEGORY_EVERY_DESTINATION
    # The branch itself: an item was named and the figure does not refine it.
    assert not basis.is_item_level


@pytest.mark.parametrize("member,is_item", [
    (UpstreamBasis.ITEM_AT_DESTINATION, True),
    (UpstreamBasis.ITEM_EVERY_DESTINATION, True),
    (UpstreamBasis.CATEGORY_AT_DESTINATION, False),
    (UpstreamBasis.CATEGORY_EVERY_DESTINATION, False),
    (UpstreamBasis.ABSENT, False),
])
def test_is_item_level_covers_every_member(member, is_item):
    """Including ABSENT, which is the one a membership test written as "not a
    category row" would get wrong: no row at all refines no food."""
    assert member.is_item_level is is_item


# ---------- resolution: the engine refuses what the database permits ----------


def test_resolving_no_item_is_not_an_error_and_stays_none():
    """Every visitor who was not asked step 2.5 arrives this way, and it is an
    answer rather than an absence. It is deliberately *not* resolved to a
    stand-in the way a null food_category is resolved to `standard_mix`:
    there is no standard food."""
    bundle = _item_matrix_bundle(())

    assert bundle.resolve_food_item(None, "dairy") is None


def test_resolving_a_known_item_returns_its_code():
    bundle = _item_matrix_bundle(())

    assert bundle.resolve_food_item("cheese", "dairy") == "cheese"


def test_an_unknown_item_is_refused_rather_than_falling_back():
    """§6. The chain falls back; the vocabulary does not. An item code this
    bundle has never heard of is a caller defect, and letting it fall through
    to the category average would return a plausible number for a food that
    does not exist."""
    bundle = _item_matrix_bundle(())

    with pytest.raises(UnknownCodeError) as excinfo:
        bundle.resolve_food_item("moon_cheese", "dairy")

    assert "moon_cheese" in str(excinfo.value)


def test_an_item_from_another_category_is_refused():
    """`submission_entry` permits `(fruit, cheese)`: two independent foreign
    keys and a CHECK that only refuses an item arriving without a category.
    The engine is what refuses the pair, and it has to, because the lookup
    would otherwise fall past candidates 1 and 3 and price cheese as the fruit
    average -- a wrong answer that looks right."""
    bundle = _item_matrix_bundle(())

    with pytest.raises(UnknownCodeError) as excinfo:
        bundle.resolve_food_item("cheese", "fruit")

    message = str(excinfo.value)
    assert "cheese" in message
    assert "dairy" in message and "fruit" in message


def test_has_food_item():
    bundle = _item_matrix_bundle(())

    assert bundle.has_food_item("cheese") is True
    assert bundle.has_food_item("moon_cheese") is False


def test_a_bundle_with_no_item_vocabulary_knows_no_items():
    """Which is every bundle written before v1.54, and the state this landing
    leaves the deployed system in."""
    bundle = _item_matrix_bundle((), items={})

    assert bundle.has_food_item("cheese") is False
    assert bundle.resolve_food_item(None, "dairy") is None
    with pytest.raises(UnknownCodeError):
        bundle.resolve_food_item("cheese", "dairy")


# ---------- the food_items section of bundle.json (§10.2) ----------


def test_food_items_is_not_a_required_key():
    """It must never join `REQUIRED_KEYS`: every bundle in the tree omits it
    -- thirteen golden cases, `build_bundle_data`'s output, and the
    `GET /factors` response a staff member pastes into the dry-run box -- and
    all of them must still load."""
    assert FOOD_ITEMS_KEY not in REQUIRED_KEYS


def test_a_bundle_with_no_food_items_section_loads_and_validates_clean():
    data = _minimal()
    assert FOOD_ITEMS_KEY not in data

    loaded = FactorBundle.from_json(data)

    assert loaded.validate() == []
    assert loaded.food_item_category_of == {}
    assert loaded.upstream(
        "processing", "dairy", None, "landfill", "co2e"
    ) == Decimal("1.9000000000")


def test_an_upstream_row_that_omits_food_item_is_the_category_row():
    """Absence and `null` mean the same thing here, which is why the key is
    optional and `destination` beside it is not. Asserted **by value**: the
    row has to answer a lookup that names an item, through candidate 4."""
    data = _minimal()
    data[FOOD_ITEMS_KEY] = [
        {"code": "cheese", "name": "Cheese", "food_category": "dairy",
         "sort_order": 1},
    ]

    loaded = FactorBundle.from_json(data)

    assert loaded.validate() == []
    assert loaded.upstream(
        "processing", "dairy", "cheese", "landfill", "co2e"
    ) == Decimal("1.9000000000")


def test_the_item_dimension_round_trips_through_from_json_in_both_states():
    """§10.2: `food_item` is a key whose value may be a code or null, and both
    forms must survive the loader. A loader that dropped the field entirely
    would still pass every matrix test above, because those build the dict
    directly."""
    data = _minimal()
    data[FOOD_ITEMS_KEY] = [
        {"code": "cheese", "name": "Cheese", "food_category": "dairy"},
    ]
    data["upstream"] = [
        {"sector": "processing", "food_category": "dairy", "food_item": None,
         "destination": None, "metric": "co2e", "value_per_kg": "1.9000000000"},
        {"sector": "processing", "food_category": "dairy", "food_item": "cheese",
         "destination": None, "metric": "co2e", "value_per_kg": "2.7000000000"},
    ]

    loaded = FactorBundle.from_json(data)

    assert loaded.validate() == []
    assert loaded.upstream(
        "processing", "dairy", "cheese", "landfill", "co2e"
    ) == Decimal("2.7000000000")
    assert loaded.upstream(
        "processing", "dairy", None, "landfill", "co2e"
    ) == Decimal("1.9000000000")
    assert loaded.upstream(
        "processing", "dairy", "butter", "landfill", "co2e"
    ) == Decimal("1.9000000000")


def test_two_upstream_rows_differing_only_in_food_item_are_not_duplicates():
    """The item is part of the key, so these are two rows and not one. If the
    loader built its key without the item slot one would silently overwrite
    the other -- the later row winning, which is precisely the failure the
    duplicate check exists to name. Both values are asserted too, because
    asserting the absence of a problem is weak on its own."""
    data = _minimal()
    data[FOOD_ITEMS_KEY] = [
        {"code": "cheese", "name": "Cheese", "food_category": "dairy"},
    ]
    data["upstream"] = [
        {"sector": "processing", "food_category": "dairy", "food_item": "cheese",
         "destination": None, "metric": "co2e", "value_per_kg": "2.7000000000"},
        {"sector": "processing", "food_category": "dairy", "food_item": None,
         "destination": None, "metric": "co2e", "value_per_kg": "1.9000000000"},
    ]

    loaded = FactorBundle.from_json(data)

    assert loaded.validate() == []
    assert loaded.upstream(
        "processing", "dairy", "cheese", "landfill", "co2e"
    ) == Decimal("2.7000000000")
    assert loaded.upstream(
        "processing", "dairy", None, "landfill", "co2e"
    ) == Decimal("1.9000000000")


def test_a_duplicate_food_item_code_is_reported():
    data = _minimal()
    data[FOOD_ITEMS_KEY] = [
        {"code": "cheese", "name": "Cheese", "food_category": "dairy"},
        {"code": "cheese", "name": "Cheddar", "food_category": "dairy"},
    ]

    problems = FactorBundle.from_json(data).validate()

    assert len(problems) == 1
    assert "cheese" in problems[0]
    assert "more than once" in problems[0]


def test_a_food_item_row_missing_its_category_is_malformed():
    """`food_item.food_category_id` is NOT NULL, and for a reason: the parent
    is both the fallback and the thing a request is checked against."""
    data = _minimal()
    data[FOOD_ITEMS_KEY] = [{"code": "cheese", "name": "Cheese"}]

    with pytest.raises(BundleFormatError) as excinfo:
        FactorBundle.from_json(data)

    assert "food_category" in str(excinfo.value)


def test_a_food_item_key_that_is_neither_a_code_nor_null_is_malformed():
    """Optional does not mean unchecked."""
    data = _minimal()
    data["upstream"][0]["food_item"] = 7

    with pytest.raises(BundleFormatError) as excinfo:
        FactorBundle.from_json(data)

    assert "food_item" in str(excinfo.value)


def test_validate_reports_an_upstream_row_naming_an_item_that_is_absent():
    """Silent in the same way a dangling destination is: the row does not
    raise, it is simply never reached, and its author sees a factor they wrote
    having no effect."""
    data = _minimal()
    data[FOOD_ITEMS_KEY] = [
        {"code": "cheese", "name": "Cheese", "food_category": "dairy"},
    ]
    data["upstream"][0]["food_item"] = "chesee"

    problems = FactorBundle.from_json(data).validate()

    assert len(problems) == 1
    assert "chesee" in problems[0]
    assert "food_item" in problems[0]


def test_validate_reports_an_upstream_row_whose_item_is_from_another_category():
    """The factor-table half of what `resolve_food_item()` refuses on a
    request: a row priced for cheese but filed under fruit is unreachable,
    because a lookup only ever builds the key with the item's real parent."""
    data = _minimal()
    data["food_categories"].append(
        {"code": "fruit", "name": "Fruit", "is_standard_mix": False, "sort_order": 2}
    )
    data[FOOD_ITEMS_KEY] = [
        {"code": "cheese", "name": "Cheese", "food_category": "dairy"},
    ]
    data["upstream"].append(
        {"sector": "processing", "food_category": "fruit", "food_item": "cheese",
         "destination": None, "metric": "co2e", "value_per_kg": "2.7000000000"}
    )

    problems = FactorBundle.from_json(data).validate()

    assert len(problems) == 1
    assert "cheese" in problems[0] and "dairy" in problems[0]
    assert "no lookup can reach this row" in problems[0]


def test_validate_reports_a_food_item_whose_category_is_absent():
    data = _minimal()
    data[FOOD_ITEMS_KEY] = [
        {"code": "cheese", "name": "Cheese", "food_category": "dariy"},
    ]

    problems = FactorBundle.from_json(data).validate()

    assert len(problems) == 1
    assert "cheese" in problems[0] and "dariy" in problems[0]
