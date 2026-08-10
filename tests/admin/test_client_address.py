"""``admin.protection._client_ip`` - which address a caller is measured by.

Split out of tests/admin/test_protection.py rather than appended to it. That
module's ``pytestmark`` marks every test in it both ``db`` and ``asyncio``, and
neither applies here: these are synchronous calls into a pure function over a
hand-built ASGI scope, with no database and no HTTP client. Left in that file
they ran under a MySQL requirement they do not have (the same objection Task
1's review raised about its own static tests) and emitted a PytestWarning each
for carrying an ``asyncio`` mark on a non-coroutine.

Why the function is called directly at all: it has three inputs
(``request.client``, ``X-Forwarded-For``, and the ``trusted_proxy`` setting)
whose interesting combinations cannot all be produced through an HTTP client.
httpx's ``ASGITransport`` fixes ``scope["client"]`` for a whole transport, and
a *malformed* one is not something any real ASGI server would ever send - but
it is exactly the case that must not become a 500 on the login page, now that
``db.blocklist.ip_fingerprint`` refuses to fingerprint an unparseable address.
"""

from starlette.requests import Request

from admin.protection import _client_ip


def _request(*, client, headers=None):
    """A bare Starlette ``Request`` over a hand-built ASGI scope."""
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({
        "type": "http", "method": "GET", "path": "/admin/login",
        "headers": raw, "client": client, "scheme": "http",
        "query_string": b"",
    })


def test_a_malformed_client_address_reads_as_no_address():
    """`db.blocklist.ip_fingerprint` raises on an unparseable address by
    design (a fingerprint of nonsense is a block that matches nobody), and
    this middleware runs ahead of every request under `/admin` - so an
    address it cannot parse must become "no address", never a 500 on the
    login page."""
    assert _client_ip(_request(client=("not-an-address", 1234)),
                      trusted_proxy=False) is None


def test_no_client_address_at_all_reads_as_no_address():
    assert _client_ip(_request(client=None), trusted_proxy=False) is None


def test_the_client_address_is_canonicalised():
    """So the fingerprint the middleware computes is the one the panel's own
    form computes for the same address, whichever spelling either was
    handed."""
    assert _client_ip(_request(client=("2001:0DB8:0000:0000:0000:0000:0000:0001", 1)),
                      trusted_proxy=False) == "2001:db8::1"


def test_x_forwarded_for_is_ignored_unless_a_proxy_is_declared():
    request = _request(client=("203.0.113.9", 1),
                       headers={"x-forwarded-for": "198.51.100.7"})

    assert _client_ip(request, trusted_proxy=False) == "203.0.113.9"
    assert _client_ip(request, trusted_proxy=True) == "198.51.100.7"


def test_an_unparseable_x_forwarded_for_falls_back_to_the_real_address():
    """Not to None. With `trusted_proxy` true, `X-Forwarded-For` is the one
    attacker-reachable input on this path, and treating an unparseable value
    as "no address" would hand every caller a one-header bypass of both the
    blocklist and the rate limit. Falling back means a forged header gains
    nothing."""
    request = _request(client=("203.0.113.9", 1),
                       headers={"x-forwarded-for": "; DROP TABLE ip_block"})

    assert _client_ip(request, trusted_proxy=True) == "203.0.113.9"


