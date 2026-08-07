"""The two comparison-scenario screens. Contract §8.2.

Both inherit AuditedModelView, so every write is audited without either of
them saying so, and both set `column_details_list` explicitly - its default
is every mapped column, which is what once rendered a bcrypt hash and an
encrypted TOTP secret on a details page (see AuditedModelView's own
docstring).

**Unlike the taxonomy screens, these allow delete** - neither
`can_delete = False` is set here, so sqladmin's default (`True`) stands.
That is deliberate, not an oversight: a scenario is a staff member's own
saved test case for the pre-publish comparison view, referenced by nothing
else in the schema. Deleting one strands no historical result, unlike a
taxonomy row or a published factor set's own factor rows, both of which a
submission or another factor_set may still point at. Do not "fix" this by
copying the taxonomy screens' `can_delete = False` - the two situations are
not the same.
"""

from sqladmin.filters import BooleanFilter, ForeignKeyFilter
from wtforms import SelectField

from admin.comparison_models import ComparisonScenario, ComparisonScenarioLine
from admin.modelviews import AuditedModelView

_CATEGORY = "Comparison"

#: §6.2's own choice, mirrored by comparison_models.py's CHECK constraint.
_GWP_HORIZON_CHOICES = [(20, "20 years"), (100, "100 years")]


class ComparisonScenarioAdmin(AuditedModelView, model=ComparisonScenario):
    name = "Comparison scenario"
    name_plural = "Comparison scenarios"
    category = _CATEGORY
    icon = "fa-solid fa-scale-unbalanced"

    column_list = [
        ComparisonScenario.code, ComparisonScenario.name, ComparisonScenario.sector,
        ComparisonScenario.food_category, ComparisonScenario.gwp_horizon,
        ComparisonScenario.sort_order, ComparisonScenario.active,
    ]
    column_details_list = [
        ComparisonScenario.code, ComparisonScenario.name, ComparisonScenario.sector,
        ComparisonScenario.food_category, ComparisonScenario.gwp_horizon,
        ComparisonScenario.sort_order, ComparisonScenario.active,
        ComparisonScenario.lines,
    ]
    form_columns = [
        ComparisonScenario.code, ComparisonScenario.name, ComparisonScenario.sector,
        ComparisonScenario.food_category, ComparisonScenario.gwp_horizon,
        ComparisonScenario.sort_order, ComparisonScenario.active,
    ]
    column_searchable_list = [ComparisonScenario.code, ComparisonScenario.name]
    column_filters = [BooleanFilter(ComparisonScenario.active)]
    column_default_sort = ("sort_order", False)

    # A free IntegerField let staff type anything - 57 sailed past the form
    # and landed on MySQL's own CHECK-violation text (error 3819). A
    # two-option select can't be typed into wrong.
    form_overrides = {"gwp_horizon": SelectField}
    form_args = {
        "gwp_horizon": {"choices": _GWP_HORIZON_CHOICES, "coerce": int},
    }


class ComparisonScenarioLineAdmin(AuditedModelView, model=ComparisonScenarioLine):
    name = "Comparison scenario line"
    name_plural = "Comparison scenario lines"
    category = _CATEGORY
    icon = "fa-solid fa-list"

    column_list = [
        ComparisonScenarioLine.scenario, ComparisonScenarioLine.destination,
        ComparisonScenarioLine.qty_kg,
    ]
    column_details_list = [
        ComparisonScenarioLine.scenario, ComparisonScenarioLine.destination,
        ComparisonScenarioLine.qty_kg,
    ]
    form_columns = [
        ComparisonScenarioLine.scenario, ComparisonScenarioLine.destination,
        ComparisonScenarioLine.qty_kg,
    ]
    column_filters = [
        ForeignKeyFilter(ComparisonScenarioLine.scenario_id, ComparisonScenario.code,
                         title="Scenario"),
    ]
