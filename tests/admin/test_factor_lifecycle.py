"""Contract §5.2. Clone, edit, publish — the recommended staff path.

Uses `_committed_session` directly rather than the plain rolled-back
`session` fixture from tests/conftest.py: `populated_set` and friends
(tests/admin/conftest.py) are built through `_committed_session`, and doing
every read, write and assertion here on that same session keeps the whole
test on one connection, so nothing depends on a second connection observing
a commit that never happens — see tests/admin/conftest.py's own docstring
for why that distinction matters.
"""

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from admin.factor_lifecycle import LifecycleError, clone_factor_set
from admin.factor_models import (
    Constant, Equivalence, FactorDownstream, FactorSet, FactorSetStatus,
    FactorUpstream, Formula,
)
from admin.models import utcnow

pytestmark = pytest.mark.db


def test_a_clone_copies_every_child_row(_committed_session, populated_set):
    """All four child kinds, or the clone is not a version of anything."""
    session = _committed_session
    new_id = clone_factor_set(session, populated_set.id, "2026-Q4", actor="kim")
    session.flush()

    for model in (FactorUpstream, FactorDownstream, Constant, Formula, Equivalence):
        source_count = session.scalar(
            select(func.count()).select_from(model)
            .where(model.factor_set_id == populated_set.id)
        )
        clone_count = session.scalar(
            select(func.count()).select_from(model)
            .where(model.factor_set_id == new_id)
        )
        assert clone_count == source_count > 0, f"{model.__tablename__} not copied"


def test_a_clone_is_always_a_draft(_committed_session, populated_set):
    """Even cloning the published set. Two published rows is the one thing
    §2.2 forbids outright, and a clone that inherited `published` would
    create exactly that."""
    session = _committed_session
    populated_set.status = FactorSetStatus.published
    session.flush()

    new_id = clone_factor_set(session, populated_set.id, "2026-Q4", actor="kim")
    session.flush()

    assert session.get(FactorSet, new_id).status is FactorSetStatus.draft


def test_a_clone_does_not_inherit_publication_stamps(_committed_session, populated_set):
    """published_at and published_by describe an event that happened to the
    source, not to the copy. Carrying them over would make the audit trail
    claim a version was published before it existed."""
    session = _committed_session
    populated_set.published_at = utcnow()
    populated_set.published_by = "someone"
    session.flush()

    new_id = clone_factor_set(session, populated_set.id, "2026-Q4", actor="kim")
    session.flush()

    clone = session.get(FactorSet, new_id)
    assert clone.published_at is None
    assert clone.published_by is None


def test_the_clone_and_the_source_are_independent(_committed_session, populated_set):
    """Editing the copy is the whole point. If the rows were shared, the
    first edit to a draft would change the published numbers."""
    session = _committed_session
    new_id = clone_factor_set(session, populated_set.id, "2026-Q4", actor="kim")
    session.flush()

    clone_row = session.scalar(
        select(FactorUpstream).where(FactorUpstream.factor_set_id == new_id)
    )
    clone_row.value_per_kg = Decimal("99.0")
    session.flush()

    source_row = session.scalar(
        select(FactorUpstream).where(FactorUpstream.factor_set_id == populated_set.id)
    )
    assert source_row.value_per_kg != Decimal("99.0")


def test_a_duplicate_label_is_refused(_committed_session, populated_set):
    """version_label is UNIQUE, so this would fail at the database anyway —
    but as an IntegrityError the staff member cannot act on. Refuse it with
    a message that says what to do."""
    session = _committed_session
    with pytest.raises(LifecycleError) as excinfo:
        clone_factor_set(session, populated_set.id, populated_set.version_label,
                         actor="kim")

    assert populated_set.version_label in str(excinfo.value)


def test_an_empty_label_is_refused(_committed_session, populated_set):
    session = _committed_session
    with pytest.raises(LifecycleError):
        clone_factor_set(session, populated_set.id, "   ", actor="kim")


def test_cloning_a_set_that_does_not_exist_is_refused(_committed_session):
    session = _committed_session
    with pytest.raises(LifecycleError):
        clone_factor_set(session, 9999, "2026-Q4", actor="kim")


def test_a_clone_is_audited(_committed_session, populated_set):
    """Contract §8.1. An @action route gets no auditing from the base class,
    so the service function writes its own."""
    from admin.models import AuditLog

    session = _committed_session
    new_id = clone_factor_set(session, populated_set.id, "2026-Q4", actor="kim")
    session.flush()

    entry = session.scalar(
        select(AuditLog).where(AuditLog.table_name == "factor_set",
                               AuditLog.row_id == new_id)
    )
    assert entry is not None
    assert entry.actor == "kim"
    assert entry.action == "create"
