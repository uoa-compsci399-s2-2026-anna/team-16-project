"""factor_upstream gains a destination dimension

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-09 23:20:52.965347

Open item O-7. `docs/architecture.md` §4.1 has always said that the special
destination `prevention` has "factors are all zero — a 100% offset", and that
this is what stops `net_benefit` being inflated by simply assuming less waste.
Until this migration the data model could not express it: `factor_upstream` was
keyed on (sector, food_category, metric) and could not see the destination, so
a line moved to `prevention` kept the entry's *full upstream factor* and only
the downstream delta reached the net benefit. Measured on the canonical
fixtures, 800 kg of `not_harvested` moved to `prevention` yielded a
`net_benefit.co2e` of 96.000 where a true offset yields 456.000 — 78.9% of the
benefit missing. The failure is one-directional, so prevention was always
understated, which made the client's headline "wasting less" number the weakest
figure on the page.

`destination_id` is nullable and NULL means "every destination for this
(sector, food_category, metric)" — the same pattern
`factor_downstream.food_category_id` already uses, so the lookup order becomes
exact destination, then the NULL row, then zero, and every row written before
this migration keeps meaning exactly what it meant.

The rejected alternative was an upstream multiplier column on `destination`. It
would have put a piece of the impact formula back into Python (the multiplier
applied in the engine rather than resolved in the lookup) and forced
`FactorBundle.destinations` from a `set[str]` into a mapping. This shape leaves
`line_value = f(qty_kg, upstream, downstream, const_*)` untouched: only the
value bound to `upstream` changes, so the evaluator, §4.3's line-variable table
and `admin/expressions.py`'s `BASE_VARIABLES` are all unaffected and no
panel/engine divergence is introduced.

**What autogenerate produced for this migration, and why almost none of it
survived.** Three separate defects, all of them the ones already documented in
0005 and 0008:

1. It logged "Detected added index 'uq_factor_upstream_generic' on
   ('factor_set_id', 'sector_id', 'food_category_id', 'metric_id')" — the
   COALESCE expression key part dropped on the way in. It happened to render a
   `sa.literal_column` back out, but that is a round trip through a
   mis-reflection, and this is the one constraint in the file that fails
   silently. Written by hand with `op.execute` and asserted against
   `information_schema` by tests/test_migrations.py.
2. It proposed dropping and recreating `uq_factor_downstream_generic` and
   `uq_submission_entry_generic` — the same false positive 0006, 0007 and 0008
   all record. Its *downgrade* would have replaced both real functional indexes
   with plain-column ones, silently reintroducing the duplicate-generic-row bug
   they exist to close. Removed entirely; both are untouched here.
3. It emitted `op.create_foreign_key(None, ...)` and the matching
   `op.drop_constraint(None, ...)`, which cannot execute. Named explicitly.

`compare_metadata` sees a missing UNIQUE but not a missing CHECK, and there is
no CHECK here — the "destination_id may only name `prevention` when the value
is zero" rule is deliberately *not* a constraint. Factors are data (§2.2's
whole premise); a future factor set may legitimately give `animal_feed` its own
upstream row, and a CHECK naming one destination code would have to be migrated
away the first time the client asked for that.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0009'
down_revision: Union[str, Sequence[str], None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: Why every backfilled row carries a note rather than a bare zero. §2.2's
#: rationale for `source_note` is that a calculator which cannot say where a
#: number came from cannot be defended in public, and a zero is the number most
#: likely to be read as missing data rather than as a decision.
_PREVENTION_NOTE = (
    "O-7: prevented waste was never produced, so no upstream burden is "
    "attributable to it. Zero is a modelling decision, not missing data — it "
    "is what makes `prevention` a 100% offset and keeps the two scenarios "
    "mass-conserving."
)


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('factor_upstream', sa.Column('destination_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_factor_upstream_destination', 'factor_upstream', 'destination',
        ['destination_id'], ['id'],
    )

    # The functional index is created *before* the old UNIQUE is dropped, and
    # the order is not cosmetic: `uq_factor_upstream` leads with
    # `factor_set_id`, so MySQL is using it as the backing index for the
    # foreign key to `factor_set` and refuses to drop it — "Cannot drop index
    # 'uq_factor_upstream': needed in a foreign key constraint" (errno 1553).
    # `uq_factor_upstream_generic` leads with the same column, so creating it
    # first hands MySQL a replacement and the drop below succeeds. The
    # downgrade sequences the mirror image for the same reason.
    #
    # Contract §2.2: MySQL treats NULLs as distinct, so the UNIQUE recreated
    # below does not stop a second `destination_id IS NULL` row for the
    # same (factor_set, sector, food_category, metric) — exactly the "every
    # destination" rows the constraint most needs to guard, and the ones almost
    # every factor row is. Two of them and the fallback lookup picks one
    # nondeterministically: the same input returning a different net benefit
    # run to run, with nothing in the logs. COALESCE collapses NULL to 0 before
    # comparing, so the second one collides for real; an AUTO_INCREMENT
    # destination.id is never 0, so a destination-specific row and a generic
    # row still coexist as the lookup order requires.
    #
    # Raw SQL rather than op.create_index, matching 0005 and 0008. This is the
    # third instance of B's factor_downstream trap and the third time
    # autogenerate has dropped the expression key part on the way in.
    op.execute(
        "CREATE UNIQUE INDEX uq_factor_upstream_generic "
        "ON factor_upstream "
        "(factor_set_id, sector_id, food_category_id, "
        "(COALESCE(destination_id, 0)), metric_id)"
    )

    op.drop_constraint('uq_factor_upstream', 'factor_upstream', type_='unique')
    op.create_unique_constraint(
        'uq_factor_upstream', 'factor_upstream',
        ['factor_set_id', 'sector_id', 'food_category_id', 'destination_id', 'metric_id'],
    )

    # The schema alone does not close O-7 — the rows do. Give every existing
    # (factor_set, sector, food_category, metric) that has a general row a
    # `prevention` row at zero, so `prevention` becomes the 100% offset
    # architecture.md §4.1 has always claimed it was.
    #
    # No NOT EXISTS guard: the column was created two statements ago, so every
    # row in the table has `destination_id IS NULL` and no `prevention` row can
    # already exist. A migration runs once, and if this one were somehow
    # replayed the unique index above would refuse it loudly, which is the
    # right outcome. Skips cleanly on a database with no `prevention`
    # destination (the JOIN yields nothing) — that is a fresh deployment whose
    # taxonomy has not been seeded yet, and `admin/seed.py` creates the
    # destination, not the factors.
    op.execute(
        sa.text(
            "INSERT INTO factor_upstream "
            "  (factor_set_id, sector_id, food_category_id, destination_id, "
            "   metric_id, value_per_kg, source_note, data_quality) "
            # `data_quality` is 'definitional', not inherited from the general
            # row: the general row's provenance describes a measurement, and
            # copying 'proxy-US' onto a zero would claim an American study as
            # the source of a number that is a modelling decision. §2.2 keeps
            # the column free text for exactly this kind of category.
            "SELECT u.factor_set_id, u.sector_id, u.food_category_id, d.id, "
            "       u.metric_id, 0, :note, 'definitional' "
            "FROM factor_upstream u "
            "JOIN destination d ON d.code = 'prevention' "
            "WHERE u.destination_id IS NULL"
        ).bindparams(note=_PREVENTION_NOTE)
    )


def downgrade() -> None:
    """Downgrade schema."""
    # The destination-specific rows go first. Dropping the column would
    # otherwise collapse each of them onto its general row and trip the
    # restored four-column UNIQUE. Deleting rather than collapsing is the only
    # honest option: a downgrade removes the dimension, and a `prevention` zero
    # merged into the general row would silently zero that row for *every*
    # destination. Any destination-specific factors staff have authored are
    # lost with them, which is what downgrading a dimension away means.
    op.execute("DELETE FROM factor_upstream WHERE destination_id IS NOT NULL")
    # Mirror of the upgrade's ordering note: whichever of the two
    # `factor_set_id`-leading indexes is dropped first, the other must still
    # exist to back the foreign key to `factor_set`, or MySQL refuses with
    # errno 1553.
    op.drop_constraint('uq_factor_upstream', 'factor_upstream', type_='unique')
    op.create_unique_constraint(
        'uq_factor_upstream', 'factor_upstream',
        ['factor_set_id', 'sector_id', 'food_category_id', 'metric_id'],
    )
    op.execute("DROP INDEX uq_factor_upstream_generic ON factor_upstream")
    op.drop_constraint('fk_factor_upstream_destination', 'factor_upstream',
                       type_='foreignkey')
    op.drop_column('factor_upstream', 'destination_id')
