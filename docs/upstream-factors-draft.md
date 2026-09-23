---
title: "Draft New Zealand upstream and downstream factors (O-1, first movement)"
date: "2026-09-05"
revised: "2026-09-21 — the client's corrected table 2 CO2-eq column (§4.1)"
status: "DRAFT factor set. is_mock = true. O-1 still open. Not the deliverable's confirmed data."
---

# Draft New Zealand factors, derived from the client's Rawtec tables

## 1. What this is, and what it is not

Open item O-1 is this project's hard blocker: the client had not supplied
real emissions factors, so the calculator has run entirely on mock data.
This document and the factor set it describes are the **first movement** on
O-1, not its close. The set is built as a **draft** (`status = draft`,
`is_mock = true`), and `is_mock` stays true wherever it goes: none of what
follows has been confirmed by the client, so the placeholder banner keeps
showing. The repository owner is taking this draft to a client meeting to ask
the questions it raises; it is deliberately "concrete enough to argue with"
rather than a finished answer.

**It was published on 2026-09-06**, on the local development stack and on the
10.0.0.130 deployment, on the repository owner's explicit instruction — the
sentence here previously said it never should be, which was written the day
before that decision. Publishing archives the set it replaces rather than
deleting it (`MOCK-v0` on 130, the ReFED benchmark set locally), so rollback
remains available, and every submission stamps its own `factor_set_id`, so
results computed before the change still reproduce exactly. What publishing
changed is the *form*: `get_taxonomy` narrows the offered vocabulary to what
the published set prices, so where `MOCK-v0` priced 6 destinations of 14 and
3 sectors of 6, this set prices all of them, and the silent zeros that
narrowing existed to hide are gone.

**The client corrected table 2's CO2-eq column on 2026-09-21, and this
document describes the corrected data.** The column the rest of this
document used to defend — a verbatim copy of table 1's CO2-eq column, food
figures sitting in destination rows — has been withdrawn and replaced by the
client. §4.1 holds the whole record: what was wrong, what the owner ruled on
2026-09-05, what the client sent on 2026-09-21, and what it changes. The
record is kept rather than deleted, because a reader who finds only the
corrected column cannot otherwise tell a defect that was found and fixed from
one nobody ever noticed.

**What it does not change: O-1 is still open.** These remain the client's own
draft figures. `is_mock` stays `true`, the placeholder banner stays
mandatory, and the set built from the corrected column is a **draft** that
this revision does not publish.

**Everything here traces to one of four places**: the client's own Rawtec
tables (transcribed, not re-measured); ReFED's already-published,
already-committed benchmark fixture (`tests/benchmark/refed/
refed-benchmark-factors.json`), used to shape the client's totals across
supply-chain stages, as a value in its own right for several destinations,
and, as of this revision, as the sole source for `ch4` everywhere it is
seeded (§4.4, §5.7); Poore & Nemecek (2018) as republished by Our World in
Data, used only to fill the one gap ReFED's own data leaves open for `co2e`
(the Farm stage for five food categories -- §5.3); or, as of this revision,
the New Zealand government's own published waste disposal levy schedule
(Ministry for the Environment, read 2026-09-05), the sole source for `cost`
(§4.5). Nothing is estimated from general knowledge. Every value this
document does not attribute to one of those four sources is either an
unweighted arithmetic mean of several client or ReFED rows (stated
explicitly, every time), an explicit, labelled zero with a stated reason, or
a stated assumption labelled as one (§5.3's water share).

**A note for anyone who read the previous version of this document.** The
first draft (commit `ba7fe93`) built the six-sector split as *shares of a
whole*: it normalised ReFED's five per-sector values to sum to 1 and
multiplied them against the client's total, so the resulting six New Zealand
values summed back to the client's own figure exactly. That was the wrong
shape for what `factor_upstream` means (§5.1 explains why in full) and has
been replaced. If you are looking for the numbers that version produced --
dairy co2e of 0.0000 / 0.9756 / 1.3036 / 1.5135 / 0.8086 / 0.8086, summing to
5.41 -- they are gone; §5 below is a full rewrite, not a patch.

**Where the files live:**

| File | What it holds |
| --- | --- |
| `data/upstream-factors-draft/rawtec_source_data.py` | The client's two tables, transcribed verbatim, nothing computed |
| `data/upstream-factors-draft/public_farm_share_source_data.py` | Poore & Nemecek (2018) product rows, transcribed verbatim, used only to fill the Farm stage where ReFED publishes none |
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
document the team received; the original is not committed -- see §8).

**Table 1**, "Impact values for EFC of foods (not including disposal)":
twenty food rows under seven client categories, each carrying kg CO2-eq/kg,
L water/kg and t/ha land, and a "Life cycle covered" column that is mostly
*Farm to Supermarket Shelf* and occasionally *Farm to Fork*. This table was
usable as supplied and is used as supplied -- and, since this revision, that
column does real work: it decides which of our sectors is the *anchor* each
food category's total is pinned to (§5.2).

**Table 2**, "Impact values of food from bin to destination": seventeen
destination rows, CO2-eq and L water/kg. **Its CO2-eq column was corrupt in
the client's 2026-09-05 document and the client replaced it on 2026-09-21.**
In the first revision it was a verbatim copy of table 1's CO2-eq column, in
table-1 row order -- not a measurement about the destination at all. The
repository owner's original brief to this draft excluded table 2 entirely on
that basis; that was overruled on 2026-09-05, ahead of the client meeting,
and the column was used exactly as printed because no better one existed. The
client's revised document supplies a real destination-side column, and this
draft now builds from it. §4.1 documents both columns in full, and §8 is
explicit about how the client's own files reach version control.

The 2026-09-21 document was diffed against the committed transcription cell
by cell before anything was changed: **table 1 showed zero differences**
across all 26 rows and all six columns, and so did table 2's destination
labels, "Life cycle covered" column and water column. All seventeen CO2-eq
values moved. That measurement is re-run on every build by
`_assert_only_table2_co2_moved()` (§6) rather than resting on this
paragraph.

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
| `staples` | *(none -- filled from ReFED alone, §5.4)* | not a client aggregation |

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
four) that seafood actually needs. These are the client's own **farm-to-
shelf totals** -- the number every sector's stage value in §5 is ultimately
built from or checked against, not a stage value in themselves.

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
category (a contract change, §2.1, not undertaken by this draft -- if the
client's word settles it, the figures would land as a seventh
`factor_upstream` food category built the same way every other one in §5 is)
or the client's Eggs figure needs a ruling on which existing category it
should join.

Conversely, this system's `staples` category has **no client row to draw
on** -- none of the twenty client rows describes anything resembling
"staples" as a category. Unlike the previous revision of this draft,
**`staples` is not left unseeded**: the owner's ruling is that nothing is
left at zero merely because a source is silent. It is filled entirely from
ReFED's own "Dry Goods" figures (the closest available ReFED category to a
pantry-staples grouping, and already the shape `nuts_seeds` draws on), used
directly with **no client total to scale against** -- there is nothing to
anchor to. §5.4 has the construction and the one place ReFED's own Dry Goods
data needed a clamp to stay non-decreasing.

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

### 4.1 The CO2-eq column: a defect, a ruling, and the client's correction

This section is kept in full after the defect was fixed, not trimmed down to
the answer. A reader who finds only the corrected column has no way to tell a
defect that was found and fixed from one nobody ever noticed.

#### What was wrong (the client's 2026-09-05 document)

Every one of table 2's seventeen CO2-eq values equalled a table 1 row's
CO2-eq value, in table-1's own row order -- food figures sitting in
destination rows:

| Table 2 destination | withdrawn CO2-eq | = table 1 row | corrected CO2-eq |
| --- | ---: | --- | ---: |
| Anaerobic Digestion | 1.46 | Bread | **-0.04** |
| BioBased | 4.28 | Bakery | **-0.56** |
| Compost | 10.13 | Cheese | **-0.11** |
| Incineration | 1.51 | Milk | **-0.12** |
| Landfill | 4.95 | Cream | **0.60** |
| Landspread | 11.39 | Butter | **0.00** |
| Not Harvested | 3.29 | Yoghurt | **0.00** |
| Other Food Waste | 1.19 | Other dairy | **0.60** |
| Refuse | 4.93 | Eggs | **0.60** |
| Sewer/Wastewater Treatment | 2.01 | Drinks/Beverages (excl. dairy) | **0.00** |
| Unknown Food Waste | 1.78 | Fruit | **0.60** |
| Charity Redistribution | 1.82 | Vegetable | **0.04** |
| Commercial Redistribution | 20.28 | Red Meat | **0.04** |
| Stock Feed | 10.62 | Pork | **-0.19** |
| Upcycled | 3.98 | Poultry | **-0.15** |
| Unknown Repurposed | 11.63 | Other meat | **-0.19** |
| Pet Food | 5.94 | Seafood | **-0.19** |

Read as destination factors, the withdrawn column inverted this tool's
central message. Compost was priced at 10.13 kg CO2-eq/kg against Landfill's
4.95, so composting came out twice as bad as landfilling, and every recovery
route was a cost rather than a saving.

#### What was ruled (2026-09-05)

**The column was used anyway, exactly as printed, on the repository owner's
explicit instruction of 2026-09-05**, ahead of the client meeting: the
client's own figures were themselves a draft, no better destination-side CO2
column existed, and the owner intended to raise the defect with the client
directly rather than have this draft quietly paper over it. Every downstream
`co2e` row's `source_note` carried the caution in full, and
`build_upstream_factors_draft.py` asserted the copy-paste correspondence
mechanically (`TABLE2_CO2_COPY_SOURCE`) so this document could not describe a
defect the data no longer had.

That set **was published**, on 2026-09-06, on the local development stack and
on the 10.0.0.130 deployment (§1). So the inverted column was live, behind the
placeholder banner, for the fortnight between the two revisions. That is why
this correction is not tidy-up.

#### What the client sent (2026-09-21)

The revised *Rawtec calculations* document replaces exactly this column and
nothing else. The corrected values read as a proper bin-to-destination
balance: small numbers, several negative (an avoided-burden credit), 0.60 for
every landfill-bound row, a flat 0.00 for the two on-farm breakdown rows and
for sewer, and 0.04 for the two redistribution rows. Compost at -0.11 against
Landfill's 0.60 now prices composting as a saving.

`TABLE2_CO2_COPY_SOURCE` and the check that asserted the copy are **gone**:
the copy they assert no longer exists, so that check would fail on correct
data. What replaces it keeps the purpose rather than the letter --
`_assert_only_table2_co2_moved()` re-runs the cell-by-cell measurement the
correction was made on (table 1 unchanged, table 2's labels, life cycle and
water unchanged) against a frozen copy of the 2026-09-05 transcription, and
refuses to write a factor set if anything else has drifted or if any
destination still carries a withdrawn value. `tests/test_upstream_factors_
draft_build.py` exercises it.

**What it does not settle: O-1 stays open.** These are still the client's
draft figures, `is_mock` stays `true`, and the set built from them is a draft
this revision does not publish.

#### What it changes on a real calculation

Measured, not asserted: one submission run end to end through
`build_bundle_data` and `engine.calculate` against a real load of each JSON
into MySQL. **1,000 kg of mixed food waste at `consumer_household`, current
all to `landfill`, alternative all to `compost`**, `gwp_horizon = 100`:

| | withdrawn column | corrected column |
| --- | ---: | ---: |
| `landfill` downstream `co2e` | 4.95 | 0.60 |
| `compost` downstream `co2e` | 10.13 | -0.11 |
| current total (landfill) | 10,990.0 kg CO2e | 6,640.0 kg CO2e |
| alternative total (compost) | 16,170.0 kg CO2e | 5,930.0 kg CO2e |
| **net benefit** | **-5,180.0 kg CO2e** | **+710.0 kg CO2e** |
| headline equivalence, current | "running 5 passenger vehicles for a year" | "running 3" |

The withdrawn column said composting a tonne of mixed food waste was 5,180 kg
CO2e **worse** than landfilling it. The corrected one says it is 710 kg CO2e
better. `water`, `ch4`, `cost` and `mass` are identical in both runs, as the
cell-by-cell diff predicted: the only rows that moved are twelve downstream
`co2e` values (the twelve destinations with a client mapping;
`other_recovery` is ReFED-filled and `prevention` is a definitional zero).

**The water column was never implicated in that defect** -- it is identical,
cell for cell, in both revisions of the client's document -- **and looks
genuine by contrast**: negative offsets (water returned to use, not consumed) for
Anaerobic Digestion (-1.50), BioBased (-2.20), Compost (-2.40), Landspread
(-2.40), Not Harvested (-1.20), Stock Feed (-1.95), Upcycled (-2.80), Pet
Food (-1.95) and Unknown Repurposed (-1.95); a small positive value (+0.06)
for every disposal-and-redistribution row that is not one of those; an exact
zero for Sewer; and a published `null` for Incineration, which the client's
own table does not measure (§4.3 fills it from elsewhere). Seventeen distinct
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
| `combustion` | Incineration | direct co2e; water filled from ReFED, §4.3 |
| `landfill` | Landfill | direct |
| `land_application` | Landspread | direct |
| `not_harvested` | Not Harvested | direct |
| `refuse_discard` | Other Food Waste, Refuse, Unknown Food Waste | unweighted mean of 3 |
| `sewer` | Sewer/Wastewater Treatment | direct |
| `food_redistribution` | Charity Redistribution, Commercial Redistribution | unweighted mean of 2 |
| `animal_feed` | Stock Feed, Pet Food, Unknown Repurposed | unweighted mean of 3 |
| `upcycling` | Upcycled | direct |
| `other_recovery` | *(none -- filled from ReFED, below)* | not a client aggregation |
| `prevention` | *(none, by design)* | **zero override, not a client figure -- see below** |

**Naming and life-cycle reasoning for the three grouped destinations:**

- **`refuse_discard`** takes "Refuse" (name match), "Other Food Waste" and
  "Unknown Food Waste" (both published as *Bin to Landfill*, the same
  disposal life cycle as "Refuse" and "Landfill" themselves). "Landfill" is
  kept separate and maps 1:1 to our `landfill`, because it is the one row
  literally named that. co2e mean of 0.60, 0.60, 0.60 = **0.60**; water mean
  of 0.06, 0.06, 0.06 = **0.06**. (Under the withdrawn 2026-09-05 column the
  co2e mean was 1.19, 4.93, 1.78 = 2.6333333...)
- **`food_redistribution`** takes "Charity Redistribution" and "Commercial
  Redistribution" -- this system has one redistribution destination where
  the client has two. co2e mean of 0.04, 0.04 = **0.04**; water mean of
  0.06, 0.06 = **0.06**. (Under the withdrawn column: 1.82, 20.28 = 11.05 --
  the single largest distortion the correction removes, since it priced
  giving food away at more than eleven kilograms of CO2e per kilogram.)
- **`animal_feed`** takes "Stock Feed", "Pet Food" (both *Bin to Farm*) and
  "Unknown Repurposed" (also *Bin to Farm* -- the shared life-cycle
  description is the basis for including it here rather than leaving it
  unmapped; this is a judgement call, stated as one). co2e mean of -0.19,
  -0.19, -0.19 = **-0.19**; water mean of -1.95, -1.95, -1.95 = **-1.95**.
  (Under the withdrawn column: 10.62, 5.94, 11.63 = 9.3966666...)

**`other_recovery`** ("Other recovery, including biodiesel") has no
comparably-shaped client destination. The client's "Other Food Waste" is a
*disposal* row (*Bin to Landfill*) and must not be folded in here merely
because the names read alike -- that was the owner's explicit instruction,
and this destination's group is `recycle_recovery`, not `disposal`. **Unlike
the previous revision of this draft, it is not left unseeded.** ReFED
publishes an "Industrial Uses" destination in the same recycle_recovery-
equivalent group (`build_refed_benchmark.py`'s own `DESTINATIONS` table), so
the match is by *shape*, not by a similar-sounding name. ReFED publishes it
per (sector, food category); this draft's own downstream rows carry no such
breakdown (matching every other destination here), so `co2e` is an
unweighted mean across all 39 published (sector, food category) rows:
**-0.0331166624** (min -0.3177516707, max 0.0000000000 -- a small net
*offset*, consistent with biodiesel/rendering displacing an emission
elsewhere). ReFED's own water figure for "Industrial Uses" is **exactly
zero** in every one of those 39 rows, so `water` is **0.0000000000**, taken
directly rather than averaged (there is nothing to average: every value
already agrees).

**`prevention`** is the mandatory 100% offset (§O-7 of
`docs/architecture.md`): the two scenarios must conserve mass, and giving
`prevention` any nonzero factor would silently inflate every net-benefit
figure the calculator reports. It receives an explicit zero override for
both `co2e` and `water`, on both the upstream and downstream side, sourced
as `"definitional"` rather than from the client's table -- not an absence,
a stated zero.

### 4.3 Incineration's water value

The client's own table prints `null` for Incineration's water column, unlike
every other destination. **Unlike the previous revision of this draft,
this is not left an absence.** ReFED publishes an explicit water figure for
its own "Incineration" destination, and it is **exactly zero** across all 39
published (sector, food category) rows in `refed-benchmark-factors.json` --
ReFED's own model assigns waste-to-energy combustion no process water at
all. That figure is adopted directly for `combustion`'s downstream water:
**0.0000000000**, `data_quality = "derived-refed"`. This is a *sourced*
zero, recorded as one, and is meant to read differently in the factor set's
own audit trail from a lookup that simply found no row -- even though, at
the point the engine reads it, the two are indistinguishable (§4.1 of
`docs/architecture.md`'s three-step fallback), which is exactly why this
section, and the row's own `source_note`, exist.

### 4.4 `ch4` downstream: matched to ReFED by shape, not by client row

Neither client table carries a methane column at all, so `ch4` cannot be
built the way §4.2 builds `co2e`/`water` -- there is no client destination
row to draw a mapping from. Every one of our fourteen destinations is
instead matched directly onto the ReFED destination whose shape it is
closest to (`REFED_DESTINATION_FOR_NZ_DESTINATION` in
`build_upstream_factors_draft.py`), the identical reasoning §4.2 already
uses for `other_recovery`'s co2e/water fill, extended to cover every
destination: an unweighted mean across every published (sector, food
category) row ReFED carries for that destination.

| Our destination | ReFED destination | `ch4` |
| --- | --- | --- |
| `food_redistribution` | Donations | 0.0000001343 |
| `animal_feed` | Animal Feed | -0.0004394564 |
| `compost` | Composting | 0.0024507901 |
| `anaerobic_digestion` | Anaerobic Digestion | 0.0042779307 |
| `land_application` | Land Application | -0.0000287599 |
| `not_harvested` | Not Harvested | 0.0000000000 |
| `bioprocessing` | Industrial Uses | -0.0009280499 |
| `other_recovery` | Industrial Uses | -0.0009280499 |
| `combustion` | Incineration | 0.0000655859 |
| `landfill` | Landfill | 0.0320905540 |
| `refuse_discard` | Dumping | -0.0000287599 |
| `sewer` | Sewer | 0.1332824899 |
| `upcycling` | *(no match -- STAND-IN, see below)* | -0.0002196610 |
| `prevention` | *(none -- zero override)* | 0.0000000000 |

**`bioprocessing` and `other_recovery` deliberately share one ReFED figure**
(Industrial Uses): both are the nearest recycle_recovery-shaped ReFED
destination to either of ours, the same reasoning §4.2 already applies to
`other_recovery`'s co2e/water fill.

**`upcycling` has no matching ReFED destination, and is filled with an
explicit stand-in rather than left a silent zero.** Two tests were applied,
both negative: by name, ReFED publishes no "Upcycling" or "Repurposed"
destination at all; by our own destination-group classification
(`admin/seed.py` puts `upcycling` in the `reuse` group), ReFED's own
`reuse`-equivalent destinations are only Donations and Animal Feed.
"Industrial Uses" (already used twice above) was considered and rejected: it
sits in ReFED's own `recycle_recovery`-equivalent group, not `reuse`, and it
produces non-food industrial output ("Other recovery, including biodiesel"),
not "another food product" -- using it here would blur the food/non-food
distinction this document relies on elsewhere to keep `other_recovery`
itself separate from disposal (§4.2 above).

A zero on screen is indistinguishable from a real measurement of none, and
that is the defect this whole revision exists to remove -- so `upcycling`
does not get one. Instead it draws a **stated derivation from the closest
available ReFED pathway by destination-group**: an unweighted mean pooling
every (sector, food category) row ReFED publishes across *both* of its real
reuse-group destinations, Donations (39 rows) and Animal Feed (39 rows) --
78 rows total, mean **-0.0002196610 kg CH4/kg**. This is explicitly a
**STAND-IN, not a measurement** of upcycling itself, tagged
`data_quality = "derived-refed-reuse-standin"` so it reads
differently in the audit trail from a cell that found an actual matching
ReFED destination, and is the first row that should be replaced the moment
a better source exists. `eggs` remains the one genuine unseeded gap in this
draft -- see §3.2.

**Negative values are genuine ReFED figures, not a defect.** `factor_
downstream.value_per_kg` already permits negative values by design (§4.2 of
`docs/architecture.md`): several of ReFED's own destination means come out
negative (`animal_feed`, `land_application`, `bioprocessing`/`other_recovery`,
`refuse_discard`), consistent with a downstream offset displacing an
emission elsewhere, the same reading §4.2 already gives `other_recovery`'s
co2e mean.

**ReFED's `ch4` is raw methane mass, not a CO2-equivalent, and is seeded
directly for that reason.** Confirmed by reading
`tests/benchmark/refed/build_refed_benchmark.py`'s own unit-conversion
table: its `ch4` column is read from the CSV's own
`..._mtch4_footprint_per_ton` field (metric tons of CH4 itself) and
converted by the *same* mass-based `1000 / 907.185` factor `co2e`'s
`..._mtco2e_footprint_per_ton` field uses, landing at `kg CH4/kg` -- a
distinct unit from `co2e`'s `kg CO2e/kg`, not a GWP-multiplied version of
it. This matches the `ch4` metric's own declared unit (`admin/seed.py`
`METRICS`: `("ch4", "Methane", "kg CH4", ...)`) and this draft's own `ch4`
formula (`qty_kg * (upstream + downstream)`, unchanged by this revision, no
`const_GWP_CH4` term -- identical to `docker/mock-factors.json`'s shipped
formula). `const_GWP_CH4` (bound to `GWP_CH4_20`/`GWP_CH4_100`, 84/28) stays
unreferenced by any formula here, exactly as before: multiplying by it would
overstate every methane figure by that same factor.

### 4.5 `cost` downstream: the New Zealand waste disposal levy

Neither client table carries a cost column, and O-2's closure (contract
v1.48, `docs/architecture.md`) already rules that `cost` is disposal cost
and the waste levy only -- the value of the wasted food itself stays at
zero via `FOOD_VALUE_PER_KG`, unchanged by this revision. `cost` is
therefore built here as a purely **downstream** figure: it depends on where
the waste goes, not what food it was, so no upstream `cost` row exists for
any food category (the upstream term resolves to zero via the ordinary
three-step lookup fallback, exactly as it did before this revision).

**The rate seeded is the one in force today (2026-09-05), not the rate the
client's own working notes cite.** Read directly on 2026-09-05 from the
Ministry for the Environment's own "Waste disposal levy expansion" page
(<https://environment.govt.nz/what-government-is-doing/areas-of-work/waste/waste-disposal-levy/expansion/>),
Class 1 (municipal landfill)'s own published schedule is:

| Effective date | Class 1 rate |
| --- | --- |
| 1 July 2025 | $65/tonne |
| **1 July 2026** | **$70/tonne** |
| 1 July 2027 | $75/tonne |

Cross-checked against an independently dated report of the same 1 July 2026
increase (Bin Bookings, "The National Waste Levy Explained", published 22
June 2026, read 2026-09-05: "$70 per tonne... up from $65"). **As of
2026-09-05 the levy in force is $70/tonne.** The figure the project's own
working notes cite, $75/tonne, is the rate that takes effect 1 July 2027 and
is **not yet in force** -- seeding it today would overstate every cost
figure by roughly seven percent. Both rates and both dates are recorded in
`build_upstream_factors_draft.py`'s `LEVY_SOURCE`; only the current one
($70/tonne = **0.0700000000 NZD/kg**) is seeded.

**Which destinations carry it, and why the rest are zero.** The levy is
charged at a "disposal facility" (a landfill of some class) -- it is not a
general waste charge, and every destination that is not one gets an
**explicit zero, with a stated reason**, never a silent absence:

| Our destination | Cost (NZD/kg) | Reason |
| --- | --- | --- |
| `landfill` | 0.0700000000 | *Bin to Landfill* -- Class 1 municipal landfill, the levy's core case. |
| `refuse_discard` | 0.0700000000 | Its own client rows are all published *Bin to Landfill* (§4.2) -- the identical life cycle as `landfill` itself. |
| `combustion` | 0.0000000000 | Energy-from-waste/incineration is not classified as a "disposal facility" under the Waste Minimisation Act and is excluded from the levy (an independent policy source, read 2026-09-05, not merely inferred from the name). |
| `compost`, `anaerobic_digestion`, `land_application`, `not_harvested`, `bioprocessing`, `other_recovery` | 0.0000000000 | Recycle/recovery pathways, not disposal to a levied facility. |
| `food_redistribution`, `animal_feed`, `upcycling` | 0.0000000000 | Reuse pathways; the food is not disposed of at all. |
| `sewer` | 0.0000000000 | Trade-waste discharge is charged under a separate regime (trade waste bylaws), not the Waste Minimisation Act's disposal levy. |
| `prevention` | 0.0000000000 | Zero by definition (§O-7) -- unchanged. |

`food_category_id` is left `NULL` on both nonzero rows: the levy is charged
per tonne of waste regardless of food type, exactly the shape §2.2 of
`docs/architecture.md` already gives the waste levy as its worked example of
a `factor_downstream.food_category_id IS NULL` row.

**What this does not include.** The levy is the only cost seeded here.
Landfill gate fees (the commercial charge a facility adds on top of the
statutory levy) are real and vary by facility and contract, but no
New-Zealand-wide public figure for them was found, and none is guessed --
`cost` in this draft is the statutory levy only, stated as such in every
row's `source_note`.

## 5. The stage split: a cumulative footprint, not a share of a whole

### 5.1 The defect this section replaces, and why it mattered

`factor_upstream(sector, food_category)` is the upstream footprint
**embodied in one kilogram wasted at that sector**. A household throwing
away a kilogram of cheese wastes everything that went into it from the farm
onward, so its factor should be close to the client's *full* farm-to-shelf
total, not a fraction of it.

The previous revision of this draft (`ba7fe93`) instead normalised ReFED's
five per-sector values into **shares of a whole** and multiplied them by the
client's farm-to-shelf total, so the six sectors summed to that total.
Dairy came out `primary_production 0.0000, processing 0.9756,
wholesale_retail 1.3036, consumer_household 1.5135, consumer_hospitality
0.8086, consumer_institution 0.8086` -- summing to 5.41. **That property --
"the stages sum back to the client's own total" -- is not meaningful and was
never worth checking**: a `POST /calculate` request names one sector and
nothing ever sums across them. Reading it as a virtue rather than noticing
it wasn't one is precisely how the earlier draft understated every consumer-
stage factor by roughly a factor of four, in the audience this calculator's
principal users actually belong to.

ReFED's own committed figures show the intended shape without any further
argument. `tests/benchmark/refed/refed-benchmark-factors.json`'s dairy
figures read `manufacturing 2.8794, retail 3.8473, residential 4.4668,
foodservice 4.7728` (kg CO2e/kg) -- each a standalone embodied footprint,
rising along the chain, exactly as a cumulative figure should. Their *sum*
(about 16) means nothing; their *shape* is the thing worth keeping. (The
earlier draft's "ReFED's numbers are not monotonic" observation was real,
but it came from `standard_mix`, an aggregate across many different foods
wasted in different mixes at each stage -- within a single food category,
as every mapping in §5.3 uses, the shape is overwhelmingly monotonic; where
it is not, §5.5 explains the fix.)

### 5.2 The construction: anchor, then scale, then clamp

For each New Zealand food category, one ReFED sector is the **anchor**: the
ReFED sector matching the client's own "Life cycle covered" wording for that
food (§3.1) --

| Client wording | Anchor sector |
| --- | --- |
| *Farm to Supermarket Shelf* | `wholesale_retail` (ReFED Retail) |
| *Farm to Fork* | `consumer_household` (ReFED Residential) |

The anchor's value is **set exactly equal to the client's own total** for
that food and metric -- never computed through a ratio, so it cannot drift
from the client's figure by so much as a rounding step, and mechanically
asserted by `build_upstream_factors_draft.py` before it will write a row.
Every other ReFED sector's published value for that food is then multiplied
by `client_total / anchor's own ReFED value`, giving a candidate New Zealand
figure that preserves the *ratios* ReFED's own data implies between stages
while pinning the curve to the one point the client actually measured.

`consumer_hospitality` and `consumer_institution` both draw the **whole** of
ReFED's Foodservice value (not half each, as the earlier share-based
construction did): this is a cumulative footprint, not a share, so splitting
it in half would understate both. This is a stated, flat assumption exactly
where our taxonomy is finer than ReFED's (`consumer_institution` has no
ReFED equivalent at all, ReFED not separating institutional food service
from restaurant/hospitality food service) -- not a claim that hospitality
and institutional waste actually carry identical footprints.

**Enforcing the non-decreasing invariant.** A cumulative footprint cannot
decrease as waste moves further down the supply chain, but ReFED's own
per-category figures do not always rise that way -- a few categories mix
enough different foods internally that a later stage's average dips below
an earlier one for reasons about food mix, not physics. Rather than ship an
unphysical dip or silently smooth it away, the build script asserts the
invariant mechanically (`_assert_non_decreasing`, raising if it is not met
after clamping) and repairs any violation with a one-directional clamp,
recorded in the affected row's own `source_note`:

  * Each stage before the anchor in the chain
    (`primary_production -> processing -> wholesale_retail [->
    consumer_household]`, stopping at the anchor) is capped at the *next*
    stage's already-resolved value, working backward from the anchor. This
    can only pull an earlier stage's candidate *down*.
  * Every consumer-stage sector that is not itself the anchor is floored at
    `wholesale_retail`'s resolved value. This can only push a candidate *up*.

`nuts_seeds` co2e is the clearest example: ReFED's own Dry Goods figures put
Farm (4.0288) *above* Manufacturing (2.2022) and Retail (3.0982) -- an
inversion at the very first step. Scaled to the client's 2.65 total (anchor
= retail), the raw Farm-stage candidate would come out at 3.4463, above the
anchor itself. The backward clamp caps it at Processing's own resolved value
(1.8836), so `nuts_seeds` co2e reads `primary_production 1.8836451842,
processing 1.8836451842` (equal, both below the 2.65 anchor) rather than an
unphysical farm-stage spike. `dairy` co2e shows the same effect: the public-
data farm-share estimate (§5.3) implies a Farm-stage candidate of 4.6582,
which exceeds Processing's own resolved value of 4.0490 -- so
`primary_production` is likewise capped at 4.0490.

### 5.3 The Farm stage where ReFED publishes none

Five of the nine ReFED food categories used here carry no Farm-stage value
at all in ReFED's own published factors: Breads & Bakery, Dairy & Eggs,
Fresh Meat & Seafood, Frozen, Prepared Foods and Ready-To-Drink Beverages
(only Dry Goods, Produce and Standard Mix do -- ReFED's own choice, stated in
`build_refed_benchmark.py`'s docstring). Four of those five are used by this
draft, feeding `meat`, `seafood` (Fresh Meat & Seafood), `dairy` (Dairy &
Eggs), `bakery_grains` (Breads & Bakery) and `beverages` (Ready-To-Drink
Beverages) -- for a New Zealand calculator the farm stage of meat and dairy
is the dominant term, so this is filled rather than carried through as a
zero, as the earlier revision of this draft did.

**`co2e`**: filled from Poore, J. & Nemecek, T. (2018), "Reducing food's
environmental impacts through producers and consumers", *Science*
360(6392):987-992, as republished by Our World in Data
(`https://ourworldindata.org/grapher/food-emissions-supply-chain`, read
2026-09-05; see `data/upstream-factors-draft/public_farm_share_source_data.py`
for the exact CSV rows transcribed and the full citation). For each of the
five categories, one or more Our World in Data product rows stand in as a
proxy:

| New Zealand category | Proxy product row(s) | Farm-share (unweighted mean) |
| --- | --- | --- |
| `meat` | Beef (beef herd), Pig Meat, Poultry Meat, Lamb & Mutton | 0.8868 |
| `seafood` | Fish (farmed), Shrimps (farmed) | 0.9456 |
| `dairy` | Cheese, Milk | 0.8610 |
| `bakery_grains` | Wheat & Rye | 0.6572 |
| `beverages` | Wine, Coffee, Soy milk | 0.5030 |

"Farm-share" is `(land-use change + farm + animal feed) / (that +
processing + transport + retail + packaging)` for one product row -- the
fraction of its own farm-to-retail total attributable to on-farm production,
land-use change and feed-crop growing, all of which happen before the farm
gate. This is calculated at a farm-to-*retail* boundary because that is what
Our World in Data's own stage breakdown publishes, then applied to the
client's own total, which for `beverages` (anchor `consumer_household`,
*Farm to Fork*) very likely mildly *overstates* the true farm share, since
"farm to fork" includes further consumer-stage steps this source does not
itemise -- recorded in that row's own `source_note`, not hidden here.
`beverages`'s own proxy rows also disagree with each other by a wide margin
(Wine 35.6%, Coffee 85.4%, Soy milk 29.9%) -- flagged in the same place, as
the type of drink clearly matters far more than any single "beverages"
figure can capture, and no closer public match than these three exists.

**`water`**: no public source with a *per-stage* water breakdown was found.
Our World in Data's water figures for food (e.g. "Freshwater withdrawals per
kilogram of food product") are farm-to-retail *totals*, not stage
breakdowns, so no equivalent "water farm share" can be read off them the way
the co2e share above can. Rather than force-fit a number no source actually
gives, the primary-production share of the client's water total for these
five categories is a **stated assumption, not a citation**: **90%**, on the
general (not per-product) finding that irrigation dominates agricultural
freshwater withdrawal. Every affected row's own `source_note` in
`upstream_factors_draft.json` names this plainly as an assumption.

### 5.4 `staples`: filled from ReFED alone, with no client total to anchor against

`staples` has no client row (§3.2), so there is nothing to anchor a scale
factor to. It is filled directly from ReFED's own Dry Goods absolute values
(the same shape `nuts_seeds` draws on), with a scale factor of exactly 1.
The non-decreasing invariant is still enforced, but by a plain forward
running-maximum clamp rather than the anchor-based backward clamp in §5.2
(there is no anchor to work back from): each stage in `primary_production ->
processing -> wholesale_retail` is floored at the running maximum of every
earlier stage, and the three consumer-stage sectors are floored at
`wholesale_retail`'s resolved value.

ReFED's own Dry Goods co2e figures put Farm (4.0288) above every later
stage (Manufacturing 2.2022, Retail 3.0982, Foodservice 3.0963, Residential
3.6768) -- the same inversion noted for `nuts_seeds` in §5.2, but here with
nothing to cap it against, so the running-maximum clamp floors every later
stage up to Farm's own value. `staples` co2e therefore reads a flat
**4.0287959349** across all six sectors; `staples` water, likewise, reads a
flat **2325.2841988065**. This is a mechanical, fully-documented consequence
of forcing a non-decreasing curve onto source data that happens to be
highest at its very first stage -- not a claim that staples waste carries
an identical footprint at every point in the chain. Every affected row's
`source_note` records the clamp explicitly.

### 5.5 Which ReFED category supplies which New Zealand category's shape

This governs the **shape** (and, for the five categories in §5.3, is
supplemented by public data for the Farm stage specifically) -- the anchor
value multiplied through remains the client's own, from §3.

| New Zealand category | ReFED category whose shape is used |
| --- | --- |
| `fruit`, `vegetables` | ReFED Produce (both draw the same shape; ReFED does not split fruit from vegetables either) |
| `nuts_seeds`, `staples` | ReFED Dry Goods (closest available; ReFED has no nuts/seeds or pantry-staples category) |
| `meat`, `seafood` | ReFED Fresh Meat & Seafood (both draw the same shape; ReFED does not split meat from seafood either) |
| `dairy` | ReFED Dairy & Eggs |
| `bakery_grains` | ReFED Breads & Bakery |
| `beverages` | ReFED Ready-To-Drink Beverages |
| `standard_mix` | ReFED Standard Mix |

### 5.6 The result

For every New Zealand food category and metric, the six sector values below
are a cumulative footprint: non-decreasing along `primary_production ->
processing -> wholesale_retail -> consumer_*`, with the anchor stage (in
**bold**) equal to the client's own total from §3 exactly. Both properties
are asserted mechanically by `build_upstream_factors_draft.py`
(`_assert_non_decreasing` and the anchor-equality check in
`_build_category_rows`), not merely claimed here. Values in kg CO2e/kg and
L/kg respectively.

| Category | primary_production | processing | wholesale_retail | consumer_household | consumer_hospitality | consumer_institution |
| --- | --- | --- | --- | --- | --- | --- |
| `fruit` co2e | 0.2156370311 | 1.1220166353 | **1.7800000000** | 2.4493781096 | 2.2942781335 | 2.2942781335 |
| `fruit` water | 1079.2419160082 | **1360.0000000000** | 1360.0000000000 | 1360.0000000000 | 1540.8789907018 | 1540.8789907018 |
| `vegetables` co2e | 0.1602281800 | 0.8337096948 | 1.3226214390 | **1.8200000000** | 1.7047536215 | 1.7047536215 |
| `vegetables` water | 405.2791462681 | 510.7100000000 | 510.7100000000 | **510.7100000000** | 578.6340509863 | 578.6340509863 |
| `nuts_seeds` co2e | 1.8836451842 | 1.8836451842 | **2.6500000000** | 3.1449222707 | 2.6500000000 | 2.6500000000 |
| `nuts_seeds` water | 13419.0542765924 | 13419.0542765924 | **13971.0400000000** | 13971.0400000000 | 13971.0400000000 | 13971.0400000000 |
| `meat` co2e | 10.3110439558 | 10.5990639635 | **11.6275000000** | 12.9013075057 | 14.2305376082 | 14.2305376082 |
| `meat` water | 6289.1673353447 | 6289.1673353447 | **7185.3475000000** | 7185.3475000000 | 7185.3475000000 | 7185.3475000000 |
| `seafood` co2e | 5.4146153467 | 5.4146153467 | **5.9400000000** | 6.5907346019 | 7.2697822742 | 7.2697822742 |
| `seafood` water | 3153.6219938211 | 3153.6219938211 | **3603.0000000000** | 3603.0000000000 | 3603.0000000000 | 3603.0000000000 |
| `dairy` co2e | 4.0489595250 | 4.0489595250 | **5.4100000000** | 6.2811800053 | 6.7114546836 | 6.7114546836 |
| `dairy` water | 2506.9170000000 | 2785.4633333333 | **2785.4633333333** | 2785.4633333333 | 3097.4076591385 | 3097.4076591385 |
| `bakery_grains` co2e | 1.8183576113 | 2.7306582492 | **2.7666666667** | 3.5136961786 | 2.7666666667 | 2.7666666667 |
| `bakery_grains` water | 1830.5340000000 | 2033.9266666667 | **2033.9266666667** | 2033.9266666667 | 2033.9266666667 | 2033.9266666667 |
| `beverages` co2e | 0.9388955359 | 0.9388955359 | 1.3877577099 | **2.0100000000** | 4.3030470783 | 4.3030470783 |
| `beverages` water | 394.2000000000 | 438.0000000000 | 438.0000000000 | **438.0000000000** | 717.0277664425 | 717.0277664425 |
| `standard_mix` co2e | 0.3729210619 | 4.7360709354 | **6.0400000000** | 6.0400000000 | 7.0223981566 | 7.0223981566 |
| `standard_mix` water | 381.7599766536 | 3098.5664218617 | **3161.0000000000** | 3161.0000000000 | 4744.8825529055 | 4744.8825529055 |
| `staples` co2e | 4.0287959349 | 4.0287959349 | 4.0287959349 | 4.0287959349 | 4.0287959349 | 4.0287959349 |
| `staples` water | 2325.2841988065 | 2325.2841988065 | 2325.2841988065 | 2325.2841988065 | 2325.2841988065 | 2325.2841988065 |

(`staples` has no anchor -- see §5.4 -- so no cell is bolded for it; every
value there is ReFED's own, floored to a running maximum.)

### 5.7 `ch4` upstream: filled from ReFED alone, every food category

Neither client table carries a methane column at all, so there is no client
total to anchor *any* food category's `ch4` to -- unlike co2e/water, this is
not a "some categories have a client figure, some don't" situation; none do.
Every one of the ten New Zealand food categories' `ch4` upstream rows is
therefore built the unanchored way §5.4 already uses for `staples`'
co2e/water: ReFED's own absolute per-stage methane figures, scale exactly 1,
non-decreasing enforced by a forward running-maximum clamp. This reuses
`NZ_TO_REFED_FOOD_SHAPE` and `REFED_SECTOR_FOR_NZ_SECTOR` exactly as already
built for co2e/water in §5.2/§5.5 -- no second food-category or sector
mapping is introduced for methane.

| Category | primary_production | processing | wholesale_retail | consumer_household | consumer_hospitality | consumer_institution |
| --- | --- | --- | --- | --- | --- | --- |
| `fruit` ch4 | 0.0008140063 | 0.0028094187 | 0.0047158881 | 0.0067383152 | 0.0056488233 | 0.0056488233 |
| `vegetables` ch4 | 0.0008140063 | 0.0028094187 | 0.0047158881 | 0.0067383152 | 0.0056488233 | 0.0056488233 |
| `nuts_seeds` ch4 | 0.0075282276 | 0.0080761003 | 0.0142371425 | 0.0164156786 | 0.0142371425 | 0.0142371425 |
| `meat` ch4 | 0.1679408876 | 0.1679408876 | 0.1816944075 | 0.1827416355 | 0.1953242898 | 0.1953242898 |
| `seafood` ch4 | 0.1679408876 | 0.1679408876 | 0.1816944075 | 0.1827416355 | 0.1953242898 | 0.1953242898 |
| `dairy` ch4 | 0.0440772809 | 0.0440772809 | 0.0480769779 | 0.0496758764 | 0.0617321061 | 0.0617321061 |
| `bakery_grains` ch4 | 0.0101927230 | 0.0101927230 | 0.0101927230 | 0.0113109546 | 0.0101927230 | 0.0101927230 |
| `beverages` ch4 | 0.0059509972 | 0.0059509972 | 0.0088489176 | 0.0110395864 | 0.0295534375 | 0.0295534375 |
| `standard_mix` ch4 | 0.0008804519 | 0.0412901324 | 0.0412901324 | 0.0412901324 | 0.0440145668 | 0.0440145668 |
| `staples` ch4 | 0.0075282276 | 0.0080761003 | 0.0142371425 | 0.0164156786 | 0.0142371425 | 0.0142371425 |

None of these rows is bolded as an anchor: there is no client figure to pin
any of them to, for any category, and that absence is the point of this
section rather than an oversight.

**The five categories missing a Farm-stage co2e value (§5.3) are missing a
Farm-stage methane value too** -- the identical structural gap in ReFED's
own published data (`meat`, `seafood`, `dairy`, `bakery_grains`,
`beverages`). Unlike co2e, **no public per-product methane farm-share source
was found** to fill it: Poore & Nemecek's own farm-share (used in §5.3) is a
CO2e-equivalent share, and does not carry over to methane, whose farm-stage
share for livestock is typically *larger*, not smaller, on account of
enteric fermentation. Rather than leave `primary_production` a silent gap --
implausible for meat and dairy in particular, where on-farm methane is
usually the dominant term -- it is floored at `processing`'s own resolved
value: the highest figure the non-decreasing invariant permits without
inventing a number no source gives. **This is very likely still an
understatement** for those five categories, and every affected row's
`source_note` says so in full (`data_quality =
"derived-refed-no-farm-floor"`, distinct from the ordinary
`"derived-refed-unanchored"` tag every other cell in this table carries).
`standard_mix`, by contrast, does have a published Farm-stage methane value
from ReFED (0.0008804519, well below every later stage) and needs no such
floor.

## 6. What is not represented, and why

- **`land`** -- the client's table 1 gives t/ha for every food row. This
  system has no `land` metric to receive it (`admin/seed.py` `METRICS`:
  `co2e`, `ch4`, `water`, `cost`, `mass`). Adding one is a metric-table
  change with system-wide effect (every existing result, on every factor
  set, would gain a `land` line at zero the moment the row exists -- §4.1 of
  `docs/architecture.md`, "metrics are data, not code" and "the engine
  iterates every active row"). That decision belongs to the owner, and is
  not taken here.
- **The waste levy's own "disposal cost" component beyond the statutory
  levy itself** -- landfill gate fees vary by facility and contract and no
  New-Zealand-wide public figure was found for them, so `cost` in this
  draft is the statutory levy only (§4.5), stated as such in every row's
  `source_note`.
- **Sector variation in the downstream figures** -- `factor_downstream.
  sector_id` exists (contract v1.31) so that a set can price a destination
  differently by supply-chain stage. The client's table 2 gives one CO2-eq
  and one water figure per destination, with no stage breakdown, so every
  downstream row in this draft carries `sector = null` ("applies to every
  sector"), matching how the New Zealand mock set's own downstream rows are
  already built. This includes every ReFED-filled row added across both
  revisions of this draft (`other_recovery`, `combustion`'s water, and now
  `ch4` for all fourteen destinations and `cost` for two): all are
  unweighted means/direct reads across everything ReFED (or, for the levy,
  the government's own published rate) gives, for the same reason.

**`ch4` and `cost`, both previously entirely absent, are as of this revision
seeded for every food category and every destination** -- see §4.4, §4.5 and
§5.7 for the full construction. `upcycling`'s `ch4` is not a gap: it draws a
stated stand-in, pooled from ReFED's two real reuse-group destinations,
tagged `derived-refed-reuse-standin` so it is never mistaken for a
cell that found an actual matching ReFED destination (§4.4).
**Completeness is asserted mechanically, not merely narrated**:
`_assert_completeness()` in `build_upstream_factors_draft.py` checks that
every one of the ten food categories carries a generic AND a
prevention-override upstream row, for every one of the six sectors, for
`co2e`, `water` and `ch4`; and that every one of the fourteen destinations
carries exactly one downstream row for `co2e`, `water`, `ch4` and `cost`
(`mass` is exempt -- its formula needs no factor lookup at all, matching the
live/mock set). It raises `SystemExit` naming the exact missing (or
duplicated) cell if a future edit ever drops one -- this is what closed the
`upcycling`/`ch4` gap this section used to describe: it was found by a
by-hand row count, and this check is what stops that class of defect coming
back silently.

**A second mechanical check joins it as of the 2026-09-21 revision.**
`_assert_only_table2_co2_moved()` re-runs the measurement that revision was
made on -- table 1 unchanged across all 26 rows and all six columns, table 2's
destination labels, life-cycle column and water column unchanged, only its
CO2-eq column replaced -- against `PRIOR_REVISION_TABLE1` and
`PRIOR_REVISION_TABLE2_EXCEPT_CO2` in `rawtec_source_data.py`, which freeze
those cells exactly as the 2026-09-05 transcription held them. It also refuses
a table 2 still carrying any value from the withdrawn column, which is what a
half-applied revision looks like. It replaces `TABLE2_CO2_COPY_SOURCE`'s check
(§4.1) and keeps its purpose: no silent drift between the client's document,
the transcription and the prose about it. Both checks are exercised by
`tests/test_upstream_factors_draft_build.py`.

What remains genuinely unfilled after this revision: `land`
(no metric exists), gate fees beyond the statutory levy (no public source
found), and `eggs`/`staples`' still-unresolved status as noted in §3.2
(`staples` itself *is* seeded, from ReFED alone, per the owner's ruling;
`eggs` remains an unseeded gap pending a taxonomy or client decision).

## 7. The three equivalences

`equivalences` in this draft was `[]` through every prior revision --
`admin/seed.py` creates no equivalence rows either, so the results page's
"Tangible equivalents" block has been empty on both deployments. This
revision fills it with the three conversions the client's own "Data sources
for impact calculator" document (received 2026-08-29) supplies, each with
its `source_note` carrying the client's exact wording. This is a **partial**
close of O-3 -- the New Zealand basis for these three conversions is now the
client's own stated figures, but the equivalence set is not necessarily
complete, and no NZ-specific source has been substituted for any of the
three.

| `code` | `source_metric` | Client's statement | `value_per_unit` |
| --- | --- | --- | --- |
| `vehicles_year` | `co2e` | "Passenger vehicles on the road: GHG emissions (t CO2e) / 2.41 (t CO2e/passenger vehicle/year)" | `1/2410` |
| `olympic_pools` | `water` | "Olympic swimming pools: = (Water Used (L)) / 2,500,000" | `1/2,500,000` |
| `meals` | `mass` | "Meals: 450g per meal" | `1/0.45` |

**The tonnes-to-kilograms adjustment.** The client states the vehicle figure
per *tonne* of CO2e -- this system's `co2e` metric is in *kilograms*
(`metric.unit`, and every `co2e` factor and formula in this draft and the
live/mock set alike). Dividing a kilogram total by 2.41 would answer "how
many vehicle-years is this if it had been measured in tonnes", which is off
by a factor of a thousand and renders as an entirely plausible number -- the
same class of error this repository already shipped once, in the
`display_unit` rows that read "t CO2e" against totals the engine returns in
kg. The other two conversions need no such adjustment: `water` is already
litres (matching "Water Used (L)" directly), and `mass` is already
kilograms, so 450 g per meal is applied as `1 / 0.45` with no unit change.

Every value above is computed as a `Decimal` division and stored quantized
to ten places (`Decimal("1E-10")`), the same scale every other factor in
this set carries; `str()` on the very small `olympic_pools` factor prints in
scientific notation (`"4.000E-7"`), which parses back to the identical
`Decimal` value and is not a different number from `"0.0000004000"`.

`build_upstream_factors_draft.py`'s `_assert_completeness()` now also
refuses to write a set where an equivalence names a `source_metric` this set
does not compute, or carries no `source_note` -- the same mechanical
guarantee §6 describes for the factor rows, extended to cover the one other
place a silent gap could hide.

## 8. The traxie data directory

Nothing under `traxie data/` (the client's original files: spreadsheets,
zips, images, the brand pack) is committed anywhere in this repository --
that directory is gitignored and stays that way. Every number in this
document and in `upstream_factors_draft.json` was retyped by hand from a
Markdown conversion of the client's tables into the two committed Python
files under `data/upstream-factors-draft/`, or transcribed from a public
CSV export (`public_farm_share_source_data.py`), which is the only form any
of it takes in version control.
