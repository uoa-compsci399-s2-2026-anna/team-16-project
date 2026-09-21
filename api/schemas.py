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
from datetime import datetime, timedelta, timezone
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
from pydantic_core import PydanticCustomError

#: §6.2's amount ceilings, and **they are deliberately the same number.**
#:
#: Both arrived in one commit as two rows of §6.2's table with no rationale
#: recorded for either, and nothing downstream requires either of them: the
#: column they land in is `DECIMAL(16,3)` (`submission_line.qty_kg`), five
#: orders of magnitude wider; no metric total is persisted at all; the
#: evaluator has no magnitude cap; and §5.4's suppression keys on `count`,
#: never on tonnage. They are plausibility guards, which is a legitimate thing
#: to be - but a plausibility guard has to be plausible about the right thing.
#:
#: **The ratio between them was the defect.** A per-line cap at a fifth of the
#: scenario cap makes "at least five destinations" a precondition of reaching
#: the scenario ceiling, and nothing in the model asks a scenario to be
#: divided. A site that only landfills, or sends everything to anaerobic
#: digestion, could not describe itself at any tonnage above 10,000 t - while
#: the identical mass split five ways was accepted. Reported by a user who
#: entered 50,000 t at step 3 and sent all of it to animal feed at step 4: an
#: ordinary, truthful answer that no combination of legal values could express.
#:
#: **Equal, rather than the line cap merely raised.** One line carrying a whole
#: scenario is the case this exists for, so the largest legal line *is* the
#: largest legal scenario, and writing that as one name means the ratio cannot
#: silently reappear. `MAX_SCENARIO_QTY` is not lowered to meet it: 50,000 t is
#: an unremarkable annual figure for a large processor.
#:
#: This raises no total. `MAX_SCENARIO_QTY` and `MAX_ENTRIES` already bound a
#: request at 20 x 50,000,000 = 1e9 kg and still do; only the distribution
#: changes. Note that `MAX_SCENARIO_LINES` x this cap has never equalled
#: `MAX_SCENARIO_QTY` and does not now - the line count is a request-size
#: bound, not a mass bound, and the scenario cap is what settles the mass.
MAX_SCENARIO_QTY = Decimal("50000000")
MAX_LINE_QTY = MAX_SCENARIO_QTY
MAX_SCENARIO_LINES = 20
MAX_ENTRIES = 20


def _kg(limit: Decimal) -> str:
    """A ceiling as §9's messages state it: `50,000,000 kg`.

    Formatted from the constant rather than written out beside it. The message
    the user saw when the ratio was wrong was itself accurate - "no more than
    10,000 tonnes" was true of the rule as it stood - so nothing about the
    wording gave the defect away. A hand-written figure adds a second way for
    the same sentence to be wrong, one that a reader *can* catch, and there is
    no reason to carry it.
    """
    return f"{int(limit):,} kg"


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
            raise ValueError(f"exceeds {_kg(MAX_LINE_QTY)}")
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
        raise ValueError(f"exceeds {_kg(MAX_SCENARIO_QTY)}")
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


#: §6.2, v1.48. A closed vocabulary for the same reason `gwp_horizon` is
#: closed to 20 and 100: the results page renders a phrase per value, and a
#: value it has no phrase for reaches a visitor as a raw identifier.
#:
#: Nothing computes with the period. It is carried to the results page and
#: into the download and no figure is scaled by it; the engine is not given it
#: at all. That ruling is unchanged by `custom` below - an interval on the
#: request is still a label, and `(period_end - period_start)` is still
#: arithmetic this contract forbids anyone to do with it.
#:
#: **`custom` is a fifth member and not a replacement for the other four
#: (v1.67).** From v1.67 the four presets are *templates*: pressing *One week*
#: fills the interval with the seven days ending now, and `time_frame` goes on
#: recording **which shortcut was pressed**. Collapsing every new row to
#: `custom` was considered and rejected twice over: the rows already in the
#: database say `one_week` with no interval, so the four preset values would
#: become a dialect only historical rows speak and the column would stop being
#: comparable across the table; and an interval alone cannot answer the
#: question the client will ask first - *did they mean a standard week, or did
#: they choose those dates?*
TIME_FRAMES = frozenset({"one_week", "one_month", "one_quarter", "one_year", "custom"})

#: v1.67. The floor under `period_start` and `period_end` (§6.2). The only one
#: worth having: a period before the epoch is a typo, not a reporting period.
PERIOD_FLOOR = datetime(1970, 1, 1)

#: v1.67. How far past **this server's own UTC clock** either period instant
#: may sit, and **38 is arithmetic rather than slack.** Read the whole of this
#: before changing it.
#:
#: The stored instants are the visitor's **local wall-clock time and they
#: carry no zone** (§2.3). So the real rule - *a reporting period may not end
#: more than 24 hours from now* - lives in the form, where "now" is the
#: visitor's own now and the two clocks being compared are the same clock.
#:
#: Here they are not. Handed `2026-09-22T17:55` with no offset, this server
#: cannot tell which side of the date line it was typed on. A visitor in New
#: Zealand sits at UTC+12 or UTC+13, and the widest civil offset in use
#: anywhere is UTC+14 (Kiritimati). Their honest "now + 24 hours", written
#: down with no zone, therefore reads as much as
#:
#:     24 (what the form allows) + 14 (the widest offset east of UTC) = 38
#:
#: hours ahead of this server's UTC clock.
#:
#: **Do not tighten this to 24.** It is not a fudge, a rounding or a margin
#: for clock drift, and tightening it does not make the check stricter in any
#: useful sense - it makes the API refuse a shift that somebody in Auckland
#: entered correctly, at the time it actually happened, with nothing in the
#: payload they could change to make it pass. A loose server bound is the
#: price of a zoneless value, and it is a price this field can afford
#: precisely because the value is a label that enters no calculation.
#:
#: The exact alternative - carrying a UTC offset on the wire - was considered
#: and rejected in planning: it would turn the stored value from a label into
#: a real instant, which is a larger decision than this field needs and is
#: `architecture.md` O-4's neighbour.
PERIOD_CEILING_HOURS = 38


class EntryPayload(BaseModel):
    """One `(sector, food_category, food_item)` triple and both of its
    scenarios (§6.2).

    Sector, food category and food item sit on the entry, not on the scenario:
    an entry's two scenarios describe the same point in the supply chain, so a
    per-scenario sector would make an unrepresentable state representable.
    """

    model_config = ConfigDict(extra="forbid")
    sector: str = Field(min_length=1, max_length=64)
    food_category: str | None = Field(default=None, min_length=1, max_length=64)
    #: v1.58. The named food *within* `food_category` -- "cheese", not
    #: "dairy". **Absent and `null` mean the same thing**, and that thing is
    #: "the visitor named a category and no food", which is every request that
    #: existed before this revision. Unlike `food_category`, `null` is *not*
    #: resolved to a stand-in: there is no standard food, and inventing one
    #: would put a number against a food nobody named.
    #:
    #: The one state this field may not be in is stated in
    #: `entry_rule_problems` rather than here: a food with no category is
    #: refused there, with a field, because a `model_validator` on this class
    #: reports `entries[i]` and not `entries[i].food_item` and a front end
    #: cannot bind that to the control the visitor used.
    food_item: str | None = Field(default=None, min_length=1, max_length=64)
    #: v1.48. Optional, and `None` is not zero: zero claims this stage put
    #: nothing through, which would make the waste share infinite rather than
    #: absent. Three decimal places to match `qty_kg` - a production total is
    #: compared against a waste mass and two scales for one comparison is how
    #: a thousandfold error gets in.
    total_input_kg: Decimal | None = Field(default=None, ge=0, decimal_places=3,
                                           max_digits=16)
    #: v1.48, statistics only. The client's ruling on O-2: the value of the
    #: food does not enter the main formula, and cost versus retail is the
    #: client's own client's question. Neither is a `metric`, and neither
    #: reaches a formula.
    total_value_nzd: Decimal | None = Field(default=None, ge=0, decimal_places=2,
                                            max_digits=14)
    wasted_value_nzd: Decimal | None = Field(default=None, ge=0, decimal_places=2,
                                             max_digits=14)
    current: CurrentScenarioPayload = Field(min_length=1)
    alternative: AlternativeScenarioPayload | None = None


class PricingOptions(BaseModel):
    """The request options that decide **how** a calculation is priced and
    **what period it is labelled with**, and every check over them.

    `gwp_horizon` is the only one of the four that reaches the engine.
    `time_frame`, `period_start` and `period_end` are labels: they are
    persisted, printed and never computed with (§2.3).

    **Why this class exists.** `CalculatePayload` and `api/export.py`'s
    `ExportPayload` both carry `gwp_horizon` and `time_frame` and both check
    them the same way. `ExportPayload` is deliberately *not* a subclass of
    `CalculatePayload` -- that model also carries `token` and `dry_run`, and
    neither means anything to a route that persists nothing and always prices
    the published set -- so until now the two checks were written out twice.

    Duplicated validators drift, and the whole justification for the export
    endpoint is that its figures are the server's rather than the client's: a
    horizon accepted on one route and refused on the other would mean two
    documents of the same request disagreeing about methane, with nothing to
    say which was right. Sharing them here is the narrowest fix that cannot
    drift -- it carries the fields the two models genuinely have in common,
    and nothing else. v1.67's two period columns joined them here for the
    same reason: a download and the calculation it documents must not
    disagree about which period the figures cover either.

    `extra="forbid"` is set here and inherited, so neither payload can be
    handed a field it does not declare -- which is what stops a client
    smuggling a precomputed figure into the export.
    """

    model_config = ConfigDict(extra="forbid")

    gwp_horizon: int = 100
    time_frame: str | None = None
    #: v1.67. The interval the period covers, to the minute - a shift, 08:10
    #: to 16:20. **Local wall-clock time, with no zone**, which is adequate
    #: for a label and inadequate for comparing one submission against
    #: another; `submission.period_start` in §2.3 says so where the column is
    #: defined, and `db/repository.upsert_submission` says it again where the
    #: value is written.
    #:
    #: A zone-carrying value is refused rather than converted: silently
    #: normalising `…T08:10+13:00` to UTC would store an instant two hours
    #: before the one the visitor typed and print it on their own download.
    period_start: datetime | None = None
    period_end: datetime | None = None

    @field_validator("gwp_horizon")
    @classmethod
    def validate_horizon(cls, value: int) -> int:
        if value not in (20, 100):
            raise ValueError("must be 20 or 100")
        return value

    @field_validator("time_frame")
    @classmethod
    def validate_time_frame(cls, value: str | None) -> str | None:
        if value is not None and value not in TIME_FRAMES:
            raise ValueError(f"must be one of {sorted(TIME_FRAMES)}")
        return value

    @field_validator("period_start", "period_end")
    @classmethod
    def validate_period_instant(cls, value: datetime | None) -> datetime | None:
        """The two bounds each instant answers on its own, and the one
        normalisation.

        Raised as `PydanticCustomError` rather than `ValueError` so that
        `details[].issue` carries a code a front end can branch on (§9); a
        plain `ValueError` arrives as `value_error` for every cause alike.
        These three land on `period_start` / `period_end` by name, because a
        `field_validator` reports against the field. The cross-field rules
        below cannot, and say so there.
        """
        if value is None:
            return None
        if value.tzinfo is not None:
            raise PydanticCustomError(
                "period_carries_a_zone",
                "must be a local wall-clock time with no timezone offset",
            )
        #: The column is `DATETIME` with no fractional-seconds precision
        #: (§2.3), so a value carrying microseconds would be a value that
        #: changes when it is stored - and the download prints what was
        #: stored. Dropped here, where the caller can be told it happened,
        #: rather than in MySQL, where nobody is.
        value = value.replace(microsecond=0)
        if value < PERIOD_FLOOR:
            raise PydanticCustomError(
                "period_before_1970",
                "must not be earlier than {floor}",
                {"floor": PERIOD_FLOOR.isoformat()},
            )
        ceiling = (
            datetime.now(timezone.utc).replace(tzinfo=None)
            + timedelta(hours=PERIOD_CEILING_HOURS)
        )
        if value > ceiling:
            raise PydanticCustomError(
                "period_too_far_ahead",
                #: The message says the number out loud for the same reason
                #: `PERIOD_CEILING_HOURS` explains itself: a reader who meets
                #: this text first must not read 38 as a typo for 24.
                "must not be more than {hours} hours past the server's UTC clock "
                "({hours} = the form's 24-hour allowance plus the widest civil "
                "UTC offset, because the value carries no zone)",
                {"hours": PERIOD_CEILING_HOURS},
            )
        return value

    @model_validator(mode="after")
    def validate_period(self) -> "PricingOptions":
        """The contradiction rule (v1.67), and it **refuses** rather than
        normalising.

        Four states, three of them refused:

        1. one instant without the other - half an interval is not a period;
        2. `time_frame == "custom"` with no interval - `custom` *means* "the
           visitor chose these dates", and there are none;
        3. an interval with no `time_frame` at all - "Not stated" is the
           default answer to §7's step 5 and it cannot carry dates;
        4. an interval beside one of the four presets - **allowed, and the
           designed normal case.** From v1.67 a preset is a button that fills
           the picker, so `one_week` plus the seven days ending now is what
           pressing *One week* produces.

        Plus the ordering rule: `period_end >= period_start`. Equal ends are
        allowed - a zero-length period is odd, but it enters no calculation,
        so refusing it buys exactly what refusing a ten-year span would buy,
        which is nothing.

        **Refusing rather than normalising, because every normalisation on
        offer invents an answer.** Turning state 3 into `custom` claims the
        visitor pressed nothing when a client bug dropping the field looks
        identical; dropping the interval in state 2 or 3 throws away the only
        record of the dates; and state 2 cannot be normalised at all, because
        there is nothing to fill it with. A refusal is the one outcome that
        puts no fact in the database that nobody stated.

        **The same rule is a CHECK constraint on `submission`**
        (`ck_submission_period`, §2.3), so it also holds against a write that
        does not come through this API. The one bound that is *not* in the
        CHECK is `PERIOD_CEILING_HOURS`, which moves with the clock and is
        therefore not something a CHECK can express; §2.3 records that.

        `details[].field` is `body` for all four, not `period_start` or
        `period_end`: a `model_validator` reports against the location of the
        model, which is this module's docstring's own standing complaint.
        `issue` is what a front end binds to instead, and each of the four
        carries its own.
        """
        start, end = self.period_start, self.period_end
        if (start is None) != (end is None):
            raise PydanticCustomError(
                "period_half_interval",
                "period_start and period_end must be given together",
            )
        if self.time_frame == "custom" and start is None:
            raise PydanticCustomError(
                "period_custom_without_interval",
                'time_frame "custom" requires period_start and period_end',
            )
        if start is not None and self.time_frame is None:
            raise PydanticCustomError(
                "period_without_time_frame",
                "period_start and period_end require a time_frame "
                '("custom", or the preset the visitor pressed)',
            )
        if start is not None and end < start:
            raise PydanticCustomError(
                "period_ends_before_it_starts",
                "period_end must not be earlier than period_start",
            )
        return self


class CalculatePayload(PricingOptions):
    #: §6.2: "Any value that does not resolve to a live submission is treated
    #: as absent and a new one is minted - a stale `sessionStorage` value must
    #: not produce an error". Typed `str`, therefore, and not `UUID4`: a token
    #: left over from an earlier deployment is not a request the user can fix,
    #: and `upsert_submission` already treats a lookup miss as absent.
    token: str | None = None
    entries: list[EntryPayload] = Field(min_length=1, max_length=MAX_ENTRIES)
    dry_run: DryRunPayload | None = None


class ContributePayload(BaseModel):
    """`POST /contribute` (§5.3, v1.48). The visitor's own opt-in.

    `token` is required here, unlike on `CalculatePayload`: there is no
    submission to create on this route, only an existing one to find, so an
    absent token has nothing to resolve. It is typed `str` and not `UUID4`
    for the same reason as `CalculatePayload.token` -- a stale
    `sessionStorage` value is not malformed input, it is a lookup that misses,
    and `set_public_contribution` already treats a miss as silent rather than
    an error.
    """

    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1)


def entry_rule_problems(
    payload: CalculatePayload,
    *,
    prevention_codes: Collection[str],
) -> list[dict[str, Any]]:
    """The four §6.2 rules Pydantic cannot express. See the module docstring.

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
    #: v1.58: a **triple**, and this is the one rule in this function that
    #: changes an existing answer rather than adding to it. Two entries naming
    #: `dairy/cheese` and `dairy/butter` are what a forked chain produces, and
    #: the pair was refused here while `uq_submission_entry` -- four columns
    #: since v1.54 -- accepted it.
    #:
    #: **The NULLs collapse, and they must.** MySQL treats NULLs as distinct
    #: inside a UNIQUE key, so `uq_submission_entry` is silent about the two
    #: states that matter most: a category with no food, twice; and a food
    #: with no category at all. `uq_submission_entry_generic` is what actually
    #: enforces them, as a functional index over `COALESCE(food_category_id,
    #: 0)` and `COALESCE(food_item_id, 0)` -- so the rule here must agree with
    #: that index and not with the constraint that does not enforce it. A
    #: Python tuple carrying `None` collapses exactly as `COALESCE(..., 0)`
    #: does, which is why this is a plain tuple and not a hand-written
    #: normalisation: `(processing, dairy, None)` twice collides, and
    #: `(processing, dairy, None)` beside `(processing, dairy, cheese)` does
    #: not. The second pair is **accepted**, matching the index, and it is the
    #: right answer on its own terms: §5.4 gives a NULL food category its
    #: own bucket meaning *the visitor did not break this down by type*, so
    #: "300 kg of dairy I did not itemise" and "40 kg of cheese I did" are two
    #: answers about two masses rather than one answer sent twice.
    first_seen: dict[tuple[str, str | None, str | None], int] = {}
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
        #: v1.58, and refused here rather than by Pydantic so that the
        #: `details` entry names `entries[i].food_item`. It is the one state
        #: `ck_submission_entry_item_has_category` refuses at the database:
        #: `food_category_id IS NULL` already means *did not break it down by
        #: type*, and §5.4 forbids conflating that with the most specific
        #: answer the calculator takes. Left to the schema it would arrive as
        #: an IntegrityError on the write, which is a 500 a visitor cannot act
        #: on -- the same reason the duplicate rule lives here.
        if entry.food_item is not None and entry.food_category is None:
            problems.append(
                {
                    "field": f"entries[{index}].food_item",
                    "issue": "item_without_category",
                    "message": (
                        f"names the food {entry.food_item!r} but no "
                        "food_category; a request that names a food must name "
                        "the category it belongs to"
                    ),
                }
            )
        key = (entry.sector, entry.food_category, entry.food_item)
        if key in first_seen:
            problems.append(
                {
                    "field": f"entries[{index}]",
                    "issue": "duplicate_entry",
                    "message": (
                        "has the same sector, food category and food as "
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
