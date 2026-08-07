"""Saved scenarios for the pre-publish comparison. Contract §8.2.

A scenario is a saved `POST /calculate` request minus the factor set: a
sector, a food category, a horizon and a set of destination lines. The
comparison view runs each one twice — against the published set and against
the draft — and shows the difference.

They are rows rather than a constant in this module because §8.2 says so,
and the reason is worth repeating: hard-coding them reintroduces "change the
code to change the configuration", which Decision 2 exists to prevent. Staff
who find a scenario that catches a class of mistake need to be able to keep
it without a deployment.
"""

from decimal import Decimal

from sqlalchemy import DECIMAL, Boolean, ForeignKey, Integer, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from admin.taxonomy_models import Destination, FoodCategory, Sector
from db.base import Base


class ComparisonScenario(Base):
    """One saved test case for the pre-publish comparison view."""

    __tablename__ = "comparison_scenario"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    sector_id: Mapped[int] = mapped_column(ForeignKey("sector.id"), nullable=False)
    #: Null means the standard mix, the same reading §6.2 gives the field.
    food_category_id: Mapped[int | None] = mapped_column(
        ForeignKey("food_category.id"), nullable=True
    )
    gwp_horizon: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=100, server_default="100"
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0,
                                            server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True,
                                         server_default="1")

    sector: Mapped[Sector] = relationship()
    food_category: Mapped[FoodCategory | None] = relationship()
    lines: Mapped[list["ComparisonScenarioLine"]] = relationship(
        back_populates="scenario", cascade="all, delete-orphan"
    )


class ComparisonScenarioLine(Base):
    """One destination and quantity within a scenario."""

    __tablename__ = "comparison_scenario_line"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    scenario_id: Mapped[int] = mapped_column(
        ForeignKey("comparison_scenario.id", ondelete="CASCADE"), nullable=False
    )
    destination_id: Mapped[int] = mapped_column(
        ForeignKey("destination.id"), nullable=False
    )
    qty_kg: Mapped[Decimal] = mapped_column(DECIMAL(16, 3), nullable=False)

    scenario: Mapped[ComparisonScenario] = relationship(back_populates="lines")
    destination: Mapped[Destination] = relationship()
