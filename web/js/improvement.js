import { calculate } from './api.js'
import { setState, draftEntry } from './state.js'
import { rowKgString } from './units.js'
import { requestLines, submissionPayload } from './submission.js'
import { escapeHtml, formatNumber, slug } from './view.js'
import { t } from './i18n.js'

// Display-only coercion of an API decimal string (§7.6.1). `Number(value) || 0` stood here
// and made "the engine did not return this figure", "this figure is malformed" and "this
// figure is zero" indistinguishable on screen; an absent figure has to read as absent,
// which is what `formatNumber` does with a non-finite input.
const number = value => (value === null || value === undefined || value === '' ? Number.NaN : Number(value))

// Arithmetic on what the user typed, not on an API figure. A blank destination row is a
// zero here, and that is the only reason a `|| 0` is correct anywhere in this module.
const typed = value => Number(value) || 0

const sorted = items => [...(items || [])].sort((a, b) => Number(a.sort_order || 0) - Number(b.sort_order || 0))

// §7.3: `units.js` holds the front end's only arithmetic, and this reads it rather than
// re-deriving it. **With the row's own unit, not the entry's** — `massToKg(qtyInput,
// entry.totalUnit)` stood here, which reinterpreted a tonnes row as kilograms and applied no
// container preset at all, so two rows of equal mass seeded the sliders at 99.88% and 0.12%.
// `|| 0` preserves the `typed()` behaviour it replaces: a blank or unusable box counts as
// nothing rather than poisoning the running total with NaN.
const lineKg = (entry, line, presets) => Number(rowKgString(line.qtyInput, line.unit || entry.totalUnit, presets)) || 0
const sumQtyKg = lines => lines.reduce((sum, line) => sum + typed(line.qty_kg), 0)

// §6.2 rejects an entry whose two scenarios differ in mass by more than this, and it
// rejects the whole submission rather than the entry.
const MASS_TOLERANCE_KG = 0.01

// The one metric code this module names, and it is not the hard-coded list §7.6.5 forbids:
// that rule exists because a view listing `['co2e','water','cost']` *omits* the metric a
// staff member inserted, and every metric the response carries still appears here. `mass` is
// held out because §6.2 requires an entry's two scenarios to describe the same mass, so its
// `net_benefit` is zero by construction — "Mass: No change" on every comparison, in a list
// whose subject is what changed.
const MASS_METRIC = 'mass'

// §7.6.6 in the one place on the page where a sign is a *direction*: `net_benefit` is
// `current − alternative` (§3), so positive means the improved scenario is lower and
// negative means it is higher.
//
// The class names say which way the IMPACT moved, not which way the number leans, and the
// stylesheet's arrows follow the name: a positive net benefit is a saving, so the impact
// fell, so `.change-down`. The classes this used — `.value-positive` / `.value-negative` /
// `.value-zero` — named the sign, and `.value-negative` was doing double duty for a figure
// that is merely negative (see `results.js`), which is how an up arrow ended up beside
// "1,104.0 kg CO2e saved". The two meanings are now two sets of classes.
const signClass = value => {
  if (!Number.isFinite(value)) return ''
  if (Math.abs(value) < 1e-9) return 'change-none'
  return value > 0 ? 'change-down' : 'change-up'
}

// The codes to render, in the response's own key order. That order is already display order:
// §4.1 defines `FactorBundle.metrics` as sorted by `sort_order` and the engine iterates it to
// build `metrics`, so no sort belongs here — and no list of codes does either (§7.6.5).
const comparableCodes = metrics => Object.keys(metrics).filter(code => code !== MASS_METRIC)

export function currentAllocationPercentages(state) {
  const presets = state.taxonomy?.unit_presets || []
  const totals = Object.fromEntries((state.taxonomy.destinations || []).map(destination => [destination.code, 0]))
  for (const entry of submissionEntries(state)) {
    for (const line of entry.current || []) totals[line.destination] = (totals[line.destination] || 0) + lineKg(entry, line, presets)
  }
  const allocated = Object.values(totals).reduce((sum, value) => sum + value, 0)
  if (!allocated) return totals
  const percentages = Object.fromEntries(Object.entries(totals).map(([code, value]) => [code, Number((value / allocated * 100).toFixed(2))]))
  // Rounding each share to the slider's 2 dp loses up to 0.005 points per destination, so
  // the seeded allocation summed to 99.99% on the fixture's own data — an improved scenario
  // 0.15 kg lighter than the current one on a 1,500 kg entry, which §6.2 rejects. The panel
  // must not open on a state the server would refuse. The remainder goes to the largest
  // share, the one place it does not change what the allocation says.
  const largest = Object.entries(percentages).reduce((best, entry) => (best && best[1] >= entry[1] ? best : entry), null)
  if (largest && largest[1] > 0) percentages[largest[0]] = Number((largest[1] + 100 - allocationTotal(percentages)).toFixed(2))
  return percentages
}

// The entries the submission will carry, in the order `submitCalculation` sends them.
//
// **`draftEntry` is `state.js`'s, not a local copy.** The copy that stood here named five
// keys, and round two added four more that it did not name — so this panel re-sent the
// submission with `total_input_kg`, `total_value_nzd` and `wasted_value_nzd` absent, under
// the same token, and §5.3's upsert wrote the absence over the visitor's figures.
function submissionEntries(state) {
  return [...state.entries, draftEntry()]
}

export function openImprovement(state) {
  const allocations = Object.keys(state.improvedAllocations || {}).length ? state.improvedAllocations : currentAllocationPercentages(state)
  setState({ improvementOpen: true, improvedAllocations: allocations, improvementError: null })
}

export function resetImprovement(state) {
  setState({ improvedAllocations: currentAllocationPercentages(state), improvementResult: null, improvementError: null })
}

export function updateImprovementInput(control, state) {
  const code = control.dataset.improvementCode
  state.improvedAllocations = { ...state.improvedAllocations, [code]: control.value }
  state.improvementResult = null
  state.improvementError = null
  document.querySelectorAll(`[data-improvement-code="${CSS.escape(code)}"]`).forEach(input => {
    if (input !== control) input.value = control.value
  })
  const total = allocationTotal(state.improvedAllocations)
  const error = improvementValidation(state)
  const totalPanel = document.querySelector('.improvement-total')
  totalPanel?.classList.toggle('invalid', Boolean(error))
  const totalValue = document.getElementById('improvement-total-value')
  if (totalValue) totalValue.textContent = `${total.toFixed(2)}%`
  const errorElement = document.getElementById('improvement-inline-error')
  if (errorElement) {
    errorElement.textContent = error
    errorElement.hidden = !error
  }
  const compareButton = document.querySelector('[data-action="compare-improvement"]')
  if (compareButton) compareButton.disabled = Boolean(error)
}

export function allocationTotal(allocations) {
  return Object.values(allocations || {}).reduce((sum, value) => sum + typed(value), 0)
}

/**
 * '' when the improvement scenario can be submitted.
 *
 * The mass check is §6.2's own rule, applied in kilograms to the lines that will actually
 * be sent. A percentage tolerance is a different rule at every tonnage: 0.01 percentage
 * points is 0.15 kg on a 1,500 kg entry, fifteen times §6.2's limit, so the panel enabled
 * Compare on a submission the server then refused with a 400 — after the user had left the
 * screen where the numbers are.
 */
export function improvementValidation(state) {
  const values = Object.values(state.improvedAllocations || {})
  if (values.some(value => value === '' || !Number.isFinite(Number(value)) || Number(value) < 0 || Number(value) > 100)) return t('Enter a percentage from 0 to 100 for every destination.')
  const total = allocationTotal(state.improvedAllocations)
  const mismatch = t('Improved destination allocations must total 100%, so the improved scenario describes the same waste as the current one. Current total: %(total)s%.', { total: total.toFixed(2) })
  if (Math.abs(total - 100) > 0.01) return mismatch
  const presets = state.taxonomy?.unit_presets || []
  for (const entry of submissionEntries(state)) {
    const currentKg = sumQtyKg(requestLines(entry, presets))
    const improvedKg = sumQtyKg(improvedLines(entry, state.improvedAllocations, presets))
    if (Math.abs(improvedKg - currentKg) > MASS_TOLERANCE_KG) return mismatch
  }
  return ''
}

// The alternative redistributes the mass the entry's *current* scenario describes, not the
// total typed at step 3. Step 4 deliberately lets a user allocate less than that total, so
// anchoring here on the typed total made every under-allocated entry send an alternative
// heavier than its current scenario — which §6.2 rejects, for the whole submission. It also
// matches `currentAllocationPercentages`, which has always taken its percentages of the
// allocated mass, so the seeded sliders now round-trip to the mass they were derived from.
function improvedLines(entry, allocations, presets) {
  const totalKg = sumQtyKg(requestLines(entry, presets))
  return Object.entries(allocations).filter(([, percentage]) => typed(percentage) > 0).map(([destination, percentage]) => ({ destination, qty_kg: (totalKg * typed(percentage) / 100).toFixed(3) }))
}

/**
 * Runs the comparison request and stores its result, or its message, on the state.
 *
 * `toPublicMessage` is `calculator.js`'s `publicError` — §9's code-to-copy map — passed in
 * rather than imported, because `calculator.js` already imports this module and the import
 * back would be a cycle. This panel set `improvementError: error.message`, so a
 * `FORMULA_ERROR` or a `NO_PUBLISHED_FACTOR_SET` showed raw backend prose here while the
 * main flow showed C's user copy for the same code, and §9.1 rules that a public
 * `FORMULA_ERROR` never echoes the expression or its location.
 *
 * @param {object} state
 * @param {(error: Error & {code?: string}) => string} [toPublicMessage]
 */
export async function compareImprovement(state, toPublicMessage = error => error.message || t('The improvement comparison could not be completed.')) {
  const validationError = improvementValidation(state)
  if (validationError) {
    setState({ improvementError: validationError })
    return
  }
  setState({ improvementLoading: true, improvementError: null, improvementResult: null })
  try {
    // §6.2: one call for the whole submission. The per-entry loop this replaced re-sent
    // `current` and `alternative` under the same token, so §5.3's upsert left the stored
    // row holding the last entry's improvement scenario alone.
    //
    // **And the same builder `calculator.js` uses**, for the same reason at one remove: this
    // call lands on the row that call created, so a field this one omits is a field the
    // visitor loses. It omitted four of them, and every destination row's unit besides.
    const entries = submissionEntries(state)
    const presets = state.taxonomy?.unit_presets || []
    const response = await calculate(
      submissionPayload(state, entries, entry => improvedLines(entry, state.improvedAllocations, presets)),
    )
    const token = response.token || state.token
    if (token) sessionStorage.setItem('kaiCalculatorToken', token)
    // The per-entry pairing `comparisons` held was read by exactly one thing — the
    // `landfill_diverted` card's cross-entry `by_destination` sum — and that card is gone.
    // Every figure this screen renders now comes from `totals`.
    setState({ improvementLoading: false, improvementResult: response, token })
    requestAnimationFrame(() => document.getElementById('comparison-results')?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
  } catch (error) {
    setState({ improvementLoading: false, improvementError: toPublicMessage(error) })
  }
}

function DestinationAllocationRow(destination, current, improved) {
  // The one interpolation on the branch that reached an attribute through neither
  // `escapeHtml` nor `slug`. `destination.code` is `VARCHAR(64)` with no pattern constraint
  // in `db/`, `api/` or `admin/`, and staff edit it through sqladmin's generic CRUD, so a
  // code containing a double quote breaks `for="…"` and `id="…"` on a public page. `slug` is
  // what `calculator.js` already uses for the identical case, and it is the right tool here
  // rather than `escapeHtml`: this value is a DOM id, and an id is not a place to carry
  // punctuation that `getElementById` and `querySelector` then have to escape again.
  // (`data-improvement-code` still carries the real code, escaped, and that is what the
  // keystroke path reads — so two codes that slug alike share a label association but never
  // a value.)
  const id = `improved-${slug(destination.code)}`
  return `<div class="improvement-allocation-row"><div><label for="${id}">${escapeHtml(destination.name)}</label><span>${escapeHtml(t('Current'))}: ${formatNumber(current, 2)}%</span></div><div class="improvement-control"><input id="${id}" type="range" min="0" max="100" step="0.01" value="${escapeHtml(improved)}" data-improvement-code="${escapeHtml(destination.code)}" aria-label="${escapeHtml(t('Improved %(destination)s percentage', { destination: destination.name }))}"><div class="percentage-input"><input type="number" min="0" max="100" step="0.01" inputmode="decimal" value="${escapeHtml(improved)}" data-improvement-code="${escapeHtml(destination.code)}" aria-label="${escapeHtml(t('Improved %(destination)s percentage value', { destination: destination.name }))}"><span>%</span></div></div></div>`
}

export function ImprovementScenario(state) {
  if (!state.improvementOpen) return `<section class="explore-improvements"><h2>${escapeHtml(t('Want to explore potential improvements?'))}</h2><p>${escapeHtml(t('Adjust how your food waste is managed to see how the environmental and economic impacts could change.'))}</p><button class="button button-primary" type="button" data-action="explore-improvements">${escapeHtml(t('Explore Improvements'))}</button></section>`
  const current = currentAllocationPercentages(state)
  const total = allocationTotal(state.improvedAllocations)
  const error = improvementValidation(state)
  return `<section class="improvement-scenario" aria-labelledby="improvement-title"><h2 id="improvement-title">${escapeHtml(t('Create an Improvement Scenario'))}</h2><p>${escapeHtml(t('Redistribute the current waste amount across different destinations. The total amount of waste should remain unchanged.'))}</p><div class="improvement-allocation-list">${sorted(state.taxonomy.destinations).map(destination => DestinationAllocationRow(destination, current[destination.code] || 0, state.improvedAllocations[destination.code] ?? 0)).join('')}</div><div class="improvement-total ${error ? 'invalid' : ''}" aria-live="polite"><span>${escapeHtml(t('Total allocation'))}</span><strong id="improvement-total-value">${total.toFixed(2)}%</strong></div><p class="field-error" id="improvement-inline-error" role="alert" ${error ? '' : 'hidden'}>${escapeHtml(error)}</p>${state.improvementError ? `<p class="field-error" role="alert">${escapeHtml(state.improvementError)}</p>` : ''}<div class="improvement-actions"><button class="button button-secondary" type="button" data-action="reset-improvement">${escapeHtml(t('Reset to Current'))}</button><button class="button button-secondary" type="button" data-action="cancel-improvement">${escapeHtml(t('Cancel'))}</button><button class="button button-primary" type="button" data-action="compare-improvement" ${error || state.improvementLoading ? 'disabled' : ''}>${escapeHtml(state.improvementLoading ? t('Comparing…') : t('Compare Impact'))}</button></div></section>`
}

/**
 * Every figure on the comparison screen, read from §6.2 rather than derived.
 *
 * `totals.current` / `totals.alternative` are the engine's cross-entry roll-up and
 * `totals.net_benefit` is `current − alternative` per metric. The `aggregateComparison`
 * this replaced added the per-entry totals up in the browser and then subtracted them
 * itself, so every "X saved" on screen was a number the engine never produced (§7.6.1).
 *
 * Equivalences carry no difference and `net_benefit` is keyed by metric code only (§3), so
 * both scenarios' `label` strings are kept and neither is subtracted from the other — §8.2's
 * ruling for the same situation on the admin comparison screen: render both, plainly
 * labelled, and say in words that no difference is shown.
 */
function comparisonData(result) {
  const totals = result.totals || {}
  const netBenefit = totals.net_benefit || {}
  const metrics = {}
  for (const [code, currentMetric] of Object.entries(totals.current?.metrics || {})) {
    const improvedMetric = totals.alternative?.metrics?.[code]
    if (!improvedMetric) continue
    metrics[code] = {
      current: number(currentMetric.total),
      improved: number(improvedMetric.total),
      difference: number(netBenefit[code]),
      unit: currentMetric.unit,
      precision: currentMetric.display_precision,
    }
  }
  // §3: `label` is `label_template` with the equivalence's own value already interpolated
  // and formatted by the engine. Each side keeps its own sentence.
  const equivalences = new Map()
  const readSide = (rows, key) => {
    for (const row of rows || []) equivalences.set(row.code, { ...(equivalences.get(row.code) || {}), [key]: row.label })
  }
  readSide(totals.current?.equivalences, 'current')
  readSide(totals.alternative?.equivalences, 'improved')
  return { metrics, equivalences: [...equivalences.values()] }
}

function changeCopy(metric) {
  // `difference` is `net_benefit[code]` verbatim. The percentage that stood beside it was
  // `difference / |current| × 100`, computed here; §6.2 defines no percentage in any version
  // and v1.5 rules it removed rather than relocated, because `net_benefit` already carries
  // the same information in the unit the user entered.
  const difference = metric.difference
  if (!Number.isFinite(difference)) return { className: 'neutral', valueClass: '', text: t('Not available') }
  const valueClass = signClass(difference)
  if (Math.abs(difference) < 1e-9) return { className: 'neutral', valueClass, text: t('No change') }
  const positive = difference > 0
  return { className: positive ? 'positive' : 'negative', valueClass, text: `${formatNumber(Math.abs(difference), metric.precision ?? 2)} ${escapeHtml(metric.unit || '')} ${escapeHtml(positive ? t('saved') : t('increase'))}` }
}

const metricName = (code, taxonomy) => (taxonomy.metrics || []).find(item => item.code === code)?.name || code

// A scenario figure. `formatNumber` already prints the minus sign; the class is what makes a
// negative total read as an offset rather than as a small number (§7.6.6).
const scenarioValue = (value, metric) => `<strong class="${Number.isFinite(value) && value < 0 ? 'value-negative' : ''}">${formatNumber(value, metric.precision ?? 2)} ${escapeHtml(metric.unit || '')}</strong>`

function ImpactComparisonCard(code, metric, taxonomy) {
  const change = changeCopy(metric)
  return `<article class="impact-comparison-card"><h3>${escapeHtml(metricName(code, taxonomy))}</h3><div class="comparison-values"><div><span>${escapeHtml(t('Current'))}</span>${scenarioValue(metric.current, metric)}</div><span class="comparison-arrow" aria-hidden="true">→</span><div><span>${escapeHtml(t('Improved'))}</span>${scenarioValue(metric.improved, metric)}</div></div><div class="comparison-change ${change.className}"><strong class="${change.valueClass}">${change.text}</strong></div></article>`
}

function ComparisonSummary(data, taxonomy) {
  const codes = comparableCodes(data.metrics)
  return `<section class="comparison-summary"><h2>${escapeHtml(t('Potential Improvement'))}</h2><ul>${codes.map(code => {
    const change = changeCopy(data.metrics[code])
    return `<li class="${change.className}"><strong>${escapeHtml(metricName(code, taxonomy))}:</strong> <span class="${change.valueClass}">${change.text}</span></li>`
  }).join('') || `<li>${escapeHtml(t('No comparable impact metrics were returned.'))}</li>`}</ul></section>`
}

/**
 * The comparison chart, which must render a negative value as negative (§7.6.6).
 *
 * `Math.abs()` stood on both widths, so a −500 kg CO2e offset drew a bar identical to +500 —
 * and `downstream` may be negative (§2.2; `animal_feed` is −0.15 in `tests/fixtures/factors.json`),
 * so a total legitimately can be. A group containing a negative value therefore draws against
 * a **centred zero line**: each bar takes at most half the track and grows right from the
 * centre when positive, left from the centre when negative. Groups with no negative value
 * keep the full-width left-anchored bar, so the common case is unchanged.
 *
 * This is still charting, not arithmetic on an API figure (§7.6.1) — a width relative to the
 * largest bar in its own group, and no width is printed on the page.
 */
function ComparisonBars(metrics, taxonomy) {
  const codes = comparableCodes(metrics)
  const anyDiverging = codes.some(code => [metrics[code].current, metrics[code].improved].some(value => Number.isFinite(value) && value < 0))
  const groups = codes.map(code => {
    const metric = metrics[code]
    const values = [metric.current, metric.improved].filter(Number.isFinite)
    const max = Math.max(...values.map(value => Math.abs(value)), 1)
    const diverging = values.some(value => value < 0)
    const bar = (value, className) => {
      if (!Number.isFinite(value)) return ''
      const width = Math.abs(value) / max * (diverging ? 50 : 100)
      const offset = diverging ? (value < 0 ? 50 - width : 50) : 0
      // `margin-inline-start`, not `margin-left`: Arabic and Urdu render this page with
      // `<html dir="rtl">`, and a physical left offset would place the negative half of a
      // diverging bar on the same side as the positive half - two opposite quantities
      // drawn on top of each other. The logical property mirrors with the document.
      return `<span class="${className}${value < 0 ? ' negative-bar' : ''}" style="width:${width}%;margin-inline-start:${offset}%"></span>`
    }
    const row = (label, value, className) => `<div><span>${label}</span><div class="comparison-bar-track${diverging ? ' diverging' : ''}">${bar(value, className)}</div>${scenarioValue(value, metric)}</div>`
    return `<div class="comparison-bar-group"><h3>${escapeHtml(metricName(code, taxonomy))}</h3>${row(escapeHtml(t('Current')), metric.current, 'current-bar')}${row(escapeHtml(t('Improved')), metric.improved, 'improved-bar')}</div>`
  }).join('')
  const note = anyDiverging ? `<p class="comparison-bar-note">${escapeHtml(t('A metric total can be negative when a destination offsets more than it costs. Those bars are drawn from a centre line marking zero and run to the left.'))}</p>` : ''
  return `<section class="comparison-bars" aria-labelledby="comparison-chart-title"><h2 id="comparison-chart-title">${escapeHtml(t('Current and Improved comparison'))}</h2>${note}${groups}</section>`
}

/**
 * Both scenarios' equivalents, side by side, never differenced.
 *
 * `net_benefit` is keyed by metric code (§3 declares it `dict[str, Decimal]`) and
 * `EquivalenceResult` carries no difference, so a "18,597 km fewer" figure could only be
 * `current − improved` computed here — the same defect as the metric sums this module no
 * longer performs, with nothing to replace it. §8.2 rules the same situation on the admin
 * comparison screen: render both values, plainly labelled, and state that no difference is
 * shown. (The copy this replaced also read "5,016 Equivalent to driving 18,597 km fewer",
 * a subtracted number glued to the front of the engine's own sentence.)
 */
function equivalentComparison(rows) {
  if (!rows.length) return `<p class="empty-state">${escapeHtml(t('No comparable tangible equivalents were returned.'))}</p>`
  const side = (label, value) => `<div><span>${escapeHtml(label)}</span><strong>${value ? escapeHtml(value) : escapeHtml(t('Not available'))}</strong></div>`
  return `<p class="comparison-equivalent-note">${escapeHtml(t('Each scenario is shown as the calculation service worded it. The difference between the two is not shown, because the service does not return one — compare the two figures.'))}</p><ul class="comparison-equivalents">${rows.map(row => `<li><div class="comparison-values">${side(t('Current'), row.current)}<span class="comparison-arrow" aria-hidden="true">→</span>${side(t('Improved'), row.improved)}</div></li>`).join('')}</ul>`
}

export function ComparisonResults(state) {
  const result = state.improvementResult
  if (!result?.totals?.alternative) return ''
  const data = comparisonData(result)
  const mock = result.factor_set?.is_mock
  return `<section class="comparison-results" id="comparison-results" aria-labelledby="comparison-results-title"><p class="eyebrow">${escapeHtml(t('Current Results → Improved Scenario'))}</p><h2 id="comparison-results-title">${escapeHtml(t('Compare Results'))}</h2>${mock ? `<p class="comparison-estimate-note">${escapeHtml(t('Demonstration only — this comparison uses mock factors and is not a verified impact result.'))}</p>` : ''}${ComparisonSummary(data, state.taxonomy)}<div class="impact-comparison-grid">${comparableCodes(data.metrics).map(code => ImpactComparisonCard(code, data.metrics[code], state.taxonomy)).join('')}</div>${ComparisonBars(data.metrics, state.taxonomy)}<section class="comparison-equivalent-section"><h2>${escapeHtml(t('Tangible equivalents'))}</h2>${equivalentComparison(data.equivalences)}</section></section>`
}
