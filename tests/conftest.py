"""Database fixtures.

These tests need a real MySQL: ENUM, VARBINARY and DATETIME all behave
differently on SQLite, and a model that only passes against SQLite proves
nothing about production.

    docker compose up -d

Run without them:  python -m pytest -m "not db"
"""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import admin.models  # noqa: F401  - registers the tables on Base.metadata
from db.base import Base

ROOT_URL = "mysql+pymysql://root:devroot@127.0.0.1:3307/"
TEST_DB = "kaicalc_test"
TEST_URL = f"mysql+pymysql://root:devroot@127.0.0.1:3307/{TEST_DB}"


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
