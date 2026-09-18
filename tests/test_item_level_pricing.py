"""A named food reaching the engine and changing the number. §3, §4.1, §4.2.

`tests/db/test_food_item_projection.py` says in terms what it does *not*
assert — "that an item-level factor prices a line differently through
`engine.calculate`" — because on the branch that wrote it the request had no
food to carry. v1.58 gives `EntryInput` its `food_item_code`, and this is the
file that closes that gap.

**Why it is not enough that `FactorBundle.upstream` takes five slots.** It has
since v1.54, and `engine/calculate.py` passed a literal `None` into the fifth
of them. Every test of the lookup passed, every golden case passed, the
projection published the item rows, staff could author them — and no request
could reach one. A dimension that is wired everywhere except at the one place
a visitor's answer enters is a dimension that does nothing, and nothing in the
suite said so.

Four things are pinned here, and each fails on its own:

1. a food with a row of its own is priced by **that** row;
2. a food with no row of its own falls to the **category average**, which is a
   defined number rather than a silent zero -- the whole reason §6.1 offers a
   food its parent covers;
3. **the destination still wins** over the item (design §2, open item O-7):
   give one food a generic row, order item-first, and a line moved to
   `prevention` picks up that food's generic factor instead of the zero;
4. the two refusals `resolve_food_item` makes are raised from `calculate`,
   not merely from the bundle.
"""

from __future__ import annotations

import copy
from decimal import Decimal

import pytest

from engine.bundle import FactorBundle
from engine.calculate import calculate
from engine.errors import UnknownCodeError
from engine.types import CalculationRequest, EntryInput, ScenarioLine


def _bundle_data():
    """`tests/test_bundle.py::_minimal`, plus a vocabulary of two foods.

    `cheese` is priced at 3.4 against the dairy category's 1.9, so any figure
    below says on its own which row answered. `butter` is declared and never
    priced, which is the state nearly every food will be in for the life of
    this project.
    """
    return {
        "version_label": "ITEM-v0",
        "is_mock": True,
        "sectors": [{"code": "processing", "name": "Processing", "sort_order": 1}],
        "food_categories": [
            {"code": "standard_mix", "name": "Mixed", "is_standard_mix": True,
             "sort_order": 0},
            {"code": "dairy", "name": "Dairy", "is_standard_mix": False,
             "sort_order": 1},
            {"code": "vegetables", "name": "Vegetables", "is_standard_mix": False,
             "sort_order": 2},
        ],
        "food_items": [
            {"code": "cheese", "name": "Cheese", "food_category": "dairy",
             "sort_order": 1},
            {"code": "butter", "name": "Butter", "food_category": "dairy",
             "sort_order": 2},
        ],
        "destination_groups": [
            {"code": "disposal", "name": "Disposal", "is_waste": True, "sort_order": 1}
        ],
        "destinations": [
            {"code": "landfill", "name": "Landfill", "group": "disposal",
             "sort_order": 1},
            {"code": "prevention", "name": "Prevented", "group": "disposal",
             "sort_order": 0, "is_prevention": True},
        ],
        "metrics": [
            {"code": "co2e", "name": "Greenhouse gas", "unit": "kg CO2e",
             "display_unit": "kg CO2e", "display_precision": 1, "sort_order": 1}
        ],
        "constants": [
            {"code": "GWP_CH4_100", "value": "28.0000000000", "unit": "", "note": ""}
        ],
        #: `qty_kg * upstream`, so the downstream half cannot mask which
        #: upstream row answered. The lookup order is the whole subject here.
        "formulas": [
            {"metric": "co2e", "expression": "qty_kg * upstream", "notes": ""}
        ],
        "upstream": [
            #: The category average, every destination.
            {"sector": "processing", "food_category": "dairy", "food_item": None,
             "destination": None, "metric": "co2e", "value_per_kg": "1.9000000000"},
            #: O-7's zero override, held at **category** level. This single row
            #: is what covers every food under dairy, and keeping it winning is
            #: what candidate ordering is for.
            {"sector": "processing", "food_category": "dairy", "food_item": None,
             "destination": "prevention", "metric": "co2e",
             "value_per_kg": "0.0000000000"},
            #: Cheese, every destination. Shape 3 in §2.2's chain -- the one
            #: that would beat the prevention zero under an item-first order.
            {"sector": "processing", "food_category": "dairy",
             "food_item": "cheese", "destination": None, "metric": "co2e",
             "value_per_kg": "3.4000000000"},
        ],
        "downstream": [
            {"destination": "landfill", "sector": None, "food_category": None,
             "metric": "co2e", "value_per_kg": "0.0000000000"},
            {"destination": "prevention", "sector": None, "food_category": None,
             "metric": "co2e", "value_per_kg": "0.0000000000"},
        ],
        "equivalences": [],
    }


@pytest.fixture
def bundle():
    loaded = FactorBundle.from_json(_bundle_data())
    assert loaded.validate() == [], loaded.validate()
    return loaded


def _request(food_item, *, food_category="dairy", destination="landfill", qty="100.000"):
    return CalculationRequest(
        entries=(
            EntryInput(
                sector_code="processing",
                food_category_code=food_category,
                food_item_code=food_item,
                current=(ScenarioLine(destination_code=destination, qty_kg=Decimal(qty)),),
                alternative=None,
            ),
        ),
        gwp_horizon=100,
    )


def _total(result):
    return result.entries[0].current.metrics["co2e"].total


# --------------------------------------------------------------------------
# The number changes, which is the only thing that makes the dimension real
# --------------------------------------------------------------------------


def test_naming_a_food_with_its_own_row_prices_the_line_at_that_row(bundle):
    """100 kg of cheese at 3.4, not at dairy's 1.9. Restore `calculate`'s
    literal `None` in the item slot and this reads 190.0000000000."""
    assert _total(calculate(_request("cheese"), bundle)) == Decimal("340.0000000000")


def test_naming_no_food_still_prices_the_line_at_the_category_average(bundle):
    """Every request written before v1.58, byte for byte unchanged."""
    assert _total(calculate(_request(None), bundle)) == Decimal("190.0000000000")


def test_a_food_with_no_row_of_its_own_falls_to_the_category_average(bundle):
    """§2.2's candidate 4, and the reason §6.1 offers a food whose parent is
    covered. `butter` is in the vocabulary and in no factor table: the answer
    is dairy's average, a defined and meaningful number, and **not** zero.
    A silent zero here is what the destination and sector rules exist to
    prevent, and it is the one thing this dimension must not reintroduce."""
    assert _total(calculate(_request("butter"), bundle)) == Decimal("190.0000000000")


def test_the_destination_still_beats_the_item_so_prevention_stays_a_whole_offset(
    bundle,
):
    """Open item O-7, in the item dimension. Measured at 456.000 -> 96.000 when
    it was last open -- 78.9% of the benefit, one-directionally, on the
    client's headline claim.

    `cheese` has a generic row at 3.4 (shape 3) and dairy has a
    prevention-specific row at zero (shape 2). Order item-first and 100 kg of
    prevented cheese costs 340; order destination-first, as §2.2 does, and the
    existing category-level zero covers every food under it with no new row
    and no new guard.
    """
    prevented = _request("cheese", destination="prevention")

    assert _total(calculate(prevented, bundle)) == Decimal("0.0000000000")
    #: The same line without the food, so the assertion above cannot pass
    #: merely because prevention is zero for everyone regardless of the chain.
    assert _total(calculate(_request(None, destination="prevention"), bundle)) == (
        Decimal("0.0000000000")
    )
    #: And the food is genuinely priced elsewhere, or the two zeros above
    #: would be consistent with the item slot never being read at all.
    assert _total(calculate(_request("cheese"), bundle)) == Decimal("340.0000000000")


def test_both_scenarios_of_an_entry_are_priced_as_the_same_food(bundle):
    """§3: an entry's two scenarios describe one point in the supply chain, so
    a per-scenario food would make an unrepresentable state representable. The
    net benefit of moving 100 kg of cheese to `prevention` is the whole 340,
    not dairy's 190."""
    request = CalculationRequest(
        entries=(
            EntryInput(
                sector_code="processing",
                food_category_code="dairy",
                food_item_code="cheese",
                current=(
                    ScenarioLine(destination_code="landfill", qty_kg=Decimal("100.000")),
                ),
                alternative=(
                    ScenarioLine(
                        destination_code="prevention", qty_kg=Decimal("100.000")
                    ),
                ),
            ),
        ),
        gwp_horizon=100,
    )

    result = calculate(request, bundle)

    assert result.entries[0].net_benefit["co2e"] == Decimal("340.0000000000")


def test_the_result_echoes_the_food_it_was_sent(bundle):
    """Echoed, never resolved -- the same rule `food_category_code` follows.
    `api/pdf_render.py` and the results page both label a figure from this."""
    assert calculate(_request("cheese"), bundle).entries[0].food_item_code == "cheese"
    assert calculate(_request(None), bundle).entries[0].food_item_code is None


# --------------------------------------------------------------------------
# The two refusals, raised from `calculate` and not only from the bundle
# --------------------------------------------------------------------------


def test_a_food_the_bundle_never_declared_is_refused(bundle):
    with pytest.raises(UnknownCodeError) as refusal:
        calculate(_request("unicorn_steak"), bundle)
    assert "unicorn_steak" in str(refusal.value)


def test_a_food_whose_parent_is_not_the_category_it_arrived_with_is_refused(bundle):
    """`submission_entry` stores the two as independent foreign keys and its
    CHECK constraint says only that an item may not arrive *without* a
    category, so `(vegetables, cheese)` is storable. Priced rather than
    refused, it would fall through candidates 1 and 3 to the vegetables
    average and look right."""
    with pytest.raises(UnknownCodeError) as refusal:
        calculate(_request("cheese", food_category="vegetables"), bundle)
    assert "dairy" in str(refusal.value)


def test_a_bundle_with_no_vocabulary_refuses_every_named_food():
    """Every bundle written before v1.54 omits the `food_items` section, and
    §10.2 keeps it optional so those still load. A request naming a food
    against one of them is refused rather than priced at the category -- a
    food this factor set has never heard of is a caller defect, unlike a food
    with no factor row, which is an ordinary expected state."""
    data = copy.deepcopy(_bundle_data())
    del data["food_items"]
    data["upstream"] = [row for row in data["upstream"] if row["food_item"] is None]
    older = FactorBundle.from_json(data)
    assert older.validate() == []

    assert _total(calculate(_request(None), older)) == Decimal("190.0000000000")
    with pytest.raises(UnknownCodeError):
        calculate(_request("cheese"), older)
