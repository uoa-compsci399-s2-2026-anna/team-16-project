import { ApiError, getStats } from './api.js'
import { renderBar, renderDonut } from './charts.js'

const chartInstances = new Map()
let latestRequestGeneration = 0

const BREAKDOWNS = [
  {
    key: 'by_destination',
    title: 'Destinations entered',
    description: 'Share of destination entries across calculations run in this tool.',
    chart: 'donut',
    countLabel: 'destination entries',
  },
  {
    key: 'by_sector',
    title: 'Sectors selected',
    description: 'Share of supply-chain points by the sector selected in this tool.',
    chart: 'bar',
    countLabel: 'supply-chain points',
  },
  {
    key: 'by_food_category',
    title: 'Food categories selected',
    description: 'Share of supply-chain points by the food category entered in this tool.',
    chart: 'bar',
    countLabel: 'supply-chain points',
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

function integer(value) {
  const number = Number(value)
  return Number.isFinite(number) ? Math.trunc(number).toLocaleString('en-NZ') : 'Not available'
}

function sharePercent(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return 'Not available'
  return number.toLocaleString('en-NZ', {
    style: 'percent',
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })
}

function kilograms(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return 'Not available'
  return `${number.toLocaleString('en-NZ', { maximumFractionDigits: 3 })} kg`
}

function generatedTime(value) {
  const time = element('time', { attributes: { datetime: String(value || '') } })
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    time.textContent = 'Generation time unavailable'
    return time
  }

  const local = new Intl.DateTimeFormat('en-NZ', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    timeZoneName: 'short',
  }).format(date)
  time.textContent = `${local} (source timestamp ${date.toISOString()}, UTC)`
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

function renderSummary(stats, target) {
  const calculations = integer(stats.total_calculations)
  const threshold = integer(stats.suppression_threshold)
  const fragment = document.createDocumentFragment()
  const headline = element('p', { className: 'stats-calculation-total' })
  headline.append('Across ', element('strong', { text: calculations }), ' calculations run in this tool.')
  fragment.append(headline)

  const generated = element('p')
  generated.append('Statistics generated: ', generatedTime(stats.generated_at))
  fragment.append(generated)

  fragment.append(element('p', {
    text: `For privacy, buckets containing fewer than ${threshold} supply-chain points or destination entries are combined by the service. If the combined sample is still too small, that breakdown is not shown.`,
  }))
  target.replaceChildren(fragment)
}

function renderEquivalentList(rows, definition) {
  const region = element('div', { className: 'stats-list-region' })
  region.append(element('h4', { className: 'sr-only', text: `${definition.title} data` }))
  const list = element('ul', { className: 'stats-breakdown-list' })

  for (const row of rows) {
    const item = element('li')
    const heading = element('strong', { text: row?.label ?? row?.code ?? 'Unlabelled bucket' })
    const details = element('span')
    details.append(
      element('span', { text: `${sharePercent(row?.share)} share` }),
      element('span', { text: `${integer(row?.count)} ${definition.countLabel}` }),
      element('span', { text: `${kilograms(row?.total_kg)} cumulative quantity entered into this tool` }),
    )
    item.append(heading, details)
    list.append(item)
  }

  region.append(list)
  return region
}

function createChart(key, canvas, rows, definition) {
  destroyChart(key)
  const options = {
    title: `${definition.title} (share)`,
    labelKey: 'label',
    valueKey: 'share',
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
    element('h3', { text: definition.title, attributes: { id: `${definition.key}-heading` } }),
    element('p', { className: 'stats-breakdown-note', text: definition.description }),
  )

  const rows = Array.isArray(stats[definition.key]) ? stats[definition.key] : []
  if (rows.length === 0) {
    section.append(element('p', {
      className: 'empty-state',
      text: 'Not enough data yet to show this breakdown.',
      attributes: { role: 'status' },
    }))
    destroyChart(definition.key)
    return section
  }

  const chartRegion = element('div', { className: 'stats-chart-region' })
  const canvas = element('canvas', {
    attributes: {
      role: 'img',
      'aria-label': `${definition.title}, charted using the API-provided share for every published bucket. The full values follow in a text list.`,
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
      text: error?.message || 'Statistics could not be loaded. Please try again later.',
      attributes: { role: 'alert' },
    }))
  }
  if (breakdownTarget) {
    breakdownTarget.replaceChildren(element('p', {
      className: 'empty-state',
      text: 'The breakdowns are temporarily unavailable.',
    }))
    breakdownTarget.setAttribute('aria-busy', 'false')
  }
}

export async function loadStats(options = {}) {
  const generation = ++latestRequestGeneration
  const breakdownTarget = options.breakdowns || document.querySelector('#stats-breakdown-content')
  if (breakdownTarget) breakdownTarget.setAttribute('aria-busy', 'true')

  try {
    const stats = await (options.getStats || getStats)()
    if (generation !== latestRequestGeneration) return null
    renderStats(stats, options)
    return stats
  } catch (error) {
    if (generation !== latestRequestGeneration) return null
    if (!(error instanceof ApiError)) throw error
    renderStatsError(error, options)
    return null
  } finally {
    if (generation === latestRequestGeneration && breakdownTarget) {
      breakdownTarget.setAttribute('aria-busy', 'false')
    }
  }
}

if (typeof window !== 'undefined' && typeof document !== 'undefined') {
  const leavePage = () => {
    latestRequestGeneration += 1
    destroyCharts()
  }
  window.addEventListener('pagehide', leavePage)
  window.addEventListener('beforeunload', leavePage)
  if (document.querySelector('#stats-summary') && document.querySelector('#stats-breakdown-content')) {
    loadStats()
  }
}
