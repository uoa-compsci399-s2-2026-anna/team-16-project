"""Contract §4.1 -- `FactorBundle`, and §10.2's `bundle.json` reader.

A fully loaded snapshot of one factor set, read-only to the engine. §4.1 names
this module explicitly, and both callers outside `engine/` import it the
documented way: `db/repository.py`'s default bundle factory and
`api/engine_adapter.py`'s `bundle_from_json`. The golden suite (§10.1) is the
third consumer and loads a `bundle.json` per case.

**Pure.** `from_json()` accepts an already-parsed object, or the JSON text --
it never opens a file, touches a database, reads a clock or looks at the
environment. That is what lets the golden suite be meaningful: a case is three
files handed to the engine, and nothing else can influence the answer.

**Decimals arrive as strings** (§1.2, §10.2) and are converted with
`Decimal()`. A JSON number is refused rather than coerced: `float` is never an
intermediate, because `0.1 + 0.2` is the one defect no test downstream of here
would attribute to the loader.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from engine.errors import BundleFormatError, UnknownCodeError, UnknownConstantError
from engine.types import EquivalenceSpec, MetricSpec

#: §4.1. Returned by `formula()` for a metric with no row of its own, and
#: byte-for-byte the string the contract prints -- `admin/expressions.py` and
#: §4.3's default table both spell it this way.
DEFAULT_FORMULA = "qty_kg * (upstream + downstream)"

#: §10.2's twelve top-level keys. A bundle is a *complete, self-contained
#: snapshot*: `has_destination()`, `has_sector()`, `has_food_category()` and
#: `standard_mix_code()` are unimplementable without the taxonomy half, so a
#: bundle missing it is malformed rather than empty. Refusing it here is what
#: turns "pasted a `GET /factors` response into the dry-run box" -- which
#: carries the factors and none of the taxonomy -- into one sentence naming
#: the missing sections, instead of a calculation that rejects every
#: destination in the request with `UNKNOWN_CODE`.
REQUIRED_KEYS = (
    "version_label",
    "is_mock",
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

#: §10.2's thirteenth top-level key, and the one that is **optional**.
#:
#: `food_items` is v1.54's vocabulary section and is deliberately *not* in
#: `REQUIRED_KEYS` above. Every bundle written before v1.54 omits it -- the
#: thirteen golden cases, `db/repository.build_bundle_data`'s output, and the
#: `GET /factors` response a staff member pastes into the dry-run box -- and
#: all of them must still load unchanged. That is what makes this revision
#: inert: a bundle with no item vocabulary answers every lookup exactly as it
#: did before the dimension existed.
#:
#: Named as a constant rather than spelled inline so that
#: `tests/test_bundle.py` can assert the *absence* from `REQUIRED_KEYS` by
#: name; a test that spelled the string itself would keep passing if the
#: section were ever renamed.
FOOD_ITEMS_KEY = "food_items"


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


@dataclass
class FactorBundle:
    #: §4.1, and §2.2 since v1.54: keyed on
    #: `(sector, food_category, food_item | None, destination | None, metric)`
    #: -- the item inserted after the category it refines, mirroring
    #: `factor_upstream`'s own column order.
    #:
    #: **Both middle slots are nullable and `food_category` is not.** An item
    #: row carries *both* its category and its item, exactly as
    #: `factor_upstream.food_category_id` stays NOT NULL beside the nullable
    #: `food_item_id`. `food_item=None` means "every food in this category"
    #: and is the normal row; `destination=None` means "every destination" and
    #: is likewise the usual value. The non-null destination rows are what make
    #: `prevention` a real 100% offset (open item O-7).
    upstream_factors: dict[tuple[str, str, str | None, str | None, str], Decimal]
    #: Keyed on `(destination, sector | None, food_category | None, metric)`,
    #: and §2.2 since v1.31. **Both** middle slots are nullable and `None`
    #: means "every value of that dimension for this destination" -- a null
    #: food category is how a per-tonne charge such as the waste levy is
    #: expressed, and a null sector is the normal case for a set whose
    #: disposal routes cost the same wherever in the supply chain the waste
    #: arose. Values may be negative.
    downstream_factors: dict[tuple[str, str | None, str | None, str], Decimal]
    constants: dict[str, Decimal]
    formulas: dict[str, str]
    destinations: set[str]
    sectors: set[str]
    food_categories: set[str]
    standard_mix: str
    version_label: str
    is_mock: bool
    metrics: tuple[MetricSpec, ...]
    equivalence_specs: tuple[EquivalenceSpec, ...]

    # The fields below carry what `validate()` needs and a calculation does
    # not. They are defaulted so that the twelve positional fields above stay
    # the constructor a hand-built bundle uses.

    #: `destination.group` per §10.2, kept as a side table rather than by
    #: turning `destinations` into a mapping -- alembic 0009 records that
    #: keeping this a `set[str]` was part of the shape O-7 was closed with.
    destination_group_of: dict[str, str] = field(default_factory=dict)
    destination_groups: frozenset[str] = frozenset()
    #: Every food category flagged `is_standard_mix`, so that `validate()` can
    #: report *two* as readily as *none*. `standard_mix` above is the single
    #: resolved code and cannot express either.
    standard_mix_codes: tuple[str, ...] = ()
    #: Rows whose key collided with an earlier row of the same section. The
    #: dictionaries above have already dropped the loser; this is the only
    #: record that it existed. See `validate()`.
    duplicate_rows: tuple[str, ...] = ()
    #: Every destination flagged `is_prevention` -- the DB column of the same
    #: name (§2.1, contract v1.22). Optional in `bundle.json`, on the same
    #: terms as `food_categories[].is_standard_mix`: a `destinations[]` row
    #: that omits the key defaults to `False`, so no existing bundle needs
    #: rewriting. `engine/calculate.py`'s money block (§4.5) reads
    #: `is_prevention_destination()` rather than testing a literal
    #: `"prevention"` string -- the ReFED vocabulary's own prevention row is
    #: spelled `refed_prevention`, and a literal missed it once already.
    prevention_destination_codes: frozenset[str] = frozenset()
    #: v1.54's item vocabulary: each `food_item` code mapped to the
    #: `food_category` code it belongs to. Built from §10.2's **optional**
    #: `food_items` section, so a bundle written before v1.54 carries an empty
    #: mapping and every lookup in it falls to the category rows it always
    #: used -- the whole of why this landing is inert by data.
    #:
    #: A side table for the same reason `destination_group_of` is one: the
    #: parent is needed to *refuse* an incoherent pair (§6), and turning
    #: `food_categories` into a mapping would change a shape v1.8 settled.
    #: `code` is the cross-layer identifier here as everywhere -- no primary
    #: key reaches the engine.
    food_item_category_of: dict[str, str] = field(default_factory=dict)

    # ---------- Lookup (§4.1) ----------

    def upstream(
        self,
        sector: str,
        food_cat: str,
        food_item: str | None,
        destination: str | None,
        metric: str,
    ) -> Decimal:
        """§2.2's four-step upstream fallback, in order, then zero.

        The value alone. `upstream_with_basis()` below is the same chain and
        says *which* row answered; this delegates to it rather than repeating
        the candidate list, because two copies of a precedence order are two
        things to keep in agreement and the whole point of the order is that
        exactly one row wins.
        """
        return self.upstream_with_basis(
            sector, food_cat, food_item, destination, metric
        )[0]

    def upstream_with_basis(
        self,
        sector: str,
        food_cat: str,
        food_item: str | None,
        destination: str | None,
        metric: str,
    ) -> tuple[Decimal, UpstreamBasis]:
        """§2.2's four-step upstream fallback, and which step answered.

        Two nullable dimensions means four rows may legally exist for one
        `(sector, food_category, metric)`, and exactly one of them must win:

            1. (item, destination)   -- this food, at this destination
            2. (NULL, destination)   -- every food in this category, here
            3. (item, NULL)          -- this food, at every destination
            4. (NULL, NULL)          -- the category average, everywhere
            5. Decimal('0')

        **Steps 2 and 3 are the decision, and the destination wins.** Both
        name one dimension, so specificity alone cannot separate them -- the
        same position `downstream()` below is in, and settled here for a
        different reason.

        **Item-first silently re-opens O-7.** The prevention offset is stored
        as a category-level, destination-specific row at zero: shape 2. Give
        one food an item-level generic row (shape 3), order item-first, and a
        line moved to `prevention` picks up that food's generic factor instead
        of the zero -- the defect measured at 456.000 against a true 456.000,
        96.000 delivered, 78.9% of the benefit gone, one-directionally and
        always understating the client's "wasting less" story. Ordering the
        destination first means the existing category-level prevention zero
        covers every item under that category automatically, with no new row
        and no new guard: `find_missing_prevention_upstream`,
        `refuse_nonzero_prevention_factors` and golden `case_03` keep working
        untouched.

        **There is no silent-zero trap in the other direction**, which is what
        makes this tie affordable. A food with no row of its own does not fall
        to zero; it falls through to candidate 4, the category average -- a
        defined, meaningful number, and the nine category factors *are* the
        averages of those same foods. That is the difference between this
        dimension and the one O-7 closed, where the fallback was zero.

        `food_item` and `destination` may each be `None`, so the four
        candidates are not always four distinct keys: with `food_item=None`
        candidate 1 *is* candidate 2 and candidate 3 *is* candidate 4, and
        with `destination=None` candidate 1 *is* candidate 3. Every step is
        therefore **looked up** rather than reached by assuming an earlier one
        missed -- the loop repeats a key harmlessly, whereas an
        `if exact not in ...: return fallback` shape would answer with the
        wrong row, or with none. `downstream()` carries the same caveat.

        The basis is read off the **winning key**, not off the arguments, and
        that is what keeps it honest when the keys coincide: a lookup that
        named no item cannot be answered by an item row, and a lookup that
        named no destination cannot be answered by a here-only row, so the
        member returned describes the row that actually priced the line.
        """
        candidates = (
            (sector, food_cat, food_item, destination, metric),
            (sector, food_cat, None, destination, metric),
            (sector, food_cat, food_item, None, metric),
            (sector, food_cat, None, None, metric),
        )
        for key in candidates:
            if key in self.upstream_factors:
                return self.upstream_factors[key], _basis_of(key)

        return Decimal("0"), UpstreamBasis.ABSENT

    def downstream(
        self, destination: str, sector: str | None, food_cat: str | None, metric: str
    ) -> Decimal:
        """§4.1's two-dimensional fallback, in order. May return a negative
        value (an offset).

        Both `sector` and `food_cat` are nullable dimensions on
        `factor_downstream` (§2.2), so four rows may legally exist for one
        `(destination, metric)` and exactly one of them must win:

            1. (sector, food_category)   -- both stated
            2. (sector, NULL)            -- this sector, every food category
            3. (NULL, food_category)     -- every sector, this food category
            4. (NULL, NULL)              -- every sector, every food category
            5. Decimal('0')

        **Steps 2 and 3 are the decision, and the sector wins.** Both name one
        dimension, so specificity alone cannot separate them; §2.2 records the
        three reasons the tie is broken this way. The short form: the sector is
        always something the caller stated (`submission_entry.sector_id` is NOT
        NULL), whereas the food category may be `standard_mix` substituted by
        §6.2 for a caller who declined to give one -- so step 2 is keyed on
        what was said and step 3 may be keyed on what was assumed.

        `sector=None` asks for the every-sector rows directly, and `food_cat`
        may be `None` on the same terms, so the four candidates are not always
        four distinct keys: when `sector` is `None`, step 1 *is* step 3 and
        step 2 *is* step 4. That is why every step is *looked up* rather than
        reached by assuming an earlier one missed — the loop below repeats a
        key harmlessly, whereas an `if exact not in ...: return fallback` shape
        would answer with the wrong row, or with none. `upstream()` above
        carries the same caveat for its own two-step order.
        """
        candidates = (
            (destination, sector, food_cat, metric),
            (destination, sector, None, metric),
            (destination, None, food_cat, metric),
            (destination, None, None, metric),
        )
        for key in candidates:
            if key in self.downstream_factors:
                return self.downstream_factors[key]

        return Decimal("0")

    def constant(self, code) -> Decimal:
        if code in self.constants:
            return self.constants[code]
        raise UnknownConstantError(f"Unknown constant: {code}")

    def formula(self, metric: str) -> str:
        if metric in self.formulas:
            return self.formulas[metric]
        return DEFAULT_FORMULA

    def has_destination(self, code):
        return code in self.destinations

    def has_sector(self, code):
        return code in self.sectors

    def has_food_category(self, code):
        return code in self.food_categories

    def has_food_item(self, code) -> bool:
        return code in self.food_item_category_of

    def resolve_food_item(self, food_item: str | None, food_cat: str) -> str | None:
        """v1.54, §6. Check a named food against the vocabulary, or pass
        `None` straight through. Returns the code; raises `UnknownCodeError`.

        **The engine is what refuses an incoherent pair.** `submission_entry`
        carries `food_category_id` and `food_item_id` as two independent
        foreign keys and its CHECK constraint only says an item may not arrive
        without a category -- so `(fruit, cheese)` is storable at the database
        and has to be rejected here, before a lookup for it quietly falls
        through candidates 1 and 3 and prices cheese as the fruit average.
        A wrong answer that looks right is the failure mode this whole
        dimension is built to avoid.

        `None` is not an error and never becomes one: it is the answer of
        every visitor who was not asked step 2.5, or was asked and declined,
        and it means "the category" -- which is what every bundle written
        before v1.54 means on every row. It is deliberately **not** resolved
        to a stand-in the way `food_category=None` is resolved to
        `standard_mix` (§6.2): there is no standard food, and inventing one
        would put a number against a food the visitor never named.

        Raising rather than falling back is the rule §6 sets and the opposite
        of the chain above: an item the bundle has never heard of is a caller
        defect, whereas an item with no *factor row* is an ordinary, expected
        state answered by the category average.
        """
        if food_item is None:
            return None
        if food_item not in self.food_item_category_of:
            raise UnknownCodeError(f"unknown food_item: {food_item!r}")
        parent = self.food_item_category_of[food_item]
        if parent != food_cat:
            raise UnknownCodeError(
                f"food_item {food_item!r} belongs to food_category "
                f"{parent!r}, not {food_cat!r}"
            )
        return food_item

    def standard_mix_code(self) -> str:
        return self.standard_mix

    def is_prevention_destination(self, code: str) -> bool:
        return code in self.prevention_destination_codes

    def equivalences(self) -> tuple[EquivalenceSpec, ...]:
        return self.equivalence_specs

    # ---------- §10.2 ----------

    @classmethod
    def from_json(cls, data: Any) -> "FactorBundle":
        """Build a bundle from §10.2's `bundle.json` shape.

        Accepts the parsed object, or the JSON text as `str`/`bytes` -- it
        does not read a file; a caller that has one opens it. Raises
        `BundleFormatError`, never `KeyError`, on malformed input.

        `source_note` and `data_quality` may appear on any `upstream`,
        `downstream` or `equivalences` row and are accepted and ignored
        (§10.2): provenance changes no number, and rejecting an unknown key
        would fail on the bundle a staff member pasted out of `GET /factors`.
        """
        if isinstance(data, (str, bytes, bytearray)):
            try:
                data = json.loads(data)
            except ValueError as exc:
                raise BundleFormatError(f"bundle is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise BundleFormatError(
                f"bundle must be a JSON object, got {_type_name(data)}"
            )

        missing = [key for key in REQUIRED_KEYS if key not in data]
        if missing:
            raise BundleFormatError(
                "bundle is missing required key(s): " + ", ".join(missing)
            )

        version_label = data["version_label"]
        if not isinstance(version_label, str):
            raise BundleFormatError(
                f"version_label must be a string, got {_type_name(version_label)}"
            )
        is_mock = data["is_mock"]
        if not isinstance(is_mock, bool):
            # A truthy string here would silently switch off the mandatory
            # placeholder-data banner, so this is not coerced.
            raise BundleFormatError(
                f"is_mock must be true or false, got {_type_name(is_mock)}"
            )

        duplicates: list[str] = []

        sectors = {
            _code(row, "code", where) for row, where in _rows(data, "sectors")
        }

        food_categories: set[str] = set()
        standard_mix_codes: list[str] = []
        for row, where in _rows(data, "food_categories"):
            code = _code(row, "code", where)
            food_categories.add(code)
            if _flag(row, "is_standard_mix", where):
                standard_mix_codes.append(code)

        # §10.2's one optional *section*, and the reason this landing is inert:
        # a bundle that predates v1.54 has no item vocabulary, so every lookup
        # in it falls to the category rows it has always used. Absent is not
        # the same shape of decision as an absent `destination` key on a row
        # below -- a missing section means "this snapshot has no items", which
        # is true of every bundle in the tree today; a missing key on a row
        # would mean one row had lost a dimension the rest of the file has.
        food_item_category_of: dict[str, str] = {}
        if FOOD_ITEMS_KEY in data:
            for row, where in _rows(data, FOOD_ITEMS_KEY):
                code = _code(row, "code", where)
                _note_duplicate(
                    duplicates, food_item_category_of, code, f"food_item '{code}'"
                )
                # NOT NULL on `food_item.food_category_id`, and NOT NULL for a
                # reason: the parent is both the fallback a food without its
                # own row lands on and the thing `resolve_food_item()` checks
                # a request against. An item with no category is an item that
                # can be neither priced nor refused.
                food_item_category_of[code] = _code(row, "food_category", where)

        destination_groups = frozenset(
            _code(row, "code", where) for row, where in _rows(data, "destination_groups")
        )

        destinations: set[str] = set()
        destination_group_of: dict[str, str] = {}
        prevention_destination_codes: set[str] = set()
        for row, where in _rows(data, "destinations"):
            code = _code(row, "code", where)
            destinations.add(code)
            destination_group_of[code] = _code(row, "group", where)
            if _flag(row, "is_prevention", where):
                prevention_destination_codes.add(code)

        metric_rows = []
        for row, where in _rows(data, "metrics"):
            metric_rows.append(
                (
                    _sort_order(row, where),
                    _code(row, "code", where),
                    MetricSpec(
                        code=_code(row, "code", where),
                        unit=_text(row, "unit", where),
                        display_precision=_whole_number(row, "display_precision", where),
                    ),
                )
            )
        # §4.1 requires this tuple to arrive sorted by `sort_order`; the
        # engine iterates it as given and never re-sorts, so the guarantee has
        # to be made here for it to hold for a hand-written golden case too.
        metrics = tuple(spec for _, _, spec in sorted(metric_rows, key=lambda x: x[:2]))

        constants: dict[str, Decimal] = {}
        for row, where in _rows(data, "constants"):
            code = _code(row, "code", where)
            _note_duplicate(duplicates, constants, code, f"constant '{code}'")
            constants[code] = _decimal(row, "value", where)

        formulas: dict[str, str] = {}
        for row, where in _rows(data, "formulas"):
            metric = _code(row, "metric", where)
            _note_duplicate(duplicates, formulas, metric, f"formula for metric '{metric}'")
            formulas[metric] = _text(row, "expression", where)

        upstream_factors: dict[
            tuple[str, str, str | None, str | None, str], Decimal
        ] = {}
        for row, where in _rows(data, "upstream"):
            key = (
                _code(row, "sector", where),
                _code(row, "food_category", where),
                # v1.54's dimension, and the one key here that is **optional**
                # rather than mandatory-with-null. `destination` below is
                # mandatory because it has been part of every bundle since
                # v1.8, so a row missing it is a row that lost it. `food_item`
                # is new: every bundle in the tree omits it on every row and
                # means exactly what `null` means -- the category row. Demanding
                # the key would refuse all thirteen golden cases and make this
                # revision the opposite of inert.
                #
                # What the strict form would have caught -- a producer that
                # emits item rows and drops the key on some of them -- is
                # caught instead by `validate()`, which reports an upstream row
                # naming an item that is not in the bundle *and* one whose item
                # belongs to a different category. Neither catches a key
                # dropped from a row whose item was legitimate; that is the
                # cost of the compatibility, stated rather than hidden.
                _optional_nullable_code(row, "food_item", where),
                # §10.2: `null` is a legal value meaning "every destination",
                # but a *missing* key is a malformed row. A bundle whose
                # generic rows had silently lost their key would compute a
                # plausible, wrong answer instead of raising.
                _nullable_code(row, "destination", where),
                _code(row, "metric", where),
            )
            _note_duplicate(duplicates, upstream_factors, key, f"upstream row {key}")
            upstream_factors[key] = _decimal(row, "value_per_kg", where)

        downstream_factors: dict[tuple[str, str | None, str | None, str], Decimal] = {}
        for row, where in _rows(data, "downstream"):
            key = (
                _code(row, "destination", where),
                # v1.31's dimension, on exactly the same terms as
                # `food_category` beside it: `null` is a legal value meaning
                # "every sector", and a *missing* key is a malformed row. A
                # bundle whose every-sector rows had silently lost their key
                # would compute a plausible, wrong answer instead of raising.
                _nullable_code(row, "sector", where),
                _nullable_code(row, "food_category", where),
                _code(row, "metric", where),
            )
            _note_duplicate(duplicates, downstream_factors, key, f"downstream row {key}")
            downstream_factors[key] = _decimal(row, "value_per_kg", where)

        equivalence_rows = []
        for row, where in _rows(data, "equivalences"):
            code = _code(row, "code", where)
            equivalence_rows.append(
                (
                    _sort_order(row, where),
                    code,
                    EquivalenceSpec(
                        code=code,
                        source_metric_code=_code(row, "source_metric", where),
                        value_per_unit=_decimal(row, "value_per_unit", where),
                        label_template=_text(row, "label_template", where),
                        # §10.2: a hand-written bundle may omit either. `name`
                        # falls back to the code rather than raising, because
                        # every producer in the tree emits it and a golden
                        # bundle that forgot it should still calculate.
                        name=str(row.get("name") or code),
                        source_note=row.get("source_note"),
                    ),
                )
            )
        equivalence_specs = tuple(
            spec for _, _, spec in sorted(equivalence_rows, key=lambda x: x[:2])
        )

        return cls(
            upstream_factors=upstream_factors,
            downstream_factors=downstream_factors,
            constants=constants,
            formulas=formulas,
            destinations=destinations,
            sectors=sectors,
            food_categories=food_categories,
            # An inconsistent count is `validate()`'s to report, not
            # `from_json()`'s to refuse: §4.1 puts "exactly one food_category
            # has is_standard_mix" in the validation list, and a bundle that
            # cannot be *loaded* cannot have its problems shown to the staff
            # member who has to fix them.
            standard_mix=standard_mix_codes[0] if standard_mix_codes else "",
            version_label=version_label,
            is_mock=is_mock,
            metrics=metrics,
            equivalence_specs=equivalence_specs,
            destination_group_of=destination_group_of,
            destination_groups=destination_groups,
            standard_mix_codes=tuple(standard_mix_codes),
            duplicate_rows=tuple(duplicates),
            prevention_destination_codes=frozenset(prevention_destination_codes),
            food_item_category_of=food_item_category_of,
        )

    def validate(self) -> list[str]:
        """Internal consistency, as a list of human-readable problems. Empty
        means well-formed. Never raises -- the API layer (§6.2.1) decides how
        to present them, and §9's `details` shape carries one entry each.

        Deliberately *not* checked: whether a formula parses (that is
        `engine/evaluator.py`'s, and a bundle can legally carry a formula no
        request in it exercises), whether every metric has a formula (§4.1
        gives `formula()` a documented default), whether a factor is present
        for a combination a request might ask for (a missing factor is
        `Decimal('0')` by design, not an error), whether a value is plausible
        (a negative downstream factor is an offset, §2.2), and anything about
        `source_note` or `data_quality` (§10.2 -- accepted and ignored, and
        that includes not being reported here).

        Since v1.54 it also checks that every upstream row's `food_item` is
        null or in this bundle's vocabulary, that such a row's item belongs to
        the row's own `food_category`, and that every `food_items` row's parent
        category exists. A bundle with no items has none of these to report,
        which is every bundle written before v1.54.
        """
        problems: list[str] = []
        metric_codes = {metric.code for metric in self.metrics}

        for sector, food_cat, food_item, destination, metric in self.upstream_factors:
            where = (
                f"upstream row (sector={sector!r}, food_category={food_cat!r}, "
                f"food_item={food_item!r}, destination={destination!r}, "
                f"metric={metric!r})"
            )
            if sector not in self.sectors:
                problems.append(f"{where} names sector {sector!r}, which is not in this bundle")
            if food_cat not in self.food_categories:
                problems.append(
                    f"{where} names food_category {food_cat!r}, which is not in this bundle"
                )
            # v1.54's dimension, silent in exactly the way the destination
            # check below is: a misspelled item does not raise, it falls past
            # candidates 1 and 3 to the category average and the calculator
            # returns a plausible number that ignores the row entirely -- and
            # the row's author sees a factor they wrote having no effect.
            if food_item is not None and food_item not in self.food_item_category_of:
                problems.append(
                    f"{where} names food_item {food_item!r}, which is not in this bundle"
                )
            # The factor-table half of what `resolve_food_item()` refuses on a
            # request. A row priced for cheese but filed under fruit is
            # unreachable -- no lookup ever builds that key, because the item's
            # real parent is the category the request arrives with -- so it is
            # a written factor that can never apply.
            elif (
                food_item is not None
                and self.food_item_category_of[food_item] != food_cat
            ):
                problems.append(
                    f"{where} names food_item {food_item!r}, which belongs to "
                    f"food_category {self.food_item_category_of[food_item]!r}; "
                    "no lookup can reach this row"
                )
            # v1.8's dimension, and the check most easily forgotten: a
            # dangling destination here does not fail, it silently falls back
            # to the generic row, so the calculator returns a plausible number
            # that charges a prevented line its full upstream burden.
            if destination is not None and destination not in self.destinations:
                problems.append(
                    f"{where} names destination {destination!r}, which is not in this bundle"
                )
            if metric not in metric_codes:
                problems.append(f"{where} names metric {metric!r}, which is not in this bundle")

        for destination, sector, food_cat, metric in self.downstream_factors:
            where = (
                f"downstream row (destination={destination!r}, sector={sector!r}, "
                f"food_category={food_cat!r}, metric={metric!r})"
            )
            if destination not in self.destinations:
                problems.append(
                    f"{where} names destination {destination!r}, which is not in this bundle"
                )
            # v1.31's dimension, and the one with the same silent failure the
            # upstream destination check above exists for: a misspelled sector
            # here does not raise, it falls through to the every-sector row and
            # the calculator returns a plausible number priced for the wrong
            # stage of the supply chain.
            if sector is not None and sector not in self.sectors:
                problems.append(
                    f"{where} names sector {sector!r}, which is not in this bundle"
                )
            if food_cat is not None and food_cat not in self.food_categories:
                problems.append(
                    f"{where} names food_category {food_cat!r}, which is not in this bundle"
                )
            if metric not in metric_codes:
                problems.append(f"{where} names metric {metric!r}, which is not in this bundle")

        for food_item, parent in self.food_item_category_of.items():
            if parent not in self.food_categories:
                problems.append(
                    f"food_item {food_item!r} is in food_category {parent!r}, "
                    "which is not in this bundle"
                )

        for destination, group in self.destination_group_of.items():
            if group not in self.destination_groups:
                problems.append(
                    f"destination {destination!r} is in group {group!r}, "
                    "which is not in this bundle"
                )

        flagged = self.standard_mix_codes or (
            (self.standard_mix,) if self.standard_mix else ()
        )
        if not flagged:
            problems.append("no food_category is flagged is_standard_mix; exactly one must be")
        elif len(flagged) > 1:
            listed = ", ".join(repr(code) for code in flagged)
            problems.append(
                f"{len(flagged)} food_categories are flagged is_standard_mix "
                f"({listed}); exactly one must be"
            )

        for metric in self.formulas:
            if metric not in metric_codes:
                problems.append(
                    f"formula for metric {metric!r} names a metric which is not in this bundle"
                )

        for spec in self.equivalence_specs:
            if spec.source_metric_code not in metric_codes:
                problems.append(
                    f"equivalence {spec.code!r} names source_metric "
                    f"{spec.source_metric_code!r}, which is not in this bundle"
                )

        # Not in §4.1's list, and reported anyway: a duplicate key is the same
        # failure mode as a dangling destination -- the later row wins, nothing
        # raises, and the answer is plausible and wrong. The database's unique
        # constraints make this unreachable on the repository path, so it can
        # only fire on a hand-edited or hand-assembled bundle, which is
        # exactly the case that has no other check behind it.
        for row in self.duplicate_rows:
            problems.append(f"{row} appears more than once; the later row silently wins")

        return problems


# ---------- Parsing helpers ----------
#
# Every one of these raises BundleFormatError rather than letting a KeyError,
# TypeError or InvalidOperation escape (§4.4). The dry-run view shows the
# message to a staff member; a raw KeyError there is a 500 with no text they
# can act on.


def _type_name(value: Any) -> str:
    return "null" if value is None else type(value).__name__


def _rows(data: dict, key: str) -> list[tuple[dict, str]]:
    """The rows of one §10.2 section, each paired with a label for errors."""
    rows = data[key]
    if not isinstance(rows, list):
        raise BundleFormatError(f"{key} must be a list, got {_type_name(rows)}")
    out = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise BundleFormatError(
                f"{key}[{index}] must be an object, got {_type_name(row)}"
            )
        out.append((row, f"{key}[{index}]"))
    return out


def _code(row: dict, key: str, where: str) -> str:
    if key not in row:
        raise BundleFormatError(f"{where} is missing required key {key!r}")
    value = row[key]
    if not isinstance(value, str) or not value:
        raise BundleFormatError(
            f"{where}.{key} must be a non-empty string, got {_type_name(value)}"
        )
    return value


def _basis_of(key: tuple[str, str, str | None, str | None, str]) -> UpstreamBasis:
    """Which of §2.2's four rows a winning upstream key is.

    Read off the key rather than off the lookup's arguments, so that the two
    cases where candidates coincide are labelled by what actually answered:
    a lookup naming no item cannot be answered by an item row, and one naming
    no destination cannot be answered by an at-this-destination row.
    """
    _, _, food_item, destination, _ = key
    if food_item is not None:
        if destination is not None:
            return UpstreamBasis.ITEM_AT_DESTINATION
        return UpstreamBasis.ITEM_EVERY_DESTINATION
    if destination is not None:
        return UpstreamBasis.CATEGORY_AT_DESTINATION
    return UpstreamBasis.CATEGORY_EVERY_DESTINATION


def _optional_nullable_code(row: dict, key: str, where: str) -> str | None:
    """A code that may be `null` **or absent**, both meaning `None`.

    The weaker cousin of `_nullable_code` below, and the difference is a
    judgement about age rather than about importance: `upstream[].food_item`
    (v1.54) is absent from every bundle written before it, and those bundles
    mean by their silence precisely what `null` means, so absence is the
    normal state and cannot be an error. A type that is neither a string nor
    `null` is still refused -- `"food_item": 7` is a malformed row however
    optional the key is.
    """
    if key not in row or row[key] is None:
        return None
    return _code(row, key, where)


def _nullable_code(row: dict, key: str, where: str) -> str | None:
    """A code that may be `null`, where `null` is a value and a missing key is
    an error (§10.2, both `upstream[].destination` and
    `downstream[].food_category`)."""
    if key not in row:
        raise BundleFormatError(
            f"{where} is missing required key {key!r}; null is a legal value "
            "for it, an absent key is not"
        )
    if row[key] is None:
        return None
    return _code(row, key, where)


def _text(row: dict, key: str, where: str) -> str:
    if key not in row:
        raise BundleFormatError(f"{where} is missing required key {key!r}")
    value = row[key]
    if not isinstance(value, str):
        raise BundleFormatError(
            f"{where}.{key} must be a string, got {_type_name(value)}"
        )
    return value


def _flag(row: dict, key: str, where: str) -> bool:
    value = row.get(key, False)
    if not isinstance(value, bool):
        raise BundleFormatError(
            f"{where}.{key} must be true or false, got {_type_name(value)}"
        )
    return value


def _whole_number(row: dict, key: str, where: str) -> int:
    if key not in row:
        raise BundleFormatError(f"{where} is missing required key {key!r}")
    value = row[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise BundleFormatError(
            f"{where}.{key} must be a whole number, got {_type_name(value)}"
        )
    return value


def _sort_order(row: dict, where: str) -> int:
    if "sort_order" not in row:
        return 0
    return _whole_number(row, "sort_order", where)


def _decimal(row: dict, key: str, where: str) -> Decimal:
    """§1.2 and §10.2: every decimal in a bundle is a **string**.

    A JSON number is refused rather than converted. `json.loads` has already
    turned `1.9` into a `float` by the time this sees it, so accepting it
    would mean the bundle's own value had been through binary floating point
    before the engine ever touched it -- and the resulting drift would appear
    in a golden case's last decimal place with nothing pointing back here.
    """
    if key not in row:
        raise BundleFormatError(f"{where} is missing required key {key!r}")
    value = row[key]
    if not isinstance(value, str):
        raise BundleFormatError(
            f"{where}.{key} must be a decimal *string* such as \"1.9000000000\", "
            f"got {_type_name(value)} (contract §1.2: float is never an intermediate)"
        )
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise BundleFormatError(f"{where}.{key} is not a decimal: {value!r}") from exc
    if not parsed.is_finite():
        raise BundleFormatError(f"{where}.{key} is not a finite decimal: {value!r}")
    return parsed


def _note_duplicate(duplicates: list[str], seen: dict, key: Any, label: str) -> None:
    if key in seen:
        duplicates.append(label)
