"""The account administration screen. Contract §8.3.

There is no email system, deliberately, so this screen is recovery layer L2:
one administrator restoring another's access in person. Everything it does
is destructive to someone's access, so every action is audited and the
two-administrator floor is enforced on every path.

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
    form_columns = [Staff.display_name, Staff.role, Staff.is_active]
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
                password = issue_password(session, staff.username, actor=actor)
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
                reset_mfa(session, staff.username)
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
                    return await self.templates.TemplateResponse(
                        request, "brand/action_refused.html",
                        {"message": str(exc), "next_url": self._list_url(request)},
                        status_code=400,
                    )
                write_audit(
                    session, actor=actor, action="update", table_name="staff",
                    row_id=staff.id, before={"is_active": True},
                    after={"is_active": False},
                )
            session.commit()
        return RedirectResponse(self._list_url(request), status_code=302)
