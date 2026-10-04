"""A supply-chain stage says on the card what it is, and in the panel what it covers

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-04

Contract v1.96. Step 1 offers six sector cards, each with a one-line
description beside its title and a *Details* button. Measured against the
running stack before this revision: all six `sector.description` values are
`NULL`, so the line beside every title was empty and every panel printed

    Additional details have not been supplied.

which is a translated placeholder standing in for content that has existed in
`tests/fixtures/taxonomy.json` since C wrote it. The owner reported the
placeholder; the copy was never the thing missing.

**One column, not two, and this revision does not add one.** §2.1's ruling
(change-log item 12) was that `sector.details` be folded into `description`:
the fixture briefly carried a `details` string per sector, the renderer read
`sector.details || sector.description || t('...not been supplied.')`, and no
version of the contract has ever defined `details`. The fixture was changed at
the time and the renderer was not, so against a real API the panel repeated the
description and against a live database it printed the placeholder. v1.96 lands
the other half: `web/js/calculator.js`'s `sectorCopy` splits the one field at
its first sentence -- the card takes the sentence, the panel takes the rest,
and a description with no sentence break draws no panel at all.

**Why this migration writes data, and what stops it overwriting anybody.**

`admin/seed.py` only ever creates rows that are absent, so a deployment whose
`sector` table already exists -- which is every deployment -- would never
receive these strings from the seed. 0015 (`unit_preset`) and 0020
(`equivalence.description`) record the same reasoning and are the precedent
followed here.

The UPDATE is conditional on `description IS NULL`, so it seeds and never
corrects: a sentence a staff member has typed or edited through the panel is
left exactly as it is, and re-running this revision after an edit changes
nothing. No `audit_log` row is written, deliberately and for 0015's reason: a
migration has no actor, and the deployment record for this change is the
revision itself.

**This is the team's wording of the MfE supply-chain stages, not the client's
own copy**, and it is seeded so the feature is testable end to end rather than
demonstrable only against a hand-edited row. The owner is putting it to the
client; a correction is a staff edit, not a migration. It is independent of
O-1: these are stage definitions, not emissions factors, and nothing here
changes a number. `web/css/styles.css`'s note over `.stage-select` attributes
the empty descriptions to O-1 -- that attribution was loose, and v1.96
corrects it.

**The downgrade cannot be conditional in the same way** and says so below.
"""
from alembic import op
import sqlalchemy as sa

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


#: (code, description). The prose is `tests/fixtures/taxonomy.json`'s and
#: `admin/seed.py`'s, character for character;
#: `test_sector_descriptions_are_the_shipped_seeds` asserts all three agree,
#: because three copies of client-facing wording drift faster than two.
SECTOR_DESCRIPTIONS = [
    (
        "primary_production",
        "Growing, farming, fishing or harvesting food. This covers farms, orchards, "
        "vineyards, fisheries and other operations where food is grown, raised or caught. "
        "Typical losses are crops left unharvested, produce rejected on grade or "
        "appearance, and food lost during first handling.",
    ),
    (
        "processing",
        "Producing, preparing, processing or packaging food. This covers factories, "
        "bakeries, packhouses and processing facilities. Typical losses are trimming waste, "
        "damaged product, production errors, rejected batches and food lost during "
        "packaging.",
    ),
    (
        "wholesale_retail",
        "Storing, moving and selling food — from distributors and wholesalers through to "
        "supermarkets, grocers and markets. Typical losses are storage and transport "
        "damage, stock that passes its date, unsold food and produce pulled from display.",
    ),
    (
        "consumer_household",
        "Food bought for the home and not eaten. Typical losses are leftovers, food that "
        "spoils before it is used, and edible parts removed during preparation.",
    ),
    (
        "consumer_hospitality",
        "Preparing or serving food outside the home for sale. This covers restaurants, "
        "cafés, hotels, caterers and commercial kitchens. Typical losses are preparation "
        "waste, overproduction, buffet waste and what customers leave on the plate.",
    ),
    (
        "consumer_institution",
        "Preparing or serving food in public and community organisations. This covers "
        "schools, universities, hospitals, aged-care facilities, prisons and workplace "
        "cafeterias. Typical losses are kitchen preparation waste, overproduction and "
        "uneaten meals.",
    ),
]


def upgrade() -> None:
    sector = sa.table(
        "sector",
        sa.column("code", sa.String),
        sa.column("description", sa.Text),
    )
    for code, description in SECTOR_DESCRIPTIONS:
        op.execute(
            sector.update()
            .where(sa.and_(sector.c.code == code, sector.c.description.is_(None)))
            .values(description=description)
        )


def downgrade() -> None:
    """Clear exactly the six descriptions this revision wrote -- and no others.

    **It cannot be conditional the way `upgrade` is.** `upgrade` skips a row
    somebody has already written; `downgrade` has no way to tell a string it
    seeded from a string a staff member has since typed, because the column
    holds no provenance. Matching on the text itself is what makes the
    difference recoverable: a row whose description is still exactly what
    `upgrade` wrote is cleared, and a row that has been edited since is left
    alone. A staff member who edited a sentence keeps it; one who did not goes
    back to `NULL`, which is the pre-0021 state and the state `admin/seed.py`
    would not restore.
    """
    sector = sa.table(
        "sector",
        sa.column("code", sa.String),
        sa.column("description", sa.Text),
    )
    for code, description in SECTOR_DESCRIPTIONS:
        op.execute(
            sector.update()
            .where(sa.and_(sector.c.code == code, sector.c.description == description))
            .values(description=None)
        )
