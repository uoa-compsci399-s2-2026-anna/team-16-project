"""The client's own figures, transcribed verbatim, and nothing else.

Every number in this file is copied from the two tables Kai Commitment /
Rawtec supplied ("Impact values for EFC of foods (not including disposal)"
and "Impact values of food from bin to destination"), converted to Markdown
for reading and reproduced here as literal ``Decimal`` values. Nothing is
estimated, rounded beyond the client's own printed precision, or filled in.

This module holds data, not logic. ``build_upstream_factors_draft.py`` is
where the aggregation and the ReFED stage split happen; splitting the two
means the numbers a reader needs to check against the client's document sit
in one place, undisturbed by the arithmetic that consumes them.

WHERE THIS CAME FROM
---------------------
The client's own file is not committed to this repository (project rule:
nothing under ``traxie data/`` is ever committed). What follows is transcribed
from ``rawtec.md``, itself a Markdown conversion of the client's document
supplied to the team, converted verbatim, table by table, row by row.

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
Seventeen destination rows. **Its CO2-eq column is a verbatim copy of table
1's CO2-eq column, in row order** -- 1.46 (Bread), 4.28 (Bakery), 10.13
(Cheese), 1.51 (Milk), 4.95 (Cream), 11.39 (Butter), 3.29 (Yoghurt), 1.19
(Other dairy), 4.93 (Eggs), 2.01 (Drinks/Beverages), 1.78 (Fruit), 1.82
(Vegetable), 20.28 (Red Meat), 10.62 (Pork), 3.98 (Poultry), 11.63 (Other
meat), 5.94 (Seafood) -- and every one of those seventeen destination rows
holds the food row at the same position in table 1, not a value about that
destination. The repository owner has ruled (2026-09-05, ahead of the client
meeting) that this column is used exactly as printed regardless, because the
client's own figures are themselves a draft and no better column exists yet.
The correspondence is recorded here, in full, precisely so nobody mistakes it
for a coincidence or an oversight later.

The water column is not implicated in that copy-paste: it holds seventeen
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
    co2e_per_kg: Decimal     # kg CO2-eq / kg -- SEE MODULE DOCSTRING: a
                              # verbatim copy of TABLE1's CO2-eq column
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

# Table 2, transcribed row by row in the client's own order.  The CO2-eq
# column is the verbatim copy described in the module docstring; the
# `_TABLE2_CO2_SOURCE` mapping alongside it names which TABLE1 row each value
# was in fact copied from, so the correspondence can be checked mechanically
# as well as by eye.
TABLE2: list[DestinationRow] = [
    DestinationRow("Anaerobic Digestion", "Bin to AD Implementation", _d("1.46"), _d("-1.50")),
    DestinationRow("BioBased", "Bin to Meat Rendering Implementation", _d("4.28"), _d("-2.20")),
    DestinationRow("Compost", "Bin to Compost used in Hort", _d("10.13"), _d("-2.40")),
    DestinationRow("Incineration", "Bin to Energy From Waste", _d("1.51"), None),
    DestinationRow("Landfill", "Bin to Landfill", _d("4.95"), _d("0.06")),
    DestinationRow("Landspread", "Breakdown on Farm", _d("11.39"), _d("-2.40")),
    DestinationRow("Not Harvested", "Breakdown on Farm", _d("3.29"), _d("-1.20")),
    DestinationRow("Other Food Waste", "Bin to Landfill", _d("1.19"), _d("0.06")),
    DestinationRow("Refuse", "Bin to Landfill", _d("4.93"), _d("0.06")),
    DestinationRow("Sewer/Wastewater Treatment", "Bin to Biosolids", _d("2.01"), _d("0.00")),
    DestinationRow("Unknown Food Waste", "Bin to Landfill", _d("1.78"), _d("0.06")),
    DestinationRow("Charity Redistribution", "Bin to Fork", _d("1.82"), _d("0.06")),
    DestinationRow("Commercial Redistribution", "Bin to Fork", _d("20.28"), _d("0.06")),
    DestinationRow("Stock Feed", "Bin to Farm", _d("10.62"), _d("-1.95")),
    DestinationRow("Upcycled", "Bin to Product", _d("3.98"), _d("-2.80")),
    DestinationRow("Unknown Repurposed", "Bin to Farm", _d("11.63"), _d("-1.95")),
    DestinationRow("Pet Food", "Bin to Farm", _d("5.94"), _d("-1.95")),
]

#: Table 2's destination label -> the TABLE1 (category, food) its CO2-eq
#: value was copied from, in the client's own row order. A mechanical check
#: (``build_upstream_factors_draft.py``) asserts every one of these actually
#: matches, so this mapping cannot silently drift from the data above it.
TABLE2_CO2_COPY_SOURCE: dict[str, tuple[str, str]] = {
    "Anaerobic Digestion": ("Bakery", "Bread"),
    "BioBased": ("Bakery", "Bakery"),
    "Compost": ("Dairy", "Cheese"),
    "Incineration": ("Dairy", "Milk"),
    "Landfill": ("Dairy", "Cream"),
    "Landspread": ("Dairy", "Butter"),
    "Not Harvested": ("Dairy", "Yoghurt"),
    "Other Food Waste": ("Dairy", "Other dairy"),
    "Refuse": ("Eggs", "Eggs"),
    "Sewer/Wastewater Treatment": ("Drinks", "Drinks/Beverages (excluding dairy)"),
    "Unknown Food Waste": ("Produce", "Fruit"),
    "Charity Redistribution": ("Produce", "Vegetable"),
    "Commercial Redistribution": ("Meat & Seafood", "Red Meat"),
    "Stock Feed": ("Meat & Seafood", "Pork"),
    "Upcycled": ("Meat & Seafood", "Poultry"),
    "Unknown Repurposed": ("Meat & Seafood", "Other meat"),
    "Pet Food": ("Meat & Seafood", "Seafood"),
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
