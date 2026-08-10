"""The New Zealand food loss and waste taxonomy, as shipped.

Sources:
  Destination groups and destinations - Ministry for the Environment, "Food
  loss and waste definition for Aotearoa New Zealand" (2023, ME 1796).
  Sectors and food categories - University of Otago national baseline (2025).

Idempotent and non-destructive: matches on `code`, creates what is missing,
and never updates an existing row. Staff rename these through the panel, and
a seed that reasserted its own names on every deployment would silently undo
that.

The eight-versus-nine discrepancy: docs/interfaces.md §2.1 describes "the
eight Otago baseline categories" (plus standard_mix), but the Otago source
list has nine substantive categories (plus standard_mix, for ten codes
below). All nine are seeded - a category too many is a row a staff member
can deactivate through the panel, a category too few is data nobody can
enter. This needs the client's word and is recorded in docs/architecture.md
§10, Open Items.

The unit presets' kg_per_unit values are placeholders - the client has not
supplied conversion data. Each says so in its source_note.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from admin.taxonomy_models import (
    Destination, DestinationGroup, FoodCategory, Metric, Sector, UnitPreset,
)
from admin.taxonomy_rules import check_prevention_intact, check_single_standard_mix

DESTINATION_GROUPS = [
    # (code, name, is_waste, sort_order)
    ("reuse", "Reuse", False, 10),
    ("recycle_recovery", "Recycle and recovery", True, 20),
    ("disposal", "Disposal", True, 30),
]

DESTINATIONS = [
    # (group_code, code, name, sort_order)
    ("reuse", "prevention", "Prevented — waste avoided", 5),
    ("reuse", "food_redistribution", "Food redistribution", 10),
    ("reuse", "upcycling", "Upcycling to other food products", 20),
    ("reuse", "animal_feed", "Animal feed", 30),
    ("recycle_recovery", "compost", "Composting (aerobic digestion)", 40),
    ("recycle_recovery", "anaerobic_digestion", "Anaerobic digestion", 50),
    ("recycle_recovery", "land_application", "Land application", 60),
    ("recycle_recovery", "not_harvested", "Not harvested or ploughed in", 70),
    ("recycle_recovery", "bioprocessing", "Processing into non-food items", 80),
    ("recycle_recovery", "other_recovery", "Other recovery, including biodiesel", 90),
    ("disposal", "combustion", "Combustion", 100),
    ("disposal", "landfill", "Landfill", 110),
    ("disposal", "refuse_discard", "Refuse or discard", 120),
    ("disposal", "sewer", "Sewer or wastewater", 130),
]

SECTORS = [
    # (code, name, sort_order)
    ("primary_production", "Primary production", 10),
    ("processing", "Processing and manufacturing", 20),
    ("wholesale_retail", "Wholesale and retail", 30),
    ("consumer_household", "Households", 40),
    ("consumer_hospitality", "Hospitality", 50),
    ("consumer_institution", "Institutions", 60),
]

FOOD_CATEGORIES = [
    # (code, name, is_standard_mix, sort_order)
    ("standard_mix", "Mixed food waste (composition unknown)", True, 5),
    ("fruit", "Fruit", False, 10),
    ("vegetables", "Vegetables", False, 20),
    ("nuts_seeds", "Nuts and edible seeds", False, 30),
    ("meat", "Meat", False, 40),
    ("seafood", "Seafood", False, 50),
    ("dairy", "Dairy", False, 60),
    ("bakery_grains", "Bakery and grains", False, 70),
    ("staples", "Staples", False, 80),
    ("beverages", "Beverages (non-dairy)", False, 90),
]

METRICS = [
    # (code, name, unit, display_unit, display_precision, sort_order)
    #
    # `display_unit` is a presentation variant of `unit` AT THE SAME SCALE
    # (§6.1) -- a typographic difference, never a conversion. These rows read
    # "t CO2e", "kL" and "t" until 2026-08-09, against totals the engine
    # returns in kg and L, and nothing in the system converts between the two:
    # the front end performs no arithmetic on an API figure (§7.6.1), so the
    # label alone would have made every greenhouse-gas figure read a thousand
    # times too small. If a metric should be reported in tonnes, that is its
    # `unit` and the formula produces tonnes.
    ("co2e", "Greenhouse gases", "kg CO2e", "kg CO₂e", 1, 10),
    ("ch4", "Methane", "kg CH4", None, 1, 20),
    ("water", "Water", "L", "L", 0, 30),
    ("cost", "Cost", "NZD", None, 0, 40),
    ("mass", "Mass", "kg", "kg", 1, 50),
]

_PLACEHOLDER = (
    "Placeholder conversion — the client has not yet supplied measured data. "
    "Replace before the calculator is published."
)

UNIT_PRESETS = [
    # (code, label, kg_per_unit)
    ("bucket_10l_full", "10 L bucket (full)", Decimal("3.0000")),
    ("bucket_20l_full", "20 L bucket (full)", Decimal("6.0000")),
    ("crate_30l_full", "30 L crate (full)", Decimal("9.0000")),
    ("wheelie_bin_120l", "120 L wheelie bin (full)", Decimal("36.0000")),
    ("wheelie_bin_240l", "240 L wheelie bin (full)", Decimal("72.0000")),
]


def _ensure(session: Session, model, code: str, **fields) -> bool:
    """Create one row if its code is absent. Returns True if it created it."""
    existing = session.scalar(select(model).where(model.code == code))
    if existing is not None:
        return False
    session.add(model(code=code, **fields))
    return True


def seed_taxonomy(session: Session) -> dict[str, int]:
    """Load the NZ taxonomy. Returns table name to rows created.

    Never commits - the caller owns the transaction, matching the convention
    the rest of admin/ follows. That leaves this function, not the caller,
    responsible for refusing to leave a broken taxonomy staged for that
    commit: it goes nowhere near AuditedModelView, so neither
    check_single_standard_mix nor check_prevention_intact would otherwise
    ever run against what it writes. A staff member who has renamed
    `standard_mix`'s *code* through the panel (a supported edit -
    FoodCategoryAdmin.form_columns includes `code`) makes this a live case,
    not a hypothetical one: re-running the seed no longer recognises the
    renamed row as `standard_mix`, creates a fresh one, and without the
    checks below would commit two active standard mixes with nothing to
    refuse it. Raising here propagates out to the caller - admin.cli's
    seed-taxonomy command never reaches its own `db_session.commit()`, and
    closing the session on the way out rolls back everything this call
    flushed.
    """
    created = {
        "destination_group": 0, "destination": 0, "sector": 0,
        "food_category": 0, "metric": 0, "unit_preset": 0,
    }

    for code, name, is_waste, sort_order in DESTINATION_GROUPS:
        created["destination_group"] += _ensure(
            session, DestinationGroup, code,
            name=name, is_waste=is_waste, sort_order=sort_order,
        )
    session.flush()  # destination rows need the group ids

    groups = {
        g.code: g.id for g in session.scalars(select(DestinationGroup)).all()
    }
    for group_code, code, name, sort_order in DESTINATIONS:
        created["destination"] += _ensure(
            session, Destination, code,
            group_id=groups[group_code], name=name, sort_order=sort_order,
        )

    for code, name, sort_order in SECTORS:
        created["sector"] += _ensure(
            session, Sector, code, name=name, sort_order=sort_order
        )

    for code, name, is_standard_mix, sort_order in FOOD_CATEGORIES:
        created["food_category"] += _ensure(
            session, FoodCategory, code,
            name=name, is_standard_mix=is_standard_mix, sort_order=sort_order,
        )

    for code, name, unit, display_unit, precision, sort_order in METRICS:
        created["metric"] += _ensure(
            session, Metric, code, name=name, unit=unit,
            display_unit=display_unit, display_precision=precision,
            sort_order=sort_order,
        )

    for code, label, kg_per_unit in UNIT_PRESETS:
        created["unit_preset"] += _ensure(
            session, UnitPreset, code, label=label, kg_per_unit=kg_per_unit,
            food_category_id=None, source_note=_PLACEHOLDER,
        )

    # Flush so the checks below see every row this call just staged, then
    # refuse to hand back a taxonomy the panel itself would not accept - see
    # this function's own docstring for why nothing else stands guard here.
    session.flush()
    check_single_standard_mix(session)
    check_prevention_intact(session)

    return created
