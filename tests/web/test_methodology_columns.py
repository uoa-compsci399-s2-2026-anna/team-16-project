"""The documentation page publishes what §6.3 exports, column for column.

`methodology.js` is the only public surface where a factor can say where it
came from and what it applies to, and §2.2's whole argument for the provenance
columns is that a calculator which cannot say that cannot be defended. A
published table missing one of a row's *scope* columns is the same failure in a
different place: with the ReFED comparison set loaded it prints 1,728 rows of
which five at a time are identical in every visible field and differ only in
the number.

**Why this reads the source rather than a rendered page.** The browser files in
this directory point at the running stack on :18080, which serves a built
image; a change to `web/js/` is invisible to them until the image is rebuilt,
and rebuilding is not something a test may do to a deployment somebody is
using. So the columns are asserted here, against the file, and the rendered
result was checked by hand in Chromium at 390 and 1278 CSS pixels in English,
Thai and Arabic.

**Anchored to one section.** The naive check — "does the file mention
`row?.sector`" — passes on the *upstream* table, which has had a sector column
since it was written. Every assertion below is made against the slice of source
between one section's heading id and the start of the next, so a column in the
wrong table fails.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SOURCE = (Path(__file__).resolve().parents[2] / "web" / "js" / "methodology.js").read_text(
    encoding="utf-8"
)


def section(heading_id: str) -> str:
    """The `makeCollectionSection(...)` call for one heading, and nothing else.

    Sliced from the heading id to the next `fragment.append(` so that a column
    belonging to the neighbouring table cannot satisfy an assertion made about
    this one.
    """
    start = SOURCE.index(f"'{heading_id}'")
    tail = SOURCE[start:]
    end = tail.find("fragment.append(")
    assert end > 0, f"{heading_id} is the last section; the slice would run to EOF"
    return tail[:end]


def test_the_slicing_this_module_depends_on_actually_separates_the_sections():
    """The helper above is the whole reason these tests are not fooled by the
    upstream table. If it ever returns the file, everything else here passes
    for the wrong reason."""
    upstream = section("upstream-heading")
    downstream = section("downstream-heading")

    assert "downstream-heading" not in upstream
    assert "upstream-heading" not in downstream
    assert len(upstream) < len(SOURCE) / 2
    #: Something only the upstream table has, to prove the slice is that table.
    assert "All destinations" in upstream
    assert "All destinations" not in downstream


def test_the_downstream_table_publishes_the_sector_of_every_row():
    """v1.31. `factor_downstream.sector_id` is nullable and NULL means "every
    sector", so the column has to render a value in both states — a blank cell
    reads as missing data rather than as a scope."""
    downstream = section("downstream-heading")

    assert "row?.sector" in downstream, (
        "the published downstream table does not show which sector a row "
        "applies to; with a set that prices by sector its rows become "
        "indistinguishable"
    )
    assert "t('All sectors')" in downstream, (
        "a row that applies to every sector would render as an empty cell"
    )
    #: Ordered as the export orders them: destination, then the two scopes.
    assert downstream.index("row?.sector") < downstream.index("row?.food_category")


def test_the_downstream_table_states_which_of_the_two_scopes_wins():
    """§4.1's order, on the page that publishes the rows it applies to.

    Two columns each show a scope and neither can say which gives way, so the
    sentence above the table must. Asserted on the substance rather than on the
    presence of a sentence: "the most specific wins" is true of three of the
    four candidates and silent on the one pair that needed a decision.
    """
    downstream = section("downstream-heading")

    assert "naming a sector and a row naming only a food category" in downstream
    assert "the one naming a sector is used" in downstream


@pytest.mark.parametrize("field", ["destination", "sector", "food_category",
                                   "metric", "value_per_kg", "source_note",
                                   "data_quality"])
def test_every_field_section_6_3_publishes_on_a_downstream_row_reaches_the_page(field):
    """§6.3's downstream row has seven fields and the table has seven columns.

    Parameterised so a future field added to the export and forgotten here
    fails by name rather than by a count that somebody adjusts.
    """
    assert f"row?.{field}" in section("downstream-heading")


def test_the_upstream_table_publishes_the_food_of_every_row():
    """v1.58, and the gap #102 named as this landing's.

    §6.3 carries `upstream[].food_item` on the rows that have one — the export
    was changed for this page specifically. Without the column, a set that
    prices cheese at 3.4 and dairy at 1.9 publishes two rows identical in
    sector, food category, destination and metric, differing only in the
    number: the reader is shown a factor table that contradicts itself and
    given nothing to resolve it with.
    """
    upstream = section("upstream-heading")

    assert "row?.food_item" in upstream, (
        "the published upstream table does not say which food a row applies "
        "to; an item-level row and the category row it refines print as two "
        "identical rows with different values"
    )
    assert "t('All foods in this category')" in upstream, (
        "a row that applies to every food in its category would render as an "
        "empty cell, which reads as missing data rather than as a scope. "
        "§6.3 omits `food_item` entirely on those rows, so the fallback is "
        "doing real work here rather than guarding a theoretical null"
    )
    #: Ordered as `factor_upstream`'s own columns are, and as the export
    #: orders them: sector, then the category, then the food that refines it.
    assert upstream.index("row?.sector") < upstream.index("row?.food_category")
    assert upstream.index("row?.food_category") < upstream.index("row?.food_item")
    assert upstream.index("row?.food_item") < upstream.index("row?.destination")


def test_the_upstream_table_states_which_of_its_two_scopes_wins():
    """The same rule the downstream table's intro carries, for the pair design
    §2 settled — and settled *against* the specific one, which is the reason
    it cannot be left to "the most specific wins".

    A food's generic row and its category's prevention-specific row can both
    apply to a prevented line. Item-first re-opens O-7: the line picks up the
    food's generic factor instead of the category's zero, and the offset stops
    being whole. Destination-first is the rule, and a reader cannot infer it
    from two columns that each say "all".
    """
    upstream = section("upstream-heading")

    assert "naming a destination and a row naming only a food" in upstream
    assert "the one naming a destination is used" in upstream
