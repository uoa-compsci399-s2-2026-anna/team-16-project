"""Every model needs a human-readable ``__str__``.

Without one, sqladmin falls back to the default ``repr`` for any
relationship it renders - in list columns, on details pages, and in every
select box on every form, where a staff member currently has to choose
between rows that all read ``<admin.factor_models.FactorSet object at
0x...>``.

No database is needed here: these are plain, transient (never
session-attached) instances built in memory, and ``__str__`` on such an
instance touches no lazy-loaded attribute that was not set explicitly below.

Two tiers:

1. ``test_every_model_defines_its_own___str__`` - a cheap, no-instance-
   construction safety net. It fails the moment a new model is added to any
   of the four model modules without a ``__str__`` override, before anyone
   has to remember to also add a factory below.
2. ``test_str_identifies_the_row`` - checks an actual instance of every
   model: the rendered string names the row a human would use to find it,
   never contains a Python object address, never contains a primary key
   where a `code` (or equivalent human identifier) already exists, and never
   contains a secret column's value.

``_FACTORIES`` is keyed by class and covers every model that exists today.
``ALL_MAPPED_CLASSES`` is collected from ``Base.registry`` rather than
hand-listed, so a model added later shows up as a new parametrised case
automatically - tier 1 fails it outright, and tier 2 fails it with a clear
"no factory registered" message rather than silently skipping it.
"""

from datetime import datetime
from decimal import Decimal

import pytest

import admin.comparison_models  # noqa: F401 - registers its tables on Base.metadata
import admin.factor_models  # noqa: F401
import admin.models  # noqa: F401
import admin.taxonomy_models  # noqa: F401
import db.blocklist_models  # noqa: F401 - registers ip_block on Base.metadata
import db.models  # noqa: F401 - registers submission and submission_line
from admin.comparison_models import ComparisonScenario, ComparisonScenarioLine
from admin.factor_models import (
    Constant, Equivalence, FactorDownstream, FactorSet, FactorUpstream, Formula,
)
from admin.models import AuditLog, Staff, StaffRecoveryCode
from admin.taxonomy_models import (
    Destination, DestinationGroup, FoodCategory, Metric, Sector, UnitPreset,
)
from db.base import Base
from db.blocklist_models import IpBlock
from db.models import Scenario, Submission, SubmissionLine

ALL_MAPPED_CLASSES = sorted(
    (m.class_ for m in Base.registry.mappers), key=lambda c: c.__name__
)


def _case(factory, identifying, forbidden):
    """factory: zero-arg callable building one instance.
    identifying: substrings that must appear in str(instance).
    forbidden: substrings (secrets, primary keys) that must not."""
    return {"factory": factory, "identifying": identifying, "forbidden": forbidden}


_FACTORIES = {
    Staff: _case(
        lambda: Staff(
            id=424242, username="alice_str_test", display_name="Alice Str",
            password_hash="SECRET_PASSWORD_HASH_VALUE",
        ),
        identifying=["alice_str_test"],
        forbidden=["SECRET_PASSWORD_HASH_VALUE", "424242"],
    ),
    StaffRecoveryCode: _case(
        lambda: StaffRecoveryCode(id=555, staff_id=1, code_hash="SECRET_CODE_HASH_VALUE"),
        identifying=["555"],
        forbidden=["SECRET_CODE_HASH_VALUE"],
    ),
    AuditLog: _case(
        lambda: AuditLog(id=1, actor="staff1", action="update",
                         table_name="factor_set", row_id=77),
        identifying=["update", "factor_set", "77"],
        forbidden=[],
    ),
    DestinationGroup: _case(
        lambda: DestinationGroup(id=9001, code="grp_code", name="Group Name", is_waste=True),
        identifying=["grp_code", "Group Name"],
        forbidden=["9001"],
    ),
    Destination: _case(
        lambda: Destination(
            id=9002, code="dest_code", name="Dest Name",
            group=DestinationGroup(code="g", name="G", is_waste=True),
        ),
        identifying=["dest_code", "Dest Name"],
        forbidden=["9002"],
    ),
    Sector: _case(
        lambda: Sector(id=9003, code="sect_code", name="Sector Name"),
        identifying=["sect_code", "Sector Name"],
        forbidden=["9003"],
    ),
    FoodCategory: _case(
        lambda: FoodCategory(id=9004, code="food_code", name="Food Name"),
        identifying=["food_code", "Food Name"],
        forbidden=["9004"],
    ),
    Metric: _case(
        lambda: Metric(id=9005, code="metric_code", name="Metric Name", unit="kg"),
        identifying=["metric_code", "Metric Name"],
        forbidden=["9005"],
    ),
    UnitPreset: _case(
        lambda: UnitPreset(id=9006, code="preset_code", label="Preset Label",
                           kg_per_unit=Decimal("2.5")),
        identifying=["preset_code", "Preset Label"],
        forbidden=["9006"],
    ),
    FactorSet: _case(
        lambda: FactorSet(id=9007, version_label="v1.2.3"),
        identifying=["v1.2.3"],
        forbidden=["9007"],
    ),
    FactorUpstream: _case(
        lambda: FactorUpstream(
            id=9008, value_per_kg=Decimal("1"),
            sector=Sector(code="up_sector", name="S"),
            food_category=FoodCategory(code="up_cat", name="C"),
            metric=Metric(code="up_metric", name="M", unit="kg"),
            factor_set=FactorSet(version_label="fs1"),
        ),
        identifying=["up_sector", "up_cat", "up_metric"],
        forbidden=["9008"],
    ),
    FactorDownstream: _case(
        lambda: FactorDownstream(
            id=9009, value_per_kg=Decimal("-1"),
            destination=Destination(
                code="down_dest", name="D",
                group=DestinationGroup(code="g2", name="G2", is_waste=True),
            ),
            food_category=FoodCategory(code="down_cat", name="C2"),
            metric=Metric(code="down_metric", name="M2", unit="kg"),
            factor_set=FactorSet(version_label="fs2"),
        ),
        identifying=["down_dest", "down_cat", "down_metric"],
        forbidden=["9009"],
    ),
    Constant: _case(
        lambda: Constant(id=9010, code="GWP_CH4_100", value=Decimal("29.8"),
                         factor_set=FactorSet(version_label="fs3")),
        identifying=["GWP_CH4_100"],
        forbidden=["9010"],
    ),
    Formula: _case(
        lambda: Formula(
            id=9011, expression="qty_kg",
            metric=Metric(code="formula_metric", name="FM", unit="kg"),
            factor_set=FactorSet(version_label="fs4"),
        ),
        identifying=["formula_metric"],
        forbidden=["9011"],
    ),
    Equivalence: _case(
        lambda: Equivalence(
            id=9012, code="km_driven", name="Kilometres driven",
            value_per_unit=Decimal("1"), label_template="x",
            source_metric=Metric(code="eq_metric", name="EM", unit="kg"),
            factor_set=FactorSet(version_label="fs5"),
        ),
        identifying=["km_driven", "Kilometres driven"],
        forbidden=["9012"],
    ),
    ComparisonScenario: _case(
        lambda: ComparisonScenario(
            id=9013, code="scenario_code", name="Scenario Name",
            sector=Sector(code="cs_sector", name="S2"),
        ),
        identifying=["scenario_code", "Scenario Name"],
        forbidden=["9013"],
    ),
    ComparisonScenarioLine: _case(
        lambda: ComparisonScenarioLine(
            id=9014, qty_kg=Decimal("12.500"),
            destination=Destination(
                code="line_dest", name="LD",
                group=DestinationGroup(code="g3", name="G3", is_waste=True),
            ),
        ),
        identifying=["line_dest", "12.500"],
        forbidden=["9014"],
    ),
    #: db/models.py's two tables, which arrived with B's branch. This module
    #: collects ALL_MAPPED_CLASSES off Base.registry precisely so that models
    #: added later show up here automatically, and these two did.
    Submission: _case(
        lambda: Submission(
            id=9016, token="4d0f8e1e-0000-4000-8000-000000000000",
            created_at=datetime(2026, 1, 1, 12, 0), gwp_horizon=100,
        ),
        identifying=["9016", "2026-01-01"],
        forbidden=["4d0f8e1e-0000-4000-8000-000000000000"],
    ),
    SubmissionLine: _case(
        lambda: SubmissionLine(
            id=9017, scenario=Scenario.current, qty_kg=Decimal("12.500"),
        ),
        identifying=["current", "12.500"],
        forbidden=["9017"],
    ),
    IpBlock: _case(
        lambda: IpBlock(
            id=9015, ip_hmac="f" * 64, reason="scripted traffic",
            created_at=datetime(2026, 1, 1, 12, 0), created_by="kim",
            expires_at=datetime(2026, 1, 1, 12, 30),
        ),
        identifying=["scripted traffic", "2026-01-01"],
        forbidden=["9015", "f" * 64],
    ),
}


@pytest.mark.parametrize("cls", ALL_MAPPED_CLASSES, ids=lambda c: c.__name__)
def test_every_model_defines_its_own___str__(cls):
    """The cheap safety net: fails the instant a model is added without a
    ``__str__`` override, before anyone constructs an instance of it."""
    assert "__str__" in cls.__dict__, (
        f"{cls.__name__} has no __str__ of its own - sqladmin will render it "
        "as a Python object address in every relationship column and select box."
    )


@pytest.mark.parametrize("cls", ALL_MAPPED_CLASSES, ids=lambda c: c.__name__)
def test_str_identifies_the_row(cls):
    case = _FACTORIES.get(cls)
    assert case is not None, (
        f"{cls.__name__} is a mapped model with no test case in "
        f"tests/admin/test_model_str.py's _FACTORIES - add one alongside its "
        f"__str__ implementation."
    )
    instance = case["factory"]()

    text = str(instance)

    assert "object at 0x" not in text
    for token in case["identifying"]:
        assert token in text, f"{cls.__name__}.__str__() == {text!r} is missing {token!r}"
    for token in case["forbidden"]:
        assert token not in text, f"{cls.__name__}.__str__() == {text!r} leaks {token!r}"


def test_downstream_generic_row_names_the_destination_without_a_category():
    """food_category_id is nullable and means 'applies to every food
    category for this destination' (e.g. the NZ waste levy) - __str__ must
    say something sensible rather than blow up on a None relationship."""
    row = FactorDownstream(
        value_per_kg=Decimal("0.5"),
        destination=Destination(
            code="levy_dest", name="Levy Destination",
            group=DestinationGroup(code="g4", name="G4", is_waste=True),
        ),
        food_category=None,
        metric=Metric(code="levy_metric", name="Levy Metric", unit="kg"),
        factor_set=FactorSet(version_label="fs6"),
    )

    text = str(row)

    assert "levy_dest" in text
    assert "levy_metric" in text
    assert "object at 0x" not in text
