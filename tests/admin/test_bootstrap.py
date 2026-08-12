"""admin.bootstrap - first-start administrator accounts.

Contract: docs/interfaces.md 8.3, "Bootstrap".
"""

import pytest

from admin.accounts import (
    count_active_admins,
    count_usable_admins,
    create_staff,
    get_staff,
)
from admin.bootstrap import BOOTSTRAP_USERNAMES, ensure_bootstrap_admins
from admin.models import StaffRole
from admin.security import verify_password

#: The key `create_staff` writes `unclaimed_password_enc` under (contract
#: v1.15). Its value is immaterial here - nothing in this file reads the
#: column back - but the argument is required, deliberately: an optional one
#: is a caller that silently forgot, and an account created without it would
#: have nothing to reveal, discovered only by the administrator who looked.
SECRET_KEY = "test-secret-key-not-used-anywhere-real"

pytestmark = pytest.mark.db


def test_an_empty_system_gets_two_administrators(session):
    """A deployment satisfies the two-administrator rule from the moment it
    comes up, instead of depending on whoever installs it running the CLI
    twice."""
    created = ensure_bootstrap_admins(session, secret_key=SECRET_KEY)
    session.flush()

    assert len(created) == 2
    assert count_active_admins(session) == 2


def test_bootstrap_accounts_are_administrators(session):
    ensure_bootstrap_admins(session, secret_key=SECRET_KEY)
    session.flush()

    for username in BOOTSTRAP_USERNAMES:
        assert get_staff(session, username).role is StaffRole.admin


def test_the_reported_passwords_actually_work(session):
    created = ensure_bootstrap_admins(session, secret_key=SECRET_KEY)
    session.flush()

    for username, password in created:
        staff = get_staff(session, username)
        assert verify_password(password, staff.password_hash) is True


def test_the_two_accounts_do_not_share_a_password(session):
    """A single shared value would make the second account worthless as an
    independent recovery path."""
    created = ensure_bootstrap_admins(session, secret_key=SECRET_KEY)

    assert created[0][1] != created[1][1]


def test_no_password_is_derived_from_the_username(session):
    """Guards against the obvious bad implementation. There must be no fixed
    or predictable default anywhere."""
    created = ensure_bootstrap_admins(session, secret_key=SECRET_KEY)

    for username, password in created:
        assert username not in password.lower()
        assert password.lower() not in ("admin", "password", "changeme")


def test_bootstrap_accounts_must_change_their_password(session):
    ensure_bootstrap_admins(session, secret_key=SECRET_KEY)
    session.flush()

    for username in BOOTSTRAP_USERNAMES:
        assert get_staff(session, username).must_change_password is True


def test_bootstrap_accounts_have_no_mfa_enrolment(session):
    """So require_staff refuses them until both setup steps are done — the
    accounts exist, but they cannot yet reach anything."""
    ensure_bootstrap_admins(session, secret_key=SECRET_KEY)
    session.flush()

    for username in BOOTSTRAP_USERNAMES:
        assert get_staff(session, username).mfa_enrolled is False


def test_bootstrap_does_nothing_when_administrators_already_exist(session):
    create_staff(
        session, username="someone", display_name="Someone", role=StaffRole.admin, actor="test",
        secret_key=SECRET_KEY,
    )
    session.flush()

    assert ensure_bootstrap_admins(session, secret_key=SECRET_KEY) == []


def test_bootstrap_is_idempotent_across_restarts(session):
    ensure_bootstrap_admins(session, secret_key=SECRET_KEY)
    session.flush()

    assert ensure_bootstrap_admins(session, secret_key=SECRET_KEY) == []
    session.flush()
    assert count_active_admins(session) == 2


def test_bootstrap_is_idempotent_while_no_administrator_is_usable_yet(session):
    """Bootstrap asks whether administrator accounts exist, not whether anyone
    can log in yet — and the difference matters at exactly this moment.

    Straight after a first start both accounts carry a forced password change
    and no MFA enrolment, so zero administrators are usable. If bootstrap
    consulted the stricter count it would try to create `admin` and `admin2`
    again on every restart until onboarding finished, and collide on usernames
    that already exist.
    """
    ensure_bootstrap_admins(session, secret_key=SECRET_KEY)
    session.flush()
    assert count_usable_admins(session) == 0

    assert ensure_bootstrap_admins(session, secret_key=SECRET_KEY) == []
    session.flush()
    assert count_active_admins(session) == 2


def test_bootstrap_ignores_non_administrator_accounts(session):
    """A system with staff but no administrator is still locked out, so it
    still needs bootstrapping."""
    create_staff(session, username="bob", display_name="Bob", role=StaffRole.staff, actor="test", secret_key=SECRET_KEY)
    session.flush()

    created = ensure_bootstrap_admins(session, secret_key=SECRET_KEY)
    session.flush()

    assert len(created) == 2
    assert count_active_admins(session) == 2
