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
from admin.auth import SESSION_GENERATION_KEY, SESSION_KEY, PendingLogin, authenticate_password
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
        one state, and not to its symmetric partner. So the default here is
        that there is no per-way-in branch past the point the username is
        resolved. Resolve the username from an established SESSION_KEY or,
        short of that, a valid non-expired pending login; read the row
        exactly once; refuse and clear the session for an unknown or
        inactive account; then apply one state gate, the same one, however
        the caller got here.

        **/admin/enrol is the one deliberate exception, and it is not an
        oversight - do not "simplify" it back.** That default is a
        heuristic against a specific historical failure, not a law, and
        enrolment is the case it does not cover. A legitimate re-enrolment
        after an administrator resets someone's MFA always arrives through
        a fresh password step, because the administrator has just issued a
        new password to the rightful holder; the pending way in is
        therefore the only one that ever needs this page. An existing
        session is, by construction, the credential the reset was meant to
        neutralise. See the branch below.

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
        naming the victim throughout. A live SESSION_KEY lacking enrolment
        is unreachable through any ordinary login - SESSION_KEY is written
        only by admin.auth._complete_login, which refuses an unenrolled
        account outright - so this is exercised *only* in the state an
        administrator's mid-session reset creates, which is precisely why
        four rounds of review drove the app and did not see it.

        Note which state that is, because round 4 got it wrong and the
        gate below is written for the corrected one. The eviction lands on
        must_change_password=**False**, not True: set_password clears the
        flag (admin/accounts.py:189) and nothing in admin/ sets it back -
        create_staff (:177) is the only writer of True, and there is no
        issue_password or reset_password service function to call. So the
        post-eviction row is (False, False), which is indistinguishable by
        state alone from an ordinary account midway through onboarding.
        No state gate can separate those two, which is why the way in has
        to carry the distinction for this one page.

        Each page is open exactly while the account still owes that step,
        in the ladder order contract 8.3 requires:

        * /admin/change-password - only while must_change_password is set
        * /admin/enrol           - only once that is cleared, the account
                                    is not yet MFA-enrolled, *and* the
                                    caller arrived on a pending login
                                    rather than an established session
                                    (the exception described above). The
                                    first two conditions keep the ladder
                                    order binding on the gate and not just
                                    on where login() sends a fresh
                                    password - otherwise a brand-new
                                    account satisfies both
                                    change-password and enrol at once
        * /admin/verify          - only once *both* onboarding flags are
                                    cleared, so a half-onboarded account
                                    cannot skip ahead to the second factor

        Two capabilities this costs, both deliberate:

        * a fully onboarded, logged-in user cannot voluntarily open
          /admin/change-password - which today *is* the forced-change
          page. A voluntary "change my password" route belongs on its own
          path, not on this gate.
        * a live session whose MFA was just reset is bounced to
          /admin/login rather than straight into enrolment. That is the
          point: enrolling requires the newly issued password, which only
          the rightful holder has.

          This used to be the only thing standing between an evicted
          cookie and the panel, and only for the MFA-reset case - a
          password-only change touches neither onboarding flag, so this
          gate alone could not tell an evicted cookie from a live one.
          ``staff.session_generation`` (contract 8.3/8.4) closes the
          ordinary case in ``authenticate()``'s main branch below, which is
          what every ordinary panel URL - including ``/admin/`` - passes
          through. It is not enough on its own, though: the state gates
          just below are evaluated on ``staff`` alone, and
          ``staff.must_change_password`` set by a future
          ``issue_password()`` (contract 8.3's still-open known
          limitation - "sets a random password **and**
          ``must_change_password = True``") would satisfy
          /admin/change-password's own gate for *any* session naming this
          account, stale or not, the moment that function lands. That
          page's own password check (``_password_problem``,
          admin/views.py) verifies length, confirmation and non-reuse of
          the issued password; it does not verify which session is
          presenting it. Left unguarded here, an evicted attacker's
          stolen-but-still-signed cookie could walk straight through
          /admin/change-password, set a password of its own choosing, and
          be re-stamped with the fresh generation on the way out
          (``ChangePasswordView`` re-stamps on every completed change) -
          the eviction undone by the very page meant to complete it. The
          hazard is a new *state* ``issue_password()`` introduces, not a
          new page, so the "only three pages exist" defence this docstring
          used to rely on does not cover it.

          So: a session that carries a generation **at all** must carry
          the right one, on every way into this function - not only the
          already-logged-in one. Absence is not the same as mismatch and
          stays admissible: a genuine first-login user arrives on a
          PendingLogin with no ``SESSION_KEY`` and no generation
          whatsoever (``login()`` never stamps one; ``stamp_session`` only
          ever runs after a second factor completes), and that path has to
          keep working regardless of what any row's ``session_generation``
          happens to be. What gets refused is narrower and deliberate: an
          already-logged-in cookie that WAS stamped, at a generation the
          account has since moved past. See the comparison just below the
          account-state checks.

          This duplicates the comparison ``authenticate()``'s main branch
          makes rather than sharing it, for the reason given there - and
          the two having already drifted once (this fix, and the gate fix
          before it) is exactly why each names the other in comment form.
        """
        username = request.session.get(SESSION_KEY)
        already_logged_in = bool(username)
        if not already_logged_in:
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

            # A generation that is present but wrong is refused; a generation
            # that is absent is not - see the docstring's "so:" paragraph for
            # why the two are not the same case here. request.session.get
            # returns None for a pending-login-only session (no SESSION_KEY,
            # no generation at all), which must fall through unrefused.
            generation = request.session.get(SESSION_GENERATION_KEY)
            if generation is not None and generation != staff.session_generation:
                request.session.clear()
                return False

            if path == "/admin/change-password":
                return staff.must_change_password
            if path == "/admin/enrol":
                # ``not already_logged_in`` is the one way-in distinction in
                # this function and it is load-bearing, not tidy-up: the
                # state an eviction leaves behind is (must_change_password
                # False, mfa_enrolled False), the same row as an ordinary
                # account partway through onboarding, so no condition on
                # ``staff`` can tell the evicted party from the rightful
                # holder. What separates them is the newly issued password,
                # which only the rightful holder has - and presenting it is
                # exactly what produces a pending login. Requiring one here
                # is therefore requiring the factor the eviction reissued.
                return (
                    not already_logged_in
                    and not staff.must_change_password
                    and not staff.mfa_enrolled
                )
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

            # Same semantics as require_staff_username (admin/auth.py): a
            # session carrying no generation at all, or one naming a
            # generation this account has since moved past, is refused
            # outright here - not redirected to onboarding, which the
            # branches below do for must_change_password/mfa_enrolled. A
            # stale SESSION_KEY does not necessarily owe onboarding: a
            # password-only change bumps the generation but clears
            # must_change_password and leaves mfa_enrolled untouched, so
            # neither of those branches would catch it. This duplicates
            # require_staff_username's comparison rather than calling it -
            # that function is not reachable from a live request yet (see
            # its own docstring) - so a future change to either check
            # belongs alongside a look at the other; they have already
            # drifted once, which is how this gate shipped without it.
            if request.session.get(SESSION_GENERATION_KEY) != staff.session_generation:
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
