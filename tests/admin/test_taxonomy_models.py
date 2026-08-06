"""Contract §2.1. The taxonomy every factor and every calculation is keyed to."""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from admin.taxonomy_models import (
    Destination, DestinationGroup, FoodCategory, Metric, Sector, UnitPreset,
)

pytestmark = pytest.mark.db


def test_a_destination_belongs_to_a_group(session):
    group = DestinationGroup(code="disposal", name="Disposal", is_waste=True)
    session.add(group)
    session.flush()
    session.add(Destination(group_id=group.id, code="landfill", name="Landfill"))
    session.flush()

    landfill = session.scalar(select(Destination).where(Destination.code == "landfill"))
    assert landfill.group.code == "disposal"
    assert landfill.group.is_waste is True


def test_a_group_can_be_moved_out_of_waste(session):
    """Contract §2.1: the MfE definition may be revised, and the 2025 Otago
    baseline has already recommended moving bioprocessing from waste to reuse.
    A hard-coded enum could not express that; a column can."""
    group = DestinationGroup(code="recycle_recovery", name="Recycle", is_waste=True)
    session.add(group)
    session.flush()

    group.is_waste = False
    session.flush()

    assert session.scalar(
        select(DestinationGroup).where(DestinationGroup.code == "recycle_recovery")
    ).is_waste is False


def test_a_code_is_unique_per_table(session):
    session.add(Sector(code="processing", name="Processing"))
    session.flush()
    session.add(Sector(code="processing", name="Processing again"))

    with pytest.raises(IntegrityError):
        session.flush()


def test_a_unit_preset_may_apply_to_every_food_category(session):
    """food_category_id is nullable: contract §2.1, "Null means it applies to
    all categories". A 20 litre bucket holds a different mass of bread than of
    potatoes, but some presets genuinely are category-independent."""
    session.add(UnitPreset(code="bucket_20l_full", label="20 L bucket (full)",
                           food_category_id=None, kg_per_unit=Decimal("6.0000")))
    session.flush()

    preset = session.scalar(select(UnitPreset))
    assert preset.food_category_id is None


def test_kg_per_unit_is_a_decimal_not_a_float(session):
    """Global constraint: DECIMAL everywhere, FLOAT and DOUBLE prohibited.
    A float cannot hold 6.0001 exactly, and unit conversion is user-facing."""
    session.add(UnitPreset(code="crate_30l", label="30 L crate",
                           kg_per_unit=Decimal("6.0001")))
    session.flush()
    session.expire_all()

    assert session.scalar(select(UnitPreset)).kg_per_unit == Decimal("6.0001")


def test_a_metric_carries_its_own_display_settings(session):
    """Contract §2.1: adding a metric is one row plus one formula, no code
    changes. Everything the front end needs to render it lives here."""
    session.add(Metric(code="co2e", name="Greenhouse gases", unit="kg CO2e",
                       display_unit="t CO2e", display_precision=1))
    session.flush()

    metric = session.scalar(select(Metric))
    assert (metric.display_unit, metric.display_precision) == ("t CO2e", 1)


def test_display_unit_may_be_absent(session):
    """Contract §2.1: display_unit "falls back to unit when null"."""
    session.add(Metric(code="mass", name="Mass", unit="kg"))
    session.flush()

    assert session.scalar(select(Metric)).display_unit is None


def test_a_food_category_can_be_the_standard_mix(session):
    session.add(FoodCategory(code="standard_mix", name="Mixed food waste",
                             is_standard_mix=True))
    session.flush()

    assert session.scalar(select(FoodCategory)).is_standard_mix is True
