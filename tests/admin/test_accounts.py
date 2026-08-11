"""admin.accounts - account lifecycle and the two-administrator invariant.

Contract: docs/interfaces.md 8.3.
"""

import time
from datetime import timedelta

import pyotp
import pytest
from sqlalchemy import select

from admin.accounts import (
    MAX_TOTP_DEVICES,
    AccountStillActiveError,
    DuplicateDeviceNameError,
    InvalidDeviceNameError,
    LastAdministratorsError,
    MfaNotEnrolledError,
    SelfRecoveryError,
    TooManyDevicesError,
    UnknownDeviceError,
    UnknownStaffError,
    begin_mfa_enrolment,
    complete_mfa_enrolment,
    count_active_admins,
    count_usable_admins,
    create_staff,
    deactivate_staff,
    delete_staff,
    discard_unconfirmed_devices,
    generate_initial_password,
    get_staff,
    issue_password,
    last_password_change,
    reactivate_staff,
    rename_totp_device,
    reset_mfa,
    set_password,
    set_role,
    verify_staff_totp,
)
from admin.audit import write_audit
from admin.models import (
    AuditLog,
    Staff,
    StaffRecoveryCode,
    StaffRole,
    StaffTotpDevice,
    utcnow,
)
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
            actor="test",
            secret_key=SECRET_KEY,
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
        session, username="alice", display_name="Alice Example", actor="test",
        secret_key=SECRET_KEY,
    )
    session.flush()

    assert verify_password(password, staff.password_hash) is True


def test_created_accounts_must_change_their_password(session):
    staff, _ = create_staff(session, username="alice", display_name="Alice Example", actor="test", secret_key=SECRET_KEY)

    assert staff.must_change_password is True


def test_created_accounts_are_not_yet_enrolled_in_mfa(session):
    staff, _ = create_staff(session, username="alice", display_name="Alice Example", actor="test", secret_key=SECRET_KEY)

    assert staff.mfa_enrolled_at is None


def test_create_staff_records_who_created_the_account(session):
    staff, _ = create_staff(
        session, username="alice", display_name="Alice Example", actor="admin0",
        secret_key=SECRET_KEY,
    )

    assert staff.created_by == "admin0"


def test_setting_a_password_clears_the_must_change_flag(session):
    create_staff(session, username="alice", display_name="Alice Example", actor="test", secret_key=SECRET_KEY)
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
    create_staff(session, username="bob", display_name="Bob", role=StaffRole.staff, actor="test", secret_key=SECRET_KEY)
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
    create_staff(session, username="bob", display_name="Bob", role=StaffRole.staff, actor="test", secret_key=SECRET_KEY)
    session.flush()

    deactivate_staff(session, "bob")
    session.flush()

    stored = session.scalar(select(Staff).where(Staff.username == "bob"))
    assert stored.is_active is False


def test_promoting_a_staff_member_to_administrator_is_allowed(session):
    add_admins(session, 2)
    create_staff(session, username="bob", display_name="Bob", role=StaffRole.staff, actor="test", secret_key=SECRET_KEY)
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
    staff, _ = create_staff(session, username="Alice", display_name="Alice Example", actor="test", secret_key=SECRET_KEY)

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
        session, username="admin0", display_name="Admin 0", role=StaffRole.admin, actor="test",
        secret_key=SECRET_KEY,
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
    create_staff(session, username="bob", display_name="Bob", role=StaffRole.staff, actor="test", secret_key=SECRET_KEY)
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
    create_staff(session, username=username, display_name=username.title(), actor="test", secret_key=SECRET_KEY)
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
    create_staff(session, username="alice", display_name="Alice Example", actor="test", secret_key=SECRET_KEY)
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
    it was a refusal.

    Asserted as "the trail did not move", not as "the trail is empty".
    Creating the account is itself an audited write now, so an empty-table
    assertion would have started failing on a row that has every right to be
    there - and, worse, would have passed for the wrong reason if creation had
    ever stopped auditing itself."""
    create_staff(session, username="alice", display_name="Alice Example", actor="test", secret_key=SECRET_KEY)
    session.flush()
    before = session.scalars(select(AuditLog.id)).all()
    assert len(before) == 1, "creating the account writes exactly one entry"

    with pytest.raises(SelfRecoveryError):
        issue_password(session, "alice", actor="alice")

    assert session.scalars(select(AuditLog.id)).all() == before


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
    create_staff(session, username="alice", display_name="Alice Example", actor="test", secret_key=SECRET_KEY)
    session.flush()

    with pytest.raises(SelfRecoveryError):
        issue_password(session, "alice", actor="  Alice  ")


def test_another_administrator_is_the_case_these_actions_exist_for(session):
    """The guard must not turn the feature off. A colleague acting on a
    locked-out account is recovery layer L2 itself."""
    create_staff(session, username="alice", display_name="Alice Example", actor="test", secret_key=SECRET_KEY)
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


# --- deleting an account ----------------------------------------------------
#
# The design question this whole group exists to settle: a staff row can be
# removed without the audit trail losing the ability to say who did what.
# It holds because audit_log.actor is VARCHAR(128) and carries no foreign key
# to staff - it cannot, since the same column also holds "cli", "bootstrap"
# and "deploy-seed". See admin/accounts.py::delete_staff.


def _deactivated(session, username: str, *, role=StaffRole.staff):
    """An account in the state delete_staff requires: already deactivated."""
    create_staff(
        session, username=username, display_name=username.title(),
        role=role, actor="test",
        secret_key=SECRET_KEY,
    )
    session.flush()
    staff = get_staff(session, username)
    staff.is_active = False
    session.flush()
    return staff


def _gone(session, username: str) -> bool:
    """Whether no account of that name remains."""
    return session.scalar(select(Staff).where(Staff.username == username)) is None


def test_audit_history_survives_deleting_its_author(session):
    """THE test for the deletion strategy.

    An account writes audit entries, is deleted, and the entries are read
    back - still present, still naming their author, still carrying their
    payloads. This is the property that made a hard delete the right answer
    rather than a tombstone, and it is asserted rather than reasoned about
    because the whole design rests on it.
    """
    create_staff(session, username="victim", display_name="Victim", actor="test", secret_key=SECRET_KEY)
    _deactivated(session, "author")
    session.flush()

    # Two entries written *by* the account about to be deleted, of the two
    # shapes this panel actually produces: one from a service function
    # (issue_password) and one a view would write by hand.
    issue_password(session, "victim", actor="author")
    write_audit(
        session, actor="author", action="update", table_name="factor_set",
        row_id=99, before={"status": "draft"}, after={"status": "published"},
    )
    session.flush()

    by_author = session.scalars(
        select(AuditLog).where(AuditLog.actor == "author").order_by(AuditLog.id)
    ).all()
    assert len(by_author) == 2, "precondition: the account wrote two entries"

    delete_staff(session, "author", actor="admin0")
    session.flush()

    assert _gone(session, "author"), "the account is gone"

    after = session.scalars(
        select(AuditLog).where(AuditLog.actor == "author").order_by(AuditLog.id)
    ).all()
    assert len(after) == 2, "the entries it wrote are still there"
    assert [row.actor for row in after] == ["author", "author"], (
        "and they still say who wrote them"
    )
    assert after[1].after_json == {"status": "published"}, (
        "with their payloads intact, not blanked or anonymised"
    )


def test_deleting_an_account_writes_its_own_audit_entry(session):
    """The entry most worth having: after the commit it is the only record
    that the account ever existed, so it carries the identity rather than a
    reference to a row that no longer resolves."""
    _deactivated(session, "alice", role=StaffRole.staff)
    session.flush()

    delete_staff(session, "alice", actor="admin0")
    session.flush()

    entry = session.scalars(
        select(AuditLog)
        .where(AuditLog.table_name == "staff", AuditLog.action == "delete")
        .order_by(AuditLog.id.desc())
    ).first()
    assert entry is not None, "a deletion is a write and must leave a trace"
    assert entry.actor == "admin0"
    assert entry.before_json["username"] == "alice"
    assert entry.before_json["display_name"] == "Alice"
    assert entry.before_json["role"] == "staff"
    assert entry.after_json is None


def test_deleting_an_account_destroys_its_authenticators_and_recovery_codes(session):
    """A stored TOTP secret must not outlive the account it authenticates.
    Both cascade, at the database and through the ORM."""
    _enrolled(session, "alice")
    staff = get_staff(session, "alice")
    staff.is_active = False
    session.add(
        StaffRecoveryCode(staff_id=staff.id, code_hash="0" * 64, created_at=utcnow())
    )
    session.flush()
    staff_id = staff.id
    assert session.scalars(
        select(StaffTotpDevice).where(StaffTotpDevice.staff_id == staff_id)
    ).all(), "precondition: the account holds devices"

    delete_staff(session, "alice", actor="admin0")
    session.flush()

    assert session.scalars(
        select(StaffTotpDevice).where(StaffTotpDevice.staff_id == staff_id)
    ).all() == []
    assert session.scalars(
        select(StaffRecoveryCode).where(StaffRecoveryCode.staff_id == staff_id)
    ).all() == []


def test_an_active_account_cannot_be_deleted(session):
    """Deactivation first, so deletion acts on a row deactivate_staff has
    already made inert rather than racing a live session."""
    create_staff(session, username="alice", display_name="Alice", actor="test", secret_key=SECRET_KEY)
    session.flush()

    with pytest.raises(AccountStillActiveError):
        delete_staff(session, "alice", actor="admin0")

    assert not _gone(session, "alice"), "the account is untouched"


def test_a_deactivated_account_can_be_deleted(session):
    """The permitted case. A refusal test alone passes against a function
    that refuses everything, which is this project's recorded failure mode."""
    _deactivated(session, "alice")
    session.flush()

    removed = delete_staff(session, "alice", actor="admin0")
    session.flush()

    assert removed["username"] == "alice"
    assert _gone(session, "alice")


def test_deleting_your_own_account_is_refused(session):
    """An administrator deleting their own account has done to the
    two-administrator floor, alone and in one action, what deactivation is
    refused for doing."""
    _deactivated(session, "alice", role=StaffRole.admin)
    session.flush()

    with pytest.raises(SelfRecoveryError):
        delete_staff(session, "alice", actor="alice")

    assert not _gone(session, "alice")


def test_the_self_deletion_check_folds_case(session):
    """Same reasoning as the issue_password equivalent: usernames are stored
    casefolded and the actor arrives from a cookie written at login."""
    _deactivated(session, "alice", role=StaffRole.admin)
    session.flush()

    with pytest.raises(SelfRecoveryError):
        delete_staff(session, "alice", actor="  Alice  ")


def test_the_cli_may_delete_the_account_it_is_signed_in_as(session):
    """Layer L3 has no acting session to be a second party, the same
    exemption reset-mfa and issue-password already carry."""
    _deactivated(session, "alice")
    session.flush()

    delete_staff(session, "alice", actor="alice", allow_self=True)
    session.flush()

    assert _gone(session, "alice")


def test_an_administrator_at_the_floor_cannot_be_removed_by_either_route(session):
    """Contract 8.3 names deletion beside deactivation and demotion.

    Deletion requires deactivation and _guard_admin_floor returns early for an
    inactive row, so the floor bites at the first step: the account never
    reaches a state in which it could be deleted. Both halves are asserted -
    the refusal, and then that clearing the floor lets the same account be
    removed outright, since a guard that turned the capability off would
    satisfy the refusal half on its own.
    """
    add_usable_admins(session, 2)

    with pytest.raises(LastAdministratorsError):
        deactivate_staff(session, "admin1")
    assert get_staff(session, "admin1").is_active is True

    with pytest.raises(AccountStillActiveError):
        delete_staff(session, "admin1", actor="admin0")
    assert not _gone(session, "admin1")

    create_staff(
        session, username="admin9", display_name="Admin 9",
        role=StaffRole.admin, actor="test",
        secret_key=SECRET_KEY,
    )
    session.flush()
    onboard(session, "admin9")

    deactivate_staff(session, "admin1")
    session.flush()
    delete_staff(session, "admin1", actor="admin0")
    session.flush()

    assert _gone(session, "admin1")


def test_a_recycled_row_id_does_not_inherit_a_dead_accounts_password_date(session):
    """SQLite hands out max(rowid)+1 and reuses an id the moment a row is
    deleted; this module is imported by SQLite and MySQL both. Without the
    created_at scope in last_password_change, a new account handed a dead
    one's id is told its password was last changed on a date belonging to
    somebody who no longer exists.

    Driven by writing the old entry at the id and then moving the new account
    onto it, rather than by hoping the database recycles one, so the assertion
    holds on both backends.

    The ghost's entry is backdated a day. Not to make the test pass - it is
    what the scenario is: an account that lived, changed its password, and was
    deleted before the replacement was created. Without the backdate the whole
    fixture runs inside one second and DATETIME has no room to separate the
    two, which tests the clock rather than the guard.
    """
    _deactivated(session, "ghost")
    session.flush()
    ghost_id = get_staff(session, "ghost").id
    write_audit(
        session, actor="ghost", action="update", table_name="staff",
        row_id=ghost_id, before=None,
        after={"username": "ghost", "changed": "password"},
    )
    session.flush()
    ghost_entry = session.scalars(
        select(AuditLog).where(AuditLog.actor == "ghost").order_by(AuditLog.id.desc())
    ).first()
    ghost_entry.at = utcnow() - timedelta(days=1)
    session.flush()
    delete_staff(session, "ghost", actor="admin0")
    session.flush()

    create_staff(session, username="newcomer", display_name="Newcomer", actor="test", secret_key=SECRET_KEY)
    session.flush()
    newcomer = get_staff(session, "newcomer")
    # Stand in for the id reuse SQLite performs on its own.
    newcomer.id = ghost_id
    session.flush()

    assert last_password_change(session, newcomer) is None, (
        "an account cannot have changed its password before it existed"
    )


def test_a_password_change_after_creation_is_still_found(session):
    """The other half of the guard above. Scoping the read by created_at must
    not hide the entries it exists to find."""
    create_staff(session, username="alice", display_name="Alice", actor="test", secret_key=SECRET_KEY)
    session.flush()
    staff = get_staff(session, "alice")
    write_audit(
        session, actor="alice", action="update", table_name="staff",
        row_id=staff.id, before=None,
        after={"username": "alice", "changed": "password"},
    )
    session.flush()

    assert last_password_change(session, staff) is not None


# --- reactivation -----------------------------------------------------------


def test_a_deactivated_account_can_be_reactivated(session):
    """Deletion requires deactivation first, which is defensible only if the
    step it makes mandatory can be undone."""
    _deactivated(session, "alice")
    session.flush()

    reactivate_staff(session, "alice")
    session.flush()

    assert get_staff(session, "alice").is_active is True


def test_reactivation_bumps_the_session_generation(session):
    """Symmetry with deactivate_staff. Nothing is live to evict, and the
    invariant holding without exception is what the next caller relies on."""
    _deactivated(session, "alice")
    session.flush()
    before = get_staff(session, "alice").session_generation

    reactivate_staff(session, "alice")
    session.flush()

    assert get_staff(session, "alice").session_generation == before + 1


# --- authenticator names ----------------------------------------------------


def test_an_authenticator_can_be_renamed(session):
    """The capability the column was missing. A name nobody can revise is a
    name nobody invests in, which is why every one of them said "admin"."""
    _enrolled(session, "alice")
    device = get_staff(session, "alice").totp_devices[0]

    rename_totp_device(session, "alice", device.id, "Work phone")
    session.flush()

    assert get_staff(session, "alice").totp_devices[0].name == "Work phone"


def test_renaming_does_not_bump_the_session_generation(session):
    """A note against a row is not a credential change. remove_totp_device
    bumps precisely because it is one."""
    _enrolled(session, "alice")
    device = get_staff(session, "alice").totp_devices[0]
    before = get_staff(session, "alice").session_generation

    rename_totp_device(session, "alice", device.id, "Work phone")
    session.flush()

    assert get_staff(session, "alice").session_generation == before


def test_renaming_leaves_the_secret_and_the_enrolment_alone(session):
    """The rename must not disturb what the device actually is. A rename that
    re-minted the secret would invalidate a working second factor to correct a
    caption."""
    _enrolled(session, "alice")
    device = get_staff(session, "alice").totp_devices[0]
    secret_before = device.secret_enc
    enrolled_before = device.enrolled_at
    account_before = get_staff(session, "alice").mfa_enrolled_at

    rename_totp_device(session, "alice", device.id, "Work phone")
    session.flush()

    staff = get_staff(session, "alice")
    assert staff.totp_devices[0].secret_enc == secret_before
    assert staff.totp_devices[0].enrolled_at == enrolled_before
    assert staff.mfa_enrolled_at == account_before


def test_renaming_to_a_name_the_account_already_uses_is_refused(session):
    """Refused here rather than left to uq_staff_totp_device_name, which
    reaches the panel as a 500 and the CLI as a traceback."""
    _enrolled(session, "alice")
    device = get_staff(session, "alice").totp_devices[0]

    with pytest.raises(DuplicateDeviceNameError):
        rename_totp_device(session, "alice", device.id, "Backup phone")


def test_renaming_a_device_to_its_own_name_is_allowed(session):
    """The uniqueness check has to exclude the row being renamed, or somebody
    correcting a typo is told the name is taken by the device they are
    editing."""
    _enrolled(session, "alice")
    device = get_staff(session, "alice").totp_devices[0]

    rename_totp_device(session, "alice", device.id, "Authenticator")
    session.flush()

    assert get_staff(session, "alice").totp_devices[0].name == "Authenticator"


def test_renaming_refuses_an_empty_or_over_length_name(session):
    _enrolled(session, "alice")
    device = get_staff(session, "alice").totp_devices[0]

    with pytest.raises(InvalidDeviceNameError):
        rename_totp_device(session, "alice", device.id, "   ")
    with pytest.raises(InvalidDeviceNameError):
        rename_totp_device(session, "alice", device.id, "x" * 65)

    # The boundary itself is accepted - an off-by-one here would refuse a name
    # the column can hold and the form's own maxlength permits.
    rename_totp_device(session, "alice", device.id, "x" * 64)
    session.flush()
    assert get_staff(session, "alice").totp_devices[0].name == "x" * 64


def test_one_account_cannot_rename_anothers_authenticator(session):
    """Scoped within the account in the service layer, so the refusal is a
    property of this function rather than of whichever view calls it."""
    _enrolled(session, "alice")
    _enrolled(session, "bob")
    bobs_device = get_staff(session, "bob").totp_devices[0]

    with pytest.raises(UnknownDeviceError):
        rename_totp_device(session, "alice", bobs_device.id, "Stolen")

    assert get_staff(session, "bob").totp_devices[0].name == "Authenticator"


# --- unconfirmed enrolments -------------------------------------------------


def _with_unconfirmed(session, username: str, name: str = "Half-scanned"):
    """A confirmed enrolment plus one abandoned scan - the exact state the
    reported screenshot shows."""
    _enrolled(session, username)
    staff = get_staff(session, username)
    staff.totp_devices.append(
        StaffTotpDevice(name=name, secret_enc=b"never-confirmed",
                        enrolled_at=None, created_at=utcnow())
    )
    session.flush()
    return staff


def test_discarding_removes_the_unconfirmed_device(session):
    _with_unconfirmed(session, "alice")

    discarded = discard_unconfirmed_devices(session, "alice")
    session.flush()

    assert [d.name for d in discarded] == ["Half-scanned"]
    names = [d.name for d in get_staff(session, "alice").totp_devices]
    assert "Half-scanned" not in names


def test_discarding_cannot_destroy_a_confirmed_device(session):
    """The failure mode that locks somebody out of their own account.

    mfa_enrolled_at is asserted **unchanged**, not merely non-null:
    _sync_mfa_enrolled_at only stamps on the none-to-some transition, so a
    reaper that wrongly destroyed the confirmed devices and let them be
    re-derived would move the timestamp. An equality assertion kills a mutant
    a null check would let live.
    """
    staff = _with_unconfirmed(session, "alice")
    enrolled_before = staff.mfa_enrolled_at
    confirmed_before = sorted(
        (d.name, d.secret_enc) for d in staff.enrolled_totp_devices
    )
    assert len(confirmed_before) == 2, "precondition: two confirmed devices"

    discard_unconfirmed_devices(session, "alice")
    session.flush()

    staff = get_staff(session, "alice")
    assert sorted(
        (d.name, d.secret_enc) for d in staff.enrolled_totp_devices
    ) == confirmed_before
    assert staff.mfa_enrolled_at == enrolled_before


def test_discarding_when_there_is_nothing_to_discard_changes_nothing(session):
    staff = _enrolled(session, "alice")
    enrolled_before = staff.mfa_enrolled_at
    names_before = [d.name for d in staff.totp_devices]

    assert discard_unconfirmed_devices(session, "alice") == []
    session.flush()

    staff = get_staff(session, "alice")
    assert [d.name for d in staff.totp_devices] == names_before
    assert staff.mfa_enrolled_at == enrolled_before


def test_an_abandoned_enrolment_no_longer_blocks_its_own_name(session):
    """Half of "does an abandoned enrolment interfere with starting a fresh
    one". It did: the row held its name against DuplicateDeviceNameError."""
    _with_unconfirmed(session, "alice", name="New phone")

    discard_unconfirmed_devices(session, "alice")
    session.flush()

    secret, _ = begin_mfa_enrolment(
        session, "alice", secret_key=SECRET_KEY,
        device_name="New phone", allow_additional=True,
    )
    session.flush()

    assert secret
    assert "New phone" in [d.name for d in get_staff(session, "alice").totp_devices]


def test_abandoned_enrolments_no_longer_consume_the_device_ceiling(session):
    """The other half. MAX_TOTP_DEVICES counts every row, so abandoned scans
    filled the account up and left it unable to add a real authenticator."""
    _enrolled(session, "alice")
    staff = get_staff(session, "alice")
    while len(staff.totp_devices) < MAX_TOTP_DEVICES:
        staff.totp_devices.append(
            StaffTotpDevice(
                name=f"Abandoned {len(staff.totp_devices)}",
                secret_enc=b"never-confirmed", enrolled_at=None,
                created_at=utcnow(),
            )
        )
    session.flush()

    with pytest.raises(TooManyDevicesError):
        begin_mfa_enrolment(
            session, "alice", secret_key=SECRET_KEY,
            device_name="Real phone", allow_additional=True,
        )

    discard_unconfirmed_devices(session, "alice")
    session.flush()

    secret, _ = begin_mfa_enrolment(
        session, "alice", secret_key=SECRET_KEY,
        device_name="Real phone", allow_additional=True,
    )
    assert secret


def test_beginning_an_enrolment_leaves_mfa_enrolled_at_alone(session):
    """_sync_mfa_enrolled_at is now called from begin_mfa_enrolment too, so
    that its claim to be the sole writer holds for every path that adds a
    device. Adding an unconfirmed row must not stamp the column."""
    staff = _enrolled(session, "alice")
    before = staff.mfa_enrolled_at

    begin_mfa_enrolment(
        session, "alice", secret_key=SECRET_KEY,
        device_name="Another phone", allow_additional=True,
    )
    session.flush()

    assert get_staff(session, "alice").mfa_enrolled_at == before


def test_an_unconfirmed_device_never_satisfies_a_login(session):
    """The property the whole unconfirmed state exists to preserve, restated
    against the reaper's fixture: verify_staff_totp gates on
    enrolled_totp_devices, never on the presence of a secret."""
    _enrolled(session, "bare")
    staff = get_staff(session, "bare")
    staff.totp_devices.clear()
    staff.mfa_enrolled_at = None
    session.flush()

    secret, _ = begin_mfa_enrolment(session, "bare", secret_key=SECRET_KEY)
    session.flush()

    code = pyotp.TOTP(secret).now()
    with pytest.raises(MfaNotEnrolledError):
        verify_staff_totp(session, "bare", code, secret_key=SECRET_KEY, now=int(time.time()))
