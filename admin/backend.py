"""sqladmin authentication backend.

Two things sqladmin makes possible that this design relies on:

* ``login()`` may return a Response instead of True. The password step
  returns a redirect to the second factor, so a correct password alone never
  establishes a session.
* ``authenticate()`` may return a Response too, and ``login_required`` wraps
  every admin route with it. Both onboarding gates therefore live in one
  place and no route can miss them - which is what contract 8.3 means by
  "enrolment is not advisory".
"""

import time

from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from admin.accounts import UnknownStaffError, get_staff
from admin.auth import SESSION_KEY, PendingLogin, authenticate_password
from admin.config import Settings

PENDING_SESSION_KEY = "pending_login"

#: Paths a partially-onboarded account may still reach, or the gate would
#: redirect the very page that clears it.
_ONBOARDING_EXEMPT = ("/admin/change-password", "/admin/enrol", "/admin/logout")


class AdminAuth(AuthenticationBackend):
    def __init__(self, *, settings: Settings, app) -> None:
        super().__init__(
            secret_key=settings.secret_key,
            max_age=settings.session_max_age_minutes * 60,
            https_only=False,  # dev; the deployment terminates TLS upstream
            same_site="lax",
        )
        self._settings = settings
        self._app = app

    def _session_factory(self):
        return self._app.state.session_factory

    async def login(self, request: Request) -> Response | bool:
        form = await request.form()
        username = (form.get("username") or "").strip()
        password = form.get("password") or ""
        now = time.time()

        with self._session_factory()() as db:
            pending = authenticate_password(
                db,
                username,
                password,
                throttle=self._app.state.throttle,
                now=now,
            )
            db.commit()

        if pending is None:
            return False  # sqladmin re-renders the login page with an error

        request.session[PENDING_SESSION_KEY] = {
            "username": pending.username,
            "expires_at": pending.expires_at,
        }
        return RedirectResponse(request.url_for("admin:view-verify"), status_code=302)

    async def logout(self, request: Request) -> Response | bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> Response | bool:
        path = request.url.path.rstrip("/")

        # The second-factor page sits behind this method too - @expose wraps
        # every route in login_required and there is no opt-out. At that point
        # the user has a pending login and deliberately no SESSION_KEY, so
        # falling through to the check below would bounce them to /admin/login
        # and the flow could never complete. The view itself validates the
        # pending value's expiry and consumes it; this only decides reachability.
        if path == "/admin/verify":
            return bool(request.session.get(PENDING_SESSION_KEY))

        username = request.session.get(SESSION_KEY)
        if not username:
            return False

        with self._session_factory()() as db:
            try:
                staff = get_staff(db, username)
            except UnknownStaffError:
                request.session.clear()
                return False

            # Re-read on every request: the cookie is signed but client-held,
            # so there is no server-side session to invalidate.
            if not staff.is_active:
                request.session.clear()
                return False

            if path in _ONBOARDING_EXEMPT:
                return True
            if staff.must_change_password:
                return RedirectResponse(
                    request.url_for("admin:view-change-password"), status_code=302
                )
            if not staff.mfa_enrolled:
                return RedirectResponse(
                    request.url_for("admin:view-enrol"), status_code=302
                )

        return True
