"""B's SQLite fixtures, for the repository and public-API contract tests.

Registered as a pytest plugin from `tests/conftest.py`
(`pytest_plugins = ["tests.support.sqlite"]`), so `sqlite_engine`,
`seeded_session` and `app` are available anywhere in the suite regardless of
which directory a test file sits in.

These were `tests/conftest.py` on the `database` branch, where the first two
were called `engine` and `session`. The repository-wide `tests/conftest.py`
defines fixtures of exactly those names with the opposite semantics — a
session-scoped MySQL scratch database with per-test transaction rollback,
rather than a function-scoped in-memory SQLite with a seeded, committed
session. Both are wanted, and whichever name won, the other suite failed
outright. Renaming B's is what makes registering them from the root possible
at all; before that, the only way to keep both was to scope hers to a
subdirectory, which quietly made the directory a test lived in determine
which database engine it ran against.

**Two engines are in play across this suite, and a test file's location no
longer tells you which one it uses — its fixtures do.** `sqlite_engine`,
`seeded_session` and `app` are SQLite and need no Docker. `engine`, `session`,
`admin_app` and `client` (all in `tests/conftest.py`) are real MySQL.

`tests/test_mysql_integration.py` is separate from both: the
COALESCE(food_category_id, 0) functional unique index it proves has no meaning
on SQLite, which is why B isolated it in the first place.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db.base import Base
from db.models import (
    Constant,
    Destination,
    DestinationGroup,
    FactorDownstream,
    FactorSet,
    FactorSetStatus,
    FactorUpstream,
    FoodCategory,
    Formula,
    Metric,
    Sector,
)
from db.repository import invalidate_factor_bundle


class FakeBundle:
    def __init__(self, data):
        self.data = data

    def validate(self):
        return self.data.get("_problems", [])


class UnknownCodeError(Exception):
    pass


class FormulaError(Exception):
    def __init__(self) -> None:
        super().__init__("division by zero in qty_kg / zero")
        self.expression = "qty_kg / zero"
        self.line = 1
        self.column = 8
        self.reason = "division by zero"


def _scenario_result(lines, *, with_breakdown):
    """A §3 `ScenarioResult`, with a deliberately trivial `co2e`.

    The single metric's total is the scenario's mass, so no factor
    arithmetic is faked here — the numbers in these tests are the ones the
    request carried. What the metric exists for is *shape*: without it the
    §6.2 body has an empty `metrics` object and nothing exercises the
    `by_destination` mapping, the `metric_code`/`destination_code` renames,
    or their omission at the totals level.
    """
    total = sum((line.qty_kg for line in lines), Decimal("0"))
    metric = SimpleNamespace(
        metric_code="co2e",
        unit="kg CO2e",
        display_precision=1,
        total=total,
        by_destination=tuple(
            SimpleNamespace(
                destination_code=line.destination_code,
                qty_kg=line.qty_kg,
                upstream=Decimal("0.0000000000"),
                downstream=Decimal("0.0000000000"),
                value=line.qty_kg,
            )
            for line in lines
        )
        if with_breakdown
        else (),
    )
    return SimpleNamespace(
        total_kg=total,
        metrics={"co2e": metric},
        equivalences=(
            SimpleNamespace(
                code="km_driven",
                label="Equivalent to driving 0 km",
                value=Decimal("0.0000000000"),
                source_metric_code="co2e",
            ),
        ),
    )


class FakeEngineAdapter:
    """Stands in for A's engine, and therefore for §3's domain objects.

    `make_request` returns the §3 request the repository writes — a
    `CalculationRequest` carrying `entries`, each an `EntryInput` with its
    own sector, food category and scenario lines — and `calculate` returns
    the §3 `CalculationResult`: `factor_set_version`, `is_mock`,
    `gwp_horizon`, `totals` and `entries`, with `by_destination` populated
    per entry and empty at the totals level (§3 rule 2).

    **`serialize_result` is not faked.** It delegates to the real
    `DefaultEngineAdapter`, so every API test that reads a 200 body is
    exercising the mapping in `api/engine_adapter.py` rather than a
    hand-written dict that happens to agree with the fixtures. That mapping
    is the piece with no other production caller until A delivers.
    """

    def bundle_from_json(self, data):
        return FakeBundle(data)

    def make_request(self, payload):
        def lines(rows):
            if rows is None:
                return None
            return tuple(
                SimpleNamespace(destination_code=x.destination, qty_kg=x.qty_kg)
                for x in rows
            )

        return SimpleNamespace(
            entries=tuple(
                SimpleNamespace(
                    sector_code=entry.sector,
                    food_category_code=entry.food_category,
                    current=lines(entry.current),
                    alternative=lines(entry.alternative),
                )
                for entry in payload.entries
            ),
            gwp_horizon=payload.gwp_horizon,
        )

    def calculate(self, request, bundle):
        if bundle.data.get("_raise_formula"):
            raise FormulaError()
        destinations = {row["code"] for row in bundle.data.get("destinations", [])}
        for entry in request.entries:
            for scenario in (entry.current, entry.alternative):
                if scenario is None:
                    continue
                if any(line.destination_code not in destinations for line in scenario):
                    raise UnknownCodeError("unknown destination")

        has_alternative = any(entry.alternative is not None for entry in request.entries)

        entries = tuple(
            SimpleNamespace(
                sector_code=entry.sector_code,
                food_category_code=entry.food_category_code,
                current=_scenario_result(entry.current, with_breakdown=True),
                alternative=_scenario_result(entry.alternative, with_breakdown=True)
                if entry.alternative is not None
                else None,
                net_benefit=_net_benefit(entry.current, entry.alternative)
                if entry.alternative is not None
                else None,
            )
            for entry in request.entries
        )

        # §3 rule 3: an entry with no alternative contributes its *current*
        # lines to the rolled-up alternative, so its net benefit is exactly
        # zero and the totals stay mass-conserving.
        current_lines = [line for entry in request.entries for line in entry.current]
        alternative_lines = [
            line
            for entry in request.entries
            for line in (entry.alternative if entry.alternative is not None else entry.current)
        ]
        totals_current = _scenario_result(current_lines, with_breakdown=False)
        totals_alternative = (
            _scenario_result(alternative_lines, with_breakdown=False)
            if has_alternative
            else None
        )
        totals = SimpleNamespace(
            current=totals_current,
            alternative=totals_alternative,
            net_benefit={
                "co2e": totals_current.metrics["co2e"].total
                - totals_alternative.metrics["co2e"].total
            }
            if has_alternative
            else None,
        )
        return SimpleNamespace(
            factor_set_version=bundle.data["version_label"],
            is_mock=bundle.data["is_mock"],
            gwp_horizon=request.gwp_horizon,
            totals=totals,
            entries=entries,
        )

    def serialize_result(self, result):
        from api.engine_adapter import DefaultEngineAdapter

        return DefaultEngineAdapter().serialize_result(result)


def _net_benefit(current, alternative):
    mass = sum((line.qty_kg for line in current), Decimal("0"))
    other = sum((line.qty_kg for line in alternative), Decimal("0"))
    return {"co2e": mass - other}


@pytest.fixture
def sqlite_engine():
    invalidate_factor_bundle()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    invalidate_factor_bundle()
    engine.dispose()


@pytest.fixture
def seeded_session(sqlite_engine):
    factory = sessionmaker(bind=sqlite_engine, expire_on_commit=False)
    with factory() as db:
        seed(db)
        db.commit()
        yield db


def seed(db):
    group = DestinationGroup(code="disposal", name="Disposal", is_waste=True)
    sector = Sector(code="processing", name="Processing")
    standard = FoodCategory(code="standard_mix", name="Standard mix", is_standard_mix=True)
    dairy = FoodCategory(code="dairy", name="Dairy")
    metric = Metric(code="co2e", name="Greenhouse gas", unit="kg CO2e", display_precision=1)
    db.add_all([group, sector, standard, dairy, metric])
    db.flush()
    landfill = Destination(group_id=group.id, code="landfill", name="Landfill")
    published = FactorSet(
        version_label="MOCK-v0", status=FactorSetStatus.published, is_mock=True
    )
    draft = FactorSet(version_label="DRAFT-v1", status=FactorSetStatus.draft, is_mock=True)
    db.add_all([landfill, published, draft])
    db.flush()
    for factor_set in (published, draft):
        db.add_all([
            FactorUpstream(
                factor_set_id=factor_set.id,
                sector_id=sector.id,
                food_category_id=dairy.id,
                metric_id=metric.id,
                value_per_kg=Decimal("1.9"),
            ),
            FactorDownstream(
                factor_set_id=factor_set.id,
                destination_id=landfill.id,
                food_category_id=dairy.id,
                metric_id=metric.id,
                value_per_kg=Decimal("0.99"),
            ),
            Constant(factor_set_id=factor_set.id, code="GWP_CH4_100", value=Decimal("28")),
            Formula(
                factor_set_id=factor_set.id,
                metric_id=metric.id,
                expression="qty_kg * (upstream + downstream)",
            ),
        ])


@pytest.fixture
def app(sqlite_engine):
    from api.app import create_app

    seeded_session_factory = sessionmaker(bind=sqlite_engine, expire_on_commit=False)
    with seeded_session_factory() as db:
        seed(db)
        db.commit()

    app = create_app(database_url="sqlite+pysqlite:///:memory:", engine_adapter=FakeEngineAdapter())
    original_engine = app.state.session_factory.kw["bind"]
    app.state.session_factory.configure(bind=sqlite_engine)
    original_engine.dispose()
    return app
