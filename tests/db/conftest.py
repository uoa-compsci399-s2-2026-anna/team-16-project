"""B's SQLite fixtures, for the repository and public-API contract tests.

These were `tests/conftest.py` on the `database` branch, where they defined
fixtures called `engine` and `session`. The repository-wide `tests/conftest.py`
already defines fixtures of exactly those two names with the opposite
semantics — a session-scoped MySQL scratch database with per-test transaction
rollback, rather than a function-scoped in-memory SQLite with a seeded,
committed session. Both are wanted, and whichever name won, the other suite
failed outright, so B's are renamed `sqlite_engine` and `seeded_session` and
scoped to this directory.

`tests/test_mysql_integration.py` stays at the top level and stays on real
MySQL: the COALESCE(food_category_id, 0) functional unique index it proves has
no meaning on SQLite, which is why B separated it in the first place.
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


class FakeEngineAdapter:
    def bundle_from_json(self, data):
        return FakeBundle(data)

    def make_request(self, payload):
        def scenario(lines):
            if lines is None:
                return None
            return SimpleNamespace(
                sector_code=payload.sector,
                food_category_code=payload.food_category,
                lines=tuple(
                    SimpleNamespace(destination_code=x.destination, qty_kg=x.qty_kg)
                    for x in lines
                ),
            )
        return SimpleNamespace(
            current=scenario(payload.current),
            alternative=scenario(payload.alternative),
            gwp_horizon=payload.gwp_horizon,
        )

    def calculate(self, request, bundle):
        if bundle.data.get("_raise_formula"):
            raise FormulaError()
        destinations = {row["code"] for row in bundle.data.get("destinations", [])}
        for scenario in (request.current, request.alternative):
            if scenario is None:
                continue
            if any(line.destination_code not in destinations for line in scenario.lines):
                raise UnknownCodeError("unknown destination")

        def scenario_result(scenario):
            if scenario is None:
                return None
            total = sum((line.qty_kg for line in scenario.lines), Decimal("0"))
            return {"total_kg": str(total), "metrics": {}, "equivalences": []}

        return {
            "factor_set": {
                "version_label": bundle.data["version_label"],
                "is_mock": bundle.data["is_mock"],
            },
            "gwp_horizon": request.gwp_horizon,
            "current": scenario_result(request.current),
            "alternative": scenario_result(request.alternative),
            "net_benefit": {} if request.alternative is not None else None,
        }

    def serialize_result(self, result):
        return result


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
