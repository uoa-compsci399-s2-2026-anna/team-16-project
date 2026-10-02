import { calculate } from './api.js'
import { setState, draftEntry, leafDisplayName } from './state.js'
import { rowKgString, percentageToKg, kgToPercentage, kgToUnitAmount, unitAmountToKg, unitDisplayPrecision, isPresetUnit, presetUnitCode, PRESET_UNIT } from './units.js'
import { requestLines, submissionLeaves, submissionPayload } from './submission.js'
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

// Item ⑧'s destination-level unit, one row at a time. `state.improvementRowUnits` maps a
// destination `code` to `'kilograms'`, `'tonnes'`, or `preset:<unit_preset.code>` — the same
// value space `calculator.js`'s own row unit selector uses (§7.3) — and a row with no entry
// yet falls back to kilograms, the unit every row was shown in before this selector existed.
const rowUnitFor = (state, code) => state.improvementRowUnits?.[code] || 'kilograms'

// A row's own display unit, named for a screen reader and for the aria-labels beside it.
// `preset.label` is staff-typed and shown exactly as written (§7.7.7), the same rule
// `calculator.js`'s `rowUnitLabel` follows for the identical value — escaped, never passed
// through `t()`, because it is not a sentence in the visitor's language, it is whatever staff
// named the container.
const rowUnitName = (unit, presets) => {
  if (unit === 'tonnes') return t('tonnes')
  if (isPresetUnit(unit)) return presets.find(item => item.code === presetUnitCode(unit))?.label || unit
  return t('kilograms')
}

// The options a row's own unit `<select>` offers: the two weights, then every container the
// taxonomy carries — unfiltered by food category. Unlike step 4's own row selector, this one
// has no single entry's food category to filter against: an allocation redistributes the
// mass of every entry in the submission (`submissionEntries`), which may not share one.
function rowUnitOptionsHtml(presets, selectedValue) {
  const weights = [['kilograms', t('kilograms')], ['tonnes', t('tonnes')]]
    .map(([value, label]) => `<option value="${value}" ${selectedValue === value ? 'selected' : ''}>${escapeHtml(label)}</option>`)
    .join('')
  const containers = presets.map(preset => {
    const value = PRESET_UNIT + preset.code
    return `<option value="${escapeHtml(value)}" ${selectedValue === value ? 'selected' : ''}>${escapeHtml(preset.label)}</option>`
  }).join('')
  return weights + containers
}

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

/**
 * **One leaf's own current shares.** Percentages of the mass *that leaf's* current
 * scenario describes, which is the mass its improved scenario has to redistribute.
 */
function leafShares(entry, destinations, presets) {
  const totals = Object.fromEntries((destinations || []).map(destination => [destination.code, 0]))
  for (const line of entry.current || []) totals[line.destination] = (totals[line.destination] || 0) + lineKg(entry, line, presets)
  const allocated = Object.values(totals).reduce((sum, value) => sum + value, 0)
  if (!allocated) return totals
  const percentages = Object.fromEntries(Object.entries(totals).map(([code, value]) => [code, Number((value / allocated * 100).toFixed(2))]))
  // Rounding each share to the slider's 2 dp loses up to 0.005 points per destination, so
  // the seeded allocation summed to 99.99% on the fixture's own data — an improved scenario
  // 0.15 kg lighter than the current one on a 1,500 kg entry, which §6.2 rejects. The panel
  // must not open on a state the server would refuse. The remainder goes to the largest
  // share, the one place it does not change what the allocation says.
  const largest = Object.entries(percentages).reduce((best, row) => (best && best[1] >= row[1] ? best : row), null)
  if (largest && largest[1] > 0) percentages[largest[0]] = Number((largest[1] + 100 - allocationTotal(percentages)).toFixed(2))
  return percentages
}

/**
 * **The current allocation, one map per leaf, in submission order.**
 *
 * It was a single submission-wide map, and that made *Match the current allocation* stop
 * being an identity the moment a chain forked. Measured: 100 kg of dairy sent entirely to
 * landfill and 200 kg of fruit sent entirely to animal feed came back as one split of
 * 33.33% landfill / 66.67% animal feed applied to **both** leaves - which describes
 * neither of them, matches neither Current column, and is not the submission the visitor
 * made. Owner decision 6 (`design.md` §10) forks the panel for exactly this.
 *
 * The shape is an array rather than a map keyed by the leaf's name because two chains may
 * legitimately carry the same sector and the same food - the duplicate notice warns about
 * it and does not forbid it - so a name is not an identity here. Position in the
 * submission is, and it is the same position `entries[]` and `entry_results[]` use.
 *
 * @returns {Array<Object<string, number>>} one destination-to-percentage map per leaf
 */
export function currentAllocationPercentages(state) {
  const presets = state.taxonomy?.unit_presets || []
  const destinations = state.taxonomy?.destinations || []
  return submissionEntries(state).map(entry => leafShares(entry, destinations, presets))
}

/**
 * `state.improvedAllocations`, reconciled to the leaves the submission actually has.
 *
 * Every reader goes through it, so a panel opened on three leaves and then re-rendered
 * after one was removed cannot read a fourth leaf's allocation, and an allocation stored
 * before the fork (a bare object) cannot be mistaken for leaf zero's.
 */
function leafAllocations(state) {
  const held = Array.isArray(state.improvedAllocations) ? state.improvedAllocations : []
  return submissionEntries(state).map((_, index) => held[index] || {})
}

// The entries the submission will carry, in the order `submitCalculation` sends them.
//
// **`draftEntry` is `state.js`'s, not a local copy.** The copy that stood here named five
// keys, and round two added four more that it did not name — so this panel re-sent the
// submission with `total_input_kg`, `total_value_nzd` and `wasted_value_nzd` absent, under
// the same token, and §5.3's upsert wrote the absence over the visitor's figures.
//
// **And they are LEAVES, not chains.** One supply-chain chain now carries several
// `entries[]`, each with its own amount and its own destination allocation
// (`design.md` §10), so every consumer below operates per leaf: the seeded percentages
// are taken over the same lines the payload carries, and `improvementValidation` checks
// mass conservation per entry exactly as the server checks it. Handed chains instead,
// this panel would seed its sliders from allocations that are not in the request and
// would compare an alternative against a current scenario the server never saw.
function submissionEntries(state) {
  return submissionLeaves([...state.entries, draftEntry()])
}

// **The mass ONE leaf's allocation redistributes**: that leaf's own current-scenario
// total. Item ⑧'s unit mode divides and multiplies by this number, and it is the same sum
// `improvementValidation` takes per entry (§6.2's own mass-conservation rule) — a second
// copy of "what does this entry weigh" here would be free to disagree with the one the
// server-side check is built on.
//
// **Per leaf and not per submission, since the fork.** A row's `max` in unit mode is "the
// whole mass being redistributed *here*", and a 100 kg leaf whose slider maxed out at the
// submission's 300 kg would let a visitor allocate three times the mass that leaf has —
// which the server refuses, per entry, after they have left the screen with the numbers on
// it.
const leafAllocatableKg = (entry, presets) => sumQtyKg(requestLines(entry, presets))

// The submission's whole mass, for the one figure that is about the submission rather than
// about a leaf: the "Total mass" line under the panel.
function totalAllocatableKg(state, presets) {
  return submissionEntries(state).reduce((sum, entry) => sum + leafAllocatableKg(entry, presets), 0)
}

// `percentageToKg`, made safe to print and restated in a row's own display unit: `NaN`
// reads as "Not available" everywhere else on this page, and a slider `max` or a box
// `value` has no use for that string — a headroom of zero is a real answer and a missing
// one should render as zero, not stall the input.
//
// **Item ⑧'s single kilogram figure, generalised to whatever unit the row is showing.**
// This was `percentageToKg` alone; `kgToUnitAmount` is `units.js`'s own conversion from
// kilograms to a row's unit, and calling it here rather than re-deriving it is the same
// rule every other figure in this module already follows (§7.3). `unit: null` (percentage
// mode, where this is never called with anything else) is not a case `kgToUnitAmount` needs
// to know about — every call site below guards it.
const displayAmount = (percentage, totalKg, unit, presets) => {
  const kg = percentageToKg(percentage, totalKg)
  const amount = kgToUnitAmount(kg, unit, presets)
  return Number.isFinite(amount) ? Math.max(0, amount) : 0
}

// Every destination at 0, for every leaf — not the current share. A visitor modelling an
// improvement is choosing a new allocation, and seeding the sliders from the old one hides
// which numbers they have actually decided; the client asked for every slider to start
// at 0.
function zeroAllocations(state) {
  const empty = Object.fromEntries((state.taxonomy.destinations || []).map(destination => [destination.code, 0]))
  return submissionEntries(state).map(() => ({ ...empty }))
}

const polar = (cx, cy, radius, degrees) => {
  const radians = (degrees - 90) * Math.PI / 180
  return { x: cx + radius * Math.cos(radians), y: cy + radius * Math.sin(radians) }
}

// The donut's own geometry, in the `viewBox="0 0 520 420"` user space this module draws
// in. Named rather than repeated because `slicePath` now has two branches and they have
// to agree: a full allocation and a 99% one must describe the *same* circle, and a
// literal `112` in one of them and a `PIE_RADIUS` in the other is a drift the chart
// cannot report — it would simply draw the wrong circle in the wrong place for exactly
// one allocation. The callouts' own radii are stated as offsets from this one for the
// same reason: the leader line has to leave the arc it is pointing at.
const PIE_CENTRE_X = 260
const PIE_CENTRE_Y = 210
const PIE_RADIUS = 112

// How short of a full 360° a sweep may fall and still be drawn as a complete circle —
// see `slicePath`, which is the only reader. **It exists because the collapse this
// tolerance is guarding against is not an equality.** Chrome holds SVG path geometry in
// single precision, so a one-slice arc's two endpoints round to the same float32 point —
// and the arc vanishes, per SVG 1.1 §8.3.8 — for a *range* of sweeps below 360, not only
// at it. Bisected in this repository's own Chromium at r=112: a share of
// 99.99999783009287 still paints a 224x224 box, 99.99999783009288 paints nothing, i.e. a
// sweep 7.8e-06° short of the full turn already collapses. That figure is not arbitrary —
// it is what a float32 ulp at a coordinate of 260 predicts (1.5e-05 / 112 radians), so
// any engine holding this geometry in single precision collapses in the same decade.
//
// 0.01° is ~1,280x that threshold, which is the margin, and it costs 0.0196 user units
// of omitted arc (112 * 0.01 * pi/180). **Measured in the browser rather than read off
// the stylesheet**, across this suite's three viewports and both places the chart is
// drawn: the largest magnification is the expanded modal at 938x898 and dpr 1.5, where
// the 720px `<svg>` is 2.0769 device pixels per user unit and the omitted arc is
// **0.0406 device pixels**. The phone is not the worst case and reading the CSS suggests
// it is — 390x700 at dpr 3 caps the modal at `92vw` and comes to 1.5623 (0.0305px),
// below the 1278 desktop's 1.7308 (0.0338px). It is also wider than nothing and
// narrower than anything the panel treats as a real allocation:
// the finest step the number box declares is 0.01 of a percentage point, whose last stop
// below a full allocation is 99.99% — a sweep of 359.964°, comfortably under the gate —
// and every share the gate does take already prints as "100.0%" in its own callout.
const FULL_SWEEP_TOLERANCE_DEGREES = 0.01

function slicePath(start, end) {
  // SVG's arc command cannot represent a full circle when its start and end points are
  // identical: SVG 1.1 §8.3.8 makes such an arc equivalent to omitting the segment, so
  // the browser paints nothing. A single destination at 100% therefore used to leave the
  // chart's centre text and callout visible while the slice itself disappeared. Draw the
  // circle as two half-arcs so the complete allocation remains visible.
  //
  // **The gate is a tolerance and not `>= 360`, because the collapse is not an
  // equality.** An exact comparison on a float sweep leaves a dead band of shares just
  // under 100 that still reproduce the original defect with this branch in place, and
  // both of the panel's entry modes reach it: a typed `99.999999` in percentage mode,
  // and in unit mode the `toFixed` rounding of a row's own `fixedRowMax` (`12.49%` of
  // every 0.01 kg mass from 1 to 3,000 kg, measured). `improvementValidation` accepts
  // every one of them — a typed `99.999999` is 1e-05 kg light on the 1,000 kg fixture
  // before `improvedLines` rounds it to three places, and nothing like the 0.01 kg
  // `MASS_TOLERANCE_KG` — so Compare Impact stays enabled and the visitor is left with
  // an allocation the panel calls valid, a callout reading
  // "100.0%", and an empty donut. The asymmetry is why nothing else catches it:
  // `updateImprovementInput`'s range clamp rounds a figure that *overshoots* back to
  // exactly 100 and leaves one that *undershoots* untouched. See
  // `FULL_SWEEP_TOLERANCE_DEGREES` for the measurement and what the tolerance costs.
  const sweep = end - start
  if (sweep > 360 - FULL_SWEEP_TOLERANCE_DEGREES) {
    // Derived from the same centre and radius the wedge branch uses, through the same
    // `polar`: the top and bottom of the vertical diameter. `polar` returns them exactly
    // — `Math.sin(±π/2)` is ±1 and the cosine's 6.1e-17 is below an ulp of 260 — so the
    // two half-arcs close on the same point and this emits the identical path string the
    // literals did, while a change to the centre or the radius above now moves both
    // branches together.
    const top = polar(PIE_CENTRE_X, PIE_CENTRE_Y, PIE_RADIUS, 0)
    const bottom = polar(PIE_CENTRE_X, PIE_CENTRE_Y, PIE_RADIUS, 180)
    return `M ${top.x} ${top.y} A ${PIE_RADIUS} ${PIE_RADIUS} 0 1 0 ${bottom.x} ${bottom.y} A ${PIE_RADIUS} ${PIE_RADIUS} 0 1 0 ${top.x} ${top.y} Z`
  }
  const from = polar(PIE_CENTRE_X, PIE_CENTRE_Y, PIE_RADIUS, end)
  const to = polar(PIE_CENTRE_X, PIE_CENTRE_Y, PIE_RADIUS, start)
  return `M ${PIE_CENTRE_X} ${PIE_CENTRE_Y} L ${from.x} ${from.y} A ${PIE_RADIUS} ${PIE_RADIUS} 0 ${sweep > 180 ? 1 : 0} 0 ${to.x} ${to.y} Z`
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
function PieChart(state, destinations, allocation, totalKg) {
  let cursor = 0
  const slices = destinations.map((destination, index) => {
    const share = typed(allocation?.[destination.code])
    const start = cursor
    cursor += share * 3.6
    return { destination, share, start, end: cursor, colour: PIE_COLOURS[index % PIE_COLOURS.length] }
  }).filter(slice => slice.share > 0)
  const paths = slices.map(slice => `<path d="${slicePath(slice.start, slice.end)}" fill="${slice.colour}"><title>${escapeHtml(slice.destination.name)} — ${formatNumber(slice.share, 1)}%</title></path>`).join('')
  const labels = slices.map(slice => {
    const middle = (slice.start + slice.end) / 2
    const edge = polar(PIE_CENTRE_X, PIE_CENTRE_Y, PIE_RADIUS + 4, middle)
    const elbow = polar(PIE_CENTRE_X, PIE_CENTRE_Y, PIE_RADIUS + 30, middle)
    const right = elbow.x >= PIE_CENTRE_X
    const endX = right ? 438 : 82
    const textX = right ? 446 : 74
    const anchor = right ? 'start' : 'end'
    return `<g class="improvement-pie-label"><polyline points="${edge.x},${edge.y} ${elbow.x},${elbow.y} ${endX},${elbow.y}" stroke="${slice.colour}"/><circle cx="${edge.x}" cy="${edge.y}" r="3" fill="${slice.colour}"/><text x="${textX}" y="${elbow.y + 4}" text-anchor="${anchor}">${formatNumber(slice.share, 1)}%</text></g>`
  }).join('')
  const legend = slices.map(slice => `<div><i style="background:${slice.colour}"></i><span>${escapeHtml(slice.destination.name)}</span></div>`).join('')
  return `<svg class="improvement-pie-chart" viewBox="0 0 520 420" role="img" aria-label="${escapeHtml(t('Total allocation'))}">${paths}<circle class="improvement-pie-centre" cx="${PIE_CENTRE_X}" cy="${PIE_CENTRE_Y}" r="48" fill="#fff"/><text class="improvement-pie-total" x="${PIE_CENTRE_X}" y="${PIE_CENTRE_Y - 5}" text-anchor="middle"><tspan>${formatNumber(totalKg, 2)}</tspan><tspan x="${PIE_CENTRE_X}" dy="20">kg</tspan></text>${labels}</svg><div class="improvement-pie-key">${legend}</div>`
}

export function openImprovement(state) {
  // Reconciled to the leaves rather than kept verbatim: a panel closed on one submission
  // and reopened on another must not carry a stale leaf's allocation into a new leaf's
  // sliders, and an allocation stored in the pre-fork shape (a bare object) is not leaf
  // zero's — `leafAllocations` answers both.
  const held = leafAllocations(state)
  const allocations = held.some(leaf => Object.keys(leaf).length) ? held : zeroAllocations(state)
  setState({ improvementOpen: true, improvedAllocations: allocations, improvementChartExpanded: null, improvementError: null })
}

// No longer an undo — the panel does not open on the current allocation any more, so
// there is nothing here to return *to*. It is kept as a shortcut to the current shares,
// and the button says so: `t('Match the current allocation')`, not `t('Reset to Current')`.
export function resetImprovement(state) {
  // **An identity on a forked chain, and that is the whole point of it.** One
  // submission-wide split applied to every leaf's own mass could satisfy this only when
  // the leaves happened to agree; `currentAllocationPercentages` is per leaf now, so
  // pressing this button and pressing Compare Impact reproduces the current scenario
  // exactly, whatever the leaves do.
  setState({ improvedAllocations: currentAllocationPercentages(state), improvementResult: null, improvementError: null })
}

// **A slider's `max` is fixed, never derived from headroom, and never recomputed after the
// row is drawn.** The previous round floored this at the row's own current value so the
// browser's silent "assigning a lower `max` clamps `.value`" could not fire against an
// UNTOUCHED row — but every OTHER slider's `max` was still recomputed from the remaining
// headroom on every keystroke, and a thumb's rendered position is `value / max`. Dragging
// one destination down grows the headroom, which grows every sibling's `max`, which moves
// their thumb left even though their stored `value` never changed — "I drag one and the
// others move" survives even once the value itself is protected. A fixed maximum removes
// the only thing that was making a sibling's thumb move: nothing about ANY other row's edit
// changes THIS row's `max` ever again, so `value / max` — and therefore the thumb — depends
// on nothing but this row's own value.
//
// `100` in percentage mode; the whole mass being redistributed, in this row's own display
// unit, in unit mode (`kgToUnitAmount(totalKg, rowUnit, presets)`, computed once at the call
// site below, where `totalKg` and `rowUnit` already are). The 100%-total rule has not gone
// away — it is now enforced only on the control being dragged, in `updateImprovementInput`'s
// per-input ceiling clamp, which never touches a sibling row's DOM at all.
function fixedRowMax(mode, totalKg, rowUnit, presets) {
  if (mode !== 'unit') return '100'
  const wholeAmount = kgToUnitAmount(totalKg, rowUnit, presets)
  const precision = unitDisplayPrecision(rowUnit, presets)
  return Number.isFinite(wholeAmount) && wholeAmount > 0 ? wholeAmount.toFixed(precision) : '0'
}

// The pointer-drag granularity: half a percentage point of the mass being redistributed,
// restated in a row's own display unit when the panel is in unit mode. `step="0.01"` gave
// the earlier percentage slider ten thousand stops and was "too sensitive" to land on with
// a pointer, so a drag is still rounded to this coarseness — but the rounding happens here,
// in `updateImprovementInput`, rather than through the range's own `step` attribute (see the
// row template): a native `step` snaps *any* value assigned to the control, including an
// exact figure mirrored in from the number box, which is what made a box reading `399.55`
// sit beside a range the browser had silently rounded to `400`. Doing it ourselves, only
// on the control the drag actually fired on, is what lets the box's exact value reach the
// range unrounded while a drag still lands on a nameable number.
//
// **The coarseness is worked out in kilograms first, in every unit.** Half a percent of the
// mass being redistributed is a fixed, meaningful step regardless of which unit a row
// happens to be shown in; only the number printed beside the thumb changes; converting
// *that* number's granularity would make a tonnes row and a kilograms row land on visibly
// different fractions of the same mass for no reason connected to either unit.
function rangeStep(mode, totalKg, rowUnit, presets) {
  if (mode !== 'unit') return 0.5
  const kgStep = Math.max(0.01, Number((totalKg * 0.005).toFixed(2)) || 0.5)
  const displayStep = kgToUnitAmount(kgStep, rowUnit, presets)
  return Number.isFinite(displayStep) && displayStep > 0 ? displayStep : 0.5
}

/**
 * Reads one control's keystroke and patches the DOM directly, the same bypass of
 * `setState` the module docstring above explains — a full re-render on every keystroke
 * loses the caret.
 *
 * **`state.improvedAllocations` is a percentage in every mode.** Item ⑧ added a
 * unit *display*, not a second place the allocation can live: storing a mass or a
 * container count instead would make the exactly-100 rule in `improvementValidation` a
 * floating-point comparison against a mass, and a mass that rounds differently at every
 * tonnage — or at every container size — would start refusing allocations that are
 * correct. So a keystroke here is converted to a percentage immediately —
 * `kgToPercentage(unitAmountToKg(control.value, rowUnit, presets), totalKg)` in unit mode,
 * `control.value` itself in percentage mode — and everything downstream of that line
 * (the total, the validation, the mirrored inputs' *raw displayed value*) is unchanged
 * by which mode, or which row's own unit, produced it.
 *
 * **A cleared box stays cleared, in both modes.** `kgToPercentage(NaN, totalKg)` and
 * `kgToPercentage('', totalKg)` both read as `0`, which is finite — so a unit box the
 * visitor had emptied was silently stored as an allocation of zero rather than as "not
 * answered yet", and `improvementValidation` had nothing to catch, only the mismatch it
 * produces once the other destinations no longer sum to 100%. Percentage mode never had
 * this: `control.value` passes straight through, so `''` reaches the state as `''` and the
 * blank check below fires on it directly. The empty string is now checked before either
 * conversion, in both modes, so it is what reaches the state either way.
 *
 * **The range carries `step="any"`, not a fixed step (see the row template).** A `step`
 * on `<input type="range">` snaps *anything* assigned to `.value` — an exact figure
 * mirrored in from the number box included — which is what put a box reading `399.55`
 * beside a range the browser had silently rounded to a multiple of five. With no native
 * step, an exact mirror lands exactly; a drag, which still has to land on a nameable
 * number, is rounded here instead, to `rangeStep`'s own coarseness, only when `control`
 * is the range itself.
 *
 * **The one coupling the client asked for — an upper bound, on the SLIDER only, enforced
 * on the value being entered rather than on anyone else's `max`.** A number box has
 * always been allowed to hold a figure `improvementValidation` will refuse — that is what
 * disables Compare and shows the message, and rewriting it here would take away the number
 * a visitor typed the moment it went out of range, which is a different defect
 * `test_the_improvement_percentage_refuses_a_minus_without_rewriting_the_number` exists to
 * catch (a clamp is exactly the "rewrite the number" that guard may not do). A range
 * control's own `max` is now fixed (see `fixedRowMax`) and never shrunk to enforce this, so
 * `ceiling` below — `this row's own current share plus whatever headroom the WHOLE
 * allocation has left` — is what stops a drag from pushing the total over 100%: it clamps
 * only the figure the control being dragged is about to store, never touches a sibling
 * row's `max`, `value` or rendered thumb, and at exactly 100% it reduces to "clamp back to
 * what this row already held" — a slider that cannot be increased, without moving anyone
 * else's.
 */
export function updateImprovementInput(control, state) {
  const code = control.dataset.improvementCode
  // **Which leaf's allocation this control edits.** Absent on a single-leaf panel, where
  // the markup is unchanged by the fork and leaf zero is the only leaf there is.
  const leafIndex = Number(control.dataset.improvementLeaf || 0)
  const leaves = submissionEntries(state)
  const allocations = leafAllocations(state)
  const mine = allocations[leafIndex] || {}
  const mode = state.improvementMode || 'percentage'
  const presets = state.taxonomy?.unit_presets || []
  const totalKg = leaves[leafIndex] ? leafAllocatableKg(leaves[leafIndex], presets) : 0
  const rowUnit = mode === 'unit' ? rowUnitFor(state, code) : null
  // Item ⑧: how many decimal places THIS row's own unit is worth printing (see
  // `unitDisplayPrecision`) — two for kilograms, more for tonnes and for a container
  // whose count would otherwise round away the same 0.01 kg kilograms is already
  // shown to. Percentage mode is unaffected; it never reads this.
  const precision = mode === 'unit' ? unitDisplayPrecision(rowUnit, presets) : 2
  let raw = control.value
  if (control.type === 'range' && raw !== '') {
    const step = rangeStep(mode, totalKg, rowUnit, presets)
    const numeric = Number(raw)
    if (Number.isFinite(numeric)) {
      const snapped = Math.round(numeric / step) * step
      raw = mode === 'unit' ? snapped.toFixed(precision) : String(Math.round(snapped * 100) / 100)
      control.value = raw
    }
  }
  let percentage = raw === '' ? '' : (mode === 'unit' ? kgToPercentage(unitAmountToKg(raw, rowUnit, presets), totalKg) : raw)
  // The ceiling below is a RANGE-only guard — see the docstring's note on why a number
  // box's out-of-range figure is refused by `improvementValidation`, never rewritten here.
  if (control.type === 'range' && percentage !== '') {
    const numericPercentage = Number(percentage)
    if (Number.isFinite(numericPercentage)) {
      const othersTotal = allocationTotal(mine) - typed(mine[code])
      const ceiling = Math.max(0, 100 - othersTotal)
      const clamped = Math.min(Math.max(0, numericPercentage), ceiling)
      if (clamped !== numericPercentage) {
        // Rounded to two decimal places: `othersTotal` is a running sum of floats, so
        // `ceiling` routinely lands a few units of float dust away from the clean
        // figure it means (`1.7999999999999998`, not `1.8`) — displaying that dust
        // on the control the visitor is looking at would read as a new, unexplained
        // bug the moment anyone dragged past their own headroom.
        percentage = Math.round(clamped * 100) / 100
        raw = mode === 'unit' ? displayAmount(percentage, totalKg, rowUnit, presets).toFixed(precision) : String(percentage)
        control.value = raw
      }
    }
  }
  allocations[leafIndex] = { ...mine, [code]: percentage }
  state.improvedAllocations = allocations
  state.improvementResult = null
  state.improvementError = null
  // **No sibling row's `max` is touched here, ever.** Every slider's `max` is fixed at
  // render time (`fixedRowMax`) from `totalKg` and its OWN row unit alone, neither of which
  // this function changes — so there is nothing left to recompute after a keystroke, and
  // no loop-ordering question between "raise the ceiling" and "mirror the value" the way a
  // headroom-derived `max` used to raise. Mirroring `raw` below can never be clamped by a
  // stale ceiling on THIS row either, because this row's own ceiling never moves: typing
  // past it (allowed on the box) mirrors straight onto the slider up to the fixed maximum,
  // exactly as before, with no ordering to get wrong.
  const total = allocationTotal(allocations[leafIndex])
  // Mirrors the *raw* value, not the percentage just computed: every control sharing this
  // code **and this leaf** is rendered in the same mode and the same row unit
  // (§ `ImprovementScenario`), so the slider and the number box always agree on which unit
  // `.value` is in and a straight copy is correct. `raw` rather than `control.value` so a
  // range's own drag mirrors its *rounded* (and, if it applied, *clamped*) figure, not the
  // pointer position that produced it.
  //
  // **Scoped to the leaf, since the fork.** Unscoped, dragging Landfill on the dairy card
  // wrote the same number into Landfill on every other food's card - the submission-wide
  // allocation reappearing through the DOM after it had been removed from the state.
  const within = document.querySelector(`[data-improvement-leaf-panel="${leafIndex}"]`) || document
  within.querySelectorAll(`[data-improvement-code="${CSS.escape(code)}"]`).forEach(input => {
    if (input !== control) input.value = raw
  })
  const error = improvementValidation(state)
  const totalPanel = within.querySelector ? within.querySelector('.improvement-total') : null
  ;(totalPanel || document.querySelector('.improvement-total'))?.classList.toggle('invalid', Boolean(error))
  const totalValue = document.getElementById(`improvement-total-value--${leafIndex}`) || document.getElementById('improvement-total-value')
  if (totalValue) totalValue.textContent = `${total.toFixed(2)}%`
  // This path deliberately patches the DOM rather than re-rendering (a re-render would take
  // the caret out of the box mid-number), so the donut has to be redrawn by hand or it would
  // show the allocation as it stood before the keystroke.
  const chart = document.querySelector(`[data-improvement-pie="${leafIndex}"]`)
  if (chart) chart.innerHTML = PieChart(state, sorted(state.taxonomy.destinations), allocations[leafIndex], totalKg)
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
 * in unit mode never typed a percentage and telling them to "enter a percentage" names a
 * unit their own screen does not show them. `state.improvementMode` decides which of the
 * two catalogue strings is returned; nothing about what is being checked changes.
 *
 * **Unit mode has no one unit to name.** Item ⑧'s single kilogram figure became a
 * per-row choice, so a message worded for kilograms alone would be wrong the moment any
 * row was switched to tonnes or a container — and different rows can be showing different
 * units at once. The unit-mode message therefore points at "the unit shown" rather than
 * naming one, which stays true regardless of what any individual row is set to.
 */
export function improvementValidation(state) {
  const mode = state.improvementMode || 'percentage'
  const rangeMessage = mode === 'unit'
    ? t('Enter an amount from 0 up to the total, in the unit shown, for every destination.')
    : t('Enter a percentage from 0 to 100 for every destination.')
  const presets = state.taxonomy?.unit_presets || []
  const leaves = submissionEntries(state)
  const allocations = leafAllocations(state)
  // **Every rule is per leaf, since the fork.** Each leaf's own sliders must total 100% of
  // *its* mass, which is what the server checks per entry — one submission-wide total of
  // 100% says nothing about whether any individual entry conserves its own mass, and it is
  // the entry the API refuses.
  //
  // The message names the food when there is more than one, because "allocations must
  // total 100%" pointing at no particular card is unactionable on a five-column panel.
  const named = (leaf, message) => (leaves.length === 1
    ? message
    : t('%(food)s: %(message)s', { food: leafDisplayName(leaf, state.taxonomy), message }))
  for (const [index, leaf] of leaves.entries()) {
    const mine = allocations[index] || {}
    const values = Object.values(mine)
    if (!values.length) return named(leaf, rangeMessage)
    if (values.some(value => value === '' || !Number.isFinite(Number(value)) || Number(value) < 0 || Number(value) > 100)) return named(leaf, rangeMessage)
    const total = allocationTotal(mine)
    const mismatch = t('Improved destination allocations must total 100%, so the improved scenario describes the same waste as the current one. Current total: %(total)s%.', { total: total.toFixed(2) })
    if (Math.abs(total - 100) > 0.01) return named(leaf, mismatch)
    const currentKg = leafAllocatableKg(leaf, presets)
    const improvedKg = sumQtyKg(improvedLines(leaf, mine, presets))
    if (Math.abs(improvedKg - currentKg) > MASS_TOLERANCE_KG) return named(leaf, mismatch)
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
    // **`submissionPayload` takes CHAINS and forks them itself.** Handing it
    // `submissionEntries` - which is already leaves - fans the leaves out a second time,
    // and a leaf has no `foodCategories`, so every entry came back as one blank
    // category-less leaf with `current: []` and the API answered 400. The panel is the
    // second builder of this body and this is exactly the class of drift the module note
    // in `submission.js` records; `alternativeFor` is still invoked with a leaf, because
    // that is what `submissionPayload` passes it.
    const presets = state.taxonomy?.unit_presets || []
    const allocations = leafAllocations(state)
    const response = await calculate(
      // `alternativeFor` is handed the leaf AND its position, and the position is what
      // selects that leaf's own allocation. Handed one shared map instead, every leaf
      // received the same shape and a chain whose leaves went to different destinations
      // could not be improved at all.
      submissionPayload(state, [...state.entries, draftEntry()], (leaf, index) => improvedLines(leaf, allocations[index] || {}, presets)),
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
// against the signature above it. `presets` and `rowUnit` joined them for the same reason
// item ⑧'s per-row unit selector needs both.
function DestinationAllocationRow({ destination, current, improved, mode, totalKg, presets, rowUnit, leafIndex }) {
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
  // **Suffixed by the leaf once there is more than one of it**, for the reason step 3's
  // field ids are: `<label for>` and `getElementById` need one element per id, and the
  // fork puts thirteen destinations on screen once per food. A single-leaf panel keeps
  // today's ids exactly, because it is today's panel.
  const id = leafIndex === null ? `improved-${slug(destination.code)}` : `improved-${slug(destination.code)}--${leafIndex}`
  // `data-improvement-leaf` is what `updateImprovementInput` reads to know whose
  // allocation a keystroke edits; absent on a single-leaf panel, where it defaults to 0.
  const leafAttr = leafIndex === null ? '' : ` data-improvement-leaf="${leafIndex}"`
  const unitMode = mode === 'unit'
  // Item ⑧: the control's raw `.value` is in the displayed unit, never the stored
  // percentage — `updateImprovementInput` is what converts back on the way in.
  //
  // **Percentage mode prints `improved` exactly as it always did — no `.toFixed`
  // added here.** `test_the_sliders_start_at_zero_and_the_total_says_so` reads the
  // sliders' own `.value` and requires the literal `"0"`; rounding every percentage to two
  // places for symmetry with the unit branch would have turned that into `"0.00"` for a
  // reason with nothing to do with units at all.
  // A genuinely blank allocation stays blank through a full re-render (a mode toggle, a
  // reopen) in both units — `displayAmount('', totalKg, unit, presets)` reads `Number('')`
  // as `0`, which is finite, so the unit branch alone would turn "not answered yet" into
  // "0.00" the moment the panel redrew, even though nothing was typed.
  // **The number of decimal places is the row's own unit's, not a flat two.** Item ⑧'s
  // whole defect (§ `unitDisplayPrecision`) was a tonnes row rounded to kilogram precision
  // — 5.90 kg read back as "0.01" t, indistinguishable from anywhere between 5 and 15 kg.
  const precision = unitMode ? unitDisplayPrecision(rowUnit, presets) : 2
  const value = improved === '' ? '' : (unitMode ? displayAmount(improved, totalKg, rowUnit, presets).toFixed(precision) : improved)
  // **Both controls' `max` are the same FIXED figure, and neither is ever touched again
  // after this render.** `100` in percentage mode; the whole mass being redistributed, in
  // THIS row's own unit, in unit mode — never this row's value plus headroom, and never
  // recomputed from any other row's edit (see `fixedRowMax`). The 100%-total rule is
  // enforced elsewhere, on the value being entered (`updateImprovementInput`'s own ceiling
  // clamp), not by shrinking what a slider is even capable of reaching.
  const ceiling = fixedRowMax(mode, totalKg, rowUnit, presets)
  const numberMax = unitMode ? (ceiling === '0' ? '' : ceiling) : 100
  // The number box stays the exact-entry control and the slider the coarse one in both
  // modes: `step` here is the row's own display precision (two decimal places for
  // kilograms, more for tonnes and for a container — see `unitDisplayPrecision`), which for
  // kilograms is the same ceiling `MASS_TOLERANCE_KG` checks, so a visitor typing to that
  // precision is typing to the precision the mass check actually honours. A tonnes or
  // container row is checked in kilograms regardless (`improvementValidation` converts
  // through `improvedLines`), so this is the box's own typing precision, not a second mass
  // rule per unit.
  const boxStep = unitMode ? (1 / 10 ** precision).toFixed(precision) : '0.01'
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
  // does.** An `aria-label` fixed to "percentage" in unit mode told a screen-reader
  // visitor the field wanted a percentage when it did not - the same defect as the
  // validation message above, on a surface a sighted visitor never sees at all. Naming the
  // unit itself, rather than saying "unit" generically, is the same reason a sighted
  // visitor is shown the row's own chosen unit rather than a placeholder word.
  const unitName = unitMode ? rowUnitName(rowUnit, presets) : ''
  const rangeLabel = unitMode
    ? t('Improved %(destination)s %(unit)s', { destination: destination.name, unit: unitName })
    : t('Improved %(destination)s percentage', { destination: destination.name })
  const boxLabel = unitMode
    ? t('Improved %(destination)s %(unit)s value', { destination: destination.name, unit: unitName })
    : t('Improved %(destination)s percentage value', { destination: destination.name })
  // The static `%` suffix becomes a real `<select>` in unit mode — kilograms, tonnes, and
  // every container the taxonomy offers (`rowUnitOptionsHtml`) — so **changing it changes
  // the presentation, not the value**: `data-improvement-unit-code` is read by the `change`
  // handler in `calculator.js`, which patches `state.improvementRowUnits` alone, and
  // `state.improvedAllocations[destination.code]` — the percentage this row actually means
  // — is untouched by it. Percentage mode keeps the plain `%` span: there is no second unit
  // to choose when the figure already is the unit.
  //
  // `aria-label="${t('Unit')}"`, bare, is `calculator.js`'s own row unit `<select>` (step 4)
  // repeated rather than a new key: neither names the destination, and the row's own visible
  // label already does that for a screen reader reading the row as a whole.
  // `title` carries the row's own unit name in full, unshortened — a container preset's
  // label (§7.7.7's staff text, never `t()`) can run well past what the select's own
  // bounded width shows (item ⑨), and `text-overflow: ellipsis` is not reliable on a closed
  // `<select>` across browsers, so a hover/focus tooltip is what still makes the full name
  // reachable rather than only ever guessable from what fits. The preset names themselves
  // are never shortened — every option keeps `preset.label` verbatim (see `rowUnitOptionsHtml`).
  const unitControl = unitMode
    ? `<select class="improvement-row-unit" data-improvement-unit-code="${escapeHtml(destination.code)}" aria-label="${escapeHtml(t('Unit'))}" title="${escapeHtml(unitName)}">${rowUnitOptionsHtml(presets, rowUnit)}</select>`
    : `<span>%</span>`
  return `<div class="improvement-allocation-row"><div><label for="${id}">${escapeHtml(destination.name)}</label><span>${escapeHtml(t('Current'))}: ${formatNumber(current, 2)}%</span></div><div class="improvement-control${unitMode ? ' improvement-control-unit' : ''}"><input id="${id}" type="range" min="0" max="${ceiling}" step="any" value="${escapeHtml(value)}" data-improvement-code="${escapeHtml(destination.code)}"${leafAttr} aria-label="${escapeHtml(rangeLabel)}"><div class="percentage-input"><input type="number" min="0" max="${numberMax}" step="${boxStep}" inputmode="decimal" value="${escapeHtml(value)}" data-improvement-code="${escapeHtml(destination.code)}"${leafAttr} aria-label="${escapeHtml(boxLabel)}">${unitControl}</div></div></div>`
}

/**
 * One leaf's editor: its own donut, its own thirteen destination rows, its own total.
 *
 * **A one-leaf submission renders exactly what it rendered before the fork** — no
 * heading, no wrapper section, the singleton ids — because that is the commonest journey
 * by far and nothing about it changed. The leaf index still reaches the controls, so the
 * keystroke path has one code path rather than two.
 */
function LeafAllocationEditor({ state, leaf, index, single, current, allocation, mode, presets }) {
  const totalKg = leafAllocatableKg(leaf, presets)
  const total = allocationTotal(allocation)
  const leafIndex = single ? null : index
  const rows = sorted(state.taxonomy.destinations).map(destination => DestinationAllocationRow({
    destination,
    current: current[destination.code] || 0,
    improved: allocation[destination.code] ?? 0,
    mode,
    totalKg,
    presets,
    rowUnit: rowUnitFor(state, destination.code),
    leafIndex,
  })).join('')
  const editor = `<div class="improvement-editor"><div class="improvement-pie-wrap"><div class="improvement-pie-content" data-improvement-pie="${index}">${PieChart(state, sorted(state.taxonomy.destinations), allocation, totalKg)}</div><button class="button button-secondary improvement-expand-chart" type="button" data-action="expand-improvement-chart" data-leaf="${index}"><span aria-hidden="true">&#9974;</span> ${escapeHtml(t('Total allocation'))}</button></div><div class="improvement-allocation-list">${rows}</div></div>`
  // **A one-leaf panel keeps the singleton ids**, for the reason step 3's fields do: it
  // is the same screen it was before the fork, and `#improvement-total-value` /
  // `#improvement-total-kg` are what `tests/web/test_step_navigation.py` reads the running
  // total off. With several leaves each total is its own element, suffixed by the leaf.
  const totalId = single ? 'improvement-total-value' : `improvement-total-value--${index}`
  const massId = single ? ' id="improvement-total-kg"' : ''
  const totalPanel = `<div class="improvement-total ${total && Math.abs(total - 100) > 0.01 ? 'invalid' : ''}" aria-live="polite"><span>${escapeHtml(t('Total allocation'))}</span><strong id="${totalId}">${total.toFixed(2)}%</strong><span class="improvement-total-mass">${escapeHtml(t('Total mass'))}: <strong${massId}>${formatNumber(totalKg, 2)}</strong> kg</span></div>`
  if (single) return editor + totalPanel
  return `<section class="improvement-leaf" data-improvement-leaf-panel="${index}"><h3 class="improvement-leaf__heading">${escapeHtml(leafDisplayName(leaf, state.taxonomy))}</h3>${editor}${totalPanel}</section>`
}

export function ImprovementScenario(state) {
  if (!state.improvementOpen) return `<section class="explore-improvements" id="improvement-section"><h2>${escapeHtml(t('Want to explore potential improvements?'))}</h2><p>${escapeHtml(t('Adjust how your food waste is managed to see how the environmental and economic impacts could change.'))}</p><button class="button button-primary" type="button" data-action="explore-improvements">${escapeHtml(t('Explore Improvements'))}</button></section>`
  const leaves = submissionEntries(state)
  const current = currentAllocationPercentages(state)
  const allocations = leafAllocations(state)
  const error = improvementValidation(state)
  // Item ⑧: a site manager thinks in tonnes or bins diverted, not in percentage points.
  // This decides only what the sliders and boxes below *display* — see the note on
  // `updateImprovementInput` for why the stored allocation is unaffected either way.
  // **Renamed from "kilograms" to "unit"**: the client asked for a per-row choice of
  // kilograms, tonnes, or a container, so the mode is no longer kilograms specifically —
  // it is "displayed in a unit", and which one is now a property of each row
  // (`state.improvementRowUnits`, read by `rowUnitFor`), not of the panel as a whole.
  const mode = state.improvementMode || 'percentage'
  const presets = state.taxonomy?.unit_presets || []
  const totalKg = totalAllocatableKg(state, presets)
  const modeField = `<div class="form-field improvement-mode-field"><label for="improvement-mode">${escapeHtml(t('Unit'))}</label><select id="improvement-mode"><option value="percentage" ${mode === 'percentage' ? 'selected' : ''}>${escapeHtml(t('Percentage'))}</option><option value="unit" ${mode === 'unit' ? 'selected' : ''}>${escapeHtml(t('Unit'))}</option></select></div>`
  const single = leaves.length === 1
  const editors = leaves.map((leaf, index) => LeafAllocationEditor({
    state, leaf, index, single, current: current[index] || {}, allocation: allocations[index] || {}, mode, presets,
  })).join('')
  // The intro says what is being redistributed. On a forked chain the answer is "each
  // food's own waste", and saying "the current waste amount" there would read as one pool
  // the visitor is splitting between foods, which is not what any of these sliders do.
  const intro = single
    ? t('Redistribute the current waste amount across different destinations. The total amount of waste should remain unchanged.')
    : t('Redistribute each food type\'s own waste across different destinations. Each food type\'s total must remain unchanged.')
  const expanded = Number.isInteger(state.improvementChartExpanded) ? state.improvementChartExpanded : null
  const expandedLeaf = expanded !== null ? leaves[expanded] : null
  const modal = expandedLeaf
    ? `<div class="improvement-chart-modal" role="dialog" aria-modal="true" aria-label="${escapeHtml(t('Total allocation'))}"><div class="improvement-chart-expanded"><button class="improvement-chart-close" type="button" data-action="close-improvement-chart" aria-label="${escapeHtml(t('Cancel'))}">×</button>${single ? '' : `<h3 class="improvement-leaf__heading">${escapeHtml(leafDisplayName(expandedLeaf, state.taxonomy))}</h3>`}${PieChart(state, sorted(state.taxonomy.destinations), allocations[expanded] || {}, leafAllocatableKg(expandedLeaf, presets))}</div></div>`
    : ''
  const submissionMass = single ? '' : `<p class="improvement-submission-mass">${escapeHtml(t('Total mass'))}: <strong id="improvement-total-kg">${formatNumber(totalKg, 2)}</strong> kg</p>`
  return `<section class="improvement-scenario" id="improvement-section" aria-labelledby="improvement-title"><h2 id="improvement-title">${escapeHtml(t('Create an Improvement Scenario'))}</h2><p>${escapeHtml(intro)}</p>${modeField}${editors}${submissionMass}<p class="field-error" id="improvement-inline-error" role="alert" ${error ? '' : 'hidden'}>${escapeHtml(error)}</p>${state.improvementError ? `<p class="field-error" role="alert">${escapeHtml(state.improvementError)}</p>` : ''}<div class="improvement-actions"><button class="button button-secondary" type="button" data-action="reset-improvement">${escapeHtml(t('Match the current allocation'))}</button><button class="button button-secondary" type="button" data-action="cancel-improvement">${escapeHtml(t('Cancel'))}</button><button class="button button-primary" type="button" data-action="compare-improvement" ${error || state.improvementLoading ? 'disabled' : ''}>${escapeHtml(state.improvementLoading ? t('Comparing…') : t('Compare Impact'))}</button></div>${modal}</section>`
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
  //
  // **This merge is by `code`, and §6.2 (v1.71) is what makes that safe.** Where several
  // equivalences are rungs of one ladder, the engine chooses the rung ONCE per
  // calculation -- from the whole submission's current scenario -- and puts the same one
  // in every `equivalences[]` on the response. A per-scenario choice would give this loop
  // two codes for one comparison and it would render two rows, each with one side filled
  // in: a before-and-after with nothing to compare. Do not "fix" that by pairing on
  // anything other than `code`; the guarantee is the engine's.
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

// §4.6's three-state rule for the one money field this module renders, mirroring
// `results.js::moneyFieldText` — a local copy rather than an import, for the same
// reason `nzd` and `hasValue` above are local copies (the import back would close a
// cycle). `complete` formats the figure; `incomplete` returns the shared sentence
// (the literal `t()` call has to live here too, not just in `results.js`, or the key
// extractor would not see this file ask for it); `not_supplied` — and any state this
// module has not learned — returns `null`, read as "print nothing".
function savingText(totals) {
  const saving = totals?.money?.saving_nzd
  if (hasValue(saving)) return nzd(saving)
  if (totals?.data_state?.saving_nzd === 'incomplete') {
    return t('Not every entry supplied this figure, so it cannot be totalled.')
  }
  return null
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
 * It only applies beside an actual figure, so it is left off when the row instead carries
 * the `incomplete` sentence.
 *
 * **On the same three-state terms as every other money field (§4.6), not a bare
 * `hasValue` check.** A bare `hasValue` printed nothing at all for an `incomplete`
 * submission, while the PDF (`api/pdf_render.py::_money_rows`, reading the same
 * `data_state.saving_nzd`) printed the shared sentence — the disagreement v1.50's
 * change-log item 4 rules out.
 *
 * The classes are `results.js`'s own `.money-row` / `.money-saving` / `.money-caveat`, so
 * the figure reads as the same kind of thing in both places rather than as a new widget.
 */
function comparisonSaving(result) {
  const totals = result.totals
  const text = savingText(totals)
  if (text === null) return ''
  const caveat = hasValue(totals?.money?.saving_nzd)
    ? `<p class="money-caveat">${escapeHtml(t('This assumes an even value per kilogram within each entry you priced, the way a box of produce is costed as a whole - not a measured price, and not an average taken across every entry.'))}</p>`
    : ''
  return `<div class="comparison-saving"><div class="money-row money-saving"><span class="money-label">${escapeHtml(t('Value of food not wasted at all'))}</span><span class="money-value">${escapeHtml(text)}</span></div>${caveat}</div>`
}

export function ComparisonResults(state) {
  const result = state.improvementResult
  if (!result?.totals?.alternative) return ''
  const data = comparisonData(result)
  const mock = result.factor_set?.is_mock
  return `<section class="comparison-results" id="comparison-results" aria-labelledby="comparison-results-title"><p class="eyebrow">${escapeHtml(t('Current Results → Improved Scenario'))}</p><h2 id="comparison-results-title">${escapeHtml(t('Compare Results'))}</h2>${mock ? `<p class="comparison-estimate-note">${escapeHtml(t('Demonstration only — this comparison uses mock factors and is not a verified impact result.'))}</p>` : ''}${ComparisonSummary(data, state.taxonomy)}${comparisonSaving(result)}<div class="impact-comparison-grid">${comparableCodes(data.metrics).map(code => ImpactComparisonCard(code, data.metrics[code], state.taxonomy)).join('')}</div>${ComparisonBars(data.metrics, state.taxonomy)}<section class="comparison-equivalent-section"><h2>${escapeHtml(t('Tangible equivalents'))}</h2>${equivalentComparison(data.equivalences)}</section></section>`
}
