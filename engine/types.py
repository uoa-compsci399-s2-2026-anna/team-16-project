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
from enum import Enum

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
    #: v1.58. The named food *within* `food_category_code` — "cheese", not
    #: "dairy" — and the fifth slot of `FactorBundle.upstream`'s key.
    #:
    #: **`None` is not a placeholder and is never resolved to a stand-in**,
    #: the way `food_category_code=None` is resolved to `standard_mix`. There
    #: is no standard food, and inventing one would put a number against a
    #: food the visitor never named. `None` means "the category", which is
    #: what every request written before v1.58 means and what the lookup
    #: chain answers with the category average.
    #:
    #: Defaulted, so every caller that predates the slot — the golden
    #: suite's `request_from_json`, `tests/support`'s fake, any hand-built
    #: request — keeps producing exactly the request it produced before. That
    #: is what makes this dimension inert by data rather than by a flag.
    food_item_code: str | None = None
    #: v1.48. All three optional, all three carried rather than computed
    #: with here: `calculate` derives the money block from them (§4.5) and
    #: nothing else in the engine reads them.
    #:
    #: `total_input_kg` is what this stage put through in the period, so that
    #: waste can be stated as a share of production. The two money figures
    #: are statistics only - the client's ruling on O-2 - and are deliberately
    #: not a metric: the formula language is per LINE and takes
    #: `(qty_kg, upstream, downstream, const_*)`, which an entry-level figure
    #: a person typed cannot be expressed in.
    total_input_kg: Decimal | None = None
    total_value_nzd: Decimal | None = None
    wasted_value_nzd: Decimal | None = None


@dataclass(frozen=True)
class CalculationRequest:
    entries: tuple[EntryInput, ...]  # at least one; request order is preserved
    gwp_horizon: int = 100  # 20 or 100; applies to the whole request


# ---------- Output ----------


class UpstreamBasis(Enum):
    """Which of §2.2's four candidate rows answered an upstream lookup.

    Returned by `FactorBundle.upstream_with_basis()` beside the value. The
    fallback disclosure the interface will render -- *"this figure is the
    Fruit average, not Feijoas"* -- has to be able to **branch** on which row
    was used, and on nothing else: a human-readable string assembled here
    would have to be assembled in English, in the engine, which is neither
    where the copy lives nor where the twenty locale files are. So this is a
    value, and the sentence is the caller's.

    The member is derived from the *winning row's own shape* rather than from
    what the caller asked for, which is what keeps it truthful in the two
    degenerate cases: a lookup with `food_item=None` can only ever be answered
    by a category row, and a lookup with `destination=None` can only ever be
    answered by an every-destination row. See `upstream_with_basis()`.
    """

    #: This food, at this destination -- §2.2 candidate 1.
    ITEM_AT_DESTINATION = "item_at_destination"
    #: Every food in this category, here -- candidate 2. **Outranks candidate
    #: 3**, and the prevention zero is stored in this shape.
    CATEGORY_AT_DESTINATION = "category_at_destination"
    #: This food, at every destination -- candidate 3.
    ITEM_EVERY_DESTINATION = "item_every_destination"
    #: The category average, everywhere -- candidate 4, and the normal row.
    CATEGORY_EVERY_DESTINATION = "category_every_destination"
    #: No row at all: `Decimal('0')`, §4.1's documented fall-through.
    ABSENT = "absent"

    @property
    def is_item_level(self) -> bool:
        """Whether the figure was refined by the named food.

        This is the branch the disclosure needs: *false* while an item was
        asked for is exactly the case that has to say "the category average,
        not this food". It is a property of the basis rather than a fifth
        thing for a caller to work out from the member name, because "which
        members are item rows" is knowledge that belongs beside the chain.
        """
        return self in (
            UpstreamBasis.ITEM_AT_DESTINATION,
            UpstreamBasis.ITEM_EVERY_DESTINATION,
        )


class ItemBasis(Enum):
    """Whether an entry's figures were priced at the food it named. v1.59.

    One value per entry, rolled up from every `BreakdownRow.upstream_basis`
    in both of its scenarios. The per-row member is the truth and this is the
    sentence's handle: a results page, a plain-text export and a PDF all have
    to tell one story about one submission, and three surfaces each rolling
    the rows up in their own language is three chances to tell it differently.
    Rolled up in the engine, where the golden suite can pin it.

    **Only `CATEGORY` asks for copy.** `MIXED` is the ordinary state, not an
    alarm: the prevention offset is stored as a category-level,
    destination-specific row (§2.2 candidate 2, the shape that closes O-7),
    so *every* entry that moves mass to `prevention` has at least one
    category-priced line however well the set prices its food. A disclosure
    raised on `MIXED` would fire on a row that is deliberately category-level
    and teach a reader to ignore it. The member is still carried, because a
    surface that wants to be precise about one metric can read the rows.
    """

    #: Every lookup that could have used the named food did. Rarer than it
    #: sounds, for the prevention reason above.
    ITEM = "item"
    #: Some lookups used the food and some fell to its category.
    MIXED = "mixed"
    #: A food was named and **not one figure came from it** -- this is the
    #: disclosure: *"this is the Fruit average, not Feijoas"*.
    CATEGORY = "category"
    #: No food was named, so there is nothing to disclose. Every entry
    #: written before v1.58 is this, and every entry today.
    NOT_APPLICABLE = "not_applicable"

    @property
    def is_disclosed(self) -> bool:
        """Whether a surface must say something. See the class note: this is
        `CATEGORY` alone, and it is a property here rather than a comparison
        at three call sites so that the rule is stated once."""
        return self is ItemBasis.CATEGORY


@dataclass(frozen=True)
class BreakdownRow:
    destination_code: str
    qty_kg: Decimal
    upstream: Decimal  # per kg
    downstream: Decimal  # per kg, may be negative
    value: Decimal  # this line's contribution to the metric total
    #: v1.59. Which of §2.2's four candidate rows produced `upstream`
    #: above -- the disclosure's evidence, per line and per metric, because
    #: that is the granularity at which the answer actually varies: the
    #: destination outranks the item, so one entry's `prevention` line is
    #: category-priced while its `landfill` line is not.
    #:
    #: **`None` at the totals level, and required rather than defaulted.**
    #: `_roll_up` sums rows across entries, and two entries sharing a
    #: destination can have been priced from different rows -- the same
    #: objection that leaves `upstream` and `downstream` at `ZERO_RATE`
    #: there. `None` says *no single row answered this*, which is the honest
    #: value and not the same as any member. It is required so that a
    #: construction site that forgot it raises instead of quietly claiming
    #: the totals-level answer for an entry-level row.
    upstream_basis: UpstreamBasis | None


@dataclass(frozen=True)
class MetricResult:
    metric_code: str
    unit: str
    display_precision: int
    total: Decimal
    #: Populated per entry with each line's own `upstream`, `downstream` and
    #: `value`. Populated at the totals level too (v1.48, amending §3 rule
    #: 2): `qty_kg` and `value` are additive across entries -- a mass is a
    #: mass, and `value` is a summand of `total`, which this metric's own
    #: total is already computed by summing (§4.3) -- so the cross-entry
    #: partition cannot disagree with the total it partitions. `upstream`
    #: and `downstream` stay at zero there: they are per-kilogram RATES
    #: drawn from factors that can differ between the entries sharing a
    #: destination, and a mean of two different rates is a number derived
    #: from nothing. `MetricResult` is one type either way; the serialiser
    #: omits the key when the tuple is empty.
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
    #: The short label (`Kilometres driven`). `label_template` is a whole
    #: sentence, so a consumer building a heading has nothing else.
    name: str = ""
    #: The conversion factor, raw and then formatted. The pair mirrors
    #: `value`/`label` above: full precision on the wire, one server-side
    #: decision about what the reader sees (§7.6 rule 1).
    value_per_unit: Decimal = Decimal(0)
    value_per_unit_display: str = ""
    #: Basis for the conversion, verbatim. `None` where none is recorded --
    #: O-3 is open, and an absence the surfaces can name beats a blank.
    source_note: str | None = None


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
    #: v1.58. Echoed as sent, exactly as `food_category_code` above is: the
    #: engine resolves the code for the *lookup* and the result reports what
    #: the request carried, so §6.2's response can be paired with the row on
    #: the visitor's screen.
    food_item_code: str | None = None
    #: v1.59. Whether this entry's figures were priced at the food it named,
    #: rolled up from every `BreakdownRow.upstream_basis` in both scenarios.
    #: `ItemBasis` has the rule; the short version is that only `CATEGORY`
    #: asks a surface to say anything.
    #:
    #: **Defaulted, unlike `BreakdownRow.upstream_basis` beside it**, and the
    #: two are defaulted or not for the same reason rather than by accident.
    #: There is exactly one honest value for a caller that predates this
    #: field -- `NOT_APPLICABLE`, because such a caller cannot have named a
    #: food -- so the default is the answer rather than a stand-in for one.
    #: A `BreakdownRow` has no such value: every member is a claim about a
    #: figure that was produced, so there is nothing safe to assume.
    item_basis: ItemBasis = ItemBasis.NOT_APPLICABLE
    #: This entry's own current mass as a percentage of the `total_input_kg`
    #: it supplied, two places. `None` when this entry supplied no production
    #: total -- absent, never zero, because "0% of what this site handles"
    #: is a claim about the site and not an absence of data.
    #:
    #: **Permanent, and independent of the totals.** An entry that answered
    #: keeps its own share whether or not its neighbours did, so a visitor
    #: who filled the field in on one row still sees that row's figure even
    #: when the summary has to say the data is incomplete.
    #:
    #: **This is the one figure on the results page that open item O-1 does
    #: not touch.** Every other number there is computed from a mock factor
    #: set and carries the mandatory placeholder banner. This one is
    #: arithmetic on two masses the visitor typed -- a waste mass over a
    #: production mass -- and no factor, real or placeholder, enters it. A
    #: reader who distrusts it because of the banner is distrusting the wrong
    #: number.
    production_share_percent: Decimal | None = None


#: §4.5/§4.6's states, and the reason `Decimal | None` alone cannot carry
#: them. A totals-level figure here is a roll-up of a **per-entry optional
#: input**, and a submission may answer it on some entries and not others.
#: That leaves three different things to say about *coverage*, not two:
#:
#:   complete      every entry supplied the input; the figure is the figure.
#:   incomplete    some entries supplied it and some did not. The value is
#:                 withheld -- summing only the entries that answered yields
#:                 a real-looking figure whose denominator silently excludes
#:                 part of the submission, and averaging the per-entry
#:                 percentages weights a 10 kg entry equally with a 10 t one.
#:                 A stated gap is better than a number that is quietly wrong.
#:   not_supplied  no entry supplied it. Nobody answered the question.
#:
#: A fourth state (v1.51) answers a different question -- not "did everybody
#: answer" but "is the arithmetic defined once they did":
#:
#:   undefined     every entry answered, and the ratio has no defined value
#:                 because what they answered summed to zero -- a
#:                 submission whose every entry typed a production total of
#:                 zero, or a total value of zero. A stated answer, an
#:                 undefined question.
#:
#: `production_share_percent` and `wasted_share_percent` are the two figures
#: that can reach it -- both are `part / whole` over a whole every entry
#: supplied, and `engine/calculate.py::_share_state` is the one place that
#: decides it, so `complete` and `None` never pair on those two figures by
#: accident (before v1.51 they did: a zero production total read back as
#: "you did not say how much food this covered", which was false).
#:
#: `None` conflates all three of `incomplete`, `not_supplied` and
#: `undefined`, which is exactly the defect the results page shipped with:
#: one card said "Not available" whether the visitor had skipped the field,
#: filled it in on half their rows, or answered every row with a total of
#: zero.
DATA_COMPLETE = "complete"
DATA_INCOMPLETE = "incomplete"
DATA_NOT_SUPPLIED = "not_supplied"
DATA_UNDEFINED = "undefined"


@dataclass(frozen=True)
class DataState:
    """Which of the three states above each totals-level figure is in.

    **Why a state beside the value rather than a sentinel inside it.**
    Decimals travel as strings and every consumer runs `Number()` on them for
    display; a sentinel decimal is a number that can be plotted, summed and
    screenshotted. A boolean flag would only name one of the two absences and
    leave the reader to infer the other from a null. A named state per figure
    says which of three things happened, and the value stays `None` for both
    non-complete states -- so a caller that forgets to read the state renders
    a blank, never a wrong number. Both failure modes are honest.

    **One direction only.** A non-complete state (`incomplete`,
    `not_supplied`, `undefined`) always implies a `None` value. For
    `production_share_percent` and `wasted_share_percent`, `complete` now
    always implies a value too (v1.51) -- the one case that used to break
    that, a denominator every entry answered as zero, is named `undefined`
    instead of being left `complete` with nothing to show for it. `saving_nzd`
    is the one field this class carries where `complete` still does not fully
    guarantee a value; see the comment beside its computation in
    `engine/calculate.py::_money`.
    """

    production_share_percent: str = DATA_NOT_SUPPLIED
    total_value_nzd: str = DATA_NOT_SUPPLIED
    wasted_value_nzd: str = DATA_NOT_SUPPLIED
    wasted_share_percent: str = DATA_NOT_SUPPLIED
    saving_nzd: str = DATA_NOT_SUPPLIED


@dataclass(frozen=True)
class MoneyResult:
    """§4.5, v1.48. What the visitor's own money figures come to.

    **Not a metric, and that is a decision rather than an omission.** Metrics
    are rows in a table with a stored formula, and the formula language is
    per-line over `(qty_kg, upstream, downstream, const_*)`. These figures are
    entry-level numbers a person typed; expressing them as a metric would mean
    inventing a per-kilogram money factor, which is exactly the modelling the
    client's ruling on open item O-2 declined to do. O-2 closes on that
    ruling: the value of the food does not enter the main formula, and
    cost-price versus retail-price is the client's own client's question.

    Every field is optional because every input is. `None` means nobody
    supplied what it is derived from - never zero, which is a claim. Since
    §4.6 it can mean one further thing: that *some* entries supplied it and
    some did not, in which case the figure is withheld rather than summed
    over the entries that answered. `CalculationTotals.data_state` is what
    tells the two apart, and it is the only thing that can.
    """

    #: Summed across entries. A business reporting at three stages has three
    #: production values and one total.
    total_value_nzd: Decimal | None
    wasted_value_nzd: Decimal | None
    #: `wasted / total * 100`, two places. None when either side is absent.
    wasted_share_percent: Decimal | None
    #: **Uniform value per kilogram, which is the client's own assumption and
    #: is stated in v1.48 rather than left implicit.** Milk and mixed waste
    #: are not worth the same per kilogram, and this figure is only as good as
    #: that assumption. None without an alternative scenario, and None when
    #: no wasted value was supplied.
    saving_nzd: Decimal | None


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

    `money` (v1.48) is the one field here that is not derived from a metric
    or a formula -- see `MoneyResult`. It is `None` when no entry supplied
    either money figure.
    """

    current: ScenarioResult
    alternative: ScenarioResult | None
    net_benefit: dict[str, Decimal] | None  # key = metric_code
    money: MoneyResult | None
    #: The whole submission's current mass over the whole submission's
    #: production, two places -- **computed only when every entry supplied a
    #: production total**, and `None` otherwise. See `DataState` for the two
    #: different reasons it can be `None` and for why neither is zero. Like
    #: the per-entry figure above, O-1's mock factors cannot reach it.
    production_share_percent: Decimal | None = None
    #: Which of the three states each totals-level figure above is in.
    #: Always present, so the caller never has to infer a state from an
    #: absent object.
    data_state: DataState = DataState()


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
    name: str = ""
    source_note: str | None = None
