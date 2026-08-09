from decimal import Decimal
from dataclasses import dataclass
from engine.errors import UnknownConstantError
from engine.types import MetricSpec, EquivalenceSpec

@dataclass
class FactorBundle:
    upstream_factors: dict[tuple[str, str, str], Decimal]
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

    def upstream(self, sector: str, food_cat: str, metric: str) -> Decimal:
        return self.upstream_factors.get((sector, food_cat, metric), Decimal("0"))
    
    def downstream(self, destination: str, food_cat: str, metric: str) -> Decimal:
        exact_key = (destination, food_cat, metric)
        fallback_key = (destination, None, metric)

        if exact_key in self.downstream_factors:
            return self.downstream_factors[exact_key]
        
        return self.downstream_factors.get(fallback_key,Decimal("0"))

    def constant(self, code) -> Decimal:
        if code in self.constants:
            return self.constants[code]
        raise UnknownConstantError(f"Unknown constant: {code}")
    
    def formula(self, metric: str) -> str:
        if metric in self.formulas:
            return self.formulas[metric]
        return "qty_kg * (upstream + downstream)"

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