"""admin.models - staff tables against a real MySQL.

Contract: docs/interfaces.md 2.4.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, inspect, select
from sqlalchemy.exc import IntegrityError

from admin.models import Staff, StaffRecoveryCode, StaffRole, utcnow

pytestmark = pytest.mark.db


def make_staff(**overrides) -> Staff:
    values = {
        "username": "alice",
        "display_name": "Alice Example",
        "password_hash": "$2b$12$abcdefghijklmnopqrstuv",
        "role": StaffRole.staff,
    }
    values.update(overrides)
    return Staff(**values)


def test_utcnow_is_naive_and_close_to_now():
    stamp = utcnow()

    assert stamp.tzinfo is None
    reference = datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs((reference - stamp).total_seconds()) < 5


def test_a_staff_row_round_trips(session):
    session.add(make_staff())
    session.flush()

    stored = session.scalar(select(Staff).where(Staff.username == "alice"))
    assert stored.display_name == "Alice Example"
    assert stored.role is StaffRole.staff


def test_new_accounts_default_to_active_and_must_change_password(session):
    session.add(make_staff())
    session.flush()

    stored = session.scalar(select(Staff).where(Staff.username == "alice"))
    assert stored.is_active is True
    assert stored.must_change_password is True


def test_new_accounts_start_unenrolled_in_mfa(session):
    session.add(make_staff())
    session.flush()

    stored = session.scalar(select(Staff).where(Staff.username == "alice"))
    assert stored.mfa_secret_enc is None
    assert stored.mfa_enrolled_at is None
    assert stored.mfa_enrolled is False


def test_usernames_are_unique(session):
    session.add(make_staff())
    session.flush()
    session.add(make_staff(display_name="Someone Else"))

    with pytest.raises(IntegrityError):
        session.flush()


def test_the_encrypted_secret_column_stores_raw_bytes(session):
    """Fernet output is bytes. A column that coerced it to text would corrupt
    it, so this asserts the round trip explicitly."""
    blob = bytes(range(256))[:200]
    session.add(make_staff(mfa_secret_enc=blob))
    session.flush()
    session.expunge_all()

    stored = session.scalar(select(Staff).where(Staff.username == "alice"))
    assert stored.mfa_secret_enc == blob


def test_the_secret_column_is_varbinary_on_mysql(engine):
    """Contract 2.4 specifies VARBINARY(255), not BLOB."""
    columns = {c["name"]: c for c in inspect(engine).get_columns("staff")}

    assert "VARBINARY" in str(columns["mfa_secret_enc"]["type"]).upper()


def test_recovery_codes_are_linked_to_their_account(session):
    staff = make_staff()
    staff.recovery_codes = [
        StaffRecoveryCode(code_hash="a" * 64),
        StaffRecoveryCode(code_hash="b" * 64),
    ]
    session.add(staff)
    session.flush()

    stored = session.scalar(select(Staff).where(Staff.username == "alice"))
    assert len(stored.recovery_codes) == 2


def test_deleting_an_account_deletes_its_recovery_codes(session):
    staff = make_staff()
    staff.recovery_codes = [StaffRecoveryCode(code_hash="a" * 64)]
    session.add(staff)
    session.flush()

    session.delete(staff)
    session.flush()

    assert session.scalars(select(StaffRecoveryCode)).all() == []


def test_the_database_cascade_deletes_recovery_codes_independently_of_the_orm(session):
    """Pins the schema's ON DELETE CASCADE rather than the ORM's
    cascade="all, delete-orphan". The ORM cascade keeps the sibling test
    passing even if the foreign key option were removed, and a leftover
    recovery-code hash for a deleted account is a residual credential."""
    staff = make_staff()
    staff.recovery_codes = [StaffRecoveryCode(code_hash="a" * 64)]
    session.add(staff)
    session.flush()
    staff_id = staff.id
    session.expunge_all()

    session.execute(delete(Staff).where(Staff.id == staff_id))
    session.flush()

    remaining = session.scalars(
        select(StaffRecoveryCode).where(StaffRecoveryCode.staff_id == staff_id)
    ).all()
    assert remaining == []


def test_the_recovery_code_hash_column_is_char_not_varchar(engine):
    """Contract 2.4 specifies CHAR(64). A SHA-256 hex digest is always
    exactly 64 characters, so a variable-length column stores a length
    prefix for a length that never varies."""
    columns = {c["name"]: c for c in inspect(engine).get_columns("staff_recovery_code")}

    assert "CHAR" in str(columns["code_hash"]["type"]).upper()
    assert "VARCHAR" not in str(columns["code_hash"]["type"]).upper()


def test_role_is_stored_as_a_database_enum(engine):
    """So that an invalid role cannot be written by anything that bypasses the
    ORM, including a hand-run SQL statement."""
    columns = {c["name"]: c for c in inspect(engine).get_columns("staff")}

    assert "ENUM" in str(columns["role"]["type"]).upper()
