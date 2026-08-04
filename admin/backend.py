"""sqladmin authentication backend.

Two things sqladmin makes possible that this design relies on:

* ``login()`` may return a Response instead of True. A correct password
  redirects to whichever step the account still owes - forced password
  change, forced MFA enrolment, or the second factor - rather than
  establishing a session by itself.
* ``authenticate()`` may return a Response too, and ``login_required`` wraps
  every route ``@expose`` registers with it. Both onboarding gates therefore
  live in one place and no exposed view can miss them - which is what
  contract 8.3 means by "enrolment is not advisory". (``/admin/login``,
  ``/admin/logout`` and the static files mount are routes sqladmin/FastAPI
  register directly, outside ``login_required``, and do not need to be
  covered by this - ``logout`` in particular has to keep working regardless
  of what state the session is in.)
"""

import time

from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from admin.accounts import UnknownStaffError, get_staff
from admin.auth import SESSION_KEY, PendingLogin, authenticate_password
from admin.config import Settings

PENDING_SESSION_KEY = "pending_login"

#: Reachable before a session exists at all. admin.auth.PendingLogin's own
#: docstring names the reason: "what authorises the forced password-change
#: and enrolment pages, which by definition are reached before any second
#: factor exists" - the second factor's own page belongs in the same set for
#: the identical reason. authenticate() admits all three on a live
#: SESSION_KEY too (see below), so an administrator forcing an already
#: logged-in account back through either step mid-session does not get
#: locked in a redirect loop. /admin/logout is deliberately absent: sqladmin
#: registers it without login_required, so authenticate() is never consulted
#: for it in the first place.
_PRE_LOGIN_PAGES = ("/admin/verify", "/admin/change-password", "/admin/enrol")


def _pending_login_from_session(session_data: dict) -> PendingLogin | None:
    """Reconstruct the PendingLogin login() stored, or None.

    The session cookie only ever carries plain data (it is JSON underneath
    the signature), so this is what lets authenticate() reuse
    PendingLogin.is_expired() rather than re-implementing the comparison.
    Defensive against a missing or malformed value rather than trusting the
    shape: the cookie is signed but still client-held.
    """
    raw = session_data.get(PENDING_SESSION_KEY)
    if not isinstance(raw, dict):
        return None
    try:
        return PendingLogin(username=raw["username"], expires_at=raw["expires_at"])
    except (KeyError, TypeError):
        return None


def current_username(session_data: dict) -> str | None:
    """The account a request is acting on, before or after the second factor.

    Tasks 5-7's three pre-login pages run with only a PendingLogin and no
    SESSION_KEY yet - authenticate() has already decided reachability
    (including expiry) by the time a page's own handler runs, so this only
    extracts *which* account, preferring an established SESSION_KEY (the
    defence-in-depth case: an administrator forced an already-logged-in
    account back through one of these pages) and falling back to the
    pending value. Returns None with neither, which a caller should not be
    able to reach given authenticate() has already run first.
    """
    username = session_data.get(SESSION_KEY)
    if username:
        return username
    pending = session_data.get(PENDING_SESSION_KEY)
    if isinstance(pending, dict):
        return pending.get("username")
    return None


class AdminAuth(AuthenticationBackend):
    def __init__(self, *, settings: Settings, app) -> None:
        super().__init__(
            secret_key=settings.secret_key,
            max_age=settings.session_max_age_minutes * 60,
            # Defaults True; SESSION_HTTPS_ONLY=false is for local http
            # development only - see admin/config.py and .env.example.
            https_only=settings.session_https_only,
            same_site="lax",
        )
        self._settings = settings
        self._app = app

    def _session_factory(self):
        return self._app.state.session_factory

    async def login(self, request: Request) -> Response | bool:
        # First statement, unconditionally: without this, a login attempt on
        # a machine that already carries someone else's SESSION_KEY - the
        # normal case on a three-to-five person team's shared machine, not
        # an edge case - leaves that account's session in place under a
        # second person's cookie, whether their own attempt succeeds or
        # fails. audit_log.actor (contract 8.4) would then record the first
        # person's username for the second person's actions.
        request.session.clear()

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
            if pending is None:
                db.commit()
                return False  # sqladmin re-renders the login page with an error

            # authenticate_password already re-read this row to check the
            # password; re-reading it here is what decides which step a
            # freshly-passed password lands on, in the order contract 8.3
            # requires an account complete them: password change, then MFA
            # enrolment, then (once both are done) the second factor. A
            # fresh account - exactly the state bootstrap leaves admin and
            # admin2 in - must land on the first page it can actually use.
            staff = get_staff(db, pending.username)
            if staff.must_change_password:
                target = "admin:view-change-password"
            elif not staff.mfa_enrolled:
                target = "admin:view-enrol"
            else:
                target = "admin:view-verify"
            db.commit()

        request.session[PENDING_SESSION_KEY] = {
            "username": pending.username,
            "expires_at": pending.expires_at,
        }
        return RedirectResponse(request.url_for(target), status_code=302)

    async def logout(self, request: Request) -> Response | bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> Response | bool:
        path = request.url.path.rstrip("/")

        # The three pre-login pages sit behind this method too - @expose
        # wraps every route in login_required and there is no opt-out. A
        # user reaching any of them has a PendingLogin and deliberately no
        # SESSION_KEY yet, so falling through to the SESSION_KEY check below
        # would bounce them all to /admin/login and no account could ever
        # finish onboarding - true of a fresh account with nowhere else to
        # go, not just of the second factor. An established SESSION_KEY also
        # admits them (see the defence-in-depth branches further down).
        # Exact membership, not a prefix match: this set is exactly these
        # three paths and nothing under them.
        if path in _PRE_LOGIN_PAGES:
            if request.session.get(SESSION_KEY):
                return True
            pending = _pending_login_from_session(request.session)
            return pending is not None and not pending.is_expired(now=time.time())

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

            # Defence in depth, not the primary path any more: login() above
            # already sends a freshly-authenticated account to whichever of
            # these it still owed, so an account reaching here with a live
            # SESSION_KEY has normally cleared both already. These branches
            # instead catch an administrator resetting someone's MFA, or
            # forcing a password change, in the middle of an existing
            # session - the very next request finds it here, at the same
            # two pages _PRE_LOGIN_PAGES already keeps reachable.
            if staff.must_change_password:
                return RedirectResponse(
                    request.url_for("admin:view-change-password"), status_code=302
                )
            if not staff.mfa_enrolled:
                return RedirectResponse(
                    request.url_for("admin:view-enrol"), status_code=302
                )

        return True
