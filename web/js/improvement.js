import { calculate } from './api.js'
import { setState, draftEntry } from './state.js'
import { rowKgString, percentageToKg, kgToPercentage } from './units.js'
import { requestLines, submissionPayload } from './submission.js'
import { escapeHtml, formatNumber, slug } from './view.js'
import { t } from './i18n.js'

// Display-only coercion of an API decimal string (§7.6.1). `Number(value) || 0` stood here
// and made "the engine did not return this figure", "this figure is malformed" and "this
// figure is zero" indistinguishable on screen; an absent figure has to read as absent,
// which is what `formatNumber` does with a non-finite input.
const number = value => (value === null || value === undefined || value === '' ? Number.NaN : Number(value))

// Arithmetic on what the user typed, not on an API figure. A blank destination row is a
// zero here, and that is the only reason a `|| 0` is correct anywhere in this module.
const typed = value => Number(value) || 0

// §4.5's two-decimal NZD string, displayed. A local copy rather than an import, for the
// reason `number`, `typed` and `MASS_METRIC` above are local copies: `results.js` already
// imports this module and the import back would close a cycle.
//
// **`hasValue` is not `Boolean(value)` and must never be relaxed into it.** §4.5 makes
// every money field `null` unless everything it derives from was supplied, and `null` has
// to render as *nothing* — a computed zero would read as "you saved nothing" when what
// happened is that nobody priced the food. `nzd(null)` prints `NZ$Not available`, because
// `formatNumber` answers a non-finite input that way, so a guard that lets `null` through
// puts that string on a client-facing page. `tests/web/test_improvement_saving_browser.py`
// drives the unpriced journey against the real API for exactly this.
const nzd = value => `NZ$${formatNumber(number(value), 2)}`
const hasValue = value => value !== null && value !== undefined

const sorted = items => [...(items || [])].sort((a, b) => Number(a.sort_order || 0) - Number(b.sort_order || 0))

// §7.3: `units.js` holds the front end's only arithmetic, and this reads it rather than
// re-deriving it. **With the row's own unit, not the entry's** — `massToKg(qtyInput,
// entry.totalUnit)` stood here, which reinterpreted a tonnes row as kilograms and applied no
// container preset at all, so two rows of equal mass seeded the sliders at 99.88% and 0.12%.
// `|| 0` preserves the `typed()` behaviour it replaces: a blank or unusable box counts as
// nothing rather than poisoning the running total with NaN.
const lineKg = (entry, line, presets) => Number(rowKgString(line.qtyInput, line.unit || entry.totalUnit, presets)) || 0
const sumQtyKg = lines => lines.reduce((sum, line) => sum + typed(line.qty_kg), 0)

// The donut's palette, brand colours first. It is display only: every slice is a share
// this module already holds, so the chart reads `improvedAllocations` and computes nothing
// the panel does not already show as a number beside its slider (§7.6.1).
const PIE_COLOURS = ['#003223', '#28c882', '#87005a', '#ffd76e', '#005ae6', '#e6beff', '#ff5032', '#5c7c70', '#9b6bc1', '#86a83e']

// §6.2 rejects an entry whose two scenarios differ in mass by more than this, and it
// rejects the whole submission rather than the entry.
const MASS_TOLERANCE_KG = 0.01

// The one metric code this module names, and it is not the hard-coded list §7.6.5 forbids:
// that rule exists because a view listing `['co2e','water','cost']` *omits* the metric a
// staff member inserted, and every metric the response carries still appears here. `mass` is
// held out because §6.2 requires an entry's two scenarios to describe the same mass, so its
// `net_benefit` is zero by construction — "Mass: No change" on every comparison, in a list
// whose subject is what changed.
const MASS_METRIC = 'mass'

// §7.6.6 in the one place on the page where a sign is a *direction*: `net_benefit` is
// `current − alternative` (§3), so positive means the improved scenario is lower and
// negative means it is higher.
//
// The class names say which way the IMPACT moved, not which way the number leans, and the
// stylesheet's arrows follow the name: a positive net benefit is a saving, so the impact
// fell, so `.change-down`. The classes this used — `.value-positive` / `.value-negative` /
// `.value-zero` — named the sign, and `.value-negative` was doing double duty for a figure
// that is merely negative (see `results.js`), which is how an up arrow ended up beside
// "1,104.0 kg CO2e saved". The two meanings are now two sets of classes.
const signClass = value => {
  if (!Number.isFinite(value)) return ''
  if (Math.abs(value) < 1e-9) return 'change-none'
  return value > 0 ? 'change-down' : 'change-up'
}

// The codes to render, in the response's own key order. That order is already display order:
// §4.1 defines `FactorBundle.metrics` as sorted by `sort_order` and the engine iterates it to
// build `metrics`, so no sort belongs here — and no list of codes does either (§7.6.5).
const comparableCodes = metrics => Object.keys(metrics).filter(code => code !== MASS_METRIC)

export function currentAllocationPercentages(state) {
  const presets = state.taxonomy?.unit_presets || []
  const totals = Object.fromEntries((state.taxonomy.destinations || []).map(destination => [destination.code, 0]))
  for (const entry of submissionEntries(state)) {
    for (const line of entry.current || []) totals[line.destination] = (totals[line.destination] || 0) + lineKg(entry, line, presets)
  }
  const allocated = Object.values(totals).reduce((sum, value) => sum + value, 0)
  if (!allocated) return totals
  const percentages = Object.fromEntries(Object.entries(totals).map(([code, value]) => [code, Number((value / allocated * 100).toFixed(2))]))
  // Rounding each share to the slider's 2 dp loses up to 0.005 points per destination, so
  // the seeded allocation summed to 99.99% on the fixture's own data — an improved scenario
  // 0.15 kg lighter than the current one on a 1,500 kg entry, which §6.2 rejects. The panel
  // must not open on a state the server would refuse. The remainder goes to the largest
  // share, the one place it does not change what the allocation says.
  const largest = Object.entries(percentages).reduce((best, entry) => (best && best[1] >= entry[1] ? best : entry), null)
  if (largest && largest[1] > 0) percentages[largest[0]] = Number((largest[1] + 100 - allocationTotal(percentages)).toFixed(2))
  return percentages
}

// The entries the submission will carry, in the order `submitCalculation` sends them.
//
// **`draftEntry` is `state.js`'s, not a local copy.** The copy that stood here named five
// keys, and round two added four more that it did not name — so this panel re-sent the
// submission with `total_input_kg`, `total_value_nzd` and `wasted_value_nzd` absent, under
// the same token, and §5.3's upsert wrote the absence over the visitor's figures.
function submissionEntries(state) {
  return [...state.entries, draftEntry()]
}

// The mass every allocation redistributes: every `submissionEntries` entry's own
// current-scenario total, summed. Item ⑧'s kilogram mode divides and multiplies by this
// number, and it is the same sum `improvementValidation` already takes per entry (§6.2's
// own mass-conservation rule) — a second copy of "what does this entry weigh" here would
// be free to disagree with the one the server-side check is built on.
function totalAllocatableKg(state, presets) {
  return submissionEntries(state).reduce((sum, entry) => sum + sumQtyKg(requestLines(entry, presets)), 0)
}

// `percentageToKg`, made safe to print: `NaN` reads as "Not available" everywhere else on
// this page, and a slider `max` or a box `value` has no use for that string — a headroom
// of zero is a real answer and a missing one should render as zero, not stall the input.
const displayKg = (percentage, totalKg) => {
  const kg = percentageToKg(percentage, totalKg)
  return Number.isFinite(kg) ? Math.max(0, kg) : 0
}

// Every destination at 0, not the current share. A visitor modelling an improvement is
// choosing a new allocation, and seeding the sliders from the old one hides which numbers
// they have actually decided — the client asked for every slider to start at 0.
function zeroAllocations(state) {
  return Object.fromEntries((state.taxonomy.destinations || []).map(destination => [destination.code, 0]))
}

const polar = (cx, cy, radius, degrees) => {
  const radians = (degrees - 90) * Math.PI / 180
  return { x: cx + radius * Math.cos(radians), y: cy + radius * Math.sin(radians) }
}

function slicePath(start, end) {
  const from = polar(260, 210, 112, end)
  const to = polar(260, 210, 112, start)
  return `M 260 210 L ${from.x} ${from.y} A 112 112 0 ${end - start > 180 ? 1 : 0} 0 ${to.x} ${to.y} Z`
}

// The improved allocation drawn as shares of one circle, with a leader line per slice.
//
// **Degrees come from the percentage, not from a re-derived one.** `share * 3.6` is the
// slider's own number turned into an angle, so a chart that disagrees with the box beside it
// is not expressible. A destination sitting at zero draws nothing rather than a zero-width
// wedge whose leader line would still claim space in the callout column.
//
// The centre reports the mass being redistributed, which is the figure the 100% rule is
// about: every arrangement of these slices moves the same kilograms.
function PieChart(state, destinations) {
  let cursor = 0
  const slices = destinations.map((destination, index) => {
    const share = typed(state.improvedAllocations?.[destination.code])
    const start = cursor
    cursor += share * 3.6
    return { destination, share, start, end: cursor, colour: PIE_COLOURS[index % PIE_COLOURS.length] }
  }).filter(slice => slice.share > 0)
  const paths = slices.map(slice => `<path d="${slicePath(slice.start, slice.end)}" fill="${slice.colour}"><title>${escapeHtml(slice.destination.name)} — ${formatNumber(slice.share, 1)}%</title></path>`).join('')
  const labels = slices.map(slice => {
    const middle = (slice.start + slice.end) / 2
    const edge = polar(260, 210, 116, middle)
    const elbow = polar(260, 210, 142, middle)
    const right = elbow.x >= 260
    const endX = right ? 438 : 82
    const textX = right ? 446 : 74
    const anchor = right ? 'start' : 'end'
    return `<g class="improvement-pie-label"><polyline points="${edge.x},${edge.y} ${elbow.x},${elbow.y} ${endX},${elbow.y}" stroke="${slice.colour}"/><circle cx="${edge.x}" cy="${edge.y}" r="3" fill="${slice.colour}"/><text x="${textX}" y="${elbow.y + 4}" text-anchor="${anchor}">${formatNumber(slice.share, 1)}%</text></g>`
  }).join('')
  const legend = slices.map(slice => `<div><i style="background:${slice.colour}"></i><span>${escapeHtml(slice.destination.name)}</span></div>`).join('')
  return `<svg class="improvement-pie-chart" viewBox="0 0 520 420" role="img" aria-label="${escapeHtml(t('Total allocation'))}">${paths}<circle class="improvement-pie-centre" cx="260" cy="210" r="48" fill="#fff"/><text class="improvement-pie-total" x="260" y="205" text-anchor="middle"><tspan>${formatNumber(totalAllocatableKg(state, state.taxonomy?.unit_presets || []), 2)}</tspan><tspan x="260" dy="20">kg</tspan></text>${labels}</svg><div class="improvement-pie-key">${legend}</div>`
}

export function openImprovement(state) {
  const allocations = Object.keys(state.improvedAllocations || {}).length ? state.improvedAllocations : zeroAllocations(state)
  setState({ improvementOpen: true, improvedAllocations: allocations, improvementChartExpanded: false, improvementError: null })
}

// No longer an undo — the panel does not open on the current allocation any more, so
// there is nothing here to return *to*. It is kept as a shortcut to the current shares,
// and the button says so: `t('Match the current allocation')`, not `t('Reset to Current')`.
export function resetImprovement(state) {
  setState({ improvedAllocations: currentAllocationPercentages(state), improvementResult: null, improvementError: null })
}

// Each slider's own ceiling is its current value plus whatever is unallocated —
// `headroom = 100 - allocationTotal(...)` — so dragging one destination to its own limit
// leaves every other destination's slider unable to move at all. Recomputed after every
// change, here and in the row template's first render, from the same two numbers.
//
// **The result is never less than `value` itself, and that floor is not decoration.** The
// bare `Math.round((value + headroom) * 100) / 100` this replaced can round *down* past
// `value` when `value` itself carries more than two decimal places, and `headroom` is at or
// near zero. `max` on an `<input type="range">` is not a suggestion: the browser clamps
// `.value` the instant a lower `max` is assigned, silently, with no event fired for anything
// to react to. So a slider nobody had touched would sit at a different number the moment
// any OTHER row's edit forced this recomputation — visible on screen as one destination
// "chasing" another, which is what the client reported as sliders dragging each other
// around. It was never the recomputation itself: two sliders sharing one 100% ARE meant to
// shrink each other's headroom, on purpose, and that coupling stays. It was this rounding
// occasionally handing an *untouched* row a ceiling below the value it already held. The
// fix is the floor, not removing the recomputation.
function sliderMax(value, headroom) {
  const numericValue = typed(value)
  return Math.max(numericValue, Math.round((numericValue + headroom) * 100) / 100, 0)
}

// The pointer-drag granularity: half a percentage point of the mass being redistributed,
// restated in kilograms when that is the display unit. `step="0.01"` gave the earlier
// percentage slider ten thousand stops and was "too sensitive" to land on with a pointer,
// so a drag is still rounded to this coarseness — but the rounding happens here, in
// `updateImprovementInput`, rather than through the range's own `step` attribute (see the
// row template): a native `step` snaps *any* value assigned to the control, including an
// exact figure mirrored in from the number box, which is what made a box reading `399.55`
// sit beside a range the browser had silently rounded to `400`. Doing it ourselves, only
// on the control the drag actually fired on, is what lets the box's exact value reach the
// range unrounded while a drag still lands on a nameable number.
function rangeStep(mode, totalKg) {
  return mode === 'kilograms' ? Math.max(0.01, Number((totalKg * 0.005).toFixed(2)) || 0.5) : 0.5
}

/**
 * Reads one control's keystroke and patches the DOM directly, the same bypass of
 * `setState` the module docstring above explains — a full re-render on every keystroke
 * loses the caret.
 *
 * **`state.improvedAllocations` is a percentage in every mode.** Item ⑧ added a
 * kilogram *display*, not a second place the allocation can live: storing kilograms
 * instead would make the exactly-100 rule in `improvementValidation` a floating-point
 * comparison against a mass, and a mass that rounds differently at every tonnage would
 * start refusing allocations that are correct. So a keystroke here is converted to a
 * percentage immediately — `kgToPercentage(control.value, totalKg)` in kilogram mode,
 * `control.value` itself in percentage mode — and everything downstream of that line
 * (the total, the validation, the mirrored inputs' *raw displayed value*) is unchanged
 * by which mode produced it.
 *
 * **A cleared box stays cleared, in both modes.** `kgToPercentage('', totalKg)` reads
 * `Number('')` as `0`, which is finite — so a kilogram box the visitor had emptied was
 * silently stored as an allocation of zero rather than as "not answered yet", and
 * `improvementValidation` had nothing to catch, only the mismatch it produces once the
 * other destinations no longer sum to 100%. Percentage mode never had this: `control.value`
 * passes straight through, so `''` reaches the state as `''` and the blank check below
 * fires on it directly. The empty string is now checked before either conversion, in
 * both modes, so it is what reaches the state either way.
 *
 * **The range carries `step="any"`, not a fixed step (see the row template).** A `step`
 * on `<input type="range">` snaps *anything* assigned to `.value` — an exact figure
 * mirrored in from the number box included — which is what put a box reading `399.55`
 * beside a range the browser had silently rounded to a multiple of five. With no native
 * step, an exact mirror lands exactly; a drag, which still has to land on a nameable
 * number, is rounded here instead, to `rangeStep`'s own coarseness, only when `control`
 * is the range itself.
 *
 * **The one coupling the client asked for — an upper bound, on the SLIDER only.** A
 * number box has always been allowed to hold a figure `improvementValidation` will refuse
 * — that is what disables Compare and shows the message, and rewriting it here would take
 * away the number a visitor typed the moment it went out of range, which is a different
 * defect `test_the_improvement_percentage_refuses_a_minus_without_rewriting_the_number`
 * exists to catch (a clamp is exactly the "rewrite the number" that guard may not do). A
 * range control has no such freedom to begin with — `<input type="range">` cannot represent
 * a value outside its own `min`/`max` at all — so `ceiling` here only ever tightens what a
 * DRAG (never a typed figure) can request, for the row being dragged, before the browser's
 * own clamp would otherwise land on whatever the coarse `rangeStep` snap rounded to. This
 * is not what stops one destination's edit from moving ANOTHER destination's slider —
 * `sliderMax`'s own floor (see its comment) is the whole reason that stopped — this is
 * only the affirmative half, restated for the control the client actually dragged.
 */
export function updateImprovementInput(control, state) {
  const code = control.dataset.improvementCode
  const mode = state.improvementMode || 'percentage'
  const presets = state.taxonomy?.unit_presets || []
  const totalKg = totalAllocatableKg(state, presets)
  let raw = control.value
  if (control.type === 'range' && raw !== '') {
    const step = rangeStep(mode, totalKg)
    const numeric = Number(raw)
    if (Number.isFinite(numeric)) {
      const snapped = Math.round(numeric / step) * step
      raw = mode === 'kilograms' ? snapped.toFixed(2) : String(Math.round(snapped * 100) / 100)
      control.value = raw
    }
  }
  let percentage = raw === '' ? '' : (mode === 'kilograms' ? kgToPercentage(raw, totalKg) : raw)
  // The ceiling below is a RANGE-only guard — see the docstring's note on why a number
  // box's out-of-range figure is refused by `improvementValidation`, never rewritten here.
  if (control.type === 'range' && percentage !== '') {
    const numericPercentage = Number(percentage)
    if (Number.isFinite(numericPercentage)) {
      const othersTotal = allocationTotal(state.improvedAllocations) - typed(state.improvedAllocations[code])
      const ceiling = Math.max(0, 100 - othersTotal)
      const clamped = Math.min(Math.max(0, numericPercentage), ceiling)
      if (clamped !== numericPercentage) {
        // Rounded to the same two decimal places `sliderMax` already rounds a
        // headroom-derived ceiling to: `othersTotal` is a running sum of floats, so
        // `ceiling` routinely lands a few units of float dust away from the clean
        // figure it means (`1.7999999999999998`, not `1.8`) — displaying that dust
        // on the control the visitor is looking at would read as a new, unexplained
        // bug the moment anyone dragged past their own headroom.
        percentage = Math.round(clamped * 100) / 100
        raw = mode === 'kilograms' ? displayKg(percentage, totalKg).toFixed(2) : String(percentage)
        control.value = raw
      }
    }
  }
  state.improvedAllocations = { ...state.improvedAllocations, [code]: percentage }
  state.improvementResult = null
  state.improvementError = null
  // Mirrors the *raw* value, not the percentage just computed: every control sharing this
  // code is rendered in the same mode (§ `ImprovementScenario`), so the slider and the
  // number box always agree on which unit `.value` is in and a straight copy is correct.
  // `raw` rather than `control.value` so a range's own drag mirrors its *rounded* (and, if
  // it applied, *clamped*) figure, not the pointer position that produced it.
  document.querySelectorAll(`[data-improvement-code="${CSS.escape(code)}"]`).forEach(input => {
    if (input !== control) input.value = raw
  })
  const total = allocationTotal(state.improvedAllocations)
  const headroom = 100 - total
  document.querySelectorAll('input[type="range"][data-improvement-code]').forEach(slider => {
    const maxPercent = sliderMax(state.improvedAllocations[slider.dataset.improvementCode], headroom)
    slider.max = String(mode === 'kilograms' ? displayKg(maxPercent, totalKg).toFixed(2) : maxPercent)
  })
  const error = improvementValidation(state)
  const totalPanel = document.querySelector('.improvement-total')
  totalPanel?.classList.toggle('invalid', Boolean(error))
  const totalValue = document.getElementById('improvement-total-value')
  if (totalValue) totalValue.textContent = `${total.toFixed(2)}%`
  // This path deliberately patches the DOM rather than re-rendering (a re-render would take
  // the caret out of the box mid-number), so the donut has to be redrawn by hand or it would
  // show the allocation as it stood before the keystroke.
  const chart = document.querySelector('.improvement-pie-content')
  if (chart) chart.innerHTML = PieChart(state, sorted(state.taxonomy.destinations))
  const errorElement = document.getElementById('improvement-inline-error')
  if (errorElement) {
    errorElement.textContent = error
    errorElement.hidden = !error
  }
  const compareButton = document.querySelector('[data-action="compare-improvement"]')
  if (compareButton) compareButton.disabled = Boolean(error)
}

export function allocationTotal(allocations) {
  return Object.values(allocations || {}).reduce((sum, value) => sum + typed(value), 0)
}

/**
 * '' when the improvement scenario can be submitted.
 *
 * The mass check is §6.2's own rule, applied in kilograms to the lines that will actually
 * be sent. A percentage tolerance is a different rule at every tonnage: 0.01 percentage
 * points is 0.15 kg on a 1,500 kg entry, fifteen times §6.2's limit, so the panel enabled
 * Compare on a submission the server then refused with a 400 — after the user had left the
 * screen where the numbers are.
 *
 * **The blank/range message names the unit on screen, not the one stored.** The state
 * this checks is always a percentage (see `updateImprovementInput`), but a visitor working
 * in kilogram mode never typed a percentage and telling them to "enter a percentage" names
 * a unit their own screen does not show them. `state.improvementMode` decides which of the
 * two catalogue strings is returned; nothing about what is being checked changes.
 */
export function improvementValidation(state) {
  const mode = state.improvementMode || 'percentage'
  const values = Object.values(state.improvedAllocations || {})
  const rangeMessage = mode === 'kilograms'
    ? t('Enter a mass in kilograms, from 0 up to the total, for every destination.')
    : t('Enter a percentage from 0 to 100 for every destination.')
  if (values.some(value => value === '' || !Number.isFinite(Number(value)) || Number(value) < 0 || Number(value) > 100)) return rangeMessage
  const total = allocationTotal(state.improvedAllocations)
  const mismatch = t('Improved destination allocations must total 100%, so the improved scenario describes the same waste as the current one. Current total: %(total)s%.', { total: total.toFixed(2) })
  if (Math.abs(total - 100) > 0.01) return mismatch
  const presets = state.taxonomy?.unit_presets || []
  for (const entry of submissionEntries(state)) {
    const currentKg = sumQtyKg(requestLines(entry, presets))
    const improvedKg = sumQtyKg(improvedLines(entry, state.improvedAllocations, presets))
    if (Math.abs(improvedKg - currentKg) > MASS_TOLERANCE_KG) return mismatch
  }
  return ''
}

// The alternative redistributes the mass the entry's *current* scenario describes, not the
// total typed at step 3. Step 4 deliberately lets a user allocate less than that total, so
// anchoring here on the typed total made every under-allocated entry send an alternative
// heavier than its current scenario — which §6.2 rejects, for the whole submission. It also
// matches `currentAllocationPercentages`, which has always taken its percentages of the
// allocated mass, so the seeded sliders now round-trip to the mass they were derived from.
function improvedLines(entry, allocations, presets) {
  const totalKg = sumQtyKg(requestLines(entry, presets))
  return Object.entries(allocations).filter(([, percentage]) => typed(percentage) > 0).map(([destination, percentage]) => ({ destination, qty_kg: (totalKg * typed(percentage) / 100).toFixed(3) }))
}

/**
 * Runs the comparison request and stores its result, or its message, on the state.
 *
 * `toPublicMessage` is `calculator.js`'s `publicError` — §9's code-to-copy map — passed in
 * rather than imported, because `calculator.js` already imports this module and the import
 * back would be a cycle. This panel set `improvementError: error.message`, so a
 * `FORMULA_ERROR` or a `NO_PUBLISHED_FACTOR_SET` showed raw backend prose here while the
 * main flow showed C's user copy for the same code, and §9.1 rules that a public
 * `FORMULA_ERROR` never echoes the expression or its location.
 *
 * @param {object} state
 * @param {(error: Error & {code?: string}) => string} [toPublicMessage]
 */
export async function compareImprovement(state, toPublicMessage = error => error.message || t('The improvement comparison could not be completed.')) {
  const validationError = improvementValidation(state)
  if (validationError) {
    setState({ improvementError: validationError })
    return
  }
  setState({ improvementLoading: true, improvementError: null, improvementResult: null })
  try {
    // §6.2: one call for the whole submission. The per-entry loop this replaced re-sent
    // `current` and `alternative` under the same token, so §5.3's upsert left the stored
    // row holding the last entry's improvement scenario alone.
    //
    // **And the same builder `calculator.js` uses**, for the same reason at one remove: this
    // call lands on the row that call created, so a field this one omits is a field the
    // visitor loses. It omitted four of them, and every destination row's unit besides.
    const entries = submissionEntries(state)
    const presets = state.taxonomy?.unit_presets || []
    const response = await calculate(
      submissionPayload(state, entries, entry => improvedLines(entry, state.improvedAllocations, presets)),
    )
    const token = response.token || state.token
    if (token) sessionStorage.setItem('kaiCalculatorToken', token)
    // The per-entry pairing `comparisons` held was read by exactly one thing — the
    // `landfill_diverted` card's cross-entry `by_destination` sum — and that card is gone.
    // Every figure this screen renders now comes from `totals`.
    setState({ improvementLoading: false, improvementResult: response, token })
    requestAnimationFrame(() => document.getElementById('comparison-results')?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
  } catch (error) {
    setState({ improvementLoading: false, improvementError: toPublicMessage(error) })
  }
}

// An options object rather than a sixth positional parameter: `mode` and `totalKg` travel
// together (one is meaningless without the other) and `DestinationAllocationRow(d, c, i,
// m, mode, totalKg)` was already unreadable at the call site without counting commas
// against the signature above it.
function DestinationAllocationRow({ destination, current, improved, max, mode, totalKg }) {
  // The one interpolation on the branch that reached an attribute through neither
  // `escapeHtml` nor `slug`. `destination.code` is `VARCHAR(64)` with no pattern constraint
  // in `db/`, `api/` or `admin/`, and staff edit it through sqladmin's generic CRUD, so a
  // code containing a double quote breaks `for="…"` and `id="…"` on a public page. `slug` is
  // what `calculator.js` already uses for the identical case, and it is the right tool here
  // rather than `escapeHtml`: this value is a DOM id, and an id is not a place to carry
  // punctuation that `getElementById` and `querySelector` then have to escape again.
  // (`data-improvement-code` still carries the real code, escaped, and that is what the
  // keystroke path reads — so two codes that slug alike share a label association but never
  // a value.)
  const id = `improved-${slug(destination.code)}`
  const kilograms = mode === 'kilograms'
  // Item ⑧: the control's raw `.value` is in the displayed unit, never the stored
  // percentage — `updateImprovementInput` is what converts back on the way in. `max` is
  // this row's `improved` value plus whatever headroom the whole allocation has left (§
  // `updateImprovementInput`), not the fixed `100` a slider starts and ends at regardless
  // of its neighbours, converted into kilograms the same way the value is.
  //
  // **Percentage mode prints `improved` / `max` exactly as it always did — no `.toFixed`
  // added here.** `test_the_sliders_start_at_zero_and_the_total_says_so` reads the
  // sliders' own `.value` and requires the literal `"0"`; rounding every percentage to two
  // places for symmetry with the new kilogram branch would have turned that into `"0.00"`
  // for a reason with nothing to do with kilograms at all.
  // A genuinely blank allocation stays blank through a full re-render (a mode toggle, a
  // reopen) in both units — `displayKg('', totalKg)` reads `Number('')` as `0`, which is
  // finite, so the kilogram branch alone would turn "not answered yet" into "0.00" the
  // moment the panel redrew, even though nothing was typed.
  const value = improved === '' ? '' : (kilograms ? displayKg(improved, totalKg).toFixed(2) : improved)
  const ceiling = kilograms ? displayKg(max, totalKg).toFixed(2) : max
  const numberMax = kilograms ? (Number.isFinite(totalKg) && totalKg > 0 ? totalKg.toFixed(2) : '') : 100
  const unitLabel = kilograms ? 'kg' : '%'
  // The number box stays the exact-entry control and the slider the coarse one in both
  // modes: `step="0.01"` here is the same two-decimal ceiling `MASS_TOLERANCE_KG` checks
  // in kilograms, so a visitor typing to that precision is typing to the precision the
  // mass check actually honours.
  //
  // **The range's own `step` is `"any"`, not the coarse figure it drags in steps of.**
  // `<input type="range">` snaps *any* value assigned to `.value` to the nearest multiple
  // of its `step` attribute — including an exact figure mirrored in from the number box —
  // so a fixed `step` here made a box reading `399.55` sit beside a range the browser had
  // silently rounded to `400`. `updateImprovementInput` applies that same coarseness
  // itself, only to a drag on the range, so the two controls never show a different number
  // for the same allocation.
  //
  // **The two `aria-label`s are the accessible name a screen reader gets for these
  // controls, and they name the unit the same way the visible label beside the box
  // does.** An `aria-label` fixed to "percentage" in kilogram mode told a screen-reader
  // visitor the field wanted a percentage when it did not - the same defect as the
  // validation message above, on a surface a sighted visitor never sees at all.
  const rangeLabel = kilograms
    ? t('Improved %(destination)s kilograms', { destination: destination.name })
    : t('Improved %(destination)s percentage', { destination: destination.name })
  const boxLabel = kilograms
    ? t('Improved %(destination)s kilograms value', { destination: destination.name })
    : t('Improved %(destination)s percentage value', { destination: destination.name })
  return `<div class="improvement-allocation-row"><div><label for="${id}">${escapeHtml(destination.name)}</label><span>${escapeHtml(t('Current'))}: ${formatNumber(current, 2)}%</span></div><div class="improvement-control"><input id="${id}" type="range" min="0" max="${ceiling}" step="any" value="${escapeHtml(value)}" data-improvement-code="${escapeHtml(destination.code)}" aria-label="${escapeHtml(rangeLabel)}"><div class="percentage-input"><input type="number" min="0" max="${numberMax}" step="0.01" inputmode="decimal" value="${escapeHtml(value)}" data-improvement-code="${escapeHtml(destination.code)}" aria-label="${escapeHtml(boxLabel)}"><span>${unitLabel}</span></div></div></div>`
}

export function ImprovementScenario(state) {
  if (!state.improvementOpen) return `<section class="explore-improvements"><h2>${escapeHtml(t('Want to explore potential improvements?'))}</h2><p>${escapeHtml(t('Adjust how your food waste is managed to see how the environmental and economic impacts could change.'))}</p><button class="button button-primary" type="button" data-action="explore-improvements">${escapeHtml(t('Explore Improvements'))}</button></section>`
  const current = currentAllocationPercentages(state)
  const total = allocationTotal(state.improvedAllocations)
  const headroom = 100 - total
  const error = improvementValidation(state)
  // Item ⑧: a site manager thinks in tonnes diverted, not in percentage points. This
  // decides only what the sliders and boxes below *display* — see the note on
  // `updateImprovementInput` for why the stored allocation is unaffected either way.
  const mode = state.improvementMode || 'percentage'
  const totalKg = totalAllocatableKg(state, state.taxonomy?.unit_presets || [])
  const modeField = `<div class="form-field improvement-mode-field"><label for="improvement-mode">${escapeHtml(t('Unit'))}</label><select id="improvement-mode"><option value="percentage" ${mode === 'percentage' ? 'selected' : ''}>${escapeHtml(t('Percentage'))}</option><option value="kilograms" ${mode === 'kilograms' ? 'selected' : ''}>${escapeHtml(t('kilograms'))}</option></select></div>`
  return `<section class="improvement-scenario" aria-labelledby="improvement-title"><h2 id="improvement-title">${escapeHtml(t('Create an Improvement Scenario'))}</h2><p>${escapeHtml(t('Redistribute the current waste amount across different destinations. The total amount of waste should remain unchanged.'))}</p>${modeField}<div class="improvement-editor"><div class="improvement-pie-wrap"><div class="improvement-pie-content">${PieChart(state, sorted(state.taxonomy.destinations))}</div><button class="button button-secondary improvement-expand-chart" type="button" data-action="expand-improvement-chart"><span aria-hidden="true">⛶</span> ${escapeHtml(t('Total allocation'))}</button></div><div class="improvement-allocation-list">${sorted(state.taxonomy.destinations).map(destination => {
    const improved = state.improvedAllocations[destination.code] ?? 0
    return DestinationAllocationRow({ destination, current: current[destination.code] || 0, improved, max: sliderMax(improved, headroom), mode, totalKg })
  }).join('')}</div></div><div class="improvement-total ${error ? 'invalid' : ''}" aria-live="polite"><span>${escapeHtml(t('Total allocation'))}</span><strong id="improvement-total-value">${total.toFixed(2)}%</strong><span class="improvement-total-mass">${escapeHtml(t('Total mass'))}: <strong id="improvement-total-kg">${formatNumber(totalKg, 2)}</strong> kg</span></div><p class="field-error" id="improvement-inline-error" role="alert" ${error ? '' : 'hidden'}>${escapeHtml(error)}</p>${state.improvementError ? `<p class="field-error" role="alert">${escapeHtml(state.improvementError)}</p>` : ''}<div class="improvement-actions"><button class="button button-secondary" type="button" data-action="reset-improvement">${escapeHtml(t('Match the current allocation'))}</button><button class="button button-secondary" type="button" data-action="cancel-improvement">${escapeHtml(t('Cancel'))}</button><button class="button button-primary" type="button" data-action="compare-improvement" ${error || state.improvementLoading ? 'disabled' : ''}>${escapeHtml(state.improvementLoading ? t('Comparing…') : t('Compare Impact'))}</button></div>${state.improvementChartExpanded ? `<div class="improvement-chart-modal" role="dialog" aria-modal="true" aria-label="${escapeHtml(t('Total allocation'))}"><div class="improvement-chart-expanded"><button class="improvement-chart-close" type="button" data-action="close-improvement-chart" aria-label="${escapeHtml(t('Cancel'))}">×</button>${PieChart(state, sorted(state.taxonomy.destinations))}</div></div>` : ''}</section>`
}

/**
 * Every figure on the comparison screen, read from §6.2 rather than derived.
 *
 * `totals.current` / `totals.alternative` are the engine's cross-entry roll-up and
 * `totals.net_benefit` is `current − alternative` per metric. The `aggregateComparison`
 * this replaced added the per-entry totals up in the browser and then subtracted them
 * itself, so every "X saved" on screen was a number the engine never produced (§7.6.1).
 *
 * Equivalences carry no difference and `net_benefit` is keyed by metric code only (§3), so
 * both scenarios' `label` strings are kept and neither is subtracted from the other — §8.2's
 * ruling for the same situation on the admin comparison screen: render both, plainly
 * labelled, and say in words that no difference is shown.
 */
function comparisonData(result) {
  const totals = result.totals || {}
  const netBenefit = totals.net_benefit || {}
  const metrics = {}
  for (const [code, currentMetric] of Object.entries(totals.current?.metrics || {})) {
    const improvedMetric = totals.alternative?.metrics?.[code]
    if (!improvedMetric) continue
    metrics[code] = {
      current: number(currentMetric.total),
      improved: number(improvedMetric.total),
      difference: number(netBenefit[code]),
      unit: currentMetric.unit,
      precision: currentMetric.display_precision,
    }
  }
  // §3: `label` is `label_template` with the equivalence's own value already interpolated
  // and formatted by the engine. Each side keeps its own sentence.
  const equivalences = new Map()
  const readSide = (rows, key) => {
    for (const row of rows || []) equivalences.set(row.code, { ...(equivalences.get(row.code) || {}), [key]: row.label })
  }
  readSide(totals.current?.equivalences, 'current')
  readSide(totals.alternative?.equivalences, 'improved')
  return { metrics, equivalences: [...equivalences.values()] }
}

function changeCopy(metric) {
  // `difference` is `net_benefit[code]` verbatim. The percentage that stood beside it was
  // `difference / |current| × 100`, computed here; §6.2 defines no percentage in any version
  // and v1.5 rules it removed rather than relocated, because `net_benefit` already carries
  // the same information in the unit the user entered.
  const difference = metric.difference
  if (!Number.isFinite(difference)) return { className: 'neutral', valueClass: '', text: t('Not available') }
  const valueClass = signClass(difference)
  if (Math.abs(difference) < 1e-9) return { className: 'neutral', valueClass, text: t('No change') }
  const positive = difference > 0
  return { className: positive ? 'positive' : 'negative', valueClass, text: `${formatNumber(Math.abs(difference), metric.precision ?? 2)} ${escapeHtml(metric.unit || '')} ${escapeHtml(positive ? t('saved') : t('increase'))}` }
}

const metricName = (code, taxonomy) => (taxonomy.metrics || []).find(item => item.code === code)?.name || code

// A scenario figure. `formatNumber` already prints the minus sign; the class is what makes a
// negative total read as an offset rather than as a small number (§7.6.6).
const scenarioValue = (value, metric) => `<strong class="${Number.isFinite(value) && value < 0 ? 'value-negative' : ''}">${formatNumber(value, metric.precision ?? 2)} ${escapeHtml(metric.unit || '')}</strong>`

function ImpactComparisonCard(code, metric, taxonomy) {
  const change = changeCopy(metric)
  return `<article class="impact-comparison-card"><h3>${escapeHtml(metricName(code, taxonomy))}</h3><div class="comparison-values"><div><span>${escapeHtml(t('Current'))}</span>${scenarioValue(metric.current, metric)}</div><span class="comparison-arrow" aria-hidden="true">→</span><div><span>${escapeHtml(t('Improved'))}</span>${scenarioValue(metric.improved, metric)}</div></div><div class="comparison-change ${change.className}"><strong class="${change.valueClass}">${change.text}</strong></div></article>`
}

function ComparisonSummary(data, taxonomy) {
  const codes = comparableCodes(data.metrics)
  return `<section class="comparison-summary"><h2>${escapeHtml(t('Potential Improvement'))}</h2><ul>${codes.map(code => {
    const change = changeCopy(data.metrics[code])
    return `<li class="${change.className}"><strong>${escapeHtml(metricName(code, taxonomy))}:</strong> <span class="${change.valueClass}">${change.text}</span></li>`
  }).join('') || `<li>${escapeHtml(t('No comparable impact metrics were returned.'))}</li>`}</ul></section>`
}

/**
 * The comparison chart, which must render a negative value as negative (§7.6.6).
 *
 * `Math.abs()` stood on both widths, so a −500 kg CO2e offset drew a bar identical to +500 —
 * and `downstream` may be negative (§2.2; `animal_feed` is −0.15 in `tests/fixtures/factors.json`),
 * so a total legitimately can be. A group containing a negative value therefore draws against
 * a **centred zero line**: each bar takes at most half the track and grows right from the
 * centre when positive, left from the centre when negative. Groups with no negative value
 * keep the full-width left-anchored bar, so the common case is unchanged.
 *
 * This is still charting, not arithmetic on an API figure (§7.6.1) — a width relative to the
 * largest bar in its own group, and no width is printed on the page.
 */
function ComparisonBars(metrics, taxonomy) {
  const codes = comparableCodes(metrics)
  const anyDiverging = codes.some(code => [metrics[code].current, metrics[code].improved].some(value => Number.isFinite(value) && value < 0))
  const groups = codes.map(code => {
    const metric = metrics[code]
    const values = [metric.current, metric.improved].filter(Number.isFinite)
    const max = Math.max(...values.map(value => Math.abs(value)), 1)
    const diverging = values.some(value => value < 0)
    const bar = (value, className) => {
      if (!Number.isFinite(value)) return ''
      const width = Math.abs(value) / max * (diverging ? 50 : 100)
      const offset = diverging ? (value < 0 ? 50 - width : 50) : 0
      // `margin-inline-start`, not `margin-left`: Arabic and Urdu render this page with
      // `<html dir="rtl">`, and a physical left offset would place the negative half of a
      // diverging bar on the same side as the positive half - two opposite quantities
      // drawn on top of each other. The logical property mirrors with the document.
      return `<span class="${className}${value < 0 ? ' negative-bar' : ''}" style="width:${width}%;margin-inline-start:${offset}%"></span>`
    }
    const row = (label, value, className) => `<div><span>${label}</span><div class="comparison-bar-track${diverging ? ' diverging' : ''}">${bar(value, className)}</div>${scenarioValue(value, metric)}</div>`
    return `<div class="comparison-bar-group"><h3>${escapeHtml(metricName(code, taxonomy))}</h3>${row(escapeHtml(t('Current')), metric.current, 'current-bar')}${row(escapeHtml(t('Improved')), metric.improved, 'improved-bar')}</div>`
  }).join('')
  const note = anyDiverging ? `<p class="comparison-bar-note">${escapeHtml(t('A metric total can be negative when a destination offsets more than it costs. Those bars are drawn from a centre line marking zero and run to the left.'))}</p>` : ''
  return `<section class="comparison-bars" aria-labelledby="comparison-chart-title"><h2 id="comparison-chart-title">${escapeHtml(t('Current and Improved comparison'))}</h2>${note}${groups}</section>`
}

/**
 * Both scenarios' equivalents, side by side, never differenced.
 *
 * `net_benefit` is keyed by metric code (§3 declares it `dict[str, Decimal]`) and
 * `EquivalenceResult` carries no difference, so a "18,597 km fewer" figure could only be
 * `current − improved` computed here — the same defect as the metric sums this module no
 * longer performs, with nothing to replace it. §8.2 rules the same situation on the admin
 * comparison screen: render both values, plainly labelled, and state that no difference is
 * shown. (The copy this replaced also read "5,016 Equivalent to driving 18,597 km fewer",
 * a subtracted number glued to the front of the engine's own sentence.)
 */
function equivalentComparison(rows) {
  if (!rows.length) return `<p class="empty-state">${escapeHtml(t('No comparable tangible equivalents were returned.'))}</p>`
  const side = (label, value) => `<div><span>${escapeHtml(label)}</span><strong>${value ? escapeHtml(value) : escapeHtml(t('Not available'))}</strong></div>`
  return `<p class="comparison-equivalent-note">${escapeHtml(t('Each scenario is shown as the calculation service worded it. The difference between the two is not shown, because the service does not return one — compare the two figures.'))}</p><ul class="comparison-equivalents">${rows.map(row => `<li><div class="comparison-values">${side(t('Current'), row.current)}<span class="comparison-arrow" aria-hidden="true">→</span>${side(t('Improved'), row.improved)}</div></li>`).join('')}</ul>`
}

/**
 * §4.5's saving, rendered where the scenario that produced it is.
 *
 * **It used to live in `results.js`'s money block and could not be reached.** That block
 * reads `state.result`, and the Calculate button sends `alternative: null` for every
 * entry, so §4.5's "`None` when no entry carries an alternative" made `saving_nzd` null
 * on every response a visitor could produce. The saving is a property of the comparison —
 * `Σ (entry's own value per kilogram × that entry's diverted mass)`, and `diverted` is
 * `current − alternative` — so it belongs beside the comparison, not beside three figures
 * describing the current scenario alone.
 *
 * The caveat rides directly beneath it rather than in a tooltip a screenshot would crop
 * away: the rate is nominal, derived from two totals the visitor typed for that entry, and
 * copy presenting it as a measured valuation claims a precision the input does not carry.
 *
 * The classes are `results.js`'s own `.money-row` / `.money-saving` / `.money-caveat`, so
 * the figure reads as the same kind of thing in both places rather than as a new widget.
 */
function comparisonSaving(result) {
  const saving = result.totals?.money?.saving_nzd
  if (!hasValue(saving)) return ''
  return `<div class="comparison-saving"><div class="money-row money-saving"><span class="money-label">${escapeHtml(t('Value of food not wasted at all'))}</span><span class="money-value">${nzd(saving)}</span></div><p class="money-caveat">${escapeHtml(t('This assumes an even value per kilogram within each entry you priced, the way a box of produce is costed as a whole - not a measured price, and not an average taken across every entry.'))}</p></div>`
}

export function ComparisonResults(state) {
  const result = state.improvementResult
  if (!result?.totals?.alternative) return ''
  const data = comparisonData(result)
  const mock = result.factor_set?.is_mock
  return `<section class="comparison-results" id="comparison-results" aria-labelledby="comparison-results-title"><p class="eyebrow">${escapeHtml(t('Current Results → Improved Scenario'))}</p><h2 id="comparison-results-title">${escapeHtml(t('Compare Results'))}</h2>${mock ? `<p class="comparison-estimate-note">${escapeHtml(t('Demonstration only — this comparison uses mock factors and is not a verified impact result.'))}</p>` : ''}${ComparisonSummary(data, state.taxonomy)}${comparisonSaving(result)}<div class="impact-comparison-grid">${comparableCodes(data.metrics).map(code => ImpactComparisonCard(code, data.metrics[code], state.taxonomy)).join('')}</div>${ComparisonBars(data.metrics, state.taxonomy)}<section class="comparison-equivalent-section"><h2>${escapeHtml(t('Tangible equivalents'))}</h2>${equivalentComparison(data.equivalences)}</section></section>`
}
