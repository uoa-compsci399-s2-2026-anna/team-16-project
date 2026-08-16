import { calculate } from './api.js'
import { state, setState, resetCalculator, entryResultsFrom } from './state.js'
import { containerKg, countLimit, entryTotal, isPlainDecimal, kgString, kgToTonnes, massToKg } from './units.js'
import { escapeHtml, formatNumber, slug, stepNav } from './view.js'
import { t } from './i18n.js'
import { downloadResults, renderResults } from './results.js'
import { compareImprovement, openImprovement, resetImprovement, updateImprovementInput } from './improvement.js'

const decimalPattern = /^\d+(\.\d{1,2})?$/

// `state.totalUnit` is an internal value that is also printed beside every figure on
// three screens. The value never changes; only what is shown for it does. Metric unit
// SYMBOLS - kg, t, kg CO2e - are a different thing and are never translated: they come
// from `metric.unit` on the API response and are international notation.
const unitLabel = unit => (unit === 'tonnes' ? t('tonnes') : t('kilograms'))

// Destination amounts are entered to two decimal places, so two sums that agree to
// two decimal places are equal as far as the user is concerned. Binary floating point
// does not agree: 0.1 + 0.2 > 0.3. Same tolerance as improvement.js's 100% check.
const ALLOCATION_EPSILON = 0.01
const exceedsTotal = (allocated, total) => allocated - total > ALLOCATION_EPSILON
const remainingAmount = (total, allocated) => (Math.abs(total - allocated) <= ALLOCATION_EPSILON ? 0 : total - allocated)

// crypto.randomUUID() is [SecureContext] and so is undefined over plain http:// to a
// LAN IP. crypto.getRandomValues() is not. These ids never leave the browser.
function randomId() {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = [...bytes].map(byte => byte.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

const sorted = items => [...(items || [])].sort((a, b) => Number(a.sort_order || 0) - Number(b.sort_order || 0))
const selected = (items, code) => items.find(item => item.code === code)
// §6.1's `is_prevention`, not a code. A prevention destination is where waste that did
// not happen goes, so it belongs in the improvement scenario and nowhere else — and the
// server refuses one in a `current` scenario (§6.2), so a form that offered it here
// would be offering a 400. This read `code !== 'prevention'` until the flag existed,
// which left every other prevention destination — the ReFED comparison set brings its
// own — on the current-waste list.
// Exported for tests/web/test_entry_destinations.py, which runs it under Node against a
// taxonomy it builds — the same seam `buildResultsReport` was pulled out for (§7.3a).
export const entryDestinations = () => sorted(state.taxonomy.destinations).filter(destination => !destination.is_prevention)
const createLine = (destination, qtyInput = '') => ({ id: randomId(), destination, qtyInput })

// ---------------------------------------------------------------- containers
//
// §7.3's container input. A café or a school does not know how many kilograms it throws
// out; it knows it fills two 240 L wheelie bins a week, and making it guess a weight is
// how a made-up number gets into a public statistic (§7.6.1's whole point is that the
// numbers on this page are traceable).

// `units.js` holds both, because the results export needs the same reconciliation and
// cannot import this module. These three lines are the taxonomy binding: everything below
// asks about `state`'s presets without repeating where they live.
const presetList = () => state.taxonomy?.unit_presets || []
const containerTotal = entry => containerKg(entry, presetList())
const totalOf = entry => entryTotal(entry, presetList())

/**
 * The containers this visitor may choose from, in the order §6.1 served them.
 *
 * **Nothing is re-sorted here.** `unit_preset` is the one taxonomy table with no
 * `sort_order`, so `get_taxonomy` orders it by `kg_per_unit` — smallest first — and
 * sorting again in the browser could only disagree with the server. It would also mean
 * `Number(kg_per_unit)` on an API decimal, which §7.6 permits for display and charting
 * and not for ordering a form.
 *
 * **A preset naming a food category is a conversion that is only true of that food** —
 * a bin of bread and a bin of potatoes do not weigh the same, which is what §2.1's
 * nullable `food_category_id` is for. NULL means "every category" and those always show;
 * a named one shows only once step 2 has chosen it. Step 2 is optional, so a visitor who
 * skipped it sees the generic containers alone, which is the correct outcome: there is no
 * category to be specific about.
 *
 * Exported for `tests/web/test_unit_presets.py`, which runs it under Node against a
 * taxonomy it builds — the same seam `entryDestinations` is exported through, and for the
 * same reason: a test that greps this file for `food_category` asserts that a property
 * name was typed, not that a row leaves the list. **No shipped preset names a category**
 * (O-6: no measured per-food density exists), so nothing in a browser can exercise this
 * filter at all.
 */
export const containerPresets = () => presetList()
  .filter(preset => !preset.food_category || preset.food_category === state.foodCategory)

const totalNumber = entry => Number(totalOf(entry).amount) || 0

/**
 * This entry's total **in kilograms**, or `null` when it is not a finite mass.
 *
 * The one form of the total that can be compared against §6.2's bounds, because those
 * bounds are on kilograms and this field is only sometimes kilograms. `totalOf` has
 * already reconciled the three ways of entering it — a weight in kilograms, a weight in
 * tonnes, or a count of containers times a preset's `kg_per_unit` — so there is exactly
 * one conversion left and it is the one `reviewStep` prints.
 */
const totalKilograms = entry => {
  const total = totalOf(entry)
  return massToKg(total.amount, total.unit)
}

// ------------------------------------------------------------------ the ceilings
//
// **§6.2's own two numbers, and nothing invented.** `api/schemas.py` bounds one
// destination line at `MAX_LINE_QTY` = 10,000,000 kg and one entry's whole `current`
// scenario at `MAX_SCENARIO_QTY` = 50,000,000 kg. The column behind them is
// `DECIMAL(16,3)`, which is four orders of magnitude wider again and never the binding
// constraint. A client-side guard restates a server rule: it may refuse earlier and more
// kindly than the API would, and it must never refuse something the API would take.
//
// **Which bound goes on which field follows from what is actually sent.** The step-3
// total never crosses the wire at all — `buildLines` sends the *destination lines* — so
// the total's only job is to be the ceiling of the step-4 allocation, and the rule that
// belongs on it is the scenario cap. The line cap belongs on a destination row, where the
// number it bounds is the number that leaves the browser.
//
// Putting the *line* cap on the total instead is the tempting simplification and it is
// wrong: 30,000,000 kg split across three destinations is three legal lines and one legal
// scenario, and the API accepts it. Refusing that at step 3 would be this project's own
// definition of a defect.
const MAX_SCENARIO_KG = 50000000
const MAX_LINE_KG = 10000000

// A ceiling stated in the unit the visitor is typing in. "50,000 tonnes" is a number they
// can act on; "50,000,000 kg" on a field labelled tonnes is a conversion they have to do
// themselves to find out how far over they are.
const limitIn = (kilograms, unit) => (unit === 'tonnes' ? kgToTonnes(kilograms) : kilograms)

// Half a wheelie bin is a reasonable thing to say; two hundred and forty thousand of them
// is not. Ten thousand containers is about twenty-seven a day for a year — past any single
// site — and still leaves the largest seeded preset (1,100 L at 0.29 kg/L) at 3,190 t.
const MAX_CONTAINER_COUNT = 10000

/**
 * The count this container may be entered to: the plausibility bound above, or the
 * scenario ceiling expressed in these containers, whichever is smaller.
 *
 * **One number for the check and for the message.** `kg_per_unit` is staff-editable
 * (§8.1), so `MAX_CONTAINER_COUNT` alone stops bounding the mass the moment somebody
 * enters a preset heavier than 5,000 kg — and a second, separate kilogram check would
 * have to be worded about kilograms on a field holding a count, and could disagree with
 * this one by a container at the boundary. Folding the ceiling into the count means the
 * refusal stays the sentence it already was, with a smaller number in it.
 */
const containerLimit = () => Math.min(MAX_CONTAINER_COUNT, countLimit(state.unitPreset, presetList(), MAX_SCENARIO_KG))

/**
 * Whether the chosen preset would still be on the list under `foodCategory`.
 *
 * A preset tied to a food category leaves the list when a different category is chosen,
 * and a selection that is no longer on the list is a conversion the visitor can no longer
 * see being applied to their total. It goes with the category. This is not tidying: a
 * stale factor still in force behind a changed choice is the exact shape of the defects
 * §10 keeps recording.
 */
function presetSurvives(foodCategory) {
  const preset = selected(presetList(), state.unitPreset)
  return !preset || !preset.food_category || preset.food_category === foodCategory
}

/**
 * The state patch that goes with a food-category change.
 *
 * Empty when the chosen container is still on the list. When it is not, the form falls all
 * the way back to a weight rather than keeping `measureMode: 'container'` with nothing
 * selected — which would leave the `<select>` showing kilograms (no option matches the
 * dead value, so the browser falls to the first) while `state` still said container: two
 * halves of one control disagreeing, which is worse than asking again.
 */
const presetPatch = foodCategory => (presetSurvives(foodCategory)
  ? {}
  : { unitPreset: null, measureMode: 'mass', totalUnit: 'kilograms', current: [] })

// §7.3: `kgString` is `massToKg(...).toFixed(3)`, and it exists so the rounding to the
// API's three decimal places happens in `units.js` rather than at each call site. Both
// sites here spelled it out instead, which is the same defect `kgToTonnes` was added for.
// A blank row stays blank — `kgString('', unit)` is "0.000", and a row the user has not
// filled is not a row holding zero.
const normaliseLines = lines => lines.map(line => ({ ...line, qtyKg: line.qtyInput === '' ? '' : kgString(line.qtyInput, state.totalUnit) }))
const allocatedAmount = lines => lines.reduce((sum, line) => sum + (Number(line.qtyInput) || 0), 0)
// `unitPreset` and `unitCount` are here for the same reason `totalAmount` is: they are
// something the visitor entered, so the Clear button has to appear once either exists.
const hasData = () => Boolean(state.entries.length || state.sector || state.foodCategory || state.totalAmount || state.unitPreset || state.unitCount || state.current.some(line => line.qtyInput !== '') || state.result)
const draftEntry = () => ({ sector: state.sector, foodCategory: state.foodCategory, totalAmount: state.totalAmount, totalUnit: state.totalUnit, measureMode: state.measureMode, unitPreset: state.unitPreset, unitCount: state.unitCount, current: state.current.map(line => ({ ...line })) })

/**
 * **There is no introduction screen, and `state.step` no longer has a -1.**
 *
 * What stood here was a landing screen: an eyebrow, an `<h1>`, a lead paragraph, a
 * "Start calculator" button, a decorative arch band and a "What you will need" panel.
 * It was reached by every visitor because `nginx` served `index.html` at `/`, which
 * made the calculator the de-facto home page while `home.html` - first in every other
 * page's navigation - went undiscovered.
 *
 * `/` now serves `home.html`. That leaves this screen saying, one click later, what the
 * page before it had just said, with a second button to press; the middle click carried
 * no information. So the hero and the button are gone, and the two things worth keeping
 * moved to `home.html` verbatim rather than being rewritten: the "What you will need"
 * list, which is genuinely step zero and is more use before the click than after it, and
 * the arch band, which is the brand's primary supporting graphic.
 *
 * **Do not reinstate a landing screen here.** Two landing pages was the defect; a second
 * one behind a link from the first is the same defect with an extra click.
 */
function sectorStep() {
  const sectors = sorted(state.taxonomy.sectors)
  return `<section class="content-section" aria-labelledby="stage-title"><p class="eyebrow">${escapeHtml(t('Step %(step)s', { step: 1 }))}</p><h1 id="stage-title">${escapeHtml(t('Where in the food supply chain did this waste occur?'))}</h1><p class="section-intro" id="supply-chain-support">${escapeHtml(t('Choose the stage that best describes where the food waste was generated.'))}</p>
    <fieldset class="stage-fieldset" aria-describedby="supply-chain-support"><legend>${escapeHtml(t('Supply-chain stage'))} <span class="required">${escapeHtml(t('(required)'))}</span></legend><div class="stage-card-list">${sectors.map(sector => {
      const isSelected = state.sector === sector.code
      const expanded = state.expandedSectors.includes(sector.code)
      const id = `sector-${slug(sector.code)}`
      return `<div class="stage-card ${isSelected ? 'selected' : ''}"><label class="stage-select" for="${id}"><input id="${id}" name="sector" type="radio" value="${escapeHtml(sector.code)}" ${isSelected ? 'checked' : ''}><span class="stage-copy"><span class="stage-title">${escapeHtml(sector.name)}</span><span class="stage-description">${escapeHtml(sector.description || '')}</span></span>${isSelected ? `<span class="selected-label" aria-hidden="true">✓ ${escapeHtml(t('Selected'))}</span>` : ''}</label><button class="details-button" type="button" data-action="toggle-sector" data-sector="${escapeHtml(sector.code)}" aria-expanded="${expanded}" aria-controls="${id}-details" aria-label="${escapeHtml(expanded ? t('Hide details for %(name)s', { name: sector.name }) : t('Show details for %(name)s', { name: sector.name }))}">${escapeHtml(t('Details'))} <span class="chevron ${expanded ? 'expanded' : ''}" aria-hidden="true">⌄</span></button><div class="stage-details" id="${id}-details" ${expanded ? '' : 'hidden'}><p>${escapeHtml(sector.details || sector.description || t('Additional details have not been supplied.'))}</p></div></div>`
    }).join('')}</div>${state.error ? `<p class="field-error" role="alert">${escapeHtml(state.error)}</p>` : ''}</fieldset>${stepNav({ step: 0, back: null })}</section>`
}

function foodStep() {
  const categories = sorted(state.taxonomy.food_categories)
  return `<section class="content-section" aria-labelledby="food-title"><p class="eyebrow">${escapeHtml(t('Step %(step)s', { step: 2 }))} · ${escapeHtml(t('Optional'))}</p><h1 id="food-title">${escapeHtml(t('What type of food waste are you measuring?'))}</h1><p class="section-intro">${escapeHtml(t('Choose one category if you know it, or continue without selecting an option.'))}</p><fieldset class="choice-fieldset"><legend class="sr-only">${escapeHtml(t('Food type'))}</legend><div class="simple-choice-list">${categories.map(category => {
    const isSelected = state.foodCategory === category.code
    return `<label class="simple-choice ${isSelected ? 'selected' : ''}"><input id="food-category-${slug(category.code)}" type="radio" name="food-category" value="${escapeHtml(category.code)}" ${isSelected ? 'checked' : ''}><span><strong>${escapeHtml(category.name)}</strong>${category.is_standard_mix ? `<small>${escapeHtml(t('Recommended if you do not separate food waste by category'))}</small>` : ''}</span>${isSelected ? `<span class="selected-label" aria-hidden="true">✓ ${escapeHtml(t('Selected'))}</span>` : ''}</label>`
  }).join('')}</div></fieldset>${state.foodCategory ? `<button type="button" class="text-button" data-action="clear-food">${escapeHtml(t('Clear optional selection'))}</button>` : ''}${stepNav({ step: 1, back: 0 })}</section>`
}

/**
 * The `<select>` value that names a container, distinguished from a mass unit by a prefix.
 *
 * The prefix is not decoration. `kilograms` and `tonnes` share this control's value space
 * with every `unit_preset.code` in the taxonomy, and a preset coded `tonnes` — which
 * nothing forbids, `code` being staff-editable (§8.1) — would otherwise silently become
 * the tonnes option and convert nothing.
 */
const PRESET_OPTION = 'preset:'
const unitSelectValue = () =>
  (state.measureMode === 'container' ? PRESET_OPTION + (state.unitPreset || '') : state.totalUnit)

/**
 * The running kilogram total, as **plain text**.
 *
 * Not escaped here, because it has two consumers that need opposite things: the template
 * below interpolates it into `innerHTML` and escapes it there, while `updateContainerCount`
 * assigns it to `textContent`, which escapes nothing and unescapes nothing. Returning
 * escaped text would put a literal `&#039;` on screen in every language whose wording
 * carries an apostrophe — French and Italian among them.
 *
 * `kg` is outside `t()`. §7.7.7: metric units are international notation and are never
 * translated, and `reviewLines` already writes it as a literal for the same reason.
 */
function containerTotalText() {
  if (!decimalPattern.test(state.unitCount || '')) return ''
  const kilograms = containerTotal(state)
  if (kilograms === '') return ''
  return t('That is about %(mass)s in total.', { mass: `${formatNumber(kilograms, 3)} kg` })
}

/**
 * Step 3.
 *
 * **The containers are options on the unit `<select>`, not a second mode with its own
 * fieldset, and the reason is measured.** The first build of this input put a "By weight /
 * By container" radio pair above the form: two `.simple-choice` cards and a legend, 169px
 * at 1278x983 on a step that had 50px of headroom. It pushed the document to 1102px in a
 * 983px viewport and broke three of `tests/web/test_step_navigation.py`'s assertions at
 * once — §7.6.3's rule that advancing must never require scrolling, which this project
 * measures rather than assumes.
 *
 * Folding the containers into the control that was already asking the question costs
 * **nothing**: "1,200 kilograms" and "2 × 240 L wheelie bin" are one question with one
 * answer, an `<optgroup>` makes the second half discoverable without a second control, and
 * the running total is the only element container mode adds — 24px, and only once a
 * container is chosen.
 *
 * The amount field keeps its position and changes its identity with the mode: `#total-waste`
 * for a mass, `#unit-count` for a count. Two ids rather than one because the two are
 * different quantities and `state` holds them apart — switching from 1,200 kilograms to
 * wheelie bins must not carry 1,200 over as a bin count.
 *
 * **The running total goes inside the count's own field, under the input.** It sat after
 * the whole panel first, which reads fine on a desktop and put it *behind the sticky
 * navigation bar at 390x700* — the one width where the confirmation matters most, hidden
 * by the element that is always on screen. Under the input it is beside the number it
 * describes at every width, and it is the field's own last child so nothing separates
 * "2" from "about 139.200 kg".
 */
function amountStep() {
  const container = state.measureMode === 'container'
  const presets = containerPresets()
  const amountId = container ? 'unit-count' : 'total-waste'
  const amountLabel = container ? t('How many containers?') : t('Waste amount')
  const amountHint = container
    ? t('Use up to two decimal places — enter 0.5 for a half-full container.')
    : t('Use up to two decimal places.')
  const amountValue = container ? state.unitCount : state.totalAmount
  return `<section class="content-section" aria-labelledby="amount-title"><p class="eyebrow">${escapeHtml(t('Step %(step)s', { step: 3 }))}</p><h1 id="amount-title">${escapeHtml(t('How much food waste are you measuring?'))}</h1><p class="section-intro">${escapeHtml(t('Enter the total amount. You will allocate this total across destinations in the next step.'))}</p><div class="form-panel amount-grid"><div class="form-field ${state.error ? 'has-error' : ''}"><label for="${amountId}">${escapeHtml(amountLabel)} <span class="required">${escapeHtml(t('(required)'))}</span></label><p class="field-hint">${escapeHtml(amountHint)}</p><input id="${amountId}" type="number" inputmode="decimal" min="0" step="0.01" value="${escapeHtml(amountValue)}" ${state.error ? 'aria-invalid="true" aria-describedby="amount-error"' : ''}>${state.error ? `<p class="field-error" id="amount-error" role="alert">${escapeHtml(state.error)}</p>` : ''}${container ? `<p class="container-total" id="container-total" aria-live="polite">${escapeHtml(containerTotalText())}</p>` : ''}</div><div class="form-field"><label for="total-unit">${escapeHtml(t('Unit'))} <span class="required">${escapeHtml(t('(required)'))}</span></label><p class="field-hint">${escapeHtml(t('Choose a weight, or the container you fill.'))}</p><select id="total-unit"><optgroup label="${escapeHtml(t('Weight'))}"><option value="kilograms" ${unitSelectValue() === 'kilograms' ? 'selected' : ''}>${escapeHtml(t('kilograms'))}</option><option value="tonnes" ${unitSelectValue() === 'tonnes' ? 'selected' : ''}>${escapeHtml(t('tonnes'))}</option></optgroup>${presets.length ? `<optgroup label="${escapeHtml(t('Containers'))}">${presets.map(preset => {
    // `preset.label` is `unit_preset.label` — staff-typed, and §7.7.7's ruling of
    // 14 August is that anything a staff member can edit is published exactly as
    // written. It is escaped and it is never passed through `t()`. Everything
    // around it is translated, the `<optgroup>` labels included; a Thai visitor gets
    // a Thai form listing English container names, which is the stated and accepted
    // consequence of that rule.
    const value = PRESET_OPTION + preset.code
    return `<option value="${escapeHtml(value)}" ${unitSelectValue() === value ? 'selected' : ''}>${escapeHtml(preset.label)}</option>`
  }).join('')}</optgroup>` : ''}</select></div></div>${stepNav({ step: 2, back: 1 })}</section>`
}

/**
 * The `entries[N].current[M].qty_kg` path §9 will use to name each row of the draft entry,
 * aligned with `state.current` so the render loop can look up its own row, and `null` for
 * a row the request never carried.
 *
 * Two things have to agree with what `submitCalculation` actually posted, and neither
 * follows from the render loop:
 *
 *   * **`buildLines` drops every blank row before sending**, so a row's request index is
 *     its position among the *submitted* lines. That is not its position in
 *     `state.current` as soon as one destination is left empty — which is the normal
 *     case, and a mismatch that predates the contract fork.
 *   * **the draft entry travels last**, at `entries[state.entries.length]`. §9's paths are
 *     rooted at the request body, so a bare `current[i].qty_kg` key can never match one.
 *
 * Both failures are silent: no error, no console warning, just the generic banner.
 */
function draftFieldPaths() {
  const entryIndex = state.entries.length
  let sent = 0
  return normaliseLines(state.current).map(line => (Number(line.qtyKg) > 0 ? `entries[${entryIndex}].current[${sent++}].qty_kg` : null))
}

function destinationRows() {
  const allocationExcess = exceedsTotal(allocatedAmount(state.current), totalNumber(state))
  const paths = draftFieldPaths()
  return state.current.map((line, index) => {
    const destination = selected(state.taxonomy.destinations, line.destination)
    const serverError = paths[index] ? state.fieldErrors[paths[index]] : null
    const invalid = Boolean(serverError) || (line.qtyInput !== '' && Number(line.qtyInput) < 0) || (allocationExcess && state.lastChangedDestination === line.destination)
    return `<div class="destination-row ${invalid ? 'invalid' : ''}"><label for="destination-${line.id}">${escapeHtml(destination?.name || line.destination)}${destination?.description ? `<small>${escapeHtml(destination.description)}</small>` : ''}</label><div class="amount-with-unit"><input id="destination-${line.id}" data-line-field="amount" data-line-id="${line.id}" type="number" inputmode="decimal" min="0" step="0.01" value="${escapeHtml(line.qtyInput)}" ${invalid ? 'aria-invalid="true"' : ''} aria-label="${escapeHtml(t('%(destination)s amount in %(unit)s', { destination: destination?.name || line.destination, unit: unitLabel(state.totalUnit) }))}"><span>${escapeHtml(unitLabel(state.totalUnit))}</span></div>${serverError ? `<p class="field-error" role="alert">${escapeHtml(serverError)}</p>` : ''}</div>`
  }).join('')
}

function destinationStep() {
  const total = totalNumber(state)
  const allocated = allocatedAmount(state.current)
  const summaryInvalid = exceedsTotal(allocated, total) || state.current.some(line => line.qtyInput !== '' && Number(line.qtyInput) < 0)
  // **The same question `updateLine` asks, asked the same way.** This used to restate
  // three of `validateCurrentStep`'s rules inline and so knew nothing about the rest: it
  // is the render path, reached by returning to step 4 from step 5, while `updateLine`'s
  // `continueButton.disabled = Boolean(error)` is the keystroke path — two lists of rules
  // for one button, and any rule added to one of them left the other enabling Continue on
  // a state the other had just refused. There is one list now, and it is the one whose
  // message the visitor is shown when they press it anyway.
  const canContinue = !validateCurrentStep()
  return `<section class="content-section wide" aria-labelledby="destination-title"><p class="eyebrow">${escapeHtml(t('Step %(step)s', { step: 4 }))}</p><h1 id="destination-title">${escapeHtml(t('Where did the food waste go?'))}</h1><p class="section-intro">${escapeHtml(t('Enter an amount for every applicable destination. The combined amount cannot exceed your total waste.'))}</p>
    <div class="allocation-summary ${summaryInvalid ? 'invalid' : ''}" id="current-summary" aria-live="polite"><div><span>${escapeHtml(t('Total waste'))}</span><strong>${formatNumber(total, 2)} ${escapeHtml(unitLabel(state.totalUnit))}</strong></div><div><span>${escapeHtml(t('Allocated'))}</span><strong data-summary="allocated">${formatNumber(allocated, 2)} ${escapeHtml(unitLabel(state.totalUnit))}</strong></div><div><span>${escapeHtml(t('Remaining'))}</span><strong data-summary="remaining">${formatNumber(remainingAmount(total, allocated), 2)} ${escapeHtml(unitLabel(state.totalUnit))}</strong></div></div>
    <div class="destination-list">${destinationRows()}</div>
    <p class="field-error" id="allocation-error" role="alert">${escapeHtml(state.error || '')}</p>${stepNav({ step: 3, back: 2, disabled: !canContinue })}</section>`
}

function reviewLines(entry) {
  const lines = normaliseEntryLines(entry).filter(line => Number(line.qtyKg) > 0)
  return `<dl class="review-destinations">${lines.map(line => {
    const destination = selected(state.taxonomy.destinations, line.destination)
    return `<div><dt>${escapeHtml(destination?.name || line.destination)}</dt><dd>${formatNumber(line.qtyInput, 2)} ${escapeHtml(unitLabel(entry.totalUnit))} <small>(${formatNumber(line.qtyKg, 3)} kg)</small></dd></div>`
  }).join('')}</dl>`
}

function normaliseEntryLines(entry) {
  return entry.current.map(line => ({ ...line, qtyKg: line.qtyInput === '' ? '' : kgString(line.qtyInput, entry.totalUnit) }))
}

/**
 * What step 3 was told, in the words it was told in — **already escaped**, so no call
 * site may escape it again.
 *
 * A container entry says how many of which container, because "139.200 kg" is not what
 * the visitor entered and is not what they can check. The kilograms are printed beside
 * it, never instead of it. `preset.label` is staff-typed and published as written
 * (§7.7.7), so it is escaped and never translated.
 */
function measuredAs(entry) {
  if (entry.measureMode !== 'container') {
    return `${formatNumber(entry.totalAmount, 2)} ${escapeHtml(unitLabel(entry.totalUnit))}`
  }
  const preset = selected(presetList(), entry.unitPreset)
  return `${formatNumber(entry.unitCount, 2)} × ${escapeHtml(preset?.label || entry.unitPreset || '')}`
}

function entryCard(entry, index) {
  const sector = selected(state.taxonomy.sectors, entry.sector)
  const food = selected(state.taxonomy.food_categories, entry.foodCategory)
  return `<article class="saved-entry-card"><div><span class="eyebrow">${escapeHtml(t('Entry %(number)s', { number: index + 1 }))}</span><h3>${escapeHtml(sector?.name || entry.sector)}</h3><p>${measuredAs(entry)} · ${escapeHtml(food?.name || t('Food type not provided'))}</p></div><div class="card-actions"><button class="text-button" type="button" data-action="edit-entry" data-index="${index}">${escapeHtml(t('Edit'))}<span class="sr-only"> ${escapeHtml(t('entry %(number)s', { number: index + 1 }))}</span></button><button class="text-button danger" type="button" data-action="remove-entry" data-index="${index}">${escapeHtml(t('Remove'))}<span class="sr-only"> ${escapeHtml(t('entry %(number)s', { number: index + 1 }))}</span></button></div></article>`
}

function reviewStep() {
  const sector = selected(state.taxonomy.sectors, state.sector)
  const food = selected(state.taxonomy.food_categories, state.foodCategory)
  const total = totalOf(state)
  const totalKg = massToKg(total.amount, total.unit)
  // §7.6.2: the placeholder wording is conditional on `is_mock`, never unconditional. It was
  // hard-coded here, which is correct only while every factor set is mock and inverts the day
  // a real one is published — a screen that tells a user their verified factors have not been
  // supplied. §6.1 puts `factor_set` on the taxonomy response, which is what this step has.
  const mock = state.taxonomy?.factor_set?.is_mock
  const estimateNotice = mock
    ? t('Demonstration only — verified calculation factors have not yet been supplied. Final results will depend on factors supplied and approved by Kai Commitment.')
    : t('Results are estimates, produced from the calculation factors supplied and approved by Kai Commitment.')
  return `<section class="content-section wide" aria-labelledby="review-title"><p class="eyebrow">${escapeHtml(t('Step %(step)s', { step: 5 }))}</p><h1 id="review-title">${escapeHtml(t('Review your information'))}</h1><p class="section-intro">${escapeHtml(t('Check this entry, or add another supply-chain entry before viewing the combined results.'))}</p>
    ${state.entries.length ? `<section class="saved-entries"><div class="section-heading-row"><h2>${escapeHtml(t('Added entries'))}</h2><span>${state.entries.length}</span></div>${state.entries.map(entryCard).join('')}</section>` : ''}
    <div class="section-heading-row current-entry-heading"><h2>${escapeHtml(t('Current entry %(number)s', { number: state.entries.length + 1 }))}</h2><span>${escapeHtml(t('Ready to calculate'))}</span></div>
    <article class="review-block"><div class="section-heading-row"><h2>${escapeHtml(t('Supply-chain stage'))}</h2><button class="text-button" type="button" data-action="go-step" data-step="0">${escapeHtml(t('Edit'))}</button></div><p>${escapeHtml(sector?.name || state.sector)}</p></article>
    <article class="review-block"><div class="section-heading-row"><h2>${escapeHtml(t('Food category'))}</h2><button class="text-button" type="button" data-action="go-step" data-step="1">${escapeHtml(t('Edit'))}</button></div><p>${escapeHtml(food?.name || t('Standard mix / not specified'))}</p></article>
    <article class="review-block"><div class="section-heading-row"><h2>${escapeHtml(t('Waste amount'))}</h2><button class="text-button" type="button" data-action="go-step" data-step="2">${escapeHtml(t('Edit'))}</button></div><p><strong>${measuredAs(state)}</strong> · ${formatNumber(totalKg, 3)} kg</p></article>
    <article class="review-block"><div class="section-heading-row"><h2>${escapeHtml(t('Waste destinations'))}</h2><button class="text-button" type="button" data-action="go-step" data-step="3">${escapeHtml(t('Edit'))}</button></div>${reviewLines(draftEntry())}</article>
    <button class="button button-add add-entry-button" type="button" data-action="add-entry">+ ${escapeHtml(t('Add another supply-chain entry'))}</button>
    <aside class="disclaimer compact" aria-label="${escapeHtml(t('Important information'))}"><span class="info-icon" aria-hidden="true">i</span><div><strong>${escapeHtml(t('Estimate notice'))}</strong><p>${escapeHtml(estimateNotice)}</p></div></aside>
    ${state.error ? `<p class="field-error api-error ${state.errorCode ? `error-${slug(state.errorCode)}` : ''}" role="alert">${escapeHtml(state.error)}</p>` : ''}${stepNav({
      step: 4,
      back: 3,
      label: state.loading ? t('Calculating…') : Date.now() < state.rateLimitedUntil ? t('Try again shortly') : state.entries.length ? t('Calculate results for %(count)s entries', { count: state.entries.length + 1 }) : t('Calculate impact'),
      disabled: state.loading || Date.now() < state.rateLimitedUntil || blocked(),
      action: 'calculate',
    })}</section>`
}

function validateCurrentStep() {
  if (state.step === 0 && !state.sector) return t('Select where in the food supply chain the waste occurred.')
  if (state.step === 2 && state.measureMode === 'container') {
    // **The two-decimal rule applies to the count, which is what the visitor typed.** It
    // is an input rule about typing, not a property of the total: `toKg` returns three
    // decimals because that is what §6.2 accepts, and a total of "139.200" is not a
    // number anybody entered. §7.2 recorded this collision as the decision building this
    // input would require; this is the decision.
    if (!state.unitCount || Number(state.unitCount) <= 0) return t('Number of containers must be greater than zero.')
    // `1e5` is a value a number input hands over, it is not written as a decimal, and it
    // has no decimal places — so the two-decimal message was the wrong sentence for it.
    // Ask the two questions separately and each answer is true of what was typed.
    if (!isPlainDecimal(state.unitCount)) return t('Write the number out in full, using digits only.')
    if (!decimalPattern.test(state.unitCount)) return t('Enter no more than two decimal places.')
    // A preset whose code the taxonomy no longer holds, or whose `kg_per_unit` will not
    // parse, leaves `containerKg` at '' — and a step that continued on that would carry a
    // zero total into step 4 and refuse every allocation with a message about the
    // allocation. Say what is actually wrong instead.
    //
    // **Ahead of the count bound, because that bound is derived from the same missing
    // number.** `countLimit` answers 0 when there is no usable conversion, so asking the
    // bound first would refuse a perfectly ordinary count with "Enter no more than 0
    // containers." — a sentence about the visitor's typing for a fault in the taxonomy.
    if (!state.unitPreset || containerTotal(state) === '') return t('That container is no longer available. Choose another.')
    if (Number(state.unitCount) > containerLimit()) return t('Enter no more than %(limit)s containers.', { limit: formatNumber(containerLimit(), 0) })
  }
  if (state.step === 2 && state.measureMode !== 'container') {
    if (!state.totalAmount || Number(state.totalAmount) <= 0) return t('Waste amount must be greater than zero.')
    if (!isPlainDecimal(state.totalAmount)) return t('Write the number out in full, using digits only.')
    if (!decimalPattern.test(state.totalAmount)) return t('Enter no more than two decimal places.')
    // **`null` is over the ceiling, not under it.** `massToKg` answers `null` when there
    // is no finite mass, and `null > MAX` is `false` — so a bare comparison lets the one
    // case the whole guard exists for straight through. It is reachable: 308 nines is the
    // longest run a number input keeps (309 is outside a double's range and the browser
    // blanks it), it is a plain decimal, it passes the two-decimal rule, and *as tonnes*
    // it is `Infinity` kilograms.
    const kilograms = totalKilograms(state)
    if (kilograms === null || kilograms > MAX_SCENARIO_KG) {
      return t('Enter no more than %(limit)s %(unit)s.', { limit: formatNumber(limitIn(MAX_SCENARIO_KG, state.totalUnit), 0), unit: unitLabel(state.totalUnit) })
    }
  }
  if (state.step === 3) {
    const total = totalNumber(state)
    const lines = state.current
    if (!lines.some(line => Number(line.qtyInput) > 0)) return t('Enter an amount for at least one waste destination.')
    if (lines.some(line => line.qtyInput !== '' && Number(line.qtyInput) < 0)) return t('Destination amounts must be zero or greater.')
    if (lines.some(line => line.qtyInput && !isPlainDecimal(line.qtyInput))) return t('Write the number out in full, using digits only.')
    if (lines.some(line => line.qtyInput && !decimalPattern.test(line.qtyInput))) return t('Enter destination amounts to no more than two decimal places.')
    // §6.2's per-line bound, restated. It is not implied by the total: the total is capped
    // at the *scenario* ceiling, which is five lines' worth, so a visitor who puts all of
    // a legal total into one destination is over this one and under that one.
    //
    // The finiteness check used to be folded into the negative rule two lines above,
    // which answered "Destination amounts must be zero or greater" for a row far too
    // large to be either. It is asked here, through the same `null`, where the true
    // answer is that the row is over the limit and the message says which limit.
    const overLine = lines.some(line => {
      if (line.qtyInput === '') return false
      const kilograms = massToKg(line.qtyInput, state.totalUnit)
      return kilograms === null || kilograms > MAX_LINE_KG
    })
    if (overLine) return t('Enter destination amounts of no more than %(limit)s %(unit)s.', { limit: formatNumber(limitIn(MAX_LINE_KG, state.totalUnit), 0), unit: unitLabel(state.totalUnit) })
    const sum = allocatedAmount(lines)
    if (exceedsTotal(sum, total)) return t('Allocated waste exceeds total waste by %(excess)s %(unit)s.', { excess: (sum - total).toFixed(2), unit: state.totalUnit === 'kilograms' ? 'kg' : t('tonnes') })
  }
  return ''
}

function buildLines(entry) {
  return normaliseEntryLines(entry).filter(line => Number(line.qtyKg) > 0).map(line => ({ destination: line.destination, qty_kg: line.qtyKg }))
}

let reloadTaxonomy = null

// §9.2: a `BLOCKED` caller will never be served, so every retry affordance has to go —
// a "Try again" button that cannot ever succeed is worse than none. This is the opposite
// of `RATE_LIMITED`, which keeps its 60-second re-enable.
const blocked = () => state.errorCode === 'BLOCKED'

// `BLOCKED` is terminal, so a step change must not wipe the banner: that would leave the
// user looking at a permanently disabled Calculate button with no text saying why.
const clearedError = () => (blocked() ? {} : { error: null, errorCode: null })

function publicError(error) {
  switch (error.code) {
    case 'VALIDATION_ERROR': return validationMessage(error)
    case 'UNKNOWN_CODE': return t('Calculator options have changed. The latest options are being loaded; please review your selections and try again.')
    case 'RATE_LIMITED': return t('Too many calculations have been requested. Please wait 60 seconds and try again.')
    case 'FORMULA_ERROR': return t('The calculator could not produce a result because its calculation configuration needs attention. Please try again later.')
    // §9: `ENGINE_UNAVAILABLE` means the calculator cannot run at all right now rather
    // than that this request was bad, which is `NO_PUBLISHED_FACTOR_SET`'s situation and
    // takes the same copy.
    case 'ENGINE_UNAVAILABLE':
    case 'NO_PUBLISHED_FACTOR_SET': return t('The calculator is currently under maintenance because no factor set is available.')
    // §9.2: `message` never varies, is written for end users, and is the whole of what a
    // refused caller is owed. Do not add copy suggesting a retry.
    case 'BLOCKED': return error.message || t('This request was refused. If you believe this is an error, contact the Kai Commitment team.')
    default: return error.message || t('The calculation could not be completed.')
  }
}

/**
 * The banner text for a 400.
 *
 * `destinationRows` shows every detail that names a row of the entry on screen against
 * that row, so the banner only has to point at them. A detail that names anything else —
 * a saved entry, an `alternative`, a field this form has no input for — has no box to
 * attach to and would otherwise vanish entirely, so it is spelled out here instead.
 */
function validationMessage(error) {
  const bound = new Set(draftFieldPaths().filter(Boolean))
  const unbound = (error.details || []).filter(detail => !bound.has(detail.field))
  if (!unbound.length) return t('Check the highlighted fields and try again.')
  return [error.message || t('The calculation could not be completed.'), ...unbound.map(describeDetail)].join(' ')
}

// §9's path is rooted at the request body and starts `entries[N]`, where N is the
// submission-order index. The user counts entries from one.
function describeDetail(detail) {
  const entry = /^entries\[(\d+)\]/.exec(detail.field || '')
  const message = detail.message || t('This value could not be accepted.')
  return entry ? t('Entry %(number)s: %(message)s', { number: Number(entry[1]) + 1, message }) : message
}

function fieldErrorMap(error) {
  // §9: `code` alone decides the shape of `details` — no presence checks. Only
  // VALIDATION_ERROR carries the `{field, issue, message}` shape; FORMULA_ERROR carries
  // `{expression, line, column, reason}` and names no field, and `BLOCKED` carries `null`
  // rather than an array at all.
  if (error.code !== 'VALIDATION_ERROR') return {}
  // The value is the detail's own `message`, not the envelope's. The envelope's `message`
  // describes the request as a whole, so standing it against each row printed "Request
  // validation failed" beside every highlighted input and discarded the only text that
  // said what was actually wrong with that row (§9).
  return Object.fromEntries((error.details || []).map(detail => [detail.field, detail.message || t('This value could not be accepted.')]))
}

async function submitCalculation() {
  if (Date.now() < state.rateLimitedUntil) return
  setState({ loading: true, error: null, errorCode: null, fieldErrors: {} })
  try {
    // §6.2: the whole submission travels in one call. One request per entry would let
    // §5.3's token upsert overwrite every entry but the last, cost N× the rate limit,
    // and leave the earlier entries persisted when a later one fails.
    const entries = [...state.entries, draftEntry()]
    const response = await calculate({
      token: state.token || null,
      gwp_horizon: state.gwpHorizon,
      entries: entries.map(entry => ({
        sector: entry.sector,
        food_category: entry.foodCategory || null,
        current: buildLines(entry),
        alternative: null,
      })),
    })
    const token = response.token || state.token
    if (token) sessionStorage.setItem('kaiCalculatorToken', token)
    setState({ result: { ...response, entry_results: entryResultsFrom(entries, response) }, token, loading: false, step: 5, error: null, errorCode: null, fieldErrors: {} })
  } catch (error) {
    const rateLimitedUntil = error.code === 'RATE_LIMITED' ? Date.now() + 60000 : state.rateLimitedUntil
    if (error.code === 'UNKNOWN_CODE' && reloadTaxonomy) await reloadTaxonomy({ preserveError: true })
    const errorStep = error.code === 'VALIDATION_ERROR' ? 3 : error.code === 'UNKNOWN_CODE' ? 0 : state.step
    setState({ loading: false, error: publicError(error), errorCode: error.code || 'UNKNOWN_ERROR', fieldErrors: fieldErrorMap(error), rateLimitedUntil, step: errorStep })
    // Clearing the deadline without clearing the banner re-enabled Calculate underneath a
    // paragraph still telling the user to wait 60 seconds — the button and the copy saying
    // opposite things, with the copy the more believable of the two. The banner only goes if
    // it is still the rate-limit one: another failure may have replaced it inside the minute,
    // and that message is about something the wait does not fix.
    if (error.code === 'RATE_LIMITED') {
      setTimeout(() => setState(state.errorCode === 'RATE_LIMITED'
        ? { rateLimitedUntil: 0, error: null, errorCode: null }
        : { rateLimitedUntil: 0 }), 60000)
    }
  }
}

/**
 * The container count, on keystroke.
 *
 * Takes §7.2's documented exception rather than `setState`: `render()` replaces
 * `main.innerHTML`, so a re-render per keystroke destroys the focused input — the same
 * reason `updateLine` and `improvement.js` bypass it. Only the running total is patched,
 * and `textContent` is the assignment because the string is plain text.
 *
 * Nothing here validates. The count is not wrong while it is half-typed; `1.` and `0.` are
 * both on the way to a legal value, and `containerTotalText` simply shows nothing until
 * `decimalPattern` matches. The refusal comes from `validateCurrentStep` on Continue,
 * which is where the mass field's rule already lives.
 */
function updateContainerCount(control) {
  state.unitCount = control.value
  state.error = null
  const total = document.getElementById('container-total')
  if (total) total.textContent = containerTotalText()
}

function updateLine(control) {
  const lineId = control.dataset.lineId
  state.current = state.current.map(line => line.id === lineId ? { ...line, qtyInput: control.value } : line)
  // The server named its failing lines by their position among the lines that were sent
  // (§9), and blanking or filling a row changes which lines would be sent at all. The
  // stored paths stop meaning what they meant, so they go rather than move to a
  // neighbouring row; the highlights they drew are cleared below with the rest.
  state.fieldErrors = {}
  state.lastChangedDestination = state.current.find(line => line.id === lineId)?.destination || null
  const total = totalNumber(state)
  const sum = allocatedAmount(state.current)
  const summary = document.getElementById('current-summary')
  const hasNegative = state.current.some(line => line.qtyInput !== '' && Number(line.qtyInput) < 0)
  summary?.classList.toggle('invalid', exceedsTotal(sum, total) || hasNegative)
  if (summary) {
    summary.querySelector('[data-summary="allocated"]').textContent = `${sum.toFixed(2)} ${unitLabel(state.totalUnit)}`
    summary.querySelector('[data-summary="remaining"]').textContent = `${remainingAmount(total, sum).toFixed(2)} ${unitLabel(state.totalUnit)}`
  }
  const error = validateCurrentStep()
  document.getElementById('allocation-error').textContent = error
  document.querySelectorAll('.destination-row').forEach(row => row.classList.remove('invalid'))
  document.querySelectorAll('.destination-row input').forEach(input => input.removeAttribute('aria-invalid'))
  // These paragraphs are the server's per-field prose, which `state.fieldErrors` has just
  // discarded. Dropping only the highlight left the sentence sitting under an unhighlighted
  // row, still naming a line position the request no longer has.
  document.querySelectorAll('.destination-row .field-error').forEach(message => message.remove())
  if (exceedsTotal(sum, total) || Number(control.value) < 0) {
    control.closest('.destination-row')?.classList.add('invalid')
    control.setAttribute('aria-invalid', 'true')
  }
  const continueButton = document.querySelector('[data-action="continue"]')
  if (continueButton) continueButton.disabled = Boolean(error)
}

function loadEntry(entry) {
  setState({ sector: entry.sector, foodCategory: entry.foodCategory, totalAmount: entry.totalAmount, totalUnit: entry.totalUnit, measureMode: entry.measureMode || 'mass', unitPreset: entry.unitPreset || null, unitCount: entry.unitCount || '', current: entry.current.map(line => ({ ...line, id: randomId() })), step: 0, error: null, fieldErrors: {}, lastChangedDestination: null })
}

function clearDraft() {
  setState({ sector: null, foodCategory: null, totalAmount: '', totalUnit: 'kilograms', measureMode: 'mass', unitPreset: null, unitCount: '', current: [], step: 0, error: null, fieldErrors: {}, expandedSectors: [], lastChangedDestination: null })
}

export function render(main) {
  main.className = 'main-content'
  if (state.loading && !state.taxonomy) {
    main.innerHTML = `<section class="content-section"><p class="loading-state" role="status">${escapeHtml(t('Loading calculator options…'))}</p></section>`
    return
  }
  if (!state.taxonomy) {
    // §9.2: `BLOCKED` is the one failure here that a retry can never clear, so the button
    // is withheld rather than disabled — the message is the whole of the response.
    main.innerHTML = `<section class="content-section error-state"><h1>${escapeHtml(t('Calculator unavailable'))}</h1><p>${escapeHtml(state.error || t('The taxonomy could not be loaded.'))}</p>${blocked() ? '' : `<button class="button button-primary" type="button" data-action="retry">${escapeHtml(t('Try again'))}</button>`}</section>`
    return
  }
  const screens = [sectorStep, foodStep, amountStep, destinationStep, reviewStep]
  main.innerHTML = state.step === 5 ? renderResults(state) : screens[state.step]()
}

/**
 * The header, and nothing else that costs vertical space.
 *
 * The six-step progress band that used to be written here — `.step-mobile` under
 * 850px and an `<ol>` of names above it — is gone, and its job moved into
 * `view.js`'s `stepNav`. It sat between the header and `<main>` on every step but
 * the intro, cost a measured 87px at 1278x983, and named steps that were never
 * clickable; the bar says the same thing on a line it was already occupying.
 * **Do not reinstate a second progress element at the top of the page** — it
 * takes back exactly the space this change was made to free.
 */
export function renderChrome() {
  // The header no longer has two appearances. It carried a Kale ground and a white
  // wordmark on the introduction screen and a white ground everywhere else; with the
  // introduction gone there is one state, so the class toggle and the second logo file
  // went with it rather than being left as a branch that can only take one side.
  const clearButton = document.getElementById('clear-button')
  clearButton.hidden = !hasData()
}

export function bindCalculator(main, retryTaxonomy) {
  reloadTaxonomy = retryTaxonomy
  main.addEventListener('click', event => {
    const control = event.target.closest('[data-action]')
    if (!control) return
    const action = control.dataset.action
    if (action === 'go-step') setState({ step: Number(control.dataset.step), ...clearedError() })
    if (action === 'toggle-sector') {
      const code = control.dataset.sector
      setState({ expandedSectors: state.expandedSectors.includes(code) ? state.expandedSectors.filter(item => item !== code) : [...state.expandedSectors, code] })
    }
    // Clearing the category takes any category-specific container with it, for the same
    // reason choosing a different one does: the preset is no longer on the list step 3
    // would offer, and a conversion nobody can see is a conversion nobody can check.
    if (action === 'clear-food') setState({ foodCategory: null, ...presetPatch(null) })
    if (action === 'continue') {
      const error = validateCurrentStep()
      if (error) setState({ error })
      else if (state.step === 2) setState({ step: 3, error: null, current: state.current.length ? normaliseLines(state.current) : entryDestinations().map(destination => createLine(destination.code)) })
      else setState({ step: state.step + 1, error: null })
    }
    if (action === 'add-entry') {
      setState({ entries: [...state.entries, draftEntry()] })
      clearDraft()
    }
    if (action === 'edit-entry') {
      const index = Number(control.dataset.index)
      const entry = state.entries[index]
      setState({ entries: state.entries.filter((_, entryIndex) => entryIndex !== index) })
      loadEntry(entry)
    }
    if (action === 'remove-entry') {
      const index = Number(control.dataset.index)
      setState({ entries: state.entries.filter((_, entryIndex) => entryIndex !== index), error: null })
    }
    if (action === 'calculate') submitCalculation()
    if (action === 'start-over' && window.confirm(t('Clear all calculator data and start again?'))) resetCalculator()
    if (action === 'download-results') downloadResults(state)
    if (action === 'breakdown-tab') setState({ resultBreakdownTab: control.dataset.tab })
    if (action === 'explore-improvements') openImprovement(state)
    if (action === 'reset-improvement') resetImprovement(state)
    if (action === 'cancel-improvement') setState({ improvementOpen: false, improvementResult: null, improvementError: null })
    // §9's code-to-copy map travels with the call. `improvement.js` cannot import it —
    // this module already imports that one — and without it the improvement panel showed
    // raw backend prose for the codes the main flow words carefully.
    if (action === 'compare-improvement') compareImprovement(state, publicError)
    if (action === 'retry' && !blocked()) retryTaxonomy()
    if (action === 'view-methodology') window.location.href = './methodology.html'
    if (['go-step', 'continue', 'add-entry', 'edit-entry', 'calculate', 'start-over', 'retry', 'view-methodology'].includes(action)) {
      window.scrollTo({ top: 0, behavior: 'smooth' })
    }
  })

  main.addEventListener('change', event => {
    const target = event.target
    if (target.name === 'sector') setState({ sector: target.value, error: null })
    if (target.name === 'food-category') setState({ foodCategory: target.value, ...presetPatch(target.value) })
    // One control, both modes. `current: []` was already this handler's behaviour and the
    // reason is unchanged and now broader: the destination amounts were entered against a
    // total in a unit that is no longer the one in force.
    //
    // **A container pins `totalUnit` to kilograms**, because that is the unit step 4
    // allocates in and §6.2 compares the two. Without it, a visitor who chose tonnes and
    // then chose a wheelie bin would reach a step 4 whose rows say "tonnes" against a total
    // in kilograms — a thousandfold error on a screen that looks entirely normal, refused
    // by nothing, because both numbers are individually plausible.
    if (target.id === 'total-unit') {
      const preset = target.value.startsWith(PRESET_OPTION) ? target.value.slice(PRESET_OPTION.length) : null
      setState({
        measureMode: preset ? 'container' : 'mass',
        unitPreset: preset,
        totalUnit: preset ? 'kilograms' : target.value,
        error: null,
        current: [],
      })
    }
  })

  /**
   * Where a minus sign may land, decided before it lands.
   *
   * **This is the one place a keystroke is declined, and it declines a
   * character rather than editing a number.** Nothing below assigns to
   * `.value`: a visitor who gets a negative number into one of these fields
   * keeps it on screen and is refused by `validateCurrentStep` on Continue,
   * in their own language, which is how every other rule in this file works.
   *
   * **The reason a minus is worth intercepting at all is `<input
   * type="number">`'s sanitising.** "5-" is not a valid floating-point number,
   * so the browser reports `.value === ''` while still *showing* "5-" in the
   * box. The row then reads as empty to `updateLine`, the allocation summary
   * drops it, and nothing on the screen says why — a field that looks filled
   * and counts as blank. Refusing the character is what stops that state
   * existing; refusing the number is not, because there is no number.
   *
   * **The two totals and the destination rows take opposite rulings, and the
   * asymmetry is deliberate.** `#total-waste` and `#unit-count` have no use for
   * a minus in any state, so it never lands. A destination amount must accept
   * one *at the start*, because `validateCurrentStep` refuses negative
   * destination amounts and `destinationStep` marks the summary and the row
   * invalid while it is typed — a refusal the visitor cannot see is a refusal
   * that teaches nothing, and blocking the character would hide it.
   *
   * **`value !== ''` stands in for "the caret is at the start", because a
   * number input has no caret to ask.** `selectionStart` throws
   * `InvalidStateError` on `type="number"`, so the only signal available is
   * whether anything is there yet. The cost is that "5" cannot be turned into
   * "-5" by prefixing; it has to be cleared first. That is the narrow side of
   * the trade and it is the right side: the wide one readmits "5-".
   */
  main.addEventListener('beforeinput', event => {
    const target = event.target
    if (!event.data?.includes('-')) return
    if (target.id === 'total-waste' || target.id === 'unit-count') {
      event.preventDefault()
      return
    }
    if (!target.matches('[data-line-field="amount"]')) return
    const alreadyEntered = target.dataset.minusEntered === 'true' ? 1 : 0
    const incoming = [...event.data].filter(character => character === '-').length
    if (target.value !== '' || !event.data.startsWith('-') || alreadyEntered + incoming > 1) event.preventDefault()
    else target.dataset.minusEntered = 'true'
  })

  main.addEventListener('input', event => {
    const target = event.target
    if (target.id === 'total-waste') {
      state.totalAmount = target.value
      state.error = null
    }
    if (target.id === 'unit-count') updateContainerCount(target)
    if (target.matches('[data-line-field="amount"]')) {
      // `beforeinput` cannot always be the whole story. A lone "-" leaves
      // `.value === ''` — the browser will not call one character a number — so
      // the flag has to be carried across that keystroke rather than recomputed
      // from a value that is not there yet, and it has to be *released* when the
      // field is emptied again or a second leading minus could never be typed.
      //
      // The other half is defensive. The InputEvent spec puts a paste's content
      // on `dataTransfer` and permits `data` to be null for `insertFromPaste`;
      // this Chromium populates `data` for a plain-text paste, so the guard
      // above does catch one today. Where it does not, the paste lands and this
      // line restores the invariant from what actually arrived — which is also
      // exactly what a refusal is supposed to do here: keep the visitor's
      // number and say no on Continue.
      if (target.value !== '' || event.inputType?.startsWith('delete')) {
        target.dataset.minusEntered = String(target.value.startsWith('-'))
      }
      updateLine(target)
    }
    if (target.matches('[data-improvement-code]')) updateImprovementInput(target, state)
  })

  main.addEventListener('keydown', event => {
    const tab = event.target.closest('[data-action="breakdown-tab"]')
    if (!tab || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
    event.preventDefault()
    const keys = ['stage', 'destination', 'food']
    const currentIndex = keys.indexOf(tab.dataset.tab)
    const nextIndex = event.key === 'Home' ? 0 : event.key === 'End' ? keys.length - 1 : (currentIndex + (event.key === 'ArrowRight' ? 1 : -1) + keys.length) % keys.length
    setState({ resultBreakdownTab: keys[nextIndex] })
    requestAnimationFrame(() => document.getElementById(`breakdown-tab-${keys[nextIndex]}`)?.focus())
  })
}
