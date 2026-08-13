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
    destination_groups: tuple[DestinationGroupSpec, ...]
    destinations: tuple[DestinationSpec, ...]
    metrics: tuple[MetricSpec, ...]
    unit_presets: tuple[UnitPresetSpec, ...]
    factor_set_version: str
    factor_set_is_mock: bool


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

