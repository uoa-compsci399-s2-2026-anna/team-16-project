"""Contract §3's dataclasses, at the shapes v1.9 defines.

These construct at module scope, as A's original file did, so a type that
loses a field fails at *collection* rather than in one assertion -- the whole
file goes red at once and names the import that broke.

The figures are the canonical ones from `tests/fixtures/calculate_response.json`
(entry 2: 800 kg of `not_harvested` vegetables, prevented) rather than
invented numbers, so a reader comparing this file against the fixture C and D
develop against sees the same arithmetic. Nothing here asserts that the engine
*computes* them -- these are storage tests -- but using the real figures means
the file does not have to be rewritten when the golden suite arrives.
"""

from decimal import Decimal

import pytest

from engine.types import (
    BreakdownRow,
    CalculationRequest,
    CalculationResult,
    CalculationTotals,
    EntryInput,
    EntryResult,
    EquivalenceResult,
    EquivalenceSpec,
    MetricResult,
    MetricSpec,
    ScenarioLine,
    ScenarioResult,
)

# ---------- Input ----------

current_line = ScenarioLine("not_harvested", Decimal("800.000"))
alternative_line = ScenarioLine("prevention", Decimal("800.000"))

entry_input = EntryInput(
    sector_code="primary_production",
    food_category_code="vegetables",
    current=(current_line,),
    alternative=(alternative_line,),
)
mix_entry_input = EntryInput(
    sector_code="retail",
    food_category_code=None,
    current=(ScenarioLine("landfill", Decimal("50.000")),),
    alternative=None,
)
calculation_request = CalculationRequest(entries=(entry_input, mix_entry_input))

# ---------- Output ----------

breakdown_row = BreakdownRow(
    destination_code="not_harvested",
    qty_kg=Decimal("800.000"),
    upstream=Decimal("0.4500000000"),
    downstream=Decimal("0.1200000000"),
    value=Decimal("456.0000000000"),
)
metric_result = MetricResult(
    metric_code="co2e",
    unit="kg CO2e",
    display_precision=1,
    total=Decimal("456.0000000000"),
    by_destination=(breakdown_row,),
)
#: v1.48, amending §3 rule 2: a totals-level row. Same destination and the
#: same additive `qty_kg`/`value` as `breakdown_row` above, but the two rates
#: are zero -- they are per-kilogram rates that can differ between the
#: entries sharing a destination, not sums.
rolled_up_breakdown_row = BreakdownRow(
    destination_code="not_harvested",
    qty_kg=Decimal("800.000"),
    upstream=Decimal("0.0000000000"),
    downstream=Decimal("0.0000000000"),
    value=Decimal("456.0000000000"),
)
rolled_up_metric = MetricResult(
    metric_code="co2e",
    unit="kg CO2e",
    display_precision=1,
    total=Decimal("456.0000000000"),
    by_destination=(rolled_up_breakdown_row,),
)
equivalence_result = EquivalenceResult(
    code="km_driven",
    label="Equivalent to driving 1,906 km",
    value=Decimal("1906.0800000000"),
    source_metric_code="co2e",
)
current_result = ScenarioResult(
    total_kg=Decimal("800.000"),
    metrics={"co2e": metric_result},
    equivalences=(equivalence_result,),
)
alternative_result = ScenarioResult(
    total_kg=Decimal("800.000"),
    metrics={
        "co2e": MetricResult("co2e", "kg CO2e", 1, Decimal("0.0000000000"), ())
    },
    equivalences=(),
)
entry_result = EntryResult(
    sector_code="primary_production",
    food_category_code="vegetables",
    current=current_result,
    alternative=alternative_result,
    net_benefit={"co2e": Decimal("456.0000000000")},
)
totals = CalculationTotals(
    current=ScenarioResult(
        total_kg=Decimal("800.000"),
        metrics={"co2e": rolled_up_metric},
        equivalences=(equivalence_result,),
    ),
    alternative=alternative_result,
    net_benefit={"co2e": Decimal("456.0000000000")},
    # §4.5, v1.48. The canonical fixture this file's figures are drawn from
    # supplies no money figure on its one entry, so the block itself is
    # absent -- not a computed zero.
    money=None,
)
calculation_result = CalculationResult(
    factor_set_version="MOCK-v0",
    is_mock=True,
    gwp_horizon=100,
    totals=totals,
    entries=(entry_result,),
)

# ---------- Input ----------


def test_scenario_line():
    assert current_line.destination_code == "not_harvested"
    assert current_line.qty_kg == Decimal("800.000")


def test_entry_input_carries_the_sector_and_both_scenarios():
    """§3: sector and food category sit on the entry, not on each scenario.

    A wire request cannot express two different sectors for one entry, so
    putting them on the scenario would make an unrepresentable state
    representable."""
    assert entry_input.sector_code == "primary_production"
    assert entry_input.food_category_code == "vegetables"
    assert entry_input.current == (ScenarioLine("not_harvested", Decimal("800.000")),)
    assert entry_input.alternative == (ScenarioLine("prevention", Decimal("800.000")),)


def test_a_null_food_category_and_a_missing_alternative_are_both_representable():
    """`None` means "use standard_mix" (§6.2); an entry may have no
    alternative at all, and §3 rule 3 governs what the roll-up then does."""
    assert mix_entry_input.food_category_code is None
    assert mix_entry_input.alternative is None


def test_calculation_request_carries_entries_in_order():
    """§3 rule 1. `submission_entry.sort_order` (§2.3) exists to persist it."""
    assert calculation_request.entries == (entry_input, mix_entry_input)
    assert calculation_request.entries[0].sector_code == "primary_production"
    assert calculation_request.gwp_horizon == 100


def test_scenario_input_is_gone_and_must_not_come_back():
    """§3 states this in terms.

    It held `sector_code`, `food_category_code` and `lines`; those three now
    live on `EntryInput`, split across `current` and `alternative`. It is
    deleted rather than emptied down to a single `lines` field because a
    surviving `ScenarioInput` is what `api/engine_adapter.py` populated with
    `req.current.sector_code` -- and a type that keeps its name while losing
    its meaning is the one an integration keeps using by accident. That is
    how a single-entry request reached a repository that writes N entries
    with nothing raising."""
    import engine.types

    assert not hasattr(engine.types, "ScenarioInput")
    with pytest.raises(ImportError):
        from engine.types import ScenarioInput  # noqa: F401


def test_calculation_request_has_no_top_level_scenarios():
    """v0.9's `CalculationRequest(current, alternative, gwp_horizon)` is the
    other half of the same deletion, and it is the shape
    `api/engine_adapter.make_request` stopped building."""
    assert not hasattr(calculation_request, "current")
    assert not hasattr(calculation_request, "alternative")


# ---------- Output ----------


def test_breakdown_row():
    assert breakdown_row.destination_code == "not_harvested"
    assert breakdown_row.qty_kg == Decimal("800.000")
    assert breakdown_row.upstream == Decimal("0.4500000000")
    assert breakdown_row.downstream == Decimal("0.1200000000")
    assert breakdown_row.value == Decimal("456.0000000000")


def test_a_breakdown_row_accepts_a_negative_downstream():
    """§3: downstream may be negative -- an offset. `animal_feed` ships one
    at -0.15 in the canonical fixtures, and the charts must render it."""
    row = BreakdownRow(
        "animal_feed",
        Decimal("300.000"),
        Decimal("1.9000000000"),
        Decimal("-0.1500000000"),
        Decimal("525.0000000000"),
    )
    assert row.downstream < 0


def test_metric_result():
    assert metric_result.metric_code == "co2e"
    assert metric_result.unit == "kg CO2e"
    assert metric_result.display_precision == 1
    assert metric_result.total == Decimal("456.0000000000")
    assert metric_result.by_destination == (breakdown_row,)


def test_one_metric_result_type_serves_both_levels():
    """§3 rule 2, as v1.48 amends it: `by_destination` is populated at
    both the entry and the totals level, in the same `MetricResult` type
    either way. The two differ in what the rows may claim: an entry's own
    rows carry real `upstream`/`downstream`, since they come from one
    scenario's own lines; a totals-level row carries only the additive
    `qty_kg` and `value`, with the two rates at zero, because the same
    destination can appear under several entries drawing different upstream
    factors and cannot state a single one. The serialiser still omits the
    key when the tuple is empty, which is the "one type either way" this
    test is named for."""
    assert rolled_up_metric.by_destination == (rolled_up_breakdown_row,)
    assert totals.current.metrics["co2e"].by_destination == (rolled_up_breakdown_row,)
    assert all(row.upstream == 0 and row.downstream == 0 for row in rolled_up_metric.by_destination)
    assert entry_result.current.metrics["co2e"].by_destination != ()


def test_equivalence_result_label_is_display_text_beside_a_full_precision_value():
    """§3 rule 5: `label` is `label_template` already interpolated -- whole
    number, comma separator -- while `value` keeps every digit. The engine
    produces both; no consumer re-derives the label and none re-rounds the
    value."""
    assert equivalence_result.code == "km_driven"
    assert equivalence_result.label == "Equivalent to driving 1,906 km"
    assert equivalence_result.value == Decimal("1906.0800000000")
    assert equivalence_result.source_metric_code == "co2e"


def test_scenario_result():
    assert current_result.total_kg == Decimal("800.000")
    assert current_result.metrics == {"co2e": metric_result}
    assert current_result.equivalences == (equivalence_result,)


def test_entry_result():
    assert entry_result.sector_code == "primary_production"
    assert entry_result.food_category_code == "vegetables"
    assert entry_result.current is current_result
    assert entry_result.alternative is alternative_result
    assert entry_result.net_benefit == {"co2e": Decimal("456.0000000000")}


def test_an_entry_without_an_alternative_is_representable():
    """§3 rule 3/4: the entry's own `alternative` and `net_benefit` stay
    `None` even though the totals roll-up counts its current figures on both
    sides."""
    lone = EntryResult("retail", None, current_result, None, None)
    assert lone.alternative is None
    assert lone.net_benefit is None


def test_calculation_totals_has_no_total_kg_field():
    """§3: `totals.total_kg` on the wire is `totals.current.total_kg` -- a
    wire-format hoist performed by `api/engine_adapter.py`, not a fourth
    field here. One mass figure is honest only because §6.2 requires an
    entry's two scenarios to agree to within 0.010 kg."""
    assert not hasattr(totals, "total_kg")
    assert totals.current.total_kg == Decimal("800.000")


def test_calculation_totals():
    assert totals.current.metrics["co2e"].total == Decimal("456.0000000000")
    assert totals.alternative is alternative_result
    assert totals.net_benefit == {"co2e": Decimal("456.0000000000")}


def test_totals_are_null_when_no_entry_carries_an_alternative():
    """§3 rule 4."""
    bare = CalculationTotals(
        current=current_result, alternative=None, net_benefit=None, money=None
    )
    assert bare.alternative is None
    assert bare.net_benefit is None


def test_calculation_result():
    assert calculation_result.factor_set_version == "MOCK-v0"
    assert calculation_result.is_mock
    assert calculation_result.gwp_horizon == 100
    assert calculation_result.totals is totals
    assert calculation_result.entries == (entry_result,)


def test_calculation_result_has_no_top_level_scenarios():
    """v0.9 put `current`, `alternative` and `net_benefit` here. They moved
    down onto `CalculationTotals`, which is what `serialize_result` reads."""
    assert not hasattr(calculation_result, "current")
    assert not hasattr(calculation_result, "alternative")
    assert not hasattr(calculation_result, "net_benefit")


# ---------- Bundle specs ----------


def test_the_bundle_specs_live_here():
    """§3 lists neither and §4.1 references both without saying where they
    live. They stay in `engine/types.py`, which is where `engine/bundle.py`
    already imports them from."""
    spec = MetricSpec(code="co2e", unit="kg CO2e", display_precision=1)
    assert (spec.code, spec.unit, spec.display_precision) == ("co2e", "kg CO2e", 1)
    equivalence = EquivalenceSpec(
        code="km_driven",
        source_metric_code="co2e",
        value_per_unit=Decimal("4.1800000000"),
        label_template="Equivalent to driving {value} km",
    )
    assert equivalence.source_metric_code == "co2e"
    assert equivalence.value_per_unit == Decimal("4.1800000000")
    assert "{value}" in equivalence.label_template


def test_engine_metric_spec_is_the_narrower_of_the_two():
    """`db/types.py::MetricSpec` carries `name`, `display_unit` and
    `sort_order` as well, because §2.1's taxonomy endpoint needs the whole
    row. Two types share a name across two packages; every import in the tree
    is absolute, so there is no collision -- but a reader who greps
    `MetricSpec` gets two hits with different arities, and this is the test
    that says which is which."""
    import db.types

    engine_fields = set(MetricSpec.__dataclass_fields__)
    db_fields = set(db.types.MetricSpec.__dataclass_fields__)
    assert engine_fields == {"code", "unit", "display_precision"}
    assert engine_fields < db_fields


def test_every_type_is_frozen():
    """§3: all are `@dataclass(frozen=True)`. A result the API layer could
    mutate on its way to the wire would be a second calculation site by
    accident."""
    with pytest.raises(Exception):
        current_line.qty_kg = Decimal("1")
    with pytest.raises(Exception):
        calculation_result.gwp_horizon = 20


def test_no_type_carries_a_primary_key():
    """§1.1: `code` is the cross-layer identifier and the front end never
    learns a primary key. None of §3's types has an `id`."""
    for cls in (
        ScenarioLine,
        EntryInput,
        CalculationRequest,
        BreakdownRow,
        MetricResult,
        EquivalenceResult,
        ScenarioResult,
        EntryResult,
        CalculationTotals,
        CalculationResult,
        MetricSpec,
        EquivalenceSpec,
    ):
        assert "id" not in cls.__dataclass_fields__, cls.__name__
