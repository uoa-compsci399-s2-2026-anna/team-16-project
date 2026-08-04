"""Staff authentication.

Contract: docs/interfaces.md 8.3 and 8.4. The only module here that knows
about HTTP requests and session cookies; everything below it takes plain
values.
"""

from sqlalchemy.orm import Session

from admin.accounts import (
    MfaNotEnrolledError,
    UnknownStaffError,
    get_staff,
    verify_staff_totp,
)
from admin.models import Staff, utcnow
from admin.security import verify_password
from admin.throttle import LoginThrottle

#: Key under which the authenticated username is held in the session cookie.
SESSION_KEY = "staff_username"


class StaffAuthRequired(Exception):
    """No usable staff session. The API layer maps this to UNAUTHORIZED (401)."""


def authenticate_password(
    session: Session,
    username: str,
    password: str,
    *,
    throttle: LoginThrottle,
    now: float,
) -> Staff | None:
    """Check a username and password, honouring the throttle.

    Returns the account on success, None on any failure. The caller must then
    complete the second factor before establishing a session — a password
    alone never grants access (contract 8.3).

    A failure against a username that does not exist still counts towards the
    throttle. Skipping it would turn the throttle into an oracle for which
    usernames are real.
    """
    if throttle.is_locked(username, now=now):
        return None

    try:
        staff = get_staff(session, username)
    except UnknownStaffError:
        throttle.record_failure(username, now=now)
        return None

    if not staff.is_active or not verify_password(password, staff.password_hash):
        throttle.record_failure(username, now=now)
        return None

    throttle.clear(username)
    staff.last_login_at = utcnow()
    return staff


def authenticate_totp(
    session: Session,
    username: str,
    code: str,
    *,
    throttle: LoginThrottle,
    secret_key: str,
    now: float,
) -> bool:
    """Check the second factor, sharing the throttle with the password step.

    Contract 8.3 requires one counter across both steps. Throttling only the
    password would leave a six-digit second factor — a 10^6 search space —
    open to anyone who already holds the password, which is precisely the
    position an attacker is in by the time they reach this call.

    A missing account or an unfinished enrolment is reported as an ordinary
    failure rather than allowed to propagate, so the response cannot be used
    to learn which accounts exist or which have finished enrolling.
    """
    if throttle.is_locked(username, now=now):
        return False

    try:
        accepted = verify_staff_totp(
            session, username, code, secret_key=secret_key, now=int(now)
        )
    except (UnknownStaffError, MfaNotEnrolledError):
        throttle.record_failure(username, now=now)
        return False

    if not accepted:
        throttle.record_failure(username, now=now)
        return False

    throttle.clear(username)
    return True


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
