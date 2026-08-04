"""Staff account tables.

Contract: docs/interfaces.md 2.4. Owned by E, but declared against the shared
``db.base.Base`` so that Alembic keeps one migration chain for the project.
"""

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    LargeBinary,
    String,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


def utcnow() -> datetime:
    """Current UTC time as a naive datetime.

    MySQL ``DATETIME`` stores no zone, and contract §1.3 fixes everything to
    UTC. Stripping the tzinfo here, in one place, keeps every stored value on
    the same footing — mixing aware and naive values in one column is a
    comparison bug waiting to happen.

    ``datetime.utcnow()`` is deprecated in 3.12 and is not used.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class StaffRole(str, enum.Enum):
    admin = "admin"
    staff = "staff"


class Staff(Base):
    __tablename__ = "staff"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[StaffRole] = mapped_column(
        Enum(StaffRole, native_enum=True), nullable=False, default=StaffRole.staff
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    # VARBINARY on MySQL; LargeBinary elsewhere so the model stays portable.
    mfa_secret_enc: Mapped[bytes | None] = mapped_column(
        LargeBinary(255).with_variant(mysql.VARBINARY(255), "mysql"), nullable=True
    )
    mfa_enrolled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    mfa_last_counter: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    recovery_codes: Mapped[list["StaffRecoveryCode"]] = relationship(
        back_populates="staff", cascade="all, delete-orphan"
    )

    @property
    def mfa_enrolled(self) -> bool:
        return self.mfa_enrolled_at is not None


class StaffRecoveryCode(Base):
    __tablename__ = "staff_recovery_code"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    staff_id: Mapped[int] = mapped_column(
        ForeignKey("staff.id", ondelete="CASCADE"), nullable=False
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )

    staff: Mapped[Staff] = relationship(back_populates="recovery_codes")
