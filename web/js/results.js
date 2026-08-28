import { escapeHtml, formatNumber, stepNav } from './view.js'
import { t, isMachineTranslated, MACHINE_TRANSLATION_NOTICE } from './i18n.js'
import { entryTotal, kgToTonnes } from './units.js'
import { ComparisonResults, ImprovementScenario } from './improvement.js'
import { buildTextReportPdf } from './pdf.js'

const DEMONSTRATION_NOTICE = 'Demonstration only — verified calculation factors have not yet been supplied.'

// The English source strings, which are also the catalogue keys. `TAB_LABELS` is
// keyed by tab and read in three places, one of which is a table caption, so the
// translation happens where it is rendered rather than here - a module-level t()
// would be evaluated once and would be right only by accident.
const TAB_LABELS = { stage: 'By supply-chain stage', destination: 'By waste destination', food: 'By food type' }

// The one metric code this module names, and it is not the hard-coded list §7.6.5 forbids:
// that rule exists because a view listing `['co2e','water','cost']` *omits* the metric a
// staff member inserted, and every metric the response carries still appears here. `mass` is
// held out because §3 hoists it — its formula is `qty_kg` (§4.3), so `scenario.total_kg` and
// `by_destination[].qty_kg` are the same figure, and it is already on screen as the primary
// card and the "Waste amount" column. Printing it twice per row is not configurability.
const MASS_METRIC = 'mass'

// §7.6.6: `downstream` may be negative (§2.2), so a metric total may be — and that negative
// total is the reuse-and-offset result the calculator exists to show. `.value-negative` is
// the stylesheet's marker for it: beetroot and bold, and deliberately **no arrow**, because
// this is a quantity rather than a movement. Nothing marks an ordinary positive total: a
// green mark against every figure on the page is decoration, not a signal. The arrows belong
// to the comparison screen's `.change-*` classes, where the sign of `net_benefit` is a
// direction of change — see the ruling beside them in `styles.css`.
const negativeClass = value => (Number.isFinite(value) && value < 0 ? ' value-negative' : '')

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
  const impactCards = Object.entries(metrics).filter(([code]) => code !== MASS_METRIC).map(([code, metric]) => {
    const definition = findByCode(taxonomy.metrics, code)
    const precision = Number(metric.display_precision ?? definition?.display_precision ?? 2)
    const total = number(metric.total)
    return `<article class="result-card"><p class="result-label">${escapeHtml(definition?.name || code)}</p><p class="result-value${negativeClass(total)}">${formatNumber(total, precision)} ${escapeHtml(metricUnit(metric, definition))}</p></article>`
  }).join('')
  const totalKg = number(totals.total_kg)
  return `<article class="result-card primary-result"><p class="result-label">${escapeHtml(t('Total food waste'))}</p><p class="result-value">${formatNumber(totalKg, 2)} kg</p><p class="result-note">${formatNumber(kgToTonnes(totals.total_kg), 3)} ${escapeHtml(t('tonnes'))}</p></article>${impactCards}<article class="result-card"><p class="result-label">${escapeHtml(t('Percentage waste'))}</p><p class="result-value">${escapeHtml(t('Not available'))}</p><p class="result-note">${escapeHtml(t('Total food handled data is required.'))}</p></article>`
}

// §3: `label` is `label_template` with the equivalence's own value already interpolated and
// formatted by the engine, and no consumer re-derives it. The map this replaced summed
// `value` across entries and then rendered three hard-coded English labels of its own for
// three hard-coded codes, so a new equivalence never appeared and the client's approved
// wording was overridden (§7.6.5).
function equivalences(totals) {
  const rows = totals.current?.equivalences || []
  if (!rows.length) return `<p class="empty-state">${escapeHtml(t('Tangible equivalents are available once approved conversion factors are supplied.'))}</p>`
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
    destination: destination.length ? { sections: destination, note: t('Each supply-chain entry is shown on its own. The same destination under two entries draws two different upstream factors, so it is genuinely two rows.') } : { unavailable: t('Waste-destination breakdown is not available because no destination data was provided.') },
    food: food.length ? { sections: [{ rows: food }] } : { unavailable: t('Food-type breakdown is not available because no food category data was provided.') },
  }
}

function metricCell(row, code, taxonomy) {
  const cell = row.metrics[code]
  if (!cell) return t('Not available')
  const definition = findByCode(taxonomy.metrics, code)
  const total = number(cell.total)
  const text = `${formatNumber(total, Number(cell.display_precision ?? definition?.display_precision ?? 2))} ${escapeHtml(metricUnit(cell, definition))}`
  return Number.isFinite(total) && total < 0 ? `<span class="value-negative">${text}</span>` : text
}

/**
 * The metric columns of a breakdown tab, taken from the response rather than from this file.
 *
 * §7.6.5 and §2.1: adding a metric is meant to cost one `INSERT` and one formula. The three
 * columns hard-coded here — `CO₂e / Cost / Water` — cost a code change instead, and they had
 * already silently dropped `ch4`, a metric that is in §2.1's own example list and in
 * `tests/fixtures/taxonomy.json`.
 *
 * The order is the response's own key order, not a sort applied here: §4.1 defines
 * `FactorBundle.metrics` as sorted by `sort_order`, and the engine iterates it in that order
 * to build `metrics`, so JSON key order already is display order. Codes are collected across
 * every section of the tab so all its tables carry the same columns, and a row that is
 * missing one renders `metricCell`'s "Not available" rather than shifting the row.
 */
function metricColumns(sections) {
  const codes = []
  for (const section of sections) {
    for (const row of section.rows || []) {
      for (const code of Object.keys(row.metrics || {})) {
        if (code !== MASS_METRIC && !codes.includes(code)) codes.push(code)
      }
    }
  }
  return codes
}

const metricName = (code, taxonomy) => findByCode(taxonomy.metrics, code)?.name || code

const widestRow = rows => rows.reduce((max, row) => Math.max(max, Number.isFinite(row.kilograms) ? Math.abs(row.kilograms) : 0), 0)

function breakdownTable(section, tabLabel, taxonomy, widest, columns) {
  // Bar width only. This is a bar's width relative to the widest bar on the tab — scaled
  // across every section, so two per-entry sections stay comparable to each other — and not
  // a figure printed on the page. The percentage-of-total that stood here was a number the
  // engine never produced, and §6.2 defines no share for an entry or a destination.
  const bars = section.rows.map(row => {
    const width = widest && Number.isFinite(row.kilograms) ? Math.min(Math.abs(row.kilograms) / widest * 100, 100) : 0
    return `<div class="bar-row"><div><strong>${escapeHtml(row.label)}</strong><span>${formatNumber(row.kilograms, 2)} kg</span></div><div class="bar-track"><span style="width:${width}%"></span></div></div>`
  }).join('')
  const caption = section.label
    ? t('%(tab)s data — %(entry)s', { tab: tabLabel, entry: section.label })
    : t('%(tab)s data', { tab: tabLabel })
  const heading = section.label ? `<h3 class="breakdown-entry-heading">${escapeHtml(section.label)}</h3>` : ''
  const head = columns.map(code => `<th scope="col">${escapeHtml(metricName(code, taxonomy))}</th>`).join('')
  const body = section.rows.map(row => `<tr><th scope="row">${escapeHtml(row.label)}</th><td>${formatNumber(row.kilograms, 2)} kg</td>${columns.map(code => `<td>${metricCell(row, code, taxonomy)}</td>`).join('')}</tr>`).join('')
  return `<div class="breakdown-entry">${heading}<div class="bar-list" aria-hidden="true">${bars}</div><div class="table-scroll" tabindex="0"><table><caption>${escapeHtml(caption)}</caption><thead><tr><th scope="col">${escapeHtml(t('Category'))}</th><th scope="col">${escapeHtml(t('Waste amount'))}</th>${head}</tr></thead><tbody>${body}</tbody></table></div></div>`
}

function breakdownSection(state, entryResults) {
  const allBreakdowns = breakdowns(entryResults, state.taxonomy)
  const active = state.resultBreakdownTab in TAB_LABELS ? state.resultBreakdownTab : 'stage'
  const current = allBreakdowns[active]
  const scale = current.sections ? widestRow(current.sections.flatMap(section => section.rows)) : 0
  const columns = current.sections ? metricColumns(current.sections) : []
  const panel = current.unavailable
    ? `<p class="empty-state">${escapeHtml(current.unavailable)}</p>`
    : `${current.note ? `<p class="breakdown-note">${escapeHtml(current.note)}</p>` : ''}${current.sections.map(section => breakdownTable(section, t(TAB_LABELS[active]), state.taxonomy, scale, columns)).join('')}`
  return `<section class="results-section" aria-labelledby="breakdown-title"><div class="result-section-heading"><span class="section-number">03</span><div><h2 id="breakdown-title">${escapeHtml(t('Breakdown by category'))}</h2><p>${escapeHtml(t('Explore how the recorded waste is distributed.'))}</p></div></div><div class="breakdown-tabs" role="tablist" aria-label="${escapeHtml(t('Waste breakdown'))}">${Object.entries(TAB_LABELS).map(([key, label]) => `<button id="breakdown-tab-${key}" type="button" role="tab" data-action="breakdown-tab" data-tab="${key}" aria-selected="${active === key}" aria-controls="breakdown-panel-${key}" tabindex="${active === key ? 0 : -1}">${escapeHtml(t(label))}</button>`).join('')}</div><div id="breakdown-panel-${active}" class="breakdown-panel" role="tabpanel" aria-labelledby="breakdown-tab-${active}" tabindex="0">${panel}</div></section>`
}

// One metric, worded the way the summary card words it: the taxonomy's name, the figure at
// the metric's own `display_precision`, and the unit that travelled with the figure. Nothing
// is recomputed and nothing is re-scaled — §7.6.1 leaves no layer that could.
function metricText(code, cell, taxonomy) {
  const definition = findByCode(taxonomy.metrics, code)
  const precision = Number(cell.display_precision ?? definition?.display_precision ?? 2)
  return `${definition?.name || code}: ${formatNumber(number(cell.total), precision)} ${metricUnit(cell, definition)}`.trimEnd()
}

// The response's own key order, which §4.1 already sorted by `sort_order`. `mass` is held
// out for the reason `summaryCards` holds it out — it is the "Total food waste" line above,
// printed twice otherwise — and that single-code exclusion is not the hard-coded list
// §7.6.5 forbids: every other metric the response carries is printed, whatever it is.
const metricLines = (scenario, taxonomy, indent) => Object.entries(scenario?.metrics || {})
  .filter(([code]) => code !== MASS_METRIC)
  .map(([code, cell]) => `${indent}${metricText(code, cell, taxonomy)}`)

// The destination breakdown table, in text: one line per destination of one entry, carrying
// that destination's mass and every metric the engine computed against it.
const destinationImpactLines = (scenario, taxonomy) => destinationRows(scenario, taxonomy).map(row => {
  const figures = Object.entries(row.metrics)
    .filter(([code]) => code !== MASS_METRIC)
    .map(([code, cell]) => metricText(code, cell, taxonomy))
  return `  - ${row.label} (${formatNumber(row.kilograms, 3)} kg): ${figures.join('; ') || t('no impact figures were returned')}`
})

// The comparison screen, in text, and only when one was run. `net_benefit` is read from the
// response — §6.2 computes `current − alternative` per metric and the browser must not
// subtract the two itself (§7.6.1), which is the defect `improvement.js` already had removed
// from the screen and which this file must not reintroduce on its way to a file.
function comparisonLines(state) {
  const totals = state.improvementResult?.totals
  if (!totals?.alternative) return []
  const netBenefit = totals.net_benefit || {}
  const lines = []
  for (const [code, cell] of Object.entries(totals.current?.metrics || {})) {
    if (code === MASS_METRIC) continue
    const improved = totals.alternative?.metrics?.[code]
    if (!improved) continue
    const definition = findByCode(state.taxonomy.metrics, code)
    const precision = Number(cell.display_precision ?? definition?.display_precision ?? 2)
    const unit = metricUnit(cell, definition)
    const difference = number(netBenefit[code])
    // The screen's own wording, from `changeCopy`: a positive `net_benefit` is a saving.
    const change = !Number.isFinite(difference)
      ? t('Not available')
      : Math.abs(difference) < 1e-9
        ? t('no change')
        : `${formatNumber(Math.abs(difference), precision)} ${unit} ${difference > 0 ? t('saved') : t('increase')}`.trimEnd()
    const figure = value => `${formatNumber(number(value), precision)} ${unit}`.trimEnd()
    lines.push(`  - ${definition?.name || code}: ${figure(cell.total)} → ${figure(improved.total)} (${change})`)
  }
  if (!lines.length) return []
  return ['', t('Improved scenario (Current → Improved)'), ...lines]
}

/**
 * The plain-text report, built and returned rather than downloaded.
 *
 * Split out of `downloadResults` because it is the half worth asserting on: the export was
 * shipping the total mass, the entries and the factor version and **not one output figure**
 * — no greenhouse gas, no methane, no water, no cost — under the file name
 * `food-waste-impact-results.txt`. A results export with no results is the file somebody
 * attaches to an email, and every number in it now comes from `state.result`, which is the
 * engine's, never from arithmetic performed here (§7.6.1).
 *
 * @param {object} state
 * @returns {string}
 */
/**
 * One entry's waste amount, as the visitor gave it.
 *
 * Mass mode reads the field the visitor typed in. Container mode reads the count and the
 * container, and appends the kilograms `entryTotal` derives — never the kilograms alone,
 * because the number a reader can check against their own bins is the count.
 */
function wasteAmountLine(entry, taxonomy) {
  if (entry.measureMode !== 'container') {
    return `${t('Waste amount')}: ${typed(entry.totalAmount).toFixed(2)} ${t(entry.totalUnit === 'tonnes' ? 'tonnes' : 'kilograms')}`
  }
  const preset = findByCode(taxonomy.unit_presets || [], entry.unitPreset)
  const kilograms = entryTotal(entry, taxonomy.unit_presets).amount
  const container = preset?.label || entry.unitPreset || ''
  return `${t('Waste amount')}: ${typed(entry.unitCount).toFixed(2)} × ${container} (${kilograms} kg)`
}

export function buildResultsReport(state) {
  const totals = state.result?.totals || {}
  const totalKg = number(totals.total_kg)
  const summary = metricLines(totals.current, state.taxonomy, '  - ')
  // §3: `label` is `label_template` with the value already interpolated and formatted by the
  // engine. It is copied verbatim, the same as on screen — nothing here re-derives a figure
  // or rewords the client's approved sentence.
  const equivalents = (totals.current?.equivalences || []).map(row => `  - ${row.label}`)
  const entryLines = (state.result?.entry_results || []).flatMap(({ entry, response }, index) => {
    const sector = findByCode(state.taxonomy.sectors, entry.sector)
    const food = findByCode(state.taxonomy.food_categories, entry.foodCategory)
    const destinations = entry.current.filter(line => typed(line.qtyInput) > 0).map(line => `  - ${findByCode(state.taxonomy.destinations, line.destination)?.name || line.destination}: ${typed(line.qtyInput).toFixed(2)} ${entry.totalUnit}`)
    const scenario = response?.current || {}
    const impact = metricLines(scenario, state.taxonomy, '  - ')
    const byDestination = destinationImpactLines(scenario, state.taxonomy)
    return [
      t('Entry %(number)s: %(sector)s', { number: index + 1, sector: sector?.name || entry.sector }),
      `${t('Food type')}: ${food?.name || t('Not provided')}`,
      // A container entry has no `totalAmount` — the visitor said "two 240 L wheelie
      // bins", not "139.20 kilograms" — so reading that field printed **0.00 kilograms**
      // into a report whose whole job is to be attached to an email and believed. The
      // report says what was entered and the kilograms it came to, in that order, and
      // `entryTotal` is the same reconciliation the form uses rather than a second copy
      // of it. `preset.label` is staff-typed and is published as written (§7.7.7).
      wasteAmountLine(entry, state.taxonomy),
      `${t('Destinations')}:`, ...destinations,
      ...(impact.length ? [`${t('Impact for this entry')}:`, ...impact] : []),
      ...(byDestination.length ? [`${t('Impact by destination')}:`, ...byDestination] : []),
      '',
    ]
  })
  // §7.6.2: the placeholder notice is conditional on `is_mock`, on **every** export, and it
  // was appended unconditionally. That reads as correct while every factor set is mock and
  // inverts the day a real one is published — a client-facing report that disclaims real
  // data is the more damaging half of the same bug. The factor version replaces it as the
  // line that says which numbers these are, so a real export is not left saying nothing.
  const notice = state.result?.factor_set?.is_mock ? [t(DEMONSTRATION_NOTICE)] : []
  // The export leaves the browser and is read by somebody who did not choose the
  // language it was written in, so a machine-translated interface has to say so on
  // the file as well as on the screen it came from.
  const translationNotice = isMachineTranslated() ? ['', MACHINE_TRANSLATION_NOTICE] : []
  return [
    t('Food Waste Impact Calculator — Results'),
    '',
    `${t('Total food waste')}: ${formatNumber(totalKg, 2)} kg`,
    `${t('Total food waste')}: ${formatNumber(kgToTonnes(totals.total_kg), 3)} ${t('tonnes')}`,
    '',
    t('Impact summary'),
    ...(summary.length ? summary : [`  - ${t('No impact metrics were returned.')}`]),
    '',
    t('Tangible equivalents'),
    ...(equivalents.length ? equivalents : [`  - ${t('Tangible equivalents are available once approved conversion factors are supplied.')}`]),
    ...comparisonLines(state),
    '',
    ...entryLines,
    `${t('Factor version')}: ${state.result?.factor_set?.version_label || t('Not supplied')}`,
    ...notice,
    t('Percentage waste is not available because total food handled data is required.'),
    ...translationNotice,
  ].join('\n')
}

/**
 * The download's file name, stamped with local time.
 *
 * A fixed name meant every export after the first arrived as
 * `food-waste-impact-results (1).pdf`, and the browser decides that suffix, not
 * this code - so the order is the download order, not the calculation order, and
 * nothing on the file says which scenario it holds. Somebody comparing two runs
 * has to open both to tell them apart.
 *
 * Sortable order (year first, zero-padded), so a directory listing is
 * chronological. Local time rather than UTC: the stamp exists to be recognised
 * by the person who pressed the button, and they are reading their own clock.
 *
 * `now` is a parameter because a function that reads the system clock cannot be
 * asserted on - the same reason `engine.calculate` takes no clock.
 */
export function exportFilename(now = new Date()) {
  const pad = (value) => String(value).padStart(2, '0')
  const stamp = [
    now.getFullYear(),
    pad(now.getMonth() + 1),
    pad(now.getDate()),
  ].join('-') + '-' + [
    pad(now.getHours()),
    pad(now.getMinutes()),
    pad(now.getSeconds()),
  ].join('')
  return `food-waste-impact-results-${stamp}.pdf`
}

export function downloadResults(state) {
  const pdf = buildTextReportPdf(buildResultsReport(state))
  const url = URL.createObjectURL(new Blob([pdf], { type: 'application/pdf' }))
  const link = document.createElement('a')
  link.href = url
  link.download = exportFilename()
  link.click()
  URL.revokeObjectURL(url)
}

export function renderResults(state) {
  const result = state.result
  const entryResults = result?.entry_results || []
  if (!entryResults.length) return `<section class="content-section"><h1>${escapeHtml(t('Results unavailable'))}</h1><p>${escapeHtml(t('No calculation result has been returned.'))}</p></section>`
  // §6.2: `totals` is what the headline figures are rendered from, and the engine computes
  // it. The `aggregateResults` this replaced added each entry's metric total up in the
  // browser and rendered the user's own step-3 arithmetic as the total mass, so the two
  // largest numbers on the page were numbers the engine never produced (§7.6.1).
  const totals = result.totals || {}
  const mock = result.factor_set?.is_mock
  const warning = mock ? `<aside class="disclaimer" role="status"><span class="info-icon" aria-hidden="true">i</span><div><strong>${escapeHtml(t('Placeholder data'))}</strong><p>${escapeHtml(t(DEMONSTRATION_NOTICE))}</p></div></aside>` : ''
  const version = result.factor_set?.version_label || t('Not supplied')
  // `stepNav` is the LAST child of this section and has to stay there: it is
  // `position: sticky; bottom: 0`, which pins only while its containing block
  // extends past the fold. The improvement panel renders above it for that
  // reason — appending anything after the bar unpins it early, and its first
  // action was 1,230-2,075px past the fold before it existed. "Edit your data"
  // and "Download results" moved into it; "Start a new calculation" did not,
  // because it is a confirm-guarded reset rather than a step action, and the
  // header's home button already offers it.
  return `<section class="content-section wide results-page" aria-labelledby="results-title"><p class="eyebrow">${escapeHtml(t('Step %(step)s', { step: 6 }))}</p><h1 id="results-title">${escapeHtml(t('Your estimated impact'))}</h1><p class="section-intro">${escapeHtml(entryResults.length === 1
      ? t('Results returned by the calculation service for one supply-chain entry.')
      : t('Results returned by the calculation service for %(count)s supply-chain entries.', { count: entryResults.length }))}</p>${warning}
    <section class="results-section" aria-labelledby="summary-title"><div class="result-section-heading"><span class="section-number">01</span><div><h2 id="summary-title">${escapeHtml(t('Impact summary'))}</h2><p>${escapeHtml(t('A high-level view of the recorded food waste.'))}</p></div></div><div class="results-grid">${summaryCards(totals, state.taxonomy)}</div></section>
    <section class="results-section" aria-labelledby="equivalents-title"><div class="result-section-heading"><span class="section-number">02</span><div><h2 id="equivalents-title">${escapeHtml(t('Tangible equivalents'))}</h2><p>${escapeHtml(t('Plain-language comparisons appear when supplied by the calculation service.'))}</p></div></div>${equivalences(totals)}</section>
    ${breakdownSection(state, entryResults)}
    <section class="methodology-compact" id="results-methodology" aria-labelledby="results-methodology-title"><h2 id="results-methodology-title">${escapeHtml(t('Methodology & Limitations'))}</h2><p>${escapeHtml(t('Results are estimates. Impact calculations are supplied by the calculation API; the front end performs unit conversion only.'))}</p><p>${escapeHtml(t('Factor version'))}: ${escapeHtml(version)}.</p><details><summary>${escapeHtml(t('View methodology'))}</summary><div><p>${escapeHtml(t('Data sources and calculation factors are maintained and approved by Kai Commitment.'))}</p><p>${escapeHtml(t('Percentage waste remains unavailable until total food handled data is supplied.'))}</p></div></details></section>
    <div class="result-actions"><button class="button button-secondary" type="button" data-action="start-over">${escapeHtml(t('Start a new calculation'))}</button></div>
    ${ImprovementScenario(state)}
    ${ComparisonResults(state)}
    ${stepNav({ step: 5, back: 4, backLabel: t('Edit your data'), label: t('Download results'), action: 'download-results' })}
  </section>`
}
