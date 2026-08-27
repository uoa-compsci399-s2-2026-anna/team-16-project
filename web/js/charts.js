/**
 * Chart.js adapters for the public statistics page. Contract §7.4.
 *
 * Two exports, both returning a Chart the caller is responsible for destroying.
 * Neither computes anything: every value drawn here arrives from the API as a
 * decimal string and is converted with `Number()` for display and charting only,
 * which is the one conversion §1.2 permits.
 */

import '../vendor/chart.umd.min.js'

const Chart = globalThis.Chart

if (typeof Chart !== 'function') {
  throw new Error('The local Chart.js runtime could not be loaded')
}

const KALE = '#003223'
const WHITE = '#FFFFFF'

/**
 * The categorical palette, derived entirely from the brand.
 *
 * **Sixteen entries, and the number is not arbitrary.** The palette this
 * replaces held nine and was indexed `PALETTE[index % PALETTE.length]`, so a
 * tenth bucket silently repeated the first one's colour — measured, `Landfill`
 * and `Other (sample too small)` were both `#005f73` in the same doughnut. That
 * is not an edge case: the NZ taxonomy carries fourteen destinations, `other` is
 * an ordinary bucket on top of them, and the canonical fixture already produces
 * ten surviving buckets. `test_the_palette_covers_the_taxonomy` pins the length
 * against `tests/fixtures/taxonomy.json`, so the wrap below is unreachable by
 * contract rather than by hope.
 *
 * **Every colour is a brand colour or a mix of two of them.** The seven from the
 * guidelines, then tints mixed 45% toward White and shades mixed 45% toward
 * Kale. Nothing here is invented: the previous palette's `#0a9396` and `#005f73`
 * appear nowhere in the brand, on a page that uses the `--kai-*` tokens
 * correctly everywhere else. Chosen greedily for maximum perceptual separation —
 * the minimum pairwise CIE76 ΔE across all sixteen is 27.4, and no two adjacent
 * entries are closer than 42, against a just-noticeable threshold near 10.
 *
 * **`ink` is the brand's text-colour rule, carried with the colour rather than
 * remembered.** For the seven brand colours it is the guidelines' own
 * classification: Kale, Blueberry and Orange are dark grounds and take White;
 * Pea, Banana and Lavender are light grounds and take Kale. (Beetroot the
 * guidelines do not classify; at 9.64:1 against White and 1.47:1 against Kale
 * it is a dark ground by any reading.) For the mixes, which the guidelines do
 * not cover, it is whichever gives the higher WCAG contrast, and each mix was
 * chosen at a ratio that clears 4.5:1 against its own ink.
 *
 * **Orange is the one entry below that floor** — 3.26:1 with White — and it is
 * the brand's own pairing rather than a choice made here, so the test names it
 * as the single stated exception instead of quietly lowering the bar for
 * everything.
 *
 * `ink` is load-bearing, not recorded: `renderDonut` paints the tooltip on the
 * hovered segment's own fill and takes that segment's ink, and
 * `test_every_palette_ink_follows_the_brand_rule` recomputes the contrast from
 * the hex, so an `ink` edited out of step with its `fill` fails rather than
 * drawing white text on Banana.
 */
export const PALETTE = [
  { fill: '#003223', ink: WHITE },  // Kale
  { fill: '#FF5032', ink: WHITE },  // Orange
  { fill: '#005AE6', ink: WHITE },  // Blueberry
  { fill: '#FFD76E', ink: KALE },   // Banana
  { fill: '#5994EF', ink: KALE },   // Blueberry, 35% toward White
  { fill: '#28C882', ink: KALE },   // Pea
  { fill: '#87005A', ink: WHITE },  // Beetroot
  { fill: '#C9F1E0', ink: KALE },   // Pea, 75% toward White
  { fill: '#44193E', ink: WHITE },  // Beetroot, 50% toward Kale
  { fill: '#FFEBB6', ink: KALE },   // Banana, 50% toward White
  { fill: '#00447B', ink: WHITE },  // Blueberry, 55% toward Kale
  { fill: '#FFB0A3', ink: KALE },   // Orange, 55% toward White
  { fill: '#506370', ink: WHITE },  // Lavender, 65% toward Kale
  { fill: '#E6BEFF', ink: KALE },   // Lavender
  { fill: '#667441', ink: WHITE },  // Banana, 60% toward Kale
  { fill: '#80412A', ink: WHITE },  // Orange, 50% toward Kale
]

/** The single-series bar colour. One series across categories carries no
 *  information in its colour, so it takes one brand colour rather than a
 *  rainbow that would invite a reader to look for a meaning that is not there. */
const BAR = { fill: '#005AE6', ink: WHITE }

/**
 * The plot area the doughnut is guaranteed, above whatever the legend needs.
 *
 * The stylesheet's `min-height: 320px` on `.stats-chart-region` was a fixed
 * surface for a legend of unknown length, and `maintainAspectRatio: false`
 * means the canvas is exactly that surface. Measured at 390px with the
 * canonical ten-bucket `by_destination` fixture, `legendHitBoxes[9]` sat at
 * `top: 324` on a 320px canvas — the tenth entry drawn past the bottom edge,
 * invisible, with no scrollbar to reach it. The clipped bucket was
 * `Other (sample too small)`, which §6.4 devotes a paragraph to precisely
 * because it will be there on the day the calculator opens.
 */
const MIN_PLOT_HEIGHT = 260

/**
 * Grow the chart's container to fit the legend Chart.js actually laid out.
 *
 * Reading the measured `legend.height` rather than guessing a per-row constant
 * is what makes this hold for any bucket count, at any width, in any language —
 * an Arabic legend wraps differently from an English one and neither is
 * predictable from the row count. It converges because legend height is a
 * function of the container's *width*, which this never changes.
 */
const fitLegend = {
  id: 'kaiFitLegend',
  afterUpdate(chart) {
    const region = chart.canvas?.parentElement
    if (!region) return
    const shown = chart.options?.plugins?.legend?.display !== false
    const legendHeight = shown && chart.legend ? Math.ceil(chart.legend.height) : 0
    const wanted = MIN_PLOT_HEIGHT + legendHeight
    const current = Math.round(Number.parseFloat(region.style.minHeight)) || 0
    if (Math.abs(current - wanted) >= 1) region.style.minHeight = `${wanted}px`
  },
}

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
    color: KALE,
  }
}

/** The palette entry for a bucket. The modulo is a floor, not a plan — see
 *  PALETTE's note and the test that keeps it unreachable. */
function paletteEntry(index) {
  return PALETTE[index % PALETTE.length]
}

/** `formatValue` is the caller's, and defaults to the plain number so a chart
 *  of factor values is unaffected by the share formatting the statistics page
 *  needs. */
function formatter(opts) {
  return typeof opts.formatValue === 'function' ? opts.formatValue : value => String(value)
}

/**
 * Shrink one legend label to fit beside its swatch, never past the canvas
 * itself. Chart.js lays out legend rows to the container's width, but it does
 * not wrap or truncate the text *inside* one entry: a single label wider than
 * the whole legend row (`db/repository.py`'s `standard_mix` bucket,
 * "Mixed food waste (composition unknown)", is 39 characters and reliably
 * wider than a 320px-viewport canvas) is still centred on that row, which
 * pushes it past both edges rather than one. Measured at 320x700: the doughnut
 * legend for the food-category breakdown drew that entry's right edge at
 * 244px on a 222px canvas.
 *
 * The full label is never lost: it is still the tooltip (`context.label`
 * below reads the untouched `data.labels`), the `aria-label`, and every row of
 * `renderEquivalentList`. Only the on-canvas swatch text is shortened, the
 * same trade-off any dashboard legend makes for a name too long to sit next
 * to its colour.
 */
function truncateForLegend(ctx, text, maxWidth) {
  if (maxWidth <= 0 || ctx.measureText(text).width <= maxWidth) return text
  const ellipsis = '…'
  let low = 0
  let high = text.length
  while (low < high) {
    const mid = Math.ceil((low + high) / 2)
    const candidate = `${text.slice(0, mid).trimEnd()}${ellipsis}`
    if (ctx.measureText(candidate).width <= maxWidth) low = mid
    else high = mid - 1
  }
  return low > 0 ? `${text.slice(0, low).trimEnd()}${ellipsis}` : ellipsis
}

/**
 * A legend's `generateLabels`, built rather than borrowed from Chart.js's own
 * arc default: that default has no width to measure against and no reason to
 * truncate anything. `chart.width` is the canvas's own CSS width, the same
 * figure `fitLegend` reads off `getBoundingClientRect` -- so the bound this
 * enforces is the canvas actually on screen, at whatever viewport it is.
 */
function donutLegendLabels(chart) {
  const dataset = chart.data.datasets[0]
  if (!dataset) return []
  const ctx = chart.ctx
  const font = Chart.helpers.toFont(chart.legend?.options?.labels?.font || {})
  const swatchAndGapWidth = 50
  const maxWidth = Math.max(0, chart.width - swatchAndGapWidth)
  ctx.save()
  ctx.font = font.string
  const labels = chart.data.labels.map((label, index) => ({
    text: truncateForLegend(ctx, String(label ?? ''), maxWidth),
    fillStyle: dataset.backgroundColor[index],
    strokeStyle: dataset.borderColor,
    lineWidth: dataset.borderWidth,
    hidden: !chart.getDataVisibility(index),
    index,
  }))
  ctx.restore()
  return labels
}

/**
 * Render every API bucket as a segment in a doughnut chart.
 *
 * @param {HTMLCanvasElement} el
 * @param {Array<Record<string, unknown>>} buckets
 * @param {{title?: string, labelKey?: string, valueKey?: string, label?: string, value?: string,
 *          formatValue?: (value: number) => string}} [opts]
 * @returns {Chart} The caller is responsible for calling destroy().
 */
export function renderDonut(el, buckets, opts = {}) {
  const rows = Array.isArray(buckets) ? buckets : []
  const labelKey = optionKey(opts, 'labelKey', 'label', 'label')
  const valueKey = optionKey(opts, 'valueKey', 'value', 'share')
  const format = formatter(opts)
  const entries = rows.map((_, index) => paletteEntry(index))

  return new Chart(el, {
    type: 'doughnut',
    plugins: [fitLegend],
    data: {
      labels: rows.map((row) => labelFor(row, labelKey)),
      datasets: [{
        label: opts.title || 'Share',
        data: rows.map((row) => chartNumber(row?.[valueKey])),
        backgroundColor: entries.map((entry) => entry.fill),
        borderColor: '#ffffff',
        borderWidth: 2,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: '58%',
      plugins: {
        legend: { position: 'bottom', labels: { color: KALE, generateLabels: donutLegendLabels } },
        title: titlePlugin(opts.title),
        // Painted on the hovered segment's own fill, with that segment's ink.
        // This is where the brand's dark-ground/light-ground text rule is
        // actually applied rather than merely recorded.
        tooltip: {
          backgroundColor: (context) => paletteEntry(context.tooltip?.dataPoints?.[0]?.dataIndex ?? 0).fill,
          titleColor: (context) => paletteEntry(context.tooltip?.dataPoints?.[0]?.dataIndex ?? 0).ink,
          bodyColor: (context) => paletteEntry(context.tooltip?.dataPoints?.[0]?.dataIndex ?? 0).ink,
          borderColor: KALE,
          borderWidth: 1,
          displayColors: false,
          callbacks: {
            label: (context) => `${context.label}: ${format(context.parsed)}`,
          },
        },
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
 * `formatValue` formats the y-axis ticks **and** the tooltip, together. The
 * axis used to read `0`, `0.05` … `0.40` immediately above a text list reading
 * `37.9% share` — one number in two units on one card, which invites a reader
 * to think they are two figures. Formatting one and not the other would only
 * move the contradiction into the hover.
 *
 * @param {HTMLCanvasElement} el
 * @param {Array<Record<string, unknown>>} rows
 * @param {{title?: string, allowNegative?: boolean, labelKey?: string, valueKey?: string,
 *          label?: string, value?: string, formatValue?: (value: number) => string}} [opts]
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
  const format = formatter(opts)

  return new Chart(el, {
    type: 'bar',
    plugins: [fitLegend],
    data: {
      labels: dataRows.map((row) => labelFor(row, labelKey)),
      datasets: [{
        label: opts.title || 'Value',
        data: values,
        backgroundColor: BAR.fill,
        borderColor: KALE,
        borderWidth: 1,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        title: titlePlugin(opts.title),
        tooltip: {
          backgroundColor: BAR.fill,
          titleColor: BAR.ink,
          bodyColor: BAR.ink,
          borderColor: KALE,
          borderWidth: 1,
          displayColors: false,
          callbacks: {
            label: (context) => `${context.label}: ${format(context.parsed.y)}`,
          },
        },
      },
      scales: {
        x: { ticks: { color: KALE } },
        y: {
          beginAtZero: true,
          suggestedMin,
          ticks: { color: KALE, callback: (value) => format(value) },
        },
      },
    },
  })
}
