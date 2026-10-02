"""An equivalence says in one sentence what the comparison means

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-02

Contract v1.80, issue #127. The client, using the tool as a tester, opened the
`?` beside a tangible-equivalence card and read this (measured off the
published set, 19, `CLIENT-DRAFT-2026-09-21`, `is_mock = 1`):

    Basis: The row above, divided by 365. No new source: it is the client's
    own 2.41 t CO2e per passenger vehicle per year (Data sources for impact
    calculator, 2026-08-29) spread over the days of that year, which comes to
    6.60 kg CO2e a day. It is the client's own suggestion -- "if a year is too
    much, change it to a day". "An average day" is the whole day and not a
    journey: the figure includes the hours the vehicle is parked, because the
    year it is divided from does.

That is good writing in the wrong place. It is `equivalence.source_note`, which
exists for whoever audits a conversion factor, and the client's words were:
*write what the copy MEANS, usually one sentence is enough; a visitor reading a
result gets nothing out of a pile of versions.* So the disclosure now prints
`description` -- one staff-authored sentence -- and `source_note` stops
reaching the results page while keeping its column, the admin form, the factor
export and the methodology page. **O-3's reasoning lives in `source_note`**;
this revision moves where it is printed and deletes nothing.

**`VARCHAR(255)`, not `TEXT`, and the limit is the feature.** It matches
`label_template` beside it rather than `source_note` below it. A column that
can hold four sentences is a column that will hold four sentences, which is
the thing being fixed.

**Safe on a populated database.** The column is nullable and `NULL` is a
meaningful value: *print nothing*. A results page rendered against a row with
no sentence shows the figures alone and must NEVER fall back to `source_note` --
that fallback is the long version coming back.

---

**Why this migration writes data, and what stops it overwriting anybody.**

`admin/seed.py` and `docker/seed_mock_factors.py` only reach a fresh database;
0015 records the same reasoning for `unit_preset` and is the precedent followed
here. Without an UPDATE, every deployed set would show an empty disclosure on
every card -- which is correct behaviour and useless as a demonstration.

The UPDATE is conditional on `description IS NULL`, so it seeds and never
corrects: a sentence anybody has typed or edited is left exactly as it is, and
re-running this revision after a staff correction changes nothing. The
downgrade cannot be conditional in the same way, and says why in its own
docstring. No `audit_log` row is written, deliberately, for 0015's reason: a
migration has no actor, and the deployment record for this change is the
revision itself.

**Every sentence below is a DRAFT pending the client's confirmation.** They are
client-facing words about the client's own comparisons; the owner is putting
the list to the client. They are seeded so the feature is testable end to end
rather than demonstrable only against a hand-edited row. Two of them carry a
caveat that currently lives inside the `source_note` prose being taken off the
page, and losing it would make the page more confident than the data warrants:

  * `showers` -- `source_note` opens `PLACEHOLDER`, names open item **O-3**,
    and states BOTH assumptions (how long a shower runs, and how fast). Its
    sentence names the ten minutes, names the nine litres a minute, and says
    the figure is not yet a New Zealand one.
  * `backyard_pools` -- `source_note` records that the 8 m x 4 m x 1.5 m
    dimensions are the team's own judgement rather than a published figure.
    Its sentence states the dimensions and says so.

`km_driven` and `metres_driven` are example rows that exist only in
`tests/fixtures/` and the golden bundles, plus the one superseded set (1) in a
deployed database; their drafts are here so a developer reading either file
sees the shape the column has.

**Every sentence is ASCII.** A character reaching `api/pdf_render.py` from the
database is covered by no font test -- `test_no_character_in_any_catalogue_
would_print_as_a_box` reads the catalogues, and `description` is not in one
(it is staff data, so it is never translated; §7.7.7). ASCII is in every
embedded face and in all four CJK subsets, so nothing has to be re-cut.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: Union[str, Sequence[str], None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: code -> the drafted sentence. Keyed on `code` and not on `id`, and applied
#: to EVERY factor set that carries the code: a deployment holds a chain of
#: cloned sets (one published, the rest draft or archived) and a staff member
#: who clones the published set tomorrow should inherit the sentence rather
#: than a blank.
#:
#: **DRAFTS, pending the client's word.** See the module docstring.
_DRAFTS = {
    "vehicles_year": (
        "The same greenhouse gases an average passenger vehicle puts out in a "
        "year of driving, counted as a number of vehicles."
    ),
    "vehicles_day": (
        "The same greenhouse gases one average passenger vehicle puts out over "
        "a whole day, parked hours included, counted as a number of days."
    ),
    "olympic_pools": (
        "The water used to produce this food, measured as Olympic swimming "
        "pools of 2,500,000 litres each."
    ),
    #: Carries its own caveat: the dimensions are the team's judgement.
    "backyard_pools": (
        "The water used to produce this food, measured as backyard pools of "
        "48,000 litres (8 m by 4 m, 1.5 m deep), which is our own estimate "
        "rather than a published figure."
    ),
    #: Carries its own caveat: PLACEHOLDER, O-3, and both assumptions.
    "showers": (
        "The water used to produce this food, measured as ten-minute showers "
        "at nine litres a minute, which is our assumption and not yet a New "
        "Zealand figure."
    ),
    "meals": "The food counted here, measured as meals of 450 grams each.",
    "km_driven": (
        "The same greenhouse gases as driving an average light petrol car this "
        "far, at a placeholder 0.24 kg CO2e a kilometre."
    ),
    "metres_driven": (
        "The kilometre comparison above expressed in metres, on the same "
        "placeholder figure, so a small result still reads as a number."
    ),
}


def upgrade() -> None:
    op.add_column(
        "equivalence", sa.Column("description", sa.String(255), nullable=True)
    )
    bind = op.get_bind()
    seed = sa.text(
        "UPDATE equivalence SET description = :description "
        "WHERE code = :code AND description IS NULL"
    )
    for code, description in _DRAFTS.items():
        bind.execute(seed, {"code": code, "description": description})


def downgrade() -> None:
    """**This loses every sentence in the column, including staff edits.**

    0015's downgrade reverts only rows still carrying the text it wrote,
    because its column survives the revert and somebody's correction has
    somewhere to stay. Here the column itself goes, so there is no such
    somewhere and no conditional that could create one. Stated rather than
    left for the operator to discover: a revert to 0019 is a revert to a
    schema with nowhere to keep these sentences, and re-upgrading seeds the
    drafts again rather than restoring what was typed.
    """
    op.drop_column("equivalence", "description")
