"""The factor screens. Contract §8.1.

Every view here inherits AuditedModelView, so each write is audited without
any of them saying so, and each sets column_details_list explicitly because
its default is every mapped column.

Unlike the taxonomy screens, these allow delete: a factor row belongs to one
version, versions are cloned rather than edited in place, and a row deleted
from a draft has no historical result pointing at it. A published or
archived set is a different matter - every one of the five views below
refuses both editing and deleting a row whose parent factor_set is not a
draft (`_refuse_if_factor_set_not_draft`, `_require_draft_factor_set`,
`_refuse_delete_from_non_draft`), and a *move* is checked on both the row's
previous factor_set and its pending one, not the pending one alone. See
FactorSetAdmin for the set-level version of the same invariant.

The two high-volume tables carry roughly 270 and 600 rows per factor set, so
every list here filters by factor_set. A staff member editing the wrong
version's number is the failure this prevents, and it is silent.
"""

import contextvars

from sqladmin import action
from sqladmin.filters import BooleanFilter, ForeignKeyFilter, OperationColumnFilter
from sqlalchemy import select
from starlette.exceptions import HTTPException
from starlette.responses import RedirectResponse

from admin.auth import SESSION_KEY
from admin.expressions import ExpressionError, validate_expression
from admin.factor_lifecycle import (
    LifecycleError, archive_factor_set, clone_factor_set, publish_factor_set,
    revalidate_formulas, rollback_to,
)
from admin.factor_models import (
    Constant, Equivalence, FactorDownstream, FactorSet, FactorSetStatus,
    FactorUpstream, Formula,
)
from admin.modelviews import AuditedModelView
from admin.taxonomy_rules import TaxonomyInvariantError, check_single_published_set

_CATEGORY = "Factors"


def _refuse_if_factor_set_not_draft(factor_set: FactorSet | None) -> None:
    """Raise unless `factor_set` is a draft, or there is none to check.

    The one check both the edit-path guard (`_require_draft_factor_set`)
    and every view's delete-path guard below actually need to make. This
    module's own docstring says "versions are cloned rather than edited in
    place" - nothing enforced that until this existed: a staff member could
    open a published set's own factor_upstream row and change value_per_kg
    directly through sqladmin's generic edit form, or delete it outright,
    nowhere near a service function - and every historical submission
    stamped with that factor_set_id would silently stop reproducing, which
    is the entire guarantee stamping a factor_set_id onto a submission
    exists to buy (contract §2.2).
    """
    if factor_set is not None and factor_set.status is not FactorSetStatus.draft:
        raise TaxonomyInvariantError(
            f"'{factor_set.version_label}' is {factor_set.status.value}, "
            "not draft. Its numbers must not change: every historical "
            "submission stamped with this factor_set_id depends on them "
            "staying exactly as published. Clone this set into a new "
            "draft first."
        )


#: The factor_set_id a row was filed under just before an edit lands, keyed
#: by nothing (one value, like _pending_deleted_constant_factor_set below) -
#: sqladmin's edit route only ever handles one row per request. See
#: `_capture_previous_factor_set_id`'s own docstring for why this exists at
#: all rather than reading SQLAlchemy's attribute history directly.
_pending_previous_factor_set_id: contextvars.ContextVar[int | None] = (
    contextvars.ContextVar("kai_admin_pending_previous_factor_set_id", default=None)
)


def _capture_previous_factor_set_id(view: AuditedModelView, model: type, pk: str) -> int | None:
    """`model`'s row `pk`'s factor_set_id, read *before* the pending edit
    lands - see each view's own `update_model` override below for where this
    is stashed on `_pending_previous_factor_set_id` for
    `_require_draft_factor_set` to read back.

    The brief for this guard pointed at SQLAlchemy's attribute history -
    the mechanism admin/modelviews.py's `_snapshot_before` already uses for
    the audit trail. Tried first, directly, in `_require_draft_factor_set`:
    it does not work from there. `_snapshot_before` is only ever called
    *before* the `session.flush()` the before_commit listener runs ahead of
    `validate_before_commit` (admin/modelviews.py's own docstring explains
    why the flush has to happen first: a query issued before it would not
    see this write's own pending change). Verified directly against this
    SQLAlchemy: for a scalar column reassigned then flushed,
    `history.deleted` holds the old value beforehand and is empty
    afterwards - the flush resets what SQLAlchemy considers the attribute's
    "committed" state, the same way it empties `session.new`/`session.dirty`
    (FormulaAdmin.validate_before_commit's own docstring below covers that
    half of the same trap). `validate_before_commit` - and so
    `_require_draft_factor_set` - only ever runs after that flush, so by the
    time either could read it, the "before" value is already gone.

    The fix follows the same shape `ConstantAdmin.delete_model` already
    uses for delete: capture what is needed before the mutating call, on a
    lookup taken while the true "before" state still exists, since nothing
    downstream can recover it once that call has run.
    """
    with view.session_maker() as lookup:
        row = lookup.get(model, int(pk))
        return row.factor_set_id if row is not None else None


def _require_draft_factor_set(session, model) -> None:
    """Refuse a create/update whose parent factor_set is not a draft - on
    either end of a move.

    Called from all five factor-row views' own validate_before_commit.
    Reads the pending rows of `model` straight out of `session.identity_map`
    - the same mechanism FormulaAdmin's own check already relies on: a row
    this session is inserting or editing stays there through the flush (see
    FormulaAdmin.validate_before_commit's docstring for how that was
    confirmed).

    Checks the row's *pending* factor_set (its new value) and, if
    `_pending_previous_factor_set_id` names a different one, that too.
    Checking the pending value alone missed a row moving **out of** a
    published set: reassigning `factor_set_id` from a published set to a
    draft makes the row look, by its new value alone, like an ordinary
    draft-row edit - and reassigning a referenced row **into** another set
    is exactly as much of a break for the set losing it. An edit that does
    not touch `factor_set_id` leaves the ContextVar equal to the row's own
    (unchanged) value, so this second check is a no-op for the ordinary
    case, draft-to-draft moves included.

    Delete is deliberately not handled here: a deleted row leaves
    session.identity_map once its own delete flushes (see
    ConstantAdmin.delete_model's docstring for the confirmed mechanism), so
    there is nothing left in `session.identity_map` for this loop to find by
    the time validate_before_commit runs. Each view's own delete_model
    override, below, checks the row before it is gone instead.
    """
    rows = [obj for obj in session.identity_map.values() if isinstance(obj, model)]
    for row in rows:
        _refuse_if_factor_set_not_draft(row.factor_set)

        previous_id = _pending_previous_factor_set_id.get()
        if previous_id is not None and previous_id != row.factor_set_id:
            _refuse_if_factor_set_not_draft(session.get(FactorSet, previous_id))


def _refuse_delete_from_non_draft(view: AuditedModelView, model: type, pk: str) -> None:
    """Refuse deleting `model`'s row `pk` out of a published or archived set.

    The delete-path counterpart to `_require_draft_factor_set`'s edit-path
    guard - closing the gap that guard's own docstring used to describe as
    "known, unclosed": removing a factor_upstream row from a published set
    breaks reproducibility exactly as editing it in place does.

    Looks the row up through `view`'s own session_maker *before* the delete
    happens, the same way `ConstantAdmin.delete_model` already does for its
    own orphan check - a deleted row leaves session.identity_map once its
    own delete flushes, so validate_before_commit has nothing left to
    inspect by the time it runs; there is no "after" hook this could be.
    """
    with view.session_maker() as lookup:
        row = lookup.get(model, int(pk))
        factor_set = row.factor_set if row is not None else None
    _refuse_if_factor_set_not_draft(factor_set)


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

    async def update_model(self, request, pk: str, data: dict):
        """Stash factor_set_id as it stood before this edit lands. Contract
        §2.2. See _capture_previous_factor_set_id's own docstring."""
        token = _pending_previous_factor_set_id.set(
            _capture_previous_factor_set_id(self, FactorUpstream, pk)
        )
        try:
            return await super().update_model(request, pk, data)
        finally:
            _pending_previous_factor_set_id.reset(token)

    async def delete_model(self, request, pk) -> None:
        """A published or archived set's rows must not be removed either.
        Contract §2.2. See _refuse_delete_from_non_draft's own docstring."""
        _refuse_delete_from_non_draft(self, FactorUpstream, pk)
        await super().delete_model(request, pk)

    def validate_before_commit(self, session) -> None:
        """A published or archived set's numbers must not change in place.
        Contract §2.2. See _require_draft_factor_set's own docstring."""
        _require_draft_factor_set(session, FactorUpstream)


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

    async def update_model(self, request, pk: str, data: dict):
        """Stash factor_set_id as it stood before this edit lands. Contract
        §2.2. See _capture_previous_factor_set_id's own docstring."""
        token = _pending_previous_factor_set_id.set(
            _capture_previous_factor_set_id(self, FactorDownstream, pk)
        )
        try:
            return await super().update_model(request, pk, data)
        finally:
            _pending_previous_factor_set_id.reset(token)

    async def delete_model(self, request, pk) -> None:
        """A published or archived set's rows must not be removed either.
        Contract §2.2. See _refuse_delete_from_non_draft's own docstring."""
        _refuse_delete_from_non_draft(self, FactorDownstream, pk)
        await super().delete_model(request, pk)

    def validate_before_commit(self, session) -> None:
        """A published or archived set's numbers must not change in place.
        Contract §2.2. See _require_draft_factor_set's own docstring."""
        _require_draft_factor_set(session, FactorDownstream)


#: The factor_set a Constant row belonged to just before ConstantAdmin
#: deletes it. See ConstantAdmin.delete_model's docstring for why this
#: cannot simply be read back out of `session` inside validate_before_commit
#: the way the update path can.
_pending_deleted_constant_factor_set: contextvars.ContextVar[int | None] = (
    contextvars.ContextVar("kai_admin_pending_deleted_constant_factor_set", default=None)
)


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

    async def update_model(self, request, pk: str, data: dict):
        """Stash factor_set_id as it stood before this edit lands. Contract
        §2.2. See _capture_previous_factor_set_id's own docstring."""
        token = _pending_previous_factor_set_id.set(
            _capture_previous_factor_set_id(self, Constant, pk)
        )
        try:
            return await super().update_model(request, pk, data)
        finally:
            _pending_previous_factor_set_id.reset(token)

    async def delete_model(self, request, pk) -> None:
        """Remember which factor_set this row belonged to, before it is gone
        - and refuse outright if that set is not a draft. Contract §2.2.

        A deleted row leaves `session.identity_map` once its delete flushes
        (confirmed directly in FormulaAdmin's own validate_before_commit
        docstring above: present for a fresh insert, present for an update,
        "correctly absent for a row this same session just deleted"), so by
        the time validate_before_commit runs there is nothing left in
        session state naming which factor_set a deleted constant came from.

        Looked up here, before `super().delete_model()` does anything, and
        carried across on a ContextVar rather than an instance attribute -
        sqladmin constructs one ConstantAdmin instance for the app's whole
        lifetime, shared by every concurrent request, so an instance
        attribute would let two simultaneous deletes clobber each other's
        value. This mirrors _actor_var/_view_var in admin/modelviews.py
        exactly: set on the request's own asyncio task before the call that
        eventually reaches `anyio.to_thread.run_sync` for the actual commit,
        which that module's own docstring already establishes propagates a
        ContextVar set beforehand into the worker thread the before_commit
        listener - and so validate_before_commit below - runs on.

        The draft check runs on this same lookup rather than through
        `_refuse_delete_from_non_draft` - that helper opens its own,
        separate lookup session, and this method already has the row open
        for exactly the same reason (the ContextVar bookkeeping above).
        """
        with self.session_maker() as lookup:
            constant = lookup.get(Constant, int(pk))
            factor_set = constant.factor_set if constant is not None else None
            factor_set_id = constant.factor_set_id if constant is not None else None
        _refuse_if_factor_set_not_draft(factor_set)
        token = _pending_deleted_constant_factor_set.set(factor_set_id)
        try:
            await super().delete_model(request, pk)
        finally:
            _pending_deleted_constant_factor_set.reset(token)

    def validate_before_commit(self, session) -> None:
        """A constant deleted or renamed must not orphan a formula, and a
        published or archived set's constants must not change in place.
        §4.3 + §2.2.

        Composes both guards rather than picking one: _require_draft_factor_set
        below stops an edit to a published set's own constants, and the
        orphan check afterwards stops a *draft* edit from stranding a
        formula. Neither subsumes the other - a constant can be renamed
        inside a draft (refused only if a formula depends on the old name)
        and a constant in a published set must not be touched at all,
        formula or no formula.

        Scoped to the factor_set(s) this write actually touched, not every
        factor_set in the database: an unrelated, already-broken formula
        sitting in some other draft must not block an edit that has nothing
        to do with it.

        The update/create path reads the affected factor_set(s) straight out
        of `session.identity_map` - a row this session is inserting or
        editing stays there through the flush, same as FormulaAdmin relies
        on. The delete path has nothing there to read (see delete_model's
        own docstring above) and falls back to the ContextVar delete_model
        stashed before the row disappeared.

        A *move* needs the same "both ends" treatment
        `_require_draft_factor_set` itself already gives the draft-only
        check above, and for the same underlying reason: moving a constant
        out of one draft into another orphans a formula in the set losing
        it exactly as surely as deleting the constant outright would -
        `session.identity_map` only ever names the row's *new* factor_set_id
        after a move, so the set it came from was never in `factor_set_ids`
        without this. `_pending_previous_factor_set_id` (stashed by
        update_model above, before the edit lands) supplies it. Not guarded
        on the previous set being a draft here: if it were not,
        `_require_draft_factor_set`'s own check above already raised before
        this line runs, so a non-None previous id reaching here is always a
        draft's.
        """
        _require_draft_factor_set(session, Constant)

        factor_set_ids = {
            obj.factor_set_id for obj in session.identity_map.values()
            if isinstance(obj, Constant)
        }
        pending_delete = _pending_deleted_constant_factor_set.get()
        if pending_delete is not None:
            factor_set_ids.add(pending_delete)
        previous_id = _pending_previous_factor_set_id.get()
        if previous_id is not None:
            factor_set_ids.add(previous_id)

        for factor_set_id in factor_set_ids:
            revalidate_formulas(session, factor_set_id)


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

    async def update_model(self, request, pk: str, data: dict):
        """Stash factor_set_id as it stood before this edit lands. Contract
        §2.2. See _capture_previous_factor_set_id's own docstring."""
        token = _pending_previous_factor_set_id.set(
            _capture_previous_factor_set_id(self, Equivalence, pk)
        )
        try:
            return await super().update_model(request, pk, data)
        finally:
            _pending_previous_factor_set_id.reset(token)

    async def delete_model(self, request, pk) -> None:
        """A published or archived set's rows must not be removed either.
        Contract §2.2. See _refuse_delete_from_non_draft's own docstring."""
        _refuse_delete_from_non_draft(self, Equivalence, pk)
        await super().delete_model(request, pk)

    def validate_before_commit(self, session) -> None:
        """A published or archived set's numbers must not change in place.
        Contract §2.2. See _require_draft_factor_set's own docstring."""
        _require_draft_factor_set(session, Equivalence)


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

    async def update_model(self, request, pk: str, data: dict):
        """Stash factor_set_id as it stood before this edit lands. Contract
        §2.2. See _capture_previous_factor_set_id's own docstring."""
        token = _pending_previous_factor_set_id.set(
            _capture_previous_factor_set_id(self, Formula, pk)
        )
        try:
            return await super().update_model(request, pk, data)
        finally:
            _pending_previous_factor_set_id.reset(token)

    async def delete_model(self, request, pk) -> None:
        """A published or archived set's rows must not be removed either.
        Contract §2.2. See _refuse_delete_from_non_draft's own docstring."""
        _refuse_delete_from_non_draft(self, Formula, pk)
        await super().delete_model(request, pk)

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

        Also composes _require_draft_factor_set: a formula in a published or
        archived set must not change even when the new expression is
        perfectly valid - contract §2.2's own immutability guarantee, not
        this method's §4.3 one, is what refuses that edit.
        """
        _require_draft_factor_set(session, Formula)

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


#: The status a FactorSet row carried just before an edit lands - the
#: FactorSetAdmin counterpart to `_pending_previous_factor_set_id` above,
#: same shape and same reason: FactorSetAdmin.update_model stashes it here
#: before the pending edit lands, and FactorSetAdmin.validate_before_commit
#: reads it back, because by the time that hook runs the flush ahead of it
#: has already erased SQLAlchemy's own attribute history for the column.
_pending_previous_factor_set_status: contextvars.ContextVar[FactorSetStatus | None] = (
    contextvars.ContextVar("kai_admin_pending_previous_factor_set_status", default=None)
)


class FactorSetAdmin(AuditedModelView, model=FactorSet):
    """Contract §8.2. Clone, publish, roll back and archive live here as
    `@action` routes rather than through the generic edit form - see
    form_columns' own comment below for why `status` was removed from it.

    Two things an `@action` does not inherit from AuditedModelView, both
    documented in that class's own docstring and both already solved in
    admin/accounts_view.py:

    1. **No auditing.** `_actor_var` (admin/modelviews.py) is set only
       inside insert_model/update_model/delete_model, so the before_commit
       listener returns early for a commit one of the four actions below
       makes itself. clone_factor_set/publish_factor_set/rollback_to/
       archive_factor_set (admin/factor_lifecycle.py) write their own audit
       entries for exactly this reason - each takes `actor` and this file
       supplies it, then commits the transaction those functions
       deliberately leave open.
    2. **No access check.** sqladmin registers `@action` routes with
       login_required only, never is_accessible, and as GET routes at
       `/{identity}/action/{slug}` with the selected rows in a `pks` query
       parameter (see admin/accounts_view.py's module docstring for the
       verification against sqladmin's own application.py). Without an
       explicit check at the top of each method below, the URL is reachable
       by any onboarded staff member even when the menu entry and the list
       page are hidden from them.

    Unlike StaffAdmin's equivalent actions, the check here is
    `self.is_accessible(request)`, not an admin-only helper: contract §8.3
    decided, deliberately, that publishing is available to both roles -
    "audit_log plus one-click rollback already provide accountability and
    recovery, and gating them behind an administrator would stall routine
    work in a three-to-five person team." FactorSetAdmin overrides neither
    is_visible nor is_accessible, so both inherit sqladmin's own default
    (ModelView.is_accessible: "By default, it will allow access for
    everyone") - both an administrator and a plain staff member pass.

    A LifecycleError from any of the four service functions is caught and
    rendered through brand/action_refused.html rather than left to
    propagate: sqladmin's own exception_handlers map only HTTPException
    (sqladmin/application.py), and a custom @action route is a plain
    Starlette route added via Admin.add_route, not one of the internally
    wrapped list/create/edit/delete handlers - nothing else along that path
    catches a bare exception, so an uncaught LifecycleError would surface as
    an unhandled 500 with no message, exactly the outcome contract §9.1
    exists to avoid a member of the public seeing from a broken formula, and
    the same failure mode here for a member of staff.

    **Archiving** (`archive_action` below) is the only route left to take
    the calculator offline once `status` came off the edit form: publishing
    a different set to displace a bad one requires a different set to exist,
    which it may not. Archiving the currently-published set on purpose,
    leaving nothing published, is a supported outcome - contract §9's
    NO_PUBLISHED_FACTOR_SET (503, "calculator under maintenance") is the
    designed response - not an error this action refuses.

    **A published or archived set's own fields must not change in place**
    either, for the same reason the five child factor views refuse it for
    their rows (`_require_draft_factor_set`'s own docstring): a staff member
    switching `is_mock` off a live set through the generic edit form would
    remove the mandatory, non-dismissible placeholder-data banner while the
    numbers underneath are still mock, with no service function anywhere
    near that path to stop it. See `_pending_previous_factor_set_status`,
    `update_model` and `validate_before_commit` below.
    """

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
    # published_at/published_by/status are absent deliberately: all three are
    # stamped by the lifecycle actions (admin/factor_lifecycle.py's
    # publish_factor_set/rollback_to), and a staff member typing them by hand
    # would both make the audit trail disagree with itself and walk straight
    # past the lock, the formula re-validation and the "at most one
    # published" refusal those functions exist to enforce - status is not a
    # plain data column, it is the entire lifecycle contract's write path,
    # and sqladmin's generic edit route (setattr then commit, no service
    # function anywhere near it) has no way to know that.
    form_columns = [FactorSet.version_label, FactorSet.is_mock,
                    FactorSet.effective_from, FactorSet.notes]
    column_default_sort = ("id", True)

    async def update_model(self, request, pk: str, data: dict):
        """Stash status as it stood before this edit lands, for
        validate_before_commit to read back. Contract §2.2.

        Same shape as `_capture_previous_factor_set_id` above, and for the
        same reason: this session carries autoflush=False, and
        validate_before_commit only ever runs after the flush the
        before_commit listener issues ahead of it (admin/modelviews.py) - by
        which point SQLAlchemy's own attribute history for `status` is
        already gone, whether or not this edit actually touched that column.
        The lookup happens on a separate, throwaway session opened before
        the pending edit lands, so it always sees the row's true "before"
        state.
        """
        with self.session_maker() as lookup:
            row = lookup.get(FactorSet, int(pk))
            previous_status = row.status if row is not None else None
        token = _pending_previous_factor_set_status.set(previous_status)
        try:
            return await super().update_model(request, pk, data)
        finally:
            _pending_previous_factor_set_status.reset(token)

    def validate_before_commit(self, session) -> None:
        """At most one published set (§2.2), and a published or archived
        set's own fields must not change in place (§2.2's same immutability
        guarantee the five child factor views already enforce for their own
        rows via `_require_draft_factor_set` - this is the parent row
        getting the same treatment, since nothing about editing FactorSet
        itself goes through any of those five).

        Only the update path can trip the second check:
        `_pending_previous_factor_set_status` is set by `update_model`
        above and stays at its default (`None`) for a create, so a brand
        new row - always inserted as `draft`, per FactorSet's own column
        default - is never refused here.
        """
        check_single_published_set(session)

        previous_status = _pending_previous_factor_set_status.get()
        if previous_status is not None and previous_status is not FactorSetStatus.draft:
            raise TaxonomyInvariantError(
                f"This factor set is {previous_status.value}, not draft. "
                "Its own fields must not change in place: switching "
                "is_mock off a published set, for instance, would remove "
                "the mandatory placeholder-data warning while the numbers "
                "underneath are still mock. Clone this set into a new "
                "draft first."
            )

    def _require_accessible(self, request) -> None:
        if not self.is_accessible(request):
            raise HTTPException(status_code=403)

    def _list_url(self, request):
        return request.url_for("admin:list", identity=self.identity)

    def _one_pk(self, request) -> int:
        """Exactly one id out of the `pks` query param, or raise
        LifecycleError naming why not.

        More than one id is not a hypothetical: sqladmin's own
        `templates/sqladmin/list.html` puts these three actions in the
        bulk **Actions** dropdown too, alongside the row-level ones, and
        `statics/js/main.js` builds `?pks=` from every ticked checkbox
        (verified live: two drafts selected, publish clicked - 302 back to
        the list, the first published, the second left as a draft, nothing
        on the page saying so). Silently acting on the first id and
        dropping the rest would leave a staff member believing the action
        covered everything they ticked when it covered one row - worse
        than refusing outright, and especially now that `status` is off
        the edit form: these actions are the only way left to fix what one
        of them just did to the row it skipped.

        A non-integer id (`?pks=abc`, only reachable by a hand-typed URL)
        is refused the same way rather than left to raise ValueError past
        this method - which would otherwise 500 in exactly the shape the
        LifecycleError catch around every action below exists to prevent.
        """
        raw = [pk for pk in request.query_params.get("pks", "").split(",") if pk]
        if not raw:
            raise LifecycleError("No factor set was selected.")
        if len(raw) > 1:
            raise LifecycleError("Select one factor set at a time.")
        try:
            return int(raw[0])
        except ValueError:
            raise LifecycleError(f"'{raw[0]}' is not a valid factor set id.")

    def _unique_clone_label(self, session, source_label: str) -> str:
        """`f"{source_label} (copy)"`, or that with a numeric suffix if the
        plain form is already taken. clone_factor_set (admin/factor_lifecycle.py)
        refuses a duplicate label outright rather than generating one itself
        - the staff member did not type this label, so it is this view's job
        to hand it one that is actually free."""
        base = f"{source_label} (copy)"
        label = base
        suffix = 2
        while session.scalar(
            select(FactorSet).where(FactorSet.version_label == label)
        ) is not None:
            label = f"{base} {suffix}"
            suffix += 1
        return label

    async def _refused(self, request, message: str):
        return await self.templates.TemplateResponse(
            request, "brand/action_refused.html",
            {"message": message, "next_url": self._list_url(request)},
            status_code=400,
        )

    @action(
        name="clone",
        label="Clone",
        confirmation_message=(
            "This creates a new draft with every factor, constant, formula "
            "and equivalence from this set duplicated. Nothing the public "
            "sees changes."
        ),
    )
    async def clone_action(self, request):
        self._require_accessible(request)
        actor = request.session.get(SESSION_KEY, "unknown")
        try:
            pk = self._one_pk(request)
        except LifecycleError as exc:
            return await self._refused(request, str(exc))
        with self.session_maker() as session:
            source = session.get(FactorSet, pk)
            if source is None:
                return await self._refused(
                    request, f"No factor set with id {pk} exists to clone."
                )
            source_label = source.version_label
            label = self._unique_clone_label(session, source_label)
            try:
                clone_id = clone_factor_set(session, pk, label, actor)
            except LifecycleError as exc:
                session.rollback()
                return await self._refused(request, str(exc))
            session.commit()
        return await self.templates.TemplateResponse(
            request, "brand/factor_set_cloned.html",
            {"clone_label": label, "source_label": source_label,
             "next_url": self._list_url(request),
             "compare_url": str(request.url_for("admin:view-compare",
                                                 factor_set_id=clone_id))},
        )

    @action(
        name="publish",
        label="Publish",
        confirmation_message=(
            "This makes this set live and archives whatever is currently "
            "published. Every calculation from this point on uses its "
            "numbers."
        ),
    )
    async def publish_action(self, request):
        self._require_accessible(request)
        actor = request.session.get(SESSION_KEY, "unknown")
        try:
            pk = self._one_pk(request)
        except LifecycleError as exc:
            return await self._refused(request, str(exc))
        with self.session_maker() as session:
            try:
                publish_factor_set(session, pk, actor)
            except LifecycleError as exc:
                session.rollback()
                return await self._refused(request, str(exc))
            session.commit()
        return RedirectResponse(self._list_url(request), status_code=302)

    @action(
        name="rollback",
        label="Roll back",
        confirmation_message=(
            "This restores this archived set to published, archiving "
            "whatever is currently published in its place."
        ),
    )
    async def rollback_action(self, request):
        self._require_accessible(request)
        actor = request.session.get(SESSION_KEY, "unknown")
        try:
            pk = self._one_pk(request)
        except LifecycleError as exc:
            return await self._refused(request, str(exc))
        with self.session_maker() as session:
            try:
                rollback_to(session, pk, actor)
            except LifecycleError as exc:
                session.rollback()
                return await self._refused(request, str(exc))
            session.commit()
        return RedirectResponse(self._list_url(request), status_code=302)

    @action(
        name="archive",
        label="Archive",
        confirmation_message=(
            "This archives this factor set on its own, with nothing else "
            "promoted to take its place. If this set is currently "
            "published, the calculator will show a maintenance message to "
            "the public until another set is published."
        ),
    )
    async def archive_action(self, request):
        self._require_accessible(request)
        actor = request.session.get(SESSION_KEY, "unknown")
        try:
            pk = self._one_pk(request)
        except LifecycleError as exc:
            return await self._refused(request, str(exc))
        with self.session_maker() as session:
            try:
                archive_factor_set(session, pk, actor)
            except LifecycleError as exc:
                session.rollback()
                return await self._refused(request, str(exc))
            session.commit()
        return RedirectResponse(self._list_url(request), status_code=302)

    @action(
        name="compare",
        label="Compare with published",
    )
    async def compare_action(self, request):
        """Contract §8.2: the last gate before publishing. Redirects to
        `CompareView.compare` (admin/dryrun_views.py) for the selected row -
        a staff member about to publish should not have to type that URL by
        hand.

        No `confirmation_message`, unlike the four actions above: this is a
        read-only report, not a lifecycle action, and writes nothing for a
        confirmation step to protect against.

        The route name is ``admin:view-compare``, not the bare
        ``view-compare`` sqladmin registers it under: Starlette's
        ``Request.url_for`` only sets ``scope["router"]`` when the scope
        does not already carry one, and inside a view mounted under
        sqladmin's own ``Mount`` (named ``"admin"``) the scope already has
        the *outer* FastAPI router in it by the time this method runs -
        exactly why ``_list_url`` above resolves ``"admin:list"``, not
        ``"list"``. The unprefixed name is only ever visible to
        ``app.url_path_for`` called directly on the inner app, never to
        ``request.url_for`` from inside a request handler.
        """
        self._require_accessible(request)
        try:
            pk = self._one_pk(request)
        except LifecycleError as exc:
            return await self._refused(request, str(exc))
        return RedirectResponse(
            request.url_for("admin:view-compare", factor_set_id=pk), status_code=302
        )
