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
    UniqueConstraint,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import BIGINT_PK, Base


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
    #: When this account's second factor came into force; NULL means no
    #: confirmed authenticator. **The secret itself no longer lives here** —
    #: contract §2.4 v1.13 moved it, and the replay counter with it, to
    #: ``staff_totp_device``, because one column can hold one phone and the
    #: honest answer to a lost phone is to have enrolled a second one first.
    #:
    #: This column stays, and it is deliberately derived state: it is true
    #: exactly when ``totp_devices`` holds at least one row with
    #: ``enrolled_at`` set. Keeping it means ``count_usable_admins``,
    #: ``AdminAuth`` and ``require_staff_username`` go on asking one indexed
    #: column the same question they always asked, rather than each growing
    #: its own EXISTS subquery — four onboarding gates whose symmetry three
    #: rounds of review on admin/backend.py were spent establishing. The
    #: price is that two places can disagree, and the mitigation is that
    #: ``admin/accounts.py`` is the only module that writes either of them
    #: (the same rule that already makes it the only module that mutates
    #: ``staff``); ``tests/admin/test_accounts.py`` pins the invariant after
    #: every operation that can move it.
    mfa_enrolled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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

    totp_devices: Mapped[list["StaffTotpDevice"]] = relationship(
        back_populates="staff",
        cascade="all, delete-orphan",
        order_by="StaffTotpDevice.id",
    )

    @property
    def mfa_enrolled(self) -> bool:
        return self.mfa_enrolled_at is not None

    @property
    def enrolled_totp_devices(self) -> list["StaffTotpDevice"]:
        """The confirmed authenticators, in enrolment order.

        An unconfirmed row (``enrolled_at`` NULL) is a scan someone started
        and did not finish. It holds a perfectly usable secret, which is
        exactly why it must never satisfy a login — the same distinction
        ``verify_staff_totp`` has always drawn between a stored secret and a
        finished enrolment, now drawn per device.
        """
        return [d for d in self.totp_devices if d.enrolled_at is not None]

    def __str__(self) -> str:
        #: username, never id - a staff member is identified by how they log
        #: in, and no other column here is safe to show (password_hash is a
        #: secret; the TOTP secret is no longer a column of this table at all
        #: since v1.13, and StaffTotpDevice.__str__ withholds it for the same
        #: reason).
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


class StaffTotpDevice(Base):
    """One enrolled authenticator. Contract §2.4.

    **Why this is a table and not the three columns it replaced.** A single
    ``staff.mfa_secret_enc`` holds one phone, so "enrol a new authenticator"
    could only ever mean "replace the one you have" — which is impossible to
    do once the phone is gone, and leaves recovery layer L2 (another
    administrator resetting your MFA) as the only way back from a lost
    device. That was already the weakest link, and it got stricter: the
    self-recovery guard means the administrator who rescues you can never be
    you. Enrolling a second phone *before* losing the first is the only fix
    that does not depend on a colleague being reachable.

    ``last_counter`` is per device and must stay that way. It is TOTP replay
    protection, and replay is a property of a secret: two phones hold two
    different secrets and produce two different codes for the same time step,
    so a counter shared between them would let a login on one phone refuse a
    genuine, unused code from the other for the rest of that step.

    ``enrolled_at`` NULL means an enrolment that was begun and not confirmed
    — a QR that was displayed, possibly scanned, never proved. Such a row is
    resumable (see ``begin_mfa_enrolment``) and is never accepted as a second
    factor.
    """

    __tablename__ = "staff_totp_device"
    __table_args__ = (
        # One name per account, so the list on the security screen and the
        # entry on the phone can be matched up by eye. The name reaches the
        # otpauth:// label, which is the whole point of having one.
        UniqueConstraint("staff_id", "name", name="uq_staff_totp_device_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    staff_id: Mapped[int] = mapped_column(
        ForeignKey("staff.id", ondelete="CASCADE"), nullable=False
    )
    #: Shown to the person, and carried into the authenticator app's own
    #: label so a second device is distinguishable from the first there too.
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    # VARBINARY on MySQL; LargeBinary elsewhere so the model stays portable.
    # NOT NULL, unlike the column it replaces: a device row exists because a
    # secret was minted for it, so there is no state in which one is present
    # without the other.
    secret_enc: Mapped[bytes] = mapped_column(
        LargeBinary(255).with_variant(mysql.VARBINARY(255), "mysql"), nullable=False
    )
    enrolled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_counter: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )

    staff: Mapped[Staff] = relationship(back_populates="totp_devices")

    def __str__(self) -> str:
        #: The name is the human identifier - it is what the person typed and
        #: what their phone shows. secret_enc must never appear here for the
        #: same reason StaffRecoveryCode.__str__ withholds code_hash. The id
        #: is withheld too: a name exists, so the row has a human identifier
        #: and the primary key is not needed to tell two apart.
        state = "enrolled" if self.enrolled_at is not None else "not confirmed"
        return f"{self.name} ({state})"


class AuditLog(Base):
    """Contract §2.3. Written only by admin/audit.py's write_audit()."""

    __tablename__ = "audit_log"

    #: BIGINT on MySQL, INTEGER on SQLite — this table is written both from the
    #: admin panel against MySQL and from B's SQLite fixtures. See db/base.py.
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    table_name: Mapped[str] = mapped_column(String(64), nullable=False)
    row_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    before_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    def __str__(self) -> str:
        return f"{self.action} {self.table_name}#{self.row_id}"
