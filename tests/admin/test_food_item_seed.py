"""The food-item vocabulary, and the factors the deployed mock set gives it.

Step 2.5 (`spec.md` §3.3) is unreachable without two things that live in
different files and have no test in common: the vocabulary `admin/seed.py`
creates, and the `item_level_enabled` switch plus item-level factor rows that
`docker/mock-factors.json` carries. A seed with no switch shows nothing; a
switch with no vocabulary shows an empty screen.

**What is pinned here is the reasoning, not the rows.** A list of twenty codes
asserted against itself proves only that somebody typed it twice. Each test
below states the property the list has to have and fails when the property
goes, which is what survives somebody adding a food.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from admin.seed import FOOD_CATEGORIES, FOOD_ITEMS

ROOT = Path(__file__).resolve().parents[2]
MOCK_FACTORS = json.loads((ROOT / "docker" / "mock-factors.json").read_text(encoding="utf-8"))

#: `data/upstream-factors-draft/rawtec_source_data.py`'s `TABLE1`, by the client's
#: own food name. Read from that module rather than retyped, so this file cannot
#: drift from the transcription the category factors are also built from.
def _client_rows() -> dict[str, tuple[Decimal, Decimal]]:
    import sys

    directory = str(ROOT / "data" / "upstream-factors-draft")
    if directory not in sys.path:
        sys.path.insert(0, directory)
    from rawtec_source_data import TABLE1  # noqa: E402

    return {row.food: row for row in TABLE1}


# --------------------------------------------------------------------------
# the vocabulary
# --------------------------------------------------------------------------


def test_every_food_names_a_category_the_same_seed_creates():
    """A food whose parent is absent is a `food_item` row the seed cannot
    write -- `food_category_id` is NOT NULL -- and the failure would be an
    integrity error during deployment rather than a message anyone could
    read."""
    categories = {code for code, _, _, _ in FOOD_CATEGORIES}
    orphans = sorted({parent for _, _, parent, _ in FOOD_ITEMS} - categories)
    assert not orphans, f"these foods name a category the seed does not create: {orphans}"


def test_no_food_is_a_synonym_for_its_own_category():
    """**This is why the vocabulary is twenty rows and not twenty-six.**

    Six of the client's table-1 rows ARE the category they sit under: Fruit,
    Vegetable, Seafood, Nuts and seeds, Drinks/Beverages and General mixed food
    product. Offering "Fruit -> Fruit" on step 2.5 asks a visitor to refine an
    answer into itself, and it would put a food on the screen whose own factor
    row could only ever equal its parent's.

    Asserted on the NAMES rather than on a list of excluded codes, so adding
    `("fruit", "Fruit", "fruit", 10)` fails here rather than shipping.
    """
    names = {code: name for code, name, _, _ in FOOD_CATEGORIES}
    same = [
        (code, parent)
        for code, name, parent, _ in FOOD_ITEMS
        if name.strip().casefold() == names.get(parent, "").strip().casefold()
    ]
    assert not same, f"these foods only repeat their own category: {same}"


def test_eggs_is_under_staples_and_deliberately_not_under_dairy():
    """**The placement most likely to be "corrected" by a future reader.**

    The client's own table draws Eggs directly beneath the Dairy block, so
    `dairy` looks like the obvious home. It is the wrong one, and the reason is
    a number rather than a taxonomy argument: a parent category is the FALLBACK
    for every metric the client did not supply per food, `ch4` among them, and
    `dairy`'s methane is ruminant. Filing a poultry product under ruminant
    methane returns a systematically high figure to a visitor who has just
    asked a more specific question.

    A dedicated `eggs` category is the right long-term answer and is not taken
    yet -- it moves the taxonomy, `NZ_TO_REFED_FOOD_SHAPE`, the factor draft
    builder and every test asserting a category count, while open item O-5 has
    not settled whether there are eight categories or nine. When that lands,
    this test is the one to change, and the docstring is the reason to read
    first.
    """
    parents = {code: parent for code, _, parent, _ in FOOD_ITEMS}
    assert parents["eggs"] == "staples", parents["eggs"]
    assert parents["eggs"] != "dairy"


def test_every_food_comes_from_the_client_s_own_table():
    """Nothing is invented into the vocabulary. Each row is a table-1 row, so
    a food on step 2.5 is one the client asked about -- and one the category
    factors were averaged from, which is what makes the fallback meaningful
    rather than arbitrary."""
    client = {name.strip().casefold() for name in _client_rows()}
    #: Two names are shortened for the screen rather than renamed: the client's
    #: "Sauces Spreads Dips" and "Herbs/Spices" read as punctuation errors in a
    #: checkbox label. Mapped here rather than silently exempted.
    ALIASES = {
        "sauces, spreads and dips": "sauces spreads dips",
        "herbs and spices": "herbs/spices",
        "red meat": "red meat",
    }
    missing = []
    for code, name, _, _ in FOOD_ITEMS:
        key = ALIASES.get(name.strip().casefold(), name.strip().casefold())
        if key not in client:
            missing.append((code, name))
    assert not missing, f"these foods are in no client row: {missing}"


def test_the_codes_and_sort_orders_are_unique_within_a_category():
    codes = [code for code, _, _, _ in FOOD_ITEMS]
    assert len(codes) == len(set(codes)), "duplicate food codes"
    seen: dict[str, set[int]] = {}
    for _, _, parent, order in FOOD_ITEMS:
        assert order not in seen.setdefault(parent, set()), (parent, order)
        seen[parent].add(order)


# --------------------------------------------------------------------------
# the factors the deployed set gives them
# --------------------------------------------------------------------------


def test_the_mock_set_releases_step_two_point_five():
    """Without this the screen is unreachable however many foods are seeded:
    `item_level_enabled` is what `GET /taxonomy` reports and what the front end
    branches on (contract v1.58)."""
    assert MOCK_FACTORS["item_level_enabled"] is True


def test_the_loader_defaults_the_switch_off():
    """Every factor file written before v1.58 omits the key, and a set that
    released step 2.5 because nobody said otherwise would ask a finer question
    than its factors can answer. Read out of the source because the default
    lives in the call, not in a constant."""
    source = (ROOT / "docker" / "seed_mock_factors.py").read_text(encoding="utf-8")
    assert 'data.get("item_level_enabled", False)' in source


@pytest.mark.parametrize("metric", ["co2e", "water"])
def test_a_category_is_the_unweighted_mean_of_its_own_foods(metric):
    """**The property that keeps the fallback and the item telling one story.**

    The client's nine category factors are the unweighted mean of their table-1
    rows, so a food with no row of its own falls back to a figure it helped
    produce. The mock set's own dairy figure is golden `case_01`'s, not the
    client's mean -- so the item rows here are the client's RELATIVITIES scaled
    to this set's level, which preserves the property instead of breaking it.

    Using the client's figures raw would have made the category row and the
    item rows disagree about the same food: pick Cheese and get 10.13, decline
    to pick and get 1.9, with nothing on screen saying why. Design §8.2 names
    that as the thing to avoid.

    The tolerance is one unit in DECIMAL(20,10)'s last place per row, times the
    six rows -- the rounding that the storage scale makes unavoidable, not a
    slack allowance.
    """
    upstream = MOCK_FACTORS["upstream"]
    category = next(
        row for row in upstream
        if row.get("food_category") == "dairy"
        and row.get("food_item") is None
        and row.get("destination") is None
        and row["metric"] == metric
    )
    items = [
        row for row in upstream
        if row.get("food_category") == "dairy"
        and row.get("food_item") is not None
        and row["metric"] == metric
    ]
    assert len(items) == 6, [row.get("food_item") for row in items]

    mean = sum((Decimal(row["value_per_kg"]) for row in items), Decimal(0)) / len(items)
    drift = abs(mean - Decimal(category["value_per_kg"]))
    assert drift <= Decimal("0.0000000006"), (
        f"{metric}: the six item rows average {mean}, the category row says "
        f"{category['value_per_kg']}"
    )


def test_the_item_rows_differ_from_each_other():
    """A set of item rows that all equalled their category would pass the mean
    test above and teach a visitor nothing -- the dimension would be wired and
    inert. Cheese against Other dairy is the client's own spread."""
    values = {
        row["food_item"]: Decimal(row["value_per_kg"])
        for row in MOCK_FACTORS["upstream"]
        if row["metric"] == "co2e" and row.get("food_item")
    }
    assert len(set(values.values())) > 1, values
    assert values["cheese"] > values["other_dairy"] * 3, values


def test_every_item_row_says_it_is_a_placeholder():
    """Open item O-1. Step 2.5 makes the calculator ask a more specific
    question and therefore return a more authoritative-looking placeholder
    (design §9), so the provenance has to travel on the row itself -- the
    `is_mock` banner says the set is mock, not what this particular number is
    derived from."""
    for row in MOCK_FACTORS["upstream"]:
        if not row.get("food_item"):
            continue
        assert "PLACEHOLDER" in (row.get("source_note") or ""), row
        assert row.get("data_quality"), row
