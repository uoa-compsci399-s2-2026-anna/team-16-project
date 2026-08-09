from decimal import Decimal
from engine.bundle import FactorBundle
import pytest
from engine.errors import UnknownConstantError
from engine.types import MetricSpec, EquivalenceSpec

constants = {
    "GWP_CH4_100": Decimal("28")
}
upstream_factors = {
    ("processing", "dairy", "co2e") : Decimal("1.9")
}
downstream_factors = {
    ("landfill", "dairy", "co2e") : Decimal("0.99"),
    ("compost", None, "co2e") : Decimal("0.25")
}
formulas = {
    "co2e" : "qty_kg * (upstream + downstream)",
    "water" : "qty_kg * upstream"
}
sectors = {
    "processing"
}
categories = {
    "dairy"
}
destinations = {"landfill", "compost", "animal_feed"}
standard_mix = "standard_mix"
version_label = "MOCK-v0"
is_mock = True
metric = MetricSpec("co2e", "kg CO2e", 1)
equivalence_specs = (
    EquivalenceSpec(
        "km_driven",
        "co2e",
        Decimal("0.5"),
        "equals to drive {value} km"
    ),
)
bundle = FactorBundle(upstream_factors, downstream_factors, constants, formulas, destinations, sectors, categories, standard_mix, version_label, is_mock, (metric,), equivalence_specs)

def test_upstream():
    assert bundle.upstream("processing", "dairy", "co2e") == Decimal("1.9")
    assert bundle.upstream("processing", "bakery", "co2e") == Decimal("0")

def test_downstream():
    assert bundle.downstream("landfill", "dairy", "co2e") == Decimal("0.99")
    assert bundle.downstream("landfill", None, "co2e") == Decimal("0")
    assert bundle.downstream("compost", "dairy", "co2e") == Decimal("0.25")

def test_constant_returns_existing_constant():
    assert bundle.constant("GWP_CH4_100") == Decimal("28")
    with pytest.raises(UnknownConstantError): bundle.constant("NOT_EXIST")

def test_formula():
    assert bundle.formula("water") == "qty_kg * upstream"
    assert bundle.formula("unknown_metric") == "qty_kg * (upstream + downstream)"

def test_has_destination():
    assert bundle.has_destination("landfill") ==  True
    assert bundle.has_destination("moon") == False

def test_has_sector():
    assert bundle.has_sector("processing") == True
    assert bundle.has_sector("moon") == False

def test_has_food_category():
    assert bundle.has_food_category("dairy") == True
    assert bundle.has_food_category("unknown_food") == False

def test_standard_mix():
    assert bundle.standard_mix_code() == "standard_mix"

def test_version_label():
    assert bundle.version_label == "MOCK-v0"

def test_is_mock():
    assert bundle.is_mock

def test_metric():
    assert bundle.metrics[0].code == "co2e"

def test_equivalences():
    result = bundle.equivalences()

    assert len(result) == 1
    assert result[0].code == "km_driven"
    assert result[0].source_metric_code == "co2e"
    assert result[0].value_per_unit == Decimal("0.5")
    assert result[0].label_template == "equals to drive {value} km"