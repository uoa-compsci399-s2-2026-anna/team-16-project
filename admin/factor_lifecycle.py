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
"""

from sqlalchemy import select

from admin.audit import row_to_dict, write_audit
from admin.factor_models import (
    Constant, Equivalence, FactorDownstream, FactorSet, FactorSetStatus,
    FactorUpstream, Formula,
)


class LifecycleError(Exception):
    """A factor-set lifecycle operation could not be carried out.

    The message is shown to the staff member who attempted it, so it says
    what is wrong and what to do, not which function raised.
    """


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
