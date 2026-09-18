"""Database fixtures, and the admin app/client fixtures built on top of them.

These tests need a real MySQL: ENUM, VARBINARY and DATETIME all behave
differently on SQLite, and a model that only passes against SQLite proves
nothing about production.

    docker compose up -d

Run without them:  python -m pytest -m "not db"
"""

from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tests.support import mysql_lock

#: B's SQLite fixtures - `sqlite_engine`, `seeded_session` and `app`. Registered
#: here rather than as a `tests/db/conftest.py` so that a test file's directory
#: does not silently decide which database engine it runs against: everything
#: under tests/db/ was otherwise half MySQL (test_blocklist, test_session, on
#: the fixtures below) and half SQLite. Registration from the root is only
#: possible because those two fixtures were renamed off `engine`/`session`
#: during the integration of B's branch - the names this module already uses.
pytest_plugins = ["tests.support.sqlite"]
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Base.metadata must be complete before the `engine` fixture below calls
# drop_all()/create_all(): drop_all() computes its drop order from whatever
# is registered here, and a model module nobody imported is invisible to it.
# If a table from an unregistered module still exists in the database (e.g.
# left over from a previous run) but references, or is referenced by, a
# table that *is* registered, drop_all() can order the drops wrongly and
# fail on a foreign key. Import every admin model module here, not just the
# one that happens to get pulled in transitively by `admin.app` - the next
# person adding a model module needs to add its import here too.
import admin.comparison_models  # noqa: F401  - registers the comparison tables
import admin.factor_models  # noqa: F401  - registers the six factor tables
import admin.models  # noqa: F401  - registers staff, staff_recovery_code, audit_log
import admin.taxonomy_models  # noqa: F401  - registers the seven taxonomy tables
import db.blocklist_models  # noqa: F401  - registers ip_block on Base.metadata
import db.models  # noqa: F401  - registers the three submission tables
from admin.app import create_app
from db.base import Base

ROOT_URL = "mysql+pymysql://root:devroot@127.0.0.1:3307/"
TEST_DB = "kaicalc_test"
TEST_URL = f"mysql+pymysql://root:devroot@127.0.0.1:3307/{TEST_DB}"

_TESTS_DIR = Path(__file__).resolve().parent

#: The open lock handle for this session, if `pytest_collection_modifyitems`
#: below acquired one - module-level rather than on `config`/`session`
#: because `pytest_sessionfinish` needs it back and neither object is a
#: reliable place to stash arbitrary state across pytest versions.
_mysql_lock_handle = None


def _shares_kaicalc_test_mysql(item) -> bool:
    """True for a collected item under tests/api, tests/db or tests/admin.

    Those three directories are the ones that actually run against the
    shared `kaicalc_test` MySQL database (see the module docstring above and
    `tests/support/mysql_lock.py`). tests/web, tests/golden, tests/benchmark
    and the root-level test_*.py files do not, and a run confined to them
    should never so much as touch the lock file.
    """
    try:
        rel = item.path.relative_to(_TESTS_DIR)
    except ValueError:  # pragma: no cover - defensive; every item is under tests/
        return False
    return len(rel.parts) > 1 and rel.parts[0] in {"api", "db", "admin"}


def pytest_collection_modifyitems(session, config, items):
    """Refuse to start, immediately and loudly, if another pytest session
    already holds the lock on the shared `kaicalc_test` MySQL database.

    This runs once, after collection and before the first test executes -
    `mysql_lock.acquire()` never blocks, so a second session is turned away
    within the same second it was started rather than left to race the
    first one to a `drop_all()` or a fixture's teardown. See
    `tests/support/mysql_lock.py` for why a lock file rather than a MySQL
    advisory lock, and why the file lives outside this (iCloud-synced)
    checkout.
    """
    global _mysql_lock_handle
    if not any(_shares_kaicalc_test_mysql(item) for item in items):
        return
    try:
        _mysql_lock_handle = mysql_lock.acquire()
    except mysql_lock.DatabaseLockHeld as exc:
        pytest.exit(str(exc), returncode=1)


def pytest_sessionfinish(session, exitstatus):
    global _mysql_lock_handle
    mysql_lock.release(_mysql_lock_handle)
    _mysql_lock_handle = None


@pytest.fixture(scope="session")
def database_url_root() -> str:
    """The root database URL, used to create and drop a scratch database.

    Reuses the same root credentials as the `engine` fixture below rather
    than reading the application's DATABASE_URL: the `kaicalc` MySQL user
    that setting resolves to is only granted privileges on the `kaicalc`
    database (MYSQL_USER/MYSQL_DATABASE in docker-compose.yml), not on an
    arbitrary scratch database. A fixture that tried to CREATE/DROP one as
    that user would fail with Access denied on a fresh `docker compose up
    -d`, before ever reaching Alembic.
    """
    return ROOT_URL


@pytest.fixture(scope="session")
def engine():
    root = create_engine(ROOT_URL, future=True, isolation_level="AUTOCOMMIT")
    with root.connect() as conn:
        conn.execute(
            text(
                f"CREATE DATABASE IF NOT EXISTS {TEST_DB} "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        )
    root.dispose()

    eng = create_engine(TEST_URL, future=True)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    """A session inside a transaction that is rolled back after the test.

    Rollback rather than truncation: it is faster, and it means one test can
    never leave a row behind that another test accidentally depends on.
    """
    connection = engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(bind=connection, future=True, expire_on_commit=False)
    db_session = factory()
    try:
        yield db_session
    finally:
        db_session.close()
        # A flush error (e.g. IntegrityError from a uniqueness violation)
        # makes the ORM roll back this same connection's transaction as
        # part of its own error handling, deactivating ``transaction``
        # before we get here. Rolling back an already-deactivated
        # transaction is a harmless no-op at the database level but SQLAlchemy
        # emits SAWarning for it, so guard on ``is_active`` rather than
        # calling it unconditionally.
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture(scope="session", autouse=True)
def _protection_off_by_default():
    """``PROTECTION_ENABLED=false`` for every ``create_app()`` call this test
    session makes, however the app is built.

    ``admin/protection.py``'s ProtectionMiddleware ships on by default
    (``admin.config.Settings.protection_enabled``), and almost nothing in
    this suite is testing it - ``tests/admin/test_protection.py`` is the one
    file that is. Every other fixture (``client``, ``admin_client``,
    ``staff_client``, the ``_login()`` helper in tests/admin/conftest.py) and
    every hand-rolled app builder (e.g.
    ``tests/admin/test_session_cookie.py``'s own ``_build_app``, which does
    not go through ``admin_app`` below - see its module docstring for why)
    was written before that middleware existed and sends requests with a
    plain httpx client, whose default User-Agent is itself one of the
    strings ``admin.detection.looks_automated`` flags. Left at the shipped
    default, those all fail in a way that gives no hint why - a 403, or in
    ``test_session_cookie.py``'s case a login that silently set no cookie at
    all - since nothing about that failure names "protection" anywhere in
    it.

    An earlier version of this fixture set the env var only from inside
    ``admin_app`` below, keyed off a small per-test override fixture. That
    covered every app built *through* ``admin_app``, but nothing else -
    ``test_session_cookie.py`` calls ``create_app()`` directly and never
    requests ``admin_app`` at all, so it fell straight through to the
    shipped ``True`` and failed for exactly the reason described above. This
    version sets the environment once, for the whole session, before any
    test's own fixtures run - so it reaches ``create_app()`` no matter how a
    given test module calls it, present or future (E-9 and E-10 will add
    more test modules that build their own app).

    Session-scoped, so the built-in ``monkeypatch`` fixture (function-scoped
    only) cannot be used directly - there is no session-scoped fixture of it
    in pytest, but ``pytest.MonkeyPatch`` is itself public API precisely for
    this: constructing one directly and calling ``.undo()`` (here via
    ``.context()``'s contextmanager form) at session teardown is pytest's own
    documented pattern for patching at a wider-than-function scope.

    ``tests/admin/test_protection.py`` overrides this back to "true" for its
    own tests, via a local ``admin_app`` override rather than a second
    ``autouse`` fixture running alongside this one - see that file's own
    ``admin_app`` docstring for why an independent ``autouse`` companion
    fixture does not reliably win the race against this one.
    ``tests/admin/test_config.py`` separately asserts that
    ``admin.config.Settings``' own shipped default is True, read directly off
    the dataclass field rather than through any fixture or environment
    variable - the guard against this session-wide override ever being
    mistaken for what ships.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("PROTECTION_ENABLED", "false")
        yield


@pytest.fixture
def admin_app(monkeypatch):
    """A freshly built admin app, backed by its own engine.

    Tasks 4-8 each build an app per test. ``create_app`` calls
    ``create_session_factory``, which calls ``create_engine`` fresh every
    time, and nothing disposes it on its own - harmless for the one engine
    a real process holds for its whole lifetime, but a leak once tests are
    doing this at volume. The teardown here disposes it; the engine is
    reachable off the session factory as ``factory.kw["bind"]``.

    Does not touch ``PROTECTION_ENABLED`` itself - ``_protection_off_by_default``
    above already sets it for the whole session before this fixture, or
    anything else in the suite, ever runs.
    """
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-not-used-anywhere-real")
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    # AsyncClient/ASGITransport talks to the app over plain http, and
    # SESSION_HTTPS_ONLY defaults to true (admin/config.py) - a Secure
    # session cookie set in response to an http:// request is one real
    # browsers and httpx's cookie jar alike will not send back, which would
    # break every test that relies on a session surviving more than one
    # request.
    monkeypatch.setenv("SESSION_HTTPS_ONLY", "false")
    app = create_app()
    yield app
    app.state.session_factory.kw["bind"].dispose()


@pytest_asyncio.fixture
async def client(admin_app):
    transport = ASGITransport(app=admin_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
