"""Staff authentication.

Contract: docs/interfaces.md 8.3 and 8.4. The only module here that knows
about HTTP requests and session cookies; everything below it takes plain
values.

A login is a two-step handshake and the two steps are joined by a value, not
by a convention:

    authenticate_password(...)  ->  PendingLogin | None
    authenticate_totp(pending, ...)            ->  username | None
    authenticate_recovery_code(pending, ...)   ->  username | None

The second-factor calls take the ``PendingLogin`` the password step produced
and refuse anything else, so the ordering contract 8.3 requires cannot be
skipped by a caller who only reads the signatures. Only the username returned
by a second-factor call belongs under ``SESSION_KEY``.
"""

import secrets
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from admin.accounts import (
    MfaNotEnrolledError,
    UnknownStaffError,
    consume_recovery_code,
    get_staff,
    verify_staff_totp,
)
from admin.models import Staff, utcnow
from admin.security import hash_password, verify_password
from admin.throttle import LoginThrottle

#: Key under which the authenticated username is held in the session cookie.
SESSION_KEY = "staff_username"

#: Key under which the generation the session was minted under is held.
#: Compared against Staff.session_generation on every request so that a
#: credential change ends the sessions that predate it. See stamp_session.
SESSION_GENERATION_KEY = "staff_generation"

#: How long a passed password step stays usable. Long enough to read a code
#: off a phone, short enough that a login abandoned on a shared machine cannot
#: be finished by whoever sits down next.
PENDING_LOGIN_TTL_SECONDS = 300

#: The same allowance, for the one onboarding step that is not "read a code off
#: a phone" — enrolment. Reading a code assumes the phone already has the
#: account on it; enrolment is the request to put it there, and what stands
#: between the QR appearing and the code being typed is installing an
#: authenticator app on a second device, finding the camera, and waiting out
#: whichever time step is half-elapsed. Five minutes is a plausible budget for
#: none of that, and the deadline lands precisely on the request that carries
#: the recovery codes — shown once, never recoverable — so the cost of it
#: being too short is not an extra login, it is an account with a second
#: factor and no way back.
#:
#: Fifteen minutes is a bound, not a licence to idle: it is granted from the
#: moment the enrolment page renders (admin/views.py EnrolView) and only to
#: that page, which the gate opens only for an account that is not yet
#: enrolled. What a pending login can reach in that window is unchanged — the
#: three onboarding pages — and the holder already presented the password, so
#: the longer window grants nothing that logging in again would not.
ENROLMENT_WINDOW_SECONDS = 900

#: A real bcrypt hash of a value nothing can supply, verified against when the
#: username does not exist so that both paths cost the same. Computed once at
#: import: hashing per request would itself be a measurable difference, and a
#: hardcoded constant would drift from the project's cost factor.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


class StaffAuthRequired(Exception):
    """No usable staff session. The API layer maps this to UNAUTHORIZED (401)."""


@dataclass(frozen=True)
class PendingLogin:
    """A password step that has passed and a second factor that has not.

    This is deliberately not a username and not a session: it is the only
    thing the second-factor calls accept, which is what makes contract 8.3's
    ordering a property of the types rather than of whoever writes the login
    page. It is also what authorises the forced password-change and enrolment
    pages, which by definition are reached before any second factor exists.

    Both fields are plain data so the value can be carried between the two
    requests in the signed session cookie. ``expires_at`` is on the same clock
    as the ``now`` passed to every function here.
    """

    username: str
    expires_at: float

    def is_expired(self, *, now: float) -> bool:
        return now >= self.expires_at


def authenticate_password(
    session: Session,
    username: str,
    password: str,
    *,
    throttle: LoginThrottle,
    now: float,
    pending_ttl_seconds: int = PENDING_LOGIN_TTL_SECONDS,
) -> PendingLogin | None:
    """Check a username and password, honouring the throttle.

    Returns a short-lived PendingLogin on success and None on any failure. A
    password alone never grants access (contract 8.3), so nothing here writes
    a session, records a login time or touches the failure counter's cleared
    state — a half-finished handshake is not a login.

    In particular the counter is **not** cleared here. Clearing on a correct
    password would let an attacker who holds the password alternate
    password-success with wrong second-factor codes and never reach the
    threshold, which is precisely the 10^6 search contract 8.3 names as the
    reason for one shared counter.

    ``must_change_password`` and an unfinished MFA enrolment are not checked:
    a user has to get through the password step before they can complete
    either. require_staff_username is what refuses those accounts everywhere
    except the setup pages.

    A failure against a username that does not exist still counts towards the
    throttle. Skipping it would turn the throttle into an oracle for which
    usernames are real.
    """
    if throttle.is_locked(username, now=now):
        return None

    try:
        staff = get_staff(session, username)
    except UnknownStaffError:
        # Verify against a dummy hash rather than returning here. The throttle
        # already refuses to reveal which usernames exist; an early return
        # would reveal it through the clock instead — measured at 3.1 ms
        # against 173.8 ms before this line existed.
        verify_password(password, _DUMMY_HASH)
        throttle.record_failure(username, now=now)
        return None

    # Evaluated unconditionally, before the branch, rather than written as
    # `not staff.is_active or not verify_password(...)`. That form short-
    # circuits on a deactivated account and skips verify_password entirely,
    # which reopens exactly the timing oracle _DUMMY_HASH exists to close —
    # only now against "is this real username's account deactivated?"
    # instead of "does this username exist?". Every found account, active or
    # not, must pay for one verify_password call so the clock cannot tell
    # the two apart. Do not "simplify" this back into a short circuit.
    password_ok = verify_password(password, staff.password_hash)
    if not staff.is_active or not password_ok:
        throttle.record_failure(username, now=now)
        return None

    return PendingLogin(
        username=staff.username, expires_at=now + pending_ttl_seconds
    )


def _complete_login(
    session: Session,
    pending: PendingLogin,
    *,
    throttle: LoginThrottle,
    now: float,
    verify: Callable[[str], bool],
) -> str | None:
    """Shared second-factor path: check the handshake, then the factor.

    Returns the username to place under SESSION_KEY, or None. Success here is
    what completes a login, so this is the one place that clears the shared
    failure counter and stamps ``last_login_at``.

    The account row is re-read rather than trusted from the pending value, so
    a deactivation that lands between the two steps takes effect immediately.

    An expired or malformed handshake is refused without recording a failure.
    It is a flow error rather than a credential guess, and charging it to the
    counter would let a user lock themselves out by walking away from the
    second-factor page.
    """
    if not isinstance(pending, PendingLogin):
        raise TypeError(
            "The second factor requires the PendingLogin returned by "
            "authenticate_password. A password step must have passed first "
            "(contract 8.3)."
        )
    if pending.is_expired(now=now):
        return None

    username = pending.username
    if throttle.is_locked(username, now=now):
        return None

    try:
        staff = get_staff(session, username)
    except UnknownStaffError:
        throttle.record_failure(username, now=now)
        return None

    if not staff.is_active or not staff.mfa_enrolled:
        throttle.record_failure(username, now=now)
        return None

    try:
        accepted = verify(staff.username)
    except (UnknownStaffError, MfaNotEnrolledError):
        accepted = False

    if not accepted:
        throttle.record_failure(username, now=now)
        return None

    throttle.clear(username)
    staff.last_login_at = utcnow()
    return staff.username


def authenticate_totp(
    session: Session,
    pending: PendingLogin,
    code: str,
    *,
    throttle: LoginThrottle,
    secret_key: str,
    now: float,
) -> str | None:
    """Check the second factor, sharing the throttle with the password step.

    Takes the PendingLogin from authenticate_password and returns the
    authenticated username, or None. There is no username parameter: the only
    account this can authenticate is the one the password step already passed.

    Contract 8.3 requires one counter across both steps. Throttling only the
    password would leave a six-digit second factor — a 10^6 search space —
    open to anyone who already holds the password, which is precisely the
    position an attacker is in by the time they reach this call.

    A missing account, a deactivated one or an unfinished enrolment is
    reported as an ordinary failure rather than allowed to propagate, so the
    response cannot be used to learn which accounts exist or which have
    finished enrolling.
    """
    return _complete_login(
        session,
        pending,
        throttle=throttle,
        now=now,
        verify=lambda username: verify_staff_totp(
            session, username, code, secret_key=secret_key, now=int(now)
        ),
    )


def authenticate_recovery_code(
    session: Session,
    pending: PendingLogin,
    code: str,
    *,
    throttle: LoginThrottle,
    now: float,
) -> str | None:
    """Redeem a recovery code in place of a TOTP: contract 8.3, layer L1.

    The layer that does not need a second human being — a lost or wiped
    authenticator, with no email system to fall back on. It stands in for the
    second factor and is therefore held to the same rules: it needs the same
    PendingLogin, it shares the same counter, and each code is spent once.
    """
    return _complete_login(
        session,
        pending,
        throttle=throttle,
        now=now,
        verify=lambda username: consume_recovery_code(session, username, code),
    )


def stamp_session(session_data: dict, staff: Staff) -> None:
    """Write both halves of the session identity.

    Callers must never set SESSION_KEY on its own — a session with no
    generation is refused by require_staff_username, so a half-stamped
    session is a login that silently does not work.
    """
    session_data[SESSION_KEY] = staff.username
    session_data[SESSION_GENERATION_KEY] = staff.session_generation


def require_staff_username(session: Session, session_data: dict) -> str:
    """Return the authenticated username, or raise StaffAuthRequired.

    The account state is re-read on every call rather than trusted from the
    cookie. The session cookie is signed but client-held, so there is no
    server-side session to invalidate: deactivating an account would otherwise
    take effect only when the cookie expired.

    An account that has not completed the forced password change or MFA
    enrolment is refused here too. Contract 8.3 requires those steps to be
    unavoidable, and a check that lives only in the enrolment page's own
    routing can be walked past by typing a URL.

    admin/backend.py's AdminAuth.authenticate() duplicates the generation
    comparison below inline rather than calling this function - it is not
    reachable from a live request yet, per require_staff's own docstring.
    The two have already drifted once (a generation check landing here
    without landing there), so a change to either belongs alongside a look
    at the other.
    """
    username = session_data.get(SESSION_KEY)
    if not username:
        raise StaffAuthRequired("No staff session")

    try:
        staff = get_staff(session, username)
    except UnknownStaffError as exc:
        raise StaffAuthRequired("Session names an account that no longer exists") from exc

    if not staff.is_active:
        raise StaffAuthRequired("Account is deactivated")
    if staff.must_change_password:
        raise StaffAuthRequired("Password change is outstanding")
    if not staff.mfa_enrolled:
        raise StaffAuthRequired("Authenticator enrolment is outstanding")
    if session_data.get(SESSION_GENERATION_KEY) != staff.session_generation:
        raise StaffAuthRequired("Credentials changed since this session began")

    return staff.username


def require_staff(request) -> str:
    """Contract 8.4 entry point, called by B from the API layer.

    Thin adapter: pulls the session dict off the Starlette request and applies
    require_staff_username. B calls this and nothing else.

    Expects a database session on ``request.state.db``, placed there by
    middleware that the next plan installs alongside the sqladmin mount. Until
    that exists this function cannot be called from a live request — which is
    why the rule it enforces is tested through require_staff_username, against
    a plain dict. Testing it through a constructed Request would be testing
    Starlette rather than our logic.
    """
    db_session = request.state.db
    return require_staff_username(db_session, request.session)
