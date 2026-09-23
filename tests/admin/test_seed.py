"""The NZ taxonomy as shipped. Contract §2.1."""

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from admin.seed import UNIT_PRESETS, seed_taxonomy
from admin.taxonomy_models import (
    Destination, DestinationGroup, FoodCategory, Metric, Sector, UnitPreset,
)
from admin.taxonomy_rules import (
    TaxonomyInvariantError, check_prevention_destination,
    check_single_standard_mix,
)

pytestmark = pytest.mark.db


def test_seeding_satisfies_the_invariants(session):
    """The seed is the one dataset guaranteed to exist, so it had better be
    a legal one — a seed that violates an invariant makes every subsequent
    edit through the panel fail, for reasons the staff member cannot see."""
    seed_taxonomy(session)
    session.flush()

    check_single_standard_mix(session)
    check_prevention_destination(session)


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
    """Six since contract v1.70, when `land` was added.

    `land` is the one column the client's own document has always carried and
    this calculator never reported. It is a plain equality rather than a
    superset check for the same reason the food-category test above is: a
    metric added without a contract revision is a metric no other stream knows
    about, and `metric` is global, so it lands in every factor set.
    """
    seed_taxonomy(session)
    session.flush()

    codes = {m.code for m in session.scalars(select(Metric)).all()}
    assert codes == {"co2e", "ch4", "water", "cost", "mass", "land"}


def test_the_land_unit_is_ascii_and_its_display_unit_is_typographic(session):
    """v1.70. The same split `co2e` uses, and it is not decoration.

    §6.1 rules `display_unit` a presentation variant of `unit` AT THE SAME
    SCALE. `m2` and `m²` are the same quantity written two ways -- exactly
    like `kg CO2e` and `kg CO₂e` -- so this pair is legal where `t` against a
    `kg` unit would not be. `unit` is what §6.2 puts on the wire beside every
    total; `display_unit` is what `api/pdf_render.py` prints, and a PDF
    carrying it was rendered and looked at rather than inferred from a cmap.
    """
    seed_taxonomy(session)
    session.flush()

    land = session.scalar(select(Metric).where(Metric.code == "land"))
    assert (land.name, land.unit, land.display_unit) == ("Land use", "m2", "m²")
    #: Chosen from the magnitudes the draft set's conversion produces
    #: (0.1684 m²/kg for vegetables through 64.0790 for meat): at 0 places a
    #: vegetables entry under 2.97 kg would print as no land at all.
    assert land.display_precision == 1


def test_unit_presets_are_marked_as_placeholder_data(session):
    """The client has not supplied conversion data. A number with no
    provenance is the kind that ends up in a report."""
    seed_taxonomy(session)
    session.flush()

    for preset in session.scalars(select(UnitPreset)).all():
        assert "placeholder" in (preset.source_note or "").lower()


def test_migration_0015_and_the_seed_hold_the_same_ten_containers():
    """A transcription, and therefore a drift risk with nothing else watching it.

    Revision 0015 writes the container numbers out longhand rather than
    importing `admin.seed`, deliberately — a migration states the numbers of
    its own moment, and a later seed edit must not retroactively change what an
    applied revision did. The cost of that decision is that the two can silently
    disagree, and the disagreement is invisible in normal use: `docker/init.sh`
    runs `alembic upgrade head` **before** `seed-taxonomy`, so on a fresh
    database the migration's INSERT lands first and `_ensure` then creates
    nothing. A wrong number in the seed would never reach a deployment and would
    never reach this suite either, because every test builds its schema with
    `create_all()` and never runs migration DDL.

    So they are compared here. If a future revision deliberately moves the
    numbers on, this test is what makes that a decision: update the seed and add
    the newer revision to the comparison, do not edit 0015.
    """
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "alembic" / "versions" / "0015_unit_preset_nz_containers.py"
    )
    spec = importlib.util.spec_from_file_location("_revision_0015", path)
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)

    migrated = {
        code: (label, Decimal(kilograms), revision._note(litres, kilograms))
        for code, litres, label, kilograms in revision._CONTAINERS
    }
    seeded = {
        code: (label, kg_per_unit, source_note)
        for code, label, kg_per_unit, source_note in UNIT_PRESETS
    }
    assert migrated == seeded


def test_every_unit_preset_shows_the_arithmetic_and_names_its_density_source(session):
    """A sourced conversion, not a bare number — and still not a measurement.

    The set this replaced carried one sentence saying the numbers were made up,
    which was honest and useless: a staff member replacing them had no way to
    tell what they were replacing. Every row now states its own capacity, the
    density, the product, and where the density came from.

    **The last assertion is the one that matters.** 0.29 kg/L is a published
    figure from the FLW Protocol's Table 3.2, not a New Zealand measurement, and
    a note that cited a source without saying so would read as authoritative
    data — which is worse than the bare placeholder it replaced, and is exactly
    what O-6 was raised about. O-6 stays open until a measured figure arrives,
    so the disclaimer has to survive any later edit to the citation.
    """
    seed_taxonomy(session)
    session.flush()

    presets = session.scalars(select(UnitPreset)).all()
    assert len(presets) == 10
    for preset in presets:
        note = preset.source_note or ""
        litres, _, rest = note.partition(" L × 0.29 kg/L = ")
        assert litres.isdigit(), f"{preset.code}: note does not open with a capacity"
        product, _, _ = rest.partition(" kg.")
        assert Decimal(product) == preset.kg_per_unit, (
            f"{preset.code}: the note quotes {product} kg and the row holds "
            f"{preset.kg_per_unit}"
        )
        assert Decimal(litres) * Decimal("0.29") == preset.kg_per_unit, (
            f"{preset.code}: {litres} L at 0.29 kg/L is not {preset.kg_per_unit}"
        )
        assert "Food Loss & Waste Protocol" in note, f"{preset.code}: no source"
        assert "not a New Zealand measurement" in note, (
            f"{preset.code}: the note cites a source without saying the density "
            "is not measured here, which reads as data the client has supplied"
        )
