"""Staff account tables.

Contract: docs/interfaces.md 2.4. Owned by E, but declared against the shared
``db.base.Base`` so that Alembic keeps one migration chain for the project.
"""

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    CHAR,
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
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

    #: Bumped by every credential change. The signed session cookie carries
    #: the value it was minted under; require_staff_username refuses a
    #: mismatch. This is what makes a password change end the old sessions
    #: without introducing server-side session storage.
    session_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    recovery_codes: Mapped[list["StaffRecoveryCode"]] = relationship(
        back_populates="staff", cascade="all, delete-orphan"
    )

    @property
    def mfa_enrolled(self) -> bool:
        return self.mfa_enrolled_at is not None

    def __str__(self) -> str:
        #: username, never id - a staff member is identified by how they log
        #: in, and no other column here is safe to show (password_hash and
        #: mfa_secret_enc are secrets).
        return self.username


class StaffRecoveryCode(Base):
    __tablename__ = "staff_recovery_code"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    staff_id: Mapped[int] = mapped_column(
        ForeignKey("staff.id", ondelete="CASCADE"), nullable=False
    )
    # CHAR, not VARCHAR: contract 2.4, and a SHA-256 hex digest is always
    # exactly 64 characters.
    code_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )

    staff: Mapped[Staff] = relationship(back_populates="recovery_codes")

    def __str__(self) -> str:
        #: code_hash must never appear here - it is the whole reason this
        #: row exists. The id is the only thing left to distinguish one
        #: recovery code from another for the same account.
        return f"recovery code #{self.id}"


class AuditLog(Base):
    """Contract §2.3. Written only by admin/audit.py's write_audit()."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    table_name: Mapped[str] = mapped_column(String(64), nullable=False)
    row_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    before_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    def __str__(self) -> str:
        return f"{self.action} {self.table_name}#{self.row_id}"
