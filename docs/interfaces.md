---
title: "Kai Commitment Impact Calculator — Interface and Data Contract"
subtitle: "Single source of truth for five-way parallel development"
date: "2026-07-31 (v0.1 draft)"
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
```

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
    """Evaluate a single scenario. Called internally by calculate(), and
    used directly by the admin dry-run page."""
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
    The result should be cached by factor_set_id and invalidated on
    publish or rollback."""

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
  ]
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

**Validation rules (enforced server-side)**

| Rule | On violation |
| --- | --- |
| `qty_kg >= 0` | `VALIDATION_ERROR` |
| Per line `qty_kg <= 10,000,000` | `VALIDATION_ERROR` |
| Per scenario total `<= 50,000,000` | `VALIDATION_ERROR` |
| Per scenario line count `<= 20` | `VALIDATION_ERROR` |
| No duplicate `destination` within a scenario | `VALIDATION_ERROR` |
| All codes exist | `UNKNOWN_CODE` |

**Request headers**

| Header | Purpose |
| --- | --- |
| `X-Dry-Run: true` | **Admin panel only.** Calculates without persisting and returns no token. Used for staff testing so that statistics are not polluted. |

**200 response**

```json
{
  "factor_set": { "version_label": "MOCK-v0 — PLACEHOLDER", "is_mock": true },
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

Requirements: list views must offer search and filtering; all writes must go through the repository functions in §5 so that `audit_log` entries are produced.

## 8.2 Custom Views

| View | Path | Function |
| --- | --- | --- |
| Factor sets | `/admin/factor-sets` | Clone, publish, roll back; shows draft/published/archived state |
| Dry run | `/admin/try` | Enter a test scenario, call `POST /api/v1/calculate` with **`X-Dry-Run: true`**, and display the line-by-line breakdown |
| Submissions | `/admin/submissions` | Record-level list with search and filtering; allows setting `excluded_from_public` with a reason |
| Audit log | `/admin/audit` | Read-only, filterable by actor, time and table |

> The dry-run view **must** send the dry-run header. Staff will run dozens of calculations while tuning a formula, and persisting them would directly pollute the public statistics.

## 8.3 Accounts and Blocklist

- Accounts: `sqladmin` built-in authentication. Staff accounts are created by an administrator; there is no self-service registration.
- Blocklist: an `ip_blocklist(id, cidr, reason, created_at, created_by)` table plus middleware. Used to block sources of junk submissions, **never to identify ordinary users.**

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
| 400 | `VALIDATION_ERROR` | Missing field, out of bounds, duplicate destination | Highlight the offending field |
| 400 | `UNKNOWN_CODE` | A code in the request does not exist | Re-fetch the taxonomy and prompt a refresh |
| 429 | `RATE_LIMITED` | Rate limit exceeded | Ask the user to retry later; disable the button for 60s |
| 500 | `FORMULA_ERROR` | A staff-configured formula is invalid | Show a generic failure message; **do not** echo the expression |
| 503 | `NO_PUBLISHED_FACTOR_SET` | No factor set has been published | Show "calculator under maintenance" |

`message` is written for end users and may be displayed verbatim. `details` is for developers and form-field targeting.

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
  bundle.json     a fixed factor set
  request.json    a fixed request
  expected.json   the expected full CalculationResult
```

Every change to the engine must leave all golden cases passing. This suite is the only evidence that the calculator computes correctly, and it is what the team can present at handover.
