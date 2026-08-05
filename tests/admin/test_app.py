"""admin.app - the composition root.

Contract: docs/interfaces.md 8.3, 8.4.

The ``admin_app`` and ``client`` fixtures live in tests/conftest.py, shared
with every later task that builds an app and hits the database: each needs
its own app, and each app's engine needs disposing, which a private copy of
these fixtures per test module would either duplicate or forget.
"""

import re

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from admin.accounts import count_active_admins
from admin.app import create_app
from admin.bootstrap import BOOTSTRAP_USERNAMES
from admin.config import Settings
from db.session import create_session_factory
from tests.conftest import ROOT_URL

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

BOOTSTRAP_DB = "kaicalc_bootstrap_test"
BOOTSTRAP_URL = f"mysql+pymysql://root:devroot@127.0.0.1:3307/{BOOTSTRAP_DB}"


def _settings(database_url: str) -> Settings:
    return Settings(
        secret_key="test-secret-key-not-used-anywhere-real",
        database_url=database_url,
        session_max_age_minutes=480,
        login_max_failures=5,
        login_lockout_minutes=15,
        session_https_only=False,
    )


def _drop_bootstrap_db() -> None:
    root = create_engine(ROOT_URL, future=True, isolation_level="AUTOCOMMIT")
    with root.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {BOOTSTRAP_DB}"))
    root.dispose()


@pytest.fixture
def empty_database():
    """A database of its own, schema created, not one row in ``staff``.

    Deliberately not the shared test database. The startup hook under test
    creates two administrator accounts and prints their passwords, and
    doing that in the database every other test shares would leave two
    accounts behind for whatever ran next.
    """
    from db.base import Base

    _drop_bootstrap_db()
    root = create_engine(ROOT_URL, future=True, isolation_level="AUTOCOMMIT")
    with root.connect() as conn:
        conn.execute(
            text(
                f"CREATE DATABASE {BOOTSTRAP_DB} "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        )
    root.dispose()

    engine = create_engine(BOOTSTRAP_URL, future=True)
    Base.metadata.create_all(engine)
    engine.dispose()
    yield BOOTSTRAP_URL
    _drop_bootstrap_db()


async def _run_startup(database_url: str) -> None:
    """Build an app and take it through its startup, as uvicorn would.

    ``create_app()`` alone runs nothing: a lifespan hook fires only when
    something drives the ASGI lifespan protocol. httpx's ASGITransport - the
    only thing this suite uses - hard-codes ``{"type": "http"}`` and never
    does, which is why every other test in the suite builds apps freely
    without triggering a bootstrap. Entering the lifespan context directly
    is what an ordinary test cannot reach by accident.
    """
    app = create_app(_settings(database_url))
    try:
        async with app.router.lifespan_context(app):
            pass
    finally:
        app.state.session_factory.kw["bind"].dispose()


def _admin_count(database_url: str) -> int:
    factory = create_session_factory(database_url)
    try:
        with factory() as db:
            return count_active_admins(db)
    finally:
        factory.kw["bind"].dispose()


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


# --- Startup bootstrap --------------------------------------------------
#
# Contract 8.3, "Bootstrap": the trigger is application start, not an
# operator remembering to run the CLI twice. Without this the deployment
# documented in the contract comes up as a panel nobody can enter.


async def test_startup_creates_two_administrators_on_an_empty_database(empty_database):
    """One administrator is a deployment one lost phone can lock out, which
    is why the contract's floor is two and why the bootstrap creates both."""
    await _run_startup(empty_database)

    assert _admin_count(empty_database) == 2
    factory = create_session_factory(empty_database)
    try:
        with factory() as db:
            names = set(db.scalars(text("SELECT username FROM staff")).all())
    finally:
        factory.kw["bind"].dispose()
    assert names == set(BOOTSTRAP_USERNAMES)


async def test_startup_prints_the_credentials_in_the_cli_bootstrap_wording(
    empty_database, capsys
):
    """The passwords exist in readable form exactly once, in this output.

    Pinned against the CLI's own wording rather than a paraphrase: an
    operator following the handover documentation has read the ``python -m
    admin.cli bootstrap`` instructions, and a start-up that says something
    different is a start-up they will not recognise as the same event. The
    "shown once and cannot be recovered" sentence is the load-bearing part -
    output that does not say it invites someone to close the terminal.
    """
    await _run_startup(empty_database)

    out = capsys.readouterr().out
    assert "Created initial administrator accounts." in out
    for username in BOOTSTRAP_USERNAMES:
        assert re.search(rf"^  {username}: \S{{20}}$", out, re.MULTILINE), out
    assert "These passwords are shown once and cannot be recovered." in out
    assert "Do not send them by email." in out


async def test_a_restart_against_a_populated_database_creates_nothing(
    empty_database, capsys
):
    """Idempotence, and the reason the whole existing suite stays silent.

    Every other test builds an app against a database that already holds
    accounts, so ensure_bootstrap_admins returns [] - and an empty list must
    print nothing at all, not even "nothing to do". Credentials appearing in
    test output would be noise at best and a leak at worst.
    """
    await _run_startup(empty_database)
    capsys.readouterr()

    await _run_startup(empty_database)

    assert _admin_count(empty_database) == 2
    assert capsys.readouterr().out == ""


async def test_startup_fails_loudly_when_the_database_is_unreachable():
    """Not a silent skip.

    A try/except around the bootstrap would turn "the database is down" into
    a panel that starts, serves a login page, and refuses every credential -
    the hardest possible failure to diagnose from the outside, and one a
    small charity's operator has no way to distinguish from a forgotten
    password.
    """
    unreachable = "mysql+pymysql://root:devroot@127.0.0.1:3399/nothing_here"

    with pytest.raises(OperationalError):
        await _run_startup(unreachable)
