import { calculate } from './api.js'
import { setState, entryResultsFrom } from './state.js'
import { escapeHtml, formatNumber } from './view.js'

// Display-only coercion of an API decimal string (§7.6.1). `Number(value) || 0` stood here
// and made "the engine did not return this figure", "this figure is malformed" and "this
// figure is zero" indistinguishable on screen; an absent figure has to read as absent,
// which is what `formatNumber` does with a non-finite input.
const number = value => (value === null || value === undefined || value === '' ? Number.NaN : Number(value))

// Arithmetic on what the user typed, not on an API figure. A blank destination row is a
// zero here, and that is the only reason a `|| 0` is correct anywhere in this module.
const typed = value => Number(value) || 0

const sorted = items => [...(items || [])].sort((a, b) => Number(a.sort_order || 0) - Number(b.sort_order || 0))
const lineKg = (entry, line) => typed(line.qtyInput) * (entry.totalUnit === 'tonnes' ? 1000 : 1)
const sumQtyKg = lines => lines.reduce((sum, line) => sum + typed(line.qty_kg), 0)

// §6.2 rejects an entry whose two scenarios differ in mass by more than this, and it
// rejects the whole submission rather than the entry.
const MASS_TOLERANCE_KG = 0.01

export function currentAllocationPercentages(state) {
  const totals = Object.fromEntries((state.taxonomy.destinations || []).map(destination => [destination.code, 0]))
  for (const entry of submissionEntries(state)) {
    for (const line of entry.current || []) totals[line.destination] = (totals[line.destination] || 0) + lineKg(entry, line)
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

function currentEntry(state) {
  return { sector: state.sector, foodCategory: state.foodCategory, totalAmount: state.totalAmount, totalUnit: state.totalUnit, current: state.current }
}

// The entries the submission will carry, in the order `submitCalculation` sends them.
function submissionEntries(state) {
  return [...state.entries, currentEntry(state)]
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
  if (values.some(value => value === '' || !Number.isFinite(Number(value)) || Number(value) < 0 || Number(value) > 100)) return 'Enter a percentage from 0 to 100 for every destination.'
  const total = allocationTotal(state.improvedAllocations)
  const mismatch = `Improved destination allocations must total 100%, so the improved scenario describes the same waste as the current one. Current total: ${total.toFixed(2)}%.`
  if (Math.abs(total - 100) > 0.01) return mismatch
  for (const entry of submissionEntries(state)) {
    const currentKg = sumQtyKg(currentLines(entry))
    const improvedKg = sumQtyKg(improvedLines(entry, state.improvedAllocations))
    if (Math.abs(improvedKg - currentKg) > MASS_TOLERANCE_KG) return mismatch
  }
  return ''
}

function currentLines(entry) {
  return (entry.current || []).filter(line => typed(line.qtyInput) > 0).map(line => ({ destination: line.destination, qty_kg: lineKg(entry, line).toFixed(3) }))
}

// The alternative redistributes the mass the entry's *current* scenario describes, not the
// total typed at step 3. Step 4 deliberately lets a user allocate less than that total, so
// anchoring here on the typed total made every under-allocated entry send an alternative
// heavier than its current scenario — which §6.2 rejects, for the whole submission. It also
// matches `currentAllocationPercentages`, which has always taken its percentages of the
// allocated mass, so the seeded sliders now round-trip to the mass they were derived from.
function improvedLines(entry, allocations) {
  const totalKg = sumQtyKg(currentLines(entry))
  return Object.entries(allocations).filter(([, percentage]) => typed(percentage) > 0).map(([destination, percentage]) => ({ destination, qty_kg: (totalKg * typed(percentage) / 100).toFixed(3) }))
}

export async function compareImprovement(state) {
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
    const entries = submissionEntries(state)
    const response = await calculate({
      token: state.token || null,
      gwp_horizon: state.gwpHorizon,
      entries: entries.map(entry => ({
        sector: entry.sector,
        food_category: entry.foodCategory || null,
        current: currentLines(entry),
        alternative: improvedLines(entry, state.improvedAllocations),
      })),
    })
    const token = response.token || state.token
    if (token) sessionStorage.setItem('kaiCalculatorToken', token)
    setState({ improvementLoading: false, improvementResult: { ...response, comparisons: entryResultsFrom(entries, response) }, token })
    requestAnimationFrame(() => document.getElementById('comparison-results')?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
  } catch (error) {
    setState({ improvementLoading: false, improvementError: error.message || 'The improvement comparison could not be completed.' })
  }
}

function DestinationAllocationRow(destination, current, improved) {
  const id = `improved-${destination.code}`
  return `<div class="improvement-allocation-row"><div><label for="${id}">${escapeHtml(destination.name)}</label><span>Current: ${formatNumber(current, 2)}%</span></div><div class="improvement-control"><input id="${id}" type="range" min="0" max="100" step="0.01" value="${escapeHtml(improved)}" data-improvement-code="${escapeHtml(destination.code)}" aria-label="Improved ${escapeHtml(destination.name)} percentage"><div class="percentage-input"><input type="number" min="0" max="100" step="0.01" inputmode="decimal" value="${escapeHtml(improved)}" data-improvement-code="${escapeHtml(destination.code)}" aria-label="Improved ${escapeHtml(destination.name)} percentage value"><span>%</span></div></div></div>`
}

export function ImprovementScenario(state) {
  if (!state.improvementOpen) return `<section class="explore-improvements"><h2>Want to explore potential improvements?</h2><p>Adjust how your food waste is managed to see how the environmental and economic impacts could change.</p><button class="button button-primary" type="button" data-action="explore-improvements">Explore Improvements</button></section>`
  const current = currentAllocationPercentages(state)
  const total = allocationTotal(state.improvedAllocations)
  const error = improvementValidation(state)
  return `<section class="improvement-scenario" aria-labelledby="improvement-title"><h2 id="improvement-title">Create an Improvement Scenario</h2><p>Redistribute the current waste amount across different destinations. The total amount of waste should remain unchanged.</p><div class="improvement-allocation-list">${sorted(state.taxonomy.destinations).map(destination => DestinationAllocationRow(destination, current[destination.code] || 0, state.improvedAllocations[destination.code] ?? 0)).join('')}</div><div class="improvement-total ${error ? 'invalid' : ''}" aria-live="polite"><span>Total allocation</span><strong id="improvement-total-value">${total.toFixed(2)}%</strong></div><p class="field-error" id="improvement-inline-error" role="alert" ${error ? '' : 'hidden'}>${escapeHtml(error)}</p>${state.improvementError ? `<p class="field-error" role="alert">${escapeHtml(state.improvementError)}</p>` : ''}<div class="improvement-actions"><button class="button button-secondary" type="button" data-action="reset-improvement">Reset to Current</button><button class="button button-secondary" type="button" data-action="cancel-improvement">Cancel</button><button class="button button-primary" type="button" data-action="compare-improvement" ${error || state.improvementLoading ? 'disabled' : ''}>${state.improvementLoading ? 'Comparing…' : 'Compare Impact'}</button></div></section>`
}

/**
 * Every figure on the comparison screen, read from §6.2 rather than derived.
 *
 * `totals.current` / `totals.alternative` are the engine's cross-entry roll-up and
 * `totals.net_benefit` is `current − alternative` per metric. The `aggregateComparison`
 * this replaced added the per-entry totals up in the browser and then subtracted them
 * itself, so every "X saved" on screen was a number the engine never produced (§7.6.1).
 *
 * `landfill_diverted` still sums `by_destination` across entries, because it is a metric
 * with no row in the `metric` table and therefore no `net_benefit` entry to read. §6.2 (v1.5)
 * rules that the card is removed rather than relocated; that removal is a separate change.
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

  let currentLandfill = 0
  let improvedLandfill = 0
  for (const { response } of result.comparisons || []) {
    for (const row of response.current?.metrics?.mass?.by_destination || []) if (row.destination === 'landfill') currentLandfill += typed(row.qty_kg)
    for (const row of response.alternative?.metrics?.mass?.by_destination || []) if (row.destination === 'landfill') improvedLandfill += typed(row.qty_kg)
  }
  metrics.landfill_diverted = { current: currentLandfill, improved: improvedLandfill, difference: currentLandfill - improvedLandfill, unit: 'kg', precision: 2, inverseLabel: true }

  const equivalences = {}
  for (const row of totals.current?.equivalences || []) {
    equivalences[row.code] = { ...(equivalences[row.code] || { improved: Number.NaN, improvedSeen: false }), label: row.label, current: number(row.value), currentSeen: true }
  }
  for (const row of totals.alternative?.equivalences || []) {
    equivalences[row.code] = { ...(equivalences[row.code] || { label: row.label, current: Number.NaN, currentSeen: false }), improved: number(row.value), improvedSeen: true }
  }
  return { metrics, equivalences }
}

function differenceData(metric) {
  // The percentage has no field in any version of §6.2 and v1.5 rules it removed rather
  // than relocated; that removal is a separate change. `difference` is `net_benefit`.
  const difference = metric.difference
  const percentage = metric.current === 0 ? null : difference / Math.abs(metric.current) * 100
  return { difference, percentage }
}

function changeCopy(metric) {
  const { difference, percentage } = differenceData(metric)
  if (!Number.isFinite(difference)) return { className: 'neutral', text: 'Not available', percentage: '' }
  if (Math.abs(difference) < 1e-9) return { className: 'neutral', text: 'No change', percentage: '' }
  const positive = difference > 0
  const noun = metric.inverseLabel ? (positive ? 'diverted from landfill' : 'more sent to landfill') : (positive ? 'saved' : 'increase')
  const percentageText = percentage === null || !Number.isFinite(percentage) ? 'Percentage change unavailable' : `${Math.abs(percentage).toFixed(1)}% ${positive ? 'reduction' : 'higher'}`
  return { className: positive ? 'positive' : 'negative', text: `${formatNumber(Math.abs(difference), metric.precision ?? 2)} ${escapeHtml(metric.unit || '')} ${noun}`, percentage: percentageText }
}

function ImpactComparisonCard(code, metric, taxonomy) {
  const definition = (taxonomy.metrics || []).find(item => item.code === code)
  const label = code === 'landfill_diverted' ? 'Food waste diverted from landfill' : definition?.name || code
  const change = changeCopy(metric)
  return `<article class="impact-comparison-card"><h3>${escapeHtml(label)}</h3><div class="comparison-values"><div><span>Current</span><strong>${formatNumber(metric.current, metric.precision ?? 2)} ${escapeHtml(metric.unit || '')}</strong></div><span class="comparison-arrow" aria-hidden="true">→</span><div><span>Improved</span><strong>${formatNumber(metric.improved, metric.precision ?? 2)} ${escapeHtml(metric.unit || '')}</strong></div></div><div class="comparison-change ${change.className}"><strong>${change.text}</strong>${change.percentage ? `<span>${change.percentage}</span>` : ''}</div></article>`
}

function ComparisonSummary(data, taxonomy) {
  const preferred = ['co2e', 'water', 'cost'].filter(code => data.metrics[code])
  return `<section class="comparison-summary"><h2>Potential Improvement</h2><ul>${preferred.map(code => {
    const metric = data.metrics[code]
    const definition = (taxonomy.metrics || []).find(item => item.code === code)
    const change = changeCopy(metric)
    return `<li class="${change.className}"><strong>${escapeHtml(definition?.name || code)}:</strong> ${change.text}${change.percentage ? ` · ${change.percentage}` : ''}</li>`
  }).join('') || '<li>No comparable impact metrics were returned.</li>'}</ul></section>`
}

function ComparisonBars(metrics, taxonomy) {
  const keys = ['co2e', 'water', 'cost'].filter(code => metrics[code])
  return `<section class="comparison-bars" aria-labelledby="comparison-chart-title"><h2 id="comparison-chart-title">Current and Improved comparison</h2>${keys.map(code => {
    const metric = metrics[code]
    const definition = (taxonomy.metrics || []).find(item => item.code === code)
    const max = Math.max(Math.abs(metric.current), Math.abs(metric.improved), 1)
    return `<div class="comparison-bar-group"><h3>${escapeHtml(definition?.name || code)}</h3><div><span>Current</span><div class="comparison-bar-track"><span class="current-bar" style="width:${Math.abs(metric.current) / max * 100}%"></span></div><strong>${formatNumber(metric.current, metric.precision ?? 2)}</strong></div><div><span>Improved</span><div class="comparison-bar-track"><span class="improved-bar" style="width:${Math.abs(metric.improved) / max * 100}%"></span></div><strong>${formatNumber(metric.improved, metric.precision ?? 2)}</strong></div></div>`
  }).join('')}</section>`
}

function equivalentDifferences(equivalences) {
  // §6.2's `net_benefit` is keyed by metric code and carries no entry for an equivalence,
  // so this subtraction is the one figure on the screen with no field to read. It is the
  // same open question as the percentage above, and it needs the same ruling.
  const rows = Object.values(equivalences).filter(row => row.currentSeen && row.improvedSeen).map(row => ({ ...row, difference: row.current - row.improved })).filter(row => Number.isFinite(row.difference) && Math.abs(row.difference) > 1e-9)
  if (!rows.length) return '<p class="empty-state">No comparable tangible equivalents were returned.</p>'
  return `<ul class="comparison-equivalents">${rows.map(row => `<li>${formatNumber(Math.abs(row.difference), 2)} ${escapeHtml(row.label)} ${row.difference > 0 ? 'fewer' : 'more'} <span>Estimate based on the current factor set.</span></li>`).join('')}</ul>`
}

export function ComparisonResults(state) {
  const result = state.improvementResult
  if (!result?.totals?.alternative) return ''
  const data = comparisonData(result)
  const mock = result.factor_set?.is_mock
  return `<section class="comparison-results" id="comparison-results" aria-labelledby="comparison-results-title"><p class="eyebrow">Current Results → Improved Scenario</p><h2 id="comparison-results-title">Compare Results</h2>${mock ? '<p class="comparison-estimate-note">Demonstration only — this comparison uses mock factors and is not a verified impact result.</p>' : ''}${ComparisonSummary(data, state.taxonomy)}<div class="impact-comparison-grid">${Object.entries(data.metrics).filter(([code]) => code !== 'mass').map(([code, metric]) => ImpactComparisonCard(code, metric, state.taxonomy)).join('')}</div>${ComparisonBars(data.metrics, state.taxonomy)}<section class="comparison-equivalent-section"><h2>Tangible equivalents</h2>${equivalentDifferences(data.equivalences)}</section></section>`
}
