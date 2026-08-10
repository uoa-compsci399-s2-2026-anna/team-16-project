"""Contract §3 -- the engine's domain objects. Frozen dataclasses only.

**The engine does not depend on SQLAlchemy**; `db/repository.py` converts ORM
rows into these types and `api/engine_adapter.py` converts them onto §6.2's
wire body. Nothing here carries an `id`: `code` is the cross-layer identifier
and the front end never learns a primary key (§1.1).

These types are multi-entry, and have been since contract v1.0. `EntryInput`
carries one `(sector, food_category)` pair and *both* of its scenarios;
`CalculationResult` carries `totals` plus one `EntryResult` per `EntryInput`,
in request order.

`FactorBundle` is **not** here -- §4.1 puts it in `engine/bundle.py`, and both
callers outside `engine/` import it from there.
"""

from dataclasses import dataclass
from decimal import Decimal

# ---------- Input ----------


@dataclass(frozen=True)
class ScenarioLine:
    """One destination and the mass sent to it."""

    destination_code: str
    qty_kg: Decimal  # >= 0


@dataclass(frozen=True)
class EntryInput:
    """One (sector, food_category) pair and both of its scenarios.

    Sector and food category sit here rather than on each scenario because
    §6.2 puts them on the entry: an entry's `current` and `alternative`
    describe the same point in the supply chain, and a wire request cannot
    express two different sectors for one entry. Putting them on the
    scenario would make an unrepresentable state representable.

    `food_category_code is None` means "use `standard_mix`" (§6.2); it is the
    engine that resolves it, through `FactorBundle.standard_mix_code()`.
    """

    sector_code: str
    food_category_code: str | None  # None -> use standard_mix
    current: tuple[ScenarioLine, ...]
    alternative: tuple[ScenarioLine, ...] | None


@dataclass(frozen=True)
class CalculationRequest:
    entries: tuple[EntryInput, ...]  # at least one; request order is preserved
    gwp_horizon: int = 100  # 20 or 100; applies to the whole request


# ---------- Output ----------


@dataclass(frozen=True)
class BreakdownRow:
    destination_code: str
    qty_kg: Decimal
    upstream: Decimal  # per kg
    downstream: Decimal  # per kg, may be negative
    value: Decimal  # this line's contribution to the metric total


@dataclass(frozen=True)
class MetricResult:
    metric_code: str
    unit: str
    display_precision: int
    total: Decimal
    #: Populated per entry, **empty at the totals level** (§3 rule 2): the
    #: same destination can appear under several entries drawing different
    #: upstream factors, so a cross-entry destination breakdown has no single
    #: correct aggregation rule. `MetricResult` is one type either way; the
    #: serialiser omits the key when the tuple is empty.
    by_destination: tuple[BreakdownRow, ...]


@dataclass(frozen=True)
class EquivalenceResult:
    code: str
    #: `equivalence.label_template` with `{value}` already interpolated, per
    #: §3's interpolation rule: whole number, `ROUND_HALF_UP` on the
    #: `Decimal` (never through `float`), comma thousands separator,
    #: everything else in the template copied verbatim. `value` below is the
    #: unrounded number and is transmitted at full precision beside it.
    label: str
    value: Decimal
    source_metric_code: str


@dataclass(frozen=True)
class ScenarioResult:
    total_kg: Decimal
    metrics: dict[str, MetricResult]  # key = metric_code
    equivalences: tuple[EquivalenceResult, ...]


@dataclass(frozen=True)
class EntryResult:
    """One `EntryInput`'s result. Position in `CalculationResult.entries`
    matches the request (§3 rule 1), which is what lets C pair a result with
    the row the user typed and what `submission_entry.sort_order` persists."""

    sector_code: str
    food_category_code: str | None
    current: ScenarioResult
    alternative: ScenarioResult | None
    net_benefit: dict[str, Decimal] | None  # key = metric_code


@dataclass(frozen=True)
class CalculationTotals:
    """The cross-entry roll-up. Computed by the engine, never by a caller.

    §4.2 rules on this and it is not open: an adapter that summed per-entry
    `MetricResult.total` values would be a second calculation site, and the
    headline figure a user reads would have no golden case behind it.

    An entry with no alternative contributes its **current** figures to
    `alternative` (§3 rule 3), so its contribution to `net_benefit` is
    exactly zero and the two sides stay mass-conserving. When *no* entry
    carries an alternative, `alternative` and `net_benefit` are both `None`
    (§3 rule 4).

    There is no `total_kg` field. §6.2's `totals.total_kg` is a wire-format
    hoist of `current.total_kg`, performed by `api/engine_adapter.py`.
    """

    current: ScenarioResult
    alternative: ScenarioResult | None
    net_benefit: dict[str, Decimal] | None  # key = metric_code


@dataclass(frozen=True)
class CalculationResult:
    factor_set_version: str
    is_mock: bool
    gwp_horizon: int
    totals: CalculationTotals
    entries: tuple[EntryResult, ...]  # request order, one per EntryInput


# ---------- Bundle specs ----------
#
# §3 lists neither of these and §4.1 references both without saying where
# they live; they are here because `FactorBundle` (`engine/bundle.py`) is
# built from them and nothing outside `engine/` needs them.
#
# `MetricSpec` is deliberately narrower than `db/types.py::MetricSpec`, which
# carries `name`, `display_unit` and `sort_order` as well because §2.1's
# taxonomy endpoint needs the whole row. These are the three fields a
# calculation reads. Ordering is a property of `FactorBundle.metrics` -- §4.1
# requires that tuple to arrive sorted by `sort_order` -- not a field here,
# so the engine iterates it as given and never re-sorts.


@dataclass(frozen=True)
class MetricSpec:
    code: str
    unit: str
    display_precision: int


@dataclass(frozen=True)
class EquivalenceSpec:
    code: str
    source_metric_code: str
    value_per_unit: Decimal
    label_template: str
