from decimal import Decimal
from engine.bundle import FactorBundle
from engine.evaluator import evaluate
from engine.types import (
    ScenarioInput,
    ScenarioResult,
    MetricResult,
    BreakdownRow,
    CalculationRequest
)
from engine.types import CalculationResult


def calculate_scenario(scenario: ScenarioInput, bundle: FactorBundle) -> ScenarioResult:
    line = scenario.lines[0]

    upstream = bundle.upstream(
    scenario.sector_code,
    scenario.food_category_code,
    "co2e")

    downstream = bundle.downstream(
    line.destination_code,
    scenario.food_category_code,
    "co2e")

    variables = {
    "qty_kg": line.qty_kg,
    "upstream": upstream,
    "downstream": downstream,}
    formula = bundle.formula("co2e")

    total = evaluate(formula, variables)

    breakdown_row = BreakdownRow(
    destination_code=line.destination_code,
    qty_kg=line.qty_kg,
    upstream=upstream,
    downstream=downstream,
    value=total)

    metric_result = MetricResult(
    metric_code="co2e",
    unit="kg CO2e",
    display_precision=2,
    total=total,
    by_destination=(breakdown_row,))

    scenario_result = ScenarioResult(
    total_kg=line.qty_kg,
    metrics={
        "co2e": metric_result
    },
    equivalences=())

    return scenario_result

def calculate(
    request: CalculationRequest,
    bundle: FactorBundle
) -> CalculationResult:

    current = calculate_scenario(
        request.current,
        bundle
    )
    alternative = None
    net_benefit = None

    if request.alternative is not None:
        alternative = calculate_scenario(
            request.alternative,
            bundle
        )

        net_benefit = {
            "co2e": (
                current.metrics["co2e"].total -
                alternative.metrics["co2e"].total
            )
        }
    return CalculationResult(
        factor_set_version=bundle.version_label,
        is_mock=bundle.is_mock,
        gwp_horizon=request.gwp_horizon,
        current=current,
        alternative=alternative,
        net_benefit=net_benefit
    )
