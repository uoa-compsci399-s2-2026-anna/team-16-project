"""The blocklist, where the traffic actually arrives. Contract §2.3 and §9.2.

E's stage built the blocklist (`db/blocklist.py`) and applied it to the panel
(`admin/protection.py`). Nothing applied it to `/api/v1/`, so a block made on
the blocklist screen did not hold where the public traffic is — which is the
one thing a blocklist is for.

Two properties are worth more than the rest here:

* **The refusal body is the same §9 envelope everywhere.** `admin/protection.py`
  answers a blocked caller with `PlainTextResponse("Refused.")`, which is right
  for a browser hitting the panel and wrong for a JSON API. If the API answered
  with anything other than a `BLOCKED` envelope, a blocked caller would get two
  different bodies depending which path they hit, and C and D would each have to
  special-case it.
* **The check runs before routing.** A router-level `Depends` never runs for a
  path that does not resolve, so a blocked caller could still tell a live route
  from a dead one by the shape of the answer. `test_a_dead_path_is_refused_too`
  is what closes that, and it is the test that fails if the check is ever moved
  back into the router.
"""

import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.orm import sessionmaker

from db.blocklist import block_ip, ip_fingerprint
from tests.support.sqlite import API_TEST_SECRET_KEY

pytestmark = pytest.mark.asyncio

#: httpx's `ASGITransport` default, and therefore the address every request in
#: this file arrives as unless the transport says otherwise.
CALLER = "127.0.0.1"

BLOCKED_BODY = {
    "error": {
        "code": "BLOCKED",
        "message": (
            "This request was refused. If you believe this is an error, "
            "contact the Kai Commitment team."
        ),
        "details": None,
    }
}


def _block(engine, address=CALLER):
    """Block an address exactly the way the panel's own form does — through
    `db.blocklist.block_ip`, on the same table, under the same key. A test that
    reached into `ip_block` with raw SQL would prove the middleware reads a
    row; this proves it reads *the row the panel writes*, which is the failure
    §2.3 names (a block applied in the panel silently failing to hold at the
    API)."""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        block_ip(db, address, reason="scripted traffic", actor="kim",
                 secret_key=API_TEST_SECRET_KEY)
        db.commit()


def _client(app, *, client=("127.0.0.1", 123)):
    transport = ASGITransport(app=app, client=client)
    return AsyncClient(transport=transport, base_url="http://testserver")


class _CapturingLimiter:
    """Records every key the router asks it about, and allows everything.

    Used instead of driving 600 real requests: the assertions below are about
    *which key* a caller is counted under (or whether they are counted at all),
    not about the limit arithmetic, which tests/test_rate_limit.py owns.
    """

    def __init__(self) -> None:
        self.keys: list[str] = []

    def allow(self, key, limit):
        self.keys.append(key)
        return True, 0


# --- The block holds, and holds everywhere --------------------------------


async def test_an_address_blocked_through_the_panel_is_refused_at_the_api(
    app, sqlite_engine
):
    _block(sqlite_engine)
    async with _client(app) as client:
        response = await client.get("/api/v1/taxonomy")

    assert response.status_code == 403, response.text
    assert response.json() == BLOCKED_BODY


@pytest.mark.parametrize("method,path", [
    ("get", "/api/v1/taxonomy"),
    ("get", "/api/v1/stats"),
    ("get", "/api/v1/factors"),
    ("post", "/api/v1/calculate"),
])
async def test_every_endpoint_under_api_v1_is_checked_get_included(
    app, sqlite_engine, method, path
):
    """§9.2: "It applies to every endpoint under `/api/v1/`, `GET` included."."""
    _block(sqlite_engine)
    async with _client(app) as client:
        response = await getattr(client, method)(path)

    assert response.status_code == 403, response.text
    assert response.json() == BLOCKED_BODY


async def test_a_dead_path_is_refused_too(app, sqlite_engine):
    """The reason this is middleware and not a router dependency.

    FastAPI solves a router's dependencies only once a request has matched a
    route, so `/api/v1/does-not-exist` answered 404 while every live path
    answered 403 — handing a blocked caller a working route scanner. Running
    ahead of routing is what removes the difference.
    """
    _block(sqlite_engine)
    async with _client(app) as client:
        response = await client.get("/api/v1/does-not-exist")

    assert response.status_code == 403, response.text
    assert response.json() == BLOCKED_BODY


async def test_the_block_is_checked_before_request_validation(app, sqlite_engine):
    """§9.2: a blocked caller must not be able to read the taxonomy out of the
    API's own validation messages."""
    _block(sqlite_engine)
    async with _client(app) as client:
        response = await client.post("/api/v1/calculate", json={"nonsense": True})

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "BLOCKED"


async def test_the_refusal_is_json_and_says_nothing_about_the_rule_it_broke(
    app, sqlite_engine
):
    """The same reticence as `admin/protection.py`'s bare "Refused.", in the
    envelope §9 puts on every other error. Nothing in the body may name the
    blocklist, the reason, or the expiry."""
    _block(sqlite_engine)
    async with _client(app) as client:
        response = await client.get("/api/v1/taxonomy")

    assert response.headers["content-type"] == "application/json; charset=utf-8"
    body = response.text.lower()
    # `BLOCKED` itself is §9.2's mandated `code`, so "block" as a substring is
    # expected. What must not appear is anything that tells the caller *which*
    # rule fired, who set it, when it lifts, or which address matched - the
    # free tuning signal `admin/protection.py`'s bare "Refused." withholds too.
    for leak in ("scripted traffic", "kim", "ip_block", "expires", CALLER):
        assert leak not in body, leak
    assert json.loads(response.text)["error"]["details"] is None


async def test_an_unblocked_caller_is_served_normally(app):
    """The counter-case. Without it every assertion above is satisfied by a
    middleware that refuses everyone."""
    async with _client(app) as client:
        response = await client.get("/api/v1/taxonomy")

    assert response.status_code == 200, response.text


async def test_a_block_on_another_address_does_not_refuse_this_caller(
    app, sqlite_engine
):
    _block(sqlite_engine, "203.0.113.9")
    async with _client(app) as client:
        response = await client.get("/api/v1/taxonomy")

    assert response.status_code == 200, response.text


async def test_the_blocklist_is_consulted_exactly_once_per_request(app):
    """`is_blocked` runs on every public request, so it must stay one query.

    It was briefly going to be two: a router-level `Depends` *and* the
    middleware, each doing its own indexed lookup on `ip_hmac`. This is what
    fails if the dependency is ever re-added alongside the middleware.
    """
    calls = []

    def _check(request):
        calls.append(request.url.path)
        return False

    app.state.blocklist_check = _check
    async with _client(app) as client:
        await client.get("/api/v1/taxonomy")

    assert calls == ["/api/v1/taxonomy"]


# --- Addresses that are not addresses --------------------------------------


async def test_an_unparseable_client_address_is_not_a_500(app, sqlite_engine):
    """§2.3: "Callers on a request path must not let that exception escape."
    `ip_fingerprint` raises `InvalidAddressError` rather than fingerprinting
    nonsense, and this middleware runs ahead of every request — so an address
    it cannot parse must read as "no address", not as an internal error."""
    _block(sqlite_engine)
    async with _client(app, client=("not-an-address", 1)) as client:
        response = await client.get("/api/v1/taxonomy")

    assert response.status_code == 200, response.text


async def test_a_caller_with_no_address_is_not_bucketed_with_every_other_one(app):
    """B's `api/router.py::_client_ip` returned the literal string `"unknown"`,
    which is a live dict key — so every client-less caller shared one
    rate-limit bucket, and the first of them to exceed the limit rate-limited
    all the rest. The same `""`-key defect E removed from
    `admin/protection.py::_client_ip`. Skipped, not bucketed."""
    limiter = _CapturingLimiter()
    app.state.rate_limiter = limiter
    async with _client(app, client=None) as client:
        response = await client.get("/api/v1/taxonomy")

    assert response.status_code == 200, response.text
    assert limiter.keys == []


async def test_a_caller_with_no_address_still_skips_the_blocklist_rather_than_matching(
    app, sqlite_engine
):
    """Not a fail-open choice so much as a logical impossibility: the blocklist
    is keyed on `HMAC(address)` and there is no address to hash. The
    alternative — inventing a stand-in key — is what gives every addressless
    caller one shared blocklist entry."""
    _block(sqlite_engine)
    async with _client(app, client=None) as client:
        response = await client.get("/api/v1/taxonomy")

    assert response.status_code == 200, response.text


# --- What the rate limiter is allowed to remember --------------------------


async def test_the_rate_limit_key_is_a_fingerprint_and_never_an_address(app):
    """§2.3 read as covering process memory, not only the database — which is
    the reading `admin/protection.py` already applies to its own counter. The
    rate limiter's dict outlives the request that filled it, so a raw address
    in a key is an address this system holds. §6.5 recorded the two layers
    applying different standards to the same data as open; they now apply the
    same one."""
    limiter = _CapturingLimiter()
    app.state.rate_limiter = limiter
    async with _client(app) as client:
        await client.get("/api/v1/taxonomy")

    assert limiter.keys == [
        f"get:{ip_fingerprint(CALLER, secret_key=API_TEST_SECRET_KEY)}"
    ]
    assert CALLER not in limiter.keys[0]
    assert "unknown" not in limiter.keys[0]
