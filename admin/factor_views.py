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

from sqladmin.filters import BooleanFilter, ForeignKeyFilter, OperationColumnFilter
from sqlalchemy import select

from admin.expressions import ExpressionError, validate_expression
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


def _revalidate_formulas(session, factor_set_id: int) -> None:
    """Re-run §4.3's own check against every formula in one factor_set.

    Shared by ConstantAdmin below: a formula that validated cleanly when it
    was saved can be broken later by an edit to a *different* row -
    deleting or renaming the constant it referenced - and nothing about the
    formula row itself changes when that happens, so FormulaAdmin's own
    validate_before_commit (admin/factor_views.py) never fires; its flush
    contains no Formula row to react to.

    Raises TaxonomyInvariantError naming the broken formula's metric, so the
    staff member sees which formula their change would strand, not just
    that something, somewhere, broke.
    """
    codes = session.scalars(
        select(Constant.code).where(Constant.factor_set_id == factor_set_id)
    ).all()
    formulas = session.scalars(
        select(Formula).where(Formula.factor_set_id == factor_set_id)
    ).all()
    for formula in formulas:
        try:
            validate_expression(formula.expression, constant_codes=codes)
        except ExpressionError as exc:
            raise TaxonomyInvariantError(
                f"This change breaks the formula for metric "
                f"'{formula.metric.code}': line {exc.line}, column "
                f"{exc.column}: {exc.message} Fix or remove that formula "
                "first."
            ) from exc


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
        """
        _require_draft_factor_set(session, Constant)

        factor_set_ids = {
            obj.factor_set_id for obj in session.identity_map.values()
            if isinstance(obj, Constant)
        }
        pending_delete = _pending_deleted_constant_factor_set.get()
        if pending_delete is not None:
            factor_set_ids.add(pending_delete)

        for factor_set_id in factor_set_ids:
            _revalidate_formulas(session, factor_set_id)


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
