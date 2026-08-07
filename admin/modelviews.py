"""sqladmin views that write an audit entry for every change. Contract §8.1.

The eleven taxonomy and factor views of §8.1 inherit AuditedModelView in E-4
and E-5, so the auditing lives here once rather than in each of them.

Atomicity. sqladmin's own ``Query._insert_sync``/``_update_sync``/
``_delete_sync`` (sqladmin/_queries.py) open a session via
``self.model_view.session_maker(...)`` and call ``session.commit()``
themselves, inside ``super().insert_model()`` etc. — control does not return
to this module until after that commit has already happened. Writing the
audit entry in a *second* session afterwards (an earlier version of this file
did exactly that) means the row change and its audit entry are two separate
transactions: a crash between them loses the entry, and contract §5.5's "a
change that is rolled back must leave no audit record claiming it happened"
is only true in one direction.

The fix used here: ``AuditedModelView.__init__`` replaces ``self.session_maker``
(an *instance* attribute, shadowing the class attribute sqladmin assigned) with
a second sessionmaker bound to the same engine, carrying a SQLAlchemy
``before_commit`` event listener. ``before_commit`` fires inside the same
transaction as whatever else the session is about to commit — the listener's
own ``session.add(AuditLog(...))`` becomes part of that same commit, and a
failed commit (the whole point of the exercise) rolls the audit entry back
along with the row change, because they were never two commits to begin with.
See ``_audited_session_maker`` for how this is kept from ever touching a
session this class did not create.
"""

import contextvars
from typing import Any

from sqladmin import ModelView
from sqladmin.filters import OperationColumnFilter
from sqlalchemy import event
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session, sessionmaker

from admin.audit import row_to_dict, write_audit
from admin.auth import SESSION_KEY
from admin.models import AuditLog

#: The acting username for whichever AuditedModelView write is in flight on
#: the current asyncio task. Set by insert_model/update_model/delete_model
#: before calling super(), read by the before_commit listener installed by
#: _audited_session_maker. anyio.to_thread.run_sync (what sqladmin's sync
#: Query path uses to run the actual commit) propagates the calling task's
#: context into the worker thread, so the listener sees the value set by the
#: request that is actually in flight — verified directly: a ContextVar set
#: before an anyio.to_thread.run_sync call is visible inside it.
#:
#: None outside of those three methods, which is also the listener's signal
#: to do nothing (see its docstring) — nothing here should ever write an
#: audit row for a commit this class did not originate.
_actor_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "kai_admin_audit_actor", default=None
)

#: The view whose write is in flight, so the before_commit listener can call
#: back into its validate_before_commit. Set alongside _actor_var and for the
#: same reason: the listener runs deep inside sqladmin's own machinery, with
#: no reference to the view that started the call.
_view_var: contextvars.ContextVar["AuditedModelView | None"] = contextvars.ContextVar(
    "kai_admin_audit_view", default=None
)


def _snapshot_before(row: Any) -> dict:
    """Every column's value as it stood before whatever is pending on ``row``.

    Reads SQLAlchemy's own attribute history rather than the row's current
    (already-mutated) values — this is what makes a genuine "before" snapshot
    possible from inside a listener that only ever sees the row mid-flush,
    never a copy fetched earlier. An attribute with no history at all (never
    loaded, e.g. deferred) falls back to its current value; there is nothing
    else available.
    """
    state = sa_inspect(row)
    result = {}
    for attr in state.mapper.column_attrs:
        history = state.attrs[attr.key].history
        if history.deleted:
            result[attr.key] = history.deleted[0]
        elif history.unchanged:
            result[attr.key] = history.unchanged[0]
        else:
            result[attr.key] = getattr(row, attr.key)
    return result


def _audited_session_maker(
    real_maker: sessionmaker, model: type, table_name: str
) -> sessionmaker[Session]:
    """A sessionmaker, configured like ``real_maker``, whose sessions audit
    their own writes.

    A brand new sessionmaker object — never ``real_maker`` itself, and never
    touched via its own ``.configure()`` or a listener registered on the
    ``Session`` class globally. Either of those would fire this listener on
    every commit anywhere in the app that happens to share the engine: the
    bootstrap step, the API layer, another view's session, all
    indistinguishable from a genuine audited write.

    Built from ``real_maker.kw`` — every option it was constructed or later
    ``.configure()``'d with, not just ``bind`` — so nothing sqladmin or this
    project's own composition root set on it is silently dropped. This is
    what carries over ``autoflush=False``: ``Admin.__init__``
    (sqladmin/application.py) configures the app's shared sessionmaker with
    it deliberately, and sqladmin's own ``_set_attributes_sync`` executes a
    ``SELECT`` for every relationship field a form submits. With autoflush
    left at SQLAlchemy's default (True), that ``SELECT`` silently flushes
    whatever was already ``setattr``'d earlier in the same call *before*
    ``before_commit`` ever runs — the object is clean and its attribute
    history is gone by the time the listener below looks at it, so a
    relation-touching update writes no audit row at all. Rebuilding this
    sessionmaker from one hardcoded attribute rather than the full ``.kw``
    reintroduces that bug the moment either sessionmaker's configuration
    changes out from under it.

    SQLAlchemy's event system scopes a listener registered on one
    ``sessionmaker`` *instance* to sessions produced by calling that
    instance — confirmed directly (two sessionmakers bound to the same
    engine, a listener attached to only one, committing through the other
    never fires it) and confirmed against SQLAlchemy's own source:
    ``sessionmaker.__init__`` builds a private, anonymous ``Session``
    subclass per instance specifically so events can bind to one instance's
    sessions without touching another's. ``AuditedModelView.__init__``
    assigns the sessionmaker this function returns to ``self.session_maker``
    as an *instance* attribute (shadowing the class attribute sqladmin's
    ``Admin.add_model_view`` set moments earlier), and each
    ``AuditedModelView`` subclass gets exactly one instance for the lifetime
    of the app (sqladmin constructs it once, at registration) — so the
    listener this function installs can only ever fire for sessions this one
    view instance opened.
    """
    audited = sessionmaker(**real_maker.kw)

    @event.listens_for(audited, "before_commit")
    def _write_audit_entries(session: Session) -> None:
        actor = _actor_var.get()
        if actor is None:
            # Not a call AuditedModelView originated - e.g. a read-only
            # session this view opened for something else entirely, should
            # sqladmin ever grow one that calls commit(). Writing an audit
            # row with no real actor behind it would be worse than writing
            # none at all.
            return

        # `isinstance(obj, model)` means only rows of the model this
        # particular AuditedModelView was constructed for are ever
        # considered. An ORM cascade — a `factor_set` delete taking its
        # `factor_upstream` rows with it via `cascade="all, delete-orphan"`,
        # for instance — deletes those child rows in the same flush without
        # this listener ever seeing them, and writes no audit entry for them
        # at all. Nothing on this branch exercises that: both views defined
        # here set `can_delete = False`. A future view that allows delete
        # and owns a cascading relationship needs its own handling for the
        # children, or they vanish untracked.
        created = [obj for obj in session.new if isinstance(obj, model)]
        updated = [
            obj for obj in session.dirty
            if isinstance(obj, model) and session.is_modified(obj)
        ]
        deleted = [obj for obj in session.deleted if isinstance(obj, model)]
        if not (created or updated or deleted):
            return

        # Before-snapshots have to be taken before the flush below: flush
        # moves `created` rows out of session.new and can affect what
        # attribute history is still available.
        before_by_id = {
            id(obj): _snapshot_before(obj) for obj in (*updated, *deleted)
        }

        # Assigns primary keys to `created` rows so row_id/after below are
        # real values, not None. This is a flush, not a commit — it happens
        # inside the transaction that is about to commit, not a new one.
        session.flush()

        # Must run *after* the flush above: this session carries
        # autoflush=False (see _audited_session_maker's own docstring), so a
        # query inside validate_before_commit would not see this write's own
        # pending changes if it ran first - a staff member unticking the
        # only is_standard_mix row would pass the guard against the
        # database's pre-write state and commit anyway. Still runs before
        # any audit row is added: raising here propagates out of
        # before_commit, which SQLAlchemy turns into a rollback of the
        # whole transaction - flush included - so a refused change leaves
        # neither the row nor an entry claiming it happened.
        view = _view_var.get()
        if view is not None:
            view.validate_before_commit(session)

        for obj in created:
            write_audit(
                session, actor=actor, action="create", table_name=table_name,
                row_id=getattr(obj, "id", None), before=None,
                after=row_to_dict(obj),
            )
        for obj in updated:
            write_audit(
                session, actor=actor, action="update", table_name=table_name,
                row_id=getattr(obj, "id", None), before=before_by_id[id(obj)],
                after=row_to_dict(obj),
            )
        for obj in deleted:
            write_audit(
                session, actor=actor, action="delete", table_name=table_name,
                row_id=getattr(obj, "id", None), before=before_by_id[id(obj)],
                after=None,
            )

    return audited


class AuditedModelView(ModelView):
    """Base for every CRUD view. Contract §8.1.

    The eleven taxonomy and factor views of contract §8.1 subclass this in
    later stages and will not read this module's own docstring or
    ``admin/accounts_view.py``'s, so three facts that shape every subclass
    of this one belong here, not just wherever they were first discovered:

    1. **A custom ``@action`` route is neither audited nor access-checked
       by inheriting from this class.** ``_actor_var`` is set only inside
       ``insert_model``/``update_model``/``delete_model`` below, so the
       ``before_commit`` listener installed by ``_audited_session_maker``
       returns early (``actor is None``) for a ``session.commit()`` an
       ``@action`` method calls itself — that write is silently unaudited
       unless the action routes its mutation through a service function
       that writes its own audit entry (as ``admin/accounts.py``'s
       ``issue_password`` does) or calls ``write_audit`` directly (as
       ``admin/accounts_view.py``'s ``reset_mfa_action`` and
       ``deactivate_action`` do). Separately, sqladmin registers
       ``@action`` routes with ``login_required`` only — never
       ``is_accessible`` — so every action method needs its own explicit
       permission check at the top; see ``admin/accounts_view.py``'s
       ``_require_admin`` for the pattern.
    2. **``column_details_list`` defaults to every mapped column**,
       independently of ``column_list`` narrowing the list page.
       ``column_export_list`` does not — it falls back to ``column_list`` —
       but the details page is one click from every list row. This produced
       a live defect on this branch: without setting it explicitly, the
       staff details page rendered the full bcrypt password hash and the
       Fernet-encrypted TOTP secret in the clear, from a list this same
       view correctly redacted. Any subclass with a sensitive column has to
       repeat that narrowing itself.
    3. See the comment beside ``session.new``'s ``isinstance`` filter in
       ``_audited_session_maker`` below for what the audit trail does and
       does not capture from an ORM cascade.
    """

    def __init__(self) -> None:
        super().__init__()
        # sqladmin's Admin.add_model_view sets the *class* attribute
        # `session_maker` to the app's one shared sessionmaker just before
        # constructing this instance (sqladmin/application.py). Replacing it
        # here, on the instance, is what lets _audited_session_maker's
        # listener see every write this view makes without ever touching
        # that shared object - see its docstring for the full argument.
        self.session_maker = _audited_session_maker(
            self.session_maker, self.model, self.model.__tablename__
        )

    def _actor(self, request) -> str:
        """The acting username, from the session only.

        Never from the submitted form: an actor a client can set is not an
        audit trail. AuthenticationBackend has already refused the request if
        this is absent, so the fallback is a defensive marker, not a path
        that runs in production.
        """
        return request.session.get(SESSION_KEY) or "unknown"

    def validate_before_commit(self, session) -> None:
        """Refuse a change that would break an invariant, by raising.

        Called from inside the transaction that is about to commit, after
        this write has been flushed but before the audit entries are
        written. The flush matters: this session carries autoflush=False,
        so a query issued before it would not see this write's own pending
        change and could pass a check against stale, pre-write data — the
        exact bypass a rule checked too early is meant to close. Raising
        rolls the whole thing back — the row change (flush included) and
        its audit entry together — so a refused edit leaves no trace
        claiming it happened.

        This is the only enforcement point a form cannot walk past.
        sqladmin's generic edit path is Query.update -> setattr -> commit and
        goes nowhere near a service function, which is how an earlier stage
        of this project shipped an administrator-floor guard that the edit
        form bypassed. Overriding this hook, rather than checking in
        insert_model, is what makes an invariant hold on every path.

        The default is deliberately a no-op: most views have no cross-row
        invariant, and inheriting one they do not need would be worse.

        This hook only runs at all if the flush produced at least one row of
        *this view's own model* in ``created``/``updated``/``deleted`` -
        ``_audited_session_maker``'s listener filters on ``isinstance(obj,
        model)`` and returns before ever reaching this call otherwise. An
        override is safe when its invariant spans a *different* model too,
        as long as editing that other model's own rows always goes through
        that other model's own ``AuditedModelView`` subclass (which carries
        the same hook against its own listener, on its own model) - but a
        view whose invariant depends solely on a model no listener ever
        fires for would never have this hook called, regardless of what the
        override itself checks.
        """

    async def insert_model(self, request, data: dict):
        actor_token = _actor_var.set(self._actor(request))
        view_token = _view_var.set(self)
        try:
            return await super().insert_model(request, data)
        finally:
            _actor_var.reset(actor_token)
            _view_var.reset(view_token)

    async def update_model(self, request, pk: str, data: dict):
        actor_token = _actor_var.set(self._actor(request))
        view_token = _view_var.set(self)
        try:
            return await super().update_model(request, pk, data)
        finally:
            _actor_var.reset(actor_token)
            _view_var.reset(view_token)

    async def delete_model(self, request, pk: str):
        actor_token = _actor_var.set(self._actor(request))
        view_token = _view_var.set(self)
        try:
            return await super().delete_model(request, pk)
        finally:
            _actor_var.reset(actor_token)
            _view_var.reset(view_token)


class AuditLogAdmin(ModelView, model=AuditLog):
    """Contract §8.2: read-only, filterable by actor, time and table."""

    name = "Audit entry"
    name_plural = "Audit log"
    icon = "fa-solid fa-clipboard-list"
    category = "Administration"

    # Contract §8.1 hard-codes all three. The audit trail is the record of
    # who changed what; a trail that can be edited records nothing.
    can_create = False
    can_edit = False
    can_delete = False
    can_export = True

    column_list = [
        AuditLog.at, AuditLog.actor, AuditLog.action,
        AuditLog.table_name, AuditLog.row_id,
    ]
    column_default_sort = ("at", True)
    column_searchable_list = [AuditLog.actor, AuditLog.table_name]
    # sqladmin 0.30 requires Filter instances here, not raw mapped columns -
    # a literal `[AuditLog.actor, ...]` raises AttributeError ("... has no
    # attribute 'parameter_name'") the first time /list is rendered, since
    # get_filters() returns column_filters unchanged and the template reads
    # .parameter_name straight off each entry.
    # OperationColumnFilter supplies contains/equals/starts-with for the three
    # string columns and equals/greater-than/less-than for the datetime one.
    column_filters = [
        OperationColumnFilter(AuditLog.actor),
        OperationColumnFilter(AuditLog.action),
        OperationColumnFilter(AuditLog.table_name),
        OperationColumnFilter(AuditLog.at),
    ]
    page_size = 50
