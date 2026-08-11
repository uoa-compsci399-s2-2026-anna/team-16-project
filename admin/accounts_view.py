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

import time

from starlette.exceptions import HTTPException
from starlette.responses import RedirectResponse

from sqladmin import action, expose
from sqladmin.filters import OperationColumnFilter

from admin.accounts import (
    MAX_DISPLAY_NAME_LENGTH,
    MAX_USERNAME_LENGTH,
    AccountStillActiveError,
    DuplicateUsernameError,
    InvalidDisplayNameError,
    InvalidUsernameError,
    LastAdministratorsError,
    SelfRecoveryError,
    UnknownStaffError,
    create_staff,
    deactivate_staff,
    delete_staff,
    get_staff,
    issue_password,
    reactivate_staff,
    reset_mfa,
)
from admin.audit import write_audit
from admin.auth import SESSION_KEY, reauthenticate
from admin.csrf import check_token, issue_token
from admin.modelviews import AuditedModelView
from admin.models import Staff, StaffRole
from admin.runtime import get_runtime


class StaffAdmin(AuditedModelView, model=Staff):
    name = "Staff account"
    name_plural = "Staff accounts"
    icon = "fa-solid fa-users"
    category = "Administration"

    # sqladmin's *generic* create form stays off, and the reason is stronger
    # than "out of scope" (which is what this comment used to say, pointing at
    # the CLI). That form's path is Query.insert -> setattr -> commit
    # (sqladmin/_queries.py): it reaches no service function, so it would run
    # none of admin/accounts.py's rules, and it has no way to produce an
    # initial password at all - `password_hash` is NOT NULL with no default,
    # so the field would either have to appear on the form (staff typing a
    # bcrypt hash) or the insert would fail. Creation is the hand-written
    # `new_staff` route below, which goes through `create_staff` exactly as
    # `python -m admin.cli create-staff` does.
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
    # through a guarded @action or the `new_staff` route instead.
    column_default_sort = ("username", False)

    # Puts "Add a staff member" in the list page's own menu bar. Without it,
    # /admin/staff/new is reachable only by somebody who already knows the
    # URL - and until this task there was no URL, only a shell on the
    # container, which is the whole defect being fixed. Same hook and same
    # shape as brand/ip_block_list.html: the template extends
    # brand/model_list.html and replaces `model_menu_bar` alone, so search,
    # filters, pagination, the export menu and the bulk-action dropdown are
    # inherited untouched.
    list_template = "brand/staff_list.html"

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

    # --- creating an account ------------------------------------------------

    #: The role values the form may submit, and the only two that exist
    #: (``admin/models.py::StaffRole``). Read as a whitelist, never coerced:
    #: an unrecognised value is refused rather than falling back to either
    #: side. A form that quietly resolved a typo to `admin` would hand out the
    #: capability this whole screen exists to control, and one that quietly
    #: resolved it to `staff` would silently ignore a deliberate choice and be
    #: discovered only when the new administrator could not do their job.
    _ROLES = {role.value: role for role in StaffRole}

    def _new_staff_context(self, request, **extra) -> dict:
        context = {
            "error": None,
            # Which dialog to reopen, and nothing more than that - purely
            # presentational, the same field brand/security.html carries. A
            # refused submission has to come back inside the dialog it was
            # made in, because a modal covers a message printed behind it.
            "open_dialog": None,
            # Echoed back so a refusal does not cost somebody the three
            # fields they had already filled in. Safe to render: both are
            # escaped by Jinja's autoescaping, and unlike the blocklist form
            # (which deliberately drops the rejected value) nothing here is a
            # value somebody may have typed believing it was private.
            "username": "",
            "display_name": "",
            "role": StaffRole.staff.value,
            "max_username_length": MAX_USERNAME_LENGTH,
            "max_display_name_length": MAX_DISPLAY_NAME_LENGTH,
            "list_url": self._list_url(request),
            "csrf_token": issue_token(request.session),
        }
        context.update(extra)
        return context

    async def _new_staff_page(self, request, context, status_code=200):
        return await self.templates.TemplateResponse(
            request, "brand/new_staff.html", context, status_code=status_code
        )

    @expose("/new", methods=["GET", "POST"])
    async def new_staff(self, request):
        """Create an account, and show its one-time password exactly once.

        **The capability this panel shipped without.** Until this route
        existed the only way to onboard a colleague was
        ``kaicalc-admin create-staff`` on the container — which means a panel
        that cannot add the person sitting next to you without a developer,
        for a client that is a trust with staff turnover and no developer.

        Both entry points reach ``admin/accounts.py::create_staff`` and
        neither holds a rule of its own; see ``admin/cli.py::cmd_create_staff``
        for the same statement from the other side. Everything refused here is
        refused there.

        **What happens if the page carrying the password is never seen.** The
        row is committed and then rendered in the same response — there is no
        redirect between the write and the display, which is the shape that
        lost a set of recovery codes on this project on 2026-08-10 (an expired
        pending-login window bounced the enrolment POST to the login page
        before the codes rendered). That narrows the window; it does not close
        it, and a closed tab or a crashed browser still loses the password for
        good. **That is the failure this design accepts, and it is survivable
        precisely because the account is not lost with it:** the row carries
        ``must_change_password`` and no enrolment, so nobody can log in as it,
        and "Issue a new password" on this same screen mints another. The
        result page says so in as many words, because an administrator who
        does not know that is one who deletes the account and starts again —
        and this screen has no delete.

        A never-collected account is also legible from the list page without
        anyone being told: ``must_change_password`` set with ``MFA enrolled``
        and ``last login`` both empty is exactly "created, never used".

        **Proof, on top of the session.** Creating an account mints a working
        credential, so it asks for the current password or a live code the
        same way ``/admin/security`` does before adding an authenticator — see
        ``admin/auth.py::reauthenticate``. A stolen administrator session
        otherwise mints itself a second administrator account, with a password
        of its own choosing, and survives the theft being noticed. Required
        for a ``staff`` account too, not only an administrator one: one rule
        with no branch in it cannot be wrong on one side of the branch.

        ``self.templates`` rather than a module-level ``Jinja2Templates``,
        matching ``admin/blocklist_views.py``: the templates rendered here
        extend ``brand/base.html``, and this is the environment sqladmin
        assigned this view.
        """
        # sqladmin registers an @expose route on a ModelView with
        # `login_required` only and never calls `is_accessible` for it - see
        # this module's own docstring, and blocklist_views.py's. Without this
        # line a plain staff member could POST to /admin/staff/new directly.
        self._require_admin(request)

        if request.method == "GET":
            return await self._new_staff_page(request, self._new_staff_context(request))

        form = await request.form()
        if not check_token(request.session, form.get("csrf_token")):
            # Page-level, not inside the dialog: an expired token means this
            # whole page is stale and the fix is to start it again, so
            # reopening the confirmation step over a form that can no longer
            # be submitted would send somebody to retype a code for nothing.
            return await self._new_staff_page(
                request,
                self._new_staff_context(
                    request, error="That form expired. Please try again."
                ),
                status_code=400,
            )

        username = (form.get("username") or "").strip()
        display_name = (form.get("display_name") or "").strip()
        role_value = (form.get("role") or "").strip()
        # Echoed back on every refusal below.
        typed = {
            "username": username,
            "display_name": display_name,
            "role": role_value if role_value in self._ROLES else StaffRole.staff.value,
        }

        async def refuse(message):
            return await self._new_staff_page(
                request,
                self._new_staff_context(
                    request, error=message, open_dialog="confirm", **typed
                ),
                status_code=400,
            )

        role = self._ROLES.get(role_value)
        if role is None:
            return await refuse(
                "Choose whether this account is staff or an administrator."
            )

        actor = request.session.get(SESSION_KEY, "unknown")
        now = time.time()
        runtime = get_runtime(request)

        with self.session_maker() as session:
            try:
                acting = get_staff(session, actor)
            except UnknownStaffError:
                # _require_admin has already read this same row, so reaching
                # here means the account was removed between the two reads.
                raise HTTPException(status_code=403) from None

            problem = reauthenticate(
                session, acting, form, runtime=runtime, now=now
            )
            if problem is not None:
                # Nothing has been written yet - reauthenticate only reads,
                # and record_failure lives on the in-memory throttle - but
                # rolled back explicitly all the same, so this branch cannot
                # start depending on the session being clean.
                session.rollback()
                return await refuse(problem)

            try:
                # create_staff writes its own audit entry, inside this
                # transaction, so the row and the record of who added it land
                # or roll back together. This view adds none of its own.
                staff, password = create_staff(
                    session,
                    username=username,
                    display_name=display_name,
                    role=role,
                    actor=actor,
                )
            except (
                DuplicateUsernameError,
                InvalidDisplayNameError,
                InvalidUsernameError,
            ) as exc:
                session.rollback()
                return await refuse(str(exc))

            created = {
                "username": staff.username,
                "display_name": staff.display_name,
                "role": staff.role.value,
            }
            session.commit()

        # Rendered from this same response. The plaintext exists nowhere else
        # - not in the database, not in audit_log, not in a redirect target
        # that would need it in a URL or a flash message.
        return await self.templates.TemplateResponse(
            request,
            "brand/staff_created.html",
            {
                "created": created,
                "password": password,
                "list_url": self._list_url(request),
                "new_url": request.url_for("admin:view-staff-new_staff"),
            },
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

    # --- removing an account ------------------------------------------------

    @action(
        name="reactivate",
        label="Reactivate",
        confirmation_message=(
            "This lets the account log in again. Its password and "
            "authenticator are unchanged."
        ),
    )
    async def reactivate_action(self, request):
        """Undo a deactivation.

        **Here because ``delete`` requires deactivation first.** A mandatory
        step that cannot be undone is a trap: deactivating the wrong account
        would leave the two exits "leave it in the list for ever" and "delete
        it", and deleting was the capability the panel did not have at all.

        No re-authentication, and the line this sits on is
        ``deactivate_action``'s rather than ``new_staff``'s: it mints nothing
        and reveals nothing. Creating an account and issuing a password hand
        out a working credential and ask for proof on top of the session for
        that reason; this restores an account to whatever credentials it
        already had, which nobody learns by pressing it.

        No floor guard either — reactivation only ever *adds* an active
        account, so every count the floor protects moves upward. See
        ``admin/accounts.py::reactivate_staff``.
        """
        self._require_admin(request)
        pks = request.query_params.get("pks", "").split(",")
        actor = request.session.get(SESSION_KEY, "unknown")
        with self.session_maker() as session:
            for pk in filter(None, pks):
                staff = session.get(Staff, int(pk))
                if staff is None:
                    continue
                if staff.is_active:
                    # Already active. Skipped rather than refused: a bulk
                    # selection that happens to include one is a slip, not an
                    # error, and an audit entry claiming a change that did not
                    # happen is worse than no entry.
                    continue
                reactivate_staff(session, staff.username)
                write_audit(
                    session, actor=actor, action="update", table_name="staff",
                    row_id=staff.id, before={"is_active": False},
                    after={"is_active": True},
                )
            session.commit()
        return RedirectResponse(self._list_url(request), status_code=302)

    @action(name="delete", label="Delete permanently")
    async def delete_action(self, request):
        """Hand the selection to the confirmation page below.

        **This action performs nothing.** sqladmin registers an ``@action``
        with ``methods=["GET"]`` only, and deletion has to take a proof — the
        current password or a live code — which means a form, which means a
        POST. So the dropdown entry exists to carry the selected ``pks`` to a
        page that can ask, exactly as ``/admin/staff/new`` asks before it mints
        a credential.

        The alternative was a ``confirmation_message``, which is what the other
        three actions use. It is a browser ``confirm()``: one OK button, no
        proof, and nothing between a stolen session and an emptied staff list.
        """
        self._require_admin(request)
        pks = request.query_params.get("pks", "")
        return RedirectResponse(
            str(request.url_for("admin:view-staff-delete_page")) + f"?pks={pks}",
            status_code=302,
        )

    def _delete_context(self, request, accounts, **extra) -> dict:
        context = {
            "accounts": accounts,
            "error": None,
            "open_dialog": None,
            # Round-tripped through the form so the POST acts on the same
            # selection the page described. Read back from the database on
            # submission all the same - this is a convenience, never the
            # authority on what may be deleted.
            "pks": ",".join(str(a["id"]) for a in accounts),
            "list_url": self._list_url(request),
            "csrf_token": issue_token(request.session),
        }
        context.update(extra)
        return context

    async def _delete_page(self, request, context, status_code=200):
        return await self.templates.TemplateResponse(
            request, "brand/delete_staff.html", context, status_code=status_code
        )

    # The route name sqladmin gives this is `admin:view-staff-delete_page` —
    # `view-{identity}-{func.__name__}` for a ModelView (verified in
    # sqladmin/application.py::_handle_expose_decorated_func, which ignores the
    # `identity` argument entirely for model views). `delete_action` above
    # redirects to that name, so renaming this method changes a URL.
    @expose("/delete", methods=["GET", "POST"])
    async def delete_page(self, request):
        """Delete accounts outright, after saying exactly what that costs.

        **The capability the panel shipped without, and the reason it is worth
        having.** Only ``deactivate_staff`` existed, so an account created by
        mistake — or by an implementer testing something — became permanent
        furniture in a list administrators read to answer "who can get into
        this system". A list that cannot shrink stops being an answer to that
        question.

        **What deleting costs, stated on the page rather than only here.** The
        account's authenticators and recovery codes go with it, and its
        username becomes free for reuse. What does *not* go is anything it
        did: ``audit_log.actor`` is text and holds no foreign key onto
        ``staff``, so every entry the account wrote stays complete and still
        names it. That property is what made a hard delete the right answer
        rather than a tombstone, and staff pressing this button are entitled to
        know it holds — an administrator who thinks deletion erases the trail
        will avoid the button, or worse, use it hoping that it does.

        Every rule lives in ``admin/accounts.py::delete_staff``: the
        two-administrator floor, the refusal of self-deletion, and the
        requirement that the account already be deactivated.
        ``kaicalc-admin delete-staff`` reaches the same function and is refused
        the same way. This route adds one thing of its own, and it is the one
        thing a service function cannot see: proof that the request came from
        the person whose session it is riding on.
        """
        self._require_admin(request)

        pks = [pk for pk in request.query_params.get("pks", "").split(",") if pk]
        if request.method == "POST":
            form = await request.form()
            pks = [pk for pk in (form.get("pks") or "").split(",") if pk]

        actor = request.session.get(SESSION_KEY, "unknown")
        with self.session_maker() as session:
            accounts = []
            for pk in pks:
                try:
                    staff = session.get(Staff, int(pk))
                except ValueError:
                    continue
                if staff is None:
                    continue
                accounts.append({
                    "id": staff.id,
                    "username": staff.username,
                    "display_name": staff.display_name,
                    "role": staff.role.value,
                    "is_active": staff.is_active,
                    "device_count": len(staff.totp_devices),
                })

            if request.method == "GET":
                return await self._delete_page(
                    request, self._delete_context(request, accounts)
                )

            async def refuse(message, status_code=400):
                return await self._delete_page(
                    request,
                    self._delete_context(
                        request, accounts, error=message, open_dialog="confirm"
                    ),
                    status_code=status_code,
                )

            if not check_token(request.session, form.get("csrf_token")):
                session.rollback()
                return await refuse("That form expired. Please try again.")
            if not accounts:
                session.rollback()
                return await refuse("Nothing was selected to delete.")

            try:
                acting = get_staff(session, actor)
            except UnknownStaffError:
                raise HTTPException(status_code=403) from None

            problem = reauthenticate(
                session, acting, form, runtime=get_runtime(request), now=time.time()
            )
            if problem is not None:
                session.rollback()
                return await refuse(problem)

            deleted = []
            for account in accounts:
                try:
                    # delete_staff writes its own audit entry, in this
                    # transaction, so the removal and the record of who made it
                    # land or roll back together.
                    delete_staff(session, account["username"], actor=actor)
                except SelfRecoveryError as exc:
                    return await self._refuse_self_recovery(
                        request, session, exc,
                        actor=actor, staff_id=account["id"], slug="delete",
                    )
                except AccountStillActiveError as exc:
                    session.rollback()
                    return await self._refuse(
                        request, str(exc),
                        "Deactivating is the reversible step and deleting is "
                        "not, so the panel makes you take them one at a time. "
                        "Deactivate ends the account's sessions and refuses "
                        "its next login; if you change your mind, Reactivate "
                        "puts it back exactly as it was.",
                    )
                except LastAdministratorsError as exc:
                    session.rollback()
                    return await self._refuse(
                        request, str(exc),
                        "The panel keeps at least two active administrators. "
                        "With no email system to recover through, one "
                        "administrator is one lost phone away from a panel "
                        "nobody can enter.",
                    )
                deleted.append(account)
            session.commit()

        return await self.templates.TemplateResponse(
            request, "brand/staff_deleted.html",
            {"deleted": deleted, "list_url": self._list_url(request)},
        )

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
