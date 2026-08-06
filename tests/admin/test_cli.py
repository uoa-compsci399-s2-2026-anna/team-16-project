"""admin.cli - operational commands.

Contract: docs/interfaces.md 8.3, "Operational commands". These are the
break-glass paths that survive us leaving the project, so they are tested as
functions rather than only exercised by hand.
"""

import pyotp
import pytest

from admin.accounts import (
    begin_mfa_enrolment,
    complete_mfa_enrolment,
    create_staff,
    get_staff,
)
from admin.cli import cmd_create_staff, cmd_issue_password, cmd_reset_mfa, cmd_rotate_key
from admin.models import StaffRole
from admin.security import (
    TotpSecretUndecryptableError,
    decrypt_totp_secret,
    verify_password,
)
from admin.totp import TOTP_INTERVAL

pytestmark = pytest.mark.db

OLD_KEY = "old-secret-key-for-tests"
NEW_KEY = "new-secret-key-for-tests"
NOW = 1800


def enrol(session, username: str, secret_key: str) -> str:
    create_staff(session, username=username, display_name=username.title())
    session.flush()
    secret, _ = begin_mfa_enrolment(session, username, secret_key=secret_key)
    code = pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(NOW)
    complete_mfa_enrolment(
        session, username, code, secret_key=secret_key, now=NOW
    )
    session.flush()
    return secret


def test_create_staff_command_returns_a_usable_initial_password(session):
    username, password = cmd_create_staff(
        session, "alice", "Alice Example", StaffRole.admin, actor="bootstrap"
    )
    session.flush()

    staff = get_staff(session, username)
    assert verify_password(password, staff.password_hash) is True
    assert staff.role is StaffRole.admin


def test_create_staff_command_is_exempt_from_the_administrator_floor(session):
    """The floor guards removal, not creation. A system with no accounts has
    to be able to bootstrap its first administrator."""
    cmd_create_staff(session, "admin0", "Admin Zero", StaffRole.admin, actor="cli")
    session.flush()

    staff = get_staff(session, "admin0")
    assert staff.role is StaffRole.admin


def test_reset_mfa_command_clears_the_enrolment(session):
    enrol(session, "alice", OLD_KEY)

    cmd_reset_mfa(session, "alice")
    session.flush()

    assert get_staff(session, "alice").mfa_enrolled is False


def test_issue_password_command_returns_a_working_password(session):
    create_staff(session, username="alice", display_name="Alice")
    session.flush()

    password = cmd_issue_password(session, "alice")
    session.flush()

    staff = get_staff(session, "alice")
    assert verify_password(password, staff.password_hash)
    assert staff.must_change_password is True


def test_rotate_key_reencrypts_every_enrolled_secret(session):
    secret_a = enrol(session, "alice", OLD_KEY)
    secret_b = enrol(session, "bob", OLD_KEY)

    rotated = cmd_rotate_key(session, old_key=OLD_KEY, new_key=NEW_KEY)
    session.flush()

    assert rotated == 2
    assert (
        decrypt_totp_secret(get_staff(session, "alice").mfa_secret_enc, secret_key=NEW_KEY)
        == secret_a
    )
    assert (
        decrypt_totp_secret(get_staff(session, "bob").mfa_secret_enc, secret_key=NEW_KEY)
        == secret_b
    )


def test_rotate_key_skips_accounts_with_no_enrolment(session):
    enrol(session, "alice", OLD_KEY)
    create_staff(session, username="bob", display_name="Bob")
    session.flush()

    assert cmd_rotate_key(session, old_key=OLD_KEY, new_key=NEW_KEY) == 1


def test_rotate_key_with_the_wrong_old_key_changes_nothing(session):
    """A half-rotated table would be unrecoverable, so the command must fail
    before writing anything rather than part way through."""
    enrol(session, "alice", OLD_KEY)
    before = get_staff(session, "alice").mfa_secret_enc

    with pytest.raises(TotpSecretUndecryptableError):
        cmd_rotate_key(session, old_key="a-completely-wrong-key", new_key=NEW_KEY)

    assert get_staff(session, "alice").mfa_secret_enc == before


def test_rotating_twice_leaves_secrets_readable_with_the_newest_key(session):
    secret = enrol(session, "alice", OLD_KEY)
    cmd_rotate_key(session, old_key=OLD_KEY, new_key=NEW_KEY)
    session.flush()

    third = "third-secret-key-for-tests"
    cmd_rotate_key(session, old_key=NEW_KEY, new_key=third)
    session.flush()

    assert (
        decrypt_totp_secret(get_staff(session, "alice").mfa_secret_enc, secret_key=third)
        == secret
    )


def test_rotate_key_writes_nothing_when_one_account_of_several_fails(session):
    """The catastrophic failure mode: a partial rotation leaves some secrets
    readable only with the old key and some only with the new one, with no
    single key that opens the whole table. Unrecoverable short of resetting
    every account's MFA.

    One account failing among several is the case that distinguishes
    decrypt-all-then-write-all from a per-row decrypt-and-write loop. A test
    where every row fails cannot tell them apart.
    """
    enrol(session, "alice", OLD_KEY)
    enrol(session, "bob", "a-third-key-nobody-else-uses")

    before_alice = get_staff(session, "alice").mfa_secret_enc
    before_bob = get_staff(session, "bob").mfa_secret_enc

    with pytest.raises(TotpSecretUndecryptableError):
        cmd_rotate_key(session, old_key=OLD_KEY, new_key=NEW_KEY)

    assert get_staff(session, "alice").mfa_secret_enc == before_alice
    assert get_staff(session, "bob").mfa_secret_enc == before_bob
