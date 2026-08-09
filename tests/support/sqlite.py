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


class FakeEngineAdapter:
    """Stands in for A's engine, and therefore for §3's domain objects.

    **It straddles the v1.2 boundary on purpose, and that is the state of the
    integration, not a shortcut.** `make_request` returns the §3 request the
    repository now writes — a `CalculationRequest` carrying `entries`, each an
    `EntryInput` with its own sector, food category and scenario lines.
    `calculate` still returns the pre-v1.2 *wire* result, with `current` and
    `alternative` at the top level, because `api/schemas.py`, the real
    `api/engine_adapter.py` and `tests/fixtures/calculate_response*.json` are
    all still single-entry: reshaping those is Task 4 (§6.2), and this file
    may not reach into `api/`.

    Mapping the single-entry payload to a one-element `entries` is the correct
    §3 conversion for a request that carries one entry, so nothing here is
    faked away — but note that **`api/engine_adapter.py`'s
    `DefaultEngineAdapter.make_request` still builds the deleted
    `ScenarioInput`** and would raise against the real engine. These tests
    inject this adapter, so they cannot see that. Task 4 closes it.
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

        entry = SimpleNamespace(
            sector_code=payload.sector,
            food_category_code=payload.food_category,
            current=lines(payload.current),
            alternative=lines(payload.alternative),
        )
        return SimpleNamespace(entries=(entry,), gwp_horizon=payload.gwp_horizon)

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

        def scenario_result(pick):
            total = sum(
                (
                    line.qty_kg
                    for entry in request.entries
                    for line in (pick(entry) or ())
                ),
                Decimal("0"),
            )
            return {"total_kg": str(total), "metrics": {}, "equivalences": []}

        return {
            "factor_set": {
                "version_label": bundle.data["version_label"],
                "is_mock": bundle.data["is_mock"],
            },
            "gwp_horizon": request.gwp_horizon,
            "current": scenario_result(lambda entry: entry.current),
            "alternative": scenario_result(lambda entry: entry.alternative)
            if has_alternative
            else None,
            "net_benefit": {} if has_alternative else None,
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
