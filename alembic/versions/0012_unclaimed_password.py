"""staff.initial_password_enc -> staff.unclaimed_password_enc

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-12 00:00:00.000000

Contract §8.3, v1.16. A rename and nothing else: same type, same nullability,
same data, same encryption key. What changed is what the column is allowed to
hold, and that change lives entirely in `admin/accounts.py`.

**Why the name had to move.** `0011` introduced the column to hold the password
an account was *created* with; `issue_password` cleared it, so a
non-NULL value meant "this account has never been used". v1.16 makes
`issue_password` store its replacement instead, on the owner's ruling that the
two should be aligned — a lost tab must not cost an issued password any more
than it costs a created one. From that point a non-NULL value can belong to an
account three years old whose owner lost their password this morning, and
`initial_password_enc` is a name that would go on telling every future reader
otherwise. It is also the name the append-only audit trail and the accounts
list would have kept repeating in prose ("Initial password still unclaimed")
about a password that was not initial.

**The HKDF `info` is deliberately NOT renamed with it.** `admin/security.py`'s
`UNCLAIMED_PASSWORD_ENCRYPTION_INFO` is still the byte string
`b"initial-password-encryption"`, because that value is key-derivation
material: changing it derives a different key and turns every value already
stored under the old one into a blob nothing can open. The constant carries the
same warning at its definition. This migration therefore needs no re-encryption
pass and cannot be combined with one.

**Data is preserved, which is the whole reason this is an ALTER and not a
DROP + ADD.** A deployment mid-onboarding has real, live passwords in this
column — that is precisely the state the feature exists for — and dropping it
would lock out every account that had not yet collected one. `op.alter_column`
with `new_column_name` emits MySQL's `ALTER TABLE ... CHANGE`, which renames in
place and carries the rows.

`existing_type`, `existing_nullable` and `existing_server_default` are all
spelled out because MySQL's `CHANGE` restates the whole column definition:
anything omitted here would be silently reset to the dialect's default, which
for `existing_nullable` would mean an attempted `NOT NULL` against a column
that is NULL for almost every row. They match `0011` exactly.

**What autogenerate produced.** Run against a database at `0011` with the
renamed model loaded, `compare_metadata` reports the rename as it reports every
rename — `remove_column initial_password_enc` plus `add_column
unclaimed_password_enc`, which is data loss written as two operations, since
Alembic cannot see that one column became the other. **Not used.** It also
reported the same three false positives every migration since `0005` records:
`uq_factor_downstream_generic`, `uq_factor_upstream_generic` and
`uq_submission_entry_generic`, each with its `COALESCE(...)` expression
silently dropped from the key. None of the three is in this file and none of
those tables is touched here.

There is still no CHECK constraint, and the one that suggests itself is still
wrong — but for a narrower reason than in `0011`. v1.16 makes
"`unclaimed_password_enc IS NOT NULL` implies `must_change_password`" true on
both writing paths, so a CHECK would now hold where it would previously have
failed. The converse still does not: an account created before v1.15, or one
whose stored copy went undecryptable across a SECRET_KEY change, owes a
password change with nothing stored. And `compare_metadata` cannot see a
missing CHECK on this stack (`tests/test_migrations.py`'s recorded blind spot),
so such a constraint would be one more thing maintained by hand for a rule
`admin/accounts.py` already holds in two functions with a test each.

**The downgrade is the same rename backwards** and is lossless. It leaves the
column named as `0011` created it, holding whatever `0012` left in it — which
after v1.16 may include an issued password that the v1.15 code would have
described as an initial one. Nothing breaks; the name is simply narrower than
the contents, which is the condition this revision exists to end.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = '0012'
down_revision: Union[str, Sequence[str], None] = '0011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TYPE = sa.LargeBinary(length=255).with_variant(mysql.VARBINARY(255), 'mysql')


def upgrade() -> None:
    op.alter_column(
        'staff',
        'initial_password_enc',
        new_column_name='unclaimed_password_enc',
        existing_type=_TYPE,
        existing_nullable=True,
        existing_server_default=None,
    )


def downgrade() -> None:
    op.alter_column(
        'staff',
        'unclaimed_password_enc',
        new_column_name='initial_password_enc',
        existing_type=_TYPE,
        existing_nullable=True,
        existing_server_default=None,
    )
