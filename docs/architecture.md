---
title: "Kai Commitment Food Waste Impact Calculator — System Architecture"
subtitle: "UoA COMPSCI 399 Capstone, Project 18 — Team 16 (502 Bad Gateway)"
date: "2026-07-31 (v0.1 draft)"
---

# 1. Project Summary

Build a New Zealand food waste impact calculator for Kai Commitment (an initiative of the New Zealand Food Waste Champions 12.3 Trust). The product is modelled on the US ReFED Impact Calculator but uses New Zealand data and New Zealand definitions, and adds an economic cost metric that ReFED does not provide.

| Item | Detail |
| --- | --- |
| Client | Juliet Gerrard, with the Kai Commitment |
| Course | UoA COMPSCI 399 Capstone, Project 18 |
| Team | Team 16 — 502 Bad Gateway (5 members) |
| Deliverable | **Source code and documentation.** DNS, certificates, hosting and post-semester operations are out of scope. |
| Deployment shape | Standalone site on a subdomain (e.g. `calculator.kaicommitment.org.nz`), linked from the client's main site |

## 1.1 Confirmed Product Decisions

1. **Dual-scenario comparison.** A current scenario and an alternative scenario are each calculated, and net benefit is the difference between them.
2. **Fully configurable.** Categories, formulas, constants and factors all live in the database and are edited by staff through the admin panel. No code change and no redeployment is required.
3. **Real backend with anonymous collection.** No identifying information about the submitter is ever collected.
4. **No user tiers.** The public and Kai Commitment signatories use the same calculator. The admin panel is for staff only.
5. **Formulas are staff-editable only.** End users only enter data and read results.
6. **Two-tier data visibility.** Record-level detail is visible only in the admin panel; the public front end shows suppressed aggregates only.
7. **Factors and formulas are published openly.** The client explicitly requires this, and it is a transparency selling point.
8. **One calculation equals one submission.** There is no consent checkbox and no separate "contribute" button.
9. **Out of scope:** te reo Māori bilingual support, and a dedicated WCAG accessibility programme. Neither was requested by the client.

## 1.2 Current Data Status

The client has not yet supplied real emissions factors. **The first version runs entirely on placeholder (mock) factors** so that the full pipeline can be built and exercised; real values will be loaded and retested once available.

Accordingly, `factor_set` carries an `is_mock` flag. Whenever the active factor set is a mock set, every results page and every export **must display a placeholder-data warning banner**. This behaviour is hard-coded and cannot be switched off.

Recommended mock source: ReFED's published department-level factors. The magnitudes are plausible and the structure matches ours exactly. The `notes` field must record that the values are US-derived.

---

# 2. Technology Stack

| Layer | Choice | Rationale |
| --- | --- | --- |
| Front end | Plain HTML, CSS and JavaScript (ES modules); Chart.js for charts | No React experience on the team; interaction complexity here is manageable; no build step |
| Back end | Python 3.11+ / **FastAPI** | An existing in-house framework can be ported; single-language stack |
| ORM | SQLAlchemy 2.x with Alembic migrations | Do not hand-roll a database abstraction layer |
| Database | MySQL 8 (or PostgreSQL) | Chosen for concurrent writes and operability, not for capacity |
| Admin panel | `sqladmin` plus custom views | CRUD, search, filtering and permissions out of the box |
| Expression evaluation | `simpleeval` | Restricted evaluation; blocks attribute access; supports iteration limits |
| Testing | `pytest` | The calculation engine must have a golden-test suite |

**No Node.js.** With no front-end framework there is no build tooling, so the stack closes cleanly on Python.

**Numeric types: factors, currency amounts and masses all use `DECIMAL`; `FLOAT` is prohibited.** This system produces numbers that will be quoted externally, so floating-point error is a genuine defect source. On the Python side, use `decimal.Decimal` throughout.

---

# 3. System Layers

```
┌─────────────────────────────────────────────────────────┐
│  web/      Static front end                              │
│            (calculator / stats / home / documentation)   │
│            Sends requests and renders; performs no       │
│            impact calculation of its own                 │
└───────────────────────┬─────────────────────────────────┘
                        │ HTTP / JSON
┌───────────────────────▼─────────────────────────────────┐
│  api/      FastAPI routing layer                         │
│            Validation, rate limiting, error envelopes,   │
│            serialisation                                 │
└───────────────────────┬─────────────────────────────────┘
                        │
        ┌───────────────┴───────────────┐
        │                               │
┌───────▼──────────┐          ┌─────────▼──────────┐
│  engine/         │          │  admin/            │
│  Pure computation│          │  sqladmin plus     │
│  No I/O          │          │  draft / publish / │
│  Unit-testable   │          │  rollback / dry-run│
└───────┬──────────┘          └─────────┬──────────┘
        │                               │
        └───────────────┬───────────────┘
                        │
┌───────────────────────▼─────────────────────────────────┐
│  db/       SQLAlchemy models and repository functions    │
│            The only code that touches the database       │
└─────────────────────────────────────────────────────────┘
```

## 3.1 Key Constraints

**The engine must be a pure function.** `engine.calculate()` takes a pre-loaded `FactorBundle` and a request object and returns a result object. It never touches the database, reads configuration files, or reads the system clock. This lets the engine owner write the engine and its entire test suite before the schema is finalised.

**Calculation happens server-side, in exactly one place.** The front end does not duplicate formula logic. When staff change a formula there is no possibility of the two sides disagreeing.

**Metrics are data, not code.** The engine iterates over the `metric` rows in the database and evaluates each metric's formula:

```python
# CORRECT — adding a metric means inserting a row
for m in bundle.metrics:
    out[m.code] = engine.run_metric(m, bundle, scenario)

# WRONG — adding a metric means changing code and redeploying
out['co2e']  = engine.run(CO2E_FORMULA, ...)
out['water'] = engine.run(WATER_FORMULA, ...)
```

The second form still satisfies a literal reading of "one engine with injected formulas", but it locks flexibility into the deployment and therefore violates Decision 2.

---

# 4. Calculation Model

## 4.1 Scenarios and Conservation

A scenario is `(sector, food_category, [(destination, qty_kg), ...])`.

"Reducing the total amount wasted" is expressed through a special destination, **`prevention`**, whose factors are all zero — a 100% offset, matching ReFED's treatment. This keeps the two scenarios mass-conserving, so `net_benefit` cannot be inflated by simply assuming less waste in the alternative.

> `net_benefit[metric] = current[metric].total − alternative[metric].total`

## 4.2 Upstream / Downstream Split

Following ReFED's two-part model:

- **upstream** — impacts accrued through production, storage and transport up to the point of reference; varies by `(sector, food_category)`.
- **downstream** — impacts of disposal or redistribution; varies by `(destination, food_category)`. **Some destinations are negative** (an offset), so charts must render negative values correctly.

## 4.3 Formula Scope

**A formula describes the contribution of a single line; summation is performed by the engine.** That is:

```
line_value   = f(qty_kg, upstream, downstream, const_*)
metric_total = Σ line_value
```

Default expression: `qty_kg * (upstream + downstream)`

The benefit of this choice is that the expression language needs **no aggregate functions, no loops and no arrays**, which keeps the evaluator minimal and its security boundary obvious.

## 4.4 Methane Time Horizon

A request may carry `gwp_horizon ∈ {20, 100}` (default 100). The engine binds the variable `const_GWP_CH4` to either the `GWP_CH4_20` or the `GWP_CH4_100` constant according to that value. Formulas always reference `const_GWP_CH4` and never hard-code a horizon.

The first UI release may offer the 100-year basis only; the API field is reserved now.

---

# 5. Data Collection and Public Statistics

## 5.1 One Calculation Equals One Submission

`POST /api/v1/calculate` persists as it returns results. **There is no consent checkbox and no separate submit button** (Decision 8).

Deduplication uses a **session token**:

1. The first call carries no token; the server generates an opaque random string (UUID4) and returns it with the results.
2. The front end stores it in `sessionStorage` and sends it on subsequent calls.
3. The server **upserts the same row** keyed by that token. However many times a user re-runs the calculator in one session, there is exactly one row, always reflecting the latest run.

The token identifies **a draft record, not a person.** It lives for one hour; a scheduled job then nulls the `token` column, keeping the data but permanently severing the linkage.

**No browser fingerprinting. No IP addresses in the database.** A fingerprint is a persistent quasi-identifier and conflicts with Decision 3. Additionally, a "last submission within a rolling window wins" rule cannot be resolved until the window closes, which would make public statistics impossible to compute in real time and would cause already-counted records to be retracted. Token upsert achieves the same de-duplication with none of these problems.

## 5.2 Rate Limiting

Rate limiting and de-duplication are separate concerns and are implemented separately. Counters live in memory or Redis; **IP addresses are never persisted.**

| Endpoint | Suggested limit | Note |
| --- | --- | --- |
| `POST /api/v1/calculate` | 120 / hour / IP | A company or campus behind one NAT address must not be locked out |
| `GET /api/v1/*` | 600 / hour / IP | Read-only |

## 5.3 Junk Data Controls

Because every calculation enters the public aggregate, two gates are required:

1. **Server-side input bounds** — per-line maximum, per-scenario total maximum, maximum line count. Violations are rejected with `VALIDATION_ERROR`.
2. **Staff exclusion flag** — `submission.excluded_from_public` plus `exclusion_reason`. This is a staff moderation power, not a user consent mechanism, and is exercised from the admin panel.

## 5.4 Suppression Rule for Public Statistics

For every group returned by `GET /api/v1/stats`, any bucket with `count < SUPPRESSION_THRESHOLD` (default 5) is merged into an "other" bucket.

**Suppression is applied server-side during aggregation and is never delegated to front-end filtering** — filtering in the browser means the data has already left the server.

## 5.5 Wording of the Statistics Page

This is a self-selected sample, not survey data. The subject of every sentence must be the calculator itself:

- Correct: "Across the 1,247 calculations run in this tool, the distribution of destinations was…"
- Incorrect: "Distribution of food waste destinations in New Zealand"

**Prefer shares over absolute tonnages.** A headline such as "X tonnes recorded to date" is the figure most likely to be screenshotted out of context, which is a real reputational risk for the client.

## 5.6 Transparency Notice

A single line of static text in the page footer, plus a "what we record" link, stating that the tool stores the sector, food category and quantities entered for aggregate statistics and stores nothing that identifies a user or their business. It interrupts nothing and requires no click.

---

# 6. Factor Set Versioning

A `factor_set` is an atomic snapshot of an entire configuration: all factors, constants, equivalences and formulas for that version.

```
draft ──publish──> published ──superseded──> archived
  ▲                                             │
  └───────────────── rollback ──────────────────┘
```

| Requirement | Detail |
| --- | --- |
| Atomic publish | Publishing switches the whole set at once; a half-applied set is never live |
| Version stamp | Every `submission` records its `factor_set_id`, so historical results remain reproducible |
| Audit log | Every write is recorded in `audit_log` (who, when, what changed, before and after) |
| Rollback | Any archived version can be restored to published in one action |
| Dry run | A draft can be previewed against a test scenario, and **must not persist a submission** |

Audit logging and rollback are not optional. Staff are expected to update the algorithm continuously, so mistakes are inevitable; without rollback the only recovery path is direct database surgery.

---

# 7. Front End

## 7.1 Pages

| Page | Content | Data source |
| --- | --- | --- |
| Home | News cards plus a calculator entry point | **Pulls the client's WordPress `/wp-json/wp/v2/posts`**; no second news system is built |
| Calculator | Dual-scenario form, unit conversion, results and equivalences | `GET /taxonomy` and `POST /calculate` |
| Statistics | Donut chart (destination shares) and bar chart | `GET /stats` |
| Documentation | Methodology notes, published factor tables, background reading | Static content plus `GET /factors` |

Pulling home-page news from the WordPress REST API is deliberate. The client's main site already has a News section; a second one would require staff to post twice, and in practice would stop being updated within months. The client's `wp-json` endpoint is already open.

## 7.2 Code Organisation

No framework, but structure is still required: **a single state object plus render functions.** Ad-hoc DOM manipulation scattered through the codebase is not acceptable.

```
web/js/
  api.js         fetch wrapper and unified error handling
  state.js       single state object with subscribe/setState
  units.js       volume-to-kilogram conversion
  calculator.js  calculator page
  results.js     results rendering
  stats.js       statistics page
  charts.js      Chart.js wrapper
  news.js        WordPress news feed
```

## 7.3 Responsive Design

One codebase for desktop, tablet and mobile. **The calculator page is designed mobile-first**, baseline width 375px — two scenarios by many destination rows is the tightest screen in the product, and compressing a finished desktop layout afterwards does not work.

Semantic elements and `<label for>` associations should be used as a matter of writing correct HTML (they cost nothing), but no dedicated WCAG audit programme is planned.

---

# 8. Team Assignment

Work is split along interfaces to minimise cross-dependencies.

| Member | Module | Primary deliverable |
| --- | --- | --- |
| **A** | `engine/` | Calculation engine, restricted evaluator, golden test suite |
| **B** | `db/` and `api/` | Schema, migrations, repository layer, FastAPI routes; owner of the API contract |
| **C** | `web/` calculator | Dual-scenario form, unit conversion, results rendering |
| **D** | `web/` statistics and content | Statistics page, charts, home page, documentation page, responsive layout |
| **E** | `admin/` | Admin panel, draft/publish/rollback, dry-run page, audit log, project documentation |

**A and B are the most tightly coupled and should work in proximity.** C and D develop against the mock JSON supplied by B from day one and do not wait for the backend.

E's work looks the least technically demanding but produces the only interface the client will interact with over the long term. It should not be assigned to whoever cares least.

## 8.1 Precondition for Parallel Work

**The API contract and data structures in `docs/interfaces.md` must be frozen in week one.** Only once the contract is fixed can the five workstreams genuinely run in parallel; contract drift is the single most common source of rework on a five-person project.

Every contract change requires all three of: update the document, notify the whole team, update the mock JSON.

---

# 9. Implementation Order

| Stage | Content | Done when |
| --- | --- | --- |
| 1 | Taxonomy tables, factor tables, engine, golden tests | Correctness is locked down |
| 2 | Calculator front end (unit conversion, dual scenario) | End-to-end run against mock data |
| 3 | Admin CRUD, draft/publish/rollback, audit log | Staff can change factors unaided |
| 4 | Editable formulas (restricted evaluator wired into the panel) | Staff can change formulas unaided |
| 5 | Submission persistence, public statistics, suppression | Feature complete |
| 6 | Load real factors and retest | Blocked on client data |

Audit logging and rollback in Stage 3 must not be dropped.

## 9.1 Deployment

**`alembic upgrade head` must be run before the admin panel is started against a new or upgraded database.** `admin/app.py`'s `lifespan` bootstraps administrator accounts on start-up, but it does not migrate the schema — and `tests/conftest.py` builds the test database with `create_all()`, which also never runs a migration. `tests/test_migrations.py` catches the schema *drifting* out of sync with the models, but nothing in the suite catches a database that a migration was simply never applied to. The failure mode is a runtime `1054 Unknown column` against live traffic while the entire test suite stays green — this has already happened once against the development database during this branch.

`alembic/env.py` calls `load_settings()` to resolve the database URL, so `SECRET_KEY` (and every other setting `load_settings()` requires) must already be set in the environment for `alembic upgrade head` to run at all — a migration cannot be the step that first establishes the environment.

**`python -m admin.cli seed-taxonomy` must be run after `alembic upgrade head` against a fresh database.** The migration creates the six taxonomy tables empty; without the seed there are no destination groups, destinations, sectors, food categories, metrics or unit presets for the calculator or the admin panel to show. `admin/seed.py`'s `seed_taxonomy()` matches on `code` and only ever creates rows that are absent, so the command is safe to re-run on every deployment — a database that already has the taxonomy prints zero rows created per table and leaves every name a staff member has already edited through the panel untouched. It does *not* leave every edit untouched, though: `code` is itself an editable field (`FoodCategoryAdmin.form_columns` includes it), and re-running the seed against a taxonomy whose `code` has been changed does not recognise the renamed row as the one it already created — it creates a fresh row alongside it instead. For most tables that is merely a duplicate a staff member can deactivate. For `food_category.standard_mix` and `destination.prevention` it is worse: a renamed `standard_mix` plus a freshly seeded one both carry `is_standard_mix=True`, an invariant the calculator's engine depends on. `seed_taxonomy()` now flushes and calls `check_single_standard_mix()` and `check_prevention_intact()` (`admin/taxonomy_rules.py`) before returning, so this case aborts the whole transaction rather than committing a taxonomy the panel's own views would refuse — see `tests/admin/test_seed.py`'s `test_seed_refuses_to_commit_a_taxonomy_it_would_leave_broken`.

---

# 10. Open Items

| ID | Item | Blocks |
| --- | --- | --- |
| O-1 | When the client will supply real emissions factors (hard dependency) | Stage 6 |
| O-2 | Definition of the cost metric: beyond the waste levy, is the value of the wasted food itself included, and at cost price or retail price? | A, E |
| O-3 | Sources for New Zealand equivalence factors (kilometres driven, meal equivalents, showers) | A, D |
| O-4 | Any localisation beyond language (units, date formats) | C, D |
| O-5 | The seeded `food_category` table carries nine substantive Otago categories (plus `standard_mix`), but contract §2.1's prose says "the eight Otago baseline categories". The client's own source list has nine entries; `admin/seed.py` seeds all nine on the ruling that a category too many is a row a staff member can deactivate through the panel, while a category too few is data nobody can enter. Needs the client's word on whether the ninth category belongs, and the contract prose corrected either way. | E |
| O-6 | The seeded `unit_preset` rows (bucket and wheelie-bin sizes to kilograms) are placeholder conversions — the client has not supplied measured data. Every row's `source_note` says so; replace before the calculator is published. Neighbour of O-1. | E |

If O-2 remains unresolved, the first version implements waste levy plus disposal cost only, leaving the value of the food itself as an optional constant defaulting to zero.
