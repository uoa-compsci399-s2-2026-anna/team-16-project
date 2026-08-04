"""admin.auth - the two-factor login handshake and the require_staff gate.

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
    PENDING_LOGIN_TTL_SECONDS,
    SESSION_KEY,
    PendingLogin,
    StaffAuthRequired,
    authenticate_password,
    authenticate_recovery_code,
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


def enrolled(session, username: str = "alice") -> tuple[str, str, list[str]]:
    """Create a fully onboarded account: password changed and MFA enrolled.

    Returns (password, TOTP secret, recovery codes). Contract 8.3's flow is
    create -> forced password change -> forced TOTP enrolment -> access
    granted; this fixture walks all of it, not just the MFA half, since a
    "good session" test has to represent an account require_staff_username
    actually admits.
    """
    _, password = create_staff(session, username=username, display_name="Alice")
    session.flush()
    set_password(session, username, password)
    secret, _ = begin_mfa_enrolment(session, username, secret_key=SECRET_KEY)
    code = pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(int(NOW))
    codes = complete_mfa_enrolment(
        session, username, code, secret_key=SECRET_KEY, now=int(NOW)
    )
    session.flush()
    return password, secret, codes


def pass_password(session, username, password, throttle, now=NOW) -> PendingLogin:
    """Run the password step and insist it produced a pending login."""
    pending = authenticate_password(
        session, username, password, throttle=throttle, now=now
    )
    assert pending is not None
    return pending


def totp_code(secret: str, at: float) -> str:
    return pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(int(at))


# --- the password step ------------------------------------------------------


def test_the_correct_password_authenticates(session):
    password, _, _ = enrolled(session)
    throttle = make_throttle()

    pending = authenticate_password(
        session, "alice", password, throttle=throttle, now=NOW
    )

    assert pending is not None
    assert pending.username == "alice"


def test_the_password_step_yields_a_pending_login_and_not_a_session(session):
    """Contract 8.3: a password alone never grants access.

    The password step hands back a half-finished login, not the username the
    session cookie is keyed on. A caller that treats this value as proof of
    identity is holding something whose type says otherwise.
    """
    password, _, _ = enrolled(session)
    throttle = make_throttle()

    pending = authenticate_password(
        session, "alice", password, throttle=throttle, now=NOW
    )

    assert isinstance(pending, PendingLogin)
    assert pending != "alice"


def test_the_pending_login_expires_on_the_injected_clock(session):
    """The expiry is derived from the caller's clock value, never read from
    the ambient one, so tests and callers agree on what 'now' means."""
    password, _, _ = enrolled(session)
    throttle = make_throttle()

    first = pass_password(session, "alice", password, throttle, now=NOW)
    second = pass_password(session, "alice", password, throttle, now=NOW + 1000)

    assert first.expires_at == NOW + PENDING_LOGIN_TTL_SECONDS
    assert second.expires_at == NOW + 1000 + PENDING_LOGIN_TTL_SECONDS


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


def test_the_initial_password_authenticates_before_the_forced_change(session):
    """The first login happens with must_change_password still set — the user
    has to get in before they can change it. authenticate_password must not
    gate on that flag; the pending login it returns is exactly what authorises
    the change-password and enrolment pages, and require_staff_username is
    what refuses the account everywhere else.

    This case is not covered by the enrolled() helper, which deliberately
    completes onboarding.
    """
    _, password = create_staff(session, username="dave", display_name="Dave")
    session.flush()
    throttle = make_throttle()

    pending = authenticate_password(
        session, "dave", password, throttle=throttle, now=NOW
    )

    assert pending is not None
    assert get_staff(session, "dave").must_change_password is True


def test_a_deactivated_account_does_not_authenticate(session):
    """Deactivation has to bite at login, not only in the admin list view."""
    password, _, _ = enrolled(session)
    enrolled(session, "admin0")
    enrolled(session, "admin1")
    deactivate_staff(session, "alice")
    session.flush()
    throttle = make_throttle()

    assert (
        authenticate_password(session, "alice", password, throttle=throttle, now=NOW)
        is None
    )


# --- the shared counter -----------------------------------------------------


def test_repeated_failures_lock_the_account_out(session):
    password, _, _ = enrolled(session)
    throttle = make_throttle()

    for _ in range(3):
        authenticate_password(session, "alice", "wrong", throttle=throttle, now=NOW)

    assert (
        authenticate_password(session, "alice", password, throttle=throttle, now=NOW)
        is None
    ), "a locked account must be refused even with the right password"


def test_a_correct_password_alone_does_not_clear_the_counter(session):
    """A password is half a login, so it clears nothing.

    Two earlier failures plus one later failure must still reach the
    threshold, even with a correct password in between. If the password step
    cleared the counter the run below would sit at one failure and the account
    would stay open.
    """
    password, _, _ = enrolled(session)
    throttle = make_throttle()
    authenticate_password(session, "alice", "wrong", throttle=throttle, now=NOW)
    authenticate_password(session, "alice", "wrong", throttle=throttle, now=NOW)

    authenticate_password(session, "alice", password, throttle=throttle, now=NOW)
    authenticate_password(session, "alice", "wrong", throttle=throttle, now=NOW)

    assert throttle.is_locked("alice", now=NOW) is True


def test_completing_both_factors_clears_the_counter(session):
    """The counter is cleared by a completed login, and only by one.

    A user who fumbles twice and then logs in properly starts fresh; the
    allowance is restored by the second factor, not by the first.
    """
    password, secret, _ = enrolled(session)
    throttle = make_throttle()
    authenticate_password(session, "alice", "wrong", throttle=throttle, now=NOW)
    authenticate_password(session, "alice", "wrong", throttle=throttle, now=NOW)

    later = NOW + 300
    pending = pass_password(session, "alice", password, throttle, now=later)
    authenticate_totp(
        session,
        pending,
        totp_code(secret, later),
        throttle=throttle,
        secret_key=SECRET_KEY,
        now=later,
    )
    authenticate_password(session, "alice", "wrong", throttle=throttle, now=later)
    authenticate_password(session, "alice", "wrong", throttle=throttle, now=later)

    assert throttle.is_locked("alice", now=later) is False


def test_a_redeemed_recovery_code_clears_the_counter(session):
    """Recovery layer L1 completes a login just as a TOTP does, so it restores
    the allowance in the same way."""
    password, _, codes = enrolled(session)
    throttle = make_throttle()
    authenticate_password(session, "alice", "wrong", throttle=throttle, now=NOW)
    authenticate_password(session, "alice", "wrong", throttle=throttle, now=NOW)

    pending = pass_password(session, "alice", password, throttle)
    authenticate_recovery_code(
        session, pending, codes[0], throttle=throttle, now=NOW
    )
    authenticate_password(session, "alice", "wrong", throttle=throttle, now=NOW)
    authenticate_password(session, "alice", "wrong", throttle=throttle, now=NOW)

    assert throttle.is_locked("alice", now=NOW) is False


def test_the_password_step_cannot_reset_the_shared_counter(session):
    """The attack the shared counter exists to stop.

    Submitting the known-good password resets nothing, so an attacker who
    holds the password cannot alternate password-success with wrong TOTP codes
    to keep the counter permanently below the threshold. With the +/-1 drift
    window three codes are live at once; an unbounded loop here reduces the
    second factor to a search an attacker can actually finish.
    """
    password, _, _ = enrolled(session)
    throttle = make_throttle()

    for _ in range(10):
        pending = authenticate_password(
            session, "alice", password, throttle=throttle, now=NOW
        )
        if pending is None:
            break
        authenticate_totp(
            session,
            pending,
            "000000",
            throttle=throttle,
            secret_key=SECRET_KEY,
            now=NOW,
        )

    assert throttle.is_locked("alice", now=NOW) is True


def test_a_failed_login_against_an_unknown_username_still_counts(session):
    """Otherwise the throttle can be probed with a username that does not
    exist to learn which usernames do."""
    throttle = make_throttle()

    for _ in range(3):
        authenticate_password(session, "ghost", "wrong", throttle=throttle, now=NOW)

    assert throttle.is_locked("ghost", now=NOW) is True


def test_a_completed_login_records_the_login_time(session):
    password, secret, _ = enrolled(session)
    throttle = make_throttle()

    pending = pass_password(session, "alice", password, throttle)
    authenticate_totp(
        session,
        pending,
        totp_code(secret, NOW + 60),
        throttle=throttle,
        secret_key=SECRET_KEY,
        now=NOW + 60,
    )
    session.flush()

    assert get_staff(session, "alice").last_login_at is not None


def test_the_password_step_alone_does_not_record_a_login_time(session):
    """last_login_at is shown to administrators as evidence of who has been in
    the panel. A half-finished handshake is not a login and must not appear as
    one."""
    password, _, _ = enrolled(session)
    throttle = make_throttle()

    authenticate_password(session, "alice", password, throttle=throttle, now=NOW)
    session.flush()

    assert get_staff(session, "alice").last_login_at is None


# --- the second factor ------------------------------------------------------


def test_a_correct_totp_completes_the_login(session):
    password, secret, _ = enrolled(session)
    throttle = make_throttle()
    pending = pass_password(session, "alice", password, throttle)

    assert (
        authenticate_totp(
            session,
            pending,
            totp_code(secret, NOW + 60),
            throttle=throttle,
            secret_key=SECRET_KEY,
            now=NOW + 60,
        )
        == "alice"
    )


def test_a_wrong_totp_does_not_authenticate(session):
    password, _, _ = enrolled(session)
    throttle = make_throttle()
    pending = pass_password(session, "alice", password, throttle)

    assert (
        authenticate_totp(
            session,
            pending,
            "000000",
            throttle=throttle,
            secret_key=SECRET_KEY,
            now=NOW,
        )
        is None
    )


def test_the_second_factor_cannot_be_called_without_a_pending_login(session):
    """Contract 8.3's ordering, enforced by the signature rather than by a
    sentence in a docstring. A page author who reaches for the second factor
    on its own gets an error, not a session.
    """
    enrolled(session)
    throttle = make_throttle()

    with pytest.raises(TypeError):
        authenticate_totp(
            session,
            "alice",
            "000000",
            throttle=throttle,
            secret_key=SECRET_KEY,
            now=NOW,
        )

    with pytest.raises(TypeError):
        authenticate_recovery_code(
            session, "alice", "AAAA-BBBB-CCCC", throttle=throttle, now=NOW
        )


def test_an_expired_pending_login_is_refused_even_with_a_correct_code(session):
    """The password step buys a short window, not an open-ended one: a login
    left half-finished on a shared machine cannot be completed later."""
    password, secret, _ = enrolled(session)
    throttle = make_throttle()
    pending = pass_password(session, "alice", password, throttle)

    stale = NOW + PENDING_LOGIN_TTL_SECONDS + 1

    assert (
        authenticate_totp(
            session,
            pending,
            totp_code(secret, stale),
            throttle=throttle,
            secret_key=SECRET_KEY,
            now=stale,
        )
        is None
    )


def test_an_expired_pending_login_does_not_count_towards_the_lockout(session):
    """Letting the window lapse is a flow error, not a credential guess.
    Charging it to the counter would let a user lock their own account out by
    walking away from the second-factor page.
    """
    password, secret, _ = enrolled(session)
    throttle = make_throttle()
    stale = NOW + PENDING_LOGIN_TTL_SECONDS + 1

    for _ in range(3):
        pending = pass_password(session, "alice", password, throttle)
        authenticate_totp(
            session,
            pending,
            totp_code(secret, stale),
            throttle=throttle,
            secret_key=SECRET_KEY,
            now=stale,
        )

    assert throttle.is_locked("alice", now=stale) is False


def test_totp_failures_share_the_lockout_counter_with_password_failures(session):
    """Contract 8.3: one counter for both steps. Throttling only the password
    would leave a six-digit second factor — a 10^6 search space — open to
    anyone who already holds the password."""
    password, _, _ = enrolled(session)
    throttle = make_throttle()

    authenticate_password(session, "alice", "wrong", throttle=throttle, now=NOW)
    authenticate_password(session, "alice", "wrong", throttle=throttle, now=NOW)
    pending = PendingLogin(username="alice", expires_at=NOW + 60)
    authenticate_totp(
        session, pending, "000000", throttle=throttle, secret_key=SECRET_KEY, now=NOW
    )

    assert throttle.is_locked("alice", now=NOW) is True


def test_a_locked_account_is_refused_even_with_a_correct_totp(session):
    password, secret, _ = enrolled(session)
    throttle = make_throttle()
    pending = pass_password(session, "alice", password, throttle)
    for _ in range(3):
        authenticate_password(session, "alice", "wrong", throttle=throttle, now=NOW)

    assert (
        authenticate_totp(
            session,
            pending,
            totp_code(secret, NOW + 60),
            throttle=throttle,
            secret_key=SECRET_KEY,
            now=NOW + 60,
        )
        is None
    )


def test_a_totp_for_an_unenrolled_account_counts_as_a_failure(session):
    """It must not escape as MfaNotEnrolledError: the login flow has to treat
    it as one more failed attempt, or it becomes an oracle for which accounts
    have finished enrolling."""
    _, password = create_staff(session, username="bob", display_name="Bob")
    session.flush()
    throttle = make_throttle()
    pending = pass_password(session, "bob", password, throttle)

    result = authenticate_totp(
        session, pending, "123456", throttle=throttle, secret_key=SECRET_KEY, now=NOW
    )

    assert result is None


def test_a_totp_for_an_unfinished_enrolment_is_refused(session):
    """A begun-but-never-completed enrolment has a usable secret sitting in the
    row. It must not satisfy the login second factor: mfa_enrolled_at is what
    says a real authenticator holds that secret.
    """
    _, password = create_staff(session, username="bob", display_name="Bob")
    session.flush()
    secret, _ = begin_mfa_enrolment(session, "bob", secret_key=SECRET_KEY)
    session.flush()
    throttle = make_throttle()
    pending = pass_password(session, "bob", password, throttle)

    result = authenticate_totp(
        session,
        pending,
        totp_code(secret, NOW),
        throttle=throttle,
        secret_key=SECRET_KEY,
        now=NOW,
    )

    assert result is None


def test_the_second_factor_refuses_a_deactivated_account(session):
    """The account can be deactivated between the two steps, and the second
    factor is a fresh request. Re-reading the row here means a deactivation
    takes effect immediately rather than at the end of a login already in
    flight.
    """
    password, secret, _ = enrolled(session)
    enrolled(session, "admin0")
    enrolled(session, "admin1")
    throttle = make_throttle()
    pending = pass_password(session, "alice", password, throttle)
    deactivate_staff(session, "alice")
    session.flush()

    assert (
        authenticate_totp(
            session,
            pending,
            totp_code(secret, NOW + 60),
            throttle=throttle,
            secret_key=SECRET_KEY,
            now=NOW + 60,
        )
        is None
    )


# --- recovery codes as the second factor (contract 8.3, layer L1) -----------


def test_a_recovery_code_completes_the_login(session):
    password, _, codes = enrolled(session)
    throttle = make_throttle()
    pending = pass_password(session, "alice", password, throttle)

    assert (
        authenticate_recovery_code(
            session, pending, codes[0], throttle=throttle, now=NOW
        )
        == "alice"
    )


def test_a_recovery_code_cannot_be_redeemed_twice(session):
    password, _, codes = enrolled(session)
    throttle = make_throttle()

    pending = pass_password(session, "alice", password, throttle)
    first = authenticate_recovery_code(
        session, pending, codes[0], throttle=throttle, now=NOW
    )
    session.flush()
    second = authenticate_recovery_code(
        session, pending, codes[0], throttle=throttle, now=NOW
    )

    assert first == "alice"
    assert second is None


def test_a_wrong_recovery_code_counts_towards_the_lockout(session):
    """Recovery codes are a second factor too, so guessing at them is
    throttled on the same counter as everything else."""
    password, _, _ = enrolled(session)
    throttle = make_throttle()

    for _ in range(3):
        pending = pass_password(session, "alice", password, throttle)
        authenticate_recovery_code(
            session, pending, "AAAA-BBBB-CCCC", throttle=throttle, now=NOW
        )

    assert throttle.is_locked("alice", now=NOW) is True


def test_a_recovery_code_is_refused_for_a_deactivated_account(session):
    password, _, codes = enrolled(session)
    enrolled(session, "admin0")
    enrolled(session, "admin1")
    throttle = make_throttle()
    pending = pass_password(session, "alice", password, throttle)
    deactivate_staff(session, "alice")
    session.flush()

    assert (
        authenticate_recovery_code(
            session, pending, codes[0], throttle=throttle, now=NOW
        )
        is None
    )


# --- the require_staff gate -------------------------------------------------


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
