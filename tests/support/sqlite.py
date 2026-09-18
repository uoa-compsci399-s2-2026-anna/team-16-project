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

from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
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
    Equivalence,
    FactorDownstream,
    FactorSet,
    FactorSetStatus,
    FactorUpstream,
    FoodCategory,
    Formula,
    Metric,
    Sector,
    UnitPreset,
)
from db.repository import invalidate_factor_bundle


class FakeBundle:
    """The bundle dict, plus the two item queries the real `FactorBundle`
    answers (v1.58).

    `DefaultEngineAdapter.food_item_problems` asks a bundle to resolve each
    named food and turns the refusal into a §9 detail with a field. The fake
    adapter delegates that method to the real one -- `serialize_result`
    already does, for the same reason -- so what has to exist here is the
    bundle's side of the question, not a second copy of the rule.

    `resolve_food_item` is `engine/bundle.py`'s, six lines and no arithmetic:
    `None` passes through, a food nobody declared is unknown, and a food whose
    parent is not the category it arrived with is refused rather than priced
    at the wrong average. `tests/support/test_fake_engine_agreement.py` runs
    both implementations over one corpus, which is what stops the copy
    drifting.
    """

    def __init__(self, data):
        self.data = data
        self.food_item_category_of = {
            row["code"]: row["food_category"]
            for row in data.get("food_items", [])
        }

    def validate(self):
        return self.data.get("_problems", [])

    def has_food_item(self, code):
        return code in self.food_item_category_of

    def resolve_food_item(self, food_item, food_cat):
        if food_item is None:
            return None
        if food_item not in self.food_item_category_of:
            raise UnknownCodeError(f"unknown food_item: {food_item!r}")
        parent = self.food_item_category_of[food_item]
        if parent != food_cat:
            raise UnknownCodeError(
                f"food_item {food_item!r} belongs to food_category "
                f"{parent!r}, not {food_cat!r}"
            )
        return food_item


class UnknownCodeError(Exception):
    pass


class FormulaError(Exception):
    def __init__(self) -> None:
        super().__init__("division by zero in qty_kg / zero")
        self.expression = "qty_kg / zero"
        self.line = 1
        self.column = 8
        self.reason = "division by zero"


def _scenario_result(lines, *, with_breakdown, rolled_up=False):
    """A §3 `ScenarioResult`, with a deliberately trivial `co2e`.

    The single metric's total is the scenario's mass, so no factor
    arithmetic is faked here — the numbers in these tests are the ones the
    request carried. What the metric exists for is *shape*: without it the
    §6.2 body has an empty `metrics` object and nothing exercises the
    `by_destination` mapping or the `metric_code`/`destination_code` renames.

    `rolled_up` mirrors `engine.calculate._roll_up` (v1.48, amending §3 rule
    2): at the entry level each line keeps its own row, in request order,
    because two lines to the same destination in one scenario are two
    separate contributions; at the totals level, `lines` is the flattened,
    cross-entry list and rows are merged one per destination, summing
    `qty_kg` (and, since this fake's `co2e` total *is* the mass, `value`
    along with it) and leaving the two rate fields at zero — they are
    per-kilogram rates that can differ between the entries sharing a
    destination, not sums.
    """
    total = sum((line.qty_kg for line in lines), Decimal("0"))
    if not with_breakdown:
        rows: tuple = ()
    elif rolled_up:
        merged: dict[str, Decimal] = {}
        order: list[str] = []
        for line in lines:
            if line.destination_code not in merged:
                merged[line.destination_code] = Decimal("0")
                order.append(line.destination_code)
            merged[line.destination_code] += line.qty_kg
        rows = tuple(
            SimpleNamespace(
                destination_code=code,
                qty_kg=merged[code],
                upstream=Decimal("0.0000000000"),
                downstream=Decimal("0.0000000000"),
                value=merged[code],
            )
            for code in order
        )
    else:
        rows = tuple(
            SimpleNamespace(
                destination_code=line.destination_code,
                qty_kg=line.qty_kg,
                upstream=Decimal("0.0000000000"),
                downstream=Decimal("0.0000000000"),
                value=line.qty_kg,
            )
            for line in lines
        )
    metric = SimpleNamespace(
        metric_code="co2e",
        unit="kg CO2e",
        display_precision=1,
        total=total,
        by_destination=rows,
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
                #: v1.52. `name`/`value_per_unit`/`value_per_unit_display`/
                #: `source_note` mirror the `km_driven` fixture row -- this
                #: fake's `co2e` total is trivial (the docstring above), so
                #: no test asserts these beyond their presence and shape.
                name="Kilometres driven",
                value_per_unit=Decimal("4.1800000000"),
                value_per_unit_display="4.18",
                source_note=(
                    "PLACEHOLDER. Open item O-3 — the New Zealand basis for "
                    "this conversion is not settled. Roughly one kilometre "
                    "of an average light petrol vehicle per 0.24 kg CO2e."
                ),
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
    at both levels (v1.48, amending §3 rule 2) — one row per line per
    entry, merged one row per destination at the totals level.

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
                    food_item_code=entry.food_item,
                    current=lines(entry.current),
                    alternative=lines(entry.alternative),
                    total_input_kg=entry.total_input_kg,
                    total_value_nzd=entry.total_value_nzd,
                    wasted_value_nzd=entry.wasted_value_nzd,
                )
                for entry in payload.entries
            ),
            gwp_horizon=payload.gwp_horizon,
        )

    def food_item_problems(self, payload, bundle):
        """Not faked, for the same reason `serialize_result` is not: the
        mapping from a bundle's refusal to a §9 detail is the piece with no
        other production caller, and a hand-written copy here would let the
        API tests agree with something the API does not do."""
        from api.engine_adapter import DefaultEngineAdapter

        return DefaultEngineAdapter().food_item_problems(payload, bundle)

    def calculate(self, request, bundle):
        if bundle.data.get("_raise_formula"):
            raise FormulaError()
        #: v1.58. The engine resolves the food before it prices a line, and
        #: refusing here is what keeps a route that skipped
        #: `food_item_problems` from silently pricing an incoherent pair.
        standard_mix = next(
            (
                row["code"]
                for row in bundle.data.get("food_categories", [])
                if row.get("is_standard_mix")
            ),
            "",
        )
        for entry in request.entries:
            bundle.resolve_food_item(
                entry.food_item_code, entry.food_category_code or standard_mix
            )
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
                food_item_code=entry.food_item_code,
                current=_scenario_result(entry.current, with_breakdown=True),
                alternative=_scenario_result(entry.alternative, with_breakdown=True)
                if entry.alternative is not None
                else None,
                net_benefit=_net_benefit(entry.current, entry.alternative)
                if entry.alternative is not None
                else None,
                production_share_percent=_share_percent(
                    sum((line.qty_kg for line in entry.current), Decimal("0")),
                    entry.total_input_kg,
                ),
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
        totals_current = _scenario_result(
            current_lines, with_breakdown=True, rolled_up=True
        )
        totals_alternative = (
            _scenario_result(alternative_lines, with_breakdown=True, rolled_up=True)
            if has_alternative
            else None
        )
        money, money_state = _money(request.entries, bundle)
        production_kg, production_state = _across_entries(
            entry.total_input_kg for entry in request.entries
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
            money=money,
            production_share_percent=_share_percent(
                totals_current.total_kg, production_kg
            ),
            data_state=SimpleNamespace(
                production_share_percent=production_state, **money_state
            ),
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


def _money(entries, bundle):
    """A stand-in for engine.calculate._money (§4.5), kept in step with it
    on purpose: fix round 2 found that a fake computing a plausible but
    different number is invisible to every shape-only test in this suite,
    so this mirrors the real per-entry rate, the real exclusion of
    prevention from both sides of diverted_kg, and the real quantisation of
    the two passthrough sums, rather than a simplified stand-in.

    Reads prevention from `bundle.data["destinations"][*]["is_prevention"]`
    -- the real flag `build_bundle_data` now selects (fix round 1) -- rather
    than a literal `"prevention"` string, since a request POSTed through
    this fake goes through the same projection a live request does.
    """
    prevention_codes = {
        row["code"]
        for row in bundle.data.get("destinations", [])
        if row.get("is_prevention")
    }

    def is_prevention(code):
        return code in prevention_codes

    total_value_nzd, total_state = _across_entries(
        entry.total_value_nzd for entry in entries
    )
    wasted_value_nzd, wasted_state = _across_entries(
        entry.wasted_value_nzd for entry in entries
    )
    if total_state == NOT_SUPPLIED and wasted_state == NOT_SUPPLIED:
        return None, {
            "total_value_nzd": NOT_SUPPLIED,
            "wasted_value_nzd": NOT_SUPPLIED,
            "wasted_share_percent": NOT_SUPPLIED,
            "saving_nzd": NOT_SUPPLIED,
        }

    if total_value_nzd is not None:
        total_value_nzd = total_value_nzd.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if wasted_value_nzd is not None:
        wasted_value_nzd = wasted_value_nzd.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    share_state = _combined_state(total_state, wasted_state)
    wasted_share_percent = (
        _share_percent(wasted_value_nzd, total_value_nzd)
        if share_state == COMPLETE
        else None
    )

    saving_nzd = None
    has_alternative = any(entry.alternative is not None for entry in entries)
    saving_state = (
        wasted_state
        if has_alternative and wasted_state != NOT_SUPPLIED
        else NOT_SUPPLIED
    )
    if saving_state == COMPLETE:
        saving_total = Decimal("0")
        any_entry_priced = False
        for entry in entries:
            if entry.wasted_value_nzd is None:
                continue
            entry_current_kg = sum((line.qty_kg for line in entry.current), Decimal("0"))
            if entry_current_kg == 0:
                continue
            value_per_kg = entry.wasted_value_nzd / entry_current_kg
            alternative_lines = (
                entry.alternative if entry.alternative is not None else entry.current
            )
            current_non_prevention_kg = sum(
                (
                    line.qty_kg
                    for line in entry.current
                    if not is_prevention(line.destination_code)
                ),
                Decimal("0"),
            )
            alternative_non_prevention_kg = sum(
                (
                    line.qty_kg
                    for line in alternative_lines
                    if not is_prevention(line.destination_code)
                ),
                Decimal("0"),
            )
            diverted_kg = current_non_prevention_kg - alternative_non_prevention_kg
            saving_total += value_per_kg * diverted_kg
            any_entry_priced = True
        if any_entry_priced:
            saving_nzd = saving_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return (
        SimpleNamespace(
            total_value_nzd=total_value_nzd,
            wasted_value_nzd=wasted_value_nzd,
            wasted_share_percent=wasted_share_percent,
            saving_nzd=saving_nzd,
        ),
        {
            "total_value_nzd": total_state,
            "wasted_value_nzd": wasted_state,
            "wasted_share_percent": share_state,
            "saving_nzd": saving_state,
        },
    )


#: The three states of §4.6, re-typed as literals for the same reason
#: `Decimal("0.01")` and `ROUND_HALF_UP` are: this file is a hand-kept copy
#: that `tests/support/test_fake_engine_agreement.py` compares against the
#: real engine, and importing the real constants would let a rename in
#: `engine/types.py` pass unnoticed.
COMPLETE = "complete"
INCOMPLETE = "incomplete"
NOT_SUPPLIED = "not_supplied"


def _across_entries(values):
    """A stand-in for engine.calculate._across_entries (§4.6): the sum only
    when every entry supplied the input, and otherwise which of the two kinds
    of absence it is."""
    values = list(values)
    present = [value for value in values if value is not None]
    if not present:
        return None, NOT_SUPPLIED
    if len(present) != len(values):
        return None, INCOMPLETE
    return sum(present, Decimal("0")), COMPLETE


def _combined_state(*states):
    if NOT_SUPPLIED in states:
        return NOT_SUPPLIED
    if INCOMPLETE in states:
        return INCOMPLETE
    return COMPLETE


def _share_percent(part, whole):
    if part is None or whole is None or whole == 0:
        return None
    return (part / whole * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


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
    """A taxonomy wide enough to POST `tests/fixtures/calculate_request.json`.

    It was three codes — `processing`, `dairy`, `landfill` — until the
    canonical fixture set landed. A fixture the contract tests cannot send to
    the API is a fixture nothing checks, and §10 makes those files the
    executable form of the contract: the request sample now spans two sectors,
    two food categories and five destinations across all three MfE groups,
    including `prevention`, so the seed has to know those codes or every
    fixture-driven assertion degrades into a 400.

    Codes, names, groups and sort orders are `admin/seed.py`'s — the shipped
    New Zealand taxonomy — abridged to the rows the fixtures use. Deliberately
    *not* invented here: `code` is the cross-layer identifier (§1.1), and a
    test seed that spells a destination differently from the production seed
    proves the API works against codes no deployment will ever hold.
    """
    reuse = DestinationGroup(code="reuse", name="Reuse", is_waste=False, sort_order=10)
    recovery = DestinationGroup(
        code="recycle_recovery", name="Recycle and recovery", is_waste=True, sort_order=20
    )
    disposal = DestinationGroup(
        code="disposal", name="Disposal", is_waste=True, sort_order=30
    )
    sector = Sector(code="processing", name="Processing and manufacturing", sort_order=20)
    primary = Sector(code="primary_production", name="Primary production", sort_order=10)
    hospitality = Sector(
        code="consumer_hospitality", name="Hospitality", sort_order=50
    )
    standard = FoodCategory(
        code="standard_mix",
        name="Mixed food waste (composition unknown)",
        is_standard_mix=True,
        sort_order=5,
    )
    vegetables = FoodCategory(code="vegetables", name="Vegetables", sort_order=20)
    dairy = FoodCategory(code="dairy", name="Dairy", sort_order=60)
    metric = Metric(
        code="co2e",
        name="Greenhouse gases",
        unit="kg CO2e",
        display_precision=1,
        sort_order=10,
    )
    db.add_all([
        reuse, recovery, disposal,
        sector, primary, hospitality,
        standard, vegetables, dairy,
        metric,
    ])
    db.flush()
    landfill = Destination(
        group_id=disposal.id, code="landfill", name="Landfill", sort_order=110
    )
    prevention = Destination(
        group_id=reuse.id,
        code="prevention",
        name="Prevented — waste avoided",
        #: §2.1. The role is the flag, not the code — `admin/seed.py` sets it
        #: on the same row for the same reason. A seed that carried the code
        #: and not the tick would leave §6.2's guard with nothing to refuse.
        is_prevention=True,
        sort_order=5,
    )
    destinations = [
        landfill,
        prevention,
        Destination(
            group_id=reuse.id, code="animal_feed", name="Animal feed", sort_order=30
        ),
        Destination(
            group_id=recovery.id,
            code="compost",
            name="Composting (aerobic digestion)",
            sort_order=40,
        ),
        Destination(
            group_id=recovery.id,
            code="anaerobic_digestion",
            name="Anaerobic digestion",
            sort_order=50,
        ),
        Destination(
            group_id=recovery.id,
            code="not_harvested",
            name="Not harvested or ploughed in",
            sort_order=70,
        ),
    ]
    #: §6.1 carries `unit_presets`, and the canonical `taxonomy.json` is no
    #: longer allowed to leave that array empty, so the contract test needs a
    #: row to compare against.
    preset = UnitPreset(
        code="bucket_20l_full",
        label="20 L bucket (full)",
        food_category_id=None,
        kg_per_unit=Decimal("6.0000"),
    )
    published = FactorSet(
        version_label="MOCK-v0",
        status=FactorSetStatus.published,
        is_mock=True,
        #: §6.3 exports `published_at`, and `factors.json` carries a timestamp
        #: there. A published set with no publication date is not a state the
        #: lifecycle produces (§5.2), so the seed should not model one.
        published_at=datetime(2026, 8, 5, 2, 0, tzinfo=timezone.utc),
    )
    draft = FactorSet(version_label="DRAFT-v1", status=FactorSetStatus.draft, is_mock=True)
    db.add_all([*destinations, preset, published, draft])
    db.flush()
    for factor_set in (published, draft):
        db.add_all([
            FactorUpstream(
                factor_set_id=factor_set.id,
                sector_id=sector.id,
                food_category_id=dairy.id,
                #: The generic row of §2.2 (v1.8): null destination means
                #: "every destination that has none of its own".
                destination_id=None,
                metric_id=metric.id,
                value_per_kg=Decimal("1.9"),
            ),
            #: O-7. `prevention` is a 100% offset, and it is *this* row that
            #: makes it one: food that was never wasted was never produced,
            #: so none of the upstream burden is attributable to it. Without
            #: it a line moved to `prevention` kept the full 1.9 and the
            #: calculator understated the benefit of wasting less.
            FactorUpstream(
                factor_set_id=factor_set.id,
                sector_id=sector.id,
                food_category_id=dairy.id,
                destination_id=prevention.id,
                metric_id=metric.id,
                value_per_kg=Decimal("0"),
            ),
            FactorDownstream(
                factor_set_id=factor_set.id,
                destination_id=landfill.id,
                food_category_id=dairy.id,
                metric_id=metric.id,
                value_per_kg=Decimal("0.99"),
            ),
            FactorDownstream(
                factor_set_id=factor_set.id,
                destination_id=landfill.id,
                #: The generic row of §2.2: null food_category means "every
                #: category that has none of its own". `factors.json` publishes
                #: the pair, so the export has both to serialise.
                food_category_id=None,
                metric_id=metric.id,
                value_per_kg=Decimal("0.70"),
            ),
            Constant(factor_set_id=factor_set.id, code="GWP_CH4_100", value=Decimal("28")),
            Formula(
                factor_set_id=factor_set.id,
                metric_id=metric.id,
                expression="qty_kg * (upstream + downstream)",
            ),
            #: §6.3 publishes `equivalences`, and an all-empty export is what
            #: made the methodology page render "No published formulas were
            #: returned" in every demo.
            Equivalence(
                factor_set_id=factor_set.id,
                code="km_driven",
                name="Kilometres driven",
                source_metric_id=metric.id,
                value_per_unit=Decimal("4.18"),
                label_template="Equivalent to driving {value} km",
                sort_order=10,
            ),
        ])


#: The `SECRET_KEY` every app built by the `app` fixture derives its §2.3
#: blocklist fingerprint and its §6.5 rate-limit key from. Passed explicitly
#: rather than left to `os.getenv`, so that this suite never depends on a
#: developer's `.env` being present — `api.app.create_app` now requires a
#: secret key the same way it requires a database URL, because an API that
#: cannot compute a fingerprint is an API where a block made in the panel
#: silently does nothing.
API_TEST_SECRET_KEY = "test-secret-key-not-used-anywhere-real"


@pytest.fixture
def app(sqlite_engine):
    from api.app import create_app

    seeded_session_factory = sessionmaker(bind=sqlite_engine, expire_on_commit=False)
    with seeded_session_factory() as db:
        seed(db)
        db.commit()

    app = create_app(
        database_url="sqlite+pysqlite:///:memory:",
        secret_key=API_TEST_SECRET_KEY,
        engine_adapter=FakeEngineAdapter(),
    )
    original_engine = app.state.session_factory.kw["bind"]
    app.state.session_factory.configure(bind=sqlite_engine)
    original_engine.dispose()
    return app
