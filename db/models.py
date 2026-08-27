"""Part B SQLAlchemy models defined by docs/interfaces.md section 2.

Only the three submission tables are declared here. The other thirteen are
re-exported from `admin/` — see the note above the imports below.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    DECIMAL,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import BIGINT_PK, Base

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
#
# **The trap while it lasts.** The full chain is
# `admin.audit → db.repository → db.models → admin.{models, taxonomy_models,
# factor_models}`, and it is acyclic only because those three model modules
# import nothing from `admin.audit`. Adding one such import — an audit helper
# pulled into a model for convenience, say — closes the cycle, and a circular
# import fails at collection with an ImportError that stops the whole test
# suite, exactly like the duplicate-table crash these re-exports replaced. The
# three model modules must stay leaves.
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
    "SubmissionEntry",
    "SubmissionLine",
    "UnitPreset",
    "utcnow",
]

def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Scenario(str, enum.Enum):
    current = "current"
    alternative = "alternative"


class Submission(Base):
    """One public calculation. Contract §2.3.

    `sector_id` and `food_category_id` are **not** here — they live on
    `submission_entry`, one row per `(sector, food_category)` pair, because a
    food business has waste at more than one point in the supply chain and
    each point draws a different upstream factor. `gwp_horizon` does stay
    here: §2.3 calls it "[a]pplies to the whole submission" and §6.2 sends it
    once, outside `entries[]`.

    **No IP address, no user agent, no fingerprint.** Deduplication is by
    `token` alone, which identifies a draft record and not a person, and
    `expire_tokens` (§5.3) nulls the column an hour on — keeping the data,
    severing the linkage.
    """

    __tablename__ = "submission"
    __table_args__ = (
        #: The two values §6.2 allows. `compare_metadata` cannot see a missing
        #: CHECK on this SQLAlchemy/MySQL combination (see the "Known blind
        #: spot" note in tests/test_migrations.py), so this one is proven
        #: behaviourally by tests/db/test_submissions.py and against
        #: information_schema by tests/test_migrations.py.
        CheckConstraint("gwp_horizon IN (20, 100)", name="ck_submission_horizon"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    #: CHAR(36), not VARCHAR: §2.3 specifies CHAR and a UUID4 in canonical
    #: form is always exactly 36 characters — the same reasoning `ip_block`
    #: gives for its CHAR(64) hex digest.
    token: Mapped[str | None] = mapped_column(CHAR(36), unique=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    #: Version stamp. Every submission records the factor set it was
    #: calculated against, so a historical result stays reproducible after
    #: staff have revised the numbers.
    factor_set_id: Mapped[int] = mapped_column(ForeignKey("factor_set.id"), nullable=False)
    gwp_horizon: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=100, server_default="100"
    )
    #: Staff moderation, not user consent (§2.3). There is no consent
    #: checkbox: one calculation is one submission.
    excluded_from_public: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    exclusion_reason: Mapped[str | None] = mapped_column(String(255))

    #: v1.48. The period the visitor says their figures cover - "one_week",
    #: "one_month", "one_quarter", "one_year". A LABEL, not a computation:
    #: the client ruled explicitly that it does not enter the engine, and
    #: nothing annualises or scales anything from it. It travels to the
    #: results page and into the download so that a figure somebody keeps has
    #: a period attached to it, which is the whole of what it is for.
    #:
    #: A string rather than a pair of dates because a period chosen from a
    #: list is what was asked for, and two dates would invite arithmetic that
    #: the ruling above says must not happen.
    time_frame: Mapped[str | None] = mapped_column(String(32))

    #: v1.48, and it reverses §2.3's "there is no consent checkbox".
    #:
    #: **This is a second axis, not a replacement for `excluded_from_public`.**
    #: That one is staff moderation - a member of staff judging a row
    #: implausible. This one is the visitor's own choice. The public
    #: aggregate needs BOTH: staff can withdraw a row the visitor offered,
    #: and a row the visitor kept is not staff's to publish. Neither can
    #: stand in for the other, and `get_public_stats` predicates on both.
    #:
    #: FALSE by default, which is the point. A default of TRUE would opt
    #: every visitor in and leave the column decorative.
    is_public_contributed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )

    entries: Mapped[list["SubmissionEntry"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan",
        order_by="SubmissionEntry.sort_order",
    )
    #: Added for `/admin/submissions` (§8.2), which shows which numbers a
    #: calculation was run against — the whole point of stamping `factor_set_id`
    #: on every submission is that a historical result stays reproducible after
    #: staff revise the factors, and an integer id does not tell a staff member
    #: which revision that was.
    #:
    #: **No `back_populates`**, for the reason `SubmissionLine.destination`
    #: gives: the reverse is every submission ever calculated against a factor
    #: set, which is a collection that grows without bound and that
    #: `FactorSetAdmin` would try to render on its details page.
    factor_set: Mapped["FactorSet"] = relationship()

    def __str__(self) -> str:
        #: Required of every mapped model by tests/admin/test_model_str.py.
        #: This table has no `code`, so the id is the only human handle there
        #: is; `token` is deliberately not rendered — §2.3 makes it a
        #: deduplication key for a draft record, not something to display.
        return f"submission #{self.id} ({self.created_at:%Y-%m-%d})" if self.created_at \
            else f"submission #{self.id}"


class SubmissionEntry(Base):
    """One `(sector, food_category)` pair within a submission. Contract §2.3.

    **The `food_category_id IS NULL` trap.** The declared
    UNIQUE(submission_id, sector_id, food_category_id) below does not stop two
    entries with the same sector and no food category from coexisting: MySQL
    treats NULLs as distinct inside a UNIQUE key, so the constraint is silent
    on exactly the "the user did not break waste down by type" rows it most
    needs to guard. `uq_submission_entry_generic` — a functional index with
    `(COALESCE(food_category_id, 0))` as a key part — is what actually closes
    it. This is the same defect B found in `factor_downstream`, which §2.2
    credits to her by name; the shape is identical and so is the failure.
    Duplicated entries are not an error anywhere: `POST /calculate` writes
    them all and §5.4's `by_sector` aggregation then counts one user's single
    answer several times. Wrong numbers, no exception, nothing in the logs.

    Declared here as a SQLAlchemy `Index` so that `Base.metadata.create_all()`
    produces it too — that is the path every test outside
    tests/test_migrations.py builds its schema with. The **same** index is
    also written as raw SQL in alembic/versions/0008_submissions.py, because
    the migration chain is a second, independent path to the same schema and
    autogenerate cannot emit an expression key part on its own. It is excluded
    from `compare_metadata` in tests/test_migrations.py's `_include_object`
    for the reason documented there, and proven instead against
    `information_schema`.
    """

    __tablename__ = "submission_entry"
    __table_args__ = (
        UniqueConstraint("submission_id", "sector_id", "food_category_id",
                         name="uq_submission_entry"),
        Index(
            "uq_submission_entry_generic",
            "submission_id", "sector_id",
            text("(COALESCE(food_category_id, 0))"),
            unique=True,
        ),
    )

    #: BIGINT, per §2.3 — one row per supply-chain stage per public
    #: calculation, so it grows faster than `submission` does.
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submission.id", ondelete="CASCADE"), nullable=False
    )
    sector_id: Mapped[int] = mapped_column(ForeignKey("sector.id"), nullable=False)
    #: Null when the user did not break waste down by type. The engine reads
    #: that as the standard mix to pick a factor (§6.2), but §5.4 gives it its
    #: own `unspecified` bucket in the statistics and explicitly forbids
    #: resolving it to `standard_mix` there: one is the factor that was
    #: applied, the other is what the user actually told us, and folding them
    #: together both claims a composition nobody gave and makes a deliberate
    #: choice of the standard mix unreadable.
    food_category_id: Mapped[int | None] = mapped_column(
        ForeignKey("food_category.id"), nullable=True
    )
    #: The order the user entered them, so `entries[]` in the §6.2 response
    #: can be paired with the rows on screen. Not id order: §5.3 rebuilds the
    #: whole entry set on every upsert, which reassigns ids.
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0,
                                            server_default="0")

    #: v1.48. What this stage of the supply chain put through in the period,
    #: so that "waste as a share of production" can be stated. Optional: a
    #: visitor who does not know it still gets every other figure, and
    #: `MoneyResult` simply omits the share.
    #:
    #: DECIMAL(16,3) matches `submission_line.qty_kg` - a production total is
    #: a mass in the same units and at the same scale as the waste measured
    #: against it, and two different scales for one comparison is how a
    #: thousandfold error gets in.
    total_input_kg: Mapped[Decimal | None] = mapped_column(DECIMAL(16, 3))

    #: v1.48, and both are STATISTICS ONLY. The client's ruling on open item
    #: O-2 was that the value of the food does not enter the main formula and
    #: that cost-price versus retail-price "doesn't matter" - it is whatever
    #: the client's own client means by it.
    #:
    #: So these are inputs a visitor typed, not a metric the engine computed.
    #: They are deliberately NOT a `metric` row: the formula language takes
    #: `(qty_kg, upstream, downstream, const_*)` per LINE, and an
    #: entry-level figure a person typed cannot be expressed in it. Making
    #: one would mean inventing a per-kilogram money factor, which is exactly
    #: the modelling this ruling avoided.
    #:
    #: DECIMAL(14,2): New Zealand dollars and cents. Two places, because
    #: money has two, and never FLOAT.
    total_value_nzd: Mapped[Decimal | None] = mapped_column(DECIMAL(14, 2))
    wasted_value_nzd: Mapped[Decimal | None] = mapped_column(DECIMAL(14, 2))

    submission: Mapped[Submission] = relationship(back_populates="entries")
    sector: Mapped[Sector] = relationship()
    food_category: Mapped[FoodCategory | None] = relationship()
    lines: Mapped[list["SubmissionLine"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan"
    )

    def __str__(self) -> str:
        #: Required of every mapped model by tests/admin/test_model_str.py.
        #: No code of its own — the (sector, food_category) pair the unique
        #: constraint is keyed on is what names the row. food_category is
        #: nullable and means the user gave no breakdown, so say that rather
        #: than rendering a blank.
        category = self.food_category.code if self.food_category else "no category breakdown"
        return f"{self.sector.code}/{category}"


class SubmissionLine(Base):
    """One destination and quantity, in one scenario, within one entry.

    Contract §2.3. The FK is `submission_entry_id`, not `submission_id`: a
    line keyed on the submission cannot say which supply-chain stage it
    belongs to, and the same destination legitimately appears once per entry.
    """

    __tablename__ = "submission_line"
    __table_args__ = (
        UniqueConstraint("submission_entry_id", "scenario", "destination_id",
                         name="uq_submission_line_scope"),
        #: §6.2 validates qty_kg >= 0 at the API too; this is the backstop for
        #: every other writer. Invisible to `compare_metadata` — see the CHECK
        #: note on Submission above.
        CheckConstraint("qty_kg >= 0", name="ck_submission_line_qty"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    submission_entry_id: Mapped[int] = mapped_column(
        ForeignKey("submission_entry.id", ondelete="CASCADE"), nullable=False
    )
    scenario: Mapped[Scenario] = mapped_column(Enum(Scenario), nullable=False)
    destination_id: Mapped[int] = mapped_column(ForeignKey("destination.id"), nullable=False)
    #: DECIMAL, never FLOAT/DOUBLE. Three decimal places is the scale §6.2
    #: validates the request against and §1.2 transmits as a string.
    qty_kg: Mapped[Decimal] = mapped_column(DECIMAL(16, 3), nullable=False)

    entry: Mapped[SubmissionEntry] = relationship(back_populates="lines")
    #: Added for `/admin/submissions` (§8.2), and the comment it replaces said
    #: this table is "written in bulk by `upsert_submission` and never read one
    #: row at a time". That was true until a screen existed to read it: the
    #: drill-down renders every line of a calculation, and a line whose
    #: destination is an integer id tells a staff member nothing.
    #:
    #: **No `back_populates`, deliberately.** The reverse — every submission
    #: line ever recorded, hanging off a taxonomy row — is a collection nothing
    #: wants and that `DestinationAdmin` would try to render on its own details
    #: page. This direction is a lookup; the other would be a liability that
    #: grows with every calculation the public runs.
    destination: Mapped["Destination"] = relationship()

    def __str__(self) -> str:
        #: Required of every mapped model by tests/admin/test_model_str.py.
        #:
        #: **Still not the destination, now that the relationship exists.**
        #: `__str__` is called by sqladmin wherever a row is rendered, including
        #: on objects that have left their session, and touching a lazy
        #: relationship there raises `DetachedInstanceError` from inside a
        #: template — a 500 on a page that was only trying to print a label.
        #: The drill-down loads `destination` eagerly and renders it itself.
        scenario = self.scenario.value if isinstance(self.scenario, Scenario) else self.scenario
        return f"{scenario} {self.qty_kg} kg"
