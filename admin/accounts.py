"""Account lifecycle service.

Contract: docs/interfaces.md 8.3. The only module that mutates ``staff`` rows,
so that the invariants below hold no matter which entry point is used.

The functions here flush but never commit: the caller owns the transaction.
That is what lets the admin panel write an audit_log entry in the same
transaction as the change it describes.
"""

import secrets
import string

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from admin.models import Staff, StaffRecoveryCode, StaffRole, utcnow
from admin.security import (
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_recovery_codes,
    hash_password,
    hash_recovery_code,
    verify_recovery_code,
)
from admin.totp import generate_totp_secret, provisioning_uri, verify_totp

#: Contract 8.3. With no email system, administrators are each other's
#: recovery path, so the system refuses to fall below two.
MIN_ACTIVE_ADMINS = 2

#: Long enough that it is never typed from memory, and drawn from a set with
#: no shell-hostile characters, since it gets read out or pasted.
_INITIAL_PASSWORD_ALPHABET = string.ascii_letters + string.digits
_INITIAL_PASSWORD_LENGTH = 20


class LastAdministratorsError(RuntimeError):
    """The change would leave fewer than MIN_ACTIVE_ADMINS administrators."""


class UnknownStaffError(RuntimeError):
    """No account with that username."""


def generate_initial_password() -> str:
    """Generate a one-time initial password.

    ``secrets``, never ``random``: the latter is a Mersenne Twister seeded
    predictably enough that its output is recoverable.
    """
    return "".join(
        secrets.choice(_INITIAL_PASSWORD_ALPHABET)
        for _ in range(_INITIAL_PASSWORD_LENGTH)
    )


def _normalise_username(username: str) -> str:
    return username.strip().casefold()


def get_staff(session: Session, username: str) -> Staff:
    """Fetch an account by username, or raise UnknownStaffError."""
    staff = session.scalar(
        select(Staff).where(Staff.username == _normalise_username(username))
    )
    if staff is None:
        raise UnknownStaffError(f"No account named {username!r}")
    return staff


def count_active_admins(session: Session) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(Staff)
            .where(Staff.role == StaffRole.admin, Staff.is_active.is_(True))
        )
        or 0
    )


def _guard_admin_floor(session: Session, staff: Staff) -> None:
    """Refuse a change that removes an active administrator when at the floor."""
    if staff.role is not StaffRole.admin or not staff.is_active:
        return
    if count_active_admins(session) <= MIN_ACTIVE_ADMINS:
        raise LastAdministratorsError(
            f"At least {MIN_ACTIVE_ADMINS} active administrator accounts must "
            "exist. Promote or create another administrator first."
        )


def create_staff(
    session: Session,
    *,
    username: str,
    display_name: str,
    role: StaffRole = StaffRole.staff,
    actor: str | None = None,
) -> tuple[Staff, str]:
    """Create an account and return it with its one-time initial password.

    The plaintext password is returned rather than stored: it is shown once
    and handed over out of band. Contract 8.3 forbids self-service
    registration, so this is the only way an account comes into existence.
    """
    password = generate_initial_password()
    staff = Staff(
        username=_normalise_username(username),
        display_name=display_name,
        password_hash=hash_password(password),
        role=role,
        is_active=True,
        must_change_password=True,
        created_at=utcnow(),
        created_by=actor,
    )
    session.add(staff)
    return staff, password


def set_password(session: Session, username: str, new_password: str) -> None:
    """Set a password and clear the forced-change flag."""
    staff = get_staff(session, username)
    staff.password_hash = hash_password(new_password)
    staff.must_change_password = False


def deactivate_staff(session: Session, username: str) -> None:
    staff = get_staff(session, username)
    _guard_admin_floor(session, staff)
    staff.is_active = False


def set_role(session: Session, username: str, role: StaffRole) -> None:
    staff = get_staff(session, username)
    if staff.role is StaffRole.admin and role is not StaffRole.admin:
        _guard_admin_floor(session, staff)
    staff.role = role


# --- MFA enrolment ----------------------------------------------------------

#: Contract 8.3. The panel prompts for regeneration once 2 remain.
RECOVERY_CODE_COUNT = 5


class MfaNotEnrolledError(RuntimeError):
    """The account has no usable TOTP enrolment, or the code offered to
    complete enrolment was wrong."""


class MfaAlreadyEnrolledError(RuntimeError):
    """The account has a finished enrolment, so a new one cannot be begun.

    A sibling of MfaNotEnrolledError rather than a reuse of it: the two say
    opposite things about the account, and a login page that catches the wrong
    one would either leak that an enrolment exists or silently swallow the
    refusal that stops an account being taken over.
    """


def begin_mfa_enrolment(
    session: Session, username: str, *, secret_key: str
) -> tuple[str, str]:
    """Start enrolment: store a fresh encrypted secret, return it and the URI.

    The secret is persisted here, already encrypted, but ``mfa_enrolled_at``
    stays NULL — so ``mfa_enrolled`` is still False and ``require_staff``
    still refuses the account. Enrolment completes only once the user produces
    a correct code, which is the only evidence the authenticator really holds
    the secret; without that step a mis-scanned QR locks someone out on their
    next login.

    Persisting it now, rather than handing it back for the caller to carry
    between the two requests, keeps the plaintext secret out of the browser
    entirely — off the form, out of the history, out of any request log.

    Calling this again replaces an unfinished enrolment, which is what should
    happen when someone abandons the page and starts over. A **finished**
    enrolment is refused: contract 8.3 puts this page behind the password step
    alone, since a user with no second factor has to be able to reach it, so
    an attacker holding only the password would otherwise scan their own QR
    code, complete the enrolment and hold both factors without ever needing
    the real owner's authenticator. Re-enrolment is an administrator action
    and goes through reset_mfa (layer L2) or the CLI (layer L3), both of which
    clear the enrolment first.
    """
    staff = get_staff(session, username)
    if staff.mfa_enrolled_at is not None:
        raise MfaAlreadyEnrolledError(
            f"{username!r} has already enrolled an authenticator. An "
            "administrator must reset it before a new one can be enrolled."
        )
    secret = generate_totp_secret()
    staff.mfa_secret_enc = encrypt_totp_secret(secret, secret_key=secret_key)
    staff.mfa_enrolled_at = None
    staff.mfa_last_counter = None
    return secret, provisioning_uri(secret, username=staff.username)


def complete_mfa_enrolment(
    session: Session,
    username: str,
    code: str,
    *,
    secret_key: str,
    now: int,
) -> list[str]:
    """Finish enrolment and return the one-time recovery codes.

    Reads the secret stored by ``begin_mfa_enrolment``. Raises
    MfaNotEnrolledError if enrolment was never begun, or if the code does not
    verify. Recovery codes are returned in plaintext because this is the only
    moment they exist in readable form; only their hashes are stored.
    """
    staff = get_staff(session, username)
    if staff.mfa_secret_enc is None:
        raise MfaNotEnrolledError(
            "Enrolment has not been started for this account."
        )

    secret = decrypt_totp_secret(staff.mfa_secret_enc, secret_key=secret_key)
    counter = verify_totp(secret, code, now=now)
    if counter is None:
        raise MfaNotEnrolledError(
            "That code did not match. Check the authenticator has the right "
            "account and that the device clock is correct."
        )

    staff.mfa_enrolled_at = utcnow()
    staff.mfa_last_counter = counter

    codes = generate_recovery_codes(RECOVERY_CODE_COUNT)
    for code_value in codes:
        session.add(
            StaffRecoveryCode(
                staff_id=staff.id,
                code_hash=hash_recovery_code(code_value),
                created_at=utcnow(),
            )
        )
    return codes


def verify_staff_totp(
    session: Session, username: str, code: str, *, secret_key: str, now: int
) -> bool:
    """Verify a login TOTP and advance the replay counter.

    Raises MfaNotEnrolledError when the account has not enrolled: contract 8.3
    requires that an unenrolled account cannot reach anything, so treating it
    as a plain failed code would hide a state that has to be handled.

    The gate is ``mfa_enrolled``, not the presence of a secret. An enrolment
    that was begun and abandoned leaves a perfectly usable secret in the row
    while ``mfa_enrolled_at`` is still NULL; accepting it here would let a
    half-finished enrolment satisfy the login second factor. Completing an
    enrolment is the separate job of complete_mfa_enrolment, which reads the
    secret directly for exactly that reason.
    """
    staff = get_staff(session, username)
    if not staff.mfa_enrolled or staff.mfa_secret_enc is None:
        raise MfaNotEnrolledError(f"{username!r} has not enrolled an authenticator")

    secret = decrypt_totp_secret(staff.mfa_secret_enc, secret_key=secret_key)
    counter = verify_totp(secret, code, now=now, last_counter=staff.mfa_last_counter)
    if counter is None:
        return False

    staff.mfa_last_counter = counter
    return True


def unused_recovery_code_count(session: Session, username: str) -> int:
    staff = get_staff(session, username)
    return int(
        session.scalar(
            select(func.count())
            .select_from(StaffRecoveryCode)
            .where(
                StaffRecoveryCode.staff_id == staff.id,
                StaffRecoveryCode.used_at.is_(None),
            )
        )
        or 0
    )


def consume_recovery_code(session: Session, username: str, code: str) -> bool:
    """Spend one recovery code. Returns False if it is unknown or already used."""
    staff = get_staff(session, username)
    candidates = session.scalars(
        select(StaffRecoveryCode).where(
            StaffRecoveryCode.staff_id == staff.id,
            StaffRecoveryCode.used_at.is_(None),
        )
    ).all()
    for candidate in candidates:
        if verify_recovery_code(code, candidate.code_hash):
            candidate.used_at = utcnow()
            return True
    return False


def reset_mfa(session: Session, username: str) -> None:
    """Clear enrolment and every recovery code.

    Contract 8.3 recovery layers L2 (another administrator) and L3 (the
    server-side CLI) both land here. The account is forced back through
    enrolment on its next login.
    """
    staff = get_staff(session, username)
    staff.mfa_secret_enc = None
    staff.mfa_enrolled_at = None
    staff.mfa_last_counter = None
    # Clear through the relationship rather than a bulk delete. Staff declares
    # cascade="all, delete-orphan", so this removes the rows AND keeps the
    # session's view of them correct. A bulk delete with
    # synchronize_session=False empties the table but leaves an already-loaded
    # staff.recovery_codes reporting the deleted rows — and sqladmin renders
    # relationships directly, so an administrator would see recovery codes
    # that no longer exist for an account they had just reset.
    staff.recovery_codes.clear()
