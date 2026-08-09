"""One detection module, imported by both callers. Contract §8.3.

`looks_automated`, `RequestRate` and `_client_ip` were built in `admin/`
because that is where E's stage lived, not because that is where they belong.
§8.3 recorded the move-vs-duplicate choice as open and recommended the move:
both the API and the panel need them, `db/` is the layer both may import, and
two copies of a detection rule drift — the copy that stops matching is the one
nobody notices.

These tests pin the *identity* of the objects, not their behaviour. Behaviour
is already covered by tests/admin/test_detection.py and
tests/admin/test_client_address.py, which now exercise the shared
implementation through the re-export; asserting `is` here is what catches the
failure those two cannot see — a second copy appearing in `api/` or a
re-export quietly becoming a fork.
"""

import ast
from pathlib import Path

import pytest

import admin.detection
import admin.protection
import api.rate_limit
import db.detection

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_admin_detection_is_the_shared_implementation():
    assert admin.detection.looks_automated is db.detection.looks_automated
    assert admin.detection.RequestRate is db.detection.RequestRate


def test_the_panel_measures_callers_with_the_shared_client_ip():
    """`admin.protection._client_ip` is the name tests/admin/test_client_address.py
    imports and the name §2.3 names; it must now *be* `db.detection.client_ip`,
    not a second function that happens to agree with it today."""
    assert admin.protection._client_ip is db.detection.client_ip


def test_the_api_rate_limiter_counts_with_the_shared_counter():
    """`api/rate_limit.py` was an independent third implementation of a
    request counter. Reconciled to the shared one rather than kept alongside
    it."""
    assert api.rate_limit.RequestRate is db.detection.RequestRate


def test_the_shared_module_imports_nothing_from_admin():
    """The layering rule that put this file in `db/`, and the same AST pin
    tests/db/test_blocklist.py applies to the blocklist itself. A stray import
    here reverses the dependency and makes the module unusable from `api/` —
    which is the entire reason it moved."""
    tree = ast.parse((REPO_ROOT / "db/detection.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("admin"), alias.name
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("admin"), node.module


def test_no_module_under_api_imports_sqlalchemy():
    """CLAUDE.md: `db/repository.py` is the only code that touches the
    database. Wiring the blocklist into `api/` added two `db.` imports to that
    layer (`db.blocklist`, `db.detection`), which is allowed — they are the
    same layer. Importing the ORM directly is not, and is what this pins.
    """
    for path in sorted((REPO_ROOT / "api").glob("*.py")):
        source = path.read_text(encoding="utf-8").lower()
        assert "sqlalchemy" not in source, path.name


# --- `client_ip`, on the request path -------------------------------------


class _FakeClient:
    def __init__(self, host: str) -> None:
        self.host = host


class _FakeRequest:
    """The two attributes `client_ip` reads. A hand-built stand-in rather than
    a real `starlette.requests.Request` so that this file — like the module it
    tests — needs nothing outside the standard library to run."""

    def __init__(self, *, client: str | None = None, headers: dict | None = None):
        self.client = _FakeClient(client) if client is not None else None
        self.headers = headers or {}


@pytest.mark.parametrize("value", ["not-an-address", "203.0.113.9:443", ""])
def test_an_unparseable_address_does_not_escape_as_an_exception(value):
    """§2.3: `ip_fingerprint` raises `InvalidAddressError` on an unparseable
    value by design, and this function runs ahead of every request. An address
    it cannot parse must read as "no address", never as a 500."""
    assert db.detection.client_ip(_FakeRequest(client=value), trusted_proxy=False) is None


def test_no_client_address_reads_as_no_address_rather_than_a_placeholder():
    """Never `""`, and never the literal string `"unknown"`. Both are live
    dict keys, so both collapse every client-less caller into one shared
    rate-limit bucket and one shared blocklist lookup."""
    result = db.detection.client_ip(_FakeRequest(client=None), trusted_proxy=False)
    assert result is None


def test_the_address_is_canonicalised_the_way_the_blocklist_stores_it():
    request = _FakeRequest(client="2001:0DB8:0000:0000:0000:0000:0000:0001")
    assert db.detection.client_ip(request, trusted_proxy=False) == "2001:db8::1"
