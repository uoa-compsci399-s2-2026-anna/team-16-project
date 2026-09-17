"""The food item level, inert: a vocabulary table, two nullable dimensions and a switch

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-17

Contract v1.54 part one, and stage 3 of
`.superpowers/sdd/2026-09-17-food-granularity/design.md`'s landing order.
Step 2.5 of the calculator will ask which *food* was wasted, not only which
category. This revision builds everywhere that answer has to be able to live
and **changes nothing**: with no `food_item` rows and `item_level_enabled`
false on every set, every figure, response and screen is what it was before.

**Safe on a populated database.** The deployed stack has real submissions in
it. Every new column is nullable or defaulted, the CHECK constraint is
satisfied by every row that could already exist (`food_item_id` is NULL on all
of them), and no row is written or deleted.

---

**Why `food_item` is not a child of `factor_set`.** §6.1 states the rule — *a
factor set brings factors, not a vocabulary* — and reproducibility is what
makes it load-bearing. `submission_entry.food_category_id` points at a global
row no lifecycle operation touches, which is why a 2026 submission still
renders "dairy" in 2029. Every `factor_set_id` in this schema carries
`ON DELETE CASCADE`, so a set-scoped item table would make
`submission_entry.food_item_id` a pointer into one version's private
vocabulary and deleting a spoiled draft would take the meaning of a stored
submission with it. `admin/factor_lifecycle.CHILD_MODELS` therefore stays at
five and is untouched by this revision; the item's *numbers* live in
`factor_upstream`, which is already a child, and `_clone_children`'s
reflection carries the new column with no edit at all.

**Why each of the two functional indexes is rewritten rather than extended.**
Both tables gain a second nullable key part, and MySQL treats NULLs as distinct
inside a UNIQUE key, so both COALESCE indexes have to collapse **both** of
their nullable columns. Collapsing only one leaves an index that exists, is
unique, contains a COALESCE and has silently stopped enforcing half of what it
was written for — the defect v1.31 recorded on `factor_downstream` when it
gained `sector_id`. `tests/test_migrations.py` names each collapsed column
against `information_schema` rather than counting them, for exactly that
reason.

**Ordering, and errno 1553.** `uq_submission_entry` and `uq_factor_upstream`
each lead with the column MySQL is using to back a foreign key
(`submission_id`, `factor_set_id`), so MySQL refuses to drop either while it is
the only index with that prefix — "Cannot drop index ...: needed in a foreign
key constraint". 0009 documents the trap and works round it by creating the
replacement first. The same sequencing is used here in both directions: the new
generic index is created before the old UNIQUE is dropped on the way up, and
the old UNIQUE is restored before the new generic index is dropped on the way
down.

**Nothing autogenerate produced survived, again.** It dropped the expression
key part from every COALESCE index on the way in (the third and fourth
instance of the defect 0005, 0008 and 0009 all record), proposed dropping and
recreating `uq_factor_downstream_generic` as a plain-column index, and emitted
`op.create_foreign_key(None, ...)`, which cannot execute. Both new foreign keys
are named explicitly, which also matters for the drift gate: alembic's MySQL
implementation only ignores an implicitly created FK-backing index when it
shares the foreign key's name.

**No CHECK on `factor_upstream`.** An upstream row naming an item without a
category is already impossible — `food_category_id` is NOT NULL there — and
nothing else about the pair is constrainable: which combinations of
(item, destination) a factor set carries is data, which is §2.2's whole
premise.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, Sequence[str], None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- §2.1: the vocabulary table ----------------------------------------
    #
    # Deliberately empty. Mapping the client's ~20 foods onto our categories is
    # a data-authoring task with client-facing consequences — seven of their
    # rows (Eggs, Fats, Sauces/Spreads/Dips, Herbs/Spices, Snack Foods and
    # desserts, Sweeteners, Other Food Types) have no New Zealand category at
    # all — and it gets its own review. An empty table is what keeps this
    # revision inert.
    op.create_table(
        "food_item",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("food_category_id", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("active", sa.Boolean(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(["food_category_id"], ["food_category.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )

    # --- §2.2: the switch --------------------------------------------------
    #
    # FALSE for every set that already exists, which is the correct reading of
    # a release flag applied retrospectively: none of them carries an
    # item-level factor, so none of them can honestly release step 2.5.
    op.add_column(
        "factor_set",
        sa.Column(
            "item_level_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    # --- §2.2: the item dimension on factor_upstream -----------------------
    op.add_column("factor_upstream", sa.Column("food_item_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_factor_upstream_food_item", "factor_upstream", "food_item",
        ["food_item_id"], ["id"],
    )

    # The replacement generic index goes in before the old one comes out, so
    # that `factor_set_id` never briefly loses its only backing index (see the
    # errno 1553 note in the module docstring). `uq_factor_upstream` still
    # exists at this point and leads with the same column, so the drop below
    # is safe either way; the order is kept as a matter of habit, because the
    # habit is what stops the next revision getting it wrong.
    op.execute("DROP INDEX uq_factor_upstream_generic ON factor_upstream")
    op.execute(
        "CREATE UNIQUE INDEX uq_factor_upstream_generic "
        "ON factor_upstream "
        "(factor_set_id, sector_id, food_category_id, "
        "(COALESCE(food_item_id, 0)), (COALESCE(destination_id, 0)), metric_id)"
    )
    op.drop_constraint("uq_factor_upstream", "factor_upstream", type_="unique")
    op.create_unique_constraint(
        "uq_factor_upstream", "factor_upstream",
        ["factor_set_id", "sector_id", "food_category_id", "food_item_id",
         "destination_id", "metric_id"],
    )

    # --- §2.3: the item dimension on submission_entry ----------------------
    op.add_column("submission_entry", sa.Column("food_item_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_submission_entry_food_item", "submission_entry", "food_item",
        ["food_item_id"], ["id"],
    )

    op.execute("DROP INDEX uq_submission_entry_generic ON submission_entry")
    op.execute(
        "CREATE UNIQUE INDEX uq_submission_entry_generic "
        "ON submission_entry "
        "(submission_id, sector_id, "
        "(COALESCE(food_category_id, 0)), (COALESCE(food_item_id, 0)))"
    )
    op.drop_constraint("uq_submission_entry", "submission_entry", type_="unique")
    op.create_unique_constraint(
        "uq_submission_entry", "submission_entry",
        ["submission_id", "sector_id", "food_category_id", "food_item_id"],
    )

    # An entry naming a food must name its category. §5.4 gives
    # `food_category_id IS NULL` its own `unspecified` bucket meaning *the user
    # did not break their waste down by type*, and forbids conflating it with
    # anything else — a row that named Cheese and no category would be counted
    # as "not broken down" while carrying the most specific answer the
    # calculator can take.
    #
    # Raw SQL rather than op.create_check_constraint for symmetry with 0004 and
    # 0008: `compare_metadata` on this SQLAlchemy/MySQL combination cannot see
    # a missing CHECK (tests/test_migrations.py's "Known blind spot"), so this
    # one is proven against information_schema there and behaviourally in
    # tests/db/test_food_item_schema.py.
    op.execute(
        "ALTER TABLE submission_entry "
        "ADD CONSTRAINT ck_submission_entry_item_has_category "
        "CHECK (food_item_id IS NULL OR food_category_id IS NOT NULL)"
    )


def downgrade() -> None:
    # The mirror image, in reverse order. Nothing is deleted from any table:
    # `food_item` is empty by construction (this revision seeds no rows), and
    # `factor_upstream.food_item_id` / `submission_entry.food_item_id` are NULL
    # on every row unless a later landing wrote to them — in which case
    # dropping the column is what downgrading a dimension away means, exactly
    # as 0009 records.
    op.execute(
        "ALTER TABLE submission_entry "
        "DROP CONSTRAINT ck_submission_entry_item_has_category"
    )

    op.drop_constraint("uq_submission_entry", "submission_entry", type_="unique")
    op.create_unique_constraint(
        "uq_submission_entry", "submission_entry",
        ["submission_id", "sector_id", "food_category_id"],
    )
    # The restored UNIQUE leads with `submission_id` and is in place before the
    # generic index is dropped, so the foreign key to `submission` is never
    # left without a backing index.
    op.execute("DROP INDEX uq_submission_entry_generic ON submission_entry")
    op.execute(
        "CREATE UNIQUE INDEX uq_submission_entry_generic "
        "ON submission_entry "
        "(submission_id, sector_id, (COALESCE(food_category_id, 0)))"
    )
    op.drop_constraint("fk_submission_entry_food_item", "submission_entry",
                       type_="foreignkey")
    op.drop_column("submission_entry", "food_item_id")

    op.drop_constraint("uq_factor_upstream", "factor_upstream", type_="unique")
    op.create_unique_constraint(
        "uq_factor_upstream", "factor_upstream",
        ["factor_set_id", "sector_id", "food_category_id", "destination_id",
         "metric_id"],
    )
    op.execute("DROP INDEX uq_factor_upstream_generic ON factor_upstream")
    op.execute(
        "CREATE UNIQUE INDEX uq_factor_upstream_generic "
        "ON factor_upstream "
        "(factor_set_id, sector_id, food_category_id, "
        "(COALESCE(destination_id, 0)), metric_id)"
    )
    op.drop_constraint("fk_factor_upstream_food_item", "factor_upstream",
                       type_="foreignkey")
    op.drop_column("factor_upstream", "food_item_id")

    op.drop_column("factor_set", "item_level_enabled")

    op.drop_table("food_item")
