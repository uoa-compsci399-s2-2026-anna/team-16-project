"""The taxonomy tables. Contract §2.1.

Separate from admin/models.py, which owns staff accounts and the audit log:
these six are the vocabulary the calculator is defined in, edited by staff
through the panel, and read by the engine on every calculation. They change
for entirely different reasons.

Every one of them carries `code` (the cross-layer identifier — the API and
the front end use it, never the primary key) and `active` (the panel does
not delete taxonomy rows; a row referenced by a historical submission must
stay resolvable, so it is deactivated instead).
"""

from decimal import Decimal

from sqlalchemy import (
    DECIMAL, Boolean, CheckConstraint, ForeignKey, Integer, SmallInteger,
    String, Text,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class DestinationGroup(Base):
    """Contract §2.1. Per MfE: reuse is not waste, the rest are.

    `is_waste` is a column and not a hard-coded enum because the definition
    is expected to move: the 2025 Otago baseline recommends reclassifying
    bioprocessing from waste to reuse, and a released calculator has to be
    able to follow that without a deployment.
    """

    __tablename__ = "destination_group"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_waste: Mapped[bool] = mapped_column(Boolean, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0,
                                            server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True,
                                         server_default="1")

    destinations: Mapped[list["Destination"]] = relationship(back_populates="group")

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"


class Destination(Base):
    """Contract §2.1. Where the food actually went.

    `prevention` is a special row: all its factors are zero, so it expresses
    "this waste did not happen" while keeping the two scenarios
    mass-conserving. admin/taxonomy_rules.py protects it from being renamed
    or deactivated.
    """

    __tablename__ = "destination"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("destination_group.id"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0,
                                            server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True,
                                         server_default="1")

    group: Mapped[DestinationGroup] = relationship(back_populates="destinations")

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"


class Sector(Base):
    """Contract §2.1. Where in the supply chain the waste arose."""

    __tablename__ = "sector"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0,
                                            server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True,
                                         server_default="1")

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"


class FoodCategory(Base):
    """Contract §2.1. The Otago baseline categories, plus a standard mix.

    Exactly one row must have is_standard_mix set — it is what the engine
    falls back to when a user does not know the composition of their waste.
    Zero of them makes that user's calculation impossible; two makes it
    ambiguous. Enforced in admin/taxonomy_rules.py, because no column
    constraint can express "exactly one row in this table".
    """

    __tablename__ = "food_category"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_standard_mix: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0,
                                            server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True,
                                         server_default="1")

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"


class Metric(Base):
    """Contract §2.1. What the calculator reports.

    "Adding a metric means inserting one row here, populating the factor
    tables, and writing one formula. No code changes." Everything needed to
    render a metric — its unit, the unit to display it in, how many decimal
    places — is data in this row, so no view or template may branch on a
    metric code.
    """

    __tablename__ = "metric"
    #: Carried over from B's db/models.py, which declared both of this
    #: module's CHECK constraints and lost them when her classes gave way to
    #: these. `compare_metadata` cannot see a missing CHECK — this project
    #: documents that blind spot in tests/test_migrations.py — so the drift
    #: gate would never have reported it. Each is proven by its own
    #: behavioural test against real MySQL in tests/admin/test_taxonomy_models.py.
    __table_args__ = (
        CheckConstraint("display_precision >= 0", name="ck_metric_precision"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    display_unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Contract §2.1 specifies TINYINT: display_precision only ever holds a
    # small decimal-places count (0-6). SmallInteger elsewhere in this
    # module so the type stays portable; here it is pinned to MySQL's
    # TINYINT via with_variant, the same shape admin/models.py uses for
    # mfa_secret_enc's VARBINARY, so the model stays declarative about its
    # MySQL type rather than hoping a generic type maps to the right one.
    display_precision: Mapped[int] = mapped_column(
        SmallInteger().with_variant(mysql.TINYINT(), "mysql"),
        nullable=False, default=2, server_default="2",
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0,
                                            server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True,
                                         server_default="1")

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"


class UnitPreset(Base):
    """Contract §2.1. "Two 20 litre buckets" in kilograms.

    food_category_id is nullable and means "applies to every category": a
    bucket of bread and a bucket of potatoes weigh different amounts, but a
    wheelie bin is a wheelie bin.
    """

    __tablename__ = "unit_preset"
    #: A negative kg_per_unit is not a hypothetical: the front end multiplies
    #: it by a unit count in web/units.js, so one negative row turns "three
    #: buckets" into a negative mass and feeds a negative quantity into every
    #: metric downstream of it. See the note on Metric above for why the
    #: migration drift gate cannot catch this one for us.
    __table_args__ = (
        CheckConstraint("kg_per_unit >= 0", name="ck_unit_preset_kg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    food_category_id: Mapped[int | None] = mapped_column(
        ForeignKey("food_category.id"), nullable=True
    )
    kg_per_unit: Mapped[Decimal] = mapped_column(DECIMAL(12, 4), nullable=False)
    source_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True,
                                         server_default="1")

    #: The panel lists and edits presets by category name, not by the raw id
    #: — and `code` is the cross-layer identifier, so a primary key must
    #: never be what a human is asked to read or choose.
    food_category: Mapped["FoodCategory | None"] = relationship()

    def __str__(self) -> str:
        return f"{self.code} — {self.label}"
