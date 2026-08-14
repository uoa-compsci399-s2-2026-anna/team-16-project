"""Clone, publish, roll back and archive a factor set. Contract §5.2.

**Ownership note.** §5.2 places the first three of these functions
(clone/publish/rollback) in `db/repository.py`, which B owns, and B has an
implementation on an unmerged branch. They live here for now because the
panel cannot do its job without them and that branch has no landing date.
When it lands, one of the two implementations goes and the other is
imported — `admin/` may import from `db/`, never the reverse. Nothing in
this module may be imported by `db/` or `api/`.

`archive_factor_set` is this module's own addition, not one of §5.2's
three: before it existed, the only way a set ever reached `archived` was as
a side effect of publish/rollback promoting a different one, leaving no way
to take the calculator offline when the live factors need pulling and
nothing else is ready to replace them. See its own docstring below.

Every function here does its whole job inside the caller's transaction and
never commits: the caller owns that, matching the convention the rest of
`admin/` follows.

**Cache invalidation** is out of scope here. Contract §5.2 says publish
must, within its transaction, "archive the current published set, publish
the target, write an audit_log entry, invalidate the cache." The first
three happen below; the fourth belongs to B's repository (whatever caches
the published factor set for the calculator's hot path) and is not
implemented in this admin-side copy — there is no cache here to invalidate.

`db/repository.py`'s `_bundle_cache` is real, and calling
`invalidate_factor_bundle` from this module would still not reach it: the
panel and the API are **separate processes** (docker/compose.yaml runs
`api` and `admin` as two services), and that cache is a module-level dict
per process. An admin-side invalidation clears the panel's own copy, which
is not the one serving the public. That is why `set_placeholder_flag` below
does not rely on one — see `db/repository.load_factor_bundle`, which
re-reads this one flag on every cache hit for exactly this reason.
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
from db.errors import FactorSetStateError
from db.repository import (
    find_missing_prevention_upstream,
    prevention_destination_codes,
    refuse_nonzero_prevention_factors,
)


class LifecycleError(Exception):
    """A factor-set lifecycle operation could not be carried out.

    The message is shown to the staff member who attempted it, so it says
    what is wrong and what to do, not which function raised.
    """


def revalidate_formulas(session, factor_set_id: int) -> None:
    """Re-run §4.3's own check against every formula in one factor_set.

    Moved here from admin/factor_views.py (Task 3): publish_factor_set below
    needs the same check, and this module may not import from a views
    module, so the shared logic lives here instead and factor_views.py
    imports it back. Named without a leading underscore because it is
    exactly that shared, cross-module dependency, not a private helper of
    this file alone.

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
    validate against its own constants (`revalidate_formulas` — publishing
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
        revalidate_formulas(session, factor_set_id)
    except TaxonomyInvariantError as exc:
        raise LifecycleError(str(exc)) from exc

    # Contract §2.2's O-7 completeness rule, enforced here as well as in
    # db/repository.publish_factor_set because **this is the copy the panel
    # calls** — admin/factor_views.py's publish action imports from this
    # module, and the repository's copy has no caller outside its own tests
    # until the two implementations are unified (see this module's docstring).
    # A guard placed only there would leave the staff path, which is the only
    # path a human takes, entirely unguarded.
    #
    # The *query* lives in db/repository.py and is imported rather than
    # repeated: `admin/` may import from `db/` and this module already relies
    # on that direction being legal. Two copies of a rule drift, and the copy
    # that stops matching is the one nobody notices — the same reasoning
    # that put the prevention *role* on one column instead of leaving a
    # code literal in five modules.
    #
    # Not applied to rollback_to below: rollback is the "put the calculator
    # back to a state that worked" operation, and a set archived before v1.8
    # will legitimately fail this check. Refusing an emergency rollback over a
    # completeness rule would be a worse failure than the one it prevents.
    #: Never empty where this message is built: with no flagged destination
    #: `find_missing_prevention_upstream` returns [] and there is nothing to
    #: report. A fallback literal here would be the magic string coming back
    #: in the one place a staff member reads.
    codes = ", ".join(sorted(prevention_destination_codes(session)))
    missing = find_missing_prevention_upstream(session, factor_set_id)
    if missing:
        listed = ", ".join(f"{sector}/{food}/{metric}" for sector, food, metric in missing)
        raise LifecycleError(
            f"{len(missing)} factor combinations have an upstream factor but "
            "no prevention upstream row at 0 — either it is missing or it "
            "carries a non-zero value — so a prevented line would still be "
            "charged upstream impact and the calculator would understate the "
            f"benefit of preventing waste for them: {listed}. Add or correct "
            f"an upstream factor of 0 against a prevention destination "
            f"({codes}) for each, then publish."
        )

    # The other half of the same property, and the half nothing checked at all
    # until the flag existed: a prevention destination priced at anything but
    # zero is not a 100% offset, so the improved scenario stops describing the
    # same mass at no cost. `_refuse_incomplete_prevention` above can only see
    # a non-zero value where a generic row exists to compare it against, which
    # a set built §10.3's way — one explicit row per destination, no generic
    # rows — has none of. An *absent* row is still zero by §4.1's lookup and
    # stays legal; this refuses only a row that exists and disagrees.
    try:
        refuse_nonzero_prevention_factors(session, factor_set_id)
    except FactorSetStateError as exc:
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

    `revalidate_formulas` runs here too, even though an archived set's own
    rows cannot be edited in place once it has been through
    publish_factor_set (every factor view refuses that — see
    admin/factor_views.py's `_refuse_if_factor_set_not_draft`). `status` is
    not on `FactorSetAdmin.form_columns` at all, so sqladmin's generic edit
    route cannot move a draft straight to `archived` or `published` the way
    an earlier version of this project let it — see
    tests/admin/test_factor_set_view.py's
    test_a_status_change_through_the_edit_form_is_ignored, which now proves
    a submitted `status` field is silently ignored. The revalidation here is
    kept anyway, as defence in depth rather than because a known gap still
    exists: it costs one query, and it keeps this function correct on its
    own even if the form-level guard is ever loosened again, rather than
    depending on that guard's history to still hold.
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
        revalidate_formulas(session, factor_set_id)
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


def archive_factor_set(session: Session, factor_set_id: int, actor: str) -> None:
    """Archive `factor_set_id` directly - no other set is promoted.

    Before this existed, the only way any set ever reached `archived` was as
    a side effect of publish_factor_set/rollback_to promoting a different
    one. That leaves staff who find an error in the live factors with no
    route to take the calculator offline: publishing something else
    requires something else to publish, which may not exist. This is that
    route.

    Archiving the currently published set — leaving zero factor sets
    published — is not refused. That is precisely "take the calculator
    offline", and contract §9's NO_PUBLISHED_FACTOR_SET (503, "calculator
    under maintenance") is the designed response to reaching that state, not
    an error condition this function should stand in the way of. The only
    thing refused is archiving a set that is already archived — nothing
    changes, and stamping a fresh audit "archive" entry over an unchanged
    row would misrepresent the trail the same way re-publishing an already
    published set would (see publish_factor_set above).

    Takes the same `SELECT ... FOR UPDATE` lock as clone/publish/rollback
    (`_lock_factor_sets`) even though there is no "at most one published"
    race to settle here: it is what makes "does factor_set_id exist" a
    question asked against a snapshot nothing else can change out from under
    this transaction before it commits, the same guarantee the other three
    functions rely on it for.

    Never commits — the caller owns the transaction, matching every other
    function in this module.
    """
    rows = _lock_factor_sets(session)
    by_id = {row.id: row for row in rows}

    target = by_id.get(factor_set_id)
    if target is None:
        raise LifecycleError(f"No factor set with id {factor_set_id} exists to archive.")
    if target.status is FactorSetStatus.archived:
        raise LifecycleError(f"'{target.version_label}' is already archived.")

    before = row_to_dict(target)
    target.status = FactorSetStatus.archived
    session.flush()

    write_audit(
        session, actor=actor, action="archive", table_name="factor_set",
        row_id=target.id, before=before, after=row_to_dict(target),
    )


#: The `audit_log.action` each direction of the placeholder flag is recorded
#: under. Not `update`: this is the one switch in the system that makes the
#: public disclaimer appear or disappear, and an entry that says `update
#: factor_set#3` is indistinguishable in a list from somebody fixing a typo
#: in that set's notes. `audit_log.action` is free text (VARCHAR(32)), the
#: same column `publish`, `rollback` and `archive` above already widen.
FLAG_PLACEHOLDER_ACTION = "flag_placeholder"
CLEAR_PLACEHOLDER_ACTION = "clear_placeholder"


def set_placeholder_flag(
    session: Session, factor_set_id: int, *, is_mock: bool, actor: str
) -> None:
    """Turn one factor set's placeholder-data flag on or off. Contract §2.2.

    **The only sanctioned write path for `factor_set.is_mock`.** It used to
    be a tick on FactorSetAdmin's generic edit form, which meant the flag
    that holds up the mandatory, non-dismissible placeholder warning on every
    public result and export (§7.6.2) could be carried off by somebody
    editing a version label. It is an action now, and this is the function
    behind it — here rather than in the view for the same reason
    `archive_factor_set` is here: a rule that lives in a request handler is a
    rule no other caller obeys, and this module is what a CLI command would
    reach for if one is ever wanted.

    **The two directions are not symmetric, and only one half of that
    asymmetry is enforceable here.** Adding the warning (`is_mock=True`) is
    always safe and always allowed. Removing it is the consequential half and
    the panel asks for a password or a live TOTP code before calling this
    with `is_mock=False` (admin/factor_views.py's `clear_placeholder_page`).
    That proof cannot be checked from here — it needs the request's form and
    the login throttle, neither of which a service function has — so what
    this function guarantees is the other three things a caller cannot skip:
    the change is recorded, it is recorded under an action name that says
    which direction it went, and it is refused when it would change nothing.

    **No status restriction, deliberately, and this is the rule that
    changed.** The panel used to refuse the flag on a published set outright
    and tell staff to clone first. The workflow it was refusing is the real
    one: staff publish the real factors, let them run publicly for a day or
    two to check them, and only then clear the flag. Forcing a clone at that
    point manufactures a new `factor_set` row, and a new version label, for a
    change in which not one factor value differs — while every `submission`
    recorded during those verification days stamps the old id. That is a
    version discontinuity created by the workflow rather than by the data.
    A published set's *other* fields are still immutable in place, and
    FactorSetAdmin.validate_before_commit still refuses them.

    Refuses a no-op — a set already flagged the way the caller asked for —
    for the reason `publish_factor_set` refuses an already-published target:
    an audit entry claiming a change that did not happen is worse than no
    entry, and this is the trail somebody reads to answer "when did the
    warning come off, and who took it off".

    Takes `_lock_factor_sets` like the four functions above, so "does this
    set exist and what does its flag say" is asked against a snapshot nothing
    else can move before this transaction commits. Never commits.
    """
    rows = _lock_factor_sets(session)
    target = next((row for row in rows if row.id == factor_set_id), None)
    if target is None:
        raise LifecycleError(
            f"No factor set with id {factor_set_id} exists to change."
        )
    if target.is_mock == is_mock:
        state = "already flagged as placeholder data" if is_mock else \
            "already not flagged as placeholder data"
        raise LifecycleError(f"'{target.version_label}' is {state}.")

    before = row_to_dict(target)
    target.is_mock = is_mock
    session.flush()

    write_audit(
        session, actor=actor,
        action=FLAG_PLACEHOLDER_ACTION if is_mock else CLEAR_PLACEHOLDER_ACTION,
        table_name="factor_set", row_id=target.id,
        before=before, after=row_to_dict(target),
    )
