import { calculate } from './api.js'
import { state, setState, resetCalculator, entryResultsFrom, draftEntry } from './state.js'
import { containerKg, countLimit, entryTotal, isPlainDecimal, isPresetUnit, kgToTonnes, massToKg, PRESET_UNIT, presetUnitCode, rowKgString } from './units.js'
import { requestLines, submissionPayload } from './submission.js'
import { escapeHtml, formatNumber, slug, stepNav } from './view.js'
import { t } from './i18n.js'
import { contributeCalculation, downloadPdf, downloadResults, renderResults } from './results.js'
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
// Item ⑥: a line's own unit, defaulting to the entry's — so a fresh row behaves exactly as
// every row did before this field existed, until a visitor changes one.
const createLine = (destination, qtyInput = '', unit = state.totalUnit) => ({ id: randomId(), destination, qtyInput, unit })

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
// destination line at `MAX_LINE_QTY` and one entry's whole `current` scenario at
// `MAX_SCENARIO_QTY`, and **as of v1.46 those are the same number: 50,000,000 kg.**
// The column behind them is `DECIMAL(16,3)`, five orders of magnitude wider again and
// never the binding constraint. A client-side guard restates a server rule: it may
// refuse earlier and more kindly than the API would, and it must never refuse something
// the API would take.
//
// **They are written as two names for one number, not collapsed into one.** They guard
// different fields for different reasons and §6.2 still states them as two rules; a
// single `MAX_KG` here would make the next divergence in `api/schemas.py` invisible on
// this side. They are asserted equal in `tests/test_schemas.py`, not here.
//
// **Which bound goes on which field follows from what is actually sent.** The step-3
// total never crosses the wire at all — `buildLines` sends the *destination lines* — so
// the total's only job is to be the ceiling of the step-4 allocation, and the rule that
// belongs on it is the scenario cap. The line cap belongs on a destination row, where the
// number it bounds is the number that leaves the browser. That was already true when the
// two differed and it is what has to stay true if they diverge again.
//
// **The ratio between them was a defect, and this comment used to state it as a fact.**
// It read: a visitor who puts all of a legal total into one destination "is over this one
// and under that one" — describing, without noticing, that "at least five destinations"
// had become a precondition of reaching the step-3 ceiling. A site that only landfills
// has no second destination to split across, and 50,000 t to animal feed is an ordinary,
// truthful answer. `MAX_LINE_QTY` was raised to meet the scenario cap; nothing here
// lowered `MAX_SCENARIO_KG`, and nothing about which cap guards which field moved.
const MAX_SCENARIO_KG = 50000000
const MAX_LINE_KG = 50000000

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

// Item ⑥: a line's own unit may itself be a container preset — `preset:<code>`, the same
// value space `#total-unit` uses (see `PRESET_OPTION` and `unitSelectValue` below) — so one
// row can be measured in crates while its neighbour is measured in tonnes.
//
// **The prefix and the conversion behind it moved to `units.js`.** They were private here,
// and while they were, `improvement.js` and `results.js` — which read the very same rows —
// each had their own idea of what a row's unit meant, and both were wrong: one re-sent the
// row in the entry's unit under the same token, the other printed the entry's unit beside a
// figure measured in another. §7.3 puts the front end's only arithmetic in one module for
// exactly this reason.
const lineUnitIsPreset = isPresetUnit
const lineUnitPresetCode = presetUnitCode
const lineKgString = (qtyInput, unit) => rowKgString(qtyInput, unit, presetList())

// The same conversion as a `Number`, for the sums and ceilings that never reach the wire —
// the allocation total and `MAX_LINE_KG`. §7.6's `Number()`-for-display-only rule is about
// what travels to the API; a comparison made only in the browser is not that.
const lineKilograms = (qtyInput, unit) => {
  const kg = lineKgString(qtyInput, unit)
  return kg === '' || kg === null ? null : Number(kg)
}

// §7.3: `kgString` is `massToKg(...).toFixed(3)`, and it exists so the rounding to the
// API's three decimal places happens in `units.js` rather than at each call site. Both
// sites here spelled it out instead, which is the same defect `kgToTonnes` was added for.
// A blank row stays blank — `kgString('', unit)` is "0.000", and a row the user has not
// filled is not a row holding zero.
//
// **Each line converts with its own `unit`, not the entry's.** A line that predates item ⑥
// has none, and falls back to `state.totalUnit` — the unit every row was implicitly in
// before a row could differ from its neighbour, so an entry saved under the old behaviour
// is never reinterpreted.
const normaliseLines = lines => lines.map(line => ({ ...line, qtyKg: lineKgString(line.qtyInput, line.unit || state.totalUnit) }))
// The running allocation, **in `state.totalUnit`** — the unit the summary and the ceiling
// are stated in — regardless of which unit each row was typed in. Summing raw `qtyInput`
// values directly was correct only because every row shared one unit; once a row can carry
// its own, "5" typed in tonnes and "5" typed in kilograms are not the same five, so the sum
// has to happen in kilograms first and only the *total* is converted back for display —
// with `kgToTonnes`, `units.js`'s own conversion, rather than a division re-typed here.
const allocatedAmount = lines => {
  const kilograms = lines.reduce((sum, line) => sum + (lineKilograms(line.qtyInput, line.unit || state.totalUnit) || 0), 0)
  return state.totalUnit === 'tonnes' ? kgToTonnes(kilograms) : kilograms
}
// `unitPreset` and `unitCount` are here for the same reason `totalAmount` is: they are
// something the visitor entered, so the Clear button has to appear once either exists.
// `totalInputKg` joins them for the same reason.
const hasData = () => Boolean(state.entries.length || state.sector || state.foodCategory || state.totalAmount || state.unitPreset || state.unitCount || state.totalInputKg || state.totalValueNzd || state.wastedValueNzd || state.current.some(line => line.qtyInput !== '') || state.result)
// `draftEntry` now lives in `state.js`: `improvement.js` builds a submission too, and it
// had its own shorter copy of this shape that was missing every field round two added.

/**
 * The introduction screen: `state.step === -1`, and the first thing a visitor meets.
 *
 * **This screen was deleted once and is back by the team's decision, not by accident.**
 * For one week `/` served `home.html` and this page opened on step one. `home.html` is
 * retired now - the team's reading is that it looked poor and duplicated the client's
 * own website, which already carries that material - so `/` serves `index.html` again
 * and the landing screen is this one. The extra click between the address and step one
 * is accepted: it buys the eyebrow, the heading, the privacy note and the "What you
 * will need" list, and there is no longer a page in front of this one saying the same
 * things.
 *
 * Recovered from `a749f70^` rather than rewritten, so this is the reviewed original.
 * Two things around it changed while it was gone and it inherits both: `.hero-food-pattern`
 * now carries a photograph behind the arch cut-outs (the mask markup here is unchanged
 * and does the cutting), and the header this screen puts on a Kale ground now also
 * carries the language chooser, whose label sits in a white capsule - see the note over
 * `.language-bar__label` in `styles.css` for why `.intro-header` must NOT repaint it.
 *
 * `home.html` keeps its own static copy of the arch band and the check-list. That is
 * duplication of markup, and it is deliberate: the retired page is frozen exactly as it
 * was reviewed, so that reviving it is a routing decision rather than a rebuild.
 */
function introduction() {
  return `<section class="hero" aria-labelledby="page-title">
    <div class="hero-copy"><p class="eyebrow">${escapeHtml(t('For New Zealand food businesses'))}</p><h1 id="page-title">${escapeHtml(t('Food Waste Impact Calculator'))}</h1><p class="lead">${escapeHtml(t('Turn your food waste measurements into a clearer view of their potential environmental and financial impact.'))}</p><button class="button button-primary button-large" type="button" data-action="start">${escapeHtml(t('Start calculator'))}</button><p class="privacy-note">${escapeHtml(t('Your entries are recorded anonymously, and they join the public statistics only if you choose to offer them.'))}</p></div>
    <div class="hero-food-pattern" aria-hidden="true"><svg class="food-arch-mask" viewBox="0 0 1500 190" preserveAspectRatio="none"><defs><mask id="food-arch-cutouts"><rect width="1500" height="190" fill="white" />${[150, 450, 750, 1050, 1350].flatMap(centre => [`<ellipse cx="${centre}" cy="190" rx="205" ry="166" fill="none" stroke="black" stroke-width="32"/>`, `<ellipse cx="${centre}" cy="190" rx="151" ry="120" fill="none" stroke="black" stroke-width="28"/>`]).join('')}${[300, 600, 900, 1200].map(x => `<path d="M ${x} 72 L ${x + 36} 126 L ${x} 181 L ${x - 36} 126 Z" fill="black"/>`).join('')}</mask></defs><rect width="1500" height="190" fill="currentColor" mask="url(#food-arch-cutouts)"/></svg></div>
    <div class="hero-support-grid"><div class="needs-panel"><h2>${escapeHtml(t('What you will need'))}</h2><ul class="check-list"><li>${escapeHtml(t('Where the waste occurred in the food supply chain'))}</li><li>${escapeHtml(t('The food category, if known'))}</li><li>${escapeHtml(t('The total waste amount — a weight, or how many containers you fill'))}</li><li>${escapeHtml(t('How that total was distributed across waste destinations'))}</li></ul></div></div>
  </section>`
}

function sectorStep() {
  const sectors = sorted(state.taxonomy.sectors)
  return `<section class="content-section" aria-labelledby="stage-title"><p class="eyebrow">${escapeHtml(t('Step %(step)s', { step: 1 }))}</p><h1 id="stage-title">${escapeHtml(t('Where in the food supply chain did this waste occur?'))}</h1><p class="section-intro" id="supply-chain-support">${escapeHtml(t('Choose the stage that best describes where the food waste was generated.'))}</p>
    <fieldset class="stage-fieldset" aria-describedby="supply-chain-support"><legend>${escapeHtml(t('Supply-chain stage'))} <span class="required">${escapeHtml(t('(required)'))}</span></legend><div class="stage-card-list">${sectors.map(sector => {
      const isSelected = state.sector === sector.code
      const expanded = state.expandedSectors.includes(sector.code)
      const id = `sector-${slug(sector.code)}`
      return `<div class="stage-card ${isSelected ? 'selected' : ''}"><label class="stage-select" for="${id}"><input id="${id}" name="sector" type="radio" value="${escapeHtml(sector.code)}" ${isSelected ? 'checked' : ''}><span class="stage-copy"><span class="stage-title">${escapeHtml(sector.name)}</span><span class="stage-description">${escapeHtml(sector.description || '')}</span></span>${isSelected ? `<span class="selected-label" aria-hidden="true">✓ ${escapeHtml(t('Selected'))}</span>` : ''}</label><button class="details-button" type="button" data-action="toggle-sector" data-sector="${escapeHtml(sector.code)}" aria-expanded="${expanded}" aria-controls="${id}-details" aria-label="${escapeHtml(expanded ? t('Hide details for %(name)s', { name: sector.name }) : t('Show details for %(name)s', { name: sector.name }))}">${escapeHtml(t('Details'))} <span class="chevron ${expanded ? 'expanded' : ''}" aria-hidden="true">⌄</span></button><div class="stage-details" id="${id}-details" ${expanded ? '' : 'hidden'}><p>${escapeHtml(sector.details || sector.description || t('Additional details have not been supplied.'))}</p></div></div>`
    }).join('')}</div>${state.error ? `<p class="field-error" role="alert">${escapeHtml(state.error)}</p>` : ''}</fieldset>${stepNav({ step: 0, back: -1 })}</section>`
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
const PRESET_OPTION = PRESET_UNIT
const unitSelectValue = () =>
  (state.measureMode === 'container' ? PRESET_OPTION + (state.unitPreset || '') : state.totalUnit)

/**
 * The options `#total-unit` offers, as markup — weights first, then this entry's
 * containers, exactly as `amountStep` built them before item ⑥.
 *
 * **Shared with a destination row's own unit `<select>`**, so a row can be measured in the
 * same containers the entry's total can be, and the two lists can never drift apart into two
 * different orderings of the same taxonomy.
 *
 * `preset.label` is staff-typed and published exactly as written (§7.7.7): escaped, never
 * passed through `t()`. The `<optgroup>` labels around it are translated, so a Thai visitor
 * reads a Thai form still listing English container names — the stated and accepted
 * consequence of that rule.
 */
function unitOptionsHtml(selectedValue) {
  const presets = containerPresets()
  return `<optgroup label="${escapeHtml(t('Weight'))}"><option value="kilograms" ${selectedValue === 'kilograms' ? 'selected' : ''}>${escapeHtml(t('kilograms'))}</option><option value="tonnes" ${selectedValue === 'tonnes' ? 'selected' : ''}>${escapeHtml(t('tonnes'))}</option></optgroup>${presets.length ? `<optgroup label="${escapeHtml(t('Containers'))}">${presets.map(preset => {
    const value = PRESET_OPTION + preset.code
    return `<option value="${escapeHtml(value)}" ${selectedValue === value ? 'selected' : ''}>${escapeHtml(preset.label)}</option>`
  }).join('')}</optgroup>` : ''}`
}

/**
 * The label beside a destination row's amount field: a translated weight name, or the
 * container's own staff-typed label when the row is measured in one.
 *
 * `unitLabel` alone would answer "kilograms" for a `preset:` value — it recognises only
 * `'tonnes'` and defaults everything else — which is the right default for `entry.totalUnit`
 * (never a preset: item ⑦'s note on the entry level) and the wrong one for a row's own unit.
 */
const rowUnitLabel = unit => (lineUnitIsPreset(unit) ? (selected(presetList(), lineUnitPresetCode(unit))?.label || unit) : unitLabel(unit))

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
 *
 * **The two money fields are wrapped in their own `.money-fields` group, not
 * dropped into the three-column grid as two more items.** Five fields in three
 * columns would leave the second row two-of-three full — a gap where the third
 * column used to be, on a row holding the one pair here that is not three
 * independent questions: the wasted figure is a part of the produced one. The
 * group is a single item in `.amount-grid`'s row, spanning the full row width
 * at `min-width: 650px`, and lays its own two children out 1fr/1fr inside
 * itself — full-width and paired, instead of ragged and scattered.
 */
function amountStep() {
  const container = state.measureMode === 'container'
  const amountId = container ? 'unit-count' : 'total-waste'
  const amountLabel = container ? t('How many containers?') : t('Waste amount')
  const amountHint = container
    ? t('Use up to two decimal places — enter 0.5 for a half-full container.')
    : t('Use up to two decimal places.')
  const amountValue = container ? state.unitCount : state.totalAmount
  // `state.error` is reused for three different things on this step now, and each
  // belongs beside a different field. `amountOnlyValidation` and
  // `massContradictionValidation`'s messages are both about `amountId` (the waste
  // amount is the mass contradiction's own subject); `moneyContradictionValidation`'s
  // is about `#wasted-value` instead, never `amountId`; a server `VALIDATION_ERROR`
  // naming `total_input_kg`/`total_value_nzd`/`wasted_value_nzd` (see `detailStep`
  // below) is neither, and carries `errorCode`, which none of the three client
  // checks ever set. Recomputing all three client checks fresh, rather than
  // trusting `state.error`'s own history, is what lets this tell them apart: none
  // of `amountOnlyValidation`/`moneyContradictionValidation`/`massContradictionValidation`
  // reads `state.error` itself, so whichever one currently agrees with it is the one
  // that produced it - stale text from an already-fixed field naturally attributes
  // to none of them instead of mislabelling whatever else is on screen.
  const isApiError = state.errorCode === 'VALIDATION_ERROR'
  const isClientError = !isApiError && Boolean(state.error)
  const amountFieldError = isClientError && (state.error === amountOnlyValidation() || state.error === massContradictionValidation())
    ? state.error
    : null
  const moneyContradictionError = isClientError && state.error === moneyContradictionValidation() ? state.error : null
  // **The classification's own `else`.** The three checks above are recomputed fresh
  // from what is currently typed, so a `state.error` left over from something none of
  // them asks about — the concrete case is `BLOCKED` (§9.2): `clearedError` deliberately
  // keeps it and its `errorCode` across a step change, so a visitor refused at Calculate
  // and then returning to this step carries an error that is not a VALIDATION_ERROR and
  // matches none of the three client checks either. Before this line such an error
  // matched nothing and rendered nothing — a refused visitor saw no message at all. It
  // is not a fault in any field on this screen, so — like a VALIDATION_ERROR — it goes
  // in the banner, not beside a field it was never about.
  const unmatchedClientError = isClientError && !amountFieldError && !moneyContradictionError ? state.error : null
  const bannerError = isApiError ? state.error : unmatchedClientError
  // The three round-two scalar fields' own per-field errors, keyed the same way
  // `destinationRows` keys a line's — `state.fieldErrors`, by the exact path the server
  // named (`entries[N].<key>`, always the draft entry's: see `ENTRY_SCALAR_FIELD_STEP`).
  //
  // **`wastedValueError` also carries the client-side money contradiction**, the one
  // check on this step that is not about `amountId` and has nowhere else on screen to
  // attach to but the figure it actually names.
  const scalarError = key => state.fieldErrors[`entries[${state.entries.length}].${key}`]
  const totalInputError = scalarError('total_input_kg')
  const totalValueError = scalarError('total_value_nzd')
  const wastedValueError = scalarError('wasted_value_nzd') || moneyContradictionError
  return `<section class="content-section" aria-labelledby="amount-title"><p class="eyebrow">${escapeHtml(t('Step %(step)s', { step: 3 }))}</p><h1 id="amount-title">${escapeHtml(t('How much food waste are you measuring?'))}</h1><p class="section-intro">${escapeHtml(t('Enter the total amount. You will allocate this total across destinations in the next step.'))}</p>${bannerError ? `<p class="field-error api-error ${state.errorCode ? `error-${slug(state.errorCode)}` : ''}" role="alert">${escapeHtml(bannerError)}</p>` : ''}<div class="form-panel amount-grid"><div class="form-field ${amountFieldError ? 'has-error' : ''}"><label for="${amountId}">${escapeHtml(amountLabel)} <span class="required">${escapeHtml(t('(required)'))}</span></label><p class="field-hint">${escapeHtml(amountHint)}</p><input id="${amountId}" type="number" inputmode="decimal" min="0" step="0.01" value="${escapeHtml(amountValue)}" ${amountFieldError ? 'aria-invalid="true" aria-describedby="amount-error"' : ''}>${amountFieldError ? `<p class="field-error" id="amount-error" role="alert">${escapeHtml(amountFieldError)}</p>` : ''}${container ? `<p class="container-total" id="container-total" aria-live="polite">${escapeHtml(containerTotalText())}</p>` : ''}</div><div class="form-field"><label for="total-unit">${escapeHtml(t('Unit'))} <span class="required">${escapeHtml(t('(required)'))}</span></label><p class="field-hint">${escapeHtml(t('Choose a weight, or the container you fill.'))}</p><select id="total-unit">${unitOptionsHtml(unitSelectValue())}</select></div><div class="form-field ${totalInputError ? 'has-error' : ''}"><label for="total-input">${escapeHtml(t('Total amount produced (%(unit)s)', { unit: unitLabel(state.totalUnit) }))} <span class="optional-tag">${escapeHtml(t('(optional)'))}</span></label><p class="field-hint">${escapeHtml(t('So results can show waste as a share of production.'))}</p><input id="total-input" type="number" inputmode="decimal" min="0" step="0.001" value="${escapeHtml(state.totalInputKg)}" ${totalInputError ? 'aria-invalid="true" aria-describedby="total-input-error"' : ''}>${totalInputError ? `<p class="field-error" id="total-input-error" role="alert">${escapeHtml(totalInputError)}</p>` : ''}</div><div class="money-fields"><div class="form-field ${totalValueError ? 'has-error' : ''}"><label for="total-value">${escapeHtml(t('Value of production (NZ$)'))} <span class="optional-tag">${escapeHtml(t('(optional)'))}</span></label><p class="field-hint">${escapeHtml(t('For statistics only — it never enters the emissions calculation.'))}</p><input id="total-value" type="number" inputmode="decimal" min="0" step="0.01" value="${escapeHtml(state.totalValueNzd)}" ${totalValueError ? 'aria-invalid="true" aria-describedby="total-value-error"' : ''}>${totalValueError ? `<p class="field-error" id="total-value-error" role="alert">${escapeHtml(totalValueError)}</p>` : ''}</div><div class="form-field ${wastedValueError ? 'has-error' : ''}"><label for="wasted-value">${escapeHtml(t('Value of the waste (NZ$)'))} <span class="optional-tag">${escapeHtml(t('(optional)'))}</span></label><p class="field-hint">${escapeHtml(t('For statistics only — it never enters the emissions calculation.'))}</p><input id="wasted-value" type="number" inputmode="decimal" min="0" step="0.01" value="${escapeHtml(state.wastedValueNzd)}" ${wastedValueError ? 'aria-invalid="true" aria-describedby="wasted-value-error"' : ''}>${wastedValueError ? `<p class="field-error" id="wasted-value-error" role="alert">${escapeHtml(wastedValueError)}</p>` : ''}</div></div></div>${stepNav({ step: 2, back: 1 })}</section>`
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
    // Item ⑥: this row's own unit, defaulting to the entry's — a line saved before this
    // field existed carries none and is never reinterpreted into a different unit.
    const rowUnit = line.unit || state.totalUnit
    return `<div class="destination-row ${invalid ? 'invalid' : ''}"><label for="destination-${line.id}">${escapeHtml(destination?.name || line.destination)}${destination?.description ? `<small>${escapeHtml(destination.description)}</small>` : ''}</label><div class="amount-with-unit"><input id="destination-${line.id}" data-line-field="amount" data-line-id="${line.id}" type="number" inputmode="decimal" min="0" step="0.01" value="${escapeHtml(line.qtyInput)}" ${invalid ? 'aria-invalid="true"' : ''} aria-label="${escapeHtml(t('%(destination)s amount in %(unit)s', { destination: destination?.name || line.destination, unit: rowUnitLabel(rowUnit) }))}"><select data-line-field="unit" data-line-id="${line.id}" aria-label="${escapeHtml(t('Unit'))}">${unitOptionsHtml(rowUnit)}</select></div>${serverError ? `<p class="field-error" role="alert">${escapeHtml(serverError)}</p>` : ''}</div>`
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
    // Item ⑥: the unit this row was typed in, not the entry's — the kilograms beside it
    // are what actually reaches the wire, and both are shown so the conversion stays
    // checkable at the one moment a visitor can still compare the two.
    return `<div><dt>${escapeHtml(destination?.name || line.destination)}</dt><dd>${formatNumber(line.qtyInput, 2)} ${escapeHtml(rowUnitLabel(line.unit || entry.totalUnit))} <small>(${formatNumber(line.qtyKg, 3)} kg)</small></dd></div>`
  }).join('')}</dl>`
}

function normaliseEntryLines(entry) {
  return entry.current.map(line => ({ ...line, qtyKg: lineKgString(line.qtyInput, line.unit || entry.totalUnit) }))
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
    <div class="form-field time-frame-field"><label for="time-frame">${escapeHtml(t('What period do these figures cover?'))} <span class="optional-tag">${escapeHtml(t('(optional)'))}</span></label><p class="field-hint">${escapeHtml(t('Optional — it only labels your figures, it never changes a result.'))}</p><select id="time-frame"><option value="" ${!state.timeFrame ? 'selected' : ''}>${escapeHtml(t('Not stated'))}</option><option value="one_week" ${state.timeFrame === 'one_week' ? 'selected' : ''}>${escapeHtml(t('One week'))}</option><option value="one_month" ${state.timeFrame === 'one_month' ? 'selected' : ''}>${escapeHtml(t('One month'))}</option><option value="one_quarter" ${state.timeFrame === 'one_quarter' ? 'selected' : ''}>${escapeHtml(t('One quarter'))}</option><option value="one_year" ${state.timeFrame === 'one_year' ? 'selected' : ''}>${escapeHtml(t('One year'))}</option></select></div>
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

/**
 * Step 2's own two rules about the amount/count field itself — everything
 * `amountFieldError` (`amountStep`, below) is entitled to show beside `amountId`.
 *
 * **Pulled out of `validateCurrentStep` so the render path can ask the identical
 * question `validateCurrentStep` just asked**, rather than re-reading `state.error`
 * (last set whenever Continue was last pressed, which may no longer be true of
 * what is typed now) or guessing from the message text which field it is about.
 * A single source for "is amountId itself wrong" is what lets the two new
 * cross-field checks below — the money and mass contradictions — share this
 * step's Continue-blocking without ALSO being mislabelled as a fault in
 * `amountId`, the one field they are never about.
 */
function amountOnlyValidation() {
  if (state.measureMode === 'container') {
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
    return ''
  }
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
  return ''
}

/**
 * **Money's own rule — not `exceedsTotal`'s mass tolerance borrowed.**
 * `ALLOCATION_EPSILON` is 0.01 *kilograms*, a tolerance built because a scale does not
 * agree with itself to the gram; reusing it as a money rule let a wasted value up to a
 * whole cent over its own total through — the client's own defect, one cent smaller —
 * and even that boundary was decided by binary floating-point error rather than by the
 * figure typed: `0.03 -> 0.04` (a whole cent over) was refused while `0.07 -> 0.08` (the
 * identical logical gap) was allowed, purely because `0.04 - 0.03` and `0.08 - 0.07`
 * land on different sides of `0.01` in a double.
 *
 * Money is exact to the cent (§1.2's own discipline), so it gets zero tolerance and an
 * integer comparison that cannot be decided by dust: both figures are parsed directly
 * into integer cents by `moneyCents`, with no floating-point arithmetic anywhere in the
 * decision.
 *
 * @param {string} value  A money field's typed string.
 * @returns {number|null} Integer cents, or `null` when `value` is not a plain decimal
 *   with at most two decimal places.
 */
function moneyCents(value) {
  if (!isPlainDecimal(value)) return null
  const [whole, fraction = ''] = String(value).trim().split('.')
  // Both money fields already refuse a third decimal place as it is typed (the
  // keystroke-level guard beside `decimalPattern`), so this is a defensive floor, not
  // the primary enforcement — a figure with a third decimal is treated as unparsable
  // for this comparison, never silently truncated into one that was not typed.
  if (fraction.length > 2) return null
  return Number(`${whole}${fraction.padEnd(2, '0')}`)
}

/**
 * **The client's own report: a wasted share of 102.17%.** The value of food wasted
 * cannot exceed the value of food handled, because the wasted food is a subset of the
 * food handled — the same reasoning §4.5's money block is built on. Refused here, at
 * entry, rather than only clamped on the results page: `moneySummary` (`results.js`)
 * prints `wasted_share_percent` exactly as the service returns it, deliberately
 * unclamped, because a contradiction that reaches it is meant to read as the visitor's
 * own typo rather than be quietly smoothed over — so the honest fix is to stop the typo
 * here, the same way `exceedsTotal` below stops an over-allocated destination total
 * rather than letting the summary print a number past what was produced.
 *
 * Both fields are optional (§4.5) and this is a comparison between the two, not a
 * format rule on either — a blank or unparsable figure on either side is `null` from
 * `moneyCents` and falls through to the server's own validation, exactly as it did
 * before this check.
 *
 * **A function of its own, not folded into `amountOnlyValidation`, because it is
 * never a fault in `amountId`** — `amountStep` (below) attaches this message to
 * `#wasted-value`'s own error slot rather than the waste-amount field's, and needs
 * to ask this exact question, independent of `state.error`'s stale history, to know
 * whether that is what is currently wrong.
 */
function moneyContradictionValidation() {
  if (state.totalValueNzd === '' || state.wastedValueNzd === '') return ''
  const totalCents = moneyCents(state.totalValueNzd)
  const wastedCents = moneyCents(state.wastedValueNzd)
  if (totalCents === null || wastedCents === null || wastedCents <= totalCents) return ''
  return t('Value of the waste exceeds value of production by NZ$%(excess)s.', { excess: ((wastedCents - totalCents) / 100).toFixed(2) })
}

/**
 * **The same contradiction, one dimension over.** The waste amount is the mass being
 * measured on this entry, and `total-input` beside it is the whole this entry is a part
 * of, so the subset rule applies here too — and the same server-side ratio
 * (`production_share_percent`) would otherwise print a share past 100% for the same
 * reason the money share could (§4.6). `massToKg` reads `total-input` in `state.totalUnit`,
 * the same unit `#total-input` is labelled and typed in for every measure mode, including
 * container mode, where it is pinned to kilograms alongside the total itself.
 *
 * **This one names `amountId` (the waste amount) as its subject, so it is treated as
 * an `amountFieldError` in `amountStep` alongside `amountOnlyValidation`'s own
 * messages** — unlike the money contradiction above, there is no other field on this
 * screen a mass contradiction could belong to instead.
 */
function massContradictionValidation() {
  if (state.totalInputKg === '') return ''
  const producedKg = massToKg(state.totalInputKg, state.totalUnit)
  const wasteKg = totalKilograms(state)
  if (producedKg === null || wasteKg === null || !exceedsTotal(wasteKg, producedKg)) return ''
  return t('Waste amount exceeds total amount produced by %(excess)s %(unit)s.', { excess: formatNumber(limitIn(wasteKg - producedKg, state.totalUnit), 2), unit: unitLabel(state.totalUnit) })
}

function validateCurrentStep() {
  if (state.step === 0 && !state.sector) return t('Select where in the food supply chain the waste occurred.')
  if (state.step === 2) {
    const amountError = amountOnlyValidation()
    if (amountError) return amountError
    const moneyError = moneyContradictionValidation()
    if (moneyError) return moneyError
    const massError = massContradictionValidation()
    if (massError) return massError
  }
  if (state.step === 3) {
    const total = totalNumber(state)
    const lines = state.current
    if (!lines.some(line => Number(line.qtyInput) > 0)) return t('Enter an amount for at least one waste destination.')
    if (lines.some(line => line.qtyInput !== '' && Number(line.qtyInput) < 0)) return t('Destination amounts must be zero or greater.')
    if (lines.some(line => line.qtyInput && !isPlainDecimal(line.qtyInput))) return t('Write the number out in full, using digits only.')
    if (lines.some(line => line.qtyInput && !decimalPattern.test(line.qtyInput))) return t('Enter destination amounts to no more than two decimal places.')
    // §6.2's per-line bound, restated. Since v1.46 it is the same number as the step-3
    // ceiling, so a visitor who puts all of a legal total into one destination is refused
    // by neither — which is the whole point of the change and the case this guard used to
    // get wrong. The check is kept as its own rule rather than dropped as redundant: the
    // total is not sent, `MAX_LINE_KG` is what §6.2 bounds the row by, and a step-3 total
    // entered in containers reaches step 4 through a different path.
    //
    // The finiteness check used to be folded into the negative rule two lines above,
    // which answered "Destination amounts must be zero or greater" for a row far too
    // large to be either. It is asked here, through the same `null`, where the true
    // answer is that the row is over the limit and the message says which limit.
    const overLine = lines.some(line => {
      if (line.qtyInput === '') return false
      const kilograms = lineKilograms(line.qtyInput, line.unit || state.totalUnit)
      return kilograms === null || kilograms > MAX_LINE_KG
    })
    if (overLine) return t('Enter destination amounts of no more than %(limit)s %(unit)s.', { limit: formatNumber(limitIn(MAX_LINE_KG, state.totalUnit), 0), unit: unitLabel(state.totalUnit) })
    const sum = allocatedAmount(lines)
    if (exceedsTotal(sum, total)) return t('Allocated waste exceeds total waste by %(excess)s %(unit)s.', { excess: (sum - total).toFixed(2), unit: state.totalUnit === 'kilograms' ? 'kg' : t('tonnes') })
  }
  return ''
}

// Items ④/⑤/⑦ and every destination row: the request body is built by
// `submission.js`, because `improvement.js` builds one too and the two disagreed. See the
// module note there — the disagreement was not theoretical, it wrote `NULL` over four
// figures the visitor had entered.
const buildLines = entry => requestLines(entry, presetList())

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

// **The three round-two scalar fields, and the step whose markup owns each.** `entries[N].
// total_input_kg` (and its two money neighbours) are answered on step 2 (`amountStep`) —
// a fact about that function's HTML, not something derivable from the field name itself.
// One map, in one place, rather than a per-call-site guess; `entryDestinations`'s and
// `draftFieldPaths`'s own paths cover the one other kind of field this form has a box
// for, `entries[N].current[M].qty_kg`, and that one is derived from `state.current`
// because there is one row per destination and the row is what the path counts.
const ENTRY_SCALAR_FIELD_STEP = {
  total_input_kg: 2,
  total_value_nzd: 2,
  wasted_value_nzd: 2,
}

// The exact `entries[N].<key>` paths the map above answers to — always the *draft*
// entry's, because a saved entry's fields sit on a read-only `entryCard` with no box to
// highlight. Shared by `detailStep`, which routes a rejected visitor, and
// `validationMessage`, which must not also describe in the banner a field already
// highlighted at its own input.
const scalarFieldPaths = () => Object.keys(ENTRY_SCALAR_FIELD_STEP).map(key => `entries[${state.entries.length}].${key}`)

/**
 * The step that owns one API validation detail, or `undefined` when this form has no
 * field to point at — a saved entry, `alternative`, anything §9 might name that never
 * reaches an `<input>` on this page. `submitCalculation` uses this to send a rejected
 * visitor to the screen that can actually show them what was wrong, instead of always
 * landing on step 3 regardless of which field the API named.
 */
function detailStep(detail) {
  const field = detail.field || ''
  const scalarKey = new RegExp(`^entries\\[${state.entries.length}\\]\\.(\\w+)$`).exec(field)?.[1]
  if (scalarKey && ENTRY_SCALAR_FIELD_STEP[scalarKey] !== undefined) return ENTRY_SCALAR_FIELD_STEP[scalarKey]
  return draftFieldPaths().includes(field) ? 3 : undefined
}

/**
 * The banner text for a 400.
 *
 * `destinationRows` shows every detail that names a row of the entry on screen against
 * that row, and `amountStep` now does the same for the three scalar fields above, so the
 * banner only has to point at what is left. A detail that names anything else — a saved
 * entry, an `alternative`, a field this form has no input for — has no box to attach to
 * and would otherwise vanish entirely, so it is spelled out here instead.
 */
function validationMessage(error) {
  const bound = new Set([...draftFieldPaths().filter(Boolean), ...scalarFieldPaths()])
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
    // `submissionPayload` is shared with `improvement.js`'s Compare Impact button, which
    // re-sends the whole submission under this same token. Two builders is how the four
    // round-two fields came to be silently dropped by the second call.
    const response = await calculate(submissionPayload(state, entries))
    const token = response.token || state.token
    if (token) sessionStorage.setItem('kaiCalculatorToken', token)
    setState({ result: { ...response, entry_results: entryResultsFrom(entries, response) }, token, loading: false, step: 5, error: null, errorCode: null, fieldErrors: {} })
  } catch (error) {
    const rateLimitedUntil = error.code === 'RATE_LIMITED' ? Date.now() + 60000 : state.rateLimitedUntil
    if (error.code === 'UNKNOWN_CODE' && reloadTaxonomy) await reloadTaxonomy({ preserveError: true })
    // A VALIDATION_ERROR names a field, and the field is what says which step it belongs
    // on — `detailStep` derives that from where each field is actually rendered. The first
    // detail with a locatable step wins; falling back to step 3 keeps this the form's own
    // long-standing default for a rejection that names no field any screen owns.
    const namedStep = error.code === 'VALIDATION_ERROR'
      ? (error.details || []).map(detailStep).find(step => step !== undefined)
      : undefined
    const errorStep = namedStep !== undefined ? namedStep : error.code === 'VALIDATION_ERROR' ? 3 : error.code === 'UNKNOWN_CODE' ? 0 : state.step
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
  setState({ sector: entry.sector, foodCategory: entry.foodCategory, totalAmount: entry.totalAmount, totalUnit: entry.totalUnit, measureMode: entry.measureMode || 'mass', unitPreset: entry.unitPreset || null, unitCount: entry.unitCount || '', totalInputKg: entry.totalInputKg || '', totalValueNzd: entry.totalValueNzd || '', wastedValueNzd: entry.wastedValueNzd || '', current: entry.current.map(line => ({ ...line, id: randomId() })), step: 0, error: null, fieldErrors: {}, lastChangedDestination: null })
}

function clearDraft() {
  setState({ sector: null, foodCategory: null, totalAmount: '', totalUnit: 'kilograms', measureMode: 'mass', unitPreset: null, unitCount: '', totalInputKg: '', totalValueNzd: '', wastedValueNzd: '', current: [], step: 0, error: null, fieldErrors: {}, expandedSectors: [], lastChangedDestination: null })
}

export function render(main) {
  main.className = `main-content${state.step === -1 ? ' introduction-main' : ''}`
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
  main.innerHTML = state.step === -1 ? introduction() : state.step === 5 ? renderResults(state) : screens[state.step]()
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
  // The header has two appearances again: a Kale ground with the white wordmark on the
  // introduction screen, a white ground with the dark one everywhere else. Both sides of
  // the branch are reachable, which is what makes it a branch rather than a leftover.
  //
  // **What this must not repaint is the language chooser's label.** It sits in a white
  // capsule on both grounds now, so an `.intro-header` colour rule would put white text
  // on white - see the note over `.language-bar__label` in `styles.css`.
  const header = document.getElementById('site-header')
  const logo = document.getElementById('brand-logo')
  const clearButton = document.getElementById('clear-button')
  const intro = state.step === -1
  header.classList.toggle('intro-header', intro)
  logo.src = intro ? './assets/kai-commitment-logo-white.webp' : './assets/kai-commitment-logo.png'
  clearButton.hidden = !hasData()
}

export function bindCalculator(main, retryTaxonomy) {
  reloadTaxonomy = retryTaxonomy
  main.addEventListener('click', event => {
    const control = event.target.closest('[data-action]')
    if (!control) return
    const action = control.dataset.action
    if (action === 'start') setState({ step: 0, ...clearedError() })
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
      // `errorCode` is cleared with it: `amountStep` tells a client-side message about
      // `amountId` apart from a server VALIDATION_ERROR naming a different field by
      // whether `errorCode` is still set from that response, and a leftover code from an
      // earlier submit must not survive to mislabel this one.
      if (error) setState({ error, errorCode: null })
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
    if (action === 'start-over' && window.confirm(t('Clear all calculator data and return to the introduction?'))) resetCalculator()
    if (action === 'download-results') downloadResults(state)
    if (action === 'download-pdf') downloadPdf(state)
    if (action === 'breakdown-tab') setState({ resultBreakdownTab: control.dataset.tab })
    if (action === 'explore-improvements') openImprovement(state)
    if (action === 'reset-improvement') resetImprovement(state)
    if (action === 'expand-improvement-chart') setState({ improvementChartExpanded: true })
    if (action === 'close-improvement-chart') setState({ improvementChartExpanded: false })
    if (action === 'cancel-improvement') setState({ improvementOpen: false, improvementChartExpanded: false, improvementResult: null, improvementError: null })
    // §9's code-to-copy map travels with the call. `improvement.js` cannot import it —
    // this module already imports that one — and without it the improvement panel showed
    // raw backend prose for the codes the main flow words carefully.
    if (action === 'compare-improvement') compareImprovement(state, publicError)
    if (action === 'retry' && !blocked()) retryTaxonomy()
    if (action === 'view-methodology') window.location.href = './methodology.html'
    if (['start', 'go-step', 'continue', 'add-entry', 'edit-entry', 'calculate', 'start-over', 'retry', 'view-methodology'].includes(action)) {
      window.scrollTo({ top: 0, behavior: 'smooth' })
    }
  })

  main.addEventListener('change', event => {
    const target = event.target
    if (target.name === 'sector') setState({ sector: target.value, error: null })
    if (target.name === 'food-category') setState({ foodCategory: target.value, ...presetPatch(target.value) })
    if (target.id === 'time-frame') setState({ timeFrame: target.value })
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
        // **`#total-input` goes with them, and for the same reason.** It is a mass in
        // `totalUnit` too, so changing this control silently reinterpreted whatever was
        // in it: 50000 typed against kilograms left as `"50000000.000"` once tonnes was
        // chosen, and 50 typed against tonnes left as `"50.000"` once a container pinned
        // `totalUnit` back to kilograms. Both measured. `#total-waste` at least sits
        // beside the select the visitor just changed and is re-read on the review step;
        // this field appears on neither screen again, so a wrong figure in it is a wrong
        // figure nobody can see. It is cleared rather than converted because converting
        // it would be the front end doing arithmetic on the visitor's behalf, and because
        // the destination rows beside it are cleared, not converted, already.
        totalInputKg: '',
      })
    }
    // Item ⑥: one row's own unit, changed without touching any other row's — the
    // assertion `test_changing_one_row_s_unit_does_not_change_the_others` exists to catch a
    // single shared value wearing several `<select>`s. A `<select>` has no mid-edit caret to
    // preserve, so this goes through `setState` and a full re-render like every other select
    // on this page, rather than the keystroke-preserving patch `updateLine` uses for typing.
    if (target.matches('[data-line-field="unit"]')) {
      const lineId = target.dataset.lineId
      setState({
        current: state.current.map(line => (line.id === lineId ? { ...line, unit: target.value } : line)),
        error: null,
      })
    }
    // §6.2.2: fires only on the tick, never on the untick — the route only ever sets
    // the flag, and the box is disabled the moment it is checked, so there is nothing
    // an untick could mean here anyway.
    if (target.id === 'contribute' && target.checked) contributeCalculation(state, publicError)
    // Item ⑧'s percentage/kilogram toggle. A discrete choice like every other `<select>`
    // on this page, so it goes through `setState` and a full re-render rather than the
    // keystroke-preserving patch `updateImprovementInput` uses — there is no caret in a
    // `<select>` to lose. `improvement.js` reads `state.improvementMode` to decide what
    // each row displays; the allocation itself, in `improvedAllocations`, is untouched.
    if (target.id === 'improvement-mode') setState({ improvementMode: target.value })
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
    // The three optional figures keep their decimal places by refusing the
    // keystroke that would create one too many, the same shape as the
    // minus-refusal below rather than `Number(...).toFixed(n)` rounding whatever
    // arrived after the fact — the difference between a character the visitor
    // cannot type and a figure the visitor typed being silently rewritten.
    //
    // **The ceiling differs by field and comes from §6.2's own columns**: the two
    // money figures are `DECIMAL(14,2)` and `total_input_kg` is `DECIMAL(16,3)`.
    // `#total-input` was left unguarded while its send path still rounded, so the
    // branch applied opposite rules to the two field families — a typed `12.345`
    // in a money box was refused at the keystroke and a typed `1.2345` here was
    // silently sent as `1.234`. Both are now refused as they are typed, and
    // neither is rewritten afterwards.
    //
    // **`event.data.length === 1` is what keeps this a keystroke guard rather
    // than a bulk-entry one.** A single character is what a real keypress hands
    // over; `page.fill()` and a paste hand over the whole string in one
    // `beforeinput` event, and counting every digit in a six-digit fill against
    // a two-decimal ceiling refused the fill outright — an ordinary whole-number
    // entry blocked by a guard meant for a fraction. Caret position is as
    // unreachable here as it is for the minus guard below — `selectionStart`
    // throws on `type="number"` — so a single new digit is refused once the
    // field already shows two decimal digits, wherever it lands: the same
    // narrow trade the minus guard below documents, on the same missing signal.
    const decimalCeiling = { 'total-value': 2, 'wasted-value': 2, 'total-input': 3 }[target.id]
    if (
      decimalCeiling !== undefined &&
      event.data?.length === 1 &&
      /\d/.test(event.data) &&
      (target.value.split('.')[1] || '').length >= decimalCeiling
    ) {
      event.preventDefault()
      return
    }
    if (!event.data?.includes('-')) return
    // Three fields refuse a minus outright; a destination amount below does not.
    // The difference is what a refusal has to point at. `validateCurrentStep`
    // rejects a negative destination amount and marks the summary invalid, so
    // that field must let the minus be typed or the refusal it triggers would
    // have nothing on screen to explain it. A negative share of a destination
    // is not a quantity anyone can mean, and the improvement panel has no
    // equivalent per-field refusal to make visible.
    //
    // The space in the selector is load-bearing: `improvement.js` renders the
    // number input INSIDE `div.percentage-input` with the range input as its
    // sibling outside, so the descendant combinator takes the typed field and
    // leaves the slider alone. Written without the space it matches nothing.
    if (
      target.id === 'total-waste' ||
      target.id === 'unit-count' ||
      target.id === 'total-input' ||
      target.id === 'total-value' ||
      target.id === 'wasted-value' ||
      target.matches('.percentage-input [data-improvement-code]')
    ) {
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
    // **The three round-two scalar fields now clear `state.error` on keystroke too,
    // the same way `#total-waste` already did above.** Before item ①'s two
    // cross-field checks, a keystroke in any of these three had nothing of
    // `state.error`'s to clear — only a server `VALIDATION_ERROR` ever named
    // them, and that lives in `state.fieldErrors`, reset elsewhere. Now that
    // `moneyContradictionValidation`/`massContradictionValidation` can set
    // `state.error` from figures typed in these boxes, leaving a stale
    // contradiction message on screen after the visitor has already fixed one
    // side of it would be exactly the defect `#total-waste`'s own clear exists
    // to avoid, one field over.
    if (target.id === 'total-input') { state.totalInputKg = target.value; state.error = null }
    if (target.id === 'total-value') { state.totalValueNzd = target.value; state.error = null }
    if (target.id === 'wasted-value') { state.wastedValueNzd = target.value; state.error = null }
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
