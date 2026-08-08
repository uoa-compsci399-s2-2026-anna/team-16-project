"""Database fixtures, and the admin app/client fixtures built on top of them.

These tests need a real MySQL: ENUM, VARBINARY and DATETIME all behave
differently on SQLite, and a model that only passes against SQLite proves
nothing about production.

    docker compose up -d

Run without them:  python -m pytest -m "not db"
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
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
import admin.taxonomy_models  # noqa: F401  - registers the six taxonomy tables
import db.blocklist_models  # noqa: F401  - registers ip_block on Base.metadata
from admin.app import create_app
from db.base import Base

ROOT_URL = "mysql+pymysql://root:devroot@127.0.0.1:3307/"
TEST_DB = "kaicalc_test"
TEST_URL = f"mysql+pymysql://root:devroot@127.0.0.1:3307/{TEST_DB}"


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


@pytest.fixture
def _protection_default_for_tests() -> str:
    """The ``PROTECTION_ENABLED`` value ``admin_app`` builds its app with.

    "false" for the whole suite. ``admin/protection.py``'s ProtectionMiddleware
    is a deployment concern - it inspects headers, rate-limits and consults
    the IP blocklist on every request - and defaulting it on here would mean
    every other file's fixtures (``client``, ``admin_client``, ``staff_client``,
    the ``_login()`` helper in tests/admin/conftest.py) start failing at
    whichever request happens to run before a session cookie exists, purely
    because httpx's own default User-Agent is one of the strings
    ``admin.detection.looks_automated`` flags as a scripting tool. None of
    those files are testing protection; they were written before it existed
    and shouldn't have to know it does.

    ``tests/admin/test_protection.py`` is the one file that must exercise the
    real thing, so it overrides this fixture to "true" for its own app
    instances - see that file. ``tests/admin/test_config.py`` separately
    asserts that ``admin.config.Settings``' own shipped default is True,
    straight off the dataclass field rather than through any fixture or env
    var - the guard against this override leaking into what actually ships.
    """
    return "false"


@pytest.fixture
def admin_app(monkeypatch, _protection_default_for_tests):
    """A freshly built admin app, backed by its own engine.

    Tasks 4-8 each build an app per test. ``create_app`` calls
    ``create_session_factory``, which calls ``create_engine`` fresh every
    time, and nothing disposes it on its own - harmless for the one engine
    a real process holds for its whole lifetime, but a leak once tests are
    doing this at volume. The teardown here disposes it; the engine is
    reachable off the session factory as ``factory.kw["bind"]``.
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
    # See _protection_default_for_tests just above for why this is not
    # simply left at admin/config.py's own shipped default.
    monkeypatch.setenv("PROTECTION_ENABLED", _protection_default_for_tests)
    app = create_app()
    yield app
    app.state.session_factory.kw["bind"].dispose()


@pytest_asyncio.fixture
async def client(admin_app):
    transport = ASGITransport(app=admin_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
