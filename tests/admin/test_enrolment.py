"""admin.accounts - MFA enrolment, TOTP verification, recovery codes.

Contract: docs/interfaces.md 8.3.
"""

import pyotp
import pytest

from admin.accounts import (
    MAX_TOTP_DEVICES,
    RECOVERY_CODE_COUNT,
    DuplicateDeviceNameError,
    LastAuthenticatorError,
    MfaAlreadyEnrolledError,
    MfaNotEnrolledError,
    TooManyDevicesError,
    UnknownDeviceError,
    begin_mfa_enrolment,
    complete_mfa_enrolment,
    consume_recovery_code,
    create_staff,
    get_staff,
    remove_totp_device,
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
    create_staff(session, username=username, display_name="Alice Example", actor="test")
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
    create_staff(session, username="alice", display_name="Alice Example", actor="test")
    session.flush()

    secret, uri = begin_mfa_enrolment(session, "alice", secret_key=SECRET_KEY)

    assert len(secret) == 32
    assert uri.startswith("otpauth://totp/")


def test_begin_enrolment_stores_the_secret_but_does_not_mark_it_enrolled(session):
    """The secret is persisted so it never has to travel back through the
    browser between the two requests. Enrolment still only counts once a
    correct code has been produced, which is what proves the authenticator
    actually holds it."""
    create_staff(session, username="alice", display_name="Alice Example", actor="test")
    session.flush()

    begin_mfa_enrolment(session, "alice", secret_key=SECRET_KEY)
    session.flush()

    staff = get_staff(session, "alice")
    # One device row, holding the secret, and not yet confirmed. Asserting
    # the row exists *and* that enrolled_at is still NULL is what separates
    # "the secret was persisted" from "the account is now enrolled" - the
    # two states this function deliberately keeps apart.
    assert len(staff.totp_devices) == 1
    assert staff.totp_devices[0].secret_enc is not None
    assert staff.totp_devices[0].enrolled_at is None
    assert staff.mfa_enrolled is False


def test_completing_an_enrolment_that_was_never_begun_is_refused(session):
    create_staff(session, username="alice", display_name="Alice Example", actor="test")
    session.flush()

    with pytest.raises(MfaNotEnrolledError):
        complete_mfa_enrolment(
            session, "alice", "123456", secret_key=SECRET_KEY, now=NOW
        )


def test_beginning_enrolment_again_replaces_an_unfinished_one(session):
    """Someone abandons the enrolment page and starts over. The second secret
    is the one that must work."""
    create_staff(session, username="alice", display_name="Alice Example", actor="test")
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


def test_beginning_enrolment_again_on_an_enrolled_account_is_refused(session):
    """The account takeover this guard exists to stop.

    Contract 8.3 puts the enrolment page behind the password step only — it
    has to be reachable by someone who has no second factor yet. If beginning
    an enrolment could overwrite a finished one, an attacker holding just the
    password would scan their own QR code and walk away with both factors,
    needing nothing from the real owner. Re-enrolment is an administrator
    action and goes through reset_mfa (layer L2) or the CLI (layer L3).
    """
    enrolled_account(session)

    with pytest.raises(MfaAlreadyEnrolledError):
        begin_mfa_enrolment(session, "alice", secret_key=SECRET_KEY)


def test_a_refused_re_enrolment_leaves_the_existing_one_intact(session):
    """Refusing is not enough on its own: the call must also not have written
    a new secret or cleared mfa_enrolled_at on its way out, or the account is
    de-enrolled and locked out even though the attempt "failed"."""
    secret = enrolled_account(session)
    before = get_staff(session, "alice").totp_devices[0].secret_enc

    with pytest.raises(MfaAlreadyEnrolledError):
        begin_mfa_enrolment(session, "alice", secret_key=SECRET_KEY)
    session.flush()

    staff = get_staff(session, "alice")
    assert staff.mfa_enrolled is True
    # No *second* device either. A refusal that appended an unconfirmed row
    # before raising would leave the account one `allow_additional=True`
    # caller away from an authenticator it never agreed to.
    assert len(staff.totp_devices) == 1
    assert staff.totp_devices[0].secret_enc == before
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


def test_completing_enrolment_with_a_correct_code_enrols_the_account(session):
    enrolled_account(session)

    assert get_staff(session, "alice").mfa_enrolled is True


def test_completing_enrolment_with_a_wrong_code_is_refused(session):
    create_staff(session, username="alice", display_name="Alice Example", actor="test")
    session.flush()
    secret, _ = begin_mfa_enrolment(session, "alice", secret_key=SECRET_KEY)

    with pytest.raises(MfaNotEnrolledError):
        complete_mfa_enrolment(
            session, "alice", "000000", secret_key=SECRET_KEY, now=NOW
        )


def test_completing_enrolment_issues_the_agreed_number_of_recovery_codes(session):
    create_staff(session, username="alice", display_name="Alice Example", actor="test")
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

    assert get_staff(session, "alice").totp_devices[0].secret_enc != secret.encode()


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
    create_staff(session, username="bob", display_name="Bob", actor="test")
    session.flush()

    with pytest.raises(MfaNotEnrolledError):
        verify_staff_totp(session, "bob", "123456", secret_key=SECRET_KEY, now=NOW)


def test_a_begun_but_unfinished_enrolment_does_not_satisfy_the_login_factor(session):
    """A stored secret is not an enrolment.

    Someone opens the enrolment page and never finishes: the row now holds a
    usable secret while mfa_enrolled_at is still NULL. Contract 8.3 grants
    access only once enrolment has completed, so the login second factor has
    to gate on mfa_enrolled_at and not on the presence of a secret.
    """
    create_staff(session, username="bob", display_name="Bob", actor="test")
    session.flush()
    secret, _ = begin_mfa_enrolment(session, "bob", secret_key=SECRET_KEY)
    session.flush()

    with pytest.raises(MfaNotEnrolledError):
        verify_staff_totp(
            session,
            "bob",
            code_for(secret, NOW // TOTP_INTERVAL),
            secret_key=SECRET_KEY,
            now=NOW,
        )


def test_a_recovery_code_works_once(session):
    create_staff(session, username="alice", display_name="Alice Example", actor="test")
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
    create_staff(session, username="alice", display_name="Alice Example", actor="test")
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

    reset_mfa(session, "alice", actor="admin")
    session.flush()

    staff = get_staff(session, "alice")
    assert staff.mfa_enrolled is False
    # Every device, not merely the enrolment flag. A reset that cleared
    # mfa_enrolled_at and left a device row behind would leave whoever the
    # reset was aimed at holding a secret that the next `allow_additional`
    # enrolment path could still confirm.
    assert staff.totp_devices == []
    assert unused_recovery_code_count(session, "alice") == 0


def test_a_reset_account_can_enrol_again(session):
    enrolled_account(session)
    reset_mfa(session, "alice", actor="admin")
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

    reset_mfa(session, "alice", actor="admin")
    session.flush()

    assert staff.recovery_codes == []
    assert unused_recovery_code_count(session, "alice") == 0


# --- multi-device service surface (contract v1.13) --------------------------


def add_device(session, username: str, name: str, *, at: int = NOW) -> str:
    """Enrol one additional, confirmed device. Returns its secret."""
    secret, _ = begin_mfa_enrolment(
        session, username, secret_key=SECRET_KEY,
        device_name=name, allow_additional=True,
    )
    complete_mfa_enrolment(
        session, username, code_for(secret, at // TOTP_INTERVAL),
        secret_key=SECRET_KEY, now=at, device_name=name,
    )
    session.flush()
    return secret


def test_removing_a_device_evicts_sessions_from_the_service_layer(session):
    """The eviction belongs to admin/accounts.py, not to whichever caller
    happens to be removing the device.

    This module's own docstring makes admin/accounts.py the only module that
    mutates ``staff``, and every other credential change here
    (set_password, issue_password, deactivate_staff, reset_mfa) bumps
    ``session_generation`` itself for exactly that reason. Written for the
    caller that does not exist yet: a ``kaicalc remove-device`` would
    otherwise remove the row and leave every session live - on precisely the
    path where the reason for removing a device is that the phone is in
    somebody else's hands. Driven through the service function directly, so
    an eviction that moved back into the view fails here with the HTTP tests
    still green.
    """
    enrolled_account(session)
    add_device(session, "alice", "Backup phone", at=NOW + 10 * TOTP_INTERVAL)
    before = get_staff(session, "alice").session_generation
    device_id = get_staff(session, "alice").totp_devices[0].id

    remove_totp_device(session, "alice", device_id)
    session.flush()

    assert get_staff(session, "alice").session_generation == before + 1


def test_removing_an_unconfirmed_device_evicts_too(session):
    """The invariant is worth more than the saved generation. A caller must
    not have to work out which case it is in for the eviction to hold."""
    enrolled_account(session)
    begin_mfa_enrolment(
        session, "alice", secret_key=SECRET_KEY,
        device_name="Abandoned scan", allow_additional=True,
    )
    session.flush()
    before = get_staff(session, "alice").session_generation
    device_id = next(
        d.id for d in get_staff(session, "alice").totp_devices
        if d.name == "Abandoned scan"
    )

    remove_totp_device(session, "alice", device_id)
    session.flush()

    assert get_staff(session, "alice").session_generation == before + 1


def test_an_account_may_not_exceed_the_device_ceiling(session):
    """MAX_TOTP_DEVICES is a rule, not a number, and this is what makes it
    one. Without a test, raising or deleting the limit changes nothing that
    anything checks."""
    enrolled_account(session)
    for i in range(MAX_TOTP_DEVICES - 1):
        add_device(session, "alice", f"Device {i}",
                   at=NOW + (i + 2) * 10 * TOTP_INTERVAL)
    assert len(get_staff(session, "alice").totp_devices) == MAX_TOTP_DEVICES

    with pytest.raises(TooManyDevicesError):
        begin_mfa_enrolment(
            session, "alice", secret_key=SECRET_KEY,
            device_name="One too many", allow_additional=True,
        )


def test_the_ceiling_does_not_raise_the_last_authenticator_error(session):
    """The two are opposite conditions - too many second factors against too
    few - and admin/self_service_view.py catches LastAuthenticatorError to
    render "this is your only authenticator". Sharing the class showed
    somebody at the ceiling a message saying their account had no second
    factor left, which is the reverse of the truth."""
    enrolled_account(session)
    for i in range(MAX_TOTP_DEVICES - 1):
        add_device(session, "alice", f"Device {i}",
                   at=NOW + (i + 2) * 10 * TOTP_INTERVAL)

    with pytest.raises(TooManyDevicesError) as caught:
        begin_mfa_enrolment(
            session, "alice", secret_key=SECRET_KEY,
            device_name="One too many", allow_additional=True,
        )

    assert not isinstance(caught.value, LastAuthenticatorError)
    assert "maximum" in str(caught.value)


def test_a_refused_ceiling_leaves_the_existing_devices_untouched(session):
    """A refusal that appended the row before raising would put the account
    one commit away from holding a device it never confirmed."""
    enrolled_account(session)
    for i in range(MAX_TOTP_DEVICES - 1):
        add_device(session, "alice", f"Device {i}",
                   at=NOW + (i + 2) * 10 * TOTP_INTERVAL)
    before = [d.name for d in get_staff(session, "alice").totp_devices]

    with pytest.raises(TooManyDevicesError):
        begin_mfa_enrolment(
            session, "alice", secret_key=SECRET_KEY,
            device_name="One too many", allow_additional=True,
        )
    session.flush()

    assert [d.name for d in get_staff(session, "alice").totp_devices] == before


def test_a_device_id_from_another_account_is_not_found(session):
    """The scope is a property of this function, not of its callers - which
    is what stops a second caller added later from having to remember."""
    enrolled_account(session, "alice")
    enrolled_account(session, "bob")
    add_device(session, "bob", "Bob backup", at=NOW + 10 * TOTP_INTERVAL)
    bob_device = get_staff(session, "bob").totp_devices[0].id

    with pytest.raises(UnknownDeviceError):
        remove_totp_device(session, "alice", bob_device)
    session.flush()

    assert len(get_staff(session, "bob").totp_devices) == 2


def test_a_confirmed_device_name_cannot_be_reused(session):
    """Overwriting a confirmed device's secret would replace a working phone
    with one nobody has scanned, leaving the account one removal from having
    no usable factor."""
    enrolled_account(session)
    add_device(session, "alice", "Backup phone", at=NOW + 10 * TOTP_INTERVAL)
    before = get_staff(session, "alice").totp_devices[1].secret_enc

    with pytest.raises(DuplicateDeviceNameError):
        begin_mfa_enrolment(
            session, "alice", secret_key=SECRET_KEY,
            device_name="Backup phone", allow_additional=True,
        )
    session.flush()

    assert get_staff(session, "alice").totp_devices[1].secret_enc == before


def test_the_default_devices_label_is_the_bare_username(session):
    """Task 1 put the system in the issuer and the account in the label, and
    an account with one authenticator must go on reading exactly that.

    Pinned because `_device_label` is now on the onboarding path too:
    admin/views.py used to build this URI from a bare `staff.username` while
    `begin_mfa_enrolment` built it from `_device_label(..., DEFAULT)`. The two
    agreed by coincidence, and consolidating them is only safe if something
    holds the consolidated value still. Without this test, making
    `_device_label` unconditional - so the first device becomes
    `alice (Authenticator)` - changes what every new enrolment shows on the
    phone with the whole suite green.
    """
    create_staff(session, username="alice", display_name="Alice Example", actor="test")
    session.flush()

    _secret, uri = begin_mfa_enrolment(session, "alice", secret_key=SECRET_KEY)

    assert "/Kai%20Commitment%20Admin:alice?" in uri
    assert "Authenticator" not in uri


def test_an_additional_devices_label_carries_its_own_name(session):
    """The other half: two devices on one account share an issuer and a
    username, so without the name the app shows two identical entries and the
    person deleting the lost phone is guessing which."""
    from urllib.parse import unquote

    enrolled_account(session)

    _secret, uri = begin_mfa_enrolment(
        session, "alice", secret_key=SECRET_KEY,
        device_name="Backup phone", allow_additional=True,
    )

    label = unquote(uri.split("?")[0])
    assert "alice" in label
    assert "Backup phone" in label
    #: The name goes in the ACCOUNT half, never the issuer - authenticators
    #: group by issuer, so a name there would split one account's two devices
    #: into two groups, which is the problem Task 1 solved one level down.
    assert "issuer=Kai%20Commitment%20Admin" in uri
    assert label.endswith("Backup phone")

