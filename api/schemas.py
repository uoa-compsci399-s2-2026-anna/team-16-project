"""Strict wire schemas for §6.2. Decimal inputs are JSON strings by contract.

Three of §6.2's rules are enforced by `entry_rule_problems` below rather than by
Pydantic. Two of them are here because of `details[].field` (§9): a
`model_validator` reports against the location of the *model*, so a
mass-conservation failure raised inside `EntryPayload` would arrive on the wire
as `entries[0]` — while §6.2 requires `entries[0].alternative`, and a duplicate
`(sector, food_category)` is a property of the whole array rather than of
either entry alone.

The third — no prevention destination in a *current* scenario — is here for a
different reason: **which destinations those are is data.** It was a comparison
against the literal `"prevention"`, so §10.3's `refed_prevention` was not
covered by it and could be entered as current-scenario waste and reach the
public statistics. The set of prevention codes now comes from
`destination.is_prevention` by way of `db.repository`, which a Pydantic
validator has no session to reach. It keeps the `entries[i].current` path the
`AfterValidator` gave it, written out by hand.
"""

from __future__ import annotations

from collections.abc import Collection
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

MAX_LINE_QTY = Decimal("10000000")
MAX_SCENARIO_QTY = Decimal("50000000")
MAX_SCENARIO_LINES = 20
MAX_ENTRIES = 20

#: §6.2. Absolute, and derived from this contract's own limits rather than
#: picked: `improvement.js` rounds each alternative line to 3 decimal places
#: independently (<= 0.0005 kg of error per line) and a scenario is capped at
#: `MAX_SCENARIO_LINES`, so 20 x 0.0005 = 0.010 kg bounds the drift whatever
#: the tonnage. A *relative* tolerance is wrong in both directions: at 5,000
#: tonnes 0.01% is 500 kg, looser than the defect the rule exists to catch,
#: and at 2 kg it is tighter than the rounding the front end unavoidably
#: produces. The worst case sits exactly on the boundary, so the comparison
#: that accepts is `<=` and the comparison that rejects is `>`.
MASS_TOLERANCE_KG = Decimal("0.010")

BUNDLE_TABLES = (
    "sectors",
    "food_categories",
    "destination_groups",
    "destinations",
    "metrics",
    "constants",
    "formulas",
    "upstream",
    "downstream",
    "equivalences",
)


def decimal_from_string(value: Any) -> Decimal:
    if not isinstance(value, str):
        raise ValueError("decimal values must be JSON strings")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("invalid decimal string") from exc
    if not parsed.is_finite():
        raise ValueError("decimal value must be finite")
    return parsed


class ScenarioLinePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destination: str = Field(min_length=1, max_length=64)
    qty_kg: Decimal

    @field_validator("qty_kg", mode="before")
    @classmethod
    def parse_qty(cls, value: Any) -> Decimal:
        return decimal_from_string(value)

    @field_validator("qty_kg")
    @classmethod
    def bound_qty(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("must be greater than or equal to zero")
        if value > MAX_LINE_QTY:
            raise ValueError("exceeds 10,000,000 kg")
        if value != value.quantize(Decimal("0.001")):
            raise ValueError("must have no more than 3 decimal places")
        return value


def scenario_mass(lines: list[ScenarioLinePayload]) -> Decimal:
    return sum((line.qty_kg for line in lines), Decimal("0"))


def _check_scenario(lines: list[ScenarioLinePayload]) -> list[ScenarioLinePayload]:
    """§6.2's per-scenario, per-entry rules that need no taxonomy.

    An `AfterValidator` on the field rather than a validator on the model, so
    that the failure is located at `entries[0].current` instead of at
    `entries[0]`.

    The prevention rule used to live here, keyed on one literal. It is in
    `entry_rule_problems` now: it needs the taxonomy, and a Pydantic validator
    has no session.
    """
    if len(lines) > MAX_SCENARIO_LINES:
        raise ValueError(f"must contain at most {MAX_SCENARIO_LINES} lines")
    destinations = [line.destination for line in lines]
    if len(destinations) != len(set(destinations)):
        raise ValueError("contains a duplicate destination")
    if scenario_mass(lines) > MAX_SCENARIO_QTY:
        raise ValueError("exceeds 50,000,000 kg")
    return lines


ScenarioPayload = Annotated[
    list[ScenarioLinePayload], AfterValidator(_check_scenario)
]
#: Kept as distinct names because §6.2's two scenarios are distinct concepts
#: and `EntryPayload` reads better for it; they are the same validator now
#: that the one asymmetric rule between them has moved.
CurrentScenarioPayload = ScenarioPayload
AlternativeScenarioPayload = ScenarioPayload


class DryRunPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    factor_set_version: str | None = Field(default=None, min_length=1, max_length=128)
    bundle: dict[str, Any] | None = None

    @model_validator(mode="after")
    def mutually_exclusive(self) -> "DryRunPayload":
        if self.factor_set_version is not None and self.bundle is not None:
            raise ValueError("factor_set_version and bundle are mutually exclusive")
        return self


class EntryPayload(BaseModel):
    """One `(sector, food_category)` pair and both of its scenarios (§6.2).

    Sector and food category sit on the entry, not on the scenario: an
    entry's two scenarios describe the same point in the supply chain, so a
    per-scenario sector would make an unrepresentable state representable.
    """

    model_config = ConfigDict(extra="forbid")
    sector: str = Field(min_length=1, max_length=64)
    food_category: str | None = Field(default=None, min_length=1, max_length=64)
    current: CurrentScenarioPayload = Field(min_length=1)
    alternative: AlternativeScenarioPayload | None = None


class CalculatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: §6.2: "Any value that does not resolve to a live submission is treated
    #: as absent and a new one is minted - a stale `sessionStorage` value must
    #: not produce an error". Typed `str`, therefore, and not `UUID4`: a token
    #: left over from an earlier deployment is not a request the user can fix,
    #: and `upsert_submission` already treats a lookup miss as absent.
    token: str | None = None
    gwp_horizon: int = 100
    entries: list[EntryPayload] = Field(min_length=1, max_length=MAX_ENTRIES)
    dry_run: DryRunPayload | None = None

    @field_validator("gwp_horizon")
    @classmethod
    def validate_horizon(cls, value: int) -> int:
        if value not in (20, 100):
            raise ValueError("must be 20 or 100")
        return value


def entry_rule_problems(
    payload: CalculatePayload,
    *,
    prevention_codes: Collection[str],
) -> list[dict[str, Any]]:
    """The three §6.2 rules Pydantic cannot express. See the module docstring.

    Returns one `details` entry per problem, in `entries` order, so a caller
    with two bad entries is told about both rather than about the first.

    `prevention_codes` is every destination flagged `is_prevention` (§2.1),
    read from the taxonomy by `db.repository.prevention_destination_codes`.
    **Required, and keyword-only**, for the reason v1.16 made `secret_key`
    required on `create_staff`: an argument that defaults to empty here
    enforces nothing and looks identical at the call site to one that was
    passed, so the caller that forgot it is the caller nobody notices. A
    caller that genuinely wants only the two entry-shape rules passes an empty
    collection and says so.
    """
    problems: list[dict[str, Any]] = []
    prevention = frozenset(prevention_codes)
    first_seen: dict[tuple[str, str | None], int] = {}
    for index, entry in enumerate(payload.entries):
        offsets = [
            line.destination for line in entry.current
            if line.destination in prevention
        ]
        if offsets:
            listed = ", ".join(f"'{code}'" for code in sorted(set(offsets)))
            problems.append(
                {
                    "field": f"entries[{index}].current",
                    "issue": "prevention_in_current",
                    "message": (
                        f"may not send waste to {listed}: a prevention "
                        "destination is where waste that did not happen goes, "
                        "and belongs in an alternative scenario only"
                    ),
                }
            )
        key = (entry.sector, entry.food_category)
        if key in first_seen:
            problems.append(
                {
                    "field": f"entries[{index}]",
                    "issue": "duplicate_entry",
                    "message": (
                        "has the same sector and food category as "
                        f"entries[{first_seen[key]}]"
                    ),
                }
            )
        else:
            first_seen[key] = index
        if entry.alternative is None:
            continue
        current = scenario_mass(entry.current)
        alternative = scenario_mass(entry.alternative)
        if abs(alternative - current) > MASS_TOLERANCE_KG:
            problems.append(
                {
                    "field": f"entries[{index}].alternative",
                    "issue": "mass_not_conserved",
                    "message": (
                        f"totals {alternative} kg against a current scenario of "
                        f"{current} kg; the two scenarios of an entry must "
                        f"describe the same mass to within {MASS_TOLERANCE_KG} kg. "
                        "Move mass to the prevention destination rather than "
                        "removing it"
                    ),
                }
            )
    return problems


def bundle_row_count(bundle: dict[str, Any]) -> int:
    total = 0
    for name in BUNDLE_TABLES:
        value = bundle.get(name, [])
        if not isinstance(value, list):
            raise ValueError(f"bundle.{name} must be an array")
        total += len(value)
    return total
