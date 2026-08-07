"""Contract §8.2: the comparison view's scenarios are data, not code."""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from admin.comparison_models import ComparisonScenario, ComparisonScenarioLine

pytestmark = pytest.mark.db


def _scenario(_committed_session, taxonomy_for_factors, code="e7-typical-dairy"):
    scenario = ComparisonScenario(
        code=code, name="Typical dairy processor",
        sector_id=taxonomy_for_factors.sector.id,
        food_category_id=taxonomy_for_factors.category.id,
        gwp_horizon=100,
    )
    _committed_session.add(scenario)
    _committed_session.flush()
    return scenario


def test_a_scenario_carries_its_lines(_committed_session, taxonomy_for_factors):
    scenario = _scenario(_committed_session, taxonomy_for_factors)
    _committed_session.add(ComparisonScenarioLine(
        scenario_id=scenario.id,
        destination_id=taxonomy_for_factors.destination.id,
        qty_kg=Decimal("1200.000"),
    ))
    _committed_session.flush()

    stored = _committed_session.scalar(select(ComparisonScenario))
    assert len(stored.lines) == 1
    assert stored.lines[0].qty_kg == Decimal("1200.000")


def test_a_scenario_code_is_unique(_committed_session, taxonomy_for_factors):
    _scenario(_committed_session, taxonomy_for_factors)
    _committed_session.add(ComparisonScenario(
        code="e7-typical-dairy", name="Another",
        sector_id=taxonomy_for_factors.sector.id, gwp_horizon=100,
    ))

    with pytest.raises(IntegrityError):
        _committed_session.flush()


def test_a_food_category_is_optional(_committed_session, taxonomy_for_factors):
    """Null means the standard mix, the same reading the calculate request
    uses (§6.2). A scenario exercising "the visitor did not know" is one of
    the more useful ones to keep."""
    scenario = ComparisonScenario(
        code="e7-unknown-mix", name="Composition unknown",
        sector_id=taxonomy_for_factors.sector.id,
        food_category_id=None, gwp_horizon=100,
    )
    _committed_session.add(scenario)
    _committed_session.flush()

    assert _committed_session.scalar(select(ComparisonScenario)).food_category_id is None


def test_qty_kg_is_a_decimal_not_a_float(_committed_session, taxonomy_for_factors):
    """Global constraint. A scenario is an input to a calculation whose
    output staff will compare digit by digit; a float here would make two
    identical-looking scenarios disagree."""
    scenario = _scenario(_committed_session, taxonomy_for_factors)
    _committed_session.add(ComparisonScenarioLine(
        scenario_id=scenario.id,
        destination_id=taxonomy_for_factors.destination.id,
        qty_kg=Decimal("1200.001"),
    ))
    _committed_session.flush()
    _committed_session.expire_all()

    assert _committed_session.scalar(select(ComparisonScenarioLine)).qty_kg == Decimal("1200.001")


def test_deleting_a_scenario_takes_its_lines(_committed_session, taxonomy_for_factors):
    """A line without its scenario is unreachable and unrunnable."""
    scenario = _scenario(_committed_session, taxonomy_for_factors)
    _committed_session.add(ComparisonScenarioLine(
        scenario_id=scenario.id,
        destination_id=taxonomy_for_factors.destination.id,
        qty_kg=Decimal("1200.000"),
    ))
    _committed_session.flush()

    _committed_session.delete(scenario)
    _committed_session.flush()

    assert _committed_session.scalar(select(ComparisonScenarioLine)) is None


def test_the_gwp_horizon_is_one_of_the_two_the_contract_allows(_committed_session,
                                                               taxonomy_for_factors):
    """§6.2: 20 or 100. A scenario is a saved request, so it carries the same
    choice — and the whole point of a methane comparison is being able to
    pin which horizon produced a number."""
    scenario = _scenario(_committed_session, taxonomy_for_factors)
    scenario.gwp_horizon = 20
    _committed_session.flush()

    assert _committed_session.scalar(select(ComparisonScenario)).gwp_horizon == 20
