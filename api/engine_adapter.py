"""Narrow, lazily imported seam between B's API and A's `engine/` package.

**`engine/` does not exist yet.** Everything in `DefaultEngineAdapter` is
written against §3 and §4.2 as they stand at contract v1.2 and against
nothing else; the API tests inject a fake adapter, so this module is exercised
here only by `tests/api/test_engine_adapter.py`, which drives
`serialize_result` with a stand-in built from §3's field names.

What A must deliver for this module to work unchanged:

* `engine.types` exporting `CalculationRequest(entries, gwp_horizon)`,
  `EntryInput(sector_code, food_category_code, current, alternative)` and
  `ScenarioLine(destination_code, qty_kg)` - §3's input half. `ScenarioInput`
  is gone in v1.2 and must not come back: `make_request` below built one
  until this task, which is how a single-entry request reached a repository
  that writes N entries without anything raising.
* `engine.calculate.calculate(req, bundle) -> CalculationResult` (§4.2),
  returning §3's output half: `factor_set_version`, `is_mock`, `gwp_horizon`,
  `totals` and `entries`.
* `engine.bundle.FactorBundle.from_json` and `.validate()` (§4.1).

**`totals` comes from the engine and is never summed here.** §4.2 rules on
this and it is not open: an adapter that added per-entry `MetricResult.total`
values together would be a second calculation site, structurally the same
defect as summing in the browser, and the headline number a user sees would
have no golden case behind it. `serialize_result` therefore performs no
arithmetic at all. The single reshaping it does perform is §3's stated wire
hoist - `totals.total_kg` is `totals.current.total_kg` - which moves a value
rather than computing one.

`by_destination` is carried per entry and omitted at the totals level (§3
rule 2): the same destination can appear under several entries drawing
different upstream factors, so a cross-entry destination breakdown has no
single correct aggregation rule. The omission is expressed as "drop the key
when the tuple is empty", which is one rule rather than two, and is exactly
equivalent here because §6.2 requires every entry scenario to carry at least
one line.

Two §6.2 response fields are supplied by `api/router.py` after this mapping
returns, because neither has a §3 counterpart: `token` (from
`upsert_submission`, null on a dry run) and `factor_source`, which the engine
cannot know - it is pure over a `FactorBundle` and has no way to tell whether
that bundle came from the published set, a named version, or the request body.
"""

from __future__ import annotations

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
            from engine.types import CalculationRequest, EntryInput, ScenarioLine
        except ImportError as exc:
            raise RuntimeError("A's engine package is not installed") from exc
        return CalculationRequest, EntryInput, ScenarioLine

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
        """§6.2's wire request as §3's `CalculationRequest`.

        Entry order is preserved from the body: §3 rule 1 makes it part of
        the result, and `submission_entry.sort_order` (§2.3) persists it.
        """
        CalculationRequest, EntryInput, ScenarioLine = self._types()

        def lines(rows):
            if rows is None:
                return None
            return tuple(
                ScenarioLine(destination_code=row.destination, qty_kg=row.qty_kg)
                for row in rows
            )

        return CalculationRequest(
            entries=tuple(
                EntryInput(
                    sector_code=entry.sector,
                    food_category_code=entry.food_category,
                    current=lines(entry.current),
                    alternative=lines(entry.alternative),
                )
                for entry in payload.entries
            ),
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
        """§3's `CalculationResult` as §6.2's 200 body. No arithmetic (§4.2).

        `Decimal` values are left as `Decimal`; `api.serialization.wire` is
        the one place a decimal becomes a JSON string (§1.2).
        """
        return {
            "factor_set": {
                "version_label": result.factor_set_version,
                "is_mock": result.is_mock,
            },
            "gwp_horizon": result.gwp_horizon,
            "totals": _totals(result.totals),
            "entries": [_entry(entry) for entry in result.entries],
        }


def _totals(totals: Any) -> dict[str, Any]:
    return {
        # §3: a wire-format hoist, not a fifth field on CalculationTotals.
        # It is the current scenario's mass, and one figure is honest only
        # because §6.2 requires each entry's two scenarios to agree to within
        # MASS_TOLERANCE_KG - a response carrying one mass cannot expose a
        # discrepancy between two.
        "total_kg": totals.current.total_kg,
        "current": _scenario(totals.current, with_total_kg=False),
        "alternative": _scenario(totals.alternative, with_total_kg=False),
        "net_benefit": _net_benefit(totals.net_benefit),
    }


def _entry(entry: Any) -> dict[str, Any]:
    return {
        "sector": entry.sector_code,
        "food_category": entry.food_category_code,
        "current": _scenario(entry.current),
        "alternative": _scenario(entry.alternative),
        "net_benefit": _net_benefit(entry.net_benefit),
    }


def _scenario(scenario: Any, *, with_total_kg: bool = True) -> dict[str, Any] | None:
    if scenario is None:
        return None
    body: dict[str, Any] = {}
    if with_total_kg:
        body["total_kg"] = scenario.total_kg
    body["metrics"] = {
        code: _metric(metric) for code, metric in scenario.metrics.items()
    }
    body["equivalences"] = [
        {
            "code": item.code,
            "label": item.label,
            "value": item.value,
            "source_metric": item.source_metric_code,
        }
        for item in scenario.equivalences
    ]
    return body


def _metric(metric: Any) -> dict[str, Any]:
    # `metric_code` is not repeated on the wire: it is the key of `metrics`.
    body: dict[str, Any] = {
        "unit": metric.unit,
        "display_precision": metric.display_precision,
        "total": metric.total,
    }
    if metric.by_destination:
        body["by_destination"] = [
            {
                "destination": row.destination_code,
                "qty_kg": row.qty_kg,
                "upstream": row.upstream,
                "downstream": row.downstream,
                "value": row.value,
            }
            for row in metric.by_destination
        ]
    return body


def _net_benefit(net_benefit: Any) -> dict[str, Decimal] | None:
    if net_benefit is None:
        return None
    return dict(net_benefit)
