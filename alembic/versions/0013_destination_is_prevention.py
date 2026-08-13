"""destination.is_prevention — the prevention offset stops being a code

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-13 13:45:16.112746

Contract §2.1. `prevention` was the one destination code this system knew by
name, and five guards were stated in terms of the literal: §6.2's refusal of it
in a *current* scenario, §6.1's coverage hold-out, the O-7 publish check,
`admin/taxonomy_rules`'s existence rule and `web/js/calculator.js`'s
current-scenario list.

Two things followed, and this column closes both.

The client has not settled what this destination will be called, whether it
survives under that name, or how it appears. A taxonomy row's identity is data
on this project — `destination_group.is_waste` is a column for exactly this
reason, because MfE may revise which destinations count as waste — and a
structural *role* recognised by a magic string is the same mistake one level
down. After this migration the row may be renamed freely and every guard
follows.

And there was a live defect. §10.3's ReFED fixture brings its own prevention
row, `refed_prevention`, whose 156 upstream and 156 downstream factors are all
zero and which is therefore a prevention destination by every property that
matters. Because §6.2's guard tested a literal, `refed_prevention` could be
entered as **current**-scenario waste, persisted as an ordinary
`submission_line` with `scenario = 'current'`, and became a `by_destination`
bucket in the public statistics — waste that by construction did not happen,
counted as real waste, on the page whose whole design problem is not
overclaiming. That is the defect v1.5 closed for `prevention` itself, arriving
one code along, and §5.4's scenario predicate structurally cannot catch it: it
excludes the *alternative* scenario, and the line is not in the alternative
scenario.

**No unique key, and that is deliberate.** `food_category.is_standard_mix` is
the prior art for a boolean marking a structurally special taxonomy row, and it
carries an "exactly one active row" invariant. This column carries "**at least**
one active row" and no upper bound: two vocabularies share these global tables
(v1.19), each brings its own prevention row, and the deployment this was written
against has both. So there is nothing for a `COALESCE(...)` functional unique
index to say, and none is added. The lower bound is not expressible as a column
constraint either — it is a statement about the table — and lives in
`admin/taxonomy_rules.check_prevention_destination`, the same place and the same
mechanism as `check_single_standard_mix`.

**What autogenerate produced, and what was removed from it.** It detected the
added column correctly and then proposed dropping and recreating all three
functional COALESCE unique indexes — `uq_factor_downstream_generic`,
`uq_factor_upstream_generic` and `uq_submission_entry_generic` — the same false
positive 0006, 0007, 0008, 0009, 0010, 0011 and 0012 each record. Its
**downgrade** would have replaced every one of them with a plain-column index,
silently reintroducing the duplicate-generic-row bug they exist to close, since
MySQL compares NULLs as distinct in a UNIQUE key. All six operations are
removed; the three indexes are untouched by this revision.

`compare_metadata` sees a missing UNIQUE but not a missing CHECK. There is no
CHECK here and there is nothing for one to express: `is_prevention` is a plain
boolean whose only rule is a cross-row count.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0013'
down_revision: Union[str, Sequence[str], None] = '0012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'destination',
        sa.Column('is_prevention', sa.Boolean(), server_default='0', nullable=False),
    )
    # The backfill, and the only place in this repository that still writes
    # either of these codes into a guard-shaped statement. It is a **one-time
    # data statement about the rows that exist**, not a rule: it says which of
    # today's destinations already held the role, which is something no
    # column default can know. Nothing reads a code to find the role
    # afterwards.
    #
    # Both are named because both are prevention destinations and both are
    # present in the deployment this migration was written against.
    # `refed_prevention` carries 156 upstream and 156 downstream rows, every
    # one of them 0.0000000000; it is the row whose omission from the old
    # literal was the defect.
    #
    # A deployment holding neither is left with no flagged row, and the next
    # taxonomy edit through the panel is refused with a message saying so —
    # which is the correct outcome, because such a database cannot express an
    # improved scenario and could not before this migration either.
    op.execute(
        "UPDATE destination SET is_prevention = 1 "
        "WHERE code IN ('prevention', 'refed_prevention')"
    )


def downgrade() -> None:
    # The flag is the only record of which row holds the role, so dropping the
    # column loses it. That is recoverable exactly as far as the codes above
    # still name the same rows — which is the assumption this whole revision
    # exists to stop the *running* system making, and is acceptable in a
    # downgrade because the code it downgrades to is the code that made it.
    op.drop_column('destination', 'is_prevention')
