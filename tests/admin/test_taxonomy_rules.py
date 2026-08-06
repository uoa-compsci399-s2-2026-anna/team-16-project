"""The two taxonomy invariants no column constraint can express."""

import asyncio
import inspect
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from admin.auth import SESSION_KEY
from admin.modelviews import AuditedModelView
from admin.models import AuditLog
from admin.taxonomy_models import Destination, DestinationGroup, FoodCategory
from admin.taxonomy_rules import (
    TaxonomyInvariantError, check_prevention_intact, check_single_standard_mix,
)

pytestmark = pytest.mark.db


def _run(result):
    """Call a sqladmin CRUD hook regardless of whether it is async.

    Mirrors tests/admin/test_modelviews.py's own `run` helper: sqladmin made
    insert_model/update_model/delete_model coroutines in 0.16, and calling
    one without awaiting it returns the coroutine and runs nothing - the
    assertion below would then pass against a broken implementation just as
    easily as a correct one.
    """
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def _request(actor="kim"):
    """A minimal stand-in for the Starlette request AuditedModelView reads."""
    return SimpleNamespace(session={SESSION_KEY: actor})


def _mix(session, code, is_mix):
    session.add(FoodCategory(code=code, name=code.title(), is_standard_mix=is_mix))
    session.flush()


def test_one_standard_mix_is_accepted(session):
    _mix(session, "standard_mix", True)
    _mix(session, "fruit", False)

    check_single_standard_mix(session)  # does not raise


def test_two_standard_mixes_are_refused(session):
    """Ambiguous: the engine would have no defensible way to pick one."""
    _mix(session, "standard_mix", True)
    _mix(session, "other_mix", True)

    with pytest.raises(TaxonomyInvariantError) as excinfo:
        check_single_standard_mix(session)

    assert "standard mix" in str(excinfo.value).lower()


def test_no_standard_mix_is_refused(session):
    """A user who does not know their waste composition has nothing to fall
    back to, which is most users."""
    _mix(session, "fruit", False)

    with pytest.raises(TaxonomyInvariantError):
        check_single_standard_mix(session)


def test_an_inactive_standard_mix_does_not_count(session):
    """Deactivating the only standard mix is the same failure as deleting it,
    and is the likelier way to reach it through the panel — every taxonomy
    view sets can_delete = False, so `active` is the only way rows leave."""
    _mix(session, "standard_mix", True)
    session.scalar(
        select(FoodCategory).where(FoodCategory.code == "standard_mix")
    ).active = False
    session.flush()

    with pytest.raises(TaxonomyInvariantError):
        check_single_standard_mix(session)


def _prevention(session, *, code="prevention", active=True):
    group = DestinationGroup(code="reuse", name="Reuse", is_waste=False)
    session.add(group)
    session.flush()
    session.add(Destination(group_id=group.id, code=code, name="Prevented",
                            active=active))
    session.flush()


def test_an_intact_prevention_is_accepted(session):
    _prevention(session)

    check_prevention_intact(session)  # does not raise


def test_a_missing_prevention_is_refused(session):
    """The engine looks this destination up by code to express "waste
    avoided". Without it the alternative scenario cannot be built at all."""
    with pytest.raises(TaxonomyInvariantError) as excinfo:
        check_prevention_intact(session)

    assert "prevention" in str(excinfo.value).lower()


def test_a_renamed_prevention_is_refused(session):
    """Renaming the code is indistinguishable from deleting it, from the
    engine's point of view — and is what a staff member tidying up codes
    would actually do."""
    _prevention(session, code="avoided")

    with pytest.raises(TaxonomyInvariantError):
        check_prevention_intact(session)


def test_a_deactivated_prevention_is_refused(session):
    _prevention(session, active=False)

    with pytest.raises(TaxonomyInvariantError):
        check_prevention_intact(session)


def test_a_view_can_refuse_a_commit_from_the_hook(session):
    """The hook has to be able to stop a commit, not merely observe one.

    Without this, an override could raise and sqladmin would commit anyway —
    which is exactly the failure mode the hook exists to prevent. This test
    alone only proves the hook exists and its default is inert; it would
    still pass if the listener never called it at all. See
    test_the_hook_actually_stops_a_commit below for the real wiring proof.
    """
    assert hasattr(AuditedModelView, "validate_before_commit")
    # The default must not raise: most views have no invariant, and one
    # inherited by accident would refuse every write in the panel.
    AuditedModelView.validate_before_commit(object(), session)


@pytest.fixture()
def standard_mix_guarded_view(session):
    """An AuditedModelView over FoodCategory whose validate_before_commit
    override enforces check_single_standard_mix for real.

    Shares `session`'s own connection, but genuine isolation for the
    assertions below requires more than that alone: SQLAlchemy's default
    `join_transaction_mode="conditional_savepoint"` only opens a real,
    independently-rollback-able SAVEPOINT for a second session sharing a
    connection if that connection is *already* inside a nested transaction
    at the moment the second session begins - otherwise it silently falls
    back to `"rollback_only"`, under which `session.close()` on failure
    detaches without touching the connection's transaction at all, so a
    write that successfully flushed stays applied, visible to `session`,
    until whatever finally rolls back the *whole* shared transaction - here,
    only `tests/conftest.py`'s teardown. Verified directly against this
    MySQL: without the `session.begin_nested()` + `session.connection()`
    pair below, this fixture's own test passes the `pytest.raises` but then
    fails the "row was rolled back" assertion, because nothing actually
    rolled it back mid-test.

    `session.begin_nested()` requests the SAVEPOINT; `session.connection()`
    is what forces SQLAlchemy to actually issue it now rather than lazily on
    first use - without the forcing call, `standard_mix_guarded_view`'s own
    session would begin before the SAVEPOINT exists on the wire, and land
    back in `"rollback_only"` regardless of the `begin_nested()` call above
    it.

    `autoflush=False` mirrors production deliberately - see
    admin/modelviews.py's `_audited_session_maker` docstring for why a
    sessionmaker that left it at SQLAlchemy's default would not exercise
    the flush-ordering bug this fixture exists to catch a regression of.

    The class attribute is assigned before construction, mirroring
    sqladmin's own registration order (Admin.add_model_view sets
    `session_maker` on the class, then constructs the one instance) -
    AuditedModelView.__init__ relies on that ordering to build its own
    audited sessionmaker from it.
    """
    session.begin_nested()
    session.connection()

    class _GuardedView(AuditedModelView, model=FoodCategory):
        def validate_before_commit(self, session):
            check_single_standard_mix(session)

    _GuardedView.session_maker = sessionmaker(
        bind=session.get_bind(), future=True, expire_on_commit=False,
        autoflush=False,
    )
    return _GuardedView()


def test_the_hook_actually_stops_a_commit(session, standard_mix_guarded_view):
    """The end-to-end proof: reached by a real update_model call, and its
    raise actually rolls the row back, not merely observed after the fact.

    Unticking the only standard mix is exactly what a staff member does
    through the panel's edit form - sqladmin's generic edit path, the same
    one the E-3 administrator-floor guard was bypassed through, because that
    guard lived in a service function the form never called. Deleting the
    listener's call to validate_before_commit (see admin/modelviews.py)
    makes this test fail; the 8 rule-level tests above do not, because their
    _mix/_prevention helpers call session.flush() directly and never go
    through a view at all.
    """
    _mix(session, "standard_mix", True)
    row = session.scalar(
        select(FoodCategory).where(FoodCategory.code == "standard_mix")
    )

    with pytest.raises(TaxonomyInvariantError):
        _run(standard_mix_guarded_view.update_model(
            _request(), pk=row.id, data={"is_standard_mix": False},
        ))

    session.expire(row)
    assert row.is_standard_mix is True, (
        "a refused change must not persist - the update was rolled back "
        "along with everything else in its transaction"
    )
    assert session.scalar(select(AuditLog)) is None, (
        "a refused change must leave no audit entry claiming it happened"
    )
