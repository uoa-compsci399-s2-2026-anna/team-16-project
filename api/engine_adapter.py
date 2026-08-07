"""Narrow, lazily imported seam between B and A's future engine package."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from decimal import Decimal
from typing import Any, Protocol

from api.schemas import CalculatePayload


class EngineAdapter(Protocol):
    def bundle_from_json(self, data: dict[str, Any]) -> Any: ...
    def make_request(self, payload: CalculatePayload) -> Any: ...
    def calculate(self, request: Any, bundle: Any) -> Any: ...
    def serialize_result(self, result: Any) -> dict[str, Any]: ...


class DefaultEngineAdapter:
    def _types(self):
        try:
            from engine.types import CalculationRequest, ScenarioInput, ScenarioLine
        except ImportError as exc:
            raise RuntimeError("A's engine package is not installed") from exc
        return CalculationRequest, ScenarioInput, ScenarioLine

    def bundle_from_json(self, data: dict[str, Any]) -> Any:
        try:
            from engine.bundle import FactorBundle
        except ImportError:
            try:
                from engine.types import FactorBundle
            except ImportError as exc:
                raise RuntimeError("A's engine package is not installed") from exc
        return FactorBundle.from_json(data)

    def make_request(self, payload: CalculatePayload) -> Any:
        CalculationRequest, ScenarioInput, ScenarioLine = self._types()

        def scenario(lines):
            if lines is None:
                return None
            return ScenarioInput(
                sector_code=payload.sector,
                food_category_code=payload.food_category,
                lines=tuple(
                    ScenarioLine(destination_code=x.destination, qty_kg=x.qty_kg)
                    for x in lines
                ),
            )

        return CalculationRequest(
            current=scenario(payload.current),
            alternative=scenario(payload.alternative),
            gwp_horizon=payload.gwp_horizon,
        )

    def calculate(self, request: Any, bundle: Any) -> Any:
        try:
            from engine.calculate import calculate
        except ImportError:
            try:
                from engine import calculate
            except ImportError as exc:
                raise RuntimeError("A's engine package is not installed") from exc
        return calculate(request, bundle)

    def serialize_result(self, result: Any) -> dict[str, Any]:
        data = asdict(result) if is_dataclass(result) else dict(result)
        factor_set_version = data.pop("factor_set_version")
        is_mock = data.pop("is_mock")
        return {
            "factor_set": {
                "version_label": factor_set_version,
                "is_mock": is_mock,
            },
            **_normalise_result(data),
        }


def _normalise_result(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            if key in {"metric_code", "destination_code", "source_metric_code"}:
                renamed = {
                    "destination_code": "destination",
                    "source_metric_code": "source_metric",
                }.get(key)
                if renamed:
                    output[renamed] = _normalise_result(item)
                continue
            output[key] = _normalise_result(item)
        return output
    if isinstance(value, (list, tuple)):
        return [_normalise_result(item) for item in value]
    if is_dataclass(value):
        return _normalise_result(asdict(value))
    return value

