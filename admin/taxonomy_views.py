"""The six taxonomy screens. Contract §8.1.

Each inherits AuditedModelView, so every write is audited without any of
them saying so. Two of them override validate_before_commit to hold an
invariant that spans rows; see admin/taxonomy_rules.py.

None of them allows delete. Every one of these tables carries `active`, and
a row a historical submission refers to has to stay resolvable - a deleted
destination turns a stored result into a dangling reference. Deactivating is
how a row leaves service.

A raised TaxonomyInvariantError needs no extra handling here: sqladmin
0.30's own edit route (sqladmin/application.py's `edit`) already wraps the
call to `update_model` in a bare `except Exception`, sets `context["error"]
= str(e)`, and re-renders `edit.html` (which prints `{{ error }}` into an
alert div) with a 400 status instead of letting the exception become a 500.
Verified directly against the installed 0.30.0 rather than assumed - see
tests/admin/test_taxonomy_views.py and task-3-report.md.
"""

from sqladmin.filters import BooleanFilter, OperationColumnFilter

from admin.modelviews import AuditedModelView
from admin.taxonomy_models import (
    Destination, DestinationGroup, FoodCategory, Metric, Sector, UnitPreset,
)
from admin.taxonomy_rules import check_prevention_intact, check_single_standard_mix

_CATEGORY = "Taxonomy"


class DestinationGroupAdmin(AuditedModelView, model=DestinationGroup):
    name = "Destination group"
    name_plural = "Destination groups"
    category = _CATEGORY
    icon = "fa-solid fa-layer-group"

    can_delete = False

    column_list = [
        DestinationGroup.code, DestinationGroup.name, DestinationGroup.is_waste,
        DestinationGroup.sort_order, DestinationGroup.active,
    ]
    column_details_list = column_list
    form_columns = [
        DestinationGroup.code, DestinationGroup.name, DestinationGroup.is_waste,
        DestinationGroup.sort_order, DestinationGroup.active,
    ]
    column_searchable_list = [DestinationGroup.code, DestinationGroup.name]
    column_filters = [BooleanFilter(DestinationGroup.is_waste),
                      BooleanFilter(DestinationGroup.active)]
    column_default_sort = ("sort_order", False)

    def validate_before_commit(self, session) -> None:
        """`prevention`'s group must survive every edit made through this
        screen - deactivating the group takes `prevention` out of service
        just as surely as deactivating `prevention` itself would."""
        check_prevention_intact(session)


class DestinationAdmin(AuditedModelView, model=Destination):
    name = "Destination"
    name_plural = "Destinations"
    category = _CATEGORY
    icon = "fa-solid fa-truck-arrow-right"

    can_delete = False

    column_list = [
        Destination.code, Destination.name, Destination.group,
        Destination.sort_order, Destination.active,
    ]
    column_details_list = [
        Destination.code, Destination.name, Destination.description,
        Destination.group, Destination.sort_order, Destination.active,
    ]
    form_columns = [
        Destination.group, Destination.code, Destination.name,
        Destination.description, Destination.sort_order, Destination.active,
    ]
    column_searchable_list = [Destination.code, Destination.name]
    column_filters = [OperationColumnFilter(Destination.code),
                      BooleanFilter(Destination.active)]
    column_default_sort = ("sort_order", False)

    def validate_before_commit(self, session) -> None:
        """`prevention` must survive every edit made through this screen."""
        check_prevention_intact(session)


class SectorAdmin(AuditedModelView, model=Sector):
    name = "Sector"
    name_plural = "Sectors"
    category = _CATEGORY
    icon = "fa-solid fa-industry"

    can_delete = False

    column_list = [Sector.code, Sector.name, Sector.sort_order, Sector.active]
    column_details_list = [
        Sector.code, Sector.name, Sector.description, Sector.sort_order, Sector.active,
    ]
    form_columns = [
        Sector.code, Sector.name, Sector.description, Sector.sort_order, Sector.active,
    ]
    column_searchable_list = [Sector.code, Sector.name]
    column_filters = [BooleanFilter(Sector.active)]
    column_default_sort = ("sort_order", False)


class FoodCategoryAdmin(AuditedModelView, model=FoodCategory):
    name = "Food category"
    name_plural = "Food categories"
    category = _CATEGORY
    icon = "fa-solid fa-carrot"

    can_delete = False

    column_list = [
        FoodCategory.code, FoodCategory.name, FoodCategory.is_standard_mix,
        FoodCategory.sort_order, FoodCategory.active,
    ]
    column_details_list = column_list
    form_columns = [
        FoodCategory.code, FoodCategory.name, FoodCategory.is_standard_mix,
        FoodCategory.sort_order, FoodCategory.active,
    ]
    column_searchable_list = [FoodCategory.code, FoodCategory.name]
    column_filters = [BooleanFilter(FoodCategory.is_standard_mix),
                      BooleanFilter(FoodCategory.active)]
    column_default_sort = ("sort_order", False)

    def validate_before_commit(self, session) -> None:
        """Exactly one active category is the standard mix. Contract §2.1."""
        check_single_standard_mix(session)


class MetricAdmin(AuditedModelView, model=Metric):
    name = "Metric"
    name_plural = "Metrics"
    category = _CATEGORY
    icon = "fa-solid fa-chart-simple"

    can_delete = False

    column_list = [
        Metric.code, Metric.name, Metric.unit, Metric.display_unit,
        Metric.display_precision, Metric.sort_order, Metric.active,
    ]
    column_details_list = column_list
    form_columns = [
        Metric.code, Metric.name, Metric.unit, Metric.display_unit,
        Metric.display_precision, Metric.sort_order, Metric.active,
    ]
    column_searchable_list = [Metric.code, Metric.name]
    column_filters = [BooleanFilter(Metric.active)]
    column_default_sort = ("sort_order", False)


class UnitPresetAdmin(AuditedModelView, model=UnitPreset):
    name = "Unit preset"
    name_plural = "Unit presets"
    category = _CATEGORY
    icon = "fa-solid fa-scale-balanced"

    can_delete = False

    column_list = [
        UnitPreset.code, UnitPreset.label, UnitPreset.food_category,
        UnitPreset.kg_per_unit, UnitPreset.active,
    ]
    column_details_list = [
        UnitPreset.code, UnitPreset.label, UnitPreset.food_category,
        UnitPreset.kg_per_unit, UnitPreset.source_note, UnitPreset.active,
    ]
    form_columns = [
        UnitPreset.code, UnitPreset.label, UnitPreset.food_category,
        UnitPreset.kg_per_unit, UnitPreset.source_note, UnitPreset.active,
    ]
    column_searchable_list = [UnitPreset.code, UnitPreset.label]
    column_filters = [BooleanFilter(UnitPreset.active)]
