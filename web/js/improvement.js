import { calculate } from './api.js'
import { setState, entryResultsFrom } from './state.js'
import { escapeHtml, formatNumber } from './view.js'

const number = value => Number(value) || 0
const sorted = items => [...(items || [])].sort((a, b) => Number(a.sort_order || 0) - Number(b.sort_order || 0))
const totalEntryKg = entry => number(entry.totalAmount) * (entry.totalUnit === 'tonnes' ? 1000 : 1)
const lineKg = (entry, line) => number(line.qtyInput) * (entry.totalUnit === 'tonnes' ? 1000 : 1)

export function currentAllocationPercentages(state) {
  const totals = Object.fromEntries((state.taxonomy.destinations || []).map(destination => [destination.code, 0]))
  for (const entry of [...state.entries, currentEntry(state)]) {
    for (const line of entry.current || []) totals[line.destination] = (totals[line.destination] || 0) + lineKg(entry, line)
  }
  const allocated = Object.values(totals).reduce((sum, value) => sum + value, 0)
  if (!allocated) return totals
  return Object.fromEntries(Object.entries(totals).map(([code, value]) => [code, Number((value / allocated * 100).toFixed(2))]))
}

function currentEntry(state) {
  return { sector: state.sector, foodCategory: state.foodCategory, totalAmount: state.totalAmount, totalUnit: state.totalUnit, current: state.current }
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
  return Object.values(allocations || {}).reduce((sum, value) => sum + (Number(value) || 0), 0)
}

export function improvementValidation(state) {
  const values = Object.values(state.improvedAllocations || {})
  if (values.some(value => value === '' || !Number.isFinite(Number(value)) || Number(value) < 0 || Number(value) > 100)) return 'Enter a percentage from 0 to 100 for every destination.'
  const total = allocationTotal(state.improvedAllocations)
  if (Math.abs(total - 100) > 0.01) return `Improved destination allocations must total 100%. Current total: ${total.toFixed(2)}%.`
  return ''
}

function currentLines(entry) {
  return (entry.current || []).filter(line => number(line.qtyInput) > 0).map(line => ({ destination: line.destination, qty_kg: lineKg(entry, line).toFixed(3) }))
}

function improvedLines(entry, allocations) {
  const totalKg = totalEntryKg(entry)
  return Object.entries(allocations).filter(([, percentage]) => number(percentage) > 0).map(([destination, percentage]) => ({ destination, qty_kg: (totalKg * number(percentage) / 100).toFixed(3) }))
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
    const entries = [...state.entries, currentEntry(state)]
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

function aggregateComparison(comparisons) {
  const metrics = {}
  const equivalences = {}
  let currentLandfill = 0
  let improvedLandfill = 0
  for (const { response } of comparisons) {
    for (const [code, currentMetric] of Object.entries(response.current?.metrics || {})) {
      const improvedMetric = response.alternative?.metrics?.[code]
      if (!improvedMetric) continue
      if (!metrics[code]) metrics[code] = { current: 0, improved: 0, unit: currentMetric.unit, precision: currentMetric.display_precision }
      metrics[code].current += number(currentMetric.total)
      metrics[code].improved += number(improvedMetric?.total)
    }
    for (const row of response.current?.metrics?.mass?.by_destination || []) if (row.destination === 'landfill') currentLandfill += number(row.qty_kg)
    for (const row of response.alternative?.metrics?.mass?.by_destination || []) if (row.destination === 'landfill') improvedLandfill += number(row.qty_kg)
    for (const row of response.current?.equivalences || []) {
      if (!equivalences[row.code]) equivalences[row.code] = { label: row.label, current: 0, improved: 0, currentSeen: false, improvedSeen: false }
      equivalences[row.code].current += number(row.value)
      equivalences[row.code].currentSeen = true
    }
    for (const row of response.alternative?.equivalences || []) {
      if (!equivalences[row.code]) equivalences[row.code] = { label: row.label, current: 0, improved: 0, currentSeen: false, improvedSeen: false }
      equivalences[row.code].improved += number(row.value)
      equivalences[row.code].improvedSeen = true
    }
  }
  metrics.landfill_diverted = { current: currentLandfill, improved: improvedLandfill, unit: 'kg', precision: 2, inverseLabel: true }
  return { metrics, equivalences }
}

function differenceData(metric) {
  const difference = metric.current - metric.improved
  const percentage = metric.current === 0 ? null : difference / Math.abs(metric.current) * 100
  return { difference, percentage }
}

function changeCopy(metric) {
  const { difference, percentage } = differenceData(metric)
  if (Math.abs(difference) < 1e-9) return { className: 'neutral', text: 'No change', percentage: '' }
  const positive = difference > 0
  const noun = metric.inverseLabel ? (positive ? 'diverted from landfill' : 'more sent to landfill') : (positive ? 'saved' : 'increase')
  const percentageText = percentage === null ? 'Percentage change unavailable' : `${Math.abs(percentage).toFixed(1)}% ${positive ? 'reduction' : 'higher'}`
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
  const rows = Object.values(equivalences).filter(row => row.currentSeen && row.improvedSeen).map(row => ({ ...row, difference: row.current - row.improved })).filter(row => Math.abs(row.difference) > 1e-9)
  if (!rows.length) return '<p class="empty-state">No comparable tangible equivalents were returned.</p>'
  return `<ul class="comparison-equivalents">${rows.map(row => `<li>${formatNumber(Math.abs(row.difference), 2)} ${escapeHtml(row.label)} ${row.difference > 0 ? 'fewer' : 'more'} <span>Estimate based on the current factor set.</span></li>`).join('')}</ul>`
}

export function ComparisonResults(state) {
  const comparisons = state.improvementResult?.comparisons
  if (!comparisons?.length) return ''
  const data = aggregateComparison(comparisons)
  const mock = comparisons.some(({ response }) => response.factor_set?.is_mock)
  return `<section class="comparison-results" id="comparison-results" aria-labelledby="comparison-results-title"><p class="eyebrow">Current Results → Improved Scenario</p><h2 id="comparison-results-title">Compare Results</h2>${mock ? '<p class="comparison-estimate-note">Demonstration only — this comparison uses mock factors and is not a verified impact result.</p>' : ''}${ComparisonSummary(data, state.taxonomy)}<div class="impact-comparison-grid">${Object.entries(data.metrics).filter(([code]) => code !== 'mass').map(([code, metric]) => ImpactComparisonCard(code, metric, state.taxonomy)).join('')}</div>${ComparisonBars(data.metrics, state.taxonomy)}<section class="comparison-equivalent-section"><h2>Tangible equivalents</h2>${equivalentDifferences(data.equivalences)}</section></section>`
}
