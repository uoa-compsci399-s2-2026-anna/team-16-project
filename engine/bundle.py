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
from typing import Any

from engine.errors import BundleFormatError, UnknownConstantError
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


@dataclass
class FactorBundle:
    #: §4.1, and §2.2 since v1.8: keyed on
    #: `(sector, food_category, destination | None, metric)`. `None` is the
    #: usual value and means "every destination for this tuple"; the non-null
    #: rows are what make `prevention` a real 100% offset (open item O-7).
    upstream_factors: dict[tuple[str, str, str | None, str], Decimal]
    #: Keyed on `(destination, food_category | None, metric)`. `None` means
    #: "every food category for this destination" -- how a per-tonne charge
    #: such as the waste levy is expressed. Values may be negative.
    downstream_factors: dict[tuple[str, str | None, str], Decimal]
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

    # ---------- Lookup (§4.1) ----------

    def upstream(
        self, sector: str, food_cat: str, destination: str | None, metric: str
    ) -> Decimal:
        """Exact match on destination first, then the generic row, then zero.

        The generic row is the normal case. When `destination` is itself
        `None` the first lookup *is* the generic key -- which is why the
        fallback is a plain `.get` and not a presence test on something
        assumed absent (§4.1).
        """
        exact_key = (sector, food_cat, destination, metric)
        generic_key = (sector, food_cat, None, metric)

        if exact_key in self.upstream_factors:
            return self.upstream_factors[exact_key]

        return self.upstream_factors.get(generic_key, Decimal("0"))

    def downstream(self, destination: str, food_cat: str | None, metric: str) -> Decimal:
        """Exact match on food category first, then the generic row, then
        zero. May return a negative value (an offset)."""
        exact_key = (destination, food_cat, metric)
        fallback_key = (destination, None, metric)

        if exact_key in self.downstream_factors:
            return self.downstream_factors[exact_key]

        return self.downstream_factors.get(fallback_key, Decimal("0"))

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

    def standard_mix_code(self) -> str:
        return self.standard_mix

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

        destination_groups = frozenset(
            _code(row, "code", where) for row, where in _rows(data, "destination_groups")
        )

        destinations: set[str] = set()
        destination_group_of: dict[str, str] = {}
        for row, where in _rows(data, "destinations"):
            code = _code(row, "code", where)
            destinations.add(code)
            destination_group_of[code] = _code(row, "group", where)

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

        upstream_factors: dict[tuple[str, str, str | None, str], Decimal] = {}
        for row, where in _rows(data, "upstream"):
            key = (
                _code(row, "sector", where),
                _code(row, "food_category", where),
                # §10.2: `null` is a legal value meaning "every destination",
                # but a *missing* key is a malformed row. A bundle whose
                # generic rows had silently lost their key would compute a
                # plausible, wrong answer instead of raising.
                _nullable_code(row, "destination", where),
                _code(row, "metric", where),
            )
            _note_duplicate(duplicates, upstream_factors, key, f"upstream row {key}")
            upstream_factors[key] = _decimal(row, "value_per_kg", where)

        downstream_factors: dict[tuple[str, str | None, str], Decimal] = {}
        for row, where in _rows(data, "downstream"):
            key = (
                _code(row, "destination", where),
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
        """
        problems: list[str] = []
        metric_codes = {metric.code for metric in self.metrics}

        for sector, food_cat, destination, metric in self.upstream_factors:
            where = (
                f"upstream row (sector={sector!r}, food_category={food_cat!r}, "
                f"destination={destination!r}, metric={metric!r})"
            )
            if sector not in self.sectors:
                problems.append(f"{where} names sector {sector!r}, which is not in this bundle")
            if food_cat not in self.food_categories:
                problems.append(
                    f"{where} names food_category {food_cat!r}, which is not in this bundle"
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

        for destination, food_cat, metric in self.downstream_factors:
            where = (
                f"downstream row (destination={destination!r}, "
                f"food_category={food_cat!r}, metric={metric!r})"
            )
            if destination not in self.destinations:
                problems.append(
                    f"{where} names destination {destination!r}, which is not in this bundle"
                )
            if food_cat is not None and food_cat not in self.food_categories:
                problems.append(
                    f"{where} names food_category {food_cat!r}, which is not in this bundle"
                )
            if metric not in metric_codes:
                problems.append(f"{where} names metric {metric!r}, which is not in this bundle")

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
