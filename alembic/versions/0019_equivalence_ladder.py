"""An equivalence learns which ladder it is a rung of, and where on it

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-24

Contract v1.71. Measured against the **published** set (15,
`CLIENT-DRAFT-2026-09-21`, `is_mock = true`), scaling the canonical fixture
and bisecting on the label the engine interpolates:

    Olympic swimming pools  reads `0` below   638.755 kg
    Passenger vehicles/year reads `0` below   403.737 kg
    Meals                   reads `0` below     0.225 kg

So a 23 kg submission -- a cafe's week -- shows **two of its three tangible
equivalents reading zero**. Decimal places do not fix it: at 10 kg the pool
figure is 0.0078, and nobody can picture 0.0078 of a pool where `0` at least
says honestly that the figure is negligible at this scale.

The four columns below are what lets a *row* say "I am the rung to use at this
size", so that adding a rung stays one INSERT and never a call site -- which is
the architecture invariant for metrics and equivalences alike, and is also
exactly what the client asked for when they said the equivalents should be
configurable without a code change.

**Safe on a populated database.** All four columns are nullable and no existing
row is written. `family IS NULL` is the pre-v1.71 meaning -- *not a rung of
anything, always shown* -- so every equivalence in every set in every database
behaves after this migration exactly as it did before it. The two CHECKs are
vacuous on such a row.

---

**Why a `family` column, and why `source_metric_id` cannot serve.** A vehicle
kilometre, a vehicle-day and a vehicle-year are all conversions of `co2e` and
*are* one ladder: showing three at once is showing the same fact three times.
But two different *framings* of `co2e` -- vehicles and, say, flights -- would
share `source_metric_id` too, and they must not displace each other. "Same
source metric" and "same ladder" are different claims and only the second one
selects.

**Why the bands are on the equivalence's own value and not on the metric
total.** A ten-minute shower is 90 litres whatever a kilogram of waste costs in
water. Expressed on the displayed value, a band survives open item O-1: real
factors change *which rung* a submission lands on, and change nothing about
where the rungs are. Expressed on the metric total, every band would have to be
retuned the day the client supplies real numbers.

`DECIMAL(20, 10)`, matching `value_per_unit` beside them, because that is what
they are compared against and §1.2 prohibits `FLOAT` and `DOUBLE` outright.

**The band is half-open, `[min_value, max_value)`**, and NULL on either side
means unbounded there. A row with neither is the ladder's catch-all.

---

**`label_template_one` is a second staff-typed string, not grammar in code.**
`Equivalent to 1 Olympic swimming pools of water` is what this repository
prints today; a ladder drives the displayed number toward 1 *by design*, so
what was occasional becomes routine. The fix that fits the model is a second
template the staff member types, used when the interpolated whole number is
exactly `1`. NULL means "no singular form was given", and the plural template
is used as before -- so this, too, is inert on every existing row. Putting a
pluralisation rule in the engine would put English grammar in a module that
serves twenty languages and would override the client's approved wording, which
§7.6 rule 9 already forbids for these sentences.

---

**Two CHECKs, and both are here rather than only in the panel's form.**

1. `ck_equivalence_band_needs_family` -- a band on a row with no family is a
   rule that can never fire, because selection only happens within a family.
   Refused rather than ignored, so that "I set a minimum and nothing happened"
   is impossible to reach.
2. `ck_equivalence_band_ordered` -- `min_value < max_value` where both are
   given. An inverted band admits nothing, and a rung that can never be chosen
   is a rung that silently is not there.

Raw SQL rather than `op.create_check_constraint`, for symmetry with 0004, 0008,
0017 and 0018, and for the reason `tests/test_migrations.py` records as its
"Known blind spot": `compare_metadata` on this SQLAlchemy/MySQL combination
cannot see a *missing* CHECK, so these two are proven behaviourally against
real MySQL in `tests/db/test_equivalence_bands.py` rather than by the drift
gate.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, Sequence[str], None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: Kept as strings so that the two directions of this migration and
#: `admin/factor_models.py`'s own `CheckConstraint`s cannot come to say
#: different things by being edited apart.
_BAND_NEEDS_FAMILY = (
    "family IS NOT NULL OR (min_value IS NULL AND max_value IS NULL)"
)
_BAND_ORDERED = (
    "min_value IS NULL OR max_value IS NULL OR min_value < max_value"
)


def upgrade() -> None:
    op.add_column("equivalence", sa.Column("family", sa.String(64), nullable=True))
    op.add_column(
        "equivalence", sa.Column("min_value", sa.DECIMAL(20, 10), nullable=True)
    )
    op.add_column(
        "equivalence", sa.Column("max_value", sa.DECIMAL(20, 10), nullable=True)
    )
    op.add_column(
        "equivalence", sa.Column("label_template_one", sa.String(255), nullable=True)
    )
    op.execute(
        "ALTER TABLE equivalence ADD CONSTRAINT ck_equivalence_band_needs_family "
        f"CHECK ({_BAND_NEEDS_FAMILY})"
    )
    op.execute(
        "ALTER TABLE equivalence ADD CONSTRAINT ck_equivalence_band_ordered "
        f"CHECK ({_BAND_ORDERED})"
    )


def downgrade() -> None:
    # Both constraints name the columns, so they go before the columns do.
    op.execute("ALTER TABLE equivalence DROP CONSTRAINT ck_equivalence_band_ordered")
    op.execute(
        "ALTER TABLE equivalence DROP CONSTRAINT ck_equivalence_band_needs_family"
    )
    op.drop_column("equivalence", "label_template_one")
    op.drop_column("equivalence", "max_value")
    op.drop_column("equivalence", "min_value")
    op.drop_column("equivalence", "family")
