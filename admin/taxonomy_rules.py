"""Cross-row taxonomy invariants. Contract §2.1.

Neither of these can be a column constraint — both are statements about the
table as a whole. They are checked inside the transaction that is about to
commit (see AuditedModelView.validate_before_commit), which is the only
point that sees the final state and can still refuse it. Checking in a
service function instead would leave sqladmin's generic edit path — plain
setattr then commit, no service function anywhere near it — free to break
them.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from admin.factor_models import FactorSet, FactorSetStatus
from admin.taxonomy_models import Destination, FoodCategory


class TaxonomyInvariantError(Exception):
    """A change would leave the taxonomy in a state the engine cannot use.

    The message is shown to the staff member who attempted the change, so it
    says what is wrong and what to do, not which function raised.
    """


def check_single_standard_mix(session: Session) -> None:
    """Contract §2.1: exactly one food_category row must be the standard mix.

    Counts active rows only. A deactivated standard mix is as unusable to the
    engine as a missing one, and deactivation is the likelier route — every
    taxonomy view sets can_delete = False.
    """
    count = session.scalar(
        select(func.count())
        .select_from(FoodCategory)
        .where(FoodCategory.is_standard_mix.is_(True), FoodCategory.active.is_(True))
    )
    if count == 1:
        return
    if count == 0:
        raise TaxonomyInvariantError(
            "No active food category is marked as the standard mix. One is "
            "required: it is what the calculator uses when a visitor does not "
            "know the composition of their waste. Mark one category as the "
            "standard mix before saving."
        )
    raise TaxonomyInvariantError(
        f"{count} active food categories are marked as the standard mix. "
        "Exactly one is allowed — the calculator has no way to choose between "
        "them. Clear the mark on all but one."
    )


def check_prevention_destination(session: Session) -> None:
    """Contract §2.1: at least one usable prevention destination must exist.

    A prevention destination's factors are zero, which is how "waste avoided"
    is expressed while keeping the current and alternative scenarios
    mass-conserving. Without one the improvement panel still renders its
    sliders and §6.2 still requires the two scenarios to describe the same
    mass, so the form silently loses the only thing it is for. Redirecting
    mass between real destinations stays expressible; saying "we wasted less"
    does not.

    **The role, not the row.** This checked for the literal code `prevention`
    until the flag existed, which made renaming the row and deleting it the
    same event — and the client has not settled what it will be called. Rename
    it freely now; the invariant is that *something* still carries
    `is_prevention`.

    **At least one, not exactly one**, which is where this departs from
    `check_single_standard_mix` above. Two vocabularies share these global
    tables (§10.3) and each brings its own prevention row, so "exactly one"
    would refuse the state this deployment is already in. There is no ambiguity
    to resolve either: the standard mix has an upper bound because §6.2 must
    resolve a null `food_category` to *one* code, and nothing anywhere has to
    choose between prevention destinations.

    Counted over **active** rows, and over rows whose *group* is active too:
    every active-destination listing is built by joining through
    `destination_group`, so a deactivated group takes its destinations out of
    service exactly as surely as deactivating them directly would — and is the
    likelier route, since a staff member tidying up the group list acts on
    `DestinationGroup` rows and never on this one by name.

    Skipped entirely on an empty destination table. A database with no
    destinations at all is one that has not been seeded yet, and refusing to
    create the first destination group because no prevention destination exists
    yet would make the panel impossible to bootstrap by hand.
    """
    any_destination = session.scalar(select(Destination).limit(1))
    if any_destination is None:
        return
    flagged = session.scalars(
        select(Destination).where(Destination.is_prevention.is_(True))
    ).all()
    if not flagged:
        raise TaxonomyInvariantError(
            "No destination is marked as the prevention destination. One is "
            "required: it represents waste that was avoided, and without it "
            "the calculator cannot express an improved scenario. Mark one "
            "destination as the prevention destination before saving."
        )
    if any(row.active and row.group.active for row in flagged):
        return
    inactive_group = next(
        (row for row in flagged if row.active and not row.group.active), None
    )
    if inactive_group is not None:
        raise TaxonomyInvariantError(
            f"The destination group '{inactive_group.group.code}', which "
            f"contains the prevention destination "
            f"'{inactive_group.code}', is deactivated. It must stay active: "
            "deactivating the group removes the destination from every "
            "active-destination listing just as surely as deactivating the "
            "destination itself, and without it the calculator cannot express "
            "an improved scenario. Reactivate the group before saving."
        )
    listed = ", ".join(sorted(f"'{row.code}'" for row in flagged))
    raise TaxonomyInvariantError(
        f"Every prevention destination ({listed}) is deactivated. At least one "
        "is required and must stay active: it represents waste that was "
        "avoided, and without it the calculator cannot express an improved "
        "scenario."
    )


def check_single_published_set(session: Session) -> None:
    """Contract §2.2: at most one factor_set row may be published.

    Zero is legitimate — a fresh deployment has none, and §9's
    NO_PUBLISHED_FACTOR_SET (503, "calculator under maintenance") is the
    designed response. Two is not: which numbers the public calculator uses
    would come down to which row the query happened to return first, and the
    two versions exist precisely because they disagree.

    Backstop, currently unreachable from FactorSetAdmin's own form: `status`
    is not on `form_columns`, so no path through the generic edit or create
    route can ever produce a second published row for this to catch — the
    lock and refusal inside publish_factor_set/rollback_to
    (admin/factor_lifecycle.py) are what actually stop that today. Kept and
    directly unit-tested anyway as defence in depth against a future form
    change reopening the gap, not because anything currently exercises it
    live.
    """
    published = session.scalars(
        select(FactorSet).where(FactorSet.status == FactorSetStatus.published)
    ).all()
    if len(published) <= 1:
        return
    labels = ", ".join(sorted(fs.version_label for fs in published))
    raise TaxonomyInvariantError(
        f"{len(published)} factor sets are marked as published ({labels}). "
        "Exactly one may be published at a time — the calculator has no way "
        "to choose between them. Archive all but one."
    )
