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

import threading
import time
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from admin.audit import row_to_dict
from admin.factor_lifecycle import (
    CHILD_MODELS, LifecycleError, archive_factor_set, clone_factor_set,
    count_child_rows, import_published_into, publish_factor_set,
    rollback_to, set_placeholder_flag,
)
from admin.factor_models import (
    Constant, Equivalence, FactorDownstream, FactorSet, FactorSetStatus,
    FactorUpstream, Formula,
)
from admin.models import utcnow
from tests.admin.conftest import _add_formula

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


# --- Task 3: publish and roll back ------------------------------------------


def test_publishing_archives_the_previous_published_set(_committed_session, two_sets):
    """§2.2's invariant is maintained by the transition itself, not by
    asking the staff member to archive the old one first."""
    session = _committed_session
    live, draft = two_sets
    publish_factor_set(session, draft.id, actor="kim")
    session.flush()

    assert session.get(FactorSet, live.id).status is FactorSetStatus.archived
    assert session.get(FactorSet, draft.id).status is FactorSetStatus.published


def test_publishing_stamps_who_and_when(_committed_session, two_sets):
    """These columns are deliberately absent from the edit form (E-5), so
    this is the only thing that may write them."""
    session = _committed_session
    _, draft = two_sets
    publish_factor_set(session, draft.id, actor="kim")
    session.flush()

    published = session.get(FactorSet, draft.id)
    assert published.published_by == "kim"
    assert published.published_at is not None


def test_publishing_the_first_set_needs_no_predecessor(_committed_session, one_draft):
    """A fresh deployment has nothing published — §9's
    NO_PUBLISHED_FACTOR_SET is the designed response to that state, so
    reaching it is normal, not an error."""
    session = _committed_session
    publish_factor_set(session, one_draft.id, actor="kim")
    session.flush()

    assert session.get(FactorSet, one_draft.id).status is FactorSetStatus.published


def test_publishing_a_set_with_a_broken_formula_is_refused(_committed_session, two_sets):
    """The whole point of publishing being a distinct step.

    A formula can become unrunnable without anyone editing it — deleting the
    constant it references does it (E-5). Publishing is the last moment the
    calculator is still working, so it is the right place to check.
    """
    session = _committed_session
    _, draft = two_sets
    _add_formula(session, draft, "qty_kg * const_GONE")

    with pytest.raises(LifecycleError) as excinfo:
        publish_factor_set(session, draft.id, actor="kim")

    assert "const_GONE" in str(excinfo.value)
    assert session.get(FactorSet, draft.id).status is FactorSetStatus.draft


def test_publishing_a_set_whose_prevention_upstream_is_missing_is_refused(
    _committed_session, taxonomy_for_factors, two_sets,
):
    """Open item O-7's second half, and the one the panel had to catch.

    The migration gave every factor combination that existed a `prevention`
    upstream row at zero, and `clone_factor_set` carries them through the
    recommended clone-edit-publish path. What neither covers is a staff member
    adding a *new* `(sector, food_category, metric)` to a draft: its general
    upstream row resolves for every destination including `prevention`, so a
    prevented line is charged the full upstream impact and that one
    combination silently reverts to pre-v1.8 behaviour.

    **That is harder to spot than the original O-7 was.** O-7 was wrong
    everywhere, so any check of any number found it; this is wrong for one
    sector while every other sector on the same results page is right, and it
    arrives with no error, no warning and nothing in the log.

    Publish is the right place, not the upstream-factor form: it is the single
    transactional choke point, and a form-level guard cannot see a row that has
    not been written yet — it would refuse the general row for the sake of a
    `prevention` row the staff member was about to add next.

    The row is committed under the "e6_" destination group rather than with an
    "e6_" code of its own only because `_cleanup_e6_rows` sweeps destinations
    by group; **the code itself no longer matters to anything** — since v1.22
    the role is `destination.is_prevention` and it is the tick, not the name,
    that this test sets. See
    `test_an_unflagged_destination_named_prevention_satisfies_nothing` below.
    """
    from admin.taxonomy_models import Destination

    session = _committed_session
    _, draft = two_sets
    session.add(Destination(group_id=taxonomy_for_factors.destination.group_id,
                            code="prevention", name="Prevented — waste avoided",
                            is_prevention=True))
    session.flush()

    with pytest.raises(LifecycleError) as excinfo:
        publish_factor_set(session, draft.id, actor="kim")

    message = str(excinfo.value)
    #: The tuples are named, or the staff member is told something is wrong
    #: and left to find which of ~270 rows it is.
    assert "e6_processing/e6_dairy/e6_co2e" in message
    assert "prevention" in message
    assert session.get(FactorSet, draft.id).status is FactorSetStatus.draft


def test_publishing_succeeds_once_the_prevention_row_is_added(
    _committed_session, taxonomy_for_factors, two_sets,
):
    """The other half of the guard: it must refuse an incomplete set and then
    get out of the way. A check that cannot be satisfied is an outage.
    """
    from admin.taxonomy_models import Destination

    session = _committed_session
    _, draft = two_sets
    prevention = Destination(group_id=taxonomy_for_factors.destination.group_id,
                             code="prevention", name="Prevented — waste avoided",
                             is_prevention=True)
    session.add(prevention)
    session.flush()
    session.add(FactorUpstream(
        factor_set_id=draft.id,
        sector_id=taxonomy_for_factors.sector.id,
        food_category_id=taxonomy_for_factors.category.id,
        destination_id=prevention.id,
        metric_id=taxonomy_for_factors.metric.id,
        value_per_kg=Decimal("0.0000000000"),
        source_note="Prevented waste was never produced.",
        data_quality="definitional",
    ))
    session.flush()

    publish_factor_set(session, draft.id, actor="kim")
    session.flush()

    assert session.get(FactorSet, draft.id).status is FactorSetStatus.published


def test_an_unflagged_destination_named_prevention_satisfies_nothing(
    _committed_session, taxonomy_for_factors, two_sets,
):
    """What proves the string is gone from the panel's own publish path.

    A row *called* `prevention` with the tick cleared is an ordinary
    destination, so its zero upstream override satisfies no tuple and the set
    must still be refused. The mirror of
    `test_publishing_succeeds_once_the_prevention_row_is_added` above, which is
    the identical arrangement with the tick set.
    """
    from admin.taxonomy_models import Destination

    session = _committed_session
    _, draft = two_sets
    named = Destination(group_id=taxonomy_for_factors.destination.group_id,
                        code="prevention", name="Prevented — waste avoided",
                        is_prevention=False)
    flagged = Destination(group_id=taxonomy_for_factors.destination.group_id,
                          code="waste_avoided", name="Waste avoided",
                          is_prevention=True)
    session.add_all([named, flagged])
    session.flush()
    session.add(FactorUpstream(
        factor_set_id=draft.id,
        sector_id=taxonomy_for_factors.sector.id,
        food_category_id=taxonomy_for_factors.category.id,
        destination_id=named.id,
        metric_id=taxonomy_for_factors.metric.id,
        value_per_kg=Decimal("0.0000000000"),
        source_note="Zero, against a destination that carries no role.",
        data_quality="definitional",
    ))
    session.flush()

    with pytest.raises(LifecycleError) as excinfo:
        publish_factor_set(session, draft.id, actor="kim")

    assert "e6_processing/e6_dairy/e6_co2e" in str(excinfo.value)
    assert session.get(FactorSet, draft.id).status is FactorSetStatus.draft


def test_publishing_a_prevention_destination_priced_above_zero_is_refused(
    _committed_session, taxonomy_for_factors, two_sets,
):
    """The half nothing checked at all before v1.22, on the path the panel
    actually takes.

    `find_missing_prevention_upstream` can only see a non-zero upstream value
    where a generic row exists to compare it against, and no rule anywhere read
    `factor_downstream`. A prevention destination priced at anything is not a
    100% offset, so the improved scenario stops describing the same mass at no
    cost and the net benefit is silently smaller than the scenario the user
    built.
    """
    from admin.taxonomy_models import Destination

    session = _committed_session
    _, draft = two_sets
    prevention = Destination(group_id=taxonomy_for_factors.destination.group_id,
                             code="prevention", name="Prevented — waste avoided",
                             is_prevention=True)
    session.add(prevention)
    session.flush()
    session.add_all([
        FactorUpstream(
            factor_set_id=draft.id,
            sector_id=taxonomy_for_factors.sector.id,
            food_category_id=taxonomy_for_factors.category.id,
            destination_id=prevention.id,
            metric_id=taxonomy_for_factors.metric.id,
            value_per_kg=Decimal("0.0000000000"),
            source_note="Prevented waste was never produced.",
            data_quality="definitional",
        ),
        FactorDownstream(
            factor_set_id=draft.id,
            destination_id=prevention.id,
            food_category_id=None,
            metric_id=taxonomy_for_factors.metric.id,
            value_per_kg=Decimal("0.5000000000"),
            source_note="Wrong: a prevention destination costs nothing.",
            data_quality="definitional",
        ),
    ])
    session.flush()

    with pytest.raises(LifecycleError) as excinfo:
        publish_factor_set(session, draft.id, actor="kim")

    assert "downstream prevention" in str(excinfo.value)
    assert session.get(FactorSet, draft.id).status is FactorSetStatus.draft


def test_rollback_is_not_blocked_by_an_incomplete_prevention_set(
    _committed_session, taxonomy_for_factors, two_sets,
):
    """Deliberately asymmetric with publish above.

    Rollback is the "put the calculator back to a state that worked"
    operation, and a set archived before v1.8 will legitimately fail the O-7
    completeness check. Refusing an emergency rollback over a completeness rule
    would be a worse failure than the one the rule prevents — so the guard is
    on publish only, and this is what says so on purpose rather than by
    omission.
    """
    from admin.taxonomy_models import Destination

    session = _committed_session
    live, draft = two_sets
    session.add(Destination(group_id=taxonomy_for_factors.destination.group_id,
                            code="prevention", name="Prevented — waste avoided",
                            is_prevention=True))
    live.status = FactorSetStatus.archived
    session.flush()

    rollback_to(session, live.id, actor="kim")
    session.flush()

    assert session.get(FactorSet, live.id).status is FactorSetStatus.published


def test_publishing_an_already_published_set_is_refused(_committed_session, two_sets):
    session = _committed_session
    live, _ = two_sets

    with pytest.raises(LifecycleError):
        publish_factor_set(session, live.id, actor="kim")


def test_publishing_refuses_when_two_are_already_published(_committed_session, two_sets):
    """§5.2: "Rolls back if the invariant would be violated." A pre-existing
    violation must be refused rather than quietly half-fixed — the operator
    needs to choose which one survives."""
    session = _committed_session
    live, draft = two_sets
    draft.status = FactorSetStatus.published
    session.flush()
    # "e6-" prefixed, not the brief's literal "third" - _cleanup_e6_rows
    # (tests/admin/conftest.py) only ever matches that prefix, and a bare
    # "third" factor_set row would leak past every future test run.
    third = FactorSet(version_label="e6-third", status=FactorSetStatus.draft,
                      is_mock=True)
    session.add(third)
    session.flush()

    with pytest.raises(LifecycleError):
        publish_factor_set(session, third.id, actor="kim")


def test_rollback_restores_an_archived_set(_committed_session, two_sets):
    """Also covers what test_both_transitions_are_audited (below) does not:
    that name notwithstanding, it only ever drives publish_factor_set, so
    nothing previously proved rollback_to's own audit verb ("rollback", not
    "publish" - contract §2.3's action list distinguishes the two) or that
    it stamps the row it actually changed."""
    from admin.models import AuditLog

    session = _committed_session
    live, draft = two_sets
    publish_factor_set(session, draft.id, actor="kim")
    session.flush()

    rollback_to(session, live.id, actor="kim")
    session.flush()

    assert session.get(FactorSet, live.id).status is FactorSetStatus.published
    assert session.get(FactorSet, draft.id).status is FactorSetStatus.archived

    actions = {
        (e.row_id, e.action)
        for e in session.scalars(
            select(AuditLog).where(AuditLog.table_name == "factor_set")
        ).all()
    }
    assert (live.id, "rollback") in actions
    assert (draft.id, "archive") in actions


def test_rollback_refuses_a_draft(_committed_session, two_sets):
    """Rollback restores something that was live before. A draft has never
    been live, and publishing it is a different decision with a different
    audit meaning."""
    session = _committed_session
    _, draft = two_sets

    with pytest.raises(LifecycleError):
        rollback_to(session, draft.id, actor="kim")


def test_both_transitions_are_audited(_committed_session, two_sets):
    from admin.models import AuditLog

    session = _committed_session
    live, draft = two_sets
    publish_factor_set(session, draft.id, actor="kim")
    session.flush()

    actions = {
        (e.row_id, e.action)
        for e in session.scalars(
            select(AuditLog).where(AuditLog.table_name == "factor_set")
        ).all()
    }
    assert (draft.id, "publish") in actions
    assert (live.id, "archive") in actions


# --- Fix wave: archive_factor_set -------------------------------------------
#
# Removing `status` from FactorSetAdmin.form_columns closed a real bypass but
# also left publish_factor_set/rollback_to as the only two ways any set ever
# reached `archived` - always as a side effect of promoting a different one.
# That leaves no way to take the calculator offline when the live factors
# need pulling and nothing else is ready to publish in their place.
# archive_factor_set is that route: archive a set directly, promoting
# nothing.


def test_archiving_a_draft_marks_it_archived(_committed_session, one_draft):
    session = _committed_session
    archive_factor_set(session, one_draft.id, actor="kim")
    session.flush()

    assert session.get(FactorSet, one_draft.id).status is FactorSetStatus.archived


def test_archiving_the_published_set_is_allowed(_committed_session, two_sets):
    """Archiving the only published set, leaving nothing published, is
    exactly 'take the calculator offline' - contract §9's
    NO_PUBLISHED_FACTOR_SET (503, 'calculator under maintenance') is the
    designed response to that state, so reaching it on purpose is a
    supported operation, not an error this function should refuse."""
    session = _committed_session
    live, _ = two_sets
    archive_factor_set(session, live.id, actor="kim")
    session.flush()

    assert session.get(FactorSet, live.id).status is FactorSetStatus.archived


def test_archiving_an_already_archived_set_is_refused(_committed_session, two_sets):
    """Nothing changes, so a fresh audit 'archive' entry over an unchanged
    row would misrepresent the trail the same way re-publishing an already
    published set would (test_publishing_an_already_published_set_is_refused,
    above)."""
    session = _committed_session
    live, _ = two_sets
    archive_factor_set(session, live.id, actor="kim")
    session.flush()

    with pytest.raises(LifecycleError):
        archive_factor_set(session, live.id, actor="kim")


def test_archiving_a_set_that_does_not_exist_is_refused(_committed_session):
    session = _committed_session
    with pytest.raises(LifecycleError):
        archive_factor_set(session, 9999, actor="kim")


def test_archive_never_commits(_committed_session, one_draft):
    """Module docstring, admin/factor_lifecycle.py: every function here
    leaves the transaction to its caller - proven the same way
    test_clone_never_commits (above) proves it for clone_factor_set.

    Commits `one_draft` itself first, unlike test_clone_never_commits: that
    test only ever checks that its own new row is gone after rollback, but
    this one needs `one_draft` itself to survive the rollback so there is a
    row left to re-read - and `_make_set` (tests/admin/conftest.py) only
    flushes its fixtures, it never commits them, so without this the
    rollback below would undo the fixture's own insert too and
    session.get(...) would come back None for a reason that has nothing to
    do with archive_factor_set.
    """
    session = _committed_session
    session.commit()

    archive_factor_set(session, one_draft.id, actor="kim")
    session.rollback()

    assert session.get(FactorSet, one_draft.id).status is FactorSetStatus.draft


def test_archive_is_audited(_committed_session, one_draft):
    from admin.models import AuditLog

    session = _committed_session
    archive_factor_set(session, one_draft.id, actor="kim")
    session.flush()

    entry = session.scalar(
        select(AuditLog).where(AuditLog.table_name == "factor_set",
                               AuditLog.row_id == one_draft.id,
                               AuditLog.action == "archive")
    )
    assert entry is not None
    assert entry.actor == "kim"


# --- The placeholder flag ------------------------------------------------
#
# Contract §2.2. `set_placeholder_flag` is the only sanctioned write path for
# `factor_set.is_mock`; the proof the panel asks for before the clearing
# direction lives in admin/factor_views.py, which is where a request's form
# and the login throttle exist. What is testable here is the half a service
# function owns: which transitions are legal, in which statuses, and what the
# audit trail is left saying.


@pytest.mark.parametrize(
    "status",
    [FactorSetStatus.draft, FactorSetStatus.published, FactorSetStatus.archived],
)
def test_flagging_a_set_as_placeholder_is_allowed_in_every_status(
    _committed_session, one_draft, status
):
    """The safe direction, and it must never be gated. Somebody who doubts
    the numbers under a *published* set has to be able to put the warning in
    front of the public without cloning, publishing or asking anyone.

    `one_draft` is moved into each status directly rather than through
    publish/archive: this is a test about the flag, and routing it through a
    lifecycle transition would make a publish refusal look like a flag
    refusal.
    """
    session = _committed_session
    one_draft.status = status
    one_draft.is_mock = False
    session.flush()

    set_placeholder_flag(session, one_draft.id, is_mock=True, actor="kim")
    session.flush()

    assert session.get(FactorSet, one_draft.id).is_mock is True


@pytest.mark.parametrize(
    "status",
    [FactorSetStatus.draft, FactorSetStatus.published, FactorSetStatus.archived],
)
def test_clearing_the_flag_is_allowed_in_every_status(
    _committed_session, one_draft, status
):
    """The rule that changed. The panel used to refuse this on a published
    set and tell staff to clone first, which is a new `factor_set` row and a
    new version label for a change in which not one factor value differs —
    while every submission recorded meanwhile stamps the old id.

    The gate on this direction is the proof the panel asks for, not the
    set's status.
    """
    session = _committed_session
    one_draft.status = status
    session.flush()

    set_placeholder_flag(session, one_draft.id, is_mock=False, actor="kim")
    session.flush()

    assert session.get(FactorSet, one_draft.id).is_mock is False


@pytest.mark.parametrize("is_mock, action", [
    (True, "flag_placeholder"),
    (False, "clear_placeholder"),
])
def test_each_direction_is_audited_with_both_values(
    _committed_session, one_draft, is_mock, action
):
    """Who, when, which set, and which value to which — §5.5, and the point
    of the change: this used to land as an ordinary `update`, which in a list
    of audit entries is indistinguishable from somebody fixing a typo in the
    same set's notes.

    Both `before` and `after` are asserted. An entry that recorded only the
    new value cannot answer the question the trail is read for, which is
    whether the public warning came off and when.
    """
    from admin.models import AuditLog

    session = _committed_session
    one_draft.is_mock = not is_mock
    session.flush()

    set_placeholder_flag(session, one_draft.id, is_mock=is_mock, actor="kim")
    session.flush()

    entry = session.scalar(
        select(AuditLog).where(AuditLog.table_name == "factor_set",
                               AuditLog.row_id == one_draft.id,
                               AuditLog.action == action)
    )
    assert entry is not None
    assert entry.actor == "kim"
    assert entry.before_json["is_mock"] is (not is_mock)
    assert entry.after_json["is_mock"] is is_mock


@pytest.mark.parametrize("is_mock", [True, False])
def test_setting_the_flag_to_what_it_already_says_is_refused(
    _committed_session, one_draft, is_mock
):
    """Same reason `publish_factor_set` refuses an already-published target:
    an audit entry claiming a change that did not happen is worse than no
    entry, on the one trail that answers "when did the warning come off"."""
    session = _committed_session
    one_draft.is_mock = is_mock
    session.flush()

    with pytest.raises(LifecycleError):
        set_placeholder_flag(session, one_draft.id, is_mock=is_mock, actor="kim")


def test_setting_the_flag_on_a_set_that_does_not_exist_is_refused(_committed_session):
    with pytest.raises(LifecycleError):
        set_placeholder_flag(_committed_session, 9999, is_mock=False, actor="kim")


def test_setting_the_flag_never_commits(_committed_session, one_draft):
    """Module docstring, admin/factor_lifecycle.py: the caller owns the
    transaction. See test_archive_never_commits for why the fixture is
    committed first."""
    session = _committed_session
    session.commit()

    set_placeholder_flag(session, one_draft.id, is_mock=False, actor="kim")
    session.rollback()

    assert session.get(FactorSet, one_draft.id).is_mock is True


# --- Importing the published set into a draft ------------------------------
#
# The fix for a draft a staff member has edited and now regrets: brings the
# published set's own numbers back into it, in place, rather than leaving
# staff to clone the published set again and abandon the spoiled draft.


#: The five child kinds, written out rather than read from `CHILD_MODELS`.
#:
#: The tests below used to loop over `CHILD_MODELS` itself, which is the list
#: the implementation loops over too - so deleting a model from it deleted the
#: assertions that would have caught the deletion, and all sixteen import
#: tests stayed green while `Equivalence` rows silently stopped being copied.
#: A draft left carrying one table's worth of its own old numbers and four
#: tables' worth of the published set's is a silent hybrid, and nothing on
#: screen says so. An expected-value list has to be written down somewhere for
#: a test to mean anything; here it is.
_EVERY_CHILD_KIND = (FactorUpstream, FactorDownstream, Constant, Formula, Equivalence)


def test_child_models_names_exactly_the_five_child_kinds():
    """`CHILD_MODELS` drives both the copy and the delete in
    `import_published_into`, and the clone before it. Adding a sixth child
    table without adding it here means clone and import both quietly ignore
    it; removing one means they quietly leave it behind. Either way the
    factor set stops being versioned atomically, which is the property
    `docs/architecture.md` rests the whole draft/publish model on."""
    assert CHILD_MODELS == _EVERY_CHILD_KIND


def _snapshot(session, model, factor_set_id):
    """Every column of every row of `model` belonging to `factor_set_id`,
    ordered by id - `id`/`factor_set_id` included this time, unlike
    test_a_clone_copies_every_column_value's own use of row_to_dict, because
    what test_import_does_not_touch_the_source_set below needs to prove is
    that the SOURCE's rows are byte-for-byte what they were, ids and all -
    not merely that some row somewhere still carries the same values."""
    rows = session.scalars(
        select(model).where(model.factor_set_id == factor_set_id).order_by(model.id)
    ).all()
    return [row_to_dict(row) for row in rows]


def test_import_replaces_every_child_row(_committed_session, taxonomy_for_factors, two_sets):
    """The ordinary case: a clean draft ends up an exact copy of the
    published set, for all five child kinds."""
    session = _committed_session
    live, draft = two_sets

    import_published_into(session, draft.id, actor="kim")
    session.flush()

    for model in _EVERY_CHILD_KIND:
        source_count = session.scalar(
            select(func.count()).select_from(model).where(model.factor_set_id == live.id)
        )
        target_count = session.scalar(
            select(func.count()).select_from(model).where(model.factor_set_id == draft.id)
        )
        assert target_count == source_count > 0, f"{model.__tablename__} not copied"


def test_import_copies_every_column_value(_committed_session, taxonomy_for_factors, two_sets):
    """Row counts alone cannot tell a real copy from one that drops every
    optional column - the same reasoning test_a_clone_copies_every_column_value
    gives for clone_factor_set."""
    session = _committed_session
    live, draft = two_sets

    import_published_into(session, draft.id, actor="kim")
    session.flush()

    for model in _EVERY_CHILD_KIND:
        source_row = session.scalar(select(model).where(model.factor_set_id == live.id))
        target_row = session.scalar(select(model).where(model.factor_set_id == draft.id))
        source_fields = {
            k: v for k, v in row_to_dict(source_row).items()
            if k not in ("id", "factor_set_id")
        }
        target_fields = {
            k: v for k, v in row_to_dict(target_row).items()
            if k not in ("id", "factor_set_id")
        }
        assert target_fields == source_fields, f"{model.__tablename__} column mismatch"


def test_import_is_a_full_replacement_not_a_merge(
    _committed_session, taxonomy_for_factors, two_sets
):
    """The row that exists only in the draft must be gone afterwards - the
    property that separates this from a merge, and the one the task brief
    names as the single most important behavioural claim after "the source
    is untouched".

    Mutating this test's target by deleting the `for model in _EVERY_CHILD_KIND:
    session.execute(delete(model)...)` loop out of import_published_into
    (admin/factor_lifecycle.py) turns it red: the extra row below would
    still be there afterwards, so `remaining` would be non-empty.
    """
    from admin.taxonomy_models import Metric

    session = _committed_session
    live, draft = two_sets

    extra_metric = Metric(code="e6_extra_only_in_draft", name="Extra", unit="x")
    session.add(extra_metric)
    session.flush()
    session.add(FactorUpstream(
        factor_set_id=draft.id,
        sector_id=taxonomy_for_factors.sector.id,
        food_category_id=taxonomy_for_factors.category.id,
        metric_id=extra_metric.id,
        value_per_kg=Decimal("42.0000000000"),
        source_note="only the draft has this row",
        data_quality="draft-only",
    ))
    session.flush()

    import_published_into(session, draft.id, actor="kim")
    session.flush()

    remaining = session.scalars(
        select(FactorUpstream).where(
            FactorUpstream.factor_set_id == draft.id,
            FactorUpstream.metric_id == extra_metric.id,
        )
    ).all()
    assert remaining == [], (
        "a row that existed only in the draft survived the import - this "
        "is a merge, not a replacement"
    )


def test_import_does_not_touch_the_source_set(
    _committed_session, taxonomy_for_factors, two_sets
):
    """The most important assertion in the task: an import that wrote back
    into the published set would corrupt live data and break the
    reproducibility of every submission stamped with it.

    Compared by content, not count: `before`/`after` are full column
    snapshots of every one of the source's own rows, ids included. A buggy
    implementation that *moved* rows into the draft instead of copying them
    (UPDATE ... SET factor_set_id = draft.id rather than INSERT a duplicate)
    would leave `after` empty for every model - `before == after` catches
    that as surely as it catches a value quietly overwritten in place.
    """
    session = _committed_session
    live, draft = two_sets

    before = {model: _snapshot(session, model, live.id) for model in _EVERY_CHILD_KIND}
    assert all(rows for rows in before.values()), "anchor: the source starts populated"

    import_published_into(session, draft.id, actor="kim")
    session.flush()
    session.expire_all()

    after = {model: _snapshot(session, model, live.id) for model in _EVERY_CHILD_KIND}
    for model in _EVERY_CHILD_KIND:
        assert after[model] == before[model], (
            f"{model.__tablename__}: the published source set changed"
        )

    # And the row itself - status, is_mock, the lot - untouched too.
    session.refresh(live)
    assert live.status is FactorSetStatus.published
    assert live.version_label == "e6-live"


def test_import_refuses_a_published_target(_committed_session, two_sets):
    """Same reason every one of the five factor-row views refuses editing a
    published row in place: every submission stamped with a version's id has
    to keep reproducing years later."""
    session = _committed_session
    live, _ = two_sets

    with pytest.raises(LifecycleError) as excinfo:
        import_published_into(session, live.id, actor="kim")

    assert "not draft" in str(excinfo.value)
    # Refused before anything was touched - not merely refused in the end.
    assert session.scalar(
        select(func.count()).select_from(FactorUpstream)
        .where(FactorUpstream.factor_set_id == live.id)
    ) > 0


def test_import_refuses_an_archived_target(_committed_session, two_sets):
    session = _committed_session
    live, draft = two_sets
    draft.status = FactorSetStatus.archived
    session.flush()

    with pytest.raises(LifecycleError) as excinfo:
        import_published_into(session, draft.id, actor="kim")

    assert "not draft" in str(excinfo.value)


def test_import_refuses_when_nothing_is_published(_committed_session, one_draft):
    session = _committed_session

    with pytest.raises(LifecycleError) as excinfo:
        import_published_into(session, one_draft.id, actor="kim")

    assert "published" in str(excinfo.value)


def test_import_refuses_when_two_sets_are_already_published(
    _committed_session, taxonomy_for_factors, two_sets
):
    session = _committed_session
    live, draft = two_sets
    third = FactorSet(version_label="e6-third-published",
                      status=FactorSetStatus.published, is_mock=True)
    session.add(third)
    session.flush()

    with pytest.raises(LifecycleError):
        import_published_into(session, draft.id, actor="kim")


def test_import_refuses_a_target_that_does_not_exist(_committed_session):
    with pytest.raises(LifecycleError):
        import_published_into(_committed_session, 9999, actor="kim")


def test_import_is_mock_follows_the_source(_committed_session, taxonomy_for_factors, two_sets):
    session = _committed_session
    live, draft = two_sets
    live.is_mock = False
    draft.is_mock = True
    session.flush()

    import_published_into(session, draft.id, actor="kim")
    session.flush()

    assert session.get(FactorSet, draft.id).is_mock is False


def test_import_leaves_version_label_and_notes_untouched(
    _committed_session, taxonomy_for_factors, two_sets
):
    session = _committed_session
    live, draft = two_sets
    draft.notes = "kept exactly as the staff member wrote it"
    session.flush()
    original_label = draft.version_label
    original_notes = draft.notes

    import_published_into(session, draft.id, actor="kim")
    session.flush()

    refreshed = session.get(FactorSet, draft.id)
    assert refreshed.version_label == original_label
    assert refreshed.notes == original_notes


def test_import_leaves_publication_stamps_untouched(
    _committed_session, taxonomy_for_factors, two_sets
):
    """The target stays a draft: published_at/published_by describe an
    event that has not happened to it, the same reasoning
    test_a_clone_does_not_inherit_publication_stamps gives for clone."""
    session = _committed_session
    live, draft = two_sets
    assert draft.published_at is None
    assert draft.published_by is None

    import_published_into(session, draft.id, actor="kim")
    session.flush()

    refreshed = session.get(FactorSet, draft.id)
    assert refreshed.published_at is None
    assert refreshed.published_by is None
    assert refreshed.status is FactorSetStatus.draft


def test_import_never_commits(_committed_session, taxonomy_for_factors, two_sets):
    """Module docstring: every function here leaves the transaction to its
    caller - proven the same way test_archive_never_commits proves it."""
    session = _committed_session
    live, draft = two_sets
    live.is_mock = False
    draft.is_mock = True
    session.commit()

    import_published_into(session, draft.id, actor="kim")
    session.rollback()

    assert session.get(FactorSet, draft.id).is_mock is True


def test_import_writes_one_audit_entry_naming_target_source_and_counts(
    _committed_session, taxonomy_for_factors, two_sets
):
    """`discarded` and `written` are asserted as full dicts, not just their
    totals - and the draft is given an extra row first so the two counts
    are genuinely different, not two dicts that happen to agree because a
    freshly-built `two_sets` starts both sides at exactly one row of each
    kind. Swap `written`'s source for `discarded`'s own value
    (`written = discarded` rather than a fresh `count_child_rows(session,
    target.id)` read) and this test is what catches it - with the fixture
    alone, that mutation is invisible.
    """
    from admin.models import AuditLog
    from admin.taxonomy_models import Metric

    session = _committed_session
    live, draft = two_sets
    extra_metric = Metric(code="e6_extra_for_audit_counts", name="Extra", unit="x")
    session.add(extra_metric)
    session.flush()
    session.add(FactorUpstream(
        factor_set_id=draft.id,
        sector_id=taxonomy_for_factors.sector.id,
        food_category_id=taxonomy_for_factors.category.id,
        metric_id=extra_metric.id,
        value_per_kg=Decimal("7.0000000000"),
        source_note="makes discarded != written for this test",
        data_quality="draft-only",
    ))
    session.flush()

    discarded_before = count_child_rows(session, draft.id)
    written_expected = count_child_rows(session, live.id)
    assert discarded_before != written_expected, (
        "anchor: the draft and the source must differ for this test to mean "
        "anything"
    )

    import_published_into(session, draft.id, actor="kim")
    session.flush()

    entry = session.scalar(
        select(AuditLog).where(AuditLog.table_name == "factor_set",
                               AuditLog.row_id == draft.id,
                               AuditLog.action == "import_published")
    )
    assert entry is not None
    assert entry.actor == "kim"
    assert entry.before_json["source_factor_set_id"] == live.id
    assert entry.before_json["source_version_label"] == live.version_label
    assert entry.before_json["discarded"] == discarded_before
    assert entry.after_json["written"] == written_expected
    # And the counts are not trivially equal by both being empty.
    assert sum(discarded_before.values()) > 0
    assert sum(written_expected.values()) > 0


def test_a_refused_import_writes_no_audit_entry(_committed_session, two_sets):
    from admin.models import AuditLog

    session = _committed_session
    live, _ = two_sets

    with pytest.raises(LifecycleError):
        import_published_into(session, live.id, actor="kim")

    assert session.scalar(
        select(AuditLog).where(AuditLog.table_name == "factor_set",
                               AuditLog.action == "import_published")
    ) is None


def test_a_concurrent_operation_on_the_published_row_blocks_the_import(
    admin_app, _committed_session, taxonomy_for_factors, two_sets
):
    """The lock, exercised directly for the first time in this file (no
    existing lifecycle test drives real concurrency; every other guarantee
    here is proven through the outcome a *single* transaction leaves
    behind). Two real threads, two real connections, a real MySQL row lock.

    **Deliberately narrow, and the narrowness is the point.** Thread 1 does
    not hold the same broad `_lock_factor_sets()` this test is trying to
    prove `import_published_into` also takes - that would pass even if
    `import_published_into` took no lock of its own at all, because its
    later, ordinary write to the *target* row (`target.is_mock = ...`)
    would still collide with thread 1's hold over every row, target
    included, and the test would say nothing about whether the read that
    decides `source` was locked. Thread 1 instead holds `SELECT ... FOR
    UPDATE` on `live`'s row alone - the one row a concurrent publish or
    rollback would be about to archive - and never touches `draft`'s.

    If `import_published_into` decided `source` from a plain, unlocked read
    (or a lock scoped to the target row only), nothing here would block it:
    `live`'s row is never written by this function, so a lock on it alone
    could never collide with anything `import_published_into` writes.
    Blocking can only happen if `import_published_into` itself takes a lock
    that includes `live`'s row before deciding what is published - which is
    exactly what `_lock_factor_sets()`'s "every row, not just the obvious
    one" does. Measured, not assumed, by timing how long the import actually
    took.
    """
    session = _committed_session
    live, draft = two_sets
    session.commit()

    factory = admin_app.state.session_factory
    lock_acquired = threading.Event()
    release_lock = threading.Event()
    result: dict = {}

    def hold_the_published_rows_lock():
        with factory() as locker:
            locker.execute(
                select(FactorSet).where(FactorSet.id == live.id).with_for_update()
            )
            lock_acquired.set()
            release_lock.wait(timeout=5)
            locker.rollback()

    def run_import():
        assert lock_acquired.wait(timeout=5), "thread 1 never acquired the lock"
        with factory() as importer:
            started = time.monotonic()
            import_published_into(importer, draft.id, actor="kim")
            result["elapsed"] = time.monotonic() - started
            importer.commit()

    holder = threading.Thread(target=hold_the_published_rows_lock)
    runner = threading.Thread(target=run_import)
    try:
        holder.start()
        assert lock_acquired.wait(timeout=5), "the locking thread never signalled"
        runner.start()

        # thread 2 must still be blocked a moment later - not finished.
        time.sleep(0.4)
        assert "elapsed" not in result, (
            "import_published_into ran to completion while another "
            "transaction held only the published row's lock - its own "
            "decision of what is published was not made under a lock "
            "covering that row"
        )
    finally:
        release_lock.set()
        holder.join(timeout=5)
        runner.join(timeout=5)

    assert "elapsed" in result, "the import never completed after the lock was released"
    assert result["elapsed"] > 0.3, (
        f"import_published_into returned in {result['elapsed']:.3f}s - too fast to "
        "have actually waited on the held lock"
    )
    _resync_after_threads(session)
    assert session.get(FactorSet, draft.id).status is FactorSetStatus.draft
    assert session.get(FactorSet, draft.id).is_mock == session.get(FactorSet, live.id).is_mock


def _resync_after_threads(session):
    """The two threads above committed/rolled back on their own connections;
    this session's own MySQL REPEATABLE READ snapshot predates both, so a
    plain re-query would still see the pre-thread state. Same fix
    tests/admin/conftest.py's own `_resync` uses."""
    session.commit()
    session.expire_all()
