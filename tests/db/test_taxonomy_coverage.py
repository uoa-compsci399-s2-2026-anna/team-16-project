"""`GET /api/v1/taxonomy` offers only what the published set can price (§5.1, §6.1).

**The defect.** §2.1's three vocabulary tables carry no `factor_set_id` — a
factor set brings factors, not a vocabulary — so publishing one could not
narrow the calculator's form. A user who typed a quantity against a destination
the published set has no factors for got a silent zero, and the form gave no
sign which it was. Loading §10.3's ReFED fixture put a second, disjoint
vocabulary in the same tables and made it visible: 26 destinations offered
against a set that prices 12.

**Why "fewer rows" is not what these tests assert.** A count is satisfied by a
filter that drops the wrong rows, and this repository has lost five defects to
assertions that were true of something adjacent to the behaviour. Every test
here names codes and asserts membership in both directions: the covered ones
are **present**, the uncovered ones are **absent**, and `prevention` is present
even when the published set prices it nowhere at all.

`seeded_session`'s published `MOCK-v0` covers exactly `processing` /`dairy` and
the two destinations `prevention` and `landfill` — it carries a generic
upstream row, a `prevention` upstream override (O-7), and two downstream rows
for `landfill`, one per food category and one generic. That is a real factor
set in miniature and it is deliberately narrower than the seeded taxonomy, so
"the taxonomy is not the vocabulary" is the seed's normal state rather than
something a test has to arrange.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from db.models import (
    Destination,
    DestinationGroup,
    FactorDownstream,
    FactorSet,
    FactorSetStatus,
    FactorUpstream,
    FoodCategory,
    Metric,
    Sector,
    UnitPreset,
)
from db.repository import get_taxonomy, get_taxonomy_for_bundle


def codes(rows):
    return {row.code for row in rows}


def published_id(session):
    return session.scalar(
        select(FactorSet.id).where(FactorSet.status == FactorSetStatus.published)
    )


def row_id(session, model, code):
    return session.scalar(select(model.id).where(model.code == code))


# ------------------------------------------------------ the covered vocabulary


def test_the_taxonomy_is_exactly_what_the_published_set_covers(seeded_session):
    """Membership, spelled out, in all three tables.

    Equality rather than `issubset`: a rule that keeps the covered rows *and*
    everything else satisfies every "is present" assertion in this file, and
    is the bug.
    """
    snapshot = get_taxonomy(seeded_session)
    assert codes(snapshot.sectors) == {"processing"}
    assert codes(snapshot.food_categories) == {"standard_mix", "dairy"}
    assert codes(snapshot.destinations) == {"prevention", "landfill"}


@pytest.mark.parametrize(
    "code", ["animal_feed", "compost", "anaerobic_digestion", "not_harvested"]
)
def test_a_destination_the_published_set_cannot_price_is_absent(seeded_session, code):
    """Each of these is an active, seeded destination with no factor row in
    `MOCK-v0`. Offering it is offering a silent zero."""
    assert row_id(seeded_session, Destination, code) is not None, "seed changed"
    assert code not in codes(get_taxonomy(seeded_session).destinations)


def test_a_sector_and_a_food_category_with_no_upstream_row_are_absent(seeded_session):
    assert row_id(seeded_session, Sector, "primary_production") is not None
    assert row_id(seeded_session, FoodCategory, "vegetables") is not None
    snapshot = get_taxonomy(seeded_session)
    assert "primary_production" not in codes(snapshot.sectors)
    assert "vegetables" not in codes(snapshot.food_categories)


def test_a_destination_covered_only_by_the_generic_downstream_row_is_present(seeded_session):
    """§2.2's nullable `food_category_id` means "every food category", and it is
    how a per-tonne charge like the NZ waste levy is held. A coverage rule that
    ignored those rows would hide the levy's destination entirely."""
    group_id = row_id(seeded_session, DestinationGroup, "disposal")
    metric_id = row_id(seeded_session, Metric, "co2e")
    seeded_session.add(
        Destination(group_id=group_id, code="levy_only", name="Levy only", sort_order=900)
    )
    seeded_session.flush()
    seeded_session.add(
        FactorDownstream(
            factor_set_id=published_id(seeded_session),
            destination_id=row_id(seeded_session, Destination, "levy_only"),
            food_category_id=None,
            metric_id=metric_id,
            value_per_kg=Decimal("0.10"),
        )
    )
    seeded_session.flush()
    assert "levy_only" in codes(get_taxonomy(seeded_session).destinations)


def test_a_food_category_covered_only_by_a_downstream_row_is_present(seeded_session):
    """The two factor tables are read for food categories as well.

    A category with a downstream factor and no upstream one prices at zero
    upstream and non-zero downstream, which is a real figure — §2.2's lookup
    order treats a missing upstream row as zero, not as an error. Reading
    `factor_upstream` alone would hide a category the set can genuinely price.
    """
    seeded_session.add(FoodCategory(code="bakery_grains", name="Bakery", sort_order=40))
    seeded_session.flush()
    seeded_session.add(
        FactorDownstream(
            factor_set_id=published_id(seeded_session),
            destination_id=row_id(seeded_session, Destination, "landfill"),
            food_category_id=row_id(seeded_session, FoodCategory, "bakery_grains"),
            metric_id=row_id(seeded_session, Metric, "co2e"),
            value_per_kg=Decimal("1.10"),
        )
    )
    seeded_session.flush()
    assert "bakery_grains" in codes(get_taxonomy(seeded_session).food_categories)


def test_a_sector_covered_only_by_a_downstream_row_is_present(seeded_session):
    """v1.31. The two factor tables are read for sectors as well now.

    `factor_downstream.sector_id` is nullable and a non-NULL value scopes the
    row to one stage of the supply chain. A set may price a stage **downstream
    only** — a per-tonne disposal charge that differs by collection contract,
    with no upstream footprint of its own — and reading `factor_upstream`
    alone would drop that sector from the form while the rows pricing it sat
    in the database. That is the silent zero this whole filter exists to
    remove, arriving through the one table it did not read.

    `primary_production` is the seed's own sector and
    `test_a_sector_and_a_food_category_with_no_upstream_row_are_absent` above
    asserts it is *absent* before this row is added, so the two together show
    that the row is what changed the answer rather than the seed.
    """
    assert "primary_production" not in codes(get_taxonomy(seeded_session).sectors), (
        "seed changed: this sector must start uncovered for the test to mean anything"
    )
    seeded_session.add(
        FactorDownstream(
            factor_set_id=published_id(seeded_session),
            destination_id=row_id(seeded_session, Destination, "landfill"),
            sector_id=row_id(seeded_session, Sector, "primary_production"),
            food_category_id=None,
            metric_id=row_id(seeded_session, Metric, "co2e"),
            value_per_kg=Decimal("0.31"),
        )
    )
    seeded_session.flush()

    assert "primary_production" in codes(get_taxonomy(seeded_session).sectors)


def test_a_null_sector_downstream_row_covers_no_sector_at_all(seeded_session):
    """The other half, and the one a permissive implementation gets wrong.

    NULL means "every sector", so such a row is evidence about no particular
    sector and must not pull one onto the form. A `COALESCE`-style read, or a
    set-union that forgot to skip NULL, would put a `None` in the covered set
    and — depending on how it was compared — either crash or quietly cover
    everything. The seed's own generic downstream rows are already NULL here,
    so this asserts the state the deployed database is in.
    """
    seeded_session.add(
        Destination(
            group_id=row_id(seeded_session, DestinationGroup, "disposal"),
            code="every_sector_only", name="Every sector only", sort_order=901,
        )
    )
    seeded_session.flush()
    seeded_session.add(
        FactorDownstream(
            factor_set_id=published_id(seeded_session),
            destination_id=row_id(seeded_session, Destination, "every_sector_only"),
            sector_id=None,
            food_category_id=None,
            metric_id=row_id(seeded_session, Metric, "co2e"),
            value_per_kg=Decimal("0.02"),
        )
    )
    seeded_session.flush()

    #: The row is real and does its own job — it covers its destination — so
    #: this is not a test that passes because nothing was written.
    assert "every_sector_only" in codes(get_taxonomy(seeded_session).destinations)

    sectors = codes(get_taxonomy(seeded_session).sectors)
    assert "primary_production" not in sectors
    assert "consumer_hospitality" not in sectors
    #: The sector that *is* covered, from `factor_upstream`, still is — the
    #: assertion above is satisfied by a filter that returns nothing at all.
    assert "processing" in sectors


def test_a_destination_covered_only_by_an_upstream_row_is_present(seeded_session):
    """`factor_upstream.destination_id` became nullable in O-7 (v1.8) and a
    non-NULL value is a per-destination override. §2.2 states the column is not
    restricted to `prevention` and must not be, so a set may price a
    destination through it alone."""
    group_id = row_id(seeded_session, DestinationGroup, "reuse")
    seeded_session.add(
        Destination(group_id=group_id, code="upstream_only", name="Upstream only", sort_order=901)
    )
    seeded_session.flush()
    seeded_session.add(
        FactorUpstream(
            factor_set_id=published_id(seeded_session),
            sector_id=row_id(seeded_session, Sector, "processing"),
            food_category_id=row_id(seeded_session, FoodCategory, "dairy"),
            destination_id=row_id(seeded_session, Destination, "upstream_only"),
            metric_id=row_id(seeded_session, Metric, "co2e"),
            value_per_kg=Decimal("0.25"),
        )
    )
    seeded_session.flush()
    assert "upstream_only" in codes(get_taxonomy(seeded_session).destinations)


# --------------------------------------------------- the two structural rows


def test_prevention_survives_a_set_that_prices_it_nowhere(seeded_session):
    """The load-bearing test of this change.

    `prevention`'s factors are zero **by construction** — that is what makes it
    a 100% offset and what keeps the two scenarios mass-conserving (§6.2). So
    "has no factor row" is not evidence a set does not support it, which is the
    inference every other row here is subject to. Strip every row naming it
    from the published set and it must still be offered: without it the
    improvement panel has no way to express wasting less, and `net_benefit`
    stops being computable at all.
    """
    factor_set_id = published_id(seeded_session)
    prevention_id = row_id(seeded_session, Destination, "prevention")
    seeded_session.execute(
        delete(FactorUpstream).where(
            FactorUpstream.factor_set_id == factor_set_id,
            FactorUpstream.destination_id == prevention_id,
        )
    )
    seeded_session.execute(
        delete(FactorDownstream).where(
            FactorDownstream.factor_set_id == factor_set_id,
            FactorDownstream.destination_id == prevention_id,
        )
    )
    seeded_session.flush()
    snapshot = get_taxonomy(seeded_session)
    assert "prevention" in codes(snapshot.destinations)
    #: And its group with it — a destination naming a group the response omits
    #: is the one way this filter breaks a consumer reading both lists.
    prevention = next(row for row in snapshot.destinations if row.code == "prevention")
    assert prevention.group in codes(snapshot.destination_groups)


def test_the_hold_out_reads_the_flag_and_not_the_code(seeded_session):
    """The other half of the test above, and what proves the string is gone.

    Clear the tick on the row *called* `prevention` and it becomes an ordinary
    destination, subject to the same inference as every other row — so once its
    factor rows are gone it must disappear. A second row that carries the tick
    and no factor row at all must appear in its place. If the first survives,
    the filter is still reading the code.
    """
    factor_set_id = published_id(seeded_session)
    prevention = seeded_session.scalar(
        select(Destination).where(Destination.code == "prevention")
    )
    prevention.is_prevention = False
    seeded_session.add(
        Destination(
            group_id=prevention.group_id, code="waste_avoided",
            name="Waste avoided", is_prevention=True, sort_order=6,
        )
    )
    seeded_session.execute(
        delete(FactorUpstream).where(
            FactorUpstream.factor_set_id == factor_set_id,
            FactorUpstream.destination_id == prevention.id,
        )
    )
    seeded_session.execute(
        delete(FactorDownstream).where(
            FactorDownstream.factor_set_id == factor_set_id,
            FactorDownstream.destination_id == prevention.id,
        )
    )
    seeded_session.flush()

    offered = codes(get_taxonomy(seeded_session).destinations)
    assert "waste_avoided" in offered, "the flagged row is held out of the inference"
    assert "prevention" not in offered, (
        "an unflagged row named 'prevention' is an ordinary destination"
    )


def test_a_second_vocabularys_prevention_is_held_out_by_the_flag(seeded_session):
    """§10.3's `refed_prevention` is a prevention destination by every property
    that matters — 156 upstream and 156 downstream rows, every one of them
    zero — and was subject to an inference that cannot be true of it while the
    hold-out named one code."""
    prevention = seeded_session.scalar(
        select(Destination).where(Destination.code == "prevention")
    )
    seeded_session.add(
        Destination(
            group_id=prevention.group_id, code="refed_prevention",
            name="Prevention (ReFED)", is_prevention=True, sort_order=803,
        )
    )
    seeded_session.flush()

    assert "refed_prevention" in codes(get_taxonomy(seeded_session).destinations)


def test_the_flag_travels_to_the_front_end(seeded_session):
    """§6.1 puts `is_prevention` on the wire because `web/js/calculator.js`
    keeps a flagged destination off the current-waste list by reading it. A
    snapshot that dropped the field would leave that list offering a
    destination §6.2 answers 400 for."""
    rows = {row.code: row for row in get_taxonomy(seeded_session).destinations}
    assert rows["prevention"].is_prevention is True
    assert rows["landfill"].is_prevention is False


def test_the_standard_mix_survives_a_set_that_prices_it_nowhere(seeded_session):
    """§2.1 requires exactly one active `is_standard_mix` row and §6.2 resolves
    a null `food_category` to it. Filtering it out would leave a caller with no
    legal way to say "composition unknown" while the server went on resolving
    null to a code it was never offered.

    `MOCK-v0` prices `dairy` only, so the seed is already this case.
    """
    snapshot = get_taxonomy(seeded_session)
    standard = [row for row in snapshot.food_categories if row.is_standard_mix]
    assert [row.code for row in standard] == ["standard_mix"]


def test_the_standard_mix_invariant_is_still_about_the_table(seeded_session):
    """Counted over the active rows, not over the narrowed list.

    Counting the narrowed list makes the calculator answer 500 the day someone
    publishes a set the standard mix is not in — which is the state the
    deployed ReFED set is in right now.
    """
    get_taxonomy(seeded_session)  # does not raise, and the set prices no standard mix


# --------------------------------------------------------- groups and presets


def test_a_group_with_no_visible_destination_is_absent(seeded_session):
    """`recycle_recovery` holds `compost`, `anaerobic_digestion` and
    `not_harvested`, none of which `MOCK-v0` prices. An empty heading promises
    options that are not there."""
    snapshot = get_taxonomy(seeded_session)
    assert row_id(seeded_session, DestinationGroup, "recycle_recovery") is not None
    assert codes(snapshot.destination_groups) == {"reuse", "disposal"}


def test_no_destination_names_a_group_the_response_omits(seeded_session):
    snapshot = get_taxonomy(seeded_session)
    listed = codes(snapshot.destination_groups)
    assert {row.group for row in snapshot.destinations} <= listed


def test_a_preset_for_every_category_stays_and_one_naming_a_hidden_category_goes(
    seeded_session,
):
    seeded_session.add(
        UnitPreset(
            code="crate_vegetables",
            label="Vegetable crate",
            food_category_id=row_id(seeded_session, FoodCategory, "vegetables"),
            kg_per_unit=Decimal("9.0000"),
        )
    )
    seeded_session.flush()
    presets = codes(get_taxonomy(seeded_session).unit_presets)
    assert "bucket_20l_full" in presets, "a preset with no food category applies to all"
    assert "crate_vegetables" not in presets


# ------------------------------------------- a second, disjoint vocabulary


def load_second_vocabulary(session):
    """§10.3's shape in miniature: `refed_`-prefixed rows in the same tables,
    priced by a factor set of their own, with an explicit row per destination
    and no generic upstream row at all."""
    group_id = row_id(session, DestinationGroup, "disposal")
    reuse_id = row_id(session, DestinationGroup, "reuse")
    metric_id = row_id(session, Metric, "co2e")
    session.add_all([
        Sector(code="refed_us", name="United States (ReFED)", sort_order=800),
        FoodCategory(code="refed_produce", name="Produce (ReFED)", sort_order=801),
        Destination(group_id=group_id, code="refed_landfill", name="Landfill (ReFED)", sort_order=802),
        Destination(group_id=reuse_id, code="refed_prevention", name="Prevention (ReFED)", sort_order=803),
        FactorSet(version_label="REFED-TEST", status=FactorSetStatus.draft, is_mock=True),
    ])
    session.flush()
    other = session.scalar(select(FactorSet).where(FactorSet.version_label == "REFED-TEST"))
    sector_id = row_id(session, Sector, "refed_us")
    food_id = row_id(session, FoodCategory, "refed_produce")
    for code, value in (("refed_landfill", Decimal("2.5")), ("refed_prevention", Decimal("0"))):
        destination_id = row_id(session, Destination, code)
        session.add_all([
            FactorUpstream(
                factor_set_id=other.id,
                sector_id=sector_id,
                food_category_id=food_id,
                destination_id=destination_id,
                metric_id=metric_id,
                value_per_kg=value,
            ),
            FactorDownstream(
                factor_set_id=other.id,
                destination_id=destination_id,
                food_category_id=food_id,
                metric_id=metric_id,
                value_per_kg=value,
            ),
        ])
    session.flush()
    return other


def test_publishing_the_other_vocabulary_switches_the_whole_answer(seeded_session):
    """The owner's reproduction, in a test.

    One session, two publishes, and the answer is the other vocabulary — not
    the union of the two. `refed_prevention` arrives through the ordinary
    covered rule and needs no special case; the New Zealand `prevention` stays
    because it is held out of the inference by name, and that is stated rather
    than left for a reader to discover from a set difference.
    """
    other = load_second_vocabulary(seeded_session)
    before = get_taxonomy(seeded_session)
    assert "refed_landfill" not in codes(before.destinations), (
        "a draft set must not put its vocabulary on the public form"
    )

    from db.repository import publish_factor_set

    publish_factor_set(seeded_session, other.id, actor="tests")
    seeded_session.flush()

    after = get_taxonomy(seeded_session)
    assert codes(after.sectors) == {"refed_us"}
    assert codes(after.destinations) == {"refed_landfill", "refed_prevention", "prevention"}
    assert "landfill" not in codes(after.destinations)
    assert codes(after.food_categories) == {"standard_mix", "refed_produce"}


# ------------------------------------ what this change must NOT reach


def test_the_bundle_taxonomy_is_not_narrowed(seeded_session):
    """`get_taxonomy_for_bundle` is a different function with two consumers
    that both need the full vocabulary: the engine's dictionary of legal codes,
    and §6.3's factor export.

    Narrowing it would turn every code the form no longer offers from a zero
    into an `UNKNOWN_CODE` 400 — including for a browser tab holding a taxonomy
    fetched before the last publish.
    """
    bundle = get_taxonomy_for_bundle(seeded_session)
    assert {row["code"] for row in bundle["destinations"]} >= {
        "prevention", "landfill", "animal_feed", "compost", "anaerobic_digestion",
        "not_harvested",
    }
    assert {row["code"] for row in bundle["sectors"]} >= {
        "processing", "primary_production", "consumer_hospitality"
    }


def test_the_filter_is_one_functions_and_not_the_tables(seeded_session):
    """Staff must still see and edit every taxonomy row whatever is published —
    a row cannot be given its first factor if the panel has stopped listing it.

    `sqladmin` queries the models directly through `admin/modelviews.py`, so an
    ordinary model query is what the panel does. This is what fails if the
    filter is ever implemented as a mapper-level `with_loader_criteria`, a
    query event or a default `active`-style predicate, all of which would look
    correct in `get_taxonomy`'s own test and silently empty the panel.
    """
    get_taxonomy(seeded_session)
    assert codes(seeded_session.scalars(select(Destination)).all()) >= {
        "prevention", "landfill", "animal_feed", "compost", "anaerobic_digestion",
        "not_harvested",
    }
    assert len(seeded_session.scalars(select(Sector)).all()) == 3
    assert len(seeded_session.scalars(select(FoodCategory)).all()) == 3


def test_the_admin_package_does_not_reach_for_the_public_taxonomy():
    """The other half of the same guarantee, structurally.

    The moment a panel screen renders from `get_taxonomy`, it inherits the
    public filter and staff lose the rows they need to edit.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "admin"
    offenders = [
        path.name
        for path in root.rglob("*.py")
        if "get_taxonomy" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
