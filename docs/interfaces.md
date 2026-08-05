---
title: "Kai Commitment Impact Calculator — Interface and Data Contract"
subtitle: "Single source of truth for five-way parallel development"
date: "2026-08-05 (v0.7 draft)"
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
| `description` | TEXT | NULL | |
| `sort_order` | INT | NOT NULL, DEFAULT 0 | |
| `active` | BOOLEAN | NOT NULL, DEFAULT TRUE | |

### `food_category`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | |
| `code` | VARCHAR(64) | UNIQUE, NOT NULL | The eight Otago baseline categories plus `standard_mix` |
| `name` | VARCHAR(128) | NOT NULL | |
| `is_standard_mix` | BOOLEAN | NOT NULL, DEFAULT FALSE | Fallback when the user does not know the composition. **Exactly one row must be TRUE.** |
| `sort_order` | INT | NOT NULL, DEFAULT 0 | |
| `active` | BOOLEAN | NOT NULL, DEFAULT TRUE | |

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

### `factor_upstream`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | BIGINT | PK, AI | |
| `factor_set_id` | INT | FK, NOT NULL | |
| `sector_id` | INT | FK, NOT NULL | |
| `food_category_id` | INT | FK, NOT NULL | |
| `metric_id` | INT | FK, NOT NULL | |
| `value_per_kg` | DECIMAL(20,10) | NOT NULL | |

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

UNIQUE(`factor_set_id`, `destination_id`, `food_category_id`, `metric_id`)

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
| `sort_order` | INT | NOT NULL, DEFAULT 0 | |
| `active` | BOOLEAN | NOT NULL, DEFAULT TRUE | |

## 2.3 Submissions

### `submission`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | BIGINT | PK, AI | |
| `token` | CHAR(36) | UNIQUE, NULL | UUID4 session token; nulled on expiry |
| `token_expires_at` | DATETIME | NULL | One hour after creation |
| `created_at` | DATETIME | NOT NULL | |
| `updated_at` | DATETIME | NOT NULL | Refreshed on upsert |
| `factor_set_id` | INT | FK, NOT NULL | Version stamp; makes results reproducible |
| `sector_id` | INT | FK, NOT NULL | |
| `food_category_id` | INT | FK, NULL | Null when the user did not break waste down by type |
| `gwp_horizon` | SMALLINT | NOT NULL, DEFAULT 100 | |
| `excluded_from_public` | BOOLEAN | NOT NULL, DEFAULT FALSE | **Staff moderation, not user consent** |
| `exclusion_reason` | VARCHAR(255) | NULL | |

**No IP address, no user agent, no fingerprint of any kind is stored.**

### `submission_line`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | BIGINT | PK, AI | |
| `submission_id` | BIGINT | FK, NOT NULL, ON DELETE CASCADE | |
| `scenario` | ENUM(`current`, `alternative`) | NOT NULL | |
| `destination_id` | INT | FK, NOT NULL | |
| `qty_kg` | DECIMAL(16,3) | NOT NULL, >= 0 | |

UNIQUE(`submission_id`, `scenario`, `destination_id`)

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

```python
from dataclasses import dataclass
from decimal import Decimal

# ---------- Input ----------

@dataclass(frozen=True)
class ScenarioLine:
    destination_code: str
    qty_kg: Decimal                 # >= 0

@dataclass(frozen=True)
class ScenarioInput:
    sector_code: str
    food_category_code: str | None  # None -> use standard_mix
    lines: tuple[ScenarioLine, ...]

@dataclass(frozen=True)
class CalculationRequest:
    current: ScenarioInput
    alternative: ScenarioInput | None
    gwp_horizon: int = 100          # 20 or 100

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
    by_destination: tuple[BreakdownRow, ...]

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
class CalculationResult:
    factor_set_version: str
    is_mock: bool
    gwp_horizon: int
    current: ScenarioResult
    alternative: ScenarioResult | None
    net_benefit: dict[str, Decimal] | None  # key = metric_code
```

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
        """Build a bundle from the bundle.json shape defined in §10.1.
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
    Evaluate the current scenario (and the alternative, if present) and
    compute net benefit.

    Parameters
      req    : A validated request. Every destination, sector and
               food_category code must exist in bundle, otherwise
               UnknownCodeError is raised.
      bundle : Factor set snapshot.

    Returns
      CalculationResult. When alternative is None, net_benefit is also None.

    Raises
      UnknownCodeError     : a code in the request is absent from bundle
      UnknownConstantError : a formula references a constant that does not exist
      FormulaError         : syntax error, division by zero, illegal identifier
      ValueError           : gwp_horizon is neither 20 nor 100

    Guarantees
      Pure. The same (req, bundle) always yields the same result.
    """
```

```python
def calculate_scenario(scenario: ScenarioInput, bundle: FactorBundle,
                       gwp_horizon: int) -> ScenarioResult:
    """Evaluate a single scenario. Called internally by calculate()."""
```

```python
def net_benefit(current: ScenarioResult,
                alternative: ScenarioResult) -> dict[str, Decimal]:
    """Per metric: current.total - alternative.total.
    Only metric codes present on both sides are included."""
```

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

## 5.3 Submissions

```python
def upsert_submission(session, token: str | None, req: CalculationRequest,
                      factor_set_id: int) -> tuple[int, str]:
    """
    Upsert keyed by token.

    token is None or expired -> insert a new row and mint a new UUID4 token.
    token is valid           -> overwrite that row, including all its lines.

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
    """

@dataclass(frozen=True)
class StatsBucket:
    code: str          # taxonomy code, or 'other'
    label: str
    count: int
    share: Decimal     # 0..1
    total_kg: Decimal

@dataclass(frozen=True)
class PublicStats:
    generated_at: datetime
    total_calculations: int
    suppression_threshold: int
    by_destination: tuple[StatsBucket, ...]
    by_sector: tuple[StatsBucket, ...]
    by_food_category: tuple[StatsBucket, ...]
```

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

**Request**

```json
{
  "token": "3f2b… (optional; omitted on the first call)",
  "sector": "processing",
  "food_category": "dairy",
  "gwp_horizon": 100,
  "current": [
    { "destination": "landfill", "qty_kg": "1200.000" },
    { "destination": "animal_feed", "qty_kg": "300.000" }
  ],
  "alternative": [
    { "destination": "anaerobic_digestion", "qty_kg": "1200.000" },
    { "destination": "animal_feed", "qty_kg": "300.000" }
  ],
  "dry_run": null
}
```

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `token` | string \| null | No | Session token; omitted on the first call |
| `sector` | string | Yes | Must exist in the taxonomy |
| `food_category` | string \| null | No | Null is treated as `standard_mix` |
| `gwp_horizon` | int | No | 20 or 100; defaults to 100 |
| `current` | array | Yes | At least one line |
| `alternative` | array \| null | No | Null means no comparison is performed |
| `dry_run` | object \| null | No | **Staff only**; see §6.2.1. Requires `X-Dry-Run: true` |

**Validation rules (enforced server-side)**

| Rule | On violation |
| --- | --- |
| `qty_kg >= 0` | `VALIDATION_ERROR` |
| Per line `qty_kg <= 10,000,000` | `VALIDATION_ERROR` |
| Per scenario total `<= 50,000,000` | `VALIDATION_ERROR` |
| Per scenario line count `<= 20` | `VALIDATION_ERROR` |
| No duplicate `destination` within a scenario | `VALIDATION_ERROR` |
| All codes exist | `UNKNOWN_CODE` |
| `dry_run` present without `X-Dry-Run: true` | `VALIDATION_ERROR` |
| `dry_run.factor_set_version` and `dry_run.bundle` both non-null | `VALIDATION_ERROR` |
| `dry_run.bundle` row count across all tables `<= 5000` | `VALIDATION_ERROR` |
| `dry_run.bundle` fails `FactorBundle.validate()` | `VALIDATION_ERROR`, one `details` entry per problem |

**Request headers**

| Header | Purpose |
| --- | --- |
| `X-Dry-Run: true` | **Do not persist.** No `submission` or `submission_line` row is written and no token is returned. **Requires an authenticated staff session** (§8.4); an unauthenticated request carrying this header is rejected with `UNAUTHORIZED` (401). |

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
| `bundle` | object \| null | A **complete** factor set snapshot in the §10.1 `bundle.json` shape |

The two are mutually exclusive. When both are null the published set is used — the request is still not persisted.

> **Why a complete bundle rather than a diff against a base version.** A merge routine is new, untested code sitting between the staff member and the engine: when a dry run produces a wrong number, there is no way to tell whether the formula was wrong or the merge was. Diffs also have unpleasant edge cases — how does a generic `food_category: null` row merge with a specific one, and how is "delete this row" expressed? A complete snapshot has none of these questions. The volume does not justify the risk: roughly 270 upstream rows, 600 downstream rows and 20 others, around 90 KB uncompressed, and it travels behind authentication.

> **The bundle carries the taxonomy, not just the factors** (§10.1). Two reasons: §4.1's `has_destination()`, `has_sector()`, `has_food_category()` and `standard_mix_code()` cannot be implemented without it; and the client has stated that food categories, destinations and groupings will change over time, so staff must be able to trial a new destination before committing it — which is impossible if the bundle cannot carry its definition.

> This does **not** change how taxonomy is normally edited. Routine create/update/delete still goes through the admin CRUD of §8.1 and writes to the database. An inline bundle is a parallel, temporary channel that exists only for the lifetime of one request.

Because every dry-run request carries its own data, concurrent staff dry runs are isolated by construction. The path is stateless: no locks, no scratch tables, no TTL sweeper, and no orphan rows when someone closes the browser mid-edit.

**200 response**

```json
{
  "factor_set": { "version_label": "MOCK-v0 — PLACEHOLDER", "is_mock": true },
  "factor_source": "published",
  "gwp_horizon": 100,
  "token": "3f2b…",
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
```

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
      "metric": "co2e", "value_per_kg": "1.9000000000" }
  ],
  "downstream": [
    { "destination": "landfill", "food_category": "dairy",
      "metric": "co2e", "value_per_kg": "0.9900000000" }
  ],
  "equivalences": [
    { "code": "km_driven", "source_metric": "co2e",
      "value_per_unit": "4.1800000000",
      "label_template": "Equivalent to driving {value} km" }
  ]
}
```

With `format=csv`, one CSV file per table is returned, bundled as a zip archive (`Content-Type: application/zip`).

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

> **Copy constraint (owner: D).** The subject of the statistics page must be the calculator itself — "Across the 1,247 calculations run in this tool…" — and **never** "Distribution of food waste destinations in New Zealand". Prefer `share`; if `total_kg` is displayed it must be explicitly labelled as the cumulative total entered into this tool.

## 6.5 Rate Limits

| Endpoint | Limit |
| --- | --- |
| `POST /api/v1/calculate` | 120 / hour / IP |
| `GET /api/v1/*` | 600 / hour / IP |

Exceeding a limit returns `429` with `RATE_LIMITED`. Counters live in memory or Redis; **IP addresses are never persisted.**

---

# 7. Front-End Modules (owners: C and D)

ES modules, no build step. Located in `web/js/`.

## 7.1 `api.js` (written by C, shared with D)

```js
/**
 * @typedef {Object} ApiError
 * @property {string} code     Error code, see §9
 * @property {string} message  Display-ready message
 * @property {Array}  details  Field-level errors; may be empty
 */

/** @returns {Promise<Taxonomy>} @throws {ApiError} */
export async function getTaxonomy();

/**
 * @param {CalculatePayload} payload
 * @param {{dryRun?: boolean}} [opts]
 * @returns {Promise<CalculationResult>}
 * @throws {ApiError}
 */
export async function calculate(payload, opts);

/** @returns {Promise<PublicStats>} @throws {ApiError} */
export async function getStats();

/** @param {{version?: string}} [opts] @returns {Promise<Factors>} */
export async function getFactors(opts);
```

`api.js` owns URL construction, headers, JSON parsing, and converting any non-2xx response into a thrown `ApiError`. **No other module calls `fetch` directly.**

## 7.2 `state.js` (written by C)

```js
/** Single state object. Ad-hoc DOM manipulation elsewhere is not permitted. */
export const state = {
  taxonomy: null,
  token: null,               // from sessionStorage
  sector: null,
  foodCategory: null,
  gwpHorizon: 100,
  current: [],               // [{destination, qtyKg, unitPreset, unitCount}]
  alternative: [],
  result: null,
  loading: false,
  error: null,
};

/** Shallow-merges the patch and notifies all subscribers */
export function setState(patch);

/** @param {(s: typeof state) => void} fn @returns {() => void} unsubscribe */
export function subscribe(fn);
```

## 7.3 `units.js` (written by C)

```js
/**
 * Convert a container count to kilograms.
 * @param {number} count       Number of containers
 * @param {string} presetCode  unit_preset code
 * @param {Array}  presets     taxonomy.unit_presets
 * @returns {string}           Kilograms as a string with 3 decimal places,
 *                             ready to send to the API
 * @throws {Error}             presetCode does not exist
 */
export function toKg(count, presetCode, presets);
```

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

## 7.6 Front-End Hard Constraints

1. **The front end performs no impact calculation.** Apart from unit conversion in `units.js`, every number comes from the API.
2. **When `is_mock` is true, the warning banner is mandatory** and cannot be dismissed.
3. The calculator page is **mobile-first**, baseline width 375px.
4. After every successful calculation, write the returned `token` back to `sessionStorage`.

---

# 8. Admin Panel (owner: E)

Built on `sqladmin`, mounted at `/admin`, authentication required.

## 8.1 Models Exposed for Direct CRUD

`sector`, `food_category`, `destination`, `destination_group`, `metric`, `unit_preset`, `constant`, `formula`, `equivalence`, `factor_upstream`, `factor_downstream`

Requirements: list views must offer search and filtering.

**Every write must produce an `audit_log` entry.** Admin CRUD achieves this through an `AuditedModelView` base class that all eleven views inherit; factor-set lifecycle operations do it inline in the repository. Both call `write_audit()` (§5.5), which is the only code that inserts into `audit_log`.

`AuditedModelView` captures the pre-change row in the before-write hook — the after-write hook only ever sees the new values — and hard-codes `can_create = can_edit = can_delete = False` on the `audit_log` view itself.

## 8.2 Custom Views

| View | Path | Function |
| --- | --- | --- |
| Factor sets | `/admin/factor-sets` | Clone, publish, roll back; shows draft/published/archived state |
| Dry run | `/admin/try` | Enter a test scenario, call `POST /api/v1/calculate` with **`X-Dry-Run: true`** and a `dry_run` object (§6.2.1), and display the line-by-line breakdown |
| Pre-publish comparison | `/admin/factor-sets/{id}/compare` | Run a fixed set of standard test scenarios against **both** the published set and this draft, and show old value, new value and change per metric. The last gate before publishing. |
| Submissions | `/admin/submissions` | Record-level list with search and filtering; allows setting `excluded_from_public` with a reason |
| Audit log | `/admin/audit` | Read-only, filterable by actor, time and table |

> The dry-run view **must** send the dry-run header. Staff will run dozens of calculations while tuning a formula, and persisting them would directly pollute the public statistics.

The comparison view is two dry-run calls per scenario — one with `factor_set_version` set to the published label, one to the draft — differenced client-side. The standard scenarios it runs are staff-editable rather than hard-coded; hard-coding them would reintroduce "change the code to change the configuration", which Decision 2 exists to prevent.

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

> ### ⚠️ Known limitation: eviction does not revoke a live session
>
> Found while implementing the panel, verified end to end. Sessions carry no generation marker, so nothing distinguishes a cookie issued before an eviction from one issued after.
>
> **Changing a compromised account's password does not terminate its sessions, at any point.** That is the plain statement, and it is worse than it first appears:
>
> | Administrator action | Effect on a stolen live session |
> | --- | --- |
> | Issue a new password only | **None. 200 throughout, no interruption.** This is the intuitive response to "my session may be compromised" and the one a `StaffAdmin` view will most plausibly offer. |
> | Reset MFA, then issue a new password | Locked out **only while `mfa_enrolled` is false** — the moment the rightful holder completes the re-enrolment the reset compels, the old cookie works again |
> | Deactivate the account | Effective immediately, but `_guard_admin_floor` forbids it for an administrator while fewer than three are usable |
>
> Until this is fixed, the only reliable answers are **deactivate** (where the floor permits it) or **wait out `SESSION_MAX_AGE_MINUTES`**. The handover documentation must say so.
>
> The window is bounded rather than rolling: Starlette re-signs the cookie only when the session is modified, so it still dies at `SESSION_MAX_AGE_MINUTES` from the original login. But eight hours is comfortably longer than "an administrator resets your authenticator, hands you a password, and you enrol".
>
> **This affects `require_staff()` (§8.4) and therefore the API layer, not only the admin panel.** B should know that a `require_staff()` success does not currently mean "this session has not been evicted".
>
> The fix is a session generation column on `staff`, incremented by `reset_mfa` and by any future `issue_password`, stamped into the session at login and compared on every `require_staff()` call. It is deferred rather than done because it changes an E-1 table and an E-1 function that are otherwise complete and reviewed.
>
> Related gap, same root: §8.3 names "issues a random password" as half of the eviction, but no service function performs it. `set_password` **clears** `must_change_password`, so an issued password leaves the account looking fully onboarded. `admin/accounts.py` needs an `issue_password()` that sets a random password **and** `must_change_password = True` — an administrator-issued password is in the same position as a bootstrap one and deserves the same forced change.

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
| `python -m admin.cli rotate-key --old <k> --new <k>` | Re-encrypt every `mfa_secret_enc` after a `SECRET_KEY` change |
| `python -m admin.cli bootstrap` | Create the initial administrator accounts if none exist |

One module with subcommands rather than three separate module entry points: `python -m admin.cli --help` then lists every operational command in one place, which is what the handover documentation needs, and settings loading and session construction are written once rather than three times.

The rotation command is not optional. `SECRET_KEY` lives in `.env`, and without rotation the day the client changes it is the day every account loses its second factor.

### Blocklist

An `ip_blocklist(id, cidr, reason, created_at, created_by)` table plus middleware, used to block sources of junk submissions, **never to identify ordinary users.** The middleware reads the connecting address and stores nothing.

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
    "details": [ { "field": "current[0].qty_kg", "issue": "exceeds_max" } ]
  }
}
```

| HTTP | `code` | Trigger | Front-end response |
| --- | --- | --- | --- |
| 400 | `VALIDATION_ERROR` | Missing field, out of bounds, duplicate destination, malformed dry-run bundle | Highlight the offending field |
| 400 | `UNKNOWN_CODE` | A code in the request does not exist | Re-fetch the taxonomy and prompt a refresh |
| 401 | `UNAUTHORIZED` | `X-Dry-Run: true` without a valid staff session | Redirect to `/admin/login` |
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
| `errors/*.json` | One sample per error code |

**These files are the executable form of the contract.** Backend contract tests assert that real responses match their shape; the front end develops against them directly. They must be updated whenever the contract changes (see §0).

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

`downstream[].food_category` may be `null`, meaning the row applies to every food category for that destination (§2.2 — this is how per-tonne charges such as the waste levy are expressed). **`null` is a legal key value, not a missing field**, and must survive both serialisation and deserialisation.
