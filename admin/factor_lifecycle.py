"""Clone, publish and roll back a factor set. Contract §5.2.

**Ownership note.** §5.2 places these three functions in `db/repository.py`,
which B owns, and B has an implementation on an unmerged branch. They live
here for now because the panel cannot do its job without them and that
branch has no landing date. When it lands, one of the two implementations
goes and the other is imported — `admin/` may import from `db/`, never the
reverse. Nothing in this module may be imported by `db/` or `api/`.

Every function here does its whole job inside the caller's transaction and
never commits: the caller owns that, matching the convention the rest of
`admin/` follows.

**Cache invalidation** is out of scope here. Contract §5.2 says publish
must, within its transaction, "archive the current published set, publish
the target, write an audit_log entry, invalidate the cache." The first
three happen below; the fourth belongs to B's repository (whatever caches
the published factor set for the calculator's hot path) and is not
implemented in this admin-side copy — there is no cache here to invalidate.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from admin.audit import row_to_dict, write_audit
from admin.expressions import ExpressionError, validate_expression
from admin.factor_models import (
    Constant, Equivalence, FactorDownstream, FactorSet, FactorSetStatus,
    FactorUpstream, Formula,
)
from admin.models import utcnow
from admin.taxonomy_rules import TaxonomyInvariantError


class LifecycleError(Exception):
    """A factor-set lifecycle operation could not be carried out.

    The message is shown to the staff member who attempted it, so it says
    what is wrong and what to do, not which function raised.
    """


def _revalidate_formulas(session, factor_set_id: int) -> None:
    """Re-run §4.3's own check against every formula in one factor_set.

    Moved here from admin/factor_views.py (Task 3): publish_factor_set below
    needs the same check, and this module may not import from a views
    module, so the shared logic lives here instead and factor_views.py
    imports it back.

    Used two ways: ConstantAdmin.validate_before_commit (factor_views.py)
    calls it because a formula that validated cleanly when saved can be
    broken later by an edit to a *different* row — deleting or renaming the
    constant it referenced — and nothing about the formula row itself
    changes when that happens, so FormulaAdmin's own validate_before_commit
    never fires. publish_factor_set calls it for the same underlying reason:
    publishing is the last moment the calculator is still guaranteed to
    work, so it is the right place to catch a formula a constant-deletion
    already broke, however long ago.

    Raises TaxonomyInvariantError naming the broken formula's metric, so the
    caller sees which formula would be stranded, not just that something,
    somewhere, broke.
    """
    codes = session.scalars(
        select(Constant.code).where(Constant.factor_set_id == factor_set_id)
    ).all()
    formulas = session.scalars(
        select(Formula).where(Formula.factor_set_id == factor_set_id)
    ).all()
    for formula in formulas:
        try:
            validate_expression(formula.expression, constant_codes=codes)
        except ExpressionError as exc:
            raise TaxonomyInvariantError(
                f"This change breaks the formula for metric "
                f"'{formula.metric.code}': line {exc.line}, column "
                f"{exc.column}: {exc.message} Fix or remove that formula "
                "first."
            ) from exc


def _clone_children(session, model, source_id: int, new_set_id: int) -> None:
    """Copy every row of one child kind from `source_id` to `new_set_id`.

    Called once per named model from `clone_factor_set` below — never from a
    loop over `Base.metadata`, which would silently pick up whatever table
    someone adds next, including tables (`submission`, for instance) that
    must keep pointing at the original factor set rather than the clone.
    """
    rows = session.scalars(
        select(model).where(model.factor_set_id == source_id)
    ).all()
    for row in rows:
        fields = {
            key: value
            for key, value in row_to_dict(row).items()
            if key not in ("id", "factor_set_id")
        }
        session.add(model(factor_set_id=new_set_id, **fields))


def clone_factor_set(session, source_id: int, new_label: str, actor: str) -> int:
    """Deep-copy `source_id` into a new draft and return the new id.

    Contract §5.2: "This is the recommended path for staff edits: clone,
    edit, publish." Every factor row, constant, formula and equivalence
    belonging to the source is duplicated under the new factor set; nothing
    else references the clone, so editing it can never change the source's
    own published numbers.

    The clone is always a draft — even when the source is published, since
    two published rows is the one state contract §2.2 forbids outright — and
    never inherits `published_at` / `published_by`, which describe an event
    that happened to the source, not to a row that did not exist yet.
    """
    label = (new_label or "").strip()
    if not label:
        raise LifecycleError("A version label is required to clone a factor set.")

    source = session.get(FactorSet, source_id)
    if source is None:
        raise LifecycleError(f"No factor set with id {source_id} exists to clone.")

    # TOCTOU: another write could insert the same label between this check
    # and the flush below. Left as a plain pre-check rather than catching
    # the resulting IntegrityError — this is a single-operator admin panel,
    # not a public endpoint, so the race is not worth the extra complexity.
    duplicate = session.scalar(
        select(FactorSet).where(FactorSet.version_label == label)
    )
    if duplicate is not None:
        raise LifecycleError(
            f"A factor set labelled '{label}' already exists. Choose a "
            "different label."
        )

    clone = FactorSet(
        version_label=label,
        status=FactorSetStatus.draft,
        is_mock=source.is_mock,
        effective_from=None,
        published_at=None,
        published_by=None,
        notes=source.notes,
    )
    session.add(clone)
    session.flush()

    for model in (FactorUpstream, FactorDownstream, Constant, Formula, Equivalence):
        _clone_children(session, model, source_id, clone.id)
    session.flush()

    write_audit(
        session, actor=actor, action="create", table_name="factor_set",
        row_id=clone.id, before=None, after=row_to_dict(clone),
    )

    return clone.id


def _lock_factor_sets(session: Session) -> list[FactorSet]:
    """Every factor_set row, locked for the rest of this transaction.

    Two staff members publishing different drafts at the same moment is a
    real race: each reads "one published", each archives it, each publishes
    its own, and the table ends with two published rows — the one state §2.2
    forbids. A check that runs after the fact cannot settle it, because both
    transactions pass their check before either commits.

    Locking every row rather than the published one is deliberate: there may
    be no published row to lock, which is exactly the fresh-deployment case,
    and `SELECT ... FOR UPDATE` over an empty result set locks nothing.
    """
    return list(session.scalars(select(FactorSet).with_for_update()).all())


def publish_factor_set(session: Session, factor_set_id: int, actor: str) -> None:
    """Publish `factor_set_id`, archiving whatever was published before it.

    Contract §5.2: "Within one transaction: archive the current published
    set, publish the target, write an audit_log entry, invalidate the
    cache. Rolls back if the 'at most one published' invariant would be
    violated." Cache invalidation is not implemented here — see this
    module's own docstring.

    Locks every factor_set row first (`_lock_factor_sets`) so the whole
    decision — what is currently published, whether the target may become
    published — is made against a snapshot nothing else can change out from
    under it before this transaction commits.

    Refuses, via LifecycleError, when: the target does not exist; the target
    is already published (nothing to do, and stamping fresh publish/actor
    values over an unchanged row would misrepresent the audit trail); more
    than one row is already published (§2.2's invariant is already broken —
    the fix is an operator choosing which survives, not this function
    silently archiving one); or the target's own formulas do not all
    validate against its own constants (`_revalidate_formulas` — publishing
    is the last moment the calculator is still guaranteed to work).

    Zero published rows beforehand is not an error — see
    test_publishing_the_first_set_needs_no_predecessor and §9's
    NO_PUBLISHED_FACTOR_SET, the designed response to that state.
    """
    rows = _lock_factor_sets(session)
    by_id = {row.id: row for row in rows}

    target = by_id.get(factor_set_id)
    if target is None:
        raise LifecycleError(f"No factor set with id {factor_set_id} exists to publish.")
    if target.status is FactorSetStatus.published:
        raise LifecycleError(f"'{target.version_label}' is already published.")

    published = [row for row in rows if row.status is FactorSetStatus.published]
    if len(published) > 1:
        labels = ", ".join(sorted(row.version_label for row in published))
        raise LifecycleError(
            f"{len(published)} factor sets are already marked as published "
            f"({labels}). Exactly one may be published at a time — archive "
            "all but one before publishing another."
        )

    try:
        _revalidate_formulas(session, factor_set_id)
    except TaxonomyInvariantError as exc:
        raise LifecycleError(str(exc)) from exc

    changes: list[tuple[FactorSet, dict, str]] = []

    if published:
        previous = published[0]
        before = row_to_dict(previous)
        previous.status = FactorSetStatus.archived
        changes.append((previous, before, "archive"))

    before_target = row_to_dict(target)
    target.status = FactorSetStatus.published
    target.published_at = utcnow()
    target.published_by = actor
    changes.append((target, before_target, "publish"))

    session.flush()

    for row, before, action_name in changes:
        write_audit(
            session, actor=actor, action=action_name, table_name="factor_set",
            row_id=row.id, before=before, after=row_to_dict(row),
        )


def rollback_to(session: Session, factor_set_id: int, actor: str) -> None:
    """Restore an archived factor set to published, archiving whatever is
    published now.

    Same shape as publish_factor_set above — same lock, same "at most one
    published" refusal, same formula re-validation — with one difference:
    the target must already be `archived`. Rollback restores something that
    was live before; a draft has never been live, and publishing it is a
    different decision with a different audit meaning (that is what
    publish_factor_set is for).

    `_revalidate_formulas` runs here too, even though an archived set's own
    rows cannot be edited in place once it has been through
    publish_factor_set (every factor view refuses that — see
    admin/factor_views.py's `_refuse_if_factor_set_not_draft`). That
    reasoning has a gap this function must not rely on: `FactorSetAdmin`'s
    edit form lets `status` be set directly, so a draft can be moved
    straight to `archived` without ever passing through
    publish_factor_set's own check — see
    tests/admin/test_factor_set_view.py's
    test_a_status_change_through_the_edit_form_is_ignored, which is what
    caught this. Re-running the check here costs one query and removes the
    dependency on that history entirely.
    """
    rows = _lock_factor_sets(session)
    by_id = {row.id: row for row in rows}

    target = by_id.get(factor_set_id)
    if target is None:
        raise LifecycleError(f"No factor set with id {factor_set_id} exists to roll back to.")
    if target.status is not FactorSetStatus.archived:
        raise LifecycleError(
            f"'{target.version_label}' is {target.status.value}, not "
            "archived. Rollback restores a set that was previously "
            "published; only an archived set qualifies."
        )

    published = [row for row in rows if row.status is FactorSetStatus.published]
    if len(published) > 1:
        labels = ", ".join(sorted(row.version_label for row in published))
        raise LifecycleError(
            f"{len(published)} factor sets are already marked as published "
            f"({labels}). Exactly one may be published at a time — archive "
            "all but one before rolling back to another."
        )

    try:
        _revalidate_formulas(session, factor_set_id)
    except TaxonomyInvariantError as exc:
        raise LifecycleError(str(exc)) from exc

    changes: list[tuple[FactorSet, dict, str]] = []

    if published:
        previous = published[0]
        before = row_to_dict(previous)
        previous.status = FactorSetStatus.archived
        changes.append((previous, before, "archive"))

    before_target = row_to_dict(target)
    target.status = FactorSetStatus.published
    target.published_at = utcnow()
    target.published_by = actor
    changes.append((target, before_target, "rollback"))

    session.flush()

    for row, before, action in changes:
        write_audit(
            session, actor=actor, action=action, table_name="factor_set",
            row_id=row.id, before=before, after=row_to_dict(row),
        )
