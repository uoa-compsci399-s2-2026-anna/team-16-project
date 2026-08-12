"""Derive the ReFED comparison factor set from ReFED's published conversion factors.

This script is the audit trail. It reads exactly one input --
``impact_calculator_conversion_factors.csv``, downloaded verbatim from

    https://refed-roadmap.s3-us-west-2.amazonaws.com/csv/public_downloads/
    impact_calculator/impact_calculator_conversion_factors.csv

which is the file behind the "Download factors" control on

    https://insights-engine.refed.org/impact-calculator

-- and writes two JSON files that the loader applies together:

    refed-benchmark-taxonomy.json   ReFED's own sectors, food types and destinations
    refed-benchmark-factors.json    the factor set itself, is_mock = true

Nothing here is estimated, interpolated or remembered. Every stored number is a
published ReFED number multiplied by an exact unit conversion, and the
conversion applied to each row is recorded in that row's ``source_note``.

WHY THE FOOD CATEGORY CODES CARRY A SECTOR
------------------------------------------
ReFED publishes upstream and downstream separately, and both vary by
(sector, food_type, destination).  Our schema splits the two tables
differently: ``factor_upstream`` is keyed (sector, food_category, destination)
-- which matches ReFED exactly -- but ``factor_downstream`` is keyed
(destination, food_category) and has no sector column.  ReFED's downstream
genuinely differs by sector in 82 of the 102 (food_type, destination) groups,
so a nine-row food_category table cannot hold it without losing data.

So this fixture uses one sector row and 39 food_category rows, one per ReFED
(sector, food_type) pair that ReFED actually publishes.  That is a lossless
re-encoding of ReFED's own dimensions, not a mapping onto New Zealand
categories: the numbers are unchanged and no two ReFED cells are merged.  A
single sector row also makes it impossible to pick a sector that disagrees
with the food category and get a silently wrong answer.

UNIT CONVERSIONS
----------------
ReFED's "ton" is the US short ton.  Two independent facts in ReFED's own code
fix this and neither is an assumption:

  * ``meals_recovered_per_ton`` is 1666.6666666666667 for every Donations row,
    which is 2000 lb / 1.2 lb per meal.
  * the unit selector in the calculator bundle reads
    ``{label:"Kg", value:"kg", conversion:907.185}`` and divides the entered
    quantity by that number to reach short tons.

We therefore use ReFED's own 907.185 kg per short ton rather than the exact
907.18474, so that entering a kilogram figure on ReFED's site and the same
figure here cannot disagree through the conversion constant.

  co2e   kg CO2e / kg  = MTCO2e per short ton   * 1000 / 907.185
  ch4    kg CH4  / kg  = MTCH4  per short ton   * 1000 / 907.185
  water  L       / kg  = US gallons per short ton * 3.785411784 / 907.185
  cost   USD     / kg  = SCC USD per short ton          / 907.185

1 metric ton = 1000 kg by definition; 1 US liquid gallon = 3.785411784 L by
definition.

WHAT IS NOT REPRESENTED
-----------------------
  * Meals recovered.  Our metric table has no meals metric and metric rows are
    global, so adding one would change what the New Zealand set reports.
  * ReFED's 20-year CO2e.  It is absent from the published CSV, and in ReFED's
    own client-side dataset it is zero for 284 of 429 rows while the 100-year
    value is non-zero.  ReFED's calculator never displays it; the time-horizon
    toggle only switches the methane read-out.  ReFED's methane mass is
    identical at 20 and 100 years in all 429 rows, so ``gwp_horizon`` has no
    effect on this factor set and none is expected.
"""

from __future__ import annotations

import argparse
import csv
import json
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path

HERE = Path(__file__).resolve().parent

# --------------------------------------------------------------------------
# Unit conversions.  Exact rationals; see the module docstring for provenance.
# --------------------------------------------------------------------------
KG_PER_SHORT_TON = Decimal("907.185")  # ReFED's own constant, from their bundle
KG_PER_METRIC_TON = Decimal("1000")
LITRES_PER_US_GALLON = Decimal("3.785411784")

SCALE = Decimal("0.0000000001")  # factor_upstream.value_per_kg is DECIMAL(20,10)

CONV = {
    "co2e": KG_PER_METRIC_TON / KG_PER_SHORT_TON,
    "ch4": KG_PER_METRIC_TON / KG_PER_SHORT_TON,
    "water": LITRES_PER_US_GALLON / KG_PER_SHORT_TON,
    "cost": Decimal(1) / KG_PER_SHORT_TON,
}
CONV_NOTE = {
    "co2e": "x1000/907.185 -> kg CO2e/kg",
    "ch4": "x1000/907.185 -> kg CH4/kg",
    "water": "x3.785411784/907.185 -> L/kg",
    "cost": "/907.185 -> USD/kg",
}

# CSV column -> (metric, upstream column, downstream column).  Water and meals
# are published as a single column with no split; water is carried entirely on
# the upstream side, which is where a production water footprint belongs.
UPSTREAM_COLS = {
    "co2e": "upstream_100_year_mtco2e_footprint_per_ton",
    "ch4": "upstream_mtch4_footprint_per_ton",
    "cost": "upstream_100_year_mtco2e_scc_footprint_per_ton",
    "water": "gallons_water_footprint_per_ton",
}
DOWNSTREAM_COLS = {
    "co2e": "downstream_100_year_mtco2e_footprint_per_ton",
    "ch4": "downstream_mtch4_footprint_per_ton",
    "cost": "downstream_100_year_mtco2e_scc_footprint_per_ton",
}

# ReFED label -> our code fragment.  ReFED's own slugs (taken from the
# calculator bundle) are used where they exist so the mapping is checkable.
SECTOR_SLUG = {
    "Farm": "farm",
    "Foodservice": "foodservice",
    "Manufacturing": "manufacturing",
    "Residential": "residential",
    "Retail": "retail",
}
FOOD_SLUG = {
    "Breads & Bakery": "breads_bakery",
    "Dairy & Eggs": "dairy_eggs",
    "Dry Goods": "dry_goods",
    "Fresh Meat & Seafood": "fresh_meat_seafood",
    "Frozen": "frozen",
    "Prepared Foods": "prepared_foods",
    "Produce": "produce",
    "Ready-To-Drink Beverages": "ready_to_drink_beverages",
    "Standard Mix": "standard_mix",
}

# ReFED destination label -> (our code, ReFED's own slug, our destination_group,
# sort order).  The group assignment mirrors what admin/seed.py already does
# with the identically-named New Zealand destinations, so the waste/non-waste
# split is not invented here.
DESTINATIONS = [
    ("Prevention", "refed_prevention", "prevention", "reuse", 205),
    ("Donations", "refed_donations", "donations", "reuse", 210),
    ("Animal Feed", "refed_animal_feed", "animal-feed", "reuse", 230),
    ("Industrial Uses", "refed_industrial_uses", "biomaterial-processing",
     "recycle_recovery", 280),
    ("Composting", "refed_composting", "composting", "recycle_recovery", 240),
    ("Anaerobic Digestion", "refed_anaerobic_digestion", "anaerobic-digestion",
     "recycle_recovery", 250),
    ("Land Application", "refed_land_application", "land-application",
     "recycle_recovery", 260),
    ("Not Harvested", "refed_not_harvested", "not-harvested",
     "recycle_recovery", 270),
    ("Incineration", "refed_incineration", "incineration", "disposal", 300),
    ("Landfill", "refed_landfill", "landfill", "disposal", 310),
    ("Dumping", "refed_dumping", "refuse-discards", "disposal", 320),
    ("Sewer", "refed_sewer", "sewer", "disposal", 330),
]
DEST_CODE = {label: code for label, code, _slug, _grp, _sort in DESTINATIONS}

SECTOR_CODE = "refed_us"
SOURCE_URL = (
    "https://refed-roadmap.s3-us-west-2.amazonaws.com/csv/public_downloads/"
    "impact_calculator/impact_calculator_conversion_factors.csv"
)
# The destination whose upstream value is taken as the generic (destination
# IS NULL) row.  Landfill is published for every sector and food type and
# carries ReFED's unadjusted upstream footprint.
BASE_DESTINATION = "Landfill"


def q(value: Decimal) -> str:
    """Quantise to the DECIMAL(20,10) scale, in plain notation."""
    return f"{value.quantize(SCALE, rounding=ROUND_HALF_EVEN):f}"


def read_csv(path: Path) -> tuple[list[dict[str, str]], str]:
    text = path.read_text(encoding="utf-8-sig")
    lines = text.split("\n")
    provenance = lines[0].strip()
    rows = [r for r in csv.DictReader(lines[3:]) if r.get("sector")]
    if not rows:
        raise SystemExit(f"no data rows parsed from {path}")
    return rows, provenance


def food_code(sector_label: str, food_label: str) -> str:
    return f"refed_{SECTOR_SLUG[sector_label]}_{FOOD_SLUG[food_label]}"


def food_name(sector_label: str, food_label: str) -> str:
    return f"ReFED {sector_label} / {food_label}"


def build(csv_path: Path, tax_path: Path, fac_path: Path) -> None:
    rows, provenance = read_csv(csv_path)

    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for r in rows:
        key = (r["sector"], r["food_type"])
        if key not in seen:
            seen.add(key)
            pairs.append(key)

    by_cell = {(r["sector"], r["food_type"], r["destination"]): r for r in rows}

    # ---------------- taxonomy companion ----------------
    taxonomy = {
        "description": (
            "ReFED's own sectors, food types and destinations, added alongside "
            "the New Zealand MfE taxonomy so that a ReFED comparison run uses "
            "ReFED's dimensions rather than a mapping onto NZ categories. "
            "Every code is prefixed refed_ and nothing here replaces or edits "
            "an existing row."
        ),
        "source": SOURCE_URL,
        "source_header": provenance,
        "sectors": [
            {
                "code": SECTOR_CODE,
                "name": "ReFED comparison (United States)",
                "description": (
                    "Benchmark fixture only. One sector row: ReFED's supply "
                    "chain stage is carried in the food category code, because "
                    "factor_downstream has no sector column and ReFED's "
                    "downstream factors differ by sector."
                ),
                "sort_order": 900,
                "active": True,
            }
        ],
        "food_categories": [
            {
                "code": food_code(s, f),
                "name": food_name(s, f),
                "is_standard_mix": False,
                "sort_order": 900 + index,
                "active": True,
            }
            for index, (s, f) in enumerate(pairs)
        ],
        "destinations": [
            {
                "code": code,
                "name": f"ReFED {label}",
                "group_code": group,
                "description": (
                    f"ReFED destination '{label}' (ReFED slug '{slug}'). "
                    "Benchmark fixture only; not a New Zealand MfE destination."
                ),
                "sort_order": sort,
                "active": True,
            }
            for label, code, slug, group, sort in DESTINATIONS
        ],
    }

    # ---------------- factor set ----------------
    upstream: list[dict[str, object]] = []
    downstream: list[dict[str, object]] = []

    for sector_label, food_label in pairs:
        fcode = food_code(sector_label, food_label)
        present = [
            label for label, *_ in DESTINATIONS
            if (sector_label, food_label, label) in by_cell
        ]
        base_row = by_cell[(sector_label, food_label, BASE_DESTINATION)]

        for metric, column in UPSTREAM_COLS.items():
            factor = CONV[metric]
            base = Decimal(base_row[column]) * factor
            upstream.append({
                "sector": SECTOR_CODE,
                "food_category": fcode,
                "destination": None,
                "metric": metric,
                "value_per_kg": q(base),
                "source_note": (
                    f"ReFED {sector_label}/{food_label}/{BASE_DESTINATION} "
                    f"[{column}] = {base_row[column]} per US short ton; "
                    f"{CONV_NOTE[metric]}. Generic row: ReFED's unadjusted "
                    f"upstream footprint, read at {BASE_DESTINATION}."
                ),
                "data_quality": "refed-published",
            })
            # Destination-specific upstream, wherever ReFED's own value differs
            # from the base.  ReFED adjusts upstream for Prevention (zero),
            # Donations and Industrial Uses; this detects that rather than
            # assuming it.
            for label in present:
                cell = by_cell[(sector_label, food_label, label)]
                if cell[column] == base_row[column]:
                    continue
                value = Decimal(cell[column]) * factor
                upstream.append({
                    "sector": SECTOR_CODE,
                    "food_category": fcode,
                    "destination": DEST_CODE[label],
                    "metric": metric,
                    "value_per_kg": q(value),
                    "source_note": (
                        f"ReFED {sector_label}/{food_label}/{label} [{column}] "
                        f"= {cell[column]} per US short ton; "
                        f"{CONV_NOTE[metric]}. ReFED publishes a different "
                        f"upstream footprint here than at {BASE_DESTINATION}."
                    ),
                    "data_quality": "refed-published",
                })

        for metric, column in DOWNSTREAM_COLS.items():
            factor = CONV[metric]
            for label in present:
                cell = by_cell[(sector_label, food_label, label)]
                value = Decimal(cell[column]) * factor
                downstream.append({
                    "destination": DEST_CODE[label],
                    "food_category": fcode,
                    "metric": metric,
                    "value_per_kg": q(value),
                    "source_note": (
                        f"ReFED {sector_label}/{food_label}/{label} [{column}] "
                        f"= {cell[column]} per US short ton; {CONV_NOTE[metric]}"
                    ),
                    "data_quality": "refed-published",
                })
        # Water has no downstream component in ReFED's data; the single
        # gallons column is carried entirely on the upstream side.  Explicit
        # zeros so nothing depends on the lookup falling back.
        for label in present:
            downstream.append({
                "destination": DEST_CODE[label],
                "food_category": fcode,
                "metric": "water",
                "value_per_kg": q(Decimal(0)),
                "source_note": (
                    "ReFED publishes one water column with no upstream / "
                    "downstream split; it is carried on the upstream side."
                ),
                "data_quality": "definitional",
            })

    # The publish gate (db/repository.py, find_missing_prevention_upstream)
    # looks for a zero upstream row against the destination code 'prevention'.
    # ReFED's prevention destination is refed_prevention, so emit the NZ-coded
    # row as well.  Both are genuinely zero in ReFED's data: prevented food was
    # never produced.
    for sector_label, food_label in pairs:
        fcode = food_code(sector_label, food_label)
        for metric in UPSTREAM_COLS:
            upstream.append({
                "sector": SECTOR_CODE,
                "food_category": fcode,
                "destination": "prevention",
                "metric": metric,
                "value_per_kg": q(Decimal(0)),
                "source_note": (
                    "Zero by definition; ReFED publishes zero for every column "
                    "of every Prevention row. Keyed on the NZ 'prevention' "
                    "code so the publish-time check passes; the ReFED-coded "
                    "twin is refed_prevention."
                ),
                "data_quality": "definitional",
            })

    factors = {
        "version_label": "REFED-COMPARISON-2026-04-03 - NOT NZ DATA",
        "is_mock": True,
        "notes": (
            "ReFED comparison fixture. United States factors published by "
            "ReFED, used to check this engine against an external calculator. "
            "THESE ARE NOT NEW ZEALAND FIGURES AND MUST NEVER BE PUBLISHED AS "
            "THE LIVE FACTOR SET. Source: the 'Download factors' file behind "
            f"ReFED's Impact Calculator, {SOURCE_URL} , header line: "
            f"'{provenance}'. Every value is a published ReFED number per US "
            "short ton converted to a per-kilogram factor using 907.185 kg per "
            "US short ton (ReFED's own constant), 1000 kg per metric ton, and "
            "3.785411784 L per US liquid gallon. The 'cost' metric carries "
            "ReFED's social cost of carbon in US DOLLARS, not New Zealand "
            "dollars; the metric's display unit will read NZD and is wrong for "
            "this set. Meals recovered is not represented: there is no meals "
            "metric and metric rows are global. gwp_horizon has no effect on "
            "this set: ReFED's methane mass is identical at 20 and 100 years "
            "in all 429 of its rows, and its 20-year CO2e is not published in "
            "this file. Derived by tests/benchmark/refed/build_refed_"
            "benchmark.py from the CSV committed beside it."
        ),
        "constants": [
            {
                "code": "FOOD_VALUE_PER_KG",
                "value": "0.0000000000",
                "unit": "USD / kg",
                "note": (
                    "Zero. ReFED's dollar metric is the social cost of carbon "
                    "only; it does not include the market value of the wasted "
                    "food. Keeping this at zero is what makes the 'cost' "
                    "formula reduce to ReFED's own calculation."
                ),
            }
        ],
        "formulas": [
            {
                "metric": metric,
                "expression": expression,
                "notes": (
                    "Identical to the expression the New Zealand set uses. The "
                    "comparison is only diagnostic if the formula under test "
                    "is the formula the product runs."
                ),
            }
            for metric, expression in (
                ("co2e", "qty_kg * (upstream + downstream)"),
                ("ch4", "qty_kg * (upstream + downstream)"),
                ("water", "qty_kg * (upstream + downstream)"),
                ("cost", "qty_kg * (upstream + downstream + const_FOOD_VALUE_PER_KG)"),
                ("mass", "qty_kg"),
            )
        ],
        "upstream": upstream,
        "downstream": downstream,
        "equivalences": [],
    }

    tax_path.write_text(json.dumps(taxonomy, indent=2) + "\n", encoding="utf-8")
    fac_path.write_text(json.dumps(factors, indent=2) + "\n", encoding="utf-8")
    print(f"{tax_path.name}: 1 sector, {len(taxonomy['food_categories'])} food "
          f"categories, {len(taxonomy['destinations'])} destinations")
    print(f"{fac_path.name}: {len(upstream)} upstream rows, "
          f"{len(downstream)} downstream rows")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv", type=Path,
        default=HERE / "impact_calculator_conversion_factors.csv")
    parser.add_argument(
        "--taxonomy-out", type=Path,
        default=HERE / "refed-benchmark-taxonomy.json")
    parser.add_argument(
        "--factors-out", type=Path,
        default=HERE / "refed-benchmark-factors.json")
    args = parser.parse_args()
    build(args.csv, args.taxonomy_out, args.factors_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
