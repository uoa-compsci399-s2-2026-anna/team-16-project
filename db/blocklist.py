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
import ipaddress
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


class InvalidAddressError(ValueError):
    """The value handed to ``normalise_ip``/``ip_fingerprint`` is not an
    address.

    A ``ValueError`` subclass so that ``except ValueError`` still catches it
    (``ipaddress.ip_address`` raises a plain ``ValueError``, which is what
    every existing caller would have been written against), but nameable, so
    a caller that wants to distinguish "the operator mistyped an address"
    from any other ``ValueError`` in the same block can - the admin form and
    the CLI both do.
    """


def normalise_ip(ip: str) -> str:
    """The one canonical spelling of an address.

    **Why this is not optional, and why it lives under ``ip_fingerprint``
    rather than at each call site.** The blocklist matches on
    ``HMAC(address)``, so two spellings of the same address produce two
    different fingerprints and a block made under one never matches a caller
    arriving as the other. Nothing fails, nothing logs, and the screen shows
    a block that stops nobody - the same silent-failure class as the two
    sides of the HKDF derivation disagreeing on ``BLOCKLIST_INFO``, which is
    why that value is named in the contract.

    The spellings that collide in practice:

    * ``" 203.0.113.9"`` - a copied value with surrounding whitespace.
    * ``"203.0.113.09"`` - a zero-padded octet. Rejected outright rather than
      normalised: ``ipaddress`` refuses it (it is ambiguous - historically
      some resolvers read a leading zero as octal), so an operator who types
      it gets told, rather than getting a block on a fourth address.
    * ``"2001:db8:0:0:0:0:0:1"`` vs ``"2001:db8::1"`` vs ``"2001:DB8::1"`` -
      three spellings of one IPv6 address. ``compressed`` is the form
      ``ipaddress`` itself considers canonical, and lower-cases the hex.
    * ``"203.0.113.9:54321"`` / ``"[2001:db8::1]:443"`` - a value carrying a
      port. Rejected: the port is not part of the address, and silently
      keeping it would fingerprint a value no caller ever arrives as.
    * ``"203.0.113.0/24"`` - a CIDR range. Rejected: the blocklist holds
      single addresses (a hash cannot be range-matched), so accepting the
      syntax would promise something the table cannot do.

    Raises ``InvalidAddressError`` on anything else. **Raising rather than
    fingerprinting the raw string is the deliberate choice**: returning a
    fingerprint of unparseable input succeeds, writes a row, and produces
    exactly the silently-dead block this function exists to prevent. A caller
    on a request path that must not fail (``admin/protection.py``'s
    middleware) normalises the connection address itself and skips the
    lookup when it cannot - see ``_client_ip`` there; a caller taking input
    from a human (the admin form, ``python -m admin.cli unblock``) reports
    the error to them.
    """
    try:
        return ipaddress.ip_address(ip.strip()).compressed
    except ValueError as exc:
        raise InvalidAddressError(
            "Not a single IP address. Enter one IPv4 or IPv6 address with no "
            "port, no prefix length and no leading zeroes - for example "
            "203.0.113.9 or 2001:db8::1."
        ) from exc


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

    Normalises through ``normalise_ip`` first, and raises
    ``InvalidAddressError`` when that fails. Normalising **here** rather than
    in each caller is deliberate: this is the single function every other
    entry point in this module funnels through (``block_ip``, ``unblock_ip``
    and ``is_blocked`` all call it), so the API layer, the admin panel and
    the CLI all inherit the same canonical form without having to remember
    to ask for it. A caller that normalised at its own boundary instead
    would be one caller away from the two sides disagreeing again.
    """
    key = _derive_key(secret_key, BLOCKLIST_INFO)
    canonical = normalise_ip(ip)
    return hmac.new(key, canonical.encode("utf-8"), hashlib.sha256).hexdigest()


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
