"""The client's own figures, transcribed verbatim, and nothing else.

Every number in this file is copied from the two tables Kai Commitment /
Rawtec supplied ("Impact values for EFC of foods (not including disposal)"
and "Impact values of food from bin to destination"), converted for reading
and reproduced here as literal ``Decimal`` values. Nothing is estimated,
rounded beyond the client's own printed precision, or filled in.

This module holds data, not logic. ``build_upstream_factors_draft.py`` is
where the aggregation and the ReFED stage split happen; splitting the two
means the numbers a reader needs to check against the client's document sit
in one place, undisturbed by the arithmetic that consumes them.

WHERE THIS CAME FROM
---------------------
The client's own file is not committed to this repository (project rule:
nothing under ``traxie data/`` is ever committed). What follows is transcribed
from an HTML conversion of the client's own *Rawtec calculations* document,
table by table, row by row.

**Two revisions of that document have now been received.** The first was
transcribed on 2026-09-05. The second was received on **2026-09-21** and is
what this file now holds. The entire substantive difference between them is
table 2's CO2-eq column -- see "TABLE 2" below, which records what was wrong
with the first, what was decided about it, and what replaced it.

The 2026-09-21 document was re-parsed and compared against the committed
2026-09-05 transcription cell by cell before any edit was made. Table 1
(26 rows x category, food, life cycle, CO2-eq, water, land) showed **zero
differences**, and so did table 2's destination labels, "Life cycle covered"
column and water column. All seventeen of table 2's CO2-eq values changed.
``PRIOR_REVISION_UNCHANGED_CELLS`` below freezes the cells that did not move,
and ``build_upstream_factors_draft.py`` asserts against it on every build, so
that measurement is not a claim in prose that nothing re-checks.

(One presentational difference, carrying no value: the 2026-09-21 table 2
prints two full-width grouping rows, "Food Waste Destinations:" before
Anaerobic Digestion and "Non-Food Waste Destinations:" before Charity
Redistribution. They hold no data and are not transcribed. Which destinations
this system treats as waste is ``destination_group.is_waste``, a configurable
table, and is not taken from the client's headings.)

TABLE 1 -- "Impact values for EFC of foods (not including disposal)"
----------------------------------------------------------------------
Twenty rows under seven client-defined categories (Bakery, Dairy, Eggs,
Drinks, Produce, Meat & Seafood, "Grains, Nuts, Seeds" -- the last one also
carries Fats, Sauces/Spreads/Dips, Herbs/Spices, Snack Foods, Sweeteners,
Other Food Types and the mixed-product aggregate). Each row carries a
"Life cycle covered" column, almost always *Farm to Supermarket Shelf* and
occasionally *Farm to Fork* -- ``life_cycle`` below is that column, kept
because it is not uniform and a reader comparing this to the client's totals
needs to know which rows already include consumer-stage steps and which do
not. This table is usable as-is; nothing in it is flagged as corrupted.

TABLE 2 -- "Impact values of food from bin to destination"
-------------------------------------------------------------
Seventeen destination rows.

**Its CO2-eq column was corrupt in the 2026-09-05 revision, and the client
replaced it on 2026-09-21.** The defect and its resolution are both recorded
here, in full, because a reader who finds only the corrected column has no way
to tell a defect that was found and fixed from one nobody ever noticed.

*What was wrong.* In the 2026-09-05 document, table 2's CO2-eq column was a
**verbatim copy of table 1's CO2-eq column, in table-1 row order** -- 1.46
(Bread), 4.28 (Bakery), 10.13 (Cheese), 1.51 (Milk), 4.95 (Cream), 11.39
(Butter), 3.29 (Yoghurt), 1.19 (Other dairy), 4.93 (Eggs), 2.01
(Drinks/Beverages), 1.78 (Fruit), 1.82 (Vegetable), 20.28 (Red Meat), 10.62
(Pork), 3.98 (Poultry), 11.63 (Other meat), 5.94 (Seafood). Every one of the
seventeen destination rows held the food row at the same position in table 1,
not a value about that destination. Read as destination factors those numbers
inverted the tool's central message: Compost was priced at 10.13 kg CO2-eq/kg
against Landfill's 4.95, so composting came out twice as bad as landfilling,
and every recovery route was a cost rather than a saving.

*What was decided.* The repository owner ruled on **2026-09-05**, ahead of the
client meeting, that the column would be used exactly as printed regardless:
the client's own figures were themselves a draft, no better column existed,
and inventing one here would have been worse than carrying the client's. The
set it fed is ``is_mock = true`` and carries the placeholder banner, but it
was **not** confined to a drawer -- it was published on 2026-09-06, on the
local development stack and on the 10.0.0.130 deployment, on the owner's
explicit instruction (``docs/upstream-factors-draft.md`` §1). So the inverted
column was live, behind a placeholder banner, for the fortnight between the
two revisions. That is the reason this correction is not merely tidy-up.

*What replaced it.* The client supplied a revised document on **2026-09-21**
whose only substantive change is this column, and the seventeen values are now
destination figures rather than food figures. They read as a proper
bin-to-destination balance: mostly small, several negative (an avoided-burden
credit -- anaerobic digestion -0.04, meat rendering -0.56, composting -0.11,
energy-from-waste -0.12, stock feed and pet food -0.19, upcycling -0.15),
0.60 for every landfill-bound row, a flat 0.00 for the two on-farm breakdown
rows and for sewer, and 0.04 for the two redistribution rows. Compost at
-0.11 against Landfill's 0.60 now prices composting as a saving, which is the
direction the underlying question actually runs.

*What this does not settle.* O-1 stays open. These are still the client's
draft figures, ``is_mock`` stays ``true``, the set stays unpublished, and the
placeholder banner stays mandatory. The correction removes a defect; it does
not make the data confirmed.

The water column is not implicated in that copy-paste -- it was identical in
both revisions, cell for cell -- and it holds seventeen
distinct values, mostly negative (an offset -- composting, anaerobic
digestion, land application, stock feed and upcycling all return water to a
usable cycle rather than consuming it), a small positive value for the
disposal-side rows, a genuine zero for sewer, and a published ``null`` for
incineration. That shape looks like a real, distinct measurement rather than
a second copy of another column, which is the basis for treating it
differently from the CO2-eq column above -- but it has not been independently
verified either, and nothing here claims more confidence for it than "printed
by the client and internally consistent".

UNITS
-----
Table 1: kg CO2-eq per kg, litres of water per kg, tonnes per hectare of
land. Table 2: kg CO2-eq per kg, litres of water per kg (no land column).
Both already match this system's per-kilogram factor convention -- no unit
conversion is applied anywhere in this package.
"""

from __future__ import annotations

from decimal import Decimal
from typing import NamedTuple


class FoodRow(NamedTuple):
    category: str          # the client's own category heading
    food: str               # the client's own row label
    life_cycle: str          # the client's own "Life cycle covered" column
    co2e_per_kg: Decimal     # kg CO2-eq / kg
    water_l_per_kg: Decimal  # L water / kg
    land_t_per_ha: Decimal   # t / ha (not used -- no `land` metric exists)


class DestinationRow(NamedTuple):
    destination: str        # the client's own destination label
    life_cycle: str          # the client's own "Life cycle covered" column
    co2e_per_kg: Decimal     # kg CO2-eq / kg -- the 2026-09-21 revision's
                              # column. SEE MODULE DOCSTRING for what the
                              # 2026-09-05 revision printed here instead.
    water_l_per_kg: Decimal | None  # L water / kg; None where the client's
                                     # own table prints "null" (Incineration)


def _d(value: str) -> Decimal:
    return Decimal(value)


# Table 1, transcribed row by row in the client's own order.
TABLE1: list[FoodRow] = [
    FoodRow("Bakery", "Bread", "Farm to Supermarket Shelf", _d("1.46"), _d("1608.00"), _d("9.36")),
    FoodRow("Bakery", "Bakery", "Farm to Supermarket Shelf", _d("4.28"), _d("1608.00"), _d("9.36")),
    FoodRow("Dairy", "Cheese", "Farm to Supermarket Shelf", _d("10.13"), _d("3968.00"), _d("0.95")),
    FoodRow("Dairy", "Milk", "Farm to Supermarket Shelf", _d("1.51"), _d("420.39"), _d("9.74")),
    FoodRow("Dairy", "Cream", "Farm to Supermarket Shelf", _d("4.95"), _d("3968.00"), _d("0.95")),
    FoodRow("Dairy", "Butter", "Farm to Supermarket Shelf", _d("11.39"), _d("3968.00"), _d("0.95")),
    FoodRow("Dairy", "Yoghurt", "Farm to Supermarket Shelf", _d("3.29"), _d("3968.00"), _d("0.95")),
    FoodRow("Dairy", "Other dairy", "Farm to Supermarket Shelf", _d("1.19"), _d("420.39"), _d("9.74")),
    FoodRow("Eggs", "Eggs", "Farm to Supermarket Shelf", _d("4.93"), _d("1781.00"), _d("26.00")),
    FoodRow("Drinks", "Drinks/Beverages (excluding dairy)", "Farm to Fork", _d("2.01"), _d("438.00"), _d("5.95")),
    FoodRow("Produce", "Fruit", "Farm to Supermarket Shelf", _d("1.78"), _d("1360.00"), _d("11.81")),
    FoodRow("Produce", "Vegetable", "Farm to Fork", _d("1.82"), _d("510.71"), _d("59.40")),
    FoodRow("Meat & Seafood", "Red Meat", "Farm to Supermarket Shelf", _d("20.28"), _d("13971.04"), _d("0.22")),
    FoodRow("Meat & Seafood", "Pork", "Farm to Supermarket Shelf", _d("10.62"), _d("6925.00"), _d("0.58")),
    FoodRow("Meat & Seafood", "Poultry", "Farm to Supermarket Shelf", _d("3.98"), _d("660.00"), _d("57.48")),
    FoodRow("Meat & Seafood", "Other meat", "Farm to Supermarket Shelf", _d("11.63"), _d("7185.35"), _d("19.42")),
    FoodRow("Meat & Seafood", "Seafood", "Farm to Supermarket Shelf", _d("5.94"), _d("3603.00"), _d("1.19")),
    FoodRow("Grains, Nuts, Seeds", "Grains", "Farm to Supermarket Shelf", _d("2.56"), _d("2885.78"), _d("9.06")),
    # Nuts and seeds: water (13971.04) and land (0.22) are identical to Red
    # Meat's row above. Reported as a likely second copy-paste in the client's
    # own table -- see the provenance document. Not corrected, not omitted.
    FoodRow("Grains, Nuts, Seeds", "Nuts and seeds", "Farm to Supermarket Shelf", _d("2.65"), _d("13971.04"), _d("0.22")),
    FoodRow("Grains, Nuts, Seeds", "Fats", "Farm to Supermarket Shelf", _d("2.39"), _d("935.36"), _d("35.61")),
    FoodRow("Grains, Nuts, Seeds", "Sauces Spreads Dips", "Farm to Supermarket Shelf", _d("3.13"), _d("510.71"), _d("59.40")),
    FoodRow("Grains, Nuts, Seeds", "Herbs/Spices", "Farm to Fork", _d("1.60"), _d("786.38"), _d("45.68")),
    FoodRow("Grains, Nuts, Seeds", "Snack Foods and desserts", "Farm to Supermarket Shelf", _d("4.56"), _d("6090.50"), _d("0.93")),
    FoodRow("Grains, Nuts, Seeds", "Sweeteners", "Farm to Fork", _d("3.70"), _d("1724.00"), _d("82.00")),
    FoodRow("Grains, Nuts, Seeds", "Other Food Types", "Farm to Supermarket Shelf", _d("6.16"), _d("4194.18"), _d("17.59")),
    FoodRow("Grains, Nuts, Seeds", "General mixed food product", "Farm to Supermarket Shelf", _d("6.04"), _d("3161"), _d("18")),
]

# Table 2, transcribed row by row in the client's own order, from the
# client's revised document of 2026-09-21. The CO2-eq column is the corrected
# one -- see the module docstring for what the 2026-09-05 revision printed in
# its place and why that column could not be used as a destination factor.
TABLE2: list[DestinationRow] = [
    DestinationRow("Anaerobic Digestion", "Bin to AD Implementation", _d("-0.04"), _d("-1.50")),
    DestinationRow("BioBased", "Bin to Meat Rendering Implementation", _d("-0.56"), _d("-2.20")),
    DestinationRow("Compost", "Bin to Compost used in Hort", _d("-0.11"), _d("-2.40")),
    DestinationRow("Incineration", "Bin to Energy From Waste", _d("-0.12"), None),
    DestinationRow("Landfill", "Bin to Landfill", _d("0.60"), _d("0.06")),
    DestinationRow("Landspread", "Breakdown on Farm", _d("0.00"), _d("-2.40")),
    DestinationRow("Not Harvested", "Breakdown on Farm", _d("0.00"), _d("-1.20")),
    DestinationRow("Other Food Waste", "Bin to Landfill", _d("0.60"), _d("0.06")),
    DestinationRow("Refuse", "Bin to Landfill", _d("0.60"), _d("0.06")),
    DestinationRow("Sewer/Wastewater Treatment", "Bin to Biosolids", _d("0.00"), _d("0.00")),
    DestinationRow("Unknown Food Waste", "Bin to Landfill", _d("0.60"), _d("0.06")),
    DestinationRow("Charity Redistribution", "Bin to Fork", _d("0.04"), _d("0.06")),
    DestinationRow("Commercial Redistribution", "Bin to Fork", _d("0.04"), _d("0.06")),
    DestinationRow("Stock Feed", "Bin to Farm", _d("-0.19"), _d("-1.95")),
    DestinationRow("Upcycled", "Bin to Product", _d("-0.15"), _d("-2.80")),
    DestinationRow("Unknown Repurposed", "Bin to Farm", _d("-0.19"), _d("-1.95")),
    DestinationRow("Pet Food", "Bin to Farm", _d("-0.19"), _d("-1.95")),
]

#: Every cell of the client's data that did **not** move between the
#: 2026-09-05 revision and the 2026-09-21 one, frozen exactly as the
#: 2026-09-05 transcription held it.
#:
#: This exists because the 2026-09-21 update was made on a measurement -- that
#: table 1 is untouched and only table 2's CO2-eq column moved -- and a
#: measurement nobody re-runs decays into a claim.
#: ``_assert_only_table2_co2_moved()`` in ``build_upstream_factors_draft.py``
#: checks ``TABLE1`` and table 2's labels, life-cycle column and water column
#: against this record on every build and refuses to write a factor set if any
#: of them has drifted. It replaces the old ``TABLE2_CO2_COPY_SOURCE`` check,
#: which asserted a copy-paste that no longer exists; the guarantee is the
#: same in kind -- the transcription cannot quietly change without the build
#: saying so.
#:
#: Updating this record is not forbidden. It is meant to be updated when a
#: further revision genuinely moves one of these cells -- deliberately, in the
#: same commit, with the reason written down. What it prevents is a cell
#: moving without anyone noticing.
#:
#: Rows: (category, food, life_cycle, co2e, water, land), as strings so this
#: record is compared against the same text a reader sees in the document
#: rather than against a value already parsed by the code under test.
PRIOR_REVISION_TABLE1: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("Bakery", "Bread", "Farm to Supermarket Shelf", "1.46", "1608.00", "9.36"),
    ("Bakery", "Bakery", "Farm to Supermarket Shelf", "4.28", "1608.00", "9.36"),
    ("Dairy", "Cheese", "Farm to Supermarket Shelf", "10.13", "3968.00", "0.95"),
    ("Dairy", "Milk", "Farm to Supermarket Shelf", "1.51", "420.39", "9.74"),
    ("Dairy", "Cream", "Farm to Supermarket Shelf", "4.95", "3968.00", "0.95"),
    ("Dairy", "Butter", "Farm to Supermarket Shelf", "11.39", "3968.00", "0.95"),
    ("Dairy", "Yoghurt", "Farm to Supermarket Shelf", "3.29", "3968.00", "0.95"),
    ("Dairy", "Other dairy", "Farm to Supermarket Shelf", "1.19", "420.39", "9.74"),
    ("Eggs", "Eggs", "Farm to Supermarket Shelf", "4.93", "1781.00", "26.00"),
    ("Drinks", "Drinks/Beverages (excluding dairy)", "Farm to Fork", "2.01", "438.00", "5.95"),
    ("Produce", "Fruit", "Farm to Supermarket Shelf", "1.78", "1360.00", "11.81"),
    ("Produce", "Vegetable", "Farm to Fork", "1.82", "510.71", "59.40"),
    ("Meat & Seafood", "Red Meat", "Farm to Supermarket Shelf", "20.28", "13971.04", "0.22"),
    ("Meat & Seafood", "Pork", "Farm to Supermarket Shelf", "10.62", "6925.00", "0.58"),
    ("Meat & Seafood", "Poultry", "Farm to Supermarket Shelf", "3.98", "660.00", "57.48"),
    ("Meat & Seafood", "Other meat", "Farm to Supermarket Shelf", "11.63", "7185.35", "19.42"),
    ("Meat & Seafood", "Seafood", "Farm to Supermarket Shelf", "5.94", "3603.00", "1.19"),
    ("Grains, Nuts, Seeds", "Grains", "Farm to Supermarket Shelf", "2.56", "2885.78", "9.06"),
    ("Grains, Nuts, Seeds", "Nuts and seeds", "Farm to Supermarket Shelf", "2.65", "13971.04", "0.22"),
    ("Grains, Nuts, Seeds", "Fats", "Farm to Supermarket Shelf", "2.39", "935.36", "35.61"),
    ("Grains, Nuts, Seeds", "Sauces Spreads Dips", "Farm to Supermarket Shelf", "3.13", "510.71", "59.40"),
    ("Grains, Nuts, Seeds", "Herbs/Spices", "Farm to Fork", "1.60", "786.38", "45.68"),
    ("Grains, Nuts, Seeds", "Snack Foods and desserts", "Farm to Supermarket Shelf", "4.56", "6090.50", "0.93"),
    ("Grains, Nuts, Seeds", "Sweeteners", "Farm to Fork", "3.70", "1724.00", "82.00"),
    ("Grains, Nuts, Seeds", "Other Food Types", "Farm to Supermarket Shelf", "6.16", "4194.18", "17.59"),
    ("Grains, Nuts, Seeds", "General mixed food product", "Farm to Supermarket Shelf", "6.04", "3161", "18"),
)

#: The same record for table 2, covering every column except the CO2-eq one
#: that the 2026-09-21 revision replaced:
#: (destination, life_cycle, water -- ``None`` where the client prints "null").
PRIOR_REVISION_TABLE2_EXCEPT_CO2: tuple[tuple[str, str, str | None], ...] = (
    ("Anaerobic Digestion", "Bin to AD Implementation", "-1.50"),
    ("BioBased", "Bin to Meat Rendering Implementation", "-2.20"),
    ("Compost", "Bin to Compost used in Hort", "-2.40"),
    ("Incineration", "Bin to Energy From Waste", None),
    ("Landfill", "Bin to Landfill", "0.06"),
    ("Landspread", "Breakdown on Farm", "-2.40"),
    ("Not Harvested", "Breakdown on Farm", "-1.20"),
    ("Other Food Waste", "Bin to Landfill", "0.06"),
    ("Refuse", "Bin to Landfill", "0.06"),
    ("Sewer/Wastewater Treatment", "Bin to Biosolids", "0.00"),
    ("Unknown Food Waste", "Bin to Landfill", "0.06"),
    ("Charity Redistribution", "Bin to Fork", "0.06"),
    ("Commercial Redistribution", "Bin to Fork", "0.06"),
    ("Stock Feed", "Bin to Farm", "-1.95"),
    ("Upcycled", "Bin to Product", "-2.80"),
    ("Unknown Repurposed", "Bin to Farm", "-1.95"),
    ("Pet Food", "Bin to Farm", "-1.95"),
)

#: The 2026-09-05 revision's own table 2 CO2-eq column -- the copy of table 1
#: described in the module docstring. It is kept so the defect's record can be
#: checked rather than merely read, and so the before/after on a real
#: calculation can be reproduced without the withdrawn document.
#: **Nothing builds a factor from this.** ``build_upstream_factors_draft.py``
#: asserts that ``TABLE2`` no longer carries any of these values.
WITHDRAWN_2026_09_05_TABLE2_CO2: dict[str, str] = {
    "Anaerobic Digestion": "1.46",
    "BioBased": "4.28",
    "Compost": "10.13",
    "Incineration": "1.51",
    "Landfill": "4.95",
    "Landspread": "11.39",
    "Not Harvested": "3.29",
    "Other Food Waste": "1.19",
    "Refuse": "4.93",
    "Sewer/Wastewater Treatment": "2.01",
    "Unknown Food Waste": "1.78",
    "Charity Redistribution": "1.82",
    "Commercial Redistribution": "20.28",
    "Stock Feed": "10.62",
    "Upcycled": "3.98",
    "Unknown Repurposed": "11.63",
    "Pet Food": "5.94",
}


def food_row(food: str) -> FoodRow:
    for row in TABLE1:
        if row.food == food:
            return row
    raise KeyError(f"No table 1 row named {food!r}")


def destination_row(destination: str) -> DestinationRow:
    for row in TABLE2:
        if row.destination == destination:
            return row
    raise KeyError(f"No table 2 row named {destination!r}")
