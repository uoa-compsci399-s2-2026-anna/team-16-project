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
| O-8 | **Interface translation is delivered** (v1.25; negotiation rule amended at v1.26; **chooser added at v1.27**): the panel in Chinese, the calculator in twenty languages, and **a language chooser at the top inline-start of both surfaces whose default option follows the browser** (on the calculator, inside the header's own row: a separate strip cost 57px above the fold, which is the budget the step-indicator band was deleted to protect) — negotiating, when nobody has chosen, from **the browser's highest-priority tag only, an unmatched one being English rather than a walk down the list.** A choice is stored in one `kaicalc_lang` cookie whose value space is closed and entropy-free, which is what keeps it outside §2.3 rather than the fact that it was chosen. **The taxonomy inside a translated page stays in the language staff typed it — ruled 2026-08-14, and not a gap: anything a staff member can edit is published exactly as written.** What remains open is only that nineteen of the twenty are machine translated and unread, and that is now the only thing open: the recorded defect is closed at v1.28. The panel proper announced every page as `<html lang="en">` because sqladmin's layout hardcodes it outside any overridable block; it is fixed by rewriting that one line in sqladmin's own template as Jinja compiles it, rather than by vendoring a second copy of a file this project has already watched go stale. **`lang` is the language rendered, not the language chosen** — a choice with no catalogue on that surface renders English and says English. `dir` is emitted too, off the catalogue, though no panel catalogue is right-to-left and the panel's RTL *layout* is therefore not claimed to work. See below. | Client, C, D, E |
| ~~O-9~~ | ~~**`/admin/try` cannot succeed in a deployed system.**~~ **Closed 2026-08-12.** The panel now mints a short-lived signed proof (`db/staff_proof.py`) and the API verifies it by default. See below. | — |
| O-10 | **There is no link from the calculator to Home or Statistics.** The three content pages carry a four-link public navigation and the calculator does not: measured at contract v1.29, the chooser (364px), the brand lockup (330px) and the navigation (355px) do not share the calculator's header row at 938px, and the footer has 32px of slack against a 44px touch target. Recorded rather than closed by shaving a target. Raised at v1.29 and still open. | C, D |
| ~~O-11~~ | ~~**The statistics, home and documentation pages ship in English.**~~ **Closed 2026-08-15 (contract v1.30).** 99 new keys across the three pages, 299 per catalogue, twenty languages; the chooser and the machine-translation notice now reach all three; and the statistics charts are destroyed and rebuilt on a language change, because a Chart.js title is a constructor argument that no DOM walk can reach. See O-8 below. | — |

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

## O-8 — interface translation — **DELIVERED 2026-08-13: the panel in Chinese, the calculator in twenty languages; a language chooser on both surfaces 2026-08-14**

Recorded as unpromised until the first client demonstration. Two of the
positions this entry used to hold were overturned there and one was confirmed,
so it now describes what exists rather than what might.

### What the client settled

**te reo Māori is out of scope.** Not deferred — removed at the client's
request. The long argument this entry used to carry, that te reo must not be
machine translated with the others, is kept below because it is now the reason
te reo is *absent* rather than pending: if it is ever asked for again, it needs
a human translator and not a pass through the same pipeline as the rest.

**Around twenty other languages, static interface strings only.** Unchanged
and confirmed.

### What is delivered

| Surface | Languages | State | Reviewed? |
| --- | --- | --- | --- |
| Admin panel | English | ~900 strings, the source language | Written by hand |
| Admin panel | Chinese (`zh`) | **Built.** 181 catalogue entries | **Yes** — by the people who use the panel daily |
| Admin panel | any other | Mechanism ready; no catalogue written | — |
| Public calculator | English | 201 translatable strings, the source language | Written by hand |
| Public calculator | Chinese (`zh`) | **Built.** 201 entries | Reviewable by the team; **not yet re-read for these 201** |
| Public calculator | 19 others | **Built.** 201 entries each | **No. Machine translated and unread — and the page says so** |

**The calculator's other nineteen**, in the client's own ordering: Traditional
Chinese, Hindi, Tagalog, Panjabi, Korean, Afrikaans, French, German, Spanish,
Dutch, Japanese, Gujarati, Arabic, Tamil, Vietnamese, Thai, Russian, Urdu —
eighteen — plus **Malayalam**, proposed here as the twentieth: the client's
list came to nineteen counting Simplified Chinese, and Malayalam is the next
language in the same census ordering, sitting between Russian and Thai.

**Samoan, Tongan and every other Pacific language are out, for te reo's
reason.** A census-ordered list of New Zealand's languages puts Samoan third
and Tongan fourteenth — above most of what did ship — so their absence has to
be stated rather than left to the ordering to explain. They have exactly the
problem the te reo section below sets out: machine translation trained on very
little data, and a community expectation that the language is handled by
somebody who speaks it. A machine pass of either would be the same mistake
under a different name.

**The Chinese calculator catalogue carries no notice**, on the panel's basis:
it is the one language with speakers on this project. It is new, it has not yet
been read line by line for these 201 strings, and that read is the first
follow-up below rather than a claim made here.

**The admin panel came into scope, and went first.** This entry previously
concluded "probably a no", on the grounds that the panel has five users all
working in English. That premise was wrong: the panel's users are the
development team, and the repository owner reported that half of them could no
longer follow its English domain vocabulary at working speed during a
debugging session. Chinese is therefore not one of twenty — **it is the only
language with real users today, and the only one that will have native
speakers noticing when a translation is wrong.**

**That difference is a difference in kind, not in priority.** A mistranslated
label on the public calculator confuses one visitor for one session. A
mistranslated field description in the panel leads a staff member to enter the
wrong factor, and that factor reaches a public number. Chinese is reviewed by
the people who use it daily; everything else will ship machine translated and
unread. **The two promises are made visibly different in the interface** — see
the notice, below — and not only recorded here.

### The mechanism

**One key scheme: the English source string is the key.** `_("Save")` looks up
`"Save"`. There is no second namespace, no key file, and no way for a key to
point at a description that has since been reworded.

It is the source text rather than an invented key for a reason that decided
itself: `sqladmin` already wraps its own fifty user-visible strings in `_()`
with the English as the msgid, so any other scheme would have left two
mechanisms in one panel. It also means the 82 field descriptions stay in
`form_args` in English and are translated on the way out — no view file was
edited to translate one.

**Catalogues are JSON, one file per language per surface.**

```
admin/locales/zh.json     read by Python, shipped as wheel package data
web/locales/<lang>.json   fetched by the browser (not yet written)
```

Two locations rather than one, and the deployment forces it: package-data
cannot reach outside its own package directory, `docker/admin.Dockerfile`
copies only `admin/ api/ db/ engine/`, and `docker/web.Dockerfile` copies only
`web/`. A top-level `i18n/` directory would be in neither image — which is how
the guidance blocks went missing from a built image once already. What the two
share is everything that matters: one JSON shape, one key rule, one fallback
rule, **one stored choice**, one notice rule. A Python process and a browser
cannot share a reader; they can share a contract.

> **On that phrase.** It read "one cookie" from v1.24 until 2026-08-14, which
> was a leftover from the deleted `kaicalc_lang` design and was wrong for the
> whole of v1.25 and v1.26, when nothing was stored at all. The chooser has
> since made a cookie real again — so the sentence would now be **true by
> coincidence**, which is worse than being wrong, because nobody re-checks a
> sentence that happens to read correctly. It says "one stored choice" instead:
> that is the property the two surfaces actually share, and it stays true
> whatever the mechanism becomes.

```json
{ "language": "zh", "endonym": "中文", "machine_translated": false,
  "strings": { "Save": "保存" } }
```

`endonym` and `machine_translated` live in the file so that **adding a language
is adding a file.** No list in code has to be extended nineteen times.

`web/locales/index.json` repeats `language`, `endonym`, `machine_translated`
and `tags` for every catalogue, so the chooser can label twenty-one options
without fetching twenty files. It is a summary, not a second source: a test
holds it to the catalogues it summarises, and `admin/locales/` carries a
vendored copy of it — held to the original by another test — because the admin
image copies no `web/`.

**No new dependency, and no build step.** `sqladmin` ships an `I18nConfig` of
its own and it is unusable here for two independent reasons: it requires
`babel`, which this project may not add, and it loads compiled catalogues from
inside its own installed package — `en, de, az, ru, tr` — so Chinese cannot be
added to it from a wheel of ours. What *is* reachable is the seam underneath
it, `jinja2.ext.i18n`'s `install_gettext_callables`, which sqladmin calls
itself and which takes any callable. Installing ours there translates
**sqladmin's own fifty strings through our catalogue**, with no fork of its
templates. A dictionary lookup is enough; gettext's plural forms, domains and
`.mo` files buy nothing at this size and cost a dependency and a build step.

**Four Jinja environments, not one.** sqladmin builds its own, and
`admin/views.py`, `admin/dryrun_views.py` and `admin/self_service_view.py` each
construct a module-level `Jinja2Templates`. `admin/i18n.py::install` is applied
to all four; an environment that missed it raises `'_' is undefined` on its own
pages and nowhere else.

**Two render-time hooks have no substitute**, and both are named because each
closes a gap the other cannot:

* `admin/templates/sqladmin/_macros.html` is a **copy** of sqladmin's, with
  four strings wrapped. Jinja can override a block but not a macro, and every
  translatable string in that file is inside one. It carries the 82 field
  descriptions and the navigation menu. A test compares it against the
  installed original with the `_()` calls stripped back out, so a version bump
  that edits it fails loudly instead of silently reverting a translation.
* `admin/i18n.py::_TranslatedAttribute` makes a view's `name`, `name_plural`
  and `category` answer in the request's language. sqladmin composes its create
  heading as `_("New %(name)s", name=model_view.name)` — it translates the
  sentence and interpolates the model name into it *untranslated* — and writes
  the list heading as `{{ model_view.name_plural }}` with no `_()` at all.
  Without this, a complete catalogue still renders `新建Constant`.

### How a language is chosen — a chooser, defaulting to the browser

**There is a chooser at the top inline-start of every page on both surfaces,
and its default option follows the browser.** Negotiation is the default rather
than the only behaviour.

This paragraph has now been rewritten twice and it is worth saying why, because
the two rewrites moved in opposite directions and both were right.

The section originally specified a `kaicalc_lang` cookie set by a `?lang=`
switcher, and declined `Accept-Language` on the grounds that §2.3 forbids
reading a visitor that way. **That reading was too strict**, and §2.3 is its
own correction: it forbids **storing** an address, a user agent or a
fingerprint, and it says in terms that "user agents, headers and paths are read
within a request and forgotten". Reading a header to decide what to render, and
keeping nothing, is the behaviour that sentence describes. So at v1.25 the
cookie and the switcher were both deleted and the header was read instead.

**What that left missing was the person.** Negotiation answers "what does this
browser claim"; it has no way to answer "what do I want". A Chinese-speaking
visitor who would rather read the English original had no way to say so, and no
control to say it with. So at v1.27 the chooser came back — **not** the old
`?lang=` switcher, and **not** a reversal of the header ruling above, which
still stands and still describes what happens when nobody has chosen.

**The distinction that has to survive both rewrites**, because it has already
been mishandled in each direction:

| Act | Stores | Permitted by §2.3? |
| --- | --- | --- |
| Read `Accept-Language`, render, forget | nothing | Yes — "read within a request and forgotten" |
| Store "this visitor chose English" | `kaicalc_lang=en` | Yes, and for reasons that are **not** the same as the row above |
| Store an address, a user agent, a fingerprint | an identifier | **No** |

The middle row needs two properties and **both are required**. It records
something the visitor **deliberately declared**, not something inferred from
their browser. And its **value space is closed, tiny and free of entropy** —
twenty-two values in total — twenty-one languages and `auto` — shared
identically by everyone who picks the same one.

**The second is the load-bearing one.** "The person declared it" would equally
justify storing a name somebody typed into a form, which would be a fingerprint
by any measure. **It is the absence of entropy, not the presence of consent,
that makes this incapable of identifying anyone.** A field that cannot
distinguish two visitors cannot correlate them.

It is also kept away from the one identifier this system does keep: the
`submission.token` de-duplication token (§5.1) has a different name, a
different lifetime and a different purpose, and the language cookie neither
extends nor refreshes it and never appears beside it in `submission`,
`audit_log` or the access log.

**"Follow the system" is stored as the literal `auto`, not as an absent
cookie.** Otherwise "chose to follow" and "never chose" cannot be told apart
and the chooser cannot show what is in effect — and, more practically,
reverting becomes an ordinary write instead of a cookie deletion, which is the
operation that silently fails when its path or domain does not match exactly.

**One cookie, both surfaces, because the panel forces it.** `localStorage`
would be shared too — the two surfaces are one origin — but the panel renders
server-side and has to know the language before it emits any HTML, which
`localStorage` cannot answer. `path=/`, and not `HttpOnly`, because the
calculator's JavaScript reads and writes the same value.

**The chooser is built differently on each surface, and "works without
JavaScript" is why.** The panel renders through FastAPI and genuinely works
with scripting off, so its chooser is a plain `<form method="post">` with a
submit button. The calculator is ES modules end to end and renders **nothing**
without scripting, so a chooser needing JavaScript adds no degradation it did
not already have — and building it in `web/js/i18n.js` means it cannot exist as
a control that is present and does nothing. Shipping a `<select>` in the static
HTML would have been the failure, not the fix.

`?lang=` is unchanged: a one-request override that persists nothing, emitted by
no control, ignored when unrecognised. It and the chooser share no mechanism,
so a support link cannot re-language its recipient for good.

**Two surfaces, two mechanisms, and the split is forced by the deployment
rather than chosen.**

| Surface | Reads | Why it cannot be the other one |
| --- | --- | --- |
| Calculator | `navigator.languages` | Static files served by nginx, which never reach FastAPI. There is no server in the path that could negotiate |
| Admin panel | `Accept-Language`, with quality values | Rendered through FastAPI, so the header is the only thing available before the first byte |

`navigator.languages`, never `navigator.language`: the second is one tag and
the first is the *ordered* list, and order is what identifies the
highest-priority tag. How far down that list the negotiation may reach is the
subject of the next section; which property is read is settled here.

Quality values are honoured because they decide the answer: `zh;q=0.8, en;q=0.9`
is a request for English, and a parser that reads the header in written order
gets it backwards. `q=0` means *not acceptable* and is dropped rather than
ranked last; a malformed entry is dropped rather than defaulted, because
guessing at input this code cannot read is how a parser starts making decisions
on nonsense. `*` is **kept and ranked like any other tag** — see the next
section, where that stopped being a technicality. The header is read to a
bounded length and a bounded number of entries.

**Matching is RFC 4647 lookup, not equality.** Try the whole tag, then drop the
last subtag, and repeat: `en-NZ` reaches `en`, `zh-CN` and `zh-Hans-CN` reach
`zh`.

**Truncation alone gets one case badly wrong**, and it is the case with the
most speakers: `zh-TW` truncates to `zh` and hands a Traditional reader
Simplified Chinese, which is the wrong script rather than a degraded
translation. So each catalogue file declares the tags it speaks for and an
**exact claim is matched before any truncation runs** — `zh-Hant` claims
`zh-TW`, `zh-HK` and `zh-MO`; `zh` claims `zh-Hans`, `zh-CN`, `zh-SG` and
`zh-MY`; `tl` claims `fil`, which is what browsers actually send for Filipino.
The claims live in the file for the reason `endonym` and `machine_translated`
do: adding a language is adding a file. **No two catalogues may claim one tag**,
and a test fails if they do — otherwise filename order would decide which
script a Hong Kong browser gets.

The panel ships no Traditional catalogue, so `zh-TW` truncates to Simplified
there. That is the right answer for a panel whose five users all read
Simplified, it is asserted by a test, and the test is the reminder to delete
itself if a Traditional catalogue is ever added to `admin/locales/`.

### Only the first language is consulted (v1.26)

**A tag nobody claims resolves to nothing, and `negotiate` turns that into
English. The rest of the visitor's list is not read.** A browser sending
`fr-CA, zh, en` to the panel gets **English**, not Chinese.

Until 2026-08-13 it got Chinese: a tag with no catalogue stepped aside so the
next preference could have a turn, and English arrived only once the list was
exhausted. The repository owner ruled against that, and the reasoning is
recorded here and repeated as a comment in both negotiators, because walking
the list is the more obvious behaviour and someone will eventually try to
restore it as a fix.

* **A browser's language list does not reliably describe what a person can
  read.** The first entry is usually deliberate. The second and third are
  frequently residue — a preinstalled system locale, an input method added
  once, a setting changed years ago and forgotten. Honouring them as a genuine
  second language means letting an unreliable signal override a reliable
  fallback.
* **English is a safe floor for this audience; an unfamiliar language is not.**
  Everyone who reaches this calculator or this panel reads English — that is
  the assumption the ruling makes explicit, and it is the assumption this
  project is already making everywhere else, since the taxonomy inside a
  translated page is English regardless. So the worst outcome under this rule
  is a page in English. The worst outcome under the walk is a page in a
  language the reader does not have, **and with no picker on either surface
  there is no way back out of it.**

**The rule removes the walk between tags, not the match inside one.** Every
paragraph above about truncation and tag claims still holds, applied to the
single tag consulted: `en-NZ` reaches English, `zh-CN` reaches Simplified,
`zh-TW` reaches Traditional, `fil` reaches `tl`. `de-AT, xx` reaching German is
the case that separates "the walk is gone" from "the matcher is broken", and it
is asserted as such.

**Ordering happens before the rule.** The tag consulted is the
highest-*priority* one, not the first one written: `zh;q=0.8, en;q=0.9` puts
`en` at the head and answers English, while `en;q=0.4, zh;q=0.9` answers
Chinese. `q=0` is an explicit refusal rather than a low rank, so `en;q=0, zh`
has `zh` at its head.

**`*` is why the parser changed.** It used to be dropped, on the reading that
"anything" is what falling through to English already does — true while the
list was walked, false the moment only the head is read, because dropping it
promotes the tag *behind* it into the one slot that decides. A header whose
first statement is "no preference" would then have answered Chinese. So `*` is
ranked like any other tag, nothing claims it and it has no subtag to drop:
**`*` at the head means English**, and `zh, *;q=0.5` still means Chinese.

A malformed entry is still dropped rather than defaulted, and a dropped entry
holds no rank — there is no quality to rank it by, which is the reason it was
dropped in the first place. So `en;q=high, zh` answers Chinese, and a header
with nothing readable in it at all answers English, as do an empty header and
an absent one.

**`?lang=` survives as a one-request override, and stays outside the rule.**
Nothing emits it; it exists for testing, screenshots and support, which is the
whole of why an interface with no picker still needs one. It is persisted
nowhere. **An unrecognised value is ignored**, and the request then negotiates
exactly as though the parameter had been absent — not an error, not a redirect,
not remembered. A typo in a support email must not look like a broken panel.

It is deliberately not folded into the single-tag rule: `?lang=` is somebody
typing a language on purpose, which is the one signal here that is *not* a
browser setting, so a typo in it falls back to the negotiation rather than
consuming its slot. `?lang=qq` on a `zh-CN` browser is still Chinese. On the
calculator that meant matching the forced value *before* the list rather than
prepending it to a list whose head is now all that is read.

**`Vary: Accept-Language, Cookie` wherever the server negotiates.** The panel
appends it — appends, not assigns, because FastAPI sets `Vary: Cookie` on
session responses and overwriting it would let a cache serve one staff member's
page to another. It is applied by the **outermost** middleware, so it reaches
the responses the inner ones refuse: a 403 cached without it is served to
everyone.

`Cookie` joined the header at v1.27, when the stored choice began deciding the
response. It is the **more** dangerous of the two to omit, because the cookie
*overrides* the header: a cache told only about `Accept-Language` would hand
one staff member's chosen Chinese page to the next visitor whose browser asked
for English, and the header it keyed on would have matched. The cost is nil —
the panel is authenticated and no shared cache stores it.

It is deliberately **not** set on the static origin, and the chooser did not
change that. **This is the payoff of building the calculator's chooser
client-side**: every visitor is still served a byte-identical `index.html`, and
the cookie is read by JavaScript after the response arrives, so nothing there
varies by anything. `Vary` on a near-unique header — or on a cookie — would
tell every shared cache to keep a separate copy of `index.html`, `styles.css`
and both font faces per value: a cache that stores everything and hits on
nothing. Had the calculator negotiated server-side to honour the same choice,
that is exactly what it would have cost.

**Nothing about the negotiation is written down anywhere, and the stored choice
is not written down beyond its own cookie.** Not in
`submission`, not in `audit_log`, not in a statistic. `docker/nginx.conf` now
declares its own access log format rather than inheriting the base image's
`main`, which logged `$remote_addr`, `$http_user_agent`, `$http_referer` and
`$http_x_forwarded_for` on every request to a calculator whose stated position
is that it stores nothing identifying about a visitor. `Accept-Language` was
absent from that format and is absent from ours; it is named in the file so
that adding it later has to be a decision somebody writes down rather than a
variable somebody appends. **`$http_cookie` is named alongside it for the same
reason and on the same terms**: the chooser's cookie rides on every request to
the static origin and to `/api/v1/`, because `path=/` cannot be scoped away
when the panel and the calculator both need it, and the one place it would
most easily end up written down is the log line that already sees it.

The API receives that cookie on every `POST /api/v1/calculate` and **ignores
it** — asserted by a test rather than left as obvious, since this is the same
file that was found logging four forbidden fields on 2026-08-12.

### A missing key falls back to English, and a test says so

Silently at runtime, loudly in the suite. A half-translated language ships as
English-in-places, which is readable; it never ships as a blank label or a raw
key, which are not.

`tests/admin/test_i18n.py` walks the live `form_args` of every registered view,
every view and base-view name, and the msgid set read out of the installed
sqladmin, and fails on anything without a translation. Rewording an English
string orphans its translation — the known cost of source-text keys — and this
is what turns that into a failing test on the commit that reworded it. A
separate check fails on any entry whose translation is blank or still equal to
its English source, because such an entry looks complete to every other test
and is not.

### The machine-translation notice

It used to sit on the switcher's option, where somebody was about to choose.
**With the switcher gone there is nothing to hang it on**, so it is now a
**non-dismissible strip at the very top of every page**, on both surfaces — the
calculator's inserted by `web/js/i18n.js`, the panel's in `brand/base.html` and
`sqladmin/layout.html` so that it reaches the five gate pages as well as the
panel proper.

**It is written twice: once in the language being read, and once in English.**
The one sentence a machine-translated page has to get right is the sentence
saying it was machine translated, and that sentence went through the same
machine as everything else in the file. The English half carries `lang="en"` so
a screen reader switches voice for it.

**It is also appended to the results export**, which leaves the browser and is
read by somebody who did not choose the language it was written in.

Driven by `machine_translated` in the catalogue, so a language arriving from a
machine pass labels itself and no list in code has to be extended nineteen
times. **English and Simplified Chinese carry no notice**: English is written
by hand, and Chinese is the one language with speakers on this project. That
exactly one calculator catalogue is unflagged is asserted by a test — flagging
Chinese would put a notice on a language that has reviewers, and unflagging any
other would make a claim about a review that has not happened.

### What must never be translated — rules, not practice

1. **Anything a staff member typed.** Factor provenance notes, taxonomy names
   and descriptions, formula labels, factor set version labels, audit log
   contents. This is what makes the panel WYSIWYG: what a staff member types is
   what the public sees, in the language they typed it. A translated copy would
   fall out of date the first time somebody renamed a row, and nobody would
   know whose job it was to fix.
2. **`code` identifiers**, on every table. §0 already forbids the front end
   learning a primary key; a translated code would be worse.
3. **Decimals.** They cross the wire as strings because JavaScript's `Number`
   is a double. No locale-aware decimal separator and no digit grouping, on
   either surface. `Number()` stays display-only.
4. **Metric units** — `kg CO2e`, `L`, `NZD` — and `metric.name`, which is a
   staff-typed row under rule 1.
5. **The equivalence sentences.** §3 defines `label` as `label_template` with
   the value already interpolated **by the engine**, and the results export
   copies it verbatim. They are data on their way through rather than
   interface, and translating them in the browser would override wording the
   client approved.
6. **`toLocaleString('en-NZ')`, in every language.** §1.2 puts decimals on the
   wire as strings because `Number` is a double, and a locale-aware separator
   would additionally make `1.200,50` and `1,200.50` the same figure written
   two ways on a page whose whole subject is a number. The interface is
   translated; the figures are not localised.
7. **Operator messages** from `admin/cli.py` and `docker/init.sh`. They are
   read in a terminal by whoever deploys the system, quoted verbatim in
   `docker/compose.yaml` and the README, and are what an operator pastes into a
   search engine. Translating them makes a deployment problem harder to
   diagnose, not easier.

### Right-to-left: shipped, and what it cost

Arabic and Urdu are two of the twenty, and both are right-to-left. The choice
was between shipping them with `dir="rtl"`, shipping them left-to-right, and
not shipping them — and **a half-mirrored page is worse than not shipping the
language**, because a reader cannot tell a layout bug from a translation error
and has no reason to trust either.

They ship. The condition was that the layout could actually carry it, and
making that true was twenty-six declarations in `styles.css`: every
`margin-left`, `padding-left`, `border-left` and `text-align: left` that
carried meaning became its `-inline-start` / `text-align: start` form, and the
symmetric pairs collapsed into `margin-inline` / `padding-inline`, which is the
same rendering in both directions. The whole layout now mirrors from the `dir`
attribute alone; there is no second stylesheet to keep in step.

Three things do not follow from `dir` and are handled by hand: the two
decorative arrows (`aria-hidden`, and not mirrored by the bidi algorithm — a
"current → improved" arrow pointing the wrong way reads as improved → current),
the progress and bar-chart fills, and the diverging comparison bar's inline
offset, which is `margin-inline-start` rather than `margin-left` — a physical
offset there would draw the negative half of a bar on the same side as the
positive half.

`tests/web/test_i18n_web.py` refuses a physical direction property in
`styles.css`, so the trade cannot be quietly undone by one convenient
`margin-left` added later.

### What a translated page actually looks like — the honest answer

**The interface is translated. The taxonomy inside it is not.** On the
calculator this is not a detail: destination names, food category names, sector
names, sector descriptions, metric names and units, and the equivalence
sentences all come from the database in the language staff entered them, which
is English. A Thai visitor gets Thai headings, Thai instructions, Thai
validation messages and Thai results wording — around a list that reads
`Landfill`, `Composting`, `Animal feed`.

**Is that coherent?** Yes, in the sense that matters: the reader can tell which
words belong to the tool and which are the names of things, and every word that
tells them what to do is in their language. It is the arrangement most public
tools with a controlled vocabulary end up in, and it is much better than an
English page. It is also plainly not finished, and calling it finished would be
the kind of claim this document exists to stop.

**This will not be closed. Ruled 2026-08-14 by the repository owner:
anything a staff member can edit is published exactly as written.**

The mechanism was costed first — a translated name column per taxonomy table
in §2.1, a panel screen to enter them, and a client decision about who writes
them — and rejected on what it would do to the panel's guarantee rather than on
its size. Today a staff member reads `Landfill` in the factor table and
`Landfill` is what the public sees; the row they maintain and the word a
visitor reads are the same string, so a question about a published number can
be traced to a field somebody can open. Nineteen unreviewed translations of
that word would break the trace in the place it is least affordable: a
destination name is not decoration, it is what tells a visitor which pathway a
figure belongs to, and a wrong one silently reassigns an impact.

It would also put the vocabulary on a different footing from every other
translated string. The interface strings are ours — a wrong one is a defect in
this repository and we fix it. Taxonomy names belong to the client and change
with MfE's definitions; a machine translation of them would be a claim about
the client's own terminology that nobody on this project is in a position to
make, and it would go stale the moment a name is edited.

**Consequence, stated so nobody reads the gap as an oversight:** a translated
page carries an English controlled vocabulary. That is the intended result, not
an unfinished one. If the client ever wants translated taxonomy, the shape is
that *they* supply the terms and staff enter them through the panel — the same
path as every other editable field — and not that a pipeline generates them.

### The strings where a translation error becomes a data error

Flagged because they are worth a second reviewer, and because the difference
between Chinese and the other nineteen is at its sharpest here. Each one, read
wrongly, produces a wrong number on the public site rather than a confused
staff member:

| Field | What a wrong reading does |
| --- | --- |
| `metric.display_unit` | "It must be the same scale." `t CO2e` against a `kg CO2e` unit relabels rather than divides, and every public greenhouse-gas figure reads a thousand times too small. This was live until August 2026 |
| `factor_upstream.value`, `destination.is_prevention` | A prevention destination's factors must be **zero**; that zero is the whole of the saving |
| `factor_downstream.value` | A negative number is legitimate and nothing clamps it. "Do not enter a negative" would delete the animal-feed credit |
| `factor_downstream.food_category`, `factor_upstream.destination` | **Leave blank** means "applies to every category / destination". Inverting this in translation mis-files every per-tonne charge, the waste levy included |
| `unit_preset.kg_per_unit` | "Get it wrong and every calculation made through this preset is wrong, with nothing on screen to say so" |
| `formula.expression` | Computes **one line**, never a total. Read as "write the total" it double-counts every calculation |
| `constant.name` | The `const_` prefix, and that `GWP_CH4_20`/`GWP_CH4_100` bind to `const_GWP_CH4` |
| `equivalence.metric`, `.label_template` | The metric must match the factor's basis, and `{value}` is template syntax rather than prose — a localised placeholder renders a sentence with no number in it |
| `factor_set.is_mock` | Unticking would remove the placeholder warning while the numbers underneath stayed placeholders |

`tests/admin/test_i18n.py` asserts that `{value}` and every `%(name)s`
placeholder survive translation, because those two fail as rendering errors
rather than as wrong words.

### te reo Māori — why it is absent rather than pending

Not a technical objection, and it survives the client's decision because it is
the answer if the question returns.

Te reo Māori is an official language of New Zealand under the Māori Language
Act 1987. Machine translation for it is trained on very little data and is
unreliable in ways that are not obvious to a non-speaker. The client is a New
Zealand trust, the deliverable carries a te reo word in its own name — **Kai**
Commitment — and the project sponsor is a former Prime Minister's Chief Science
Advisor. Bad te reo on a public New Zealand government-adjacent tool is not
read as a rough translation; it is read as carelessness about the language, and
it is the kind of thing that gets pointed out publicly.

**If it is ever asked for:** a human translator, which is a question to put to
the client because a trust at this level usually has that resource or knows who
does. If one is not available, shipping no te reo is the better answer than
shipping a machine one.

### One implementation route is closed

**Runtime translation through an external service is out of the question.**
This site's stated position is that it stores nothing about a visitor; sending
the page a visitor is reading to a third-party translation API contradicts
§2.3's whole argument and the statistics page's own restraint about what may be
claimed. Static language packs only, fetched at runtime the same way `?mock=1`
already fetches fixtures — which also keeps the no-build-step decision intact.

### Three places English still leaks through the boundary

Unchanged by this work, and all three still open. **Item 1 is no longer
theoretical**: it now renders on the calculator's most prominent card in
twenty languages, and the case for splitting the column is correspondingly
stronger.

1. **`equivalence.label_template` is a whole sentence, not a label.** It ships
   as `"Equivalent to driving {value} km"`, it is staff-editable, and it
   renders on the most prominent card of the results page. Under rule 1 above
   it stays English in every language. **Worth changing regardless**, and cheap
   only until O-1 lands: split the column into a unit fragment staff own
   (`"{value} km"`) and a static prefix the language pack owns. After real
   factors arrive, the same change is a migration over client data.
2. **`details[].field`'s companion `message` is composed by the API.**
   Top-level errors are already fine — `web/js/api.js`'s `publicError()`
   branches on the §9 `code` and renders its own copy. Field-level messages are
   the exception. Translating them means the API returns a code plus parameters
   and the front end composes the sentence.
3. **`metric.unit`.** `kg CO2e`, `L` and `NZD` need no translation; `kg` has
   local spellings in some scripts. Low risk, and rule 4 leaves it alone.

### What is not translated yet, and why the number is written down

**The calculator is complete: 299 of 299 strings, in twenty languages.** The
count is read out of the front end by `tests/web/i18n_keys.py` rather than
maintained by hand — `t('...')` calls, the `data-i18n` markers in the five HTML
files, and seven module-level constants whose contents reach `t()` by reference
— so a label added without a translation fails the suite rather than shipping
in English. A key nobody asks for any more fails it too, because a stale entry
makes a reworded string look translated.

**201 of those were the calculator alone; 99 arrived on 2026-08-15 with the
statistics, home and documentation pages** (contract v1.30, closing O-11), and
one was retired when the documentation page's title took the navigation's name.
Three things came out of that pass and belong here rather than only in the
change log:

* **The extractor was losing keys, silently and in a self-defending way.** It
  matched marked elements with a regex that consumed everything up to the
  outermost closing tag, and the public navigation is a `<nav
  data-i18n-attr="aria-label">` — which the pattern matched, because `\b` is
  satisfied by `data-i18n-attr` — wrapping four `<a data-i18n>` links. All four
  were invisible to it on all three pages, and **no test could have caught it**:
  a key nobody extracts is a key the coverage test cannot ask for, and
  translating it would have failed the *stale-key* test instead, which reads as
  a reason to delete the translation. It is an `html.parser` walk now, with a
  rule alongside it: a `data-i18n` element may not contain element children,
  because `applyToDocument` assigns `textContent` and would delete them.
* **A chart needed a second path.** `applyToDocument()` walks the DOM; a
  Chart.js title and a canvas `aria-label` are constructor arguments, and the
  title is painted onto a bitmap. So the statistics page destroys and rebuilds
  its three charts on a language change, from the response it already holds. The
  test reads the title back off the live chart's laid-out title block in two
  languages and proves the first canvases were destroyed rather than left
  behind the new ones — because "a rebuild function was called" is not evidence
  that a title changed.
* **A translation may legitimately equal its English source.** `Code` in French,
  `Name` in German, `Sector` in Dutch, `No` in Spanish. The test that forbids an
  entry equal to its key exists for a real failure — a placeholder that looks
  translated — but forcing a synonym would make the interface worse to read in
  exchange for a greener suite. A small per-language allowlist carries them, and
  a second test fails on any entry in it that is not actually identical, so it
  cannot outlive its reason.

**The panel is not.** Roughly 900 user-visible English strings exist there and
177 are translated: every field description, every view and menu name,
sqladmin's own chrome, the login gauntlet and this project's own template
strings. That last set was covered by nothing until this pass added a test that
walks the msgids in `admin/templates/`, which immediately found "Add a staff
member" and "Block an address" rendering in English on an otherwise Chinese
panel. **Deliberately left, with reasons:**

* **The six guidance blocks (123 strings).** Long-form prose in six template
  files, and the highest-value remaining work. They need a decision first:
  string-by-string `_()` wrapping loses the document; per-locale template files
  duplicate it and let the two drift structurally with no test able to compare
  them. The recommendation is the wrapping, for one mechanism.
* **~170 validation and error strings** in Python (`admin/accounts.py`,
  `factor_lifecycle.py`, `expressions.py`, `auth.py` and seven others). Raised
  as exception messages and surfaced through `{"error": ...}`; translating them
  means wrapping at each raise site — mechanical, but across eleven modules.
* **The staff-management and `/admin/security` templates (~270 strings).**
* **The login page's lockout paragraph**, alone among its neighbours: it
  composes English plurals inline (`attempt{{ 's' if ... }}`), so making it one
  translatable string means dropping that handling. A copy decision for the
  owner, not one to take silently.
* **Operator messages and seed display names** — never, per rules 5 and 1.

### The calculator's language list is still the client's to choose

This front end builds its pages in JavaScript, so most of the work is in the
modules rather than in the HTML: `calculator.js`, `results.js` and
`improvement.js` carry the bulk, and `methodology.js` and `stats.js` joined them
on 2026-08-15. The statistics page's wording is the most constrained on the
project — §6.4's rule that the subject is the calculator and never New Zealand —
and both guards on it — the source scan and the rendered-page scan — read
**English**. A translated page inherits its compliance from the source sentence
it was made from, and the one legitimate mention of New Zealand is a negation
that every catalogue was asked to keep as one. That is an inheritance, not an
enforcement, and it is the honest limit of what is checked: nothing in the suite
would notice a translation that turned the caveat into a claim.

A proposed list drawn from New Zealand's census language distribution rather
than from a generic global top twenty is with the repository owner; it is not
recorded here until the client has picked one.


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
