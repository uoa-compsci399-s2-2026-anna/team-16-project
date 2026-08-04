"""admin.accounts - account lifecycle and the two-administrator invariant.

Contract: docs/interfaces.md 8.3.
"""

import pyotp
import pytest
from sqlalchemy import select

from admin.accounts import (
    LastAdministratorsError,
    UnknownStaffError,
    begin_mfa_enrolment,
    complete_mfa_enrolment,
    count_active_admins,
    count_usable_admins,
    create_staff,
    deactivate_staff,
    generate_initial_password,
    get_staff,
    set_password,
    set_role,
)
from admin.models import Staff, StaffRole, utcnow
from admin.security import verify_password
from admin.totp import TOTP_INTERVAL

pytestmark = pytest.mark.db

SECRET_KEY = "test-secret-key-not-used-anywhere-real"


def add_admins(session, count: int) -> None:
    """Administrators in the state create_staff and bootstrap leave them in:
    active, but with a forced password change outstanding and no
    authenticator, so none of them can actually log in yet."""
    for index in range(count):
        create_staff(
            session,
            username=f"admin{index}",
            display_name=f"Admin {index}",
            role=StaffRole.admin,
        )
    session.flush()


def onboard(session, username: str) -> None:
    """Finish contract 8.3's onboarding for an account.

    Sets the two fields the real flow sets, rather than replaying it: the
    password change and the enrolment are covered by their own tests, and
    bcrypt plus a TOTP round trip in every fixture here would buy nothing.
    test_count_usable_admins_counts_an_administrator_who_finished_onboarding
    anchors this shortcut against the real API.
    """
    staff = get_staff(session, username)
    staff.must_change_password = False
    staff.mfa_enrolled_at = utcnow()
    session.flush()


def add_usable_admins(session, count: int) -> None:
    """Administrators who have completed onboarding and can log in."""
    add_admins(session, count)
    for index in range(count):
        onboard(session, f"admin{index}")


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
    add_usable_admins(session, 3)

    deactivate_staff(session, "admin2")
    session.flush()

    assert count_active_admins(session) == 2


def test_deactivating_an_administrator_is_refused_when_only_two_remain(session):
    """Contract 8.3: at least two active administrators at all times. Without
    an email system, administrators are each other's recovery path."""
    add_usable_admins(session, 2)

    with pytest.raises(LastAdministratorsError):
        deactivate_staff(session, "admin1")


def test_demoting_an_administrator_is_refused_when_only_two_remain(session):
    """Demotion removes an administrator just as effectively as deactivation,
    so it has to be guarded by the same rule."""
    add_usable_admins(session, 2)

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


# --- the administrator floor counts administrators who can actually log in ---


def test_count_usable_admins_ignores_administrators_who_cannot_log_in(session):
    """The two counts are not interchangeable.

    Freshly created administrators are active but carry a forced password
    change and no authenticator, so require_staff refuses them. They keep the
    system out of the "no administrators at all" state that bootstrap looks
    for, but they are not a recovery path for anybody.
    """
    add_admins(session, 2)

    assert count_active_admins(session) == 2
    assert count_usable_admins(session) == 0


def test_count_usable_admins_counts_an_administrator_who_finished_onboarding(session):
    """Walks the real flow rather than setting the columns, so the shortcut
    the other tests here use is anchored to what the service layer does."""
    _, password = create_staff(
        session, username="admin0", display_name="Admin 0", role=StaffRole.admin
    )
    session.flush()
    set_password(session, "admin0", password)
    secret, _ = begin_mfa_enrolment(session, "admin0", secret_key=SECRET_KEY)
    now = 1800
    complete_mfa_enrolment(
        session,
        "admin0",
        pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(now),
        secret_key=SECRET_KEY,
        now=now,
    )
    session.flush()

    assert count_usable_admins(session) == 1


def test_count_usable_admins_ignores_deactivated_and_non_admin_accounts(session):
    add_usable_admins(session, 2)
    create_staff(session, username="bob", display_name="Bob", role=StaffRole.staff)
    session.flush()
    onboard(session, "bob")

    assert count_usable_admins(session) == 2


def test_deactivating_the_only_usable_administrator_is_refused(session):
    """The permanent-lockout path, and the reason the floor cannot count
    active administrators alone.

    The charity onboards `admin` and files `admin2`'s printed password away
    unused. Creating a third account and deactivating the real one would leave
    the panel owned by two accounts nobody can log into, with no email system
    and no reset link: contract 8.3's layer L2 is gone and only server shell
    access is left.
    """
    add_admins(session, 3)
    onboard(session, "admin0")

    with pytest.raises(LastAdministratorsError):
        deactivate_staff(session, "admin0")


def test_demoting_the_only_usable_administrator_is_refused(session):
    """Demotion removes a usable administrator just as effectively."""
    add_admins(session, 3)
    onboard(session, "admin0")

    with pytest.raises(LastAdministratorsError):
        set_role(session, "admin0", StaffRole.staff)


def test_deactivating_an_administrator_is_allowed_once_three_are_usable(session):
    """The floor is a floor, not a freeze: it has to let go once enough
    administrators can genuinely log in."""
    add_usable_admins(session, 3)

    deactivate_staff(session, "admin2")
    session.flush()

    assert count_usable_admins(session) == 2
