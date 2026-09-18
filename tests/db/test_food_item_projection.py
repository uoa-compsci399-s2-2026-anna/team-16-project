"""The item dimension crossing `db/` — the projection, coverage and the guards.

**The projection is the one that can produce a wrong number**, and it produces
it with no error anywhere. A staff member authors an item-level
`factor_upstream` row, the row is written, and `build_bundle_data` never
mentions it: the bundle carries the category average, the engine prices the
line at the category average, and the screen says the food was named. §10.2's
`food_items` section is **optional** in `FactorBundle.from_json` — deliberately,
so that every bundle written before v1.54 still loads — which is exactly what
lets the omission be silent instead of an exception. So both halves are pinned
here, and each fails on its own: `food_item` dropped from the upstream row, and
the `food_items` section dropped from the taxonomy.

**Nothing in this file seeds an item into any shared fixture.** Every test that
needs one writes it, inside its own transaction (`seeded_session` is rolled
back), so the repository's normal state stays "no item rows anywhere" and the
inertness of this landing is the suite's default rather than something a
separate test asserts.

**One thing this file deliberately does not assert**: that an item-level factor
*prices a line differently* through `engine.calculate`. The five-slot lookup key
is PR #100's (`feat/engine-item-lookup`), which is open; on this branch
`FactorBundle` still keys upstream on four slots, so an item row and its
category row collapse onto the same key. That collapse is loud rather than
silent — `validate()` reports the duplicate — and it is why this landing must
not reach a deployment ahead of #100. The projection is still right, and is what
is checked here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from db.errors import FactorSetStateError
from db.models import (
    FactorSet,
    FactorSetStatus,
    FactorUpstream,
    FoodCategory,
    FoodItem,
    Metric,
    Sector,
)
from db.repository import (
    _covered_by,
    build_bundle_data,
    find_item_rows_without_category_fallback,
    get_factor_export,
    get_published_factor_set_id,
    item_level_coverage,
    refuse_item_level_without_item_rows,
    refuse_item_rows_without_category_fallback,
)




def _id(session, model, code):
    return session.scalar(select(model.id).where(model.code == code))


def _add_item(session, code, name, category_code, **kwargs):
    item = FoodItem(
        code=code,
        name=name,
        food_category_id=_id(session, FoodCategory, category_code),
        **kwargs,
    )
    session.add(item)
    session.flush()
    return item


def _add_item_factor(session, factor_set_id, item, *, value, destination=None):
    """One item-level upstream row, on the sector/metric the seed prices."""
    row = FactorUpstream(
        factor_set_id=factor_set_id,
        sector_id=_id(session, Sector, "processing"),
        food_category_id=item.food_category_id,
        food_item_id=item.id,
        destination_id=destination,
        metric_id=_id(session, Metric, "co2e"),
        value_per_kg=Decimal(value),
    )
    session.add(row)
    session.flush()
    return row


# --- the projection --------------------------------------------------------


def test_the_bundle_carries_each_upstream_rows_food_item(seeded_session):
    """§10.2 (v1.54): every `upstream[]` row publishes a `food_item`, `null`
    for the category row that applies to every food in the category.

    Without the key an item factor is written, stored, and then priced as the
    category average by an engine that never saw it — a wrong number with
    nothing on screen or in a log to say so.

    `key in row` is asserted separately from the value, for the reason §10.2
    already gives about `upstream[].destination`: `row.get("food_item")` is
    `None` both when the row is the category average and when the projection
    forgot the field, and those are not the same thing.
    """
    published = get_published_factor_set_id(seeded_session)
    cheese = _add_item(seeded_session, "cheese", "Cheese", "dairy")
    _add_item_factor(seeded_session, published, cheese, value="3.4")

    data = build_bundle_data(seeded_session, published)

    assert all("food_item" in row for row in data["upstream"]), (
        "an upstream row published no `food_item` key at all, so an item "
        "factor cannot be told apart from the category average"
    )
    by_item = {}
    for row in data["upstream"]:
        by_item.setdefault(row["food_item"], []).append(row)

    assert set(by_item) == {None, "cheese"}, data["upstream"]
    assert by_item["cheese"][0]["value_per_kg"] == "3.4000000000"
    assert by_item["cheese"][0]["food_category"] == "dairy", (
        "an item row must still carry the category it refines"
    )
    #: The category rows keep their null — the projection must not fill the
    #: new column in with something.
    assert {Decimal(row["value_per_kg"]) for row in by_item[None]} == {
        Decimal("1.9"), Decimal("0"),
    }
    #: §1.1 — `code` crosses the layer boundary, never a primary key.
    assert all("food_item_id" not in row for row in data["upstream"])


def test_the_bundle_publishes_the_food_item_vocabulary(seeded_session):
    """§10.2's `food_items` section, and the parent each item belongs to.

    Fails on its own: a projection that carried `food_item` on every upstream
    row but published no vocabulary hands the engine a bundle in which
    `resolve_food_item()` refuses every named food as unknown, and
    `validate()` reports every item row as naming an item that is not in this
    bundle.
    """
    published = get_published_factor_set_id(seeded_session)
    _add_item(seeded_session, "cheese", "Cheese", "dairy", sort_order=20)
    _add_item(seeded_session, "carrots", "Carrots", "vegetables", sort_order=10)

    data = build_bundle_data(seeded_session, published)

    assert "food_items" in data, (
        "the bundle published no `food_items` section, so no item is "
        "reachable by the engine however many item factors exist"
    )
    rows = {row["code"]: row for row in data["food_items"]}
    assert rows["cheese"]["food_category"] == "dairy"
    assert rows["carrots"]["food_category"] == "vegetables"
    assert rows["cheese"]["name"] == "Cheese"
    #: Ordered the way every other taxonomy section is, `sort_order` then code.
    assert [row["code"] for row in data["food_items"]] == ["carrots", "cheese"]
    assert all("id" not in row for row in data["food_items"])


def test_a_retired_item_leaves_the_vocabulary(seeded_session):
    """`active` is how a taxonomy row leaves service, here as everywhere."""
    published = get_published_factor_set_id(seeded_session)
    _add_item(seeded_session, "cheese", "Cheese", "dairy")
    _add_item(seeded_session, "butter", "Butter", "dairy", active=False)

    data = build_bundle_data(seeded_session, published)

    assert {row["code"] for row in data["food_items"]} == {"cheese"}


def test_the_bundle_still_loads_and_validates_with_no_items_at_all(seeded_session):
    """The inert state, which is every database in existence today.

    An empty `food_items` section is not an absent one: `from_json` reads the
    key when it is there and every bundle written before v1.54 omits it, so
    both shapes have to load. This is the assertion that would fail if the new
    section were made *required* of the projection's consumers.
    """
    from engine.bundle import FactorBundle

    data = build_bundle_data(
        seeded_session, get_published_factor_set_id(seeded_session)
    )

    assert data["food_items"] == []
    assert all(row["food_item"] is None for row in data["upstream"])
    assert FactorBundle.from_json(data).validate() == []


def test_the_public_factor_export_tells_an_item_row_from_its_category_row(seeded_session):
    """§6.3's `GET /factors`, and a decision this test previously held the
    opposite of.

    The export is a **public document**: `tests/api/test_api.py` compares its
    key set against `tests/fixtures/factors.json`, and the methodology page
    renders its `upstream[]` rows to visitors. So the first answer here was to
    strip `food_item` and leave publishing the dimension to the API landing,
    and this test asserted that.

    **What changed the answer is that THIS landing is the one that lets a staff
    member author an item-level row.** From the first such row, a stripped
    export publishes two rows identical in every key it prints, pricing
    differently — a transparency page actively misleading about the very
    numbers it exists to disclose. A contract bump is the smaller harm.

    So the key is emitted only where it has a value: with no item rows the
    export is byte-identical to what the fixture pins, and the day one exists
    the two rows are told apart. The same optional-when-present shape §10.2
    gives `upstream[].food_item` in the bundle.

    `food_items` is still NOT carried. The vocabulary is a section of its own
    and giving the item a public *name* is the API landing's to do; this is
    only about not printing two rows that claim to be the same row.
    """
    published = get_published_factor_set_id(seeded_session)
    cheese = _add_item(seeded_session, "cheese", "Cheese", "dairy")
    _add_item_factor(seeded_session, published, cheese, value="3.4")

    export = get_factor_export(seeded_session)

    assert "food_items" not in export, (
        "the vocabulary is the API landing's to publish; this landing only stops "
        "two rows claiming to be one"
    )

    carried = [row for row in export["upstream"] if "food_item" in row]
    assert len(carried) == 1, (
        f"exactly the authored item row should carry the key, got {len(carried)}: "
        f"{carried}"
    )
    assert carried[0]["food_item"] == "cheese"
    assert carried[0]["value_per_kg"] == "3.4000000000"

    #: The other half, and the half that keeps this landing inert: every row
    #: that is not an item row is shaped exactly as it was, so a fixture
    #: comparison over a set with no item rows sees nothing new.
    category_rows = [row for row in export["upstream"] if "food_item" not in row]
    assert category_rows, "the seed's category rows should still be here"
    assert all(set(row) == set(category_rows[0]) for row in category_rows), (
        "a category row gained or lost a key"
    )


# --- coverage: `_covered_by`, and the parent-covered rule ------------------


def test_an_item_is_covered_by_its_parent_category(seeded_session):
    """§5.1, and **the one rule here that is not "the factor tables say so"**.

    A sector, a destination or a food category is priceable only if some row
    names it. An item is priceable when its own rows exist **or** when its
    parent category is covered, because §2.2's lookup falls through a food
    with no row of its own to the category average — a defined, meaningful
    number rather than the silent zero the other three dimensions would get.

    `cheese` below has no factor row anywhere and is still covered; `carrots`
    is in a category this set prices nowhere and is not.
    """
    published = get_published_factor_set_id(seeded_session)
    cheese = _add_item(seeded_session, "cheese", "Cheese", "dairy")
    carrots = _add_item(seeded_session, "carrots", "Carrots", "vegetables")

    covered = _covered_by(seeded_session, published)

    assert cheese.id in covered["food_items"], (
        "an item whose parent category this set prices must be offered: the "
        "lookup falls back to that category's average"
    )
    assert carrots.id not in covered["food_items"]


def test_an_item_with_its_own_rows_is_covered_however_it_is_filed(seeded_session):
    """The other half of the union, and the only case it is not redundant in.

    An item row carries a NOT NULL `food_category_id`, so an item priced under
    its own parent is covered by the parent clause anyway. The clause earns
    its place on the incoherent row — a factor filed under a category the item
    does not belong to — which is storable (the CHECK constraint says only
    that an item may not arrive without a category) and which the engine
    reports rather than prices.
    """
    published = get_published_factor_set_id(seeded_session)
    carrots = _add_item(seeded_session, "carrots", "Carrots", "vegetables")
    seeded_session.add(
        FactorUpstream(
            factor_set_id=published,
            sector_id=_id(seeded_session, Sector, "processing"),
            #: Filed under dairy, which this set prices; the item is a vegetable.
            food_category_id=_id(seeded_session, FoodCategory, "dairy"),
            food_item_id=carrots.id,
            destination_id=None,
            metric_id=_id(seeded_session, Metric, "co2e"),
            value_per_kg=Decimal("0.4"),
        )
    )
    seeded_session.flush()

    assert carrots.id in _covered_by(seeded_session, published)["food_items"]


def test_coverage_counts_priced_items_against_the_whole_vocabulary(seeded_session):
    """What the factor-set screen shows beside the flag. Releasing step 2.5 is
    a judgement about how much of the vocabulary actually carries numbers, and
    a boolean cannot carry that."""
    published = get_published_factor_set_id(seeded_session)
    cheese = _add_item(seeded_session, "cheese", "Cheese", "dairy")
    _add_item(seeded_session, "butter", "Butter", "dairy")
    _add_item(seeded_session, "carrots", "Carrots", "vegetables")

    assert item_level_coverage(seeded_session, published) == (0, 3)

    _add_item_factor(seeded_session, published, cheese, value="3.4")

    assert item_level_coverage(seeded_session, published) == (1, 3)

    #: A second metric for the same food is still one food priced, not two.
    seeded_session.add(
        FactorUpstream(
            factor_set_id=published,
            sector_id=_id(seeded_session, Sector, "primary_production"),
            food_category_id=cheese.food_category_id,
            food_item_id=cheese.id,
            destination_id=None,
            metric_id=_id(seeded_session, Metric, "co2e"),
            value_per_kg=Decimal("2.2"),
        )
    )
    seeded_session.flush()

    assert item_level_coverage(seeded_session, published) == (1, 3)


# --- the flag guard (design §3.5, soft) -----------------------------------


def test_the_flag_is_refused_on_a_set_with_no_item_rows(seeded_session):
    """A set nobody has authored an item factor for must not claim item-level
    precision: every food on the new screen would be priced at its category's
    average while the screen asked a more specific question than the numbers
    can answer."""
    published = get_published_factor_set_id(seeded_session)
    seeded_session.get(FactorSet, published).item_level_enabled = True
    seeded_session.flush()

    with pytest.raises(FactorSetStateError) as excinfo:
        refuse_item_level_without_item_rows(seeded_session, published)

    assert "item-level" in str(excinfo.value).lower()


def test_one_item_row_is_enough_for_the_flag(seeded_session):
    """The guard is **soft** by design (§3.5), and this is the assertion that
    keeps it soft. Full coverage is 19 items x 6 sectors x 5 metrics = 570
    rows and is unreachable from any data that will exist; one item row is
    coherent because every other food falls back to its category average."""
    published = get_published_factor_set_id(seeded_session)
    cheese = _add_item(seeded_session, "cheese", "Cheese", "dairy")
    _add_item_factor(seeded_session, published, cheese, value="3.4")
    seeded_session.get(FactorSet, published).item_level_enabled = True
    seeded_session.flush()

    refuse_item_level_without_item_rows(seeded_session, published)


def test_the_flag_being_off_is_never_refused(seeded_session):
    """The state of every set in every deployment today."""
    refuse_item_level_without_item_rows(
        seeded_session, get_published_factor_set_id(seeded_session)
    )


# --- the fallback guard, which #100 could not enforce ---------------------


def test_an_item_row_with_no_category_row_to_fall_back_on_is_refused(seeded_session):
    """**The trap #100 named for this landing.** §2.2's chain has no
    silent-zero *as long as the data has a category row to fall back to*, and
    that is a property of the data, not of the chain.

    A set carrying an item row for some `(sector, food_category, metric)` and
    no category-level row for the same tuple sends every *other* food in that
    category — and the category itself — to `Decimal("0")` rather than to an
    average. That is the O-7 failure shape exactly: right for one food, wrong
    for its neighbours, with no error anywhere.

    `primary_production`/`dairy`/`co2e` is a tuple the seeded set prices
    nowhere, so the item row below is the only row for it.
    """
    published = get_published_factor_set_id(seeded_session)
    cheese = _add_item(seeded_session, "cheese", "Cheese", "dairy")
    seeded_session.add(
        FactorUpstream(
            factor_set_id=published,
            sector_id=_id(seeded_session, Sector, "primary_production"),
            food_category_id=cheese.food_category_id,
            food_item_id=cheese.id,
            destination_id=None,
            metric_id=_id(seeded_session, Metric, "co2e"),
            value_per_kg=Decimal("3.4"),
        )
    )
    seeded_session.flush()

    assert find_item_rows_without_category_fallback(seeded_session, published) == [
        ("primary_production", "dairy", "co2e")
    ]
    with pytest.raises(FactorSetStateError) as excinfo:
        refuse_item_rows_without_category_fallback(seeded_session, published)

    message = str(excinfo.value)
    #: The refusal has to say which row to add, not that something is wrong.
    assert "primary_production/dairy/co2e" in message
    assert "food item" in message.lower()


def test_an_item_row_beside_its_category_row_is_accepted(seeded_session):
    """The healthy shape: refine a tuple the set already prices.

    `processing`/`dairy`/`co2e` carries the seed's own generic row at 1.9, so
    a food with no row of its own lands on 1.9 rather than on zero.
    """
    published = get_published_factor_set_id(seeded_session)
    cheese = _add_item(seeded_session, "cheese", "Cheese", "dairy")
    _add_item_factor(seeded_session, published, cheese, value="3.4")

    assert find_item_rows_without_category_fallback(seeded_session, published) == []
    refuse_item_rows_without_category_fallback(seeded_session, published)


def test_a_destination_specific_category_row_is_not_the_fallback(seeded_session):
    """The half of the rule most easily got wrong.

    §2.2's candidate 4 — the row that catches a food with no row of its own —
    is `(NULL item, NULL destination)`. A category row that names a
    destination is candidate 2 and only answers *at that destination*, so a
    set whose only category row for the tuple is the `prevention` override
    still drops every other destination to zero.

    The seed's `processing`/`dairy`/`co2e` carries both, so this test removes
    the generic one and keeps the `prevention` override.
    """
    published = get_published_factor_set_id(seeded_session)
    generic = seeded_session.scalars(
        select(FactorUpstream).where(
            FactorUpstream.factor_set_id == published,
            FactorUpstream.destination_id.is_(None),
        )
    ).all()
    assert generic, "the seed no longer carries a generic upstream row"
    for row in generic:
        seeded_session.delete(row)
    seeded_session.flush()

    cheese = _add_item(seeded_session, "cheese", "Cheese", "dairy")
    _add_item_factor(seeded_session, published, cheese, value="3.4")

    assert find_item_rows_without_category_fallback(seeded_session, published) == [
        ("processing", "dairy", "co2e")
    ]


def test_a_set_with_no_item_rows_reports_nothing(seeded_session):
    """The inert state again: the guard must be silent on every set that
    exists today, or this landing is not inert."""
    published = get_published_factor_set_id(seeded_session)

    assert find_item_rows_without_category_fallback(seeded_session, published) == []
    refuse_item_rows_without_category_fallback(seeded_session, published)


def test_publishing_refuses_both_item_level_defects(seeded_session):
    """The guards where the other publish-time guards live, not only as
    functions somebody has to remember to call. §5.2."""
    from db.repository import publish_factor_set

    draft = seeded_session.scalar(
        select(FactorSet).where(FactorSet.status == FactorSetStatus.draft)
    )
    seeded_session.scalar(
        select(FactorSet).where(FactorSet.status == FactorSetStatus.published)
    ).status = FactorSetStatus.archived
    draft.item_level_enabled = True
    seeded_session.flush()

    with pytest.raises(FactorSetStateError):
        publish_factor_set(seeded_session, draft.id, actor="kim")
