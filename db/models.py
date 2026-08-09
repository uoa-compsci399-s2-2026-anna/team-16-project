"""Part B SQLAlchemy models defined by docs/interfaces.md section 2.

Only the two submission tables are declared here. The other thirteen are
re-exported from `admin/` — see the note above the imports below.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base

# --- Re-exports, not definitions -------------------------------------------
#
# These thirteen tables were originally declared here as well. Two class
# definitions for one `__tablename__` against the same `Base` is not a merge
# inconvenience — it is `InvalidRequestError: Table 'destination_group' is
# already defined for this MetaData instance`, raised at *import* time, which
# kills pytest during collection so that not one test in the repository runs.
# Exactly one definition of each table can survive, and these are the ones
# that did:
#
#   * The six taxonomy tables and the six factor tables became E's under
#     ToB v3.0 §1, and E's versions already carry contract v1.1 §2.2's
#     `source_note` and `data_quality` columns, which the versions here never
#     had. E's `factor_downstream` also carries the COALESCE(food_category_id,
#     0) functional unique index that B found and that v1.2 §2.2 credits to
#     her — adopted, not lost.
#   * `audit_log` is E's because `admin/models.py` already declares `staff`
#     and `staff_recovery_code` alongside it.
#
# `db/repository.py` refers to every one of these by name only, so re-exporting
# them here means the repository, and both of B's test modules, need no change.
#
# **This import direction inverts the project's layering rule** (`db/` must not
# depend on `admin/`). It is a temporary state, not the destination: these
# models belong in `db/`, and E declared them in `admin/` only because this
# branch was still unmerged when that work was done. Moving them touches all
# eleven of E's admin views, so it is deliberately out of scope here and should
# be tracked as follow-up work.
from admin.factor_models import (  # noqa: E402
    Constant,
    Equivalence,
    FactorDownstream,
    FactorSet,
    FactorSetStatus,
    FactorUpstream,
    Formula,
)
from admin.models import AuditLog  # noqa: E402
from admin.taxonomy_models import (  # noqa: E402
    Destination,
    DestinationGroup,
    FoodCategory,
    Metric,
    Sector,
    UnitPreset,
)

__all__ = [
    "AuditLog",
    "BIGINT_PK",
    "Constant",
    "Destination",
    "DestinationGroup",
    "Equivalence",
    "FactorDownstream",
    "FactorSet",
    "FactorSetStatus",
    "FactorUpstream",
    "FoodCategory",
    "Formula",
    "Metric",
    "Scenario",
    "Sector",
    "Submission",
    "SubmissionLine",
    "UnitPreset",
    "utcnow",
]

BIGINT_PK = BigInteger().with_variant(Integer, "sqlite")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Scenario(str, enum.Enum):
    current = "current"
    alternative = "alternative"


class Submission(Base):
    __tablename__ = "submission"
    __table_args__ = (CheckConstraint("gwp_horizon IN (20, 100)", name="ck_submission_horizon"),)
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    token: Mapped[str | None] = mapped_column(String(36), unique=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    factor_set_id: Mapped[int] = mapped_column(ForeignKey("factor_set.id"), nullable=False)
    sector_id: Mapped[int] = mapped_column(ForeignKey("sector.id"), nullable=False)
    food_category_id: Mapped[int | None] = mapped_column(ForeignKey("food_category.id"))
    gwp_horizon: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=100)
    excluded_from_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    exclusion_reason: Mapped[str | None] = mapped_column(String(255))

    def __str__(self) -> str:
        #: Required of every mapped model by tests/admin/test_model_str.py.
        #: This table has no `code`, so the id is the only human handle there
        #: is; `token` is deliberately not rendered — §2.3 makes it a
        #: deduplication key for a draft record, not something to display.
        return f"submission #{self.id} ({self.created_at:%Y-%m-%d})" if self.created_at \
            else f"submission #{self.id}"


class SubmissionLine(Base):
    __tablename__ = "submission_line"
    __table_args__ = (
        UniqueConstraint("submission_id", "scenario", "destination_id", name="uq_submission_line_scope"),
        CheckConstraint("qty_kg >= 0", name="ck_submission_line_qty"),
    )
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submission.id", ondelete="CASCADE"), nullable=False)
    scenario: Mapped[Scenario] = mapped_column(Enum(Scenario), nullable=False)
    destination_id: Mapped[int] = mapped_column(ForeignKey("destination.id"), nullable=False)
    qty_kg: Mapped[Decimal] = mapped_column(Numeric(16, 3), nullable=False)

    def __str__(self) -> str:
        #: Required of every mapped model by tests/admin/test_model_str.py.
        #: No `destination` relationship is declared on this table, so the
        #: scenario and the quantity are what identify the row.
        scenario = self.scenario.value if isinstance(self.scenario, Scenario) else self.scenario
        return f"{scenario} {self.qty_kg} kg"
