"""`equivalence`'s two v1.71 CHECK constraints, against real MySQL.

**This file exists because `tests/test_migrations.py` cannot see these.** Its
own docstring records the blind spot: on this SQLAlchemy/MySQL combination
`compare_metadata` sees a missing UNIQUE constraint and **not** a missing
CHECK. So `ck_equivalence_band_needs_family` and `ck_equivalence_band_ordered`
could be deleted from `alembic/versions/0019_equivalence_ladder.py`, or from
`admin/factor_models.py`, and the drift gate would stay green. The only thing
that can tell is a write that the database has to refuse.

It uses the `session` fixture from `tests/conftest.py` — the `kaicalc_test`
scratch database on real MySQL — and not `seeded_session` from
`tests/support/sqlite.py`: SQLite enforces CHECK constraints too, but it is
not the engine the deployment runs and the point of this file is the
deployment's own refusal.
"""

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from db.models import Equivalence, FactorSet, FactorSetStatus, Metric


def _prereqs(session):
    """The factor set and metric an equivalence has to point at."""
    factor_set = FactorSet(
        version_label="BAND-TEST", status=FactorSetStatus.draft, is_mock=True
    )
    metric = Metric(
        code="band_water", name="Water", unit="L", display_unit="L",
        display_precision=0, sort_order=10,
    )
    session.add_all([factor_set, metric])
    session.flush()
    return factor_set, metric


def _equivalence(factor_set, metric, **overrides):
    row = dict(
        factor_set_id=factor_set.id,
        code="band_showers",
        name="Ten-minute showers",
        source_metric_id=metric.id,
        value_per_unit=Decimal("0.0111111111"),
        label_template="Equivalent to {value} showers",
        sort_order=10,
    )
    row.update(overrides)
    return Equivalence(**row)


@pytest.mark.db
def test_a_band_without_a_family_is_refused(session):
    """`ck_equivalence_band_needs_family`. Selection only ever happens within
    a family, so a `min_value` on a family-less row is a rule that can never
    fire. Refused rather than ignored: "I set a minimum and nothing happened"
    is the failure this constraint exists to make unreachable."""
    factor_set, metric = _prereqs(session)
    session.add(
        _equivalence(factor_set, metric, family=None, min_value=Decimal("1"))
    )
    with pytest.raises((IntegrityError, OperationalError)):
        session.flush()


@pytest.mark.db
def test_an_upper_bound_without_a_family_is_refused_too(session):
    """The same constraint's other half. `max_value` alone is as unreachable
    as `min_value` alone, and a check written with only one of them named
    would let this through."""
    factor_set, metric = _prereqs(session)
    session.add(
        _equivalence(factor_set, metric, family=None, max_value=Decimal("1"))
    )
    with pytest.raises((IntegrityError, OperationalError)):
        session.flush()


@pytest.mark.db
def test_a_family_with_no_band_at_all_is_accepted(session):
    """The catch-all rung. A ladder's bottom rung carries no bounds — it is
    what a value that matched no other rung falls to — so the constraint above
    must not require a band merely because a family is named."""
    factor_set, metric = _prereqs(session)
    session.add(_equivalence(factor_set, metric, family="water"))
    session.flush()
    assert session.query(Equivalence).count() == 1


@pytest.mark.db
def test_a_row_with_neither_family_nor_band_is_accepted(session):
    """Every equivalence in every database written before v1.71. This is what
    makes the migration inert: the four columns are NULL on every existing
    row and both constraints are vacuous there."""
    factor_set, metric = _prereqs(session)
    session.add(_equivalence(factor_set, metric))
    session.flush()
    assert session.query(Equivalence).count() == 1


@pytest.mark.db
def test_an_inverted_band_is_refused(session):
    """`ck_equivalence_band_ordered`. `[10, 1)` admits nothing, so the rung
    can never be chosen — which looks exactly like a rung nobody added."""
    factor_set, metric = _prereqs(session)
    session.add(
        _equivalence(
            factor_set, metric, family="water",
            min_value=Decimal("10"), max_value=Decimal("1"),
        )
    )
    with pytest.raises((IntegrityError, OperationalError)):
        session.flush()


@pytest.mark.db
def test_an_empty_band_is_refused(session):
    """`[1, 1)` is empty, because the band is half-open. The constraint is
    `<` rather than `<=` for exactly this row."""
    factor_set, metric = _prereqs(session)
    session.add(
        _equivalence(
            factor_set, metric, family="water",
            min_value=Decimal("1"), max_value=Decimal("1"),
        )
    )
    with pytest.raises((IntegrityError, OperationalError)):
        session.flush()


@pytest.mark.db
def test_a_well_formed_band_is_accepted(session):
    """The shape every rung in the shipped ladders has."""
    factor_set, metric = _prereqs(session)
    session.add(
        _equivalence(
            factor_set, metric, family="water",
            min_value=Decimal("1"), max_value=Decimal("1000"),
        )
    )
    session.flush()
    stored = session.query(Equivalence).one()
    assert stored.family == "water"
    assert stored.min_value == Decimal("1")
    assert stored.max_value == Decimal("1000")
