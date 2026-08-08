"""Blocking without storing an address. Contract §2.3's one exception."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from db.blocklist import (
    block_ip, ip_fingerprint, is_blocked, unblock_ip,
)
from db.blocklist import utcnow
from db.blocklist_models import IpBlock

KEY = "test-secret-key-of-sufficient-length-for-hkdf"


@pytest.mark.db
def test_the_address_is_not_recoverable_from_what_is_stored(session):
    """The whole reason this exception was acceptable. A database dump on
    its own must not yield a list of who visited."""
    block_ip(session, "203.0.113.9", reason="scripted traffic", actor="kim",
             secret_key=KEY)
    session.flush()

    row = session.scalar(select(IpBlock))
    assert "203.0.113.9" not in row.ip_hmac
    assert "203.0.113" not in row.ip_hmac
    assert len(row.ip_hmac) == 64


@pytest.mark.db
def test_the_same_address_gives_the_same_fingerprint(session):
    """Otherwise a block could never match the caller it was made for."""
    assert ip_fingerprint("203.0.113.9", secret_key=KEY) == \
           ip_fingerprint("203.0.113.9", secret_key=KEY)


@pytest.mark.db
def test_a_different_key_gives_a_different_fingerprint():
    """Keyed, not plain hashed. A rainbow table over the whole IPv4 space is
    trivial to build; an HMAC under a key the attacker does not have is not."""
    a = ip_fingerprint("203.0.113.9", secret_key=KEY)
    b = ip_fingerprint("203.0.113.9", secret_key="a-completely-different-key")

    assert a != b


@pytest.mark.db
def test_a_blocked_address_reads_as_blocked(session):
    block_ip(session, "203.0.113.9", reason="scripted traffic", actor="kim",
             secret_key=KEY)
    session.flush()

    assert is_blocked(session, "203.0.113.9", secret_key=KEY) is True
    assert is_blocked(session, "203.0.113.10", secret_key=KEY) is False


@pytest.mark.db
def test_a_block_can_expire(session):
    """A permanent block on a shared or reassigned address punishes whoever
    holds it next. Temporary is the default a human should reach for."""
    block_ip(session, "203.0.113.9", reason="burst", actor="kim",
             secret_key=KEY, minutes=30)
    session.flush()

    later = utcnow() + timedelta(minutes=31)
    assert is_blocked(session, "203.0.113.9", secret_key=KEY, now=later) is False


@pytest.mark.db
def test_a_block_with_no_expiry_does_not_expire(session):
    block_ip(session, "203.0.113.9", reason="persistent abuse", actor="kim",
             secret_key=KEY, minutes=None)
    session.flush()

    much_later = utcnow() + timedelta(days=3650)
    assert is_blocked(session, "203.0.113.9", secret_key=KEY, now=much_later) is True


@pytest.mark.db
def test_unblocking_works_and_reports_whether_it_did_anything(session):
    block_ip(session, "203.0.113.9", reason="burst", actor="kim", secret_key=KEY)
    session.flush()

    assert unblock_ip(session, "203.0.113.9", actor="kim", secret_key=KEY) is True
    session.flush()
    assert is_blocked(session, "203.0.113.9", secret_key=KEY) is False

    assert unblock_ip(session, "198.51.100.1", actor="kim", secret_key=KEY) is False


@pytest.mark.db
def test_blocking_twice_updates_rather_than_duplicating(session):
    """A second block on the same address is an extension or a change of
    reason, not a second row nobody will notice."""
    block_ip(session, "203.0.113.9", reason="first", actor="kim",
             secret_key=KEY, minutes=10)
    session.flush()
    block_ip(session, "203.0.113.9", reason="second", actor="kim",
             secret_key=KEY, minutes=60)
    session.flush()

    rows = session.scalars(select(IpBlock)).all()
    assert len(rows) == 1
    assert rows[0].reason == "second"


@pytest.mark.db
def test_the_row_records_who_blocked(session):
    """The audit entry is the caller's job — this module cannot write one,
    because write_audit lives in admin/ and db/ may not import it. But the
    row itself carries the name, which is what the screen shows."""
    block_ip(session, "203.0.113.9", reason="burst", actor="kim", secret_key=KEY)
    session.flush()

    assert session.scalar(select(IpBlock)).created_by == "kim"


def test_this_module_imports_nothing_from_admin():
    """The layering rule that put this file in db/. A stray import here
    reverses the dependency and makes the module unusable from the API layer
    — which is the entire reason the blocklist is not in admin/.

    Needs no database — kept off the `db` marker so this and the derivation
    pin below are the two checks in this file that still run in a CI job
    with no MySQL available.
    """
    import ast
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    for name in ("db/blocklist.py", "db/blocklist_models.py"):
        tree = ast.parse((repo_root / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("admin"), f"{name}: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("admin"), \
                    f"{name}: {node.module}"


def test_the_key_derivation_is_pinned():
    """Both this panel and the API derive the fingerprint key independently,
    from the same SECRET_KEY and the same info string — one of five
    parameters (algorithm, length, salt, info, encoding) that must match on
    both sides. If any of them diverge, nothing raises: fingerprints just
    stop matching, so a block made in the panel silently stops blocking
    anything in the API.

    The digest below is a fixed vector, not a self-consistency check — it
    pins the whole derivation (HKDF-SHA256, no salt, 32-byte key, this
    module's own BLOCKLIST_INFO, UTF-8 encoding), so a change to any one of
    those fails this test rather than the two sides quietly disagreeing at
    runtime. Needs no database — see the note on the layering test above.
    """
    from db.blocklist import BLOCKLIST_INFO

    assert BLOCKLIST_INFO == b"kaicalc-blocklist-v1"
    assert ip_fingerprint("203.0.113.9", secret_key="fixed-key-for-this-vector") == \
        "3b35112e5cf707aa8525a79b0391e8bf08daea563a0a4c0dc23c24c9f59059a1"
    assert len(ip_fingerprint("203.0.113.9", secret_key=KEY)) == 64
