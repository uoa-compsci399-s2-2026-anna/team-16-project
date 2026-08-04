"""admin.app - the composition root.

Contract: docs/interfaces.md 8.3, 8.4.

The ``admin_app`` and ``client`` fixtures live in tests/conftest.py, shared
with every later task that builds an app and hits the database: each needs
its own app, and each app's engine needs disposing, which a private copy of
these fixtures per test module would either duplicate or forget.
"""

import pytest

pytestmark = [pytest.mark.db, pytest.mark.asyncio]


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


async def test_the_throttle_is_a_single_shared_instance(admin_app):
    """A per-request throttle counts nothing - every request would start a
    fresh counter and the lockout would never trigger.

    Pins the throttle a view would actually reach - through the inner
    sqladmin Starlette application that ``request.app`` resolves to inside
    a view (see admin/runtime.py) - against the one the outer app holds,
    rather than comparing an attribute to itself.
    """
    from admin.throttle import LoginThrottle

    admin_mount = next(r for r in admin_app.routes if r.name == "admin")
    inner_app = admin_mount.app

    assert inner_app.state.runtime.throttle is admin_app.state.throttle
    assert isinstance(admin_app.state.throttle, LoginThrottle)
