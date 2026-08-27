"""Contract §4.2 -- the calculation. **Pure**: no database, no file access,
no system clock, no environment. `calculate(request, bundle)` is the only
entry point outside `engine/` (§4.2 names this module for that reason).

Four properties of this file are load-bearing, and each of them replaced a
walking-skeleton shortcut whose failure mode was silent:

**No metric code appears here.** The loop iterates `bundle.metrics` and
evaluates each metric's stored formula. Adding a metric costs one INSERT and
one expression, never a call site -- and `tests/test_calculator.py` proves it
by adding a metric *row* rather than by checking that today's metrics render.

**A formula computes one line; the engine performs the summation** (§4.3):

    line_value   = f(qty_kg, upstream, downstream, const_*)
    metric_total = Σ line_value

This is why the expression language needs no arrays, no loops and no `sum()`,
which is what keeps the evaluator's security boundary unambiguous.

**The upstream lookup takes the line's destination** (§4.1, contract v1.8).
`prevention` carries an upstream row of its own at zero, so a prevented line
draws no upstream burden and the offset is whole. The lookup therefore has to
happen *inside* the per-line loop -- it is the same edit as the summation
above, which is why open item O-7 and the `lines[0]` defect were fixed
together.

**Equivalences are data too.** They are read from `bundle.equivalences()` in
exactly the same way, and no equivalence code appears here either. §4.2
requires the rolled-up equivalence to be derived from the *rolled-up metric
total* rather than summed from the per-entry ones -- the conversion is linear
so the two agree mathematically, but `Decimal` has finite precision and one
computation is one rounding.

**Every lookup on `FactorBundle` falls back to `Decimal('0')`, so this module
checks the codes itself.** Zero is the right answer for a missing *factor*
(§4.1) and a catastrophic one for a missing *code*: an unknown sector would
otherwise produce a calculation of zero that reads as a real result. The
`has_*` predicates exist for exactly this, and §4.4 gives the failure a name.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from engine.bundle import FactorBundle
from engine.errors import UnknownCodeError
from engine.evaluator import evaluate
from engine.types import (
    BreakdownRow,
    CalculationRequest,
    CalculationResult,
    CalculationTotals,
    EntryInput,
    EntryResult,
    EquivalenceResult,
    MetricResult,
    MoneyResult,
    ScenarioLine,
    ScenarioResult,
)

#: §1.2 and §6.2: a metric value travels as a decimal string at ten places,
#: which `tests/api/test_fixture_consistency.py` asserts of every fixture.
#: `Decimal` arithmetic does not produce that scale on its own -- `800.000 *
#: 0.4500000000` carries thirteen places -- so the engine, which is the only
#: place a metric value is produced, is where the scale is applied.
METRIC_SCALE = Decimal("0.0000000001")

#: The totals-level roll-up (below) leaves upstream and downstream at
#: zero because they are per-kilogram rates, not sums -- but zero is
#: still a metric value on the wire (§1.2), so it carries the same ten
#: places as every other rate rather than rendering as the bare "0" a
#: scale-zero Decimal would produce.
ZERO_RATE = Decimal("0").quantize(METRIC_SCALE)

#: §4.5, v1.48. NZD figures carry two places on the wire (`api/schemas.py`'s
#: `total_value_nzd`/`wasted_value_nzd` fields, `DECIMAL(14, 2)` in
#: `db/models.py`), unlike the metric figures above, which carry ten. This is
#: the money block's own scale, applied here rather than at the wire edge.
MONEY_SCALE = Decimal("0.01")

#: §4.3's special binding. A formula names `const_GWP_CH4` and never a
#: horizon, so switching the request between 20 and 100 years rebinds one
#: variable and touches no stored expression.
GWP_CH4 = "GWP_CH4"

LEGAL_HORIZONS = (20, 100)

#: §2.2 and §3 rule 5: the *only* placeholder `label_template` carries.
#: `label_template` is staff-authored (§8.1), so it is never passed to
#: `str.format` -- a template containing any other brace sequence would either
#: raise or interpolate something a staff member did not intend. A plain
#: `str.replace` copies every other character verbatim, which is the rule.
VALUE_PLACEHOLDER = "{value}"


def calculate(request: CalculationRequest, bundle: FactorBundle) -> CalculationResult:
    """§4.2. Evaluate every entry's scenarios, roll them up, and compute net
    benefit at both levels. `entries` comes back in request order (§3 rule 1).

    Raises `UnknownCodeError`, `UnknownConstantError`, `FormulaError`, or
    `ValueError` when `gwp_horizon` is neither 20 nor 100.
    """
    if request.gwp_horizon not in LEGAL_HORIZONS:
        raise ValueError(
            f"gwp_horizon must be 20 or 100, got {request.gwp_horizon!r}"
        )

    entries: list[EntryResult] = []
    for entry in request.entries:
        current = calculate_scenario(
            entry.current,
            entry.sector_code,
            entry.food_category_code,
            bundle,
            request.gwp_horizon,
        )
        alternative = None
        benefit = None
        if entry.alternative is not None:
            alternative = calculate_scenario(
                entry.alternative,
                entry.sector_code,
                entry.food_category_code,
                bundle,
                request.gwp_horizon,
            )
            benefit = net_benefit(current, alternative)
        entries.append(
            EntryResult(
                sector_code=entry.sector_code,
                # Echoed as sent, **not** resolved. §6.2: "null is treated as
                # standard_mix" is true of the factor lookup and of nothing
                # else -- §5.4 keeps `unspecified` and `standard_mix` distinct
                # on purpose, and resolving here would erase the difference.
                food_category_code=entry.food_category_code,
                current=current,
                alternative=alternative,
                net_benefit=benefit,
            )
        )

    return CalculationResult(
        factor_set_version=bundle.version_label,
        is_mock=bundle.is_mock,
        gwp_horizon=request.gwp_horizon,
        totals=_totals(request.entries, tuple(entries), bundle),
        entries=tuple(entries),
    )


def calculate_scenario(
    lines: tuple[ScenarioLine, ...],
    sector_code: str,
    food_category_code: str | None,
    bundle: FactorBundle,
    gwp_horizon: int,
) -> ScenarioResult:
    """Evaluate one scenario of one entry. Internal to the engine (§4.2): no
    caller outside `engine/` may depend on this signature.

    Sector and food category are passed alongside the lines because they live
    on the entry, not on the scenario -- an entry's `current` and
    `alternative` describe the same point in the supply chain (§3).
    """
    food_category = _resolve_food_category(food_category_code, bundle)
    if not bundle.has_sector(sector_code):
        raise UnknownCodeError(f"unknown sector: {sector_code!r}")
    for scenario_line in lines:
        if not bundle.has_destination(scenario_line.destination_code):
            raise UnknownCodeError(
                f"unknown destination: {scenario_line.destination_code!r}"
            )

    constants = _constant_bindings(bundle, gwp_horizon)

    metrics: dict[str, MetricResult] = {}
    for spec in bundle.metrics:
        formula = bundle.formula(spec.code)
        rows: list[BreakdownRow] = []
        total = Decimal("0")
        for scenario_line in lines:
            upstream = bundle.upstream(
                sector_code,
                food_category,
                # v1.8's fourth dimension. Outside a per-line loop this
                # argument cannot exist, which is why the two fixes are one.
                scenario_line.destination_code,
                spec.code,
            )
            downstream = bundle.downstream(
                scenario_line.destination_code,
                # v1.31's dimension. The sector reaches the downstream lookup
                # as the caller gave it, unresolved: unlike `food_category`
                # above there is no standard-mix stand-in for a sector, and
                # §6.2 requires one on every entry, so what arrives here is
                # always what the user chose.
                sector_code,
                food_category,
                spec.code,
            )
            value = evaluate(
                formula,
                {
                    "qty_kg": scenario_line.qty_kg,
                    "upstream": upstream,
                    "downstream": downstream,
                    **constants,
                },
            )
            # Summed **unrounded**, and quantised once below. Rounding each
            # line and adding those is a different number from adding and
            # rounding once; the breakdown row carries the rounded figure
            # because it is display, and it is never added to anything.
            total += value
            rows.append(
                BreakdownRow(
                    destination_code=scenario_line.destination_code,
                    qty_kg=scenario_line.qty_kg,
                    # Quantised for the same reason as `value`: §6.2 carries
                    # both at ten places. A factor read from DECIMAL(20,10)
                    # already has them, but `FactorBundle`'s missing-factor
                    # fallback is `Decimal('0')` at scale zero, so a line
                    # whose metric has no row would otherwise put a bare
                    # `"0"` on the wire beside its neighbour's
                    # `"1.9000000000"`.
                    upstream=upstream.quantize(METRIC_SCALE),
                    downstream=downstream.quantize(METRIC_SCALE),
                    value=value.quantize(METRIC_SCALE),
                )
            )
        metrics[spec.code] = MetricResult(
            metric_code=spec.code,
            unit=spec.unit,
            display_precision=spec.display_precision,
            total=total.quantize(METRIC_SCALE),
            by_destination=tuple(rows),
        )

    return ScenarioResult(
        total_kg=sum((row.qty_kg for row in lines), Decimal("0")),
        metrics=metrics,
        equivalences=_equivalences(metrics, bundle),
    )


def net_benefit(
    current: ScenarioResult, alternative: ScenarioResult
) -> dict[str, Decimal]:
    """§4.2. Per metric, `current.total - alternative.total`. Only metric
    codes present on both sides are included. Applied at both levels."""
    return {
        code: metric.total - alternative.metrics[code].total
        for code, metric in current.metrics.items()
        if code in alternative.metrics
    }


# ---------- Internals ----------


def _resolve_food_category(code: str | None, bundle: FactorBundle) -> str:
    """§3, §6.2: `None` means the standard mix, and it is the engine that
    resolves it. The resolved code is then checked like any other, so a
    bundle with no `is_standard_mix` row fails loudly rather than looking up
    the empty string and drawing zero from every factor table."""
    resolved = bundle.standard_mix_code() if code is None else code
    if not bundle.has_food_category(resolved):
        if code is None:
            raise UnknownCodeError(
                "food_category was null and this factor set has no standard mix"
            )
        raise UnknownCodeError(f"unknown food_category: {code!r}")
    return resolved


def _constant_bindings(bundle: FactorBundle, gwp_horizon: int) -> dict[str, Decimal]:
    """§4.3's `const_<CODE>` variables, plus the `const_GWP_CH4` binding.

    Every constant in the bundle is bound, not only the ones a shipped
    formula happens to reference: the variable set is a property of the
    factor set, and a staff member editing an expression in the panel against
    `admin/expressions.py`'s whitelist must find the same names available
    here.

    `const_GWP_CH4` is bound only when the horizon's own constant exists. A
    formula naming it against a bundle that carries neither `GWP_CH4_20` nor
    `GWP_CH4_100` raises `FormulaError` from the evaluator rather than
    `UnknownConstantError`; §4.4 maps both to `FORMULA_ERROR` (500), so the
    two are indistinguishable at the API boundary.
    """
    bindings = {f"const_{code}": value for code, value in bundle.constants.items()}
    horizon_code = f"{GWP_CH4}_{gwp_horizon}"
    if horizon_code in bundle.constants:
        bindings[f"const_{GWP_CH4}"] = bundle.constant(horizon_code)
    return bindings


def _totals(
    request_entries: tuple[EntryInput, ...],
    entries: tuple[EntryResult, ...],
    bundle: FactorBundle,
) -> CalculationTotals:
    """§4.2's roll-up table, computed here and never in `api/`.

    An entry with no alternative contributes its **current** figures to the
    rolled-up alternative (§3 rule 3), so its contribution to net benefit is
    exactly zero and the two sides stay mass-conserving. Excluding it would
    make the alternative lighter than the current scenario and inflate the
    headline benefit -- the precise failure the dual-scenario design exists
    to prevent.

    `request_entries` -- the original `EntryInput`s -- is threaded through
    only for `_money()` (§4.5): it is the one figure here derived from what a
    visitor typed rather than from a metric, so it reads the request's own
    money fields and raw scenario lines instead of the computed `EntryResult`s
    (which carry `MetricResult` breakdowns, not the entry-level NZD figures).
    """
    has_alternative = any(entry.alternative is not None for entry in entries)

    current = _roll_up(tuple(entry.current for entry in entries), bundle)
    alternative = (
        _roll_up(
            tuple(
                entry.alternative if entry.alternative is not None else entry.current
                for entry in entries
            ),
            bundle,
        )
        if has_alternative
        else None
    )
    return CalculationTotals(
        current=current,
        alternative=alternative,
        # Computed on the rolled-up scenarios, not summed from the per-entry
        # net_benefit maps -- one computation is one rounding.
        net_benefit=net_benefit(current, alternative) if alternative else None,
        money=_money(request_entries, bundle),
    )


def _money(entries: tuple[EntryInput, ...], bundle: FactorBundle) -> MoneyResult | None:
    """§4.5, v1.48. Derived from what the visitor typed, never from a factor
    or a formula -- see `MoneyResult`'s own docstring for why this is not a
    metric.

    **Absent stays absent.** Every field is `None` unless every value it is
    derived from was supplied; a computed zero would read as "this food was
    worth nothing" rather than "nobody said" (§4.5).
    """
    total_value_nzd = _sum_present(entry.total_value_nzd for entry in entries)
    wasted_value_nzd = _sum_present(entry.wasted_value_nzd for entry in entries)

    if total_value_nzd is None and wasted_value_nzd is None:
        return None

    wasted_share_percent = None
    if (
        total_value_nzd is not None
        and wasted_value_nzd is not None
        and total_value_nzd != 0
    ):
        wasted_share_percent = (wasted_value_nzd / total_value_nzd * 100).quantize(
            MONEY_SCALE, rounding=ROUND_HALF_UP
        )

    saving_nzd = None
    has_alternative = any(entry.alternative is not None for entry in entries)
    current_total_kg = sum(
        (line.qty_kg for entry in entries for line in entry.current), Decimal("0")
    )
    if wasted_value_nzd is not None and has_alternative and current_total_kg != 0:
        value_per_kg = wasted_value_nzd / current_total_kg
        # §3 rule 3's substitution, on the same terms as `_totals()` above: an
        # entry with no alternative contributes its *current* lines, so it
        # cannot manufacture a saving out of a scenario nobody supplied.
        alternative_non_prevention_kg = sum(
            (
                line.qty_kg
                for entry in entries
                for line in (
                    entry.alternative if entry.alternative is not None else entry.current
                )
                # The prevention predicate, not a literal `"prevention"`
                # string (§4.5) -- the ReFED vocabulary's own prevention row
                # is spelled `refed_prevention`.
                if not bundle.is_prevention_destination(line.destination_code)
            ),
            Decimal("0"),
        )
        diverted_kg = current_total_kg - alternative_non_prevention_kg
        saving_nzd = (value_per_kg * diverted_kg).quantize(
            MONEY_SCALE, rounding=ROUND_HALF_UP
        )

    return MoneyResult(
        total_value_nzd=total_value_nzd,
        wasted_value_nzd=wasted_value_nzd,
        wasted_share_percent=wasted_share_percent,
        saving_nzd=saving_nzd,
    )


def _sum_present(values) -> Decimal | None:
    """`None` when every value is `None`; otherwise the sum of the ones that
    are not. The "nobody said" case and the "the answer is zero" case are
    different claims, and only a value actually seen can tell them apart."""
    present = [value for value in values if value is not None]
    if not present:
        return None
    return sum(present, Decimal("0"))


def _roll_up(
    scenarios: tuple[ScenarioResult, ...], bundle: FactorBundle
) -> ScenarioResult:
    metrics: dict[str, MetricResult] = {}
    #: v1.48, amending §3 rule 2 for the half of it that was wrong. Keyed
    #: first by metric code and then by destination: `qty_kg` and `value`
    #: are additive across entries -- a mass is a mass, and `value` is a
    #: summand of the metric total this loop already computes by summing
    #: (§4.3), so the cross-entry partition cannot disagree with the total
    #: it partitions. It never crosses a metric boundary, so `value` stays
    #: in that metric's own unit -- kg CO2e is not additive with NZD -- and
    #: `qty_kg` comes out identical in every metric's rows, because mass does
    #: not depend on which metric is being computed. `upstream` and
    #: `downstream` are left at zero: they are per-kilogram RATES drawn from
    #: factors that can differ between the entries sharing a destination,
    #: and a mean of two different rates is a number derived from nothing --
    #: that half of the old rule stands.
    destinations: dict[str, dict[str, list[Decimal]]] = {}
    for scenario in scenarios:
        for code, metric in scenario.metrics.items():
            running = metrics[code].total if code in metrics else Decimal("0")
            bucket_for_metric = destinations.setdefault(code, {})
            for row in metric.by_destination:
                bucket = bucket_for_metric.setdefault(
                    row.destination_code, [Decimal("0"), Decimal("0")]
                )
                bucket[0] += row.qty_kg
                bucket[1] += row.value
            metrics[code] = MetricResult(
                metric_code=code,
                unit=metric.unit,
                display_precision=metric.display_precision,
                total=running + metric.total,
                # Dict insertion order is first-appearance order across
                # entries, so two runs of the same request produce the same
                # JSON.
                by_destination=tuple(
                    BreakdownRow(
                        destination_code=destination_code,
                        qty_kg=qty,
                        upstream=ZERO_RATE,
                        downstream=ZERO_RATE,
                        value=value,
                    )
                    for destination_code, (qty, value) in bucket_for_metric.items()
                ),
            )
    return ScenarioResult(
        total_kg=sum((scenario.total_kg for scenario in scenarios), Decimal("0")),
        metrics=metrics,
        # §4.2: derived from the metric totals **this function just rolled
        # up**, not from `scenario.equivalences`. Adding the per-entry values
        # would be a second place a headline number is produced, and the two
        # disagree in the last place whenever a per-entry product rounds.
        equivalences=_equivalences(metrics, bundle),
    )


def _equivalences(
    metrics: dict[str, MetricResult], bundle: FactorBundle
) -> tuple[EquivalenceResult, ...]:
    """§2.2, §3 rule 5, §4.2. One `EquivalenceResult` per active equivalence
    in the bundle, in `sort_order` (which `FactorBundle.equivalences()`
    already applies), converted from the metric total it names.

    `value = source metric total x value_per_unit`, quantised to the
    contract's ten places for the same reason every other decimal on the wire
    is: the raw product carries twenty.

    An equivalence naming a metric absent from `metrics` is **skipped**
    rather than raising. `validate()` (§4.1) reports a dangling
    `source_metric` as a bundle problem, which is where that belongs; a
    calculation over a bundle carrying an equivalence for a metric it does
    not compute drops the equivalence, exactly as `net_benefit` includes only
    metric codes present on both sides.
    """
    results: list[EquivalenceResult] = []
    for spec in bundle.equivalences():
        source = metrics.get(spec.source_metric_code)
        if source is None:
            continue
        value = (source.total * spec.value_per_unit).quantize(METRIC_SCALE)
        results.append(
            EquivalenceResult(
                code=spec.code,
                label=_interpolate(spec.label_template, value),
                value=value,
                source_metric_code=spec.source_metric_code,
            )
        )
    return tuple(results)


def _interpolate(template: str, value: Decimal) -> str:
    """§3 rule 5. `{value}` is substituted; everything else in the template is
    copied verbatim.

    **This is the engine's job and not the browser's** (§7.6 rule 1): rounding
    is arithmetic, and a client that formatted the label itself would be a
    second place a number is turned into the figure a user reads -- one the
    golden suite could not cover.
    """
    return template.replace(VALUE_PLACEHOLDER, _whole_units(value))


def _whole_units(value: Decimal) -> str:
    """§3 rule 5's number format: no decimal places, `ROUND_HALF_UP`, a comma
    every three digits, a leading `-` when negative.

    Three details are deliberate.

    **`ROUND_HALF_UP` is passed explicitly.** `Decimal`'s default context
    rounds half to *even*, so `2.5` would become `2` and `4.5` would become
    `4`. No fixture value lands on a half, so nothing in the fixture set can
    tell the two modes apart -- which is precisely why the mode is written
    out here and pinned by a test of its own.

    **`to_integral_value` rather than `quantize(Decimal("1"))`.** The former
    is exact at any magnitude; the latter raises `InvalidOperation` once the
    integral part exceeds the context precision.

    **A value that rounds to zero from below prints `0`, never `-0`.**
    `Decimal("-0.4")` rounds to `Decimal("-0")`, and "-0 km" reads as a bug on
    a results page. §3 gives negatives a leading `-` because a metric total
    can genuinely be negative when a downstream offset dominates; a magnitude
    that rounds away is not that case.
    """
    whole = value.to_integral_value(rounding=ROUND_HALF_UP)
    if whole.is_zero():
        whole = abs(whole)
    return format(whole, ",")
