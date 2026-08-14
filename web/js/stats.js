/**
 * The public statistics page. Contract §6.4, §7.4, §7.7.
 *
 * ## Two translation paths, and why the second one has to exist
 *
 * `applyToDocument()` walks `data-i18n` in the DOM, which covers `stats.html`
 * and every element this module builds *as an element*. **It cannot reach a
 * chart.** A Chart.js title (`plugins.title.text`) and the canvas's own
 * `aria-label` are arguments to a constructor, not text nodes: once the chart
 * exists they live inside the instance and are painted onto a bitmap, so no DOM
 * walk can find them and no attribute change would repaint them.
 *
 * So a language change here **destroys every chart and builds it again**. That
 * is what `render()` below does, through the ordinary `renderStats` path:
 * `destroyCharts()` then `createChart` per breakdown, each reading `t()` at
 * construction time. It is a second path and it is deliberate — see
 * `tests/web/test_i18n_browser.py::test_the_charts_are_rebuilt_in_the_new_language`,
 * which reads the title back off the live `Chart` instance in two languages and
 * proves the old instance was destroyed rather than left behind the new one.
 *
 * **The response is held rather than re-fetched.** The figures do not depend on
 * the language, and a page that calls the statistics service every time somebody
 * reads a label in another language is a page that rate-limits itself. Same
 * reasoning as `methodology.js`.
 *
 * ## What stays in English, on purpose
 *
 * **Bucket labels come from the API `label` field** — destination, sector and
 * food-category names that staff type into the panel. §7.7.7 rules that anything
 * a staff member can edit is published exactly as written, so they are never
 * passed through `t()`, in the text list or in a chart legend. A Thai statistics
 * page correctly lists English destination names.
 *
 * **Axis ticks and every figure are numbers**, and §7.7.7 gives them no
 * locale-aware separator on either surface. `integer`, `sharePercent` and the
 * date formatter below are pinned to `en-NZ` for that reason and must stay
 * pinned: making them follow the active language is the tempting change and is
 * the one the rule forbids. Date and number *formats* are O-4, not O-8.
 */

import { ApiError, getStats } from './api.js'
import { renderBar, renderDonut } from './charts.js'
import { applyDocumentLanguage, applyToDocument, installLanguageChooser, t } from './i18n.js'

const chartInstances = new Map()
let latestRequestGeneration = 0

/**
 * The three breakdowns §6.4 publishes.
 *
 * **Every string is a function, not a value**, the same shape
 * `methodology.js::METADATA_FIELDS` uses. A constant would be read once at module
 * evaluation and would still be in the first language after a change; a call
 * evaluated per render is in whatever language is active now. It also keeps every
 * key a literal argument to `t()`, so `tests/web/i18n_keys.py` finds it through
 * its ordinary scan rather than through the indirect list, which is the list its
 * own docstring warns about growing.
 *
 * `chartTitle` is spelled out per breakdown rather than composed as
 * `` `${title} (share)` ``: a suffix glued onto a translated noun phrase is a
 * grammar bet in twenty languages, and three whole sentences cost a translator
 * less than one that only works in English.
 */
const BREAKDOWNS = [
  {
    key: 'by_destination',
    title: () => t('Destinations entered'),
    description: () => t('Share of destination entries across calculations run in this tool.'),
    chart: 'donut',
    chartTitle: () => t('Destinations entered (share)'),
    count: (count) => t('%(count)s destination entries', { count }),
  },
  {
    key: 'by_sector',
    title: () => t('Sectors selected'),
    description: () => t('Share of supply-chain points by the sector selected in this tool.'),
    chart: 'bar',
    chartTitle: () => t('Sectors selected (share)'),
    count: (count) => t('%(count)s supply-chain points', { count }),
  },
  {
    key: 'by_food_category',
    title: () => t('Food categories selected'),
    description: () => t('Share of supply-chain points by the food category entered in this tool.'),
    chart: 'bar',
    chartTitle: () => t('Food categories selected (share)'),
    count: (count) => t('%(count)s supply-chain points', { count }),
  },
]

function element(tag, options = {}) {
  const node = document.createElement(tag)
  if (options.className) node.className = options.className
  if (options.text !== undefined) node.textContent = String(options.text)
  for (const [name, value] of Object.entries(options.attributes || {})) {
    node.setAttribute(name, String(value))
  }
  return node
}

/** `en-NZ` is pinned, not inherited from the active language — §7.7.7. */
function integer(value) {
  const number = Number(value)
  return Number.isFinite(number) ? Math.trunc(number).toLocaleString('en-NZ') : t('Not available')
}

function sharePercent(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return t('Not available')
  return number.toLocaleString('en-NZ', {
    style: 'percent',
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })
}

/** §1.2 decimals cross the wire as strings; §7.7.7 rules that they take no
 *  locale-aware separator on either surface. So the transmitted string is
 *  printed exactly as it arrived.
 *
 *  `toLocaleString('en-NZ', { maximumFractionDigits: 3 })` broke that twice
 *  over. It grouped — `521952.691` rendered as `521,952.691 kg`, the only
 *  place on the public front end still doing so — and it round-tripped through
 *  `Number`, which dropped the significant trailing zero the service sent:
 *  `139132.500` came out as `139,132.5`. Printing the string keeps both the
 *  separator rule and the precision the service published. */
function kilograms(value) {
  if (typeof value !== 'string' || !/^-?\d+(\.\d+)?$/.test(value.trim())) return t('Not available')
  return `${value.trim()} kg`
}

function generatedTime(value) {
  const time = element('time', { attributes: { datetime: String(value || '') } })
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    time.textContent = t('Generation time unavailable')
    return time
  }

  // `en-NZ`, not the active language: a date *format* is O-4 (localisation
  // beyond language), which is open and promises nothing. The sentence around
  // it is translated; the timestamp inside it is not reformatted.
  const local = new Intl.DateTimeFormat('en-NZ', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    timeZoneName: 'short',
  }).format(date)
  time.textContent = t('%(local)s (source timestamp %(iso)s, UTC)', {
    local,
    iso: date.toISOString(),
  })
  return time
}

function destroyChart(key) {
  const chart = chartInstances.get(key)
  if (chart && typeof chart.destroy === 'function') chart.destroy()
  chartInstances.delete(key)
}

export function destroyCharts() {
  for (const key of [...chartInstances.keys()]) destroyChart(key)
}

/**
 * The headline sentence, with its figure still inside a `<strong>`.
 *
 * The sentence is **one key** rather than three fragments concatenated around
 * the number, because a translator handed "Across " and " calculations run in
 * this tool." cannot move the figure — and in several of these twenty languages
 * the number does not sit where English puts it. It is split on the placeholder
 * *after* the lookup and *before* substitution, so the emphasis survives without
 * any markup crossing the catalogue.
 *
 * **It takes the translated sentence, not the key, and that is not a style
 * choice.** Written the other way — `sentenceAround('Across %(count)s …')`, with
 * the `t()` call inside — the literal is an argument to *this* function rather
 * than to `t()`, so `tests/web/i18n_keys.py` never extracts it, no catalogue is
 * required to carry it, and every test passes while the headline of the
 * statistics page renders in English on an Arabic screen. It did, and it was
 * caught by looking at a screenshot rather than by anything in the suite.
 */
function sentenceAround(translated, value, className) {
  const paragraph = element('p', { className })
  const [before, after = ''] = translated.split('%(count)s')
  paragraph.append(before, element('strong', { text: value }), after)
  return paragraph
}

function renderSummary(stats, target) {
  const fragment = document.createDocumentFragment()
  fragment.append(sentenceAround(
    t('Across %(count)s calculations run in this tool.'),
    integer(stats.total_calculations),
    'stats-calculation-total',
  ))

  const generated = element('p')
  generated.append(`${t('Statistics generated:')} `, generatedTime(stats.generated_at))
  fragment.append(generated)

  fragment.append(element('p', {
    text: t(
      'For privacy, buckets containing fewer than %(threshold)s supply-chain points or destination entries are combined by the service. If the combined sample is still too small, that breakdown is not shown.',
      { threshold: integer(stats.suppression_threshold) },
    ),
  }))
  target.replaceChildren(fragment)
}

function renderEquivalentList(rows, definition) {
  const region = element('div', { className: 'stats-list-region' })
  region.append(element('h4', {
    className: 'sr-only',
    text: t('%(title)s data', { title: definition.title() }),
  }))
  const list = element('ul', { className: 'stats-breakdown-list' })

  for (const row of rows) {
    const item = element('li')
    // `row.label` is the name a staff member typed into the panel. §7.7.7: it is
    // published exactly as written and never passed through `t()`.
    const heading = element('strong', { text: row?.label ?? row?.code ?? t('Unlabelled bucket') })
    const details = element('span')
    details.append(
      element('span', { text: t('%(share)s share', { share: sharePercent(row?.share) }) }),
      element('span', { text: definition.count(integer(row?.count)) }),
      element('span', {
        text: t('%(mass)s cumulative quantity entered into this tool', {
          mass: kilograms(row?.total_kg),
        }),
      }),
    )
    item.append(heading, details)
    list.append(item)
  }

  region.append(list)
  return region
}

/**
 * Build one chart. **`title` is read here, at construction**, which is the whole
 * reason a language change has to come back through this function: Chart.js
 * copies it into the instance and paints it, and nothing outside the instance
 * can change it afterwards.
 */
function createChart(key, canvas, rows, definition) {
  destroyChart(key)
  const options = {
    title: definition.chartTitle(),
    labelKey: 'label',
    valueKey: 'share',
    // The same formatter the text list below the chart uses, so the y axis, the
    // tooltip and the list all read `37.9%` rather than the axis reading `0.379`
    // beside a list reading `37.9% share`.
    formatValue: sharePercent,
  }
  const chart = definition.chart === 'donut'
    ? renderDonut(canvas, rows, options)
    : renderBar(canvas, rows, { ...options, allowNegative: false })
  chartInstances.set(key, chart)
}

function renderBreakdown(stats, definition) {
  const section = element('section', {
    className: 'stats-breakdown',
    attributes: { 'aria-labelledby': `${definition.key}-heading` },
  })
  section.append(
    element('h3', { text: definition.title(), attributes: { id: `${definition.key}-heading` } }),
    element('p', { className: 'stats-breakdown-note', text: definition.description() }),
  )

  const rows = Array.isArray(stats[definition.key]) ? stats[definition.key] : []
  if (rows.length === 0) {
    section.append(element('p', {
      className: 'empty-state',
      text: t('Not enough data yet to show this breakdown.'),
      attributes: { role: 'status' },
    }))
    destroyChart(definition.key)
    return section
  }

  const chartRegion = element('div', { className: 'stats-chart-region' })
  // The canvas is a picture to a screen reader, so this attribute is the chart.
  // It is set at build time like the title, and for the same reason is rebuilt
  // rather than re-walked when the language changes.
  const canvas = element('canvas', {
    attributes: {
      role: 'img',
      'aria-label': t(
        '%(title)s, charted using the API-provided share for every published bucket. The full values follow in a text list.',
        { title: definition.title() },
      ),
    },
  })
  chartRegion.append(canvas)
  section.append(chartRegion, renderEquivalentList(rows, definition))
  createChart(definition.key, canvas, rows, definition)
  return section
}

export function renderStats(stats, { summary, breakdowns } = {}) {
  const summaryTarget = summary || document.querySelector('#stats-summary')
  const breakdownTarget = breakdowns || document.querySelector('#stats-breakdown-content')
  if (!summaryTarget || !breakdownTarget) return

  destroyCharts()
  renderSummary(stats || {}, summaryTarget)
  const fragment = document.createDocumentFragment()
  for (const definition of BREAKDOWNS) fragment.append(renderBreakdown(stats || {}, definition))
  breakdownTarget.replaceChildren(fragment)
  breakdownTarget.setAttribute('aria-busy', 'false')
}

export function renderStatsError(error, { summary, breakdowns } = {}) {
  const summaryTarget = summary || document.querySelector('#stats-summary')
  const breakdownTarget = breakdowns || document.querySelector('#stats-breakdown-content')
  destroyCharts()
  if (summaryTarget) {
    summaryTarget.replaceChildren(element('p', {
      className: 'error-state',
      text: error?.message || t('Statistics could not be loaded. Please try again later.'),
      attributes: { role: 'alert' },
    }))
  }
  if (breakdownTarget) {
    breakdownTarget.replaceChildren(element('p', {
      className: 'empty-state',
      text: t('The breakdowns are temporarily unavailable.'),
    }))
    breakdownTarget.setAttribute('aria-busy', 'false')
  }
}

// What the last successful load returned, and what the last failed one said.
// Held so a language change re-renders from them; see the module note on why
// re-fetching would be both correct and wrong.
let latestStats = null
let latestFailure = null

export async function loadStats(options = {}) {
  const generation = ++latestRequestGeneration
  const breakdownTarget = options.breakdowns || document.querySelector('#stats-breakdown-content')
  if (breakdownTarget) breakdownTarget.setAttribute('aria-busy', 'true')

  try {
    const stats = await (options.getStats || getStats)()
    if (generation !== latestRequestGeneration) return null
    latestStats = stats
    latestFailure = null
    renderStats(stats, options)
    return stats
  } catch (error) {
    if (generation !== latestRequestGeneration) return null
    if (!(error instanceof ApiError)) throw error
    latestStats = null
    latestFailure = error
    renderStatsError(error, options)
    return null
  } finally {
    if (generation === latestRequestGeneration && breakdownTarget) {
      breakdownTarget.setAttribute('aria-busy', 'false')
    }
  }
}

/**
 * Redraw everything this page owns in whatever language is now active.
 *
 * **This is the chart-rebuild path**, and it is one line because `renderStats`
 * already destroys and rebuilds: the DOM walk `applyToDocument()` performs
 * covers `stats.html`, and this covers the three charts, their titles and their
 * canvas labels — the strings that are constructor arguments and are therefore
 * out of the DOM walk's reach. Nothing here re-fetches.
 */
export function rerenderInActiveLanguage() {
  if (latestFailure !== null) {
    renderStatsError(latestFailure)
    return
  }
  if (latestStats === null) return
  renderStats(latestStats)
}

if (typeof window !== 'undefined' && typeof document !== 'undefined') {
  const leavePage = () => {
    latestRequestGeneration += 1
    destroyCharts()
  }
  window.addEventListener('pagehide', leavePage)
  window.addEventListener('beforeunload', leavePage)
  if (document.querySelector('#stats-summary') && document.querySelector('#stats-breakdown-content')) {
    applyDocumentLanguage()
    applyToDocument()
    installLanguageChooser(rerenderInActiveLanguage)
    loadStats()
  }
}
