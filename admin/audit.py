"""The single insertion point into audit_log. Contract §5.5.

Two callers and no others: AuditedModelView (§8.1) for admin CRUD, and the
factor-set lifecycle operations (§5.2) once E-6 exists.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from admin.models import AuditLog, utcnow

#: Contract §5.5. audit_log is readable by every staff member through
#: /admin/audit, so an unfiltered staff row would expose password hashes and
#: TOTP secrets to anyone holding an account.
#:
#: ``ip_hmac`` (``db/blocklist_models.py``'s ``IpBlock``) is here as defence in
#: depth, not because any current caller needs it. ``admin/blocklist_views.py``
#: hand-builds both of its audit payloads from four named columns precisely so
#: the fingerprint never reaches this table, and ``IpBlockAdmin`` sets
#: ``can_create``/``can_edit``/``can_delete`` all False so ``row_to_dict`` —
#: which serialises *every* mapped column — is never called for that model.
#: But that is three class flags and two hand-built dicts standing between a
#: 64-character fingerprint and a table every staff member can read: flip
#: ``can_delete`` to True for the convenience of sqladmin's bulk delete and
#: ``AuditedModelView``'s listener writes the fingerprint into ``audit_log``,
#: widening its audience from administrators only (the floor
#: ``IpBlockAdmin.is_accessible`` sets) to all staff. One line here removes
#: that whole class of accident. §2.3's reasoning for not displaying the
#: fingerprint anywhere — it invites someone to try to reverse it, and with
#: SECRET_KEY in hand a specific address can be confirmed — applies to
#: /admin/audit exactly as it does to the blocklist screen.
REDACTED_FIELDS = {"password_hash", "mfa_secret_enc", "code_hash", "ip_hmac"}

_REDACTED = "[redacted]"


def row_to_dict(row: Any) -> dict:
    """Every mapped column of one row, by name.

    Relationships are excluded — a relationship is other rows, not a column
    of this one. Shared by admin/modelviews.py (building an `after` snapshot
    for ordinary CRUD) and admin/factor_lifecycle.py (building one for a
    clone's own audit entry): both need the same "every column, nothing
    derived" shape, so this lives in admin/audit.py, which both already
    depend on for `write_audit`, rather than being defined twice.
    """
    return {
        column.key: getattr(row, column.key)
        for column in row.__mapper__.column_attrs
    }


def _encode(value: Any) -> Any:
    """JSON-safe form of one value.

    Decimal becomes a string, never a float (contract §1.2): a float cannot
    represent 1.9000000000 exactly, and an audit trail that rounds is not an
    audit trail. bytes has no JSON form at all — every bytes column in the
    project is in REDACTED_FIELDS, so this is the belt to that braces.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return value


def _scrub(payload: dict | None) -> dict | None:
    if payload is None:
        return None
    return {
        key: _REDACTED if key in REDACTED_FIELDS else _encode(value)
        for key, value in payload.items()
    }


def write_audit(
    session: Session,
    actor: str,
    action: str,
    table_name: str,
    row_id: int | None,
    before: dict | None,
    after: dict | None,
) -> None:
    """Record one change. Never commits — the caller owns the transaction.

    A change that is rolled back must leave no audit record claiming it
    happened, which is only true while this shares the caller's transaction.
    """
    entry = AuditLog(
        at=utcnow(),
        actor=actor,
        action=action,
        table_name=table_name,
        row_id=row_id,
        before_json=_scrub(before),
        after_json=_scrub(after),
    )
    session.add(entry)
