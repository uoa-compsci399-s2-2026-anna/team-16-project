"""Contract §8.1: every admin CRUD write produces an audit entry."""

import asyncio
import inspect
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from admin.accounts import create_staff
from admin.auth import SESSION_KEY
from admin.models import AuditLog, Staff, StaffRecoveryCode
from admin.modelviews import AuditLogAdmin, AuditedModelView

pytestmark = pytest.mark.db


def run(result):
    """Call a sqladmin CRUD hook regardless of whether it is async.

    sqladmin made insert_model / update_model / delete_model coroutines in
    0.16. Calling a coroutine without awaiting it returns the coroutine and
    runs nothing — the assertions below would then all fail against a correct
    implementation, and the obvious "fix" is to weaken them. This keeps the
    tests honest on either version.
    """
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


@pytest.fixture()
def request_of():
    """A minimal stand-in for the Starlette request AuditedModelView reads.

    Only the session dict is touched, so a namespace is enough — building a
    real Request would be testing Starlette rather than our logic.
    """

    def build(*, actor: str):
        return SimpleNamespace(session={SESSION_KEY: actor})

    return build


@pytest.fixture()
def audited_view(session):
    """An AuditedModelView over Staff, sharing `session`'s connection.

    A genuine sessionmaker, not a fake - AuditedModelView.__init__ reads the
    whole `.kw` of whatever sqladmin assigned to build its own
    listener-carrying sessionmaker (see admin/modelviews.py's
    _audited_session_maker), and needs a real sessionmaker to copy from.
    Bound to `session.get_bind()` - the same Connection `session` itself
    uses - so writes made through this view are visible to `session`'s own
    queries in the tests below, and are undone along with everything else
    when that outer transaction rolls back at teardown. This does *not* give
    the audited write its own real, independently-rollback-able SAVEPOINT:
    SQLAlchemy's default `conditional_savepoint` join mode only issues one
    for a second session sharing a connection if that connection is already
    inside a nested transaction (`session.begin_nested()` +
    `session.connection()`) at the moment the second session begins - this
    fixture does neither, so it silently falls back to `"rollback_only"`. See
    tests/admin/test_taxonomy_rules.py's `standard_mix_guarded_view` fixture
    for the pair that actually forces a real SAVEPOINT, and for the mismatch
    this leaves between the two fixtures' docstrings if only one of them is
    ever updated.

    `autoflush=False` mirrors production: sqladmin's own `Admin.__init__`
    (sqladmin/application.py) configures the app's real, shared sessionmaker
    with it, and `_audited_session_maker` is specifically relied on to carry
    that setting into the wrapped one it builds - a fixture that left
    autoflush at SQLAlchemy's default would not exercise that at all.

    The class attribute is assigned before construction, mirroring
    sqladmin's own registration order (Admin.add_model_view sets
    `session_maker` on the class, then constructs the one instance) - that
    ordering is exactly what AuditedModelView.__init__ relies on.
    """

    class StaffTestView(AuditedModelView, model=Staff):
        pass

    StaffTestView.session_maker = sessionmaker(
        bind=session.get_bind(), future=True, expire_on_commit=False,
        autoflush=False,
    )
    return StaffTestView()


@pytest.fixture()
def existing_row(session):
    staff, _ = create_staff(
        session, username="original", display_name="Original", actor="setup"
    )
    session.flush()
    return staff


@pytest.fixture()
def existing_row_with_recovery_code(session):
    """A Staff row plus one already-flushed StaffRecoveryCode belonging to it.

    Lets a test submit a real ONETOMANY relationship field (recovery_codes)
    the way sqladmin's own attribute-setting code handles one - see
    test_a_relation_touching_update_still_writes_an_audit_row.
    """
    staff, _ = create_staff(
        session, username="withcode", display_name="Original", actor="setup"
    )
    session.flush()
    code = StaffRecoveryCode(staff_id=staff.id, code_hash="a" * 64)
    session.add(code)
    session.flush()
    return staff, code


def test_the_audit_log_view_permits_no_writes():
    """Contract §8.1 hard-codes this. An editable audit trail is not one."""
    assert AuditLogAdmin.can_create is False
    assert AuditLogAdmin.can_edit is False
    assert AuditLogAdmin.can_delete is False


def test_an_insert_is_recorded_with_no_before_state(session, audited_view, request_of):
    # Staff.username and Staff.password_hash are NOT NULL with no default, so
    # a valid insert has to carry them even though the assertions below only
    # care about display_name.
    run(audited_view.insert_model(
        request_of(actor="kim"),
        {"username": "newrow", "password_hash": "x", "display_name": "New Row"},
    ))
    session.flush()

    entry = session.scalar(select(AuditLog))
    assert entry.action == "create"
    assert entry.actor == "kim"
    assert entry.before_json is None
    assert entry.after_json["display_name"] == "New Row"


def test_an_update_records_the_state_from_before_the_write(
    session, audited_view, existing_row, request_of
):
    """The property the contract calls out by name.

    "AuditedModelView captures the pre-change row in the before-write hook —
    the after-write hook only ever sees the new values." An implementation
    that read the row in the after-hook would record the new value twice,
    and the audit trail would show every update as a no-op.
    """
    run(audited_view.update_model(
        request_of(actor="kim"), pk=existing_row.id, data={"display_name": "Renamed"}
    ))
    session.flush()

    entry = session.scalar(select(AuditLog).where(AuditLog.action == "update"))
    assert entry.before_json["display_name"] == "Original"
    assert entry.after_json["display_name"] == "Renamed"


def test_a_delete_records_what_was_removed(
    session, audited_view, existing_row, request_of
):
    run(audited_view.delete_model(request_of(actor="kim"), pk=existing_row.id))
    session.flush()

    entry = session.scalar(select(AuditLog).where(AuditLog.action == "delete"))
    assert entry.before_json["display_name"] == "Original"
    assert entry.after_json is None


def test_the_actor_comes_from_the_session_not_the_form(
    session, audited_view, request_of
):
    """An actor a client could set is not an audit trail."""
    run(audited_view.insert_model(
        request_of(actor="kim"),
        {
            "username": "clientset",
            "password_hash": "x",
            "display_name": "X",
            "actor": "someone-else",
        },
    ))
    session.flush()

    assert session.scalar(select(AuditLog)).actor == "kim"


def test_a_failed_write_leaves_no_audit_row(session, audited_view, request_of):
    """Contract §5.5, verbatim: "a change that is rolled back must leave no
    audit record claiming it happened."

    Staff.username and Staff.password_hash are NOT NULL with no default, so
    omitting both makes the underlying INSERT fail - a real IntegrityError
    from MySQL, not a mocked failure. Now that the audit entry is written by
    a before_commit listener sharing the write's own transaction (see
    admin/modelviews.py's module docstring), this is the test that would
    catch a listener that fired anyway, or fired against a session that
    survived the failed commit - the four tests above only ever exercise the
    success path and would not notice either bug.
    """
    with pytest.raises(IntegrityError):
        run(audited_view.insert_model(
            request_of(actor="kim"), {"display_name": "Never Persisted"},
        ))

    assert session.scalar(select(AuditLog)) is None


def test_a_relation_touching_update_still_writes_an_audit_row(
    session, audited_view, existing_row_with_recovery_code, request_of
):
    """Regression test: autoflush must stay off on the audited sessionmaker.

    sqladmin's own attribute-setting code (`_set_attributes_sync` in
    sqladmin/_queries.py) issues a `session.execute(select(...))` for every
    relationship field a submitted form touches - here, `recovery_codes`, a
    real ONETOMANY relationship on Staff. `display_name` is set first in
    `data` below, exactly as it would be from a real edit form whose fields
    happen to be declared in this order.

    If the audited sessionmaker's autoflush is left at SQLAlchemy's default
    (True) rather than carried over from the real one, that `SELECT` for
    `recovery_codes` silently flushes the pending `display_name` change
    first - before the before_commit listener ever runs. Once flushed, the
    object is no longer "dirty" and its attribute history is gone, so the
    listener's `session.dirty` scan finds nothing for Staff and writes no
    audit row at all - not a wrong one, none. This is what caught it: the
    tests above never touch a relationship field, so they cannot see this
    failure mode.
    """
    staff, code = existing_row_with_recovery_code

    run(audited_view.update_model(
        request_of(actor="kim"),
        pk=staff.id,
        data={"display_name": "Renamed", "recovery_codes": [code.id]},
    ))
    session.flush()

    entry = session.scalar(select(AuditLog).where(AuditLog.action == "update"))
    assert entry is not None, "a relation-touching update must still be audited"
    assert entry.before_json["display_name"] == "Original"
    assert entry.after_json["display_name"] == "Renamed"
