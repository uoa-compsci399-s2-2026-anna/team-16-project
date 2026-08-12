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
    # Every editable field carries help text; tests/admin/test_field_help.py
    # fails until a new one does. See admin/taxonomy_views.py's note on the
    # register these are written in.
    form_args = {
        "code": {"description": (
            "The short name for this saved test case. Lower case, no spaces. "
            "Scenarios never leave the panel, so no member of the public "
            "ever sees it."
        )},
        "name": {"description": (
            "What this scenario is for, in words — the heading it appears "
            "under on the comparison screen. 'Household, mixed, mostly "
            "landfill' tells the next person why it is worth running; "
            "'Scenario 3' does not."
        )},
        "sector": {"description": (
            "The supply-chain stage this scenario is run for. It decides "
            "which upstream factors the comparison exercises, so a set of "
            "scenarios that all name one sector leaves the rest untested."
        )},
        "food_category": {"description": (
            "Leave blank for the standard mix, which is what a visitor who "
            "does not know their composition gets — and so the case most "
            "worth having a scenario for."
        )},
        "gwp_horizon": {
            "choices": _GWP_HORIZON_CHOICES,
            "coerce": int,
            "description": (
                "Which methane time horizon to run this scenario at. The "
                "20-year value weights methane several times more heavily "
                "than the 100-year one, so the same formula and the same "
                "factors give different greenhouse-gas figures on each. "
                "Visitors can pick either, so it is worth having a scenario "
                "on each."
            ),
        },
        "sort_order": {"description": (
            "Order on the comparison screen, lowest first; equal values fall "
            "back to the order the rows were created in."
        )},
        "active": {"description": (
            "Only ticked scenarios are run by the pre-publish comparison. "
            "Untick one that is no longer worth checking rather than "
            "deleting it, so the reason it existed is not lost."
        )},
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
    form_args = {
        "scenario": {"description": (
            "Which saved test case this line belongs to. Deleting a scenario "
            "deletes its lines with it."
        )},
        "destination": {"description": (
            "Where this line's waste goes. A scenario cannot name the same "
            "destination twice — put the whole quantity for a destination on "
            "one line."
        )},
        "qty_kg": {"description": (
            "Kilograms on this line. Stored to three decimal places, the "
            "same limit the public calculator accepts, so anything finer is "
            "lost. A scenario is a saved request: what you enter here is "
            "what gets sent when the comparison runs."
        )},
    }
    column_filters = [
        ForeignKeyFilter(ComparisonScenarioLine.scenario_id, ComparisonScenario.code,
                         title="Scenario"),
    ]
