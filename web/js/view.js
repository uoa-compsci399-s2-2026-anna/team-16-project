export function escapeHtml(value = '') {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;')
}

/**
 * An API figure printed at the metric's own precision.
 *
 * §6.1's `display_precision` supplies **both** bounds, and only the upper one was set here:
 * a cost of exactly `825.00` rendered as "825" at `display_precision: 2`, one row above
 * "1,204.50" in the same column of money. §7.3a recorded it as a known gap.
 *
 * The digits are clamped to Intl's legal 0–20 because the value arrives from the database
 * (§2.1 `metric.display_precision`) rather than from this file, and an out-of-range one
 * makes `toLocaleString` throw a RangeError — which would take out the whole render rather
 * than one figure.
 */
export function formatNumber(value, precision = 2) {
  const number = Number(value)
  if (!Number.isFinite(number)) return 'Not available'
  const requested = Number(precision)
  const digits = Number.isFinite(requested) ? Math.min(Math.max(Math.trunc(requested), 0), 20) : 2
  return number.toLocaleString('en-NZ', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export function slug(value) {
  return String(value).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '')
}

export function buttonRow(backStep, label = 'Continue', disabled = false, action = 'continue') {
  return `<div class="navigation-buttons">
    <button class="button button-secondary" type="button" data-action="go-step" data-step="${backStep}">Back</button>
    <button class="button button-primary" type="button" data-action="${action}" ${disabled ? 'disabled' : ''}>${escapeHtml(label)}</button>
  </div>`
}
