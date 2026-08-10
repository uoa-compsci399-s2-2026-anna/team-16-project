"""Contract §2.2. Where the calculator's actual numbers live."""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from admin.factor_models import (
    Constant, Equivalence, FactorDownstream, FactorSet, FactorSetStatus,
    FactorUpstream, Formula,
)
from admin.taxonomy_models import FoodCategory, Metric, Sector

pytestmark = pytest.mark.db


def _downstream_prereqs(session) -> int:
    """A destination id, with the group its foreign key requires."""
    from admin.taxonomy_models import Destination, DestinationGroup

    group = DestinationGroup(code="disposal", name="Disposal", is_waste=True)
    session.add(group)
    session.flush()
    dest = Destination(group_id=group.id, code="landfill", name="Landfill")
    session.add(dest)
    session.flush()
    return dest.id


def _set(session, label="2026-Q3", status=FactorSetStatus.draft):
    fs = FactorSet(version_label=label, status=status, is_mock=True)
    session.add(fs)
    session.flush()
    return fs


def _metric(session, code="co2e"):
    m = Metric(code=code, name=code.upper(), unit="kg")
    session.add(m)
    session.flush()
    return m


def test_a_downstream_factor_may_be_negative(session):
    """Contract §2.2: downstream may be an offset. Animal feed displaces
    feed production, so its downstream factor is genuinely below zero — a
    column or a validator that clamped it would silently delete the entire
    benefit of diverting food to animals."""
    fs, metric = _set(session), _metric(session)
    dest_group = _downstream_prereqs(session)
    session.add(FactorDownstream(
        factor_set_id=fs.id, destination_id=dest_group, food_category_id=None,
        metric_id=metric.id, value_per_kg=Decimal("-1.9000000000"),
    ))
    session.flush()

    assert session.scalar(select(FactorDownstream)).value_per_kg == Decimal("-1.9000000000")


def test_two_generic_downstream_rows_are_refused(session):
    """Contract §2.2, raised by B: MySQL treats NULLs as distinct, so the
    declared UNIQUE does not stop a second `food_category_id IS NULL` row.
    The engine's fallback lookup would then pick one nondeterministically —
    the same input returning different numbers, with nothing in the logs.
    A functional index over COALESCE(food_category_id, 0) is what actually
    enforces it."""
    fs, metric = _set(session), _metric(session)
    dest = _downstream_prereqs(session)
    session.add(FactorDownstream(factor_set_id=fs.id, destination_id=dest,
                                 food_category_id=None, metric_id=metric.id,
                                 value_per_kg=Decimal("1.0")))
    session.flush()
    session.add(FactorDownstream(factor_set_id=fs.id, destination_id=dest,
                                 food_category_id=None, metric_id=metric.id,
                                 value_per_kg=Decimal("2.0")))

    with pytest.raises(IntegrityError):
        session.flush()


def test_a_specific_and_a_generic_downstream_row_coexist(session):
    """The lookup order is exact match, then the NULL row, then zero — so
    both must be insertable for the same (destination, metric)."""
    fs, metric = _set(session), _metric(session)
    dest = _downstream_prereqs(session)
    cat = FoodCategory(code="dairy", name="Dairy")
    session.add(cat)
    session.flush()

    session.add(FactorDownstream(factor_set_id=fs.id, destination_id=dest,
                                 food_category_id=None, metric_id=metric.id,
                                 value_per_kg=Decimal("1.0")))
    session.add(FactorDownstream(factor_set_id=fs.id, destination_id=dest,
                                 food_category_id=cat.id, metric_id=metric.id,
                                 value_per_kg=Decimal("2.0")))
    session.flush()

    assert len(session.scalars(select(FactorDownstream)).all()) == 2


def test_an_upstream_factor_is_unique_per_combination(session):
    """Both rows leave `destination_id` NULL, which is the generic case — so
    since v1.8 this is the functional index below doing the work, not the
    declared UNIQUE. It stays here as the canary: delete
    `uq_factor_upstream_generic` and this is the first test to notice."""
    fs, metric = _set(session), _metric(session)
    sector = Sector(code="processing", name="Processing")
    cat = FoodCategory(code="dairy", name="Dairy")
    session.add_all([sector, cat])
    session.flush()

    for _ in range(2):
        session.add(FactorUpstream(
            factor_set_id=fs.id, sector_id=sector.id, food_category_id=cat.id,
            metric_id=metric.id, value_per_kg=Decimal("1.9"),
        ))

    with pytest.raises(IntegrityError):
        session.flush()


def _upstream_prereqs(session):
    """A (sector, food_category) pair, and a `prevention` destination."""
    from admin.taxonomy_models import Destination, DestinationGroup

    sector = Sector(code="primary_production", name="Primary production")
    cat = FoodCategory(code="vegetables", name="Vegetables")
    group = DestinationGroup(code="reuse", name="Reuse", is_waste=False)
    session.add_all([sector, cat, group])
    session.flush()
    prevention = Destination(group_id=group.id, code="prevention",
                             name="Prevented — waste avoided")
    session.add(prevention)
    session.flush()
    return sector, cat, prevention


def test_two_generic_upstream_rows_are_refused(session):
    """Contract §2.2 (v1.8), and the same trap B found on `factor_downstream`.

    `destination_id` is nullable — NULL means "every destination for this
    (sector, food_category, metric)" — and MySQL treats NULLs as distinct
    inside a UNIQUE key, so the declared UNIQUE is silent on exactly the rows
    it most needs to guard. Two of them would make the upstream fallback pick
    one nondeterministically: the same input returning different numbers, with
    nothing in the logs. The functional index over COALESCE(destination_id, 0)
    is what actually enforces it."""
    fs, metric = _set(session), _metric(session)
    sector, cat, _ = _upstream_prereqs(session)
    session.add(FactorUpstream(
        factor_set_id=fs.id, sector_id=sector.id, food_category_id=cat.id,
        destination_id=None, metric_id=metric.id, value_per_kg=Decimal("0.45"),
    ))
    session.flush()
    session.add(FactorUpstream(
        factor_set_id=fs.id, sector_id=sector.id, food_category_id=cat.id,
        destination_id=None, metric_id=metric.id, value_per_kg=Decimal("0.90"),
    ))

    with pytest.raises(IntegrityError):
        session.flush()


def test_two_upstream_rows_for_the_same_destination_are_refused(session):
    """The declared UNIQUE's own job, once `destination_id` is not NULL."""
    fs, metric = _set(session), _metric(session)
    sector, cat, prevention = _upstream_prereqs(session)
    for value in ("0", "0.45"):
        session.add(FactorUpstream(
            factor_set_id=fs.id, sector_id=sector.id, food_category_id=cat.id,
            destination_id=prevention.id, metric_id=metric.id,
            value_per_kg=Decimal(value),
        ))

    with pytest.raises(IntegrityError):
        session.flush()


def test_a_destination_specific_and_a_generic_upstream_row_coexist(session):
    """The lookup order is exact destination, then the NULL row, then zero —
    so both must be insertable for the same (sector, food_category, metric).

    This pair is O-7: `prevention` carries an upstream row of its own at zero,
    and every other destination falls through to the general one. Without it a
    line moved to `prevention` kept its full upstream burden and the calculator
    understated the benefit of wasting less by most of its value."""
    fs, metric = _set(session), _metric(session)
    sector, cat, prevention = _upstream_prereqs(session)
    session.add_all([
        FactorUpstream(
            factor_set_id=fs.id, sector_id=sector.id, food_category_id=cat.id,
            destination_id=None, metric_id=metric.id,
            value_per_kg=Decimal("0.4500000000"),
        ),
        FactorUpstream(
            factor_set_id=fs.id, sector_id=sector.id, food_category_id=cat.id,
            destination_id=prevention.id, metric_id=metric.id,
            value_per_kg=Decimal("0.0000000000"),
        ),
    ])
    session.flush()

    rows = session.scalars(select(FactorUpstream)).all()
    assert len(rows) == 2
    assert {row.destination_id for row in rows} == {None, prevention.id}


def test_an_upstream_row_is_generic_by_default(session):
    """`destination_id` is nullable and defaults to NULL, so every upstream row
    written before v1.8 keeps meaning exactly what it meant: every destination."""
    fs, metric = _set(session), _metric(session)
    sector, cat, _ = _upstream_prereqs(session)
    session.add(FactorUpstream(
        factor_set_id=fs.id, sector_id=sector.id, food_category_id=cat.id,
        metric_id=metric.id, value_per_kg=Decimal("0.45"),
    ))
    session.flush()

    assert session.scalar(select(FactorUpstream)).destination_id is None


def test_a_constant_is_unique_per_set(session):
    fs = _set(session)
    session.add(Constant(factor_set_id=fs.id, code="GWP_CH4_100",
                         value=Decimal("29.8")))
    session.flush()
    session.add(Constant(factor_set_id=fs.id, code="GWP_CH4_100",
                         value=Decimal("27.0")))

    with pytest.raises(IntegrityError):
        session.flush()


def test_the_same_constant_code_may_exist_in_two_sets(session):
    """Versions are independent: a new set revising GWP_CH4_100 must not
    collide with the published one it was cloned from."""
    first, second = _set(session, "2026-Q3"), _set(session, "2026-Q4")
    session.add(Constant(factor_set_id=first.id, code="GWP_CH4_100",
                         value=Decimal("29.8")))
    session.add(Constant(factor_set_id=second.id, code="GWP_CH4_100",
                         value=Decimal("27.0")))
    session.flush()

    assert len(session.scalars(select(Constant)).all()) == 2


def test_one_formula_per_metric_per_set(session):
    fs, metric = _set(session), _metric(session)
    session.add(Formula(factor_set_id=fs.id, metric_id=metric.id,
                        expression="qty_kg * (upstream + downstream)"))
    session.flush()
    session.add(Formula(factor_set_id=fs.id, metric_id=metric.id,
                        expression="qty_kg"))

    with pytest.raises(IntegrityError):
        session.flush()


def test_a_version_label_is_unique(session):
    _set(session, "2026-Q3")
    session.add(FactorSet(version_label="2026-Q3", status=FactorSetStatus.draft,
                          is_mock=True))

    with pytest.raises(IntegrityError):
        session.flush()


def test_a_factor_row_can_record_where_its_number_came_from(session):
    """Contract §2.2 (v1.1). The client has not supplied real factors yet,
    and the Otago baseline says quality varies by an order of magnitude —
    primary-production loss rates are largely borrowed from Australian
    figures. `is_mock` sits on the whole set and cannot express "these forty
    rows are measured and those twelve are proxies".

    Both columns are nullable: they exist now, empty, so that real data
    arrives as an import rather than a migration.
    """
    fs, metric = _set(session), _metric(session)
    dest = _downstream_prereqs(session)
    session.add(FactorDownstream(
        factor_set_id=fs.id, destination_id=dest, food_category_id=None,
        metric_id=metric.id, value_per_kg=Decimal("1.0"),
        source_note="MfE 2023 waste levy schedule, table 4",
        data_quality="proxy-AU",
    ))
    session.flush()

    row = session.scalar(select(FactorDownstream))
    assert row.data_quality == "proxy-AU"
    assert "MfE 2023" in row.source_note


def test_provenance_may_be_left_empty(session):
    """Nothing is known about the real factors yet, so a row without
    provenance must still be insertable — otherwise seeding mock data would
    require inventing sources for it."""
    fs, metric = _set(session), _metric(session)
    dest = _downstream_prereqs(session)
    session.add(FactorDownstream(
        factor_set_id=fs.id, destination_id=dest, food_category_id=None,
        metric_id=metric.id, value_per_kg=Decimal("1.0"),
    ))
    session.flush()

    row = session.scalar(select(FactorDownstream))
    assert row.source_note is None
    assert row.data_quality is None


def test_an_equivalence_carries_its_own_label_template(session):
    """Contract §2.1's metric note applies here too: a fourth equivalence
    added by staff must appear with no code change, so the label lives in
    the row rather than in a template."""
    fs, metric = _set(session), _metric(session)
    session.add(Equivalence(
        factor_set_id=fs.id, code="km_driven", name="Kilometres driven",
        source_metric_id=metric.id, value_per_unit=Decimal("0.192"),
        label_template="Equivalent to driving {value} km",
    ))
    session.flush()

    assert "{value}" in session.scalar(select(Equivalence)).label_template
