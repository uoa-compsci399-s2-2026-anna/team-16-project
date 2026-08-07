"""The NZ taxonomy as shipped. Contract §2.1."""

import pytest
from sqlalchemy import func, select

from admin.seed import seed_taxonomy
from admin.taxonomy_models import (
    Destination, DestinationGroup, FoodCategory, Metric, Sector, UnitPreset,
)
from admin.taxonomy_rules import (
    TaxonomyInvariantError, check_prevention_intact, check_single_standard_mix,
)

pytestmark = pytest.mark.db


def test_seeding_satisfies_the_invariants(session):
    """The seed is the one dataset guaranteed to exist, so it had better be
    a legal one — a seed that violates an invariant makes every subsequent
    edit through the panel fail, for reasons the staff member cannot see."""
    seed_taxonomy(session)
    session.flush()

    check_single_standard_mix(session)
    check_prevention_intact(session)


def test_seed_refuses_to_commit_a_taxonomy_it_would_leave_broken(session):
    """A staff member can rename `standard_mix`'s *code* through the panel -
    `code` is in FoodCategoryAdmin.form_columns, so this is a supported edit,
    not a misuse of the tool. Re-running the seed afterwards does not see the
    renamed row as "standard_mix" any more, so it creates a fresh one - and
    without this check, that fresh row and the renamed one both still carry
    is_standard_mix=True, giving two active standard mixes with nothing to
    refuse the commit. The seed has to catch what it just did to itself,
    inside the same transaction the CLI is about to commit, or a taxonomy no
    view would ever accept gets written anyway - seed_taxonomy is not called
    through AuditedModelView and its validate_before_commit hook at all."""
    seed_taxonomy(session)
    session.flush()
    renamed = session.scalar(
        select(FoodCategory).where(FoodCategory.code == "standard_mix")
    )
    renamed.code = "mixed"
    session.flush()

    with pytest.raises(TaxonomyInvariantError):
        seed_taxonomy(session)


def test_seeding_twice_creates_nothing_the_second_time(session):
    """Idempotent: it runs on every deployment, not only the first."""
    seed_taxonomy(session)
    session.flush()
    before = session.scalar(select(func.count()).select_from(Destination))

    created = seed_taxonomy(session)
    session.flush()

    assert session.scalar(select(func.count()).select_from(Destination)) == before
    assert sum(created.values()) == 0


def test_seeding_does_not_overwrite_a_staff_edit(session):
    """Staff rename things. A seed that reset those names on every deploy
    would quietly undo their work, and they would have no way to tell."""
    seed_taxonomy(session)
    session.flush()
    landfill = session.scalar(select(Destination).where(Destination.code == "landfill"))
    landfill.name = "Landfill (municipal)"
    session.flush()

    seed_taxonomy(session)
    session.flush()

    assert session.scalar(
        select(Destination).where(Destination.code == "landfill")
    ).name == "Landfill (municipal)"


def test_reuse_is_not_waste_and_the_other_two_groups_are(session):
    """Per MfE's 2023 definition — the classification the whole "is this
    waste" question rests on."""
    seed_taxonomy(session)
    session.flush()

    by_code = {
        g.code: g.is_waste
        for g in session.scalars(select(DestinationGroup)).all()
    }
    assert by_code["reuse"] is False
    assert by_code["recycle_recovery"] is True
    assert by_code["disposal"] is True


def test_prevention_is_seeded_and_sits_outside_waste(session):
    """It represents waste that did not happen, so counting it as waste
    would make the alternative scenario worse than the current one."""
    seed_taxonomy(session)
    session.flush()

    prevention = session.scalar(
        select(Destination).where(Destination.code == "prevention")
    )
    assert prevention is not None
    assert prevention.group.is_waste is False


def test_every_sector_the_contract_names_is_present(session):
    seed_taxonomy(session)
    session.flush()

    codes = {s.code for s in session.scalars(select(Sector)).all()}
    assert codes == {
        "primary_production", "processing", "wholesale_retail",
        "consumer_household", "consumer_hospitality", "consumer_institution",
    }


def test_every_food_category_the_contract_names_is_present(session):
    """Pinned deliberately, unlike the sector and metric sets above: this is
    the one code set actually in dispute. Contract §2.1's prose says "the
    eight Otago baseline categories", but the client's own source list has
    nine substantive entries, and O-5 (docs/architecture.md §10) seeds all
    nine pending the client's ruling. When that ruling lands, the change to
    FOOD_CATEGORIES has to fail this test rather than pass silently - a
    diff nobody wrote a test to catch is a diff nobody notices."""
    seed_taxonomy(session)
    session.flush()

    codes = {c.code for c in session.scalars(select(FoodCategory)).all()}
    assert codes == {
        "standard_mix", "fruit", "vegetables", "nuts_seeds", "meat",
        "seafood", "dairy", "bakery_grains", "staples", "beverages",
    }


def test_every_metric_the_contract_names_is_present(session):
    seed_taxonomy(session)
    session.flush()

    codes = {m.code for m in session.scalars(select(Metric)).all()}
    assert codes == {"co2e", "ch4", "water", "cost", "mass"}


def test_unit_presets_are_marked_as_placeholder_data(session):
    """The client has not supplied conversion data. A number with no
    provenance is the kind that ends up in a report."""
    seed_taxonomy(session)
    session.flush()

    for preset in session.scalars(select(UnitPreset)).all():
        assert "placeholder" in (preset.source_note or "").lower()
