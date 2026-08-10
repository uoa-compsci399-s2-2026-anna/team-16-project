"""Shared SQLAlchemy declarative base.

Owner: B (db). Created here by E only so that the admin-owned tables of
docs/interfaces.md 2.4 can share one metadata object, and therefore one
Alembic migration chain. Two chains fork ``down_revision`` and reconciling
that by hand is the least pleasant kind of merge conflict.

Every model in the project inherits from this ``Base``, whichever package it
lives in, so that ``alembic revision --autogenerate`` sees all of them.
"""

from sqlalchemy import BigInteger, Integer
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


#: BIGINT on every dialect except SQLite, where it is plain INTEGER.
#:
#: Contract §2.2/§2.3 specify BIGINT for the primary keys of the high-volume
#: tables — `factor_upstream`, `factor_downstream`, `submission`,
#: `submission_line`, `audit_log` — because roughly 600 factor rows per factor
#: set, and one submission per public calculation, are what actually exhaust an
#: INT. But SQLite only auto-assigns a primary key for a column declared
#: *exactly* `INTEGER`: a BIGINT primary key there is NOT NULL with no default,
#: and every insert fails with `NOT NULL constraint failed`. Several of these
#: tables are written both from the admin panel against MySQL and from B's
#: in-memory SQLite fixtures (`tests/db/conftest.py`), so they need both
#: behaviours from one declaration.
#:
#: `with_variant` leaves the MySQL DDL untouched, so the migration chain and
#: `compare_metadata`'s drift gate are unaffected. It lives here, on the shared
#: base module, because `admin/` and `db/` both need it and `db/base.py` is the
#: one module both already import — putting it in `db/models.py` would make
#: `admin/` import `db.models`, which imports `admin/`.
BIGINT_PK = BigInteger().with_variant(Integer, "sqlite")
