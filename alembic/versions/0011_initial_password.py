"""staff.initial_password_enc — the unclaimed initial password

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-12 00:00:00.000000

Contract §8.3, v1.15 item 3. One nullable column holding an account's initial
password, encrypted with Fernet under a key derived from `SECRET_KEY`, from
creation until the password is first changed.

**This is a deliberate weakening of credential storage** and the repository
owner took the decision with the cost stated: anyone holding both a database
dump and `SECRET_KEY` can log in as every account that has not yet claimed its
password, and those accounts are pre-MFA in the way that matters — the attacker
reaches the forced enrolment page and enrols their own authenticator, so the
password is the whole of the protection. It exists because a one-time reveal is
easy to lose and this project has already lost a set of recovery codes to
exactly that shape. What bounds the exposure is the column's lifetime: NULL for
the whole of an account's life except the window between creation and first
login. `admin/security.py`'s note above `encrypt_initial_password` carries the
long form.

**Nullable, with no server default, and no backfill.** Every `staff` row that
exists before this migration runs gets NULL, which is the correct and only
possible value: the plaintext those accounts were created with was never
stored anywhere and cannot be reconstructed from `password_hash`, which is
bcrypt. The panel renders NULL as "not recoverable", not as an error — an
account created before this migration simply has nothing to reveal, exactly
like one that has already logged in. Backfilling would have meant issuing every
existing account a new password, which is not an upgrade, it is a lockout.

**VARBINARY(255), matching `staff_totp_device.secret_enc`.** A Fernet token
over the 24-character password `generate_initial_password` produces is about
160 bytes; 255 leaves room without a second migration if that length changes.
`LargeBinary(255).with_variant(mysql.VARBINARY(255), "mysql")` in the model, so
the column is portable to the SQLite `admin/accounts.py` is also imported under.

**What autogenerate produced.** Run against a database at 0010 with the new
model loaded, `compare_metadata` reported the one real change (`add_column`
`staff.initial_password_enc`) and then the same three false positives that
0005 through 0010 each record: it proposed dropping and recreating
`uq_factor_downstream_generic`, `uq_factor_upstream_generic` and
`uq_submission_entry_generic`, each time having dropped the `COALESCE(...)`
expression from the key and rendered a functional index as plain columns.
Executing any of them would silently reintroduce the duplicate-generic-row bug
each exists to close. All three are absent from this file; none of those three
tables is touched here.

There is no CHECK constraint, and the one that suggests itself would be wrong.
"`initial_password_enc IS NOT NULL` implies `must_change_password`" is true —
but so is its converse's failure: `issue_password` sets `must_change_password`
and *clears* this column deliberately (an administrator-issued replacement is a
one-time reveal, contract v1.15 item 3), so `must_change_password` with a NULL
here is an ordinary state and the only implication that holds is the one
direction. `compare_metadata` does not see a missing CHECK anyway (this
project's known blind spot, `tests/test_migrations.py`), so a constraint here
would be one more thing that has to be maintained by hand for a rule the
service layer already holds in one function.

**The downgrade is not lossy in any way that matters.** Dropping the column
discards ciphertext for accounts that have not yet logged in; the accounts
themselves are untouched and their passwords still work. Anyone who had not
collected theirs is back to where they were before v1.15 — the administrator
issues a new one.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = '0011'
down_revision: Union[str, Sequence[str], None] = '0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'staff',
        sa.Column(
            'initial_password_enc',
            sa.LargeBinary(length=255).with_variant(mysql.VARBINARY(255), 'mysql'),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column('staff', 'initial_password_enc')
