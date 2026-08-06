"""Contract §8.1: every admin CRUD write produces an audit entry."""

import asyncio
import inspect
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from admin.accounts import create_staff
from admin.auth import SESSION_KEY
from admin.models import AuditLog, Staff
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
    """An AuditedModelView over Staff, bound to the test session."""

    class StaffTestView(AuditedModelView, model=Staff):
        pass

    view = StaffTestView()
    view.session_maker = lambda *args, **kwargs: session
    return view


@pytest.fixture()
def existing_row(session):
    staff, _ = create_staff(
        session, username="original", display_name="Original", actor="setup"
    )
    session.flush()
    return staff


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
