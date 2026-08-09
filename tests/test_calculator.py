from engine.types import (
    ScenarioInput,
    ScenarioLine,
    CalculationRequest,
)
from decimal import Decimal
from engine.bundle import FactorBundle
from engine.calculator import (
    calculate_scenario,
    calculate)

scenario_line = ScenarioLine(
    destination_code="landfill",
    qty_kg=Decimal("100")
)
alternative_line = ScenarioLine(
    "compost",
    Decimal("100")
)
scenario_input = ScenarioInput(
    "processing",
    "dairy",
    (
       scenario_line,
    )
)
alternative_input = ScenarioInput(
    "processing",
    "dairy",
    (
        alternative_line,
    )
)
calculation_request = CalculationRequest(
    current=scenario_input,
    alternative=None
)
calculation_request_alternative = CalculationRequest(
    current=scenario_input,
    alternative=alternative_input
)
upstream_factors = {
    ("processing", "dairy", "co2e"): Decimal("1.9")
}
downstream_factors = {
    ("landfill", "dairy", "co2e"): Decimal("0.99"),
    ("compost", "dairy", "co2e"): Decimal("0.1")
}
formulas = {
    "co2e": "qty_kg * (upstream + downstream)"
}
bundle = FactorBundle(
    upstream_factors=upstream_factors,
    downstream_factors=downstream_factors,
    constants={},
    formulas={
        "co2e": "qty_kg * (upstream + downstream)"
    },
    destinations={
        "landfill"
    },
    sectors={
        "processing"
    },
    food_categories={
        "dairy"
    },
    standard_mix="dairy",
    version_label="MOCK-v0",
    is_mock=True,
    metrics=(),
    equivalence_specs=()
)

def test_calculate_scenario():
    result = calculate_scenario(
        scenario_input,
        bundle
    )

    assert result.total_kg == Decimal("100")
    assert result.metrics["co2e"].total == Decimal("289")
    assert result.metrics["co2e"].by_destination[0].value == Decimal("289")
    assert result.metrics["co2e"].by_destination[0].qty_kg == Decimal("100")
    assert result.metrics["co2e"].by_destination[0].destination_code == "landfill"

def test_calculate():
    result = calculate(
        calculation_request,
        bundle
    )

    assert result.factor_set_version == "MOCK-v0"
    assert result.is_mock
    assert result.gwp_horizon == 100
    assert result.current.metrics["co2e"].total == Decimal("289")
    assert result.alternative is None
    assert result.net_benefit is None

def test_calculate_with_alternative():
    result = calculate(
        calculation_request_alternative,
        bundle
    )

    assert result.current.metrics["co2e"].total == Decimal("289")
    assert result.alternative.metrics["co2e"].total == Decimal("200")
    assert result.net_benefit["co2e"] == Decimal("89")