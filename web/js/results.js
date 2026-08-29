import { escapeHtml, formatNumber, stepNav } from './view.js'
import { t, activeLanguage, isMachineTranslated, MACHINE_TRANSLATION_NOTICE } from './i18n.js'
import { entryTotal, isPresetUnit, kgToTonnes, presetUnitCode, rowKgString } from './units.js'
import { ComparisonResults, ImprovementScenario } from './improvement.js'
import { contribute, exportPdf } from './api.js'
import { exportPayload } from './submission.js'
import { setState } from './state.js'

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

// Item ⑦: `time_frame` is a closed vocabulary (§6.2) for exactly the reason
// `gwp_horizon` is closed to 20 and 100 - a value this map has no phrase for must
// never reach a visitor as the raw identifier. The four phrases are the ones
// `calculator.js`'s own step-4 `<select>` already offers, reused rather than
// reworded so the word a visitor chose is the word they see reflected back.
const TIME_FRAME_LABELS = {
  one_week: 'One week',
  one_month: 'One month',
  one_quarter: 'One quarter',
  one_year: 'One year',
}

// A label, never a computation (contract v1.48, §6.2): nothing on this page is scaled
// by the period, so it renders as one plain line rather than beside any figure it might
// be misread as multiplying. `''` - "not stated" - renders nothing, the same way an
// unstated money figure renders nothing rather than a placeholder.
function resultsPeriod(timeFrame) {
  const phrase = TIME_FRAME_LABELS[timeFrame]
  if (!phrase) return ''
  return `<p class="results-period">${escapeHtml(t('These figures cover: %(period)s', { period: t(phrase) }))}</p>`
}

// The export's own line for the same fact, worded identically to `resultsPeriod`
// above so a visitor reading the page and the file they downloaded from it sees
// the same sentence rather than two different ways of saying the same thing.
const periodLine = timeFrame => {
  const phrase = TIME_FRAME_LABELS[timeFrame]
  return phrase ? t('These figures cover: %(period)s', { period: t(phrase) }) : ''
}

// Display-only coercion of a two-decimal-place NZD string (§4.5, §1.2): the money
// block is dollars and cents, not a ten-place metric value, but it is still a string
// on the wire and still passes through `Number()` for display only (§7.6.1). 'NZ$' is
// currency notation, not a phrase, so it is not passed through `t()` - the same
// reasoning `kg` throughout this file is written in and never translated (§7.7.7).
const nzd = value => `NZ$${formatNumber(number(value), 2)}`
const hasValue = value => value !== null && value !== undefined

/**
 * §4.5's money block, rendered beside `summaryCards()` inside the same "Impact
 * summary" section rather than as a section of its own - four figures the engine
 * derived from what the visitor typed, and nothing here derives a fifth. `totals.money`
 * is `null` unless at least one entry supplied a value, and each of the four fields is
 * independently `null` unless what it derives from was supplied - a computed zero would
 * read as "this food was worth nothing" rather than "nobody said" (§4.5), so a `null`
 * field is left off the list rather than printed as `0.00`, a dash, or a bare `NZ$`.
 *
 * The share is printed exactly as the response gives it, deliberately unclamped
 * (§4.5): a figure over 100% is a visitor's own typo showing through, not a rendering
 * fault, and nothing here is a bar or a width that a figure over 100% could overflow.
 *
 * **Three figures, not four: `saving_nzd` is not one of them, and that is the fix
 * rather than a deletion.** The three above are properties of the *current* scenario
 * alone — what the visitor said their food was worth — and this block is fed by the
 * Calculate button, which sends `alternative: null` for every entry
 * (`submission.js`). §4.5 makes `saving_nzd` `None` "when no entry carries an
 * alternative", so the row this block used to carry could not be reached by any
 * visitor: the response that does hold a saving is Compare Impact's, and it lands on
 * `state.improvementResult`. The saving is now rendered by `ComparisonResults`
 * (`improvement.js`), beside the comparison that produced it, and written to the
 * export by `comparisonLines` below, under the same heading as that comparison.
 * Teaching this block to read a second response would have put a figure about the
 * improved scenario inside a section describing the current one.
 */
function moneySummary(totals) {
  const money = totals.money
  if (!money) return ''
  const rows = []
  if (hasValue(money.total_value_nzd)) {
    rows.push(`<div class="money-row"><span class="money-label">${escapeHtml(t('Total value of food handled'))}</span><span class="money-value">${nzd(money.total_value_nzd)}</span></div>`)
  }
  if (hasValue(money.wasted_value_nzd)) {
    rows.push(`<div class="money-row"><span class="money-label">${escapeHtml(t('Value of food wasted'))}</span><span class="money-value">${nzd(money.wasted_value_nzd)}</span></div>`)
  }
  if (hasValue(money.wasted_share_percent)) {
    rows.push(`<div class="money-row"><span class="money-label">${escapeHtml(t('Share of value wasted'))}</span><span class="money-value">${formatNumber(number(money.wasted_share_percent), 2)}%</span></div>`)
  }
  if (!rows.length) return ''
  return `<div class="money-summary"><h3>${escapeHtml(t('The money'))}</h3><p class="result-note">${escapeHtml(t("Figures the calculator did not derive: what you typed for value, summed by the calculation service."))}</p><div class="money-rows">${rows.join('')}</div></div>`
}

// `moneySummary`'s figures, in text, worded to match the rows on screen rather than
// re-deriving them: same three fields, same `null`-means-absent rule. Returns `[]` (no
// heading printed) when the block is `null` or carries nothing - a heading over an empty
// list is the "—" this module exists to avoid. The saving is not here for the reason it
// is not on screen here either: it belongs to the comparison, and `savingLines` below
// writes it under that comparison's own heading.
function moneyLines(totals) {
  const money = totals.money
  if (!money) return []
  const lines = []
  if (hasValue(money.total_value_nzd)) lines.push(`  - ${t('Total value of food handled')}: ${nzd(money.total_value_nzd)}`)
  if (hasValue(money.wasted_value_nzd)) lines.push(`  - ${t('Value of food wasted')}: ${nzd(money.wasted_value_nzd)}`)
  if (hasValue(money.wasted_share_percent)) lines.push(`  - ${t('Share of value wasted')}: ${formatNumber(number(money.wasted_share_percent), 2)}%`)
  if (!lines.length) return []
  return ['', t('The money'), ...lines]
}

function summaryCards(totals, taxonomy) {
  const metrics = totals.current?.metrics || {}
  const impactCards = Object.entries(metrics).filter(([code]) => code !== MASS_METRIC).map(([code, metric]) => {
    const definition = findByCode(taxonomy.metrics, code)
    const precision = Number(metric.display_precision ?? definition?.display_precision ?? 2)
    const total = number(metric.total)
    return `<article class="result-card"><p class="result-label">${escapeHtml(definition?.name || code)}</p><p class="result-value${negativeClass(total)}">${formatNumber(total, precision)} ${escapeHtml(metricUnit(metric, definition))}</p></article>`
  }).join('')
  const totalKg = number(totals.total_kg)
  return `<article class="result-card primary-result"><p class="result-label">${escapeHtml(t('Total food waste'))}</p><p class="result-value">${formatNumber(totalKg, 2)} kg</p><p class="result-note">${formatNumber(kgToTonnes(totals.total_kg), 3)} ${escapeHtml(t('tonnes'))}</p></article>${impactCards}<article class="result-card"><p class="result-label">${escapeHtml(t('Percentage waste'))}</p><p class="result-value">${escapeHtml(t('Not available'))}</p><p class="result-note">${escapeHtml(t('This calculator does not report waste as a share of food handled yet.'))}</p></article>`
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

// One row per destination of one scenario, every figure read from that scenario's own
// result: `qty_kg` and each metric's `value` come from `by_destination`. Called both on
// one entry's `current` (§6.2 populates it per entry) and on `totals.current` (populated
// per metric since v1.48, §3 rule 2) — the two calls read the same shape, one entry-scale
// and one rolled up across every entry, and `code` is kept on the row so a caller can match
// one scenario's row to the other's by destination rather than by re-reading `label`.
function destinationRows(scenario, taxonomy) {
  const rows = new Map()
  for (const [code, metric] of Object.entries(scenario.metrics || {})) {
    for (const line of metric.by_destination || []) {
      const row = rows.get(line.destination) || {
        code: line.destination,
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

// The food leaf under a destination-first stage: the entry's own food category, or the
// same "unspecified" wording `calculator.js`'s review step already uses for a standard-mix
// entry — not a new string, so the destination tab does not invent a second way to say it.
function stageFoodLabel(entry, response, taxonomy) {
  const foodCode = response.food_category ?? entry.foodCategory
  const foodDefinition = findByCode(taxonomy.food_categories, foodCode)
  if (!foodCode || foodDefinition?.is_standard_mix) return t('Standard mix / not specified')
  return foodDefinition?.name || foodCode
}

// Destination first, then the entries that share it, then each entry's own food category —
// the transpose the client asked for of the per-entry sections this replaced. The group
// total is `totalsRow`, read whole from `totals.current.by_destination` (§7.6.1: nothing
// here adds the entries' rows together); the per-entry rows nested under it are each
// entry's own figures, computed once per entry and matched to a group by destination code.
function destinationGroups(entryResults, totalsRows, taxonomy) {
  const perEntry = entryResults.map(({ entry, response }) => ({
    entry,
    response,
    rows: destinationRows(response.current || {}, taxonomy),
  }))
  return totalsRows
    .map(totalsRow => {
      const stages = perEntry.flatMap(({ entry, response, rows }) => {
        const row = rows.find(candidate => candidate.code === totalsRow.code)
        if (!row) return []
        return [{
          label: sectorName(entry, response, taxonomy),
          foodLabel: stageFoodLabel(entry, response, taxonomy),
          row,
        }]
      })
      return { code: totalsRow.code, label: totalsRow.label, totalsRow, stages }
    })
    .filter(group => group.stages.length)
}

function breakdowns(entryResults, totals, taxonomy) {
  const stage = []
  const food = []
  for (const { entry, response } of entryResults) {
    const scenario = response.current || {}
    const metrics = metricCells(scenario)
    const kilograms = number(scenario.total_kg)
    stage.push({ label: sectorName(entry, response, taxonomy), kilograms, metrics })
    const foodCode = response.food_category ?? entry.foodCategory
    const foodDefinition = findByCode(taxonomy.food_categories, foodCode)
    if (foodCode && !foodDefinition?.is_standard_mix) food.push({ label: foodDefinition?.name || foodCode, kilograms, metrics })
  }
  const totalsRows = destinationRows(totals.current || {}, taxonomy)
  const groups = destinationGroups(entryResults, totalsRows, taxonomy)
  // The note explains a relationship between two levels of figures, so it is printed
  // only where that relationship exists: a group fed by one entry now prints its
  // figures once (see `destinationTree`), and with one entry — the commonest journey
  // by far — *every* group is such a group. Printing the sentence there had the page
  // saying the rows "add up to the total rather than repeat it" directly beneath rows
  // that repeated it exactly.
  const anySplit = groups.some(group => group.stages.length > 1)
  return {
    stage: { sections: [{ rows: stage }] },
    destination: groups.length
      ? { groups, note: anySplit ? t("The figure beside each destination is the engine's own cross-entry total. The rows beneath it are each entry's own — they can draw different upstream factors, which is why they add up to the total shown rather than repeat it.") : '' }
      : { unavailable: t('Waste-destination breakdown is not available because no destination data was provided.') },
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

// One destination group's figures beside its heading or beside a stage: every metric but
// `mass` (held out for the reason `metricLines` holds it out — it is the kilogram figure
// printed next to it), read through the same `metricCell` the other two tabs use so a
// negative `downstream` offset (§7.6.6) is marked here exactly as it is there.
function destinationRowFigures(row, taxonomy) {
  return Object.keys(row.metrics || {})
    .filter(code => code !== MASS_METRIC)
    .map(code => `<span>${escapeHtml(metricName(code, taxonomy))}: ${metricCell(row, code, taxonomy)}</span>`)
    .join('')
}

// The destination-first tree: one `.destination-group` per destination, carrying the
// engine's own rolled-up total (`data-destination` names the code so a caller — this
// file's tests among them — can find one group without depending on render order); inside
// it, one `.destination-group__stage` per entry that used that destination, each showing
// that entry's own sector and figures; inside that, the one `.destination-group__food` its
// food category is. Three levels for the three questions the client asked in that order:
// which destination, which stage of the supply chain sent it there, what food it was.
function destinationTree(groups, taxonomy) {
  return groups.map(group => {
    // **One contributing entry means one set of figures.** §3 rule 2 builds the
    // group's total by rolling the entries' own `by_destination` rows up, so when
    // exactly one entry used this destination the roll-up *is* that entry's row —
    // identical by construction, at every metric. Printing both put the same
    // numbers on screen twice under a note saying they were different, in the
    // default single-entry journey. The stage and its food category still render:
    // "which stage sent it there, what food it was" are two of the three questions
    // this tree exists to answer, and neither is a repeated figure.
    const repeated = group.stages.length === 1
    const stages = group.stages.map(stage => `<li class="destination-group__stage"><div class="destination-group__stage-row"><strong>${escapeHtml(stage.label)}</strong>${repeated ? '' : `<span>${formatNumber(stage.row.kilograms, 3)} kg</span>`}</div>${repeated ? '' : `<div class="destination-group__stage-figures">${destinationRowFigures(stage.row, taxonomy)}</div>`}<ul class="destination-group__foods"><li class="destination-group__food">${escapeHtml(stage.foodLabel)}</li></ul></li>`).join('')
    return `<article class="destination-group" data-destination="${escapeHtml(group.code)}"><div class="destination-group__header"><h3 class="destination-group__name">${escapeHtml(group.label)}</h3><p class="destination-group__total">${formatNumber(group.totalsRow.kilograms, 3)} kg</p></div><div class="destination-group__figures">${destinationRowFigures(group.totalsRow, taxonomy)}</div><ol class="destination-group__stages">${stages}</ol></article>`
  }).join('')
}

function breakdownSection(state, entryResults) {
  const totals = state.result?.totals || {}
  const allBreakdowns = breakdowns(entryResults, totals, state.taxonomy)
  const active = state.resultBreakdownTab in TAB_LABELS ? state.resultBreakdownTab : 'stage'
  const current = allBreakdowns[active]
  let panel
  if (current.unavailable) {
    panel = `<p class="empty-state">${escapeHtml(current.unavailable)}</p>`
  } else if (active === 'destination') {
    // The tree, not `breakdownTable`: a destination's stages and foods are two more levels
    // than that table's single row of columns has room for, and its bars and per-tab metric
    // columns describe a flat list that this shape no longer is.
    panel = `${current.note ? `<p class="breakdown-note">${escapeHtml(current.note)}</p>` : ''}${destinationTree(current.groups, state.taxonomy)}`
  } else {
    const scale = widestRow(current.sections.flatMap(section => section.rows))
    const columns = metricColumns(current.sections)
    panel = `${current.note ? `<p class="breakdown-note">${escapeHtml(current.note)}</p>` : ''}${current.sections.map(section => breakdownTable(section, t(TAB_LABELS[active]), state.taxonomy, scale, columns)).join('')}`
  }
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

/**
 * §4.5's saving, in text, in the two lines the comparison screen shows it in.
 *
 * Same fields and the same `null`-means-absent rule as `moneyLines`, and the caveat
 * travels with the figure here exactly as it does on screen: the rate is nominal —
 * `wasted_value_nzd ÷ that entry's current mass` — so a sentence saying so has to be
 * as hard to crop away in a text file as it is in a screenshot.
 */
function savingLines(totals) {
  const saving = totals?.money?.saving_nzd
  if (!hasValue(saving)) return []
  return [
    `  - ${t('Value of food not wasted at all')}: ${nzd(saving)}`,
    `    ${t('This assumes an even value per kilogram within each entry you priced, the way a box of produce is costed as a whole - not a measured price, and not an average taken across every entry.')}`,
  ]
}

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
  // §4.5's saving is a figure about *this* comparison — `rate x diverted mass`, and
  // `diverted` is `current − alternative` — so it is written here rather than beside the
  // three current-scenario money figures `moneyLines` prints. It is also why the heading
  // is printed when `lines` is empty but a saving is present: a response can carry a
  // saving and no comparable metric, and dropping the heading would strand the figure.
  const saving = savingLines(totals)
  if (!lines.length && !saving.length) return []
  return ['', t('Improved scenario (Current → Improved)'), ...lines, ...saving]
}

/**
 * One destination row of one entry, in the unit **that row** was measured in.
 *
 * `${qtyInput} ${entry.totalUnit}` stood here, and since a row carries its own unit that
 * was a figure labelled with somebody else's: 500 kg entered against an entry measured in
 * tonnes exported as `500.00 tonnes`, and half a tonne against an entry measured in
 * kilograms exported as `0.50 kilograms`. This file's own note says the report exists to
 * be attached to an email and believed, which is why a wrong label on it is not a cosmetic
 * defect — it is a thousandfold error in a document written to be trusted.
 *
 * A row measured in anything but kilograms carries the kilograms too, the way
 * `wasteAmountLine` does for a container: `0.50 tonnes` is what the visitor said and
 * `(500.000 kg)` is what was calculated from it, and a reader holding only this file needs
 * both to check one against the other. `kg` is not translated — §7.7.7, metric units are
 * international notation — and a container's `label` is staff-typed and published as
 * written.
 */
function destinationLine(line, entry, taxonomy) {
  const unit = line.unit || entry.totalUnit
  const destination = findByCode(taxonomy.destinations, line.destination)?.name || line.destination
  const amount = typed(line.qtyInput).toFixed(2)
  const presets = taxonomy.unit_presets || []
  const kilograms = rowKgString(line.qtyInput, unit, presets)
  if (isPresetUnit(unit)) {
    const preset = findByCode(presets, presetUnitCode(unit))
    return `  - ${destination}: ${amount} × ${preset?.label || presetUnitCode(unit)}${kilograms ? ` (${kilograms} kg)` : ''}`
  }
  if (unit === 'tonnes') return `  - ${destination}: ${amount} ${t('tonnes')} (${kilograms} kg)`
  return `  - ${destination}: ${amount} ${t('kilograms')}`
}

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
    const destinations = entry.current.filter(line => typed(line.qtyInput) > 0).map(line => destinationLine(line, entry, state.taxonomy))
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
  // Item ⑦: a label, printed once near the top of the file, same as on screen — no
  // figure below it is scaled by the period (contract v1.48).
  const period = periodLine(state.timeFrame)
  return [
    t('Food Waste Impact Calculator — Results'),
    '',
    ...(period ? [period, ''] : []),
    `${t('Total food waste')}: ${formatNumber(totalKg, 2)} kg`,
    `${t('Total food waste')}: ${formatNumber(kgToTonnes(totals.total_kg), 3)} ${t('tonnes')}`,
    '',
    t('Impact summary'),
    ...(summary.length ? summary : [`  - ${t('No impact metrics were returned.')}`]),
    '',
    t('Tangible equivalents'),
    ...(equivalents.length ? equivalents : [`  - ${t('Tangible equivalents are available once approved conversion factors are supplied.')}`]),
    ...moneyLines(totals),
    ...comparisonLines(state),
    '',
    ...entryLines,
    `${t('Factor version')}: ${state.result?.factor_set?.version_label || t('Not supplied')}`,
    ...notice,
    `${t('Percentage waste')}: ${t('Not available')}. ${t('This calculator does not report waste as a share of food handled yet.')}`,
    ...translationNotice,
  ].join('\n')
}

/**
 * The download's file name, stamped with local time.
 *
 * A fixed name meant every export after the first arrived as
 * `food-waste-impact-results (1).txt`, and the browser decides that suffix, not
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
  return `food-waste-impact-results-${stamp}.txt`
}

export function downloadResults(state) {
  const url = URL.createObjectURL(new Blob([buildResultsReport(state)], { type: 'text/plain;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = exportFilename()
  link.click()
  URL.revokeObjectURL(url)
}

/**
 * The document `POST /api/v1/export/pdf` renders (`api/export.py`) — beside the plain-text
 * download above, not instead of it. Sends what the state already has and downloads what
 * comes back; every figure in the document is the server's (§7.6.1), computed on this
 * request rather than carried over from `state.result`.
 *
 * `exportPayload` reads the locale from `activeLanguage()` — the same value `i18n.js`
 * negotiated to put the rest of this page's own text on screen — because the server has no
 * other way to know which language the visitor is reading (§O-8).
 *
 * **One fixed name, matching the server's own `Content-Disposition`** (`EXPORT_FILENAME` in
 * `api/export.py`): the blob this creates has no headers of its own for the browser to read
 * a name from, and a document downloaded twice under two different names would be the
 * confusing sibling of `exportFilename()`'s reason for stamping the text export instead.
 */
const PDF_EXPORT_FILENAME = 'kai-commitment-impact-calculator.pdf'

export async function downloadPdf(state) {
  if (state.pdfExporting) return
  setState({ pdfExporting: true, pdfError: null })
  try {
    const blob = await exportPdf(exportPayload(state, activeLanguage()))
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = PDF_EXPORT_FILENAME
    link.click()
    // Same fix PR #46's review confirmed was right: revoking synchronously can cancel the
    // download in some browsers, because Firefox and Safari can still be reading the
    // object URL when `click()` returns. Deferred to the next task instead.
    setTimeout(() => URL.revokeObjectURL(url), 0)
    setState({ pdfExporting: false })
  } catch (error) {
    setState({ pdfExporting: false, pdfError: error.message || t('The calculator service could not be reached. Check your connection and try again.') })
  }
}

/**
 * §6.2.2, and the control this whole task exists to write.
 *
 * **Unticked by construction.** `state.contributed` starts `false` and nothing here
 * sets it before a press — a pre-checked box would make stage one's `is_public_
 * contributed` default of FALSE decorative, which is exactly what item ⑬ reverses
 * §2.3's "no consent checkbox" decision to prevent.
 *
 * **One-way, and said so before the click, not after.** The route only ever sets the
 * flag (§6.2.2's own table has no path that clears it), so unticking this box would be
 * a control that lies about its own affordance. Rather than allow that gesture, both
 * `checked` and `disabled` are keyed on `pending || done` — ticked and locked the
 * moment the press happens, not only once the response lands. **Fix round 1:** `checked`
 * used to read `done` alone, so for the whole of the request the box showed unticked
 * and disabled — a visitor watching their own tick appear to undo itself, which is the
 * one message this particular control must never send. The sentence beside it says the
 * choice is one-way before the click reaches it at all.
 *
 * **Consent survives a recalculation, on purpose.** `state.contributed` is not reset
 * by `submitCalculation` or `compareImprovement` on a return trip through the wizard,
 * matching `upsert_submission`'s own behaviour: the row is the same submission,
 * revised, and the flag this control set is not touched by an update either. Resetting
 * the box here would show an unticked control over a row that is, in the database,
 * still contributed — a front end telling a visitor "you haven't" about a choice the
 * server has already recorded. The sentence below says plainly that a recalculation's
 * *figures* replace the earlier ones under the same choice, which is the true state of
 * affairs rather than a fresh question with a false "no" already implied. The status
 * line after a successful press (**fix round 1**) states the same thing in the present
 * tense, rather than a past tense the server cannot actually promise — §6.2.2 answers
 * 204 on an unknown or expired token exactly as it does on a live one, so "has been
 * added" may be false in a way this page cannot detect; "are in" is true on the first
 * press and true again after every revision.
 */
function contributeBlock(state) {
  const pending = state.contributing
  const done = state.contributed
  return `<div class="contribute-block">
    <p class="contribute-sentence" id="contribute-sentence">${escapeHtml(t('This sends an anonymous copy of your results into this calculator\'s public statistics — no name, no address, nothing that identifies you. It cannot be undone from here once sent, and if you come back and recalculate, your updated figures take its place under this same choice.'))}</p>
    <div class="contribute-control">
      <input type="checkbox" id="contribute" aria-describedby="contribute-sentence" ${pending || done ? 'checked' : ''} ${pending || done ? 'disabled' : ''}>
      <label for="contribute">${escapeHtml(t('I would like to contribute to the Kai Commitment'))}</label>
    </div>
    ${done ? `<p class="contribute-status" role="status">${escapeHtml(t("Your latest figures are in this calculator's public statistics."))}</p>` : ''}
    ${state.contributeError ? `<p class="field-error" role="alert">${escapeHtml(state.contributeError)}</p>` : ''}
  </div>`
}

/**
 * Runs on the checkbox's `change` and stores the result, or its message, on the state.
 *
 * `toPublicMessage` is `calculator.js`'s `publicError`, passed in rather than imported
 * for the same reason `compareImprovement` (`improvement.js`) takes it as a parameter:
 * that module already imports this one, and the import back would be a cycle. §6.2.2
 * answers 204 always, so there is nothing here to read as a domain-level failure — only
 * the network call itself can fail, and `ApiError`'s message is already the calculator's
 * own network-unreachable copy.
 *
 * A failed call leaves `contributed` false, which re-enables the checkbox and leaves it
 * unticked — the "pre-press state" the brief asks for — rather than reporting a success
 * that did not happen.
 */
export async function contributeCalculation(state, toPublicMessage = error => error.message || t('The calculator service could not be reached. Check your connection and try again.')) {
  if (state.contributing || state.contributed || !state.token) return
  setState({ contributing: true, contributeError: null })
  try {
    await contribute(state.token)
    setState({ contributing: false, contributed: true })
  } catch (error) {
    setState({ contributing: false, contributed: false, contributeError: toPublicMessage(error) })
  }
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
      : t('Results returned by the calculation service for %(count)s supply-chain entries.', { count: entryResults.length }))}</p>${resultsPeriod(state.timeFrame)}${warning}
    <section class="results-section" aria-labelledby="summary-title"><div class="result-section-heading"><span class="section-number">01</span><div><h2 id="summary-title">${escapeHtml(t('Impact summary'))}</h2><p>${escapeHtml(t('A high-level view of the recorded food waste.'))}</p></div></div><div class="results-grid">${summaryCards(totals, state.taxonomy)}</div>${moneySummary(totals)}</section>
    <section class="results-section" aria-labelledby="equivalents-title"><div class="result-section-heading"><span class="section-number">02</span><div><h2 id="equivalents-title">${escapeHtml(t('Tangible equivalents'))}</h2><p>${escapeHtml(t('Plain-language comparisons appear when supplied by the calculation service.'))}</p></div></div>${equivalences(totals)}</section>
    ${breakdownSection(state, entryResults)}
    <section class="methodology-compact" id="results-methodology" aria-labelledby="results-methodology-title"><h2 id="results-methodology-title">${escapeHtml(t('Methodology & Limitations'))}</h2><p>${escapeHtml(t('Results are estimates. Impact calculations are supplied by the calculation API; the front end performs unit conversion only.'))}</p><p>${escapeHtml(t('Factor version'))}: ${escapeHtml(version)}.</p><details><summary>${escapeHtml(t('View methodology'))}</summary><div><p>${escapeHtml(t('Data sources and calculation factors are maintained and approved by Kai Commitment.'))}</p><p>${escapeHtml(t('This calculator does not report waste as a share of food handled yet.'))}</p></div></details></section>
    <div class="result-actions"><button class="button button-secondary" type="button" data-action="start-over">${escapeHtml(t('Start a new calculation'))}</button><button class="button button-secondary" type="button" data-action="download-pdf" ${state.pdfExporting ? 'disabled' : ''}>${escapeHtml(t('Download PDF'))}</button>${state.pdfError ? `<p class="field-error" role="alert">${escapeHtml(state.pdfError)}</p>` : ''}</div>
    ${ImprovementScenario(state)}
    ${ComparisonResults(state)}
    ${contributeBlock(state)}
    ${stepNav({ step: 5, back: 4, backLabel: t('Edit your data'), label: t('Download results'), action: 'download-results' })}
  </section>`
}
