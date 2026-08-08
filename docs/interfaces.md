---
title: "Kai Commitment Impact Calculator — Interface and Data Contract"
subtitle: "Single source of truth for five-way parallel development"
date: "2026-08-09 (v1.2 draft)"
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

### v1.2 — 2026-08-09 (the merge of the two contract lines, **affects everybody**)

Two documents became one. Most of the work was mechanical; the corrections below were not. Eleven of the eighteen changes are defects that were already in the document before the merge — the merge is what made them visible, by putting statements next to the statements they contradict. **Three of them (1, 2, 5) were live blockers on A's and B's integration work**: a section of this document that had gone two revisions without being updated, and that two people were about to code against.

| # | Change | Section | Affects |
| --- | --- | --- | --- |
| 1 | **The two forks are merged.** v0.10–v0.13 (`admin_panel`) and v1.0–v1.1 (`docs/contract-v1.0`) both revised the same document in parallel for two days, and both were live and unmarked. Nothing was dropped from either side except §6.2's placeholder note, which said only "this file has not been reconciled with the other fork yet" and which this commit falsifies. The cost of the fork is concrete rather than theoretical: `admin/calc_client.py` and `admin/dryrun_views.py` were written on the `admin_panel` branch **against the other fork's §6.2**, because that was the only place the shape they needed existed. Code was being written against a contract that was not in the tree it was being written in | all | **all** |
| 2 | **§3, §4.2 and §5.3 now describe a multi-entry calculation, which is what §6.2 has described since v1.0.** They did not. `CalculationRequest` was `current`/`alternative` with `sector_code` on `ScenarioInput`; `calculate()` returned one `ScenarioResult` pair; `upsert_submission` took that request. Meanwhile §2.3 had grown `submission_entry` and §6.2 sent `entries[]` and returned `totals`. v1.0's own change-log entry is marked "Affects **A, B and C**" and only two of the three sections were ever changed. **This is what "A and B are the most tightly coupled" costs when it goes wrong:** A builds an engine that structurally cannot produce `totals`, B has to invent the cross-entry aggregation signature with no contract to code against, and the two inventions meet for the first time at integration — where the golden suite cannot adjudicate, because the golden suite tests the engine A built | §3, §4.2, §5.3 | **A, B** |
| 3 | **`totals` is computed by the engine, not summed in the API adapter, and §4.2 now says so with the reasoning.** An adapter in `api/` that adds per-entry metric totals together is a *second* impact-calculation site — the same defect as the browser doing it, differing only in which process runs the arithmetic. It would put the page's headline figure beyond the reach of the golden suite, which exercises `calculate()` and nothing above it, and it would make a non-additive roll-up a code change in `api/` — "metrics are data, not code" broken in the layer least likely to be reviewed for it. The rules for what the roll-up does with an entry that has no alternative (its current figures count on both sides, so its net benefit is zero and mass is conserved) are written out, because that is the one behaviour §6.2 states in prose and nowhere in a signature. §4.2 also names the one §6.2 response field that has **no** §3 counterpart and cannot have one — `factor_source`, which the engine has no way to know because it is pure over a bundle and cannot see where that bundle came from. The API layer supplies it from the branch it took when resolving the bundle. Without that sentence the "no arithmetic" rule reads as forbidding B from populating a field §6.2 requires | §3, §4.2 | **A, B** |
| 4 | **§6.2 now requires an entry's two scenarios to describe the same mass, to within 0.010 kg. Nothing enforced it before, in any version.** The dual-scenario design rests on the rule: `architecture.md` §4.1 states that the `prevention` destination — all factors zero — exists so that "wasting less" is expressed by *moving* mass to it rather than by sending less of it, precisely so `net_benefit` cannot be inflated by assuming away the waste. **An implementer building from §6.2 alone permitted exactly what `prevention` was designed to prevent**, and nothing downstream would have surfaced it: an alternative that simply drops a 1,200 kg landfill line yields a large fictitious `net_benefit`, `totals.total_kg` reports the current scenario's mass only so the two figures are never both on the page, and the golden suite cannot catch it because it tests the engine against a fixed request and this is a property of the *request*. The tolerance is **absolute and derived, not chosen**: the front end rounds each alternative line independently to 3 dp (≤ 0.0005 kg each) and §6.2 already caps a scenario at 20 lines, bounding drift at 0.010 kg at any tonnage. A relative tolerance is looser than the defect at 5,000 t and tighter than the unavoidable rounding at 2 kg | §3, §6.2 | **B, C** |
| 5 | **§6.2's dry-run row named two of the three submission tables.** It said no `submission` or `submission_line` row is written; `submission_entry` was added between that sentence and now. An implementer following it literally writes **orphan `submission_entry` rows on every staff dry run** — and §5.4 aggregates `by_sector` and `by_food_category` over exactly that table, so the pollution lands in the public statistics `X-Dry-Run` exists to protect, while `total_calculations` stays flat and conceals it. Staff run dozens of calculations while tuning one formula | §6.2 | **B, E** |
| 6 | **§5.4 now carries the entry-aggregation rule that §2.3 attributes to it, and the scenario filter that nothing has ever stated.** §2.3's `submission_line` note asserts "statistics aggregate over entries, not submissions (§5.4)" — and §5.4's `get_public_stats` and `StatsBucket` said nothing about entries. B implements §5.4 from §5.4. Joining `by_sector` to `submission` instead counts a multi-stage food business once, as whichever stage it entered first: the query returns a plausible number, nothing fails, and the population the calculator is most useful to is the one it silently mis-describes. **The second half is worse and no version of this document has ever said it:** `submission_line.scenario` is `ENUM('current','alternative')` and both scenarios live in one table, so a `by_destination` group-by with no scenario predicate counts hypothetical lines as real waste — **`prevention`, the destination for waste that did not happen, becomes a bucket in the public chart**, every `total_kg` roughly doubles, and §6.4's "the cumulative total entered into this tool" is false on its face. All three breakdowns and `total_kg` read `scenario = 'current'` only. Also states the consequence D has to write copy around — `total_calculations` counts submissions while every bucket `count` counts entries, so the two figures on the statistics page differ by design and must not be presented as a breakdown of one another | §5.4, §6.4 | **B, D** |
| 7 | **§7 replaced with the eleven modules that exist, transcribed from C's branch.** It named five, described two of those inaccurately, and omitted six — including `view.js`, which holds the escaping and formatting primitives D and E would each otherwise reimplement, and whose single `escapeHtml` is the reason that branch is XSS-clean. Two shapes changed **to match her code rather than the reverse**: a line is `{id, destination, qtyInput}`, where `id` survives a full re-render (`render()` replaces `main.innerHTML`) and `qtyInput` keeps the raw string so nothing rounds until it is sent. `unitPreset` and `unitCount` stay in this document as unmet requirements — the container-preset input was never built, `toKg` is imported by nothing — as does the `gwp_horizon` control | §7 | **C, D, E** |
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

> **Still open after this revision.** None of these is a defect in the document; all of them are decisions nobody has taken. **O-1 remains the hard blocker** — the client has not supplied real emissions factors, so everything runs on mock data and the banner stays mandatory. Beyond it: whether an in-memory rate-limit counter may hold a raw address (#15, B's); whether `admin.detection.looks_automated`, `RequestRate` and `_client_ip` move into a shared layer or are duplicated in `api/` (§8.3, B's, and the four blocklist items in `api/` are all downstream of it); whether `landfill_diverted` becomes a real `metric` row plus a formula (#8, the client's); and the positive/negative semantic colour pair, which C and D both need and neither has written down.

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

> `prevention` is a special destination with all factors set to zero. It expresses "waste avoided" and keeps the two scenarios mass-conserving.

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
| `display_unit` | VARCHAR(32) | NULL | Falls back to `unit` when null |
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
| `label_template` | VARCHAR(255) | NOT NULL | `Equivalent to driving {value} km` |
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
> that exception escape** — `admin/protection.py`'s `_client_ip` normalises the
> connection address itself and treats an unusable one as "no address"; B's
> middleware needs the same guard.
>
> **Rotating `SECRET_KEY` clears the blocklist.** An HMAC cannot be re-keyed the
> way an encrypted TOTP secret can — there is no plaintext address left to
> re-fingerprint from, which is the property this whole design wanted. So
> `python -m admin.cli rotate-key` deletes every `ip_block` row and reports how
> many, rather than leaving rows that `is_blocked` would never match and
> `unblock` could never remove. Blocks must be re-applied after a rotation.
>
> Auditing: `db/blocklist.py` writes no audit entry. See §8.3's "Blocklist"
> section for who writes one today, and for the reconciliation item this
> creates for the API layer.

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

**Four rules govern these types. Each is forced by §6.2 and none of them is A's to choose.**

1. **`entries` preserves request order.** §6.2 states it, and `submission_entry.sort_order` (§2.3) exists to persist it. It is what lets C pair a result with the row the user typed.
2. **`by_destination` is populated per entry and empty at the totals level.** §6.2: the same destination can appear under several entries drawing different upstream factors, so a cross-entry destination breakdown has no single correct aggregation rule. `MetricResult` is one type either way; at the totals level the tuple is empty and the serialiser omits the key. See §6.2 for the ruling on how the front end renders that breakdown.
3. **An entry with no alternative contributes its `current` result to `totals.alternative`.** This is what §6.2's "entries without one contribute zero to it rather than being excluded, so the totals stay mass-conserving" means in code: the entry's own `EntryResult.alternative` and `EntryResult.net_benefit` stay `None`, but the totals roll-up counts its current figures on both sides, so its contribution to `totals.net_benefit` is exactly zero and `totals.alternative`'s mass equals `totals.current`'s. Excluding it instead would make the alternative lighter than the current scenario and inflate net benefit — the precise failure the dual-scenario design exists to prevent.
4. **When *no* entry carries an alternative, `totals.alternative` and `totals.net_benefit` are both `None`,** and so is every `EntryResult.alternative` / `EntryResult.net_benefit`.

> **`ScenarioInput` is gone.** It held `sector_code`, `food_category_code` and `lines`; those three now live on `EntryInput`, split across `current` and `alternative`. It is deleted rather than emptied down to a single `lines` field, because a surviving `ScenarioInput` is exactly what B's `api/engine_adapter.py` currently populates with `req.current.sector_code`, and a type that keeps its name while losing its meaning is the one an integration will keep using by accident.
>
> **`totals.total_kg` on the wire is `totals.current.total_kg`** (§6.2 hoists it one level). It is the current scenario's mass, and one number is enough because §6.2 now **requires** each entry's two scenarios to describe the same mass to within 0.010 kg. That rule and this hoist depend on each other: the hoist is only honest while the rule holds, and the rule is only checkable at request time, because a response carrying one mass figure cannot expose a discrepancy between two. The alternative's per-entry mass remains available as `entries[].alternative.total_kg`. This is a wire-format hoist, not a fifth field on `CalculationTotals`.

---

# 4. Calculation Engine (owner: A)

Lives in `engine/`. **Pure functions: no database access, no file access, no system clock.**

## 4.1 `FactorBundle`

A fully loaded snapshot of one factor set. Constructed by the repository layer (§5.3) and treated as read-only by the engine.

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

> **One §6.2 response field has no §3 counterpart, by design: `factor_source`.** It cannot come from the engine — the engine is pure over a `FactorBundle` and has no way to know whether that bundle was loaded from the published set, from a named version, or handed to it inline in a request body. **The API layer supplies it**, from the branch it took when it resolved the bundle (§6.2.1's four-row table): `"published"`, `"version:<label>"` or `"inline"`. That is a fact the API layer already holds and the engine never had, so it is not a second calculation site and does not weaken the rule above. `factor_set_version` and `is_mock` do come from the bundle and therefore from `CalculationResult`.

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
    (§6.2) are never cached; they differ on every request."""

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

    - Excludes rows with excluded_from_public = TRUE
    - Merges any bucket with count < threshold into 'other'
    - Suppression happens here and is never delegated to the front end

    EVERY BREAKDOWN READS THE CURRENT SCENARIO ONLY.
    THE UNIT OF AGGREGATION IS THE ENTRY, NOT THE SUBMISSION (§2.3).

      by_sector         group over submission_entry.sector_id,
                        restricted to entries having current-scenario lines
      by_food_category  group over submission_entry.food_category_id, same
      by_destination    group over submission_line joined to its entry,
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
    code: str          # taxonomy code, or 'other'
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
> **`total_calculations` and the bucket counts are deliberately counting different things, and the statistics page must not present them as if they were not.** `total_calculations` is submissions; every `StatsBucket.count` is entries. `Σ by_sector[].count` is therefore ≥ `total_calculations`, and the gap is exactly the number of multi-entry submissions. `share` is computed within its own breakdown — over entries — so shares still sum to 1 and are the safe figure to display. Copy that reads "1,247 calculations" beside a sector chart whose counts add to 1,600 invites the obvious question; the honest phrasing names the unit ("1,247 calculations, covering 1,600 points in the supply chain"). See §6.4's copy constraint, which is D's.

## 5.5 Audit

```python
REDACTED_FIELDS = {"password_hash", "mfa_secret_enc", "code_hash"}

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

    Decimal values serialise as strings, never as float (§1.2).
    """
```

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
| No duplicate `destination` within one entry's scenario | `VALIDATION_ERROR` |
| No duplicate `(sector, food_category)` across entries | `VALIDATION_ERROR` |
| All codes exist | `UNKNOWN_CODE` |
| `dry_run` present without `X-Dry-Run: true` | `VALIDATION_ERROR` |
| `dry_run.factor_set_version` and `dry_run.bundle` both non-null | `VALIDATION_ERROR` |
| `dry_run.bundle` row count across all tables `<= 5000` | `VALIDATION_ERROR` |
| `dry_run.bundle` fails `FactorBundle.validate()` | `VALIDATION_ERROR`, one `details` entry per problem |

**Request headers**

| Header | Purpose |
| --- | --- |
| `X-Dry-Run: true` | **Do not persist.** No `submission`, **no `submission_entry`** and no `submission_line` row is written, and no token is returned — the three tables of §2.3 are untouched, not two of them. **Requires an authenticated staff session** (§8.4); an unauthenticated request carrying this header is rejected with `UNAUTHORIZED` (401). |

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

Without this field a staff member who gets an unexpected number cannot tell whether their data failed to take effect or their formula is wrong. On a dry run `token` is `null`.

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
    { "code": "km_driven", "source_metric": "co2e",
      "value_per_unit": "4.1800000000",
      "label_template": "Equivalent to driving {value} km",
      "source_note": null }
  ]
}
```

**`source_note` and `data_quality` are part of this response, and both may be `null`.** v1.1 added `source_note` to `factor_upstream`, `factor_downstream` and `equivalence`, and `data_quality` to the two factor tables only — `equivalence` has no `data_quality` column (§2.2) — and this endpoint is the whole reason they exist: v1.1's stated rationale is that a calculator which cannot say which of its numbers are measured and which are borrowed cannot be defended in public, and §6.3 is the only public surface where a number can say so. A factor export that carries the values and drops their provenance publishes exactly the figure that is hardest to defend, with the defence removed. `null` is a legal value — most rows will carry `null` until the client supplies real data — and it must appear as `null`, not as an omitted key, so a consumer can tell "no provenance recorded" from "this endpoint does not report provenance".

With `format=csv`, one CSV file per table is returned, bundled as a zip archive (`Content-Type: application/zip`). The two provenance columns are columns in the `upstream` and `downstream` CSVs like any other.

## 6.4 `GET /api/v1/stats`

**200 response**

```json
{
  "generated_at": "2026-09-01T03:00:00Z",
  "total_calculations": 1247,
  "suppression_threshold": 5,
  "by_destination": [
    { "code": "landfill", "label": "Landfill", "count": 512,
      "share": "0.4105", "total_kg": "884200.000" },
    { "code": "other", "label": "Other (sample too small)", "count": 9,
      "share": "0.0072", "total_kg": "3100.000" }
  ],
  "by_sector": [ "… same shape …" ],
  "by_food_category": [ "… same shape …" ]
}
```

`total_calculations` counts **submissions**; every bucket `count` counts **entries** (§5.4). One submission can carry up to twenty entries, so the bucket counts will exceed `total_calculations` and are not a breakdown of it. `share` is computed within its own breakdown and does sum to 1.

> **Copy constraint (owner: D).** The subject of the statistics page must be the calculator itself — "Across the 1,247 calculations run in this tool…" — and **never** "Distribution of food waste destinations in New Zealand". Prefer `share`; if `total_kg` is displayed it must be explicitly labelled as the cumulative total entered into this tool. Do not label a bucket `count` as a number of calculations — it is a number of supply-chain points entered, and the two figures on this page differ by design.

## 6.5 Rate Limits

| Endpoint | Limit |
| --- | --- |
| `POST /api/v1/calculate` | 120 / hour / IP |
| `GET /api/v1/*` | 600 / hour / IP |

Exceeding a limit returns `429` with `RATE_LIMITED`. Counters live in memory or Redis.

**No IP address is persisted by the rate limiter, and none is persisted anywhere in this system except the one exception §2.3 records.** That exception is `ip_block`, which stores an HMAC of an address — never the address — and only for a caller a staff member or the automatic protection has blocked. This sentence used to read "IP addresses are never persisted" without qualification; v0.12 added the blocklist exception in §2.3 and this line was not updated, so the document asserted an absolute and its exception in two places at once. The absolute is the one that was wrong: a reader implementing §6.5 literally would have had grounds to call §2.3's table a contract violation.

> **Open, and B owns it: whether an *in-memory* counter may be keyed on a raw address.** §6.5 sets its bar at persistence, and `api/rate_limit.py` keys on `"post-calculate:203.0.113.9"`, which satisfies it. §2.3's prohibition has been read as covering process memory too, and `admin/protection.py` keys on the §2.3 fingerprint accordingly. **The two layers currently apply different privacy standards to the same data**, which is not a defensible position for a calculator whose stated selling point is that it stores nothing about the visitor. Not resolved here because §6.5 is B's section and this is a decision, not a correction. Whichever way it goes, both layers move together.

---

# 7. Front-End Modules (owners: C and D)

ES modules, no build step. Located in `web/js/`.

**Eleven modules, nine of them C's and built.** Until v1.2 this section named five and described two of those inaccurately — six real modules were absent, including `view.js`, which holds the escaping and formatting primitives D and E would otherwise each reimplement. The signatures below are transcribed from the branch, not proposed for it. Where C's code and the old contract disagreed on shape, **the contract has changed to match her code** and says so at the point of change; where a contract requirement is genuinely unmet, it is marked **Not built** and stays a requirement.

## 7.1 `api.js` (written by C, shared with D and E)

The only module that calls `fetch` — verified across the branch. **No other module calls `fetch` directly.**

```js
export class ApiError extends Error {
  constructor(code, message, details = [], status = 0);
  code;      // string — §9 error code, or 'NETWORK_ERROR' | 'HTTP_ERROR' | 'MOCK_FIXTURE_ERROR'
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

> **Current behaviour, not a requirement — do not reimplement this.** The mock path re-synthesises the `mass` metric from the request body in JavaScript before returning the fixture. It is simulating a server rather than violating §7.6, but it means the numbers on screen in a mock demo were computed in the browser, which is the one property mock mode should not have in common with a bug. It must be rewritten when the fixtures move to the `entries` / `totals` shape (§10), and the rewrite should **serve the fixture as written** rather than deriving anything from the request.

**Known defect, not a contract question:** the fixture path is absolute from the site root (`fetch('/tests/fixtures/…')`). That works under `python3 -m http.server` at the repo root and breaks the moment FastAPI serves `web/` as the static root — which breaks C, D and E simultaneously, because all three develop in mock mode.

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

**Still requirements, and still unmet:**

- **`unitPreset` and `unitCount` are absent from the line shape because the container-preset input was never built.** `taxonomy.unit_presets` is never read and `units.js`'s `toKg` is never imported. §7.3 remains a live requirement, not a documented omission.
- **`gwpHorizon` is set to 100 at initialisation and no control ever writes it.** §6.2 makes the horizon user-selectable between 20 and 100; a stated requirement is currently unmet and invisible on screen.

> **Documented exception to "no ad-hoc DOM manipulation".** Two modules deliberately bypass `setState` and mutate the DOM directly on keystroke (`calculator.js`, `improvement.js`). The reason is sound — `render()` replaces `main.innerHTML`, so a `setState` per keystroke destroys the focused input — but the exception must be documented rather than merely present, because the two fast paths have already drifted: the typed path and the re-rendered path apply **different validity rules to the same field**. Any change to a validation rule has to be made in both.

## 7.3 `units.js` (written by C)

**All front-end mass arithmetic belongs in this module.** That is the whole point of §7.6 rule 1: the front end's arithmetic can be audited in one file. It currently is not — `tonnes ? 1000 : 1` is duplicated at six sites across `results.js` and `improvement.js`, which is not a correctness bug today and defeats the rule.

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
 * ** Currently imported by nothing — see §7.2, the preset input is not built. **
 */
export function toKg(count, presetCode, presets);

/**
 * @param {number|string} amount
 * @param {'kilograms'|'tonnes'} unit
 * @returns {number|null}  null when amount is not finite
 */
export function massToKg(amount, unit);

/** massToKg(...) fixed to 3 decimal places, i.e. API-ready.
 *  @returns {string|null}  Currently imported by nothing. */
export function kgString(amount, unit);
```

## 7.3a Calculator Modules (written by C)

Six modules that no version of §7 named. Transcribed from the branch.

### `view.js` — the shared primitives

**D and E consume this module rather than reimplementing it.** One `escapeHtml`, applied at every interpolation site, is the single reason C's branch is XSS-clean; a second copy in D's or E's code is a second thing to get right.

```js
/** &, <, >, " and ' -> entities. Safe for text and double-quoted attributes. */
export function escapeHtml(value = '');

/** Number(value).toLocaleString('en-NZ', {maximumFractionDigits: precision});
 *  returns 'Not available' for a non-finite input. */
export function formatNumber(value, precision = 2);

/** lower-case, non-alphanumerics -> '-', trimmed. For DOM ids and class names. */
export function slug(value);

/** HTML for the standard Back / primary-action pair. Emits
 *  data-action="go-step" data-step="<backStep>" and data-action="<action>". */
export function buttonRow(backStep, label = 'Continue', disabled = false, action = 'continue');
```

> **Precondition, stated because D and E will now depend on it:** `escapeHtml` does not escape backticks or `/`, so it is safe only in **double-quoted** attribute contexts and in text. Every attribute in C's branch is double-quoted. An unquoted attribute breaks the guarantee silently.
>
> **Known gap:** `formatNumber` sets no `minimumFractionDigits`, so a cost of exactly `825.00` renders as "825" at `display_precision: 2`. §6.1 supplies `display_precision` for both bounds.

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

Module-private and worth knowing: `validateCurrentStep()` returns a display string or `''`; `buildLines(entry)` produces `[{destination, qty_kg}]` filtered to `qty_kg > 0`; `publicError(error)` maps a §9 code to user copy; `fieldErrorMap(error)` turns `details[]` into `{fieldPath: message}`; `submitCalculation()` issues the request.

> **`prevention` is excluded from the destination entry step and included in the improvement panel.** That modelling is correct and must survive any refactor — `prevention` is how the alternative scenario expresses waste avoided (§2.1), and offering it as a current-scenario destination would let a user claim to be already preventing what they are about to describe wasting.
>
> **Two defects here are contract-relevant.** `fieldErrorMap` keys on the raw `details[].field` string while the render loop looks up `current[<index>].qty_kg` — §9's format is `entries[0].current[1].qty_kg`, so it never binds; and the index is the position in `state.current`, which includes blank rows, while `buildLines` filters them out before sending, so the request index and the render index differ whenever any destination is left empty, which is the normal case. Both fail silently: no error, no console warning, the user sees only the generic banner. The index half is a bug under any `field` format.

### `results.js` — the results screen

```js
/** The step-5 screen: mock banner (when any response has factor_set.is_mock),
 *  impact summary cards, tangible equivalents, a three-tab breakdown
 *  (stage / destination / food), a methodology-and-limitations block naming
 *  the factor version, action buttons, and the improvement panel.
 *  @returns {string} HTML */
export function renderResults(state);

/** Builds a plain-text report and triggers a Blob download as
 *  'food-waste-impact-results.txt'. */
export function downloadResults(state);
```

> Most of this module is currently a client-side aggregation layer that sums engine-computed metric totals, equivalence values and destination rows across entries. **All of it is deleted** by reading `totals` and `net_benefit` from §6.2 instead; the destination tab is rebuilt per entry per the ruling in §6.2. What survives is `summaryCards` (which already iterates metrics correctly), the tab/table/bar markup, and the download plumbing.
>
> Two hard-codings must go with it: the breakdown table hard-codes the columns `CO₂e / Cost / Water`, and the equivalence list hard-codes its own labels for `km_driven` / `meals` / `showers`, discarding the `label` the API renders from `label_template`. Both defeat the promise that a new metric or equivalence costs one `INSERT` (§2.1), and the second overrides the client's approved wording with C's.

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
export async function compareImprovement(state);
export function ImprovementScenario(state);           // collapsed CTA or open panel
export function ComparisonResults(state);             // '' until a comparison exists
```

> **Charts must render negative values.** `downstream` may be negative (§2.2) and a metric total therefore may be too, but the comparison bars currently apply `Math.abs()` to their widths, so −500 and +500 draw identically. The reuse-and-offset story is the client's headline message and it is currently invisible. The `.value-positive` / `.value-negative` / `.value-zero` CSS already exists in the stylesheet and is referenced by nothing.
>
> The alternative lines are built as `(totalKg × percentage / 100).toFixed(3)` **per line independently**, so Σ parts can differ from the entry total by up to 0.0005 × n. The dual-scenario design depends on the two scenarios conserving mass; this can break it by fractions of a gram. **Settled in v1.2, in C's favour:** §6.2's mass-conservation rule is derived from exactly this behaviour and its 0.010 kg tolerance is 20 lines × 0.0005 kg, so the drift this module produces is accepted rather than rejected — but only because §6.2 also caps a scenario at 20 lines per entry. The worst case sits on the boundary, and the comparison is `<=`. If that cap ever rises, this allocation must round to a running remainder instead.

### `main.js` — entry point for `index.html`

No exports. Wires `subscribe(→ renderChrome + render)`, calls `bindCalculator`, binds the header home and "Clear all data" buttons, defines `loadTaxonomy({preserveError})` (also passed to `bindCalculator` as the `UNKNOWN_CODE` reload path), and performs the first render and taxonomy fetch.

> **Known defect:** it calls `main.focus()` after *every* `setState`. Arrow-key navigation inside the sector radio group fires `change` → full re-render → focus yanked to `<main>`, so a keyboard-only user cannot get past step 1. This undoes a substantial and otherwise well-built accessibility layer.

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
3. The calculator page is **mobile-first**, baseline width 375px.
4. After every successful calculation, write the returned `token` back to `sessionStorage`.
5. **Iterate over metrics and equivalences; never hard-code their codes.** A view that lists `['co2e','water','cost']` silently omits the metric a staff member added, and adding a metric is meant to cost one `INSERT` and one formula (§2.1).
6. **Charts must render negative values.** `downstream` may be negative (§2.2), so a metric total may be too. Discarding the sign hides the reuse-and-offset result the calculator exists to show.

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

`db/blocklist.py` (owner: E, layer: `db/`) is the whole of the write/read surface: `block_ip`, `unblock_ip`, `is_blocked`, `ip_fingerprint`. `admin/protection.py`'s `ProtectionMiddleware` (owner: E) reads `is_blocked` ahead of every other check on every request under `/admin`, with no exemption — not even for an authenticated staff session, because a block is another administrator's deliberate act. It never writes to the blocklist itself.

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
> **The same unresolved split applies to three more names.** `admin.detection.looks_automated`, `admin.detection.RequestRate` and `admin.protection._client_ip` are all in `admin/` and are all things B's public-traffic middleware needs. They were built there because `admin/` is where E's stage lived, not because that is where they belong: `detection.py` imports nothing outside the standard library and `_client_ip` imports nothing outside Starlette, so neither has any reason to sit above the layering boundary. **Recommendation:** move `detection.py` to `db/` or to a new shared module and leave `admin/protection.py` importing it, rather than have `api/` duplicate the header-marker list and the sliding-window counter — two copies of a detection rule drift, and the copy that stops matching is the one nobody notices. `_client_ip` should move with it. **This is genuinely unresolved, and B decides it**, since a move changes a file in her layer; E's recommendation is on record here so that the alternative (duplication) is a choice someone made rather than the default nobody discussed. Note also that `RequestRate` is per-process, so under more than one worker the effective limit is multiplied by the worker count — the API's own rate limiting (§6.5) is a separate problem and E has not solved it.

**The one case this whole design is built around not causing:** an administrator blocks the address they are sitting behind, and the block itself now stands between them and every page that would let them undo it — including the login page, because the blocklist check has no exemption. `python -m admin.cli unblock <address>` (above) is the only way back short of editing the database by hand, and is the reason that command exists at all.

`ProtectionMiddleware` also carries a stateless header check and a per-address rate limit (`PROTECTION_MAX_REQUESTS_PER_MINUTE`), both configurable and both able to be turned off in one place: `PROTECTION_ENABLED=false` disables the blocklist, the header check and the rate limit together, with no finer-grained switch — the documented escape hatch for a false-positive lockout that is not a blocklist entry. **It needs no code change, but it does need a process restart**: settings are read once, by `load_settings()` at start-up. See `docs/architecture.md` §9.1.1 for the operational detail, the `PROTECTION_TRUSTED_PROXY` warning, the four recovery paths, and this design's explicit limits.

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
    "details": [ { "field": "entries[0].current[1].qty_kg", "issue": "exceeds_max" } ]
  }
}
```

**`field` is a bracket-indexed path into the request body**, exactly as a front end would write it: `entries[0].current[1].qty_kg`. Array positions are `[n]`, object keys are `.key`, and the path starts at the root of the request.

> This needs stating because the two obvious implementations disagree and the disagreement is silent. Pydantic's native `loc` is a tuple that renders as `entries.0.current.1.qty_kg`; a front end building a lookup key from its own render loop writes `entries[0].current[1].qty_kg`. Neither is wrong, but if the API emits one and the client looks up the other, **field-level highlighting simply never binds** — no error, no console warning, the user just sees the generic banner and never learns which row is bad. Both fixture sets on the team had already chosen different formats. The API is responsible for converting Pydantic's `loc` to this form before it goes on the wire.

| HTTP | `code` | Trigger | Front-end response |
| --- | --- | --- | --- |
| 400 | `VALIDATION_ERROR` | Missing field, out of bounds, duplicate destination, malformed dry-run bundle | Highlight the offending field |
| 400 | `UNKNOWN_CODE` | A code in the request does not exist | Re-fetch the taxonomy and prompt a refresh |
| 401 | `UNAUTHORIZED` | `X-Dry-Run: true` without a valid staff session | Redirect to `/admin/login` |
| 403 | `BLOCKED` | The caller's address is on the blocklist (`db.blocklist.is_blocked`, §2.3) | Show the `message` and stop. **Do not retry, and do not offer a retry button** |
| 429 | `RATE_LIMITED` | Rate limit exceeded | Ask the user to retry later; disable the button for 60s |
| 500 | `FORMULA_ERROR` | A staff-configured formula is invalid | See below |
| 503 | `NO_PUBLISHED_FACTOR_SET` | No factor set has been published | Show "calculator under maintenance" |

`message` is written for end users and may be displayed verbatim. `details` is for developers and form-field targeting.

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

---

# 10. Mock Data Convention

Located in `tests/fixtures/`. C and D consume these directly before the backend is ready.

| File | Content |
| --- | --- |
| `taxonomy.json` | A complete sample `GET /taxonomy` response |
| `calculate_request.json` | A standard request sample |
| `calculate_response.json` | The corresponding full response (dual scenario) |
| `calculate_response_single.json` | A response with no alternative scenario |
| `stats.json` | A sample `GET /stats` response including an `other` bucket |
| `factors.json` | A sample `GET /factors` response |
| `errors/*.json` | One sample per error code, including `errors/unauthorized.json` and `errors/blocked.json` |

**These files are the executable form of the contract.** Backend contract tests assert that real responses match their shape; the front end develops against them directly. They must be updated whenever the contract changes (see §0).

> **As of v1.2 this section describes an intention, not a directory. `tests/fixtures/` does not exist on any branch a reader of this document is likely to be standing on** — not on `main`, not on `admin_panel` (which owns this document), not on `docs/contract-v1.0`. **Two sets exist, on two unmerged branches, and they disagree with each other and with this contract:**
>
> | Branch | Files | Shape |
> | --- | --- | --- |
> | `origin/database` (B) | 12 | Flat, pre-v1.0: top-level `sector` / `food_category` / `current`. Carries `factor_source`. `details[].field` is Pydantic's dotted `current.0.qty_kg` |
> | `origin/Demo-UI` (C) | 10 | Flat, pre-v1.0. **No `factor_source`.** `details[].field` is the bracket form `current[0].qty_kg` |
>
> Neither matches §6.2, and a fixture that disagrees with the contract does not fail — it produces code bound to fields the API will never send. The `field` disagreement is the sharpest example: v1.0 §9 ratified C's bracket form and extended it to `entries[0].current[1].qty_kg`, so **B's fixture is the one that must change**, and until it does her handlers and C's lookup keys will never bind to each other.
>
> **The canonical set lands once, in the v1.2 shape, with the B integration PR.** Not twice and not in parallel: two people writing fixtures from one contract produce two sets that differ wherever the contract is silent, which is exactly where a fixture is load-bearing. B owns §6 and therefore owns the shape. **Start from B's twelve, not C's ten** — hers is the superset, it already has `factor_source` and the two files C lacks, and it is the set her contract tests assert against. C's `taxonomy.json` is by far the better *content* (six sectors with real descriptions, ten NZ-appropriate food categories, nine MfE destinations) and should fill it.
>
> What each file needs:
>
> | File | State |
> | --- | --- |
> | `calculate_request.json`, `calculate_response.json` | On both branches, both flat. Rewrite to `entries[]` and `totals` + `entries[]`, and make the pair **correspond** — B's currently do not, and hers is not mass-conserving either, which §6.2's new validation rule now rejects outright |
> | `calculate_response_single.json` | **On `origin/database` only.** Reshape; also `"total_kg": "1"` breaks the 3-decimal rule her own validator enforces |
> | `errors/unauthorized.json` | **On `origin/database` only.** Shape-correct; carry it across |
> | `errors/blocked.json` | **Absent from every branch — the only genuinely missing file.** `BLOCKED` was added in v0.13 and has never had a fixture. It is the one error whose `details` is `null` rather than `[]` (§9.2), and every existing error fixture on both branches uses `[]`; a set that makes `[]` universal is how that requirement gets implemented away |
> | `errors/validation_error.json` | On both, in **two different `field` formats**. §9's bracket path is the ratified one |
> | `errors/{unknown_code,rate_limited,formula_error,no_published_factor_set}.json` | On both, shape-correct under §9 |
> | `taxonomy.json` | On both. Must contain a `prevention` destination and at least one destination in the `reuse` group — without them the mass-conserving offset and the entire non-waste half of the MfE taxonomy, which is the client's headline story, cannot be demonstrated at all |
> | `stats.json` | On both, both effectively empty. Must contain a suppressed `other` bucket: §6.4's copy constraint is the thing D has to write against and there is nothing to write against without one |
> | `factors.json` | On both. Must not be all-empty arrays, or the methodology page renders "No published formulas were returned" in every demo |

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
