"""Contract §5.2. Clone, edit, publish — the recommended staff path.

Uses `_committed_session` directly rather than the plain rolled-back
`session` fixture from tests/conftest.py: `populated_set` and friends
(tests/admin/conftest.py) are built through `_committed_session`, and doing
every read, write and assertion here on that same session keeps the whole
test on one connection, so nothing depends on a second connection observing
a commit that never happens — see tests/admin/conftest.py's own docstring
for why that distinction matters.

Every clone label used below is "e6-clone"-prefixed, not the "2026-Q4" the
brief this file was written from actually uses. `clone_factor_set` never
commits (proven by `test_clone_never_commits`), so nothing here leaks
today - but `_cleanup_e6_rows` only matches `version_label LIKE 'e6-%'`, and
a "2026-Q4" row that *did* leak (the day a regression adds a stray commit)
would sit outside that net forever, silently poisoning every later run of
`test_a_duplicate_label_is_refused`.
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
    new_id = clone_factor_set(session, populated_set.id, "e6-clone", actor="kim")
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

    new_id = clone_factor_set(session, populated_set.id, "e6-clone", actor="kim")
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

    new_id = clone_factor_set(session, populated_set.id, "e6-clone", actor="kim")
    session.flush()

    clone = session.get(FactorSet, new_id)
    assert clone.published_at is None
    assert clone.published_by is None


def test_the_clone_and_the_source_are_independent(_committed_session, populated_set):
    """Editing the copy is the whole point. If the rows were shared, the
    first edit to a draft would change the published numbers.

    `session.expire_all()` before the second read forces it back to the
    database rather than the identity map: without it, a buggy
    re-parenting implementation (moving the source's own row onto the
    clone instead of copying it) would make `source_row` come back `None`
    and this test would die on `AttributeError` on the line below rather
    than on the assertion actually meant to catch that bug. Comparing
    against the value captured *before* the clone runs, rather than only
    asserting `!= 99.0`, additionally catches an implementation that
    mutates the source to some other placeholder rather than leaving it
    alone.
    """
    session = _committed_session
    original_value = session.scalar(
        select(FactorUpstream.value_per_kg)
        .where(FactorUpstream.factor_set_id == populated_set.id)
    )

    new_id = clone_factor_set(session, populated_set.id, "e6-clone", actor="kim")
    session.flush()

    clone_row = session.scalar(
        select(FactorUpstream).where(FactorUpstream.factor_set_id == new_id)
    )
    clone_row.value_per_kg = Decimal("99.0")
    session.flush()
    session.expire_all()

    source_row = session.scalar(
        select(FactorUpstream).where(FactorUpstream.factor_set_id == populated_set.id)
    )
    assert source_row is not None
    assert source_row.value_per_kg == original_value
    assert source_row.value_per_kg != Decimal("99.0")


def test_a_clone_copies_every_column_value(_committed_session, populated_set):
    """Row counts (test_a_clone_copies_every_child_row, above) cannot tell a
    real deep copy from one that only copies the NOT NULL columns and drops
    every optional one - `source_note`, `data_quality`, `unit`, `note`,
    `notes`, `sort_order`, `active`. `_make_set` (tests/admin/conftest.py)
    deliberately gives every optional column a non-default value so this
    comparison has something to catch.
    """
    from admin.audit import row_to_dict

    session = _committed_session
    new_id = clone_factor_set(session, populated_set.id, "e6-clone", actor="kim")
    session.flush()

    for model in (FactorUpstream, FactorDownstream, Constant, Formula, Equivalence):
        source_row = session.scalar(
            select(model).where(model.factor_set_id == populated_set.id)
        )
        clone_row = session.scalar(
            select(model).where(model.factor_set_id == new_id)
        )
        source_fields = {
            key: value for key, value in row_to_dict(source_row).items()
            if key not in ("id", "factor_set_id")
        }
        clone_fields = {
            key: value for key, value in row_to_dict(clone_row).items()
            if key not in ("id", "factor_set_id")
        }
        assert clone_fields == source_fields, f"{model.__tablename__} column mismatch"


def test_clone_never_commits(_committed_session, populated_set):
    """Module docstring, admin/factor_lifecycle.py: "never commits — the
    caller owns the transaction." Proven by rolling back right after the
    call: a version that committed internally would leave the clone
    findable even after this rollback undoes everything else."""
    session = _committed_session
    new_id = clone_factor_set(session, populated_set.id, "e6-clone", actor="kim")

    session.rollback()

    assert session.get(FactorSet, new_id) is None


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
        clone_factor_set(session, 9999, "e6-clone", actor="kim")


def test_a_clone_is_audited(_committed_session, populated_set):
    """Contract §8.1. An @action route gets no auditing from the base class,
    so the service function writes its own."""
    from admin.models import AuditLog

    session = _committed_session
    new_id = clone_factor_set(session, populated_set.id, "e6-clone", actor="kim")
    session.flush()

    entry = session.scalar(
        select(AuditLog).where(AuditLog.table_name == "factor_set",
                               AuditLog.row_id == new_id)
    )
    assert entry is not None
    assert entry.actor == "kim"
    assert entry.action == "create"
