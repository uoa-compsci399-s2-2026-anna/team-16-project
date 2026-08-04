"""admin.accounts - MFA enrolment, TOTP verification, recovery codes.

Contract: docs/interfaces.md 8.3.
"""

import pyotp
import pytest

from admin.accounts import (
    RECOVERY_CODE_COUNT,
    MfaNotEnrolledError,
    begin_mfa_enrolment,
    complete_mfa_enrolment,
    consume_recovery_code,
    create_staff,
    get_staff,
    reset_mfa,
    unused_recovery_code_count,
    verify_staff_totp,
)
from admin.totp import TOTP_INTERVAL

pytestmark = pytest.mark.db

SECRET_KEY = "test-secret-key-not-used-anywhere-real"
NOW = 1800


def code_for(secret: str, counter: int) -> str:
    return pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(counter * TOTP_INTERVAL)


def enrolled_account(session, username: str = "alice") -> str:
    """Create an account, enrol it, and return its TOTP secret."""
    create_staff(session, username=username, display_name="Alice Example")
    session.flush()
    secret, _ = begin_mfa_enrolment(session, username, secret_key=SECRET_KEY)
    complete_mfa_enrolment(
        session,
        username,
        code_for(secret, NOW // TOTP_INTERVAL),
        secret_key=SECRET_KEY,
        now=NOW,
    )
    session.flush()
    return secret


def test_begin_enrolment_returns_a_secret_and_a_scannable_uri(session):
    create_staff(session, username="alice", display_name="Alice Example")
    session.flush()

    secret, uri = begin_mfa_enrolment(session, "alice", secret_key=SECRET_KEY)

    assert len(secret) == 32
    assert uri.startswith("otpauth://totp/")


def test_begin_enrolment_stores_the_secret_but_does_not_mark_it_enrolled(session):
    """The secret is persisted so it never has to travel back through the
    browser between the two requests. Enrolment still only counts once a
    correct code has been produced, which is what proves the authenticator
    actually holds it."""
    create_staff(session, username="alice", display_name="Alice Example")
    session.flush()

    begin_mfa_enrolment(session, "alice", secret_key=SECRET_KEY)
    session.flush()

    staff = get_staff(session, "alice")
    assert staff.mfa_secret_enc is not None
    assert staff.mfa_enrolled is False


def test_completing_an_enrolment_that_was_never_begun_is_refused(session):
    create_staff(session, username="alice", display_name="Alice Example")
    session.flush()

    with pytest.raises(MfaNotEnrolledError):
        complete_mfa_enrolment(
            session, "alice", "123456", secret_key=SECRET_KEY, now=NOW
        )


def test_beginning_enrolment_again_replaces_an_unfinished_one(session):
    """Someone abandons the enrolment page and starts over. The second secret
    is the one that must work."""
    create_staff(session, username="alice", display_name="Alice Example")
    session.flush()
    begin_mfa_enrolment(session, "alice", secret_key=SECRET_KEY)
    second_secret, _ = begin_mfa_enrolment(session, "alice", secret_key=SECRET_KEY)
    session.flush()

    complete_mfa_enrolment(
        session,
        "alice",
        code_for(second_secret, NOW // TOTP_INTERVAL),
        secret_key=SECRET_KEY,
        now=NOW,
    )
    session.flush()

    assert get_staff(session, "alice").mfa_enrolled is True


def test_completing_enrolment_with_a_correct_code_enrols_the_account(session):
    enrolled_account(session)

    assert get_staff(session, "alice").mfa_enrolled is True


def test_completing_enrolment_with_a_wrong_code_is_refused(session):
    create_staff(session, username="alice", display_name="Alice Example")
    session.flush()
    secret, _ = begin_mfa_enrolment(session, "alice", secret_key=SECRET_KEY)

    with pytest.raises(MfaNotEnrolledError):
        complete_mfa_enrolment(
            session, "alice", "000000", secret_key=SECRET_KEY, now=NOW
        )


def test_completing_enrolment_issues_the_agreed_number_of_recovery_codes(session):
    create_staff(session, username="alice", display_name="Alice Example")
    session.flush()
    secret, _ = begin_mfa_enrolment(session, "alice", secret_key=SECRET_KEY)

    codes = complete_mfa_enrolment(
        session,
        "alice",
        code_for(secret, NOW // TOTP_INTERVAL),
        secret_key=SECRET_KEY,
        now=NOW,
    )

    assert len(codes) == RECOVERY_CODE_COUNT
    assert RECOVERY_CODE_COUNT == 5


def test_the_stored_secret_is_not_the_plaintext_secret(session):
    secret = enrolled_account(session)

    assert get_staff(session, "alice").mfa_secret_enc != secret.encode()


def test_a_valid_code_is_accepted_after_enrolment(session):
    secret = enrolled_account(session)

    assert (
        verify_staff_totp(
            session,
            "alice",
            code_for(secret, NOW // TOTP_INTERVAL + 10),
            secret_key=SECRET_KEY,
            now=NOW + 300,
        )
        is True
    )


def test_the_same_code_cannot_be_used_twice(session):
    """Replay protection through staff.mfa_last_counter."""
    secret = enrolled_account(session)
    counter = NOW // TOTP_INTERVAL + 10
    later = NOW + 300

    first = verify_staff_totp(
        session, "alice", code_for(secret, counter), secret_key=SECRET_KEY, now=later
    )
    session.flush()
    second = verify_staff_totp(
        session, "alice", code_for(secret, counter), secret_key=SECRET_KEY, now=later
    )

    assert first is True
    assert second is False


def test_verifying_a_code_for_an_unenrolled_account_is_refused(session):
    create_staff(session, username="bob", display_name="Bob")
    session.flush()

    with pytest.raises(MfaNotEnrolledError):
        verify_staff_totp(session, "bob", "123456", secret_key=SECRET_KEY, now=NOW)


def test_a_recovery_code_works_once(session):
    create_staff(session, username="alice", display_name="Alice Example")
    session.flush()
    secret, _ = begin_mfa_enrolment(session, "alice", secret_key=SECRET_KEY)
    codes = complete_mfa_enrolment(
        session,
        "alice",
        code_for(secret, NOW // TOTP_INTERVAL),
        secret_key=SECRET_KEY,
        now=NOW,
    )
    session.flush()

    assert consume_recovery_code(session, "alice", codes[0]) is True
    session.flush()
    assert consume_recovery_code(session, "alice", codes[0]) is False


def test_an_unknown_recovery_code_is_rejected(session):
    enrolled_account(session)

    assert consume_recovery_code(session, "alice", "AAAA-BBBB-CCCC") is False


def test_consuming_a_recovery_code_leaves_the_others_usable(session):
    create_staff(session, username="alice", display_name="Alice Example")
    session.flush()
    secret, _ = begin_mfa_enrolment(session, "alice", secret_key=SECRET_KEY)
    codes = complete_mfa_enrolment(
        session,
        "alice",
        code_for(secret, NOW // TOTP_INTERVAL),
        secret_key=SECRET_KEY,
        now=NOW,
    )
    session.flush()

    consume_recovery_code(session, "alice", codes[0])
    session.flush()

    assert unused_recovery_code_count(session, "alice") == RECOVERY_CODE_COUNT - 1


def test_resetting_mfa_clears_enrolment_and_all_recovery_codes(session):
    """The administrator reset path, contract 8.3 layer L2."""
    enrolled_account(session)

    reset_mfa(session, "alice")
    session.flush()

    staff = get_staff(session, "alice")
    assert staff.mfa_enrolled is False
    assert staff.mfa_secret_enc is None
    assert staff.mfa_last_counter is None
    assert unused_recovery_code_count(session, "alice") == 0


def test_a_reset_account_can_enrol_again(session):
    enrolled_account(session)
    reset_mfa(session, "alice")
    session.flush()

    secret, _ = begin_mfa_enrolment(session, "alice", secret_key=SECRET_KEY)
    complete_mfa_enrolment(
        session,
        "alice",
        code_for(secret, NOW // TOTP_INTERVAL),
        secret_key=SECRET_KEY,
        now=NOW,
    )
    session.flush()

    assert get_staff(session, "alice").mfa_enrolled is True


def test_resetting_mfa_leaves_no_stale_recovery_codes_on_the_relationship(session):
    """The ORM's view must agree with the database, not just the database.
    sqladmin renders relationships directly, so a stale collection would show
    an administrator recovery codes that no longer exist for an account they
    had just reset."""
    enrolled_account(session)
    staff = get_staff(session, "alice")
    assert len(staff.recovery_codes) == RECOVERY_CODE_COUNT  # force the load

    reset_mfa(session, "alice")
    session.flush()

    assert staff.recovery_codes == []
    assert unused_recovery_code_count(session, "alice") == 0
