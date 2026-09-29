"""The period, the consent, the production total and the two money figures.

Contract v1.48. Four of the client's second-round requests need somewhere to
put a number, and this is one revision rather than four because they arrived
together and a schema is cheaper to get right once.

**Every column is nullable or defaulted, so this migration is safe on a
populated database.** The deployed stack has real submissions in it; a NOT
NULL column with no default would fail on the first existing row, and there is
no value that could be back-filled honestly - nobody asked those visitors what
period their figures covered or whether they wanted to contribute.

`is_public_contributed` defaults FALSE, which means **every submission
recorded before this revision stops counting towards the public statistics.**
That is the correct reading of a consent flag applied retrospectively: those
visitors were never asked. It is stated here because it is a data change
disguised as a schema change, and the statistics page's own count will drop
when this lands.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, Sequence[str], None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("submission", sa.Column("time_frame", sa.String(32), nullable=True))
    op.add_column(
        "submission",
        sa.Column(
            "is_public_contributed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "submission_entry",
        sa.Column("total_input_kg", sa.DECIMAL(16, 3), nullable=True),
    )
    op.add_column(
        "submission_entry",
        sa.Column("total_value_nzd", sa.DECIMAL(14, 2), nullable=True),
    )
    op.add_column(
        "submission_entry",
        sa.Column("wasted_value_nzd", sa.DECIMAL(14, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("submission_entry", "wasted_value_nzd")
    op.drop_column("submission_entry", "total_value_nzd")
    op.drop_column("submission_entry", "total_input_kg")
    op.drop_column("submission", "is_public_contributed")
    op.drop_column("submission", "time_frame")
