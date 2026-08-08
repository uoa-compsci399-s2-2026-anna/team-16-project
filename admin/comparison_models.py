"""Saved scenarios for the pre-publish comparison. Contract §8.2.

A scenario is a saved `POST /calculate` request minus the factor set: a
sector, a food category, a horizon and a set of destination lines. The
comparison view runs each one twice — against the published set and against
the draft — and shows both totals side by side. It does not compute a
difference between them: Decision 6 puts every impact number server-side,
in exactly one place, and the engine has no concept of a difference between
two separate calls made at two different factor-set versions (see
`admin.dryrun_views.CompareView`'s own docstring).

They are rows rather than a constant in this module because §8.2 says so,
and the reason is worth repeating: hard-coding them reintroduces "change the
code to change the configuration", which Decision 2 exists to prevent. Staff
who find a scenario that catches a class of mistake need to be able to keep
it without a deployment.
"""

from decimal import Decimal

from sqlalchemy import (
    DECIMAL, Boolean, CheckConstraint, ForeignKey, Integer, SmallInteger, String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from admin.taxonomy_models import Destination, FoodCategory, Sector
from db.base import Base


class ComparisonScenario(Base):
    """One saved test case for the pre-publish comparison view.

    Carries no current/alternative discriminator, even though §6.2's
    `POST /calculate` request has two line arrays. Judged acceptable under
    YAGNI rather than a gap: the comparison view's two runs of the same
    scenario (published vs. draft) already put every `current` line's
    published and draft totals side by side, and any destination reachable
    through `alternative` is reachable as a `current` line in a second,
    separate scenario. Do not "fix" this by adding a scenario column
    without a concrete need for it.
    """

    __tablename__ = "comparison_scenario"
    __table_args__ = (
        CheckConstraint("gwp_horizon IN (20, 100)", name="ck_comparison_scenario_gwp_horizon"),
    )

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

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"


class ComparisonScenarioLine(Base):
    """One destination and quantity within a scenario.

    UNIQUE(scenario_id, destination_id): §6.2's validation table forbids a
    duplicate `destination` within one scenario's line array, and every
    sibling line table (`admin/factor_models.py`'s `FactorUpstream`,
    `FactorDownstream`) carries the equivalent composite constraint. A
    scenario is a saved request, so it must not be possible to save one the
    request validator will refuse the first time the comparison view runs
    it.
    """

    __tablename__ = "comparison_scenario_line"
    __table_args__ = (
        UniqueConstraint("scenario_id", "destination_id",
                         name="uq_comparison_scenario_line"),
    )

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

    def __str__(self) -> str:
        #: No natural name of its own - the destination and quantity are
        #: what a scenario's line array actually varies.
        return f"{self.destination.code}: {self.qty_kg} kg"
