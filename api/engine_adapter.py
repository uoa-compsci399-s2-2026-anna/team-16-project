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

`by_destination` is carried per entry, and now at the totals level too
(v1.48, amending §3 rule 2): `qty_kg` and `value` are additive across
entries, so the engine sums them per destination for each metric; `upstream`
and `downstream` stay at zero there, because they are per-kilogram rates
that can differ between the entries sharing a destination and cannot be
summed or averaged into a meaningful figure. Nothing in this module computes
any of that -- it is `engine.calculate._roll_up`'s output, arriving on
`result.totals.current.metrics[code].by_destination` exactly as
`result.entries[i].current.metrics[code].by_destination` does, and this
module's own rule stays "drop the key when the tuple is empty", which now
applies uniformly at both levels rather than being unconditional at the
totals level.

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
    def food_item_problems(
        self, payload: CalculatePayload, bundle: Any
    ) -> list[dict[str, Any]]: ...


class DefaultEngineAdapter:
    def _types(self):
        try:
            from engine.types import CalculationRequest, EntryInput, ScenarioLine
        except ImportError as exc:
            raise RuntimeError("A's engine package is not installed") from exc
        return CalculationRequest, EntryInput, ScenarioLine

    def bundle_from_json(self, data: dict[str, Any]) -> Any:
        # One path, not a search. This tried `engine.bundle` and fell back to
        # `engine.types` because no version of the contract named the module
        # `FactorBundle` lives in; v1.4 §4.1 names it, so a fallback would
        # only let a layout the contract forbids work by accident and then
        # diverge from the golden suite, which imports it the documented way.
        try:
            from engine.bundle import FactorBundle
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
                    # v1.58. Passed through unresolved, exactly as the two
                    # codes above are: the engine is what checks a food
                    # against the vocabulary and against its parent, and a
                    # second place that did the same check is a second place
                    # that can disagree with it.
                    food_item_code=entry.food_item,
                    current=lines(entry.current),
                    alternative=lines(entry.alternative),
                    total_input_kg=entry.total_input_kg,
                    total_value_nzd=entry.total_value_nzd,
                    wasted_value_nzd=entry.wasted_value_nzd,
                )
                for entry in payload.entries
            ),
            gwp_horizon=payload.gwp_horizon,
        )

    def food_item_problems(
        self, payload: CalculatePayload, bundle: Any
    ) -> list[dict[str, Any]]:
        """The engine's two item refusals, raised early and with a field.

        `FactorBundle.resolve_food_item` refuses a food the bundle has never
        heard of, and a food whose parent is not the category it arrived with.
        Both are `UnknownCodeError`, which §4.4 maps to a bare
        `UNKNOWN_CODE` carrying no `details` at all -- so a front end is told
        that *something* in the request named a code that does not exist and
        cannot put the message beside the control the visitor used. Step 2.5
        is a control per entry, so that is the whole of what the message needs
        to say.

        **The rule is not restated here.** This asks `bundle` the same
        question `calculate_scenario` asks it, catches the refusal and gives
        it a field; the engine still refuses on its own if this is skipped, so
        the two cannot drift into disagreeing about which pairs are legal.
        That is the opposite of `admin/expressions.py` and
        `engine/evaluator.py`, which are two implementations of one rule and
        need an agreement test to stay honest.

        Matched on the exception's **class name** rather than by importing
        `engine.errors`, for the reason `api/errors.engine_problem` gives: this
        module is imported at start-up and `engine/` may not be installed.
        Anything else the bundle raises is not a documented condition and is
        re-raised, so it reaches `engine_problem` and lands on
        `INTERNAL_ERROR` rather than being reported as the visitor's fault.

        An entry whose `food_item` is `None` -- every request written before
        v1.58 -- asks the bundle nothing and produces nothing.
        """
        problems: list[dict[str, Any]] = []
        for index, entry in enumerate(payload.entries):
            if entry.food_item is None:
                continue
            #: `food_category=None` resolves to the standard mix in the engine
            #: and is refused outright by `entry_rule_problems` when a food is
            #: named, so by the time this runs a food always has a category.
            #: Guarded anyway rather than assumed: this method is public on
            #: the adapter and the ordering of two checks in one route is not
            #: something a future caller should have to know.
            if entry.food_category is None:
                continue
            try:
                bundle.resolve_food_item(entry.food_item, entry.food_category)
            except Exception as exc:  # noqa: BLE001 - re-raised below
                if type(exc).__name__ != "UnknownCodeError":
                    raise
                known = False
                try:
                    known = bool(bundle.has_food_item(entry.food_item))
                except Exception:  # noqa: BLE001 - a bundle without the query
                    known = False
                problems.append(
                    {
                        "field": f"entries[{index}].food_item",
                        "issue": (
                            "food_item_category_mismatch" if known
                            else "unknown_food_item"
                        ),
                        "message": str(exc),
                    }
                )
        return problems

    def calculate(self, request: Any, bundle: Any) -> Any:
        # v1.4 §4.2 names `engine/calculate.py`. The `from engine import
        # calculate` fallback that used to sit here was worse than redundant:
        # if `engine/calculate.py` existed but exported the function under
        # another name, the fallback bound the *module* object and the failure
        # became `TypeError: 'module' object is not callable` on the first
        # public calculation, instead of an ImportError naming what is
        # missing.
        try:
            from engine.calculate import calculate
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
        "money": _money(totals.money),
        # §4.6. The share of production, and the state of every figure it
        # is grouped with. Carried, not computed: `_share_percent` in the
        # engine is the only place either is decided, and an adapter that
        # divided one mass by another here would be the second calculation
        # site §4.2 rules out.
        "production_share_percent": totals.production_share_percent,
        "data_state": _data_state(totals.data_state),
    }


def _data_state(state: Any) -> dict[str, str]:
    """§4.6's four-state discriminant (v1.51 added the fourth, `undefined`
    -- see `engine/types.py::DataState`), one entry per totals-level
    figure. Passed straight through as strings, so a new state value needs
    no change here: this function has never named one.

    **Additive, and deliberately not a change to the figures' own shape.**
    Every value above stays the decimal string §1.2 requires, so a caller
    that has not learned about this key keeps reading exactly what it read
    before -- it simply renders nothing where a partial figure used to show a
    number that excluded part of the submission. A caller that has learned
    about it can tell "nobody answered" from "half of them answered", which
    a bare `null` cannot express and a sentinel decimal could only express by
    smuggling a plottable number into a numeric field.
    """
    return {
        "production_share_percent": state.production_share_percent,
        "total_value_nzd": state.total_value_nzd,
        "wasted_value_nzd": state.wasted_value_nzd,
        "wasted_share_percent": state.wasted_share_percent,
        "saving_nzd": state.saving_nzd,
    }


def _money(money: Any) -> dict[str, Any] | None:
    """§4.5, v1.48. `None` when no entry supplied a money figure at all --
    present-and-null rather than omitted, on the same terms `net_benefit` and
    `totals.alternative` already carry. No arithmetic: `MoneyResult`'s four
    fields arrive already computed and already at their own scale (§4.5)."""
    if money is None:
        return None
    return {
        "total_value_nzd": money.total_value_nzd,
        "wasted_value_nzd": money.wasted_value_nzd,
        "wasted_share_percent": money.wasted_share_percent,
        "saving_nzd": money.saving_nzd,
    }


def _entry(entry: Any) -> dict[str, Any]:
    return {
        "sector": entry.sector_code,
        "food_category": entry.food_category_code,
        # v1.58. Present and null when the visitor named no food, on the terms
        # `alternative` and `net_benefit` already travel on: a caller that
        # reads the key finds it on every entry, and a key that appeared only
        # sometimes would make "named no food" indistinguishable from "this
        # response predates the dimension".
        "food_item": entry.food_item_code,
        # v1.59. This entry's rows rolled up into the one handle a surface
        # branches on, computed in the engine so that the results page, the
        # plain-text export and the PDF read the same value rather than each
        # rolling the rows up in its own language. `not_applicable` on every
        # entry that names no food, which is every entry today.
        "item_basis": entry.item_basis,
        "current": _scenario(entry.current),
        "alternative": _scenario(entry.alternative),
        "net_benefit": _net_benefit(entry.net_benefit),
        # §4.6. This entry's own share of its own production, present
        # whenever the entry supplied a production total and independent of
        # whether its neighbours did.
        "production_share_percent": entry.production_share_percent,
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
            "name": item.name,
            "label": item.label,
            "value": item.value,
            "value_per_unit": item.value_per_unit,
            "value_per_unit_display": item.value_per_unit_display,
            "source_metric": item.source_metric_code,
            "source_note": item.source_note,
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
                # v1.59. Which of §2.2's four candidate rows priced this
                # line, or `null` on a totals-level row, where a sum across
                # entries was priced by no single row -- the same reason
                # `upstream` and `downstream` are zero there. Passed through
                # as the member; `api/serialization.wire` renders an Enum as
                # its `value`, so the token on the wire is the contract's and
                # not a Python repr.
                "upstream_basis": row.upstream_basis,
            }
            for row in metric.by_destination
        ]
    return body


def _net_benefit(net_benefit: Any) -> dict[str, Decimal] | None:
    if net_benefit is None:
        return None
    return dict(net_benefit)
