"""Shared SQLAlchemy declarative base.

Owner: B (db). Created here by E only so that the admin-owned tables of
docs/interfaces.md 2.4 can share one metadata object, and therefore one
Alembic migration chain. Two chains fork ``down_revision`` and reconciling
that by hand is the least pleasant kind of merge conflict.

Every model in the project inherits from this ``Base``, whichever package it
lives in, so that ``alembic revision --autogenerate`` sees all of them.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
