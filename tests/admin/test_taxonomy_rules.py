"""The two taxonomy invariants no column constraint can express."""

import pytest
from sqlalchemy import select

from admin.taxonomy_models import Destination, DestinationGroup, FoodCategory
from admin.taxonomy_rules import (
    TaxonomyInvariantError, check_prevention_intact, check_single_standard_mix,
)

pytestmark = pytest.mark.db


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
    which is exactly the failure mode the hook exists to prevent.
    """
    from admin.modelviews import AuditedModelView

    assert hasattr(AuditedModelView, "validate_before_commit")
    # The default must not raise: most views have no invariant, and one
    # inherited by accident would refuse every write in the panel.
    AuditedModelView.validate_before_commit(object(), session)
