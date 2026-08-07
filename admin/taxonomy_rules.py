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

#: The destination expressing "this waste did not happen". The engine looks
#: it up by this exact string; it is not configurable.
PREVENTION_CODE = "prevention"


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


def check_prevention_intact(session: Session) -> None:
    """Contract §2.1: the `prevention` destination must exist and be usable.

    All of its factors are zero, which is how "waste avoided" is expressed
    while keeping the current and alternative scenarios mass-conserving. The
    engine resolves it by code, so renaming it and deleting it are the same
    event as far as a calculation is concerned.

    "Usable" also requires its *group* to be active, not only the row
    itself: every active-destination listing is built by joining through
    `destination_group`, so a deactivated group drops `prevention` out of
    service exactly as surely as deactivating `prevention` directly would -
    and is the likelier route, since a staff member tidying up the group
    list acts on `DestinationGroup` rows, never on `prevention` by name.

    Skipped entirely on an empty destination table. A database with no
    destinations at all is one that has not been seeded yet, and refusing to
    create the first destination group because `prevention` does not exist
    yet would make the panel impossible to bootstrap by hand.
    """
    any_destination = session.scalar(select(Destination).limit(1))
    if any_destination is None:
        return
    prevention = session.scalar(
        select(Destination).where(Destination.code == PREVENTION_CODE)
    )
    if prevention is None:
        raise TaxonomyInvariantError(
            f"No destination with the code '{PREVENTION_CODE}' exists. It is "
            "required: it represents waste that was avoided, and without it "
            "the calculator cannot express an improved scenario. Restore it "
            "before saving."
        )
    if not prevention.active:
        raise TaxonomyInvariantError(
            f"The '{PREVENTION_CODE}' destination is deactivated. It is "
            "required and must stay active: it represents waste that was "
            "avoided, and without it the calculator cannot express an "
            "improved scenario."
        )
    if not prevention.group.active:
        raise TaxonomyInvariantError(
            f"The destination group '{prevention.group.code}', which contains "
            f"'{PREVENTION_CODE}', is deactivated. It must stay active: "
            f"deactivating the group removes '{PREVENTION_CODE}' from every "
            "active-destination listing just as surely as deactivating "
            "'prevention' itself, and without it the calculator cannot "
            "express an improved scenario. Reactivate the group before saving."
        )


def check_single_published_set(session: Session) -> None:
    """Contract §2.2: at most one factor_set row may be published.

    Zero is legitimate — a fresh deployment has none, and §9's
    NO_PUBLISHED_FACTOR_SET (503, "calculator under maintenance") is the
    designed response. Two is not: which numbers the public calculator uses
    would come down to which row the query happened to return first, and the
    two versions exist precisely because they disagree.
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
