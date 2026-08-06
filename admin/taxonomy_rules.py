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
    """
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
