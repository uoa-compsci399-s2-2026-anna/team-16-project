"""The account administration screen. Contract §8.3.

There is no email system, deliberately, so this screen is recovery layer L2:
one administrator restoring another's access in person. Everything it does
is destructive to someone's access, so every action is audited and the
two-administrator floor is enforced on every path.

**"Another administrator" is a requirement, not a description.** Both
recovery actions skip a proof the ordinary path demands - issuing a password
never asks for the current one, resetting an enrolment never asks for the
device - and that is safe only because the person performing them is not the
person being recovered. Aimed at the actor's own account they are the two
halves of a takeover from a stolen session, so ``admin/accounts.py`` refuses
them there. The refusal lives in the service layer, not here: ``admin/cli.py``
reaches the same two functions, and a guard in this file would be a guard on
one of the paths. This file's job is to turn that refusal into a page and to
record the attempt (``_refuse_self_recovery``).

Two things about sqladmin 0.30, verified against the installed package
(``sqladmin/application.py``) rather than assumed, that shape this file:

* ``@action``-decorated methods are registered with ``methods=["GET"]`` only
  (``_handle_action_decorated_func``), at ``/{identity}/action/{slug}`` -
  not the ``POST .../{action}/{pk}`` shape a first draft of this file
  assumed. The selected rows arrive as the same ``pks`` query parameter
  either way.
* ``login_required`` (what ``@action`` and ``@expose`` both wrap every route
  in) only checks that *some* onboarded staff session exists - it calls
  ``AdminAuth.authenticate()``, not this view's ``is_accessible``. sqladmin
  calls ``is_accessible`` itself only for the routes it generates internally
  (list/details/create/edit/delete - see ``BaseAdminView._list`` and
  siblings in ``application.py``). A custom ``@action`` route is not one of
  those, so without an explicit check here, a plain staff member could reach
  ``/admin/staff/action/issue-password`` directly - the URL is never
  screened just because the menu entry and ``/admin/staff/list`` are. Every
  action method below starts with the same ``is_accessible`` check the list
  route gets for free.

Route names for ``request.url_for`` need the ``admin:`` prefix
(``admin:list``, ``admin:action-staff-issue-password``) even though the
handler runs inside sqladmin's own mounted sub-application. Starlette's
``Router.app`` only sets ``scope["router"]`` when it is not already present
(``starlette/routing.py``), so the outermost FastAPI app's router - the one
that owns the ``Mount(..., name="admin")`` - stays the router every
``request.url_for`` call resolves against, no matter how many
sub-applications the request has since been dispatched through. Confirmed
directly against sqladmin's own internal helpers (``ModelView._url_for_delete``,
``_url_for_action`` in ``sqladmin/models.py``, both prefixed) and against
this project's own convention: every ``_redirect`` call in ``admin/views.py``
already passes a prefixed name.
"""

from starlette.exceptions import HTTPException
from starlette.responses import RedirectResponse

from sqladmin import action
from sqladmin.filters import OperationColumnFilter

from admin.accounts import (
    LastAdministratorsError,
    SelfRecoveryError,
    UnknownStaffError,
    deactivate_staff,
    get_staff,
    issue_password,
    reset_mfa,
)
from admin.audit import write_audit
from admin.auth import SESSION_KEY
from admin.modelviews import AuditedModelView
from admin.models import Staff, StaffRole


class StaffAdmin(AuditedModelView, model=Staff):
    name = "Staff account"
    name_plural = "Staff accounts"
    icon = "fa-solid fa-users"
    category = "Administration"

    # Contract §8.3 forbids self-service registration, and an account created
    # through a generic form would have no initial password to hand over.
    # Creation goes through the CLI (`python -m admin.cli create-staff`) -
    # deliberately out of scope here, see the task's "Carried Forward" note.
    can_create = False
    can_delete = False
    # Contract §8.3: "This must be enforced in the service layer, not only
    # in the form — sqladmin's form validation can be bypassed." The
    # guarded actions below (issue_password_action, reset_mfa_action,
    # deactivate_action) all route through admin.accounts, which runs
    # _guard_admin_floor and bumps session_generation on every credential
    # change. sqladmin's own generic edit form reaches display_name, role
    # and is_active through a completely different path — Query.update ->
    # plain setattr -> commit (sqladmin/_queries.py) — where none of that
    # runs: an administrator could untick is_active on the second-to-last
    # administrator through /admin/staff/edit/{pk} and the floor in
    # accounts.py would never see it. Verified live: with two active
    # administrators, that request succeeded (302, is_active=False,
    # session_generation unchanged) before this line existed. Editing
    # display_name through the form is not worth reopening that path — the
    # alternative (overriding update_model to route through
    # deactivate_staff/set_role and translate LastAdministratorsError) is
    # more code for a field nobody needs to edit here.
    can_edit = False

    # password_hash, mfa_secret_enc and mfa_last_counter are absent by
    # design. They are in write_audit's REDACTED_FIELDS for the trail; the
    # same reasoning applies to the screen the trail sits beside.
    column_list = [
        Staff.username, Staff.display_name, Staff.role, Staff.is_active,
        Staff.must_change_password, Staff.mfa_enrolled_at, Staff.last_login_at,
    ]
    # column_list only narrows the *list* page. sqladmin's get_details_columns
    # (sqladmin/models.py) falls back to every mapped column
    # (self._prop_names) when column_details_list is unset, regardless of
    # column_list - so without this line, /admin/staff/details/{pk} rendered
    # the bcrypt hash, the encrypted TOTP secret and the replay counter in
    # full, one click away from every row on the list this same view
    # correctly redacts. Reusing column_list here (rather than a second,
    # independently-maintained column_details_exclude_list) is deliberate:
    # the two pages should never drift out of sync on which fields are safe
    # to show.
    column_details_list = column_list
    column_searchable_list = [Staff.username, Staff.display_name]
    # Raw mapped columns here raise AttributeError the first time /list
    # renders on sqladmin 0.30 - the same fix AuditLogAdmin's column_filters
    # already carries a comment about (admin/modelviews.py). Filter instances
    # only, not bare columns.
    column_filters = [
        OperationColumnFilter(Staff.role),
        OperationColumnFilter(Staff.is_active),
    ]
    # No form_columns: can_create and can_edit are both False above, so
    # sqladmin never scaffolds a form for this view — every mutation goes
    # through a guarded @action instead.
    column_default_sort = ("username", False)

    def is_visible(self, request) -> bool:
        return self._is_admin(request)

    def is_accessible(self, request) -> bool:
        """Only role=admin.

        is_visible alone hides the menu entry while leaving the URL open;
        both are needed, and sqladmin calls them for different purposes -
        and, per this module's docstring, calls this one for its own
        generated routes only. The action methods below call it again
        explicitly for exactly that reason.
        """
        return self._is_admin(request)

    def _is_admin(self, request) -> bool:
        username = request.session.get(SESSION_KEY)
        if not username:
            return False
        with self.session_maker() as session:
            try:
                return get_staff(session, username).role is StaffRole.admin
            except UnknownStaffError:
                return False

    def _require_admin(self, request) -> None:
        if not self.is_accessible(request):
            raise HTTPException(status_code=403)

    def _list_url(self, request):
        return request.url_for("admin:list", identity=self.identity)

    async def _refuse(self, request, message, explanation, status_code=400):
        return await self.templates.TemplateResponse(
            request, "brand/action_refused.html",
            {
                "message": message,
                "explanation": explanation,
                "next_url": self._list_url(request),
                "link_text": "Back to accounts",
            },
            status_code=status_code,
        )

    #: Shown beside every self-recovery refusal. The message from
    #: SelfRecoveryError says what was refused; this says why, in the terms
    #: the person reading it will be thinking in - they are almost certainly
    #: not an attacker, they are an administrator who selected their own row.
    _SELF_RECOVERY_EXPLANATION = (
        "These are recovery actions one administrator performs for another. "
        "Applied to your own account they would replace your password without "
        "asking for the current one, and clear your authenticator without "
        "asking for the device - so anyone holding your session could take the "
        "account over outright. Ask the other administrator to do it for you."
    )

    async def _refuse_self_recovery(self, request, session, exc, *, actor, staff_id, slug):
        """Undo the batch, record the attempt, and show the refusal.

        The rollback comes first and discards any row the same request had
        already acted on - the same choice ``deactivate_action`` makes for the
        two-administrator floor, and the right one here: a password issued to
        a colleague earlier in the selection would never be rendered on the
        page this request now returns, leaving that account holding a
        credential nobody read out.

        The audit entry is written *after* the rollback, and committed, so
        that undoing the change does not also undo the record of the attempt.
        A refusal is worth a row precisely because the successful case has
        one: a self-aimed recovery action that left no trace at all would be
        the only thing an administrator reading the trail could not see.

        ``action="refuse"`` is a value contract §2.3's ``audit_log.action``
        note does not list. That note is already behind the code - it lists
        ``create``/``update``/``delete``/``publish``/``rollback`` while
        ``admin/factor_lifecycle.py`` has written ``archive`` since E-6 - and
        the column is a plain VARCHAR(32), so nothing rejects it. It is
        recorded as needing a contract revision rather than fixed here,
        because §2.3 is shared with B's API layer and a unilateral edit is
        the specific thing this project's contract rule forbids. The
        alternative, reusing ``update``, would put a row in the trail
        claiming a change that was refused.
        """
        session.rollback()
        write_audit(
            session, actor=actor, action="refuse", table_name="staff",
            row_id=staff_id, before=None,
            after={"refused": slug, "reason": "self-recovery"},
        )
        session.commit()
        return await self._refuse(
            request, str(exc), self._SELF_RECOVERY_EXPLANATION
        )

    @action(
        name="issue-password",
        label="Issue a new password",
        confirmation_message=(
            "This replaces the account's password and ends its live sessions. "
            "The new password is shown once — have the person with you."
        ),
    )
    async def issue_password_action(self, request):
        self._require_admin(request)
        pks = request.query_params.get("pks", "").split(",")
        actor = request.session.get(SESSION_KEY, "unknown")
        issued = []
        with self.session_maker() as session:
            for pk in filter(None, pks):
                staff = session.get(Staff, int(pk))
                if staff is None:
                    continue
                # issue_password writes its own audit entry (admin/accounts.py) -
                # the plaintext never passes through this view's own
                # write_audit call, which is what keeps it out of the trail.
                try:
                    password = issue_password(session, staff.username, actor=actor)
                except SelfRecoveryError as exc:
                    return await self._refuse_self_recovery(
                        request, session, exc,
                        actor=actor, staff_id=staff.id, slug="issue-password",
                    )
                issued.append((staff.username, password))
            session.commit()
        return await self.templates.TemplateResponse(
            request, "brand/issued_credential.html",
            {"issued": issued, "next_url": self._list_url(request)},
        )

    @action(
        name="reset-mfa",
        label="Reset the authenticator",
        confirmation_message=(
            "This clears the authenticator binding and every recovery code. "
            "The account enrols again at its next login."
        ),
    )
    async def reset_mfa_action(self, request):
        self._require_admin(request)
        pks = request.query_params.get("pks", "").split(",")
        actor = request.session.get(SESSION_KEY, "unknown")
        with self.session_maker() as session:
            for pk in filter(None, pks):
                staff = session.get(Staff, int(pk))
                if staff is None:
                    continue
                # Snapshotted before reset_mfa mutates the row in place -
                # reset_mfa itself does not audit (admin/accounts.py), so
                # this is the only "before" this change ever gets.
                before = {
                    "username": staff.username,
                    "mfa_enrolled_at": staff.mfa_enrolled_at,
                }
                try:
                    reset_mfa(session, staff.username, actor=actor)
                except SelfRecoveryError as exc:
                    return await self._refuse_self_recovery(
                        request, session, exc,
                        actor=actor, staff_id=staff.id, slug="reset-mfa",
                    )
                write_audit(
                    session, actor=actor, action="update", table_name="staff",
                    row_id=staff.id, before=before,
                    after={"username": staff.username, "mfa_enrolled_at": None},
                )
            session.commit()
        return RedirectResponse(self._list_url(request), status_code=302)

    @action(
        name="deactivate",
        label="Deactivate",
        confirmation_message="This ends the account's sessions and refuses its next login.",
    )
    async def deactivate_action(self, request):
        self._require_admin(request)
        pks = request.query_params.get("pks", "").split(",")
        actor = request.session.get(SESSION_KEY, "unknown")
        with self.session_maker() as session:
            for pk in filter(None, pks):
                staff = session.get(Staff, int(pk))
                if staff is None:
                    continue
                try:
                    deactivate_staff(session, staff.username)
                except LastAdministratorsError as exc:
                    session.rollback()
                    return await self._refuse(
                        request, str(exc),
                        "The panel keeps at least two active administrators. "
                        "With no email system to recover through, one "
                        "administrator is one lost phone away from a panel "
                        "nobody can enter.",
                    )
                write_audit(
                    session, actor=actor, action="update", table_name="staff",
                    row_id=staff.id, before={"is_active": True},
                    after={"is_active": False},
                )
            session.commit()
        return RedirectResponse(self._list_url(request), status_code=302)
