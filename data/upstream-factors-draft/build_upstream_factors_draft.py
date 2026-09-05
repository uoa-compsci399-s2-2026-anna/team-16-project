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
-- see "STAPLES", below), for ``co2e`` and ``water`` only (the client's table
has no ``ch4`` or ``cost`` column, and its ``land`` column has no metric to
attach to -- see "WHAT IS NOT REPRESENTED" below). Plus a zero override at
``destination = prevention`` for every one of those rows, so the set is
consistent with the other New Zealand-style sets in this repository even
though it is never published (§O-7).

``factor_downstream`` -- one row per (our destination, metric), ``sector =
null`` and ``food_category = null`` (the client's table 2 does not vary by
either), for ``co2e`` and ``water`` where a value exists. Plus an explicit
zero override at ``prevention``, matching the upstream override above and
for the identical reason.

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
2026-09-05)
----------------------------------------------------------------------------
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

WHAT IS NOT REPRESENTED
-------------------------
  * `land` -- the client's table gives t/ha for every food row, and this
    system has no `land` metric to receive it. Adding one is a metric-table
    change with system-wide effect (every existing result would gain a
    `land` line at zero); left for the owner to decide, not done here.
  * `ch4` -- the client's CO2-eq column is already a CO2-equivalent figure,
    not decomposed into its methane component; no upstream or downstream
    `ch4` row is written.
  * `cost` -- the client's tables carry no cost column; the NZ landfill levy
    ($75/tonne, `data-sources.md` item 3b) is a distinct figure this task
    does not seed.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
REFED_FACTORS_PATH = (
    REPO_ROOT / "tests" / "benchmark" / "refed" / "refed-benchmark-factors.json"
)

sys.path.insert(0, str(HERE))
from rawtec_source_data import (  # noqa: E402
    TABLE1,
    TABLE2,
    TABLE2_CO2_COPY_SOURCE,
    destination_row,
    food_row,
)
from public_farm_share_source_data import farm_share as owid_farm_share  # noqa: E402

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

# ReFED's own destination code for `other_recovery`'s fill, and for
# `combustion`'s water fill -- see the module docstring.
REFED_OTHER_RECOVERY_DESTINATION = "refed_industrial_uses"
REFED_INCINERATION_DESTINATION = "refed_incineration"


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
        if row["metric"] not in ("co2e", "water"):
            continue
        key = (row["food_category"], row["sector"], row["metric"])
        out[key] = Decimal(row["value_per_kg"])
    return out


def load_refed_downstream_values(destination: str) -> dict[str, list[Decimal]]:
    """Every published (sector, food_category) value for one ReFED
    destination, by metric -- used to fill `other_recovery` and
    `combustion`'s water, both by an unweighted mean/direct read across
    everything ReFED publishes for that destination (see module docstring).
    """
    data = json.loads(REFED_FACTORS_PATH.read_text(encoding="utf-8"))
    out: dict[str, list[Decimal]] = {"co2e": [], "water": []}
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


def build_upstream(refed_upstream) -> list[dict]:
    rows: list[dict] = []
    for nz_food, client_foods in NZ_FOOD_CATEGORY_SOURCES.items():
        rows.extend(_build_category_rows(nz_food, client_foods, refed_upstream))
    rows.extend(_build_staples_rows(refed_upstream))
    return rows


def build_downstream() -> list[dict]:
    rows: list[dict] = []

    # Mechanical check that the copy-paste correspondence this file's own
    # docstring describes is actually what the transcribed data says --
    # this is the check that keeps the provenance document's claim honest.
    for label, (category, food) in TABLE2_CO2_COPY_SOURCE.items():
        dest = destination_row(label)
        src = food_row(food)
        if src.category != category or dest.co2e_per_kg != src.co2e_per_kg:
            raise SystemExit(
                f"TABLE2_CO2_COPY_SOURCE claims {label!r} copies "
                f"{category}/{food}, but the transcribed data disagrees "
                f"({dest.co2e_per_kg} vs {src.co2e_per_kg})."
            )

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
                    f" [CO2-eq copied from Rawtec table 1's {TABLE2_CO2_COPY_SOURCE[r.destination][1]} row -- see provenance doc]"
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
                    "CAUTION: the CO2-eq column of table 2 is a verbatim copy "
                    "of table 1's CO2-eq column -- used here on the "
                    "repository owner's explicit instruction of 2026-09-05, "
                    "pending client confirmation. See "
                    "docs/upstream-factors-draft.md."
                )
            else:
                caution = (
                    "The water column does not share that defect and looks "
                    "like an independent measurement -- see "
                    "docs/upstream-factors-draft.md."
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
                "data_quality": "client-table2-verbatim" if metric == "co2e" else "client-table2",
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


def build() -> dict:
    refed_upstream = load_refed_generic_upstream()
    upstream = build_upstream(refed_upstream)
    downstream = build_downstream()

    return {
        "version_label": (
            "CLIENT-DRAFT-2026-09-05 (Rawtec + ReFED cumulative footprint, "
            "corrected) - NOT PUBLISHED"
        ),
        "is_mock": True,
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
            "roughly fourfold; see docs/upstream-factors-draft.md. is_mock "
            "stays true: these values are derived, not yet the client's "
            "confirmed figures -- the placeholder banner must keep showing "
            "until the owner decides otherwise. This set is a DRAFT and "
            "must never be published: doing so would archive whatever New "
            "Zealand set is currently live. Full provenance: "
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
             "notes": "No ch4 upstream or downstream row exists in this draft; the term is zero throughout."},
            {"metric": "water", "expression": "qty_kg * (upstream + downstream)",
             "notes": "Same expression as the live/mock set."},
            {"metric": "cost", "expression": "qty_kg * (upstream + downstream + const_FOOD_VALUE_PER_KG)",
             "notes": "No cost upstream or downstream row exists in this draft; the term is zero throughout."},
            {"metric": "mass", "expression": "qty_kg",
             "notes": "Same expression as the live/mock set."},
        ],
        "upstream": upstream,
        "downstream": downstream,
        "equivalences": [],
    }


def main() -> int:
    out_path = HERE / "upstream_factors_draft.json"
    data = build()
    out_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    n_food = len(NZ_FOOD_CATEGORY_SOURCES) + 1  # + staples
    n_sector = len(ALL_NZ_SECTORS)
    print(
        f"{out_path.name}: {len(data['upstream'])} upstream rows "
        f"({n_food} food categories x {n_sector} sectors x 2 metrics, plus "
        f"prevention overrides), {len(data['downstream'])} downstream rows."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
