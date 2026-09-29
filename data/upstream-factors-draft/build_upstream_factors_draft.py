"""Derive a draft New Zealand factor set from the client's Rawtec tables.

This is the audit trail for open item O-1's first movement. It reads three
committed inputs --

    rawtec_source_data.py                    the client's own two tables,
                                              transcribed verbatim (see its
                                              own docstring for where from)
    public_farm_share_source_data.py         one public source (Poore &
                                              Nemecek 2018, via Our World in
                                              Data), used only to fill the
                                              Farm stage where ReFED
                                              publishes none (see below)
    tests/benchmark/refed/
        refed-benchmark-factors.json          ReFED's published upstream and
                                              downstream footprint, already
                                              derived and committed in this
                                              repository by
                                              build_refed_benchmark.py

-- and writes one file, ``upstream_factors_draft.json``, in the same shape
``docker/mock-factors.json`` and ``tests/benchmark/refed/
refed-benchmark-factors.json`` already use, so it loads through the same
kind of script (``load_upstream_factors_draft.py``, modelled directly on
``tests/benchmark/refed/load_refed_benchmark.py``).

Every stored value's ``source_note`` states the client row(s) it came from,
the arithmetic applied, and (for the upstream stage split) the ReFED shares
used. ``docs/upstream-factors-draft.md`` is the narrative companion; this
docstring and that document should be read together.

WHAT THIS PRODUCES
------------------
``factor_upstream`` -- one generic (``destination = null``) row per (sector,
food_category, metric), covering the six New Zealand sectors
(``admin/seed.py`` ``SECTORS``) and every food category for which the client
supplied a usable total, **plus** ``staples`` (no client total exists for it
-- see "STAPLES", below), for ``co2e``, ``water`` **and, as of this revision,
``ch4``** (the client's table has no ``ch4`` column at all, so every food
category's ``ch4`` is filled the unanchored way -- see "CH4: FILLED FROM
REFED ALONE" below). ``cost`` has no upstream row (see "COST" below -- it is
a downstream-only figure). **And, as of this revision, ``land``** -- the
client's own t/ha column, which is a *yield* and has to be inverted before it
can be used as a factor at all; see "LAND" below. Plus a zero override at
``destination = prevention`` for every one of those rows, so the set is
consistent with the other New Zealand-style sets in this repository even
though it is never published (§O-7).

``factor_downstream`` -- one row per (our destination, metric), ``sector =
null`` and ``food_category = null`` (the client's table 2 does not vary by
either), for ``co2e`` and ``water`` where a value exists, **plus, as of this
revision, ``ch4`` (filled from ReFED, see below) and ``cost`` (filled from
the New Zealand waste disposal levy, see below)**. Plus an explicit zero
override at ``prevention``, matching the upstream override above and for the
identical reason.

THE STAGE SPLIT IS A CUMULATIVE FOOTPRINT, NOT A SHARE OF A WHOLE
------------------------------------------------------------------
``factor_upstream(sector, food_category)`` is the upstream footprint
*embodied in one kilogram wasted at that sector* -- a household throwing
away a kilogram of cheese wastes everything that went into it from the farm
onward, so its factor must be close to the client's *full* farm-to-shelf
total, not a fraction of it. An earlier version of this script (commit
ba7fe93) read ReFED's five per-sector values as *shares of a whole* --
normalised so they summed to 1 and multiplied against the client's total, so
the six New Zealand sector values summed back to the client's own figure.
That was the wrong shape: it understated every consumer-stage factor
roughly fourfold, and the consumer stage is this calculator's principal
audience. It has been replaced with the construction below. (Why the old
"sums to the client's total" property looked like a virtue and is not: a
`POST /calculate` request names one sector and nothing ever sums across
them, so nothing was ever checking that property against reality.)

**What replaces it.** Read ReFED's own five per-sector values for a food
category as a *cumulative shape* -- Farm's value is the average embodied
footprint of a kilogram wasted at the farm, Manufacturing's is the average
embodied footprint of a kilogram wasted during manufacturing (which has
already survived farming and lost more along the way), and so on. ReFED's own
committed dairy figures make the point without any further argument:
manufacturing 2.8794, retail 3.8473, residential 4.4668, foodservice 4.7728
kg CO2e/kg -- rising along the chain, exactly as a cumulative figure should.
(The earlier draft's "not monotonic" objection was real, but it was read off
``standard_mix``, an aggregate across many food types wasted in different
mixes at each stage; *within one food category* -- as here -- the shape is
overwhelmingly monotonic, and where it is not, see "ENFORCING THE
NON-DECREASING INVARIANT" below.)

For each New Zealand food category, one ReFED sector is designated the
*anchor*: the ReFED sector matching the client's own "Life cycle covered"
column for that food (§3.1 of the provenance document) -- ``wholesale_retail``
for *Farm to Supermarket Shelf* (ReFED Retail), ``consumer_household`` for
*Farm to Fork* (ReFED Residential). The anchor's value is **set exactly
equal to the client's own total** for that food and metric (never computed
through a ratio, so it cannot drift from the client's figure by so much as a
rounding step); every other ReFED sector's value for that food is multiplied
by ``client_total / anchor_ReFED_value`` to reach a candidate New Zealand
figure, preserving the *ratios* ReFED's own data implies between stages while
pinning the curve to the client's own number at the stage the client actually
measured.

ENFORCING THE NON-DECREASING INVARIANT
-----------------------------------------
A cumulative footprint cannot decrease as waste moves further down the
supply chain. ReFED's own per-category, per-metric figures mostly rise this
way but not always -- a few categories mix enough different foods internally
that a later stage's average dips below an earlier one for reasons that are
about food mix, not physics (nothing this script treats as meaningful; see
``refed_dry_goods`` co2e below, where ReFED's own Farm figure is *higher*
than its Manufacturing and Retail figures). Rather than let an anomaly like
that either ship an unphysical dip or get silently smoothed away, this
script asserts the invariant mechanically and repairs any violation with a
one-directional clamp, and every clamp is recorded in the row's own
``source_note``:

  * For the three stages that precede the anchor in the chain (some subset
    of ``primary_production -> processing -> wholesale_retail [->
    consumer_household]``, stopping at the anchor, which is fixed), each
    earlier stage's *candidate* value is capped at the *next* stage's
    (already-resolved) value, working backward from the anchor. This can
    only pull an earlier stage's value *down*, never invents a higher one,
    and it guarantees the anchor is reached exactly (nothing after it in
    this backward pass can push it up).
  * For every consumer-stage sector not itself the anchor (so, when the
    anchor is ``wholesale_retail``: all three of ``consumer_household``,
    ``consumer_hospitality``, ``consumer_institution``; when the anchor is
    ``consumer_household``: the remaining two), each candidate is floored at
    ``wholesale_retail``'s resolved value. This can only push a candidate
    *up*, never down.

Nothing here invents a number ReFED did not publish; it only decides, in a
stated and checkable way, which of two published numbers to keep when they
disagree about which one is "further down the chain".

FIVE OF REFED'S NINE FOOD CATEGORIES HAVE NO FARM-STAGE VALUE AT ALL
-----------------------------------------------------------------------
Breads & Bakery, Dairy & Eggs, Fresh Meat & Seafood, Frozen, Prepared Foods
and Ready-To-Drink Beverages carry no Farm row in ReFED's own published
factors (only Dry Goods, Produce and Standard Mix do -- ReFED's own choice,
stated in ``build_refed_benchmark.py``'s docstring). That is four of the nine
ReFED categories this draft actually uses (the fifth, Frozen, is not mapped
to by anything here) -- ``meat`` and ``seafood`` (Fresh Meat & Seafood),
``dairy`` (Dairy & Eggs), ``bakery_grains`` (Breads & Bakery) and
``beverages`` (Ready-To-Drink Beverages). For a New Zealand calculator the
farm stage of meat and dairy is the dominant term, so this is filled rather
than left at zero:

  * **``co2e``**: filled from Poore & Nemecek (2018), as republished by Our
    World in Data (see ``public_farm_share_source_data.py`` for the exact
    citation, the transcribed rows and the read date). For each of the five
    categories, one or more Our World in Data product rows are read as a
    proxy (``FOOD_CO2E_FARM_SHARE_SOURCES`` below), each contributes a
    "farm share" -- the fraction of its own farm-to-retail total that is
    on-farm production, land-use change and feed-growing rather than
    processing/transport/retail/packaging -- and an unweighted mean of those
    shares (stated, for the same reason every other aggregation in this
    draft is unweighted: no production weights exist to do better) is
    multiplied against the *client's own* total for that category. This is
    a real, checkable public figure, not an assumption.
  * **``water``**: no public source with a *per-stage* water breakdown was
    found (Our World in Data's water figures for food are farm-to-retail
    totals, not stage breakdowns -- see ``public_farm_share_source_data.py``).
    Rather than force-fit a number this source does not give, the primary
    production share of the client's water total for these five categories
    is a **stated assumption**, not a citation: 90%, on the general (not
    per-product) finding that irrigation dominates agricultural freshwater
    withdrawal. Every affected row's ``source_note`` says so in full and
    names this an assumption, not a measurement.

STAPLES: FILLED FROM REFED ALONE, WITH NO CLIENT TOTAL TO ANCHOR AGAINST
----------------------------------------------------------------------------
The client's twenty rows have no analogue for ``staples`` at all (§3.2 of the
provenance document). Rather than leave it unseeded, it is filled from
ReFED's own Dry Goods figures -- the nearest available ReFED category to a
"pantry basics" grouping, and already the shape used for ``nuts_seeds``.
There is no client total to anchor against, so ReFED's absolute published
values are used directly (a scale factor of exactly 1), with the same
non-decreasing invariant enforced by a plain forward running-maximum clamp
(no backward pass, because there is no fixed anchor to work back from).
Every clamp is recorded exactly as for the other categories.

FOOD CATEGORY MAPPING (STEP 2)
--------------------------------
See ``NZ_FOOD_CATEGORY_SOURCES`` below for the client rows behind each New
Zealand category, and ``NZ_TO_REFED_FOOD_SHAPE`` for the ReFED category whose
per-stage *shape* is used. ``meat`` and ``dairy`` are unweighted arithmetic
means of several client rows because the client supplied no production
weights -- see the provenance document for the full working. The client's
``Eggs`` row has no New Zealand category to receive it and is left out --
reported, not silently absorbed into a neighbour.

DESTINATION MAPPING (STEP 3, TABLE 2 -- IN SCOPE ON THE OWNER'S RULING OF
2026-09-05; CO2-eq COLUMN CORRECTED BY THE CLIENT 2026-09-21)
----------------------------------------------------------------------------
Table 2's CO2-eq column was a verbatim copy of table 1's in the client's
2026-09-05 document and was used as printed on the owner's ruling of that
date. The client replaced it on 2026-09-21 and this script now builds from
the corrected column; ``rawtec_source_data.py``'s module docstring holds the
full record of the defect, the ruling and the correction, and
``_assert_only_table2_co2_moved()`` below re-checks, on every build, that
nothing else in either table moved with it.

See ``NZ_DESTINATION_SOURCES`` below. `prevention` receives no client row --
it is the mandatory 100% offset (§O-7) and giving it a factor would silently
inflate every net-benefit figure the calculator reports.

``other_recovery`` ("Other recovery, including biodiesel") has no
comparably-shaped client destination -- "Other Food Waste" is a *disposal*
row (*Bin to Landfill*) and must not be folded in here merely because the
names read alike. It is filled instead from ReFED's own "Industrial Uses"
destination, which sits in ReFED's own ``recycle_recovery``-equivalent group
(``build_refed_benchmark.py``'s ``DESTINATIONS``) -- the same group
``admin/seed.py`` gives ``other_recovery`` -- and is therefore the correct
shape of match, not merely a similar name. ReFED publishes it per (sector,
food category); since our own destination-level rows here carry no sector or
food-category breakdown (the client's table doesn't either), an unweighted
mean across every published (sector, food category) combination is used for
``co2e``; ReFED's own water figure for this destination is exactly zero in
every one of those combinations, so it is used directly rather than averaged.

Incineration's water is `null` in the client's own table. Rather than leave
it an absence or invent a figure, ReFED's own water value for its
"Incineration" destination is used: it is exactly zero across every
published (sector, food category) row, i.e. ReFED's own model assigns
combustion-for-energy no process water at all. Adopted here as a real,
sourced zero (§4.3 below), not a silent one.

CH4: FILLED FROM REFED ALONE, FOR EVERY FOOD CATEGORY AND EVERY DESTINATION
----------------------------------------------------------------------------
Neither client table carries a `ch4` column at all -- table 1's "CO2-eq" is
already a carbon-dioxide-equivalent figure, not decomposed into a separate
methane mass, and table 2 has no methane column either. There is therefore no
client total to anchor *any* food category to, so every one of the ten New
Zealand food categories' `ch4` upstream rows is built the unanchored way
§"STAPLES" already uses for `staples`' co2e/water: ReFED's own absolute
per-stage methane figures, scale exactly 1, non-decreasing enforced by a
forward running-maximum clamp. This reuses `NZ_TO_REFED_FOOD_SHAPE` and
`REFED_SECTOR_FOR_NZ_SECTOR` exactly as already built for co2e/water --
no second food-category or sector mapping is introduced for methane.

**ReFED's own `ch4` figures are raw methane mass, not a CO2-equivalent.**
Confirmed by reading `tests/benchmark/refed/build_refed_benchmark.py`'s own
unit-conversion table: its `ch4` column is read from the CSV's
`..._mtch4_footprint_per_ton` field (metric tons of CH4 itself) and converted
by the *same* mass-based `1000 / 907.185` factor as `co2e`'s
`..._mtco2e_footprint_per_ton` field, landing at `kg CH4/kg` -- a distinct
unit from `co2e`'s `kg CO2e/kg`, not a GWP-multiplied version of it. This
matches the `ch4` metric's own declared unit (`admin/seed.py` `METRICS`:
`("ch4", "Methane", "kg CH4", ...)`) and this draft's own `ch4` formula
(`qty_kg * (upstream + downstream)`, unchanged, no `const_GWP_CH4` term --
identical to `docker/mock-factors.json`'s shipped formula). Seeding ReFED's
methane figures directly, with no further GWP multiplication, is therefore
correct: `const_GWP_CH4` (bound to `GWP_CH4_20`/`GWP_CH4_100`, 84/28) stays
unreferenced by any formula in this draft, exactly as before this revision --
multiplying by it here would overstate every methane figure by that same
factor.

Five of ReFED's own food categories publish no Farm-stage `ch4` value either
(the identical structural gap as `co2e` -- see "FIVE OF REFED'S NINE FOOD
CATEGORIES..." above -- affecting the same New Zealand categories: `meat`,
`seafood`, `dairy`, `bakery_grains`, `beverages`). Unlike `co2e`, no public
per-product *methane* farm-share source was found (Poore & Nemecek's own
farm-share, used above, is a CO2e-equivalent share and does not carry over to
methane, whose farm-stage share for livestock is typically much larger on
account of enteric fermentation, not smaller). Rather than leave `primary_
production` a silent gap -- implausible for meat and dairy, where on-farm
methane is usually the dominant term -- it is floored at `processing`'s own
resolved value: the highest figure the non-decreasing invariant permits
without inventing a number no source gives. This is very likely still an
**understatement** for those five categories and every affected row's
`source_note` says so explicitly, as does the provenance document.

Downstream, ReFED's own destination code is matched to each of our fourteen
destinations by *shape* (`REFED_DESTINATION_FOR_NZ_DESTINATION` below),
exactly the reasoning already used for `other_recovery`'s co2e/water fill
above -- extended here to cover every destination because, again, the client
supplies no methane column to draw a client-based mapping from. Two of our
destinations legitimately share one ReFED destination (`bioprocessing` and
`other_recovery` both draw ReFED's "Industrial Uses", the nearest
recycle_recovery-shaped destination to either of them) and one has no
comparably-shaped ReFED destination at all: `upcycling` ("Upcycling to other
food products") matches nothing ReFED publishes under its own `reuse`-
equivalent destinations (`Donations`, `Animal Feed` only), and the
process-based alternative (`Industrial Uses`, already used twice above) is
in ReFED's `recycle_recovery`-equivalent group and produces non-food
industrial output, not "another food product" -- using it here would blur
the food/non-food distinction this document relies on elsewhere. Rather
than leave this one destination priced at a silent zero -- indistinguishable
on screen from a real measurement of none, the defect this whole revision
exists to remove -- `upcycling` draws an explicit STAND-IN: an unweighted
mean pooling every row ReFED publishes across its two real reuse-group
destinations (`REFED_UPCYCLING_STANDIN_DESTINATIONS`), carrying its own
`data_quality` tag (`derived-refed-reuse-standin`) so it reads
differently from a cell that found an actual matching ReFED destination.
`eggs` remains the one genuine unseeded gap in this draft -- see §3.2 of the
provenance document.

COST: THE NEW ZEALAND WASTE DISPOSAL LEVY, DOWNSTREAM ONLY
----------------------------------------------------------
Neither client table carries a cost column, and O-2's closure (contract
v1.48, `docs/architecture.md`) already rules that `cost` is disposal cost and
the waste levy only -- the value of the wasted food itself stays at zero via
`FOOD_VALUE_PER_KG`, unchanged. `cost` is therefore a purely **downstream**
figure here: it depends on where the waste goes, not what food it was, so no
upstream `cost` row is written for any food category (the upstream term
resolves to zero via the ordinary three-step lookup fallback, exactly as it
did before this revision).

**The rate seeded is the one in force today (2026-09-05), not the client's
own cited figure.** Read directly (`WebFetch`, 2026-09-05) from the Ministry
for the Environment's own "Waste disposal levy expansion" page
(https://environment.govt.nz/what-government-is-doing/areas-of-work/waste/
waste-disposal-levy/expansion/), Class 1 (municipal landfill)'s own published
schedule is 1 July 2025 $65/tonne, **1 July 2026 $70/tonne**, 1 July 2027
$75/tonne -- cross-checked against an independently dated report of the same
1 July 2026 increase (Bin Bookings, "The National Waste Levy Explained", 22
June 2026, read 2026-09-05: "$70 per tonne... up from $65"). As of today the
levy in force is **$70/tonne**; the client's own cited $75/tonne is the rate
that takes effect 1 July 2027 and is **not yet in force** -- seeding it today
would overstate every cost figure by roughly seven percent. `LEVY_SOURCE`
below records both rates and both dates; only the current one is seeded.

**Which destinations carry it.** The levy is charged at a "disposal
facility" (a landfill of some class); it is not a general waste charge.
`landfill` carries it directly. `refuse_discard` also carries it: its own
client rows (`NZ_DESTINATION_SOURCES["refuse_discard"]`) are all published
*Bin to Landfill*, the identical life cycle as `landfill` itself -- see §4.2
of the provenance document -- so it is priced the same way. Every other
destination gets an **explicit zero, with a stated reason** in its own
`source_note` (`LEVY_EXCLUDED_WITH_REASON` below), never a silent absence:
`combustion` (incineration/energy-from-waste is not classified as a
"disposal facility" under New Zealand's waste levy regulations and is
excluded -- confirmed by an independent policy source, read 2026-09-05, not
merely inferred), every `recycle_recovery`-group destination (`compost`,
`anaerobic_digestion`, `land_application`, `not_harvested`, `bioprocessing`,
`other_recovery` -- none is a disposal facility), every `reuse`-group
destination (`food_redistribution`, `animal_feed`, `upcycling` -- the food is
not disposed of at all), and `sewer` (trade-waste discharge is charged under
a separate regime, not the Waste Minimisation Act's disposal levy).
`prevention` keeps its existing zero-by-definition override.

LAND: THE CLIENT'S YIELD COLUMN, INVERTED, AND CHECKED ROW BY ROW
--------------------------------------------------------------------
The client's table 1 gives a land column for every food row, and until this
revision this system had no `land` metric to receive it. The owner has ruled
that one is introduced, so it is built here.

**The client publishes t/ha, which is a yield, not a footprint.** Used as a
factor exactly as printed it would be upside down -- a bigger number would
mean more land. What the metric reports is land occupation per kilogram:

    1 kg                      = 0.001 t
    0.001 t / (Y t/ha)        = 0.001/Y ha
    0.001/Y ha x 10,000 m2/ha = 10/Y  m2

so `land_m2_per_kg = 10 / yield_t_per_ha`. That conversion is
`land_m2_per_kg_from_yield()` below, and the derivation is written out beside
the line that performs it. It is done entirely on `Decimal`.

**The land column is the loosest thing in the client's document**, and
several rows are implausible once inverted -- `Poultry` at 57.48 t/ha becomes
0.17 m2/kg, a vegetable's footprint rather than a chicken's, and `Nuts and
seeds` carries Red Meat's `0.22` to two decimal places, which is a copy
rather than a measurement. So every client row is checked against a public
source: Poore & Nemecek (2018), the same study this draft already uses for
the co2e farm share, whose land-use-per-kilogram table is transcribed in
`public_land_use_source_data.py` with its own URL and read date.

**One stated rule decides each row, and the row says which way it went.**
Where the client-derived figure and the public one differ by a factor of ten
or more, the public figure is taken; otherwise the client's is kept. Nothing
is averaged between them, and no implausible client figure is kept silently:
every `source_note` names which of the two it is, both numbers, the ratio
and why. `print_land_comparison()` prints the whole table on every build.
Four rows cross the threshold -- `Poultry` (70x), `Other meat` (352x),
`Eggs` (16x, and not used by any New Zealand category) and `Sweeteners`
(16x). One row the owner flagged does NOT cross it and is reported rather
than quietly fixed: `Nuts and seeds` is 4.1x from the public figure, so the
stated rule keeps the client's 45.45 m2/kg even though its cell is a known
duplicate of Red Meat's; its `source_note` records both.

**Land is flat across all six sectors**, unlike every other metric here.
co2e, water and ch4 are cumulative footprints -- a kilogram wasted at retail
carries processing and transport a kilogram wasted at the farm gate does not.
Land does not accumulate that way: the land was occupied to grow the food,
and the same kilogram carries the same land wherever it is thrown away. Both
sources are farm-gate quantities and neither publishes a downstream land
term, so there is nothing to escalate a later stage with. This is very likely
a slight understatement for the later stages (a kilogram on a shelf embodies
more than a kilogram of farm output) and every row says so.

**`factor_downstream` carries no land row at all, and that is correct rather
than an omission.** The client's table 2 has no land column and should not
have one: sending a kilogram to landfill, to compost or to an anaerobic
digester returns no land and occupies none. An absent row resolves to zero
through the documented three-step lookup order, so the absence *is* the
answer. `_assert_no_land_downstream_rows()` checks it mechanically, so a
future edit that adds one has to argue for it.

`staples` has no client row and ReFED publishes no land figure for anything,
so neither of the two routes the other metrics use exists. It is built from
the six client rows `admin/seed.py`'s own `FOOD_ITEMS` files under `staples`
-- see `LAND_STAPLES_CLIENT_ROWS` for which, and why `Eggs` is excluded.

WHAT IS NOT REPRESENTED
-------------------------
  * `upcycling`'s `ch4` -- no ReFED destination matches its shape; filled
    with a stated stand-in rather than left a gap; see "CH4" above.
  * The waste levy's own "disposal cost" component beyond the statutory levy
    itself (landfill gate fees vary by facility and contract and no
    NZ-wide public figure was found) -- `cost` here is the levy only, stated
    as such in every row's `source_note`.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path
from typing import NamedTuple

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
REFED_FACTORS_PATH = (
    REPO_ROOT / "tests" / "benchmark" / "refed" / "refed-benchmark-factors.json"
)

sys.path.insert(0, str(HERE))
from rawtec_source_data import (  # noqa: E402
    PRIOR_REVISION_TABLE1,
    PRIOR_REVISION_TABLE2_EXCEPT_CO2,
    TABLE1,
    TABLE2,
    WITHDRAWN_2026_09_05_TABLE2_CO2,
    destination_row,
    food_row,
)
from public_farm_share_source_data import farm_share as owid_farm_share  # noqa: E402
from public_land_use_source_data import land_use as owid_land_use  # noqa: E402

SCALE = Decimal("0.0000000001")  # factor_upstream/downstream.value_per_kg: DECIMAL(20,10)


def q(value: Decimal) -> str:
    return f"{value.quantize(SCALE, rounding=ROUND_HALF_EVEN):f}"


def qd(value: Decimal) -> Decimal:
    return value.quantize(SCALE, rounding=ROUND_HALF_EVEN)


def mean(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / Decimal(len(values))


# ---------------------------------------------------------------------------
# Step 2: New Zealand food category -> client TABLE1 row(s).
#
# One food name: used directly. Several: unweighted arithmetic mean, because
# the client supplied no production weights to do anything better with --
# see the provenance document for the stated reasoning. `staples` has no
# client row at all (see the module docstring) and is built separately,
# below, entirely from ReFED.
# ---------------------------------------------------------------------------
NZ_FOOD_CATEGORY_SOURCES: dict[str, list[str]] = {
    "fruit": ["Fruit"],
    "vegetables": ["Vegetable"],
    "nuts_seeds": ["Nuts and seeds"],
    "meat": ["Red Meat", "Pork", "Poultry", "Other meat"],
    "seafood": ["Seafood"],
    "dairy": ["Cheese", "Milk", "Cream", "Butter", "Yoghurt", "Other dairy"],
    "bakery_grains": ["Bread", "Bakery", "Grains"],
    "beverages": ["Drinks/Beverages (excluding dairy)"],
    "standard_mix": ["General mixed food product"],
}

# Which ReFED food category supplies the stage *shape* for each New Zealand
# category above (plus `staples`, which has no client row -- see the module
# docstring). Chosen by the closest available match; several New Zealand
# categories necessarily share one ReFED category because ReFED's own nine
# do not split as finely (meat and seafood both draw from ReFED's one "Fresh
# Meat & Seafood"; fruit and vegetables both draw from ReFED's one
# "Produce"; staples and nuts_seeds both draw from ReFED's one "Dry Goods").
NZ_TO_REFED_FOOD_SHAPE: dict[str, str] = {
    "fruit": "refed_produce",
    "vegetables": "refed_produce",
    "nuts_seeds": "refed_dry_goods",
    "meat": "refed_fresh_meat_seafood",
    "seafood": "refed_fresh_meat_seafood",
    "dairy": "refed_dairy_eggs",
    "bakery_grains": "refed_breads_bakery",
    "beverages": "refed_ready_to_drink_beverages",
    "standard_mix": "refed_standard_mix",
    "staples": "refed_dry_goods",
}

# The client's own "Life cycle covered" column, per New Zealand category
# (verified consistent across every constituent client row -- asserted
# below) -- and which of our sectors is the anchor for that wording: the
# stage the client's own total actually measures up to.
LIFE_CYCLE_ANCHOR: dict[str, str] = {
    "Farm to Supermarket Shelf": "wholesale_retail",
    "Farm to Fork": "consumer_household",
}

# Our sectors -> the ReFED sector whose stage value they draw on.
# `consumer_hospitality` and `consumer_institution` both draw the *whole* of
# ReFED's Foodservice value (not half each -- see the module docstring: this
# is a cumulative footprint, not a share, so splitting it would understate
# both). This is a stated, flat assumption exactly where our taxonomy is
# finer than ReFED's (`consumer_institution` has no ReFED equivalent at
# all), not a claim that hospitality and institutional waste actually carry
# identical footprints.
REFED_SECTOR_FOR_NZ_SECTOR: dict[str, str] = {
    "primary_production": "refed_farm",
    "processing": "refed_manufacturing",
    "wholesale_retail": "refed_retail",
    "consumer_household": "refed_residential",
    "consumer_hospitality": "refed_foodservice",
    "consumer_institution": "refed_foodservice",
}
ALL_NZ_SECTORS = list(REFED_SECTOR_FOR_NZ_SECTOR)
CONSUMER_BRANCHES = ["consumer_household", "consumer_hospitality", "consumer_institution"]

# Public co2e farm-share proxy rows (Our World in Data / Poore & Nemecek --
# see public_farm_share_source_data.py) for the five categories ReFED
# publishes no Farm stage for. Unweighted mean where more than one row is
# used, for the same reason as every other aggregation in this draft.
FOOD_CO2E_FARM_SHARE_SOURCES: dict[str, list[str]] = {
    "meat": ["Beef (beef herd)", "Pig Meat", "Poultry Meat", "Lamb & Mutton"],
    "seafood": ["Fish (farmed)", "Shrimps (farmed)"],
    "dairy": ["Cheese", "Milk"],
    "bakery_grains": ["Wheat & Rye"],
    "beverages": ["Wine", "Coffee", "Soy milk"],
}
# Stated assumption, not a citation -- no public per-stage water breakdown
# was found for these five categories. See the module docstring and
# public_farm_share_source_data.py.
WATER_FARM_SHARE_ASSUMPTION = Decimal("0.90")

# ---------------------------------------------------------------------------
# Step 3 (table 2, in scope on the owner's ruling of 2026-09-05): New
# Zealand destination -> client TABLE2 destination(s).
#
# `prevention` is deliberately absent -- see the module docstring.
# `other_recovery` has no client row either but IS seeded, from ReFED -- see
# below.
# ---------------------------------------------------------------------------
NZ_DESTINATION_SOURCES: dict[str, list[str]] = {
    "anaerobic_digestion": ["Anaerobic Digestion"],
    "bioprocessing": ["BioBased"],
    "compost": ["Compost"],
    "combustion": ["Incineration"],
    "landfill": ["Landfill"],
    "land_application": ["Landspread"],
    "not_harvested": ["Not Harvested"],
    "refuse_discard": ["Other Food Waste", "Refuse", "Unknown Food Waste"],
    "sewer": ["Sewer/Wastewater Treatment"],
    "food_redistribution": ["Charity Redistribution", "Commercial Redistribution"],
    "animal_feed": ["Stock Feed", "Pet Food", "Unknown Repurposed"],
    "upcycling": ["Upcycled"],
    # "other_recovery": no client analogue -- filled from ReFED, see below.
    # "prevention": the mandatory 100% offset. Never given a client value.
}

#: The canonical fourteen -- must be kept in sync with `admin/seed.py`'s
#: `DESTINATIONS` by hand, since this script does not import admin.seed (it
#: must not import SQLAlchemy -- see `docs/architecture.md`'s "db/repository.py
#: is the only code that may touch the database" invariant). Used only by
#: `_assert_completeness` below, to fail loudly if a future edit drops a row
#: for one of them rather than quietly pricing it at zero.
ALL_NZ_DESTINATIONS = (
    "prevention", "food_redistribution", "upcycling", "animal_feed",
    "compost", "anaerobic_digestion", "land_application", "not_harvested",
    "bioprocessing", "other_recovery",
    "combustion", "landfill", "refuse_discard", "sewer",
)

# ReFED's own destination code for `other_recovery`'s fill, and for
# `combustion`'s water fill -- see the module docstring.
REFED_OTHER_RECOVERY_DESTINATION = "refed_industrial_uses"
REFED_INCINERATION_DESTINATION = "refed_incineration"

# ---------------------------------------------------------------------------
# `ch4` downstream: every one of our fourteen destinations mapped onto the
# ReFED destination whose shape it matches (see module docstring, "CH4").
# Neither client table carries a methane column, so unlike co2e/water above
# this cannot be built from NZ_DESTINATION_SOURCES -- there is no client row
# to draw the mapping from. `bioprocessing` and `other_recovery` deliberately
# share `refed_industrial_uses` (both are the nearest recycle_recovery-shaped
# ReFED destination to either of them; the same reasoning already used for
# `other_recovery`'s co2e/water fill, above).
#
# `upcycling` ("Upcycling to other food products", client row "Upcycled",
# life cycle "Bin to Product" -- the only client row with that wording) has
# no ReFED destination that matches it, checked two ways: by name (ReFED
# publishes no "Upcycling" or "Repurposed" destination at all) and by our own
# destination-group classification (`admin/seed.py` puts `upcycling` in the
# `reuse` group; ReFED's own DESTINATIONS table -- build_refed_benchmark.py
# -- puts only Prevention, Donations and Animal Feed in that group).
# `refed_industrial_uses` was considered and rejected for this row even
# though it is already used twice above: it is explicitly in ReFED's own
# recycle_recovery-equivalent group, not reuse, and it produces non-food
# industrial output, not "other food products" -- using it here would blur
# the food/non-food distinction this document already relies on to keep
# `other_recovery` itself separate from disposal (§4.2 of the provenance
# document). Left with no single ReFED destination that matches by either
# test, `upcycling` draws a STAND-IN, not a measurement: an unweighted mean
# pooling every (sector, food_category) row ReFED publishes for its two real
# reuse-group destinations, Donations and Animal Feed
# (`REFED_UPCYCLING_STANDIN_DESTINATIONS`, below) -- the closest available
# ReFED pathway by our own group classification, not by name or process.
# `data_quality = "derived-refed-reuse-standin"` marks this row so a
# reader (and a future edit) can tell it apart from every other
# `derived-refed` cell in this table, which reads an actual matching ReFED
# destination rather than an average standing in for one that does not
# exist.
#
# `prevention` is never given a value here; it keeps the standard
# zero-by-definition override.
# ---------------------------------------------------------------------------
REFED_DESTINATION_FOR_NZ_DESTINATION: dict[str, str] = {
    "food_redistribution": "refed_donations",
    "animal_feed": "refed_animal_feed",
    "compost": "refed_composting",
    "anaerobic_digestion": "refed_anaerobic_digestion",
    "land_application": "refed_land_application",
    "not_harvested": "refed_not_harvested",
    "bioprocessing": REFED_OTHER_RECOVERY_DESTINATION,
    "other_recovery": REFED_OTHER_RECOVERY_DESTINATION,
    "combustion": REFED_INCINERATION_DESTINATION,
    "landfill": "refed_landfill",
    "refuse_discard": "refed_dumping",
    "sewer": "refed_sewer",
    # "upcycling": no single ReFED destination matches -- see
    # REFED_UPCYCLING_STANDIN_DESTINATIONS and build_ch4_downstream(), below.
    # "prevention": the mandatory 100% offset. Never given a ReFED value.
}

#: `upcycling`'s ch4 stand-in: ReFED's two real reuse-group destinations
#: (excluding Prevention), pooled -- see the comment above.
REFED_UPCYCLING_STANDIN_DESTINATIONS = ("refed_donations", "refed_animal_feed")

# ---------------------------------------------------------------------------
# `cost`: the New Zealand waste disposal levy, downstream only. See module
# docstring, "COST". Both the rate in force today and the client's own cited
# future rate are recorded; only the current one is seeded.
# ---------------------------------------------------------------------------
LEVY_CURRENT_NZD_PER_TONNE = Decimal("70.00")
LEVY_CURRENT_EFFECTIVE = "1 July 2026"
LEVY_NEXT_NZD_PER_TONNE = Decimal("75.00")
LEVY_NEXT_EFFECTIVE = "1 July 2027"
LEVY_SOURCE = (
    "Ministry for the Environment, 'Waste disposal levy expansion' "
    "(https://environment.govt.nz/what-government-is-doing/areas-of-work/"
    "waste/waste-disposal-levy/expansion/, read 2026-09-05): Class 1 "
    "(municipal landfill) rate schedule -- 1 July 2025 $65/tonne, 1 July "
    "2026 $70/tonne, 1 July 2027 $75/tonne. Cross-checked against an "
    "independently dated report of the 1 July 2026 increase (Bin Bookings, "
    "'The National Waste Levy Explained', 22 June 2026, read 2026-09-05: "
    "'$70 per tonne... up from $65'). As of 2026-09-05 the levy in force is "
    "therefore $70/tonne; the client's own cited $75/tonne is the rate that "
    "takes effect 1 July 2027 and is NOT yet in force."
)
#: Destinations that are 'Bin to Landfill' in the client's own life-cycle
#: wording (§4.2 of the provenance document) and therefore carry the levy.
LEVY_CARRYING_DESTINATIONS = ("landfill", "refuse_discard")
#: Every other destination: an explicit zero, with the reason it does not
#: carry the levy, never a silent absence.
LEVY_EXCLUDED_WITH_REASON: dict[str, str] = {
    "combustion": (
        "Energy-from-waste/incineration facilities are not classified as a "
        "'disposal facility' under New Zealand's Waste Minimisation Act and "
        "are excluded from the waste disposal levy (confirmed via an "
        "independent policy source, read 2026-09-05, not merely inferred "
        "from the name)."
    ),
    "compost": "Composting is a recycle_recovery pathway, not disposal to a levied facility.",
    "anaerobic_digestion": "Anaerobic digestion is a recycle_recovery pathway, not disposal to a levied facility.",
    "land_application": "Land application is a recycle_recovery pathway, not disposal to a levied facility.",
    "not_harvested": "Not-harvested/ploughed-in waste never reaches a disposal facility at all.",
    "bioprocessing": "Processing into non-food items is a recycle_recovery pathway, not disposal to a levied facility.",
    "other_recovery": "Other recovery, including biodiesel, is a recycle_recovery pathway, not disposal to a levied facility.",
    "food_redistribution": "Food redistribution is a reuse pathway; the food is not disposed of at all.",
    "animal_feed": "Animal feed is a reuse pathway; the food is not disposed of at all.",
    "upcycling": "Upcycling to other food products is a reuse pathway; the food is not disposed of at all.",
    "sewer": (
        "Sewer/wastewater discharge is charged under trade-waste bylaws, a "
        "separate regime from the Waste Minimisation Act's disposal levy, "
        "and is not itself a 'disposal facility' the levy covers."
    ),
}


def load_refed_generic_upstream() -> dict[tuple[str, str, str], Decimal]:
    """(refed_food_category, refed_sector, metric) -> generic upstream value.

    Reads the committed ReFED benchmark fixture rather than re-deriving it --
    see the module docstring for why that file is the right source.
    """
    data = json.loads(REFED_FACTORS_PATH.read_text(encoding="utf-8"))
    out: dict[tuple[str, str, str], Decimal] = {}
    for row in data["upstream"]:
        if row["destination"] is not None:
            continue
        if row["metric"] not in ("co2e", "water", "ch4"):
            continue
        key = (row["food_category"], row["sector"], row["metric"])
        out[key] = Decimal(row["value_per_kg"])
    return out


def load_refed_downstream_values(destination: str) -> dict[str, list[Decimal]]:
    """Every published (sector, food_category) value for one ReFED
    destination, by metric -- used to fill `other_recovery` and
    `combustion`'s water (co2e/water), and every destination's `ch4` (see
    module docstring), all by an unweighted mean/direct read across
    everything ReFED publishes for that destination.
    """
    data = json.loads(REFED_FACTORS_PATH.read_text(encoding="utf-8"))
    out: dict[str, list[Decimal]] = {"co2e": [], "water": [], "ch4": []}
    for row in data["downstream"]:
        if row["destination"] != destination:
            continue
        if row["metric"] not in out:
            continue
        out[row["metric"]].append(Decimal(row["value_per_kg"]))
    return out


def _life_cycle_for(client_rows: list) -> str:
    life_cycles = {r.life_cycle for r in client_rows}
    if len(life_cycles) != 1:
        raise SystemExit(
            f"Client rows for this category disagree on 'Life cycle "
            f"covered': {sorted(life_cycles)}. The anchor-stage mapping "
            "assumes one wording per New Zealand category; add a per-row "
            "rule if this ever legitimately varies."
        )
    (life_cycle,) = life_cycles
    if life_cycle not in LIFE_CYCLE_ANCHOR:
        raise SystemExit(f"No anchor mapping for life cycle wording {life_cycle!r}.")
    return life_cycle


def _resolve_stage_values(
    *,
    metric: str,
    anchor_nz_sector: str,
    client_total: Decimal,
    refed_food: str,
    refed_upstream: dict[tuple[str, str, str], Decimal],
    primary_production_override: Decimal | None,
    primary_production_note: str,
) -> tuple[dict[str, Decimal], dict[str, Decimal], dict[str, str | None], Decimal]:
    """Compute the six New Zealand sector values for one (food, metric).

    Returns (final values, raw candidates before clamping, which sectors
    were clamped, the scale factor used). See the module docstring,
    "ENFORCING THE NON-DECREASING INVARIANT", for the algorithm.
    """
    anchor_refed_sector = REFED_SECTOR_FOR_NZ_SECTOR[anchor_nz_sector]
    anchor_raw = refed_upstream[(refed_food, anchor_refed_sector, metric)]
    scale = client_total / anchor_raw

    candidates: dict[str, Decimal] = {}
    for nz_sector in ("processing", "wholesale_retail", "consumer_household",
                       "consumer_hospitality", "consumer_institution"):
        refed_sector = REFED_SECTOR_FOR_NZ_SECTOR[nz_sector]
        candidates[nz_sector] = refed_upstream[(refed_food, refed_sector, metric)] * scale

    if primary_production_override is not None:
        candidates["primary_production"] = primary_production_override
    else:
        raw_farm = refed_upstream.get((refed_food, "refed_farm", metric))
        if raw_farm is None:
            raise SystemExit(
                f"No ReFED Farm value for {refed_food!r}/{metric!r} and no "
                "public-data override supplied -- this category needs one "
                "or the other."
            )
        candidates["primary_production"] = raw_farm * scale

    pre_anchor_chain = ["primary_production", "processing", "wholesale_retail"]
    if anchor_nz_sector == "consumer_household":
        pre_anchor_chain.append("consumer_household")
    assert pre_anchor_chain[-1] == anchor_nz_sector

    final: dict[str, Decimal] = {anchor_nz_sector: client_total}
    clamped: dict[str, str | None] = {anchor_nz_sector: None}
    next_value = client_total
    for nz_sector in reversed(pre_anchor_chain[:-1]):
        raw_candidate = candidates[nz_sector]
        value = min(raw_candidate, next_value)
        clamped[nz_sector] = "down" if value != raw_candidate else None
        final[nz_sector] = value
        next_value = value

    floor = final["wholesale_retail"]
    for nz_sector in CONSUMER_BRANCHES:
        if nz_sector in final:
            continue
        raw_candidate = candidates[nz_sector]
        value = max(raw_candidate, floor)
        clamped[nz_sector] = "up" if value != raw_candidate else None
        final[nz_sector] = value

    return final, candidates, clamped, scale


def _assert_non_decreasing(nz_food: str, metric: str, final: dict[str, Decimal]) -> None:
    chain = ["primary_production", "processing", "wholesale_retail"]
    for a, b in zip(chain, chain[1:]):
        if final[a] > final[b]:
            raise SystemExit(
                f"{nz_food}/{metric}: {a} ({final[a]}) > {b} ({final[b]}); "
                "non-decreasing invariant violated after clamping."
            )
    for branch in CONSUMER_BRANCHES:
        if final["wholesale_retail"] > final[branch]:
            raise SystemExit(
                f"{nz_food}/{metric}: wholesale_retail ({final['wholesale_retail']}) "
                f"> {branch} ({final[branch]}); non-decreasing invariant violated "
                "after clamping."
            )


def _build_category_rows(
    nz_food: str,
    client_foods: list[str],
    refed_upstream: dict[tuple[str, str, str], Decimal],
) -> list[dict]:
    rows: list[dict] = []
    refed_food = NZ_TO_REFED_FOOD_SHAPE[nz_food]
    client_rows = [food_row(name) for name in client_foods]
    life_cycle = _life_cycle_for(client_rows)
    anchor_nz_sector = LIFE_CYCLE_ANCHOR[life_cycle]

    for metric, attr in (("co2e", "co2e_per_kg"), ("water", "water_l_per_kg")):
        values = [getattr(r, attr) for r in client_rows]
        client_total = mean(values)
        components = "; ".join(
            f"{r.food} ({r.category}) = {getattr(r, attr)} [{r.life_cycle}]"
            for r in client_rows
        )
        agg_note = (
            f"mean of {len(values)} client row(s): {components} -> "
            f"unweighted mean {client_total}"
            if len(values) > 1
            else f"client row: {components} (used directly, no aggregation)"
        )

        pp_override: Decimal | None = None
        pp_note = ""
        if (refed_food, "refed_farm", metric) not in refed_upstream:
            if metric == "co2e":
                sources = FOOD_CO2E_FARM_SHARE_SOURCES[nz_food]
                shares = [owid_farm_share(p) for p in sources]
                share = mean(shares)
                pp_override = client_total * share
                pp_note = (
                    f"No ReFED Farm-stage value published for {refed_food}. "
                    f"Filled from Poore & Nemecek (2018) via Our World in "
                    f"Data (public_farm_share_source_data.py, read "
                    f"2026-09-05): farm-share of {', '.join(sources)} = "
                    f"{', '.join(str(s) for s in shares)} -> unweighted mean "
                    f"{share}. {client_total} x {share} = "
                    f"{client_total * share} -> {q(client_total * share)}."
                )
            else:
                share = WATER_FARM_SHARE_ASSUMPTION
                pp_override = client_total * share
                pp_note = (
                    f"No ReFED Farm-stage value published for {refed_food}, "
                    "and no public per-stage water breakdown was found "
                    "(see public_farm_share_source_data.py). STATED "
                    f"ASSUMPTION, not a citation: primary production is "
                    f"assumed to account for {share} of the client's total "
                    "water figure, on the general finding that irrigation "
                    f"dominates agricultural freshwater withdrawal. "
                    f"{client_total} x {share} = {client_total * share} -> "
                    f"{q(client_total * share)}."
                )

        final, candidates, clamped, scale = _resolve_stage_values(
            metric=metric,
            anchor_nz_sector=anchor_nz_sector,
            client_total=client_total,
            refed_food=refed_food,
            refed_upstream=refed_upstream,
            primary_production_override=pp_override,
            primary_production_note=pp_note,
        )
        _assert_non_decreasing(nz_food, metric, final)
        stored_anchor = qd(final[anchor_nz_sector])
        if stored_anchor != qd(client_total):
            raise SystemExit(
                f"{nz_food}/{metric}: anchor {anchor_nz_sector} stored as "
                f"{stored_anchor}, expected exactly {qd(client_total)}."
            )
        for nz_sector, refed_sector in REFED_SECTOR_FOR_NZ_SECTOR.items():
            value = final[nz_sector]
            if value == 0:
                raise SystemExit(
                    f"{nz_food}/{metric}/{nz_sector}: resolved to zero; a "
                    "gap must be filled from a source, never a silent zero."
                )
            if nz_sector == "primary_production" and pp_note:
                stage_note = pp_note
            else:
                raw_value = refed_upstream[(refed_food, refed_sector, metric)]
                stage_note = (
                    f"ReFED {refed_food} {refed_sector} stage ({raw_value}) x "
                    f"scale {scale} (= client total {client_total} / anchor "
                    f"{REFED_SECTOR_FOR_NZ_SECTOR[anchor_nz_sector]} value "
                    f"{refed_upstream[(refed_food, REFED_SECTOR_FOR_NZ_SECTOR[anchor_nz_sector], metric)]}) "
                    f"= {candidates[nz_sector]}."
                )
            if clamped[nz_sector] == "down":
                stage_note += (
                    f" CLAMPED DOWN to {value} to preserve the "
                    "non-decreasing cumulative-footprint invariant (see "
                    "module docstring, 'ENFORCING THE NON-DECREASING "
                    f"INVARIANT'): the raw candidate "
                    f"({candidates.get(nz_sector, pp_override)}) exceeded "
                    "the next stage's resolved value, which would have made "
                    "the footprint decrease later in the chain."
                )
            elif clamped[nz_sector] == "up":
                stage_note += (
                    f" CLAMPED UP to {value} to preserve the non-decreasing "
                    "cumulative-footprint invariant (see module docstring, "
                    "'ENFORCING THE NON-DECREASING INVARIANT'): the raw "
                    f"candidate ({candidates[nz_sector]}) was lower than "
                    "wholesale_retail's resolved value, which a consumer-"
                    "stage sector may not fall below."
                )
            anchor_note = (
                f" This is the anchor stage (client's own {life_cycle!r} "
                "total, stored exactly)." if nz_sector == anchor_nz_sector else ""
            )
            rows.append({
                "sector": nz_sector,
                "food_category": nz_food,
                "destination": None,
                "metric": metric,
                "value_per_kg": q(value),
                "source_note": (
                    f"Rawtec farm-to-{'fork' if life_cycle == 'Farm to Fork' else 'shelf'} "
                    f"total for {nz_food}: {agg_note}. {stage_note}{anchor_note}"
                ),
                "data_quality": (
                    "derived-client-public-data"
                    if nz_sector == "primary_production" and pp_note
                    else "derived-client-refed-shape"
                ),
            })

        for nz_sector in ALL_NZ_SECTORS:
            rows.append({
                "sector": nz_sector,
                "food_category": nz_food,
                "destination": "prevention",
                "metric": metric,
                "value_per_kg": q(Decimal(0)),
                "source_note": (
                    "Zero by definition. 'prevention' is the mandatory "
                    "100% offset (contract §O-7): food that was never "
                    "produced in excess carries no upstream footprint. "
                    "Not a client or ReFED figure."
                ),
                "data_quality": "definitional",
            })
    return rows


def _build_staples_rows(refed_upstream: dict[tuple[str, str, str], Decimal]) -> list[dict]:
    """`staples` has no client row at all -- filled entirely from ReFED's
    Dry Goods figures, unscaled (no client total to anchor against). See
    the module docstring, "STAPLES".
    """
    rows: list[dict] = []
    refed_food = NZ_TO_REFED_FOOD_SHAPE["staples"]

    for metric in ("co2e", "water"):
        raw = {
            nz_sector: refed_upstream[(refed_food, refed_sector, metric)]
            for nz_sector, refed_sector in REFED_SECTOR_FOR_NZ_SECTOR.items()
        }

        final: dict[str, Decimal] = {}
        clamped: dict[str, bool] = {}
        running: Decimal | None = None
        for nz_sector in ("primary_production", "processing", "wholesale_retail"):
            candidate = raw[nz_sector]
            value = candidate if running is None else max(candidate, running)
            clamped[nz_sector] = value != candidate
            final[nz_sector] = value
            running = value
        floor = final["wholesale_retail"]
        for nz_sector in CONSUMER_BRANCHES:
            candidate = raw[nz_sector]
            value = max(candidate, floor)
            clamped[nz_sector] = value != candidate
            final[nz_sector] = value

        _assert_non_decreasing("staples", metric, final)

        for nz_sector, refed_sector in REFED_SECTOR_FOR_NZ_SECTOR.items():
            value = final[nz_sector]
            if value == 0:
                raise SystemExit(
                    f"staples/{metric}/{nz_sector}: resolved to zero; a gap "
                    "must be filled from a source, never a silent zero."
                )
            note = (
                f"No client (Rawtec) row exists for 'staples'. Filled "
                f"entirely from ReFED's own Dry Goods figures (the closest "
                f"available ReFED category to a New Zealand 'staples/pantry "
                f"basics' grouping -- also used for nuts_seeds), used "
                f"directly with no client total to scale against: ReFED "
                f"{refed_food} {refed_sector} stage = {raw[nz_sector]}."
            )
            if clamped[nz_sector]:
                note += (
                    f" CLAMPED to {value} (forward running-maximum, no "
                    "anchor to work back from -- see module docstring, "
                    "'ENFORCING THE NON-DECREASING INVARIANT'): ReFED's own "
                    f"raw value ({raw[nz_sector]}) was lower than an earlier "
                    "stage's (or, for a consumer-stage sector, lower than "
                    "wholesale_retail's) resolved value."
                )
            rows.append({
                "sector": nz_sector,
                "food_category": "staples",
                "destination": None,
                "metric": metric,
                "value_per_kg": q(value),
                "source_note": note,
                "data_quality": "derived-refed-unanchored",
            })

        for nz_sector in ALL_NZ_SECTORS:
            rows.append({
                "sector": nz_sector,
                "food_category": "staples",
                "destination": "prevention",
                "metric": metric,
                "value_per_kg": q(Decimal(0)),
                "source_note": (
                    "Zero by definition. 'prevention' is the mandatory "
                    "100% offset (contract §O-7): food that was never "
                    "produced in excess carries no upstream footprint. "
                    "Not a client or ReFED figure."
                ),
                "data_quality": "definitional",
            })
    return rows


def _build_ch4_upstream_rows(refed_upstream: dict[tuple[str, str, str], Decimal]) -> list[dict]:
    """`ch4` upstream, all ten New Zealand food categories (nine plus
    `staples`). See the module docstring, "CH4: FILLED FROM REFED ALONE...".

    Neither client table carries a methane column, so there is no client
    total to anchor *any* category to -- every one is built the unanchored
    way already used for `staples`' co2e/water (`_build_staples_rows`):
    ReFED's own absolute per-stage values, scale exactly 1, non-decreasing
    enforced by a forward running-maximum clamp. Reuses
    `NZ_TO_REFED_FOOD_SHAPE` and `REFED_SECTOR_FOR_NZ_SECTOR` exactly as
    built for co2e/water -- no second mapping.
    """
    rows: list[dict] = []
    metric = "ch4"

    for nz_food, refed_food in NZ_TO_REFED_FOOD_SHAPE.items():
        raw = {
            nz_sector: refed_upstream.get((refed_food, refed_sector, metric))
            for nz_sector, refed_sector in REFED_SECTOR_FOR_NZ_SECTOR.items()
        }
        for nz_sector in ("processing", "wholesale_retail", *CONSUMER_BRANCHES):
            if raw[nz_sector] is None:
                raise SystemExit(
                    f"{nz_food}/{metric}/{nz_sector}: no ReFED value "
                    "published; only primary_production is ever expected to "
                    "be missing (see module docstring, 'FIVE OF REFED'S "
                    "NINE FOOD CATEGORIES...')."
                )

        no_farm_data = raw["primary_production"] is None
        final: dict[str, Decimal] = {}
        clamped: dict[str, str | None] = {}

        if no_farm_data:
            final["processing"] = raw["processing"]
            clamped["processing"] = None
        else:
            running = raw["primary_production"]
            final["primary_production"] = running
            clamped["primary_production"] = None
            value = max(raw["processing"], running)
            clamped["processing"] = "running-max" if value != raw["processing"] else None
            final["processing"] = value

        running = final["processing"]
        value = max(raw["wholesale_retail"], running)
        clamped["wholesale_retail"] = "running-max" if value != raw["wholesale_retail"] else None
        final["wholesale_retail"] = value

        if no_farm_data:
            final["primary_production"] = final["processing"]
            clamped["primary_production"] = "no-data-floor"

        floor = final["wholesale_retail"]
        for nz_sector in CONSUMER_BRANCHES:
            candidate = raw[nz_sector]
            value = max(candidate, floor)
            clamped[nz_sector] = "running-max" if value != candidate else None
            final[nz_sector] = value

        _assert_non_decreasing(nz_food, metric, final)

        for nz_sector, refed_sector in REFED_SECTOR_FOR_NZ_SECTOR.items():
            value = final[nz_sector]
            if value == 0:
                raise SystemExit(
                    f"{nz_food}/{metric}/{nz_sector}: resolved to zero; a "
                    "gap must be filled from a source, never a silent zero."
                )
            if clamped[nz_sector] == "no-data-floor":
                note = (
                    f"No client (Rawtec) ch4 column exists at all, and no "
                    f"ReFED Farm-stage methane value is published for "
                    f"{refed_food} (the same structural gap ReFED leaves "
                    f"for co2e, but here with no public farm-share source "
                    f"to fill it -- see module docstring). primary_"
                    f"production is floored at processing's own resolved "
                    f"value ({value}) -- the highest figure the non-"
                    f"decreasing invariant permits without inventing a "
                    f"number no source gives. LIKELY AN UNDERSTATEMENT for "
                    f"meat- and dairy-adjacent categories, where on-farm "
                    f"enteric methane is typically the dominant term; "
                    f"flagged, not measured."
                )
            else:
                raw_value = raw[nz_sector]
                note = (
                    f"No client (Rawtec) ch4 column exists. Filled entirely "
                    f"from ReFED's own absolute methane figures (no client "
                    f"total to anchor a scale factor against, so scale = 1, "
                    f"matching how 'staples' is built for co2e/water): "
                    f"ReFED {refed_food} {refed_sector} stage = {raw_value}."
                )
                if clamped[nz_sector] == "running-max":
                    note += (
                        f" CLAMPED to {value} (forward running-maximum; see "
                        "module docstring, 'ENFORCING THE NON-DECREASING "
                        f"INVARIANT'): ReFED's own raw value ({raw_value}) "
                        "was lower than an earlier stage's (or, for a "
                        "consumer-stage sector, lower than wholesale_"
                        "retail's) resolved value."
                    )
            rows.append({
                "sector": nz_sector,
                "food_category": nz_food,
                "destination": None,
                "metric": metric,
                "value_per_kg": q(value),
                "source_note": note,
                "data_quality": (
                    "derived-refed-no-farm-floor"
                    if clamped[nz_sector] == "no-data-floor"
                    else "derived-refed-unanchored"
                ),
            })

        for nz_sector in ALL_NZ_SECTORS:
            rows.append({
                "sector": nz_sector,
                "food_category": nz_food,
                "destination": "prevention",
                "metric": metric,
                "value_per_kg": q(Decimal(0)),
                "source_note": (
                    "Zero by definition. 'prevention' is the mandatory "
                    "100% offset (contract §O-7): food that was never "
                    "produced in excess carries no upstream footprint. "
                    "Not a client or ReFED figure."
                ),
                "data_quality": "definitional",
            })
    return rows


# ---------------------------------------------------------------------------
# `land`: the client's own column, inverted, checked against a public source.
# ---------------------------------------------------------------------------
#: Square metres in a hectare, and kilograms in a tonne. Named rather than
#: inlined so the derivation below reads as the arithmetic it is.
M2_PER_HA = Decimal(10000)
KG_PER_TONNE = Decimal(1000)


def land_m2_per_kg_from_yield(yield_t_per_ha: Decimal) -> Decimal:
    """The client's land column is a YIELD, not a footprint. Invert it.

    Table 1 publishes land as **t/ha** -- tonnes of food produced per hectare.
    Used as a factor exactly as printed it would be upside down: a *higher*
    number would mean *more* land, when it means the opposite. What the
    `land` metric reports is land occupation per kilogram of food, so:

        1 kg                        = 0.001 t
        0.001 t / (Y t/ha)          = 0.001/Y ha
        0.001/Y ha x 10,000 m2/ha   = 10/Y  m2

    so ``land_m2_per_kg = 10 / yield_t_per_ha``. The line below performs it in
    those three steps rather than as the collapsed ``10 / Y`` so that each
    factor can be read against the sentence above it, and entirely on
    ``Decimal`` -- never through ``float`` (contract section 1.2).

    A yield of zero is not a number this can invert, and the client publishes
    none; it raises rather than producing an infinity that would propagate as
    a plausible-looking factor.
    """
    if yield_t_per_ha <= 0:
        raise SystemExit(
            f"Land yield {yield_t_per_ha} t/ha cannot be inverted into a "
            "per-kilogram footprint. The client publishes no zero or negative "
            "yield; if one now exists, it needs a ruling, not a division."
        )
    return (Decimal(1) / KG_PER_TONNE) / yield_t_per_ha * M2_PER_HA


#: The client food row -> the Our World in Data product row(s) whose published
#: land use is used to check it (`public_land_use_source_data.py`). Several
#: rows: unweighted mean, the same rule and the same stated reason as every
#: other aggregation in this draft -- no production weights exist.
#:
#: Chosen by the closest available match, and two of them deliberately reuse a
#: mapping this script already makes elsewhere: the beverages row draws Wine,
#: Coffee and Soy milk, exactly as `FOOD_CO2E_FARM_SHARE_SOURCES` does, and
#: `Other meat` -- the client's own catch-all -- draws the unweighted mean of
#: all four named meats, which is the same construction
#: `NZ_FOOD_CATEGORY_SOURCES["meat"]` uses for a meat not otherwise specified.
#:
#: **A client row absent from this mapping has no public counterpart**, not a
#: missing entry: `Fats`, `Sauces Spreads Dips`, `Herbs/Spices`, `Snack Foods
#: and desserts`, `Other Food Types` and `General mixed food product` are
#: composites or catch-alls with no single published product behind them, and
#: inventing a basket for each would be the invention this whole comparison
#: exists to avoid. Those rows keep the client's figure and their `source_note`
#: says that no public figure was available to check it against.
LAND_PUBLIC_PROXIES: dict[str, tuple[str, ...]] = {
    "Bread": ("Wheat & Rye",),
    "Bakery": ("Wheat & Rye",),
    "Grains": ("Wheat & Rye", "Rice", "Maize", "Barley", "Oatmeal"),
    "Cheese": ("Cheese",),
    "Milk": ("Milk",),
    "Cream": ("Milk",),
    "Butter": ("Milk",),
    "Yoghurt": ("Milk",),
    "Other dairy": ("Milk",),
    "Eggs": ("Eggs",),
    "Drinks/Beverages (excluding dairy)": ("Wine", "Coffee", "Soy milk"),
    "Fruit": ("Apples", "Bananas", "Berries & Grapes", "Citrus Fruit", "Other Fruit"),
    "Vegetable": ("Brassicas", "Onions & Leeks", "Other Vegetables",
                  "Root Vegetables", "Tomatoes"),
    "Red Meat": ("Beef (beef herd)", "Lamb & Mutton"),
    "Pork": ("Pig Meat",),
    "Poultry": ("Poultry Meat",),
    "Other meat": ("Beef (beef herd)", "Lamb & Mutton", "Pig Meat", "Poultry Meat"),
    "Seafood": ("Fish (farmed)", "Prawns (farmed)"),
    "Nuts and seeds": ("Nuts", "Groundnuts"),
    "Sweeteners": ("Beet Sugar", "Cane Sugar"),
}

#: The one stated rule. Where the client-derived figure and the public one
#: differ by this factor or more, the public one is taken; otherwise the
#: client's is kept. Either way the row's `source_note` names which it is and
#: why, because a quiet average and a quietly-kept implausible figure hide the
#: same decision.
LAND_SUBSTITUTION_RATIO = Decimal(10)

#: `staples` has no client row of its own for co2e or water and is filled from
#: ReFED there -- but ReFED publishes no land figure at all, for any category,
#: so that route does not exist here.
#:
#: What does exist is this repository's own item vocabulary. `admin/seed.py`'s
#: `FOOD_ITEMS` files the client's six home-less pantry rows under `staples`
#: -- Fats, Sauces/Spreads/Dips, Herbs/Spices, Snack Foods and desserts,
#: Sweeteners and Other Food Types -- and says so in its own comment. So
#: `staples`' land is the unweighted mean of those six client rows, resolved
#: the same way every other row is (client figure unless the public one
#: disagrees by ten times or more). That is the client's own data reaching
#: `staples` through this repository's own documented mapping, which is a
#: better answer than a basket invented here.
#:
#: **`Eggs` is excluded, deliberately.** `seed.py` files the `eggs` *item*
#: under `staples` as well, but this draft's co2e and water leave the client's
#: Eggs row out of every category (section 3.2 of the provenance document:
#: reported, not silently absorbed into a neighbour), and pulling it into
#: `staples` for land alone would price one metric on a membership the other
#: two do not use. Its comparison is computed and printed anyway, so the
#: exclusion is visible rather than inferred.
LAND_STAPLES_CLIENT_ROWS = (
    "Fats", "Sauces Spreads Dips", "Herbs/Spices",
    "Snack Foods and desserts", "Sweeteners", "Other Food Types",
)


class LandComparison(NamedTuple):
    """One client food row, both figures, and which was taken."""
    food: str
    yield_t_per_ha: Decimal
    client_m2_per_kg: Decimal
    public_m2_per_kg: Decimal | None
    public_sources: tuple[str, ...]
    ratio: Decimal | None
    taken: str  # "client" or "public"


def land_cell_duplicates(food: str) -> tuple[str, ...]:
    """Other client food rows printing the identical land cell.

    Detected mechanically rather than listed by hand, because the reason it
    matters is not that somebody once noticed one: several of the client's
    water and land cells are identical to the last decimal across unrelated
    foods, and `Red Meat` and `Nuts and seeds` sharing `0.22` is a copy rather
    than a measurement. A duplicate is not by itself wrong -- Cheese, Cream,
    Butter and Yoghurt sharing a dairy figure is plausible -- so this does not
    change any value. It puts the fact in the row's own `source_note` so that
    a reader can weigh it.
    """
    this = food_row(food).land_t_per_ha
    return tuple(
        other.food for other in TABLE1
        if other.food != food and other.land_t_per_ha == this
    )


def land_comparison(food: str) -> LandComparison:
    """Both figures for one client food row, and the stated rule applied."""
    row = food_row(food)
    client = land_m2_per_kg_from_yield(row.land_t_per_ha)
    sources = LAND_PUBLIC_PROXIES.get(food, ())
    if not sources:
        return LandComparison(food, row.land_t_per_ha, client, None, (), None, "client")
    public = mean([owid_land_use(name) for name in sources])
    if public <= 0:
        raise SystemExit(
            f"Public land figure for {food!r} resolved to {public}; a zero "
            "cannot be compared by ratio and must not be seeded."
        )
    ratio = max(client, public) / min(client, public)
    taken = "public" if ratio >= LAND_SUBSTITUTION_RATIO else "client"
    return LandComparison(food, row.land_t_per_ha, client, public, sources, ratio, taken)


def land_value_for(food: str) -> tuple[Decimal, str]:
    """The land figure this draft seeds for one client food, and its note."""
    c = land_comparison(food)
    duplicates = land_cell_duplicates(food)
    derivation = (
        f"Client (Rawtec) table 1 land column for {food!r}: "
        f"{c.yield_t_per_ha} t/ha, which is a YIELD, not a footprint. "
        f"Inverted to land occupation per kilogram: "
        f"1 kg = 0.001 t; 0.001 t / {c.yield_t_per_ha} t/ha = "
        f"{Decimal(1) / KG_PER_TONNE / c.yield_t_per_ha} ha; "
        f"x 10,000 m2/ha = {c.client_m2_per_kg} m2/kg."
    )
    if duplicates:
        derivation += (
            f" NOTE: the client prints this identical land cell "
            f"({c.yield_t_per_ha} t/ha) for {', '.join(repr(d) for d in duplicates)} "
            "as well. Agreement to the last decimal across foods that are not "
            "the same thing reads as a copy rather than a measurement. No "
            "value is changed on that basis -- only the stated ratio rule "
            "below decides -- but it is recorded here so a reader can weigh "
            "it."
        )
    if c.public_m2_per_kg is None:
        note = (
            f"{derivation} KEPT, UNCHECKED: no single public product row "
            "corresponds to this client row -- it is a composite or a "
            "catch-all -- so the client figure could not be compared against "
            "Poore & Nemecek (2018) the way the other rows were. It is the "
            "client's own figure and nothing here corroborates it."
        )
        return c.client_m2_per_kg, note
    comparison = (
        f"Checked against Poore & Nemecek (2018) via Our World in Data "
        f"(public_land_use_source_data.py, read 2026-09-23): "
        f"{', '.join(c.public_sources)} = "
        f"{', '.join(str(owid_land_use(n)) for n in c.public_sources)} m2/kg "
        f"-> unweighted mean {c.public_m2_per_kg} m2/kg. The two differ by a "
        f"factor of {c.ratio}."
    )
    if c.taken == "public":
        note = (
            f"{derivation} {comparison} That is at or beyond the stated "
            f"{LAND_SUBSTITUTION_RATIO}x threshold, so THE PUBLIC FIGURE IS "
            "TAKEN and the client's inverted figure is not seeded: a "
            "disagreement this large is a defect in one of the two columns, "
            "and the public one is the one that can be checked. It is a "
            "GLOBAL MEAN, not a New Zealand measurement."
        )
        return c.public_m2_per_kg, note
    note = (
        f"{derivation} {comparison} That is below the stated "
        f"{LAND_SUBSTITUTION_RATIO}x threshold, so THE CLIENT'S FIGURE IS "
        "KEPT. The public figure is a global mean and this is the client's "
        "own New Zealand-facing document; where the two broadly agree the "
        "client's is the one this draft is built from."
    )
    return c.client_m2_per_kg, note


def _build_land_upstream_rows() -> list[dict]:
    """`land` upstream, all ten New Zealand food categories.

    **Flat across all six sectors, and that is the modelling decision this
    function turns on.** co2e, water and ch4 are built as a *cumulative*
    footprint -- a kilogram wasted at retail carries the emissions of
    processing and transport that a kilogram wasted at the farm gate does not.
    Land does not accumulate that way: the land was occupied to grow the food,
    and the same kilogram carries the same land wherever along the chain it is
    thrown away. Both sources here are farm-gate quantities (the client's t/ha
    is a field yield; Poore & Nemecek's m2/kg is land used to produce one
    kilogram) and neither publishes a downstream land term at all, so there is
    nothing to escalate a later stage with.

    This is very likely a slight UNDERSTATEMENT for the later stages, for the
    reason the cumulative construction exists: a kilogram that reaches a
    supermarket shelf embodies rather more than a kilogram of farm output,
    because some was lost on the way. No source here quantifies that for land,
    so it is flagged in every row's `source_note` rather than estimated.

    There is no ReFED shape to anchor against either -- ReFED publishes no
    land figure for any category or destination -- so none of the anchoring,
    scaling or clamping machinery the other metrics use applies. The
    non-decreasing invariant is satisfied trivially and is still asserted.
    """
    rows: list[dict] = []
    metric = "land"

    for nz_food in NZ_TO_REFED_FOOD_SHAPE:  # the canonical ten
        if nz_food == "staples":
            client_foods = list(LAND_STAPLES_CLIENT_ROWS)
            basis = (
                "'staples' has no client (Rawtec) row of its own and ReFED "
                "publishes no land figure for any category, so the route used "
                "for its co2e and water does not exist here. It is built "
                "instead from the six client rows admin/seed.py's FOOD_ITEMS "
                "files under 'staples' (Fats, Sauces Spreads Dips, "
                "Herbs/Spices, Snack Foods and desserts, Sweeteners, Other "
                "Food Types) -- the client's own data reaching 'staples' "
                "through this repository's own item mapping. The client's "
                "Eggs row is filed there too and is deliberately excluded, "
                "because this draft's co2e and water leave Eggs out of every "
                "category (provenance document section 3.2) and pricing one "
                "metric on a membership the other two do not use would be "
                "inconsistent."
            )
        else:
            client_foods = list(NZ_FOOD_CATEGORY_SOURCES[nz_food])
            basis = ""

        resolved = [land_value_for(name) for name in client_foods]
        values = [value for value, _ in resolved]
        value = mean(values)
        if value <= 0:
            raise SystemExit(
                f"{nz_food}/{metric}: resolved to {value}; a gap must be "
                "filled from a source, never a silent zero."
            )

        components = "; ".join(
            f"[{name}] {note}" for name, (_, note) in zip(client_foods, resolved)
        )
        aggregation = (
            f"Unweighted mean of {len(values)} client row(s) -- no production "
            f"weights were supplied, the same stated reason as every other "
            f"aggregation in this draft -- = {value} m2/kg."
            if len(values) > 1
            else "Single client row, used directly with no aggregation."
        )
        note = (
            f"{basis + ' ' if basis else ''}"
            f"{aggregation} SAME VALUE FOR ALL SIX SECTORS: land occupation "
            "is a property of growing the food, not of how far down the "
            "supply chain it is wasted, so unlike co2e, water and ch4 it is "
            "not built as a cumulative footprint. Both sources are farm-gate "
            "quantities and neither publishes a downstream land term. This is "
            "very likely a slight understatement for the later stages, since "
            "a kilogram on a shelf embodies more than a kilogram of farm "
            "output; nothing available quantifies that for land, so it is "
            "flagged rather than estimated. Per-row working: " + components
        )

        final = {nz_sector: value for nz_sector in ALL_NZ_SECTORS}
        _assert_non_decreasing(nz_food, metric, final)

        data_quality = (
            "derived-public-substituted"
            if any(land_comparison(name).taken == "public" for name in client_foods)
            else "derived-client"
        )

        for nz_sector in ALL_NZ_SECTORS:
            rows.append({
                "sector": nz_sector,
                "food_category": nz_food,
                "destination": None,
                "metric": metric,
                "value_per_kg": q(value),
                "source_note": note,
                "data_quality": data_quality,
            })

        for nz_sector in ALL_NZ_SECTORS:
            rows.append({
                "sector": nz_sector,
                "food_category": nz_food,
                "destination": "prevention",
                "metric": metric,
                "value_per_kg": q(Decimal(0)),
                "source_note": (
                    "Zero by definition. 'prevention' is the mandatory "
                    "100% offset (contract O-7): food that was never "
                    "produced in excess occupies no land. Not a client or "
                    "public figure."
                ),
                "data_quality": "definitional",
            })
    return rows


# ---------------------------------------------------------------------------
# THE ITEM LEVEL: THE CLIENT'S OWN PER-FOOD FIGURES, AS A RELATIVITY ON THIS
# SET'S OWN CATEGORY FACTOR
# ---------------------------------------------------------------------------
#: Which `admin/seed.py` `food_item` code draws on which client table-1 row,
#: per New Zealand food category, **in the seed's own `sort_order`**.
#:
#: These are the four categories the client actually subdivided. The other five
#: -- `beverages`, `fruit`, `vegetables`, `seafood`, `nuts_seeds` -- have one
#: client row each, which IS the category (contract v1.72 gave them a
#: vocabulary of New Zealand foods that the client's table says nothing about),
#: and `standard_mix` has no vocabulary at all. **The twenty-seven foods v1.72
#: added get no row here, and that is the correct answer rather than a gap**:
#: section 2.2's upstream chain falls a food with no row of its own through to
#: its category's factor -- a defined, meaningful average -- and section 7.3c's
#: fallback disclosure then tells the reader on the page, in the text download
#: and on the PDF that that is what happened. Inventing a figure for Kiwifruit
#: would be the invention this whole draft exists to avoid.
#:
#: **Kept in sync with `admin/seed.py`'s `FOOD_ITEMS` by hand**, for the reason
#: `ALL_NZ_DESTINATIONS` is: this script must not import `admin.seed`, which
#: imports SQLAlchemy (`docs/architecture.md`, "db/repository.py is the only
#: code that may touch the database"). Three things catch a drifted entry
#: rather than one: `_assert_item_lists_match_the_category_construction()`
#: below, `load_upstream_factors_draft.py`'s `_lookup` (a hard stop on a
#: `food_item` code the taxonomy does not have), and `FactorBundle.validate()`'s
#: own check that an upstream row's `food_item` belongs to the row's
#: `food_category`. `tests/admin/test_food_item_seed.py` asserts the agreement
#: with the seed directly.
#:
#: **`Eggs` is excluded from `staples`, deliberately, and it is the entry most
#: likely to be "corrected".** `seed.py` files the `eggs` item under `staples`,
#: but this draft leaves the client's Eggs row out of every category for co2e
#: and water (provenance document section 3.2: reported, not silently absorbed
#: into a neighbour) and `LAND_STAPLES_CLIENT_ROWS` excludes it for land for
#: that same reason. Adding it here would price the item level on a membership
#: none of the three category figures uses, and would break the
#: mean-preservation property below -- `staples`' land factor is the mean of
#: six rows, not seven. So `eggs` carries no item row and is priced at the
#: `staples` average, exactly like the twenty-seven foods v1.72 added.
ITEM_LEVEL_CLIENT_FOODS: dict[str, tuple[tuple[str, str], ...]] = {
    "bakery_grains": (
        ("bread", "Bread"),
        ("bakery", "Bakery"),
        ("grains", "Grains"),
    ),
    "dairy": (
        ("cheese", "Cheese"),
        ("milk", "Milk"),
        ("cream", "Cream"),
        ("butter", "Butter"),
        ("yoghurt", "Yoghurt"),
        ("other_dairy", "Other dairy"),
    ),
    "meat": (
        ("red_meat", "Red Meat"),
        ("pork", "Pork"),
        ("poultry", "Poultry"),
        ("other_meat", "Other meat"),
    ),
    "staples": (
        ("fats", "Fats"),
        ("sauces_spreads_dips", "Sauces Spreads Dips"),
        ("herbs_spices", "Herbs/Spices"),
        ("snack_foods_desserts", "Snack Foods and desserts"),
        ("sweeteners", "Sweeteners"),
        ("other_food_types", "Other Food Types"),
    ),
}

#: The metrics an item row can be built for, and the three reasons the other
#: three are absent -- each an absence with a cause, not an oversight:
#:
#:   * **`ch4`**: neither client table carries a methane column at all. Every
#:     `ch4` figure in this draft comes from ReFED, whose finest resolution is
#:     its own nine food *categories* -- there is no per-food ReFED methane
#:     number to take a relativity from, and manufacturing one out of the CO2-eq
#:     relativity would assert that methane's share of a food's footprint is the
#:     same for cheese as for milk, which nothing here supports.
#:   * **`cost`**: has no upstream row anywhere in this set (see "COST" in the
#:     module docstring -- it is the waste levy, charged per tonne at the
#:     destination and identical for every food). The item dimension exists only
#:     on `factor_upstream`, so there is nothing for it to refine.
#:   * **`mass`**: its formula is `qty_kg` and it reads no factor row at all.
ITEM_LEVEL_METRICS = ("co2e", "water", "land")

#: The mean-preservation and client-reproduction checks below both compare
#: values that have been through `DECIMAL(20,10)` at least once, so neither can
#: be an equality. One unit in that column's last place is 1E-10; this allows
#: ten of them, which is the rounding the storage scale makes unavoidable and
#: about four orders of magnitude tighter than any drift a wrong construction
#: would produce (the two drifts actually measured on this data are 0 and
#: 5E-11). It is not a slack allowance:
#: `tests/admin/test_food_item_seed.py` already states the same tolerance the
#: same way for `docker/mock-factors.json`'s own item rows.
ITEM_LEVEL_TOLERANCE = Decimal("1E-9")


def _assert_item_lists_match_the_category_construction() -> None:
    """The item list of each category must be the list its factor was built from.

    This is the whole basis of the mean-preservation property, and it is a
    property of two dicts agreeing rather than of any arithmetic -- so it is
    checked here, before a single value is computed, and it names the two lists
    it compared.

    `bakery_grains`, `dairy` and `meat` are anchored to the unweighted mean of
    their own `NZ_FOOD_CATEGORY_SOURCES` rows, so that is the list. `staples`
    has no client row at all for co2e and water (filled from ReFED Dry Goods --
    see "STAPLES") and its `land` figure is built from
    `LAND_STAPLES_CLIENT_ROWS`, so that is the list there. A food added to one
    and not the other would drift the item mean away from the category figure
    silently, which is exactly the disagreement between "pick Cheese" and
    "decline to pick" that design section 8.2 names as the thing to avoid.
    """
    #: **Checked before the list comparison below**, so that the message a
    #: reader gets explains the decision instead of merely reporting a
    #: mismatch: "Eggs is missing from LAND_STAPLES_CLIENT_ROWS" reads as an
    #: omission unless something says it is not. See the note on
    #: ITEM_LEVEL_CLIENT_FOODS.
    all_named = {
        client_food
        for pairs in ITEM_LEVEL_CLIENT_FOODS.values()
        for _, client_food in pairs
    }
    if "Eggs" in all_named:
        raise SystemExit(
            "The client's 'Eggs' row has been given an item-level factor. It "
            "is excluded on purpose: this draft leaves Eggs out of every "
            "category figure (co2e and water, provenance section 3.2; land, "
            "LAND_STAPLES_CLIENT_ROWS), so an item row for it would be priced "
            "against a category mean it is not part of. If Eggs is to be "
            "included, it has to be included in the category figures first, in "
            "the same commit, with the reason written down."
        )

    for nz_food, pairs in ITEM_LEVEL_CLIENT_FOODS.items():
        named = [client_food for _, client_food in pairs]
        if nz_food == "staples":
            expected = list(LAND_STAPLES_CLIENT_ROWS)
            where = "LAND_STAPLES_CLIENT_ROWS"
        else:
            expected = list(NZ_FOOD_CATEGORY_SOURCES[nz_food])
            where = f"NZ_FOOD_CATEGORY_SOURCES[{nz_food!r}]"
        if sorted(named) != sorted(expected):
            raise SystemExit(
                f"{nz_food}: the item level names client rows {sorted(named)} "
                f"but the category factor is built from {where} = "
                f"{sorted(expected)}. The two must be the same list, or the "
                "unweighted mean of the item rows stops being the category "
                "factor and the calculator gives two different answers for "
                "the same food depending on whether the visitor named it."
            )
        for client_food in named:
            food_row(client_food)  # raises KeyError if the transcription lost it



def _client_item_value(metric: str, client_food: str) -> tuple[Decimal, str]:
    """One client food's own figure for one metric, and how it was arrived at.

    `co2e` and `water` are read straight off table 1. `land` is the figure this
    draft *resolves* for that row -- the client's t/ha inverted, or the public
    Poore & Nemecek figure where the two disagree by ten times or more -- and
    not the raw client cell, because the category factor is the mean of the
    resolved values and the item rows have to be relative to the same thing.
    """
    if metric == "co2e":
        row = food_row(client_food)
        return row.co2e_per_kg, (
            f"client (Rawtec) table 1 CO2-eq column for {client_food!r} = "
            f"{row.co2e_per_kg} kg CO2-eq/kg [{row.life_cycle}]"
        )
    if metric == "water":
        row = food_row(client_food)
        return row.water_l_per_kg, (
            f"client (Rawtec) table 1 water column for {client_food!r} = "
            f"{row.water_l_per_kg} L/kg [{row.life_cycle}]"
        )
    if metric == "land":
        value, note = land_value_for(client_food)
        return value, (
            f"the land figure this draft resolves for {client_food!r} = "
            f"{value} m2/kg. {note}"
        )
    raise SystemExit(
        f"No client per-food figure exists for metric {metric!r}; "
        "ITEM_LEVEL_METRICS and this function must agree."
    )


def _item_data_quality(nz_food: str, metric: str, client_food: str) -> str:
    """`VARCHAR(32)`, and three tags rather than one, so a reader can tell the
    three provenances apart without reading the note."""
    if metric == "land" and land_comparison(client_food).taken == "public":
        return "item-public-substituted"
    if nz_food == "staples" and metric in ("co2e", "water"):
        return "item-client-refed-level"
    return "item-client-relativities"


def _build_item_level_upstream_rows(category_rows: list[dict]) -> list[dict]:
    """One upstream row per (item, sector, metric), for the four categories the
    client subdivided.

    **The construction, in one line.** ::

        item_value(sector) = category_value(sector)
                             x client_figure(food) / mean(client_figures)

    so the unweighted mean of a category's item rows *is* that category's own
    factor, at every sector, for every metric -- which is the property that
    keeps "pick Cheese" and "decline to pick" telling one story. It is the same
    construction `docker/mock-factors.json`'s six dairy rows already use, built
    here from the client's data rather than as local test data.

    **Read off the STORED category value, not recomputed.** The dict below is
    keyed on the rows this build has already written, and the value is parsed
    back out of the `DECIMAL(20,10)` string that will reach the database. A
    second computation of the category figure here would be a second place for
    it to drift, and the mean the calculator can be held to is the mean of what
    is actually stored.

    **Where each of the three metrics ends up, and why they differ.**

      * `land` is built as the mean of the same per-food figures at every
        sector (see `_build_land_upstream_rows`), so the relativity collapses
        and each item row lands exactly on that food's own resolved figure --
        the client's inverted t/ha, or Poore & Nemecek's where the stated
        ten-times rule replaced it.
      * `co2e` and `water` for `bakery_grains`, `dairy` and `meat` land exactly
        on the client's own printed figure **at the anchor sector** -- the stage
        the client's own "Life cycle covered" column measures up to, where the
        category factor is pinned to the client's own mean -- and carry ReFED's
        cumulative shape at the other five.
      * `co2e` and `water` for `staples` carry the client's relativity on a
        level ReFED supplies, because the client has no `staples` row at all.
        Only the *spread* between those six foods is the client's there, and
        every such row's note says so.

    No `prevention` override is written for an item row, and that is section
    2.2's ordering rather than an omission: the four candidates are tried
    destination-first, so a prevented line finds the category-level
    `(NULL item, prevention)` zero before it could reach an item's generic row.
    `engine/bundle.py::upstream` records the measurement behind that ordering --
    item-first reopened O-7 at 78.9% of the benefit lost.
    """
    _assert_item_lists_match_the_category_construction()
    category_value = {
        (row["food_category"], row["sector"], row["metric"]): Decimal(row["value_per_kg"])
        for row in category_rows
        if row["destination"] is None and row.get("food_item") is None
    }

    rows: list[dict] = []
    for nz_food, pairs in ITEM_LEVEL_CLIENT_FOODS.items():
        for metric in ITEM_LEVEL_METRICS:
            resolved = {code: _client_item_value(metric, name) for code, name in pairs}
            basis = mean([value for value, _ in resolved.values()])
            if basis <= 0:
                raise SystemExit(
                    f"{nz_food}/{metric}: the client figures for this category "
                    f"average {basis}, which cannot be divided by. A "
                    "relativity needs a positive basis."
                )
            spread = "; ".join(f"{name} = {resolved[code][0]}" for code, name in pairs)
            for nz_sector in ALL_NZ_SECTORS:
                key = (nz_food, nz_sector, metric)
                if key not in category_value:
                    raise SystemExit(
                        f"No category-level upstream row for {key} to scale the "
                        "item rows onto. An item row without its category's "
                        "fallback is the one silent zero the item dimension can "
                        "still produce, and "
                        "`refuse_item_rows_without_category_fallback` would "
                        "refuse to publish the set."
                    )
                category = category_value[key]
                for code, client_food in pairs:
                    value, working = resolved[code]
                    relativity = value / basis
                    item_value = category * relativity
                    if item_value <= 0:
                        raise SystemExit(
                            f"{nz_food}/{code}/{nz_sector}/{metric}: resolved "
                            f"to {item_value}; a gap must be filled from a "
                            "source, never a silent zero."
                        )
                    level = (
                        "This set's own 'staples' figure is built from ReFED "
                        "Dry Goods, because the client supplied no 'staples' "
                        "row at all (see 'STAPLES' in "
                        "build_upstream_factors_draft.py), so the LEVEL of this "
                        "row is ReFED's and only the SPREAD between the six "
                        "foods is the client's."
                        if nz_food == "staples" and metric != "land"
                        else "This set's own category figure is anchored to the "
                        "client's own total for this category, so this row is "
                        "the client's own figure carried through that anchoring."
                    )
                    rows.append({
                        "sector": nz_sector,
                        "food_category": nz_food,
                        "food_item": code,
                        "destination": None,
                        "metric": metric,
                        "value_per_kg": q(item_value),
                        "source_note": (
                            "ITEM-LEVEL FACTOR, DERIVED FROM THE CLIENT'S OWN "
                            "TABLE 1. It is NOT a per-food New Zealand "
                            "measurement: no such measurement exists, open item "
                            "O-1 is still open, is_mock is still true and the "
                            "placeholder banner still applies. What the client's "
                            "table supplies is one figure per food row, and this "
                            "row is that figure expressed as a relativity on "
                            "this set's own category factor, so the two cannot "
                            f"disagree. Working: {working}; unweighted mean of "
                            f"the {len(pairs)} client row(s) this category's "
                            f"factor is built from ({spread}) = {basis}; "
                            f"relativity = {relativity}; x this set's own "
                            f"{nz_food}/{nz_sector}/{metric} category factor "
                            f"{category} = {item_value} -> {q(item_value)}. "
                            f"{level} The unweighted mean of the {len(pairs)} "
                            "item rows for this (sector, category, metric) is "
                            "therefore the category factor itself, asserted on "
                            "every build by "
                            "_assert_item_level_preserves_the_category_mean(). "
                            "No prevention override is written for an item row: "
                            "section 2.2 tries its four candidates "
                            "destination-first, so the category-level prevention "
                            "zero already covers every food under it "
                            "(engine/bundle.py::upstream)."
                        ),
                        "data_quality": _item_data_quality(nz_food, metric, client_food),
                    })
    return rows


def _assert_item_level_preserves_the_category_mean(data: dict) -> None:
    """The category factors ARE the averages of these foods -- checked, not said.

    An item set whose mean has drifted from its own category would make the
    calculator give two different answers for the same food, depending only on
    whether the visitor happened to name it, with nothing on the screen saying
    why. That is design section 8.2's named failure and it is invisible in the
    output, so it is asserted here on the values that will actually be stored.

    Three things are checked per (food_category, sector, metric):

      1. the group is complete -- one row per food in
         `ITEM_LEVEL_CLIENT_FOODS`, no duplicates;
      2. the unweighted mean of the stored item values equals the stored
         category value, within `ITEM_LEVEL_TOLERANCE`;
      3. the group holds more than one distinct value. A group whose rows all
         equalled their category would satisfy (2) perfectly while teaching a
         visitor nothing -- the dimension wired and inert.
    """
    groups: dict[tuple[str, str, str], dict[str, Decimal]] = {}
    categories: dict[tuple[str, str, str], Decimal] = {}
    for row in data["upstream"]:
        if row["destination"] is not None:
            continue
        key = (row["food_category"], row["sector"], row["metric"])
        if row.get("food_item") is None:
            categories[key] = Decimal(row["value_per_kg"])
        else:
            group = groups.setdefault(key, {})
            if row["food_item"] in group:
                raise SystemExit(
                    f"Two item rows for {key} both name {row['food_item']!r}; "
                    "the bundle keys on that tuple and the later row would "
                    "silently win."
                )
            group[row["food_item"]] = Decimal(row["value_per_kg"])

    problems: list[str] = []
    for nz_food, pairs in ITEM_LEVEL_CLIENT_FOODS.items():
        expected = {code for code, _ in pairs}
        for metric in ITEM_LEVEL_METRICS:
            for nz_sector in ALL_NZ_SECTORS:
                key = (nz_food, nz_sector, metric)
                group = groups.get(key, {})
                if set(group) != expected:
                    problems.append(
                        f"{nz_food}/{nz_sector}/{metric}: item rows for "
                        f"{sorted(group)}, expected {sorted(expected)}"
                    )
                    continue
                if key not in categories:
                    problems.append(
                        f"{nz_food}/{nz_sector}/{metric}: item rows exist but "
                        "the category row they fall back to does not"
                    )
                    continue
                stored_mean = mean(list(group.values()))
                drift = abs(stored_mean - categories[key])
                if drift > ITEM_LEVEL_TOLERANCE:
                    problems.append(
                        f"{nz_food}/{nz_sector}/{metric}: the {len(group)} item "
                        f"rows average {stored_mean}, the category row says "
                        f"{categories[key]} (drift {drift}, tolerance "
                        f"{ITEM_LEVEL_TOLERANCE})"
                    )
                if len(set(group.values())) < 2:
                    problems.append(
                        f"{nz_food}/{nz_sector}/{metric}: all {len(group)} item "
                        f"rows carry the same value "
                        f"{next(iter(group.values()))}, so the item dimension "
                        "is wired and inert here"
                    )

    #: The other direction: an item row for a category this function does not
    #: know about would never be checked at all.
    unknown = sorted(
        key for key in groups
        if key[0] not in ITEM_LEVEL_CLIENT_FOODS or key[2] not in ITEM_LEVEL_METRICS
    )
    if unknown:
        problems.append(
            f"item rows exist for {unknown}, which ITEM_LEVEL_CLIENT_FOODS / "
            "ITEM_LEVEL_METRICS do not cover, so nothing checked their mean"
        )

    if problems:
        raise SystemExit(
            "Item-level mean-preservation check failed -- the item rows and the "
            "category row they fall back to would give a visitor two different "
            "answers for the same food:\n  " + "\n  ".join(problems)
        )


def _assert_item_level_reproduces_the_client_figure(data: dict) -> None:
    """Where the construction should land exactly on the client's own number, it does.

    Mean preservation is a property of the group. This is a property of the
    individual row, and it is the one a reader can check against the client's
    document with nothing but a calculator:

      * **`land`, every category, every sector.** The category factor is the
        mean of the same per-food figures at every sector, so the relativity
        collapses and the item row must equal that food's own resolved figure --
        the client's inverted t/ha, or the public one where the stated
        ten-times rule took over.
      * **`co2e` and `water`, at the anchor sector, for the three categories the
        client supplied a total for.** There the category factor is the client's
        own unweighted mean, stored exactly, so the item row must be the
        client's own printed cell for that food. Butter at `wholesale_retail`
        must read 11.39, and Cheese 10.13.

    `staples` co2e and water are deliberately absent from the second check:
    there is no client `staples` total to anchor against, so nothing there
    should reproduce a client cell and asserting that it does would be asserting
    the wrong thing.
    """
    stored = {
        (row["food_category"], row["sector"], row["metric"], row["food_item"]):
            Decimal(row["value_per_kg"])
        for row in data["upstream"]
        if row["destination"] is None and row.get("food_item") is not None
    }
    problems: list[str] = []
    for nz_food, pairs in ITEM_LEVEL_CLIENT_FOODS.items():
        anchor = (
            None if nz_food == "staples"
            else LIFE_CYCLE_ANCHOR[
                _life_cycle_for(
                    [food_row(name) for name in NZ_FOOD_CATEGORY_SOURCES[nz_food]]
                )
            ]
        )
        for code, client_food in pairs:
            for metric in ITEM_LEVEL_METRICS:
                if metric == "land":
                    sectors = list(ALL_NZ_SECTORS)
                elif anchor:
                    sectors = [anchor]
                else:
                    sectors = []
                for nz_sector in sectors:
                    want, _ = _client_item_value(metric, client_food)
                    got = stored[(nz_food, nz_sector, metric, code)]
                    if abs(got - qd(want)) > ITEM_LEVEL_TOLERANCE:
                        problems.append(
                            f"{nz_food}/{code}/{nz_sector}/{metric}: stored "
                            f"{got}, the client's own resolved figure is "
                            f"{qd(want)}"
                        )
    if problems:
        raise SystemExit(
            "Item-level rows that should reproduce the client's own figure "
            "exactly do not:\n  " + "\n  ".join(problems)
        )


def _assert_no_land_downstream_rows(data: dict) -> None:
    """The absence of a downstream `land` row is checked, not merely described.

    The client's table 2 has no land column and should not have one: sending a
    kilogram to landfill, to compost or to an anaerobic digester returns no
    land and occupies none, so every destination's downstream land term is
    zero, and an absent row already resolves to zero through the documented
    three-step lookup order. Writing seventeen explicit zeroes would say the
    same thing at more length.

    What makes that a decision rather than an oversight is this check. A future
    edit that adds a downstream land row -- a loop widened by one metric, a
    copied block -- stops here and has to argue for it.
    """
    offenders = sorted(
        row["destination"] for row in data["downstream"] if row["metric"] == "land"
    )
    if offenders:
        raise SystemExit(
            "This draft carries downstream `land` rows for "
            f"{offenders}. The client's table 2 has no land column, and land "
            "occupation is a property of growing the food rather than of "
            "where it is sent afterwards. An absent row already resolves to "
            "zero through the three-step lookup order. If a real "
            "destination-side land figure now exists, it needs a source and a "
            "note in the provenance document, and this check needs removing "
            "deliberately in the same commit."
        )


def print_land_comparison() -> None:
    """Every client food row, both figures, and which was taken.

    Printed on every build rather than recorded once in a document, because
    the decision it reports is the one the owner asked to see and a table
    nobody regenerates is a table that stops matching the data.
    """
    print()
    print("land: the client's t/ha column inverted (10/Y m2/kg), against "
          "Poore & Nemecek (2018) via Our World in Data, read 2026-09-23")
    print(f"  {'client food row':36s} {'t/ha':>8s} {'client':>12s} "
          f"{'public':>12s} {'ratio':>8s}  taken")
    used = {
        food for foods in NZ_FOOD_CATEGORY_SOURCES.values() for food in foods
    } | set(LAND_STAPLES_CLIENT_ROWS)
    for row in TABLE1:
        c = land_comparison(row.food)
        public = "-" if c.public_m2_per_kg is None else f"{c.public_m2_per_kg:.4f}"
        ratio = "-" if c.ratio is None else f"{c.ratio:.2f}"
        mark = " " if row.food in used else "*"
        print(f"{mark} {row.food:36s} {c.yield_t_per_ha:>8} "
              f"{c.client_m2_per_kg:>12.4f} {public:>12s} {ratio:>8s}  {c.taken}")
    print("  * not used by any New Zealand food category in this draft.")
    print()


#: Metrics that carry factor rows at all in this draft. `mass` never does --
#: its formula (`qty_kg`) needs no upstream/downstream lookup, matching the
#: live/mock set -- so it is deliberately excluded from both checks below.
UPSTREAM_METRICS = ("co2e", "water", "ch4", "land")
#: `cost` has no upstream row anywhere (see module docstring, "COST" -- it
#: is a downstream-only figure), so it is absent from UPSTREAM_METRICS but
#: present here.
#:
#: **`land` is absent here, and that is a decision rather than an omission.**
#: The client's table 2 has no land column, and it should not have one: land
#: occupation is a property of growing the food, and sending a kilogram to
#: landfill, to compost or to an anaerobic digester returns no land and
#: occupies none. An absent row resolves to zero through the documented
#: three-step lookup order, which is the correct answer here rather than a
#: gap. `_assert_no_land_downstream_rows()` below checks the absence
#: mechanically, so that a future edit which adds one has to argue for it.
DOWNSTREAM_METRICS = ("co2e", "water", "ch4", "cost")


def _assert_completeness(data: dict) -> None:
    """Fail loudly if any row this factor set must carry is missing.

    This is the mechanical guarantee the module docstring's per-section
    narrative only *describes*: every one of the ten New Zealand food
    categories has a generic (destination=None) upstream row AND a
    prevention-override row, for every one of the six sectors, for every
    metric in UPSTREAM_METRICS; and every one of the fourteen destinations
    has exactly one downstream row for every metric in DOWNSTREAM_METRICS.
    A future edit that drops a row -- a refactor that narrows a loop, a
    merge that drops a dict entry -- fails here instead of quietly pricing
    something at zero, which is indistinguishable on screen from a real
    measurement of none. This is what closed the `upcycling`/`ch4` gap this
    check itself was written to catch: the coordinator found it by counting
    rows by hand; this makes that count part of the build.
    """
    upstream_generic: dict[tuple[str, str, str], int] = {}
    upstream_prevention: dict[tuple[str, str, str], int] = {}
    for row in data["upstream"]:
        if row["metric"] not in UPSTREAM_METRICS:
            continue
        #: **An item row is counted by neither**, and leaving them in would
        #: have broken this check the moment the item level landed: an item row
        #: carries `destination = None` too, so six dairy foods would have made
        #: `upstream_generic[('dairy', sector, 'co2e')]` read 7 and this
        #: function would have reported the set as duplicating the very row it
        #: needs. What a category's fallback row is, is the one with **both**
        #: nullable dimensions empty (section 2.2's candidate 4). That every
        #: item row has such a row behind it is
        #: `_assert_item_level_preserves_the_category_mean`'s to say here, and
        #: `refuse_item_rows_without_category_fallback`'s at publish.
        if row.get("food_item") is not None:
            continue
        key = (row["food_category"], row["sector"], row["metric"])
        if row["destination"] is None:
            upstream_generic[key] = upstream_generic.get(key, 0) + 1
        elif row["destination"] == "prevention":
            upstream_prevention[key] = upstream_prevention.get(key, 0) + 1

    missing: list[str] = []
    for nz_food in NZ_TO_REFED_FOOD_SHAPE:  # the canonical ten -- see module docstring
        for nz_sector in ALL_NZ_SECTORS:
            for metric in UPSTREAM_METRICS:
                key = (nz_food, nz_sector, metric)
                if upstream_generic.get(key, 0) != 1:
                    missing.append(
                        f"upstream generic {nz_food}/{nz_sector}/{metric}: "
                        f"found {upstream_generic.get(key, 0)}, expected 1"
                    )
                if upstream_prevention.get(key, 0) != 1:
                    missing.append(
                        f"upstream prevention-override {nz_food}/{nz_sector}/"
                        f"{metric}: found {upstream_prevention.get(key, 0)}, "
                        "expected 1"
                    )

    downstream_counts: dict[tuple[str, str], int] = {}
    for row in data["downstream"]:
        if row["metric"] not in DOWNSTREAM_METRICS:
            continue
        key = (row["destination"], row["metric"])
        downstream_counts[key] = downstream_counts.get(key, 0) + 1

    for nz_dest in ALL_NZ_DESTINATIONS:
        for metric in DOWNSTREAM_METRICS:
            key = (nz_dest, metric)
            if downstream_counts.get(key, 0) != 1:
                missing.append(
                    f"downstream {nz_dest}/{metric}: found "
                    f"{downstream_counts.get(key, 0)}, expected 1"
                )

    # Task 4: an equivalence is data too, and the same two ways it can go
    # silently wrong -- a `source_metric` this set never computes, or a
    # missing `source_note` -- are exactly the kind of thing this function
    # exists to catch mechanically rather than by inspection.
    known_metrics = {formula["metric"] for formula in data["formulas"]}
    for equivalence in data.get("equivalences", []):
        code = equivalence.get("code", "<no code>")
        source_metric = equivalence.get("source_metric")
        if source_metric not in known_metrics:
            missing.append(
                f"equivalence {code}: source_metric {source_metric!r} is not "
                f"one of this set's metrics {sorted(known_metrics)}"
            )
        if not equivalence.get("source_note"):
            missing.append(
                f"equivalence {code}: source_note is missing -- every "
                "shipped equivalence must record where its factor came from"
            )

    #: `factor_upstream.data_quality` and `factor_downstream.data_quality` are
    #: both `VARCHAR(32)`. A longer tag writes a perfectly valid JSON file that
    #: the loader then refuses at INSERT time with a MySQL `Data too long`
    #: error, several minutes into a load -- which is how this check came to
    #: exist. Checked here so the build stops instead.
    for section in ("upstream", "downstream"):
        for row in data[section]:
            tag = row.get("data_quality") or ""
            if len(tag) > 32:
                missing.append(
                    f"{section} {row.get('food_category') or row.get('destination')}"
                    f"/{row['metric']}: data_quality {tag!r} is {len(tag)} "
                    "characters; the column is VARCHAR(32) and the loader "
                    "would refuse this row"
                )

    if missing:
        raise SystemExit(
            "Completeness check failed -- this factor set is missing (or "
            "duplicates) rows a submission could actually need, which "
            "would price silently at zero rather than fail:\n  "
            + "\n  ".join(missing)
        )


def _assert_ladders_are_well_formed(data: dict) -> None:
    """Contract v1.71. Three things about a ladder that nothing else catches.

    The engine's selection rule is *the first rung, in `sort_order`, whose own
    value reaches its `min_value`*. That makes `sort_order` within a family a
    PRIORITY ORDER, which is exactly the kind of thing that is correct the day
    it is written and silently wrong after somebody inserts a rung. Each of the
    three failures below produces a factor set that loads, validates, computes
    and shows the wrong sentence:

    1. **Largest unit first.** A bigger unit has a *smaller* `value_per_unit`
       (one Olympic pool is 4e-7 of a litre's worth; one shower is 0.0111), so
       within a family `value_per_unit` must strictly increase down the sort
       order. Reversed, the first rung tried is the smallest unit, it reaches
       one long before any other does, and the ladder never climbs -- every
       submission ever made reads in showers.
    2. **A bottom rung with no band.** If every rung carries a `min_value`, a
       value below all of them matches nothing and falls back to the family's
       first row, which is the largest unit -- so the smallest submissions get
       the unit that reads `0`, which is the defect the ladder was built to
       remove.
    3. **One metric per ladder.** A ladder is one quantity at several sizes;
       rungs drawn from two metrics are two facts wearing one name, and which
       one a reader gets would be decided by magnitude.

    `FactorBundle.validate()` reports 3 (and the two schema CHECKs cover the
    band shapes), but nothing anywhere reports 1 or 2 -- they are not malformed
    data, they are a ladder that works and is upside down. Checked here so the
    build stops rather than the reader finding out.
    """
    families: dict[str, list[dict]] = {}
    for row in data.get("equivalences", []):
        family = row.get("family")
        if family is None:
            if row.get("min_value") is not None or row.get("max_value") is not None:
                raise SystemExit(
                    f"equivalence {row['code']}: carries a band and no family. "
                    "Selection only happens within a family, so the band can "
                    "never fire -- and the database refuses the row outright."
                )
            continue
        families.setdefault(family, []).append(row)

    problems: list[str] = []
    for family, rows in families.items():
        rungs = sorted(rows, key=lambda r: (r["sort_order"], r["code"]))
        metrics = {row["source_metric"] for row in rungs}
        if len(metrics) > 1:
            problems.append(
                f"family {family!r} draws on more than one metric "
                f"({sorted(metrics)}); a ladder is one quantity at several "
                "sizes"
            )
        previous = None
        for row in rungs:
            value = Decimal(row["value_per_unit"])
            if previous is not None and value <= previous:
                problems.append(
                    f"family {family!r}: {row['code']} has value_per_unit "
                    f"{value} at sort_order {row['sort_order']}, which is not "
                    f"larger than the rung before it ({previous}). Rungs are "
                    "tried in sort_order and the first that reaches one wins, "
                    "so they must run LARGEST UNIT (smallest value_per_unit) "
                    "FIRST or the ladder never climbs"
                )
            previous = value
        if rungs[-1].get("min_value") is not None:
            problems.append(
                f"family {family!r}: its last rung ({rungs[-1]['code']}) "
                "carries a min_value, so a value below every band matches "
                "nothing and falls back to the FIRST rung -- the largest unit, "
                "which is the one that reads 0. The bottom rung of a ladder "
                "carries no band"
            )
    if problems:
        raise SystemExit(
            "Equivalence ladder check failed -- this factor set would show "
            "the wrong rung:\n  " + "\n  ".join(problems)
        )


def _assert_only_table2_co2_moved() -> None:
    """Re-run, on every build, the measurement this revision was made on.

    The client's revised document of 2026-09-21 changed exactly one thing:
    table 2's CO2-eq column. That was established by parsing the new document
    and diffing it cell by cell against the committed 2026-09-05
    transcription -- table 1 showed zero differences across all 26 rows and
    all six columns, and so did table 2's destination labels, life-cycle
    column and water column.

    A measurement made once and then only described in prose is a claim. This
    checks it. `rawtec_source_data.PRIOR_REVISION_TABLE1` and
    `PRIOR_REVISION_TABLE2_EXCEPT_CO2` freeze the cells that did not move,
    exactly as the 2026-09-05 transcription held them; if a later edit moves
    one of them without moving the frozen record in the same commit, the
    build stops here instead of writing a factor set whose provenance
    document says something that is no longer true.

    It also refuses a `TABLE2` that still carries any of the withdrawn
    column's values, which is what a half-applied revision would look like.

    This replaces the `TABLE2_CO2_COPY_SOURCE` check that stood here before.
    That one asserted that table 2's CO2-eq column *was* a copy of table 1's,
    so that the provenance document could not describe a defect the data no
    longer had. The client has now removed the defect, so that check would
    fail on correct data; what is kept is its purpose -- no silent drift
    between the client's document, the transcription and the prose about it.
    """
    problems: list[str] = []

    if len(TABLE1) != len(PRIOR_REVISION_TABLE1):
        problems.append(
            f"TABLE1 has {len(TABLE1)} rows; the 2026-09-05 transcription had "
            f"{len(PRIOR_REVISION_TABLE1)}."
        )
    else:
        for index, (row, frozen) in enumerate(zip(TABLE1, PRIOR_REVISION_TABLE1)):
            f_cat, f_food, f_cycle, f_co2, f_water, f_land = frozen
            for column, now, then in (
                ("category", row.category, f_cat),
                ("food", row.food, f_food),
                ("life_cycle", row.life_cycle, f_cycle),
                ("co2e_per_kg", row.co2e_per_kg, Decimal(f_co2)),
                ("water_l_per_kg", row.water_l_per_kg, Decimal(f_water)),
                ("land_t_per_ha", row.land_t_per_ha, Decimal(f_land)),
            ):
                if now != then:
                    problems.append(
                        f"TABLE1 row {index} ({f_food}) {column}: now {now!r}, "
                        f"2026-09-05 transcription {then!r}"
                    )

    if len(TABLE2) != len(PRIOR_REVISION_TABLE2_EXCEPT_CO2):
        problems.append(
            f"TABLE2 has {len(TABLE2)} rows; the 2026-09-05 transcription had "
            f"{len(PRIOR_REVISION_TABLE2_EXCEPT_CO2)}."
        )
    else:
        for index, (row, frozen) in enumerate(
            zip(TABLE2, PRIOR_REVISION_TABLE2_EXCEPT_CO2)
        ):
            f_dest, f_cycle, f_water = frozen
            expected_water = None if f_water is None else Decimal(f_water)
            for column, now, then in (
                ("destination", row.destination, f_dest),
                ("life_cycle", row.life_cycle, f_cycle),
                ("water_l_per_kg", row.water_l_per_kg, expected_water),
            ):
                if now != then:
                    problems.append(
                        f"TABLE2 row {index} ({f_dest}) {column}: now {now!r}, "
                        f"2026-09-05 transcription {then!r}"
                    )

    for row in TABLE2:
        withdrawn = WITHDRAWN_2026_09_05_TABLE2_CO2.get(row.destination)
        if withdrawn is not None and row.co2e_per_kg == Decimal(withdrawn):
            problems.append(
                f"TABLE2 row {row.destination!r} still carries the withdrawn "
                f"2026-09-05 CO2-eq value {withdrawn} -- that column was a "
                "copy of table 1's food figures and was replaced by the "
                "client on 2026-09-21."
            )

    if problems:
        raise SystemExit(
            "The client's transcription has moved somewhere it was measured "
            "not to. The 2026-09-21 revision changed table 2's CO2-eq column "
            "and nothing else; if that is no longer true, the provenance in "
            "rawtec_source_data.py and docs/upstream-factors-draft.md needs "
            "rewriting in the same commit as the data, and PRIOR_REVISION_* "
            "needs re-freezing deliberately rather than to make this pass:\n  "
            + "\n  ".join(problems)
        )


def build_upstream(refed_upstream) -> list[dict]:
    rows: list[dict] = []
    for nz_food, client_foods in NZ_FOOD_CATEGORY_SOURCES.items():
        rows.extend(_build_category_rows(nz_food, client_foods, refed_upstream))
    rows.extend(_build_staples_rows(refed_upstream))
    rows.extend(_build_ch4_upstream_rows(refed_upstream))
    rows.extend(_build_land_upstream_rows())
    #: Last, and read off `rows` rather than off the client's table a second
    #: time: an item row is a relativity on the category row this build has just
    #: written, so it has to be able to read it. See
    #: `_build_item_level_upstream_rows`.
    rows.extend(_build_item_level_upstream_rows(rows))
    return rows


def build_downstream() -> list[dict]:
    rows: list[dict] = []

    _assert_only_table2_co2_moved()

    for nz_dest, client_dests in NZ_DESTINATION_SOURCES.items():
        client_rows = [destination_row(name) for name in client_dests]

        for metric, attr in (("co2e", "co2e_per_kg"), ("water", "water_l_per_kg")):
            present = [r for r in client_rows if getattr(r, attr) is not None]
            if not present:
                # Every mapped row is null for this metric (Incineration's
                # water). Not seeded from the client -- see below, where
                # `combustion`'s water is filled from ReFED instead.
                continue
            values = [getattr(r, attr) for r in present]
            total = mean(values)
            components = "; ".join(
                f"{r.destination} ({r.life_cycle}) = {getattr(r, attr)}"
                + (
                    f" [2026-09-21 revision; the withdrawn 2026-09-05 column "
                    f"printed {WITHDRAWN_2026_09_05_TABLE2_CO2[r.destination]} "
                    f"here, a copy of a table 1 food row -- see provenance doc]"
                    if metric == "co2e" else ""
                )
                for r in present
            )
            agg_note = (
                f"mean of {len(values)} client row(s): {components} -> "
                f"unweighted mean {total}"
                if len(values) > 1
                else f"client row: {components} (used directly, no aggregation)"
            )
            skipped = [r.destination for r in client_rows if r not in present]
            if skipped:
                agg_note += f". Excluded (null in the client's table): {', '.join(skipped)}."

            if metric == "co2e":
                caution = (
                    "This is the client's CORRECTED CO2-eq column, from the "
                    "revised document of 2026-09-21. The 2026-09-05 revision "
                    "printed a verbatim copy of table 1's CO2-eq column here "
                    "-- food figures in destination rows, which priced "
                    "composting worse than landfill -- and that column was "
                    "used as printed on the repository owner's explicit "
                    "instruction of 2026-09-05. It is withdrawn. Still not "
                    "client-confirmed: O-1 stays open and is_mock stays true. "
                    "See docs/upstream-factors-draft.md."
                )
            else:
                caution = (
                    "The water column was never implicated in that defect and "
                    "is identical in both revisions of the client's document "
                    "-- see docs/upstream-factors-draft.md."
                )

            rows.append({
                "destination": nz_dest,
                "sector": None,
                "food_category": None,
                "metric": metric,
                "value_per_kg": q(total),
                "source_note": (
                    f"Rawtec table 2 ('bin to destination'), {agg_note}. {caution}"
                ),
                "data_quality": "client-table2",
            })

    # `other_recovery`: no client row -- filled from ReFED's "Industrial
    # Uses" destination (same recycle_recovery-equivalent group). See the
    # module docstring.
    industrial = load_refed_downstream_values(REFED_OTHER_RECOVERY_DESTINATION)
    co2e_values = industrial["co2e"]
    co2e_mean = mean(co2e_values)
    rows.append({
        "destination": "other_recovery",
        "sector": None,
        "food_category": None,
        "metric": "co2e",
        "value_per_kg": q(co2e_mean),
        "source_note": (
            "No client (Rawtec) destination row exists for 'other_recovery' "
            "('Other recovery, including biodiesel'); the client's own "
            "'Other Food Waste' is a disposal row (Bin to Landfill) and is "
            "deliberately not folded in here. Filled instead from ReFED's "
            "own 'Industrial Uses' destination "
            f"({REFED_FACTORS_PATH.relative_to(REPO_ROOT).as_posix()}), which "
            "sits in the same recycle_recovery-equivalent group as "
            "other_recovery -- an unweighted mean across all "
            f"{len(co2e_values)} published (sector, food category) rows: "
            f"min {min(co2e_values)}, max {max(co2e_values)}, mean {co2e_mean}."
        ),
        "data_quality": "derived-refed",
    })
    water_values = industrial["water"]
    if any(v != 0 for v in water_values):
        raise SystemExit(
            "ReFED's Industrial Uses water values are no longer uniformly "
            "zero; the source_note below assumes they are and needs "
            "rewriting."
        )
    rows.append({
        "destination": "other_recovery",
        "sector": None,
        "food_category": None,
        "metric": "water",
        "value_per_kg": q(Decimal(0)),
        "source_note": (
            "No client destination row exists for 'other_recovery'. ReFED's "
            "own water figure for its 'Industrial Uses' destination "
            f"(same match as the co2e row above) is exactly zero across "
            f"all {len(water_values)} published (sector, food category) "
            "rows; adopted directly as a sourced zero, not an invented one."
        ),
        "data_quality": "derived-refed",
    })

    # `combustion`'s water: the client's own table prints `null`. Filled
    # from ReFED's own "Incineration" destination rather than left an
    # absence. See the module docstring.
    incineration = load_refed_downstream_values(REFED_INCINERATION_DESTINATION)
    water_values = incineration["water"]
    if any(v != 0 for v in water_values):
        raise SystemExit(
            "ReFED's Incineration water values are no longer uniformly "
            "zero; the source_note below assumes they are and needs "
            "rewriting."
        )
    rows.append({
        "destination": "combustion",
        "sector": None,
        "food_category": None,
        "metric": "water",
        "value_per_kg": q(Decimal(0)),
        "source_note": (
            "The client's own table prints `null` for Incineration's water "
            "column (no measurement offered). Rather than leave this an "
            "absence or invent a figure, ReFED's own water value for its "
            "'Incineration' destination is used: it is exactly zero across "
            f"all {len(water_values)} published (sector, food category) "
            "rows in refed-benchmark-factors.json -- ReFED's own model "
            "assigns waste-to-energy combustion no process water at all. "
            "Adopted here as a real, sourced zero, not a silent one."
        ),
        "data_quality": "derived-refed",
    })

    # Prevention override, zero by definition -- not from client data.
    for metric in ("co2e", "water"):
        rows.append({
            "destination": "prevention",
            "sector": None,
            "food_category": None,
            "metric": metric,
            "value_per_kg": q(Decimal(0)),
            "source_note": (
                "Zero by definition. 'prevention' is the mandatory 100% "
                "offset (contract §O-7): waste that never happened has no "
                "downstream fate to price. Not a client figure."
            ),
            "data_quality": "definitional",
        })
    return rows


def build_ch4_downstream() -> list[dict]:
    """`ch4` downstream, all fourteen destinations. See the module
    docstring, "CH4...". Neither client table carries a methane column, so
    this cannot be built from `NZ_DESTINATION_SOURCES` (there is no client
    row to draw a mapping from) -- every destination is matched directly
    onto a ReFED destination by shape (`REFED_DESTINATION_FOR_NZ_DESTINATION`),
    an unweighted mean across every published (sector, food category) row
    for that ReFED destination, the same technique already used for
    `other_recovery`'s co2e/water fill above.
    """
    rows: list[dict] = []
    for nz_dest, refed_dest in REFED_DESTINATION_FOR_NZ_DESTINATION.items():
        values = load_refed_downstream_values(refed_dest)["ch4"]
        if not values:
            raise SystemExit(
                f"No ReFED ch4 values found for {refed_dest!r} (mapped from "
                f"{nz_dest!r})."
            )
        mean_value = mean(values)
        rows.append({
            "destination": nz_dest,
            "sector": None,
            "food_category": None,
            "metric": "ch4",
            "value_per_kg": q(mean_value),
            "source_note": (
                f"No client (Rawtec) ch4 column exists in table 2 at all. "
                f"Filled from ReFED's own {refed_dest!r} destination "
                f"(matched by shape, not by client row -- see module "
                f"docstring), an unweighted mean across all {len(values)} "
                f"published (sector, food category) rows: min "
                f"{min(values)}, max {max(values)}, mean {mean_value}."
            ),
            "data_quality": "derived-refed",
        })

    # `upcycling`: no single ReFED destination matches by name or by our own
    # destination-group classification -- see module docstring and the
    # comment above REFED_DESTINATION_FOR_NZ_DESTINATION. A STAND-IN, not a
    # measurement: an unweighted mean pooling every (sector, food_category)
    # row ReFED publishes across BOTH of its real reuse-group destinations
    # (Donations, Animal Feed), flattened into one list rather than averaging
    # two already-computed means -- the same pooling convention every other
    # multi-source mean in this draft uses.
    standin_values: list[Decimal] = []
    standin_counts: dict[str, int] = {}
    for refed_dest in REFED_UPCYCLING_STANDIN_DESTINATIONS:
        values = load_refed_downstream_values(refed_dest)["ch4"]
        if not values:
            raise SystemExit(
                f"No ReFED ch4 values found for {refed_dest!r}, needed for "
                "upcycling's stand-in."
            )
        standin_counts[refed_dest] = len(values)
        standin_values.extend(values)
    standin_mean = mean(standin_values)
    rows.append({
        "destination": "upcycling",
        "sector": None,
        "food_category": None,
        "metric": "ch4",
        "value_per_kg": q(standin_mean),
        "source_note": (
            "STAND-IN, NOT A MEASUREMENT of 'upcycling to other food "
            "products' (client row 'Upcycled', life cycle 'Bin to Product' "
            "-- the only client row with that wording). No client (Rawtec) "
            "ch4 column exists at all, and no ReFED destination matches "
            "this one: not by name (ReFED publishes no 'Upcycling' or "
            "'Repurposed' destination) and not by our own destination-group "
            "classification (admin/seed.py puts upcycling in the 'reuse' "
            "group; ReFED's own DESTINATIONS table puts only Prevention, "
            "Donations and Animal Feed there). 'Industrial Uses' (already "
            "used for bioprocessing/other_recovery, above) was considered "
            "and rejected: it is in ReFED's recycle_recovery-equivalent "
            "group, not reuse, and produces non-food industrial output, not "
            "another food product. Absent a destination that matches by "
            "either test, this cell pools every (sector, food category) row "
            "ReFED publishes across its two real reuse-group destinations "
            f"instead -- Donations ({standin_counts['refed_donations']} rows) "
            f"and Animal Feed ({standin_counts['refed_animal_feed']} rows), "
            f"{len(standin_values)} rows total, unweighted mean "
            f"{standin_mean}. This is a stated derivation from the closest "
            "available ReFED pathway by destination-group, not a "
            "measurement of upcycling itself, and should be replaced the "
            "moment a better source exists."
        ),
        "data_quality": "derived-refed-reuse-standin",
    })

    rows.append({
        "destination": "prevention",
        "sector": None,
        "food_category": None,
        "metric": "ch4",
        "value_per_kg": q(Decimal(0)),
        "source_note": (
            "Zero by definition. 'prevention' is the mandatory 100% offset "
            "(contract §O-7): waste that never happened has no downstream "
            "fate to price. Not a client or ReFED figure."
        ),
        "data_quality": "definitional",
    })
    return rows


def build_cost_downstream() -> list[dict]:
    """`cost` downstream, all fourteen destinations. See the module
    docstring, "COST...". The New Zealand waste disposal levy, seeded at the
    rate in force today (2026-09-05) -- not the client's own cited future
    rate. No upstream `cost` row is written anywhere (cost is downstream-
    only); the upstream term resolves to zero via the ordinary three-step
    lookup fallback.
    """
    rows: list[dict] = []
    levy_per_kg = qd(LEVY_CURRENT_NZD_PER_TONNE / Decimal(1000))

    for nz_dest in LEVY_CARRYING_DESTINATIONS:
        rows.append({
            "destination": nz_dest,
            "sector": None,
            "food_category": None,
            "metric": "cost",
            "value_per_kg": q(levy_per_kg),
            "source_note": (
                f"New Zealand waste disposal levy, Class 1 (municipal "
                f"landfill), the rate in force as of 2026-09-05: "
                f"${LEVY_CURRENT_NZD_PER_TONNE}/tonne effective "
                f"{LEVY_CURRENT_EFFECTIVE} = {levy_per_kg} NZD/kg. The "
                f"client's own cited ${LEVY_NEXT_NZD_PER_TONNE}/tonne takes "
                f"effect {LEVY_NEXT_EFFECTIVE} and is NOT yet in force -- "
                f"not seeded. food_category_id is left NULL: the levy is "
                f"charged per tonne of waste regardless of food type. "
                f"{LEVY_SOURCE}"
            ),
            "data_quality": "derived-public-nz-levy",
        })

    for nz_dest, reason in LEVY_EXCLUDED_WITH_REASON.items():
        rows.append({
            "destination": nz_dest,
            "sector": None,
            "food_category": None,
            "metric": "cost",
            "value_per_kg": q(Decimal(0)),
            "source_note": (
                f"Zero, not a gap: {reason} {LEVY_SOURCE}"
            ),
            "data_quality": "not-applicable-nz-levy",
        })

    rows.append({
        "destination": "prevention",
        "sector": None,
        "food_category": None,
        "metric": "cost",
        "value_per_kg": q(Decimal(0)),
        "source_note": (
            "Zero by definition. 'prevention' is the mandatory 100% offset "
            "(contract §O-7): waste that never happened has no disposal "
            "cost to price. Not a client or public figure."
        ),
        "data_quality": "definitional",
    })
    return rows


def build() -> dict:
    refed_upstream = load_refed_generic_upstream()
    upstream = build_upstream(refed_upstream)
    downstream = build_downstream() + build_ch4_downstream() + build_cost_downstream()

    data = {
        #: **The suffix describes the DATA, not the publication state**, and
        #: that is a correction. It read "- NOT PUBLISHED", which was true of
        #: this file and became false the moment the owner published the set
        #: (2026-09-23). `version_label` is not an internal note:
        #: `results.js` prints it as "Factor version" on the results page and
        #: in the text download, and `methodology.js` prints it as "Version",
        #: so a published set announcing itself as not published tells a
        #: visitor something untrue. What has NOT changed is that these
        #: figures are a draft the client has not confirmed -- that is O-1,
        #: and it is what `is_mock` already drives the mandatory banner from.
        #:
        #: **It also has to change whenever the data does, because
        #: `load_upstream_factors_draft.py` refuses a label that already
        #: exists** -- deliberately, so that two different sets cannot wear one
        #: name in the "Factor version" line a visitor reads. v1.71's ladders
        #: are why this line moved again; "Rawtec revised table 2" was
        #: shortened to "rev. table 2" to make room, because
        #: `factor_set.version_label` is **VARCHAR(128)** and the previous
        #: label was already 125 characters. Anything added here from now on
        #: has to displace something. The item level is what displaced
        #: "equivalence ladders" down to "ladders": 124 characters before, 124
        #: after, and the ladders are still in the notes in full.
        "version_label": (
            "CLIENT-DRAFT-2026-09-21 (Rawtec rev. table 2 + ReFED footprint; "
            "ch4, cost, land; ladders; item level) - NOT CLIENT-CONFIRMED"
        ),
        "is_mock": True,
        #: v1.58's release switch, and the reason this set may carry it: it has
        #: item-level `factor_upstream` rows for every food the client's own
        #: table 1 subdivides a category into (see "THE ITEM LEVEL" above), so
        #: step 2.5 asks a question this set's numbers can answer for those
        #: foods and falls the rest through to a category average that section
        #: 7.3c discloses on the page. `refuse_item_level_without_item_rows`
        #: is what refuses the flag on a set with no such row.
        "item_level_enabled": True,
        "notes": (
            "Draft factor set derived from the client's own Rawtec tables "
            "(farm-to-fork/-shelf production footprint by food, and bin-to-"
            "destination footprint by destination). Upstream figures are "
            "built as a CUMULATIVE footprint per sector -- ReFED's own "
            "per-sector shape (tests/benchmark/refed/"
            "refed-benchmark-factors.json), anchored so the sector matching "
            "the client's own 'Life cycle covered' wording equals the "
            "client's own total exactly, with the client's farm stage for "
            "five categories ReFED does not publish one for filled from "
            "Poore & Nemecek (2018) via Our World in Data (co2e) or a "
            "stated assumption (water) -- see "
            "data/upstream-factors-draft/public_farm_share_source_data.py. "
            "This replaces an earlier construction (commit ba7fe93) that "
            "read ReFED's per-sector values as shares summing to the "
            "client's total, which understated every consumer-stage factor "
            "roughly fourfold; see docs/upstream-factors-draft.md. As of "
            "this revision, ch4 (raw methane mass, ReFED-only, every food "
            "category and all fourteen destinations -- thirteen matched "
            "directly to a ReFED destination, upcycling as a stated "
            "stand-in pooling ReFED's two reuse-group destinations) and "
            "cost (the "
            "New Zealand waste disposal levy at the rate in force "
            "2026-09-05, landfill and refuse_discard only) are also seeded "
            "-- neither client table carries either column, so both are "
            "built entirely from ReFED or public New Zealand government "
            "sources; see docs/upstream-factors-draft.md. is_mock "
            "stays true: these values are derived, not yet the client's "
            "confirmed figures -- the placeholder banner must keep showing "
            "until the owner decides otherwise. This revision carries the "
            "client's CORRECTED table 2 CO2-eq column, received 2026-09-21: "
            "the 2026-09-05 column was a verbatim copy of table 1's food "
            "figures, which priced composting worse than landfill. The set "
            "built from that column was published on 2026-09-06 on the "
            "owner's instruction and stayed live until the corrected set "
            "replaced it on 2026-09-23, also on the owner's instruction. "
            "Publishing archives rather than deletes, so rollback "
            "remains available either way. "
            "THIS revision adds `land`, the sixth metric (contract v1.70) "
            "and the one column the client's own document has always "
            "carried and this calculator never reported. The client "
            "publishes t/ha, which is a YIELD: it is inverted into land "
            "occupation per kilogram (10/Y m2/kg) and every row is checked "
            "against Poore & Nemecek (2018) via Our World in Data, taking "
            "the public figure wherever the two differ by a factor of ten "
            "or more (Poultry, Other meat, Eggs, Sweeteners) and the "
            "client's otherwise; every row's source_note names which and "
            "why. Land is flat across all six sectors and has no "
            "downstream row at all, both for stated reasons. "
            "THIS revision releases the ITEM LEVEL (step 2.5) and carries "
            "item-level upstream factors for the four food categories the "
            "client's own table 1 actually subdivides: bakery_grains (3 "
            "foods), dairy (6), meat (4) and staples (6), for co2e, water and "
            "land -- 342 rows. Each is the client's own per-food figure "
            "expressed as a relativity on this set's own category factor, so "
            "the unweighted mean of a category's item rows IS that category's "
            "factor at every sector and the calculator cannot give two "
            "different answers for the same food depending on whether the "
            "visitor named it; the build asserts that on every run. ch4 has "
            "no item rows because neither client table has a methane column "
            "and ReFED's finest resolution is a food category; cost has no "
            "upstream row at all; mass reads no factor. The 27 foods contract "
            "v1.72 added to the five categories the client never subdivided, "
            "and the client's Eggs row, carry NO item factor on purpose -- "
            "section 2.2's chain prices them at their category average and "
            "section 7.3c discloses that it did. "
            "THIS SET IS A "
            "DRAFT AND IS NOT PUBLISHED -- whether it goes live is the "
            "owner's decision. Full provenance: "
            "docs/upstream-factors-draft.md and "
            "data/upstream-factors-draft/build_upstream_factors_draft.py."
        ),
        "constants": [
            {
                "code": "GWP_CH4_100",
                "value": "28.0000000000",
                "unit": "kg CO2e / kg CH4",
                "note": "IPCC AR5, 100-year horizon. Copied from docker/mock-factors.json; no formula in this draft references it, matching the live mock set.",
            },
            {
                "code": "GWP_CH4_20",
                "value": "84.0000000000",
                "unit": "kg CO2e / kg CH4",
                "note": "IPCC AR5, 20-year horizon. Copied from docker/mock-factors.json.",
            },
            {
                "code": "FOOD_VALUE_PER_KG",
                "value": "0.0000000000",
                "unit": "NZD / kg",
                "note": "Zero per O-2's closure (contract v1.48): the value of the wasted food itself does not enter the cost formula. Unchanged from the live set.",
            },
        ],
        "formulas": [
            {"metric": "co2e", "expression": "qty_kg * (upstream + downstream)",
             "notes": "Same expression as the live/mock set."},
            {"metric": "ch4", "expression": "qty_kg * (upstream + downstream)",
             "notes": (
                 "Same expression as the live/mock set: no const_GWP_CH4 "
                 "term, matching the ch4 metric's own unit (kg CH4, not kg "
                 "CO2e). ReFED's own ch4 figures are raw methane mass, "
                 "confirmed against build_refed_benchmark.py's unit-"
                 "conversion table, and are seeded directly for that reason "
                 "-- see data/upstream-factors-draft/"
                 "build_upstream_factors_draft.py, 'CH4'. Upstream and "
                 "downstream ch4 rows now exist for every food category and "
                 "all fourteen destinations -- upcycling has no matching "
                 "ReFED destination and carries a stated stand-in instead "
                 "(data_quality='derived-refed-reuse-standin'), not a "
                 "silent zero. _assert_completeness() enforces this "
                 "mechanically at build time."
             )},
            {"metric": "water", "expression": "qty_kg * (upstream + downstream)",
             "notes": "Same expression as the live/mock set."},
            {"metric": "cost", "expression": "qty_kg * (upstream + downstream + const_FOOD_VALUE_PER_KG)",
             "notes": (
                 "Same expression as the live/mock set; const_FOOD_VALUE_PER_KG "
                 "stays zero per O-2. Downstream cost rows now exist for "
                 "every destination: the New Zealand waste disposal levy "
                 "($70/tonne, the rate in force 2026-09-05) for landfill and "
                 "refuse_discard, an explicit stated-reason zero for every "
                 "other destination. No upstream cost row exists anywhere -- "
                 "cost is a downstream-only figure here (see 'COST' in the "
                 "module docstring); the upstream term is zero throughout."
             )},
            {"metric": "land", "expression": "qty_kg * (upstream + downstream)",
             "notes": (
                 "Section 4.3's shape, unchanged -- the same expression co2e "
                 "and water use. The downstream term is always zero for land "
                 "because this set carries no downstream land row at all: the "
                 "client's table 2 has no land column, and sending food to "
                 "landfill or to compost returns no land and occupies none. "
                 "An absent row resolves to zero through the documented "
                 "three-step lookup order, so the absence is the correct "
                 "answer rather than a gap, and "
                 "_assert_no_land_downstream_rows() checks it on every build. "
                 "The upstream figure is the client's own t/ha yield column "
                 "INVERTED into land occupation per kilogram (10/Y m2/kg -- "
                 "see land_m2_per_kg_from_yield()), checked row by row "
                 "against Poore & Nemecek (2018) and replaced by the public "
                 "figure wherever the two differ by ten times or more. This "
                 "is the only factor set in this repository that carries a "
                 "land formula, and that is what keeps every other set from "
                 "reporting land at a silent zero (contract v1.70)."
             )},
            {"metric": "mass", "expression": "qty_kg",
             "notes": "Same expression as the live/mock set."},
        ],
    }
    data["upstream"] = upstream
    data["downstream"] = downstream

    # The client's own conversions, from "Data sources for impact calculator"
    # received 2026-08-29, each at the TOP of its ladder, with smaller rungs
    # added below it (contract v1.71). This closes the vehicle and meal halves
    # of O-3: the document states both the factor and its basis, which is
    # exactly what `source_note` is for.
    #
    # THE DIVISOR FOR VEHICLES IS 2410, NOT 2.41. The client states the
    # figure per TONNE of CO2e; this system's `co2e` metric is in
    # kilograms.
    #
    # WHY THE RUNGS EXIST, MEASURED RATHER THAN ASSUMED. Against the published
    # set (15) and this one, scaling the canonical fixture and bisecting on the
    # label the engine prints, the client's own two units read `0` below:
    #
    #     Olympic swimming pools   638.755 kg
    #     Passenger vehicles/year  403.737 kg
    #
    # so a 23 kg submission -- a cafe's week -- showed two of its three cards
    # reading zero. Decimal places make that worse rather than better: at 10 kg
    # the pool figure is 0.0078, and `0` at least says honestly that the figure
    # is negligible at this scale.
    #
    # HOW THE BANDS WORK. Rungs of one family are tried in `sort_order`, which
    # is therefore LARGEST UNIT FIRST, and the first whose own value reaches
    # its `min_value` is the one shown. Every rung above the bottom one says
    # `min_value = 1` and nothing else -- "use the biggest unit that still
    # comes to at least one of them" -- and the bottom rung carries no band at
    # all, so a value too small for everything above it always has somewhere to
    # land. `_assert_ladders_are_well_formed` below checks both.
    #
    # NOTHING THE CLIENT SUPPLIED IS REPLACED. `vehicles_year` and
    # `olympic_pools` keep their factors, their wording and their place at the
    # top of each ladder; the rungs are added underneath. Every added rung is
    # either arithmetic on the client's own figure (the vehicle-day, which is
    # the client's own suggestion: "if a year is too much, change it to a day")
    # or carries a stated derivation of its own.
    one = lambda n: str((Decimal(1) / Decimal(n)).quantize(Decimal("1E-10")))
    data["equivalences"] = [
        {
            "code": "vehicles_year",
            "name": "Passenger vehicles for a year",
            "source_metric": "co2e",
            "family": "vehicles",
            "min_value": "1.0000000000",
            "value_per_unit": one(2410),
            "label_template": "Equivalent to running {value} passenger vehicles for a year",
            "label_template_one": "Equivalent to running {value} passenger vehicle for a year",
            "source_note": (
                "Client, Data sources for impact calculator (2026-08-29): "
                "\"Passenger vehicles on the road: GHG emissions (t CO2e) / "
                "2.41 (t CO2e/passenger vehicle/year)\". Applied per "
                "kilogram, so the divisor here is 2,410."
            ),
            "sort_order": 10,
        },
        {
            "code": "vehicles_day",
            "name": "An average passenger vehicle's day",
            "source_metric": "co2e",
            "family": "vehicles",
            #: The bottom rung of this ladder, so no band: a figure too small
            #: for a whole vehicle-year lands here whatever it is.
            "value_per_unit": str(
                (Decimal(365) / Decimal(2410)).quantize(Decimal("1E-10"))
            ),
            "label_template": (
                "Equivalent to an average passenger vehicle's emissions "
                "over {value} days"
            ),
            "label_template_one": (
                "Equivalent to an average passenger vehicle's emissions "
                "over {value} day"
            ),
            "source_note": (
                "The row above, divided by 365. No new source: it is the "
                "client's own 2.41 t CO2e per passenger vehicle per year "
                "(Data sources for impact calculator, 2026-08-29) spread "
                "over the days of that year, which comes to 6.60 kg CO2e a "
                "day. It is the client's own suggestion -- \"if a year is "
                "too much, change it to a day\". \"An average day\" is "
                "the whole day and not a journey: the figure includes the "
                "hours the vehicle is parked, because the year it is divided "
                "from does."
            ),
            "sort_order": 11,
        },
        {
            "code": "olympic_pools",
            "name": "Olympic swimming pools",
            "source_metric": "water",
            "family": "water_volume",
            "min_value": "1.0000000000",
            "value_per_unit": one(2500000),
            "label_template": "Equivalent to {value} Olympic swimming pools of water",
            "label_template_one": "Equivalent to {value} Olympic swimming pool of water",
            "source_note": (
                "Client, Data sources for impact calculator (2026-08-29): "
                "\"Olympic swimming pools: = (Water Used (L)) / 2,500,000\"."
            ),
            "sort_order": 20,
        },
        {
            "code": "backyard_pools",
            "name": "Backyard swimming pools",
            "source_metric": "water",
            "family": "water_volume",
            "min_value": "1.0000000000",
            "value_per_unit": one(48000),
            "label_template": "Equivalent to {value} backyard swimming pools of water",
            "label_template_one": "Equivalent to {value} backyard swimming pool of water",
            "source_note": (
                "48,000 litres, which is 8 m x 4 m x 1.5 m of water -- an "
                "ordinary domestic rectangular pool at an average depth. "
                "Derived here from those dimensions and not taken from a "
                "published figure: the team's own judgement, which the "
                "client asked for (\"be creative\", and these numbers "
                "\"need not be especially precise\"). It sits between the "
                "client's Olympic pool and a shower, which is the gap it was "
                "added to fill, and the dimensions are stated so that a "
                "reader who disagrees can see exactly what to change."
            ),
            "sort_order": 21,
        },
        {
            "code": "showers",
            "name": "Ten-minute showers",
            "source_metric": "water",
            "family": "water_volume",
            #: The bottom rung of this ladder, so no band.
            "value_per_unit": one(90),
            "label_template": "Equivalent to {value} ten-minute showers",
            "label_template_one": "Equivalent to {value} ten-minute shower",
            "source_note": (
                "PLACEHOLDER. Open item O-3 names showers as an intended "
                "equivalent and the New Zealand basis for one is not "
                "settled. 90 litres is ten minutes at 9 litres a minute, "
                "which is an ordinary (not a low-flow) showerhead; the "
                "assumption is stated here rather than hidden in the factor "
                "so that replacing it is one number in one row. Both halves "
                "are assumptions: how long a shower runs and how fast."
            ),
            "sort_order": 22,
        },
        {
            "code": "meals",
            "name": "Meals",
            "source_metric": "mass",
            #: No family, and that is a decision rather than an omission.
            #: `meals` needs no ladder: measured against the published set it
            #: stops reading `0` at 0.225 kg, which is below anything a
            #: business reports, and a smaller unit than a meal ("mouthfuls")
            #: would argue against the tool's own message. §6.4's neighbouring
            #: rule for the statistics page is the same class of decision.
            "value_per_unit": str((Decimal(1) / Decimal("0.45")).quantize(Decimal("1E-10"))),
            "label_template": "Equivalent to {value} meals",
            "label_template_one": "Equivalent to {value} meal",
            "source_note": (
                "Client, Data sources for impact calculator (2026-08-29): "
                "\"Meals: 450g per meal\"."
            ),
            "sort_order": 30,
        },
    ]

    _assert_completeness(data)
    _assert_no_land_downstream_rows(data)
    _assert_ladders_are_well_formed(data)
    _assert_item_level_preserves_the_category_mean(data)
    _assert_item_level_reproduces_the_client_figure(data)
    return data


def main() -> int:
    out_path = HERE / "upstream_factors_draft.json"
    data = build()
    print_land_comparison()
    out_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    n_food = len(NZ_FOOD_CATEGORY_SOURCES) + 1  # + staples
    n_sector = len(ALL_NZ_SECTORS)
    n_ch4_matched = len(REFED_DESTINATION_FOR_NZ_DESTINATION)
    n_item = sum(1 for row in data["upstream"] if row.get("food_item"))
    print(
        f"{out_path.name}: {len(data['upstream'])} upstream rows "
        f"(of which {n_item} name a food item: "
        + ", ".join(
            f"{nz_food} x{len(pairs)}"
            for nz_food, pairs in ITEM_LEVEL_CLIENT_FOODS.items()
        )
        + f", x {n_sector} sectors x {len(ITEM_LEVEL_METRICS)} metrics "
        f"{ITEM_LEVEL_METRICS}) "
        f"({n_food} food categories x {n_sector} sectors x 2 metrics "
        f"(co2e, water) + ch4 (unanchored) + land (the client's t/ha column "
        f"inverted, flat across sectors), plus prevention overrides), "
        f"{len(data['downstream'])} downstream rows "
        f"({n_ch4_matched} destinations matched directly to a ReFED "
        f"destination for ch4, 1 (upcycling) a stated stand-in, "
        f"{len(LEVY_CARRYING_DESTINATIONS)} get a nonzero cost). "
        f"Completeness asserted for all {len(ALL_NZ_DESTINATIONS)} "
        f"destinations and {n_food} food categories x {n_sector} sectors."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
