"""Derive a draft New Zealand factor set from the client's Rawtec tables.

This is the audit trail for open item O-1's first movement. It reads two
committed inputs --

    rawtec_source_data.py                    the client's own two tables,
                                              transcribed verbatim (see its
                                              own docstring for where from)
    tests/benchmark/refed/
        refed-benchmark-factors.json          ReFED's published upstream
                                              footprint, already derived and
                                              committed in this repository by
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
supplied a usable total, for ``co2e`` and ``water`` only (the client's table
has no ``ch4`` or ``cost`` column, and its ``land`` column has no metric to
attach to -- see "WHAT IS NOT REPRESENTED" below). Plus a zero override at
``destination = prevention`` for every one of those rows, so the set is
consistent with the other New Zealand-style sets in this repository even
though it is never published (§O-7).

``factor_downstream`` -- one row per (our destination, metric), ``sector =
null`` and ``food_category = null`` (the client's table 2 does not vary by
either), for ``co2e`` and ``water`` where the client published a value. Plus
an explicit zero override at ``prevention``, matching the upstream override
above and for the identical reason.

THE STAGE SPLIT (STEP 1 OF THE CLIENT'S OWN INSTRUCTION)
----------------------------------------------------------
The client's own method note (``data-sources.md``) says plainly that the
supply-chain-stage breakdown does not exist in their data, and directs the
team to "figure out the proportions of CO2e/water etc. from each supply
chain stage that ReFED use, and apply this to the EFC with the Rawtec farm to
fork data to get EFC calculations by supply chain stage."

This repository already holds exactly the ReFED material that instruction
asks for: ``tests/benchmark/refed/refed-benchmark-factors.json``, built by
``build_refed_benchmark.py`` from ReFED's own published
"Download factors" CSV. For each ReFED food category it carries one generic
upstream value per ReFED sector (Farm, Manufacturing, Retail, Foodservice,
Residential) -- the average, per kilogram, embodied production footprint of
that food type's waste at that stage.

**Those five values are not a monotonic build-up along the supply chain**,
and this script does not treat them as one. For "Standard Mix", ReFED's own
number is lower at Residential (806.92 L/kg water) than at Retail (1275.66)
or Foodservice (1914.85) -- an aggregate across many food types wasted in
different mixes at each stage, not one food's cumulative footprint gaining a
processing step at a time. Reading five non-monotonic numbers as "stage N
adds this much on top of stage N-1" would fabricate a cumulative curve this
data does not support -- exactly the "plausible-looking curve" the brief
warns against inventing. Reading them as *shares of a whole* asks the data
for less than that, and is what it is used for here: each ReFED sector's
generic value is normalised against the sum of the (up to five) values ReFED
publishes for that food category, giving a proportion for each stage that
sums to 1 by construction, and each proportion is then multiplied against
the client's own farm-to-fork (or farm-to-shelf) total for the matching New
Zealand category. The six New Zealand stage values for a category therefore
sum back to the client's own total exactly -- a property a reader can check
by hand, and the reason this is the "proportions" reading of the client's
instruction rather than a cumulative one.

Where ReFED publishes no Farm-stage value for a food category at all (true
for five of the nine ReFED categories -- Breads & Bakery, Dairy & Eggs,
Fresh Meat & Seafood, Frozen, Prepared Foods, Ready-To-Drink Beverages carry
no Farm row; only Dry Goods, Produce and Standard Mix do, per
``build_refed_benchmark.py``'s own docstring), this script assigns
``primary_production`` a proportion of zero for that category rather than
inventing one. That is ReFED's own choice about its own data, carried
through rather than second-guessed.

ReFED has five sectors; this system has six, because Foodservice and
Residential do not exhaust "consumer stage" here -- Institutions
(`consumer_institution`) is also seeded and ReFED has no equivalent sector at
all. In the absence of a fourth-way to divide it, ReFED's Foodservice share is
split evenly (50 / 50) between `consumer_hospitality` and
`consumer_institution` rather than given to both in full, which would double
count it and break the "sums back to the client's total" property above.
This is a stated, flat assumption exactly where our taxonomy is finer than
ReFED's, not a claim that hospitality and institutional waste actually carry
identical footprints.

FOOD CATEGORY MAPPING (STEP 2)
--------------------------------
See ``NZ_FOOD_CATEGORY_SOURCES`` below for the client rows behind each New
Zealand category, and the client rows a matching ReFED category's shares are
drawn from. ``meat`` and ``dairy`` are unweighted arithmetic means of several
client rows because the client supplied no production weights -- see the
module docstring on ``rawtec_source_data.py`` and the provenance document
for the full working. ``staples`` has no client analogue at all and is left
unseeded; ``standard_mix`` maps directly to the client's "General mixed food
product". The client's ``Eggs`` row has no New Zealand category to receive
it and is also left out -- both gaps are reported, not silently absorbed
into a neighbour.

DESTINATION MAPPING (STEP 3, TABLE 2 -- IN SCOPE ON THE OWNER'S RULING OF
2026-09-05)
----------------------------------------------------------------------------
See ``NZ_DESTINATION_SOURCES`` below. `prevention` receives no client row --
it is the mandatory 100% offset (§O-7) and giving it a factor would silently
inflate every net-benefit figure the calculator reports. `other_recovery`
also receives no client row: nothing in the client's seventeen destinations
is a comparably-shaped catch-all, and the owner was explicit that
"Other Food Waste" (a disposal row, *Bin to Landfill*) must not be folded into
`other_recovery` (a `recycle_recovery` row) merely because the names read
alike.

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

SCALE = Decimal("0.0000000001")  # factor_upstream/downstream.value_per_kg: DECIMAL(20,10)


def q(value: Decimal) -> str:
    return f"{value.quantize(SCALE, rounding=ROUND_HALF_EVEN):f}"


def mean(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / Decimal(len(values))


# ---------------------------------------------------------------------------
# Step 2: New Zealand food category -> client TABLE1 row(s).
#
# One food name: used directly. Several: unweighted arithmetic mean, because
# the client supplied no production weights to do anything better with --
# see the provenance document for the stated reasoning. `staples` is absent
# on purpose: the client's twenty rows have no analogue for it.
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
    # "staples": no client analogue. Not seeded -- see the module docstring.
}

# Which ReFED food category supplies the stage *shares* for each New Zealand
# category above. Chosen by the closest available match; several New Zealand
# categories necessarily share one ReFED category because ReFED's own nine
# do not split as finely (meat and seafood both draw their shares from
# ReFED's one "Fresh Meat & Seafood"; fruit and vegetables both draw from
# ReFED's one "Produce"). This governs the *proportions* only -- the totals
# multiplied through remain the client's own.
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
}

# ---------------------------------------------------------------------------
# Step 3 (table 2, in scope on the owner's ruling of 2026-09-05): New
# Zealand destination -> client TABLE2 destination(s).
#
# `prevention` and `other_recovery` are deliberately absent -- see the module
# docstring.
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
    # "other_recovery": no client analogue. Not seeded -- see the module
    # docstring; do not fold "Other Food Waste" in here, it is a disposal row.
    # "prevention": the mandatory 100% offset. Never given a client value.
}

# Our six sectors -> the ReFED sector(s) whose share this sector takes.
# `consumer_hospitality` and `consumer_institution` split ReFED's single
# Foodservice share in half -- see the module docstring.
NZ_SECTOR_TO_REFED_SECTORS: dict[str, list[tuple[str, Decimal]]] = {
    "primary_production": [("refed_farm", Decimal(1))],
    "processing": [("refed_manufacturing", Decimal(1))],
    "wholesale_retail": [("refed_retail", Decimal(1))],
    "consumer_household": [("refed_residential", Decimal(1))],
    "consumer_hospitality": [("refed_foodservice", Decimal("0.5"))],
    "consumer_institution": [("refed_foodservice", Decimal("0.5"))],
}
REFED_SECTOR_ORDER = [
    "refed_farm", "refed_manufacturing", "refed_retail",
    "refed_foodservice", "refed_residential",
]


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


def refed_stage_shares(
    refed_upstream: dict[tuple[str, str, str], Decimal],
    refed_food: str,
    metric: str,
) -> dict[str, Decimal]:
    """Normalised (sum = 1) share of each ReFED sector for one food/metric.

    A ReFED sector absent for this food category (no Farm row for most
    categories -- see the module docstring) is given a share of zero rather
    than being interpolated or dropped from the normalisation silently: it
    is simply excluded from both the numerator and the denominator, which is
    the same thing stated the other way.
    """
    available = {
        sector: refed_upstream[(refed_food, sector, metric)]
        for sector in REFED_SECTOR_ORDER
        if (refed_food, sector, metric) in refed_upstream
    }
    total = sum(available.values(), Decimal(0))
    if total == 0:
        raise SystemExit(
            f"No ReFED upstream data at all for {refed_food!r}/{metric!r}; "
            "cannot derive a stage share."
        )
    return {sector: available.get(sector, Decimal(0)) / total for sector in REFED_SECTOR_ORDER}


def build_upstream(refed_upstream) -> list[dict]:
    rows: list[dict] = []
    all_sectors = list(NZ_SECTOR_TO_REFED_SECTORS)
    all_food_categories = list(NZ_FOOD_CATEGORY_SOURCES)

    for nz_food, client_foods in NZ_FOOD_CATEGORY_SOURCES.items():
        refed_food = NZ_TO_REFED_FOOD_SHAPE[nz_food]
        for metric, attr in (("co2e", "co2e_per_kg"), ("water", "water_l_per_kg")):
            client_rows = [food_row(name) for name in client_foods]
            values = [getattr(r, attr) for r in client_rows]
            total = mean(values)
            components = "; ".join(
                f"{r.food} ({r.category}) = {getattr(r, attr)} [{r.life_cycle}]"
                for r in client_rows
            )
            agg_note = (
                f"mean of {len(values)} client row(s): {components} -> "
                f"unweighted mean {total}"
                if len(values) > 1
                else f"client row: {components} (used directly, no aggregation)"
            )

            shares = refed_stage_shares(refed_upstream, refed_food, metric)
            for nz_sector, refed_parts in NZ_SECTOR_TO_REFED_SECTORS.items():
                share = sum(
                    (shares[refed_sector] * weight for refed_sector, weight in refed_parts),
                    Decimal(0),
                )
                value = total * share
                share_note = " + ".join(
                    f"{weight} x {shares[refed_sector]} ({refed_sector})"
                    for refed_sector, weight in refed_parts
                )
                rows.append({
                    "sector": nz_sector,
                    "food_category": nz_food,
                    "destination": None,
                    "metric": metric,
                    "value_per_kg": q(value),
                    "source_note": (
                        f"Rawtec farm-to-{'fork' if any(r.life_cycle == 'Farm to Fork' for r in client_rows) else 'shelf'} "
                        f"total for {nz_food}: {agg_note}. ReFED stage share "
                        f"for {nz_sector} (from {refed_food}, normalised over "
                        f"the ReFED sectors it publishes): {share_note} = "
                        f"{share}. {total} x {share} = {value} -> "
                        f"{q(value)} (DECIMAL(20,10))."
                    ),
                    "data_quality": "derived-client-refed-split",
                })

            # Prevention override, zero by definition -- not from client data.
            # See tests/benchmark/refed/build_refed_benchmark.py for the same
            # pattern and its rationale (§O-7).
            for nz_sector in all_sectors:
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
                # water). Not seeded -- see the provenance document.
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
        "version_label": "CLIENT-DRAFT-2026-09-05 (Rawtec + ReFED stage split) - NOT PUBLISHED",
        "is_mock": True,
        "notes": (
            "Draft factor set derived from the client's own Rawtec tables "
            "(farm-to-fork/-shelf production footprint by food, and bin-to-"
            "destination footprint by destination), with upstream figures "
            "split across this system's six supply-chain sectors using "
            "ReFED's own published stage shares (tests/benchmark/refed/"
            "refed-benchmark-factors.json). is_mock stays true: these values "
            "are derived, partly from United States ReFED data via a stated "
            "proportional split, and not yet the client's confirmed figures "
            "-- the placeholder banner must keep showing until the owner "
            "decides otherwise. This set is a DRAFT and must never be "
            "published: doing so would archive whatever New Zealand set is "
            "currently live. Full provenance: docs/upstream-factors-draft.md "
            "and data/upstream-factors-draft/build_upstream_factors_draft.py."
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
    n_food = len(NZ_FOOD_CATEGORY_SOURCES)
    n_sector = len(NZ_SECTOR_TO_REFED_SECTORS)
    print(
        f"{out_path.name}: {len(data['upstream'])} upstream rows "
        f"({n_food} food categories x {n_sector} sectors x 2 metrics, plus "
        f"prevention overrides), {len(data['downstream'])} downstream rows."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
