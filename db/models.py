"""Part B SQLAlchemy models defined by docs/interfaces.md section 2."""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base

BIGINT_PK = BigInteger().with_variant(Integer, "sqlite")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class FactorSetStatus(str, enum.Enum):
    draft = "draft"
    published = "published"
    archived = "archived"


class Scenario(str, enum.Enum):
    current = "current"
    alternative = "alternative"


class DestinationGroup(Base):
    __tablename__ = "destination_group"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_waste: Mapped[bool] = mapped_column(Boolean, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Destination(Base):
    __tablename__ = "destination"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("destination_group.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Sector(Base):
    __tablename__ = "sector"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class FoodCategory(Base):
    __tablename__ = "food_category"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_standard_mix: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Metric(Base):
    __tablename__ = "metric"
    __table_args__ = (CheckConstraint("display_precision >= 0", name="ck_metric_precision"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    display_unit: Mapped[str | None] = mapped_column(String(32))
    display_precision: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=2)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class UnitPreset(Base):
    __tablename__ = "unit_preset"
    __table_args__ = (CheckConstraint("kg_per_unit >= 0", name="ck_unit_preset_kg"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    food_category_id: Mapped[int | None] = mapped_column(ForeignKey("food_category.id"))
    kg_per_unit: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    source_note: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class FactorSet(Base):
    __tablename__ = "factor_set"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    version_label: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    status: Mapped[FactorSetStatus] = mapped_column(Enum(FactorSetStatus), nullable=False)
    is_mock: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime)
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    published_by: Mapped[str | None] = mapped_column(String(128))
    notes: Mapped[str | None] = mapped_column(Text)


class FactorUpstream(Base):
    __tablename__ = "factor_upstream"
    __table_args__ = (
        UniqueConstraint(
            "factor_set_id",
            "sector_id",
            "food_category_id",
            "metric_id",
            name="uq_factor_upstream_scope",
        ),
    )
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    factor_set_id: Mapped[int] = mapped_column(ForeignKey("factor_set.id", ondelete="CASCADE"), nullable=False)
    sector_id: Mapped[int] = mapped_column(ForeignKey("sector.id"), nullable=False)
    food_category_id: Mapped[int] = mapped_column(ForeignKey("food_category.id"), nullable=False)
    metric_id: Mapped[int] = mapped_column(ForeignKey("metric.id"), nullable=False)
    value_per_kg: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)


class FactorDownstream(Base):
    __tablename__ = "factor_downstream"
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    factor_set_id: Mapped[int] = mapped_column(ForeignKey("factor_set.id", ondelete="CASCADE"), nullable=False)
    destination_id: Mapped[int] = mapped_column(ForeignKey("destination.id"), nullable=False)
    food_category_id: Mapped[int | None] = mapped_column(ForeignKey("food_category.id"))
    metric_id: Mapped[int] = mapped_column(ForeignKey("metric.id"), nullable=False)
    value_per_kg: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)


Index(
    "uq_factor_downstream_scope",
    FactorDownstream.factor_set_id,
    FactorDownstream.destination_id,
    func.coalesce(FactorDownstream.food_category_id, 0),
    FactorDownstream.metric_id,
    unique=True,
)


class Constant(Base):
    __tablename__ = "constant"
    __table_args__ = (UniqueConstraint("factor_set_id", "code", name="uq_constant_scope"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    factor_set_id: Mapped[int] = mapped_column(ForeignKey("factor_set.id", ondelete="CASCADE"), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text)


class Formula(Base):
    __tablename__ = "formula"
    __table_args__ = (UniqueConstraint("factor_set_id", "metric_id", name="uq_formula_scope"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    factor_set_id: Mapped[int] = mapped_column(ForeignKey("factor_set.id", ondelete="CASCADE"), nullable=False)
    metric_id: Mapped[int] = mapped_column(ForeignKey("metric.id"), nullable=False)
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)


class Equivalence(Base):
    __tablename__ = "equivalence"
    __table_args__ = (UniqueConstraint("factor_set_id", "code", name="uq_equivalence_scope"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    factor_set_id: Mapped[int] = mapped_column(ForeignKey("factor_set.id", ondelete="CASCADE"), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    source_metric_id: Mapped[int] = mapped_column(ForeignKey("metric.id"), nullable=False)
    value_per_unit: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    label_template: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


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


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    table_name: Mapped[str] = mapped_column(String(64), nullable=False)
    row_id: Mapped[int | None] = mapped_column(BigInteger)
    before_json: Mapped[dict | None] = mapped_column(JSON)
    after_json: Mapped[dict | None] = mapped_column(JSON)
