"""The blocklist. Contract §2.3's single, deliberate exception.

The project stores no IP address, no user agent and no fingerprint. This
table stores an **HMAC** of an address, under a key derived from
SECRET_KEY — and only for callers someone has actually blocked.

What that buys: a database taken on its own yields no list of who visited,
because the fingerprints cannot be reversed without the key.

What it does not buy: an attacker holding *both* the database and
SECRET_KEY can confirm whether a specific address is present, and could
enumerate IPv4's 4.3 billion addresses to recover the list. That is
accepted, because SECRET_KEY also signs every session cookie and encrypts
every TOTP secret — an attacker holding it has already won, and this table
is the least of the damage.
"""

from datetime import datetime, timezone

from sqlalchemy import CHAR, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


def utcnow() -> datetime:
    """Naive UTC, matching every other timestamp column in this schema.

    A deliberate duplicate of ``admin.models.utcnow``. ``db/`` may not import
    ``admin/`` — see the layering note in CLAUDE.md — so this three-line
    helper is copied rather than shared. Change both together.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class IpBlock(Base):
    __tablename__ = "ip_block"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    #: HMAC-SHA256 of the address, hex. CHAR because it is always 64 chars.
    ip_hmac: Mapped[str] = mapped_column(CHAR(64), unique=True, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False,
                                                 default=utcnow)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Null means it does not expire. A finite expiry is the kinder default:
    #: addresses are reassigned, and a permanent block on a shared address
    #: punishes whoever holds it next.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
