import { escapeHtml, formatNumber } from './view.js'
import { ComparisonResults, ImprovementScenario } from './improvement.js'

const DEMONSTRATION_NOTICE = 'Demonstration only — verified calculation factors have not yet been supplied.'
const TAB_LABELS = { stage: 'By supply-chain stage', destination: 'By waste destination', food: 'By food type' }

// Display-only coercion of an API decimal string (§7.6.1): §1.2 puts decimals on the wire
// as strings and `toLocaleString` needs a number. `Number(value) || 0` stood here and made
// "the engine did not return this metric", "this value is malformed" and "this value is
// zero" the same figure on screen. A figure that is absent has to read as absent, which is
// what `formatNumber` does with a non-finite input.
const number = value => (value === null || value === undefined || value === '' ? Number.NaN : Number(value))

// Arithmetic on what the user typed, not on an API figure. A blank destination row is a
// zero here, and that is the only reason a `|| 0` is correct anywhere in this module.
const typed = value => Number(value) || 0

const findByCode = (items, code) => (items || []).find(item => item.code === code)

// §6.2 returns each metric total in `unit`, and that is the unit the number is in. §6.1
// rules `display_unit` a presentation variant of `unit` *at the same scale* — never a
// different scale, because §7.6.1 leaves no layer that could convert between the two.
// Prefer the response's own `unit` regardless: it travels with the figure.
const metricUnit = (metric, definition) => metric?.unit || definition?.unit || ''

function summaryCards(totals, taxonomy) {
  const metrics = totals.current?.metrics || {}
  const impactCards = Object.entries(metrics).filter(([code]) => code !== 'mass').map(([code, metric]) => {
    const definition = findByCode(taxonomy.metrics, code)
    const precision = Number(metric.display_precision ?? definition?.display_precision ?? 2)
    return `<article class="result-card"><p class="result-label">${escapeHtml(definition?.name || code)}</p><p class="result-value">${formatNumber(number(metric.total), precision)} ${escapeHtml(metricUnit(metric, definition))}</p></article>`
  }).join('')
  const totalKg = number(totals.total_kg)
  return `<article class="result-card primary-result"><p class="result-label">Total food waste</p><p class="result-value">${formatNumber(totalKg, 2)} kg</p><p class="result-note">${formatNumber(totalKg / 1000, 3)} tonnes</p></article>${impactCards}<article class="result-card"><p class="result-label">Percentage waste</p><p class="result-value">Not available</p><p class="result-note">Total food handled data is required.</p></article>`
}

// §3: `label` is `label_template` with the equivalence's own value already interpolated and
// formatted by the engine, and no consumer re-derives it. The map this replaced summed
// `value` across entries and then rendered three hard-coded English labels of its own for
// three hard-coded codes, so a new equivalence never appeared and the client's approved
// wording was overridden (§7.6.5).
function equivalences(totals) {
  const rows = totals.current?.equivalences || []
  if (!rows.length) return '<p class="empty-state">Tangible equivalents are available once approved conversion factors are supplied.</p>'
  return `<div class="equivalent-grid">${rows.map(row => `<article><h3>${escapeHtml(row.label)}</h3></article>`).join('')}</div>`
}

// §6.2: the breakdown is rendered from `entries[]`, and nothing on this screen is added
// together. The accumulator this replaced merged rows whose labels collided — two entries
// in one sector, or the same destination under two entries — by summing engine-computed
// metric totals in the browser (§7.6.1). It is also the wrong presentation: 1,200 kg to
// landfill from processing and 1,200 kg from primary production draw different upstream
// factors and are genuinely two rows, which is the whole reason a multi-entry calculation
// was worth making. A single combined view, if the client asks for one, arrives as a field
// the engine fills with a stated aggregation rule — not as a loop here.
const metricCells = scenario => Object.fromEntries(Object.entries(scenario?.metrics || {})
  .map(([code, metric]) => [code, { total: metric.total, unit: metric.unit, display_precision: metric.display_precision }]))

const sectorName = (entry, response, taxonomy) => {
  const code = response.sector ?? entry.sector
  return findByCode(taxonomy.sectors, code)?.name || code
}

// §6.2 labels each per-entry destination section with that entry's sector and food category.
function entryLabel(entry, response, taxonomy) {
  const foodCode = response.food_category ?? entry.foodCategory
  const name = sectorName(entry, response, taxonomy)
  if (!foodCode) return name
  return `${name} · ${findByCode(taxonomy.food_categories, foodCode)?.name || foodCode}`
}

// One row per destination of one entry, every figure read from that entry's own result:
// `qty_kg` and each metric's `value` come from `by_destination`, which §6.2 populates per
// entry and leaves empty at the totals level.
function destinationRows(scenario, taxonomy) {
  const rows = new Map()
  for (const [code, metric] of Object.entries(scenario.metrics || {})) {
    for (const line of metric.by_destination || []) {
      const row = rows.get(line.destination) || {
        label: findByCode(taxonomy.destinations, line.destination)?.name || line.destination,
        kilograms: number(line.qty_kg),
        metrics: {},
      }
      row.metrics[code] = { total: line.value, unit: metric.unit, display_precision: metric.display_precision }
      rows.set(line.destination, row)
    }
  }
  return [...rows.values()]
}

function breakdowns(entryResults, taxonomy) {
  const stage = []
  const food = []
  const destination = []
  for (const { entry, response } of entryResults) {
    const scenario = response.current || {}
    const metrics = metricCells(scenario)
    const kilograms = number(scenario.total_kg)
    stage.push({ label: sectorName(entry, response, taxonomy), kilograms, metrics })
    const foodCode = response.food_category ?? entry.foodCategory
    const foodDefinition = findByCode(taxonomy.food_categories, foodCode)
    if (foodCode && !foodDefinition?.is_standard_mix) food.push({ label: foodDefinition?.name || foodCode, kilograms, metrics })
    const rows = destinationRows(scenario, taxonomy)
    if (rows.length) destination.push({ label: entryLabel(entry, response, taxonomy), rows })
  }
  return {
    stage: { sections: [{ rows: stage }] },
    destination: destination.length ? { sections: destination, note: 'Each supply-chain entry is shown on its own. The same destination under two entries draws two different upstream factors, so it is genuinely two rows.' } : { unavailable: 'Waste-destination breakdown is not available because no destination data was provided.' },
    food: food.length ? { sections: [{ rows: food }] } : { unavailable: 'Food-type breakdown is not available because no food category data was provided.' },
  }
}

function metricCell(row, code, taxonomy) {
  const cell = row.metrics[code]
  if (!cell) return 'Not available'
  const definition = findByCode(taxonomy.metrics, code)
  return `${formatNumber(number(cell.total), Number(cell.display_precision ?? definition?.display_precision ?? 2))} ${escapeHtml(metricUnit(cell, definition))}`
}

const widestRow = rows => rows.reduce((max, row) => Math.max(max, Number.isFinite(row.kilograms) ? Math.abs(row.kilograms) : 0), 0)

function breakdownTable(section, tabLabel, taxonomy, widest) {
  // Bar width only. This is a bar's width relative to the widest bar on the tab — scaled
  // across every section, so two per-entry sections stay comparable to each other — and not
  // a figure printed on the page. The percentage-of-total that stood here was a number the
  // engine never produced, and §6.2 defines no share for an entry or a destination.
  const bars = section.rows.map(row => {
    const width = widest && Number.isFinite(row.kilograms) ? Math.min(Math.abs(row.kilograms) / widest * 100, 100) : 0
    return `<div class="bar-row"><div><strong>${escapeHtml(row.label)}</strong><span>${formatNumber(row.kilograms, 2)} kg</span></div><div class="bar-track"><span style="width:${width}%"></span></div></div>`
  }).join('')
  const caption = section.label ? `${tabLabel} data — ${section.label}` : `${tabLabel} data`
  const heading = section.label ? `<h3 class="breakdown-entry-heading">${escapeHtml(section.label)}</h3>` : ''
  const body = section.rows.map(row => `<tr><th scope="row">${escapeHtml(row.label)}</th><td>${formatNumber(row.kilograms, 2)} kg</td><td>${metricCell(row, 'co2e', taxonomy)}</td><td>${metricCell(row, 'cost', taxonomy)}</td><td>${metricCell(row, 'water', taxonomy)}</td></tr>`).join('')
  return `<div class="breakdown-entry">${heading}<div class="bar-list" aria-hidden="true">${bars}</div><div class="table-scroll" tabindex="0"><table><caption>${escapeHtml(caption)}</caption><thead><tr><th scope="col">Category</th><th scope="col">Waste amount</th><th scope="col">CO₂e</th><th scope="col">Cost</th><th scope="col">Water</th></tr></thead><tbody>${body}</tbody></table></div></div>`
}

function breakdownSection(state, entryResults) {
  const allBreakdowns = breakdowns(entryResults, state.taxonomy)
  const active = state.resultBreakdownTab in TAB_LABELS ? state.resultBreakdownTab : 'stage'
  const current = allBreakdowns[active]
  const scale = current.sections ? widestRow(current.sections.flatMap(section => section.rows)) : 0
  const panel = current.unavailable
    ? `<p class="empty-state">${escapeHtml(current.unavailable)}</p>`
    : `${current.note ? `<p class="breakdown-note">${escapeHtml(current.note)}</p>` : ''}${current.sections.map(section => breakdownTable(section, TAB_LABELS[active], state.taxonomy, scale)).join('')}`
  return `<section class="results-section" aria-labelledby="breakdown-title"><div class="result-section-heading"><span class="section-number">03</span><div><h2 id="breakdown-title">Breakdown by category</h2><p>Explore how the recorded waste is distributed.</p></div></div><div class="breakdown-tabs" role="tablist" aria-label="Waste breakdown">${Object.entries(TAB_LABELS).map(([key, label]) => `<button id="breakdown-tab-${key}" type="button" role="tab" data-action="breakdown-tab" data-tab="${key}" aria-selected="${active === key}" aria-controls="breakdown-panel-${key}" tabindex="${active === key ? 0 : -1}">${label}</button>`).join('')}</div><div id="breakdown-panel-${active}" class="breakdown-panel" role="tabpanel" aria-labelledby="breakdown-tab-${active}" tabindex="0">${panel}</div></section>`
}

function downloadButton() {
  return '<button class="button button-primary" type="button" data-action="download-results">Download results</button>'
}

export function downloadResults(state) {
  const totals = state.result?.totals || {}
  const totalKg = number(totals.total_kg)
  const entryLines = (state.result?.entry_results || []).flatMap(({ entry }, index) => {
    const sector = findByCode(state.taxonomy.sectors, entry.sector)
    const food = findByCode(state.taxonomy.food_categories, entry.foodCategory)
    const destinations = entry.current.filter(line => typed(line.qtyInput) > 0).map(line => `  - ${findByCode(state.taxonomy.destinations, line.destination)?.name || line.destination}: ${typed(line.qtyInput).toFixed(2)} ${entry.totalUnit}`)
    return [`Entry ${index + 1}: ${sector?.name || entry.sector}`, `Food type: ${food?.name || 'Not provided'}`, `Waste amount: ${typed(entry.totalAmount).toFixed(2)} ${entry.totalUnit}`, 'Destinations:', ...destinations, '']
  })
  const report = ['Food Waste Impact Calculator — Results', '', `Total food waste: ${formatNumber(totalKg, 2)} kg`, `Total food waste: ${formatNumber(totalKg / 1000, 3)} tonnes`, '', ...entryLines, DEMONSTRATION_NOTICE, 'Percentage waste is not available because total food handled data is required.'].join('\n')
  const url = URL.createObjectURL(new Blob([report], { type: 'text/plain;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = 'food-waste-impact-results.txt'
  link.click()
  URL.revokeObjectURL(url)
}

export function renderResults(state) {
  const result = state.result
  const entryResults = result?.entry_results || []
  if (!entryResults.length) return '<section class="content-section"><h1>Results unavailable</h1><p>No calculation result has been returned.</p></section>'
  // §6.2: `totals` is what the headline figures are rendered from, and the engine computes
  // it. The `aggregateResults` this replaced added each entry's metric total up in the
  // browser and rendered the user's own step-3 arithmetic as the total mass, so the two
  // largest numbers on the page were numbers the engine never produced (§7.6.1).
  const totals = result.totals || {}
  const mock = result.factor_set?.is_mock
  const warning = mock ? `<aside class="disclaimer" role="status"><span class="info-icon" aria-hidden="true">i</span><div><strong>Placeholder data</strong><p>${DEMONSTRATION_NOTICE}</p></div></aside>` : ''
  const version = result.factor_set?.version_label || 'Not supplied'
  return `<section class="content-section wide results-page" aria-labelledby="results-title"><p class="eyebrow">Step 6</p><h1 id="results-title">Your estimated impact</h1><p class="section-intro">Results returned by the calculation service for ${entryResults.length} supply-chain ${entryResults.length === 1 ? 'entry' : 'entries'}.</p>${warning}
    <section class="results-section" aria-labelledby="summary-title"><div class="result-section-heading"><span class="section-number">01</span><div><h2 id="summary-title">Impact summary</h2><p>A high-level view of the recorded food waste.</p></div></div><div class="results-grid">${summaryCards(totals, state.taxonomy)}</div></section>
    <section class="results-section" aria-labelledby="equivalents-title"><div class="result-section-heading"><span class="section-number">02</span><div><h2 id="equivalents-title">Tangible equivalents</h2><p>Plain-language comparisons appear when supplied by the calculation service.</p></div></div>${equivalences(totals)}</section>
    ${breakdownSection(state, entryResults)}
    <section class="methodology-compact" id="results-methodology" aria-labelledby="results-methodology-title"><h2 id="results-methodology-title">Methodology &amp; Limitations</h2><p>Results are estimates. Impact calculations are supplied by the calculation API; the front end performs unit conversion only.</p><p>Factor version: ${escapeHtml(version)}.</p><details><summary>View methodology</summary><div><p>Data sources and calculation factors are maintained and approved by Kai Commitment.</p><p>Percentage waste remains unavailable until total food handled data is supplied.</p></div></details></section>
    <div class="result-actions"><button class="button button-secondary" type="button" data-action="go-step" data-step="4">Edit your data</button><button class="button button-secondary" type="button" data-action="start-over">Start a new calculation</button>${downloadButton()}</div>
    ${ImprovementScenario(state)}
    ${ComparisonResults(state)}
  </section>`
}
