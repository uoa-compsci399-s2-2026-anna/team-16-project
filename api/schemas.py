"""Strict wire schemas. Decimal inputs are JSON strings by contract."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, UUID4, field_validator, model_validator

MAX_LINE_QTY = Decimal("10000000")
MAX_SCENARIO_QTY = Decimal("50000000")
MAX_SCENARIO_LINES = 20
BUNDLE_TABLES = (
    "sectors",
    "food_categories",
    "destination_groups",
    "destinations",
    "metrics",
    "constants",
    "formulas",
    "upstream",
    "downstream",
    "equivalences",
)


def decimal_from_string(value: Any) -> Decimal:
    if not isinstance(value, str):
        raise ValueError("decimal values must be JSON strings")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("invalid decimal string") from exc
    if not parsed.is_finite():
        raise ValueError("decimal value must be finite")
    return parsed


class ScenarioLinePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destination: str = Field(min_length=1, max_length=64)
    qty_kg: Decimal

    @field_validator("qty_kg", mode="before")
    @classmethod
    def parse_qty(cls, value: Any) -> Decimal:
        return decimal_from_string(value)

    @field_validator("qty_kg")
    @classmethod
    def bound_qty(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("must be greater than or equal to zero")
        if value > MAX_LINE_QTY:
            raise ValueError("exceeds 10,000,000 kg")
        if value != value.quantize(Decimal("0.001")):
            raise ValueError("must have no more than 3 decimal places")
        return value


class DryRunPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    factor_set_version: str | None = Field(default=None, min_length=1, max_length=128)
    bundle: dict[str, Any] | None = None

    @model_validator(mode="after")
    def mutually_exclusive(self) -> "DryRunPayload":
        if self.factor_set_version is not None and self.bundle is not None:
            raise ValueError("factor_set_version and bundle are mutually exclusive")
        return self


class CalculatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: UUID4 | None = None
    sector: str = Field(min_length=1, max_length=64)
    food_category: str | None = Field(default=None, min_length=1, max_length=64)
    gwp_horizon: int = 100
    current: list[ScenarioLinePayload]
    alternative: list[ScenarioLinePayload] | None = None
    dry_run: DryRunPayload | None = None

    @field_validator("gwp_horizon")
    @classmethod
    def validate_horizon(cls, value: int) -> int:
        if value not in (20, 100):
            raise ValueError("must be 20 or 100")
        return value

    @model_validator(mode="after")
    def validate_scenarios(self) -> "CalculatePayload":
        self._validate_scenario("current", self.current, required=True)
        self._validate_scenario("alternative", self.alternative, required=False)
        return self

    @staticmethod
    def _validate_scenario(
        name: str, lines: list[ScenarioLinePayload] | None, *, required: bool
    ) -> None:
        if lines is None:
            if required:
                raise ValueError(f"{name} is required")
            return
        if required and not lines:
            raise ValueError(f"{name} must contain at least one line")
        if len(lines) > MAX_SCENARIO_LINES:
            raise ValueError(f"{name} exceeds 20 lines")
        destinations = [line.destination for line in lines]
        if len(destinations) != len(set(destinations)):
            raise ValueError(f"{name} contains duplicate destinations")
        if sum((line.qty_kg for line in lines), Decimal("0")) > MAX_SCENARIO_QTY:
            raise ValueError(f"{name} exceeds 50,000,000 kg")


def bundle_row_count(bundle: dict[str, Any]) -> int:
    total = 0
    for name in BUNDLE_TABLES:
        value = bundle.get(name, [])
        if not isinstance(value, list):
            raise ValueError(f"bundle.{name} must be an array")
        total += len(value)
    return total
