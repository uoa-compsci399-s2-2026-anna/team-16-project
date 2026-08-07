"""Shared SQLAlchemy declarative base for every project-owned model."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass

