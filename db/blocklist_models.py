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

    A deliberate duplicate of ``admin.models.utcnow`` and ``db.models.utcnow``
    — there are THREE, not two, which is the first thing this note got wrong.
    ``db/`` may not import ``admin/`` — see the layering note in CLAUDE.md — so
    the helper is copied rather than shared. Change all three together;
    ``test_all_three_utcnows_agree`` is what notices if one is missed.

    **Microseconds are dropped, and not for tidiness.** Every ``DATETIME``
    column in this schema carries zero digits of fractional-seconds precision
    (asserted in ``tests/test_migrations.py``), and MySQL **rounds** rather
    than truncates when it stores one: measured,
    ``CAST('2026-10-06 22:26:59.700000' AS DATETIME)`` is
    ``2026-10-06 22:27:00``. So a value with microseconds is, in
    ``api/schemas.py``'s words about the same problem on the request path, "a
    value that changes when it is stored" - and it can change by a whole
    displayed minute.

    That is not hypothetical. ``test_the_list_page_shows_the_published_set``
    wrote ``utcnow()``, committed, and asserted the panel printed
    ``published_at.strftime('%d %b %Y, %H:%M')``. On 2026-10-06 the write
    landed in the last half-second of a minute, MySQL rounded it up, the page
    rendered 22:27 and the test expected 22:26. It is a 0.5-in-60 window, so
    roughly one run in a hundred and twenty, which is exactly often enough to
    be dismissed as a flake and never fixed.

    ``api/schemas.py`` already does this on the request path and says why:
    dropped "where the caller can be told it happened, rather than in MySQL,
    where nobody is". This is the same decision at the other source of
    timestamps.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


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

    def __str__(self) -> str:
        #: reason, plus enough of the timing to tell two blocks with the same
        #: reason apart. Never ip_hmac: it is 64 characters no human can act
        #: on, and it is derived from an address - putting it in the one
        #: string sqladmin renders everywhere (list columns, detail pages,
        #: every select box) is exactly the leak contract §2.3's exception
        #: exists to prevent. Never id either, for the same reason every
        #: other model in this project prefers a human identifier over its
        #: primary key.
        when = self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at \
            else "not yet recorded"
        expiry = (
            f"expires {self.expires_at.strftime('%Y-%m-%d %H:%M')}"
            if self.expires_at is not None else "no expiry"
        )
        return f"{self.reason} (blocked {when}, {expiry})"
