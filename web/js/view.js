import { t } from './i18n.js'

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
  if (!Number.isFinite(number)) return t('Not available')
  const requested = Number(precision)
  const digits = Number.isFinite(requested) ? Math.min(Math.max(Math.trunc(requested), 0), 20) : 2
  // **'en-NZ' stays, in every language.** Section 1.2 puts decimals on the wire as
  // strings because JavaScript's Number is a double, and 7.6.1 leaves the front end no
  // arithmetic but unit conversion; a locale-aware separator would additionally make
  // "1.200,50" and "1,200.50" the same figure written two ways on a page whose whole
  // subject is a number. The interface is translated; the figures are not localised.
  return number.toLocaleString('en-NZ', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export function slug(value) {
  return String(value).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '')
}

/**
 * The two icon shapes the interface draws by hand, and the box their coordinates live in.
 *
 * **They are paths rather than characters because a character's vertical position is a
 * property of the font, and nothing in this repository can hold a font to it.** The
 * disclosure chevron was `⌄` U+2304 DOWN ARROWHEAD and the dismiss was `×` U+00D7, and
 * both are present in Kumbh Sans — `document.fonts.check` says so and the advance width
 * is not the fallback's, so this was never a missing-glyph fallback. It is where the ink
 * sits inside the em. Measured in Kumbh Sans at a 100-unit em, ink relative to the
 * baseline:
 *
 *     glyph           ink top   ink bottom   ink centre
 *     ⌄ U+2304             12      -22          -5   (below the baseline)
 *     × U+00D7             45        4        24.5
 *     x (reference)        49        0        24.5
 *     D (reference)        74        0          37
 *
 * `⌄`'s ink is drawn almost entirely *under* the baseline, so every box a layout can
 * centre — the line box, the inline box, the flex item — puts the arrowhead below the
 * word it labels: 4.88px low on step 1's *Details* button and 5.3px low in a card
 * header, measured on the built page.
 *
 * `×` has the same defect in a milder form, and it is worth stating because it shows the
 * cause is not "a broken glyph". Kumbh Sans is ascent 0.99em / descent 0.25em, so the
 * baseline sits about 80% of the way down the em and the centre of a line box is
 * (0.99 − 0.25) / 2 = 0.37em above the baseline. `×`'s ink is centred 0.245em above it —
 * so any box-centring layout draws it 0.125em low, which on the dismiss control's
 * 1.4rem is the 2.8px measured off the centre of its 44px circle.
 *
 * `x` and `D` are in the table because they are what makes that number legible rather
 * than arbitrary: `x` shares `×`'s ink centre exactly (0.245em), and `D`'s cap centre is
 * 0.37em — the line box's own centre, to the unit. **So centring a box on a line of text
 * puts the box's centre on the cap height of that text**, which is the right answer for
 * an icon beside a capitalised label and is the mechanism the three rules in
 * `styles.css` now use. What it could never be made to do is rescue a glyph whose ink is
 * 22 units under the baseline.
 *
 * A `translateY` would fix either one for one font at one size, which is a number that
 * stops being true the day the font or the `font-size` changes and nothing in the suite
 * would report it. A path has no font metrics to compensate for.
 *
 * **Both shapes are symmetric about the centre of `ICON_BOX`**, so the element's box
 * centre IS the ink's centre and centring the box is all a layout has to do — and
 * `rotate(180deg)` maps the chevron onto itself, which is what lets one shape serve both
 * halves of a disclosure state (see `.step-card__chevron` in `styles.css`).
 *
 * `currentColor`, so the colour rules that already exist — `--muted` on a card
 * chevron, `--green-dark` on the dismiss — keep working with nothing new declared.
 *
 * Here rather than at the three call sites (`calculator.js`'s *Details* button,
 * `cards.js`'s card header, `results.js`'s improvement tip) because the geometry is one
 * decision and the coordinates are meaningless without the viewBox they are in: sharing
 * the path and restating the box at each site is how the two drift apart. Each site
 * still writes its own `class` attribute as a literal, which is what keeps those class
 * names visible to `tests/web/test_class_rules.py`.
 */
export const ICON_BOX = '0 0 24 24'

/** A chevron pointing down, 10 units wide and 6 deep, centred on `ICON_BOX`. */
export const CHEVRON_PATH = '<path d="M 5 9 L 12 15 L 19 9" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'

/** A cross on the diagonals of `ICON_BOX`, centred on it by construction. */
export const CROSS_PATH = '<path d="M 7 7 L 17 17 M 17 7 L 7 17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>'

/**
 * The six screens the wizard names, in order.
 *
 * Lives here rather than in `calculator.js` because `results.js` needs the same
 * vocabulary for its own bar and `calculator.js` already imports `results.js` —
 * importing back would close a cycle for the sake of one array.
 */
export const STEPS = ['Supply-chain stage', 'Food type', 'Waste amount', 'Destinations', 'Review', 'Results']

/** The same six names, in the language being read. Kept apart from `STEPS` so the
 *  English source strings stay the keys and stay in one place. */
export const stepName = index => t(STEPS[index] || '')

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
 * @param {string|null} options.action `data-action` for the primary action, or `null` to
 *   omit the primary button entirely — the results step's own case: its "download"
 *   action moved out of the bar (Task 3, 2026-08-31) to sit beside the PDF button as
 *   an equal-weight choice of format, leaving only the back action here.
 * @returns {string}
 */
export function stepNav({ step, back, backLabel = t('Back'), label = t('Continue'), disabled = false, action = 'continue', backAction = 'go-step' }) {
  const position = t('Step %(step)s of %(total)s', { step: step + 1, total: STEPS.length })
  const percent = Math.round(((step + 1) / STEPS.length) * 100)
  const backButton = back === null || back === undefined
    ? ''
    //: `backAction` (v1.60) is for a Back that moves within a step rather than
    //: between two. Step 2.5 is step 2's second panel, so its Back is a panel
    //: change and carries no `data-step`: a step number there would be read by
    //: `goToStep`, which resets the panel, and the button would do nothing
    //: visible.
    : backAction === 'go-step'
      ? `<button class="button button-secondary" type="button" data-action="go-step" data-step="${back}">${escapeHtml(backLabel)}</button>`
      : `<button class="button button-secondary" type="button" data-action="${backAction}">${escapeHtml(backLabel)}</button>`
  const primaryButton = action === null
    ? ''
    : `<button class="button button-primary" type="button" data-action="${action}" ${disabled ? 'disabled' : ''}>${escapeHtml(label)}</button>`
  return `<div class="step-nav" role="group" aria-label="${escapeHtml(t('Step navigation'))}">
    ${backButton}
    <p class="step-nav-progress" aria-current="step"><span class="step-nav-label">${escapeHtml(position)}</span> · <span class="step-nav-name">${escapeHtml(stepName(step))}</span><span class="step-nav-track" aria-hidden="true"><span style="width:${percent}%"></span></span></p>
    ${primaryButton}
  </div>`
}
