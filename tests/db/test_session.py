"""db.session - engine and session factory.

Owner: B. Tested here because admin/cli.py builds its session through it.
"""

import uuid

import pytest
from sqlalchemy import create_engine, delete, select, text

import admin.models  # noqa: F401  - registers tables on Base.metadata
from admin.models import Staff, StaffRole
from db.base import Base
from db.session import create_session_factory

pytestmark = pytest.mark.db

TEST_URL = "mysql+pymysql://root:devroot@127.0.0.1:3307/kaicalc_test"


def test_the_factory_produces_a_usable_session():
    factory = create_session_factory(TEST_URL)

    with factory() as db_session:
        assert db_session.scalar(select(text("1"))) == 1


def test_committed_objects_stay_readable_without_a_refetch():
    """expire_on_commit=False. The admin panel reads attributes off an
    object after committing it. With the default expire_on_commit=True,
    commit() expires every attribute, and reading one back from a session
    that has since closed raises DetachedInstanceError instead of returning
    the value already held in memory - there is no connection left to
    refresh from.

    A raw text() query after commit would not exercise this at all: Core
    rows are never subject to expire_on_commit, only ORM-mapped instances
    are. So this keeps a handle to the mapped Staff instance itself and
    reads an attribute off it after the session that committed it has
    closed.
    """
    setup_engine = create_engine(TEST_URL, future=True)
    Base.metadata.create_all(setup_engine)
    setup_engine.dispose()

    factory = create_session_factory(TEST_URL)
    username = f"probe-{uuid.uuid4().hex}"

    with factory() as db_session:
        staff = Staff(
            username=username,
            display_name="Probe",
            password_hash="x" * 20,
            role=StaffRole.staff,
        )
        db_session.add(staff)
        db_session.commit()

    # The session is closed - no connection remains to refresh an expired
    # attribute from. This only succeeds because commit() did not expire it.
    assert staff.username == username

    with factory() as db_session:
        db_session.execute(delete(Staff).where(Staff.username == username))
        db_session.commit()
