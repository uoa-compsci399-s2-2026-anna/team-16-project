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
#: docstring names the reason these pages must be reachable pre-session:
#: "what authorises the forced password-change and enrolment pages, which by
#: definition are reached before any second factor exists" - the second
#: factor's own page belongs in the same set for the identical reason. A
#: bare pending value is necessary but not sufficient, though - see
#: _may_open_pre_login_page, which also re-reads the account and gates each
#: page on the specific state it exists to clear. /admin/logout is
#: deliberately absent: sqladmin registers it without login_required, so
#: authenticate() is never consulted for it in the first place.
_PRE_LOGIN_PAGES = ("/admin/verify", "/admin/change-password", "/admin/enrol")


def _is_pre_login_page(path: str) -> bool:
    """Exact membership, never a prefix match.

    A prefix check would let any future path merely beginning with one of
    these three strings reach the same reduced-security gate - e.g. a
    hypothetical /admin/enrolment/list ModelView route matching a
    startswith("/admin/enrol") check - and nothing is registered under any
    of these three paths today to expose that through an ordinary
    end-to-end HTTP test. Mutation testing confirmed the gap directly:
    changing the ``in`` below to ``startswith()`` left the whole existing
    suite green.
    """
    return path in _PRE_LOGIN_PAGES


def _pending_login_from_session(session_data: dict) -> PendingLogin | None:
    """Reconstruct the PendingLogin login() stored, or None.

    The session cookie only ever carries plain data (it is JSON underneath
    the signature), so this is what lets authenticate() reuse
    PendingLogin.is_expired() rather than re-implementing the comparison.

    Validates the shape explicitly rather than relying on a try/except
    around the PendingLogin construction: PendingLogin is a plain frozen
    dataclass with no field validation of its own, so construction succeeds
    with a value of any type, and a malformed ``expires_at`` (missing,
    ``None``, or a non-numeric string) would otherwise reach
    ``is_expired()``'s comparison and raise there instead - a session is
    signed but still client-held, so this has to be checked, not assumed.
    """
    raw = session_data.get(PENDING_SESSION_KEY)
    if not isinstance(raw, dict):
        return None
    username = raw.get("username")
    expires_at = raw.get("expires_at")
    if not isinstance(username, str) or not isinstance(expires_at, (int, float)):
        return None
    return PendingLogin(username=username, expires_at=expires_at)


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

    def _may_open_pre_login_page(self, request: Request, path: str) -> bool:
        """Whether ``path`` - one of _PRE_LOGIN_PAGES - is reachable right now.

        There are two ways in - an established SESSION_KEY, or a pending
        login from the password step - and four account states the two
        onboarding booleans can be in. Rounds 1-3 of review each found a
        real defect of the same shape: a rule applied to one way in, or
        one state, and not to its symmetric partner. So this function
        deliberately has no per-way-in branch past the point the username
        is resolved. Resolve the username from an established SESSION_KEY
        or, short of that, a valid non-expired pending login; read the row
        exactly once; refuse and clear the session for an unknown or
        inactive account; then apply one state gate, the same one, however
        the caller got here.

        A live SESSION_KEY is NOT a reason to skip that gate, and this is
        the least obvious thing in the file. Contract 8.3's
        non-deactivating eviction - reset the account's MFA, issue a new
        password - is by definition performed against a live session, and
        it is the *only* eviction available while _guard_admin_floor
        refuses to deactivate an administrator (the normal state of a
        two-administrator deployment). That reset clears mfa_enrolled_at,
        which is also what removes begin_mfa_enrolment's own
        MfaAlreadyEnrolledError backstop - so nothing behind this gate
        would stop the person being evicted from enrolling a fresh
        authenticator, minting new recovery codes and setting a password
        of their choosing, holding both factors again with audit_log.actor
        naming the victim throughout. A live SESSION_KEY carrying
        must_change_password, or lacking enrolment, is unreachable through
        any ordinary login: SESSION_KEY is written only by
        admin.auth._complete_login, which refuses an unenrolled account
        outright, and which can only be reached through /admin/verify -
        a page the gate below opens only once *both* are cleared. So this
        branch is exercised *only* in the states an administrator's
        mid-session reset creates, which is precisely why three rounds of
        review drove the app and never saw it.

        Each page is open exactly while the account still owes that step,
        in the ladder order contract 8.3 requires:

        * /admin/change-password - only while must_change_password is set
        * /admin/enrol           - only once that is cleared *and* the
                                    account is not yet MFA-enrolled, so the
                                    ladder order binds on the gate too, not
                                    just on where login() sends a fresh
                                    password - otherwise a brand-new
                                    account satisfies both change-password
                                    and enrol at once
        * /admin/verify          - only once *both* of the above are
                                    cleared, so a half-onboarded account
                                    cannot skip ahead to the second factor

        The three conditions are mutually exclusive and total: every
        account state reaches exactly the one page matching what it owes
        next.

        The one capability this costs is a fully onboarded, logged-in user
        voluntarily opening /admin/change-password - which today *is* the
        forced-change page. A voluntary "change my password" route belongs
        on its own path, not on this gate.
        """
        username = request.session.get(SESSION_KEY)
        if not username:
            pending = _pending_login_from_session(request.session)
            if pending is None or pending.is_expired(now=time.time()):
                return False
            username = pending.username

        with self._session_factory()() as db:
            try:
                staff = get_staff(db, username)
            except UnknownStaffError:
                request.session.clear()
                return False
            if not staff.is_active:
                request.session.clear()
                return False

            if path == "/admin/change-password":
                return staff.must_change_password
            if path == "/admin/enrol":
                return not staff.must_change_password and not staff.mfa_enrolled
            if path == "/admin/verify":
                return not staff.must_change_password and staff.mfa_enrolled

        # Unreachable while _is_pre_login_page gates the only call site, and
        # deliberately not folded into the /admin/verify branch above: a
        # fourth entry added to _PRE_LOGIN_PAGES without a matching branch
        # here should be refused, not silently inherit whichever rule
        # happened to be written last. Missing exactly that kind of
        # symmetric case is what three rounds of review found here.
        return False

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
        # go, not just of the second factor. See _may_open_pre_login_page
        # for what "reachable" actually requires, which since review is
        # more than merely holding a pending value.
        if _is_pre_login_page(path):
            return self._may_open_pre_login_page(request, path)

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
