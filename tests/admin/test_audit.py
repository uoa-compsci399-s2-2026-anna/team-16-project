"""Contract §5.5. write_audit is the only code that inserts into audit_log."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from admin.audit import write_audit
from admin.models import AuditLog

pytestmark = pytest.mark.db


def test_a_redacted_field_never_reaches_the_row(session):
    """audit_log is readable by every staff member (contract §2.3 note).

    An unfiltered staff row would hand out password hashes and TOTP secrets
    to anyone holding an account.
    """
    write_audit(
        session, actor="admin", action="update", table_name="staff", row_id=1,
        before={"username": "kim", "password_hash": "$2b$12$realhash"},
        after={"username": "kim", "password_hash": "$2b$12$newhash"},
    )
    session.flush()

    row = session.scalar(select(AuditLog))
    assert row.before_json["password_hash"] == "[redacted]"
    assert row.after_json["password_hash"] == "[redacted]"
    assert row.before_json["username"] == "kim"  # non-sensitive keys survive


def test_every_redacted_field_is_covered(session):
    """All three of contract §5.5's names, not just the one a test remembered."""
    payload = {
        "password_hash": "h", "mfa_secret_enc": b"secret", "code_hash": "c",
        "display_name": "Kim",
    }
    write_audit(
        session, actor="admin", action="update", table_name="staff", row_id=1,
        before=payload, after=None,
    )
    session.flush()

    row = session.scalar(select(AuditLog))
    assert row.before_json == {
        "password_hash": "[redacted]", "mfa_secret_enc": "[redacted]",
        "code_hash": "[redacted]", "display_name": "Kim",
    }


def test_a_decimal_is_stored_as_a_string(session):
    """Contract §1.2: decimals serialise as strings, never as float.

    json.dumps(Decimal(...)) raises TypeError, so an implementation with no
    encoder crashes here rather than quietly storing a float — but one that
    reached for `default=float` would pass a laxer test than this.
    """
    write_audit(
        session, actor="admin", action="update", table_name="factor_upstream",
        row_id=7, before={"value_per_kg": Decimal("1.9000000000")}, after=None,
    )
    session.flush()

    row = session.scalar(select(AuditLog))
    assert row.before_json["value_per_kg"] == "1.9000000000"
    assert isinstance(row.before_json["value_per_kg"], str)


def test_a_rolled_back_change_leaves_no_audit_record(session):
    """Contract §5.5: runs inside the caller's transaction, never commits.

    An implementation that called session.commit() to "make sure the audit
    lands" would leave a record claiming a change happened that did not.
    """
    write_audit(
        session, actor="admin", action="delete", table_name="staff", row_id=1,
        before={"username": "kim"}, after=None,
    )
    session.rollback()

    assert session.scalar(select(AuditLog)) is None
