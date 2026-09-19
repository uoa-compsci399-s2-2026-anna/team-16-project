import '../vendor/chart.umd.min.js'

const Chart = globalThis.Chart

if (typeof Chart !== 'function') {
  throw new Error('The local Chart.js runtime could not be loaded')
}

const SHARE_FORMATTER = new Intl.NumberFormat('en-NZ', {
  style: 'percent',
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
})

const PIE_PALETTE = [
  '#005f73',
  '#0a9396',
  '#94d2bd',
  '#e9d8a6',
  '#ee9b00',
  '#ca6702',
  '#bb3e03',
  '#ae2012',
  '#9b2226',
  '#3a0ca3',
  '#4361ee',
  '#4cc9f0',
  '#6a994e',
]

function optionKey(opts, primary, alias, fallback) {
  const value = opts[primary] ?? opts[alias]
  return typeof value === 'string' && value.length > 0 ? value : fallback
}

function chartNumber(value) {
  if ((typeof value !== 'number' && typeof value !== 'string') || String(value).trim() === '') {
    return null
  }

  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

function labelFor(row, key) {
  const value = row?.[key]
  return value == null ? '' : String(value)
}

function titlePlugin(title) {
  return {
    display: typeof title === 'string' && title.length > 0,
    text: title,
  }
}

function prefersReducedMotion() {
  return typeof globalThis.matchMedia === 'function'
    && globalThis.matchMedia('(prefers-reduced-motion: reduce)').matches
}

function animationOptions() {
  return prefersReducedMotion() ? { animation: false } : {}
}

function stableHash(value) {
  let hash = 2166136261
  for (const character of String(value)) {
    hash ^= character.codePointAt(0)
    hash = Math.imul(hash, 16777619)
  }
  return hash >>> 0
}

function colourKey(row, label) {
  const code = row?.code
  return code != null && String(code).length > 0
    ? `code:${String(code)}`
    : `label:${label}`
}

function extendedColour(index) {
  const hue = ((index - PIE_PALETTE.length) * 137.50776405003785) % 360
  const lightness = 40 + (Math.floor((index - PIE_PALETTE.length) / 12) % 3) * 7
  return `hsl(${hue.toFixed(3)} 68% ${lightness}%)`
}

function coloursForRows(rows, labels) {
  const keys = rows.map((row, index) => colourKey(row, labels[index]))
  const sortedKeys = [...new Set(keys)].sort()
  const coloursByKey = new Map()
  const usedSlots = new Set()

  for (const [index, key] of sortedKeys.entries()) {
    if (index >= PIE_PALETTE.length) {
      coloursByKey.set(key, extendedColour(index))
      continue
    }

    let slot = stableHash(key) % PIE_PALETTE.length
    while (usedSlots.has(slot)) slot = (slot + 1) % PIE_PALETTE.length
    usedSlots.add(slot)
    coloursByKey.set(key, PIE_PALETTE[slot])
  }

  return keys.map((key) => coloursByKey.get(key))
}

function shareTooltip(context) {
  const label = context?.label == null ? '' : String(context.label)
  const value = chartNumber(context?.raw)
  const formatted = value === null ? 'Not available' : SHARE_FORMATTER.format(value)
  return label ? `${label}: ${formatted}` : formatted
}

function percentTicks(value) {
  const number = chartNumber(value)
  return number === null ? String(value) : SHARE_FORMATTER.format(number)
}

/**
 * Render every API bucket as a segment in a pie chart.
 *
 * @param {HTMLCanvasElement} el
 * @param {Array<Record<string, unknown>>} buckets
 * @param {{title?: string, labelKey?: string, valueKey?: string, label?: string, value?: string}} [opts]
 * @returns {Chart} The caller is responsible for calling destroy().
 */
export function renderPie(el, buckets, opts = {}) {
  const rows = Array.isArray(buckets) ? buckets : []
  const labelKey = optionKey(opts, 'labelKey', 'label', 'label')
  const valueKey = optionKey(opts, 'valueKey', 'value', 'share')
  const labels = rows.map((row) => labelFor(row, labelKey))

  return new Chart(el, {
    type: 'pie',
    data: {
      labels,
      datasets: [{
        label: opts.title || 'Share',
        data: rows.map((row) => chartNumber(row?.[valueKey])),
        backgroundColor: coloursForRows(rows, labels),
        borderColor: '#ffffff',
        borderWidth: 2,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: 'bottom',
          labels: { boxWidth: 11, padding: 8, font: { size: 11 } },
        },
        title: titlePlugin(opts.title),
        tooltip: { callbacks: { label: shareTooltip } },
      },
      ...animationOptions(),
    },
  })
}

/**
 * Render API values as a bar chart without changing their sign.
 *
 * `allowNegative` affects the scale hint only. Values are never clipped or
 * converted to their absolute value, even when the option is false.
 *
 * @param {HTMLCanvasElement} el
 * @param {Array<Record<string, unknown>>} rows
 * @param {{title?: string, allowNegative?: boolean, labelKey?: string, valueKey?: string, label?: string, value?: string, valueFormat?: 'percent'}} [opts]
 * @returns {Chart} The caller is responsible for calling destroy().
 */
export function renderBar(el, rows, opts = {}) {
  const dataRows = Array.isArray(rows) ? rows : []
  const labelKey = optionKey(opts, 'labelKey', 'label', 'label')
  const valueKey = optionKey(opts, 'valueKey', 'value', 'value')
  const values = dataRows.map((row) => chartNumber(row?.[valueKey]))
  const finiteValues = values.filter((value) => value !== null)
  const allowNegative = opts.allowNegative !== false
  const suggestedMin = allowNegative ? Math.min(0, ...finiteValues) : 0
  const percent = opts.valueFormat === 'percent'

  return new Chart(el, {
    type: 'bar',
    data: {
      labels: dataRows.map((row) => labelFor(row, labelKey)),
      datasets: [{
        label: opts.title || 'Value',
        data: values,
        backgroundColor: '#0a9396',
        borderColor: '#005f73',
        borderWidth: 1,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      ...animationOptions(),
      plugins: {
        legend: { display: false },
        title: titlePlugin(opts.title),
        ...(percent ? { tooltip: { callbacks: { label: shareTooltip } } } : {}),
      },
      scales: {
        y: {
          beginAtZero: true,
          suggestedMin,
          ...(percent ? { ticks: { callback: percentTicks } } : {}),
        },
      },
    },
  })
}

/**
 * Render API values in their supplied category order on a discrete x-axis.
 * The connecting line compares adjacent categories; it is not a time series.
 *
 * @param {HTMLCanvasElement} el
 * @param {Array<Record<string, unknown>>} rows
 * @param {{title?: string, labelKey?: string, valueKey?: string, label?: string, value?: string, valueFormat?: 'percent'}} [opts]
 * @returns {Chart} The caller is responsible for calling destroy().
 */
export function renderLine(el, rows, opts = {}) {
  const dataRows = Array.isArray(rows) ? rows : []
  const labelKey = optionKey(opts, 'labelKey', 'label', 'label')
  const valueKey = optionKey(opts, 'valueKey', 'value', 'value')
  const values = dataRows.map((row) => chartNumber(row?.[valueKey]))
  const finiteValues = values.filter((value) => value !== null)
  const percent = opts.valueFormat === 'percent'

  return new Chart(el, {
    type: 'line',
    data: {
      labels: dataRows.map((row) => labelFor(row, labelKey)),
      datasets: [{
        label: opts.title || 'Value',
        data: values,
        borderColor: '#005f73',
        backgroundColor: '#0a9396',
        borderWidth: 2,
        pointRadius: 4,
        pointHoverRadius: 5,
        fill: false,
        tension: 0,
        spanGaps: false,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      ...animationOptions(),
      plugins: {
        legend: { display: false },
        title: titlePlugin(opts.title),
        ...(percent ? { tooltip: { callbacks: { label: shareTooltip } } } : {}),
      },
      scales: {
        x: { type: 'category', ticks: { autoSkip: true, maxRotation: 0 } },
        y: {
          beginAtZero: true,
          suggestedMin: Math.min(0, ...finiteValues),
          ...(percent ? { ticks: { callback: percentTicks } } : {}),
        },
      },
    },
  })
}
