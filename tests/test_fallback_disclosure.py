"""Which row priced the line, and what the interface is allowed to say. §3, §4.2.

Contract v1.59. `UpstreamBasis` has existed since v1.56 with its own docstring
saying what it is for — *the fallback disclosure the interface will render* —
and **nothing called it**: `engine/calculate.py` used `bundle.upstream()`, the
value alone, and threw the basis away. A dimension is not disclosed by an enum
nobody reads.

Two things are pinned here and they fail separately:

1. **`BreakdownRow.upstream_basis` is the winning candidate, per line and per
   metric**, because that is the granularity at which the answer varies — the
   destination outranks the item (§2.2), so one entry's `prevention` line is
   category-priced while its `landfill` line is not. A roll-up alone could not
   say that, and a totals-level row carries `None` because a sum across
   entries was priced by no single row.

2. **`EntryResult.item_basis` is the roll-up the copy branches on**, computed
   once in the engine. Three surfaces render this sentence — the results page,
   its plain-text export and the PDF — and a roll-up performed in each of them
   is the same submission described three ways.

**The `MIXED` case is the one worth reading twice.** `prevention`'s zero is
stored as a category-level, destination-specific row, which is the shape that
closes O-7 — so an entry that moves mass to prevention has a category-priced
line *however well the set prices its food*. That makes `MIXED` ordinary
rather than alarming, and it is why `is_disclosed` is `CATEGORY` alone. A
disclosure that fired on `MIXED` would fire on nearly every submission that
used the calculator's headline feature.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from engine.bundle import FactorBundle
from engine.calculate import calculate
from engine.types import (
    CalculationRequest,
    EntryInput,
    ItemBasis,
    ScenarioLine,
    UpstreamBasis,
)
from tests.test_item_level_pricing import _bundle_data


@pytest.fixture
def bundle():
    """The same three upstream rows `test_item_level_pricing` uses.

    `cheese` carries an every-destination row of its own, `butter` is declared
    and never priced, and the prevention zero sits at category level. Between
    them those three rows reach every member of both enums this file asserts
    on, which is why the fixture is shared rather than copied: a second bundle
    that drifted from it would make the two files disagree about which row
    wins without either of them failing.
    """
    loaded = FactorBundle.from_json(_bundle_data())
    assert loaded.validate() == [], loaded.validate()
    return loaded


def _entry(food_item, current, alternative=None):
    return EntryInput(
        sector_code="processing",
        food_category_code="dairy",
        food_item_code=food_item,
        current=tuple(
            ScenarioLine(destination_code=code, qty_kg=Decimal(qty))
            for code, qty in current
        ),
        alternative=None if alternative is None else tuple(
            ScenarioLine(destination_code=code, qty_kg=Decimal(qty))
            for code, qty in alternative
        ),
    )


def _run(bundle, *entries):
    return calculate(CalculationRequest(entries=tuple(entries)), bundle)


def _bases(scenario):
    return [row.upstream_basis for row in scenario.metrics["co2e"].by_destination]


# --------------------------------------------------------------------------
# 1. the row says which candidate answered
# --------------------------------------------------------------------------


def test_a_food_with_its_own_row_is_priced_by_that_row_and_says_so(bundle):
    """`cheese` has an every-destination row, so §2.2's candidate 3 wins.

    The value is asserted beside the member deliberately: a basis that named
    a row the figure did not come from would be worse than no basis at all,
    and only the pair together says the two agree.
    """
    result = _run(bundle, _entry("cheese", [("landfill", "100.000")]))
    row = result.entries[0].current.metrics["co2e"].by_destination[0]

    assert row.upstream_basis is UpstreamBasis.ITEM_EVERY_DESTINATION
    assert row.upstream == Decimal("3.4000000000")


def test_a_food_with_no_row_of_its_own_is_priced_by_its_category_and_says_so(bundle):
    """`butter` is declared and never priced -- the state nearly every food
    will be in for the life of this project. Candidate 4, the category
    average, which is a defined number and not a silent zero."""
    result = _run(bundle, _entry("butter", [("landfill", "100.000")]))
    row = result.entries[0].current.metrics["co2e"].by_destination[0]

    assert row.upstream_basis is UpstreamBasis.CATEGORY_EVERY_DESTINATION
    assert row.upstream == Decimal("1.9000000000")


def test_the_prevention_line_of_a_well_priced_food_is_still_category_level(bundle):
    """O-7's zero is a **category-level, destination-specific** row, and it
    beats `cheese`'s own every-destination row because the destination
    outranks the item.

    This is the row that makes `MIXED` ordinary, and the reason the basis is
    carried per line rather than only per entry: one entry, two lines, two
    different answers about where the figure came from.
    """
    result = _run(
        bundle,
        _entry("cheese", [("landfill", "100.000"), ("prevention", "50.000")]),
    )

    assert _bases(result.entries[0].current) == [
        UpstreamBasis.ITEM_EVERY_DESTINATION,
        UpstreamBasis.CATEGORY_AT_DESTINATION,
    ]


def test_a_line_no_row_prices_at_all_reports_absent(bundle):
    """§2.2's fifth step. `vegetables` is in the vocabulary and nothing
    prices it, so the chain runs out -- and `absent` is a different statement
    from `category_every_destination`: one says the category answered, the
    other says nothing did and the line was valued at zero."""
    entry = EntryInput(
        sector_code="processing",
        food_category_code="vegetables",
        current=(ScenarioLine(destination_code="landfill", qty_kg=Decimal("100.000")),),
        alternative=None,
    )
    result = _run(bundle, entry)
    row = result.entries[0].current.metrics["co2e"].by_destination[0]

    assert row.upstream_basis is UpstreamBasis.ABSENT
    assert row.upstream == Decimal("0.0000000000")


def test_a_totals_level_row_carries_no_basis(bundle):
    """Two entries, one destination, two different candidate rows: `cheese`
    at its own and `butter` at the category's. The rolled-up row is one row
    summed from both, so naming either would describe neither -- the same
    objection that leaves `upstream` and `downstream` at zero there.

    Asserted with two entries that genuinely disagree. A single-entry
    assertion would pass against an implementation that copied the row's
    basis up, which is exactly the implementation this rules out.
    """
    result = _run(
        bundle,
        _entry("cheese", [("landfill", "100.000")]),
        _entry("butter", [("landfill", "100.000")]),
    )

    assert _bases(result.entries[0].current) == [UpstreamBasis.ITEM_EVERY_DESTINATION]
    assert _bases(result.entries[1].current) == [UpstreamBasis.CATEGORY_EVERY_DESTINATION]
    assert _bases(result.totals.current) == [None]


# --------------------------------------------------------------------------
# 2. the entry-level roll-up, which is what a surface branches on
# --------------------------------------------------------------------------


def test_an_entry_that_names_no_food_discloses_nothing(bundle):
    """Every entry written before v1.58, and every entry today. `None` is not
    a fallback: nobody asked for a food, so there is nothing to say about one
    -- and a sentence shown to a visitor who never chose a food would be a
    caveat about a choice they did not make."""
    result = _run(bundle, _entry(None, [("landfill", "100.000")]))

    assert result.entries[0].item_basis is ItemBasis.NOT_APPLICABLE
    assert not result.entries[0].item_basis.is_disclosed


def test_a_food_priced_only_at_its_category_is_the_disclosure(bundle):
    """The case the whole revision exists for: *this is the Dairy average,
    not Butter*."""
    result = _run(bundle, _entry("butter", [("landfill", "100.000")]))

    assert result.entries[0].item_basis is ItemBasis.CATEGORY
    assert result.entries[0].item_basis.is_disclosed


def test_a_food_priced_at_itself_throughout_discloses_nothing(bundle):
    """Every lookup that could have used `cheese` did. Nothing to disclose,
    and `is_disclosed` is what says so rather than three surfaces each
    comparing against a list of members."""
    result = _run(bundle, _entry("cheese", [("landfill", "100.000")]))

    assert result.entries[0].item_basis is ItemBasis.ITEM
    assert not result.entries[0].item_basis.is_disclosed


def test_the_prevention_line_makes_a_well_priced_food_mixed_and_still_quiet(bundle):
    """**The reason `is_disclosed` is `CATEGORY` alone.**

    `cheese` is priced by its own row at landfill and by the category's zero
    at prevention, because O-7's override is category-level. That is `MIXED`
    -- and `MIXED` must not raise the sentence, or every submission that uses
    the calculator's headline feature carries a caveat about a row that is
    deliberately category-level.
    """
    result = _run(
        bundle,
        _entry("cheese", [("landfill", "100.000"), ("prevention", "50.000")]),
    )

    assert result.entries[0].item_basis is ItemBasis.MIXED
    assert not result.entries[0].item_basis.is_disclosed


def test_the_alternative_scenario_counts_towards_the_roll_up(bundle):
    """Both scenarios, not just `current`.

    Both sets of figures are on the results page and in both exports, so a
    roll-up that looked only at `current` would put a claim above an
    alternative it does not cover. Here `current` is `cheese` at its own row
    throughout -- `ITEM` on its own -- and the alternative moves the mass to
    `prevention`, which is category-priced. A roll-up over `current` alone
    reads `ITEM`; over both it reads `MIXED`.
    """
    result = _run(
        bundle,
        _entry(
            "cheese",
            [("landfill", "100.000")],
            alternative=[("prevention", "100.000")],
        ),
    )

    assert _bases(result.entries[0].current) == [UpstreamBasis.ITEM_EVERY_DESTINATION]
    assert _bases(result.entries[0].alternative) == [UpstreamBasis.CATEGORY_AT_DESTINATION]
    assert result.entries[0].item_basis is ItemBasis.MIXED


def test_one_entrys_fallback_is_not_another_entrys(bundle):
    """The roll-up is per entry, and a submission that named two foods can
    disclose one of them and not the other. Asserted together, because a
    single-entry test passes against an implementation that rolled the whole
    submission up into one answer and put it on every entry."""
    result = _run(
        bundle,
        _entry("cheese", [("landfill", "100.000")]),
        _entry("butter", [("landfill", "100.000")]),
    )

    assert result.entries[0].item_basis is ItemBasis.ITEM
    assert result.entries[1].item_basis is ItemBasis.CATEGORY


def test_the_food_is_echoed_beside_the_basis_so_the_sentence_can_name_it(bundle):
    """The disclosure reads *"%(food)s is priced at the %(category)s
    average"*, so the response has to carry the food it is about. `item_basis`
    without `food_item_code` is a caveat that cannot name its subject."""
    result = _run(bundle, _entry("butter", [("landfill", "100.000")]))

    assert result.entries[0].food_item_code == "butter"
    assert result.entries[0].item_basis is ItemBasis.CATEGORY
