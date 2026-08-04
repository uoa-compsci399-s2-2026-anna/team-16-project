"""admin.accounts - account lifecycle and the two-administrator invariant.

Contract: docs/interfaces.md 8.3.
"""

import pytest
from sqlalchemy import select

from admin.accounts import (
    LastAdministratorsError,
    UnknownStaffError,
    count_active_admins,
    create_staff,
    deactivate_staff,
    generate_initial_password,
    set_password,
    set_role,
)
from admin.models import Staff, StaffRole
from admin.security import verify_password

pytestmark = pytest.mark.db


def add_admins(session, count: int) -> None:
    for index in range(count):
        create_staff(
            session,
            username=f"admin{index}",
            display_name=f"Admin {index}",
            role=StaffRole.admin,
        )
    session.flush()


def test_generated_initial_passwords_are_long_and_unrepeated():
    first = generate_initial_password()
    second = generate_initial_password()

    assert len(first) >= 16
    assert first != second


def test_create_staff_returns_a_working_initial_password(session):
    staff, password = create_staff(
        session, username="alice", display_name="Alice Example"
    )
    session.flush()

    assert verify_password(password, staff.password_hash) is True


def test_created_accounts_must_change_their_password(session):
    staff, _ = create_staff(session, username="alice", display_name="Alice Example")

    assert staff.must_change_password is True


def test_created_accounts_are_not_yet_enrolled_in_mfa(session):
    staff, _ = create_staff(session, username="alice", display_name="Alice Example")

    assert staff.mfa_enrolled_at is None


def test_create_staff_records_who_created_the_account(session):
    staff, _ = create_staff(
        session, username="alice", display_name="Alice Example", actor="admin0"
    )

    assert staff.created_by == "admin0"


def test_setting_a_password_clears_the_must_change_flag(session):
    create_staff(session, username="alice", display_name="Alice Example")
    session.flush()

    set_password(session, "alice", "a brand new password")
    session.flush()

    stored = session.scalar(select(Staff).where(Staff.username == "alice"))
    assert stored.must_change_password is False
    assert verify_password("a brand new password", stored.password_hash) is True


def test_setting_a_password_for_an_unknown_account_raises(session):
    with pytest.raises(UnknownStaffError):
        set_password(session, "nobody", "irrelevant")


def test_count_active_admins_ignores_deactivated_and_non_admin_accounts(session):
    add_admins(session, 2)
    create_staff(session, username="bob", display_name="Bob", role=StaffRole.staff)
    session.flush()

    assert count_active_admins(session) == 2


def test_a_third_administrator_can_be_deactivated(session):
    add_admins(session, 3)

    deactivate_staff(session, "admin2")
    session.flush()

    assert count_active_admins(session) == 2


def test_deactivating_an_administrator_is_refused_when_only_two_remain(session):
    """Contract 8.3: at least two active administrators at all times. Without
    an email system, administrators are each other's recovery path."""
    add_admins(session, 2)

    with pytest.raises(LastAdministratorsError):
        deactivate_staff(session, "admin1")


def test_demoting_an_administrator_is_refused_when_only_two_remain(session):
    """Demotion removes an administrator just as effectively as deactivation,
    so it has to be guarded by the same rule."""
    add_admins(session, 2)

    with pytest.raises(LastAdministratorsError):
        set_role(session, "admin1", StaffRole.staff)


def test_a_non_administrator_can_always_be_deactivated(session):
    add_admins(session, 2)
    create_staff(session, username="bob", display_name="Bob", role=StaffRole.staff)
    session.flush()

    deactivate_staff(session, "bob")
    session.flush()

    stored = session.scalar(select(Staff).where(Staff.username == "bob"))
    assert stored.is_active is False


def test_promoting_a_staff_member_to_administrator_is_allowed(session):
    add_admins(session, 2)
    create_staff(session, username="bob", display_name="Bob", role=StaffRole.staff)
    session.flush()

    set_role(session, "bob", StaffRole.admin)
    session.flush()

    assert count_active_admins(session) == 3


def test_the_guard_does_not_block_a_no_op_role_change(session):
    """Setting an administrator's role to administrator changes nothing and
    must not be refused."""
    add_admins(session, 2)

    set_role(session, "admin1", StaffRole.admin)
    session.flush()

    assert count_active_admins(session) == 2


def test_usernames_are_stored_folded_to_lower_case(session):
    """So that 'Alice' and 'alice' cannot become two accounts."""
    staff, _ = create_staff(session, username="Alice", display_name="Alice Example")

    assert staff.username == "alice"
