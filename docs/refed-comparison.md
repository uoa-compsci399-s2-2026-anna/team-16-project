---
title: "Checking the engine against ReFED's Impact Calculator"
date: "2026-08-12"
status: "Benchmark fixture. Not part of the deliverable calculator."
---

# Checking the engine against ReFED's Impact Calculator

## 1. Why

Every correctness check this project has descends from `docs/interfaces.md`.
The golden cases, the fixtures, the engine and the contract were written by the
same people from the same document, so they share one ancestor: they can prove
the engine does what we wrote down, and they cannot prove that what we wrote
down is right. ReFED's Impact Calculator is the product this calculator is
modelled on, it is public, and it publishes its factors. Running one scenario
through both is the only check here whose expected answer nobody on this team
produced.

This is a **test fixture**. It is United States data. It must never be
published as the live factor set, and `is_mock` is `true` on it so the
placeholder banner stays up if it ever is.

## 2. Where every number came from

| What | Where | Used for |
| --- | --- | --- |
| `impact_calculator_conversion_factors.csv` | <https://refed-roadmap.s3-us-west-2.amazonaws.com/csv/public_downloads/impact_calculator/impact_calculator_conversion_factors.csv> — the file behind the **Download factors** control on <https://insights-engine.refed.org/impact-calculator> | Every stored factor. It is the only file with upstream and downstream as separate columns, which is what our two factor tables need. Header line: *"Calculated by ReFED based on data from various sources. Data last updated April 03 2026"*. |
| `refed_calculator_totals.json` | The JSON blob inside the client bundle of <https://insights-engine.refed.org/impact-calculator> (webpack module `36573`; the chunk filename carries a content hash and changes when ReFED redeploys, so re-fetch the page and search its `/_next/static/chunks/*.js` for `total_100_year_mtco2e_footprint_per_ton`) | The independent expected answer. It carries only combined totals, never the split, and it is what ReFED's own page computes with. |

Both files are committed under `tests/benchmark/refed/`. Nothing was estimated,
interpolated, or carried from memory. `build_refed_benchmark.py` derives the
factor set from the CSV alone and records, in every row's `source_note`, the
ReFED column and value it came from and the conversion applied.

The CSV's `upstream + downstream` reproduces the totals file for all 429 cells,
worst relative difference **2.4e-16**. That agreement is not decoration: it is
what licenses loading the split into two tables, and
`test_refed_split_sums_to_refed_totals` re-checks it so that a ReFED
republication is diagnosed as a data problem before it looks like an engine
problem.

ReFED's methodology PDFs were checked and are not a source of factors.
`Impact_Calculator_Methodology_Sep2024.pdf` contains no numeric factors at all,
and the Quantis factor methodology contains food item properties — water
content, biogenic and fossil carbon, energy, nitrogen, phosphorus, lipid,
protein — which are inputs to a derivation, not finished factors.

## 3. Units, stated once and precisely

**ReFED's "ton" is the US short ton, not a tonne.** Two facts in ReFED's own
code settle it, and neither is an assumption:

- `meals_recovered_per_ton` is `1666.6666666666667` on every Donations row,
  which is 2000 lb divided by 1.2 lb per meal;
- ReFED's unit selector reads
  `{label:"Kg", value:"kg", conversion:907.185}` and divides the entered
  quantity by that number to reach short tons.

Conversions applied, all in `build_refed_benchmark.py`:

| Our metric | Our unit | From | Multiplied by |
| --- | --- | --- | --- |
| `co2e` | kg CO2e per kg | MTCO2e per short ton | `1000 / 907.185` |
| `ch4` | kg CH4 per kg | MTCH4 per short ton | `1000 / 907.185` |
| `water` | L per kg | US gallons per short ton | `3.785411784 / 907.185` |
| `cost` | see §6 | USD per short ton | `1 / 907.185` |

907.185 is **ReFED's own constant**, deliberately used instead of the exact
907.18474, so that a kilogram figure entered on ReFED's site and the same
figure entered here cannot disagree through the conversion. 1 metric ton is
1000 kg and 1 US liquid gallon is 3.785411784 L, both by definition.

> **Enter kilograms on ReFED's site, not metric tons.** ReFED's Metric Tons
> option uses `conversion: 0.907` — three significant figures — which is a
> 0.02% error against its own kilogram option. That is far larger than
> anything this comparison is trying to detect and would look exactly like an
> engine fault.

## 4. ReFED's taxonomy had to be added, and how

ReFED's taxonomy is not ours, so the fixture brings its own rather than mapping
onto the New Zealand MfE categories: five supply chain stages, nine food types,
twelve destinations. `refed-benchmark-taxonomy.json` adds

- **1 sector** — `refed_us`
- **39 food categories** — `refed_retail_produce`, `refed_farm_dry_goods`, and
  so on: one per (stage, food type) pair ReFED actually publishes
- **12 destinations** — `refed_landfill`, `refed_composting`, …, assigned to
  the three existing destination groups the same way `admin/seed.py` assigns
  the identically-named New Zealand destinations, so the waste/non-waste split
  is not invented here

Every code is prefixed `refed_`. Nothing existing is edited, renamed or
deactivated; the loader creates only codes that are absent.

**Why the food category carries the supply chain stage.** `factor_upstream` is
keyed `(sector, food_category, destination)`, which matches ReFED's key
exactly. `factor_downstream` is keyed `(destination, food_category)` and has no
sector column — and ReFED's downstream factors genuinely differ by sector in
**82 of their 102** (food type, destination) groups. Nine food category rows
therefore cannot hold ReFED's downstream data without losing some of it. The
39-row encoding is lossless: no two ReFED cells are merged and no number
changes. Using a single sector row rather than five is what makes it impossible
to select a stage that disagrees with the food category and get a plausible
wrong answer instead of an obvious one.

No metric rows are added. Metric rows are global, and an extra active metric
would add a column to what the New Zealand set reports.

## 5. The scenario to run first

**Retail / Produce, 10,000 kg, mass-balanced.**

| | Current | Alternative |
| --- | --- | --- |
| Landfill | 8,000 kg | 2,000 kg |
| Composting | 2,000 kg | 2,000 kg |
| Donations | — | 3,000 kg |
| Prevention | — | 3,000 kg |
| **Total** | **10,000 kg** | **10,000 kg** |

It was chosen so the comparison exercises the machinery rather than one
multiplication:

- **Landfill and Composting** draw the generic upstream row;
- **Donations** draws a destination-specific upstream row — ReFED reduces the
  upstream footprint to about 27% there, and halves the water;
- **Landfill** has a positive downstream factor, **Composting** a negative one
  (an avoided-emissions credit, which charts must render below the axis);
- **Prevention** is a complete offset, upstream and downstream both zero, which
  is what keeps the two scenarios mass-conserving;
- four lines are summed on the alternative side, and the net benefit is
  `current − alternative`.

### On ReFED

<https://insights-engine.refed.org/impact-calculator>

1. Sector: **Retail**
2. Food type: **Produce**
3. Unit: **Kg** (see §3 — not Metric Tons)
4. Current scenario: Landfill `8000`, Composting `2000`
5. Alternative scenario: Landfill `2000`, Composting `2000`, Donations `3000`,
   Prevention `3000`
6. Time Horizon: **100 Year**
7. Read the five tabs. Use **Download Results** rather than the on-screen
   figures where you can: the page rounds for display, and at these magnitudes
   it shows only two decimal places.

### Here

The engine check needs no database and no Docker:

```
python -m pytest tests/benchmark/refed/ -q
```

To see it in the calculator interface instead, load it into a database (§7)
and use the admin dry-run page at `/admin/try`, selecting the factor set
`REFED-COMPARISON-2026-04-03 - NOT NZ DATA`, sector `refed_us`, food category
`refed_retail_produce`. The dry-run path persists nothing, which is the point:
tuning against a benchmark would otherwise pollute the public statistics.

## 6. What agreement to expect

Verified end to end, both from an in-memory bundle and from a real database
round trip:

| Metric | Current | Alternative | Net benefit | ReFED tab |
| --- | --- | --- | --- | --- |
| CO2e (MTCO2e) | 21.433806 | 9.657311 | 11.776495 | Total Emissions (CO2e) |
| Methane (MTCH4) | 0.166646 | 0.054191 | 0.112454 | Methane |
| Water (US gallons) | 440,625.98 | 242,344.29 | 198,281.69 | Water |
| Social cost of carbon (USD) | 6,410.92 | 3,041.43 | 3,369.49 | Social Cost of Carbon |

Our own results come out in our units and need dividing to reach ReFED's:
`co2e` and `ch4` totals are in kilograms, so divide by 1000; `water` is in
litres, so divide by 3.785411784; `cost` is already per-dollar.

**Expect agreement to about 1e-9 relative, and no better.** The limit is ours,
not ReFED's: `value_per_kg` is `DECIMAL(20,10)`, so each factor is rounded at
the tenth decimal place, which is worth up to `1e-10 × kg` in the metric total.
That is invisible for water (factors around 170 L/kg) and dominates for methane
(factors around 5e-3 kg/kg), where the observed disagreement is 1.5e-9
relative. The test derives its tolerance from that bound rather than choosing
one; if it ever has to be widened, our storage precision has changed.

### What can be compared, and what cannot

| ReFED metric | Comparable | Why |
| --- | --- | --- |
| Total Emissions (CO2e) | **Yes** | 100-year, `co2e` |
| Methane | **Yes** | mass of CH4, `ch4` |
| Water | **Yes** | `water`, gallons → litres |
| Social Cost of Carbon | **Yes, with a caveat** | see below |
| Meals Recovered | **No** | We have no meals metric, and metric rows are global — adding one would change what the New Zealand set reports. ReFED's figure for the alternative scenario above is 5,511.55 meals; nothing here produces it. |
| 20-year time horizon | **No** | Not published in the CSV. In ReFED's own dataset the 20-year CO2e is zero for 284 of 429 rows while the 100-year value is not, and their calculator never displays it — the horizon toggle only switches the methane read-out. Their methane mass is identical at 20 and 100 years in all 429 rows. `gwp_horizon` therefore has **no effect** on this factor set, and that is correct, not a bug. |

**The social cost of carbon caveat.** It is carried on our `cost` metric, whose
global `unit` reads `NZD`. The numbers are **US dollars**, and they are the
social cost of carbon only — not the market value of the wasted food, and not
a waste levy. The `FOOD_VALUE_PER_KG` constant is zero in this set for exactly
that reason. The displayed currency label will be wrong while this set is
selected. It is a label, not a number; every `cost` figure above is directly
comparable to ReFED's Social Cost of Carbon tab.

### What would make a disagreement ambiguous rather than diagnostic

Read this list first if the numbers do not match.

1. **Metric Tons entered on ReFED's site instead of Kg** — 0.02%, from ReFED's
   own three-significant-figure conversion. Rule this out before anything else.
2. **Reading the on-screen figure instead of Download Results** — the page
   rounds to two decimal places, and above 1,000,000 it switches to
   "n.nn million".
3. **ReFED republishing.** The committed CSV is dated *April 03 2026*. If
   ReFED updates it, the site will disagree with a fixture derived from the old
   file — and that is a data difference, not an engine difference. Re-download
   the CSV, re-run `build_refed_benchmark.py`, and check the diff before
   concluding anything about the engine.
4. **Picking a New Zealand code by mistake.** `refed_landfill` and `landfill`
   are different destinations with different factors. A run that mixes them
   produces a plausible number.
5. **A different factor set selected in the dry-run form.** The page defaults to
   the published set.
6. **Methane at the 20-year setting** — see above; it changes nothing here, so
   a difference that appears when you toggle it is coming from somewhere else.

## 7. Loading it into a database, and undoing that

```
DATABASE_URL=... python tests/benchmark/refed/load_refed_benchmark.py
```

Or, into a running stack, copying the directory in first because the images do
not carry it:

```
docker cp tests/benchmark/refed <container>:/app/refed
docker compose exec <admin service> python /app/refed/load_refed_benchmark.py
```

The loader adds the taxonomy rows and the factor set in **one transaction**:
they land together or neither lands. A half-applied load would leave food
categories with no factors behind them, which computes a plausible zero instead
of failing. It refuses if a set with this label already exists, and it never
touches an existing row.

**It leaves the set as a `draft` and does not publish it.** Nothing that is
live changes. Publishing would archive whatever is currently live, which is
what `publish_factor_set` is supposed to do and is not what you want here.

Three things to know before you load it into a database you care about:

- **The new taxonomy codes are global and will appear in the public
  dropdowns.** There is no per-factor-set taxonomy in this schema. Deactivating
  them afterwards hides them from `GET /api/v1/taxonomy` — but it also makes
  `POST /api/v1/calculate` reject them as `UNKNOWN_CODE`, so a deactivated
  fixture cannot be dry-run. Load it into a scratch database if the dropdowns
  matter, or run the pytest, which needs no database at all.
- **If you do publish it**, switch back from the admin panel: the factor set
  list has a **rollback** action, which is the archived-set route back. The
  New Zealand set will have been archived by the publish, and rollback is what
  restores it.
- **Deleting it** is a delete of the `factor_set` row from the admin panel;
  the factor rows cascade. The taxonomy rows do not — they are global and
  survive, which is why they are all prefixed `refed_`.

## 8. Files

| Path | What |
| --- | --- |
| `tests/benchmark/refed/impact_calculator_conversion_factors.csv` | ReFED's published factors, verbatim |
| `tests/benchmark/refed/refed_calculator_totals.json` | ReFED's own combined totals, the independent expected answer |
| `tests/benchmark/refed/build_refed_benchmark.py` | Derives the two JSON files from the CSV. The audit trail. |
| `tests/benchmark/refed/refed-benchmark-taxonomy.json` | 1 sector, 39 food categories, 12 destinations |
| `tests/benchmark/refed/refed-benchmark-factors.json` | The factor set: 764 upstream rows, 1728 downstream rows |
| `tests/benchmark/refed/load_refed_benchmark.py` | Loads both into a database in one transaction, as a draft |
| `tests/benchmark/refed/test_refed_benchmark.py` | The comparison, with no database |

They live in the repository rather than in a scratch directory because the
whole value of the exercise is that it can be re-run — after an engine change,
after a schema change, and by whoever picks this up next. A benchmark that
exists only in someone's temporary folder proves nothing a month from now.
