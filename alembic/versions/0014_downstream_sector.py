"""factor_downstream gains a sector dimension

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-15 09:47:36.804483

Contract v1.31, §2.2 and §4.1. `factor_downstream` was keyed on
(destination, food_category) and could not see which stage of the supply chain
the waste arose at. No New Zealand requirement had asked for one — but ReFED's
downstream factors, which the comparison fixture (§10.3) is built from, differ
by sector in **82 of their 102** (food type, destination) groups, and the
fixture absorbed the difference by folding the stage into the food category's
*code*: one sector row and 39 categories named `refed_farm_dry_goods`,
`refed_foodservice_frozen`. That was numerically lossless and structurally
wrong, and every consequence was visible in the product: the calculator's
supply-chain step offered a single radio button that selected itself, the
category step listed 39 compound entries the client read as stages, and §5.4's
`by_sector` chart was one 100% bucket carrying no information.

`sector_id` is nullable and NULL means "every sector for this destination" —
the same pattern `food_category_id` beside it already uses, and the same one
0009 gave `factor_upstream.destination_id`. **Every row written before this
migration keeps meaning exactly what it meant**, which is why there is no
backfill here and why 0009's `INSERT ... SELECT` has no counterpart below: a
NULL sector on an existing row says "applies to every sector", which is what
that row already said by having no opinion. The New Zealand mock set takes NULL
on all fifteen of its rows and computes identically before and after.

**The lookup order is the part that had to be decided rather than derived.**
With two nullable dimensions, four rows may legally exist for one
(destination, metric) and exactly one must win:

    1. (sector, food_category)   -- both stated
    2. (sector, NULL)            -- this sector, every food category
    3. (NULL, food_category)     -- every sector, this food category
    4. (NULL, NULL)              -- every sector, every food category
    5. zero

Steps 2 and 3 both name one dimension, so specificity cannot separate them.
**The sector wins**; §2.2 carries the three reasons. A wrong precedence here
returns a plausible number rather than an error, which is why the matrix is
tested cell by cell in tests/test_bundle.py rather than sampled.

WHAT AUTOGENERATE PRODUCED, AND WHY MOST OF IT WAS REMOVED
----------------------------------------------------------
Four defects, three of them already recorded by 0005, 0008 and 0009:

1. It proposed dropping and recreating `uq_factor_upstream_generic` and
   `uq_submission_entry_generic` — the same false positive 0006 through 0013
   each record, caused by SQLAlchemy's MySQL dialect reflecting a functional
   index's expression key part back out as a plain column. Its *downgrade*
   would have replaced both real functional indexes with plain-column ones,
   silently reintroducing the duplicate-generic-row bug they exist to close.
   Removed entirely; both are untouched here.
2. Its **downgrade** for this table's own `uq_factor_downstream_generic`
   likewise recreated it over plain `food_category_id`, dropping the
   COALESCE. Written by hand below.
3. It emitted `op.create_foreign_key(None, ...)` and the matching
   `op.drop_constraint(None, ...)`, which cannot execute. Named explicitly.
4. It ordered the UNIQUE swap before a replacement index existed. Both
   `uq_factor_downstream` and `uq_factor_downstream_generic` lead with
   `factor_set_id`, so MySQL uses one of them to back the foreign key to
   `factor_set` and refuses to drop the last one standing — errno 1553,
   "Cannot drop index 'uq_factor_downstream': needed in a foreign key
   constraint". 0009 records the same trap. The statements below are
   sequenced so that one of the two always exists.

`compare_metadata` sees a missing UNIQUE but not a missing CHECK, and there is
no CHECK here: "a downstream factor may only name a sector when it genuinely
differs by one" is a modelling judgement, not a constraint, and factors are
data (§2.2's premise).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0014'
down_revision: Union[str, Sequence[str], None] = '0013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'factor_downstream', sa.Column('sector_id', sa.Integer(), nullable=True)
    )

    # Contract §2.2: MySQL treats NULLs as distinct, so the UNIQUE recreated
    # below does not stop a second `sector_id IS NULL` row — nor a second
    # `food_category_id IS NULL` one — for the same (factor_set, destination,
    # metric). Those are the rows almost every set is made of. Two of them and
    # the fallback lookup picks one nondeterministically: the same input
    # returning a different net benefit run to run, with nothing in the logs.
    #
    # **Both** columns are COALESCEd, not just the new one. Collapsing only
    # `sector_id` would leave two rows that are NULL in `food_category_id`
    # legal again, quietly undoing what 0005 created this index for. An
    # AUTO_INCREMENT id is never 0, so a row naming a sector and a row naming
    # none still coexist, exactly as the four-step lookup requires.
    #
    # Raw SQL rather than op.create_index, matching 0005, 0008 and 0009: this
    # is the fourth instance of B's trap and the fourth time autogenerate has
    # dropped an expression key part on the way in.
    #
    # Dropped and recreated in this order because `uq_factor_downstream` (the
    # plain UNIQUE) is still in place at this point and leads with
    # `factor_set_id`, so it backs the foreign key to `factor_set` across the
    # gap. See defect 4 in the module docstring.
    op.execute("DROP INDEX uq_factor_downstream_generic ON factor_downstream")
    op.execute(
        "CREATE UNIQUE INDEX uq_factor_downstream_generic "
        "ON factor_downstream "
        "(factor_set_id, destination_id, (COALESCE(sector_id, 0)), "
        "(COALESCE(food_category_id, 0)), metric_id)"
    )

    # The generic index above now backs the factor_set foreign key, so the
    # declared UNIQUE can be swapped.
    op.drop_constraint('uq_factor_downstream', 'factor_downstream', type_='unique')
    op.create_unique_constraint(
        'uq_factor_downstream', 'factor_downstream',
        ['factor_set_id', 'destination_id', 'sector_id', 'food_category_id',
         'metric_id'],
    )

    # Named, because autogenerate's `None` cannot execute and because the
    # downgrade has to drop it by name.
    op.create_foreign_key(
        'fk_factor_downstream_sector', 'factor_downstream', 'sector',
        ['sector_id'], ['id'],
    )

    # No data statement. Every existing row keeps `sector_id IS NULL`, which
    # this migration defines as "applies to every sector" — the claim the row
    # was already making by not having the column. Contrast 0009, which had to
    # INSERT the `prevention` zeros because there the *absence* of a row meant
    # something different after the change than before.


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_factor_downstream_sector', 'factor_downstream',
                       type_='foreignkey')

    # The sector-specific rows go first, for the reason 0009's downgrade gives
    # about its own: dropping the column would otherwise collapse each of them
    # onto the every-sector row and trip the restored four-column UNIQUE.
    # Deleting rather than collapsing is the only honest option — a downgrade
    # removes the dimension, and a farm-specific landfill factor merged into
    # the general row would silently reprice every other sector. Any
    # sector-specific factors staff have authored are lost with them, which is
    # what downgrading a dimension away means. The ReFED comparison set is
    # built entirely of such rows and does not survive a downgrade; it is a
    # fixture and is rebuilt from its CSV by
    # tests/benchmark/refed/build_refed_benchmark.py.
    op.execute("DELETE FROM factor_downstream WHERE sector_id IS NOT NULL")

    # Mirror of the upgrade's ordering note: the five-column generic index is
    # still in place here and leads with `factor_set_id`, so it backs the
    # foreign key while the UNIQUE is swapped back.
    op.drop_constraint('uq_factor_downstream', 'factor_downstream', type_='unique')
    op.create_unique_constraint(
        'uq_factor_downstream', 'factor_downstream',
        ['factor_set_id', 'destination_id', 'food_category_id', 'metric_id'],
    )

    # Restored by hand with its COALESCE intact. Autogenerate's version of this
    # line was plain columns, which is defect 2 above and the one that would
    # have left the schema looking right and enforcing nothing.
    op.execute("DROP INDEX uq_factor_downstream_generic ON factor_downstream")
    op.execute(
        "CREATE UNIQUE INDEX uq_factor_downstream_generic "
        "ON factor_downstream "
        "(factor_set_id, destination_id, (COALESCE(food_category_id, 0)), "
        "metric_id)"
    )

    op.drop_column('factor_downstream', 'sector_id')
