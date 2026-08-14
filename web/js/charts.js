import '../vendor/chart.umd.min.js'

const Chart = globalThis.Chart

if (typeof Chart !== 'function') {
  throw new Error('The local Chart.js runtime could not be loaded')
}

const PALETTE = [
  '#005f73',
  '#0a9396',
  '#94d2bd',
  '#e9d8a6',
  '#ee9b00',
  '#ca6702',
  '#bb3e03',
  '#ae2012',
  '#9b2226',
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

/**
 * Render every API bucket as a segment in a doughnut chart.
 *
 * @param {HTMLCanvasElement} el
 * @param {Array<Record<string, unknown>>} buckets
 * @param {{title?: string, labelKey?: string, valueKey?: string, label?: string, value?: string}} [opts]
 * @returns {Chart} The caller is responsible for calling destroy().
 */
export function renderDonut(el, buckets, opts = {}) {
  const rows = Array.isArray(buckets) ? buckets : []
  const labelKey = optionKey(opts, 'labelKey', 'label', 'label')
  const valueKey = optionKey(opts, 'valueKey', 'value', 'share')

  return new Chart(el, {
    type: 'doughnut',
    data: {
      labels: rows.map((row) => labelFor(row, labelKey)),
      datasets: [{
        label: opts.title || 'Share',
        data: rows.map((row) => chartNumber(row?.[valueKey])),
        backgroundColor: rows.map((_, index) => PALETTE[index % PALETTE.length]),
        borderColor: '#ffffff',
        borderWidth: 2,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: '58%',
      plugins: {
        legend: { position: 'bottom' },
        title: titlePlugin(opts.title),
      },
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
 * @param {{title?: string, allowNegative?: boolean, labelKey?: string, valueKey?: string, label?: string, value?: string}} [opts]
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
      plugins: {
        legend: { display: false },
        title: titlePlugin(opts.title),
      },
      scales: {
        y: {
          beginAtZero: true,
          suggestedMin,
        },
      },
    },
  })
}
