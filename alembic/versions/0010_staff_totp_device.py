"""TOTP secrets move off staff onto staff_totp_device

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-10 00:00:00.000000

Contract §2.4, v1.13. `staff.mfa_secret_enc` is one column, so it holds one
phone, and "enrol a new authenticator" could only ever mean "replace the one
you have" — which is impossible to do once the phone is gone. That left
recovery layer L2 (another administrator resetting your MFA) as the only way
back from a lost device, and L2 got stricter the commit before last: the
self-recovery guard means the administrator who rescues you can never be you.
Enrolling a second phone *before* losing the first is the only fix that does
not depend on a colleague being reachable.

**What moves and what does not.** `mfa_secret_enc` and `mfa_last_counter` move
and are dropped. `mfa_enrolled_at` stays on `staff`, as derived state meaning
"this account's second factor is in force" — true exactly when at least one
device row is confirmed. That is a deliberate denormalisation and it is priced
in `admin/models.py`'s comment on the column: four onboarding gates in
`admin/backend.py`, plus `require_staff_username` and `count_usable_admins`,
ask that one indexed column the same question today, and the symmetry between
them cost three rounds of review to establish. `admin/accounts.py` is the only
module that writes it — the same rule that already makes it the only module
that mutates `staff` — through a single `_sync_mfa_enrolled_at` helper.

`last_counter` is per device and must stay that way. TOTP replay protection is
a property of a secret: two phones hold two secrets and emit two *different*
codes for the same time step, so a counter shared between them would let a
login on one push the counter past the step the other's current, entirely
unused code belongs to. That code would then be refused as a replay for the
rest of its life, and the symptom — "the backup phone does not work",
intermittently, only on accounts with two devices — is the kind that gets
blamed on the phone.

**What autogenerate produced, and what was wrong with it.** Run against a
database at 0009 with the new models loaded, `compare_metadata` reported the
three real changes (add_table `staff_totp_device`, remove_column
`staff.mfa_secret_enc`, remove_column `staff.mfa_last_counter`) and then the
same three false positives 0005, 0006, 0007, 0008 and 0009 all record: it
proposed dropping and recreating `uq_factor_downstream_generic`,
`uq_factor_upstream_generic` and `uq_submission_entry_generic`, each time
having lost the `COALESCE(...)` expression key part on the way in
("expression ('factor_set_id', 'destination_id', 'food_category_id',
'metric_id') to ('factor_set_id', 'destination_id', 'metric_id')" — note the
column silently missing from the second tuple). Executing any of those would
have replaced a real functional index with a plain-column one and silently
reintroduced the duplicate-generic-row bug each exists to close. All three are
absent from this file; none of those three tables is touched here.

It also gave no separate signal for `uq_staff_totp_device_name`, because that
UNIQUE arrives inside the `add_table` rather than as a diff of its own — which
is the one shape of missing constraint `compare_metadata` would *not* have
caught had it been dropped from the model. It is written out explicitly below
and asserted against `information_schema` by
`tests/admin/test_models.py::test_one_authenticator_name_per_account`.

There is no CHECK constraint here. `compare_metadata` does not see a missing
one (this project's known blind spot, documented in `tests/test_migrations.py`),
so the temptation is to add belt-and-braces rules in SQL — e.g. "a confirmed
device must have a counter". It would be wrong: `last_counter` is NULL between
`enrolled_at` being set and the first *login* on that device, which is an
ordinary state lasting as long as the person takes to log in again.

**The downgrade is lossy and cannot be made otherwise.** One column holds one
secret, so downgrading an account with two enrolled phones keeps one of them
and discards the rest — that is what removing the dimension means, the same
way 0009's downgrade deletes destination-specific factors. It keeps the
earliest *confirmed* device, so an account that can log in before the
downgrade can still log in after it; anyone whose second phone was the one
discarded re-enrols through their administrator. See the comment on
`downgrade()`.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = '0010'
down_revision: Union[str, Sequence[str], None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: What the one device every pre-migration account can have is called. It has
#: to match `admin.accounts.DEFAULT_DEVICE_NAME` exactly: the onboarding
#: enrolment page resumes an unfinished enrolment by looking the device up by
#: this name, so a backfilled row under any other name would leave a
#: half-enrolled account unable to finish on the page it was already sitting
#: on — it would mint a second secret instead, invalidating the QR already on
#: the person's phone.
_DEFAULT_DEVICE_NAME = "Authenticator"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'staff_totp_device',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('staff_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column(
            'secret_enc',
            sa.LargeBinary(length=255).with_variant(mysql.VARBINARY(255), 'mysql'),
            nullable=False,
        ),
        sa.Column('enrolled_at', sa.DateTime(), nullable=True),
        sa.Column('last_counter', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ['staff_id'], ['staff.id'],
            name='fk_staff_totp_device_staff', ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        # Named explicitly, matching the model's __table_args__. This is the
        # constraint compare_metadata would not have reported as missing —
        # it arrives inside add_table rather than as a diff of its own — so
        # it is also the one asserted directly in the test suite.
        sa.UniqueConstraint('staff_id', 'name', name='uq_staff_totp_device_name'),
    )

    # Carry every existing secret across, confirmed or not. An unconfirmed
    # one (mfa_enrolled_at IS NULL with a secret present) is somebody who has
    # scanned a QR and not yet typed the code; dropping it would invalidate
    # that scan mid-onboarding, and admin/views.py's whole
    # "reuse, never re-mint" property exists to avoid exactly that.
    #
    # `created_at` falls back to the account's own creation time rather than
    # to NOW(): the column records when the device came into existence, and
    # for a pre-migration row the honest answer is "no later than the
    # enrolment", not "when the migration ran".
    op.execute(
        sa.text(
            "INSERT INTO staff_totp_device "
            "  (staff_id, name, secret_enc, enrolled_at, last_counter, created_at) "
            "SELECT id, :name, mfa_secret_enc, mfa_enrolled_at, mfa_last_counter, "
            "       COALESCE(mfa_enrolled_at, created_at) "
            "FROM staff "
            "WHERE mfa_secret_enc IS NOT NULL"
        ).bindparams(name=_DEFAULT_DEVICE_NAME)
    )

    # Dropped only after the backfill has read them. mfa_enrolled_at is
    # deliberately NOT dropped - see the module docstring.
    op.drop_column('staff', 'mfa_last_counter')
    op.drop_column('staff', 'mfa_secret_enc')


def downgrade() -> None:
    """Downgrade schema.

    Lossy, unavoidably: the restored schema has one secret column per
    account. The device kept is the earliest **confirmed** one — ordering by
    `enrolled_at IS NULL` first puts confirmed rows ahead of unconfirmed
    ones, then `id` breaks the tie in enrolment order — so an account that
    could log in before the downgrade can still log in after it. Accounts
    holding only an unfinished enrolment keep that, which leaves them in the
    same mid-onboarding state they were already in.

    Every other device is discarded with the table. That is what removing
    the dimension means; the alternative, refusing to downgrade whenever any
    account holds two, would make the downgrade unusable on precisely the
    deployments that adopted the feature.
    """
    op.add_column(
        'staff',
        sa.Column(
            'mfa_secret_enc',
            sa.LargeBinary(length=255).with_variant(mysql.VARBINARY(255), 'mysql'),
            nullable=True,
        ),
    )
    op.add_column('staff', sa.Column('mfa_last_counter', sa.BigInteger(), nullable=True))

    # One correlated subquery per column rather than a JOIN: MySQL forbids
    # naming the UPDATE target inside its own subquery, but staff_totp_device
    # is a different table, so this form is legal. Both subqueries carry the
    # identical ORDER BY, which is what makes them pick the same row.
    _pick = (
        "SELECT d.{column} FROM staff_totp_device d WHERE d.staff_id = s.id "
        "ORDER BY (d.enrolled_at IS NULL), d.id LIMIT 1"
    )
    op.execute(
        "UPDATE staff s SET "
        f"  s.mfa_secret_enc = ({_pick.format(column='secret_enc')}), "
        f"  s.mfa_last_counter = ({_pick.format(column='last_counter')}) "
        "WHERE EXISTS (SELECT 1 FROM staff_totp_device d WHERE d.staff_id = s.id)"
    )

    op.drop_table('staff_totp_device')
