"""The food-item vocabulary, and the factors the deployed mock set gives it.

Step 2.5 (`spec.md` §3.3) is unreachable without two things that live in
different files and have no test in common: the vocabulary `admin/seed.py`
creates, and the `item_level_enabled` switch plus item-level factor rows that
`docker/mock-factors.json` carries. A seed with no switch shows nothing; a
switch with no vocabulary shows an empty screen.

**What is pinned here is the reasoning, not the rows.** A list of forty-seven
codes asserted against itself proves only that somebody typed it twice. Each
test below states the property the list has to have and fails when the property
goes, which is what survives somebody adding a food.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from admin.seed import FOOD_CATEGORIES, FOOD_ITEM_SOURCES, FOOD_ITEMS

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
    """**This is why twenty of the client's twenty-six rows are here and six are not.**

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


def test_every_food_is_the_client_s_or_names_where_it_came_from():
    """**Nothing reaches the global vocabulary anonymously.**

    Until v1.72 this test said something simpler -- every food is a row of the
    client's table 1 -- and that was the right rule while the vocabulary was
    exactly the twenty rows the client subdivided. It could not survive the
    package that gave the other five categories foods, because the client
    supplies one row for each of Fruit, Vegetable, Seafood, Nuts and seeds and
    Drinks/Beverages and that row IS the category.

    So the rule is now the one that was actually load-bearing: a food is a
    client row, **or** it has an entry in `FOOD_ITEM_SOURCES` saying who chose
    it and why. `food_item` is global taxonomy shared by every factor set and
    every stored submission, and the thing this file is guarding against is the
    48 `refed_*` rows that a draft loader wrote into `food_category` -- a pile
    of vocabulary nobody can attribute. A name with no provenance fails here.
    """
    client = {name.strip().casefold() for name in _client_rows()}
    #: Two names are shortened for the screen rather than renamed: the client's
    #: "Sauces Spreads Dips" and "Herbs/Spices" read as punctuation errors in a
    #: checkbox label. Mapped here rather than silently exempted.
    ALIASES = {
        "sauces, spreads and dips": "sauces spreads dips",
        "herbs and spices": "herbs/spices",
        "red meat": "red meat",
    }
    unattributed = []
    for code, name, _, _ in FOOD_ITEMS:
        key = ALIASES.get(name.strip().casefold(), name.strip().casefold())
        if key in client or FOOD_ITEM_SOURCES.get(code, "").strip():
            continue
        unattributed.append((code, name))
    assert not unattributed, (
        "these foods are in no client row and name no source in "
        f"FOOD_ITEM_SOURCES: {unattributed}"
    )


def test_no_source_note_describes_a_food_that_is_not_seeded():
    """The mirror of the test above, and it catches the other half of a rename.

    A `FOOD_ITEM_SOURCES` key with no row is provenance for a food nobody can
    choose, which reads on the page as attribution and is not: renaming a code
    in `FOOD_ITEMS` and forgetting the mapping leaves the new code unattributed
    (caught above) and the old note pointing at nothing (caught here).
    """
    seeded = {code for code, _, _, _ in FOOD_ITEMS}
    orphans = sorted(set(FOOD_ITEM_SOURCES) - seeded)
    assert not orphans, f"these source notes describe no seeded food: {orphans}"


def test_a_source_note_says_which_of_the_two_provenances_it_is():
    """**A sentence that does not name its origin is not provenance.**

    §0's rule for this round was that every added name says where it came from,
    and there are exactly two places it can have come from: a product row of
    Poore & Nemecek (2018), already transcribed in
    `data/upstream-factors-draft/public_land_use_source_data.py`, or the team's
    own judgement about what a New Zealand staff member would type. Both are
    acceptable and they are not interchangeable -- one can be checked against a
    file in this repository and the other cannot -- so a reader has to be able
    to tell them apart without reading the prose closely.
    """
    vague = [
        code
        for code, note in FOOD_ITEM_SOURCES.items()
        if "Poore & Nemecek" not in note and "Team's judgement" not in note
    ]
    assert not vague, (
        "these notes name neither Poore & Nemecek (2018) nor the team's "
        f"judgement, so a reader cannot tell which kind of claim they are: {vague}"
    )


def test_every_poore_and_nemecek_note_names_a_row_that_exists():
    """The half of the provenance that **can** be checked, checked.

    `public_land_use_source_data.py` transcribes all thirty-eight published
    product labels and raises on a misspelling rather than defaulting, for the
    reason its own docstring gives. A note quoting `'Root Vegtables'` would
    look like a citation and be a typo, and nothing else in the tree would ever
    read it -- these notes are prose that no code consumes.
    """
    import sys

    directory = str(ROOT / "data" / "upstream-factors-draft")
    if directory not in sys.path:
        sys.path.insert(0, directory)
    from public_land_use_source_data import LAND_M2_PER_KG  # noqa: E402

    quoted = re.compile(r"product row '([^']+)'")
    wrong = []
    for code, note in FOOD_ITEM_SOURCES.items():
        for label in quoted.findall(note):
            if label not in LAND_M2_PER_KG:
                wrong.append((code, label))
    assert not wrong, (
        "these notes quote a product label Our World in Data does not "
        f"publish: {wrong}"
    )


def test_the_five_categories_the_client_did_not_subdivide_now_have_foods():
    """**The package's own reason for existing, stated as a property.**

    `web/js/calculator.js::itemStepOffered()` requires a *chosen* category to
    have foods, so before v1.72 a visitor who ticked only Fruit, Vegetables,
    Seafood, Nuts and edible seeds or Beverages was shown no step 2.5 at all --
    not an empty one, no step. The client's table gives one row for each of
    those five and that row is the category itself, which is why they were
    empty rather than because anybody decided they should be.

    Asserted per category rather than on a total, because a total is satisfied
    by piling every new food into one of them.
    """
    counts: dict[str, int] = {}
    for _, _, parent, _ in FOOD_ITEMS:
        counts[parent] = counts.get(parent, 0) + 1
    empty = [
        category
        for category in ("fruit", "vegetables", "seafood", "nuts_seeds", "beverages")
        if not counts.get(category)
    ]
    assert not empty, f"step 2.5 is still hidden for a visitor who ticks only: {empty}"


def test_no_category_offers_more_foods_than_a_visitor_can_scan():
    """A `<select>` or checkbox group nobody reads is worse than no subdivision.

    The ceiling is the largest group this vocabulary has ever had -- `staples`
    at seven, which is where the client's seven homeless rows landed -- so this
    is a measurement of the screen that already exists rather than a number
    somebody liked. It is the guard against the failure mode the `refed_*`
    categories are the cautionary example of: a source's taxonomy imported
    wholesale, correct in every row and unusable as a list.
    """
    counts: dict[str, int] = {}
    for _, _, parent, _ in FOOD_ITEMS:
        counts[parent] = counts.get(parent, 0) + 1
    too_many = sorted((c, n) for c, n in counts.items() if n > 7)
    assert not too_many, f"these categories are too long to scan on step 2.5: {too_many}"


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
