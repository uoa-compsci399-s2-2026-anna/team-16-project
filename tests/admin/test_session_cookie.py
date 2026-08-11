"""The session cookie's security attributes, read off the wire.

Contract: docs/interfaces.md 8.3.

Every other test in this suite runs behind ``tests/conftest.py``'s
``admin_app`` fixture, which sets ``SESSION_HTTPS_ONLY=false`` so that a
Secure cookie set in response to httpx's plain-http request is not dropped
by the cookie jar. The consequence is that the production value of that
setting is never exercised end to end: ``tests/admin/test_config.py`` pins
the *parsing* of the environment variable, and nothing pins what actually
reaches the browser. Hardcoding ``https_only=False`` in
``AdminAuth.__init__`` left all 314 tests green.

So this module builds its own app, deliberately **not** inheriting
conftest's override — the shared fixture is what every other module depends
on and must keep the override — and asserts on the ``Set-Cookie`` header a
real login produces, not on ``Middleware(SessionMiddleware).kwargs``. An
assertion against the middleware object would pass just as happily if
Starlette stopped honouring the argument.
"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from admin.accounts import create_staff, set_password
from admin.app import create_app
from tests.admin.conftest import _cleanup_staff_named
from tests.conftest import TEST_URL

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

SECRET_KEY = "test-secret-key-not-used-anywhere-real"
PASSWORD = "a-long-enough-password"


def _build_app(monkeypatch, https_only: str):
    monkeypatch.setenv("SECRET_KEY", SECRET_KEY)
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    monkeypatch.setenv("SESSION_HTTPS_ONLY", https_only)
    return create_app()


@pytest.fixture
def secure_app(monkeypatch):
    """An app built the way production builds it: SESSION_HTTPS_ONLY unset.

    Unset rather than "true" - the default is what a deployment that never
    writes the variable gets, and the default is the thing worth pinning.
    """
    monkeypatch.delenv("SESSION_HTTPS_ONLY", raising=False)
    monkeypatch.setenv("SECRET_KEY", SECRET_KEY)
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    app = create_app()
    yield app
    app.state.session_factory.kw["bind"].dispose()


@pytest.fixture
def insecure_app(monkeypatch):
    app = _build_app(monkeypatch, "false")
    yield app
    app.state.session_factory.kw["bind"].dispose()


@pytest.fixture
def account(secure_app):
    """An account past the forced password change, in the shared test DB.

    Both apps in this module point at the same database, so one fixture
    serves either.
    """
    username = f"u{uuid.uuid4().hex[:10]}"
    factory = secure_app.state.session_factory
    with factory() as db:
        create_staff(db, username=username, display_name="Test User", actor="test", secret_key=SECRET_KEY)
        db.flush()
        set_password(db, username, PASSWORD)
        db.commit()
    yield username
    # See tests/admin/test_enrol_page.py's `pending` fixture for why this is
    # conftest's helper rather than a bare `DELETE FROM staff`.
    _cleanup_staff_named(secure_app, username)


async def _login_set_cookie(app, username: str) -> str:
    """Perform a real password step and return its Set-Cookie header.

    A login is what first writes to the session, and Starlette's
    SessionMiddleware only emits Set-Cookie when the session was modified -
    so this is the earliest request that can carry the header at all.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        response = await c.post(
            "/admin/login",
            data={"username": username, "password": PASSWORD},
            follow_redirects=False,
        )
    headers = response.headers.get_list("set-cookie")
    assert headers, f"the login step set no cookie at all (status {response.status_code})"
    session_cookies = [h for h in headers if h.startswith("session=")]
    assert len(session_cookies) == 1, headers
    return session_cookies[0]


def _attributes(set_cookie: str) -> set[str]:
    """The cookie's attributes, lowercased, without its name=value pair.

    Parsed into a set of whole attributes rather than substring-matched:
    ``"secure" in header`` would also match a cookie *value* that happened
    to contain those six characters, and the negative test below depends on
    that distinction being real.
    """
    return {part.strip().lower() for part in set_cookie.split(";")[1:]}


async def test_the_session_cookie_is_secure_httponly_and_samesite_lax(
    secure_app, account
):
    """The three attributes together, off the wire.

    ``secure`` is the one Task 4's review round 1 raised (Finding 4) and the
    one no test has exercised since: an admin session cookie without it is
    handed over by a single http:// navigation on the admin subdomain.
    ``httponly`` keeps it out of ``document.cookie``, and ``samesite=lax``
    is what stops a cross-site POST carrying it.
    """
    set_cookie = await _login_set_cookie(secure_app, account)

    attributes = _attributes(set_cookie)
    assert "secure" in attributes, set_cookie
    assert "httponly" in attributes, set_cookie
    assert "samesite=lax" in attributes, set_cookie


async def test_the_session_cookie_expires_at_the_configured_max_age(
    secure_app, account
):
    """SESSION_MAX_AGE_MINUTES reaching the cookie, not just the Settings object.

    Same blind spot as the flags above: ``max_age`` is a constructor
    argument, so nothing branches on it and no existing test dies if it is
    dropped. Without it the cookie becomes a session cookie that outlives
    the eight-hour bound the contract's eviction limitation relies on.
    """
    set_cookie = await _login_set_cookie(secure_app, account)

    assert "max-age=28800" in _attributes(set_cookie), set_cookie


async def test_setting_https_only_false_drops_secure_and_keeps_the_rest(
    insecure_app, account
):
    """The negative half, so the positive test above cannot pass vacuously.

    SESSION_HTTPS_ONLY=false is the documented local-development escape
    hatch (admin/config.py, .env.example) and this suite's own fixture
    depends on it working. It must drop ``secure`` and nothing else - a
    change that dropped ``httponly`` or ``samesite`` along with it would
    weaken every test-suite session and the local development panel.
    """
    set_cookie = await _login_set_cookie(insecure_app, account)

    attributes = _attributes(set_cookie)
    assert "secure" not in attributes, set_cookie
    assert "httponly" in attributes, set_cookie
    assert "samesite=lax" in attributes, set_cookie
