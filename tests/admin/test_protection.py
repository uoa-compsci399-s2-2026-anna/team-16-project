"""The middleware. Contract §2.3 - nothing here is stored."""

import json
import time
from base64 import b64encode

import itsdangerous
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from admin.accounts import get_staff
from admin.app import create_app
from admin.auth import SESSION_GENERATION_KEY, SESSION_KEY
from tests.admin.conftest import _BROWSER_HEADERS, _cleanup_staff, _create_onboarded_account
from tests.conftest import TEST_URL

# pytest.mark.asyncio, not in the brief's own listing, is required here:
# pytest.ini sets asyncio_mode = strict for this project, so an `async def`
# test with no marker is collected but never actually run as a coroutine -
# every other async test file in tests/admin/ (e.g. test_compare_view.py,
# test_factor_set_actions.py) carries this same pair for the same reason.
pytestmark = [pytest.mark.db, pytest.mark.asyncio]


@pytest.fixture
def admin_app(monkeypatch):
    """This file's own app: ProtectionMiddleware genuinely on, not
    tests/conftest.py's session-wide ``PROTECTION_ENABLED=false`` default.

    A full local override of tests/conftest.py's ``admin_app``, not a
    companion ``autouse`` fixture that patches the environment on the side -
    that was tried first and is wrong. ``_cleanup_ip_blocks`` below is also
    ``autouse`` and also requires ``admin_app``, so with two independent
    ``autouse`` fixtures at the same scope and no dependency between them,
    pytest does not guarantee which runs first: confirmed directly, with a
    debug print, that the environment-patching version of this fixture ran
    *after* ``admin_app`` had already called ``create_app()`` and read
    ``PROTECTION_ENABLED=false`` - the session default - rather than before
    it. Setting the variable inside *this* fixture's own body, ahead of its
    own ``create_app()`` call, makes the ordering an actual dependency
    (this function must return before anything downstream of ``admin_app``
    can run) instead of a hoped-for scope/autouse tiebreak.

    Otherwise identical to tests/conftest.py's ``admin_app`` - see that
    fixture's own docstring for why each remaining line is here. Duplicated
    rather than composed (e.g. requesting the outer ``admin_app`` as a
    same-named parameter, pytest's usual override-and-extend recipe) because
    that recipe only lets an override see the outer fixture's *result*,
    after its body - including its ``create_app()`` call - has already run;
    what this fixture needs is to change the environment *before* that call,
    which requires owning the call itself.
    """
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-not-used-anywhere-real")
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    monkeypatch.setenv("SESSION_HTTPS_ONLY", "false")
    monkeypatch.setenv("PROTECTION_ENABLED", "true")
    app = create_app()
    yield app
    app.state.session_factory.kw["bind"].dispose()


@pytest.fixture
def session(_committed_session):
    """This file's name for the hard-committing session against the running
    admin app's own database - see tests/admin/conftest.py's
    `_committed_session` docstring for the general reasoning.

    Load-bearing, not a style choice: the plain, rolled-back `session`
    tests/conftest.py defines is bound to a `Connection` whose transaction
    was started manually, outside the Session, via `connection.begin()`.
    Calling `.commit()` on a Session in that arrangement does not end that
    outer transaction - confirmed directly: `transaction.is_active` reads
    `True` immediately after `session.commit()` returns - so nothing
    written through it is ever visible to another connection until the
    fixture's own teardown runs, and that teardown rolls everything back
    rather than committing it. admin/protection.py's blocklist check opens
    its *own* connection via `admin_app.state.session_factory` on every
    request, so a `block_ip` written through the rolled-back session can
    never be seen there - a test using it would only pass because the
    request's default httpx User-Agent independently trips the *header*
    check to the same 403, never actually exercising the blocklist path it
    claims to. `_committed_session` performs a real commit, which is what a
    block visible to a second connection requires.
    """
    return _committed_session


@pytest.fixture(autouse=True)
def _cleanup_ip_blocks(admin_app):
    """Remove every ip_block row this file's tests commit through the
    `session` override above.

    Unlike `_committed_session`'s own "e6-"/"e7-"-prefixed cleanup, an
    ip_block row carries no code or label to prefix-match - `db.blocklist`
    identifies rows only by an HMAC fingerprint. `created_by` is the field
    every call in this file sets to the same actor value ("kim", per the
    brief's own test bodies), so that is what is matched here instead - the
    same reasoning tests/admin/conftest.py's `_cleanup_staff` gives for
    keying on the actor rather than on the row.
    """
    yield
    factory = admin_app.state.session_factory
    with factory() as db:
        db.execute(text("DELETE FROM ip_block WHERE created_by = 'kim'"))
        db.commit()


def _sign_session_cookie(data: dict, *, secret_key: str,
                         signer_cls=itsdangerous.TimestampSigner) -> str:
    """Build a session cookie value exactly as SessionMiddleware would write
    one - by hand, not by driving a real login through `/admin/login` and
    `/admin/verify` - so a test can hand ProtectionMiddleware a cookie
    carrying precisely the fields needed to isolate one check inside
    `_is_authenticated_staff`/`_decode_session_cookie`. Mirrors
    `starlette.middleware.sessions.SessionMiddleware`'s own write side
    exactly: base64(json(data)), then signed - see admin/protection.py's
    module docstring for the full parameter-by-parameter comparison this
    is built from.
    """
    payload = b64encode(json.dumps(data).encode("utf-8"))
    return signer_cls(secret_key).sign(payload).decode("utf-8")


class _ExpiredTimestampSigner(itsdangerous.TimestampSigner):
    """Signs as though it happened long enough ago that any realistic
    `max_age` has already elapsed. Used only to *build* a test cookie that
    looks expired - never to verify one; `_decode_session_cookie` itself is
    never touched by this class."""

    def get_timestamp(self) -> int:
        return int(time.time()) - 10_000_000  # roughly 116 days ago


@pytest.fixture
def onboarded_staff(admin_app):
    """A real, fully onboarded, active staff account to build hand-signed
    cookies against.

    Not `admin_client`: the four tests below exist specifically to bypass
    the real login flow and hand ProtectionMiddleware a cookie it never
    itself issued, to prove each refusal check inside
    `_is_authenticated_staff` is independently load-bearing rather than
    covered incidentally by one of the others.
    """
    staff, _password, _secret = _create_onboarded_account(admin_app)
    yield staff
    _cleanup_staff(admin_app, staff)


async def test_a_blocked_address_is_refused(client, session, settings):
    """Browser-shaped headers are load-bearing here, not decoration: without
    them httpx's default User-Agent trips the *header* check first and the
    test passes at 403 regardless of whether the blocklist check works at
    all - confirmed directly (see Step 6-style mutation test below, and the
    task report). Sending headers that would otherwise pass makes a 403 here
    only explainable by the blocklist."""
    from db.blocklist import block_ip

    block_ip(session, "127.0.0.1", reason="test", actor="kim",
             secret_key=settings.secret_key)
    session.commit()

    response = await client.get("/admin/login", headers=_BROWSER_HEADERS)

    assert response.status_code == 403


async def test_an_ordinary_browser_request_passes(client):
    response = await client.get("/admin/login", headers={
        "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36",
        "accept": "text/html,application/xhtml+xml",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "none",
        "sec-fetch-dest": "document",
    })

    assert response.status_code == 200


async def test_a_scripted_request_is_refused(client):
    response = await client.get("/admin/login",
                                headers={"user-agent": "curl/8.4.0"})

    assert response.status_code == 403


async def test_an_authenticated_staff_member_is_exempt_from_the_header_check(
    admin_client
):
    """They passed a password, a TOTP code and a session check. Refusing them
    for a header protects nothing - and the panel has no email recovery, so
    walling off its own operators is the failure this exemption exists to
    prevent."""
    response = await admin_client.get("/admin/factor-set/list",
                                      headers={"user-agent": "curl/8.4.0"})

    assert response.status_code == 200


async def test_a_blocked_address_is_refused_even_when_authenticated(
    admin_client, session, settings
):
    """The exemption covers the behavioural checks, not the blocklist. A
    blocked address is a deliberate act by another administrator."""
    from db.blocklist import block_ip

    block_ip(session, "127.0.0.1", reason="test", actor="kim",
             secret_key=settings.secret_key)
    session.commit()

    response = await admin_client.get("/admin/factor-set/list")

    assert response.status_code == 403


async def test_static_files_are_not_checked(client):
    """The stylesheet loads on the login page, before anyone has a session.
    Refusing it leaves an unstyled page that looks broken rather than
    protected."""
    response = await client.get("/admin/static/brand.css",
                                headers={"user-agent": "curl/8.4.0"})

    assert response.status_code == 200


_RATE_TEST_HEADERS = {
    "user-agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/131.0",
    "accept": "text/html", "sec-fetch-mode": "navigate",
}


async def test_exceeding_the_rate_limit_is_refused(client, settings):
    """Faster than a human types, from one source.

    Counted against `/admin/factor-set/list`, not `/admin/login`: the two
    login-handshake paths are deliberately exempt from this check (see
    `admin/protection.py`'s `_RATE_EXEMPT_PATHS` and the test below), so
    hammering `/admin/login` no longer proves the rate limit exists at all.
    Any other path under `/admin` does. Unauthenticated, this path answers
    with sqladmin's own redirect to the login page until the limit is
    reached; only the final assertion is about the middleware.
    """
    limit = settings.protection_max_requests_per_minute

    for _ in range(limit):
        await client.get("/admin/factor-set/list", headers=_RATE_TEST_HEADERS)
    response = await client.get("/admin/factor-set/list", headers=_RATE_TEST_HEADERS)

    assert response.status_code == 429


# ---------------------------------------------------------------------------
# Two visitors through the proxy do not share a bucket
# ---------------------------------------------------------------------------
#
# THIS IS THE BEHAVIOUR THE UNPUBLISHED api/admin PORTS EXIST TO BUY, AND IT IS
# ASSERTED AS BEHAVIOUR RATHER THAN AS A SETTING. `docker/compose.yaml` saying
# `PROTECTION_TRUSTED_PROXY: ${PROTECTION_TRUSTED_PROXY:-true}` is a string in
# a YAML file; a test that reads it back has asserted that somebody typed it.
# What matters is that two visitors arriving through the proxy are counted
# separately, and the only way to know that is to send two and look.
#
# So the value is READ FROM THE COMPOSE FILE AND USED TO BUILD THE APP. That
# coupling is the point: revert the compose default to `false` and this test
# fails, because the app it builds then measures the connection - which is one
# address for both visitors - instead of the forwarded one. Measured the same
# way against the running stack before the change: one container exhausted the
# panel's minute through :18080 and a second container's next request was
# refused 429 on its first try.

#: Two visitors, as an edge or our own nginx would report them. TEST-NET-2 and
#: TEST-NET-3 (RFC 5737): unroutable, and not addresses any test harness can
#: supply by accident, so a bucket keyed on one of these got there from the
#: header and from nowhere else.
_VISITOR_A = "198.51.100.11"
_VISITOR_B = "203.0.113.22"


def _compose_trusted_proxy_default(service: str) -> bool:
    """What `docker compose up` actually hands ``service``, parsed from the file.

    Not a copy of the value and not a hard-coded ``True``: either would let
    the compose file drift away from the behaviour asserted below while every
    test still passed, which is the failure this whole module keeps finding in
    other forms.
    """
    import re
    from pathlib import Path

    import yaml

    compose = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "docker" / "compose.yaml")
        .read_text(encoding="utf-8")
    )
    raw = compose["services"][service]["environment"]["PROTECTION_TRUSTED_PROXY"]
    # `${PROTECTION_TRUSTED_PROXY:-true}` -> `true`. An expansion with no
    # default, or a literal, would not match and is a failure rather than a
    # guess: this test may not invent the value it is supposed to be checking.
    match = re.fullmatch(r"\$\{PROTECTION_TRUSTED_PROXY:-(\w+)\}", str(raw).strip())
    assert match, (
        f"docker/compose.yaml's {service} service sets PROTECTION_TRUSTED_PROXY "
        f"to {raw!r}, which this test cannot read a default out of"
    )
    return match.group(1).lower() in ("1", "true", "yes", "on")


@pytest.fixture
def stack_app(monkeypatch):
    """The panel, built the way ``docker/compose.yaml`` builds it.

    Same body as ``admin_app`` above apart from the one line that matters -
    see that fixture's docstring for why the environment has to be set inside
    the fixture that owns the ``create_app()`` call rather than beside it.
    """
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-not-used-anywhere-real")
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    monkeypatch.setenv("SESSION_HTTPS_ONLY", "false")
    monkeypatch.setenv("PROTECTION_ENABLED", "true")
    monkeypatch.setenv(
        "PROTECTION_TRUSTED_PROXY",
        "true" if _compose_trusted_proxy_default("admin") else "false",
    )
    app = create_app()
    yield app
    app.state.session_factory.kw["bind"].dispose()


@pytest_asyncio.fixture
async def stack_client(stack_app):
    """Every request from one peer address, which is the whole point.

    ``ASGITransport`` gives every request the same ``scope["client"]``, exactly
    as nginx does when it is the only thing connecting to this application. The
    two visitors below are distinguished by ``X-Forwarded-For`` and by nothing
    else, so if that header is not what the limiter keys on, they are one
    caller.
    """
    transport = ASGITransport(app=stack_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def test_two_visitors_through_the_proxy_do_not_share_a_rate_limit_bucket(
    stack_client, settings
):
    """The acceptance test, and the previous experiment run backwards.

    Visitor A spends the whole minute; visitor B, arriving through the same
    proxy on the same connection, must still be served. Before the api and
    admin ports became opt-in this failed - `PROTECTION_TRUSTED_PROXY` was
    false, `client_ip` read the connection, and both visitors were the one
    address nginx connects from.

    B is asserted at both ends of A's flood. The first assertion is what stops
    a green result from meaning "B was refused for some reason that has nothing
    to do with buckets", and the last is the property itself.
    """
    limit = settings.protection_max_requests_per_minute
    a = dict(_RATE_TEST_HEADERS, **{"x-forwarded-for": _VISITOR_A})
    b = dict(_RATE_TEST_HEADERS, **{"x-forwarded-for": _VISITOR_B})

    assert (await stack_client.get("/admin/factor-set/list", headers=b)).status_code != 429

    for i in range(limit + 1):
        response = await stack_client.get("/admin/factor-set/list", headers=a)
    assert response.status_code == 429, (
        f"visitor A was still being served after {limit + 1} requests, so this "
        "test never reached the state it is about"
    )

    served = await stack_client.get("/admin/factor-set/list", headers=b)
    assert served.status_code != 429, (
        "a second visitor through the same proxy was refused because the first "
        "one had spent the minute - the rate-limit bucket is shared, which is "
        "the defect unpublishing the api and admin ports exists to fix"
    )


# WHAT THIS FILE DELIBERATELY DOES NOT TEST: that a FORGED X-Forwarded-For
# cannot move another visitor's bucket. It cannot be tested here, and writing
# something that looked like it would be worse than the gap. The application
# now believes the header, so from inside it a forged chain and a real one are
# the same bytes - by design. What stops the forgery is that nginx OVERWRITES
# X-Forwarded-For with the peer it saw, so a forger's claim lands in the
# forger's own bucket. That is a property of the proxy and is asserted against
# the real image in `tests/test_web_forwarded_headers.py`
# (`test_a_forged_header_lands_in_the_forgers_own_bucket`). Both halves are
# needed and neither implies the other: this file failing means the
# application stopped keying on the header, that file failing means nginx
# stopped sanitising it.


@pytest.mark.parametrize("path", ["/admin/login", "/admin/verify"])
async def test_the_login_handshake_is_never_rate_limited(client, settings, path):
    """The remote-lockout weapon this exemption removes.

    A reverse proxy is in front of the panel in every deployment of it, and
    while `PROTECTION_TRUSTED_PROXY` is False every caller arrives as that
    proxy's own address and shares one rate-limit bucket. `docker/compose.yaml`
    now defaults it True and publishes no route around nginx, so the shipped
    stack is not in that state - but a deployment that overlays
    `docker/compose.direct-ports.yaml`, or one assembled without those files,
    still is, and this exemption is what keeps it recoverable.
    `RequestRate.record` counts refused requests too, so
    one request a second from any unauthenticated caller anywhere kept that
    single bucket permanently over the limit and answered 429 to *everyone* -
    including these two pages. The authenticated-staff exemption structurally
    could not help, because it needs the session only these two pages mint:
    recovery was an env var plus a restart.

    Asserts every response, not just the last: a 429 anywhere in this loop is
    the lockout. Very little is given up - `admin/throttle.py` still throttles
    login attempts per account, and the header check still refuses a scripted
    caller here (see `test_a_scripted_request_is_refused`, which uses
    `/admin/login`).
    """
    limit = settings.protection_max_requests_per_minute

    for i in range(limit * 2 + 1):
        response = await client.get(path, headers=_RATE_TEST_HEADERS)
        assert response.status_code != 429, f"rate-limited at request {i + 1}"
        # 200 for /admin/login, which renders; 302 for /admin/verify, which
        # sends a caller with no half-completed login back to /admin/login.
        # Both are the page's own answer, i.e. the request reached it -
        # `!= 429` above is the assertion about the middleware, and this one
        # guards against the exemption being satisfied by some *other*
        # refusal (a 403 from the header check, say) instead.
        assert response.status_code in (200, 302), response.status_code


async def test_the_login_handshake_does_not_consume_the_shared_rate_budget(
    client, settings
):
    """Not merely "exempt from refusal" - not counted at all.

    A version of the exemption that still called `record()` and only skipped
    the refusal would leave the attack intact in a different shape: the flood
    would go on filling the one bucket every *other* unauthenticated path
    shares with it, so staff could reach `/admin/login` but nothing else, and
    an attacker would still have a remote denial-of-service against the whole
    panel from an unauthenticated endpoint.
    """
    limit = settings.protection_max_requests_per_minute

    for _ in range(limit * 2 + 1):
        await client.get("/admin/login", headers=_RATE_TEST_HEADERS)

    response = await client.get("/admin/factor-set/list", headers=_RATE_TEST_HEADERS)

    assert response.status_code != 429


async def test_the_login_page_is_still_refused_to_a_blocked_address(
    client, session, settings
):
    """The exemption is from the rate limit only. A block is an
    administrator's deliberate act, it is per-address rather than a shared
    counter (so it has none of the lockout failure mode the rate limit had),
    and it outranks reaching the login page - which is precisely why
    `python -m admin.cli unblock` exists.
    """
    from db.blocklist import block_ip

    block_ip(session, "127.0.0.1", reason="test", actor="kim",
             secret_key=settings.secret_key)
    session.commit()

    response = await client.get("/admin/login", headers=_RATE_TEST_HEADERS)

    assert response.status_code == 403


async def test_the_refusal_page_does_not_explain_what_tripped_it(client):
    """A caller being refused is not owed the rule they broke - that is a
    free tuning signal. Staff read the reason on the blocklist screen and in
    the audit trail instead."""
    response = await client.get("/admin/login",
                                headers={"user-agent": "curl/8.4.0"})

    body = response.text.lower()
    assert "user-agent" not in body
    assert "curl" not in body


# --- The anti-lockout exemption's four refusal paths, one test each --------
#
# `_is_authenticated_staff`/`_decode_session_cookie` (admin/protection.py) is
# the most security-sensitive code in this file's target - it decides who
# gets past the header and rate checks below it. Each test here builds a
# cookie by hand (never through a real login) that isolates exactly one of
# its checks, and each is written so that removing the corresponding check
# turns this test's 403 into something else (typically a redirect from
# sqladmin's own, separate session check, which is real and unaffected by
# whatever admin/protection.py's own checks do) - never a silent pass.


async def test_a_forged_cookie_earns_no_exemption(client, onboarded_staff):
    """A cookie naming a real, active account and its correct
    session_generation, but never signed with the real SECRET_KEY - what an
    attacker who has read `admin.auth.SESSION_KEY`'s name (public, in this
    very repository) but not the secret could produce unaided. If
    `_decode_session_cookie` ever stopped verifying the signature (e.g.
    became a bare `json.loads(b64decode(raw))`), this exact cookie would
    decode successfully and hand the exemption - and with it, a curl UA's
    way past the header check - to anyone who can read this source file.
    """
    payload = b64encode(json.dumps({
        SESSION_KEY: onboarded_staff.username,
        SESSION_GENERATION_KEY: onboarded_staff.session_generation,
    }).encode("utf-8")).decode("utf-8")
    forged = f"{payload}.not-a-real-signature"

    client.cookies.set("session", forged)
    response = await client.get("/admin/factor-set/list",
                                headers={"user-agent": "curl/8.4.0"})

    assert response.status_code == 403


async def test_an_expired_cookie_earns_no_exemption(client, onboarded_staff, settings):
    """Signed with the real secret key, but with an embedded timestamp far
    enough in the past that `TimestampSigner`'s own `max_age` check rejects
    it. If `_decode_session_cookie` ever called `unsign()` without
    `max_age=` (or otherwise dropped expiry enforcement), a copy of this
    exact cookie would keep granting the exemption long after a real staff
    session would have expired.
    """
    cookie = _sign_session_cookie(
        {SESSION_KEY: onboarded_staff.username,
         SESSION_GENERATION_KEY: onboarded_staff.session_generation},
        secret_key=settings.secret_key,
        signer_cls=_ExpiredTimestampSigner,
    )
    client.cookies.set("session", cookie)

    response = await client.get("/admin/factor-set/list",
                                headers={"user-agent": "curl/8.4.0"})

    assert response.status_code == 403


async def test_a_stale_generation_cookie_earns_no_exemption(client, onboarded_staff, settings):
    """Correctly signed and naming a real, active account - but at a
    `session_generation` the account has since moved past, exactly the
    shape an evicted cookie has (an administrator's MFA reset or password
    change bumps this value - see admin/models.py's `Staff.session_generation`).
    If the generation comparison in `_is_authenticated_staff` were ever
    dropped, this cookie would go on being exempt after the eviction it
    exists to end.
    """
    cookie = _sign_session_cookie(
        {SESSION_KEY: onboarded_staff.username,
         SESSION_GENERATION_KEY: onboarded_staff.session_generation + 1},
        secret_key=settings.secret_key,
    )
    client.cookies.set("session", cookie)

    response = await client.get("/admin/factor-set/list",
                                headers={"user-agent": "curl/8.4.0"})

    assert response.status_code == 403


async def test_a_deactivated_accounts_cookie_earns_no_exemption(
    client, admin_app, onboarded_staff, settings
):
    """Correctly signed and, at the moment it was issued, entirely valid -
    the account behind it has simply been deactivated since. If the
    `is_active` check in `_is_authenticated_staff` were ever dropped, a
    deactivated account's already-issued cookie would go on being exempt
    indefinitely - the same failure `require_staff_username`
    (admin/auth.py) exists to close on the panel's ordinary auth path.
    """
    cookie = _sign_session_cookie(
        {SESSION_KEY: onboarded_staff.username,
         SESSION_GENERATION_KEY: onboarded_staff.session_generation},
        secret_key=settings.secret_key,
    )
    factory = admin_app.state.session_factory
    with factory() as db:
        row = get_staff(db, onboarded_staff.username)
        row.is_active = False
        db.commit()

    client.cookies.set("session", cookie)
    response = await client.get("/admin/factor-set/list",
                                headers={"user-agent": "curl/8.4.0"})

    assert response.status_code == 403


# --- Requests that arrive with no address at all --------------------------
#
# `_client_ip`'s pure-function cases live in tests/admin/test_client_address.py:
# they need no database and no HTTP client, and this module's `pytestmark`
# marks every test in it `db` and `asyncio`, neither of which is true of them.


@pytest_asyncio.fixture
async def clientless_client(admin_app):
    """A client whose requests arrive with no address at all
    (`scope["client"] is None`, which Starlette surfaces as
    `request.client is None`).

    Rare but real - some ASGI transports and test harnesses omit it - and
    until now nothing pinned what the middleware does with it.
    """
    transport = ASGITransport(app=admin_app, client=None)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def test_a_request_with_no_address_skips_the_blocklist_deliberately(
    clientless_client, session, settings
):
    """Not a fail-open choice so much as a logical impossibility: the
    blocklist is keyed on `HMAC(address)`, so with no address there is no
    fingerprint to compute and no row to look up. The alternative - inventing
    a stand-in key - is what an earlier version did with `""`, which
    collapsed every such caller into one shared bucket and one shared
    blocklist entry. The header check below still constrains them.
    """
    from db.blocklist import block_ip

    block_ip(session, "127.0.0.1", reason="test", actor="kim",
             secret_key=settings.secret_key)
    session.commit()

    response = await clientless_client.get("/admin/login",
                                          headers=_RATE_TEST_HEADERS)

    assert response.status_code == 200


async def test_a_request_with_no_address_skips_the_rate_limit(
    clientless_client, settings
):
    """Same reasoning: `RequestRate` is keyed on the fingerprint, and there is
    none. Pinned so that a later change which invents a key for this case -
    and thereby gives every addressless caller one shared bucket - fails
    here rather than shipping."""
    limit = settings.protection_max_requests_per_minute

    for _ in range(limit * 2 + 1):
        response = await clientless_client.get("/admin/factor-set/list",
                                               headers=_RATE_TEST_HEADERS)
        assert response.status_code != 429


async def test_a_request_with_no_address_is_still_header_checked(
    clientless_client
):
    """The one check that does not need an address, and therefore the only
    thing still standing between an addressless caller and the panel. If the
    two skips above were ever widened into "no address, no checks", this is
    the test that catches it."""
    response = await clientless_client.get("/admin/login",
                                           headers={"user-agent": "curl/8.4.0"})

    assert response.status_code == 403
