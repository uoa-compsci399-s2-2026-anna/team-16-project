---
title: "Kai Commitment Impact Calculator — Interface and Data Contract"
subtitle: "Single source of truth for five-way parallel development"
date: "2026-08-08 (v0.12 draft)"
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

### `ip_block`

Owned by B's layer (`db/`), built by E — see §8.3's "Blocklist" and the note
above. No foreign keys: a block is not owned by a staff row, and it must
survive the account of whoever made it being deleted.

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
> (§2.3).

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

> This section is v0.10 and its request body is flat (one `sector` /
> `food_category` / `current` per call). A multi-entry `entries` array is
> proposed as v1.1 on the unmerged `docs/contract-v1.0` branch (PR #10),
> fixing a one-POST-per-entry front end whose shared session token had each
> call's upsert overwrite the previous entry's row. Not yet reconciled into
> this file — the admin panel's dry-run client (`admin/calc_client.py`,
> `admin/dryrun_views.py`) already builds to v1.1.

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

> **Known reconciliation item for the `db/` merge — not an instruction that can be followed today.** §2.3 says auditing is the caller's job, and §5.5 says audit writing belongs to the repository layer. Neither is true of the code as built: `write_audit` lives in `admin/audit.py`, and `api/` may not import `admin/` (CLAUDE.md's layering rule, AST-pinned by `tests/db/test_blocklist.py`). So an API-side automatic block cannot write an audit entry at all as things stand, and "the caller audits" is executable for the admin panel and the CLI only. This is a consequence of E building the blocklist in B's layer while B's repository was on an unmerged branch, the same way the factor-set lifecycle was, and it is E's to declare rather than B's to discover.
>
> **Resolution when the two branches merge:** `write_audit` and `row_to_dict` move to `db/` (which is where §5.5 already says they belong), `admin/audit.py` becomes a re-export or is deleted, and only then does the "caller audits" instruction become executable from `api/`. Until that happens, an API-side block writes no audit entry and `/admin/audit` will not show it. **Owner of the decision: B**, as owner of `db/` and the repository layer; E's part is done and the note above is the handover.
>
> **The same unresolved split applies to three more names.** `admin.detection.looks_automated`, `admin.detection.RequestRate` and `admin.protection._client_ip` are all in `admin/` and are all things B's public-traffic middleware needs. They were built there because `admin/` is where E's stage lived, not because that is where they belong: `detection.py` imports nothing outside the standard library and `_client_ip` imports nothing outside Starlette, so neither has any reason to sit above the layering boundary. **Recommendation:** move `detection.py` to `db/` or to a new shared module and leave `admin/protection.py` importing it, rather than have `api/` duplicate the header-marker list and the sliding-window counter — two copies of a detection rule drift, and the copy that stops matching is the one nobody notices. `_client_ip` should move with it. **This is genuinely unresolved, and B decides it**, since a move changes a file in her layer; E's recommendation is on record here so that the alternative (duplication) is a choice someone made rather than the default nobody discussed. Note also that `RequestRate` is per-process, so under more than one worker the effective limit is multiplied by the worker count — the API's own rate limiting (§6.5) is a separate problem and E has not solved it.

**The one case this whole design is built around not causing:** an administrator blocks the address they are sitting behind, and the block itself now stands between them and every page that would let them undo it — including the login page, because the blocklist check has no exemption. `python -m admin.cli unblock <address>` (above) is the only way back short of editing the database by hand, and is the reason that command exists at all.

`ProtectionMiddleware` also carries a stateless header check and a per-address rate limit (`PROTECTION_MAX_REQUESTS_PER_MINUTE`), both configurable and both able to be turned off in one place: `PROTECTION_ENABLED=false` disables the blocklist, the header check and the rate limit together, with no finer-grained switch — the documented escape hatch for a false-positive lockout that is not a blocklist entry. **It needs no code change, but it does need a process restart**: settings are read once, by `load_settings()` at start-up. See `docs/architecture.md` §9.1.1 for the operational detail, the `PROTECTION_TRUSTED_PROXY` warning, the three recovery paths, and this design's explicit limits.

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
    "details": [ { "field": "current[0].qty_kg", "issue": "exceeds_max" } ]
  }
}
```

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
