"""The factor screens. Contract §8.1.

Every view here inherits AuditedModelView, so each write is audited without
any of them saying so, and each sets column_details_list explicitly because
its default is every mapped column.

Unlike the taxonomy screens, these allow delete: a factor row belongs to one
version, versions are cloned rather than edited in place, and a row deleted
from a draft has no historical result pointing at it. A published set is a
different matter — see FactorSetAdmin.

The two high-volume tables carry roughly 270 and 600 rows per factor set, so
every list here filters by factor_set. A staff member editing the wrong
version's number is the failure this prevents, and it is silent.
"""

from sqladmin.filters import BooleanFilter, ForeignKeyFilter, OperationColumnFilter
from sqlalchemy import select

from admin.expressions import ExpressionError, validate_expression
from admin.factor_models import (
    Constant, Equivalence, FactorDownstream, FactorSet, FactorUpstream, Formula,
)
from admin.modelviews import AuditedModelView
from admin.taxonomy_rules import TaxonomyInvariantError, check_single_published_set

_CATEGORY = "Factors"


class FactorUpstreamAdmin(AuditedModelView, model=FactorUpstream):
    name = "Upstream factor"
    name_plural = "Upstream factors"
    category = _CATEGORY
    icon = "fa-solid fa-seedling"

    column_list = [FactorUpstream.factor_set, FactorUpstream.sector,
                   FactorUpstream.food_category, FactorUpstream.metric,
                   FactorUpstream.value_per_kg, FactorUpstream.data_quality]
    column_details_list = [FactorUpstream.factor_set, FactorUpstream.sector,
                           FactorUpstream.food_category, FactorUpstream.metric,
                           FactorUpstream.value_per_kg,
                           FactorUpstream.data_quality, FactorUpstream.source_note]
    form_columns = [FactorUpstream.factor_set, FactorUpstream.sector,
                    FactorUpstream.food_category, FactorUpstream.metric,
                    FactorUpstream.value_per_kg, FactorUpstream.data_quality,
                    FactorUpstream.source_note]
    column_filters = [
        ForeignKeyFilter(FactorUpstream.factor_set_id, FactorSet.version_label,
                         title="Factor set"),
        OperationColumnFilter(FactorUpstream.data_quality),
    ]
    column_sortable_list = [FactorUpstream.value_per_kg]
    page_size = 100


class FactorDownstreamAdmin(AuditedModelView, model=FactorDownstream):
    """The largest table, and the one with the two traps.

    `food_category` may be empty, meaning "every category for this
    destination" — that is how a per-tonne charge like the waste levy is
    expressed, and the form must allow it rather than requiring a selection.

    `value_per_kg` may be negative: animal feed displaces feed that would
    otherwise have been produced, so the factor is a genuine credit. Nothing
    here may clamp or reject a negative.
    """

    name = "Downstream factor"
    name_plural = "Downstream factors"
    category = _CATEGORY
    icon = "fa-solid fa-truck-arrow-right"

    column_list = [FactorDownstream.factor_set, FactorDownstream.destination,
                   FactorDownstream.food_category, FactorDownstream.metric,
                   FactorDownstream.value_per_kg, FactorDownstream.data_quality]
    column_details_list = [FactorDownstream.factor_set, FactorDownstream.destination,
                           FactorDownstream.food_category, FactorDownstream.metric,
                           FactorDownstream.value_per_kg,
                           FactorDownstream.data_quality,
                           FactorDownstream.source_note]
    form_columns = [FactorDownstream.factor_set, FactorDownstream.destination,
                    FactorDownstream.food_category, FactorDownstream.metric,
                    FactorDownstream.value_per_kg, FactorDownstream.data_quality,
                    FactorDownstream.source_note]
    column_filters = [
        ForeignKeyFilter(FactorDownstream.factor_set_id, FactorSet.version_label,
                         title="Factor set"),
        OperationColumnFilter(FactorDownstream.data_quality),
    ]
    column_sortable_list = [FactorDownstream.value_per_kg]
    page_size = 100


class ConstantAdmin(AuditedModelView, model=Constant):
    name = "Constant"
    name_plural = "Constants"
    category = _CATEGORY
    icon = "fa-solid fa-hashtag"

    column_list = [Constant.factor_set, Constant.code, Constant.value, Constant.unit]
    column_details_list = [Constant.factor_set, Constant.code, Constant.value,
                           Constant.unit, Constant.note]
    form_columns = [Constant.factor_set, Constant.code, Constant.value,
                    Constant.unit, Constant.note]
    column_searchable_list = [Constant.code]
    column_filters = [
        ForeignKeyFilter(Constant.factor_set_id, FactorSet.version_label,
                         title="Factor set"),
    ]
    page_size = 50


class EquivalenceAdmin(AuditedModelView, model=Equivalence):
    """`label_template` is what the public sees, e.g.
    "Equivalent to driving {value} km" — the `{value}` placeholder is
    substituted by the front end, so a template without it renders a sentence
    with no number in it."""

    name = "Equivalence"
    name_plural = "Equivalences"
    category = _CATEGORY
    icon = "fa-solid fa-right-left"

    column_list = [Equivalence.factor_set, Equivalence.code, Equivalence.name,
                   Equivalence.source_metric, Equivalence.value_per_unit,
                   Equivalence.sort_order, Equivalence.active]
    column_details_list = [Equivalence.factor_set, Equivalence.code,
                           Equivalence.name, Equivalence.source_metric,
                           Equivalence.value_per_unit, Equivalence.label_template,
                           Equivalence.source_note, Equivalence.sort_order,
                           Equivalence.active]
    form_columns = [Equivalence.factor_set, Equivalence.code, Equivalence.name,
                    Equivalence.source_metric, Equivalence.value_per_unit,
                    Equivalence.label_template, Equivalence.source_note,
                    Equivalence.sort_order, Equivalence.active]
    column_searchable_list = [Equivalence.code, Equivalence.name]
    column_filters = [
        ForeignKeyFilter(Equivalence.factor_set_id, FactorSet.version_label,
                         title="Factor set"),
        BooleanFilter(Equivalence.active),
    ]
    column_default_sort = ("sort_order", False)
    page_size = 50


class FormulaAdmin(AuditedModelView, model=Formula):
    name = "Formula"
    name_plural = "Formulas"
    category = _CATEGORY
    icon = "fa-solid fa-square-root-variable"

    column_list = [Formula.factor_set, Formula.metric, Formula.expression]
    column_details_list = [Formula.factor_set, Formula.metric, Formula.expression,
                           Formula.notes]
    form_columns = [Formula.factor_set, Formula.metric, Formula.expression,
                    Formula.notes]
    column_filters = [ForeignKeyFilter(Formula.factor_set_id, FactorSet.version_label,
                                       title="Factor set")]
    page_size = 50

    def validate_before_commit(self, session) -> None:
        """Refuse an expression the engine could not run. Contract §4.3.

        Saved broken, a formula surfaces as a FORMULA_ERROR 500 to a member
        of the public on their next calculation — §9.1 exists because of
        exactly that. Catching it here costs one parse.

        Constants resolve per factor set, so the same expression can be valid
        in one version and invalid in another; each pending row is checked
        against its own set's constants.

        NOTE (deviation from task-3-brief.md's Step 3 listing): the brief's
        own text builds `pending` from `session.new` and `session.dirty`.
        AuditedModelView's before_commit listener (admin/modelviews.py)
        calls `session.flush()` immediately before invoking this hook - and
        by design, since a query issued before that flush would not see this
        write's own pending change. But that same flush is what empties
        `session.new` (a freshly inserted row becomes persistent and leaves
        it) and clears `session.dirty` (attribute history resets once it is
        written), for both the create and the update path - confirmed
        directly against this SQLAlchemy: a toy model added, then flushed,
        left both collections empty. Built as the brief describes, `pending`
        is always `[]` by the time this runs, so the loop below never
        executes and every formula is silently accepted, valid or not -
        which is exactly the failure Step 6 is designed to catch, and did,
        against this first draft.

        `session.identity_map` does not have this problem: a row this
        session just inserted or is editing stays in the identity map after
        flush (verified the same way - present for a fresh insert, present
        for an update, and correctly absent for a row this same session just
        deleted, so no separate exclusion is needed there). Because sqladmin
        opens a brand new session per write (Query._insert_sync /
        _update_sync's own `self.model_view.session_maker(...)`, per
        admin/modelviews.py's docstring), the identity map holds nothing
        from any other request - only what this one write loaded or created,
        which for a Formula edit is the Formula row itself plus whatever
        foreign-key lookups the form performed (FactorSet, Metric - filtered
        out below by `isinstance`).
        """
        pending = [obj for obj in session.identity_map.values()
                   if isinstance(obj, Formula)]
        for formula in pending:
            codes = session.scalars(
                select(Constant.code).where(
                    Constant.factor_set_id == formula.factor_set_id
                )
            ).all()
            try:
                validate_expression(formula.expression, constant_codes=codes)
            except ExpressionError as exc:
                raise TaxonomyInvariantError(
                    f"Line {exc.line}, column {exc.column}: {exc.message}"
                ) from exc


class FactorSetAdmin(AuditedModelView, model=FactorSet):
    """Contract §8.2. Clone, publish and roll back arrive in the next stage;
    this shows the versions and holds the one-published invariant."""

    name = "Factor set"
    name_plural = "Factor sets"
    category = _CATEGORY
    icon = "fa-solid fa-layer-group"

    # Every submission stamps the set it was calculated against, so a
    # deleted set strands every historical result naming it. Archiving is
    # how a version leaves service.
    can_delete = False

    column_list = [FactorSet.version_label, FactorSet.status, FactorSet.is_mock,
                   FactorSet.published_at, FactorSet.published_by]
    column_details_list = [FactorSet.version_label, FactorSet.status,
                           FactorSet.is_mock, FactorSet.effective_from,
                           FactorSet.published_at, FactorSet.published_by,
                           FactorSet.notes]
    # published_at/published_by are absent deliberately: they are stamped by
    # the publish action in E-6, and a staff member typing them by hand would
    # make the audit trail disagree with itself.
    form_columns = [FactorSet.version_label, FactorSet.status, FactorSet.is_mock,
                    FactorSet.effective_from, FactorSet.notes]
    column_default_sort = ("id", True)

    def validate_before_commit(self, session) -> None:
        """At most one published set. Contract §2.2."""
        check_single_published_set(session)
