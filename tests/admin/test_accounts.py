"""admin.accounts - account lifecycle and the two-administrator invariant.

Contract: docs/interfaces.md 8.3.
"""

import pyotp
import pytest
from sqlalchemy import select

from admin.accounts import (
    LastAdministratorsError,
    SelfRecoveryError,
    UnknownStaffError,
    begin_mfa_enrolment,
    complete_mfa_enrolment,
    count_active_admins,
    count_usable_admins,
    create_staff,
    deactivate_staff,
    generate_initial_password,
    get_staff,
    issue_password,
    reset_mfa,
    set_password,
    set_role,
)
from admin.models import AuditLog, Staff, StaffRole, StaffTotpDevice, utcnow
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


# --- No recovery action may be aimed at the actor's own account ------------
#
# The guard lives here, beside the two-administrator floor, rather than in
# admin/accounts_view.py, because the view is not the only caller: admin/cli.py
# reaches the same two functions, and so will anything added later. A guard in
# the view is a guard on one of the paths.


def _enrolled(session, username: str):
    """An account with something for reset_mfa to destroy.

    The enrolment is hand-set rather than driven through
    begin/complete_mfa_enrolment - what these tests need is a device row and
    a timestamp to compare against afterwards, and a real TOTP round trip
    would only make the assertions harder to read. tests/admin/test_enrolment.py
    covers the real path.

    Two devices, not one, since contract v1.13 made that possible. Every
    caller here is testing something reset_mfa or the self-recovery guard
    does to an *enrolment*, and an enrolment is now a collection: a reset
    that removed one device and left the other would satisfy a single-device
    fixture completely while leaving the account it was aimed at still
    holding a working second factor.
    """
    create_staff(session, username=username, display_name=username.title())
    session.flush()
    staff = get_staff(session, username)
    staff.totp_devices.append(
        StaffTotpDevice(name="Authenticator", secret_enc=b"not-a-real-secret-1",
                        enrolled_at=utcnow(), created_at=utcnow())
    )
    staff.totp_devices.append(
        StaffTotpDevice(name="Backup phone", secret_enc=b"not-a-real-secret-2",
                        enrolled_at=utcnow(), created_at=utcnow())
    )
    staff.mfa_enrolled_at = utcnow()
    session.flush()
    return staff


def test_issuing_yourself_a_password_is_refused(session):
    """Issuing a password is a password change that never asks for the
    current one. Aimed at yourself it is the first half of a takeover from a
    stolen session, not recovery."""
    create_staff(session, username="alice", display_name="Alice Example")
    session.flush()
    before = get_staff(session, "alice")
    hash_before = before.password_hash
    generation_before = before.session_generation

    with pytest.raises(SelfRecoveryError):
        issue_password(session, "alice", actor="alice")

    staff = get_staff(session, "alice")
    assert staff.password_hash == hash_before
    assert staff.session_generation == generation_before


def test_a_refused_self_issue_writes_no_audit_entry_of_its_own(session):
    """issue_password audits the change it makes. A refused call makes no
    change, so it must leave no entry claiming one - the refusal is recorded
    by the caller that turned it away (admin/accounts_view.py), which knows
    it was a refusal."""
    create_staff(session, username="alice", display_name="Alice Example")
    session.flush()

    with pytest.raises(SelfRecoveryError):
        issue_password(session, "alice", actor="alice")

    assert session.scalars(select(AuditLog)).all() == []


def test_resetting_your_own_authenticator_is_refused(session):
    """Resetting MFA is a second factor that never asks for the device."""
    staff = _enrolled(session, "alice")
    secrets_before = [d.secret_enc for d in staff.totp_devices]
    enrolled_before = staff.mfa_enrolled_at

    with pytest.raises(SelfRecoveryError):
        reset_mfa(session, "alice", actor="alice")

    staff = get_staff(session, "alice")
    assert [d.secret_enc for d in staff.totp_devices] == secrets_before
    assert staff.mfa_enrolled_at == enrolled_before


def test_the_self_check_folds_case_the_way_a_username_does(session):
    """Usernames are stored casefolded (_normalise_username), and the actor
    string arrives from a session cookie that was written at login. A guard
    comparing the two raw would be defeated by typing one capital letter into
    the login form."""
    create_staff(session, username="alice", display_name="Alice Example")
    session.flush()

    with pytest.raises(SelfRecoveryError):
        issue_password(session, "alice", actor="  Alice  ")


def test_another_administrator_is_the_case_these_actions_exist_for(session):
    """The guard must not turn the feature off. A colleague acting on a
    locked-out account is recovery layer L2 itself."""
    create_staff(session, username="alice", display_name="Alice Example")
    session.flush()

    issued = issue_password(session, "alice", actor="bob")
    session.flush()

    assert verify_password(issued, get_staff(session, "alice").password_hash)


def test_the_cli_may_act_on_the_account_it_is_recovering(session):
    """Layer L3. The exemption is a parameter the caller passes, not a
    consequence of the CLI reaching a different function - see
    admin/cli.py's own comment for why it is the one caller that gets it."""
    staff = _enrolled(session, "alice")

    issued = issue_password(session, "alice", actor="alice", allow_self=True)
    reset_mfa(session, "alice", actor="alice", allow_self=True)
    session.flush()

    staff = get_staff(session, "alice")
    assert verify_password(issued, staff.password_hash)
    assert staff.totp_devices == []
    assert staff.mfa_enrolled_at is None
