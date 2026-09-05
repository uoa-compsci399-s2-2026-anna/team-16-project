---
title: "Draft New Zealand upstream and downstream factors (O-1, first movement)"
date: "2026-09-05"
status: "DRAFT factor set. is_mock = true. Never published. Not the deliverable's live data."
---

# Draft New Zealand factors, derived from the client's Rawtec tables

## 1. What this is, and what it is not

Open item O-1 is this project's hard blocker: the client had not supplied
real emissions factors, so the calculator has run entirely on mock data.
This document and the factor set it describes are the **first movement** on
O-1, not its close. The set is loaded as a **draft** (`status = draft`,
`is_mock = true`) and must never be published: publishing would archive
whatever New Zealand set is currently live, and none of what follows has
been confirmed by the client. The repository owner is taking this draft to
a client meeting to ask the questions it raises; it is deliberately "concrete
enough to argue with" rather than a finished answer.

**Everything here traces to one of two places**: the client's own Rawtec
tables (transcribed, not re-measured), or ReFED's already-published,
already-committed benchmark fixture (`tests/benchmark/refed/
refed-benchmark-factors.json`), used only to split the client's totals across
supply-chain stages. Nothing is estimated from general knowledge. Every value
this document does not attribute to one of those two sources is either an
unweighted arithmetic mean of several client rows (stated explicitly, every
time) or an explicit, labelled zero.

**Where the files live:**

| File | What it holds |
| --- | --- |
| `data/upstream-factors-draft/rawtec_source_data.py` | The client's two tables, transcribed verbatim, nothing computed |
| `data/upstream-factors-draft/build_upstream_factors_draft.py` | The aggregation and the ReFED stage split; writes the JSON below |
| `data/upstream-factors-draft/upstream_factors_draft.json` | The factor set itself -- every row carries its own `source_note` |
| `data/upstream-factors-draft/load_upstream_factors_draft.py` | Loads the JSON into a database as a `draft` factor set. Never publishes. |

Run the loader against a database that already holds the New Zealand
taxonomy (`kaicalc-admin seed-taxonomy`), with `DATABASE_URL` set:

    python data/upstream-factors-draft/load_upstream_factors_draft.py

It refuses if a set with the same `version_label` already exists, and it
touches nothing else. Reach the loaded set from the admin panel's dry-run
page, `/admin/try`.

## 2. The client's two tables, and what changed in scope

The client supplied two tables (`rawtec.md`, itself a conversion of the
document the team received; the original is not committed -- see §6).

**Table 1**, "Impact values for EFC of foods (not including disposal)":
twenty food rows under seven client categories, each carrying kg CO2-eq/kg,
L water/kg and t/ha land, and a "Life cycle covered" column that is mostly
*Farm to Supermarket Shelf* and occasionally *Farm to Fork*. This table was
usable as supplied and is used as supplied.

**Table 2**, "Impact values of food from bin to destination": seventeen
destination rows, CO2-eq and L water/kg. **Its CO2-eq column is a verbatim
copy of table 1's CO2-eq column, in table-1 row order** -- not a measurement
about the destination at all. The repository owner's original brief to this
draft excluded table 2 entirely on that basis. **That was overruled on
2026-09-05, ahead of the client meeting**: the client's own figures are
themselves a draft, no better destination-side column exists yet, and the
owner will raise the defect with the client directly. Table 2 is used here,
CO2-eq column included, exactly as printed. Nothing is substituted, corrected
or dropped. §4 documents the correspondence in full and §7 is explicit that
this is a recorded, deliberate choice, not an oversight.

## 3. Table 1: New Zealand food categories

### 3.1 The mapping

| Our category | Client row(s) used | Aggregation |
| --- | --- | --- |
| `fruit` | Fruit (1.78 / 1360.00 / 11.81) | direct, one row |
| `vegetables` | Vegetable (1.82 / 510.71 / 59.40) | direct, one row |
| `nuts_seeds` | Nuts and seeds (2.65 / 13971.04 / 0.22) | direct, one row -- see §3.3 |
| `meat` | Red Meat, Pork, Poultry, Other meat | unweighted mean of 4 |
| `seafood` | Seafood (5.94 / 3603.00 / 1.19) | direct, one row |
| `dairy` | Cheese, Milk, Cream, Butter, Yoghurt, Other dairy | unweighted mean of 6 |
| `bakery_grains` | Bread, Bakery, Grains | unweighted mean of 3 |
| `beverages` | Drinks/Beverages excl. dairy (2.01 / 438.00 / 5.95) | direct, one row |
| `standard_mix` | General mixed food product (6.04 / 3,161 / 18) | direct, one row |
| `staples` | *(none)* | **not seeded -- see §3.2** |

**Why an unweighted mean.** The client supplied no production or waste
volumes for its sub-categories, so there is no weighting the mean could use
that would not itself be invented. An unweighted mean is the only aggregation
that adds no information the client did not supply. It is visible, not buried:
every aggregated row's `source_note` in `upstream_factors_draft.json` states
every constituent value and the mean computed from them, and
`rawtec_source_data.py` carries the same rows as data any reader can re-check
by hand.

**`meat`**: co2e mean of 20.28, 10.62, 3.98, 11.63 = **11.6275**; water mean
of 13971.04, 6925.00, 660.00, 7185.35 = **7185.3475**. Seafood is excluded --
the client lists it under "Meat & Seafood" but this system already carries
`seafood` as its own category, and folding it into `meat` would double-count
it nowhere while erasing the one figure (5.94, much lower than the other
four) that seafood actually needs.

**`dairy`**: co2e mean of 10.13, 1.51, 4.95, 11.39, 3.29, 1.19 = **5.41**;
water mean of 3968.00, 420.39, 3968.00, 3968.00, 3968.00, 420.39 =
**2785.4633333...** (repeating; stored at `DECIMAL(20,10)`).

**`bakery_grains`**: three client rows, not two categories folded blind --
Bread (1.46) and Bakery (4.28) are both under the client's own "Bakery"
heading, and Grains (2.56) is under "Grains, Nuts, Seeds". co2e mean =
**2.7666666...**; water mean of 1608.00, 1608.00, 2885.78 = **2033.9266666...**.

### 3.2 Gaps: Eggs and `staples`

The client's table 1 has a row for **Eggs** (4.93 / 1781.00 / 26.00,
*Farm to Supermarket Shelf*) that no New Zealand category matches -- this
system has no `eggs` category, and folding it into `dairy` (as the client's
own layout visually suggests, Eggs sitting directly under the Dairy block)
would silently change what `dairy`'s six rows mean without being asked to.
**Eggs is not seeded anywhere in this draft.** It is reported here as a gap
for the owner to raise with the client: either the taxonomy needs an `eggs`
category (a contract change, §2.1, not undertaken by this draft) or the
client's Eggs figure needs a ruling on which existing category it should join.

Conversely, this system's `staples` category has **no client row to draw
on** -- none of the twenty client rows describes anything resembling
"staples" as a category (nothing is expressed at a "cupboard base foods"
level distinct from Grains, Sauces/Spreads/Dips, Sweeteners or the mixed
aggregate). **`staples` is not seeded in this draft.** A calculator entry
naming `staples` against this factor set prices its upstream footprint at
zero rather than at an invented number -- correct behaviour for a genuine
gap, but one that should not be mistaken for "staples generates no
footprint".

### 3.3 A likely defect in the client's own table, reported and left alone

**"Nuts and seeds" carries water 13971.04 and land 0.22 -- identical to Red
Meat's row.** This is almost certainly a second copy-paste in the client's
source spreadsheet, of the same kind that produced table 2's CO2-eq column
(§4). It has **not** been corrected, adjusted, or excluded: the brief for
this draft is explicit that recording provenance matters more than deciding
which number is right, and the client's meeting is the right place to settle
it. `nuts_seeds`'s upstream water and (unused) land figures should be treated
as unverified until the client confirms or corrects them.

## 4. Table 2: New Zealand destinations

### 4.1 The CO2-eq column is a verbatim copy of table 1 -- the correspondence, in full

Every one of table 2's seventeen CO2-eq values equals a table 1 row's CO2-eq
value, in table-1's own row order:

| Table 2 destination | CO2-eq | = table 1 row |
| --- | --- | --- |
| Anaerobic Digestion | 1.46 | Bread |
| BioBased | 4.28 | Bakery |
| Compost | 10.13 | Cheese |
| Incineration | 1.51 | Milk |
| Landfill | 4.95 | Cream |
| Landspread | 11.39 | Butter |
| Not Harvested | 3.29 | Yoghurt |
| Other Food Waste | 1.19 | Other dairy |
| Refuse | 4.93 | Eggs |
| Sewer/Wastewater Treatment | 2.01 | Drinks/Beverages (excl. dairy) |
| Unknown Food Waste | 1.78 | Fruit |
| Charity Redistribution | 1.82 | Vegetable |
| Commercial Redistribution | 20.28 | Red Meat |
| Stock Feed | 10.62 | Pork |
| Upcycled | 3.98 | Poultry |
| Unknown Repurposed | 11.63 | Other meat |
| Pet Food | 5.94 | Seafood |

`build_upstream_factors_draft.py` asserts this correspondence mechanically
against the transcribed data (`TABLE2_CO2_COPY_SOURCE`) before it will write
a file, so this table cannot silently drift from the data behind it.

**This column is used anyway, exactly as printed, on the repository owner's
explicit instruction of 2026-09-05**, ahead of the client meeting: the
client's own figures are themselves a draft, no better destination-side CO2
column exists yet, and the owner intends to raise the defect with the client
directly rather than have this draft quietly paper over it. Every downstream
`co2e` row's `source_note` in `upstream_factors_draft.json` repeats this
caution in full. **Anyone reading the loaded factor set must be able to see
that this is a known, recorded choice and not an oversight** -- that
sentence is the whole reason this section exists.

**The water column is not implicated in that defect and looks genuine by
contrast**: negative offsets (water returned to use, not consumed) for
Anaerobic Digestion (-1.50), BioBased (-2.20), Compost (-2.40), Landspread
(-2.40), Not Harvested (-1.20), Stock Feed (-1.95), Upcycled (-2.80), Pet
Food (-1.95) and Unknown Repurposed (-1.95); a small positive value (+0.06)
for every disposal-and-redistribution row that is not one of those; an exact
zero for Sewer; and a published `null` for Incineration, which this draft
therefore does not seed a water value for at all (§4.3). Seventeen distinct
values, not one column repeated, is the basis for treating this column
differently from CO2-eq above -- it has not been independently verified
either, only found internally consistent with a real bin-to-destination
model. `factor_downstream.value_per_kg` already permits negative values by
design (§4.2 of `docs/architecture.md`): downstream figures are offsets, not
only costs, and a chart or a table that clamped one at zero would misrepresent
it.

### 4.2 The mapping onto our fourteen destinations

| Our destination | Client row(s) used | Aggregation |
| --- | --- | --- |
| `anaerobic_digestion` | Anaerobic Digestion | direct |
| `bioprocessing` | BioBased | direct |
| `compost` | Compost | direct |
| `combustion` | Incineration | direct (co2e only -- water is `null`, §4.3) |
| `landfill` | Landfill | direct |
| `land_application` | Landspread | direct |
| `not_harvested` | Not Harvested | direct |
| `refuse_discard` | Other Food Waste, Refuse, Unknown Food Waste | unweighted mean of 3 |
| `sewer` | Sewer/Wastewater Treatment | direct |
| `food_redistribution` | Charity Redistribution, Commercial Redistribution | unweighted mean of 2 |
| `animal_feed` | Stock Feed, Pet Food, Unknown Repurposed | unweighted mean of 3 |
| `upcycling` | Upcycled | direct |
| `other_recovery` | *(none)* | **not seeded -- see below** |
| `prevention` | *(none, by design)* | **zero override, not a client figure -- see below** |

**Naming and life-cycle reasoning for the three grouped destinations:**

- **`refuse_discard`** takes "Refuse" (name match), "Other Food Waste" and
  "Unknown Food Waste" (both published as *Bin to Landfill*, the same
  disposal life cycle as "Refuse" and "Landfill" themselves). "Landfill" is
  kept separate and maps 1:1 to our `landfill`, because it is the one row
  literally named that. co2e mean of 1.19, 4.93, 1.78 = **2.6333333...**;
  water mean of 0.06, 0.06, 0.06 = **0.06**.
- **`food_redistribution`** takes "Charity Redistribution" and "Commercial
  Redistribution" -- this system has one redistribution destination where
  the client has two. co2e mean of 1.82, 20.28 = **11.05**; water mean of
  0.06, 0.06 = **0.06**.
- **`animal_feed`** takes "Stock Feed", "Pet Food" (both *Bin to Farm*) and
  "Unknown Repurposed" (also *Bin to Farm* -- the shared life-cycle
  description is the basis for including it here rather than leaving it
  unmapped; this is a judgement call, stated as one). co2e mean of 10.62,
  5.94, 11.63 = **9.3966666...**; water mean of -1.95, -1.95, -1.95 =
  **-1.95**.

**Two of our destinations receive no client row, deliberately:**

- **`other_recovery`** ("Other recovery, including biodiesel") has no
  comparably-shaped client destination. The client's "Other Food Waste" is a
  *disposal* row (*Bin to Landfill*) and must not be folded in here merely
  because the names read alike -- that was the owner's explicit instruction,
  and §4's group for `other_recovery` is `recycle_recovery`, not `disposal`.
  Left unseeded rather than mapped to a row that means something different.
- **`prevention`** is the mandatory 100% offset (§O-7 of
  `docs/architecture.md`): the two scenarios must conserve mass, and giving
  `prevention` any nonzero factor would silently inflate every net-benefit
  figure the calculator reports. It receives an explicit zero override for
  both `co2e` and `water`, on both the upstream and downstream side, sourced
  as `"definitional"` rather than from the client's table -- not an absence,
  a stated zero.

### 4.3 Incineration's water value

The client's own table prints `null` for Incineration's water column, unlike
every other destination. This draft does not write a water row for
`combustion` at all -- not a zero (a zero would say "no water impact",
which is not what the client's table says) and not an invented figure. A
calculator entry naming `combustion` against this factor set prices its
downstream water at zero only because no row exists to look up (§4.1 of
`docs/architecture.md`'s three-step fallback), which is observably different
from a stated zero and is recorded here so the difference is not lost.

## 5. The stage split: applying ReFED's proportions to the client's totals

### 5.1 What the client's own instruction asks for

The client's method note (their own words, `data-sources.md` in the working
notes, not committed) says plainly that the supply-chain-stage breakdown
does **not exist** in their data, and directs the team to:

> "figure out the proportions of CO2e/water etc. from each supply chain
> stage that ReFED use, and apply this to the EFC with the Rawtec farm to
> fork data to get EFC calculations by supply chain stage."

### 5.2 What this repository already holds

`tests/benchmark/refed/build_refed_benchmark.py` derives a factor set from
ReFED's own published "Download factors" CSV
(`impact_calculator_conversion_factors.csv`, committed beside it) and writes
`refed-benchmark-factors.json`. For each of ReFED's nine food categories, it
carries one generic (`destination = null`) upstream value **per ReFED
sector** -- Farm, Manufacturing, Retail, Foodservice, Residential -- in
`co2e` (kg CO2e/kg) and `water` (L/kg). This is exactly "the proportions ...
from each supply chain stage that ReFED use": nothing further needed
fetching, and nothing was.

**Important, and stated rather than smoothed over: these five values are
not a monotonic build-up along the supply chain.** For ReFED's own "Standard
Mix", the published water figure is *lower* at Residential (806.92 L/kg)
than at Retail (1275.66) or Foodservice (1914.85). That is an aggregate
across many food types wasted in different mixes at each stage -- not one
food's cumulative footprint gaining a processing step at a time -- and
reading it as a cumulative build-up (stage N's value minus stage N-1's) would
manufacture a curve the data does not support. This draft therefore reads
the five values as **shares of a whole**: each ReFED sector's value is
normalised against the sum of the (up to five) values ReFED publishes for
that food category, giving a proportion per stage that sums to 1 by
construction. Multiplying those proportions against the client's own total
means the resulting New Zealand stage values **sum back to the client's own
total exactly** -- a property stated so a reader can check it by hand (§5.4).

**Five of ReFED's nine food categories have no published Farm-stage value at
all**: Breads & Bakery, Dairy & Eggs, Fresh Meat & Seafood, Frozen, Prepared
Foods and Ready-To-Drink Beverages carry no Farm row (only Dry Goods, Produce
and Standard Mix do -- ReFED's own choice, stated in
`build_refed_benchmark.py`'s docstring). Where that is true, this draft gives
`primary_production` a proportion of **zero** for the matching New Zealand
category, rather than inventing a Farm-stage share ReFED itself does not
publish. This is why `meat`, `seafood`, `dairy`, `bakery_grains` and
`beverages` all carry `primary_production = 0.0000000000` for both metrics
below (§5.4) -- it is ReFED's absence, carried through, not a New Zealand
claim that no meat, dairy, bakery or beverage waste happens on-farm.

### 5.3 Which ReFED category supplies which New Zealand category's shares

This governs the **proportions only** -- the totals multiplied through
remain the client's own, from §3.

| New Zealand category | ReFED category whose shares are used |
| --- | --- |
| `fruit`, `vegetables` | ReFED Produce (both draw the same shape; ReFED does not split fruit from vegetables either) |
| `nuts_seeds` | ReFED Dry Goods (closest available; ReFED has no nuts/seeds category) |
| `meat`, `seafood` | ReFED Fresh Meat & Seafood (both draw the same shape; ReFED does not split meat from seafood either) |
| `dairy` | ReFED Dairy & Eggs |
| `bakery_grains` | ReFED Breads & Bakery |
| `beverages` | ReFED Ready-To-Drink Beverages |
| `standard_mix` | ReFED Standard Mix |

### 5.4 The six sectors, and the one place our taxonomy is finer than ReFED's

ReFED publishes five supply-chain sectors; this system seeds six
(`admin/seed.py` `SECTORS`). Five map straightforwardly:

| Our sector | ReFED sector |
| --- | --- |
| `primary_production` | Farm |
| `processing` | Manufacturing |
| `wholesale_retail` | Retail |
| `consumer_household` | Residential |
| `consumer_hospitality` | Foodservice |

**`consumer_institution` has no ReFED equivalent at all.** ReFED does not
separate institutional food service (schools, hospitals, cafeterias) from
restaurant/hospitality food service; this system does. In the absence of a
fourth way to split it, ReFED's Foodservice share is divided **evenly
(50 / 50)** between `consumer_hospitality` and `consumer_institution`, rather
than handed to both in full (which would double-count it and break the
"sums back to the client's total" property this section relies on). This is
a stated, flat assumption exactly where our taxonomy is finer than ReFED's --
not a claim that hospitality and institutional food waste actually carry
identical footprints. It is exactly the kind of judgement the brief asked to
be made and written down rather than agonised over.

### 5.5 The result, and the check a reader can perform by hand

For every New Zealand food category and metric, the six sector values below
sum to the client's own total from §3 (to `DECIMAL(20,10)`). Values in
kg CO2e/kg and L/kg respectively.

| Category | primary_production | processing | wholesale_retail | consumer_household | consumer_hospitality | consumer_institution | Σ (= client total) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `fruit` co2e | 0.0488256944 | 0.2540530311 | 0.4030371575 | 0.5546013432 | 0.2597413869 | 0.2597413869 | 1.78 |
| `fruit` water | 213.3075174617 | 304.5480974564 | 268.7981438127 | 268.7981438127 | 152.2740487282 | 152.2740487282 | 1360.00 |
| `vegetables` co2e | 0.0499229010 | 0.2597620880 | 0.4120941723 | 0.5670642947 | 0.2655782720 | 0.2655782720 | 1.82 |
| `vegetables` water | 80.1016781198 | 114.3645285676 | 100.9396323725 | 100.9396323725 | 57.1822642838 | 57.1822642838 | 510.71 |
| `nuts_seeds` co2e | 0.6630310047 | 0.3624229556 | 0.5098735369 | 0.6050991100 | 0.2547866964 | 0.2547866964 | 2.65 |
| `nuts_seeds` water | 7317.5067371698 | 1629.8615675327 | 1696.9050638824 | 1696.9050638824 | 814.9307837664 | 814.9307837664 | 13971.04 |
| `meat` co2e | 0.0000000000 | 2.4968514695 | 2.7391230548 | 3.0391974909 | 1.6761639924 | 1.6761639924 | 11.6275 |
| `meat` water | 0.0000000000 | 1681.3720818893 | 1920.9606043199 | 1901.6427319016 | 840.6860409446 | 840.6860409446 | 7185.3475 |
| `seafood` co2e | 0.0000000000 | 1.2755362484 | 1.3993025969 | 1.5525979872 | 0.8562815838 | 0.8562815838 | 5.94 |
| `seafood` water | 0.0000000000 | 843.1023845467 | 963.2409646666 | 953.5542662399 | 421.5511922734 | 421.5511922734 | 3603.00 |
| `dairy` co2e | 0.0000000000 | 0.9756488035 | 1.3036089875 | 1.5135309994 | 0.8086056048 | 0.8086056048 | 5.41 |
| `dairy` water | 0.0000000000 | 733.5645808107 | 659.6862497100 | 658.6479220019 | 366.7822904053 | 366.7822904053 | 2785.4633333... |
| `bakery_grains` co2e | 0.0000000000 | 0.6591159848 | 0.6678075608 | 0.8481227257 | 0.2958101977 | 0.2958101977 | 2.7666666... |
| `bakery_grains` water | 0.0000000000 | 508.4816666667 | 508.4816666667 | 508.4816666667 | 254.2408333333 | 254.2408333333 | 2033.9266666... |
| `beverages` co2e | 0.0000000000 | 0.2184311905 | 0.3228576099 | 0.4676203859 | 0.5005454068 | 0.5005454068 | 2.01 |
| `beverages` water | 0.0000000000 | 135.9526458265 | 83.0473541735 | 83.0473541735 | 67.9763229132 | 67.9763229132 | 438.00 |
| `standard_mix` co2e | 0.0942399264 | 1.1968403562 | 1.5263529306 | 1.4479545342 | 0.8873061263 | 0.8873061263 | 6.04 |
| `standard_mix` water | 90.1516006552 | 731.7181992623 | 746.4617222820 | 472.1771135271 | 560.2456821366 | 560.2456821366 | 3161 |

(Rows sum to the client's total to the last written digit; the two
`bakery_grains` and `dairy` water totals carry a repeating decimal that is
truncated in this table's last column for display only -- the JSON stores
the full `DECIMAL(20,10)` value and the six-way sum against *that* value is
exact.)

## 6. What is not represented, and why

- **`land`** -- the client's table 1 gives t/ha for every food row. This
  system has no `land` metric to receive it (`admin/seed.py` `METRICS`:
  `co2e`, `ch4`, `water`, `cost`, `mass`). Adding one is a metric-table
  change with system-wide effect (every existing result, on every factor
  set, would gain a `land` line at zero the moment the row exists -- §4.1 of
  `docs/architecture.md`, "metrics are data, not code" and "the engine
  iterates every active row"). That decision belongs to the owner, and is
  not taken here.
- **`ch4`** -- the client's CO2-eq figures are already carbon-dioxide
  equivalents, not decomposed into a separate methane mass. No `ch4` row is
  written, upstream or downstream, matching how the shipped mock set already
  handles it.
- **`cost`** -- neither table carries a cost column. The NZ landfill levy
  ($75/tonne from July 2027, per the team's working notes) is a real,
  distinct figure this task does not seed; it belongs to a future pass, not
  folded in here as a guess.
- **Sector variation in the downstream figures** -- `factor_downstream.
  sector_id` exists (contract v1.31) so that a set can price a destination
  differently by supply-chain stage. The client's table 2 gives one CO2-eq
  and one water figure per destination, with no stage breakdown, so every
  downstream row in this draft carries `sector = null` ("applies to every
  sector"), matching how the New Zealand mock set's own downstream rows are
  already built.

## 7. The traxie data directory

Nothing under `traxie data/` (the client's original files: spreadsheets,
zips, images, the brand pack) is committed anywhere in this repository --
that directory is gitignored and stays that way. Every number in this
document and in `upstream_factors_draft.json` was retyped by hand from a
Markdown conversion of the client's tables into the two committed Python
files under `data/upstream-factors-draft/`, which is the only form any of it
takes in version control.
