"""The factor tables. Contract §2.2.

These carry the numbers the calculator actually multiplies. Every row belongs
to exactly one `factor_set`, which is what makes a published result
reproducible: a submission stamps the set it was calculated against, so the
numbers can be recovered years later even after staff have revised them.

Separate from admin/taxonomy_models.py because the lifecycle is different —
taxonomy rows are edited in place and stay resolvable forever, factor rows
are cloned into a new version and the old version is archived intact.
"""

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DECIMAL, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text,
    UniqueConstraint, text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from admin.taxonomy_models import Destination, FoodCategory, Metric, Sector
from db.base import Base


class FactorSetStatus(str, enum.Enum):
    draft = "draft"
    published = "published"
    archived = "archived"


class FactorSet(Base):
    """One version of every number in the calculator. Contract §2.2.

    At most one row may be `published` at any time. That invariant is not a
    database constraint — it is a statement about the table — and is enforced
    in admin/taxonomy_rules.py, called from AuditedModelView's
    validate_before_commit hook.
    """

    __tablename__ = "factor_set"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    version_label: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    status: Mapped[FactorSetStatus] = mapped_column(
        Enum(FactorSetStatus, native_enum=True), nullable=False,
        default=FactorSetStatus.draft,
    )
    #: Drives the site-wide placeholder-data banner, which is mandatory and
    #: non-dismissible while true. Defaults to true so a set nobody has
    #: vouched for cannot be published silently as real data.
    is_mock: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True,
                                          server_default="1")
    effective_from: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class FactorUpstream(Base):
    """Per-kilogram impact of producing the food, by sector and category.

    Contract §2.2. Roughly 270 rows per factor set.
    """

    __tablename__ = "factor_upstream"
    __table_args__ = (
        UniqueConstraint("factor_set_id", "sector_id", "food_category_id",
                         "metric_id", name="uq_factor_upstream"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    factor_set_id: Mapped[int] = mapped_column(
        ForeignKey("factor_set.id", ondelete="CASCADE"), nullable=False
    )
    sector_id: Mapped[int] = mapped_column(ForeignKey("sector.id"), nullable=False)
    food_category_id: Mapped[int] = mapped_column(
        ForeignKey("food_category.id"), nullable=False
    )
    metric_id: Mapped[int] = mapped_column(ForeignKey("metric.id"), nullable=False)
    value_per_kg: Mapped[Decimal] = mapped_column(DECIMAL(20, 10), nullable=False)
    source_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_quality: Mapped[str | None] = mapped_column(String(32), nullable=True)

    factor_set: Mapped[FactorSet] = relationship()
    sector: Mapped[Sector] = relationship()
    food_category: Mapped[FoodCategory] = relationship()
    metric: Mapped[Metric] = relationship()


class FactorDownstream(Base):
    """Per-kilogram impact of the disposal route. Contract §2.2.

    `food_category_id` is nullable and means "applies to every food category
    for this destination" — that is how a per-tonne charge like the waste levy
    is expressed. Lookup order: exact match, then the NULL row, then zero.

    `value_per_kg` **may be negative**: animal feed displaces feed that would
    otherwise have been produced, so diverting to it is a genuine credit.
    Nothing may clamp this to zero.

    Roughly 600 rows per factor set.

    The declared UNIQUE(factor_set_id, destination_id, food_category_id,
    metric_id) below does not stop two `food_category_id IS NULL` rows from
    coexisting — MySQL treats NULLs as distinct, so the constraint is silent
    on exactly the "applies to every category" rows it most needs to guard.
    A functional index over COALESCE(food_category_id, 0) is what actually
    closes that gap, but it is **not** declared here as a SQLAlchemy `Index`:
    doing so makes `alembic revision --autogenerate` emit it correctly, but
    it also makes `compare_metadata` (tests/test_migrations.py) misreport it
    as a "changed index" on every run — confirmed against this MySQL/
    SQLAlchemy combination, which can only produce an "approximate
    signature" when reflecting an expression index back out of MySQL, and
    then diffs that approximation against the model's declaration as if it
    were a real difference. The index instead lives only as raw SQL in
    alembic/versions/0005_factors.py's upgrade()/downgrade(), which is what
    the brief calls for and what a plain `compare_metadata` genuinely cannot
    see either way. tests/admin/test_factor_models.py creates the same index
    by hand against the `create_all()`-built test schema, since that path
    never runs migration DDL — see the fixture there for why.
    """

    __tablename__ = "factor_downstream"
    __table_args__ = (
        UniqueConstraint("factor_set_id", "destination_id", "food_category_id",
                         "metric_id", name="uq_factor_downstream"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    factor_set_id: Mapped[int] = mapped_column(
        ForeignKey("factor_set.id", ondelete="CASCADE"), nullable=False
    )
    destination_id: Mapped[int] = mapped_column(
        ForeignKey("destination.id"), nullable=False
    )
    food_category_id: Mapped[int | None] = mapped_column(
        ForeignKey("food_category.id"), nullable=True
    )
    metric_id: Mapped[int] = mapped_column(ForeignKey("metric.id"), nullable=False)
    value_per_kg: Mapped[Decimal] = mapped_column(DECIMAL(20, 10), nullable=False)
    source_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_quality: Mapped[str | None] = mapped_column(String(32), nullable=True)

    factor_set: Mapped[FactorSet] = relationship()
    destination: Mapped[Destination] = relationship()
    food_category: Mapped[FoodCategory | None] = relationship()
    metric: Mapped[Metric] = relationship()


class Constant(Base):
    """A named number a formula can reference. Contract §2.2.

    `GWP_CH4_20` and `GWP_CH4_100` are the pair behind the special
    `const_GWP_CH4` binding (§4.3): the engine resolves it to one or the other
    according to the request's gwp_horizon, so a formula never hard-codes a
    horizon.
    """

    __tablename__ = "constant"
    __table_args__ = (
        UniqueConstraint("factor_set_id", "code", name="uq_constant_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    factor_set_id: Mapped[int] = mapped_column(
        ForeignKey("factor_set.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[Decimal] = mapped_column(DECIMAL(20, 10), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    factor_set: Mapped[FactorSet] = relationship()


class Formula(Base):
    """How one metric is computed from one line. Contract §2.2, §4.3.

    The expression computes a single line's contribution; the engine sums.
    That is why the language needs no arrays, no loops and no sum() — which
    is what keeps the evaluator's security boundary unambiguous.
    """

    __tablename__ = "formula"
    __table_args__ = (
        UniqueConstraint("factor_set_id", "metric_id", name="uq_formula_metric"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    factor_set_id: Mapped[int] = mapped_column(
        ForeignKey("factor_set.id", ondelete="CASCADE"), nullable=False
    )
    metric_id: Mapped[int] = mapped_column(ForeignKey("metric.id"), nullable=False)
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    factor_set: Mapped[FactorSet] = relationship()
    metric: Mapped[Metric] = relationship()


class Equivalence(Base):
    """"Equivalent to driving 14,500 km". Contract §2.2.

    `label_template` lives in the row, not in a front-end template, so staff
    can add a fourth equivalence or reword an existing one without a code
    change — the same reasoning as the metric table.
    """

    __tablename__ = "equivalence"
    __table_args__ = (
        UniqueConstraint("factor_set_id", "code", name="uq_equivalence_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    factor_set_id: Mapped[int] = mapped_column(
        ForeignKey("factor_set.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    source_metric_id: Mapped[int] = mapped_column(
        ForeignKey("metric.id"), nullable=False
    )
    value_per_unit: Mapped[Decimal] = mapped_column(DECIMAL(20, 10), nullable=False)
    label_template: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Open item O-3: the New Zealand basis for km driven, meals and showers
    #: is unsettled, and an equivalence with no stated source is the figure
    #: most likely to be challenged in public.
    source_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0,
                                            server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True,
                                         server_default="1")

    factor_set: Mapped[FactorSet] = relationship()
    source_metric: Mapped[Metric] = relationship()
