"""Contract §8.3: an administrator issues a random password to evict an account."""

import pyotp
import pytest
from sqlalchemy import select

from admin.accounts import (
    UnknownStaffError,
    begin_mfa_enrolment,
    complete_mfa_enrolment,
    create_staff,
    get_staff,
    issue_password,
    set_password,
)
from admin.auth import StaffAuthRequired, require_staff_username, stamp_session
from admin.models import AuditLog, Staff
from admin.security import verify_password
from admin.totp import TOTP_INTERVAL

pytestmark = pytest.mark.db

SECRET_KEY = "test-secret-key-not-used-anywhere-real"
NOW = 1800


@pytest.fixture
def enrolled_staff(session) -> Staff:
    """A fully onboarded account: password changed and MFA enrolled.

    Built through the service functions rather than by hand-constructing a
    Staff row, so the account is in a state require_staff_username actually
    admits - the same reasoning as test_session_generation.py's own fixture
    of the same name.
    """
    username = "erin"
    _, password = create_staff(session, username=username, display_name="Erin", actor="test", secret_key=SECRET_KEY)
    session.flush()
    set_password(session, username, "a-strong-initial-password")
    secret, _ = begin_mfa_enrolment(session, username, secret_key=SECRET_KEY)
    code = pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(NOW)
    complete_mfa_enrolment(session, username, code, secret_key=SECRET_KEY, now=NOW)
    session.flush()
    return get_staff(session, username)


def test_the_returned_password_is_the_one_that_works(session, enrolled_staff):
    issued = issue_password(session, enrolled_staff.username, actor="admin")
    session.flush()

    staff = get_staff(session, enrolled_staff.username)
    assert verify_password(issued, staff.password_hash)


def test_the_account_is_forced_to_change_it(session, enrolled_staff):
    """The distinction from set_password, and the whole point of this function.

    set_password clears must_change_password. An issued password that did the
    same would leave the account looking already-onboarded, and the forced
    change page would never appear.
    """
    issue_password(session, enrolled_staff.username, actor="admin")
    session.flush()

    assert get_staff(session, enrolled_staff.username).must_change_password is True


def test_issuing_a_password_ends_the_account_s_live_sessions(session, enrolled_staff):
    """Eviction that leaves the attacker's session open is not eviction."""
    session_data = {}
    stamp_session(session_data, enrolled_staff)

    issue_password(session, enrolled_staff.username, actor="admin")
    session.flush()

    with pytest.raises(StaffAuthRequired):
        require_staff_username(session, session_data)


def test_two_calls_produce_different_passwords(session, enrolled_staff):
    first = issue_password(session, enrolled_staff.username, actor="admin")
    second = issue_password(session, enrolled_staff.username, actor="admin")

    assert first != second


def test_the_issued_password_is_never_written_to_the_audit_trail(session, enrolled_staff):
    """The plaintext is handed over out of band, not recorded.

    An audit entry carrying it would put a working credential in a table
    every staff member can read.
    """
    issued = issue_password(session, enrolled_staff.username, actor="admin")
    session.flush()

    entries = session.scalars(select(AuditLog)).all()
    assert entries, "issuing a password must be audited"
    for entry in entries:
        assert issued not in repr(entry.before_json)
        assert issued not in repr(entry.after_json)


def test_an_unknown_account_raises(session):
    with pytest.raises(UnknownStaffError):
        issue_password(session, "nobody", actor="admin")
