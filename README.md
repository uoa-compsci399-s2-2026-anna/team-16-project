# team-16-project

COMPSCI 399 project repository for Team 16 - 502 Bad Gateway.

**Kai Commitment Food Waste Impact Calculator** — a New Zealand food waste impact
calculator built for the Kai Commitment, an initiative of the New Zealand Food Waste
Champions 12.3 Trust.

> ### The factors currently shipped are placeholder data
>
> The client has not yet supplied real New Zealand emissions factors (open item **O-1**).
> Everything in this repository runs on a mock factor set so the full pipeline could be
> built and exercised end to end. While a mock factor set is the published one, a
> **non-dismissible placeholder-data banner appears on every results view and every
> export**, and that behaviour cannot be switched off. The numbers the calculator
> produces today are structurally correct and substantively meaningless. Do not quote
> them.
>
> Replacing them is a data task, not a code task: load a real factor set through the
> admin panel and publish it. Nothing needs to be rebuilt or redeployed.

**Contents**

- [What this is](#what-this-is)
- [How to run it](#how-to-run-it) — Docker, one command
- [How to develop on it](#how-to-develop-on-it)
- [Where the real documentation lives](#where-the-real-documentation-lives)

---

## What this is

| | |
| --- | --- |
| Client | Juliet Gerrard, with the Kai Commitment (NZ Food Waste Champions 12.3 Trust) |
| Course | University of Auckland COMPSCI 399 Capstone, Project 18 |
| Team | Team 16 — 502 Bad Gateway, five members |
| Deliverable | **Source code and documentation.** DNS, certificates, hosting and post-semester operations are out of scope. |

The product is modelled on the US ReFED Impact Calculator but uses New Zealand data and
New Zealand (Ministry for the Environment) definitions, and adds an **economic cost metric
that ReFED does not provide**.

**Every calculation is a comparison.** A visitor describes what happens to their food
waste today (the *current* scenario) and what could happen instead (the *alternative*
scenario). Both are calculated, and the difference between them is the net benefit. Doing
less harm by simply wasting less is expressed through a special destination, `prevention`,
whose factors are all zero — so the two scenarios always move the same mass and net
benefit cannot be inflated by quietly assuming less waste in the alternative.

**Nothing about the model is compiled in.** Food categories, destinations, sectors,
metrics, constants, equivalences and the *formulas themselves* are rows in the database,
edited by staff through the admin panel. Adding a metric means inserting a row and
writing one formula — not changing code and redeploying.

**Nothing identifying is ever collected.** No IP address, no user agent, no browser
fingerprint is stored. Each calculation is saved as an anonymous submission so that the
aggregate statistics can be built, and any statistical bucket with fewer than five
records is merged into `other` on the server before it is published.

What runs today:

- a public calculator at `/` — a six-step wizard, dual scenario, results and comparison
  screens, and a published-methodology page at `/methodology.html`;
- a public JSON API at `/api/v1/` — `taxonomy`, `calculate`, `factors`, `stats`;
- a staff admin panel at `/admin` — the taxonomy, the factor sets and their
  draft/publish/rollback lifecycle, editable formulas, the audit log, staff accounts with
  TOTP two-factor authentication, and an IP blocklist.

The public statistics page and its charts are not built. One known defect in the deployed
system is recorded as open item **O-6** (the seeded bucket-to-kilogram conversions are
placeholders too). **O-9** — `/admin/try`, the staff dry-run view, answering
`UNAUTHORIZED` in a deployed stack — was closed on 2026-08-12. See
`docs/architecture.md` §10 for the full list.

---

## How to run it

Docker is the answer for anyone who is not developing. One command brings up the
database, migrates it, seeds the New Zealand taxonomy, publishes the mock factor set,
creates two administrator accounts and starts the calculator behind nginx.

```bash
docker compose -f docker/compose.yaml up -d
```

Then open **<http://localhost:18080/>**.

Nothing binds port 80, 8000, 8080, 3000 or 5000 — a clean machine very often has
something on all of them already. Every published port is an environment variable with a
high default:

| Variable | Default | What |
| --- | --- | --- |
| `KAICALC_WEB_PORT` | `18080` | nginx — the calculator, the API and the panel. **The only port that must be published.** |
| `KAICALC_API_PORT` | `18000` | the API, direct. Development convenience only. |
| `KAICALC_ADMIN_PORT` | `18001` | the panel, direct. Development convenience only. |
| — | not published | MySQL. Reachable only from inside the compose network. |

nginx routes everything by default, so a headless server needs no further configuration:

| Path | Serves |
| --- | --- |
| `/` | the calculator (static HTML, CSS and ES modules) |
| `/api/v1/` | the public JSON API |
| `/admin` | the staff panel |

`/admin` being routed on the public origin is a deliberate decision, not an oversight —
on a headless server there is no other way in, because the panel is a browser-only
surface (two-factor enrolment is a QR code). The panel has TOTP, per-account login
lockout, a server-side IP blocklist, headless-client detection and a per-address rate
limit. **Whoever deploys this is still expected to put a WAF, an IP allowlist or a
network boundary in front of `/admin`.** To remove the route, delete the single
`location /admin` block in `docker/nginx.conf`; the cost of removing it is spelled out
there.

### The first administrator

The stack creates two administrator accounts on first start and prints their one-time
passwords:

```bash
docker compose -f docker/compose.yaml logs migrate
```

```
Created initial administrator accounts.
  admin: nEzVAJpKqCLvB1UPOFdP
  admin2: wWJv6nKE206RFhYbjkrK
```

Sign in at <http://localhost:18080/admin/login>. Each account is walked through a forced
password change and then two-factor enrolment before it reaches the panel; scan the QR
code with any authenticator app and save the recovery codes it shows you.

If a password is lost before that account has changed it, the **other** administrator can
read it back from `/admin/staff/list`. If both are lost, nobody can log in, and the way
back is a command on the container:

```bash
docker exec kaicalc-admin kaicalc issue-password admin
```

`kaicalc`, **not** `kaicalc-admin`. The second is the console script and it fails under
`docker exec` with `MissingSettingError: SECRET_KEY is not set`, because `docker exec`
does not run the image's entrypoint and inherits none of the environment it builds.
`kaicalc` is a wrapper that resolves the secret first and then calls the same command.
`kaicalc-admin --help` does work, which is exactly what makes the wrong form look correct
until the moment it is needed.

Everything after `kaicalc` is a subcommand:

```bash
docker exec kaicalc-admin kaicalc create-staff bob "Bob Smith" --admin
docker exec kaicalc-admin kaicalc issue-password alice     # mint a new password
docker exec kaicalc-admin kaicalc reset-mfa alice          # clear a lost authenticator
docker exec kaicalc-admin kaicalc unblock 203.0.113.5      # locked yourself out of /admin
docker exec kaicalc-admin kaicalc rotate-key --old <k> --new <k>
docker exec kaicalc-admin kaicalc --help
```

`unblock` exists for the case the protection layer is designed around not causing: an
administrator blocks the address they are sitting behind, and there is then no page left
to click. `.env.example` documents three routes back in, in the order to try them.

### What to set in the environment

**`SECRET_KEY` above all.** It signs staff session cookies, and it derives both the key
that encrypts two-factor secrets at rest and the key that fingerprints blocked addresses.
The API and the panel must hold the **same** value: they each derive the blocklist
fingerprint independently, so two different secrets mean a block applied in the panel
silently never matches at `/api/v1/`, with nothing raised on either side.

Out of the box the entrypoint generates one into a shared Docker volume on first start,
once, and every service reads that same file — so the stack works on a clean machine with
no configuration at all. **For anything that is not a laptop, set it explicitly** (in the
environment, or from a secret manager) and the generation path becomes a no-op:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Changing it later invalidates every enrolled authenticator. Run `kaicalc rotate-key
--old <old> --new <new>` **before** restarting with the new value; it re-encrypts every
stored secret in one transaction, and it clears the IP blocklist, telling you how many
rows it removed (an address is stored as an HMAC, and an HMAC cannot be re-keyed — the
rows would otherwise survive matching nobody). Re-apply any blocks that are still needed.

The other settings, each documented at length in **`.env.example`** with the failure it
prevents:

| Setting | Note |
| --- | --- |
| `MYSQL_ROOT_PASSWORD`, `MYSQL_USER`, `MYSQL_PASSWORD` | change these for anything that is not a laptop |
| `KAICALC_SESSION_HTTPS_ONLY` | defaults to `false` so the shipped plain-http stack is usable. **Set it to `true` the moment TLS is in front.** |
| `PROTECTION_TRUSTED_PROXY` | defaults to `false`. Behind a real reverse proxy, leave it false and every caller arrives as the proxy — the API's per-visitor rate limits collapse into one site-wide bucket. Set it true, and remove the direct `ports:` for `api` and `admin`, together. |
| `PROTECTION_ENABLED` | the escape hatch if the panel's protection layer locks everyone out. Every setting is read once at start-up, so edit *and restart*. |
| `LOGIN_MAX_FAILURES`, `LOGIN_LOCKOUT_MINUTES` | login throttling. Lockout counters live in process memory, so `docker compose -f docker/compose.yaml restart admin` clears every lockout immediately. |

### Stopping it

```bash
docker compose -f docker/compose.yaml down       # keeps the data and the secret
docker compose -f docker/compose.yaml down -v    # destroys both — new secret,
                                                 # new administrator passwords,
                                                 # empty database
```

### Released images and the wheel

- **Releases** are cut by the `Release` workflow (`.github/workflows/release.yaml`),
  triggered by hand with a version number of the form `X.Y.Z`. It commits that version to
  the default branch, tags that commit, runs the full test suite against MySQL 8, builds
  three multi-architecture images tagged `X.Y.Z` and `latest`, and attaches a wheel and an
  sdist to a GitHub Release.
- **CI** (`.github/workflows/ci.yaml`) runs the same test suite on every push and pull
  request, and on `main` publishes alpha images tagged `alpha` and by short commit SHA.
  Alpha versions are `<base>.dev0+<sha>`, which sorts *below* every release and can never
  be mistaken for one.
- Images are `ghcr.io/uoa-compsci399-s2-2026-anna/team-16-project/kaicalc-{api,admin,web}`.
  Deployers should pin the version tag rather than `latest`.

---

## How to develop on it

### The stack

Python 3.11+ / FastAPI / SQLAlchemy 2.x with Alembic / MySQL 8 / `sqladmin` for the admin
panel / `pytest`.

The front end is plain HTML, CSS and JavaScript ES modules. **No React, no Node.js, no
build step** — what is in `web/` is what the browser runs. The team had no front-end
framework experience, and with no framework there is no build tooling, so the stack closes
cleanly on Python. Chart.js is the charting library `docs/architecture.md` §2 selects, but
nothing in `web/` imports it yet; the charts belong to the unbuilt statistics page.

Expression evaluation is hand-rolled over the standard library's `ast`, deliberately not
`simpleeval` — see below.

### Getting a checkout running

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows;  source .venv/bin/activate elsewhere
pip install -e ".[dev]" -c docker/constraints.txt

docker compose up -d            # the DEVELOPMENT DATABASE ONLY, MySQL on host port 3307
cp .env.example .env            # then fill in SECRET_KEY
```

**`-c docker/constraints.txt` is not optional, and leaving it off is not a slower
install — it is a different one.** `pyproject.toml` states every dependency as a `>=`
floor, so without the constraint file pip resolves each floor to whatever PyPI published
most recently, and your checkout is running different software from the two images, which
build with that same file. The gap is silent while it is small. It has already cost this
project once: the pin moved to sqladmin 0.31.0 on 10 August, the desk stayed on 0.30.0,
and `admin/templates/sqladmin/_macros.html` — a vendored copy of a template out of that
package, added on 13 August — was therefore copied from a version the panel has never
run, and was wrong the moment it was written. `tests/admin/test_i18n.py` now fails
if the installed version is not the pinned one, so a skewed environment reports itself
rather than surfacing later as an unrelated-looking test failure.

Use it on `pip install -r requirements.txt` too — that file now carries the constraint
itself, so plain `-r requirements.txt` is already correct.

Two compose files, two jobs, and confusing them wastes an afternoon:

- **`docker-compose.yml`** (repository root) — one MySQL container on host port 3307, for
  the test suite and for running the application from a checkout. It starts no
  application service.
- **`docker/compose.yaml`** — the whole deployment, as described above. Its database is
  not published at all, so the two run side by side.

Then, against the development database:

```bash
./run.sh migrate                # alembic upgrade head. run.ps1 is the PowerShell twin
kaicalc-admin seed-taxonomy     # the NZ taxonomy. Idempotent; safe on every deploy
python docker/seed_mock_factors.py   # a published mock factor set. Does nothing if any
                                     # factor set already exists
./run.sh api                    # http://127.0.0.1:18000
./run.sh admin                  # http://127.0.0.1:18001
```

Everything after the subcommand is forwarded untouched, so `./run.sh api --reload` and
`./run.sh migrate current` both work.

**Run `migrate` once, from one process, before either server starts.** MySQL autocommits
DDL, so two processes running `alembic upgrade head` concurrently interleave and leave a
half-applied schema stamped at a revision it does not match; recovery is `DROP DATABASE`.
Nothing in this system migrates on start-up, for that reason. `SECRET_KEY` must already
be in the environment before `alembic upgrade head` will run at all — a migration cannot
be the step that first establishes the environment.

Without the last two seeding steps a correctly installed system answers
`503 NO_PUBLISHED_FACTOR_SET` to every public endpoint. That is expected, not a fault: the
taxonomy is the vocabulary and the factors are the data.

The front end can be developed with no backend at all, against the contract fixtures.
From the **repository root**:

```bash
python -m http.server 8000
# then http://127.0.0.1:8000/web/index.html?mock=1
```

### The layering, and the invariants that hold it up

```
web/    static front end          — renders; calculates nothing but unit conversion
  │  HTTP / JSON
api/    FastAPI                   — validation, rate limiting, error envelopes
  │
  ├── engine/   pure computation
  └── admin/    sqladmin + custom views
        │
db/     SQLAlchemy models and the repository
```

Violating any of the following silently breaks a decision that was made deliberately.
`docs/architecture.md` §3.1 is the long form.

- **`db/repository.py` is the only code that touches the database.** No other module may
  import SQLAlchemy.
- **The engine is a pure function.** `engine.calculate(request, bundle)` takes a
  pre-loaded `FactorBundle` and returns a result. No database access, no file access, no
  system clock. This is what makes the golden test suite meaningful.
- **Metrics are data, not code.** The engine iterates over `metric` rows and evaluates
  each one's stored formula. The test for correctness: adding a metric must mean inserting
  a row and writing one formula, never editing a call site.
- **`DECIMAL` everywhere; `FLOAT` and `DOUBLE` are prohibited.** `decimal.Decimal` on the
  Python side. In JSON, decimals are transmitted **as strings** (`"qty_kg": "1200.500"`)
  because JavaScript's `Number` is a double. The front end may call `Number()` for display
  and charting only, never for a calculation.
- **`code` is the cross-layer identifier.** Requests and responses use the `code` column,
  never the auto-increment `id`. The front end must never learn a primary key.
- **A formula computes one line; the engine performs the summation.**
  `line_value = f(qty_kg, upstream, downstream, const_*)`, then
  `metric_total = Σ line_value`. This is why the expression language needs no arrays, no
  loops and no `sum()` — which is what keeps the evaluator's security boundary
  unambiguous.
- **Impact calculation happens server-side, in exactly one place.** When staff change a
  formula there is no possibility of two sides disagreeing.
- **Taxonomy groupings are tables, not enums.** Which destinations count as waste is an
  MfE definition that may be revised, so `destination_group.is_waste` stays configurable.

### The expression evaluator

There are **two independent AST whitelists and they must agree**:
`admin/expressions.py` validates a formula when staff save it, and `engine/evaluator.py`
evaluates it when the calculator runs. `tests/test_evaluator.py` runs one corpus through
both and fails when they diverge. That test is the only thing stopping them drifting —
eight divergences have been found and closed that way, one of which made the engine
silently evaluate `round(qty_kg)` for a formula written as `round(qty_kg, ndigits=2)`.

If you change one whitelist, change the other, and add the case to the corpus.

### Tests

The suite needs a **real MySQL 8**, not SQLite. `ENUM`, `VARBINARY`, `DECIMAL` precision
and collation ordering all behave differently, the two functional `COALESCE` unique
indexes do not exist on SQLite at all, and a MySQL `CHECK` violation surfaces as
`OperationalError` where SQLite raises `IntegrityError` — which this suite asserts
directly. `tests/conftest.py` connects to `root:devroot@127.0.0.1:3307` (the root
`docker-compose.yml`'s credentials) and creates its own scratch databases.

```bash
docker compose up -d            # MySQL on 3307, if it is not already running
python -m pytest                # the whole suite; takes several minutes
python -m pytest -m "not db"    # skips everything that needs the database
python -m pytest tests/golden   # the correctness suite alone
```

Run one suite at a time. Two concurrent runs share one MySQL server and the admin tests
deadlock against each other.

Two directories carry more weight than the rest:

- **`tests/fixtures/*.json` — the executable form of the contract.** Backend contract
  tests assert that real responses match these shapes, and the front end develops against
  them directly. A contract change that does not reach these files is not finished.
- **`tests/golden/case_*/` — the correctness suite.** Nine cases, three files each
  (`bundle.json`, `request.json`, `expected.json`). Every engine change must leave all of
  them passing. This suite is the only evidence that the calculator computes correctly.

CI runs the same suite against MySQL 8 on every push and pull request, on Python 3.11 —
the version the images run.

### Changing the contract

`docs/interfaces.md` governs every cross-module call. A contract change requires all three
steps, every time:

1. update `docs/interfaces.md` (and its change log);
2. tell the whole team;
3. update `tests/fixtures/*.json`.

Renaming a field unilaterally is the single largest source of rework on a five-person
project.

---

## Where the real documentation lives

| File | What it is |
| --- | --- |
| **`docs/interfaces.md`** | **The contract.** Database schema, engine domain objects, repository signatures, the full REST request and response shapes, front-end module signatures, error codes, fixture conventions. The single source of truth. |
| **`docs/architecture.md`** | System layering, the calculation model, factor-set versioning, the team split, implementation order (§9), deployment notes (§9.1), and the **open items (§10)** — including O-1, the missing real factors. |
| `.env.example` | Every setting, with the failure each one prevents. |
| `docker/compose.yaml`, `docker/nginx.conf` | The deployment topology and the routing, both heavily commented. |
| `web/README.md` | The front-end module layout and the no-backend development mode. |
| `db/README.md` | The schema and public API module. |

The English `.md` files under `docs/` are canonical and committed; `.docx` versions are
generated artefacts and are not tracked:

```bash
pandoc docs/interfaces.md -o docs/interfaces.docx --toc --toc-depth=2
```

Front-end work follows `Kai Commitment_Brand Guidelines_v1-Oct25.pdf`. Palette: White
`#FFFFFF`, Kale `#003223` (primary), Orange `#FF5032`, Pea `#28C882`, Blueberry `#005AE6`,
Beetroot `#87005A`, Banana `#FFD76E`, Lavender `#E6BEFF`. Headings in Geologica Bold, body
in Kumbh Sans Regular, both self-hosted under `web/assets/fonts/`.
