"""Database-facing immutable DTOs; none of these expose ORM identities."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

#: Contract §2.1 and §6.2. **There is no `PREVENTION_CODE` here any more.**
#:
#: It was `"prevention"`, and three rules were stated in terms of that literal.
#: A second vocabulary's prevention row (`refed_prevention`, §10.3) was
#: therefore not covered by any of them, and could be entered as
#: *current*-scenario waste and reach the public statistics -- the defect v1.5
#: closed for `prevention` itself, arriving one code along. The role is now
#: `destination.is_prevention`, a column, and every guard reads it:
#: `admin/taxonomy_rules.check_prevention_destination` refuses a taxonomy with
#: no flagged row, §6.2 refuses *any* flagged destination in a current
#: scenario, and §5.4 excludes the alternative scenario so none can become a
#: bucket. `db/repository.prevention_destination_codes` is how a caller
#: outside `db/` -- `api/` may not import `admin/` -- asks which codes those
#: are.


@dataclass(frozen=True)
class SectorSpec:
    code: str
    name: str
    description: str | None
    sort_order: int


@dataclass(frozen=True)
class FoodCategorySpec:
    code: str
    name: str
    is_standard_mix: bool
    sort_order: int


@dataclass(frozen=True)
class FoodItemSpec:
    """One named food *within* a category -- "cheese", not "dairy". §2.1, v1.54.

    `food_category` is the parent's **code**, not its id, for the reason every
    other spec here carries codes: no primary key crosses a layer, and the
    front end groups step 2.5's foods under the categories step 2 offered by
    matching this against `FoodCategorySpec.code`.

    The parent is not decoration. A food with no factor row of its own is
    priced at its category's average (§2.2), so the category is both the
    fallback and what the engine checks a named food against -- an item whose
    parent the response did not carry is one the front end can neither group
    nor explain.
    """

    code: str
    name: str
    food_category: str
    sort_order: int


@dataclass(frozen=True)
class DestinationGroupSpec:
    code: str
    name: str
    is_waste: bool
    sort_order: int


@dataclass(frozen=True)
class DestinationSpec:
    code: str
    name: str
    group: str
    description: str | None
    sort_order: int
    #: §2.1/§6.1. Travels to the front end so `calculator.js` can keep the
    #: prevention offset out of the *current* scenario without knowing a code.
    #: Defaulted so that a hand-built spec in a test stays a positional
    #: five-tuple, the way `FoodCategorySpec.is_standard_mix` is not -- that
    #: one predates this and is positional.
    is_prevention: bool = False


@dataclass(frozen=True)
class MetricSpec:
    code: str
    name: str
    unit: str
    display_unit: str | None
    display_precision: int
    sort_order: int


@dataclass(frozen=True)
class UnitPresetSpec:
    code: str
    label: str
    food_category: str | None
    kg_per_unit: Decimal


@dataclass(frozen=True)
class TaxonomySnapshot:
    sectors: tuple[SectorSpec, ...]
    food_categories: tuple[FoodCategorySpec, ...]
    #: v1.58. The item vocabulary, placed beside the categories it refines so
    #: that §6.1's JSON reads in the order the form is filled in.
    #:
    #: **Required rather than defaulted**, unlike `DestinationSpec.
    #: is_prevention`: this is a list, and a defaulted empty one would let a
    #: caller that forgot it return a snapshot in which nothing distinguishes
    #: "this deployment has no foods" from "this function was not updated".
    #: Both constructors in `db/repository.py` pass it explicitly.
    food_items: tuple[FoodItemSpec, ...]
    destination_groups: tuple[DestinationGroupSpec, ...]
    destinations: tuple[DestinationSpec, ...]
    metrics: tuple[MetricSpec, ...]
    unit_presets: tuple[UnitPresetSpec, ...]
    factor_set_version: str
    factor_set_is_mock: bool
    #: v1.58, and the one `factor_set` field that must reach the browser and
    #: must never reach the engine (design §3). It decides whether the
    #: front end renders step 2.5 at all; it decides no figure, which is what
    #: keeps a flag flip in either direction reproducible.
    #: Required, not defaulted, for the same reason `factor_set_version` and
    #: `factor_set_is_mock` are: a snapshot that reported the switch off
    #: because nobody set it looks exactly like one reporting a set that has
    #: it off, and only one of those is an answer.
    factor_set_item_level_enabled: bool


@dataclass(frozen=True)
class StatsBucket:
    code: str
    label: str
    count: int
    share: Decimal
    total_kg: Decimal


@dataclass(frozen=True)
class PublicStats:
    generated_at: datetime
    total_calculations: int
    suppression_threshold: int
    by_destination: tuple[StatsBucket, ...]
    by_sector: tuple[StatsBucket, ...]
    by_food_category: tuple[StatsBucket, ...]

