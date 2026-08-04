"""admin.auth - password authentication and the require_staff gate.

Contract: docs/interfaces.md 8.3 and 8.4.
"""

import pyotp
import pytest

from admin.accounts import (
    begin_mfa_enrolment,
    complete_mfa_enrolment,
    create_staff,
    deactivate_staff,
    get_staff,
    set_password,
)
from admin.auth import (
    SESSION_KEY,
    StaffAuthRequired,
    authenticate_password,
    authenticate_totp,
    require_staff_username,
)
from admin.throttle import LoginThrottle
from admin.totp import TOTP_INTERVAL

pytestmark = pytest.mark.db

SECRET_KEY = "test-secret-key-not-used-anywhere-real"
NOW = 1800.0


def make_throttle() -> LoginThrottle:
    return LoginThrottle(max_failures=3, lockout_seconds=900)


def enrolled(session, username: str = "alice") -> tuple[str, str]:
    """Create a fully onboarded account: password changed and MFA enrolled.

    Returns (password, TOTP secret). Contract 8.3's flow is create -> forced
    password change -> forced TOTP enrolment -> access granted; this fixture
    walks all of it, not just the MFA half, since a "good session" test has
    to represent an account require_staff_username actually admits.
    """
    _, password = create_staff(session, username=username, display_name="Alice")
    session.flush()
    set_password(session, username, password)
    secret, _ = begin_mfa_enrolment(session, username, secret_key=SECRET_KEY)
    code = pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(int(NOW))
    complete_mfa_enrolment(
        session, username, code, secret_key=SECRET_KEY, now=int(NOW)
    )
    session.flush()
    return password, secret


def test_the_correct_password_authenticates(session):
    password, _ = enrolled(session)
    throttle = make_throttle()

    staff = authenticate_password(
        session, "alice", password, throttle=throttle, now=NOW
    )

    assert staff is not None
    assert staff.username == "alice"


def test_a_wrong_password_does_not_authenticate(session):
    enrolled(session)
    throttle = make_throttle()

    assert (
        authenticate_password(session, "alice", "wrong", throttle=throttle, now=NOW)
        is None
    )


def test_an_unknown_username_does_not_authenticate(session):
    throttle = make_throttle()

    assert (
        authenticate_password(session, "nobody", "x", throttle=throttle, now=NOW)
        is None
    )


def test_a_deactivated_account_does_not_authenticate(session):
    """Deactivation has to bite at login, not only in the admin list view."""
    password, _ = enrolled(session)
    enrolled(session, "admin0")
    enrolled(session, "admin1")
    deactivate_staff(session, "alice")
    session.flush()
    throttle = make_throttle()

    assert (
        authenticate_password(session, "alice", password, throttle=throttle, now=NOW)
        is None
    )


def test_repeated_failures_lock_the_account_out(session):
    password, _ = enrolled(session)
    throttle = make_throttle()

    for _ in range(3):
        authenticate_password(session, "alice", "wrong", throttle=throttle, now=NOW)

    assert (
        authenticate_password(session, "alice", password, throttle=throttle, now=NOW)
        is None
    ), "a locked account must be refused even with the right password"


def test_a_successful_login_clears_the_failure_counter(session):
    password, _ = enrolled(session)
    throttle = make_throttle()
    authenticate_password(session, "alice", "wrong", throttle=throttle, now=NOW)
    authenticate_password(session, "alice", "wrong", throttle=throttle, now=NOW)

    authenticate_password(session, "alice", password, throttle=throttle, now=NOW)

    assert throttle.is_locked("alice", now=NOW) is False


def test_a_failed_login_against_an_unknown_username_still_counts(session):
    """Otherwise the throttle can be probed with a username that does not
    exist to learn which usernames do."""
    throttle = make_throttle()

    for _ in range(3):
        authenticate_password(session, "ghost", "wrong", throttle=throttle, now=NOW)

    assert throttle.is_locked("ghost", now=NOW) is True


def test_successful_authentication_records_the_login_time(session):
    password, _ = enrolled(session)
    throttle = make_throttle()

    authenticate_password(session, "alice", password, throttle=throttle, now=NOW)
    session.flush()

    assert get_staff(session, "alice").last_login_at is not None


def test_a_correct_totp_authenticates(session):
    _, secret = enrolled(session)
    throttle = make_throttle()

    assert (
        authenticate_totp(
            session,
            "alice",
            pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(int(NOW) + 300),
            throttle=throttle,
            secret_key=SECRET_KEY,
            now=NOW + 300,
        )
        is True
    )


def test_a_wrong_totp_does_not_authenticate(session):
    enrolled(session)
    throttle = make_throttle()

    assert (
        authenticate_totp(
            session,
            "alice",
            "000000",
            throttle=throttle,
            secret_key=SECRET_KEY,
            now=NOW,
        )
        is False
    )


def test_totp_failures_share_the_lockout_counter_with_password_failures(session):
    """Contract 8.3: one counter for both steps. Throttling only the password
    would leave a six-digit second factor — a 10^6 search space — open to
    anyone who already holds the password."""
    enrolled(session)
    throttle = make_throttle()

    authenticate_password(session, "alice", "wrong", throttle=throttle, now=NOW)
    authenticate_password(session, "alice", "wrong", throttle=throttle, now=NOW)
    authenticate_totp(
        session, "alice", "000000", throttle=throttle, secret_key=SECRET_KEY, now=NOW
    )

    assert throttle.is_locked("alice", now=NOW) is True


def test_a_locked_account_is_refused_even_with_a_correct_totp(session):
    _, secret = enrolled(session)
    throttle = make_throttle()
    for _ in range(3):
        authenticate_password(session, "alice", "wrong", throttle=throttle, now=NOW)

    assert (
        authenticate_totp(
            session,
            "alice",
            pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(int(NOW) + 300),
            throttle=throttle,
            secret_key=SECRET_KEY,
            now=NOW + 300,
        )
        is False
    )


def test_a_totp_for_an_unenrolled_account_counts_as_a_failure(session):
    """It must not escape as MfaNotEnrolledError: the login flow has to treat
    it as one more failed attempt, or it becomes an oracle for which accounts
    have finished enrolling."""
    create_staff(session, username="bob", display_name="Bob")
    session.flush()
    throttle = make_throttle()

    result = authenticate_totp(
        session, "bob", "123456", throttle=throttle, secret_key=SECRET_KEY, now=NOW
    )

    assert result is False


def test_require_staff_returns_the_username_for_a_good_session(session):
    enrolled(session)

    assert require_staff_username(session, {SESSION_KEY: "alice"}) == "alice"


def test_require_staff_rejects_an_empty_session(session):
    with pytest.raises(StaffAuthRequired):
        require_staff_username(session, {})


def test_require_staff_rejects_an_unknown_username(session):
    with pytest.raises(StaffAuthRequired):
        require_staff_username(session, {SESSION_KEY: "ghost"})


def test_require_staff_rejects_a_deactivated_account(session):
    """Contract 8.3: the account state is re-checked on every request, so
    deactivating someone takes effect immediately rather than when their
    cookie expires."""
    enrolled(session)
    enrolled(session, "admin0")
    enrolled(session, "admin1")
    deactivate_staff(session, "alice")
    session.flush()

    with pytest.raises(StaffAuthRequired):
        require_staff_username(session, {SESSION_KEY: "alice"})


def test_require_staff_rejects_an_account_that_has_not_enrolled_mfa(session):
    """Contract 8.3: while mfa_enrolled_at is NULL every route except the
    password-change and enrolment pages is refused. Without this the forced
    enrolment is advisory and can be walked past by typing a URL."""
    create_staff(session, username="bob", display_name="Bob")
    session.flush()

    with pytest.raises(StaffAuthRequired):
        require_staff_username(session, {SESSION_KEY: "bob"})


def test_require_staff_rejects_an_account_that_must_change_its_password(session):
    """Same reasoning: the forced password change has to be unavoidable."""
    _, _ = create_staff(session, username="carol", display_name="Carol")
    session.flush()
    secret, _ = begin_mfa_enrolment(session, "carol", secret_key=SECRET_KEY)
    code = pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(int(NOW))
    complete_mfa_enrolment(
        session, "carol", code, secret_key=SECRET_KEY, now=int(NOW)
    )
    session.flush()
    # carol has enrolled MFA but has not changed her initial password.

    with pytest.raises(StaffAuthRequired):
        require_staff_username(session, {SESSION_KEY: "carol"})
