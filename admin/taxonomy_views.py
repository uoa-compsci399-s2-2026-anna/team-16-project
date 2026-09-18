"""The seven taxonomy screens. Contract §8.1.

Each inherits AuditedModelView, so every write is audited without any of
them saying so. Two of them override validate_before_commit to hold an
invariant that spans rows; see admin/taxonomy_rules.py.

None of them allows delete. Every one of these tables carries `active`, and
a row a historical submission refers to has to stay resolvable - a deleted
destination turns a stored result into a dangling reference. Deactivating is
how a row leaves service - which is what _TaxonomyAdmin's bulk deactivate/
activate actions below are for.

A raised TaxonomyInvariantError needs no extra handling on the ordinary
single-row edit path: sqladmin 0.30's own edit route
(sqladmin/application.py's `edit`) already wraps the call to `update_model`
in a bare `except Exception`, sets `context["error"] = str(e)`, and
re-renders `edit.html` (which prints `{{ error }}` into an alert div) with a
400 status instead of letting the exception become a 500. Verified directly
against the installed 0.30.0 rather than assumed - see
tests/admin/test_taxonomy_views.py and task-3-report.md. The bulk actions
below are a different route entirely (a plain Starlette route added via
`@action`, not one of sqladmin's own wrapped handlers) and catch it
themselves - see _TaxonomyAdmin._bulk_set_active.
"""

from sqladmin import action
from sqladmin.filters import BooleanFilter, OperationColumnFilter
from starlette.exceptions import HTTPException
from starlette.responses import RedirectResponse

from admin.audit import row_to_dict, write_audit
from admin.auth import SESSION_KEY
from admin.modelviews import described, AuditedModelView
from admin.taxonomy_models import (
    Destination, DestinationGroup, FoodCategory, FoodItem, Metric, Sector,
    UnitPreset,
)
from admin.taxonomy_rules import (
    TaxonomyInvariantError, check_prevention_destination,
    check_single_standard_mix,
)

_CATEGORY = "Taxonomy"

#: Every editable field on this panel carries help text, and
#: tests/admin/test_field_help.py fails until a new one does. The register is
#: "what does this mean, and what happens if I get it wrong", written for
#: somebody who has never seen this system - not a restatement of the label,
#: which teaches a reader that the help text is not worth reading.
#:
#: These two are shared because they genuinely say the same thing on all seven
#: tables (`FoodItemAdmin` extends `_ACTIVE_HELP` with one more sentence rather
#: than restating it, which is why its own key is a separate string). Everything else below is written per field: `code` on `metric` and
#: `code` on `destination` are not the same field with a different name.
_SORT_ORDER_HELP = (
    "Position in the list a visitor sees, lowest first. Rows sharing a "
    "number fall back to alphabetical order by code, so leave gaps between "
    "the values if you expect to slot something in later."
)
_ACTIVE_HELP = (
    "Untick to retire this row. It stays in the database and anything that "
    "already points at it — a stored result, an existing factor — keeps "
    "resolving, but it disappears from the calculator and from the lists "
    "this panel offers when you build a new row. Nothing on these screens "
    "can be deleted; this is how a row leaves service."
)


class _TaxonomyAdmin(AuditedModelView):
    """Common base for the seven taxonomy screens below: the bulk deactivate/
    activate actions that make each screen's Actions button live.

    **The defect this class fixes.** sqladmin's own
    `templates/sqladmin/list.html` disables the Actions dropdown unless
    `model_view.can_delete` or `model_view._custom_actions_in_list` is
    truthy:

        <button {% if not model_view.can_delete and not
        model_view._custom_actions_in_list %} disabled {% endif %}

    Every one of the seven views below sets `can_delete = False` deliberately
    - a destination named by a stored historical result must stay
    resolvable, so `active` is how a row leaves service, not delete - and,
    before this class existed, defined no custom action either. The button
    was therefore correctly disabled and permanently useless: not a bug,
    the absence of a feature. `active` is exactly what these two actions are
    for, and with 14 destinations and 10 food categories, opening each
    row's own edit form to flip one checkbox is the wrong shape for
    retiring several at once.

    **Genuinely bulk, unlike FactorSetAdmin's actions.** clone/publish/
    rollback/archive (admin/factor_views.py) refuse more than one selected
    id outright, via `_one_pk` - "published" is a property of one row at a
    time, and silently acting on the first id of several would leave a
    staff member believing the action covered everything they ticked. That
    reasoning does not apply here: a staff member selecting eight
    destinations to retire together is the point, so `_selected_pks` below
    keeps every id rather than refusing past the first.

    **The trap.** An `@action` inherits neither auditing nor the invariant
    guard from AuditedModelView - documented directly in
    admin/modelviews.py: `_actor_var`/`_view_var` are set only inside
    insert_model/update_model/delete_model, so the `before_commit` listener
    installed by `_audited_session_maker` returns early for a commit an
    `@action` makes itself, and `validate_before_commit` never runs. Built
    naively (`for pk in pks: row.active = False; session.commit()`), this
    would let a staff member deactivate every destination flagged
    `is_prevention` - the row an alternative scenario moves mass to in order
    to express "waste avoided", without which that scenario cannot be built at
    all - or the destination group containing them
    (`check_prevention_destination` already checks the group's own `active`,
    since every active-destination listing joins through it), or
    the last active standard-mix food category (what the calculator falls
    back to when a visitor does not know their waste composition, which is
    most visitors).

    So `_bulk_set_active` below applies every change in the batch first,
    flushes, and only then calls this subclass's own
    `_bulk_invariant_check` - the same function its `validate_before_commit`
    already calls for the ordinary single-row edit path, there is exactly
    one rule per table, not two. A raised TaxonomyInvariantError rolls the
    *entire* batch back before anything is audited or committed: a partial
    application that left some of the batch changed and some not would be
    worse than refusing outright, indistinguishable on the page from a
    complete one.

    **Auditing is explicit too**, one `write_audit` call per row actually
    changed - the same pattern admin/accounts_view.py's own
    `deactivate_action`/`reset_mfa_action` already use for exactly this
    reason (contract §8.1: every admin write produces an audit_log entry,
    and an action that skips it is invisible in /admin/audit).
    """

    #: The cross-row invariant this table's bulk actions must not break, or
    #: None for the three tables with no such invariant (Sector, Metric,
    #: UnitPreset). Set with `staticmethod(...)` on the three subclasses
    #: that need one, below - a plain function assigned directly would bind
    #: through the descriptor protocol and receive `self` as its first
    #: (and only) positional argument instead of `session`.
    _bulk_invariant_check: staticmethod | None = None

    def _require_accessible(self, request) -> None:
        if not self.is_accessible(request):
            raise HTTPException(status_code=403)

    def _list_url(self, request):
        return request.url_for("admin:list", identity=self.identity)

    def _selected_pks(self, request) -> list[int]:
        """Every id named in `pks`, or raise TaxonomyInvariantError naming
        why not. Unlike FactorSetAdmin's own `_one_pk`
        (admin/factor_views.py), every id is kept rather than refusing past
        the first - see this class's own docstring for why these actions
        are deliberately bulk."""
        raw = [pk for pk in request.query_params.get("pks", "").split(",") if pk]
        if not raw:
            raise TaxonomyInvariantError("No rows were selected.")
        ids = []
        for pk in raw:
            try:
                ids.append(int(pk))
            except ValueError:
                raise TaxonomyInvariantError(f"'{pk}' is not a valid id.")
        return ids

    async def _refused(self, request, message: str):
        """Contract §8.2/§9.1's shared refusal rendering, same template and
        shape as FactorSetAdmin's own `_refused` (admin/factor_views.py)."""
        return await self.templates.TemplateResponse(
            request, "brand/action_refused.html",
            {
                "message": message,
                "next_url": self._list_url(request),
                "link_text": f"Back to {self.name_plural.lower()}",
            },
            status_code=400,
        )

    async def _bulk_set_active(self, request, *, active: bool):
        """Shared body of `deactivate_action`/`activate_action` below - see
        this class's own docstring for what each step guards against."""
        self._require_accessible(request)
        actor = request.session.get(SESSION_KEY, "unknown")
        try:
            pks = self._selected_pks(request)
        except TaxonomyInvariantError as exc:
            return await self._refused(request, str(exc))

        with self.session_maker() as session:
            rows = [
                row for row in (session.get(self.model, pk) for pk in pks)
                if row is not None
            ]

            before_by_id = {row.id: row_to_dict(row) for row in rows}
            for row in rows:
                row.active = active
            session.flush()

            if self._bulk_invariant_check is not None:
                try:
                    self._bulk_invariant_check(session)
                except TaxonomyInvariantError as exc:
                    session.rollback()
                    return await self._refused(request, str(exc))

            for row in rows:
                write_audit(
                    session, actor=actor, action="update",
                    table_name=self.model.__tablename__, row_id=row.id,
                    before=before_by_id[row.id], after=row_to_dict(row),
                )
            session.commit()
        return RedirectResponse(self._list_url(request), status_code=302)

    @action(
        name="deactivate",
        label=described(
            "Deactivate",
            "Drops the selected rows out of the calculator's own lists and this panel's forms. Anything that already refers to one - a historical result, for instance - still resolves it.",
        ),
        confirmation_message=(
            "This deactivates every selected row. A deactivated row stays "
            "resolvable by anything that already refers to it - a historical "
            "result, for instance - but drops out of every active-only "
            "listing the calculator and this panel's own forms use."
        ),
    )
    async def deactivate_action(self, request):
        return await self._bulk_set_active(request, active=False)

    @action(
        name="activate",
        label=described(
            "Activate",
            "Puts the selected rows back into the calculator's lists and this panel's forms, so they can be chosen again.",
        ),
        confirmation_message="This activates every selected row.",
    )
    async def activate_action(self, request):
        return await self._bulk_set_active(request, active=True)


class DestinationGroupAdmin(_TaxonomyAdmin, model=DestinationGroup):
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
    form_args = {
        "code": {"description": (
            "The short name the API and the front end use for this group — "
            "'reuse', 'recycle_recovery', 'disposal'. Lower case, no spaces. "
            "Destinations reference their group by row, not by this text, so "
            "renaming it does not detach them."
        )},
        "name": {"description": (
            "The wording a visitor reads. The code above is never shown to "
            "the public; this is."
        )},
        "is_waste": {"description": (
            "Whether destinations in this group count as waste under the "
            "Ministry for the Environment's definition — reuse does not, the "
            "rest do. It is published with the taxonomy so the public site "
            "can separate waste from diversion. It is a setting rather than "
            "a rule in the code because the definition is expected to move: "
            "the 2025 Otago baseline already recommends reclassifying "
            "bioprocessing from waste to reuse."
        )},
        "sort_order": {"description": _SORT_ORDER_HELP},
        "active": {"description": (
            _ACTIVE_HELP + " Retiring a group takes every destination in it "
            "out of service too, because the calculator lists destinations by "
            "joining through their group."
        )},
    }
    column_searchable_list = [DestinationGroup.code, DestinationGroup.name]
    column_filters = [BooleanFilter(DestinationGroup.is_waste),
                      BooleanFilter(DestinationGroup.active)]
    column_default_sort = ("sort_order", False)

    #: Same rule the bulk deactivate action below must not break either -
    #: see _TaxonomyAdmin's own docstring.
    _bulk_invariant_check = staticmethod(check_prevention_destination)

    def validate_before_commit(self, session) -> None:
        """A prevention destination's group must survive every edit made
        through this screen - deactivating the group takes the destination out
        of service just as surely as deactivating the destination would."""
        check_prevention_destination(session)


class DestinationAdmin(_TaxonomyAdmin, model=Destination):
    name = "Destination"
    name_plural = "Destinations"
    category = _CATEGORY
    icon = "fa-solid fa-truck-arrow-right"

    can_delete = False

    column_list = [
        Destination.code, Destination.name, Destination.group,
        Destination.is_prevention, Destination.sort_order, Destination.active,
    ]
    column_details_list = [
        Destination.code, Destination.name, Destination.description,
        Destination.group, Destination.is_prevention, Destination.sort_order,
        Destination.active,
    ]
    form_columns = [
        Destination.group, Destination.code, Destination.name,
        Destination.description, Destination.is_prevention,
        Destination.sort_order, Destination.active,
    ]
    form_args = {
        "group": {"description": (
            "Which grouping this destination belongs to, and so whether it "
            "counts as waste. A destination in a retired group is out of "
            "service even while this row is still ticked active."
        )},
        "code": {"description": (
            "The short name the API and the front end use for this "
            "destination — 'landfill', 'compost', 'animal_feed'. Lower case, "
            "no spaces. No code has any special meaning to this system; the "
            "one destination that plays a special part is marked by the "
            "prevention tick below, not by what it is called, so you may "
            "rename any row here freely."
        )},
        "name": {"description": (
            "The wording a visitor picks from on the calculator, and reads "
            "on the results page."
        )},
        "description": {"description": (
            "What actually happens to food sent here, in a sentence or two, "
            "shown to a visitor beside the name. Optional — but a visitor "
            "who cannot tell two destinations apart will guess, and the "
            "guess goes into the numbers."
        )},
        "is_prevention": {"description": (
            "Tick this for the destination that means the waste never "
            "happened at all. It is what an improved scenario moves waste "
            "onto in order to say 'we wasted less', and it is the only way "
            "the calculator can say that while both scenarios still describe "
            "the same total amount. Its factors must all be zero — that zero "
            "is the whole of the saving. At least one destination must carry "
            "this tick and stay active; more than one is allowed, and each "
            "set of factors brings its own. A ticked destination cannot be "
            "chosen for current waste, only for an improved scenario."
        )},
        "sort_order": {"description": _SORT_ORDER_HELP},
        "active": {"description": _ACTIVE_HELP},
    }
    column_searchable_list = [Destination.code, Destination.name]
    column_filters = [OperationColumnFilter(Destination.code),
                      BooleanFilter(Destination.active)]
    column_default_sort = ("sort_order", False)

    #: Same rule the bulk deactivate action below must not break either -
    #: see _TaxonomyAdmin's own docstring.
    _bulk_invariant_check = staticmethod(check_prevention_destination)

    def validate_before_commit(self, session) -> None:
        """A usable prevention destination must survive every edit made
        through this screen."""
        check_prevention_destination(session)


class SectorAdmin(_TaxonomyAdmin, model=Sector):
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
    form_args = {
        "code": {"description": (
            "The short name the API and the front end use for this stage of "
            "the supply chain — 'primary_production', 'processing', "
            "'consumer_household'. Lower case, no spaces. Upstream factors "
            "point at this row rather than at the text, so renaming it does "
            "not detach the numbers filed under it."
        )},
        "name": {"description": (
            "The wording a visitor picks from when they say where in the "
            "supply chain their waste arose."
        )},
        "description": {"description": (
            "The whole of the explanatory text a visitor reads about this "
            "stage — there is only one such field, so the longer wording "
            "that would have gone in a separate 'more detail' panel belongs "
            "here too."
        )},
        "sort_order": {"description": _SORT_ORDER_HELP},
        "active": {"description": _ACTIVE_HELP},
    }
    column_searchable_list = [Sector.code, Sector.name]
    column_filters = [BooleanFilter(Sector.active)]
    column_default_sort = ("sort_order", False)


class FoodCategoryAdmin(_TaxonomyAdmin, model=FoodCategory):
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
    form_args = {
        "code": {"description": (
            "The short name the API and the front end use for this category "
            "— 'bread_bakery', 'standard_mix'. Lower case, no spaces. Factor "
            "rows point at this row rather than at the text, so renaming it "
            "does not detach the numbers filed under it."
        )},
        "name": {"description": (
            "The wording a visitor picks from when they say what kind of "
            "food was wasted."
        )},
        "is_standard_mix": {"description": (
            "Marks the fallback for a visitor who does not know how their "
            "waste breaks down, which is most visitors. Exactly one active "
            "category must carry it: with none, that visitor's calculation "
            "cannot be run at all, and with two it is ambiguous. The panel "
            "refuses any edit — including a bulk deactivation — that would "
            "leave either."
        )},
        "sort_order": {"description": _SORT_ORDER_HELP},
        "active": {"description": (
            _ACTIVE_HELP + " Retiring the standard mix is refused unless "
            "another active category carries it."
        )},
    }
    column_searchable_list = [FoodCategory.code, FoodCategory.name]
    column_filters = [BooleanFilter(FoodCategory.is_standard_mix),
                      BooleanFilter(FoodCategory.active)]
    column_default_sort = ("sort_order", False)

    #: Same rule the bulk deactivate action below must not break either -
    #: see _TaxonomyAdmin's own docstring.
    _bulk_invariant_check = staticmethod(check_single_standard_mix)

    def validate_before_commit(self, session) -> None:
        """Exactly one active category is the standard mix. Contract §2.1."""
        check_single_standard_mix(session)


class FoodItemAdmin(_TaxonomyAdmin, model=FoodItem):
    """Contract §2.1 (v1.54). The named foods *within* a category — "cheese",
    not "dairy" — and the vocabulary step 2.5 of the calculator offers.

    **Both roles, the same as the six screens around it, and decided on their
    grounds rather than on the flag's.** §8.3 gives taxonomy CRUD to both roles
    and reserves the administrator floor for three things: account management,
    the blocklist and the audit trail — capabilities about *who may use the
    system*, not about what it says. This table is the same kind of thing as
    `food_category` next to it: rows a staff member types, `active` rather than
    delete, nothing identifying, and every write already in `audit_log`. It
    carries no more consequence than `FoodCategoryAdmin` does; if anything
    less, because a food with no factor rows of its own is priced at its
    category's average either way.

    The act that *does* have outward consequence is switching
    `item_level_enabled` on and publishing the set, and that sits on the
    factor-set screen with its own guard. Putting a floor here instead would
    gate the typing while leaving the releasing open, which is the wrong way
    round — the same asymmetry `FactorSetAdmin._require_admin_for_import` was
    careful to avoid arguing the other way.

    **Nothing seeds this table.** Mapping the client's ~20 foods onto our
    categories is a data-authoring task with client-facing consequences (seven
    of their rows have no New Zealand category at all) and gets its own review.
    An empty table is what keeps the item level inert, and this screen is how
    it stops being empty.
    """

    name = "Food item"
    name_plural = "Food items"
    category = _CATEGORY
    icon = "fa-solid fa-cheese"

    can_delete = False

    column_list = [
        FoodItem.code, FoodItem.name, FoodItem.food_category,
        FoodItem.sort_order, FoodItem.active,
    ]
    column_details_list = column_list
    form_columns = [
        FoodItem.food_category, FoodItem.code, FoodItem.name,
        FoodItem.sort_order, FoodItem.active,
    ]
    form_args = {
        "food_category": {"description": (
            "Which category this food belongs to. Required, and it is not "
            "only a grouping: a food with no factors of its own is priced at "
            "this category's average, so the category you pick here is the "
            "number this food gets until somebody writes it one."
        )},
        "code": {"description": (
            "The short name the API and the front end use for this food — "
            "'cheese', 'bread'. Lower case, no spaces. Factor rows point at "
            "this row rather than at the text, so renaming it does not "
            "detach the numbers filed under it."
        )},
        "name": {"description": (
            "The wording a visitor picks from when they say which food was "
            "wasted, once item-level detail is switched on for the published "
            "factor set."
        )},
        "sort_order": {"description": _SORT_ORDER_HELP},
        "active": {"description": (
            _ACTIVE_HELP + " A retired food also stops counting towards the "
            "item-level coverage shown on the factor-set screen."
        )},
    }
    column_searchable_list = [FoodItem.code, FoodItem.name]
    column_filters = [BooleanFilter(FoodItem.active)]
    column_default_sort = ("sort_order", False)


class MetricAdmin(_TaxonomyAdmin, model=Metric):
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
    form_args = {
        "code": {"description": (
            "The short name everything else refers to this metric by — "
            "'co2e', 'water', 'cost', 'mass'. Lower case, no spaces. It "
            "appears in the API response and is how the formula screen picks "
            "which metric an expression computes."
        )},
        "name": {"description": (
            "The heading a visitor reads above this figure — 'Greenhouse "
            "gas', not 'co2e'."
        )},
        "unit": {"description": (
            "The unit the stored number is actually in, which is whatever "
            "this metric's formula produces — 'kg CO2e', 'L', 'NZD'. Nothing "
            "anywhere converts a metric total, so if this should be reported "
            "in tonnes, that is a change to the formula, not to a label."
        )},
        "display_unit": {"description": (
            "A typographic variant of the unit above, used for display only "
            "— 'kg CO₂e' for 'kg CO2e'. Leave blank to show the unit as "
            "typed. It must be the same scale. Putting 't CO2e' here against "
            "a unit of 'kg CO2e' divides nothing by a thousand; it relabels "
            "the number, and every greenhouse-gas figure on the public page "
            "then reads a thousand times too small. That was live until "
            "August 2026."
        )},
        "display_precision": {"description": (
            "How many decimal places a visitor sees. It changes what is "
            "printed and nothing else — the calculation and the stored "
            "figure keep their full precision either way."
        )},
        "sort_order": {"description": _SORT_ORDER_HELP},
        "active": {"description": (
            _ACTIVE_HELP + " A retired metric drops out of the taxonomy every "
            "calculation is built from, so it stops appearing on the results "
            "page; its factors and its formula stay where they are."
        )},
    }
    column_searchable_list = [Metric.code, Metric.name]
    column_filters = [BooleanFilter(Metric.active)]
    column_default_sort = ("sort_order", False)


class UnitPresetAdmin(_TaxonomyAdmin, model=UnitPreset):
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
    form_args = {
        "code": {"description": (
            "The short name the front end uses for this container — "
            "'bucket_20l_full'. Lower case, no spaces. A visitor never sees "
            "it; they see the label below."
        )},
        "label": {"description": (
            "What a visitor picks from the container list — '20 L bucket "
            "(full)'. Say how full it is, because the weight below is the "
            "weight of it in that state."
        )},
        "food_category": {"description": (
            "Leave blank unless the weight genuinely depends on what is in "
            "the container — blank means the preset applies to every "
            "category, which is the usual case. A bucket of bread and a "
            "bucket of potatoes weigh different amounts; a wheelie bin is a "
            "wheelie bin."
        )},
        "kg_per_unit": {"description": (
            "Kilograms one of these holds. The calculator multiplies it by "
            "the number of containers a visitor types, so this figure is the "
            "whole of the conversion — get it wrong and every calculation "
            "made through this preset is wrong, with nothing on screen to "
            "say so."
        )},
        "source_note": {"description": (
            "Where the weight came from. Every preset shipped with this "
            "panel is an unmeasured estimate — open item O-6 — and says so "
            "here; replace the note along with the number when real data "
            "arrives."
        )},
        "active": {"description": _ACTIVE_HELP},
    }
    column_searchable_list = [UnitPreset.code, UnitPreset.label]
    column_filters = [BooleanFilter(UnitPreset.active)]
