from decimal import Decimal
from engine.types import ScenarioLine
from engine.types import ScenarioInput
from engine.types import CalculationRequest
from engine.types import BreakdownRow
from engine.types import MetricResult
from engine.types import EquivalenceResult
from engine.types import ScenarioResult
from engine.types import CalculationResult

scenario_line = ScenarioLine("landfill", Decimal("100.000"))
scenario_input = ScenarioInput("processing", "dairy", (scenario_line,))                                                         
calculation_request = CalculationRequest(scenario_input, None)
breakdown_row = BreakdownRow("landfill", Decimal("100"), Decimal("1.9"), Decimal("0.99"), Decimal("289"))
metric_result = MetricResult("co2e", "kg CO2e", 1, Decimal("339"), (breakdown_row,))
equivalence_result = EquivalenceResult("km_driven", "equals to drive 14,500 km", Decimal("14500"), "co2e")
scenario_result = ScenarioResult(Decimal("500"), {"co2e": metric_result}, (equivalence_result,))
calculation_result = CalculationResult("MOCK-v0", True, 100, scenario_result, None, None)

def test_scenario_line():
    assert scenario_line.destination_code == "landfill"
    assert scenario_line.qty_kg == Decimal("100.000")

def test_scenario_input():                                         
    assert scenario_input.sector_code == "processing"
    assert scenario_input.food_category_code == "dairy"
    assert scenario_input.lines == (ScenarioLine("landfill", Decimal("100.000")),)            

def test_calculation_request():
    assert calculation_request.current == ScenarioInput("processing", "dairy", (ScenarioLine("landfill", Decimal("100.000")),))
    assert calculation_request.alternative == None
    assert calculation_request.gwp_horizon == 100

def test_breakdown_row():
    assert breakdown_row.destination_code == "landfill"
    assert breakdown_row.qty_kg == Decimal("100")
    assert breakdown_row.upstream == Decimal("1.9")
    assert breakdown_row.downstream == Decimal("0.99")
    assert breakdown_row.value == Decimal("289")

def test_metric_result():
    assert metric_result.metric_code == "co2e"
    assert metric_result.unit == "kg CO2e"
    assert metric_result.display_precision == 1
    assert metric_result.total == Decimal("339")
    assert metric_result.by_destination == (BreakdownRow("landfill", Decimal("100"), Decimal("1.9"), Decimal("0.99"), Decimal("289")),)

def test_equivalence_result():
    assert equivalence_result.code == "km_driven"
    assert equivalence_result.label == "equals to drive 14,500 km"
    assert equivalence_result.value == Decimal("14500")
    assert equivalence_result.source_metric_code == "co2e"

def test_scenario_result():
    assert scenario_result.total_kg == Decimal("500")
    assert scenario_result.metrics == {"co2e": metric_result}
    assert scenario_result.equivalences == (equivalence_result,)

def test_calculation_result():
    assert calculation_result.factor_set_version == "MOCK-v0"
    assert calculation_result.is_mock
    assert calculation_result.gwp_horizon == 100
    assert calculation_result.current == scenario_result
    assert calculation_result.alternative == None
    assert calculation_result.net_benefit == None