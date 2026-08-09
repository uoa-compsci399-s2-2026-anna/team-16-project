"""Database-facing immutable DTOs; none of these expose ORM identities."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


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

