"""IP blocklist service. Contract §2.3's single, deliberate exception.

See ``db.blocklist_models`` for what is stored and why. This module derives
the HMAC key, computes fingerprints, and provides the three operations the
admin panel and the API need: block, unblock, and check.

``db/`` may not import ``admin/`` (see the layering note in CLAUDE.md), so
the HKDF key derivation below is a deliberate copy of
``admin/security.py::_derive_key`` rather than a shared import — same
algorithm, same length, same "no salt" reasoning, but this module's own
``info`` value so the two derivations can never collide.
"""

import hashlib
import hmac
from datetime import datetime, timedelta

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.blocklist_models import IpBlock, utcnow  # noqa: F401  - re-exported

#: HKDF ``info`` for the blocklist fingerprint key. Must match the value the
#: admin panel and the API both derive against, or a block made in one place
#: silently stops matching lookups made in the other.
BLOCKLIST_INFO = b"kaicalc-blocklist-v1"


def _derive_key(secret_key: str, info: bytes) -> bytes:
    """Derive a 32-byte, purpose-specific key from SECRET_KEY via HKDF-SHA256.

    A deliberate duplicate of ``admin/security.py::_derive_key``. ``db/`` may
    not import ``admin/`` — see the layering note in CLAUDE.md — so this is
    copied rather than shared. Change both together.

    No salt: SECRET_KEY is high-entropy random material, which is the case
    HKDF's salt is optional for.
    """
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=info,
    ).derive(secret_key.encode("utf-8"))


def ip_fingerprint(ip: str, *, secret_key: str) -> str:
    """Keyed HMAC-SHA256 of an address, hex-encoded (64 chars).

    Keyed, not plain hashed: a rainbow table over the whole IPv4 space is
    trivial to build, but an HMAC under a key the attacker does not have is
    not reversible.
    """
    key = _derive_key(secret_key, BLOCKLIST_INFO)
    return hmac.new(key, ip.encode("utf-8"), hashlib.sha256).hexdigest()


def block_ip(
    session: Session,
    ip: str,
    *,
    reason: str,
    actor: str,
    secret_key: str,
    minutes: int | None = None,
) -> IpBlock:
    """Block an address, upserting on its fingerprint.

    A second call for the same address updates the existing row (new reason,
    new expiry, new actor) rather than creating a duplicate — a repeat block
    is an extension or a change of reason, not a second row nobody will
    notice.

    Writes no audit entry: ``write_audit`` lives in ``admin/audit.py``,
    across the layering boundary this module may not cross. The caller
    writes its own audit entry after this returns.
    """
    fp = ip_fingerprint(ip, secret_key=secret_key)
    row = session.scalar(select(IpBlock).where(IpBlock.ip_hmac == fp))

    expires_at: datetime | None = None
    if minutes is not None:
        expires_at = utcnow() + timedelta(minutes=minutes)

    if row is None:
        row = IpBlock(
            ip_hmac=fp,
            reason=reason,
            created_by=actor,
            expires_at=expires_at,
        )
        session.add(row)
    else:
        row.reason = reason
        row.created_by = actor
        row.expires_at = expires_at

    return row


def unblock_ip(session: Session, ip: str, *, actor: str, secret_key: str) -> bool:
    """Remove a block, if one exists. Returns whether a row was removed.

    ``actor`` is accepted for symmetry with ``block_ip`` and because the
    caller's audit entry needs it, but nothing about the removal itself
    is keyed on it — this module still writes no audit entry.
    """
    fp = ip_fingerprint(ip, secret_key=secret_key)
    row = session.scalar(select(IpBlock).where(IpBlock.ip_hmac == fp))
    if row is None:
        return False
    session.delete(row)
    return True


def is_blocked(
    session: Session,
    ip: str,
    *,
    secret_key: str,
    now: datetime | None = None,
) -> bool:
    """Whether an address currently has an unexpired block.

    Runs on every public request once wired into the API, so it filters
    expired rows in SQL rather than fetching and checking in Python — this
    stays one round trip as the table grows, and it must not do anything
    else.
    """
    if now is None:
        now = utcnow()
    fp = ip_fingerprint(ip, secret_key=secret_key)
    stmt = select(IpBlock.id).where(
        IpBlock.ip_hmac == fp,
        (IpBlock.expires_at.is_(None)) | (IpBlock.expires_at > now),
    )
    return session.scalar(stmt) is not None
