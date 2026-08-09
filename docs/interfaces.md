---
title: "Kai Commitment Impact Calculator — Interface and Data Contract"
subtitle: "Single source of truth for five-way parallel development"
date: "2026-08-09 (v1.7 draft)"
---

# 0. How to Use This Document

This document defines **what every person's code receives and what it returns.** Any cross-module call is governed by this document.

> **Contract change process (mandatory):** update this document, then notify the whole team, then update the mock data in `tests/fixtures/*.json`. All three steps, every time. Renaming a field unilaterally is the most common source of rework on a five-person project.

**Ownership**

| Section | Owner | Content |
| --- | --- | --- |
| §2 Database | B | Schema and migrations |
| §3 Domain objects | A | Data classes for engine input and output |
| §4 Engine | A | Calculation function signatures |
| §5 Repository | B | The only functions that touch the database |
| §6 REST API | B (implements) / C, D (consume) | HTTP contract |
| §7 Front-end modules | C, D | JavaScript module signatures |
| §8 Admin panel | E | Staff-only interfaces |
| §9 Error codes | B | Global and uniform |

## 0.1 Change Log

> **On version numbers.** Two lines of this document ran in parallel from 2026-08-07 to 2026-08-09: v0.10–v0.13 on `admin_panel`, and v1.0–v1.1 on `docs/contract-v1.0`. They were merged as v1.2. Entries below appear in the order they were merged, not in numeric order, and both sequences are real — a reference to "v0.13 §8.3" and one to "v1.1 §2.2" both resolve here.

### v1.7 — 2026-08-09 (the change that was made without a row, **affects B, C, D and E**)

**Change 1 is a §2.1 and §6.1 ruling that has been in the code and the fixtures since 2026-08-09 and in this change log nowhere.** §0's process is document, notify, fixture — and the change log *is* the notify step. A rule that only exists in a commit message has been applied to `tests/fixtures/taxonomy.json` and `admin/seed.py` without being announced to the two people who consume them, and v1.6's own closing note then asserted the opposite, that §1–§6 were untouched and the fixtures were clean. **A false "nothing changed" is worse than no note**: it tells a reader not to look.

The rest are the four defects and three omissions the whole-branch review turned up in `web/`. None changes a wire shape.

| # | Change | Section | Affects |
| --- | --- | --- | --- |
| 1 | **`display_unit` is a presentation variant of `unit` at the same scale, never a different scale, and nothing anywhere converts between the two.** `tests/fixtures/taxonomy.json` and `admin/seed.py` both carried `t CO2e` against a `unit` of `kg CO2e`, `kL` against `L`, and `t` against `kg`. **§7.6.1 is what makes that unfixable rather than merely wrong**: the front end performs no arithmetic on an API figure, so there is no layer that could divide a `kg CO2e` total by 1,000 on its way to a `t CO2e` label — the number is relabelled and every greenhouse-gas figure on the page reads a thousand times too small. It was live: C's results table rendered "3,993 t CO2e" and "1,530,000 kL" one section below the same two figures labelled correctly. The column is for a typographic variant — `kg CO₂e` against `kg CO2e` — which is how §6.1's example is written, with the two identical. **If a metric should be reported in tonnes, that is the metric's `unit` and the formula produces tonnes**: scale is a property of a formula, which is data (§2.1), not of a label. Corrected in **both** the fixture and the seed, and the seed is the half that matters, because it is what ships to the database. Ruled in §2.1 and §6.1 on 2026-08-09; **recorded here, which is the step that was missed** | §2.1, §6.1, §10 | **B, D, E** |
| 2 | **§7.2 gains `entryResultsFrom(entries, response)`, the one export §7 still did not name.** It is imported by `calculator.js` and its output is read by `results.js`, so it is already a cross-module call — which is the definition §0 gives of what this document governs. Documented with the distinction a consumer has to get right: cross-entry figures come from `result.totals`, per-entry figures from `result.entry_results`, and never a sum over the latter (§7.6.1) | §7.2 | **C, D, E** |
| 3 | **`improvement.js` interpolated `destination.code` into `for="…"` and `id="…"` through neither `escapeHtml` nor `slug`** — the only unescaped interpolation left on the branch. `destination.code` is `VARCHAR(64)` with no pattern constraint in `db/`, `api/` or `admin/`, and staff edit it through §8.1's generic CRUD, so a code containing a double quote breaks the attribute on a **public** page. Fixed with `slug`, which `calculator.js` already uses for the identical case. Recorded rather than fixed quietly because it names a standing hazard: **§7.3a's escaping guarantee holds only for values that pass through `view.js`**, and a taxonomy `code` reads as safe while being staff-authored content on a public page, exactly like the `formula.expression` §7.3a already calls out | §7.3a | **C, D, E** |
| 4 | **§7.6 rule 3's 375px baseline was being met at 375px and broken between 481px and 849px.** `.results-page`'s −80px bleed sat outside every media query while its `.wide` counterpart existed only at ≥850px and its reset only at ≤480px, so in the band between them the results page was pulled 80px past a container with 20px of padding: 60px off the left edge, unreachable in LTR, plus horizontal body scroll. **A tablet is the likeliest non-desktop demo device.** Written into the section because "mobile-first, baseline 375px" reads as a floor and is not one — a layout can pass at the baseline and at desktop and fail in between, and only a rule that says so will get the middle checked | §7.6 | **C, D** |
| 5 | **The stylesheet asked for seven weights the project does not have, and now asks for two.** Self-hosting (v1.6 change 10) replaced a five-weight CDN request with the two static faces the brand authorises — Geologica Bold and Kumbh Sans Regular — leaving 500, 600, 750, 800 and 900 with no face behind them. `font-synthesis: weight` kept them looking bold by synthesising; collapsing every declaration to 400 or 700 makes them bold **and** removes the synthesis, and `font-synthesis: none` is back. The brand-correct answer and the technically clean one were the same answer. **A weight that is not 400 or 700 now requires a font file to go with it** | §7.6 | **C, D, E** |
| 6 | **`RATE_LIMITED`'s 60-second timer cleared the deadline and not the banner**, so Calculate re-enabled underneath a paragraph still telling the user to wait 60 seconds — the button and the copy saying opposite things, with the copy the more believable of the two. The banner is now cleared with the deadline, but only if it is still the rate-limit banner: another failure may have replaced it inside the minute, and that message is about something the wait does not fix. §9.2's `BLOCKED` rule is the same shape and was already right; this was the one code with a timed recovery and no matching copy reset | §9, §7.3a | **B, C** |
| 7 | **`web/README.md` now says not to run a client demo on `?mock=1`.** §7.1 documents that mock mode re-derives only the mass figures and serves every impact figure from the fixture, cycled by entry index — so **a user who enters 5 kg is shown 4,449 kg CO2e**. That is correct behaviour for a fixture server and a catastrophic thing to put in front of the client, and the file the team actually opens said nothing about it. The instruction is to demo against the real API with the mock **factor set**: the figures are still placeholders, but they are placeholders the engine computed from what was entered, and the mandatory banner (§7.6.2) says so on screen | §7.1 | **all** |

> **Change 1 is the only one that touches a fixture, and the fixture was already changed** — this row is the announcement, not a new edit. Nothing in this revision alters a request or response shape.
>
> **Still open after this revision.** Unchanged: **O-1** (real emissions factors, the hard blocker), **O-7** (on A's critical path), `landfill_diverted` as a real `metric` row, the container-preset input (v1.6 change 4), and `gwpHorizon`, which still has no control. The positive/negative colour pair stays partly open on the same terms as v1.6. Recorded and **deliberately not fixed here**, because each is a refactor rather than a correction: `main.js` shows the taxonomy-failure screen's raw `error.message` because `publicError` is not exported from `calculator.js`; an all-`standard_mix` submission renders "no food category data was provided", which is untrue; and `.stage-fieldset.has-error` and `.destination-list.has-error` are dead as written, because `has-error` is only ever applied to `.form-field`.

### v1.6 — 2026-08-09 (§7 caught up with the front-end branch, **affects C, D and E**)

**Every row here is §7 describing code that no longer exists.** The `integrate/frontend` branch removed thirteen violations of §7.6 over six tasks, and §7 was written from a survey of the branch *before* them — so the section D and E are told to read before consuming C's modules has spent a week telling them that `formatNumber` has a rounding gap it does not have, that `results.js` hard-codes three metric columns it no longer names, and that `compareImprovement` takes one argument when it takes two. **A stale §7 is not a cosmetic problem: it is a second implementation.** §7.3a exists so D and E use C's one `escapeHtml` instead of each writing their own, and a reader who finds the described module and the real module disagreeing has no reason to trust either.

Two rows are not corrections. Row 9 settles an ambiguity nobody had ruled on, and row 10 adds a constraint the branch had already broken once.

| # | Change | Section | Affects |
| --- | --- | --- | --- |
| 1 | **§7.1's mock-mode blockquote described the rewrite it was asking for as still owed.** It required the mock path to move to the `entries` / `totals` shape and to "serve the fixture as written"; it has moved, and it **cannot** serve the fixture as written — `mass` and the whole `totals` roll-up are functions of a request whose entry count a static file cannot know. Stated as it is, with the licence named as `mockRequest`'s alone, because the sentence a reader takes from a stale requirement is that mock mode is untrustworthy in ways it is not, and the sentence they need is which two figures are browser-derived | §7.1 | **C, D, E** |
| 2 | **§7.1's "known defect" on the fixture path was fixed and replaced by a different one that no version stated.** The absolute `fetch('/tests/fixtures/…')` is gone — the URL now resolves against the module's own URL, so it follows the page. What survives is structural and unfixable in JavaScript: a browser clamps `../` at the origin root, so **the document root must be an ancestor of both `web/` and `tests/`**, and FastAPI serving `web/` as the static root makes every mock call 404. Recorded with an owner (**B**, a dev-only mount) because all three of C, D and E develop in mock mode and would each rediscover it separately | §7.1 | **B, C, D, E** |
| 3 | **§7.2's key table is now exhaustive, and says so.** `state.alternative` and `state.compareAlternative` were on the object and in neither the table nor any reader: initialised, reset by `resetCalculator`, assigned `[]` by two functions in `calculator.js`, read by nothing. Removed. A key that is initialised and reset but never populated reads as a feature under construction, and the next person to need an alternative scenario would have wired theirs into a dead one rather than into `improvedAllocations`, which is where the live one is | §7.2 | **C** |
| 4 | **§7.2's `unitPreset` / `unitCount` requirement now states what building it costs**, because "not built" was hiding a decision. `toKg` returns kilograms at **three** decimal places and `calculator.js` validates `totalAmount` at **two**, so a preset whose `kg_per_unit` is not a whole number produces a total the form then refuses. Every `kg_per_unit` in `tests/fixtures/taxonomy.json` is integral and the column is `DECIMAL(12,4)`, so the collision is invisible on the fixture and certain on real data. **This is why `toKg` is still imported by nothing and was not wired up in this revision** — it needs a ruling on what a user may type, not a refactor | §7.2, §7.3 | **B, C** |
| 5 | **§7.2's fast-path exception claimed a drift wider than the one that exists.** The typed and re-rendered paths no longer disagree about server-supplied field errors — `updateLine` clears them, deliberately, because blanking or filling a row changes which lines the request carries and the server's line positions stop meaning what they meant. What remains is narrower and still real: on a negative amount `updateLine` marks only the row being typed in while `destinationRows` marks every negative row. An overstated warning and an understated one fail the same way — the reader stops believing the section | §7.2 | **C, D** |
| 6 | **§7.3 said `tonnes ? 1000 : 1` was duplicated at six sites. It is duplicated at none.** The last of them — `improvement.js`'s `lineKg`, the denominator of every allocation percentage — now calls `massToKg`, and the three sites that rounded a conversion to the API's three decimal places call `kgString`, which the section described as imported by nothing. §7.3's rule is that the front end's arithmetic can be audited in one file, and a rule observed at five of six sites is worth less than none, because a reader who checks one site concludes it holds. The section now states the auditable form of the rule: **a `*`, `/` or `.toFixed()` on a mass anywhere else in `web/` is a defect on sight** | §7.3 | **C, D** |
| 7 | **§7.3a's `formatNumber` JSDoc and its "known gap" described opposite behaviours, and both were wrong.** The signature said `{maximumFractionDigits: precision}` and the note beneath said a cost of `825.00` therefore renders as "825"; the function sets **both** bounds and has since 2026-08-09, and it clamps the digits to Intl's legal 0–20 because `display_precision` arrives from the database and an out-of-range value makes `toLocaleString` throw a `RangeError` that would take out the whole render rather than one figure. Added in its place is the precondition the calling modules actually depend on: `Number('')` is `0`, so a `\|\| 0` on an API figure makes absent, malformed and zero the same figure on screen, and every caller maps absent to `NaN` so that `formatNumber` prints "Not available" | §7.3a | **C, D, E** |
| 8 | **§7.3a's `results.js` and `calculator.js` notes were both to-do lists for work that is done.** The client-side aggregation layer, the hard-coded `CO₂e / Cost / Water` columns and the three hard-coded equivalence labels are gone; `aggregateResults` and `differenceData` no longer exist. So are all three of `calculator.js`'s silent field-binding failures — the `field` format, the index mismatch, and a third the section never named: `fieldErrorMap` stored the **envelope's** message against every field, so a correctly bound row would still have read "Request validation failed" and discarded the only prose that said what was wrong with that row. Replaced by what the modules now are, plus the two things a reader will otherwise re-litigate: why `mass` may be named in both modules without breaching §7.6.5, and why a bar width is not a figure | §7.3a | **C, D** |
| 9 | **Ruled: the arrow beside a comparison figure shows the direction of the impact, not the sign of the number.** An up arrow beside "1,104.0 kg CO2e saved" reads as "better" to one person and "went up" to another, and nothing in this document had ever said which. `net_benefit` is `current − alternative` (§3), so a positive net benefit is a saving, the impact fell, and the arrow points **down**. The ambiguity had a structural cause worth naming: `.value-negative` was carrying two different statements — "this change moved the wrong way" and "this quantity is below zero, because a destination offsets more than it costs" — so no single arrow could be right for both. They are now two sets of classes, tabulated in §7.3a, and `.value-negative` deliberately carries **no** arrow because `formatNumber` already prints the minus sign and a quantity is not a movement. §7.6.6 sends D to the same four classes so the statistics page does not grow a second convention | §7.3a, §7.6 | **C, D** |
| 10 | **New rule §7.6.7: no page may request an asset from a third-party host at runtime.** `styles.css` opened with an `@import` from `fonts.googleapis.com`, so every visitor's browser announced itself to Google before the first paint — on a calculator whose privacy position is §2.3's and whose statistics page says so in its own copy — and the first paint waited on a network the project does not control. The two brand faces were already in `admin/static/fonts/` and are now in `web/assets/fonts/` as well. **The rule is written down because §7.4 is the next place it would break:** that section tells D to return a Chart.js instance and says nothing about where Chart.js comes from, and the one-line CDN `<script>` is the documented way to add it | §7.6, §7.4 | **C, D, E** |
| 11 | **§7.3a's `main.js` "known defect" was fixed in the first task of the branch, and the fix is now a requirement rather than an implementation detail.** `main.focus()` after every `setState` ejected a keyboard user from the sector radio group, so step 1 could not be passed without a mouse. Both halves of the replacement are stated, because the obvious half is not sufficient: focus moves to `<main>` on a **step transition** and returns to the element that had it, by `id`, on a **same-step** re-render — merely scoping the focus call leaves the user on `<body>`, which is worse than where they started | §7.3a | **C** |
| 12 | **`compareImprovement(state)` takes two arguments.** The second is `calculator.js`'s `publicError` — §9's code-to-copy map — passed in rather than imported, because `calculator.js` already imports this module and the import back would be a cycle. Without it the improvement panel showed raw backend prose for the codes the main flow words carefully, and §9.1 rules that a public `FORMULA_ERROR` never echoes the expression or its location. A wrong arity in a section whose purpose is to be called from D's and E's code is the cheapest possible defect to introduce and among the more annoying to diagnose | §7.3a | **C, D, E** |
| 13 | **`improvementValidation`'s tolerance was a percentage-point tolerance where §6.2's is an absolute 0.010 kg**, and §7.3a described neither. 0.01 percentage points is 0.15 kg on a 1,500 kg entry — fifteen times the limit — so the panel enabled Compare on a submission the server then refused with a 400, **for the whole submission**, after the user had left the screen with the numbers on it. It now sums the lines that will actually be sent. Two consequences are recorded with it: the seeded allocation was itself invalid under the corrected check (52.17 + 34.78 + 13.04 = 99.99%), so the rounding remainder goes to the largest share; and `improvedLines` anchors on the entry's **allocated** current mass rather than the total typed at step 3, because step 4 deliberately permits allocating less than the total and anchoring on the typed total made every under-allocated entry send an alternative heavier than its current scenario | §7.3a | **B, C** |

> **Corrected in v1.7 — the two sentences that stood here were false.** They read "Nothing in §1–§6 or §8–§10 changed" and "`tests/fixtures/` is untouched", and both were written from this revision's own edits rather than from the branch's. The `display_unit` same-scale ruling had already changed **§2.1**, **§6.1**, `tests/fixtures/taxonomy.json` and `admin/seed.py`, with no change-log row anywhere. See **v1.7 change 1**, which is that row. What is true of *this* revision's own edits: they touch §7 only, plus §7.6's new rule 7, and moved no request or response shape.
>
> **Still open after this revision.** **O-1** (real emissions factors) remains the hard blocker and **O-7** is still on A's critical path. Carried forward and unchanged: `landfill_diverted` as a real `metric` row (the client's). The **positive/negative semantic colour pair**, carried since v1.2, is *partly* closed — change 9 fixes the four classes, their arrows and their brand colours for the calculator page, and D can adopt them as they stand — but whether the client wants Kale-and-Beetroot for better-and-worse, rather than a green-and-red pair the brand does not contain, has still not been asked. New and unclosed: **the container-preset input (change 4)**, which needs a ruling on the two-decimal rule before `toKg` can be wired to anything, and **`gwpHorizon`**, which still has no control.

### v1.5 — 2026-08-09 (from the whole-branch review, **affects B, C, D and E**)

Two of these are privacy defects, and **both are of a kind that is invisible from inside any one module.** Each is produced by two correct-looking halves meeting: a validator with no rule about `prevention` next to an aggregation that filters *for* the scenario `prevention` would land in; a suppression threshold that protects every bucket except the bucket suppression creates, in three breakdowns that are all drawn from the same entries. Neither shows up in a review of the file it lives in, which is the argument for reviewing a branch end to end before it merges rather than each task as it lands.

The third closes v1.4's one open decision. The remainder are the same class v1.4 set out to close — a convention only the implementer knew — and finding four more of them one revision later is the honest measure of how large that class was.

| # | Change | Section | Affects |
| --- | --- | --- | --- |
| 1 | **`prevention` is now refused in a `current` scenario, and §6.2's validation table says so.** Nothing on the server enforced it — only C's UI, which never offers it there. A hand-rolled `POST /calculate` persisted a current-scenario `prevention` line, and §5.4 selects `scenario = 'current'`, so it became a bucket in the **public** `by_destination` chart: the destination for waste that by construction did not happen, counted as real waste, on the page whose entire design problem is not overclaiming. §5.4's scenario predicate is the other half of this and structurally cannot catch it — it excludes the *alternative* scenario, and the line is not in the alternative scenario. `tests/api/test_fixture_consistency.py` asserts this of the fixture, which is what made it look covered; a fixture constrains the fixture. The rule sits on the current scenario only, so the failure locates at `entries[i].current` and the alternative keeps the destination that is its whole purpose. `PREVENTION_CODE` moved to `db/types.py` with `admin/taxonomy_rules` re-exporting it, because `api/` may not import `admin/` — v1.3's ruling on `db/detection.py`, for the same reason | §6.2, §2.1 | **B, C** |
| 2 | **`submission.token` joins §5.5's `REDACTED_FIELDS`, ahead of the screen that would leak it.** §8.2 specifies `/admin/submissions` as a record-level moderation view that sets `excluded_from_public`. The moment it is built on `AuditedModelView`, `write_audit` snapshots the whole row and copies the **live session token** into `audit_log` — readable by every staff member, never expiring, and out of reach of `expire_tokens`, which nulls the column on `submission` and knows nothing about copies. **§2.3's guarantee is that the linkage is severed after an hour**, and a copy in an audit row makes that false for every moderated submission, permanently. One line now; a privacy regression later that nobody would think to look for, in a table whose whole purpose is to be trusted | §5.5 | **B, E** |
| 3 | **v1.4's open item on `other` is decided, in favour of privacy: `other` is subject to the threshold like any other bucket.** While it is below the threshold it absorbs the **smallest visible** bucket, repeatedly; if merging everything still cannot clear it, the breakdown publishes **no buckets at all** and `total_calculations` stands alone. What made this urgent was not the single-bucket case v1.4 described but its cross-breakdown form: `by_sector`'s `other` and `by_food_category`'s `other` are formed from the same entries, so `tests/fixtures/stats.json` showed both carrying `count: 4, total_kg: "3210.750"` — identical, therefore the same four entries, therefore joinable at a glance. At `count: 1` that is one user's exact tonnage published three times with only the label hidden. **The shares property v1.4 worried about survives**: `other` stays an ordinary bucket carrying every suppressed entry, so the pre-suppression denominator is untouched and §6.4's "shares sum to 1" holds. The fixture is regenerated through `_bucketise` rather than levelled by hand, and its two `other` buckets are now 118 and 88 | §5.4, §6.4 | **B, D** |
| 4 | **§2.1's `destination` blockquote was the last site still claiming `prevention` has "all factors set to zero".** v1.4 corrected `architecture.md` §4.1 and §6.2 and left this one — in the **normative schema section**, which is where a new reader meets the word first, and which therefore outranks both of the sites that were fixed. It now states what is true (downstream zero, upstream not and structurally cannot be), points at O-7, and lists the three rules stated in terms of this code so that a future editor can see what removing it would break | §2.1 | **all** |
| 5 | **§9 defines two `details` shapes; the code emitted three, and one of them had `issue` and `message` inverted.** `api/router.py`'s inline-bundle check emitted `{field, issue}` with `FactorBundle.validate()`'s human-readable prose (§4.1) in `issue` — so a consumer told to branch on `issue` would branch on a sentence that changes whenever the engine's wording changes, and would find no `message` to display. **The code was corrected to the contract rather than the contract widened to the code**: two shapes a consumer can predict from `code` is a contract, three it must sniff at runtime is not. §9 now states the rule as a table and says plainly that no presence checks are needed | §9 | **B, C** |
| 6 | **§10's `taxonomy.json` row described a file that does not exist.** It said "nine MfE destinations across the `prevention`, `reuse`, `recycling` and `disposal` groups"; the fixture has **fourteen** destinations across **three** groups, `prevention` is a destination in `reuse` rather than a group, and `recycling` is not a group code — it is `recycle_recovery`. v1.4 change 5 claimed §10 had been rewritten from the directory, and this row had not been. Recorded rather than quietly fixed because it is the second-order failure worth naming: a section whose stated purpose is to describe what is on disk is the section a reader will not verify | §10 | **C, D** |
| 7 | **§6.2 states what `food_category: null` and `"standard_mix"` do to the duplicate check: they are distinct, both are accepted, and one supply-chain point is then counted twice.** The field table says "Null is treated as `standard_mix`" — true of the *factor lookup* and of nothing else. Keeping them distinct is deliberate and follows from §5.4, which must be able to tell "did not break it down" from "chose the mixed figure", but the asymmetry reads as a bug from §6.2 alone and a front end should send one or the other consistently | §6.2 | **B, C** |

> **Still open after this revision.** **O-1** (real emissions factors) remains the hard blocker. **O-7 is still the only item on A's critical path** and is still the client's to settle. Carried forward unchanged: `landfill_diverted` as a real `metric` row, and the positive/negative semantic colour pair that C and D both need. v1.4's `other` question (change 3) is **closed**.

### v1.4 — 2026-08-09 (the conventions nobody wrote down, **affects everybody**)

Nothing here is a new requirement. Every row is a rule the system already had — instantiated in a fixture, emitted by a handler, relied on by a test — and that this document did not state, so a second implementer had no way to arrive at it except by reading someone else's code. **That is the whole class of defect this revision closes**, and it is the one a five-way parallel split produces most reliably: the shape of a thing gets contracted, the *convention inside the shape* does not, and the convention is what the next person has to reproduce exactly.

Three of them (1, 3, 4) were already load-bearing on somebody's unwritten work. One (9) is not closed here and must not be closed here — it changes what the client is told the calculator measures.

| # | Change | Section | Affects |
| --- | --- | --- | --- |
| 1 | **`GET /factors` now actually emits `source_note` and `data_quality`.** §6.3 has required them since v1.2 and `db/repository.py`'s `build_bundle_data` never selected them, so for two revisions the export published every value with its provenance stripped — the exact combination v1.1 added the columns to prevent, since it removes the defence and keeps the exposure. `build_bundle_data` is the single projection that §6.3 and §10.2's `bundle.json` are both built from, which is why one six-line fix serves both and why the omission hit both. The fixture was written to the contract and its test parked on a **strict** `xfail`, so the gap was a tracked failure rather than a silent one and the marker came off with the fix — the pattern worth repeating whenever a fixture has to lead the code | §6.3 | **B, D** |
| 2 | **§6.3's equivalence rows gain `name` and `sort_order`.** Both were emitted from the first commit and shown in no version of this section. The contract moved rather than the code because §10.2 **requires** both on a bundle equivalence row and the same projection produces both surfaces: removing them here means a second projection whose only job is to hide two harmless fields, and a second projection is a second thing to keep in step. `name` is the short label (`Kilometres driven`) — `label_template` is a whole sentence, so a consumer building a heading or a CSV column has nothing else, and hard-coding it is what §7.3a already rules out | §6.3 | **B, D** |
| 3 | **§3 gains rule 5: the `EquivalenceResult.label` interpolation format.** `{value}` renders as whole units, `ROUND_HALF_UP`, comma thousands separator — `Equivalent to driving 18,597 km`. **This was a convention `tests/fixtures/calculate_response.json` invented** to match §6.2's own samples, defined in neither §2.2, §4.3 nor §6.2, and A has to reproduce it in the engine byte for byte or every equivalence on the page is wrong in a way no test in this repository could see: `_assert_shape` compares JSON types and key sets, and `Equivalent to driving 18596.8200000000 km` is a well-formed string of the right type under the right key. Recording it in a test docstring was **not** enough — §0's rule is document, notify, fixture, all three. The rounding mode is the part that had to be written out rather than left to a default: the fixture test used `quantize(Decimal("1"))`, whose context rounds half to **even**, so it would have pinned the wrong rule the first time a value landed on a half. No value in the set does, so both modes passed; the test now names `ROUND_HALF_UP` | §3, §2.2 | **A, B, C, D** |
| 4 | **§9 gains the five codes `api/errors.py` has always emitted and this section never listed** — `NOT_FOUND` (404), `METHOD_NOT_ALLOWED` (405), `INTERNAL_ERROR` (500), `ENGINE_UNAVAILABLE` (503) and the residual `HTTP_ERROR` — **and the third key on a `details` entry.** §7.1 tells C to branch on `body.error.code`; a code from a closed set is one she can handle and a code from nowhere lands in whatever her default branch does, which is the difference between "the calculator is under maintenance" and a blank panel. `details[]` carries `field`, `issue` **and** `message`, the last of which `errors/validation_error.json` has always had and the sample never showed — a front end built from the sample alone renders the envelope's one generic message against every highlighted row and discards the only text that says what is wrong with that row. Also written down rather than fixed: `HTTP_ERROR` now names two different events, §7.1's client-side "response was not JSON" and this residual server code. They stay sharing a name because the front end's response to both is identical, and a reader who finds one string in two sections should not have to guess which is the mistake | §9, §7.1 | **B, C, D** |
| 5 | **§10 rewritten from the directory that now exists**, and gains §10.0, which names what enforces it. v1.2's §10 described two divergent sets on two unmerged branches and none in the tree; there is now one canonical set of thirteen files here. The new subsection separates the two kinds of check and says why neither substitutes for the other: `test_fixture_consistency.py` holds the fixtures against each other, against the arithmetic and against `admin/seed.py` without touching HTTP, while `test_api.py` holds them against real responses from the real app. The shape check cannot prove a number — which is precisely how a `stats.json` of three empty arrays survived two revisions while giving D nothing to build a page from. Stated as a standing rule, because change 3 is an instance of it: **anything whose correctness lives inside a string is invisible to `_assert_shape`** and needs an assertion, a contract line, or both | §10 | **all** |
| 6 | **§4.1 and §4.2 name the modules: `engine/bundle.py` and `engine/calculate.py`.** No version of this document said where `FactorBundle` or `calculate()` live, so both external callers — `db/repository.py`'s bundle factory and `api/engine_adapter.py` — searched two candidates each. A search is not a contract: it lets a layout this document does not describe work in the API and fail in the golden suite, which imports both the documented way. The `from engine import calculate` half was actively harmful — if `engine/calculate.py` existed but exported the function under another name it bound the *module object*, turning a start-up `ImportError` that names the missing thing into `TypeError: 'module' object is not callable` at the first public calculation. Both fallbacks are gone | §4.1, §4.2 | **A, B** |
| 7 | **§5.2: only a `published` factor set is cached.** The per-set partitioning stands and its reasoning is unchanged, but caching the draft as well satisfied that sentence literally while defeating the path it exists to serve. §8.1's CRUD screens write factor rows directly and have no reason to call `invalidate_factor_bundle` — only `publish_factor_set` and `rollback_to` do — so the first dry run of a draft pinned its numbers for the life of the process: a staff member edits a factor, re-runs the dry run, sees the old figure, and cannot distinguish that from a formula that ignores the column they just changed. That is the confusion `factor_source` was added to prevent, arriving by another route. Not caching is one branch in one function; the alternative is an invalidation hook in all eleven §8.1 views that whoever adds the twelfth has to remember | §5.2 | **B, E** |
| 8 | **§5.4 records, as open and as the client's, whether `other` is suppressed against itself.** A single sub-threshold bucket is republished verbatim under a new name — `count: 1` with its exact `total_kg` — which is a public statement that exactly one such calculation exists, with only its destination hidden. Raised as `docs/ToB_v2.0.md` S6 and required by no version of this contract. It is recorded rather than implemented because **each available fix breaks something else this document promises**: dropping the bucket makes §6.4's "shares sum to 1" false, since the denominator is computed before suppression; re-normalising inflates every remaining share by the suppressed mass, which is the overclaiming the merge-rather-than-drop rule exists to prevent; and folding it into the largest bucket hides a small number inside a big one. The trade is a real disclosure against a real distortion of the client's own chart, so it is not B's to take in a query or D's to take in a legend | §5.4 | **B, D** |
| 9 | **`docs/architecture.md` gains O-7: `prevention` is not the 100% offset §4.1 claims, and §4.1 now says so at the point of the claim.** `upstream` is keyed on `(sector, food_category, metric)` and cannot see the destination — §4.3's line variables carry neither the destination nor its group — so a line moved to `prevention` keeps its entry's full upstream factor, while `architecture.md` §4.1 describes `prevention` as all-zero and matching ReFED. Measured on the fixture: 800 kg `not_harvested` → `prevention` yields `net_benefit.co2e` of 96.0, the downstream delta only, where ReFED would also avoid 800 × 0.45 = 360.0. **79% of the benefit is missing, and upstream is the larger term for most categories** — so the failure is one-directional and lands on exactly the number the client's "wasting less" story is built from. §6.2's anti-inflation argument survives untouched: mass conservation is a property of the request. **Not resolved here.** O-7 states three options and who decides; the cheapest changes what the client is told the calculator measures, so it is the client's, and it must be settled before A writes the engine because the golden suite bakes in whichever answer is chosen | `architecture.md` §4.1, §10 | **A, B, E** |
| 10 | `write_audit`'s serialiser now emits UTC with a `Z` designator for every `datetime`, per §1.3. It appended `Z` to a naive value and left an aware one carrying `+00:00`, so one column serialised two ways depending on whether the object had round-tripped through MySQL — and a non-UTC aware value kept its own offset while still claiming compliance. Same normalisation as `api/serialization.wire()`, reimplemented rather than imported because `db/` may not import `api/` | §5.5, §1.3 | **B, E** |

> **Still open after this revision.** **O-1 remains the hard blocker** and everything still runs on mock factors, so the placeholder banner stays mandatory. **O-7 (change 9) is new and is on A's critical path** — it is the only item here that cannot be deferred past the start of engine work. Carried forward unchanged from v1.2: `landfill_diverted` as a real `metric` row (the client's), and the positive/negative semantic colour pair, which C and D both need and neither has written down. Added by this revision: whether `other` is suppressed against itself (change 8, the client's) — **closed in v1.5 change 3**, in favour of privacy, once the whole-branch review showed the cross-breakdown join that made it more than a single-bucket curiosity.

### v1.3 — 2026-08-09 (the blocklist reaches the API, **affects B, C, D and E**)

Two of the open items v1.2 recorded are now closed, and closed the same way, by the same change: the blocklist is applied to `/api/v1/` and the detection helpers it needed moved into a layer both callers can import. Nothing about a request or response shape changed — **§9.2's `BLOCKED` envelope is unchanged and is now actually emitted**, which is the part C and D care about.

| # | Change | Section | Affects |
| --- | --- | --- | --- |
| 1 | **Closed the v1.2 open item on `looks_automated`, `RequestRate` and `_client_ip`.** They are in `db/detection.py`; `admin/detection.py` is a re-export; `admin/protection.py` keeps `_client_ip` as an alias for `db.detection.client_ip`, which is the same object rather than a second copy. §8.3's recommendation was the one implemented, and its reasoning was the deciding one: two copies of a detection rule drift, and the copy that stops matching is the one nobody notices. Note that the stated rationale was slightly wrong in one particular — `db/detection.py` is not standard-library-only at runtime, because it imports `db.blocklist` for address normalisation. What actually mattered, and what holds, is that it needs nothing from `admin/` | §8.3 | **B, E** |
| 2 | **Closed v1.2's item #15: whether an in-memory rate-limit counter may be keyed on a raw address.** It may not. Both layers now key on §2.3's HMAC fingerprint, and `api/rate_limit.py` counts with the same `db.detection.RequestRate` the panel uses rather than being a third implementation. §6.5 sets its bar at persistence and a raw address in a process-memory dict cleared that bar, but the dict outlives the request that filled it, so the address is one this system holds — and two layers applying different privacy standards to the same data was never a defensible position for a calculator whose selling point is that it stores nothing about the visitor | §6.5, §2.3 | **B, E** |
| 3 | **§9.2's "checked before anything else" is now literally true, and is implemented as middleware rather than as a router dependency.** FastAPI solves a router's dependencies only after a request has matched a route, so a dead path under `/api/v1/` answered `404` while every live path answered `403` — which hands a blocked caller a working route scanner. Recorded in §9.2 because it is a requirement on the implementation, not a free choice | §9.2 | **B** |
| 4 | **Recorded the two deployment hazards the API inherits and cannot fix in process**, both of which make §6.5 and §2.3 silently do nothing: every caller arriving as a reverse proxy's address, and a deployment (`uvicorn --uds`) that gives the process no client address at all. Both are warned about at start-up or on first occurrence. The panel survives the first only because `_RATE_EXEMPT_PATHS` keeps its login handshake reachable; a public API has no login handshake, so there is no equivalent to build | §6.5 | **B, E** |
| 5 | **`SECRET_KEY` is required by the API, and must be the same value the panel uses.** Both derive the fingerprint key from it through the same `BLOCKLIST_INFO`; two different secrets mean a block made in the panel never matches at the API, with nothing raised on either side. `api/app.py` refuses to start without one rather than starting with a blocklist that does nothing | §2.3 | **B, E** |
| 6 | **Recorded that `looks_automated` is deliberately not applied to `/api/v1/`.** It is applied to `/admin` only. Scripting a public JSON API is a legitimate way to use it, and §6.3's CSV export exists to be fetched by a tool — refusing `curl` there would refuse a use this contract invites. Written down because a reader who found the shared module and not this line would reasonably assume the omission was an oversight | §8.3, §6.5 | **B, E** |

### v1.2 — 2026-08-09 (the merge of the two contract lines, **affects everybody**)

Two documents became one. Most of the work was mechanical; the twenty-three corrections below were not. **Most of them are defects that were already in the document before the merge** — the merge is what made them visible, by putting statements next to the statements they contradict. **Three (1, 2, 5) were live blockers on A's and B's integration work**: a section of this document that had gone two revisions without being updated, and that two people were about to code against.

A smaller group — 6's scenario filter, 20 and 21 — are defects the corrections themselves created, and they share one cause worth stating once, because it will recur every time this document is tightened. **Making a vague section specific makes every remaining omission in it load-bearing in a way it was not before.** Pre-merge §5.4 named no table, no column and no join, so it could not be implemented wrongly from §5.4 alone; the rewrite named all three and left out a scenario predicate, a nullable column's fallback, and one join. Each omission then read as a followable instruction for the wrong query. When you make a section precise, re-read it against the cases it now appears to answer.

| # | Change | Section | Affects |
| --- | --- | --- | --- |
| 1 | **The two forks are merged.** v0.10–v0.13 (`admin_panel`) and v1.0–v1.1 (`docs/contract-v1.0`) both revised the same document in parallel for two days, and both were live and unmarked. Nothing was dropped from either side except §6.2's placeholder note, which said only "this file has not been reconciled with the other fork yet" and which this commit falsifies. The cost of the fork is concrete rather than theoretical: `admin/calc_client.py` and `admin/dryrun_views.py` were written on the `admin_panel` branch **against the other fork's §6.2**, because that was the only place the shape they needed existed. Code was being written against a contract that was not in the tree it was being written in | all | **all** |
| 2 | **§3, §4.2 and §5.3 now describe a multi-entry calculation, which is what §6.2 has described since v1.0.** They did not. `CalculationRequest` was `current`/`alternative` with `sector_code` on `ScenarioInput`; `calculate()` returned one `ScenarioResult` pair; `upsert_submission` took that request. Meanwhile §2.3 had grown `submission_entry` and §6.2 sent `entries[]` and returned `totals`. v1.0's own change-log entry is marked "Affects **A, B and C**" and only two of the three sections were ever changed. **This is what "A and B are the most tightly coupled" costs when it goes wrong:** A builds an engine that structurally cannot produce `totals`, B has to invent the cross-entry aggregation signature with no contract to code against, and the two inventions meet for the first time at integration — where the golden suite cannot adjudicate, because the golden suite tests the engine A built | §3, §4.2, §5.3 | **A, B** |
| 3 | **`totals` is computed by the engine, not summed in the API adapter, and §4.2 now says so with the reasoning.** An adapter in `api/` that adds per-entry metric totals together is a *second* impact-calculation site — the same defect as the browser doing it, differing only in which process runs the arithmetic. It would put the page's headline figure beyond the reach of the golden suite, which exercises `calculate()` and nothing above it, and it would make a non-additive roll-up a code change in `api/` — "metrics are data, not code" broken in the layer least likely to be reviewed for it. The rules for what the roll-up does with an entry that has no alternative (its current figures count on both sides, so its net benefit is zero and mass is conserved) are written out, because that is the one behaviour §6.2 states in prose and nowhere in a signature. §4.2 also names the one §6.2 response field that has **no** §3 counterpart and cannot have one — `factor_source`, which the engine has no way to know because it is pure over a bundle and cannot see where that bundle came from. The API layer supplies it from the branch it took when resolving the bundle. Without that sentence the "no arithmetic" rule reads as forbidding B from populating a field §6.2 requires | §3, §4.2 | **A, B** |
| 4 | **§6.2 now requires an entry's two scenarios to describe the same mass, to within 0.010 kg. Nothing enforced it before, in any version.** The dual-scenario design rests on the rule: `architecture.md` §4.1 states that the `prevention` destination — all factors zero — exists so that "wasting less" is expressed by *moving* mass to it rather than by sending less of it, precisely so `net_benefit` cannot be inflated by assuming away the waste. **An implementer building from §6.2 alone permitted exactly what `prevention` was designed to prevent**, and nothing downstream would have surfaced it: an alternative that simply drops a 1,200 kg landfill line yields a large fictitious `net_benefit`, `totals.total_kg` reports the current scenario's mass only so the two figures are never both on the page, and the golden suite cannot catch it because it tests the engine against a fixed request and this is a property of the *request*. The tolerance is **absolute and derived, not chosen**: the front end rounds each alternative line independently to 3 dp (≤ 0.0005 kg each) and §6.2 already caps a scenario at 20 lines, bounding drift at 0.010 kg at any tonnage. A relative tolerance is looser than the defect at 5,000 t and tighter than the unavoidable rounding at 2 kg | §3, §6.2 | **B, C** |
| 5 | **§6.2's dry-run row named two of the three submission tables.** It said no `submission` or `submission_line` row is written; `submission_entry` was added between that sentence and now. An implementer following it literally writes **orphan `submission_entry` rows on every staff dry run** — and §5.4 aggregates `by_sector` and `by_food_category` over exactly that table, so the pollution lands in the public statistics `X-Dry-Run` exists to protect, while `total_calculations` stays flat and conceals it. Staff run dozens of calculations while tuning one formula | §6.2 | **B, E** |
| 6 | **§5.4 now carries the entry-aggregation rule that §2.3 attributes to it, and the scenario filter that nothing has ever stated.** §2.3's `submission_line` note asserts "statistics aggregate over entries, not submissions (§5.4)" — and §5.4's `get_public_stats` and `StatsBucket` said nothing about entries. B implements §5.4 from §5.4. Joining `by_sector` to `submission` instead counts a multi-stage food business once, as whichever stage it entered first: the query returns a plausible number, nothing fails, and the population the calculator is most useful to is the one it silently mis-describes. **The second half is worse and no version of this document has ever said it:** `submission_line.scenario` is `ENUM('current','alternative')` and both scenarios live in one table, so a `by_destination` group-by with no scenario predicate counts hypothetical lines as real waste — **`prevention`, the destination for waste that did not happen, becomes a bucket in the public chart**, every `total_kg` roughly doubles, and §6.4's "the cumulative total entered into this tool" is false on its face. All three breakdowns and `total_kg` read `scenario = 'current'` only. Also states the consequence D has to write copy around — `total_calculations` counts submissions while every bucket `count` counts entries, so the two figures on the statistics page differ by design and must not be presented as a breakdown of one another | §5.4, §6.4 | **B, D** |
| 7 | **§7 replaced with the eleven modules it now names — the nine of C's that are built, transcribed from her branch, plus D's two, which are still specifications: `charts.js` and `news.js` do not exist and Chart.js appears nowhere in the tree.** §7 named five, described two of those inaccurately, and omitted six — including `view.js`, which holds the escaping and formatting primitives D and E would each otherwise reimplement, and whose single `escapeHtml` is the reason that branch is XSS-clean. Two shapes changed **to match her code rather than the reverse**: a line is `{id, destination, qtyInput}`, where `id` survives a full re-render (`render()` replaces `main.innerHTML`) and `qtyInput` keeps the raw string so nothing rounds until it is sent. `unitPreset` and `unitCount` stay in this document as unmet requirements — the container-preset input was never built, `toKg` is imported by nothing — as does the `gwp_horizon` control | §7 | **C, D, E** |
| 8 | **Settled: the cross-entry destination breakdown is rendered per entry, from `entries[]`.** C's results page builds one combined destination tab by adding `by_destination[].value` across entries in JavaScript — the last §7.6 violation that could not be removed by reading a different field. The ruling costs no new field, no engine change and nothing on A's critical path, and it is the more truthful rendering: the same destination under two entries draws two different upstream factors and is genuinely two rows. Two front-end figures are **removed** rather than relocated, because no field exists to move them to — the percentage-change figure (defined in no version of this contract; `net_benefit` already carries it in the user's own units) and the `landfill_diverted` card (a metric with no row in the `metric` table, with the destination code hard-coded in JavaScript) | §6.2 | **A, C** |
| 9 | **§8.3's audit item is resolved, not open.** v0.13 recorded "an API-side block cannot audit itself today" as unresolved with B named as the decider. **Her branch had already resolved it** — `write_audit` and `_json_safe` sit in `db/repository.py`, where §5.5 has placed them since v0.3. There was never a decision to take, only a duplicate to remove, and leaving it open means the person who owns it goes looking for a choice that does not exist. The integration detail matters more than the bookkeeping: B's `_json_safe` recurses into nested dicts and lists and redacts at every level, E's `_scrub` handles top-level keys only — so a `password_hash` or `mfa_secret_enc` one level down inside a payload passes straight through E's copy into a table every staff member can read. **B's survives**; E's `date` handling folds in | §8.3 | **B, E** |
| 10 | **The submissions migration is `0008`, `down_revision = "0007"`.** `docs/ToB_v3.0.md` §1.3 says `0006` / `"0005"`, which was true when written; `0006` (comparison scenarios) and `0007` (`ip_block`) have landed since. Following it literally creates a **third Alembic head** — not a merge conflict but an ambiguous chain, and `upgrade heads` then fails partway through on a table that already exists. **MySQL DDL autocommits**, so there is nothing to roll back and the recovery is `DROP DATABASE`. Cheapest possible thing to get right; among the most expensive to get wrong, which is why it is in the contract rather than a task brief | §2.3, §8.3 | **B** |
| 11 | **§10 now records where the fixtures actually are.** This section calls them "the executable form of the contract", and **no branch a reader is likely to be standing on has any** — not `main`, not `admin_panel` (which owns this document), not `docs/contract-v1.0`. Two sets exist on two unmerged branches: B's twelve and C's ten, both in the pre-v1.0 flat shape, disagreeing with the contract and with each other. The `details[].field` split is the sharpest case — B's fixtures use Pydantic's dotted `current.0.qty_kg`, C's use the bracket form, v1.0 §9 ratified C's and extended it, so B's is the one that changes; until it does, her handlers and C's lookup keys can never bind. The canonical set lands **once**, in the v1.2 shape, with the B integration PR, and **starts from B's twelve rather than C's ten** — hers is the superset, already carries `factor_source`, and already has `calculate_response_single.json` and `errors/unauthorized.json`, which C lacks. C's `taxonomy.json` is the better content and should fill it. **`errors/blocked.json` is the only file genuinely absent everywhere**, and it is the one whose `details` is `null` rather than `[]` — every existing error fixture on both branches uses `[]`, which is how that §9.2 requirement gets implemented away | §10 | **B, C, D** |
| 12 | **`sector.details` is folded into `description`. No new column.** C's `taxonomy.json` adds a `details` string per sector and her UI renders it in an expander that falls back to `description` — so against a real API every sector shows its description twice. `description` is already `TEXT` and already carries user-facing prose; a second column means a migration, a schema change, a §6.1 field and one more thing for staff to keep in step, for a field the client has not asked for | §2.1 | **B, C** |
| 13 | **§6.3 carries `source_note` and `data_quality`; §10.2 states they are optional in `bundle.json` and must not be rejected.** v1.1 added the columns to both factor tables and `source_note` to `equivalence` on the stated ground that a calculator which cannot say which numbers are measured and which are borrowed cannot be defended in public — and then the only public surface that could say so, `GET /factors`, was left without them. Publishing the values and dropping the provenance removes the defence and keeps the exposure. On the bundle side the fields are optional and ignored: the engine computes nothing from provenance, but §8.2's **Save as regression case** writes a `bundle.json` straight out of a dry run, so `from_json()` must accept and ignore the keys rather than raise `BundleFormatError` on a bundle that is otherwise entirely valid | §6.3, §10.2 | **A, B** |
| 14 | **§8.1's list of CRUD models is no longer "eleven".** `comparison_scenario` and `comparison_scenario_line` (v0.10) and `ip_block` (v0.12) were each specified in §2 and §8.2/§8.3 without being added here — so the section that states "every write must produce an `audit_log` entry" named three fewer tables than the panel writes to, and an audit of that requirement against this list would have come back clean. `ip_block` is listed with the qualification that it is **not** generic CRUD: list-only, `role = admin`, `ip_hmac` never in `column_list`, created through a custom form and removed through an audited `unblock` action | §8.1 | **E** |
| 15 | **§6.5's "IP addresses are never persisted" contradicted §2.3.** v0.12 deliberately softened that absolute with the blocklist exception — an HMAC, never an address, only for a blocked caller — and §6.5 kept asserting the unqualified form, so the document stated a rule and its exception in two places at once. The absolute is the one that was wrong: a reader implementing §6.5 literally had grounds to call §2.3's table a contract violation. Also records as **open** the question underneath it, which is genuinely unsettled and B's: whether an *in-memory* rate-limit counter may be keyed on a raw address. `api/rate_limit.py` does; `admin/protection.py` uses the §2.3 fingerprint. Two layers currently applying different privacy standards to the same data is not a defensible position for a calculator whose selling point is that it stores nothing about the visitor | §6.5 | **B, E** |
| 16 | **`ip_block`'s "see the note above" now names its target.** The merge reordered §2.3 to put the three submission tables in dependency order, which moved `submission_entry` and `submission_line` in between — so a reference written when the two were adjacent pointed at whatever happened to precede it. It now names the E-8 privacy blockquote explicitly, which is the note that licenses the table's existence against the no-address rule and is the one thing a reader must not fail to find from here | §2.3 | **B, E** |
| 17 | **v0.11's standing instruction is discharged, and this entry closes it.** It asked for §8.2's corrected pre-publish-comparison wording to be applied to the unmerged `docs/contract-v1.0` branch as well. That branch did still carry the stale "old value, new value and change" / "differenced client-side" text, and the merge takes the corrected version because `contract-v1.0` never touched those lines. §8.2 now carries the correction. Recorded because an open instruction in a change log stays open until something says otherwise, and the next reader would spend their time confirming a done action | §0.1, §8.2 | **E** |
| 18 | **Stale cross-references and internal inconsistencies corrected.** Four sit on the path A follows between `FactorBundle.from_json()` and the file format it parses: three sites pointed at §10.1 for the `bundle.json` shape (it is §10.2) and one pointed at §10.2 for the golden suite (it is §10.1). Four more were found by reading the merged document end to end rather than as a diff — §2.3's `submission.token` expiry job pointed at §2.3 itself rather than at §5.3's `expire_tokens`; §7.3 cited a "§7.6.1" that has never existed in any version (it is §7.6 rule 1); §7.3a still called `improvement.js`'s per-line rounding drift "not resolved here" after change 4 resolved it, when §6.2's 0.010 kg tolerance is derived from that exact behaviour and accepts it — **on the boundary, and only while the 20-line cap holds**; and §6.3 said v1.1 added `data_quality` to `equivalence`, which it did not (§2.2 gives `equivalence` a `source_note` and no `data_quality`) | §2.3, §4.1, §6.2, §6.2.1, §6.3, §7.3, §7.3a | **A, B, C** |
| 19 | **`ip_hmac` is added to §5.5's `REDACTED_FIELDS`, which said three fields for four revisions after it stopped being three.** §8.3 has asserted since v0.12 that `write_audit` redacts `ip_hmac`; §5.5's literal — the v0.3 definition, written before the field existed — did not carry it, so the document stated a protection and its absence in two places at once. **The definition was the stale half, not the claim.** E-8's final review added it to `admin/audit.py`'s copy precisely because flipping `can_delete = True` on the blocklist screen would otherwise serialise a whole `IpBlock` row through `row_to_dict` and land the fingerprint in `audit_log`, which every staff member can read. It matters more after B's integration than before it: `db/repository.py`'s `write_audit` becomes the canonical one and `admin/audit.py` becomes a re-export (change 9), so **the copy that already redacts `ip_hmac` stops being the code that runs**, and an API-side automatic block writes its entry through this one. The reason is written in rather than left implicit — `ip_hmac` is not a credential like the other three; it is derived from a visitor's address, and §2.3 permits storing such a derivation in `ip_block` alone, not in a table with a wider audience | §5.5 | **B, E** |
| 20 | **§5.4 now says what happens to a NULL `food_category_id`: it groups into an explicit `unspecified` bucket, suppressed on the same threshold as any other, and never dropped.** This is change 6's lesson repeating one field over. Pre-merge §5.4 named no grouping column, so it could not be implemented wrongly from §5.4 alone; naming `submission_entry.food_category_id` made the nullability load-bearing, and §2.3 has always said NULL means the user did not break their waste down by type — a real answer, likely a common one, and one `StatsBucket.code` had no legal value for. Dropping the bucket does not remove a number from the page, it **inflates every other share on it**, in the direction of overclaiming. Also stated: do **not** resolve NULL to `standard_mix` here. The engine does that (§3, §6.2) because it needs a factor; the statistics must not, because it would report a composition the user never claimed and make `standard_mix`'s share indistinguishable from the users who chose it | §5.4, §6.4 | **B, D** |
| 21 | **§5.4's `excluded_from_public` exclusion named a join path one table short of the column it filters on.** Third instance of the same class, found by re-reading §5.4 against changes 6 and 20. `excluded_from_public` is on `submission` (§2.3); the breakdowns group over `submission_entry` and `submission_line`, and `by_destination`'s stated path stopped at "joined to its entry". Followed literally, **staff moderation applies to nothing** — the excluded submission's entries and lines are counted anyway, silently, which is the entire purpose of the flag defeated by a missing join. Every breakdown now joins up to `submission`, and the docstring says it goes one table further than its own grouping needs so nobody trims it back | §5.4 | **B** |
| 22 | Four smaller inconsistencies the end-to-end read turned up, all fixed: §4.2 said "one" §6.2 response field has no §3 counterpart when there are two (`token` is the other, from §5.3); the dry-run row said "no token is returned" while §6.2's prose said `token` is `null` — **settled as `null`, present as a key**, per §6.3's own rule for the same choice; §6.4's worked example had bucket counts summing to exactly `total_calculations`, demonstrating the equality on the page whose new paragraph explains why the two differ, and now carries 1,247 submissions across 1,600 entries plus an `unspecified` bucket so D can see one; and §2.3 still sent the reader to §8.3 for a reconciliation item change 9 had closed | §2.3, §4.2, §6.2, §6.4 | **A, B, C, D, E** |
| 23 | `docs/architecture.md` §9.1.1, cited by §8.3 for the protection design's operational detail, **is not on `main`** — it lands with PR #9, and until then resolves only on `admin_panel`. Said out loud at the citation. A cross-reference that dangles for a stated reason is a known state; one that dangles silently reads as an error in this document | §8.3 | **B, E** |

> **Still open after this revision.** None of these is a defect in the document; all of them are decisions nobody has taken. **O-1 remains the hard blocker** — the client has not supplied real emissions factors, so everything runs on mock data and the banner stays mandatory. Beyond it: whether an in-memory rate-limit counter may hold a raw address (#15, B's); whether `admin.detection.looks_automated`, `RequestRate` and `_client_ip` move into a shared layer or are duplicated in `api/` (§8.3, B's, and the four blocklist items in `api/` are all downstream of it); whether `landfill_diverted` becomes a real `metric` row plus a formula (#8, the client's); and the positive/negative semantic colour pair, which C and D both need and neither has written down.
>
> **Two of those four closed in v1.3** (above): the detection helpers moved to `db/detection.py`, and the rate-limit counter now keys on the §2.3 fingerprint in both layers. `landfill_diverted` and the semantic colour pair are still open. This paragraph is left as it was written rather than edited, because it is a record of what was true at v1.2.

### v1.1 — 2026-08-07 (raised by E, **affects B**)

Made while planning the factor screens, on the principle that the last thing to arrive should not be the thing that forces a migration.

| # | Change | Section |
| --- | --- | --- |
| 1 | `factor_upstream` and `factor_downstream` each gain `source_note` and `data_quality`, both nullable. The client has not supplied real factors, and the Otago 2025 baseline says data quality varies by an order of magnitude across the supply chain — primary-production loss rates are largely borrowed from Australian figures. `is_mock` is all-or-nothing and cannot express "these forty rows are solid and those twelve are borrowed". The columns exist now, empty, so real data arrives as an import rather than a migration. `data_quality` is free text, not an enum, for the same reason the destination groupings are a table. | §2.2 |
| 2 | `equivalence` gains `source_note`. Open item O-3 — the New Zealand sources for km driven, meal equivalents and showers are unsettled, and an equivalence with no stated basis is the figure most likely to be challenged in public. | §2.2 |

### v1.0 — 2026-08-07 (raised by E from reviews of B's and C's branches, **affects A, B and C**)

The first revision driven by reading other people's code rather than by writing the panel. Four of the five come from a real defect found on a branch.

| # | Change | Section | Affects |
| --- | --- | --- | --- |
| 1 | **A submission now carries one or more `entries`**, each a `(sector, food_category)` pair with its own scenario lines. The response carries engine-computed `totals` alongside per-entry results. Found by reviewing C's branch against B's: C's multi-entry UI sent one `POST` per entry sharing one session token, and §5.3's token upsert overwrote each row with the next — a five-row calculation persisted one row, while the client added the per-entry results together in JavaScript. The client now computes nothing; a multi-stage business is one submission and one request. | §2.3, §6.2 | **A, B, C** |
| 2 | **`UNIQUE` containing a nullable column does not prevent duplicates in MySQL.** NULLs compare distinct, so `factor_downstream`'s generic `food_category_id IS NULL` rows could duplicate without limit and the fallback lookup would pick one nondeterministically — wrong numbers, no error, nothing in the logs. A functional index over `COALESCE(food_category_id, 0)` is now required. **Raised by B during implementation**, with an integration test proving it. | §2.2, §2.3 | **B** |
| 3 | **`details[].field` is a bracket-indexed path** (`entries[0].current[1].qty_kg`), not Pydantic's native `loc` form. The two fixture sets on the team had already chosen different formats, and a mismatch makes field-level highlighting fail silently — the user only ever sees the generic banner. | §9 | **B, C** |
| 4 | `qty_kg` is limited to 3 decimal places, and the rule is now written down. B enforced it; it was in no version of this document, and `unit_preset.kg_per_unit` is `DECIMAL(12,4)`, so a container preset times a non-integer count lands on 4 places and returns a 400 the user cannot act on. | §6.2 | **B, C** |
| 5 | A `token` that does not resolve to a live submission is treated as absent and a new one is minted, rather than returning `VALIDATION_ERROR`. A stale `sessionStorage` value from an earlier deployment must not break the calculator. | §6.2 | **B** |

> **Still open after this revision:** §7's module list does not match what C actually built — `view.js`, `calculator.js`, `results.js`, `improvement.js`, `main.js` and `methodology.js` are not named there, and `view.js` in particular holds the shared escaping and formatting primitives that D and E will otherwise reimplement. C to supply the JSDoc; E to fold it in.

### v0.13 — 2026-08-09 (raised by E, **affects B, C and D**)

| # | Change | Section |
| --- | --- | --- |
| 1 | Added the `ip_block` schema table — columns, types and constraints — which §8.3 previously named without defining. **B needs this for the migration**, and the `CHAR(64)` / UNIQUE choices are load-bearing rather than incidental. | §2.3 |
| 2 | Added the `BLOCKED` (403) error code and its envelope, with `details` fixed at `null` and a `message` that never varies. Previously undefined, so B would have had to invent a code and C and D would each have handled it differently — and would likely have retried it as though it were `RATE_LIMITED`, which never succeeds. | §9, §9.2 |
| 3 | Stated that addresses are normalised inside `ip_fingerprint` (`ipaddress.ip_address(x).compressed`) and that unparseable input raises `InvalidAddressError` rather than being hashed. **B's middleware must not let that exception escape** on the request path. Before this, `" 203.0.113.9"`, `203.0.113.09` and the several spellings of one IPv6 address each fingerprinted differently from what the middleware computes, so a block appeared on the screen and stopped nobody, with nothing failing anywhere. | §2.3 |
| 4 | Replaced §8.3's "auditing is the caller's job" instruction — which, as written, told the API layer to do something the layering rule forbids — with an honest reconciliation item: `write_audit` lives in `admin/`, `api/` may not import it, so an API-side block cannot audit itself today. Named the resolution (move `write_audit`/`row_to_dict` to `db/`, where §5.5 already says they belong) and named B as the owner of the decision. Same for `looks_automated`, `RequestRate` and `_client_ip`, which are in `admin/` and which B's middleware needs: recommendation recorded, decision hers, and stated plainly as unresolved rather than pretending otherwise. | §8.3 |
| 5 | Stated that `python -m admin.cli rotate-key` now clears `ip_block`. An HMAC cannot be re-keyed, so every block previously survived a documented, supported `SECRET_KEY` rotation as an unreachable row that `is_blocked` never matched and `unblock` could never remove. | §2.3, §8.3 |
| 6 | Stated that `/admin/login` and `/admin/verify` are exempt from the rate limit (and from that check only). Behind a proxy with `PROTECTION_TRUSTED_PROXY` false — the shipped arrangement — every caller shared one bucket and refused requests were counted, so 1 request/second from any unauthenticated caller locked every administrator out remotely. | §8.3 |
| 7 | Recorded that nothing in this system ever shows staff a caller's address, so the manual-block form cannot supply its own input — it has to come from a proxy or platform access log. Worth confirming such a log exists before an incident. | §8.3 |

### v0.12 — 2026-08-08 (raised by E, affects E only)

| # | Change | Section |
| --- | --- | --- |
| 1 | Documented E-8's blocklist as an explicit, named exception to "no IP address, no user agent, no fingerprint of any kind is stored" — an HMAC of an address, keyed on `SECRET_KEY` with a named `info` string (`kaicalc-blocklist-v1`), stored only for an address a staff member or the automatic protection has blocked. | §2.3 |
| 2 | Corrected §8.3's "Blocklist" paragraph, which predated E-8 and described a table (`ip_blocklist`, CIDR-keyed, storing nothing) that does not match what was actually built (`ip_block`, HMAC-keyed). Left uncorrected, it directly contradicted the new §2.3 text added by this same version. | §8.3 |
| 3 | Added the blocklist screen (`/admin/ip-block/list`, `/admin/ip-block/block`, the `unblock` action) and the `python -m admin.cli unblock <address>` operational command — the server-side way back in for an administrator who has blocked the address they are sitting behind, since the blocklist check exempts no one, not even an authenticated staff session. | §8.3 |

### v0.11 — 2026-08-08 (raised by E, affects E only)

| # | Change | Section |
| --- | --- | --- |
| 1 | Corrected §8.2's description of the pre-publish comparison view to match what was actually built: the published and draft values are shown side by side, per metric, with no difference computed — not "old value, new value and change" and not "differenced client-side" (both stale, from before Decision 6 was applied to this view). The stale wording claimed a client-side subtraction that would have put a number in front of staff no server-side calculation produced. **The same sentence exists on the unmerged `docs/contract-v1.0` branch (PR #10, v1.1) and needs the same correction there** — this change only touches the copy in this tree. | §8.2 |

### v0.10 — 2026-08-08 (raised by E, affects E only)

| # | Change | Section |
| --- | --- | --- |
| 1 | Added `comparison_scenario` and `comparison_scenario_line`. §8.2 named the pre-publish comparison view's standard scenarios as staff-editable but never defined where they live; these two tables are it, with their own CRUD screens under a new "Comparison" category. | §2.2a, §8.2 |

### v0.9 — 2026-08-07 (raised by E, affects E only)

| # | Change | Section |
| --- | --- | --- |
| 1 | Added `issue_password()`. §8.3 named "issues a random password" as half of an eviction; no function performed it. | §8.3 |

### v0.8 — 2026-08-06 (raised by E, **affects B**)

| # | Change | Section |
| --- | --- | --- |
| 1 | Added `staff.session_generation`. Removes the v0.7 known limitation: a credential change now ends the sessions that predate it. | §2.4, §8.3, §8.4 |

### v0.7 — 2026-08-05 (raised by E, **affects B**)

| # | Change | Section |
| --- | --- | --- |
| 1 | Recorded a known limitation: **changing a compromised account's password does not terminate its sessions, at any point.** Sessions carry no generation marker. Found while building the panel and reproduced end to end. **`require_staff()` returning a username does not mean that session has not been evicted** — B should not assume otherwise. | §8.3 |
| 2 | Recorded the gap underneath it: §8.3 names "issues a random password" as half of the eviction, but no service function performs it. `set_password` clears `must_change_password`, so an issued password leaves the account looking fully onboarded. | §8.3 |

### v0.6 — 2026-08-04 (raised by E, affects E only)

| # | Change | Section |
| --- | --- | --- |
| 1 | The administrator floor now guards on two counts, not one: active administrators, and *usable* administrators (active, MFA-enrolled, past the forced password change). Found in the final security review — bootstrap's two accounts are active but cannot log in, so the single count reported two usable administrators when a client who onboarded only one had exactly one. Deactivating the real administrator would have been permitted, leaving the panel owned by accounts nobody can access and no email path back. | §8.3 |

### v0.5 — 2026-08-04 (raised by E, affects E only)

| # | Change | Section |
| --- | --- | --- |
| 1 | The operational commands are subcommands of one module (`python -m admin.cli reset-mfa`) rather than three separate module entry points (`python -m admin.reset_mfa`). `--help` then lists every command in one place, and settings loading and session construction are written once. Caught during implementation review: `admin/security.py` was raising an error that told the operator to run `python -m admin.rotate_key`, a command that does not exist — and it fires precisely in the scenario where `SECRET_KEY` has been rotated and no authenticator works, which is the worst moment to hand someone an invalid instruction. | §8.3 |

### v0.4 — 2026-08-04 (raised by E, affects E only)

| # | Change | Section |
| --- | --- | --- |
| 1 | The application creates two administrator accounts on first start, with per-deployment random passwords printed once to standard output. A deployment now satisfies the two-administrator rule from the moment it comes up instead of depending on the installer running the CLI twice, and a system that starts with one administrator can be locked out by a single lost phone. **No default password exists anywhere in the source** — a fixed one on a public panel is exactly how community-sector accounts get taken over. | §8.3 |

### v0.3 — 2026-08-04 (raised by E, affects B)

Specifies staff authentication, which v0.1 named but did not define, and makes the audit-log requirement executable.

| # | Change | Section |
| --- | --- | --- |
| 1 | v0.1 §8.3 said "sqladmin built-in authentication". **There is no such thing** — `sqladmin` supplies an `AuthenticationBackend` abstract class and nothing else: no user store, no password hashing, no login page. §2.4 and §8.3 now specify all of it. | §2.4, §8.3 |
| 2 | Added the `staff` and `staff_recovery_code` tables. Owned by E, but carried in this document so that Alembic has a single migration chain — two chains will collide. | §2.4 |
| 3 | **MFA (TOTP) is mandatory for every account**, enrolled on first login and enforced before any other admin route is reachable. New Zealand community organisations have had credential-stuffing incidents; a public admin panel with password-only authentication is not defensible. | §8.3 |
| 4 | Two roles, `admin` and `staff`. Publishing and rollback are available to both — `audit_log` plus one-click rollback already provide accountability and recovery, and gating them behind an administrator would stall routine work in a three-to-five person team. Account management and MFA resets are administrator-only. | §8.3 |
| 5 | **No email system.** Account recovery is therefore three layers: recovery codes, another administrator, and a server-side CLI. At least two active administrator accounts must exist at all times, enforced in the service layer. | §8.3 |
| 6 | v0.1 §8.1 required all writes to go through "the repository functions in §5", but §5 contains **no write function for any of the eleven taxonomy and factor tables**, so the requirement was literally unexecutable. Replaced with a requirement on the outcome — every write produces an `audit_log` entry — plus a single insertion point, `write_audit()`. | §5.5, §8.1 |
| 7 | `write_audit()` carries a field blocklist. `audit_log` is readable by every staff member, so serialising a `staff` row into `before_json` would expose password hashes and TOTP secrets to anyone with an account — a real privilege-escalation path. | §5.5 |

**Unchanged:** nothing in §3, §4, §6 or §7. The public API is untouched.

### v0.2 — 2026-08-04 (raised by E, affects A and B)

Adds the dry-run capability the admin panel needs, and closes two holes in v0.1.

| # | Change | Section |
| --- | --- | --- |
| 1 | Resolved a contradiction: §4.2 said the admin dry-run page calls the engine directly, §8.2 said it calls the HTTP API. **The HTTP path wins**; the §4.2 docstring is corrected. Two paths would drift, and the point of a dry run is that it exercises what production exercises. | §4.2, §8.2 |
| 2 | `X-Dry-Run: true` now means exactly one thing — **do not persist** — and is enforced by staff authentication. v0.1 declared it "admin panel only" but specified no mechanism, so anyone could send it and compute without leaving a record. | §6.2 |
| 3 | `POST /calculate` gains an optional `dry_run` object carrying either a persisted `factor_set_version` or a complete inline `bundle`. Without it a dry run could only ever exercise the published factors, which defeats its purpose. | §6.2 |
| 4 | Defined the shape of `bundle.json`, which §10.1 named but never specified. It is now the single shape used by golden tests, `FactorBundle.from_json()` and dry-run requests. **It includes the taxonomy**, because §4.1's `has_destination()` / `has_sector()` / `standard_mix_code()` cannot be implemented without it, and because staff must be able to trial a new destination or food category before committing it. | §10.2 |
| 5 | Added `FactorBundle.from_json()` and `FactorBundle.validate()`. | §4.1 |
| 6 | Added error code `UNAUTHORIZED` (401); v0.1 had no authentication failure code at all. `FORMULA_ERROR` now has two presentations — opaque for the public, fully located for authenticated dry runs, because staff tuning a formula must be told where it broke. | §9 |
| 7 | Added `require_staff()` as the sole interface between the admin authentication system (E) and the API layer (B). | §8.4 |
| 8 | Added the pre-publish comparison view. | §8.2 |
| 9 | `load_factor_bundle` caching must be slotted by `factor_set_id` so that loading a draft cannot contaminate the published slot. | §5.2 |

**Unchanged:** the public request path (no header, no `dry_run`) behaves exactly as in v0.1 — C and D require no changes. No database table or column is added or altered. No §3 domain object changes.

---

# 1. Global Conventions

## 1.1 Naming

| Object | Convention | Example |
| --- | --- | --- |
| Table name | Singular, snake_case | `food_category` |
| Business code (`code` column) | snake_case, lowercase, globally stable | `anaerobic_digestion` |
| JSON field | snake_case | `qty_kg` |
| Python function or variable | snake_case | `load_factor_bundle` |
| JavaScript function or variable | camelCase | `renderDonut` |
| Constant code | UPPER_SNAKE | `GWP_CH4_100` |

**The `code` column is the cross-layer identifier.** API requests and responses use `code`, never the database `id`. The front end must never learn an auto-increment primary key.

## 1.2 Numbers

| Context | Type |
| --- | --- |
| Database | `DECIMAL`; **`FLOAT` and `DOUBLE` are prohibited** |
| Python | `decimal.Decimal`; **`float` is prohibited** |
| JSON | Decimals are transmitted **as strings** to avoid JavaScript double-precision loss (e.g. `"qty_kg": "1200.500"`) |
| JavaScript | Convert with `Number()` only for display; never used in a calculation |

> Sending decimals as JSON strings is deliberate. JavaScript's `Number` is a double, and `0.1 + 0.2 !== 0.3`. The front end only displays values, so converting to `Number` is safe for formatting and charting.

## 1.3 Time

Always UTC, ISO 8601 with a timezone designator: `2026-07-31T09:15:00Z`. The front end converts to local time for display.

## 1.4 Scenario Key

`scenario` has exactly two legal values: `"current"` and `"alternative"`.

---

# 2. Database Schema (owner: B)

## 2.1 Taxonomy

### `destination_group`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | |
| `code` | VARCHAR(64) | UNIQUE, NOT NULL | `reuse` / `recycle_recovery` / `disposal` |
| `name` | VARCHAR(128) | NOT NULL | Display name |
| `is_waste` | BOOLEAN | NOT NULL | Per MfE: reuse is false, the rest are true |
| `sort_order` | INT | NOT NULL, DEFAULT 0 | |
| `active` | BOOLEAN | NOT NULL, DEFAULT TRUE | |

> Groupings are a table rather than a hard-coded enum because the MfE definition may be revised. The 2025 Otago baseline has already recommended moving bioprocessing from waste to reuse.

### `destination`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | |
| `group_id` | INT | FK → `destination_group.id`, NOT NULL | |
| `code` | VARCHAR(64) | UNIQUE, NOT NULL | `landfill`, `compost`, `animal_feed`, `anaerobic_digestion`, `prevention`, … |
| `name` | VARCHAR(128) | NOT NULL | |
| `description` | TEXT | NULL | User-facing explanation |
| `sort_order` | INT | NOT NULL, DEFAULT 0 | |
| `active` | BOOLEAN | NOT NULL, DEFAULT TRUE | |

> **`prevention` is the one destination code this system knows by name.** It expresses "waste avoided" and keeps the two scenarios mass-conserving. Three rules are stated in terms of it and none of them is optional: `admin/taxonomy_rules.check_prevention_intact` refuses any edit that would remove or deactivate it (or its group); §6.2 refuses it in a **current** scenario, because it is by construction the destination for waste that did not happen; and §5.4 reads the current scenario only, so it can never become a public statistic. The literal lives once, in `db/types.PREVENTION_CODE`, and `admin/taxonomy_rules` re-exports that object — `api/` may not import from `admin/` and needs the same string.
>
> **Its *downstream* factors are zero. Its upstream factors are not, and cannot be.** This blockquote read "with all factors set to zero" for five revisions and that is not true of the data model: `factor_upstream` is keyed on `(sector, food_category, metric)` and has no destination column, so a line moved to `prevention` keeps its entry's full upstream factor. See `architecture.md` **O-7**, which measures the gap and states the three options; it is unsettled and it is on A's critical path. Corrected here in v1.5 because this is the normative schema section, the first place a new reader meets the word, and the last site still asserting the original claim.

### `sector`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | |
| `code` | VARCHAR(64) | UNIQUE, NOT NULL | `primary_production`, `processing`, `wholesale_retail`, `consumer_household`, `consumer_hospitality`, `consumer_institution` |
| `name` | VARCHAR(128) | NOT NULL | |
| `description` | TEXT | NULL | The whole of the user-facing explanatory text, short or long. **There is no second `details` column** — see below |
| `sort_order` | INT | NOT NULL, DEFAULT 0 | |
| `active` | BOOLEAN | NOT NULL, DEFAULT TRUE | |

> **There is one description field, not two.** C's `tests/fixtures/taxonomy.json` added a `details` string per sector and her sector step renders it in an expandable panel, falling back to `description`. No version of this contract has ever defined `details`, so against a real API the expander shows every sector's `description` twice. **Ruling: fold the longer text into `description`.** `description` is `TEXT` and already carries user-facing prose; a second column means a migration, a schema change, an extra field in §6.1 and one more thing for staff to keep in step — for a field the client has not asked for. C's fixture drops `details` and merges its text into `description`; her expander reads `description`.

### `food_category`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | |
| `code` | VARCHAR(64) | UNIQUE, NOT NULL | The eight Otago baseline categories plus `standard_mix` |
| `name` | VARCHAR(128) | NOT NULL | |
| `is_standard_mix` | BOOLEAN | NOT NULL, DEFAULT FALSE | Fallback when the user does not know the composition. **Exactly one row must be TRUE.** |
| `sort_order` | INT | NOT NULL, DEFAULT 0 | |
| `active` | BOOLEAN | NOT NULL, DEFAULT TRUE | |

> **Where the taxonomy invariants are enforced.** "Exactly one row must be
> TRUE" and the existence of `prevention` are statements about a table, not a
> column, so neither is a database constraint. Both are checked in
> `admin/taxonomy_rules.py`, called from `AuditedModelView`'s
> `validate_before_commit` hook — inside the transaction that is about to
> commit, before the audit entries are written. A refused change rolls back
> the row and its audit entry together. This placement is deliberate:
> `sqladmin`'s generic edit path never calls a service function, so a check
> that lives only in one is bypassed by the edit form.
>
> **"Exactly one row must be TRUE" means exactly one *active* row.** The
> check counts `is_standard_mix = TRUE AND active = TRUE`; a deactivated
> standard mix does not count towards the one, because it is as unusable to
> the engine as a missing one. Two rows may hold `is_standard_mix = TRUE` at
> once as long as only one of them is `active`.

### `metric`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | |
| `code` | VARCHAR(64) | UNIQUE, NOT NULL | `co2e`, `ch4`, `water`, `cost`, `mass` |
| `name` | VARCHAR(128) | NOT NULL | |
| `unit` | VARCHAR(32) | NOT NULL | Internal unit, e.g. `kg CO2e` |
| `display_unit` | VARCHAR(32) | NULL | Falls back to `unit` when null. **A presentation variant of `unit` at the same scale, never a different scale — see §6.1** |
| `display_precision` | TINYINT | NOT NULL, DEFAULT 2 | Decimal places |
| `sort_order` | INT | NOT NULL, DEFAULT 0 | |
| `active` | BOOLEAN | NOT NULL, DEFAULT TRUE | |

> **Adding a metric means inserting one row here, populating the factor tables, and writing one formula. No code changes.** This table is where Decision 2 actually lands.

### `unit_preset`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | |
| `code` | VARCHAR(64) | UNIQUE, NOT NULL | `bucket_20l_full` |
| `label` | VARCHAR(128) | NOT NULL | "20 L bucket (full)" |
| `food_category_id` | INT | FK, NULL | Null means it applies to all categories |
| `kg_per_unit` | DECIMAL(12,4) | NOT NULL | |
| `source_note` | TEXT | NULL | Basis for the conversion |
| `active` | BOOLEAN | NOT NULL, DEFAULT TRUE | |

## 2.2 Factors

### `factor_set`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | |
| `version_label` | VARCHAR(128) | UNIQUE, NOT NULL | `MOCK-v0 — PLACEHOLDER` / `2026-Q3` |
| `status` | ENUM | NOT NULL | `draft` / `published` / `archived` |
| `is_mock` | BOOLEAN | NOT NULL, DEFAULT TRUE | Triggers the site-wide warning banner |
| `effective_from` | DATETIME | NULL | |
| `published_at` | DATETIME | NULL | |
| `published_by` | VARCHAR(128) | NULL | Staff username |
| `notes` | TEXT | NULL | Provenance of the data |

**Constraint: at most one row may have `status = 'published'` at any time.** Enforced transactionally in the repository layer.

> **Where this is enforced.** In `admin/taxonomy_rules.py`'s
> `check_single_published_set`, called from `AuditedModelView`'s
> `validate_before_commit` hook — inside the transaction that is about to
> commit, after the flush so it sees the pending change, and before any audit
> row is written so a refusal leaves no record claiming it happened. The
> publish and rollback actions of §8.2 will additionally take `SELECT ... FOR
> UPDATE` over the table, because two staff members publishing different
> drafts at the same moment is a race this hook alone cannot settle.

### `factor_upstream`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | BIGINT | PK, AI | |
| `factor_set_id` | INT | FK, NOT NULL | |
| `sector_id` | INT | FK, NOT NULL | |
| `food_category_id` | INT | FK, NOT NULL | |
| `metric_id` | INT | FK, NOT NULL | |
| `value_per_kg` | DECIMAL(20,10) | NOT NULL | |
| `source_note` | TEXT | NULL | Where this number came from |
| `data_quality` | VARCHAR(32) | NULL | Free text, e.g. `measured` / `modelled` / `proxy-AU` |

UNIQUE(`factor_set_id`, `sector_id`, `food_category_id`, `metric_id`)

### `factor_downstream`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | BIGINT | PK, AI | |
| `factor_set_id` | INT | FK, NOT NULL | |
| `destination_id` | INT | FK, NOT NULL | |
| `food_category_id` | INT | FK, **NULL** | **NULL means the row applies to every food category for that destination** |
| `metric_id` | INT | FK, NOT NULL | |
| `value_per_kg` | DECIMAL(20,10) | NOT NULL | **May be negative** (an offset) |
| `source_note` | TEXT | NULL | Where this number came from |
| `data_quality` | VARCHAR(32) | NULL | Free text, e.g. `measured` / `modelled` / `proxy-AU` |

UNIQUE(`factor_set_id`, `destination_id`, `food_category_id`, `metric_id`)

> **Why every factor row carries its own provenance.** The client has not yet supplied real factors, and when they arrive they will not arrive uniformly: the Otago 2025 baseline states plainly that data quality varies by an order of magnitude across the supply chain, and that primary-production loss rates are largely borrowed from Australian figures. A calculator that cannot say which of its numbers are measured and which are proxies cannot be defended in public — and `is_mock` on the factor set is all-or-nothing, unable to express "these forty rows are solid and those twelve are borrowed".
>
> These columns exist now, empty, so that the arrival of real data is an **import** rather than a **migration**. `data_quality` is free text rather than an enum for the same reason the destination groupings are a table and not a hard-coded set: nobody yet knows which categories the client will use, and a column that must be altered to accept a new value puts us back where we started.

> **This UNIQUE does not do what it appears to, and a functional index is required.** MySQL treats NULLs as distinct in a unique key, so the constraint above permits unlimited duplicate rows for the generic case — the very rows where `food_category_id IS NULL`. The lookup below would then pick one of them nondeterministically, and the calculator would return different numbers for the same input with nothing in the logs to explain it. Add a unique index over `COALESCE(food_category_id, 0)` alongside the declared constraint, and test it by inserting the second generic row and asserting `IntegrityError`. The same caveat applies to `submission_entry` (§2.3) and to any other UNIQUE containing a nullable column.
>
> Raised by B during implementation, before it could produce a wrong answer in the field.

> The nullable `food_category_id` exists for cost items such as the waste levy, which are charged per tonne regardless of food type, so no special case is needed. Lookup order: exact match on `food_category_id` first, then fall back to the NULL row, then treat as zero.

### `constant`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | |
| `factor_set_id` | INT | FK, NOT NULL | |
| `code` | VARCHAR(64) | NOT NULL | `GWP_CH4_20`, `GWP_CH4_100`, `MEAL_KG`, `LEVY_NZD_PER_T` |
| `value` | DECIMAL(20,10) | NOT NULL | |
| `unit` | VARCHAR(32) | NULL | |
| `note` | TEXT | NULL | |

UNIQUE(`factor_set_id`, `code`)

### `formula`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | |
| `factor_set_id` | INT | FK, NOT NULL | |
| `metric_id` | INT | FK, NOT NULL | |
| `expression` | TEXT | NOT NULL | See §4.3 |
| `notes` | TEXT | NULL | |

UNIQUE(`factor_set_id`, `metric_id`)

### `equivalence`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | |
| `factor_set_id` | INT | FK, NOT NULL | |
| `code` | VARCHAR(64) | NOT NULL | `km_driven`, `meals`, `showers` |
| `name` | VARCHAR(128) | NOT NULL | |
| `source_metric_id` | INT | FK, NOT NULL | Which metric it converts from |
| `value_per_unit` | DECIMAL(20,10) | NOT NULL | Result = metric total × this factor |
| `label_template` | VARCHAR(255) | NOT NULL | `Equivalent to driving {value} km`. `{value}` is the only placeholder; everything else is copied verbatim. **The engine interpolates it, and §3's rule 5 fixes the number format** (whole units, comma thousands separator, `ROUND_HALF_UP`) |
| `source_note` | TEXT | NULL | Basis for the conversion. Open item O-3 — the New Zealand sources for km driven, meal equivalents and showers are not yet settled, and an equivalence with no stated basis is the figure most likely to be challenged |
| `sort_order` | INT | NOT NULL, DEFAULT 0 | |
| `active` | BOOLEAN | NOT NULL, DEFAULT TRUE | |

## 2.2a Comparison Scenarios

The standard test scenarios §8.2's pre-publish comparison view runs. A scenario is a saved `POST /calculate` request minus the factor set: a sector, a food category, a horizon and a set of destination lines. Rows, not a constant, per §8.2 — hard-coding them would reintroduce "change the code to change the configuration."

### `comparison_scenario`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | |
| `code` | VARCHAR(64) | UNIQUE, NOT NULL | |
| `name` | VARCHAR(128) | NOT NULL | |
| `sector_id` | INT | FK, NOT NULL | |
| `food_category_id` | INT | FK, NULL | Null means the standard mix — the same reading §6.2 gives the field |
| `gwp_horizon` | SMALLINT | NOT NULL, DEFAULT 100 | `20` or `100`, per §6.2 |
| `sort_order` | INT | NOT NULL, DEFAULT 0 | |
| `active` | BOOLEAN | NOT NULL, DEFAULT TRUE | |

### `comparison_scenario_line`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | |
| `scenario_id` | INT | FK → `comparison_scenario.id`, NOT NULL, ON DELETE CASCADE | |
| `destination_id` | INT | FK, NOT NULL | |
| `qty_kg` | DECIMAL(16,3) | NOT NULL | |

> Unlike the taxonomy tables, a scenario may be deleted through the panel: it is a staff member's own saved test case, referenced by nothing else in the schema, so deleting one strands no historical result.

## 2.3 Submissions

Three tables, written together by one `POST /api/v1/calculate` (§5.3). They are filed as a single Alembic migration, **`0008`, `down_revision = "0007"`** — see §8.3, and do not take the revision number from `docs/ToB_v3.0.md` §1.3, which predates two migrations that have since landed.

### `submission`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | BIGINT | PK, AI | |
| `token` | CHAR(36) | UNIQUE, NULL | UUID4 session token; nulled on expiry |
| `token_expires_at` | DATETIME | NULL | One hour after creation |
| `created_at` | DATETIME | NOT NULL | |
| `updated_at` | DATETIME | NOT NULL | Refreshed on upsert |
| `factor_set_id` | INT | FK, NOT NULL | Version stamp; makes results reproducible |
| `gwp_horizon` | SMALLINT | NOT NULL, DEFAULT 100 | Applies to the whole submission |
| `excluded_from_public` | BOOLEAN | NOT NULL, DEFAULT FALSE | **Staff moderation, not user consent** |
| `exclusion_reason` | VARCHAR(255) | NULL | |

**No IP address, no user agent, no fingerprint of any kind is stored.**

> **One exception, added deliberately in E-8.** The `ip_block` table stores an
> HMAC of an address — never the address — under a key derived from
> `SECRET_KEY`, and only for callers a staff member or the automatic
> protection has blocked. A database taken on its own yields no list of who
> visited. Nothing else is persisted: user agents, headers and paths are read
> within a request and forgotten. **Browser fingerprinting was considered and
> rejected** — ineffective against the traffic this defends against, and the
> highest privacy risk of the options.
>
> **One blocklist, owned by `db/`.** `ip_block` and the block/unblock/query
> functions live in `db/blocklist.py`, not in `admin/`. There is one list, and
> both callers read it: the API layer applies it to public traffic, the admin
> panel applies it to itself and provides the screen that manages it. Both
> derive the HMAC key from the same `SECRET_KEY` with
> `info = b"kaicalc-blocklist-v1"` — the same address must produce the same
> fingerprint on both sides, or a block applied in the panel silently fails to
> hold at the API.
>
> **Addresses are normalised before they are fingerprinted, inside
> `ip_fingerprint`.** `db.blocklist.normalise_ip` canonicalises through
> `ipaddress.ip_address(x).compressed`, so `" 203.0.113.9"`,
> `2001:db8:0:0:0:0:0:1` and `2001:db8::1` all reach the same row. It is inside
> `ip_fingerprint` deliberately, so every caller — the API layer, the panel,
> the CLI — inherits it without asking. An unparseable value raises
> `db.blocklist.InvalidAddressError` (a `ValueError` subclass) rather than being
> hashed: a fingerprint of nonsense writes a row that matches no caller and
> that `unblock` cannot remove either. **Callers on a request path must not let
> that exception escape** — `db.detection.client_ip` normalises the connection
> address itself and treats an unusable one as "no address". Both middlewares
> now go through it (`admin/protection.py` and `api/app.py`), and the API's
> also catches `InvalidAddressError` narrowly around an *injected* check, since
> that callable is not its own code. A caller with no usable address is skipped
> by both the blocklist and the rate limit rather than being given a stand-in
> key — see §6.5 for the deployment in which that becomes every caller.
>
> **Rotating `SECRET_KEY` clears the blocklist.** An HMAC cannot be re-keyed the
> way an encrypted TOTP secret can — there is no plaintext address left to
> re-fingerprint from, which is the property this whole design wanted. So
> `python -m admin.cli rotate-key` deletes every `ip_block` row and reports how
> many, rather than leaving rows that `is_blocked` would never match and
> `unblock` could never remove. Blocks must be re-applied after a rotation.
>
> Auditing: `db/blocklist.py` writes no audit entry. See §8.3's "Blocklist"
> section for who writes one instead. The reconciliation item this used to
> create for the API layer — that `write_audit` sat in `admin/`, which
> `api/` may not import — is **closed**: it has always been in
> `db/repository.py` on B's branch (v1.2 change 9). The question that was still open there
> — whether `looks_automated`, `RequestRate` and `_client_ip` move to a shared
> layer or are duplicated in `api/` — is **closed in v1.3**: they are in
> `db/detection.py`, and `admin/detection.py` re-exports them. See §8.3.

`sector_id` and `food_category_id` live on `submission_entry`, not here: one submission carries several, each with its own factors.

### `submission_entry`

One `(sector, food_category)` pair within a submission. A food business has waste at more than one point in the supply chain, and each point draws a different upstream factor, so they cannot share one set of lines.

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | BIGINT | PK, AI | |
| `submission_id` | BIGINT | FK, NOT NULL, ON DELETE CASCADE | |
| `sector_id` | INT | FK, NOT NULL | |
| `food_category_id` | INT | FK, NULL | Null when the user did not break waste down by type |
| `sort_order` | INT | NOT NULL, DEFAULT 0 | Preserves the order the user entered them, so `entries[]` in the §6.2 response can be paired with the rows on screen |

UNIQUE(`submission_id`, `sector_id`, `food_category_id`)

> The uniqueness constraint has the same MySQL NULL caveat as `factor_downstream` (§2.2): a nullable column in a UNIQUE key does not prevent duplicates, because NULLs compare distinct. Use a functional index over `COALESCE(food_category_id, 0)`.

### `submission_line`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | BIGINT | PK, AI | |
| `submission_entry_id` | BIGINT | FK, NOT NULL, ON DELETE CASCADE | |
| `scenario` | ENUM(`current`, `alternative`) | NOT NULL | |
| `destination_id` | INT | FK, NOT NULL | |
| `qty_kg` | DECIMAL(16,3) | NOT NULL, >= 0 | |

UNIQUE(`submission_entry_id`, `scenario`, `destination_id`)

> **Statistics aggregate over entries, not submissions** (§5.4). One submission with three entries is three sector observations, not one — otherwise a multi-stage business would be counted as whichever stage happened to be first. `total_calculations` still counts submissions.

### `ip_block`

Owned by B's layer (`db/`), built by E — see §8.3's "Blocklist", and the
**"One exception, added deliberately in E-8" blockquote under `submission`
earlier in this section**, which is what licenses this table's existence
against the no-address rule. No foreign keys: a block is not owned by a staff
row, and it must survive the account of whoever made it being deleted.

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | Never leaves the server; the panel selects rows by it, no API exposes it |
| `ip_hmac` | CHAR(64) | UNIQUE, NOT NULL | HMAC-SHA256 of the normalised address, hex. `CHAR`, not `VARCHAR` — always exactly 64 characters. UNIQUE is what makes `block_ip` an upsert rather than a source of duplicates |
| `reason` | TEXT | NOT NULL | Free text, shown on the screen and in `audit_log` **in place of** the address |
| `created_at` | DATETIME | NOT NULL | Naive UTC (§1.3). Not refreshed by a re-block — see below |
| `created_by` | VARCHAR(64) | NOT NULL | Staff username, or `cli`. Overwritten by a re-block |
| `expires_at` | DATETIME | NULL | NULL means it does not expire. `is_blocked` filters on this in SQL |

> A re-block of the same address updates `reason`, `created_by` and
> `expires_at` in place and leaves `created_at` alone, so the two can name
> different people at different times. The panel labels them "First blocked"
> and "Most recently blocked by" rather than "when" and "who" for that reason
> (`admin/blocklist_views.py`). A re-block with no duration also clears any
> existing `expires_at` — blank means permanent, not "leave as it was".

> Expired rows are filtered, never pruned. At this scale that is fine; a
> scheduled job can own it later, alongside the `submission.token` expiry job
> (`expire_tokens`, §5.3).

### `audit_log`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | BIGINT | PK, AI | |
| `at` | DATETIME | NOT NULL | |
| `actor` | VARCHAR(128) | NOT NULL | Staff username |
| `action` | VARCHAR(32) | NOT NULL | `create` / `update` / `delete` / `publish` / `rollback` |
| `table_name` | VARCHAR(64) | NOT NULL | |
| `row_id` | BIGINT | NULL | |
| `before_json` | JSON | NULL | |
| `after_json` | JSON | NULL | |

> Both JSON columns pass through the field blocklist in `write_audit()` (§5.5). `audit_log` is readable by every staff member, so an unfiltered dump of a `staff` row would hand out password hashes and TOTP secrets.

## 2.4 Staff and Access Control

Owned by E. Carried in this document so that Alembic keeps a single migration chain.

### `staff`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | |
| `username` | VARCHAR(64) | UNIQUE, NOT NULL | Written to `audit_log.actor` |
| `display_name` | VARCHAR(128) | NOT NULL | |
| `password_hash` | VARCHAR(255) | NOT NULL | bcrypt (`passlib`) |
| `role` | ENUM(`admin`, `staff`) | NOT NULL, DEFAULT `staff` | |
| `is_active` | BOOLEAN | NOT NULL, DEFAULT TRUE | |
| `must_change_password` | BOOLEAN | NOT NULL, DEFAULT TRUE | Set on creation and on an administrator reset |
| `mfa_secret_enc` | VARBINARY(255) | NULL | TOTP secret, encrypted at rest; NULL means not yet enrolled |
| `mfa_enrolled_at` | DATETIME | NULL | |
| `mfa_last_counter` | BIGINT | NULL | Last accepted TOTP time step; blocks replay within the window |
| `created_at` | DATETIME | NOT NULL | |
| `created_by` | VARCHAR(64) | NULL | |
| `last_login_at` | DATETIME | NULL | |
| `session_generation` | INT | NOT NULL, DEFAULT 0 | Bumped by every credential change; the session cookie carries the value it was minted under |

### `staff_recovery_code`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | |
| `staff_id` | INT | FK → `staff.id`, NOT NULL, ON DELETE CASCADE | |
| `code_hash` | CHAR(64) | NOT NULL | SHA-256 |
| `used_at` | DATETIME | NULL | Single use |
| `created_at` | DATETIME | NOT NULL | |

> **Recovery codes are hashed with SHA-256, not bcrypt, and this is deliberate.** bcrypt is slow in order to resist brute force against low-entropy human-chosen passwords. A recovery code is a high-entropy string we generate ourselves, so brute force is already infeasible and a slow hash buys nothing but latency.

> `mfa_secret_enc` is encrypted with a **sub-key derived from `SECRET_KEY` via HKDF-SHA256** (`info=b"totp-secret-encryption"`), not with `SECRET_KEY` itself — that key already signs session cookies, and reusing one key for two purposes is a defect waiting to happen. This protects the case where a database dump leaks on its own, which is the common one: a committed backup, a misconfigured export. It does not protect against losing the database and the key together.
>
> **Consequence: rotating `SECRET_KEY` invalidates every enrolled TOTP secret.** A rotation command (§8.3) must decrypt with the old key and re-encrypt with the new one. Without it, the day the client decides to rotate their key is the day nobody can log in.

---

# 3. Domain Objects (owner: A)

All are `@dataclass(frozen=True)` and live in `engine/types.py`. **The engine does not depend on SQLAlchemy**; the repository layer converts ORM objects into these types.

> **These types carry `entries`, and they did so from v1.0 onwards.** v1.0's change-log entry 1 is marked "Affects **A, B and C**", and it reshaped §2.3 and §6.2 — but §3, §4.2 and §5.3 were never brought into line and went on describing a single-entry calculation for two revisions. Corrected in v1.2. If you are holding an older copy, the tell is `sector_code` on `ScenarioInput` and a `CalculationResult` with `current` at the top level.

```python
from dataclasses import dataclass
from decimal import Decimal

# ---------- Input ----------

@dataclass(frozen=True)
class ScenarioLine:
    destination_code: str
    qty_kg: Decimal                 # >= 0

@dataclass(frozen=True)
class EntryInput:
    """One (sector, food_category) pair and both of its scenarios.

    Sector and food category sit here rather than on each scenario because
    §6.2 puts them on the entry: an entry's `current` and `alternative`
    describe the same point in the supply chain, and a wire request cannot
    express two different sectors for one entry. Putting them on the
    scenario would make an unrepresentable state representable."""
    sector_code: str
    food_category_code: str | None          # None -> use standard_mix
    current: tuple[ScenarioLine, ...]
    alternative: tuple[ScenarioLine, ...] | None

@dataclass(frozen=True)
class CalculationRequest:
    entries: tuple[EntryInput, ...]         # at least one; request order is preserved
    gwp_horizon: int = 100                  # 20 or 100; applies to the whole request

# ---------- Output ----------

@dataclass(frozen=True)
class BreakdownRow:
    destination_code: str
    qty_kg: Decimal
    upstream: Decimal               # per kg
    downstream: Decimal             # per kg, may be negative
    value: Decimal                  # this line's contribution to the metric total

@dataclass(frozen=True)
class MetricResult:
    metric_code: str
    unit: str
    display_precision: int
    total: Decimal
    by_destination: tuple[BreakdownRow, ...]    # empty at the totals level; see below

@dataclass(frozen=True)
class EquivalenceResult:
    code: str
    label: str                      # label_template already interpolated
    value: Decimal
    source_metric_code: str

@dataclass(frozen=True)
class ScenarioResult:
    total_kg: Decimal
    metrics: dict[str, MetricResult]        # key = metric_code
    equivalences: tuple[EquivalenceResult, ...]

@dataclass(frozen=True)
class EntryResult:
    sector_code: str
    food_category_code: str | None
    current: ScenarioResult
    alternative: ScenarioResult | None
    net_benefit: dict[str, Decimal] | None  # key = metric_code

@dataclass(frozen=True)
class CalculationTotals:
    """The cross-entry roll-up. Computed by the engine, never by a caller."""
    current: ScenarioResult
    alternative: ScenarioResult | None
    net_benefit: dict[str, Decimal] | None  # key = metric_code

@dataclass(frozen=True)
class CalculationResult:
    factor_set_version: str
    is_mock: bool
    gwp_horizon: int
    totals: CalculationTotals
    entries: tuple[EntryResult, ...]        # request order, one per EntryInput
```

**Five rules govern these types. Each is forced by §6.2 and none of them is A's to choose.**

1. **`entries` preserves request order.** §6.2 states it, and `submission_entry.sort_order` (§2.3) exists to persist it. It is what lets C pair a result with the row the user typed.
2. **`by_destination` is populated per entry and empty at the totals level.** §6.2: the same destination can appear under several entries drawing different upstream factors, so a cross-entry destination breakdown has no single correct aggregation rule. `MetricResult` is one type either way; at the totals level the tuple is empty and the serialiser omits the key. See §6.2 for the ruling on how the front end renders that breakdown.
3. **An entry with no alternative contributes its `current` result to `totals.alternative`.** This is what §6.2's "entries without one contribute zero to it rather than being excluded, so the totals stay mass-conserving" means in code: the entry's own `EntryResult.alternative` and `EntryResult.net_benefit` stay `None`, but the totals roll-up counts its current figures on both sides, so its contribution to `totals.net_benefit` is exactly zero and `totals.alternative`'s mass equals `totals.current`'s. Excluding it instead would make the alternative lighter than the current scenario and inflate net benefit — the precise failure the dual-scenario design exists to prevent.
4. **When *no* entry carries an alternative, `totals.alternative` and `totals.net_benefit` are both `None`,** and so is every `EntryResult.alternative` / `EntryResult.net_benefit`.
5. **`EquivalenceResult.label` interpolates `{value}` in exactly one format**, defined below. Until v1.4 it was defined nowhere, and §6.2's samples were the only evidence of it.

> **`EquivalenceResult.label`: the interpolation rule.**
>
> `label` is `equivalence.label_template` (§2.2) with the single placeholder `{value}` replaced by the equivalence's own `value`, formatted as:
>
> | Aspect | Rule |
> | --- | --- |
> | Decimal places | **None.** Rounded to a whole number |
> | Rounding | `ROUND_HALF_UP`, on the `Decimal` — never through `float` |
> | Thousands separator | A comma every three digits: `18,597` |
> | Decimal separator | Not applicable; there is no fractional part |
> | Negative values | A leading `-`, same grouping. Possible: a metric total can be negative when a downstream offset dominates (§4.2) |
> | Anything else in the template | Copied **verbatim**. `{value}` is the only placeholder substituted, and any other brace sequence is literal text — `label_template` is staff-authored (§8.1) and must never behave as a format string |
>
> `Equivalent to driving {value} km` with `value = Decimal("18596.8200000000")` gives `Equivalent to driving 18,597 km`.
>
> **`value` itself is unaffected and is transmitted at full precision**, as a string, next to the label (§1.2). `label` is display text; `value` is the number. A consumer that wants a different presentation formats `value`, and no consumer re-derives `label`.
>
> **Why this is A's to produce and not C's.** §7.6 rule 1: the browser computes nothing. Rounding is arithmetic — a client that formatted the label itself would be the second place a number is turned into the figure a user reads, and the golden suite (§10.1) could not cover it. It is also the client's approved wording (§7.3a warns against C's hard-coded equivalence labels for the same reason).
>
> **Why it needs stating at all, in these words.** This is a convention that `tests/fixtures/calculate_response.json` instantiated to match §6.2's samples, which show `21,400` and `14,500`. Neither §2.2, §4.3 nor §6.2 defined it, so the fixtures C and D build against carried a format A had no way to know he had to reproduce — and the mismatch would be invisible to every test in the tree: `_assert_shape` compares types, not text, and a `label` reading `Equivalent to driving 18596.8200000000 km` is a well-formed string of the right type in the right key. The half-up rule in particular is **not** pinned by any fixture, because no fixture value lands on a half; it is stated because half-even would silently round `2.5` down and nobody would find out from a test.



> **`ScenarioInput` is gone.** It held `sector_code`, `food_category_code` and `lines`; those three now live on `EntryInput`, split across `current` and `alternative`. It is deleted rather than emptied down to a single `lines` field, because a surviving `ScenarioInput` is exactly what B's `api/engine_adapter.py` currently populates with `req.current.sector_code`, and a type that keeps its name while losing its meaning is the one an integration will keep using by accident.
>
> **`totals.total_kg` on the wire is `totals.current.total_kg`** (§6.2 hoists it one level). It is the current scenario's mass, and one number is enough because §6.2 now **requires** each entry's two scenarios to describe the same mass to within 0.010 kg. That rule and this hoist depend on each other: the hoist is only honest while the rule holds, and the rule is only checkable at request time, because a response carrying one mass figure cannot expose a discrepancy between two. The alternative's per-entry mass remains available as `entries[].alternative.total_kg`. This is a wire-format hoist, not a fifth field on `CalculationTotals`.

---

# 4. Calculation Engine (owner: A)

Lives in `engine/`. **Pure functions: no database access, no file access, no system clock.**

## 4.1 `FactorBundle`

A fully loaded snapshot of one factor set. Constructed by the repository layer (§5.3) and treated as read-only by the engine.

**It lives in `engine/bundle.py`**, imported as `from engine.bundle import FactorBundle`. The module is named here because two callers outside `engine/` import it — `db/repository.py`'s default bundle factory and `api/engine_adapter.py`'s `bundle_from_json` — and until v1.4 no version of this document said where it was, so both searched `engine.bundle` and then `engine.types`. A search is not a contract: it lets a layout this document does not describe work in the API and fail in the golden suite, which imports it the documented way. `engine/types.py` is §3's module and holds the frozen dataclasses only.

```python
class FactorBundle:
    version_label: str
    is_mock: bool
    metrics: tuple[MetricSpec, ...]         # active only, sorted by sort_order

    def upstream(self, sector: str, food_cat: str, metric: str) -> Decimal:
        """Returns Decimal('0') when no row matches."""

    def downstream(self, destination: str, food_cat: str, metric: str) -> Decimal:
        """Exact match on food_cat first; then fall back to the generic row
        (food_category NULL); then Decimal('0'). May return a negative value."""

    def constant(self, code: str) -> Decimal:
        """Raises UnknownConstantError when not found."""

    def formula(self, metric: str) -> str:
        """Returns the default 'qty_kg * (upstream + downstream)' when not found."""

    def equivalences(self) -> tuple[EquivalenceSpec, ...]:
        """Active only, sorted by sort_order."""

    def has_destination(self, code: str) -> bool: ...
    def has_sector(self, code: str) -> bool: ...
    def has_food_category(self, code: str) -> bool: ...
    def standard_mix_code(self) -> str: ...

    @classmethod
    def from_json(cls, data: dict) -> "FactorBundle":
        """Build a bundle from the bundle.json shape defined in §10.2.
        Pure: no database, no file system, no clock. All decimals arrive as
        strings and are converted with Decimal(); float is never used as an
        intermediate. Raises BundleFormatError on malformed input."""

    def validate(self) -> list[str]:
        """Check internal consistency and return a list of human-readable
        problems; an empty list means the bundle is well-formed. Does not
        raise — the API layer decides how to present the problems.

        Checks: every upstream row's sector / food_category / metric exists
        in this bundle; every downstream row's destination / metric exists
        and its food_category is null or exists; every destination.group
        exists; exactly one food_category has is_standard_mix; every
        formula.metric and every equivalence.source_metric exists."""
```

> `from_json()` is required by the golden test suite regardless (§10.1 loads a `bundle.json` per case). Dry-run requests are simply a second caller of it. `validate()` exists because a bundle arriving over HTTP may be internally inconsistent in ways a database-loaded one cannot be; the rules are engine domain knowledge and are therefore implemented once, here, rather than duplicated in the API layer.

## 4.2 Entry Points

**`calculate()` lives in `engine/calculate.py`**, imported as `from engine.calculate import calculate`. Named for the same reason as §4.1's module: `api/engine_adapter.py` is the only caller outside `engine/`, and with nothing written down it tried `engine.calculate` and then `from engine import calculate`. The second form is the dangerous one — if `engine/calculate.py` exists but exports the function under another name, it binds the *module object* and the failure becomes `TypeError: 'module' object is not callable` at the first public calculation, rather than an `ImportError` at start-up naming what is missing. Re-exporting from `engine/__init__.py` is fine; relying on the re-export is not.

```python
def calculate(req: CalculationRequest, bundle: FactorBundle) -> CalculationResult:
    """
    Evaluate every entry's current scenario (and its alternative, if present),
    roll the entries up into totals, and compute net benefit at both levels.

    Parameters
      req    : A validated request carrying one or more entries. Every
               destination, sector and food_category code must exist in
               bundle, otherwise UnknownCodeError is raised.
      bundle : Factor set snapshot.

    Returns
      CalculationResult, carrying `totals` and `entries` (§3). `entries` is in
      request order. When no entry carries an alternative, totals.alternative,
      totals.net_benefit and every entry's alternative / net_benefit are None.

    Raises
      UnknownCodeError     : a code in the request is absent from bundle
      UnknownConstantError : a formula references a constant that does not exist
      FormulaError         : syntax error, division by zero, illegal identifier
      ValueError           : gwp_horizon is neither 20 nor 100

    Guarantees
      Pure. The same (req, bundle) always yields the same result.
    """
```

**`totals` is computed by the engine, not summed in the API adapter. This is a ruling, and it is not open.**

Impact calculation happens server-side **in exactly one place**, and the engine is that place. An adapter in `api/` that adds per-entry `MetricResult.total` values together is a *second* calculation site — structurally the same defect as C's browser-side `aggregateResults`, differing only in which process the arithmetic runs in. It would put a headline figure in front of a user that no golden case can cover, because the golden suite (§10.1) exercises `calculate()` and nothing above it. Three consequences follow directly, and they are the reason this is worth a paragraph rather than a sentence:

- The number a user sees on the results page would have no test.
- A metric whose roll-up is not a plain sum — a maximum, a threshold, anything the client asks for later — would need a code change in `api/`, which is "metrics are data, not code" broken in the one layer that is hardest to notice it in.
- `POST /calculate` and the golden suite would disagree about what the calculator computes, and only the API path would be wrong.

B's `serialize_result` therefore **maps** `CalculationResult` onto the §6.2 JSON and performs no arithmetic beyond the `totals.total_kg` hoist described in §3. It adds nothing, and it must not.

> **Two §6.2 response fields have no §3 counterpart, by design, and only one of them is interesting: `factor_source`.** (The other is `token`, which comes from `upsert_submission`'s return value, §5.3, and is `null` on a dry run — it was never a candidate to come from the engine, which has no concept of a session.) `factor_source` cannot come from the engine — the engine is pure over a `FactorBundle` and has no way to know whether that bundle was loaded from the published set, from a named version, or handed to it inline in a request body. **The API layer supplies it**, from the branch it took when it resolved the bundle (§6.2.1's four-row table): `"published"`, `"version:<label>"` or `"inline"`. That is a fact the API layer already holds and the engine never had, so it is not a second calculation site and does not weaken the rule above. `factor_set_version` and `is_mock` do come from the bundle and therefore from `CalculationResult`.

Within the engine, the roll-up rules are:

| Field | Rule |
| --- | --- |
| `totals.current.metrics[code].total` | Σ over entries of that entry's metric total |
| `totals.current.total_kg` | Σ over entries of `current.total_kg` |
| `totals.current.equivalences` | Computed **from the rolled-up metric total**, not summed from the per-entry equivalence values. The conversion is linear so the two agree mathematically, but `Decimal` has finite precision and one computation is one rounding |
| `totals.current.metrics[code].by_destination` | Empty (§3 rule 2) |
| `totals.alternative` | Same rules, over each entry's `alternative` — **or its `current` where the entry has none** (§3 rule 3) |
| `totals.net_benefit` | `net_benefit(totals.current, totals.alternative)` — computed on the rolled-up scenarios, not summed from the per-entry `net_benefit` maps |

```python
def net_benefit(current: ScenarioResult,
                alternative: ScenarioResult) -> dict[str, Decimal]:
    """Per metric: current.total - alternative.total.
    Only metric codes present on both sides are included.
    Applied at both levels: per entry, and to the rolled-up totals."""
```

**Everything below `calculate()` is A's to shape.** §6.2 determines the request, the result and the roll-up rules above; it says nothing about how the engine is decomposed internally. The v0.13 helper `calculate_scenario(scenario, bundle, gwp_horizon)` no longer type-checks — `ScenarioInput` is gone and a scenario can no longer supply its own sector — so a per-scenario helper now needs the sector and food category passed alongside the lines, for example:

```python
def calculate_scenario(lines: tuple[ScenarioLine, ...], sector_code: str,
                       food_category_code: str | None, bundle: FactorBundle,
                       gwp_horizon: int) -> ScenarioResult:
    """Evaluate one scenario of one entry. Internal to the engine."""
```

That signature is illustrative, not contractual. No caller outside `engine/` may depend on it; B calls `calculate()` and nothing else.

## 4.3 Expression Evaluation

```python
def evaluate_expression(expression: str, variables: dict[str, Decimal]) -> Decimal:
    """
    Restricted expression evaluation (built on simpleeval).

    Permitted
      Literals  : decimal numbers
      Operators : + - * / ( ) and unary minus
      Functions : min, max, abs, round
      Variables : only the keys present in `variables`

    Forbidden
      Attribute access, subscripting, function definitions, comprehensions,
      assignment, imports, and every builtin name

    Raises FormulaError (with line and column) on:
      syntax error / undefined variable / forbidden syntax /
      division by zero / non-finite result
    """
```

**Formula scope: an expression computes the contribution of a single line. Summation is performed by the engine.**

```
line_value   = evaluate_expression(expr, vars)
metric_total = Σ line_value
```

Variables available on each line:

| Variable | Type | Meaning |
| --- | --- | --- |
| `qty_kg` | Decimal | Quantity sent to this destination, in kilograms |
| `upstream` | Decimal | Upstream factor per kilogram |
| `downstream` | Decimal | Downstream factor per kilogram; may be negative |
| `const_<CODE>` | Decimal | A constant, as a flat identifier, e.g. `const_GWP_CH4_100` |
| `const_GWP_CH4` | Decimal | **Special binding:** resolves to `GWP_CH4_20` or `GWP_CH4_100` according to the request's `gwp_horizon` |

Because no aggregation is required, the expression language has **no arrays, no loops and no `sum()`**, which keeps the evaluator's security boundary unambiguous.

**Default formulas**

| Metric | Expression |
| --- | --- |
| `mass` | `qty_kg` |
| `co2e` | `qty_kg * (upstream + downstream)` |
| `ch4` | `qty_kg * (upstream + downstream) * const_GWP_CH4` |
| `water` | `qty_kg * upstream` |
| `cost` | `qty_kg * (upstream + downstream)` |

## 4.4 Exceptions

All defined in `engine/errors.py`, deriving from `EngineError`.

| Exception | Trigger | Mapped API error |
| --- | --- | --- |
| `UnknownCodeError` | Request contains a code absent from the bundle | `UNKNOWN_CODE` (400) |
| `UnknownConstantError` | Formula references a missing constant | `FORMULA_ERROR` (500) |
| `FormulaError` | Expression is invalid | `FORMULA_ERROR` (500) |
| `BundleFormatError` | `from_json()` received malformed input | `VALIDATION_ERROR` (400) |

`FormulaError` carries **structured attributes**, not a pre-formatted message:

```python
class FormulaError(EngineError):
    expression: str
    line: int
    column: int
    reason: str
```

The fields must be separable because §9 gives this error two presentations: opaque for public requests (the expression is never echoed), fully located for authenticated dry runs (staff tuning a formula cannot fix what they cannot see).

---

# 5. Repository Layer (owner: B)

Lives in `db/repository.py`. **The only code in the system that touches the database.** No other module may import SQLAlchemy.

## 5.1 Taxonomy

```python
def get_taxonomy(session) -> TaxonomySnapshot:
    """All active taxonomy rows, sorted by sort_order.
    Serialised directly by GET /api/v1/taxonomy."""
```

```python
@dataclass(frozen=True)
class TaxonomySnapshot:
    sectors: tuple[SectorSpec, ...]
    food_categories: tuple[FoodCategorySpec, ...]
    destination_groups: tuple[DestinationGroupSpec, ...]
    destinations: tuple[DestinationSpec, ...]
    metrics: tuple[MetricSpec, ...]
    unit_presets: tuple[UnitPresetSpec, ...]
    factor_set_version: str
    factor_set_is_mock: bool
```

## 5.2 Factor Sets

```python
def get_published_factor_set_id(session) -> int:
    """Raises NoPublishedFactorSetError when no version is published."""

def load_factor_bundle(session, factor_set_id: int | None = None) -> FactorBundle:
    """Loads the currently published version when factor_set_id is None.

    The result must be cached in a slot keyed by factor_set_id, and
    invalidated on publish or rollback. A single overwrite-on-load cache is
    not acceptable: staff repeatedly dry-running a draft would otherwise
    either evict the published bundle continuously, or — far worse — serve
    draft factors to a public request. Inline bundles supplied over HTTP
    (§6.2) are never cached; they differ on every request.

    **Only a `published` set is cached (v1.4).** A draft or archived set is
    rebuilt on every load."""

def publish_factor_set(session, factor_set_id: int, actor: str) -> None:
    """Within one transaction: archive the current published set, publish the
    target, write an audit_log entry, invalidate the cache. Rolls back if the
    'at most one published' invariant would be violated."""

def rollback_to(session, factor_set_id: int, actor: str) -> None:
    """Restores an archived version to published. Same semantics as publish."""

def clone_factor_set(session, source_id: int, new_label: str, actor: str) -> int:
    """Deep-copies a version into a new draft (all factors, constants,
    formulas and equivalences) and returns the new id. This is the
    recommended path for staff edits: clone, edit, publish."""
```

> **Why the draft slot exists but is not filled.** Partitioning by
> `factor_set_id` is what stops a dry run evicting the published bundle, and
> that requirement stands. Caching the draft *as well* satisfies the sentence
> above literally and defeats the thing the draft path exists to support:
> §8.1's CRUD screens write `factor_upstream`, `factor_downstream`,
> `constant`, `formula` and `equivalence` rows directly and have no reason to
> call `invalidate_factor_bundle` — only `publish_factor_set` and
> `rollback_to` do — so the first dry run of a draft pins that draft's numbers
> for the lifetime of the process. A staff member edits a factor, re-runs the
> dry run, sees the old figure, and cannot tell that from a formula that
> ignores the column they just changed. That is precisely the confusion
> `factor_source` (§6.2) was added to prevent, arriving by another route.
>
> The alternative fix — an invalidation hook on every admin write path —
> touches all eleven §8.1 views and has to be remembered by whoever adds the
> twelfth. Not caching is one branch in one function, and there is no load
> argument on the other side: a draft is dry-run by one staff member at a
> time, while the published set serves every public request.

> **Where these live today.** `admin/factor_lifecycle.py`, not
> `db/repository.py`. The admin panel needs them and the repository
> implementation is on an unmerged branch; when it lands, one implementation
> goes and the other is imported. `admin/` may import from `db/`, never the
> reverse. Cache invalidation is the repository's half and is not implemented
> in the admin copy.

## 5.3 Submissions

```python
def upsert_submission(session, token: str | None, req: CalculationRequest,
                      factor_set_id: int) -> tuple[int, str]:
    """
    Upsert keyed by token. One call, one submission, N entries.

    token is None, unparseable, or does not resolve to a live submission
                             -> insert a new row and mint a new UUID4 token.
    token is valid           -> overwrite that row and its whole entry set.

    `req` is a §3 CalculationRequest and therefore carries `req.entries`, not
    a single `req.current`. Writing the submission means writing three tables:

      submission        one row; stamps factor_set_id and req.gwp_horizon
      submission_entry  one row per req.entries[i], with sort_order = i so
                        the response's entries[] can be paired back to the
                        rows on the user's screen (§2.3)
      submission_line   one row per line, per scenario, per entry, keyed on
                        submission_entry_id

    On overwrite the entry set is rebuilt, not patched: delete every
    submission_entry for this submission (ON DELETE CASCADE takes the lines
    with it) and insert the request's entries in order. Patching in place
    would have to reconcile an entry the user removed against one they added,
    and the entries have no client-supplied identity to reconcile on.

    Returns (submission_id, token). The token is always returned so the
    front end can store it in sessionStorage.
    """

def expire_tokens(session, now: datetime) -> int:
    """Nulls the token column for rows whose token_expires_at is in the past,
    retaining the data. Returns the number of rows affected.
    Called by a scheduled job."""
```

## 5.4 Statistics

```python
def get_public_stats(session, threshold: int = 5) -> PublicStats:
    """
    Aggregate public statistics.

    - Excludes every entry and every line belonging to a submission with
      excluded_from_public = TRUE. That column is on `submission` (§2.3),
      so each breakdown below joins one table further than its own
      grouping needs — up to `submission`, not merely to the entry.
    - Merges any bucket with count < threshold into 'other'
    - Then, while 'other' is itself below the threshold, merges the
      smallest remaining visible bucket into it as well; if even that
      cannot clear it, returns NO buckets for that breakdown (v1.5)
    - Suppression happens here and is never delegated to the front end

    EVERY BREAKDOWN READS THE CURRENT SCENARIO ONLY.
    THE UNIT OF AGGREGATION IS THE ENTRY, NOT THE SUBMISSION (§2.3).

      by_sector         group over submission_entry.sector_id,
                        restricted to entries having current-scenario lines
      by_food_category  group over submission_entry.food_category_id, same;
                        a NULL food_category_id groups into an explicit
                        'unspecified' bucket -- it is never dropped
      by_destination    group over submission_line joined to its entry and
                        on to its submission,
                        WHERE submission_line.scenario = 'current'
      total_kg          summed over the same current-scenario lines
      total_calculations  counts submission rows

    A submission with three entries is three sector observations. Joining
    by_sector to `submission` instead — which is what a schema-driven reading
    of the old single-sector shape produces — counts a multi-stage food
    business once, as whichever stage it happened to enter first. That is the
    exact population the calculator is most useful to and least able to
    describe, and the query returns a plausible number either way, so nothing
    fails and nobody notices.
    """

@dataclass(frozen=True)
class StatsBucket:
    code: str          # taxonomy code, 'unspecified', or 'other'
    label: str
    count: int         # ENTRIES in this bucket, not submissions
    share: Decimal     # 0..1, of the entry count within this breakdown
    total_kg: Decimal  # CURRENT-scenario mass only

@dataclass(frozen=True)
class PublicStats:
    generated_at: datetime
    total_calculations: int      # SUBMISSIONS, not entries
    suppression_threshold: int
    by_destination: tuple[StatsBucket, ...]
    by_sector: tuple[StatsBucket, ...]
    by_food_category: tuple[StatsBucket, ...]
```

> **`submission_line.scenario` must be filtered, and this is the single easiest way to make the public statistics false.** The column is `ENUM('current', 'alternative')` (§2.3) and both scenarios' lines sit in the same table. A `by_destination` query that groups over `submission_line` without a scenario predicate — which is what the instruction above reads like if you stop before the `WHERE` — counts every hypothetical line as real waste. **`prevention` then appears as a destination in the public chart**, and `prevention` is by construction the destination for waste that *did not happen*; every `total_kg` roughly doubles; and §6.4's "the cumulative total entered into this tool" becomes false on its face, on the page whose whole design problem is not overclaiming. The alternative scenario is a user's what-if. It is not an observation of anything and it does not belong in a statistic.
>
> **A NULL `food_category_id` is a bucket, not a gap.** `submission_entry.food_category_id` is nullable and §2.3 already says what NULL means: the user did not break their waste down by type. That is a real answer about a real submission, and it is likely to be a common one — the calculator is aimed at businesses that mostly do not weigh their waste by food type. It groups into an explicit **`unspecified`** bucket, carrying the same `count`, `share` and `total_kg` as any other, and it is **subject to the same suppression threshold** as any other. It is never silently dropped: `share` is computed within its own breakdown and the shares must sum to 1, so discarding a bucket does not remove a number from the page — it inflates every other share on it, in the direction of overclaiming. D renders it with one rule, like every other bucket; only the label is special ("Not broken down by type").
>
> Note that `unspecified` is a *storage* fact, not a calculation one. The engine resolves a null `food_category` to `standard_mix` before it computes anything (§3, §6.2), so the same entry is `standard_mix` to the engine and `unspecified` to the statistics. Both are correct: one is the factor that was applied, the other is what the user actually told us. **Do not resolve NULL to `standard_mix` here** — that would report a composition the user never claimed, and would make `standard_mix`'s share indistinguishable from the users who selected it deliberately.
>
> **`other` is subject to the threshold like every other bucket, and clears it by absorbing more (settled in v1.5).** Until then, a single sub-threshold bucket was republished verbatim under a new name — `{"code": "other", "count": 1, …}` carrying its exact `total_kg` — which hid the label and nothing else. **In combination it was worse than alone**: `by_sector`'s `other` and `by_food_category`'s `other` are formed from the same entries, so an `other` of one published that user's exact tonnage in three breakdowns at once, and the three rows join on sight because the counts and masses are identical.
>
> The rule, in order:
>
> 1. Merge every bucket below the threshold into `other`, as before.
> 2. **While `other` exists and is itself below the threshold, merge the smallest visible bucket into it too**, and repeat. Smallest first, because it raises `other`'s count fastest per bucket surrendered and costs the page its least informative row first.
> 3. **If merging every bucket still leaves `other` below the threshold, publish no buckets for that breakdown at all** — an empty array, with `total_calculations` standing alone. That is a data set so small that every row in it is identifying, and there is no arrangement of it that is both useful and safe. `total_calculations` is a count of submissions and identifies nobody, so it still reports.
>
> **This does not break the shares property.** `other` remains an ordinary bucket carrying every suppressed entry, so the denominator — computed before suppression — is untouched and §6.4's "`share` … does sum to 1" holds exactly as before. An empty breakdown has no shares to sum. The alternatives all cost something the merge-rather-than-drop rule was written to protect: *dropping* `other` removes its entries from that denominator and makes the shares false; *re-normalising* inflates every remaining share by the suppressed mass, in the direction of overclaiming; *folding `other` into the largest visible bucket* hides a small number inside a large one and misattributes its tonnage. Absorbing costs one row of resolution and nothing else.
>
> Raised as `docs/ToB_v2.0.md` S6 and recorded as open in v1.4. Decided in favour of privacy: a statistics page that cannot describe its smallest cohort is a page with one fewer row, while a page that describes it is a page that identifies it.

> **`total_calculations` and the bucket counts are deliberately counting different things, and the statistics page must not present them as if they were not.** `total_calculations` is submissions; every `StatsBucket.count` is entries. `Σ by_sector[].count` is therefore ≥ `total_calculations`, and the gap is exactly the number of multi-entry submissions. `share` is computed within its own breakdown — over entries — so shares still sum to 1 and are the safe figure to display. Copy that reads "1,247 calculations" beside a sector chart whose counts add to 1,600 invites the obvious question; the honest phrasing names the unit ("1,247 calculations, covering 1,600 points in the supply chain"). See §6.4's copy constraint, which is D's.

## 5.5 Audit

```python
REDACTED_FIELDS = {"password_hash", "mfa_secret_enc", "code_hash",
                   "ip_hmac", "token"}

def write_audit(session, actor: str, action: str, table_name: str,
                row_id: int | None,
                before: dict | None, after: dict | None) -> None:
    """The only code that inserts into audit_log.

    Runs inside the caller's transaction: a change that is rolled back must
    leave no audit record claiming it happened.

    Every key in REDACTED_FIELDS is replaced with "[redacted]" before
    serialisation. audit_log is readable by every staff member through
    /admin/audit, so an unfiltered staff row would expose password hashes
    and TOTP secrets to anyone holding an account.

    ip_hmac is in that set for a different reason from the other three:
    it is not a credential, it is derived from a visitor's address, and
    §2.3 permits storing such a derivation in exactly one place — the
    ip_block table, which only administrators can read. audit_log has a
    wider audience, so the same value must not reach it.

    Decimal values serialise as strings, never as float (§1.2).
    """
```

> **Why `ip_hmac` is redacted, and why the list said three for four revisions.** The set above is the v0.3 definition; `ip_hmac` did not exist then. E-8's final review round added it to `admin/audit.py`'s copy, because flipping `can_delete = True` on the blocklist screen would otherwise serialise a whole `IpBlock` row through `row_to_dict` and write the fingerprint into `audit_log` — §8.3 has stated that this list carries it ever since, while this list did not. **The definition was the stale half, not the claim.**
>
> **Why `token` is redacted, and why it is here before the view that would leak it.** `submission.token` (§2.3) is a live session token. §8.2 specifies `/admin/submissions` as a record-level moderation screen that sets `excluded_from_public`; the moment it is built on `AuditedModelView`, `write_audit` snapshots the whole row and copies that token into `audit_log` — readable by every staff member, never expiring, and out of reach of `expire_tokens`, which nulls the column on `submission` and knows nothing about copies of it. **§2.3's guarantee is that the linkage is severed after an hour**, and a copy in an audit row makes that guarantee false for every moderated submission, permanently. Like `ip_hmac` it is not a credential; like `ip_hmac` it is the one field tying a stored row back to a person's browser session, which is the thing this system's stated design promises not to keep. Added in v1.5, ahead of the screen, because a redaction added after the screen ships is a redaction that arrives after the rows do.
>
> This matters more after B's integration than before it. `db/repository.py`'s `write_audit` becomes the canonical one and `admin/audit.py` becomes a re-export of it (§8.3), so E's copy — the one that already redacts `ip_hmac` — stops being the code that runs. An API-side automatic block writes its audit entry through **this** function.

Two callers, and no others:

| Caller | Writes |
| --- | --- |
| `AuditedModelView` (§8.1) | Every admin CRUD operation |
| `publish_factor_set` / `rollback_to` / `clone_factor_set` (§5.2) | Factor-set lifecycle operations |

> v0.1 required that "all writes go through the repository functions in §5", but §5 defines no write function for any of the eleven taxonomy and factor tables, so nothing could satisfy it. Adding thirty-odd boilerplate CRUD functions to the repository would not help either: they would have exactly one caller (the admin panel), and `sqladmin`'s `insert_model` / `update_model` / `delete_model` would all have to be overridden to route through them. The requirement is therefore stated on the outcome — every write produces an audit entry — with a single insertion point to keep the format uniform.

---

# 6. REST API (implemented by B; consumed by C and D)

Base path `/api/v1`. All responses are `application/json; charset=utf-8`.

## 6.1 `GET /api/v1/taxonomy`

Called once on page load to build every dropdown and input row.

**200 response**

```json
{
  "factor_set": { "version_label": "MOCK-v0 — PLACEHOLDER", "is_mock": true },
  "sectors": [
    { "code": "processing", "name": "Processing / Manufacturing",
      "description": "…", "sort_order": 2 }
  ],
  "food_categories": [
    { "code": "standard_mix", "name": "Standard mix (composition unknown)",
      "is_standard_mix": true, "sort_order": 0 }
  ],
  "destination_groups": [
    { "code": "disposal", "name": "Disposal", "is_waste": true, "sort_order": 3 }
  ],
  "destinations": [
    { "code": "landfill", "name": "Landfill", "group": "disposal",
      "description": "…", "sort_order": 1 }
  ],
  "metrics": [
    { "code": "co2e", "name": "Greenhouse gas", "unit": "kg CO2e",
      "display_unit": "kg CO2e", "display_precision": 1, "sort_order": 1 }
  ],
  "unit_presets": [
    { "code": "bucket_20l_full", "label": "20 L bucket (full)",
      "food_category": null, "kg_per_unit": "12.0000" }
  ]
}
```

> **`display_unit` is a presentation variant of `unit` at the same scale. It is never a different scale, and nothing anywhere converts between the two.** A typographic difference — `kg CO₂e` against `kg CO2e` — is what the column is for. It is not a unit conversion, and the example above is written with the two identical for that reason.
>
> **The rule is forced by §7.6.1 rather than chosen.** Every figure the front end prints comes from the API, and the only arithmetic it may perform is unit conversion on what the *user typed*, in `units.js`. So there is no layer that could divide a `kg CO2e` total by 1,000 on its way to a `t CO2e` label: the number would simply be relabelled, and every greenhouse-gas figure on the page would read a thousand times too small. §6.2 returns each metric total in `unit`, and a consumer that has both should prefer the `unit` travelling with the figure.
>
> **This was live in the fixtures.** `tests/fixtures/taxonomy.json` and `admin/seed.py` carried `t CO2e` against `kg CO2e`, `kL` against `L` and `t` against `kg` until 2026-08-09, and C's results table rendered "3,993 t CO2e" and "1,530,000 kL" one section below the same two figures labelled correctly. Both were corrected; the seed matters more than the fixture, because it is what ships to the database. **If a metric should be reported in tonnes, that is the metric's `unit` and the formula produces tonnes** — a metric's scale is a property of its formula, which is data (§2.1), not of a label.

## 6.2 `POST /api/v1/calculate`

**Calculates and persists. One call equals one submission** (Decision 8).

**A submission carries one or more entries.** A food business has waste at more than one point in the supply chain, and each point has its own sector, its own food category and therefore its own upstream factor — so they cannot be folded into a single set of lines. Each entry is one `(sector, food_category)` pair with its own scenario lines.

> **Why the whole submission travels in one call.** The alternative — one request per entry — breaks three things at once. `token` keys an upsert (§5.3), so the second entry would overwrite the first and only the last would survive. The client would have to add the per-entry results together itself, which puts an impact number in the browser that the engine never produced and that no golden case can cover (Decision 6). And one user action would cost N requests against a 120/hour limit. **Every number the user sees is computed server-side, including the totals across entries.**

**Request**

```json
{
  "token": "3f2b… (optional; omitted on the first call)",
  "gwp_horizon": 100,
  "entries": [
    {
      "sector": "processing",
      "food_category": "dairy",
      "current": [
        { "destination": "landfill", "qty_kg": "1200.000" },
        { "destination": "animal_feed", "qty_kg": "300.000" }
      ],
      "alternative": [
        { "destination": "anaerobic_digestion", "qty_kg": "1200.000" },
        { "destination": "animal_feed", "qty_kg": "300.000" }
      ]
    },
    {
      "sector": "primary_production",
      "food_category": "vegetables",
      "current": [ { "destination": "not_harvested", "qty_kg": "800.000" } ],
      "alternative": [ { "destination": "prevention", "qty_kg": "800.000" } ]
    }
  ],
  "dry_run": null
}
```

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `token` | string \| null | No | Session token; omitted on the first call. Any value that does not resolve to a live submission is treated as absent and a new one is minted — a stale `sessionStorage` value must not produce an error |
| `gwp_horizon` | int | No | 20 or 100; defaults to 100. Applies to the whole submission |
| `entries` | array | Yes | At least one entry |
| `entries[].sector` | string | Yes | Must exist in the taxonomy |
| `entries[].food_category` | string \| null | No | Null is treated as `standard_mix` |
| `entries[].current` | array | Yes | At least one line |
| `entries[].alternative` | array \| null | No | Null means no comparison is performed **for that entry** |
| `dry_run` | object \| null | No | **Staff only**; see §6.2.1. Requires `X-Dry-Run: true` |

If **any** entry carries an `alternative`, the response carries `net_benefit` at both levels; entries without one contribute zero to it rather than being excluded, so the totals stay mass-conserving.

> **The two scenarios of an entry must describe the same mass, and until v1.2 nothing enforced it.** The dual-scenario design rests on this: `architecture.md` §4.1 states that the `prevention` destination exists precisely so that `net_benefit` cannot be inflated by simply assuming less waste in the alternative — "wasting less" is expressed by *moving* mass to `prevention`, whose factors are all zero, not by sending less of it. That is a 100% offset and it conserves mass by construction.
>
> **The half of that sentence this rule depends on is the mass half, and it holds.** The "100% offset" half does not, as built — see `architecture.md` **O-7**: only `prevention`'s *downstream* factors are zero, and a prevented line keeps its entry's upstream factor. It is repeated here because it is the stated motivation for this rule, and the rule survives it: mass conservation is a property of the *request*, which O-7 does not touch. Do not read the phrase as a description of what the engine computes until O-7 is settled.
>
> **Without a rule, an implementer building from §6.2 alone permits exactly what `prevention` was designed to prevent**, and nothing downstream exposes it. An alternative that simply drops a 1,200 kg landfill line produces a large, entirely fictitious `net_benefit`. The response cannot reveal it: `totals.total_kg` reports the **current** scenario's mass only (§3), so the two figures a reader would compare are never both on the page. The golden suite cannot catch it either — it exercises `calculate()` against a fixed request, and this is a property of the *request*. The only place it can be caught is here.
>
> **The tolerance is absolute — 0.010 kg — and it is derived from this contract's own limits rather than picked.** The front end builds alternative lines by allocating percentages of an entry total and rounding each line independently to 3 decimal places (§7.3a, `improvement.js`), so each line carries at most 0.0005 kg of rounding error, and §6.2 already caps a scenario at 20 lines per entry. 20 × 0.0005 = 0.010 kg, and that bound holds regardless of the tonnage involved. A **relative** tolerance would be wrong in both directions: at 5,000 tonnes even 0.01% is 500 kg, which is looser than the defect this rule exists to catch, and at 2 kg it is tighter than the rounding the front end unavoidably produces, rejecting a legitimate request the user cannot fix.
>
> `details[].field` points at `entries[i].alternative` — the whole array, not a line, because no single line is at fault.

**Validation rules (enforced server-side)**

| Rule | On violation |
| --- | --- |
| `qty_kg >= 0` | `VALIDATION_ERROR` |
| `qty_kg` has at most 3 decimal places | `VALIDATION_ERROR` |
| Per line `qty_kg <= 10,000,000` | `VALIDATION_ERROR` |
| Per scenario total, per entry `<= 50,000,000` | `VALIDATION_ERROR` |
| Per scenario line count, per entry `<= 20` | `VALIDATION_ERROR` |
| Entry count `<= 20` | `VALIDATION_ERROR` |
| **Per entry carrying an `alternative`: `\|Σ alternative.qty_kg − Σ current.qty_kg\| <= 0.010`** | `VALIDATION_ERROR`, `field` = `entries[i].alternative` |
| **No `prevention` line in a `current` scenario** | `VALIDATION_ERROR`, `field` = `entries[i].current` |
| No duplicate `destination` within one entry's scenario | `VALIDATION_ERROR` |
| No duplicate `(sector, food_category)` across entries | `VALIDATION_ERROR` |
| All codes exist | `UNKNOWN_CODE` |
| `dry_run` present without `X-Dry-Run: true` | `VALIDATION_ERROR` |
| `dry_run.factor_set_version` and `dry_run.bundle` both non-null | `VALIDATION_ERROR` |
| `dry_run.bundle` row count across all tables `<= 5000` | `VALIDATION_ERROR` |
| `dry_run.bundle` fails `FactorBundle.validate()` | `VALIDATION_ERROR`, one `details` entry per problem |

> **Why `prevention` in a `current` scenario is a rejection and not a curiosity.** Until v1.5 nothing on the server refused it — only C's own UI, which never offers it in the current column. A hand-rolled request carrying it persists an ordinary `submission_line` with `scenario = 'current'`, and §5.4 selects exactly that, so the line becomes a bucket in the public `by_destination` chart. `prevention` is the destination for waste that *did not happen*; counting it as real waste is the failure §5.4's scenario predicate exists to prevent, arriving through the one door that predicate cannot close — the predicate excludes the alternative scenario, and this line is not in the alternative scenario. It also makes no sense as an input: the current scenario is a description of what a business is doing now, and "we sent 900 kg to not existing" is not a description of anything. `tests/api/test_fixture_consistency.py` asserted this of the *fixture*, which is what made it look covered; a fixture constrains the fixture.

> **`food_category: null` and `"standard_mix"` are the same thing to the engine and different things to the duplicate check.** Two entries with the same sector, one carrying `null` and one carrying `"standard_mix"`, are **both accepted** — the duplicate rule compares the values as sent. They then draw identical upstream factors, appear as two entries in the response, and count as two entries in §5.4's `by_sector`, so one supply-chain point is described twice. This is deliberate and it follows from §5.4, which keeps the two distinct on purpose: `unspecified` records that the user did not break their waste down, `standard_mix` records that they chose the mixed-composition figure, and collapsing them here would make the statistics unable to tell those apart. It is written down because it is the kind of asymmetry that reads as a bug — the field table two paragraphs up says "Null is treated as `standard_mix`", and that is true of the *factor lookup* and of nothing else. A front end should send one or the other consistently and never both for one sector.

**Request headers**

| Header | Purpose |
| --- | --- |
| `X-Dry-Run: true` | **Do not persist.** No `submission`, **no `submission_entry`** and no `submission_line` row is written — the three tables of §2.3 are untouched, not two of them. No token is minted, and the response carries **`"token": null`** — the key is present and null, never omitted, per §6.3's rule for the same choice. **Requires an authenticated staff session** (§8.4); an unauthenticated request carrying this header is rejected with `UNAUTHORIZED` (401). |

> `submission_entry` is named explicitly because it was added after this row was written and an implementer working from the older wording writes orphan entry rows on every staff dry run. Staff run dozens of calculations while tuning one formula, and `submission_entry` is what §5.4 aggregates `by_sector` and `by_food_category` over — so those orphans would land squarely in the public statistics this header exists to protect, while `total_calculations` stayed flat and hid it.

## 6.2.1 The `dry_run` Object

`X-Dry-Run` and `dry_run` are orthogonal: the header decides **whether the result is persisted**, the object decides **which factors are used**. All four combinations are legal.

| `X-Dry-Run` | `dry_run` | Scenario | Persisted |
| --- | --- | --- | --- |
| absent | null | Public calculation against the published set | **Yes** |
| `true` | null | Staff verifying live behaviour | No |
| `true` | `{ "factor_set_version": "…" }` | Comparing a persisted draft against published before publishing | No |
| `true` | `{ "bundle": {…} }` | Trialling changes that have not been saved anywhere | No |

```json
"dry_run": {
  "factor_set_version": "2026-Q3-draft",
  "bundle": null
}
```

| Field | Type | Notes |
| --- | --- | --- |
| `factor_set_version` | string \| null | `version_label` of a persisted set — draft, published or archived |
| `bundle` | object \| null | A **complete** factor set snapshot in the §10.2 `bundle.json` shape |

The two are mutually exclusive. When both are null the published set is used — the request is still not persisted.

> **Why a complete bundle rather than a diff against a base version.** A merge routine is new, untested code sitting between the staff member and the engine: when a dry run produces a wrong number, there is no way to tell whether the formula was wrong or the merge was. Diffs also have unpleasant edge cases — how does a generic `food_category: null` row merge with a specific one, and how is "delete this row" expressed? A complete snapshot has none of these questions. The volume does not justify the risk: roughly 270 upstream rows, 600 downstream rows and 20 others, around 90 KB uncompressed, and it travels behind authentication.

> **The bundle carries the taxonomy, not just the factors** (§10.2). Two reasons: §4.1's `has_destination()`, `has_sector()`, `has_food_category()` and `standard_mix_code()` cannot be implemented without it; and the client has stated that food categories, destinations and groupings will change over time, so staff must be able to trial a new destination before committing it — which is impossible if the bundle cannot carry its definition.

> This does **not** change how taxonomy is normally edited. Routine create/update/delete still goes through the admin CRUD of §8.1 and writes to the database. An inline bundle is a parallel, temporary channel that exists only for the lifetime of one request.

Because every dry-run request carries its own data, concurrent staff dry runs are isolated by construction. The path is stateless: no locks, no scratch tables, no TTL sweeper, and no orphan rows when someone closes the browser mid-edit.

**200 response**

```json
{
  "factor_set": { "version_label": "MOCK-v0 — PLACEHOLDER", "is_mock": true },
  "factor_source": "published",
  "gwp_horizon": 100,
  "token": "3f2b…",
  "totals": {
    "total_kg": "2300.000",
    "current": {
      "metrics": {
        "co2e": {
          "unit": "kg CO2e",
          "display_precision": 1,
          "total": "5118.0000000000"
        }
      },
      "equivalences": [
        { "code": "km_driven", "label": "Equivalent to driving 21,400 km",
          "value": "21400.0000000000", "source_metric": "co2e" }
      ]
    },
    "alternative": { "… same shape as current …" },
    "net_benefit": { "co2e": "2100.0000000000", "water": "0.0000000000" }
  },
  "entries": [
    {
      "sector": "processing",
      "food_category": "dairy",
      "current": {
        "total_kg": "1500.000",
        "metrics": {
          "co2e": {
            "unit": "kg CO2e",
            "display_precision": 1,
            "total": "3468.0000000000",
            "by_destination": [
              { "destination": "landfill", "qty_kg": "1200.000",
                "upstream": "1.9000000000", "downstream": "0.9900000000",
                "value": "3468.0000000000" }
            ]
          }
        },
        "equivalences": [
          { "code": "km_driven", "label": "Equivalent to driving 14,500 km",
            "value": "14500.0000000000", "source_metric": "co2e" }
        ]
      },
      "alternative": { "… same shape as current …" },
      "net_benefit": { "co2e": "2100.0000000000", "water": "0.0000000000" }
    }
  ]
}
```

**`totals` is what the headline figures are rendered from; `entries` is what the breakdown table is rendered from.** Both are computed by the engine. The client adds nothing together — it has no correct way to, because a decimal transmitted as a string (§1.2) cannot be summed in JavaScript without going through `Number`, and because the golden suite (§10.1) can only cover a number the engine produced.

`totals.current.metrics[code]` carries no `by_destination`: the same destination can appear under several entries with different upstream factors, so a cross-entry destination breakdown would need its own aggregation rule. If the client asks for one later, it belongs here as a new field the engine fills, not as a loop in the browser.

> **Settled: the destination breakdown is rendered per entry, from `entries[]`.** C's results page currently builds a single combined destination tab by looping over entries and adding `by_destination[].value` together in JavaScript — a §7.6 violation, and it is the last one that cannot be removed by reading a different field. The resolution is a rendering change, not a contract change: **one breakdown section per entry**, each read straight from `entries[i].current.metrics[code].by_destination`, labelled with that entry's sector and food category.
>
> This adds no field, requires no engine change and puts nothing on A's critical path. It is also the more truthful presentation: 1,200 kg to landfill from processing and 1,200 kg to landfill from primary production carry different upstream factors and are genuinely different rows, and merging them into one "landfill" bar hides the reason a multi-entry calculation was worth making. If the client later asks for a single combined view, it arrives as an engine-filled field with a stated aggregation rule — not as a loop in the browser, and not by reopening this.
>
> **Two front-end figures are removed rather than relocated, because no field exists to move them to:**
>
> - **The percentage-change figure** ("34.2% reduction", `improvement.js:153`, computed as `difference / |current| × 100`). No version of this contract has ever defined a percentage. It is a derived impact number computed in the browser, so it cannot stay; and it is not worth an engine field, because `net_benefit` already carries the same information in the unit the user entered. Removed. If it is wanted back, it is a metric-shaped request and goes through the engine.
> - **The `landfill_diverted` card** (`improvement.js:147`). It synthesises a metric that has no row in the `metric` table, and it hard-codes the destination code `'landfill'` in JavaScript — the two things "metrics are data, not code" exists to prevent. `docs/ToC_v1.0.md` §2.2 already ruled that if the client wants this figure it becomes a real `metric` row plus a formula. Removed meanwhile.

`entries[]` preserves request order, so a client can pair each result with the row the user typed. `submission_entry.sort_order` (§2.3) is what persists that order.

When `alternative` is not supplied, both `alternative` and `net_benefit` are `null`.

`factor_source` states which factors the engine actually used:

| Value | Meaning |
| --- | --- |
| `"published"` | The published set. Public responses are always this. |
| `"version:<label>"` | The named persisted set |
| `"inline"` | The bundle supplied in the request body |

Without this field a staff member who gets an unexpected number cannot tell whether their data failed to take effect or their formula is wrong. On a dry run `token` is `null` — present as a key, holding `null`, never omitted.

> **The front end must check `factor_set.is_mock`.** When true, a placeholder-data warning banner is mandatory in the results area.

## 6.3 `GET /api/v1/factors`

Factors and formulas are published openly (Decision 7).

| Query parameter | Notes |
| --- | --- |
| `version` | Version label; defaults to the currently published set |
| `format` | `json` (default) or `csv` |

**200 response (json)**

```json
{
  "factor_set": {
    "version_label": "MOCK-v0 — PLACEHOLDER", "is_mock": true,
    "published_at": "2026-08-05T02:00:00Z",
    "notes": "Placeholder data derived from ReFED (United States)"
  },
  "constants": [
    { "code": "GWP_CH4_100", "value": "28.0000000000", "unit": "", "note": "…" }
  ],
  "formulas": [
    { "metric": "co2e", "expression": "qty_kg * (upstream + downstream)", "notes": "" }
  ],
  "upstream": [
    { "sector": "processing", "food_category": "dairy",
      "metric": "co2e", "value_per_kg": "1.9000000000",
      "source_note": "Otago 2025 baseline, table 14",
      "data_quality": "measured" }
  ],
  "downstream": [
    { "destination": "landfill", "food_category": "dairy",
      "metric": "co2e", "value_per_kg": "0.9900000000",
      "source_note": null, "data_quality": "proxy-AU" }
  ],
  "equivalences": [
    { "code": "km_driven", "name": "Kilometres driven", "source_metric": "co2e",
      "value_per_unit": "4.1800000000",
      "label_template": "Equivalent to driving {value} km",
      "source_note": null, "sort_order": 1 }
  ]
}
```

**`source_note` and `data_quality` are part of this response, and both may be `null`.** v1.1 added `source_note` to `factor_upstream`, `factor_downstream` and `equivalence`, and `data_quality` to the two factor tables only — `equivalence` has no `data_quality` column (§2.2) — and this endpoint is the whole reason they exist: v1.1's stated rationale is that a calculator which cannot say which of its numbers are measured and which are borrowed cannot be defended in public, and §6.3 is the only public surface where a number can say so. A factor export that carries the values and drops their provenance publishes exactly the figure that is hardest to defend, with the defence removed. `null` is a legal value — most rows will carry `null` until the client supplies real data — and it must appear as `null`, not as an omitted key, so a consumer can tell "no provenance recorded" from "this endpoint does not report provenance".

> **Emitted since v1.4; the contract said so from v1.2 and the code did not.** `db/repository.py`'s `build_bundle_data` — the one projection this export and §10.2's bundle are both built from — did not select the three columns, so `GET /factors` published every value with its provenance stripped for two revisions after §6.3 required it. The fixture (`tests/fixtures/factors.json`) was written to the contract and its contract test parked on a strict `xfail`, which is what made the gap a tracked failure rather than a silent one; the marker came off with the repository fix.

**`name` and `sort_order` are part of the equivalence rows, and v1.4 added them here rather than removing them from the export.** They were emitted from the beginning and appeared in no version of this section. Three reasons the contract moved rather than the code. §10.2's `bundle.json` **requires** both on every equivalence row, and this response is produced by the same projection — dropping them here means writing a second projection whose only purpose is to hide two harmless fields, and a second projection is a second thing to keep in step. `name` is the short human label (`Kilometres driven`); `label_template` is a whole sentence, so a consumer building a heading, a legend or a CSV column has nothing else to use — D needs it and the alternative is hard-coding it, which §7.3a already rules out for the labels themselves. And `sort_order` is the display order §4.1's `equivalences()` promises; a consumer reading this endpoint directly would otherwise have to invent one.

With `format=csv`, one CSV file per table is returned, bundled as a zip archive (`Content-Type: application/zip`). The two provenance columns are columns in the `upstream` and `downstream` CSVs like any other.

## 6.4 `GET /api/v1/stats`

**200 response**

```json
{
  "generated_at": "2026-09-01T03:00:00Z",
  "total_calculations": 1247,
  "suppression_threshold": 5,
  "by_destination": [
    { "code": "landfill", "label": "Landfill", "count": 1268,
      "share": "0.4064", "total_kg": "884200.000" },
    { "code": "other", "label": "Other (sample too small)", "count": 9,
      "share": "0.0029", "total_kg": "3100.000" }
  ],
  "by_sector": [
    { "code": "processing", "label": "Processing / Manufacturing", "count": 604,
      "share": "0.3775", "total_kg": "521800.000" },
    { "code": "other", "label": "Other (sample too small)", "count": 7,
      "share": "0.0044", "total_kg": "2400.000" }
  ],
  "by_food_category": [
    { "code": "unspecified", "label": "Not broken down by type", "count": 742,
      "share": "0.4638", "total_kg": "612900.000" },
    { "code": "dairy", "label": "Dairy", "count": 231,
      "share": "0.1444", "total_kg": "168300.000" }
  ]
}
```

`total_calculations` counts **submissions**; every bucket `count` counts **entries** (§5.4). **The sample above is written so the difference is visible rather than hidden:** its 1,247 submissions carry 1,600 entries between them, so `by_sector` and `by_food_category` counts sum to 1,600, not to 1,247. `by_destination` sums higher again — 3,120 — because one entry lands in the bucket of every destination it used. (Each array above is abridged to two buckets for length; a real response carries every bucket that survives suppression, and it is against those full totals that the `share` values shown are computed.) `share` is computed within its own breakdown, against that breakdown's own total, and does sum to 1. **None of the three is a breakdown of `total_calculations`**, and one submission can carry up to twenty entries.

`by_food_category` shows the `unspecified` bucket (§5.4): entries whose user did not break their waste down by food type. It is an ordinary bucket — suppressed on the same threshold, counted and shared like any other — and it is expected to be one of the largest. It is not the same thing as `standard_mix`, which is what a user selects deliberately.

> **Two things D must build for, both of which the sample above does not show because a mature data set does not reach them.**
>
> 1. **`other` may be large.** It is subject to the threshold itself (§5.4), so when it would fall below it, it absorbs the smallest visible buckets until it clears. On a young data set `other` can be the biggest row in the breakdown. A legend that renders it as a footnote, or a chart that gives it a de-emphasised colour on the assumption that it is always small, will look wrong on the day the calculator opens — which is the day it is most likely to be screenshotted.
> 2. **A breakdown may be an empty array**, while `total_calculations` is non-zero. That is the case where even everything merged together stays below the threshold. It is not an error and not a loading state: render "not enough data yet to show this breakdown" and keep the page. All three breakdowns are independent, so one can be empty while another is full.

> **Copy constraint (owner: D).** The subject of the statistics page must be the calculator itself — "Across the 1,247 calculations run in this tool…" — and **never** "Distribution of food waste destinations in New Zealand". Prefer `share`; if `total_kg` is displayed it must be explicitly labelled as the cumulative total entered into this tool. Do not label a bucket `count` as a number of calculations — it is a number of supply-chain points entered, and the two figures on this page differ by design.

## 6.5 Rate Limits

| Endpoint | Limit |
| --- | --- |
| `POST /api/v1/calculate` | 120 / hour / IP |
| `GET /api/v1/*` | 600 / hour / IP |

Exceeding a limit returns `429` with `RATE_LIMITED`. Counters live in memory or Redis.

**No IP address is persisted by the rate limiter, and none is persisted anywhere in this system except the one exception §2.3 records.** That exception is `ip_block`, which stores an HMAC of an address — never the address — and only for a caller a staff member or the automatic protection has blocked. This sentence used to read "IP addresses are never persisted" without qualification; v0.12 added the blocklist exception in §2.3 and this line was not updated, so the document asserted an absolute and its exception in two places at once. The absolute is the one that was wrong: a reader implementing §6.5 literally would have had grounds to call §2.3's table a contract violation.

> **Resolved in v1.3 — an in-memory counter may not be keyed on a raw address either.** This was recorded as open and B's: §6.5 sets its bar at persistence, and `api/rate_limit.py` keyed on `"post-calculate:203.0.113.9"`, which cleared that bar, while `admin/protection.py` keyed on the §2.3 fingerprint because §2.3's prohibition has been read as covering process memory too. **Both layers now key on the fingerprint**, and both count with the same `db.detection.RequestRate`. The deciding argument was that the counter's dict outlives the request that filled it, so an address in a key is an address this system holds — and two layers applying different privacy standards to the same data was never defensible for a calculator whose stated selling point is that it stores nothing about the visitor. As the note said, both layers moved together.

**How the counters actually behave, since "in memory or Redis" above understates it.** Both are sliding windows, not fixed ones: a fixed window resets on a boundary, which lets a caller spend a full hour's allowance in its last second and another in the first second of the next — 240 calculations inside two seconds against a limit of 120 per hour. The API's limiter (`api/rate_limit.py`) does **not** count a refused request, so a caller who overshoots recovers one window after their last *allowed* request rather than never, and `Retry-After` is the wait until their oldest counted request leaves the window. `admin/protection.py` does count refusals, deliberately — see §8.3's `_RATE_EXEMPT_PATHS`, which exists to survive what that implies. Both counters are per-process: **running more than one worker multiplies the effective limit by the worker count.**

> **Two deployment facts silently disable this section, and neither has an in-process fix.** Both are warned about — the first at start-up, the second the first time it happens — and both are named in `docs/architecture.md` §9.1.1.
>
> 1. **Behind a reverse proxy with `PROTECTION_TRUSTED_PROXY` false, "per IP" above is a fiction.** That is the shipped arrangement (TLS terminates upstream) and `false` is the correct default — with no proxy that overwrites `X-Forwarded-For`, trusting it lets any caller claim any address, which is the worse failure. But every caller then arrives as the proxy's own address, so 600/hour becomes 600/hour for the whole internet, and one `ip_block` row denies every visitor at once. The panel survives this only because §8.3's `_RATE_EXEMPT_PATHS` keeps its login handshake reachable; **a public API has no login handshake to exempt, so there is no equivalent mitigation.** Set the flag true once the proxy is confirmed to overwrite the header itself.
> 2. **A deployment that gives the process no client address disables both this section and §2.3's blocklist.** `uvicorn --uds` behind nginx does exactly that (`scope["client"] is None`). There is then no address to fingerprint and none to count under, so both checks are skipped — the correct answer per request, since inventing a stand-in key collapses every such caller into one shared bucket and one shared blocklist entry, and a silent no-op in aggregate. Bind a TCP socket, or supply the address in `X-Forwarded-For` and set `PROTECTION_TRUSTED_PROXY=true`.

---

# 7. Front-End Modules (owners: C and D)

ES modules, no build step. Located in `web/js/`. `web/README.md` is the operational companion to this section — how to run the front end, and how to run it against the fixtures with no backend — and this document is the authority where the two disagree.

**Eleven modules, nine of them C's and built.** Until v1.2 this section named five and described two of those inaccurately — six real modules were absent, including `view.js`, which holds the escaping and formatting primitives D and E would otherwise each reimplement. The signatures below are transcribed from the branch, not proposed for it. Where C's code and the old contract disagreed on shape, **the contract has changed to match her code** and says so at the point of change; where a contract requirement is genuinely unmet, it is marked **Not built** and stays a requirement.

## 7.1 `api.js` (written by C, shared with D and E)

The only module that calls `fetch` — verified across the branch. **No other module calls `fetch` directly.**

```js
export class ApiError extends Error {
  constructor(code, message, details = [], status = 0);
  code;      // string — §9 error code, or 'NETWORK_ERROR' | 'HTTP_ERROR' | 'MOCK_FIXTURE_ERROR'
             // note: 'HTTP_ERROR' is now BOTH — this module's "response was not JSON"
             // and §9's residual server code. Same handling either way; see §9.
  message;   // string — display-ready
  details;   // Array  — field-level errors; [] when absent
  status;    // number — HTTP status; 0 when the request never completed
}

/** GET /api/v1/taxonomy   @returns {Promise<Taxonomy>} @throws {ApiError} */
export async function getTaxonomy();

/**
 * POST /api/v1/calculate
 * @param {CalculatePayload} payload   §6.2 body, carrying `entries`
 * @param {{dryRun?: boolean}} [opts]  dryRun === true -> X-Dry-Run: true
 * @returns {Promise<CalculationResult>} @throws {ApiError}
 */
export async function calculate(payload, opts = {});

/** GET /api/v1/stats   @returns {Promise<PublicStats>} @throws {ApiError} */
export async function getStats();

/** GET /api/v1/factors[?version=…]   @returns {Promise<Factors>} */
export async function getFactors(opts = {});
```

`api.js` owns URL construction, headers, JSON parsing, and converting any non-2xx response into a thrown `ApiError`, reading `body.error.{code,message,details}` with a fallback to a flat `body.{code,message,details}`. It distinguishes three failure modes — network unreachable, non-JSON response, structured API error — and they carry different messages.

**Mock mode is part of this contract, not a private convenience.** It is the substrate C, D and E all develop on while the backend is unmerged, so its behaviour is written down here rather than left to be rediscovered:

| Parameter | Effect |
| --- | --- |
| `?mock=1` | Every call is served from `tests/fixtures/` instead of the network. Read once at module load |
| `&mockError=<NAME>` | `POST /calculate` throws from `tests/fixtures/errors/<name>.json` (lower-cased); status 429 for `RATE_LIMITED`, else 400 |

Mapping: `/taxonomy` → `taxonomy.json`, `/factors*` → `factors.json`, `/stats` → `stats.json`, `POST /calculate` → `calculate_response.json`.

> **Current behaviour, not a requirement — do not reimplement this.** The mock path was rewritten for the `entries` / `totals` shape on 2026-08-09 and now reproduces §6.2 end to end: one request carrying `entries[]`, one response carrying `totals` beside a per-entry result in request order. It still **derives** two things in JavaScript rather than serving them verbatim — the `mass` metric, which is an identity (`value === qty_kg`, §4.3) and not a formula, and the `totals` roll-up, including the rule that an entry with no `alternative` contributes its current figures to the alternative side. Neither can come from a static file, because both are functions of a request whose entry count the fixture cannot know. Every other figure is the fixture's own. **This is `mockRequest`'s licence and nothing else's** — no module outside it may derive an impact figure (§7.6.1), and the numbers on screen in a mock demo are still partly browser-computed, which is the one property mock mode should not share with a bug.

**Mock mode constrains the document root, and this is not fixable in JavaScript.** The fixture URL is resolved against this module's own URL (`new URL('../../tests/fixtures/', import.meta.url)`), so it follows the page wherever the site is served from — that much was a real defect and is fixed. What remains is structural: a browser clamps `../` at the origin root, so the root **must be an ancestor of both `web/` and `tests/`**. `python3 -m http.server` at the repository root satisfies it; FastAPI serving `web/` as the static root does not, and every mock call 404s. C, D and E all develop in mock mode, so **B owns a dev-only static mount that exposes `tests/fixtures/`**; until it exists, mock mode runs only under the plain HTTP server.

## 7.2 `state.js` (written by C)

```js
/** Single mutable state object with a subscriber set. */
export const state;

/** Object.assign of the patch, then notify every subscriber. */
export function setState(patch);

/** @param {(s: typeof state) => void} fn @returns {() => void} unsubscribe */
export function subscribe(fn);

/** Clears the sessionStorage token and returns to the intro step. */
export function resetCalculator();

/**
 * Pairs the entries the user typed with the per-entry results §6.2 returns, which
 * preserve request order. Each paired `response` is one entry's `current` /
 * `alternative` / `net_benefit` plus the submission-level `factor_set`,
 * `factor_source` and `gwp_horizon`, which the rendering modules read
 * `is_mock` and `version_label` from.
 *
 * `state.result` carries both this and the whole response, so a consumer reads
 * cross-entry figures from `result.totals` (§7.6.1) and per-entry figures from
 * `result.entry_results` — never a sum over the latter.
 *
 * @param {Array<object>} entries   Draft entries, in the order they were sent
 * @param {object} response         The §6.2 response
 * @returns {Array<{entry: object, response: object}>}
 */
export function entryResultsFrom(entries, response);
```

Keys, grouped. **This is C's shape and the contract has adopted it**; the previous ten-key object in this section was a proposal that her code superseded.

| Group | Keys |
| --- | --- |
| Server data | `taxonomy`, `result` |
| Session | `token` — initialised from `sessionStorage.kaiCalculatorToken` at module load |
| Draft entry | `sector`, `foodCategory`, `gwpHorizon`, `totalAmount` (raw string), `totalUnit` (`'kilograms'` \| `'tonnes'`), `current: [{id, destination, qtyInput}]` |
| Multi-entry | `entries: []` — committed entries, same shape as the draft |
| UI | `step` (−1 intro … 5 results), `expandedSectors`, `resultBreakdownTab` (`'stage'` \| `'destination'` \| `'food'`), `lastChangedDestination` |
| Status | `loading`, `error`, `errorCode`, `fieldErrors: {fieldPath: message}`, `rateLimitedUntil` (epoch ms) |
| Improvement | `improvementOpen`, `improvedAllocations: {destinationCode: percentString}`, `improvementResult`, `improvementLoading`, `improvementError` |

> **Two of her decisions are better than what this section used to require, and are now the requirement.** A line is `{id, destination, qtyInput}`, not `{destination, qtyKg, …}`: the `id` is a stable identity that survives a full re-render, which matters because `render()` replaces `main.innerHTML` wholesale; and `qtyInput` holds the **raw string the user typed**, so no rounding happens until the value is converted for the API. The old `qtyKg` shape rounds on every keystroke, which is precisely the premature-decimal hazard §1.2 exists to avoid.

> **The table above is exhaustive as of 2026-08-09.** `alternative: []` and `compareAlternative: false` were also on the object — initialised, reset by `resetCalculator`, assigned `[]` by two functions in `calculator.js`, and **read by nothing.** The alternative scenario is built from `improvedAllocations` by `improvement.js`, which never looks at either. Both are removed. A key that is initialised and reset but never populated reads as a feature under construction, and the next person to need an alternative scenario would have wired theirs into a dead one.

**Still requirements, and still unmet:**

- **`unitPreset` and `unitCount` are absent from the line shape because the container-preset input was never built.** `taxonomy.unit_presets` is never read and `units.js`'s `toKg` is never imported. §7.3 remains a live requirement, not a documented omission. **What it will cost, so the next person is not surprised:** `toKg` returns kilograms at **three** decimal places (§7.3, API-ready) and `calculator.js` validates `totalAmount` against `/^\d+(\.\d{1,2})?$/`, so a preset whose `kg_per_unit` is not a whole number produces a total the form then refuses. `unit_preset.kg_per_unit` is `DECIMAL(12,4)` (§2.1) and every value in `tests/fixtures/taxonomy.json` happens to be integral, so the collision is invisible on the current fixture and certain on real data. Building the input therefore requires a decision — either the two-decimal rule moves, or the preset writes a rounded amount and `units.js` gains the rounding — and it is a decision about what a user is allowed to type, not a refactor.
- **`gwpHorizon` is set to 100 at initialisation and no control ever writes it.** §6.2 makes the horizon user-selectable between 20 and 100; a stated requirement is currently unmet and invisible on screen.

> **Documented exception to "no ad-hoc DOM manipulation".** Two modules deliberately bypass `setState` and mutate the DOM directly on keystroke (`calculator.js`, `improvement.js`). The reason is sound — `render()` replaces `main.innerHTML`, so a `setState` per keystroke destroys the focused input — but the exception must be documented rather than merely present, because the two fast paths **apply different validity rules to the same field**. As of 2026-08-09 the divergence is narrower than it was and is not zero: on a negative amount `updateLine` marks only the row being typed in, while `destinationRows` marks every negative row on the screen; and `destinationRows` additionally marks a row named by `state.fieldErrors`, which `updateLine` clears on the first keystroke because blanking or filling a row changes which lines the request would carry, so the server's line positions stop meaning what they meant. Any change to a validation rule has to be made in both.

## 7.3 `units.js` (written by C)

**All front-end mass arithmetic belongs in this module, and as of 2026-08-09 all of it is here.** That is the whole point of §7.6 rule 1: the front end's arithmetic can be audited in one file. It was not — `tonnes ? 1000 : 1` and `.toFixed(3)` were spelled out at six sites across `results.js`, `improvement.js` and `calculator.js` while `calculator.js` also called this module for the same conversion, so the front end held two copies of its only arithmetic rule and either could be changed without the other. The last of them moved here in the same revision that added `kgToTonnes`. **A `*`, `/` or `.toFixed()` on a mass anywhere else in `web/` is now a defect on sight.**

```js
/**
 * Convert a container count to kilograms.
 * @param {number} count       Number of containers
 * @param {string} presetCode  unit_preset code
 * @param {Array}  presets     taxonomy.unit_presets
 * @returns {string}           Kilograms as a string with 3 decimal places,
 *                             ready to send to the API
 * @throws {Error}             presetCode does not exist, or the product is
 *                             not finite
 * ** Still imported by nothing — see §7.2, the preset input is not built, and
 *    the note there states what building it costs. **
 */
export function toKg(count, presetCode, presets);

/**
 * @param {number|string} amount
 * @param {'kilograms'|'tonnes'} unit
 * @returns {number|null}  null when amount is not finite
 * Imported by calculator.js (the review step's kg figure) and improvement.js
 * (lineKg, which is every allocation percentage's denominator).
 */
export function massToKg(amount, unit);

/** massToKg(...) fixed to 3 decimal places, i.e. API-ready.
 *  @returns {string|null}  null when massToKg returns null.
 *  Imported by calculator.js (both line-normalising helpers) and
 *  improvement.js (currentLines). Note that kgString('', unit) is "0.000":
 *  a row the user has not filled is not a row holding zero, so the two
 *  callers that render blank rows keep their own '' check. */
export function kgString(amount, unit);

/**
 * Kilograms to tonnes, for display. The only arithmetic §7.6.1 permits on a
 * figure the API supplied, and therefore the only one of these functions whose
 * input is an API decimal string rather than something the user typed.
 * @param {number|string} kilograms
 * @returns {number}  NaN when the input is not finite, so an absent figure
 *                    reaches formatNumber() as absent rather than as zero
 */
export function kgToTonnes(kilograms);
```

> `kgToTonnes` was added on 2026-08-09 for `results.js`, which printed `totals.total_kg / 1000` inline at two sites — the summary card's "2.300 tonnes" note and the same line in the downloaded report. §7.6.1's exception is stated in terms of *this module*, and neither site was in it. It is a one-line function and it exists so the rule reads the same everywhere: **outside `units.js`, nothing divides, multiplies or adds a number the API supplied.** Bar and chart widths scaled against a local maximum are not figures and are not covered by this.

## 7.3a Calculator Modules (written by C)

Six modules that no version of §7 named. Transcribed from the branch.

### `view.js` — the shared primitives

**D and E consume this module rather than reimplementing it.** One `escapeHtml`, applied at every interpolation site, is the single reason C's branch is XSS-clean; a second copy in D's or E's code is a second thing to get right.

```js
/** &, <, >, " and ' -> entities. Safe for text and double-quoted attributes. */
export function escapeHtml(value = '');

/** An API figure printed at the metric's own precision. toLocaleString('en-NZ')
 *  with `precision` as BOTH minimumFractionDigits and maximumFractionDigits, so
 *  825.00 at display_precision 2 prints "825.00" rather than "825" one row above
 *  "1,204.50" in the same column of money.
 *
 *  `precision` arrives from the database (§2.1 metric.display_precision), not
 *  from this file, so it is clamped to Intl's legal 0-20: an out-of-range value
 *  makes toLocaleString throw a RangeError, which would take out the whole
 *  render rather than one figure.
 *
 *  Returns 'Not available' for a non-finite input — see the note below. */
export function formatNumber(value, precision = 2);

/** lower-case, non-alphanumerics -> '-', trimmed. For DOM ids and class names. */
export function slug(value);

/** HTML for the standard Back / primary-action pair. Emits
 *  data-action="go-step" data-step="<backStep>" and data-action="<action>". */
export function buttonRow(backStep, label = 'Continue', disabled = false, action = 'continue');
```

> **Precondition, stated because D and E will now depend on it:** `escapeHtml` does not escape backticks or `/`, so it is safe only in **double-quoted** attribute contexts and in text. Every attribute in C's branch is double-quoted. An unquoted attribute breaks the guarantee silently.
>
> **Precondition on `formatNumber`, and the reason the calling modules coerce their own inputs:** `Number(null)`, `Number('')` and `Number(undefined)` are `0`, `0` and `NaN`, so a plain `Number(value) || 0` makes "the engine did not return this metric", "this value is malformed" and "this value is zero" the same figure on screen. Every caller in `results.js` and `improvement.js` therefore maps absent to `NaN` before calling in, and `formatNumber` renders that as "Not available". **An absent figure has to read as absent** — a `|| 0` on an API figure is the defect this guards.
>
> The v1.2 "known gap" — `formatNumber` setting no `minimumFractionDigits` — **is closed**; the JSDoc above is the current behaviour.

### `calculator.js` — the wizard

```js
/** Writes the current screen into `main`. Handles the loading and
 *  taxonomy-failure screens; dispatches on state.step (−1 intro, 0-4 screens,
 *  5 delegates to results.renderResults). */
export function render(main);

/** Updates the header, the "Clear all data" button and the six-step
 *  progress indicator. */
export function renderChrome();

/** Installs four delegated listeners on `main` (click / change / input /
 *  keydown) and stores the taxonomy-reload callback the UNKNOWN_CODE path uses. */
export function bindCalculator(main, retryTaxonomy);
```

`data-action` vocabulary handled by the click delegate: `start`, `go-step`, `toggle-sector`, `clear-food`, `continue`, `add-entry`, `edit-entry`, `remove-entry`, `calculate`, `start-over`, `download-results`, `breakdown-tab`, `explore-improvements`, `reset-improvement`, `cancel-improvement`, `compare-improvement`, `retry`, `view-methodology`.

Module-private and worth knowing: `validateCurrentStep()` returns a display string or `''`; `buildLines(entry)` produces `[{destination, qty_kg}]` filtered to `qty_kg > 0`; `draftFieldPaths()` produces the §9 `field` path for each row of the draft entry, aligned with `state.current` and `null` for a row the request will not carry; `publicError(error)` maps a §9 code to user copy; `validationMessage(error)` and `describeDetail(detail)` build the 400 banner from the details that no row on screen can display; `fieldErrorMap(error)` turns `details[]` into `{fieldPath: message}`; `blocked()` and `clearedError()` implement §9.2's rule that `BLOCKED` is terminal; `submitCalculation()` issues the request.

> **`prevention` is excluded from the destination entry step and included in the improvement panel.** That modelling is correct and must survive any refactor — `prevention` is how the alternative scenario expresses waste avoided (§2.1), and offering it as a current-scenario destination would let a user claim to be already preventing what they are about to describe wasting.
>
> **The three silent failures this module used to have are fixed, and the shape of them is worth keeping.** `fieldErrorMap` keyed on the raw `details[].field` string while the render loop looked up `current[<index>].qty_kg` — §9's format is `entries[0].current[1].qty_kg`, so it never bound; the index was the position in `state.current`, which includes blank rows, while `buildLines` filters them out before sending, so the request index and the render index differed whenever any destination was left empty, which is the normal case; and `fieldErrorMap` stored the **envelope's** `message` against every field, so even a correctly bound row would have read "Request validation failed" while the server's own per-field prose was discarded. All three are silent by construction: no error, no console warning, only the generic banner. `draftFieldPaths()` exists to make the first two impossible to reintroduce independently — it derives the path from the same filter `buildLines` applies and roots it at `entries[state.entries.length]`, because the draft entry travels last.
>
> **`api/errors.py::bracket_path` is the server half of that agreement** and `tests/api/test_api_entries.py` asserts the exact string, so both ends of the `field` format are pinned by a test in one tree.

### `results.js` — the results screen

```js
/** The step-5 screen: placeholder banner (conditional on the submission-level
 *  factor_set.is_mock, §7.6.2), impact summary cards read from totals.current,
 *  tangible equivalents printed as the engine worded them, a three-tab
 *  breakdown (stage / destination / food) built per entry from entries[], a
 *  methodology-and-limitations block naming the factor version, action buttons,
 *  and the improvement panel.
 *  @returns {string} HTML */
export function renderResults(state);

/** Builds a plain-text report and triggers a Blob download as
 *  'food-waste-impact-results.txt'. Carries the placeholder notice only when
 *  factor_set.is_mock, and always the factor version (§7.6.2). */
export function downloadResults(state);
```

> **The client-side aggregation layer is gone.** This module summed engine-computed metric totals, equivalence values and destination rows across entries; the two largest numbers on the page were numbers the engine never produced. It now reads `totals` and `net_benefit` from §6.2, and the destination tab is rendered per entry per the ruling there. `aggregateResults` and `differenceData` no longer exist.
>
> The two hard-codings went with it: the breakdown columns are collected from the response's own key order (which §4.1 already sorts by `sort_order`), and the equivalence list prints `label` — the sentence the engine interpolated from `label_template` — rather than three English labels of its own for three hard-coded codes.
>
> **`mass` is named in this module, and that is not a §7.6.5 violation.** Rule 5 exists because a view listing `['co2e','water','cost']` *omits* the metric a staff member inserted; every metric the response carries still appears here. `mass` is held out of the impact cards and the breakdown columns because §3 hoists it — its formula is `qty_kg` (§4.3), so `scenario.total_kg` and `by_destination[].qty_kg` are the same figure, and it is already on screen as the primary card and the "Waste amount" column. `improvement.js` holds it out for a different reason, stated there. Both are single-code exclusions with a stated cause, not lists.
>
> **Bar widths are not figures.** A bar is scaled against the widest bar on the tab, across every section so two per-entry sections stay comparable, and no width is printed. §6.2 defines no share for an entry or a destination, so the percentage-of-total that used to sit beside each bar was a number the engine never produced.

### `improvement.js` — the alternative scenario

```js
export function currentAllocationPercentages(state);  // {destinationCode: number}
export function openImprovement(state);               // seeds from current allocation
export function resetImprovement(state);
/** Keystroke fast path: mirrors slider and number input, updates the running
 *  total and inline error, enables/disables Compare — all without setState. */
export function updateImprovementInput(control, state);
export function allocationTotal(allocations);
export function improvementValidation(state);         // '' when valid
/** @param {object} state
 *  @param {(e: Error & {code?: string}) => string} [toPublicMessage]
 *  calculator.js's publicError — §9's code-to-copy map — PASSED IN rather than
 *  imported, because calculator.js already imports this module and the import
 *  back would be a cycle. Without it this panel showed raw backend prose for
 *  the codes the main flow words carefully, and §9.1 rules that a public
 *  FORMULA_ERROR never echoes the expression or its location. */
export async function compareImprovement(state, toPublicMessage);
export function ImprovementScenario(state);           // collapsed CTA or open panel
export function ComparisonResults(state);             // '' until a comparison exists
```

> **Charts must render negative values, and these do.** `downstream` may be negative (§2.2) and a metric total therefore may be too. `Math.abs()` stood on both comparison-bar widths, so a −500 kg CO2e offset drew a bar identical to +500 and the reuse-and-offset story — the client's headline message — was invisible. A group containing a negative value now draws against a **centred zero line**: each bar takes at most half the track and grows right from the centre when positive, left when negative, and a group with no negative value keeps the full-width left-anchored bar so the common case is unchanged.
>
> **The arrow shows the direction of the impact, not the sign of the number.** This was ambiguous — an up arrow beside "1,104.0 kg CO2e saved" reads as "better" to one person and "went up" to another — and is now ruled. `net_benefit` is `current − alternative` (§3), so a positive net benefit is a saving, the impact fell, and the arrow points **down**. The two meanings that were sharing one class are now two sets:
>
> | Class | Applied by | Meaning | Mark |
> | --- | --- | --- | --- |
> | `.change-down` | `signClass()`, on `net_benefit > 0` | the impact fell — a saving | ↓, Kale |
> | `.change-up` | `signClass()`, on `net_benefit < 0` | the impact rose | ↑, Beetroot |
> | `.change-none` | `signClass()`, on \|`net_benefit`\| < 1e-9 | no change | —, muted |
> | `.value-negative` | `results.js` and `scenarioValue()` | this **quantity** is below zero | no arrow, Beetroot |
>
> **`.value-negative` carries no arrow deliberately.** `formatNumber` already prints the minus sign, and a quantity is not a movement. D's charts (§7.4) should use the same four classes rather than a second set. Nothing marks an ordinary positive quantity: a green mark against every figure on the page is decoration, not a signal.
>
> **`mass` is held out of the comparison lists** because §6.2 requires an entry's two scenarios to describe the same mass, so its `net_benefit` is zero by construction — "Mass: No change" on every comparison, in a list whose subject is what changed. Same exclusion as `results.js`, different reason.
>
> **The mass check is §6.2's own rule, applied in kilograms.** `improvementValidation` compared allocation percentages to within ±0.01 **percentage points**, which is a different rule at every tonnage: 0.01 points is 0.15 kg on a 1,500 kg entry, fifteen times §6.2's absolute 0.010 kg limit, so the panel enabled Compare on a submission the server then refused with a 400 — for the whole submission, after the user had left the screen with the numbers on it. It now sums the lines that will actually be sent. The seeded allocation was itself invalid under the corrected check (52.17 + 34.78 + 13.04 = 99.99%), so `currentAllocationPercentages` gives the rounding remainder to the largest share, and `improvedLines` anchors on the entry's **allocated** current mass rather than the total typed at step 3 — step 4 deliberately permits allocating less than the total, and anchoring on the typed total made every under-allocated entry send an alternative heavier than its current scenario.
>
> The alternative lines are built as `(totalKg × percentage / 100).toFixed(3)` **per line independently**, so Σ parts can differ from the entry total by up to 0.0005 × n. The dual-scenario design depends on the two scenarios conserving mass; this can break it by fractions of a gram. **Settled in v1.2, in C's favour:** §6.2's mass-conservation rule is derived from exactly this behaviour and its 0.010 kg tolerance is 20 lines × 0.0005 kg, so the drift this module produces is accepted rather than rejected — but only because §6.2 also caps a scenario at 20 lines per entry. The worst case sits on the boundary, and the comparison is `<=`. If that cap ever rises, this allocation must round to a running remainder instead.

### `main.js` — entry point for `index.html`

No exports. Wires `subscribe(→ renderChrome + render)`, calls `bindCalculator`, binds the header home and "Clear all data" buttons, defines `loadTaxonomy({preserveError})` (also passed to `bindCalculator` as the `UNKNOWN_CODE` reload path), and performs the first render and taxonomy fetch. It does **not** retry the taxonomy on load; the retry is the user's, through the `retry` action on the failure screen.

> **The focus policy, which is now a requirement rather than an implementation detail.** `render()` replaces `main.innerHTML` wholesale, so every re-render detaches whatever the user had focused. This module called `main.focus()` after *every* `setState`, so arrow-keying the sector radio group fired `change` → full re-render → focus yanked to `<main>`, and a keyboard-only user could not get past step 1.
>
> Moving focus to `<main>` is right on a **step transition** and wrong on every other `setState`. On a **same-step** re-render, focus goes back to the element that had it, looked up by `id` — which is why the food-category radios needed ids. Scoping the focus call alone is not sufficient and was the first attempted fix: the focused radio is detached regardless, so the keyboard user lands on `<body>` instead of `<main>`, which is worse. Any change here has to preserve both halves.

### `methodology.js` — entry point for `methodology.html`

No exports. Uses top-level `await` to call `getFactors()`, then writes the factor-set metadata and the published-formula table into `#factor-content`, prefixed by the placeholder-data banner when `factor_set.is_mock`. Renders an escaped error block on failure. `formula.expression` is staff-authored content reaching a public page and is escaped inside `<code>`.

## 7.4 `charts.js` (written by D)

```js
/**
 * @param {HTMLCanvasElement} el
 * @param {Array<{code,label,count,share}>} buckets
 * @param {{title?: string}} [opts]
 * @returns {Chart}  Chart.js instance; the caller is responsible for destroy()
 */
export function renderDonut(el, buckets, opts);

/**
 * @param {HTMLCanvasElement} el
 * @param {Array<{label, value, unit}>} rows
 * @param {{allowNegative?: boolean}} [opts]  Downstream factors may be
 *        negative, so the bar chart must render negative values
 * @returns {Chart}
 */
export function renderBar(el, rows, opts);
```

## 7.5 `news.js` (written by D)

```js
/**
 * Fetches news from the client's WordPress site. No second news system
 * is built.
 * @param {number} [limit=6]
 * @returns {Promise<Array<{title, excerpt, link, date, imageUrl}>>}
 *          Returns [] on failure so the home page never blanks out
 *          because the news feed is down.
 */
export async function fetchNews(limit);
```

Source: `https://kaicommitment.org.nz/wp-json/wp/v2/posts?per_page={limit}&_embed`

> **§7.4 and §7.5 are specifications, not descriptions.** Neither module exists yet, and Chart.js appears nowhere in the tree — C's bars are CSS-width `<span>` elements. They remain D's deliverables.

## 7.6 Front-End Hard Constraints

1. **The front end performs no impact calculation.** Apart from unit conversion in `units.js`, every number comes from the API. This includes cross-entry totals: read `totals` and `net_benefit` from §6.2, never a sum over `entries[]`.
2. **When `is_mock` is true, the warning banner is mandatory** and cannot be dismissed. This covers **every results view and every export**, and it must be conditional on `is_mock` rather than unconditional — an export that always carries the placeholder disclaimer becomes an export that disclaims real data the day real factors are published, which is the more damaging direction of the same bug.
3. The calculator page is **mobile-first**, baseline width 375px. **The baseline is not a floor — check the band between the breakpoints.** A layout can pass at 375px and at desktop and fail in between: `.results-page`'s −80px bleed had its reset at ≤480px and its desktop counterpart at ≥850px and nothing in between, so from 481px to 849px the results page sat 60px off the left edge with the body scrolling sideways. A tablet is the likeliest non-desktop device a demo runs on.
4. After every successful calculation, write the returned `token` back to `sessionStorage`.
5. **Iterate over metrics and equivalences; never hard-code their codes.** A view that lists `['co2e','water','cost']` silently omits the metric a staff member added, and adding a metric is meant to cost one `INSERT` and one formula (§2.1).
6. **Charts must render negative values.** `downstream` may be negative (§2.2), so a metric total may be too. Discarding the sign hides the reuse-and-offset result the calculator exists to show. The sign classes and the arrow convention are in §7.3a under `improvement.js`; use those four classes rather than a second set.
7. **No page may request an asset from a third-party host at runtime.** Fonts, scripts, stylesheets, icons and images are served from this origin. `styles.css` opened with an `@import` from `fonts.googleapis.com`, so every visitor's browser announced itself to a third party before the first paint — on a calculator whose stated privacy position is §2.3's, and whose statistics page says so in its own copy — and the first paint waited on a network the project does not control. The brand fonts are in `web/assets/fonts/`. **This binds §7.4:** Chart.js is self-hosted, never loaded from a CDN.

---

# 8. Admin Panel (owner: E)

Built on `sqladmin`, mounted at `/admin`, authentication required.

## 8.1 Models Exposed for Direct CRUD

**Taxonomy and factors — eleven, from v0.1:** `sector`, `food_category`, `destination`, `destination_group`, `metric`, `unit_preset`, `constant`, `formula`, `equivalence`, `factor_upstream`, `factor_downstream`

**Comparison scenarios — two, added v0.10 (§2.2a):** `comparison_scenario`, `comparison_scenario_line`. Edited under their own "Comparison" category. Unlike the taxonomy tables these may be **deleted** through the panel: a scenario is a staff member's own saved test case and is referenced by nothing else in the schema.

**Blocklist — one, added v0.12 (§2.3):** `ip_block`. Listed here for completeness but **it is not generic CRUD, and treating it as such would leak the thing it exists to avoid storing.** The view is list-only and restricted to `role = admin`; `ip_hmac` never appears in `column_list`; a block is created through the custom form at `/admin/ip-block/block` and removed through the audited `unblock` action rather than `sqladmin`'s generic delete. §8.3 is the specification.

Requirements: list views must offer search and filtering.

**Every write must produce an `audit_log` entry.** Admin CRUD achieves this through an `AuditedModelView` base class that every view above inherits; factor-set lifecycle operations do it inline in the repository. Both call `write_audit()` (§5.5), which is the only code that inserts into `audit_log`.

> This list said "eleven" for four revisions after it stopped being eleven. `comparison_scenario` and `comparison_scenario_line` landed in v0.10 and `ip_block` in v0.12, and each was specified in §2 and §8.2/§8.3 without being added here — so §8.1, the section that states the audit requirement, named three fewer tables than the panel actually writes to. A reader auditing "does every write produce an audit entry" against this list would have concluded yes while three tables sat outside it.

`AuditedModelView` captures the pre-change row in the before-write hook — the after-write hook only ever sees the new values — and hard-codes `can_create = can_edit = can_delete = False` on the `audit_log` view itself.

## 8.2 Custom Views

| View | Path | Function |
| --- | --- | --- |
| Factor sets | `/admin/factor-sets` | Clone, publish, archive, roll back; shows draft/published/archived state. **`status` is not on the edit form** — these four actions are the only way it changes, so each one takes `SELECT ... FOR UPDATE`, revalidates the set's formulas where relevant, stamps `published_at` / `published_by`, and writes its own audit entry. Archiving the currently published set is permitted and takes the calculator offline: `NO_PUBLISHED_FACTOR_SET` (503) is the designed response to having none |
| Dry run | `/admin/try` | Enter a test scenario, call `POST /api/v1/calculate` with **`X-Dry-Run: true`** and a `dry_run` object (§6.2.1), and display the line-by-line breakdown |
| Pre-publish comparison | `/admin/factor-sets/{id}/compare` | Run a fixed set of standard test scenarios against **both** the published set and this draft, and show the published value and the draft value per metric, side by side. The last gate before publishing. |
| Submissions | `/admin/submissions` | Record-level list with search and filtering; allows setting `excluded_from_public` with a reason |
| Audit log | `/admin/audit` | Read-only, filterable by actor, time and table |

> The dry-run view **must** send the dry-run header. Staff will run dozens of calculations while tuning a formula, and persisting them would directly pollute the public statistics.

The comparison view is two dry-run calls per scenario — one with `factor_set_version` set to the published label, one to the draft — shown side by side, **not** differenced. Decision 6 puts every impact number server-side, in exactly one place; `POST /api/v1/calculate` computes `net_benefit` only for a current-versus-alternative comparison made *within one call*, and has no concept of a difference between two separate calls made at two different `factor_set_version`s. Subtracting the two response strings in the view or the template would put a number in front of staff that no server-side calculation ever produced, which is exactly what Decision 6 forbids — so the page renders both values, plainly labelled, and says in words that no difference is shown. Whether `POST /api/v1/calculate` should grow a two-version diff so this page can show one is open (raised in the E7 task report; not yet assigned an owner). The standard scenarios it runs are staff-editable rather than hard-coded; hard-coding them would reintroduce "change the code to change the configuration", which Decision 2 exists to prevent. They live in `comparison_scenario` / `comparison_scenario_line` (§2.2a), edited through their own CRUD screens like every other §8.1 table.

Because a dry-run request body is a `bundle` plus a scenario, the dry-run view can offer a **Save as regression case** action that writes `tests/golden/case_NN/{bundle,request,expected}.json` (§10.1) directly from a run staff considers worth keeping. Tuning factors then produces golden cases as a by-product rather than requiring them to be authored separately.

## 8.3 Accounts, Authentication and Recovery

`sqladmin` provides an `AuthenticationBackend` abstract class — `login()`, `logout()`, `authenticate()` — and nothing more. There is no built-in user store, password hashing or login page; all of it is specified here.

### Roles

| Capability | `staff` | `admin` |
| --- | --- | --- |
| Taxonomy, factor and formula CRUD | ✅ | ✅ |
| Dry run, view submissions, view audit log | ✅ | ✅ |
| Set `excluded_from_public` | ✅ | ✅ |
| Publish, roll back | ✅ | ✅ |
| Create, deactivate and re-role accounts | ❌ | ✅ |
| Reset another account's MFA, issue a random password | ❌ | ✅ |

Publishing is available to both roles deliberately: `audit_log` records who published and rollback is one action, so accountability and recovery are already covered. Restricting it would stall routine work whenever the administrator is unavailable, in a team of three to five people.

### Mandatory MFA

Every account enrols a TOTP authenticator. There is no opt-out.

```
admin creates account  ->  random initial password, shown once,
                           handed over out of band
        v
first login            ->  must_change_password = true
        v
forced password change
        v
forced TOTP enrolment  ->  QR code plus 5 single-use recovery codes,
                           shown once; one correct TOTP required to finish
        v
mfa_enrolled_at set    ->  access granted
```

**While `mfa_enrolled_at IS NULL`, every route except the password-change and enrolment pages is refused, `require_staff()` included.** Without that, enrolment is advisory — a user can navigate straight past it by typing a URL.

Implementation notes: `pyotp` with `valid_window=1` (±30 s clock drift); `qrcode` with the SVG factory, which avoids a Pillow dependency; initial passwords and recovery codes from `secrets`, never `random`.

### Login throttling

Consecutive failures lock a username for a cooling-off period. **Password failures and TOTP failures share one counter** — throttling only the password step leaves a six-digit second factor, a 10⁶ search space, open to anyone who already has the password.

Counters are keyed by **username and held in memory**. They are not keyed by IP address and no IP address is stored, per Decision 3. Username keying is also the more precise signal.

### Recovery, with no email system

The project builds no email capability, so there is no reset link. Three layers, all required:

| Layer | Mechanism | Covers |
| --- | --- | --- |
| L1 | 5 single-use recovery codes issued at enrolment | Lost or wiped authenticator; needs no second person |
| L2 | Another administrator resets MFA and issues a random password | Recovery codes also lost |
| L3 | `python -m admin.cli reset-mfa <username>` on the server | Every administrator locked out |

**At least two administrator accounts must exist at all times, and at least two must be able to log in.** Deleting, deactivating or demoting an administrator is refused while *either* count is 2 or fewer:

| Count | Definition | Used by |
| --- | --- | --- |
| Active | `role = admin` and `is_active` | Bootstrap's "does this system have any administrator yet" check |
| **Usable** | Active, **plus** MFA enrolled, **plus** past the forced password change | The removal guard |

The second count exists because bootstrap creates two administrators carrying `must_change_password` and no enrolment. Counting only active accounts reports two usable administrators when there is one — so a system where the client onboarded `admin` and filed `admin2`'s printed password away would permit deactivating the only account anyone can actually log in as, and with no email system there is no way back.

This must be enforced in the service layer, not only in the form — `sqladmin`'s form validation can be bypassed.

> Consequence worth knowing: while fewer than two administrators are usable, **no** administrator can be deactivated or demoted, including one that was never onboarded. Eviction is still possible without deactivation — reset the account's MFA and issue a new password — but it is indirect. This errs toward "cannot be locked out" over "can always evict", which is the correct side for a small organisation with no email recovery.

**Session invalidation.** Every credential change — password change, MFA
reset, deactivation — increments `staff.session_generation`. The signed
session cookie carries the generation it was minted under, and
`require_staff_username()` (§8.4) refuses any mismatch. An administrator
resetting a compromised colleague's account therefore ends that account's
live sessions immediately, without server-side session storage.

Note for B: this is enforced inside `require_staff()`, so the API layer
inherits it with no change on your side.

For E: `require_staff_username()` is not the only implementation of this
comparison. The admin panel is not routed through it — it is gated by
`AdminAuth.authenticate()` in `admin/backend.py`, which carries its own,
deliberately duplicated `session_generation` comparison rather than calling
`require_staff_username()`. The two are documented as needing to change
together (each names the other in its own comment), but nothing enforces
that beyond the comment — they have already drifted out of sync once during
this branch. E-4 through E-6 read this section: if either comparison
changes, check the other.

> **The submissions migration is `0008`, with `down_revision = "0007"`. Recorded here because it is the second reconciliation item this merge settles, and because the document that says otherwise is still on the shelf.** `docs/ToB_v3.0.md` §1.3 instructs B to file the three submission tables (`submission`, `submission_entry`, `submission_line`, plus the `COALESCE(food_category_id, 0)` functional index) as `alembic/versions/0006_submissions.py` with `down_revision = "0005"`. That was correct when it was written. Since then `0006` (comparison scenarios, v0.10) and `0007` (`ip_block`, v0.12) have both landed, so **ToB v3.0 is stale on this point and must not be followed literally.**
>
> Following it produces a **third Alembic head**, not a merge conflict: two migrations both claiming `down_revision = "0005"`, `alembic upgrade head` aborting on ambiguity, and `upgrade heads` then failing partway through on a table that already exists. **MySQL DDL autocommits**, so there is no transaction to roll back and the recovery is `DROP DATABASE` — which is survivable on a developer's machine and is not survivable anywhere else. This is the cheapest possible thing to get right and one of the most expensive to get wrong, which is the only reason it is written into the contract rather than left in a task brief.



`admin.accounts.issue_password(session, username, *, actor) -> str` performs the L2 half named above: it sets a random password, forces `must_change_password = True` (an issued password is in the same position as a bootstrap one and gets the same forced change — this is what distinguishes it from `set_password`, which clears that flag because the user chose the password themselves), and bumps `session_generation` so the account's live sessions end immediately. The plaintext is returned once, to be read out and handed over out of band, and never reaches `audit_log` — the entry records that `password_hash` changed, not what it changed to.

The CLI account-creation command is exempt from that rule; it only ever adds, and a system with no accounts yet must be able to bootstrap. While exactly one active administrator exists, the panel displays a non-dismissible banner advising that a second be created.

### Bootstrap

**On first start, when no administrator account exists, the application creates two.** A deployment therefore satisfies the two-administrator rule from the moment it comes up, rather than depending on whoever installs it remembering to run the CLI twice — and a system that starts with one administrator is a system that can be locked out by a single lost phone.

| Property | Behaviour |
| --- | --- |
| Trigger | Application start, only when the active administrator count is zero |
| Accounts | `admin` and `admin2` |
| Passwords | **Randomly generated per deployment, printed once to standard output.** There is no default password and no fixed value anywhere in the source. |
| State | Both carry `must_change_password` and no MFA enrolment, so `require_staff()` refuses them until both steps are completed |
| Idempotence | Runs once. A restart with administrators present creates nothing. |

**A fixed default password would be the single worst defect this system could ship.** `admin`/`admin` on a public panel is exactly how community-sector accounts get taken over, and it is the reason MFA is mandatory here in the first place. The generated passwords exist only in the start-up output; they cannot be recovered afterwards, only reset via `reset-mfa` and a new password.

> Deployment note for the handover documentation: the start-up output contains live credentials. Capture them, log in with both accounts, change both passwords, enrol both authenticators, then discard the output. Do not pipe first-start output into a shared log collector.

> Two administrators is not sufficient on its own. A small organisation is likely to hand both accounts to the same person, or to replace phones at the same time. L1 is the layer that does not depend on a second human being available, which is why it is mandatory rather than a convenience. The panel prompts for regeneration once 2 codes remain.

### Operational commands (owner: E, must be documented for handover)

| Command | Purpose |
| --- | --- |
| `python -m admin.cli create-staff <username> "<name>" [--admin]` | Bootstrap and routine account creation |
| `python -m admin.cli reset-mfa <username>` | L3 break-glass |
| `python -m admin.cli issue-password <username>` | L3 break-glass — issues a random password and forces a change at next login; ends the account's live sessions |
| `python -m admin.cli rotate-key --old <k> --new <k>` | Re-encrypt every `mfa_secret_enc` after a `SECRET_KEY` change — **and clear `ip_block`**, because an HMAC cannot be re-keyed; it reports how many blocks were cleared and that they must be re-applied |
| `python -m admin.cli unblock <address>` | E-8's own break-glass: remove a block from the server when the panel itself is unreachable because of it. Rejects a value that is not a single IP address rather than silently doing nothing |
| `python -m admin.cli bootstrap` | Create the initial administrator accounts if none exist |

One module with subcommands rather than three separate module entry points: `python -m admin.cli --help` then lists every operational command in one place, which is what the handover documentation needs, and settings loading and session construction are written once rather than three times.

The rotation command is not optional. `SECRET_KEY` lives in `.env`, and without rotation the day the client changes it is the day every account loses its second factor.

### Blocklist

**Corrected in v0.12 — the previous paragraph here (`ip_blocklist(id, cidr, reason, created_at, created_by)`, "the middleware ... stores nothing") predates E-8 and described the wrong table under the wrong name.** §2.3 is now the authoritative description of what is actually built: the real table is `ip_block(id, ip_hmac, reason, created_at, created_by, expires_at)`, keyed on a 64-character HMAC of the address rather than a CIDR, and it does store something — a fingerprint, never the address itself. Storing nothing at all derived from the address was considered and rejected here, for a different reason than §2.3's own "browser fingerprinting rejected" callout (see below): an operator needs to be able to *remove* a specific block, which requires recomputing the same fingerprint for the same address again on request, not merely detecting a one-time match — a construction with no stored, comparable value cannot support that.

> **Three things this document calls "fingerprint," and they are not the same thing.** §2.3's "no fingerprint of any kind is stored" and "browser fingerprinting was considered and rejected" both refer to a *browser* fingerprint — a persistent quasi-identifier built from device/header characteristics, rejected there as ineffective against the traffic it would defend against and the highest privacy risk of the options considered. The `ip_hmac` fingerprint described in this section is a different construction entirely — a keyed HMAC of a single known address, not a browser characteristic — kept specifically *because* an operator needs to recompute and compare it, which is exactly the property that made the browser kind unacceptable. Do not read §2.3's rejection of browser fingerprinting as covering this one; it doesn't, and the two are evaluated on different grounds.

`db/blocklist.py` (owner: E, layer: `db/`) is the whole of the write/read surface: `block_ip`, `unblock_ip`, `is_blocked`, `ip_fingerprint`. **Two middlewares read it, and neither writes to it.** `admin/protection.py`'s `ProtectionMiddleware` (owner: E) reads `is_blocked` ahead of every other check on every request under `/admin`, with no exemption — not even for an authenticated staff session, because a block is another administrator's deliberate act. `api/app.py`'s `blocklist` middleware (added v1.3, owner: B) does the same for every request under `/api/v1/`, ahead of routing so that a dead path is refused identically to a live one (§9.2). Until v1.3 there was no second reader at all: a block made on the screen below held on the panel and did nothing where the public traffic actually arrives, which is the one thing a blocklist is for.

Three differences between the two, each deliberate and each explained where it is implemented:

| | `admin/protection.py` | `api/app.py` |
| --- | --- | --- |
| Refusal body | `PlainTextResponse("Refused.")` — a browser surface | §9.2's `BLOCKED` envelope, built by `api/errors.py` like every other error. A JSON client that got plain text back for one error out of twelve would have to special-case it, and C and D would each have to do so separately |
| Header check | `looks_automated` applies | Does **not** apply — scripting a public JSON API is a legitimate use, and §6.3's CSV export exists to be fetched by a tool |
| Rate limit | `PROTECTION_MAX_REQUESTS_PER_MINUTE`, refusals counted, `/admin/login` and `/admin/verify` exempt | §6.5's fixed hourly limits, refusals not counted, nothing exempt |

Both key on `db.detection.client_ip`, so a caller with no usable address is skipped by both rather than given a stand-in key — and both inherit the deployment hazards §6.5 records.

The admin screen, `/admin/ip-block/list` (`admin.blocklist_views.IpBlockAdmin`), is where a block is actually created or removed by a person: `column_list` shows `reason`, `created_by`, `created_at` and `expires_at` — never `ip_hmac` — and is restricted to `role = admin`, the same floor `StaffAdmin` sets for account management. A manual block is entered through its own form at `/admin/ip-block/block` (address, reason, an optional duration in minutes); removal is an audited `unblock` action, not sqladmin's generic delete. Both write their own `audit_log` entry, built from `reason`/`created_by`/`created_at`/`expires_at` only — never from `ip_hmac` — since `db/blocklist.py` itself writes none (see §2.3). `ip_hmac` is additionally named in `write_audit`'s `REDACTED_FIELDS` (§5.5), so a future caller that serialises a whole `IpBlock` row through `row_to_dict` still cannot land the fingerprint in a table every staff member can read.

**Where an operator gets an address to type into that form.** Nowhere in this system — and that is worth stating, because the form otherwise reads as more capable than the panel is. Nothing here ever shows staff a caller's address: §2.3 forbids storing one, and the panel deliberately does not log one either. The address has to come from outside: the reverse proxy's or hosting platform's own access log, an alert from the host, or a report from someone who can see the traffic. The form's purpose is to *apply* an address an operator already has in hand from one of those, during an incident, with no CDN or upstream firewall available to do it for them. Anyone planning to rely on this screen should confirm the deployment keeps a proxy access log at all, before an incident rather than during one.

> **Resolved — `write_audit` lives in `db/repository.py`, and it always did on B's branch.** v0.13 recorded this as an open item awaiting a decision from B, on the evidence available to E at the time: `write_audit` was in `admin/audit.py`, `api/` may not import `admin/` (CLAUDE.md's layering rule, AST-pinned by `tests/db/test_blocklist.py`), and so an API-side automatic block could not audit itself. **The survey of `origin/database` closed it.** B's branch already carries `write_audit` and `_json_safe` in `db/repository.py` — exactly where §5.5 has placed them since v0.3 — so there is no decision left to take, only a duplicate to remove. It was never two designs; it was one design and two branches.
>
> **What that means for the integration, concretely:**
>
> - **`db/repository.py`'s `write_audit` is the one that survives.** `admin/audit.py` becomes a re-export of it, or is deleted. E's copy gives way.
> - **B's `_json_safe` recurses into nested dicts and lists and redacts at every level; E's `_scrub` only handles top-level keys.** That is not a style difference. A `mfa_secret_enc` or a `password_hash` nested one level down inside a `before_json` payload passes straight through `_scrub` and lands in `audit_log`, which every staff member can read — the exact privilege-escalation path §5.5's field blocklist exists to close. **B's is the one that survives**, on this ground alone.
> - **E's `_encode` handles `date` more carefully than B's.** That one branch folds into B's function; nothing else of E's does.
> - Once the re-export is in place, "the caller audits" becomes executable from `api/`, and an API-side automatic block appears in `/admin/audit` like every other write.
>
> **Resolved in v1.3 — the three names moved to `db/detection.py`.** This paragraph recorded the split as genuinely unresolved and B's to decide: `admin.detection.looks_automated`, `admin.detection.RequestRate` and `admin.protection._client_ip` were all in `admin/` and were all things the public-traffic middleware needed. **The recommendation recorded here is the one that was implemented.** They are now `db.detection.looks_automated`, `db.detection.RequestRate` and `db.detection.client_ip`; `admin/detection.py` is a re-export, and `admin/protection.py` keeps `_client_ip` as an alias for the same object rather than a second copy — `tests/db/test_detection_shared.py` asserts that identity with `is`, and AST-pins that `db/detection.py` imports nothing from `admin/`, the same pin `tests/db/test_blocklist.py` applies to the blocklist. The deciding argument was the one written here: two copies of a detection rule drift, and the copy that stops matching is the one nobody notices.
>
> One part of the rationale above was wrong and is corrected rather than repeated: **`db/detection.py` is not standard-library-only at runtime.** It imports `db.blocklist` for address normalisation, which pulls in SQLAlchemy and `cryptography`. Starlette is imported under `TYPE_CHECKING` only, so the module is still usable and testable without a web framework, but the stdlib-only property the recommendation leaned on does not survive the move in full. What does survive — and what the layering rule actually required — is that nothing in it needs `admin/`.
>
> **`looks_automated` is applied to `/admin` and deliberately not to `/api/v1/`.** The other two are used by both. This is a decision, not an oversight: `/admin` is a browser-only surface, so a caller there that is plainly a script is refused, whereas scripting a public JSON API is a legitimate way to use it and §6.3's CSV export exists precisely to be fetched by a tool. Refusing `curl` at `/api/v1/` would refuse a use this contract invites. The API applies the blocklist and §6.5's rate limit and nothing else.
>
> **`RequestRate` is still per-process**, so under more than one worker the effective limit is multiplied by the worker count — true of both layers now, since both count with the same class. The API's own rate limiting (§6.5) is no longer a separate problem: `api/rate_limit.py` wraps this counter rather than being a third implementation of one, keeping only the policy §6.5 needs (a per-call limit, and `Retry-After` derived from the oldest hit still inside the sliding window). It does **not** count refused requests, which is where it deliberately differs from `admin/protection.py` — see §6.5.

**The one case this whole design is built around not causing:** an administrator blocks the address they are sitting behind, and the block itself now stands between them and every page that would let them undo it — including the login page, because the blocklist check has no exemption. `python -m admin.cli unblock <address>` (above) is the only way back short of editing the database by hand, and is the reason that command exists at all.

`ProtectionMiddleware` also carries a stateless header check and a per-address rate limit (`PROTECTION_MAX_REQUESTS_PER_MINUTE`), both configurable and both able to be turned off in one place: `PROTECTION_ENABLED=false` disables the blocklist, the header check and the rate limit together, with no finer-grained switch — the documented escape hatch for a false-positive lockout that is not a blocklist entry. **It needs no code change, but it does need a process restart**: settings are read once, by `load_settings()` at start-up. See `docs/architecture.md` §9.1.1 for the operational detail, the `PROTECTION_TRUSTED_PROXY` warning, the four recovery paths, and this design's explicit limits. **That section lands with PR #9 and is not on `main` yet** — until #9 merges, `architecture.md` stops at §9.1 Deployment and the reference resolves only on the `admin_panel` branch. Stated rather than left to be discovered, because a cross-reference that dangles for a known reason is a known state and one that dangles silently reads as an error in this document.

**`/admin/login` and `/admin/verify` are exempt from the rate limit** — and from that check only; the blocklist and the header check still apply to both. Behind a reverse proxy with `PROTECTION_TRUSTED_PROXY` false (which is the shipped arrangement, since TLS is terminated upstream and trusting `X-Forwarded-For` without a proxy that overwrites it would let any caller forge any address) every caller arrives as the proxy's own address and shares one rate-limit bucket, and refused requests are counted too — so without this exemption one request a second from any unauthenticated caller kept that bucket permanently over the limit and answered 429 to every unauthenticated request in the deployment, the login pages included. The authenticated-staff exemption cannot rescue that, because it needs the session only those two pages mint. Login attempts are still throttled per account by `LOGIN_MAX_FAILURES`/`LOGIN_LOCKOUT_MINUTES`, which is the check that actually defends a credential-stuffing run.

## 8.4 Staff Authentication Interface (owner: E, consumed by B)

The only coupling between the admin authentication system and the API layer.

```python
# admin/auth.py

def require_staff(request: Request) -> str:
    """Return the authenticated staff username.

    Raises StaffAuthRequired when the session is absent or expired, when the
    account is no longer active, or when MFA enrolment is incomplete
    (§8.3). The API layer maps that to UNAUTHORIZED (401).

    The mechanism — sqladmin's AuthenticationBackend over a same-origin
    Starlette session cookie — is E's concern and is not part of this
    contract. B calls this and nothing else.
    """
```

The return value is the staff username and is written directly to `audit_log.actor` (`VARCHAR(128)`).

B calls it in exactly one place: the `X-Dry-Run` branch of `POST /api/v1/calculate`.

> **Terminology.** `submission.token` (§2.3) is the anonymous de-duplication token — a UUID4 that expires after an hour and identifies a draft record, not a person. Staff credentials are **not** a token and are never carried in the request body; they travel as a same-origin session cookie. The admin dry-run page is served from the same origin as the API, so the browser attaches the cookie without any explicit handling.
>
> Should the public front end later be split onto a separate origin, only the public requests are affected (CORS); they carry no cookie, so the dry-run path is unchanged.

---

# 9. Error Contract (owner: B)

Every non-2xx response uses one envelope:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "A single line may not exceed 10,000,000 kg",
    "details": [
      { "field": "entries[0].current[1].qty_kg",
        "issue": "exceeds_max",
        "message": "A single line may not exceed 10,000,000 kg" }
    ]
  }
}
```

**`details` has exactly two shapes, and which one you get is decided by the `code`, never by inspection.**

| Shape | Keys | `code` |
| --- | --- | --- |
| **Field problem** | `field`, `issue`, `message` — all three, always | `VALIDATION_ERROR` |
| **Formula problem** | `expression`, `line`, `column`, `reason` | `FORMULA_ERROR`, and only on an **authenticated dry run** (§9.1) |

Every other code carries `details: []`, except §9.2's `BLOCKED`, which carries `null`. **So: if `code` is `VALIDATION_ERROR`, every entry has `field`, `issue` and `message`; if it is `FORMULA_ERROR`, entries are the formula shape or the array is empty; otherwise the array is empty.** No entry mixes the two shapes and no key is conditionally absent within a shape — a consumer needs no presence checks, only a branch on `code`.

On the field shape's three keys: `field` is §9's bracket path, `issue` is a stable machine-readable slug (Pydantic's error `type` where the failure is Pydantic's, e.g. `value_error`; a name this API chooses otherwise, e.g. `mass_not_conserved`, `bundle_invalid`), and `message` is per-field prose distinct from the envelope's single `message`, which describes the request as a whole. **Branch on `issue`, display `message`, target `field`.**

> **Both halves of this were wrong until v1.5, in opposite directions.** The `message` key was emitted from the first commit and shown in no version of this section, while `tests/fixtures/errors/validation_error.json` has always carried it — so a front end built from the old sample rendered `Request validation failed` against every highlighted row and discarded the only text saying what was wrong with that row. And `api/router.py`'s inline-bundle check emitted a *third* shape, `{field, issue}` with no `message`, putting `FactorBundle.validate()`'s human-readable prose (§4.1) into `issue` — inverting the two keys, so a consumer told to branch on `issue` got a sentence that changes whenever the engine's wording changes, and found no `message` to display. **The code was corrected to the contract rather than the contract widened to the code**, because two shapes a consumer can predict from `code` is a contract, and three shapes it must sniff at runtime is not.

**`field` is a bracket-indexed path into the request body**, exactly as a front end would write it: `entries[0].current[1].qty_kg`. Array positions are `[n]`, object keys are `.key`, and the path starts at the root of the request.

> This needs stating because the two obvious implementations disagree and the disagreement is silent. Pydantic's native `loc` is a tuple that renders as `entries.0.current.1.qty_kg`; a front end building a lookup key from its own render loop writes `entries[0].current[1].qty_kg`. Neither is wrong, but if the API emits one and the client looks up the other, **field-level highlighting simply never binds** — no error, no console warning, the user just sees the generic banner and never learns which row is bad. Both fixture sets on the team had already chosen different formats. The API is responsible for converting Pydantic's `loc` to this form before it goes on the wire.

| HTTP | `code` | Trigger | Front-end response |
| --- | --- | --- | --- |
| 400 | `VALIDATION_ERROR` | Missing field, out of bounds, duplicate destination, malformed dry-run bundle | Highlight the offending field |
| 400 | `UNKNOWN_CODE` | A code in the request does not exist | Re-fetch the taxonomy and prompt a refresh |
| 401 | `UNAUTHORIZED` | `X-Dry-Run: true` without a valid staff session | Redirect to `/admin/login` |
| 403 | `BLOCKED` | The caller's address is on the blocklist (`db.blocklist.is_blocked`, §2.3) | Show the `message` and stop. **Do not retry, and do not offer a retry button** |
| 429 | `RATE_LIMITED` | Rate limit exceeded | Ask the user to retry later; disable the button for 60s |
| 404 | `NOT_FOUND` | No route matches the path | Bug in the caller. Show the generic banner; do not retry |
| 405 | `METHOD_NOT_ALLOWED` | The path exists, the method does not | Bug in the caller. Show the generic banner; do not retry |
| 500 | `FORMULA_ERROR` | A staff-configured formula is invalid | See below |
| 500 | `INTERNAL_ERROR` | Any unhandled server-side failure | Show the generic banner. A retry may succeed; do not retry automatically |
| 503 | `NO_PUBLISHED_FACTOR_SET` | No factor set has been published | Show "calculator under maintenance" |
| 503 | `ENGINE_UNAVAILABLE` | The calculation engine is not installed or failed to load | Show "calculator under maintenance", as for `NO_PUBLISHED_FACTOR_SET` |
| *(other)* | `HTTP_ERROR` | Residual: any other framework-level HTTP failure, carrying that failure's status | Show the generic banner |

`message` is written for end users and may be displayed verbatim. `details` is for developers and form-field targeting.

> **The last five were emitted by `api/errors.py` from the beginning and appeared in no version of this section**, which listed no generic 500 at all. That is the shape of the defect: §7.1 tells C to branch on `body.error.code`, and a code that reaches her from a closed set she was given is one she can handle, while a code that reaches her from nowhere falls into whatever her `default` branch does. All five already emit the correct envelope — this is the table catching up with the code, not a behaviour change.
>
> **Every one of them is a `code` the front end must treat as terminal-and-generic.** None carries actionable `details`, none names a field, and none should be retried automatically. `ENGINE_UNAVAILABLE` is the one worth distinguishing in copy: like `NO_PUBLISHED_FACTOR_SET` it means the calculator cannot run at all right now, rather than that this particular request was bad, and the two deserve the same maintenance message rather than the generic failure banner.
>
> **`HTTP_ERROR` is a name collision, deliberately left standing.** §7.1's `ApiError.code` already uses `HTTP_ERROR` for a *client-side* condition — a response the browser could not parse as JSON. The API now also emits it as the residual server code for a framework-level `HTTPException` that is neither 404 nor 405. The two are not the same event, and they are not distinguishable from `code` alone. They are left sharing a name because **the front end's response to both is identical** — a generic banner, no field targeting, no automatic retry — so the distinction would cost C a branch and buy nothing; `status` separates them if it is ever needed (`0` or an unparsed body on C's side, a real status and a well-formed envelope on the API's). Written down so that a reader who finds the same string in two sections does not conclude one of them is a mistake.
>
> **What is *not* here is also a rule:** the API does not invent codes beyond this table. §4.4's four engine exceptions map onto rows above; anything else the engine raises is an engine bug, not a documented condition, and lands on `INTERNAL_ERROR` deliberately — a code minted at the point of failure is a code no consumer could have branched on.

## 9.1 `FORMULA_ERROR` Has Two Presentations

| Request | Response |
| --- | --- |
| Public | Generic message. The expression is **never** echoed, and no location is given. |
| Authenticated dry run | `details` carries `expression`, `line`, `column` and `reason` from the `FormulaError` (§4.4) |

Withholding the location from the public protects staff-authored configuration from disclosure. Withholding it from the staff member who is at that moment editing the formula would make the editor unusable — they cannot fix what they are not told.

## 9.2 `BLOCKED` (403)

Defined here so that B does not have to invent a code and C and D do not each
handle it differently. Distinct from `RATE_LIMITED` on purpose: `RATE_LIMITED`
means "too fast, try again", and the front end is told to re-enable the button
after 60 seconds. `BLOCKED` means a staff member decided this caller should not
be served, and a retry will never succeed — a front end that treated the two
the same would poll a blocked caller against the API forever.

```json
{
  "error": {
    "code": "BLOCKED",
    "message": "This request was refused. If you believe this is an error, contact the Kai Commitment team.",
    "details": null
  }
}
```

**`details` is always `null`, and `message` never varies.** The refusal must not
say which rule fired, when the block expires, or that a blocklist exists at all
— the same reasoning `admin/protection.py`'s bare `"Refused."` body records: a
caller being refused is not owed the rule it broke, because that is a free
tuning signal for whoever is probing. Staff read the reason and the expiry on
`/admin/ip-block/list` and in `audit_log`.

**The block is checked before anything else**, including request validation, so
a blocked caller cannot use the API's own error messages to probe the
taxonomy — and, being a single indexed lookup on `ip_hmac`, it costs one query.
It applies to every endpoint under `/api/v1/`, `GET` included.

**It must run ahead of routing, which means middleware and not a router
dependency** (`api/app.py`'s `blocklist` middleware). FastAPI solves a router's
dependencies only once a request has matched a route, so a dependency leaves
`/api/v1/does-not-exist` answering `404` while every live path answers `403` —
a working route scanner for a blocked caller, and a difference no amount of
reticence in the body above can hide. Running ahead of routing also keeps the
one-query cost honest: a dependency *and* a middleware would be two lookups.
The response is built directly rather than raised, because an exception raised
in middleware never reaches the handlers registered with
`add_exception_handler` — Starlette's `ExceptionMiddleware` sits inside them,
and the session middleware outside would turn a raised `ApiProblem` into a
`500 INTERNAL_ERROR`.

---

# 10. Mock Data Convention

Located in `tests/fixtures/`. C and D consume these directly before the backend is ready.

**These files are the executable form of the contract.** Backend contract tests assert that real responses match their shape, the fixtures are checked against each other and against the shipped seed data, and the front end develops against them directly. They must be updated whenever the contract changes (see §0).

**One canonical set, in this tree, in the v1.3 shape.** Thirteen files. v1.2 recorded two divergent sets on two unmerged branches and neither of them here; that is now history and the paragraph describing it has been replaced by what is actually on disk.

| File | Content |
| --- | --- |
| `taxonomy.json` | A complete `GET /taxonomy` response: six sectors, ten food categories including `standard_mix`, **fourteen destinations across the three `destination_group` rows `reuse`, `recycle_recovery` and `disposal`** — `prevention` is a destination in the `reuse` group, not a group of its own — the metrics, and the unit presets. **Its codes are `admin/seed.py`'s codes**, not prose invented for the fixture — `code` is the cross-layer identifier (§1.1), and a fixture that renames one produces a front end bound to a code the API will never send |
| `calculate_request.json` | A two-entry `POST /calculate` request (§6.2), mass-conserving per entry, and the request that produces `calculate_response.json` |
| `calculate_response.json` | The corresponding 200 body: `totals` plus two `entries`, dual scenario, with `by_destination` per entry and absent at the totals level |
| `calculate_response_single.json` | A 200 body with no alternative scenario: `alternative` and `net_benefit` null at both levels (§3 rule 4) |
| `stats.json` | A `GET /stats` response with a suppressed `other` bucket in every breakdown, an `unspecified` food-category bucket, and shares that sum to exactly 1 |
| `factors.json` | A `GET /factors` response: constants, five formulas, upstream and downstream rows including a **negative** downstream factor and a generic (`food_category: null`) row, all three `prevention` rows at zero, and `source_note` / `data_quality` on every row |
| `errors/*.json` | **Seven files, one per §9 code that has a fixed body**: `validation_error`, `unknown_code`, `unauthorized`, `blocked`, `rate_limited`, `formula_error`, `no_published_factor_set`. `errors/blocked.json` is the only one whose `details` is `null` rather than `[]` (§9.2) |

`errors/` does not carry the four codes v1.4 added to §9 — `NOT_FOUND`, `METHOD_NOT_ALLOWED`, `INTERNAL_ERROR`, `ENGINE_UNAVAILABLE` — and that is deliberate rather than an omission: their bodies are the same three-key envelope with a fixed `message` and an empty `details`, none is reachable from a front-end code path C or D can exercise, and a fixture per framework failure would add four files that pin nothing the envelope check does not already pin. `HTTP_ERROR` has no fixture for the same reason **and** because its status varies. If a code ever gains a body worth reading, it gains a fixture.

## 10.0 What Enforces This Section

A fixture that agrees with nothing is a fixture that drifts. Two test modules hold this set to the contract, and they check different things:

| Module | What it holds | Examples |
| --- | --- | --- |
| `tests/api/test_fixture_consistency.py` | The fixtures against **each other, the arithmetic, and `admin/seed.py`** — no HTTP, no app | Every decimal is a string at the contracted scale; no fixture leaks a primary key (§1.1); the request and the response describe the same calculation; every entry conserves mass to §6.2's 0.010 kg; a destination's factors do not change between scenarios; the response's own arithmetic closes; every line equals its formula applied to `factors.json`; every equivalence is derived from the metric total it names; `taxonomy.json`'s codes **and names** are the shipped seeds; `prevention` never appears in a current scenario; `stats.json`'s shares sum to 1; every §9 code has a fixture; `blocked` is the one `details: null`; `details[].field` uses the bracket form |
| `tests/api/test_api.py` | The fixtures against **real responses from the real app** | `test_contract_fixtures_have_the_same_top_level_shapes` (taxonomy, both calculate responses), `test_factors_fixture_matches_the_published_export`, `test_stats_fixture_shape_holds_against_a_populated_database`, and the per-code error assertions inside the behavioural tests |

Both matter, and neither substitutes for the other. The shape check proves the API can produce the fixture; it cannot prove the fixture's numbers are right, because `_assert_shape` compares JSON types and key sets rather than values — which is exactly how a `stats.json` of three empty arrays and a `calculate_response.json` of empty `metrics` passed for two revisions while giving C and D nothing to build against. The consistency check proves the numbers, and cannot prove the API emits them.

> **`_assert_shape` compares types, not text.** A string is a string. Anything whose correctness lives in the *content* of a string — §3 rule 5's `label` format is the case that has already bitten — is invisible to it and needs an assertion in `test_fixture_consistency.py` or a rule in this document. Preferably both.

## 10.1 Golden Test Suite (owner: A)

Under `tests/golden/`, three files per case:

```
case_01_landfill_dairy/
  bundle.json     a fixed factor set          <- shape defined in §10.2
  request.json    a fixed request
  expected.json   the expected full CalculationResult
```

Every change to the engine must leave all golden cases passing. This suite is the only evidence that the calculator computes correctly, and it is what the team can present at handover.

## 10.2 `bundle.json` Shape (owner: A)

One shape, three consumers: the golden suite above, `FactorBundle.from_json()` (§4.1), and the `dry_run.bundle` field of `POST /calculate` (§6.2.1).

It is a **complete, self-contained snapshot** — the taxonomy as well as the factors. §4.1's `has_destination()`, `has_sector()`, `has_food_category()` and `standard_mix_code()` are unimplementable otherwise, and staff must be able to trial a destination or food category that does not yet exist in the database.

```json
{
  "version_label": "GOLDEN-case-01",
  "is_mock": true,

  "sectors": [
    { "code": "processing", "name": "Processing / Manufacturing", "sort_order": 2 }
  ],
  "food_categories": [
    { "code": "standard_mix", "name": "Standard mix", "is_standard_mix": true,  "sort_order": 0 },
    { "code": "dairy",        "name": "Dairy",        "is_standard_mix": false, "sort_order": 6 }
  ],
  "destination_groups": [
    { "code": "disposal", "name": "Disposal", "is_waste": true, "sort_order": 3 }
  ],
  "destinations": [
    { "code": "landfill", "name": "Landfill", "group": "disposal", "sort_order": 1 }
  ],
  "metrics": [
    { "code": "co2e", "name": "Greenhouse gas", "unit": "kg CO2e",
      "display_unit": "kg CO2e", "display_precision": 1, "sort_order": 1 }
  ],

  "constants": [
    { "code": "GWP_CH4_100", "value": "28.0000000000", "unit": "", "note": "" }
  ],
  "formulas": [
    { "metric": "co2e", "expression": "qty_kg * (upstream + downstream)", "notes": "" }
  ],
  "upstream": [
    { "sector": "processing", "food_category": "dairy",
      "metric": "co2e", "value_per_kg": "1.9000000000" }
  ],
  "downstream": [
    { "destination": "landfill", "food_category": "dairy",
      "metric": "co2e", "value_per_kg": "0.9900000000" },
    { "destination": "landfill", "food_category": null,
      "metric": "cost", "value_per_kg": "0.0650000000" }
  ],
  "equivalences": [
    { "code": "km_driven", "name": "Kilometres driven", "source_metric": "co2e",
      "value_per_unit": "4.1800000000",
      "label_template": "Equivalent to driving {value} km", "sort_order": 1 }
  ]
}
```

| Convention | Reason |
| --- | --- |
| No `id` fields | §1.1 — `code` is the only cross-layer identifier |
| No `active` fields | Anything present in a bundle is active. §4.1 already states `metrics` is "active only"; filtering happens in the repository, and the engine does not re-check. |
| No `unit_presets` | Volume-to-kilogram conversion happens in the front end (§7.3); the engine only ever receives kilograms. |
| Every decimal is a **string** | §1.2. `from_json()` converts with `Decimal()`; `float` is never an intermediate. |
| `source_note` and `data_quality` are **optional and ignored** | They may appear on any `upstream`, `downstream` or `equivalences` row and may be `null`. The engine does not read them — provenance changes no number. **`from_json()` must accept and ignore them, never raise `BundleFormatError`, and `validate()` must not report them.** |

> **Why the provenance columns are optional here but required in §6.3.** v1.1 added `source_note` and `data_quality` to both factor tables and `source_note` to `equivalence` (§2.2). §6.3 is the public factor export and must carry them — that is what they are for. `bundle.json` is a different object with three consumers (§10.1's golden cases, `FactorBundle.from_json()`, and `dry_run.bundle`), none of which computes anything from provenance, so requiring them would mean writing a note on every row of every golden case to say nothing.
>
> Optional-and-ignored rather than forbidden, because the bundles that reach `from_json()` are not all hand-written. §8.2's **Save as regression case** action writes a `bundle.json` straight out of a dry run, and a dry-run bundle is the natural place to paste a `GET /factors` response — which carries both fields. A parser that rejects an unknown key turns that into a `BundleFormatError` on a bundle that is otherwise entirely valid, at the moment a staff member is trying to capture a case worth keeping.
>
> **The database and §6.3 are the authoritative provenance surface, not the bundle.** Provenance may be dropped on a round trip through a dry run; that is acceptable because an inline bundle is never written back (§6.2.1) and a golden case is not a factor source. If provenance ever has to survive a round trip, this convention is the line that changes.

`downstream[].food_category` may be `null`, meaning the row applies to every food category for that destination (§2.2 — this is how per-tonne charges such as the waste levy are expressed). **`null` is a legal key value, not a missing field**, and must survive both serialisation and deserialisation.
