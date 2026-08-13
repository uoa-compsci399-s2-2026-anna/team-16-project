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

/**
 * The six screens the wizard names, in order.
 *
 * Lives here rather than in `calculator.js` because `results.js` needs the same
 * vocabulary for its own bar and `calculator.js` already imports `results.js` —
 * importing back would close a cycle for the sake of one array.
 */
export const STEPS = ['Supply-chain stage', 'Food type', 'Waste amount', 'Destinations', 'Review', 'Results']

/**
 * The step navigation bar: a step's Back and primary actions, and its position
 * in the flow, as one `position: sticky; bottom: 0` element.
 *
 * **This is the whole of the step chrome.** It replaced two things at once — the
 * `.navigation-buttons` row, which was ordinary content and so sat wherever the
 * form's length put it (2,961px past the fold on the food-type step at
 * 1278x983), and the `#step-indicator` band above `<main>`, which cost 87px at
 * the top of every step for a list of names nothing could click. Folding the
 * position label in here is what buys those 87px back for the form, which is
 * the point: one more row of content fitting is one less step that has to be
 * scrolled.
 *
 * **`sticky`, never `fixed`.** Sticky sits in its natural place when the step is
 * short and pins only when the section would push it past the fold, so a short
 * step is not made to look like it has a cookie banner. It is also the half of
 * the pair that survives a mobile soft keyboard: sticky is in flow, so it moves
 * with the layout viewport instead of being stranded behind the keyboard.
 *
 * **It must stay the last child of the step's `<section>`.** Two reasons, both
 * load-bearing: Tab has to reach the form before the navigation, and sticky
 * bottom pins only for as long as the *containing block* extends below the fold
 * — put anything after this element and the bar unpins early, above whatever
 * follows it.
 *
 * @param {object} options
 * @param {number} options.step Zero-based index into `STEPS`; sets the label and the progress track.
 * @param {number|null} options.back `data-step` for the Back button, `null` to omit it.
 * @param {string} options.backLabel Text for the secondary action.
 * @param {string} options.label Text for the primary action.
 * @param {boolean} options.disabled Whether the primary action is disabled.
 * @param {string} options.action `data-action` for the primary action.
 * @returns {string}
 */
export function stepNav({ step, back, backLabel = 'Back', label = 'Continue', disabled = false, action = 'continue' }) {
  const position = `Step ${step + 1} of ${STEPS.length}`
  const percent = Math.round(((step + 1) / STEPS.length) * 100)
  const backButton = back === null || back === undefined
    ? ''
    : `<button class="button button-secondary" type="button" data-action="go-step" data-step="${back}">${escapeHtml(backLabel)}</button>`
  return `<div class="step-nav" role="group" aria-label="Step navigation">
    ${backButton}
    <p class="step-nav-progress" aria-current="step"><span class="step-nav-label">${escapeHtml(position)}</span> <span class="step-nav-name">${escapeHtml(STEPS[step] || '')}</span><span class="step-nav-track" aria-hidden="true"><span style="width:${percent}%"></span></span></p>
    <button class="button button-primary" type="button" data-action="${action}" ${disabled ? 'disabled' : ''}>${escapeHtml(label)}</button>
  </div>`
}
