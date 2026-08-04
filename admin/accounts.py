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

from admin.models import Staff, StaffRole, utcnow
from admin.security import hash_password

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
