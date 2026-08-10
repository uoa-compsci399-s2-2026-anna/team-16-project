"""admin.cli - operational commands.

Contract: docs/interfaces.md 8.3, "Operational commands". These are the
break-glass paths that survive us leaving the project, so they are tested as
functions rather than only exercised by hand.
"""

import pyotp
import pytest
from sqlalchemy import select

from admin.accounts import (
    begin_mfa_enrolment,
    complete_mfa_enrolment,
    create_staff,
    get_staff,
)
from admin.cli import (
    cmd_create_staff, cmd_issue_password, cmd_reset_mfa, cmd_rotate_key, cmd_unblock,
)
from admin.models import AuditLog, StaffRole
from admin.security import (
    TotpSecretUndecryptableError,
    decrypt_totp_secret,
    verify_password,
)
from admin.totp import TOTP_INTERVAL
from db.blocklist import block_ip, is_blocked
from db.blocklist_models import IpBlock

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


def stored_secret(session, username: str, device_name: str = "Authenticator") -> bytes:
    """The ciphertext as it sits in the database, for one device.

    Since contract v1.13 an account can hold several, so "the account's
    secret" is no longer a thing to read - which is exactly what
    `cmd_rotate_key` had to stop assuming.
    """
    staff = get_staff(session, username)
    return next(d.secret_enc for d in staff.totp_devices if d.name == device_name)


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


def test_the_cli_can_recover_an_account_that_happens_to_be_named_cli(session):
    """The CLI's exemption from the self-recovery guard is the `allow_self`
    argument its two commands pass (admin/cli.py), not the accident that the
    actor string it stamps - "cli" - differs from every username.

    `staff.username` is a plain VARCHAR with no reserved values, so an account
    really can be called `cli`. Layer L3 exists precisely for the case where
    nobody can log in to recover it through the panel, and an exemption that
    quietly stopped applying to one username would fail at exactly that
    moment. Both commands are driven here because they take the exemption
    separately.
    """
    enrol(session, "cli", OLD_KEY)

    password = cmd_issue_password(session, "cli")
    cmd_reset_mfa(session, "cli")
    session.flush()

    staff = get_staff(session, "cli")
    assert verify_password(password, staff.password_hash)
    assert staff.mfa_enrolled is False


def test_rotate_key_reencrypts_every_enrolled_secret(session):
    secret_a = enrol(session, "alice", OLD_KEY)
    secret_b = enrol(session, "bob", OLD_KEY)

    rotated, _cleared = cmd_rotate_key(session, old_key=OLD_KEY, new_key=NEW_KEY)
    session.flush()

    assert rotated == 2
    assert (
        decrypt_totp_secret(stored_secret(session, "alice"), secret_key=NEW_KEY)
        == secret_a
    )
    assert (
        decrypt_totp_secret(stored_secret(session, "bob"), secret_key=NEW_KEY)
        == secret_b
    )


def test_rotate_key_reencrypts_every_device_not_one_per_account(session):
    """The count is devices, not accounts, and this is the whole of what
    v1.13 changed about this command.

    A rotation that walked `staff` rows and re-encrypted "the" secret would
    leave every *second* phone readable only with the old key - and
    `cmd_rotate_key`'s decrypt-everything-first ordering could not save it,
    because the row it never looked at never raised. The operator would be
    told "Re-encrypted 1 TOTP secret(s)", restart, and discover the failure
    only when somebody reached for their backup device.
    """
    first = enrol(session, "alice", OLD_KEY)
    second, _uri = begin_mfa_enrolment(
        session, "alice", secret_key=OLD_KEY,
        device_name="Backup phone", allow_additional=True,
    )
    complete_mfa_enrolment(
        session, "alice",
        pyotp.TOTP(second, interval=TOTP_INTERVAL).at(NOW),
        secret_key=OLD_KEY, now=NOW, device_name="Backup phone",
    )
    session.flush()

    rotated, _cleared = cmd_rotate_key(session, old_key=OLD_KEY, new_key=NEW_KEY)
    session.flush()

    assert rotated == 2
    assert decrypt_totp_secret(
        stored_secret(session, "alice"), secret_key=NEW_KEY
    ) == first
    assert decrypt_totp_secret(
        stored_secret(session, "alice", "Backup phone"), secret_key=NEW_KEY
    ) == second


def test_rotate_key_skips_accounts_with_no_enrolment(session):
    enrol(session, "alice", OLD_KEY)
    create_staff(session, username="bob", display_name="Bob")
    session.flush()

    assert cmd_rotate_key(session, old_key=OLD_KEY, new_key=NEW_KEY) == (1, 0)


def test_rotate_key_with_the_wrong_old_key_changes_nothing(session):
    """A half-rotated table would be unrecoverable, so the command must fail
    before writing anything rather than part way through."""
    enrol(session, "alice", OLD_KEY)
    before = stored_secret(session, "alice")

    with pytest.raises(TotpSecretUndecryptableError):
        cmd_rotate_key(session, old_key="a-completely-wrong-key", new_key=NEW_KEY)

    assert stored_secret(session, "alice") == before


def test_rotating_twice_leaves_secrets_readable_with_the_newest_key(session):
    secret = enrol(session, "alice", OLD_KEY)
    cmd_rotate_key(session, old_key=OLD_KEY, new_key=NEW_KEY)
    session.flush()

    third = "third-secret-key-for-tests"
    cmd_rotate_key(session, old_key=NEW_KEY, new_key=third)
    session.flush()

    assert (
        decrypt_totp_secret(stored_secret(session, "alice"), secret_key=third)
        == secret
    )


def test_seed_taxonomy_is_idempotent_through_the_cli(session):
    from admin.cli import cmd_seed_taxonomy

    first = cmd_seed_taxonomy(session)
    session.flush()
    second = cmd_seed_taxonomy(session)

    assert sum(first.values()) > 0
    assert sum(second.values()) == 0


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

    before_alice = stored_secret(session, "alice")
    before_bob = stored_secret(session, "bob")

    with pytest.raises(TotpSecretUndecryptableError):
        cmd_rotate_key(session, old_key=OLD_KEY, new_key=NEW_KEY)

    assert stored_secret(session, "alice") == before_alice
    assert stored_secret(session, "bob") == before_bob


def test_unblock_command_removes_a_block(session):
    """The escape hatch: an administrator who has blocked the address they
    are sitting behind gets back in with this one command."""
    block_ip(session, "203.0.113.9", reason="test", actor="kim",
              secret_key=OLD_KEY)
    session.flush()

    removed = cmd_unblock(session, "203.0.113.9", secret_key=OLD_KEY)
    session.flush()

    assert removed is True
    assert is_blocked(session, "203.0.113.9", secret_key=OLD_KEY) is False


def test_unblock_command_reports_nothing_to_do_for_an_unblocked_address(session):
    assert cmd_unblock(session, "203.0.113.9", secret_key=OLD_KEY) is False


def test_unblock_command_writes_an_audit_entry_naming_no_address(session):
    """db.blocklist writes no audit entry of its own (auditing is the
    caller's job); the CLI is the caller here, same as every other
    server-side recovery command in this file."""
    block_ip(session, "203.0.113.9", reason="test", actor="kim",
              secret_key=OLD_KEY)
    session.flush()

    cmd_unblock(session, "203.0.113.9", secret_key=OLD_KEY)
    session.flush()

    entry = session.scalar(
        select(AuditLog).where(AuditLog.table_name == "ip_block")
    )
    assert entry is not None
    assert entry.actor == "cli"
    assert entry.action == "delete"
    assert "203.0.113.9" not in str(entry.before_json)
    assert "203.0.113.9" not in str(entry.after_json)


def test_rotate_key_clears_the_blocklist(session):
    """SECRET_KEY also derives the key `ip_block.ip_hmac` is computed under
    (db/blocklist.py's BLOCKLIST_INFO), and an HMAC cannot be re-keyed the way
    an encrypted TOTP secret can — there is no plaintext address left to
    re-fingerprint from, which is exactly the property §2.3 wanted.

    So before this, a documented and supported rotation left every block
    behind as an unreachable value: `is_blocked` matched nothing, `cli
    unblock` could not remove the row either (it recomputes a new-key
    fingerprint to find it), and the panel went on listing it as though it
    were in force. An unreachable row that silently stops blocking is worse
    than no row.
    """
    enrol(session, "alice", OLD_KEY)
    block_ip(session, "203.0.113.9", reason="scripted traffic", actor="kim",
             secret_key=OLD_KEY)
    block_ip(session, "198.51.100.7", reason="burst", actor="kim",
             secret_key=OLD_KEY)
    session.flush()

    rotated, cleared = cmd_rotate_key(session, old_key=OLD_KEY, new_key=NEW_KEY)
    session.flush()

    assert (rotated, cleared) == (1, 2)
    assert session.scalars(select(IpBlock)).all() == []


def test_rotate_key_leaves_no_block_that_the_new_key_cannot_reach(session):
    """The consequence stated as the operator would experience it: after a
    rotation there is no row that `is_blocked` misses and `unblock` cannot
    remove. Asserted through the public functions rather than by counting
    rows, so it still holds if the implementation ever changes from a delete
    to something else."""
    block_ip(session, "203.0.113.9", reason="scripted traffic", actor="kim",
             secret_key=OLD_KEY)
    session.flush()

    cmd_rotate_key(session, old_key=OLD_KEY, new_key=NEW_KEY)
    session.flush()

    assert is_blocked(session, "203.0.113.9", secret_key=NEW_KEY) is False
    assert session.scalars(select(IpBlock)).all() == []


def test_a_failed_rotation_leaves_the_blocklist_alone(session):
    """Same all-or-nothing rule the TOTP half already follows: a rotation that
    raises must not have destroyed the blocklist on its way out, because the
    operator's next move is to retry with the correct old key."""
    enrol(session, "alice", OLD_KEY)
    block_ip(session, "203.0.113.9", reason="scripted traffic", actor="kim",
             secret_key=OLD_KEY)
    session.flush()

    with pytest.raises(TotpSecretUndecryptableError):
        cmd_rotate_key(session, old_key="a-completely-wrong-key", new_key=NEW_KEY)

    assert is_blocked(session, "203.0.113.9", secret_key=OLD_KEY) is True


def test_unblock_refuses_a_value_that_is_not_an_address(session):
    """`ip_fingerprint` raises rather than hashing unparseable input, so this
    command has to say so rather than surfacing an ipaddress traceback from
    four frames down to an operator who is already locked out."""
    from db.blocklist import InvalidAddressError

    with pytest.raises(InvalidAddressError):
        cmd_unblock(session, "203.0.113.9:54321", secret_key=OLD_KEY)
