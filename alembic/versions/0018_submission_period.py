"""The reporting period gains a start and an end, and they carry no zone

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-21

Contract v1.67. Step 5 of the calculator asks *"What period do these figures
cover?"* and until now the only answers were four presets. The client wants to
say **a shift** -- 08:10 to 16:20 -- so the period needs a start and an end,
each with a date and a time, to the minute.

**Safe on a populated database.** The deployed stack has real submissions in
it. Both columns are nullable, so no existing row needs a backfill and none is
written or deleted; the CHECK added below is satisfied by every row that could
already exist, because `period_start` and `period_end` are NULL on all of them
and every clause of it is vacuous when they are.

**Nullable rather than defaulted, and that is the meaning.** NULL here says *no
period was given* -- the answer of every visitor who left step 5 at "Not
stated", and of every visitor who ever used the calculator before this
revision. A sentinel instant standing in for "unstated" would be a fact nobody
supplied, and the first query that filtered on it would count those rows as
having reported something.

---

**READ THIS BEFORE QUERYING EITHER COLUMN. The instants stored here are the
visitor's LOCAL WALL-CLOCK TIME AND THEY CARRY NO ZONE.**

What is stored is what a person read off the clock on their own wall, written
down verbatim. That makes the value:

* **adequate as a label** -- printed back to the visitor who typed it, on the
  results screen and in the download they keep, which is the whole of what the
  field is for; and
* **inadequate for comparison across submissions.** Two rows that both say
  `08:10` may be two hours apart, or twenty-two. This column cannot say which,
  and neither can anything else on the row: §2.3 stores no IP address, no user
  agent and no fingerprint of any kind, so there is nothing on the submission
  from which a zone could be inferred, by design and permanently.

So an analyst who sorts these across rows, buckets them by hour of day,
differences them against `created_at` (which *is* UTC), or reads them as UTC
will get an answer, and the answer will be wrong, and nothing in the data will
show that it is wrong. That is why this is written here, beside the columns,
rather than only in a planning document nobody reading a schema will open.

Carrying a UTC offset alongside them was considered during planning and
**rejected**: it would make the stored value a real instant rather than a
label, which is a larger decision than this field needs and is a neighbour of
`architecture.md` O-4 (localisation beyond language). If it is ever wanted, it
is a third column and another migration -- never a reinterpretation of these
two.

---

**`DATETIME`, and not two columns plus a duration.** The period is two
instants. Nothing derives a length from them: `(period_end - period_start)` is
arithmetic this contract forbids anybody to do with the period (§2.3, and it
is why the engine is not given the fields at all), so there is no duration to
store -- which is fortunate, because a duration in a column is exactly where a
`FLOAT` gets in, and §1.2 prohibits `FLOAT` and `DOUBLE` outright.

**No fractional-seconds precision**, so the column is `DATETIME(0)` in MySQL's
terms. `api.schemas.PricingOptions` drops microseconds before the value is
written, where the caller can be told it happened, rather than leaving MySQL to
truncate silently and hand the download a different instant from the one that
was sent.

---

**`ck_submission_period` is the contradiction rule, and it is here *as well as*
in the validator.** `api.schemas.PricingOptions.validate_period` refuses the
same three states on the wire. It is repeated as a constraint because a rule
the API holds and the schema does not is a rule that lasts until the first
write that does not go through the API -- the admin panel, a CLI, a correction
made by hand in a client. Clause by clause:

1. `(period_start IS NULL) = (period_end IS NULL)` -- both or neither. Half an
   interval is not a period.
2. `period_end >= period_start` -- it runs forwards. **Equal ends are allowed:**
   a zero-length period is odd, but it enters no calculation, so refusing it
   buys precisely what refusing a ten-year span would buy, which is nothing.
3. `period_start >= '1970-01-01'` -- the only floor worth having. A period
   before the epoch is a typo, not a reporting period.
4. `time_frame = 'custom'` requires the interval. `custom` *means* "the visitor
   chose these dates"; with no dates it says nothing and is not a preset
   either.
5. The interval requires a `time_frame`. "Not stated" is step 5's default
   answer and it cannot be carrying dates.

**A preset beside an interval passes all five, and that is the designed normal
case from v1.67**: the four presets became templates that fill the picker, and
`time_frame` goes on recording which button was pressed, so `one_week` plus the
seven days ending now is an ordinary row. It also means clause 5 is not "the
interval implies `custom`" -- writing it that way would have refused every row
the new form produces.

**Refuse, not normalise**, and the choice is recorded in §6.2 as well: every
normalisation available invents an answer. Turning state 5 into `custom` claims
the visitor pressed nothing, which is indistinguishable from a client bug that
dropped the field; dropping the interval in state 4 or 5 throws away the only
record of the dates there is.

**One bound is deliberately absent from this constraint.** The wire also
refuses an instant more than `api.schemas.PERIOD_CEILING_HOURS` (38) hours past
the server's UTC clock. That bound moves with the clock, so a CHECK cannot
express it; it is not missing by oversight. The 38 is not slack either -- read
the constant's own comment before tightening it to 24, because at 24 the API
refuses a shift a visitor in Auckland entered correctly.

**Raw SQL rather than `op.create_check_constraint`**, for symmetry with 0004,
0008 and 0017, and for the reason `tests/test_migrations.py` records as its
"Known blind spot": `compare_metadata` on this SQLAlchemy/MySQL combination
cannot see a *missing* CHECK, so this one is proven against
`information_schema` there and behaviourally in `tests/db/test_submissions.py`.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: Union[str, Sequence[str], None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: Kept as one string so the two directions of this migration and
#: `db/models.py`'s `ck_submission_period` cannot come to say different things
#: by being edited apart.
_PERIOD_CHECK = (
    "(period_start IS NULL) = (period_end IS NULL)"
    " AND (period_start IS NULL OR period_end >= period_start)"
    " AND (period_start IS NULL OR period_start >= '1970-01-01 00:00:00')"
    " AND (period_start IS NOT NULL OR time_frame IS NULL"
    " OR time_frame <> 'custom')"
    " AND (period_start IS NULL OR time_frame IS NOT NULL)"
)


def upgrade() -> None:
    op.add_column("submission", sa.Column("period_start", sa.DateTime(), nullable=True))
    op.add_column("submission", sa.Column("period_end", sa.DateTime(), nullable=True))
    op.execute(
        "ALTER TABLE submission "
        f"ADD CONSTRAINT ck_submission_period CHECK ({_PERIOD_CHECK})"
    )


def downgrade() -> None:
    # The constraint names both columns, so it goes before they do.
    op.execute("ALTER TABLE submission DROP CONSTRAINT ck_submission_period")
    op.drop_column("submission", "period_end")
    op.drop_column("submission", "period_start")
