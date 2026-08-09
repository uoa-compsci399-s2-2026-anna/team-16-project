from dataclasses import dataclass
from decimal import Decimal

@dataclass(frozen=True)
class ScenarioLine:
    destination_code: str
    qty_kg: Decimal

@dataclass(frozen=True)
class ScenarioInput:
    sector_code: str
    food_category_code: str | None
    lines: tuple[ScenarioLine, ...]

@dataclass(frozen=True)
class CalculationRequest:
    current: ScenarioInput
    alternative: ScenarioInput | None
    gwp_horizon: int = 100

@dataclass(frozen=True)
class BreakdownRow:
    destination_code: str
    qty_kg: Decimal
    upstream: Decimal
    downstream: Decimal
    value: Decimal

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
    label: str
    value: Decimal
    source_metric_code: str

@dataclass(frozen=True)
class ScenarioResult:
    total_kg: Decimal
    metrics: dict[str, MetricResult]
    equivalences: tuple[EquivalenceResult, ...]

@dataclass(frozen=True)
class CalculationResult:
    factor_set_version: str
    is_mock: bool
    gwp_horizon: int
    current: ScenarioResult
    alternative: ScenarioResult | None
    net_benefit: dict[str, Decimal] | None

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