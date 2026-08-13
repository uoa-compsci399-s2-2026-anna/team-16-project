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
| Front end | Plain HTML, CSS and JavaScript (ES modules). Chart.js is **selected but not yet present** — see the note below | No React experience on the team; interaction complexity here is manageable; no build step |
| Back end | Python 3.11+ / **FastAPI** | An existing in-house framework can be ported; single-language stack |
| ORM | SQLAlchemy 2.x with Alembic migrations | Do not hand-roll a database abstraction layer |
| Database | MySQL 8 (or PostgreSQL) | Chosen for concurrent writes and operability, not for capacity |
| Admin panel | `sqladmin` plus custom views | CRUD, search, filtering and permissions out of the box |
| Expression evaluation | Standard library `ast`, hand-written whitelist (`engine/evaluator.py`) | Restricted evaluation; refuses every node type it does not name; located errors from `lineno`/`col_offset`; `admin/expressions.py` is its static twin over the same tree. `simpleeval` was the intended choice through contract v1.9 and was never imported — see `interfaces.md` §4.3 |
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

> **This paragraph became true on 2026-08-09, and the sentence before that date is worth knowing.** For five contract revisions "whose factors are all zero" described only the *downstream* half: `factor_upstream` was keyed on `(sector, food_category, metric)`, could not see the destination, and therefore applied the entry's full upstream factor to a line sent to `prevention`. On the shipped mock factors that left 79% of the benefit of preventing waste out of the answer, one-directionally, on the client's headline claim. **O-7 closed it** — `factor_upstream` gained a nullable `destination_id`, so `prevention` now carries its own upstream row at zero and both terms of `line_value` vanish for a prevented line. §10's O-7 records what was done and what was rejected. The claim is now enforced by data rather than asserted by prose, which means it can also be *un*-enforced by data: a `(sector, food_category, metric)` given a general upstream row and no `prevention` counterpart silently reverts to the old behaviour for that tuple. `tests/api/test_fixture_consistency.py::test_prevention_is_a_whole_offset_upstream_as_well_as_down` is what notices.

## 4.2 Upstream / Downstream Split

Following ReFED's two-part model:

- **upstream** — impacts accrued through production, storage and transport up to the point of reference; varies by `(sector, food_category)`, and **may optionally be overridden for one destination** (O-7). The destination is nullable and null is the normal case: producing a kilogram of dairy costs what it costs whatever later becomes of it. The one row that overrides it is `prevention` at zero — food that was never wasted was never produced. Lookup order: exact destination, then the null row, then zero.
- **downstream** — impacts of disposal or redistribution; varies by `(destination, food_category)`. **Some destinations are negative** (an offset), so charts must render negative values correctly.

Note what this split does **not** do: it does not put the destination into the formula. `line_value = f(qty_kg, upstream, downstream, const_*)` has exactly the shape it had before O-7 — only the value bound to `upstream` changes, because the destination is resolved in the *lookup* rather than applied in the evaluator. That is the property that made the schema option cheaper than the line-variable option, and it is the reason `interfaces.md` §4.3's variable table and `admin/expressions.py`'s `BASE_VARIABLES` were untouched by the change.

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
  api.js         fetch wrapper and unified error handling     built
  state.js       single state object with subscribe/setState  built
  units.js       volume-to-kilogram conversion                built
  calculator.js  calculator page                              built
  results.js     results rendering                            built
  view.js        escaping and formatting primitives           built
  main.js        calculator page entry point                  built
  methodology.js documentation page                           built
  improvement.js improvement-scenario controls                built
  stats.js       statistics page                              not built (D)
  charts.js      Chart.js wrapper                             not built (D)
  news.js        WordPress news feed                          not built (D)
```

**Chart.js is selected, not vendored.** No file under `web/` imports it — there is no `<script src>`, no `new Chart(` and no copy of the library in the tree. `calculator.js` draws its one graphic as hand-written inline SVG and `results.js` renders bars as CSS-width `<span>` elements, so nothing built so far has needed a charting library. Chart.js is the library the statistics page will use when D builds it, and `interfaces.md` §7.6 rule 7 requires it to be **self-hosted** under `web/assets/` rather than loaded from a CDN. `interfaces.md` §7.4 specifies the wrapper's interface in advance; that section is a specification, not a description.

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

**`python -m admin.cli seed-taxonomy` must be run after `alembic upgrade head` against a fresh database.** The migration creates the six taxonomy tables empty; without the seed there are no destination groups, destinations, sectors, food categories, metrics or unit presets for the calculator or the admin panel to show. `admin/seed.py`'s `seed_taxonomy()` matches on `code` and only ever creates rows that are absent, so the command is safe to re-run on every deployment — a database that already has the taxonomy prints zero rows created per table and leaves every name a staff member has already edited through the panel untouched. It does *not* leave every edit untouched, though: `code` is itself an editable field (`FoodCategoryAdmin.form_columns` includes it), and re-running the seed against a taxonomy whose `code` has been changed does not recognise the renamed row as the one it already created — it creates a fresh row alongside it instead. For most tables that is merely a duplicate a staff member can deactivate. For `food_category.standard_mix` it is worse: a renamed `standard_mix` plus a freshly seeded one both carry `is_standard_mix=True`, an invariant the calculator's engine depends on. The prevention destination is no longer in this position — since contract v1.22 its role is `destination.is_prevention` and a rename carries the tick with it, so a re-seed adds an unflagged duplicate rather than a second claimant. `seed_taxonomy()` flushes and calls `check_single_standard_mix()` and `check_prevention_destination()` (`admin/taxonomy_rules.py`) before returning, so this case aborts the whole transaction rather than committing a taxonomy the panel's own views would refuse — see `tests/admin/test_seed.py`'s `test_seed_refuses_to_commit_a_taxonomy_it_would_leave_broken`.

A correctly installed system answers `NO_PUBLISHED_FACTOR_SET` (503,
"calculator under maintenance") until staff create and publish a factor set.
`seed-taxonomy` deliberately creates none — the taxonomy is the vocabulary,
the factors are the data, and the client has not supplied real factors yet.
Reaching that 503 on a fresh install is expected, not a fault.

### 9.1.1 E-8: panel protection

`ProtectionMiddleware` (`admin/protection.py`) sits ahead of every route under
`/admin`, static files excepted, and refuses a request in three ways: the
blocklist (`db/blocklist.py`, §2.3), a stateless check on header shape
(`db/detection.py`'s `looks_automated`, re-exported as `admin.detection`), and
a per-address rate limit. All
three are controlled by three settings, none of which existed in `.env.example`
before this stage — a genuine gap, since the first of them is the only way
out of a false-positive lockout:

| Setting | Default | Meaning |
| --- | --- | --- |
| `PROTECTION_ENABLED` | `true` | Whether any of the three checks run at all. **This is the escape hatch.** Setting it to `false` and restarting turns off the blocklist, the header check and the rate limit together — there is no finer-grained switch. Use it when protection itself is producing the lockout (a false-positive header match, a shared address hitting the rate limit) and reaching the CLI's `unblock` command would not fix it, because the block is not what is refusing the request. |
| `PROTECTION_MAX_REQUESTS_PER_MINUTE` | `30` | Requests per minute, per address, before further ones are refused with 429. Counted in this process's own memory (`db/detection.py`'s `RequestRate`, shared with the public API's own limiter) — running more than one worker multiplies the effective limit by the worker count, since each worker holds its own counter. Must be 1 or greater; `admin/config.py` refuses to start on `0` or a negative value, because a limit of `0` refuses the first unauthenticated request from every address, `/admin/login` included. |
| `PROTECTION_TRUSTED_PROXY` | `false` | Whether to read the caller's address from `X-Forwarded-For` instead of the raw TCP connection. **Must stay `false` unless a reverse proxy that itself overwrites `X-Forwarded-For` genuinely sits in front of this panel.** With no such proxy, `X-Forwarded-For` is a header any caller can set to any value — trusting it lets one visitor forge another's address, collapses the rate limit into a single shared counter, and can turn one legitimate block into a block on every visitor at once. |

**Every one of these settings needs a process restart to take effect.**
`load_settings()` (`admin/config.py`) reads the environment once, at start-up,
and `Settings` is frozen; nothing re-reads `.env` while the panel is running.
"No redeploy" is true — no code changes and no rebuild — but "no restart" is
not, and an operator who edits `.env` mid-incident and watches nothing change
will conclude the escape hatch is broken. Edit `.env`, then restart the
process.

**Locked out? Four recovery paths, in the order to reach for them.**

| Situation | What to do |
| --- | --- |
| You know the exact address string that is blocked | `python -m admin.cli unblock <address>`. One command, no restart, and it audits itself. |
| A block is refusing you and you do **not** know which address it was | `PROTECTION_ENABLED=false` in `.env` → **restart** → log in → remove the block by row on `/admin/ip-block/list` (the `unblock` action needs no address, only the row) → set `PROTECTION_ENABLED=true` → **restart** again. This is the path that actually works when you cannot reproduce the address you typed, and it is easy to miss: the CLI command needs the address string, the screen does not. |
| Protection itself is refusing you and no block is involved (a false-positive header match, a shared address hitting the rate limit) | `PROTECTION_ENABLED=false` → restart. There is nothing for `unblock` to remove. Do not leave it off longer than the incident. |
| Nothing above is reachable — no shell that can run `python -m admin.cli`, no way to edit `.env` | Against the database directly: `DELETE FROM ip_block;` clears every block. It is safe in the sense that matters here — `ip_block` has no foreign keys and nothing references it, so nothing else breaks — and the panel is reachable again on the next request, with no restart. It removes *every* block, and it writes no `audit_log` entry, so note what you did. Nothing else in this schema should ever be edited by hand. |

**The CLI escape hatch.** `python -m admin.cli unblock <address>` removes a row
from the blocklist directly against the database, bypassing the panel
entirely. It exists for the one case this whole stage is designed around not
causing: an administrator blocks the address they are sitting behind. The
blocklist check in `ProtectionMiddleware` has no exemption for an
authenticated staff session — a block is another administrator's deliberate
act and outranks everything else, `/admin/login` included — so once it
applies to your own address, there is no page left to click. With no email
system to recover through either, this command (or one of the other two paths
in the table above) is the only way back.

It takes the address, not a row id, and re-derives the fingerprint from it, so
it only works if you can reproduce the address string. That is not as easy as
it sounds — a block entered as `2001:db8:0:0:0:0:0:1` and a caller arriving as
`2001:db8::1` are the same address, which is why `db.blocklist.normalise_ip`
canonicalises both before hashing, and why the command rejects a value that is
not a single address rather than reporting "nothing to do". If you still cannot
reproduce it, use the second path in the table above: the screen removes a
block by row and needs no address at all.

**Where the address in the manual-block form is supposed to come from.**
Nothing in this system ever shows staff a caller's address — §2.3 forbids
storing one and the panel does not log one — so `/admin/ip-block/block` cannot
supply the value it asks for. It has to come from outside: the reverse proxy's
or hosting platform's own access log, an alert from the host, or a report from
someone who can see the traffic. **Confirm the deployment actually keeps a
proxy access log before an incident, not during one**; without one, the manual
block form has no usable input and the only protections in force are the header
check and the rate limit.

**A `SECRET_KEY` rotation clears the blocklist.** `python -m admin.cli
rotate-key` deletes every `ip_block` row and says how many. `SECRET_KEY`
derives the HMAC key the fingerprints are computed under, and an HMAC cannot be
re-keyed the way an encrypted TOTP secret can — with the plaintext address gone
there is nothing to re-fingerprint from, which is precisely the property §2.3
wanted. Left in place, every row would survive the rotation matching nobody:
`is_blocked` would find nothing and `unblock` could not remove them either.
Re-apply any blocks that are still needed after a rotation.

**`/admin/login` and `/admin/verify` are exempt from the rate limit.** From
that check only — the blocklist and the header check still apply to both. This
is not a convenience: with `PROTECTION_TRUSTED_PROXY` correctly `false` and a
reverse proxy in front (the shipped arrangement — TLS is terminated upstream),
every caller arrives as the proxy's own address and shares **one** rate-limit
bucket, and `RequestRate.record` counts refused requests too. So one request a
second from any unauthenticated caller anywhere kept that single bucket
permanently over the limit and answered 429 to every unauthenticated request in
the deployment, including the two login pages — and the authenticated-staff
exemption below structurally could not help, because it needs the session only
those two pages mint. Recovery was an env var plus a restart, for an attack any
unauthenticated caller can mount remotely. Very little is given up: login
attempts are still throttled **per account** by `LOGIN_MAX_FAILURES` /
`LOGIN_LOCKOUT_MINUTES` (`admin/throttle.py`), which is the check that actually
defends a credential-stuffing run, and a bare `curl` loop against
`/admin/login` is still refused by the header check. A flood against those two
paths is not even counted, so it cannot fill the bucket the rest of the panel
shares.

**Known limitation: the anti-lockout exemption cannot cover the login
handshake itself.** `ProtectionMiddleware` exempts an already-authenticated
staff session from the header and rate checks, but that exemption is built on
`SESSION_KEY`, which is only set after a password *and* a completed TOTP
step (`admin/auth.py`'s `stamp_session`). A caller who has not yet logged in —
which is everyone at `/admin/login` and `/admin/verify`, by definition — has
no session to be exempt on, so a pre-16.4 Safari, or any privacy extension
that strips `Sec-Fetch-*` headers, can be refused by the **header** check on
the login page itself, with nothing past "Refused." to explain why. (The rate
limit no longer applies there — see just above — but the header check still
does.) This is not a bug in the check order — the exemption cannot exist before
the credential it is built on does — so it is not fixed here. **Recovery:** an operator who
hits this can either turn off protection for the affected caller's session
with `PROTECTION_ENABLED=false` (the same escape hatch as above, since this
is not a blocklist entry and `unblock` has nothing to remove), or have the
affected person log in from a browser/extension configuration
`db.detection.looks_automated` does not flag, then treat the header check's
false-positive rate on real browsers as a tuning problem for `db/detection.py`
going forward.

**What this is not.** `ProtectionMiddleware` is in-process, application-level
protection for a small admin panel — it is **not** a CDN, **not** an upstream
firewall, and makes no claim to be either. It does not stop a real headless
browser that sends convincing header shapes, and it does not stop a
distributed attack: the rate limit and the header check both key on one
process's own view of one address at a time, so traffic spread across many
addresses passes both checks at whatever rate each individual address stays
under. The blocklist is the one layer that survives a distributed attacker
who has been identified and blocked by address, and even that assumes the
addresses are stable enough to be worth blocking. An operator deciding
whether to trust this panel's exposure to the open internet should read this
paragraph before any of the settings above.

**The public API half.** The blocklist is now applied to `/api/v1/` as well
(`api/app.py`'s `blocklist` middleware), which is where the traffic actually
arrives — a block made on the panel's screen and not enforced at the API stops
nobody. It shares one implementation with the panel: `db/blocklist.py` for the
table and the fingerprint, `db/detection.py` for the address resolution and the
sliding-window counter. Four operational differences from everything above, and
each one is a decision rather than an omission:

| | Panel (`/admin`) | Public API (`/api/v1/`) |
| --- | --- | --- |
| Blocklist | Yes, no exemption | Yes, no exemption. Ahead of routing, so a dead path under `/api/v1/` is refused identically to a live one — otherwise a blocked caller can still map which routes exist |
| Header check (`looks_automated`) | Yes | **No.** `/admin` is a browser-only surface; a public JSON API is not. §6.3's CSV export exists to be fetched by a tool, so refusing `curl` here would refuse a use the contract invites |
| Rate limit | `PROTECTION_MAX_REQUESTS_PER_MINUTE`, refusals counted | §6.5's fixed 120/hour (`POST /calculate`) and 600/hour (`GET`), refusals **not** counted — there is no login page behind this to keep reachable, and not counting is what makes `Retry-After` a promise rather than a guess |
| Refusal body | `PlainTextResponse("Refused.")` | §9.2's `BLOCKED` envelope, built by `api/errors.py` like every other error. A JSON client that got plain text back for one error out of twelve would have to special-case it |
| Off switch | `PROTECTION_ENABLED=false` | **None.** That variable is read by `admin/config.py` only. The API's protections are always on; there is no env var and no restart that turns them off |

**`SECRET_KEY` is required by the API too, and must be the same value.** Both
layers derive the fingerprint key from it independently, through the same
`BLOCKLIST_INFO`, so two different secrets mean two different fingerprints for
one address — a block made in the panel would simply never match at
`/api/v1/`, with nothing raised on either side. `api/app.py` refuses to start
without one rather than starting with a blocklist that silently does nothing.

**Two hazards the API inherits with no equivalent mitigation, and what is done
about them instead.** Both are the same problem seen from two sides — the API
is measuring callers by a value the deployment may not be giving it — and
neither has an in-process fix, so both are warned about at `WARNING` level and
named here:

1. **Behind the shipped deployment, every API caller arrives as the proxy's own
   address.** TLS terminates upstream and `PROTECTION_TRUSTED_PROXY` correctly
   defaults to `false`, so §6.5's "600 / hour / IP" is one global bucket for all
   public traffic, and one blocklist entry denies every visitor at once. On the
   panel this exact chain was a Critical — any unauthenticated caller could
   lock out every administrator remotely — and `_RATE_EXEMPT_PATHS` is what
   made it survivable by keeping the login handshake reachable. **A public API
   has no login handshake to exempt**, so there is nothing equivalent to build.
   `api/app.py` logs a start-up warning naming both consequences. Set
   `PROTECTION_TRUSTED_PROXY=true` only once the proxy is confirmed to
   overwrite `X-Forwarded-For` itself — with no such proxy, trusting that
   header lets any caller claim any address, which is the worse failure of the
   two, and is why `false` remains the default.
2. **A deployment that hides the client address disables both protections
   silently.** `uvicorn --uds` behind nginx gives every request
   `scope["client"] is None`. There is then no address to fingerprint and none
   to count under, so the blocklist and the rate limit are both skipped — the
   correct answer per request (inventing a stand-in key is what collapses every
   such caller into one shared bucket and one shared blocklist entry) and a
   silent no-op in aggregate. `api/app.py` logs a warning the first time it
   sees such a request, once per process. **Bind a TCP socket, or put the real
   address in `X-Forwarded-For` and set `PROTECTION_TRUSTED_PROXY=true`.**

**Confirm both at deployment time, not during an incident** — together with the
proxy access log the manual-block form depends on (see above). All three are
facts about the deployment that this code cannot establish for itself.

---

# 10. Open Items

| ID | Item | Blocks |
| --- | --- | --- |
| O-1 | When the client will supply real emissions factors (hard dependency) | Stage 6 |
| O-2 | Definition of the cost metric: beyond the waste levy, is the value of the wasted food itself included, and at cost price or retail price? **Read the note below before resolving it to a non-zero value — it is O-7 again, in the constant dimension.** | A, E |
| O-3 | Sources for New Zealand equivalence factors (kilometres driven, meal equivalents, showers) | A, D |
| O-4 | Any localisation beyond language (units, date formats) | C, D |
| O-5 | The seeded `food_category` table carries nine substantive Otago categories (plus `standard_mix`), but contract §2.1's prose says "the eight Otago baseline categories". The client's own source list has nine entries; `admin/seed.py` seeds all nine on the ruling that a category too many is a row a staff member can deactivate through the panel, while a category too few is data nobody can enter. Needs the client's word on whether the ninth category belongs, and the contract prose corrected either way. | E |
| O-6 | The seeded `unit_preset` rows (bucket and wheelie-bin sizes to kilograms) are placeholder conversions — the client has not supplied measured data. Every row's `source_note` says so; replace before the calculator is published. Neighbour of O-1. | E |
| ~~O-7~~ | ~~**`prevention` is not the 100% offset §4.1 claims.**~~ **Closed 2026-08-09.** `factor_upstream` gained a nullable `destination_id`, `prevention` was seeded at zero against every general row, and §4.1's claim is true for the first time. See below. | — |
| O-8 | **Interface translation. Nothing here is promised** — the shape below is a sketch pending the client's word, and dropping it entirely is a likely outcome. Sibling of O-4. See below. | Client, C, D, E |
| ~~O-9~~ | ~~**`/admin/try` cannot succeed in a deployed system.**~~ **Closed 2026-08-12.** The panel now mints a short-lived signed proof (`db/staff_proof.py`) and the API verifies it by default. See below. | — |

If O-2 remains unresolved, the first version implements waste levy plus disposal cost only, leaving the value of the food itself as an optional constant defaulting to zero.

### O-2 is O-7 again, in the constant dimension — read this before setting `FOOD_VALUE_PER_KG`

The shipped `cost` formula is `qty_kg * (upstream + downstream + const_FOOD_VALUE_PER_KG)`. The two factor terms are now offset for a prevented line — `prevention` has an upstream row at zero (O-7) and downstream rows at zero. **The constant term is not, and structurally cannot be.** A constant is bound once per formula from the factor set; it has no destination to vary by, so a `prevention` line carries `qty_kg × FOOD_VALUE_PER_KG` exactly as the wasted line it replaced does, and `net_benefit.cost` nets it to **zero**.

That is wrong in the same direction and for the same reason as O-7: preventing the waste saves the entire value of the food, and the calculator would report none of it. Today it is harmless only because O-2 is unresolved and `FOOD_VALUE_PER_KG = 0`, which is also why **no test would catch it** — `tests/api/test_fixture_consistency.py::test_prevention_is_a_whole_offset_upstream_as_well_as_down` asserts `upstream`, not the line value, and deliberately so (`mass`'s formula is `qty_kg`, so a prevented line must still weigh what it weighs).

**The fix needs no schema change, because O-7 already made it.** Model the food's value as an **upstream factor** on the `cost` metric rather than as a constant: it is a property of having produced the food, which is what upstream means, it varies by `(sector, food_category)` — which is exactly how `factor_upstream` is keyed, and a single `FOOD_VALUE_PER_KG` cannot express that a kilogram of dairy and a kilogram of vegetables are not worth the same — and `prevention`'s zero row then offsets it automatically, through the same lookup and with no new rule to remember. The `cost` formula collapses back to the default `qty_kg * (upstream + downstream)` and the constant is deleted.

So: **resolving O-2 to a non-zero food value means moving it out of `constant` and into `factor_upstream`, not raising the constant.** If it is raised in place instead, the same 78.9%-shaped understatement returns on the metric the client is most likely to quote, and nothing in the suite will say so.

Recorded here, while the connection is visible, rather than as a separate open item: it is not a defect today and filing it as one would imply work that should not be done until O-2 is answered.

## O-7 — `prevention` and the upstream factor — **CLOSED 2026-08-09**

**What was wrong.** `factor_upstream` was keyed on `(sector, food_category, metric)`. It had no destination column and could not acquire one from `interfaces.md` §4.3's line variables, which carry `qty_kg`, `upstream`, `downstream` and the constants — neither the destination nor its group. So `line_value = qty_kg * (upstream + downstream)` applied the entry's upstream factor to every line in the entry, including a line sent to `prevention`. Setting `prevention`'s downstream factors to zero, which the data did, zeroed only the second term. §4.1 meanwhile claimed all factors zero, a 100% offset, parity with ReFED — which treats prevented waste as avoiding the production impact as well as the disposal impact.

**The size of the gap, measured on `tests/fixtures/`.** 800 kg of `not_harvested` vegetables from `primary_production`, moved to `prevention` in the alternative:

| | Current | Alternative | `net_benefit.co2e` |
| --- | --- | --- | --- |
| As built | 800 × (0.45 + 0.12) = 456.0 | 800 × (0.45 + 0.00) = 360.0 | **96.0** |
| As §4.1 describes, and as built since | 456.0 | 800 × (0.00 + 0.00) = 0.0 | **456.0** |

**78.9% of the benefit was missing**, and upstream is the larger of the two terms for most categories, so this was representative rather than a worst case. The failure was one-directional — `prevention` systematically *understated*, never overstated — so `interfaces.md` §6.2's anti-inflation argument was never affected (mass conservation is a property of the request), but the client's headline message, that not wasting food in the first place beats every disposal route, came out as the weakest number on the results page. A user who tried "what if we prevented this" saw a smaller improvement than composting it.

### The ruling

**Option 2, in its `factor_upstream` form: a nullable `destination_id`.** NULL means "applies to every destination for this `(sector, food_category, metric)`", so the lookup order becomes exact destination, then the NULL row, then zero — the pattern `factor_downstream.food_category_id` already used, and the fallback shape `FactorBundle.downstream()` already implements. Every upstream row written before the change keeps meaning exactly what it meant.

| Option | Verdict |
| --- | --- |
| **1. Documentation only** — restate §4.1 as downstream-only and drop the ReFED-parity claim | **Rejected.** It was the only free option and the only one nobody on this team could choose alone, because it changes what the client is told the calculator measures |
| **2. Schema** — a destination dimension on `factor_upstream`, *or* an upstream multiplier on `destination` | **Chosen, in the first form.** The multiplier form was rejected separately: it would have applied the multiplier in the engine rather than resolving it in the lookup, putting a piece of the impact formula back into Python, and would have forced `FactorBundle.destinations` from a `set[str]` into a mapping |
| **3. One new line variable** — derive `prevented` and make the default formula `qty_kg * (upstream * (1 - prevented) + downstream)` | **Rejected**, and this reverses the recommendation this section carried before it was settled. It adds a fifth line variable, changes the default formula, and requires `admin/expressions.py`'s `BASE_VARIABLES` to gain a member in lockstep with the engine's evaluator — two whitelists, written by two people, that must agree or the panel accepts formulas the engine rejects. The configurability argument in its favour turns out to cut the other way: under option 2 the offset is a **row**, which is data staff can edit, where under option 3 it is a term in an expression that the default formula has to carry |

**What did not change, which is the point.** `line_value = f(qty_kg, upstream, downstream, const_*)` keeps its exact shape; only the value bound to `upstream` changes, because the destination is resolved in the lookup. The evaluator, §4.3's line-variable table, the formula language and `BASE_VARIABLES` were all untouched, so **no panel/engine divergence was introduced.**

### What was done

- `alembic/versions/0009_upstream_destination.py`: the nullable column, the widened `UNIQUE(factor_set_id, sector_id, food_category_id, destination_id, metric_id)`, and — because MySQL compares NULLs as distinct inside a UNIQUE key and would otherwise admit unlimited duplicate generic rows — the functional unique index `uq_factor_upstream_generic` over `COALESCE(destination_id, 0)`, written by hand and verified against `information_schema`. This is the third appearance of the trap B first found on `factor_downstream`.
- A data backfill in the same migration: every `(factor_set, sector, food_category, metric)` with a general row gains a `prevention` row at zero, with the reasoning in `source_note` — prevented waste was never produced, so no upstream burden is attributable to it.
- `db/repository.py`: `build_bundle_data` publishes `destination` on every upstream row (`null` for the generic case), and `clone_factor_set` copies `destination_id` — without which the recommended clone-edit-publish workflow would reopen this item on the first real factor set.
- `admin/factor_views.py`: the upstream screen gains the destination column and a form hint saying that blank means every destination.
- `tests/fixtures/factors.json` and `calculate_response.json`: the canonical fixtures had 96.000 baked in as the correct answer. The response fixture was regenerated from the published factors rather than hand-edited, and the only bytes that changed are the ones this item causes.

**`interfaces.md` v1.8** carries the contract half: §2.2's column and lookup order, §4.1's `upstream()` signature, §10.2's `bundle.json` key.

### What stays open behind it

The claim is now enforced by data, and data can stop enforcing it. A `(sector, food_category, metric)` given a general upstream row with no `prevention` counterpart silently reverts to the old behaviour for that tuple — a staff member adding a new sector to a draft is the realistic path.

**Three things now hold it.** `tests/api/test_fixture_consistency.py::test_prevention_is_a_whole_offset_upstream_as_well_as_down` holds the fixtures to it. The migration covered everything that existed on the day. And **publication is refused** if any tuple is missing its `prevention` row: `find_missing_prevention_upstream` in `db/repository.py`, enforced by both `publish_factor_set` implementations, naming the offending tuples in the message.

That last one closes the gap this paragraph originally described as an open follow-up. It also means the rule is now something a staff member meets as a refusal rather than a convention they are trusted to remember — which is why the field-level help on the upstream screen states it as a consequence and not as advice. If that guard is ever relaxed, this section and that help text both become untrue.

## O-8 — interface translation — **OPEN, and unpromised**

Recorded so the shape of the question survives the conversation that produced it. **No commitment is made here.** CLAUDE.md and §1.1's scope list both place te reo Māori bilingual support outside this project, the client has not asked for translation, and dropping this item entirely is a likely and acceptable outcome. It is written down because two of its decisions are cheap now and expensive later, not because the work is agreed.

### The boundary the owner drew, and why it is the right one

**Static text is translated. Anything staff can edit is not** — what a staff member types in English is what every visitor sees, in every language.

That line is drawn where it is because the alternative does not survive contact with operations. `destination`, `food_category`, `sector`, `metric` and `unit_preset` rows are the things the panel exists to let staff change; a translated copy of each would fall out of date the first time somebody renames one, and nobody would know whose job it was to fix. A calculator showing a stale translation of a category name is worse than one showing the English the staff member actually wrote.

So: the interface chrome, the step instructions, the button labels, the error copy, the methodology prose — translated. The taxonomy, the factor provenance notes, the version labels — not.

### Three places English leaks through that boundary anyway

1. **`equivalence.label_template` is a whole sentence, not a label.** It ships as `"Equivalent to driving {value} km"`, it is staff-editable, and it renders on the most prominent card of the results page. Under the rule above it stays English in every language.

   **This is the one worth changing regardless of whether O-8 proceeds**, and changing it is cheap only until O-1 lands: split the column into a unit fragment staff own (`"{value} km"`) and a static prefix the language pack owns. After real factors arrive, the same change is a migration over client data.

2. **`details[].field`'s companion `message` is composed by the API.** Top-level errors are already fine — `web/js/api.js`'s `publicError()` branches on the §9 `code` and renders its own copy, so the envelope's English never reaches a visitor. Field-level messages are the exception, and they were only recently wired through to the user deliberately (they carry the specific reason a row was rejected). Translating them means the API returns a code plus parameters and the front end composes the sentence.

3. **`metric.unit`.** `kg CO2e`, `L` and `NZD` need no translation; `kg` has local spellings in some scripts. Low risk, and the rule as drawn leaves it alone.

### te reo Māori should not be machine translated with the other thirty

This is not a technical objection.

Te reo Māori is an official language of New Zealand under the Māori Language Act 1987. Machine translation for it is trained on very little data and is unreliable in ways that are not obvious to a non-speaker. The client is a New Zealand trust, the deliverable carries a te reo word in its own name — **Kai** Commitment — and the project sponsor is a former Prime Minister's Chief Science Advisor.

Bad te reo on a public New Zealand government-adjacent tool is not read as a rough translation. It is read as carelessness about the language, and it is the kind of thing that gets pointed out publicly.

**If O-8 proceeds:** English hand-written; te reo Māori translated by a person, which is a question to put to the client because a trust at this level usually has that resource or knows who does; the remaining languages machine translated **and labelled as such in the language switcher**. If a human te reo translation is not available, shipping no te reo is the better answer than shipping a machine one.

### One implementation route is closed

**Runtime translation through an external service is out of the question.** This site's stated position is that it stores nothing about a visitor; sending the page a visitor is reading to a third-party translation API contradicts §2.3's whole argument and the statistics page's own restraint about what may be claimed. If O-8 proceeds it is static language packs, fetched at runtime the same way `?mock=1` already fetches fixtures — which also keeps the no-build-step decision intact.

### Timing, if it is going to happen at all

**D has not started.** The statistics and content pages will add the next batch of user-facing copy, and its wording is the most constrained on the project — §6.4's rule that the subject is the calculator and never New Zealand. Building the key mechanism before she writes means she writes against it; building it afterwards means revisiting exactly the copy that is most delicate to revisit.

Roughly 150 translatable strings exist in `web/js` today, concentrated in `calculator.js` (67), `results.js` (35) and `improvement.js` (27). The two HTML files are 33 and 18 lines — this front end builds its pages in JavaScript, so the work is in the modules rather than in templates.

### The admin panel is a separate question, and probably a no

`sqladmin` renders its own templates; translating them means overriding or forking them. The panel has five users, all in New Zealand, working in English. Unless the client asks, this is effort better spent on the field-level help in O-7.


## O-9 — the dry-run authenticator was never wired — **CLOSED 2026-08-12**

**What was wrong.** `POST /api/v1/calculate` accepts `X-Dry-Run: true` from a staff member, and the panel's `/admin/try` screen is built on it. `api.app:create_app` takes a `staff_authenticator` callable to decide who that staff member is, and **nothing in production passed one.** `run.sh` served `api.app:create_app` bare; the only code that supplied the argument was `tests/api/test_api.py` and `tests/api/test_api_entries.py`, each injecting a lambda.

So every dry run in a deployed system answered `UNAUTHORIZED`, and §8.2 — tuning a formula against real numbers without persisting anything, and the pre-publish comparison built beside it — did not work at all. The page rendered that refusal inside a 200, which is what let it look like a working screen for the whole of its life.

There was a second thread leading to the same place. `admin/auth.py::require_staff` exists, its docstring says "B calls this and nothing else", and `api/app.py`'s own module docstring told the reader to "pass `admin.auth.require_staff` as the staff authenticator instead". That instruction could never be followed: `api/` may not import `admin/`, the two run as separate service images, and `require_staff` reads a `request.state.db` placed there by middleware its docstring says "the next plan installs", which was never installed. So the documented wiring was impossible, nobody did it, and nothing was put in its place. Both docstrings are corrected.

**Why no test caught it.** The tests supplied exactly the thing production lacked. That is the failure mode worth naming: a test double that fills a gap rather than standing in for something real reports success on a path nobody has ever run. The panel's own tests reached the screen and stopped at the API boundary; the API's tests reached the endpoint with an authenticator already injected. Neither one crossed the seam where the wiring was missing. `tests/admin/test_calc_client.py` went further and asserted the *wrong mechanism* — that the browser's cookie jar was forwarded, "because the API authenticates it with require_staff()" — and passed.

**The question was never how to pass an argument.** It was **who may run an unpersisted calculation against published factors**, and how the API — which may not import `admin/` — satisfies itself that a caller is staff. Recorded as needing a B-and-E decision; the repository owner now owns both modules and took it.

### The ruling

**A short-lived signed proof, minted by the panel, verified by the API: `db/staff_proof.py`.** The panel signs `{"sub": "<username>"}` with `itsdangerous.TimestampSigner` under the deployment's one `SECRET_KEY` and a **pinned salt**, sends it as `X-Staff-Proof` on the server-to-server call, and the API's default `staff_authenticator` verifies it. It lives in `db/` because both layers need it and `api/` may not import `admin/` — v1.3's ruling on `db/detection.py`, and the reason `db/repository.prevention_destination_codes` — not `admin/taxonomy_rules` — is what §6.2 reads the prevention flag through (v1.22).

| Option | Verdict |
| --- | --- |
| **1. Signed proof minted by the panel** (`db/staff_proof.py`) | **Chosen.** Both processes already share one `SECRET_KEY` through one mounted volume, and the §2.3 blocklist fingerprint already proves keys derived from it agree across the boundary |
| **2. Move the panel's session machinery into `db/` and let the API read the session cookie** | **Rejected**, and this is the substantive rejection. It is not a layering problem — `admin/protection.py` already decodes that cookie standalone, so moving it was entirely feasible. It is that doing so would make the API **a second place where a staff session is established**, so a mistake in it becomes an authentication defect in the public-facing service. It also widens reach: `/admin` and `/api/v1/` are one origin behind nginx, so a shared session cookie is sent by the browser to the API too, and any staff member's browser could then drive the arbitrary `dry_run.bundle` path directly. Option 1 keeps authentication in exactly one place and gives the API a smaller question to answer |
| **3. An internal network boundary** — trust anything that can reach `api:18000` | **Rejected.** It makes the guarantee a deployment property rather than a code one. The `ports:` block in `docker/compose.yaml` publishes the API on 18000 for development, so out of the box the boundary does not exist, and the failure is silent |
| **4. Move the dry run into the panel against its own engine import** | **Rejected** by the contract, not by preference: v1.1 decision 1 already resolved §4.2 against §8.2 in favour of the HTTP path, because two calculation paths drift and the point of a dry run is that it exercises what production exercises |

### The security boundary, stated

**What a proof asserts:** "at time T, the panel had an authenticated staff session for this username". It is minted only in `admin/calc_client.py`, only after sqladmin's `login_required`, the role floor and the onboarding gates have already passed.

**Can it authenticate a non-staff caller?** No. Minting requires `SECRET_KEY`, which lives in a volume mounted into the application containers and is never sent to a browser. A visitor cannot construct one, and the salt is pinned to a value distinct from the default `SessionMiddleware` signs with — so a **stolen session cookie cannot be replayed as a proof**, and a proof cannot be replayed as a session cookie, even though both are signed under the same secret. Both directions are asserted in `tests/db/test_staff_proof.py`.

**Can a dry run now reach anything a normal calculation cannot?** It reaches what §6.2.1 always specified and no more: the inline `dry_run.bundle` (capped at 5000 rows) and an unpublished `factor_set_version`, and it persists nothing. A valid proof on a request *without* `X-Dry-Run` changes nothing — the request is an ordinary public calculation, persisted, with a token — and the proof opens no other route. Both are asserted in `tests/api/test_dry_run_auth.py`.

**What it deliberately does not do.** The API performs no `staff` lookup: it holds no `staff` model, and giving the public service a reason to read the credential table would be a worse trade than the one taken. So an account deactivated in the seconds after a proof was minted can have that proof accepted for up to `PROOF_TTL_SECONDS` (60). The window is bounded, it buys only a calculation that persists nothing, and if it ever stops being acceptable the fix is a lookup here — not a wider credential.

**A residual, named rather than left implicit.** `SESSION_HTTPS_ONLY` defaults to `false` in the shipped compose file so the panel is usable over plain http, and `PROTECTION_TRUSTED_PROXY` defaults to `false` alongside it. The proof travels only between two containers on the compose network, so it is not exposed by that default, but it is a bearer credential for its 60 seconds and TLS in front is what keeps it that way in production. This is the same pairing `docker/compose.yaml` already documents.

### What was done

- `db/staff_proof.py`: `mint_staff_proof` / `verify_staff_proof`, `STAFF_PROOF_HEADER`, `PROOF_TTL_SECONDS`, and the pinned salt. Every verification failure — absent, malformed, wrongly signed, expired, or validly signed over the wrong payload — returns `None`, because the caller's only correct response to any of them is 401.
- `api/app.py`: `staff_authenticator=None` now means **"use the default"**, not "no authenticator" — the identical correction `blocklist_check` already carried, arrived at the same way and for the same reason. Assigning `app.state.staff_authenticator = None` after construction still disables it.
- `admin/calc_client.py`: takes `secret_key`, mints one proof per call, and **no longer forwards the browser's cookie jar**. That forwarding authenticated nothing — the API cannot read the panel's session cookie — so its only effect was to hand a live staff session cookie to a second service on every dry run.
- `tests/db/test_staff_proof.py` and `tests/api/test_dry_run_auth.py`.

**Why no test caught the original defect, and what now would.** The tests supplied exactly the thing production lacked: `test_dry_run_requires_staff_and_does_not_persist` asserts the refusal, then assigns `app.state.staff_authenticator = lambda request: "alice"` and asserts the success — both halves passing against an app that could never authenticate anybody. `tests/api/test_dry_run_auth.py` drives the fixture app **without ever assigning an authenticator**, so it exercises the seam the old tests stepped over. That is the general lesson worth keeping: *a test double that fills a gap production has reports success on a path nobody has run.*
