"""admin.app - the composition root.

Contract: docs/interfaces.md 8.3, 8.4.

The ``admin_app`` and ``client`` fixtures live in tests/conftest.py, shared
with every later task that builds an app and hits the database: each needs
its own app, and each app's engine needs disposing, which a private copy of
these fixtures per test module would either duplicate or forget.
"""

import re
import time
import uuid

import pyotp
import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from admin.accounts import (
    begin_mfa_enrolment,
    complete_mfa_enrolment,
    count_active_admins,
    create_staff,
    set_password,
)
from admin.app import create_app
from admin.bootstrap import BOOTSTRAP_USERNAMES
from admin.config import Settings
from admin.totp import TOTP_INTERVAL
from admin.views import time as views_time
from db.session import create_session_factory
from tests.admin.conftest import _cleanup_staff_named
from tests.conftest import ROOT_URL

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

BOOTSTRAP_DB = "kaicalc_bootstrap_test"
BOOTSTRAP_URL = f"mysql+pymysql://root:devroot@127.0.0.1:3307/{BOOTSTRAP_DB}"

# Matches the value tests/conftest.py's admin_app fixture sets via
# monkeypatch.setenv("SECRET_KEY", ...) - the same convention
# tests/admin/test_flow.py and test_verify_page.py use.
SECRET_KEY = "test-secret-key-not-used-anywhere-real"


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
    """The start-up output has to say what these two lines are worth.

    Pinned against the CLI's own wording rather than a paraphrase: an operator
    following the handover documentation has read the ``python -m admin.cli
    bootstrap`` instructions, and a start-up that says something different is
    a start-up they will not recognise as the same event.

    **The load-bearing sentence changed with contract v1.15 and this test
    changed with it.** It used to be "shown once and cannot be recovered",
    which was true when it was written: bootstrap goes through
    ``create_staff``, which since v1.15 stores each password encrypted until
    the account claims it, so one lost line is recoverable *by the other
    administrator* from /admin/staff. Losing both is still terminal for the
    panel — a reveal needs a signed-in administrator and there is nobody else —
    and the output has to name the way back from that, which is
    ``kaicalc-admin issue-password`` on the container. An operator told the
    flat "cannot be recovered" would rebuild a deployment they could have
    logged in to.
    """
    await _run_startup(empty_database)

    out = capsys.readouterr().out
    assert "Created initial administrator accounts." in out
    for username in BOOTSTRAP_USERNAMES:
        assert re.search(rf"^  {username}: \S{{20}}$", out, re.MULTILINE), out
    assert "cannot be recovered" not in out, (
        "the sentence that stopped being true in v1.15 is back"
    )
    assert "the other administrator can read it back from /admin/staff" in out
    assert "kaicalc-admin issue-password admin" in out
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


# --- Task 6: the audit log view is actually mounted -------------------------


@pytest_asyncio.fixture
async def logged_in_client(admin_app, client, monkeypatch):
    """A client holding a real, fully-authenticated staff session.

    Built by driving the real HTTP login flow with a real account - the same
    approach tests/admin/test_flow.py's walks use - rather than forging a
    session dict, so this proves a route is reachable through
    AuthenticationBackend as a real staff member would reach it, not just
    that the view class exists.
    """
    username = f"u{uuid.uuid4().hex[:10]}"
    password = "a-long-enough-password"
    factory = admin_app.state.session_factory
    with factory() as db:
        create_staff(db, username=username, display_name="Route Check", actor="test", secret_key=SECRET_KEY)
        db.flush()
        set_password(db, username, password)
        secret, _ = begin_mfa_enrolment(db, username, secret_key=SECRET_KEY)
        # Backdated so the login step below, which mints a fresh TOTP code
        # for "now", cannot collide with the counter enrolment just spent -
        # see test_flow.py's `onboarded` fixture for the same reasoning.
        enrol_now = int(time.time()) - 4 * TOTP_INTERVAL
        complete_mfa_enrolment(
            db,
            username,
            pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(enrol_now),
            secret_key=SECRET_KEY,
            now=enrol_now,
        )
        db.commit()

    now = int(time.time())
    monkeypatch.setattr(views_time, "time", lambda: now)

    login = await client.post(
        "/admin/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert login.status_code == 302, "password step should have succeeded"

    verify_page = await client.get("/admin/verify")
    match = re.search(r'name="csrf_token" value="([^"]+)"', verify_page.text)
    assert match, "no CSRF token rendered on /admin/verify"
    code = pyotp.TOTP(secret, interval=TOTP_INTERVAL).at(now)
    verify = await client.post(
        "/admin/verify",
        data={"code": code, "csrf_token": match.group(1)},
        follow_redirects=False,
    )
    assert verify.status_code == 302, "TOTP step should have completed the login"

    yield client
    # This fixture had no teardown at all, and committed both a `staff` row
    # and (since admin/accounts.py::create_staff began auditing itself) an
    # `audit_log` row against a database every other test in the directory
    # shares. The stray audit entry is the more damaging half: four tests in
    # test_audit.py and test_modelviews.py read the first row of that table
    # and assert what it is, so a leftover `create staff` row makes them fail
    # for a reason that has nothing to do with what they check.
    _cleanup_staff_named(admin_app, username)


async def test_the_audit_log_view_is_registered_and_administrator_only(
    logged_in_client,
):
    """403, not 200, and 403 is what "registered" looks like from here.

    `logged_in_client` is a plain `staff` account (create_staff's default
    role), and contract v1.15 closed the audit log to that role - the trail is
    an administrator's oversight tool. This test was written to prove the view
    is *registered* rather than to prove anything about roles, and 403 proves
    that just as well as 200 did: an unregistered identity answers 404 from
    sqladmin's `_find_model_view`, so the two are distinguishable and this
    assertion still fails if `AuditLogAdmin` stops being added in
    `create_app`.

    The role rule itself is held at both roles, over real HTTP, in
    tests/admin/test_role_matrix.py - including the details and export routes,
    which are separate handlers from this one.
    """
    response = await logged_in_client.get("/admin/audit-log/list")

    assert response.status_code == 403
