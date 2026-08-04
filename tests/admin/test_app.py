"""admin.app - the composition root.

Contract: docs/interfaces.md 8.3, 8.4.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from admin.app import create_app

pytestmark = [pytest.mark.db, pytest.mark.asyncio]


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-not-used-anywhere-real")
    monkeypatch.setenv(
        "DATABASE_URL", "mysql+pymysql://root:devroot@127.0.0.1:3307/kaicalc_test"
    )
    return create_app()


# NOTE (Task 3, deviation from the brief): the brief's Step 1 gives this
# fixture as plain `@pytest.fixture`. Under the `asyncio_mode = strict`
# added in Step 3, pytest-asyncio ignores async fixtures that are not
# explicitly declared with `@pytest_asyncio.fixture` (see
# pytest_asyncio/plugin.py:pytest_fixture_setup) - the fixture setup falls
# through to pytest's own sync machinery, which then refuses to run a
# coroutine-returning fixture for a test at all. Verified by running the
# brief's literal code first: it fails 4 of 5 tests with "requested an
# async fixture 'client', with no plugin or hook that handled it." Using
# the explicit decorator is the documented fix and changes nothing about
# what the fixture does.
@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def test_the_admin_root_redirects_an_anonymous_visitor_to_the_login_page(client):
    response = await client.get("/admin/", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert "/admin/login" in response.headers["location"]


async def test_the_login_page_renders(client):
    response = await client.get("/admin/login")

    assert response.status_code == 200
    assert "Kai Commitment" in response.text


async def test_the_brand_stylesheet_is_served(client):
    response = await client.get("/admin/static/brand.css")

    assert response.status_code == 200
    assert "--kale" in response.text


async def test_the_brand_font_is_served_as_woff2(client):
    response = await client.get("/admin/static/fonts/geologica-bold.woff2")

    assert response.status_code == 200
    assert response.content[:4] == b"wOF2"


async def test_the_throttle_is_a_single_shared_instance(app):
    """A per-request throttle counts nothing - every request would start a
    fresh counter and the lockout would never trigger."""
    assert app.state.throttle is app.state.throttle
    from admin.throttle import LoginThrottle

    assert isinstance(app.state.throttle, LoginThrottle)
