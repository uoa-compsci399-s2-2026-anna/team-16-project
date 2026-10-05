import { calculate } from './api.js'
import { state, setState, resetCalculator, entryResultsFrom, draftEntry, draftLeafFigures, EMPTY_LEAF, entryLeaves, leafDisplayName, leafFigures, leafKey } from './state.js'
import { containerKg, countLimit, entryTotal, isPlainDecimal, isPresetUnit, kgToTonnes, massToKg, PRESET_UNIT, presetUnitCode, rowKgString } from './units.js'
import { requestLines, submissionLeaves, submissionPayload } from './submission.js'
import { escapeHtml, formatNumber, slug, stepNav } from './view.js'
import { cardId, cardIsFixedOpen, cardIsOpen, cardStatus, collapsibleCard, keyAttr, openedCard } from './cards.js'
import { t } from './i18n.js'
import { armContribute, bindResultsSectionSpy, cancelContribute, downloadPdf, downloadResults, renderResults, resultsNavIsDocked } from './results.js'
import { compareImprovement, openImprovement, resetImprovement, updateImprovementInput } from './improvement.js'
import { handlePeriodBlur, handlePeriodClick, handlePeriodComposition, handlePeriodInput, handlePeriodKeydown, handlePeriodPointer, PeriodField, periodProblem, timeFrameChanged } from './period.js'
import { DROPPED_DESTINATION, DROPPED_FOOD_CATEGORY, DROPPED_FOOD_ITEM, DROPPED_SECTOR, DROPPED_UNIT_PRESET, writeResultSnapshot, writeSnapshot } from './snapshot.js'

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
const createLine = (destination, qtyInput = '', unit = 'kilograms') => ({ id: randomId(), destination, qtyInput, unit })

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
export const containerPresets = (foodCategory = null) => presetList()
  .filter(preset => !preset.food_category || preset.food_category === foodCategory)

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
const containerLimit = figures => Math.min(MAX_CONTAINER_COUNT, countLimit(figures.unitPreset, presetList(), MAX_SCENARIO_KG))

/**
 * ---------------------------------------------------------------- leaves
 *
 * `presetSurvives` and `presetPatch` stood here. They existed because changing
 * `state.foodCategory` could strand a container preset that was no longer on the list,
 * and their `current: []` fallback was the cure. **Under the leaf model a leaf's
 * category never changes** — unticking a category destroys the leaf, taking its
 * `leafFigures` entry, its preset and its destination rows with it — so the hazard they
 * guarded has no path left. What replaces them is `rewriteStrandedUnits` on the 3 -> 4
 * move below, which is the one remaining case: a row unit set to a category-scoped
 * preset before this change landed within a live session.
 */

/** The draft chain, as `draftEntry()` builds it — the shape every leaf reader takes. */
const draftChain = () => draftEntry()

/** The draft's leaves, in ticking order. One call, so nothing re-derives the rule. */
const draftLeaves = () => entryLeaves(state)

/** How many leaves the whole submission would carry — what `MAX_ENTRIES` actually counts. */
const savedLeafCount = () => state.entries.reduce((count, entry) => count + entryLeaves(entry).length, 0)
const submissionLeafCount = () => savedLeafCount() + draftLeaves().length

/**
 * `api/schemas.py`'s `MAX_ENTRIES`, restated on this side — and it counts **leaves**.
 *
 * Four chains of five categories is exactly twenty; a fifth is a 400 the form could have
 * prevented. Step 2 refuses the tick that would take the whole submission past it, which
 * is the pattern the ceilings block above already follows: a client-side guard may refuse
 * earlier and more kindly than the API would, and must never refuse something the API
 * would take.
 */
const MAX_LEAVES = 20

/**
 * **The ceiling rule, and the only statement of it.**
 *
 * It takes the chains a submission *would* hold and answers whether that submission
 * would carry more leaves than the API accepts. Every guard asks it the same question in
 * the same words, which is what the two halves of the previous arrangement could not do:
 * step 2 asked `submissionLeafCount() >= MAX_LEAVES` - the count *now*, not the count
 * after the tick - so at exactly twenty it refused the first tick on an empty draft,
 * which replaces the draft's category-less leaf with a named one and adds nothing; and
 * `add-entry` asked nothing at all, so a visitor could open a twenty-first leaf and meet
 * the ceiling as a 400 two screens later.
 *
 * A client-side ceiling may refuse earlier and more kindly than the API, and must never
 * refuse something the API would take. Stating the prospective submission rather than
 * the current one is what keeps it on the right side of that.
 *
 * @param {Array<object>} chains every chain the submission would hold, draft included
 * @returns {boolean} true when it would carry more leaves than `MAX_LEAVES`
 */
const exceedsLeafCeiling = chains => chains.reduce((count, chain) => count + entryLeaves(chain).length, 0) > MAX_LEAVES

/** The chains the submission would hold with `draft` as its draft. */
const submissionWith = draft => [...state.entries, draft]

/** The draft's selection as it would stand after one box was ticked. */
const draftTicking = (code, unspecified) => ({
  foodCategories: unspecified ? [...state.foodCategories] : [...state.foodCategories, code],
  foodUnspecified: unspecified ? true : Boolean(state.foodUnspecified),
  foodItems: state.foodItems || {},
})

/** Whether ticking one of step 2's boxes would take the submission past the ceiling. */
const tickRefused = (code, unspecified) => exceedsLeafCeiling(submissionWith(draftTicking(code, unspecified)))

/**
 * The draft's selection as it would stand after one of step 2.5's boxes was ticked.
 *
 * **Ticking a food is not always +1 leaf.** A category with no food ticked is already
 * one leaf (`entryLeaves`), so the FIRST food under it replaces that leaf rather than
 * adding to it; the second one adds. Asking the question through `entryLeaves` rather
 * than counting ticks is what gets that right without restating the leaf rule here --
 * the mistake step 2's own ceiling note warns about, one level down.
 */
const draftTickingItem = (category, item) => ({
  foodCategories: [...state.foodCategories],
  foodUnspecified: Boolean(state.foodUnspecified),
  foodItems: {
    ...(state.foodItems || {}),
    [category]: [...((state.foodItems || {})[category] || []), item],
  },
})

/** Whether ticking one of step 2.5's boxes would take the submission past the ceiling. */
const itemTickRefused = (category, item) =>
  exceedsLeafCeiling(submissionWith(draftTickingItem(category, item)))

/**
 * Whether step 2.5 is offered at all.
 *
 * Three conditions, and each is a different question:
 *
 * * **`factor_set.item_level_enabled`** -- the published set prices foods
 *   individually (§6.1). A non-empty vocabulary is NOT the same question: the
 *   vocabulary is global taxonomy and exists as soon as staff type it in, whereas
 *   the flag says the numbers behind the finer question can answer it. Asking a
 *   more specific question than the factors can answer is what the flag exists to
 *   prevent.
 * * **a non-empty vocabulary** -- there is something to offer. A deployment may
 *   have deactivated every `food_item`, or be running a seed older than the one
 *   that grew a vocabulary, and a screen of empty groups is worse than no screen.
 * * **at least one category chosen** -- step 2.5 refines step 2 (`spec.md` §3.3).
 *   A visitor who skipped the categories, or answered "I do not know", has nothing
 *   to refine, and the step is skipped rather than shown empty.
 */
function itemStepOffered() {
  if (!state.taxonomy?.factor_set?.item_level_enabled) return false
  // **At least one CHOSEN category must have foods, not just the vocabulary.**
  // Asking `food_items.length > 0` was asking whether the deployment has a
  // vocabulary at all, and the seed then gave one to four of its ten categories
  // -- so ticking any of the other six opened a panel headed *Do you know which
  // foods these were?* whose every group read "No specific foods are listed for
  // this category", with nothing on it to tick and Continue the only way out.
  // Contract v1.72 gave foods to five more categories, which shrinks the case
  // this guard is for without removing it: `standard_mix` still has none, and
  // it is the category a visitor who does not know the composition ticks.
  //
  // `itemStep`'s own note is about an empty group BESIDE full ones, and that
  // still holds: hiding it would leave the group list disagreeing with step 2's
  // ticks. It was never an argument for a screen made entirely of them.
  return state.foodCategories.some(category => itemsUnder(category).length > 0)
}

/** The vocabulary under one category, in the taxonomy's own order. */
const itemsUnder = category =>
  sorted((state.taxonomy?.food_items || []).filter(item => item.food_category === category))

/**
 * Whether *Add another supply-chain entry* would take the submission past the ceiling.
 *
 * The chain being built is committed and a fresh, empty draft takes its place - and an
 * empty draft is one leaf, the category-less one, because that is what it will send if
 * the visitor calculates without touching step 2. So the question is asked about the
 * list the click actually produces, not about the list before it.
 */
const addEntryRefused = () => exceedsLeafCeiling([...state.entries, draftEntry(), EMPTY_DRAFT])

/**
 * The `value` of step 2's "I do not know" checkbox.
 *
 * It is a control value and never a food-category code: `foodUnspecified` is its own
 * boolean on `state`, and nothing derived from it ever reaches `foodCategories`. The
 * double underscores keep it outside the `code` space `VARCHAR(64)` staff can type.
 */
const UNSPECIFIED_CHOICE = '__unspecified__'

/**
 * A leaf's name, in the visitor's language.
 *
 * **Not decided here.** `state.js`'s `leafDisplayName` is the one place a leaf is named,
 * because six surfaces show one - steps 3 and 4, the review step, the saved-entry card,
 * the results page and both exports - and while each decided for itself, one leaf
 * carried four different names and two different leaves could render the same one. This
 * is the local spelling of that call and nothing more.
 */
const leafName = leaf => leafDisplayName(leaf, state.taxonomy)

/**
 * A leaf's identity, as an **attribute value**.
 *
 * `leafKey` is NUL-joined, and NUL cannot survive a round trip through an HTML attribute:
 * the parser replaces U+0000 with U+FFFD, so `element.dataset.leaf` came back as a string
 * that matched no key in `leafFigures` and every keystroke was written into a new,
 * unreachable record - a field that looked filled and counted as empty. Measured: a waste
 * amount typed on step 3 left `Enter a waste amount for ...` on Continue with the figure
 * still on screen.
 *
 * `encodeURIComponent` is reversible, produces only attribute-safe characters, and keeps
 * the key's own collision-safety: the NUL becomes `%00` and nothing else a `code` can
 * hold encodes to it.
 */
const leafAttr = leaf => encodeURIComponent(leafKey(leaf))
/** The inverse, for a handler reading `data-leaf` off the control it was given. */
const leafOf = control => decodeURIComponent(control.dataset.leaf || '')

/** A leaf's identity as a DOM id fragment. `any` for the category-less leaf, never ''. */
const leafSlug = leaf => slug(`${leaf.foodCategory || 'any'}-${leaf.foodItem || ''}`) || 'any'

/**
 * A step-3 / step-4 field's `id`.
 *
 * **A chain with one leaf keeps today's singleton ids exactly** — `total-waste`,
 * `unit-count`, `total-unit`, `total-input`, `total-value`, `wasted-value`,
 * `container-total`. One leaf is the commonest journey by far and its screens are
 * unchanged by the fork, so nothing about it should change; the suffix appears only
 * where there is genuinely more than one of the field on screen and `<label for>` and
 * `getElementById` would otherwise be ambiguous. Every handler below keys on
 * `data-leaf-field` / `data-leaf` rather than on an id, so the ids are for labelling and
 * for tests, never for behaviour.
 */
const fieldId = (base, leaf, leaves) => (leaves.length === 1 ? base : `${base}--${leafSlug(leaf)}`)


/**
 * Step 2's tick and untick.
 *
 * **Appends on tick**, so the selection keeps the order the visitor built it in - which
 * is the leaf order steps 3 and 4 iterate, so a card never reshuffles when a category is
 * added. **Removes on untick**, and prunes that leaf's figures with it.
 *
 * The ceiling is re-checked here as well as disabling the box, because a disabled
 * attribute is a rendering and this is the rule.
 */
function toggleFoodChoice(target) {
  const unspecified = target.value === UNSPECIFIED_CHOICE
  // **Asked about the submission this tick would produce, not the one on screen.** The
  // same `exceedsLeafCeiling` the `disabled` attribute above was rendered from, so a box
  // that is tickable cannot be refused here and a box that is refused here cannot have
  // been tickable. Re-checked all the same, because a `disabled` attribute is a
  // rendering and this is the rule.
  if (target.checked && tickRefused(target.value, unspecified)) {
    target.checked = false
    return
  }
  const foodUnspecified = unspecified ? target.checked : Boolean(state.foodUnspecified)
  const foodCategories = unspecified
    ? [...state.foodCategories]
    : target.checked
      ? [...state.foodCategories.filter(code => code !== target.value), target.value]
      : state.foodCategories.filter(code => code !== target.value)
  const foodItems = Object.fromEntries(foodCategories.map(code => [code, [...((state.foodItems || {})[code] || [])]]))
  setState({
    foodCategories,
    foodUnspecified,
    foodItems,
    // **Unticking keeps this leaf's figures, and re-ticking brings them back.**
    // They are pruned at the boundary instead - `draftEntry()` in state.js drops
    // every record whose leaf is not live, so nothing dead reaches the request
    // body, a saved entry, or the fingerprint.
    //
    // Pruning HERE was the first fix for a real defect - 400 kg typed under one
    // category reappeared under the next one - but it overshot. That defect was a
    // record reaching a DIFFERENT leaf; `leafFigures` is keyed by leaf, so a kept
    // record can only ever come back to the food it was typed for. Pruning on the
    // untick instead threw the figures away silently, and re-ticking the same
    // category did not bring them back: work destroyed, no question asked, for a
    // food the visitor had named themselves.
    error: null,
    errorAt: null,
  })
}

/**
 * A row unit that names a food category, rewritten to kilograms.
 *
 * `presetSurvives`/`presetPatch` guarded the chain-level case and are gone: a leaf's
 * category cannot change, so a leaf-scoped preset cannot be stranded. **One case is
 * left** - a step-4 row whose `unit` is `preset:<code>` naming a category, set before
 * this change landed within a live session or restored from a `returnTo.draft` snapshot.
 * A destination row is shared by nothing and measured against no single food, so a
 * per-food density is not true of it anyway.
 */
function strandedUnit(unit) {
  if (!isPresetUnit(unit)) return unit
  const preset = selected(presetList(), presetUnitCode(unit))
  return preset && preset.food_category ? 'kilograms' : unit
}

/**
 * The 3 -> 4 move: **one set of destination rows per leaf**.
 *
 * A leaf that already has rows keeps them (with any stranded container unit rewritten);
 * a leaf that has none gets one row per destination the sector offers, created with an
 * **explicit** unit - its own leaf's - so that nothing downstream has to fall back to a
 * chain unit that could later be re-derived under it.
 *
 * `state.totalUnit` is fixed here and nowhere else: the leaves' common mass unit when
 * they share one, `'kilograms'` otherwise. It is what the combined figures on steps 3 and
 * 5 are stated in, and it is never the unit any row is converted with.
 */
function destinationRowsPatch() {
  const leaves = draftLeaves()
  const units = new Set(leaves.map(leaf => draftLeafFigures(leaf).totalUnit))
  const next = { ...state.leafFigures }
  for (const leaf of leaves) {
    const figures = draftLeafFigures(leaf)
    const rows = (figures.current || []).length
      ? figures.current.map(line => ({ ...line, unit: strandedUnit(line.unit || figures.totalUnit) }))
      : entryDestinations().map(destination => createLine(destination.code, '', figures.totalUnit))
    next[leafKey(leaf)] = { ...figures, current: rows }
  }
  return { leafFigures: next, totalUnit: units.size === 1 ? [...units][0] : 'kilograms' }
}

/** A `stepProblemAt` result, as the `state.errorAt` it should be stored under. */
const leafErrorAt = problem => (problem.leaf ? { leaf: problem.leaf, field: problem.field } : null)

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
const normaliseLines = (lines, unit) => (lines || []).map(line => ({ ...line, qtyKg: lineKgString(line.qtyInput, line.unit || unit) }))
// The running allocation, **in `state.totalUnit`** — the unit the summary and the ceiling
// are stated in — regardless of which unit each row was typed in. Summing raw `qtyInput`
// values directly was correct only because every row shared one unit; once a row can carry
// its own, "5" typed in tonnes and "5" typed in kilograms are not the same five, so the sum
// has to happen in kilograms first and only the *total* is converted back for display —
// with `kgToTonnes`, `units.js`'s own conversion, rather than a division re-typed here.
const allocatedAmount = (lines, unit) => {
  const kilograms = (lines || []).reduce((sum, line) => sum + (lineKilograms(line.qtyInput, line.unit || unit) || 0), 0)
  return unit === 'tonnes' ? kgToTonnes(kilograms) : kilograms
}
// `unitPreset` and `unitCount` are here for the same reason `totalAmount` is: they are
// something the visitor entered, so the Clear button has to appear once either exists.
// `totalInputKg` joins them for the same reason.
const leafHasFigures = figures => Boolean(figures.totalAmount || figures.unitPreset || figures.unitCount || figures.totalInputKg || figures.totalValueNzd || figures.wastedValueNzd || (figures.current || []).some(line => line.qtyInput !== ''))
// Reads `leafFigures` WHOLE, live leaves and parked ones alike. A record for a
// category that was ticked, filled and unticked is still something the visitor
// typed, and start-over destroys it, so it has to count as data to clear.
const hasData = () => Boolean(state.entries.length || state.sector || state.foodCategories.length || state.foodUnspecified || Object.values(state.leafFigures || {}).some(leafHasFigures) || state.result)
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

/**
 * Where a step's Back goes: the screen the visitor actually came from when
 * `state.returnTo` recorded a jump onto this step, and the previous screen otherwise.
 *
 * **All four form steps consult it**, because all four are reachable by a jump: the
 * review step has an *Edit* link per section, and each one lands on a screen whose Back
 * would otherwise walk the visitor out of the wizard rather than back to the review they
 * pressed Edit on. Only `data-step="0"` had a marker at first, so *Food category / Edit*
 * went to step 2, whose Back was hard-coded to step 1, whose Back then had nothing — two
 * presses and the visitor was on the introduction, which is the original complaint
 * arrived at by a different door.
 *
 * The results step keeps its own `back: 4` (`results.js`): it is reached by calculating,
 * never by a jump, and `submitCalculation` clears the marker on the way there.
 *
 * **This only ever reads a marker that is still about where the visitor is standing** —
 * see `markerAfterLeaving`, which is what makes that true. A marker left live after its
 * own step had been walked past turned this into a loop: *Food category / Edit*,
 * Continue, Back, and step 2's Back then went *forward* to the review step, with step 1
 * and the introduction unreachable behind it.
 */
const backTarget = step => (state.returnTo && state.returnTo.from === step ? state.returnTo.step : step - 1)

/**
 * `state.returnTo` as it stands once the visitor has left the step the marker is about
 * by anything other than that step's own Back.
 *
 * **A marker that moved nothing is spent the moment its step is left.** A review *Edit*
 * link's marker exists for one purpose: to give the screen it landed on a Back that
 * returns to the review step it was pressed on. Walking forward from that screen ends
 * the excursion — the visitor is in the wizard now, and `continue` will carry them to
 * the review step by itself. Keeping the marker past that point made `backTarget` point
 * the *wrong* step at review:
 *
 *     review -> Food category / Edit   {from: 1, step: 4}
 *            -> Continue               step 3, marker carried
 *            -> Back                   step 2, marker carried
 *            -> Back                   the REVIEW step, not step 1
 *
 * and from there Back walked review -> 4 -> 3 -> 2 -> review, with step 1 and the
 * introduction reachable by no sequence of Back presses at all. The same held for
 * *Supply-chain stage / Edit* and *Waste amount / Edit*; *Waste destinations / Edit* was
 * spared only because `continue` already drops every marker on the 3 -> 4 move.
 *
 * **A marker that moved something is not spent, and must not be dropped here.** An
 * `add-entry` or `edit-entry` marker carries `draft`: it is an undo for a mutation that
 * has already happened, and the whole of `test_step_one_back_navigation_browser.py` is
 * written on its surviving a forward walk — press Add, choose a sector, continue to step
 * 2, walk back to step 1, and backing out of the add still works from there. Those
 * markers are dropped where they always were: by `continue` on the 3 -> 4 move, by
 * `start`, and by `submitCalculation`.
 */
const markerAfterLeaving = () => {
  const back = state.returnTo
  return back && !back.draft && back.from === state.step ? null : back
}

/** The first sentence terminator with text after it: `(lead, rest)`. */
const SECTOR_SENTENCE = /^([\s\S]*?[.!?])\s+([\s\S]+)$/

/**
 * One `sector.description` read as the two parts step 1 has room for: the sentence the
 * card carries and the remainder its *Details* panel carries.
 *
 * **There is one column and two slots, and the second half of §2.1's ruling is here.**
 * `tests/fixtures/taxonomy.json` briefly carried a `details` string per sector and this
 * renderer read `sector.details || sector.description || t('…not been supplied.')`. No
 * version of the contract has ever defined `details`, so against the real API the panel
 * printed every sector's description a second time, and against a live database — where
 * all six descriptions are `NULL` — it printed *Additional details have not been
 * supplied.* on all six cards, which is what the owner reported. The ruling folded the
 * longer text into `description` and the fixture was changed; this renderer was not.
 *
 * The split is the shape the copy is already written in — a defining sentence, then
 * *This covers…*, then *Typical losses are…* — and the card's geometry was computed for
 * exactly one line of description: `styles.css`'s `min-height: 92px` is
 * 20 + 22.5 + 6 + 24 + 20, with the 24 being one line of `.stage-description`.
 *
 * **It degrades rather than guesses.** A description with no sentence break is shown
 * whole on the card and no panel is drawn, so staff-typed prose can never leave a button
 * that opens an empty box; a description that is absent leaves the card with its title
 * alone, which is the state the stylesheet's slack note already describes. Nothing here
 * invents copy for an empty column — the placeholder sentence is gone from all twenty
 * catalogues rather than being made unreachable, because an orphaned key fails
 * `test_no_catalogue_carries_a_key_the_front_end_never_asks_for` in every language.
 */
function sectorCopy(description) {
  const text = (description || '').trim()
  if (!text) return { lead: '', rest: '' }
  const split = SECTOR_SENTENCE.exec(text)
  return split ? { lead: split[1], rest: split[2] } : { lead: text, rest: '' }
}

function sectorStep() {
  const sectors = sorted(state.taxonomy.sectors)
  return `<section class="content-section" aria-labelledby="stage-title"><p class="eyebrow">${escapeHtml(t('Step %(step)s', { step: 1 }))}</p><h1 id="stage-title">${escapeHtml(t('Where in the food supply chain did this waste occur?'))}</h1><p class="section-intro" id="supply-chain-support">${escapeHtml(t('Choose the stage that best describes where the food waste was generated.'))}</p>
    <fieldset class="stage-fieldset" aria-describedby="supply-chain-support"><legend>${escapeHtml(t('Supply-chain stage'))} <span class="required">${escapeHtml(t('(required)'))}</span></legend><div class="stage-card-list">${sectors.map(sector => {
      const isSelected = state.sector === sector.code
      const expanded = state.expandedSectors.includes(sector.code)
      const id = `sector-${slug(sector.code)}`
      const { lead, rest } = sectorCopy(sector.description)
      return `<div class="stage-card ${isSelected ? 'selected' : ''}"><label class="stage-select" for="${id}"><input id="${id}" name="sector" type="radio" value="${escapeHtml(sector.code)}" ${isSelected ? 'checked' : ''}><span class="stage-copy"><span class="stage-title">${escapeHtml(sector.name)}</span><span class="stage-description">${escapeHtml(lead)}</span></span>${isSelected ? `<span class="selected-label" aria-hidden="true">✓ ${escapeHtml(t('Selected'))}</span>` : ''}</label>${rest ? `<button class="details-button" type="button" data-action="toggle-sector" data-sector="${escapeHtml(sector.code)}" aria-expanded="${expanded}" aria-controls="${id}-details" aria-label="${escapeHtml(expanded ? t('Hide details for %(name)s', { name: sector.name }) : t('Show details for %(name)s', { name: sector.name }))}">${escapeHtml(t('Details'))} <span class="chevron ${expanded ? 'expanded' : ''}" aria-hidden="true">⌄</span></button><div class="stage-details" id="${id}-details" ${expanded ? '' : 'hidden'}><p>${escapeHtml(rest)}</p></div>` : ''}</div>`
    }).join('')}</div>${state.error ? `<p class="field-error" role="alert">${escapeHtml(state.error)}</p>` : ''}</fieldset>${stepNav({ step: 0, back: backTarget(0) })}</section>`
}

/**
 * Every saved leaf, keyed `(sector, foodCategory, foodItem)`, to the first saved chain
 * index that holds it.
 *
 * The same `(sector, food_category)` pair `api/schemas.py:343` keys on and
 * `uq_submission_entry` enforces, one dimension deeper now that an entry is a leaf. **A
 * null food category counts**: "this sector, no breakdown" is as much a pair as any
 * other, and a saved chain that ticked nothing collides with a draft that ticks
 * "I do not know" - correctly, because they are the same answer.
 */
const savedLeafKey = (sector, leaf) => `${sector}\u0000${leafKey(leaf)}`

function savedLeafIndex() {
  const found = new Map()
  state.entries.forEach((entry, index) => {
    for (const leaf of entryLeaves(entry)) {
      const key = savedLeafKey(entry.sector, leaf)
      if (!found.has(key)) found.set(key, index)
    }
  })
  return found
}

/**
 * Which of the draft's leaves repeat which saved entries - one group per colliding
 * saved chain.
 *
 * Two leaves of one chain can never collide with each other: the multi-select is a set,
 * so they are distinct by construction. Collisions are only ever draft-leaf against
 * saved-leaf.
 *
 * @returns {Array<{index: number, leaves: Array<object>}>}
 */
function draftRepeatsSavedEntries() {
  if (!state.sector) return []
  const found = savedLeafIndex()
  const groups = new Map()
  for (const leaf of draftLeaves()) {
    const index = found.get(savedLeafKey(state.sector, leaf))
    if (index === undefined) continue
    if (!groups.has(index)) groups.set(index, [])
    groups.get(index).push(leaf)
  }
  return [...groups].map(([index, leaves]) => ({ index, leaves }))
}

/**
 * Said at the point of choice, because the collision is knowable here and the
 * alternative is building a whole chain to be refused at the end of it.
 *
 * **It does not block.** The constraint lives in the database and the API
 * enforces it; this is a convenience, and a guard that refused to let the
 * visitor continue would be a second enforcement in a place that cannot see the
 * whole submission. Continuing lands on the review step with the entry marked -
 * see `entryIndexOf`.
 *
 * **The copy has three shapes now**, because "this combination" is wrong in three
 * different ways once a chain holds several foods: one colliding leaf names the food,
 * several name the list, and several colliding *chains* get one `<li>` each with its own
 * Open button.
 *
 * **The separator is `t(', ')`, and the key is the separator itself.** It was
 * `t('List separator')`, which is a *description* of a string rather than the string:
 * `i18n.js:293` says outright that English is not a catalogue file, so an English key IS
 * the English output, and the review step read
 * `DairyList separatorBakery and grainsList separatorFruit`. A catalogue can still
 * override it - `", "` is as overridable a key as any other, and a locale that joins
 * lists differently maps it to its own.
 */
function duplicateNotice() {
  const groups = draftRepeatsSavedEntries()
  if (!groups.length) return ''
  const separator = t(', ')
  const item = ({ index, leaves }) => {
    const number = index + 1
    const foods = leaves.map(leafName)
    const sentence = foods.length === 1
      ? t('Entry %(number)s already covers %(food)s at this supply-chain stage. Add these figures to entry %(number)s instead, or untick %(food)s here.', { number, food: foods[0] })
      : t('Entry %(number)s already covers %(foods)s at this supply-chain stage. Add these figures to entry %(number)s instead, or untick them here.', { number, foods: foods.join(separator) })
    return `<li><p>${escapeHtml(sentence)}</p><button class="text-button" type="button" data-action="edit-entry" data-index="${index}">${escapeHtml(t('Open entry %(number)s', { number }))}</button></li>`
  }
  const single = groups.length === 1 && groups[0].leaves.length === 1
  const heading = single ? t('You have already entered this combination') : t('Some of these food types are already entered')
  return `<aside class="disclaimer compact duplicate-notice" aria-label="${escapeHtml(t('Important information'))}"><span class="info-icon" aria-hidden="true">i</span><div><strong>${escapeHtml(heading)}</strong><ul class="duplicate-notice__list">${groups.map(item).join('')}</ul></div></aside>`
}

/**
 * Step 2, a **multi-select**.
 *
 * `type="checkbox"` on the same `.simple-choice` markup, the same `.selected` class and
 * the same `id="food-category-<code>"` the radios carried - the ids matter more here,
 * not less: `interfaces.md:3766` records that a same-step re-render restores focus by
 * `id`, and a visitor ticks several of these in a row.
 *
 * **Ticking order, not `sort_order`, is the leaf order.** The list is sorted as it
 * always was; the *selection* keeps the order the visitor built it in, so step 3's cards
 * appear in the order they were created and do not reshuffle when a category is added.
 *
 * **"I do not know" is a peer option at the end of the list, not a third bucket on the
 * wire.** Nothing ticked and "I do not know" ticked are the same submission - both send
 * `food_category: null` - and that is correct: §5.4's `unspecified` bucket means the user
 * did not break their waste down by type, and skipping an optional step and saying so are
 * the same answer to that question. What the tick buys is a visible affirmative answer, a
 * named row on step 3, and the ability to combine: "300 kg dairy and 200 kg I cannot
 * identify" is two distinct rows under `COALESCE(food_category_id, 0)` and would
 * otherwise have to be forced into a named category.
 *
 * **The ceiling is `MAX_ENTRIES`, and it counts leaves.** An unticked box is disabled
 * once the whole submission is at twenty, with the reason said rather than left to the
 * API to answer 400 for.
 */
function foodStep() {
  const categories = sorted(state.taxonomy.food_categories)
  const chosen = state.foodCategories.length + (state.foodUnspecified ? 1 : 0)
  // **Per box, and about the tick rather than about the count.** A box is disabled when
  // ticking *it* would take the submission past the ceiling - which is not the same
  // question as "is the submission at the ceiling now": ticking the first category on an
  // empty draft replaces that draft's category-less leaf with a named one and adds
  // nothing, so refusing it at exactly twenty refused a move the API would have taken.
  const isTicked = code => (code === UNSPECIFIED_CHOICE ? Boolean(state.foodUnspecified) : state.foodCategories.includes(code))
  const refused = code => !isTicked(code) && tickRefused(code, code === UNSPECIFIED_CHOICE)
  const atCeiling = [...categories.map(category => category.code), UNSPECIFIED_CHOICE].some(refused)
  const choice = (code, label, isSelected, sub, extra = '') =>
    `<label class="simple-choice ${isSelected ? 'selected' : ''} ${extra}"><input id="food-category-${slug(code)}" type="checkbox" name="food-category" value="${escapeHtml(code)}" ${isSelected ? 'checked' : ''} ${!isSelected && refused(code) ? 'disabled' : ''}><span><strong>${escapeHtml(label)}</strong>${sub ? `<small>${escapeHtml(sub)}</small>` : ''}</span>${isSelected ? `<span class="selected-label" aria-hidden="true">&#10003; ${escapeHtml(t('Selected'))}</span>` : ''}</label>`
  return `<section class="content-section" aria-labelledby="food-title"><p class="eyebrow">${escapeHtml(t('Step %(step)s', { step: 2 }))} &middot; ${escapeHtml(t('Optional'))}</p><h1 id="food-title">${escapeHtml(t('What types of food waste are you measuring?'))}</h1><p class="section-intro">${escapeHtml(t('Choose every category that applies, or continue without choosing one.'))}</p><fieldset class="choice-fieldset"><legend class="sr-only">${escapeHtml(t('Food type'))}</legend><p class="choice-count" aria-live="polite">${escapeHtml(t('%(count)s selected', { count: chosen }))}</p><div class="simple-choice-list">${categories.map(category => choice(category.code, category.name, state.foodCategories.includes(category.code), category.is_standard_mix ? t('Recommended if you do not separate food waste by category') : '')).join('')}${choice(UNSPECIFIED_CHOICE, t('I do not know, or my waste is not broken down by type'), state.foodUnspecified, t('Counted as its own answer. If your waste really is a mixture, choose the mixed category above instead.'), 'simple-choice--unspecified')}</div>${atCeiling ? `<p class="field-hint choice-ceiling" role="status">${escapeHtml(t('You can enter at most %(limit)s food types in one calculation. Untick one, or calculate what you have.', { limit: MAX_LEAVES }))}</p>` : ''}</fieldset>${chosen ? `<button type="button" class="text-button" data-action="clear-food">${escapeHtml(t('Clear all selections'))}</button>` : ''}${duplicateNotice()}${stepNav({ step: 1, back: backTarget(1) })}</section>`
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
const unitSelectValue = figures =>
  (figures.measureMode === 'container' ? PRESET_OPTION + (figures.unitPreset || '') : figures.totalUnit)

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
function unitOptionsHtml(selectedValue, presets = containerPresets()) {
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
function containerTotalText(figures) {
  if (!decimalPattern.test(figures.unitCount || '')) return ''
  const kilograms = containerTotal(figures)
  if (kilograms === '') return ''
  return t('That is about %(mass)s in total.', { mass: `${formatNumber(kilograms, 3)} kg` })
}

/**
 * A step-3 field name that carries its own explanation.
 *
 * **The trigger is the term itself, not an `i` beside it** — the client's own call. An
 * icon is a second thing to notice on a label that already carries a `(required)` or
 * `(optional)` tag; the dotted underline is carried by the words the explanation is
 * about, and `text-decoration: underline dotted` is the whole affordance. Without it
 * nobody hovers, so the rule is not decoration and must not be dropped as such.
 *
 * **It opens three ways, and the third is the one that needed code.** `:hover` and
 * `:focus-within` cover the pointer and the keyboard — `:focus-within` rather than
 * `:focus-visible`, which is keyboard-only and would have left a phone with nothing at
 * all. The span sits inside `<label for=…>`, so a tap activates the label, focus jumps
 * to the input, the span blurs and the panel shuts before it has been seen: the
 * delegated `click` listener in `bindCalculator` calls `preventDefault()` for a click
 * inside a `.term`, and that is the entire fix. No open/closed flag on `state` —
 * `render()` replaces `main.innerHTML` on every `setState`, so a flag there would
 * rebuild the whole step to show a tooltip.
 *
 * **The panel opens DOWNWARD, and its containing block is the LABEL, not the term.**
 * #96's `i` sits at the foot of a results card so its panel goes up; a term here is a
 * label at the *top* of a field, and upward put the panel outside the card and over the
 * page heading. Anchoring the inline axis to the label rather than to the span is the
 * same lesson `.result-explanation__body` records one screen over: a panel sized from a
 * short inline trigger has no relation to the room available, and at 320px a fixed
 * 250px minimum is wider than the zone it sits in. A block box spanning the label spans
 * the zone's own content width, so it cannot leave the screen at any viewport, in either
 * direction, by construction.
 *
 * `aria-describedby` points at the panel and the panel is `role="tooltip"`; the ids come
 * from `fieldId()`, so five leaves produce five distinct ones rather than five copies of
 * the same id.
 *
 * @param {string} text The term, already plain text — the field's name and nothing else.
 *   The unit and the `(NZ$)` marker stay outside it: they annotate the field, they are
 *   not what the explanation is about.
 * @param {string} tipId Per-leaf id, from `fieldId()`.
 * @param {string[]} paragraphs At most two, each a short sentence or two.
 * @returns {string} HTML.
 */
function term(text, tipId, paragraphs) {
  const body = paragraphs.map(line => `<p>${escapeHtml(line)}</p>`).join('')
  return `<span class="term" tabindex="0" aria-describedby="${tipId}">${escapeHtml(text)}<span class="tip" id="${tipId}" role="tooltip">${body}</span></span>`
}

/**
 * **Step 2.5's badge, and it is a different question from `cardStatus`'s on purpose**
 * (#138).
 *
 * The step is optional *as a whole*: `entryLeaves` gives a chosen category with no food
 * ticked exactly the leaf it gave before step 2.5 existed, so **"no food chosen" is a
 * legitimate answer and can never be wrong.** `cardStatus`'s second state says *Incomplete*
 * in `--error` with a cross, which is the right thing to say about a step-3 card that
 * Continue refuses and the wrong thing to say about an answer nothing refuses. So this
 * badge reports *answered* against *not looked at* and never *invalid*:
 *
 * * **The number is the answer.** `0 selected` is not an error, it is an empty count, and
 *   a visitor who meant to choose nothing is told their card is in the state they left it.
 *   It is also #138's own criterion that the collapsed card says how many foods are inside
 *   it, so the count *is* the badge rather than sitting beside one.
 * * **`neutral` has no colour rule in the stylesheet**, deliberately: it inherits the
 *   header's `--ink`, so the unanswered state is neither the green of complete nor the red
 *   of refused. The mark and the number carry it in the markup, so it is not colour-only
 *   either way.
 * * **The key is `%(count)s selected`, which this step's own `.choice-count` already
 *   prints.** One wording for one fact, and no twenty-first catalogue entry for a sentence
 *   that is already translated.
 *
 * A category the vocabulary offers no food for reads `0 selected` too, which is true;
 * *why* there is nothing to choose is the group's own `.field-hint`, which `itemStep` keeps
 * showing for the reason written there.
 */
const itemStatus = chosen => ({
  state: chosen ? 'complete' : 'neutral',
  mark: chosen ? '✓' : '—',
  text: t('%(count)s selected', { count: chosen }),
})

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
 * **Two tinted zones, symmetric, each with its own heading — and both always
 * visible.** The first holds the quantities the unit measures (the unit itself,
 * then the waste amount and the production total); the second holds the two
 * optional NZ$ figures. The split is *"what this unit measures"* against
 * *"supporting figures"*, not *"required"* against *"optional"*: the production
 * total is optional and still belongs with the waste amount, because one unit
 * control governs both, and a required/optional split would put the unit in one
 * zone and one of the two figures it applies to in the other.
 *
 * Client feedback, restated: the two columns had no visual separator, so reading
 * across drifted out of one column into the next and the reader only noticed when
 * a sentence failed to line up; reading down, nothing said where a column ended.
 * `gap: 0 20px` between two ~445px columns makes that drift inevitable. Each zone
 * now carries its own tint and border — `--zone` / `--zone-line` in `styles.css`,
 * one step lighter than `--mint`, which is the combined-total strip on this same
 * screen and would otherwise compete with them.
 *
 * **Nothing on this step is collapsed, and the leaf card's second column is no
 * longer dead.** The multi-leaf `<details class="leaf-extras">` disclosure is gone.
 * Its stated reason was §7.6.3's rule that advancing must never require scrolling,
 * which three full panels could not hold — written when the step bar was not
 * sticky. The bar is `position: sticky` now: with stickiness intact every step's
 * primary action is above the fold at all three viewports, and removing stickiness
 * fails every one of them. Content height no longer decides whether Continue is
 * reachable, so the only cost of showing everything is page length. The disclosure
 * also took the money pair *outside* the card's own grid, while the grid itself
 * carried `.amount-grid--leaf` — a class with no rule anywhere, so it inherited
 * `.amount-grid`'s two-column template with one child and left 445px of a 960px
 * card empty. Both zones now occupy that row.
 *
 * **The unit sits ABOVE the two quantities it governs, and is visibly narrower
 * than either.** It is not a third quantity beside the amount and the total, it is
 * what both of them are measured in, so it leads the column they are in and
 * `.unit-field select` is half-width to say so. Step 4's `.amount-with-unit` sets a
 * unit beside its amount and is right to: there the unit governs one number. Here
 * it governs two, and a compound control could only ever sit beside one of them.
 * The closed control may still truncate a long container label (staff type
 * `unit_preset.label`, and there is no ceiling on how long it can be), so the
 * select's `title` carries the option currently selected in full, for hover and
 * assistive technology.
 */
function leafPanel(leaf, leaves, index) {
  const figures = draftLeafFigures(leaf)
  const key = leafKey(leaf)
  const single = leaves.length === 1
  const container = figures.measureMode === 'container'
  const amountId = fieldId(container ? 'unit-count' : 'total-waste', leaf, leaves)
  const amountLabel = container
    ? t('How many containers?')
    : single ? t('Waste amount') : t('Waste amount for %(food)s', { food: leafName(leaf) })
  const amountHint = container
    ? t('Use up to two decimal places — enter 0.5 for a half-full container.')
    : t('Use up to two decimal places.')
  const amountValue = container ? figures.unitCount : figures.totalAmount
  // **The place, not the prose.** This used to compare `state.error` against the string
  // each validator returns; two leaves produce the byte-identical sentence, so the
  // highlight landed on whichever card was asked first. `state.errorAt` records which
  // leaf and which field the message belongs to, set by `stepProblem` at the moment the
  // message was produced.
  const isApiError = state.errorCode === 'VALIDATION_ERROR'
  const isClientError = !isApiError && Boolean(state.error)
  const at = state.errorAt
  const mine = field => (isClientError && at && at.leaf === key && at.field === field ? state.error : null)
  const amountFieldError = mine('amount')
  const moneyContradictionError = mine('wastedValue')
  // The three round-two scalar fields' own per-field errors, keyed by the exact path the
  // server named. **The draft now occupies a RANGE of request indices** - one per leaf -
  // so the path is this leaf's own position in the submission, never
  // `entries[state.entries.length]`.
  const requestIndex = savedLeafCount() + index
  const scalarError = field => state.fieldErrors[`entries[${requestIndex}].${field}`]
  const totalInputError = scalarError('total_input_kg')
  const totalValueError = scalarError('total_value_nzd')
  const wastedValueError = scalarError('wasted_value_nzd') || moneyContradictionError
  const unitId = fieldId('total-unit', leaf, leaves)
  const inputId = fieldId('total-input', leaf, leaves)
  const valueId = fieldId('total-value', leaf, leaves)
  const wastedId = fieldId('wasted-value', leaf, leaves)
  const totalId = fieldId('container-total', leaf, leaves)
  // The error paragraph is addressed by `aria-describedby` and by three browser tests;
  // it keeps the singleton spelling for a singleton leaf, exactly as the boxes do.
  const errorId = fieldId('amount-error', leaf, leaves)
  const presets = containerPresets(leaf.foodCategory)
  const data = field => `data-leaf-field="${field}" data-leaf="${keyAttr(key)}"`
  //: The four term tooltips. Each is at most two short paragraphs, and each says
  //: something the visible `.field-hint` does not — a hint that a tooltip repeats is a
  //: hint the visitor has been made to hover for twice.
  //:
  //: **Nothing here may say the money figures become a cost.** O-2 is decided
  //: (v1.48): `cost` is the waste levy plus disposal cost, `const_FOOD_VALUE_PER_KG`
  //: stays at zero permanently, and the value of the food never enters a metric. So the
  //: limit this sentence states is about the **emissions and cost figures**, which is
  //: the claim the client actually ruled on.
  //:
  //: **It may not say no results figure is computed from these two, which is what it
  //: said until #159.** The results page prints *Share of value wasted*, and §4.5
  //: computes it as `wasted_value_nzd ÷ total_value_nzd × 100` — from exactly these
  //: two boxes. `moneySummary` also echoes both sums back (*Total value of food
  //: handled*, *Value of food wasted*) and `savingLines` prints `saving_nzd`, which
  //: §4.5 derives from `wasted_value_nzd`. "Statistics only" is true in the sense that
  //: changes no impact number and false as a claim about the whole page; the sentence
  //: now names the one derived ratio and states the real limit.
  //:
  //: The sentence is shared by both money terms because it is the same fact about both
  //: — one key, said twice. *"the two"* reads for either box: the production term's
  //: first paragraph already says "both money figures" and the waste term's says "the
  //: figure above".
  const statisticsOnly = t('It joins the anonymous statistics, and the two together give the share of value wasted on the results page. No emissions or cost figure is calculated from it.')
  const amountTip = container
    ? [t('Count the containers you filled with waste over the period you are reporting, not the food inside them.'),
       t('The calculator turns the count into a weight using the container chosen above.')]
    : [t('Only the food that left your process as waste over the period you are reporting — not everything you handled.'),
       t('Every emissions figure on the results page is built from this number.')]
  const amountTipId = fieldId('term-tip-amount', leaf, leaves)
  const inputTipId = fieldId('term-tip-total-input', leaf, leaves)
  const valueTipId = fieldId('term-tip-total-value', leaf, leaves)
  const wastedTipId = fieldId('term-tip-wasted-value', leaf, leaves)
  //: Five fields, in the order a visitor answers them. **`h2`, not `h3`**: the step's
  //: own title is the `h1` in `amountStep`, the leaf card's name is a `<legend>` and a
  //: legend is not a heading, so `h3` here would skip a level. These name a group and
  //: belong in the outline, which is why they are headings and not styled paragraphs.
  //:
  //: **The two NZ$ fields carry a hint each again, and the two are different sentences.**
  //: They used to carry the same sentence, twice, verbatim - "For statistics only - it
  //: never enters the emissions calculation." That fact is about both fields, so the zone
  //: says it once now, over both; what was missing afterwards was anything saying *what
  //: to put in the box*, which is different for each of the two and is the one thing a
  //: visitor stalls on. Every other field on this step carries a hint, and decision 6 of
  //: the zoning plan is that nothing essential lives behind a hover, so a bare label here
  //: would have put "which price?" behind the tooltip and nowhere else.
  //:
  //: **Both hints say "total", and both are scoped to THIS card.** The client could not
  //: tell whether the NZ$ boxes wanted the total value of the amount or a value per unit
  //: (PR #147), so each hint now says "the total value" and the production one says "not
  //: the value per unit" outright. The scope is not decoration: `leafPanel` draws one
  //: panel per leaf, each panel's figure becomes its own `EntryInput.total_value_nzd`,
  //: and `engine/calculate.py` SUMS those across entries - so a hint reading "everything
  //: produced during this reporting period" invites a visitor with three food types to
  //: type the whole-business total three times and see it trebled on the results page.
  //: "for this food type" is the same scoping `Waste amount for %(food)s` already uses on
  //: this card, and it still reads correctly where there is only one.
  //:
  //: **The waste hint keeps the shared-basis clause, and in one sentence.** It used to be
  //: a sentence of its own ("Valued the same way as production above."); it is now the
  //: trailing clause of the same sentence, because `wasted_share_percent` is
  //: `wasted_value_nzd / total_value_nzd` (§4.5) and is printed on the results page, so a
  //: mismatched basis makes that percentage meaningless. The server's only guard is
  //: `wasted <= total`, which cannot see a basis mismatch, and the tooltip is a hover - so
  //: the constraint has to be in the always-visible hint.
  const amountField = `<div class="form-field ${amountFieldError ? 'has-error' : ''}"><label for="${amountId}">${term(amountLabel, amountTipId, amountTip)} <span class="required">${escapeHtml(t('(required)'))}</span></label><p class="field-hint">${escapeHtml(amountHint)}</p><input id="${amountId}" ${data(container ? 'count' : 'amount')} type="number" inputmode="decimal" min="0" step="0.01" value="${escapeHtml(amountValue)}" ${amountFieldError ? `aria-invalid="true" aria-describedby="${errorId}"` : ''}>${amountFieldError ? `<p class="field-error" id="${errorId}" role="alert">${escapeHtml(amountFieldError)}</p>` : ''}${container ? `<p class="container-total" id="${totalId}" aria-live="polite">${escapeHtml(containerTotalText(figures))}</p>` : ''}</div>`
  const unitField = `<div class="form-field unit-field"><label for="${unitId}">${escapeHtml(t('Unit'))} <span class="required">${escapeHtml(t('(required)'))}</span></label><p class="field-hint">${escapeHtml(t('Choose a weight, or the container you fill.'))}</p><select id="${unitId}" ${data('unit')} title="${escapeHtml(rowUnitLabel(unitSelectValue(figures)))}">${unitOptionsHtml(unitSelectValue(figures), presets)}</select></div>`
  //: **This hint is scoped to the card, and the word "stage" is why it had to change.**
  //: Until #158 it read "Everything that went through this stage over the same period,
  //: waste included" — a **stage**, which is the supply chain, not this card.
  //: `total_input_kg` is a per-leaf field: each panel's box becomes that leaf's own
  //: `EntryInput.total_input_kg`, and `engine/calculate.py` sums them for
  //: `totals.production_share_percent` (§4.6). So a visitor with three food types who
  //: read it literally typed the whole stage's throughput three times, making the
  //: denominator three times too large and the waste share three times too small — and
  //: that share is printed on the results page, not confined to the statistics. Scoped
  //: in the shape v1.85 settled for the two money hints ("for this food type"), one
  //: sentence, keeping the "waste included" clarification: the denominator has to
  //: contain the wasted mass or the share it carries is not a share of production.
  //:
  //: **The two sentences sit in two places, and neither repeats the other.** Issue #65
  //: is the client saying this field is not explained; the explanation existed from
  //: 2026-09-19 but only behind the term tooltip, which a reader has to know is there
  //: before it can help them. So *what to put in the box* is always on screen as the
  //: hint, and *why it is optional and what it changes* stays in the tooltip. Printing
  //: both in both places was tried first: `aria-describedby` makes the tooltip the
  //: label's description, so a screen reader read the same two sentences twice in a
  //: row - once after the label, once as the hint.
  const totalInputHint = t('Enter the total amount you produced for this food type over the same period, waste included.')
  const totalInputField = `<div class="form-field ${totalInputError ? 'has-error' : ''}"><label for="${inputId}">${term(t('Total amount produced'), inputTipId, [t('It lets the results show the waste as a share of production. Leaving it empty changes no emissions figure.')])} (${escapeHtml(unitLabel(figures.totalUnit))}) <span class="optional-tag">${escapeHtml(t('(optional)'))}</span></label><p class="field-hint">${escapeHtml(totalInputHint)}</p><input id="${inputId}" ${data('totalInput')} type="number" inputmode="decimal" min="0" step="0.001" value="${escapeHtml(figures.totalInputKg)}" ${totalInputError ? `aria-invalid="true" aria-describedby="${inputId}-error"` : ''}>${totalInputError ? `<p class="field-error" id="${inputId}-error" role="alert">${escapeHtml(totalInputError)}</p>` : ''}</div>`
  //: **An example value, and it carries NO thousands separator.** Both boxes are
  //: `type="number"`, which refuses `50,000` outright — a placeholder showing one would
  //: demonstrate a format the field rejects, which is worse than showing nothing. PR #101
  //: proposed exactly that; the idea is its, the digits are not.
  //:
  //: One key with the number interpolated rather than two spelled-out keys: "e.g." is a
  //: Latin abbreviation and needs translating (`z. B.`, `p. ex.`, `例：`), the number does
  //: not — every catalogue already carries Latin digits, Arabic and Urdu included.
  const example = amount => `placeholder="${escapeHtml(t('e.g. %(amount)s', { amount }))}"`
  const totalValueField = `<div class="form-field ${totalValueError ? 'has-error' : ''}"><label for="${valueId}">${term(t('Value of production'), valueTipId, [t('What everything you produced was worth. Any consistent basis will do, as long as both money figures use the same one.'), statisticsOnly])} (NZ$) <span class="optional-tag">${escapeHtml(t('(optional)'))}</span></label><p class="field-hint">${escapeHtml(t('Enter the total value of everything you produced for this food type over the reporting period, not the value per unit.'))}</p><input id="${valueId}" ${data('totalValue')} type="number" inputmode="decimal" min="0" step="0.01" ${example('50000')} value="${escapeHtml(figures.totalValueNzd)}" ${totalValueError ? `aria-invalid="true" aria-describedby="${valueId}-error"` : ''}>${totalValueError ? `<p class="field-error" id="${valueId}-error" role="alert">${escapeHtml(totalValueError)}</p>` : ''}</div>`
  const wastedValueField = `<div class="form-field ${wastedValueError ? 'has-error' : ''}"><label for="${wastedId}">${term(t('Value of the waste'), wastedTipId, [t('What the wasted food was worth, on the same basis as the figure above. It cannot be more than the value of production.'), statisticsOnly])} (NZ$) <span class="optional-tag">${escapeHtml(t('(optional)'))}</span></label><p class="field-hint">${escapeHtml(t('Enter the total value of the waste for this food type over the same period, valued the same way as production above.'))}</p><input id="${wastedId}" ${data('wastedValue')} type="number" inputmode="decimal" min="0" step="0.01" ${example('1200')} value="${escapeHtml(figures.wastedValueNzd)}" ${wastedValueError ? `aria-invalid="true" aria-describedby="${wastedId}-error"` : ''}>${wastedValueError ? `<p class="field-error" id="${wastedId}-error" role="alert">${escapeHtml(wastedValueError)}</p>` : ''}</div>`
  const zones = `<section class="zone"><h2>${escapeHtml(t('How much was wasted'))}</h2><p class="zone-sub">${escapeHtml(t('Needed for the calculation.'))}</p>${unitField}${amountField}${totalInputField}</section><section class="zone"><h2>${escapeHtml(t('Supporting figures'))}</h2><p class="zone-sub">${escapeHtml(t('Optional. They never enter the emissions calculation.'))}</p>${totalValueField}${wastedValueField}</section>`
  //: **`.amount-grid` carries no declarations of its own any more** - `.zones` is what
  //: lays this step out and both branches share it - but the class stays on the
  //: single-leaf panel because three browser tests address it directly, as the one
  //: element wrapping every step-3 field: `test_step_navigation.py` removes it and
  //: requires the document to collapse to the viewport, selects `.amount-grid
  //: .form-field`, and `test_leaf_figure_migration_browser.py` reads its `innerText`.
  //: **One leaf is not a card, which is decision 2 of #134 taken literally.** *A single
  //: card is not collapsed* - and where there is exactly one food type there is nothing
  //: below the fold to be missed, so there is nothing for the chrome to buy either. This
  //: branch therefore stays byte for byte what it has always been, which is also what
  //: `test_leaf_figure_migration_browser.py` says in words ("one leaf renders today's
  //: screen byte for byte - no card, no legend") and what the three `.amount-grid` tests
  //: above measure. `cardIsOpen` carries the same decision for the consumers that do
  //: draw a card at a count of one.
  if (single) return `<div class="form-panel amount-grid zones">${zones}</div>`
  //: A card per leaf, collapsible since #134. `.leaf-panel` and `data-leaf-panel` stay
  //: on the `<fieldset>` - `test_leaf_multiselect_browser.py` dereferences
  //: `panel.querySelector('legend')` unconditionally and throws on anything else, and
  //: `updateCardBadges` finds a card's badge through `data-leaf-panel`.
  //:
  //: **A card holding a message about itself is open whatever the open set says.** A
  //: collapsed card hiding its own error is the reverse of the problem #134 is about, so
  //: openness is forced by the presence of a message rather than only arranged by
  //: Continue - which also covers the one route Continue does not own, a server
  //: `VALIDATION_ERROR` naming one of the three scalar fields on a step the visitor was
  //: routed back to.
  const errored = Boolean(amountFieldError || totalInputError || totalValueError || wastedValueError)
  return collapsibleCard({
    step: AMOUNT_CARD_STEP,
    key,
    anchor: `amount-${index + 1}`,
    name: leafName(leaf),
    count: leaves.length,
    forceOpen: errored,
    status: cardStatus(leafSettled(2, leaf, leafName(leaf))),
    body: `<div class="zones">${zones}</div>`,
    extraClass: 'leaf-panel',
    dataAttr: `id="amount-leaf-${index + 1}" data-leaf-panel="${keyAttr(key)}"`,
  })
}

/**
 * Step 3, one panel per leaf.
 *
 * **The containers are options on the unit `<select>`, not a second mode with its own
 * fieldset, and the reason is measured.** The first build of this input put a "By weight /
 * By container" radio pair above the form: two `.simple-choice` cards and a legend, 169px
 * at 1278x983 on a step that had 50px of headroom. It pushed the document to 1102px in a
 * 983px viewport and broke three of `tests/web/test_step_navigation.py`'s assertions at
 * once - §7.6.3's rule that advancing must never require scrolling, which this project
 * measures rather than assumes.
 *
 * **Not a `<table>`, and that is arithmetic rather than preference.** Five inputs across N
 * rows cannot be a table at 320px: `body { min-width: 320px }` and
 * `tests/web/test_horizontal_overflow.py` measures `scrollWidth` against `clientWidth` at
 * 320 and 390 in a real browser with the scrollbar drawn. So the "table" is a stack of
 * per-leaf cards and each card is exactly today's panel - every existing rule, the 650px
 * two-column split and the block-flow stacking below it all apply unchanged, per card.
 *
 * **Two tinted zones at `min-width: 650px`, not a grid of five equal cells.** The first
 * holds the unit and the two quantities it measures - the waste amount and the production
 * total, in the order a visitor answers them - and the second holds the two optional NZ$
 * figures, which may be left entirely empty. Below the breakpoint the zones stack and
 * every field inside them is ordinary block flow, which is already the correct
 * single-column reading order. See `leafPanel`'s own docstring for why the split is not
 * required-against-optional.
 *
 * **The combined total is under the stack, and it is browser-only arithmetic.** Step 4
 * allocates against each leaf's own amount, but the visitor is about to be shown a matrix
 * of N columns and the chain's own total is what tells them the fork adds up. It is a sum
 * of figures already on screen and it never reaches the wire - the same category of
 * arithmetic `allocatedAmount` already performs.
 */
/**
 * **Step 2.5, and it is the second panel of step 2 rather than a seventh step.**
 *
 * `spec.md` §3.3: *step 2.5 refines step 2; it does not replace it.* One screen, one
 * group per chosen category, in the order they were ticked -- no nested wizard and no
 * repeat-per-category pass. A category with **no food ticked stays a category-level
 * leaf**, which is the natural "I know it was fruit, but not which fruit" answer and
 * is what `entryLeaves` already does with an empty list.
 *
 * **The step number does not advance.** It is the same question asked one level finer,
 * so the eyebrow still says step 2 and the progress bar does not move; what changes is
 * the panel. Making it a seventh step would have meant renumbering twenty hard-coded
 * step references for a panel that is absent from most deployments, and would have made
 * "step 3 of 7" a lie whenever the flag is off.
 *
 * **The ceiling is the same twenty leaves**, asked per box through `entryLeaves` --
 * see `draftTickingItem` for why ticking a food is not always one more leaf.
 */
/**
 * Step 2.5's tick.
 *
 * **Order within a category is ticking order**, for the reason `foodCategories` is:
 * steps 3 and 4 iterate the leaves, and a list that reshuffled when a box was ticked
 * would move a visitor's half-filled panel out from under them.
 *
 * **An untick drops the food and nothing else.** The category it sat under stays
 * ticked -- that is step 2's answer -- and unticking the last food under a category
 * returns it to a category-level leaf, which `entryLeaves` does on its own. The
 * figures typed against the leaf are kept, exactly as `toggleFoodChoice` keeps them
 * for a category: re-ticking restores what was typed, and `draftEntry()` prunes at
 * the boundary so nothing dead is ever sent.
 */
function toggleFoodItem(target) {
  const category = target.dataset.category
  if (!category) return
  // Re-checked rather than trusted, for the reason step 2's tick re-checks: a
  // `disabled` attribute is a rendering, and this is the rule.
  if (target.checked && itemTickRefused(category, target.value)) {
    target.checked = false
    return
  }
  const current = (state.foodItems || {})[category] || []
  const next = target.checked
    ? [...current.filter(code => code !== target.value), target.value]
    : current.filter(code => code !== target.value)
  setState({
    foodItems: { ...(state.foodItems || {}), [category]: next },
    error: null,
    errorAt: null,
  })
}

/**
 * **The step number step 2.5's cards are filed under in `state.openCards`.**
 *
 * `cardId` joins the owning step to the card's own key so that two screens drawing one
 * card per the same thing cannot share a fact. Step 2.5 is the second panel of step 1 —
 * `state.step` does not advance into it (`itemStep`'s own note) — and step 1's category
 * panel draws no cards at all, so `1` is this screen's and nothing else's. The constant
 * exists so the renderer and nothing else decides it: the `toggle-card` handler reads the
 * step back off the element it was pressed on.
 */
const ITEM_CARD_STEP = 1

/**
 * The Step 2.5 category list, using the expanded-card treatment from the results
 * page's section navigation but without its disclosure handle. The links are derived from `foodCategories`, not
 * from the item vocabulary: every category chosen on Step 2 therefore remains
 * represented even when it has no more specific foods to show.
 */
function itemFloatingNavigation() {
  if (state.foodCategories.length < 2) return ''
  const entries = state.foodCategories.map((category, index) => {
    const definition = selected(state.taxonomy.food_categories, category)
    return { name: definition?.name || category, id: `item-group-${index + 1}` }
  })
  return stepFloatingNavigation(entries, 'item-floating-nav')
}

/** Shared expanded navigation for the category, amount and destination panels. */
function stepFloatingNavigation(entries, extraClass) {
  const links = entries.map(({ name, id }) => `<li><button type="button" data-action="focus-card" data-nav-target="${id}">${escapeHtml(name)}</button></li>`).join('')
  return `<nav class="step-floating-nav ${extraClass}" aria-label="${escapeHtml(t('Sections on this page'))}"><div class="step-floating-nav__panel"><ul class="step-floating-nav__links ${extraClass}__links">${links}</ul></div></nav>`
}

let itemNavScrollBound = false
let itemNavFrame = null

/** Mark the last section that has reached the viewport's reading line. */
function markCurrentStepSection(preferredId = null) {
  const nav = document.querySelector('.step-floating-nav')
  if (!nav) return
  const links = [...nav.querySelectorAll('.step-floating-nav__links button[data-nav-target]')]
  if (!links.length) return
  // Item groups can be much taller than result sections. Halfway down the
  // viewport changes the marker when the next group's heading is actually in
  // view, including a short final group that cannot reach the page top.
  const threshold = window.innerHeight * 0.5
  let current = links[0]
  if (window.scrollY > 1) {
    for (const link of links) {
      const target = document.getElementById(link.dataset.navTarget)
      if (target?.getClientRects().length && target.getBoundingClientRect().top <= threshold) current = link
      else break
    }
  }
  // The last group can be too close to the document end to ever reach the
  // reading line. At the bottom of the page it is nevertheless the group in
  // view, so let the document boundary settle the final item.
  const scrollable = document.documentElement.scrollHeight > window.innerHeight + 1
  const atBottom = scrollable && Math.ceil(window.scrollY + window.innerHeight) >= document.documentElement.scrollHeight - 1
  if (atBottom && window.scrollY > 1) current = links[links.length - 1]
  if (preferredId) current = links.find(link => link.dataset.navTarget === preferredId) || current
  for (const link of links) {
    if (link === current) link.setAttribute('aria-current', 'location')
    else link.removeAttribute('aria-current')
  }
}

/** Rebind the section marker after `render()` replaces `<main>`. */
function bindStepSectionNavigation(root) {
  if (!root?.querySelector?.('.step-floating-nav')) return
  if (!itemNavScrollBound) {
    itemNavScrollBound = true
    const schedule = () => {
      if (itemNavFrame !== null) return
      itemNavFrame = requestAnimationFrame(() => {
        itemNavFrame = null
        markCurrentStepSection()
      })
    }
    document.addEventListener('scroll', schedule, { passive: true })
    window.addEventListener('resize', schedule, { passive: true })
  }
  markCurrentStepSection()
}

function itemStep() {
  const chosen = Object.values(state.foodItems || {}).reduce((total, items) => total + items.length, 0)
  const ticked = (category, item) => ((state.foodItems || {})[category] || []).includes(item)
  const refused = (category, item) => !ticked(category, item) && itemTickRefused(category, item)
  const atCeiling = state.foodCategories.some(
    category => itemsUnder(category).some(item => refused(category, item.code)))
  //: **One collapsible card per chosen category** (#138). The client's report is #134's on
  //: a different screen: five ticked categories make a page long enough that the one below
  //: the fold goes unanswered, and nothing on screen says so.
  //:
  //: **What is NOT shared with step 3 is the badge**, and that is the whole difference
  //: between the two consumers. See `itemStatus`: this step is optional as a whole, so the
  //: badge counts what was chosen and never calls an empty card wrong.
  const count = state.foodCategories.length
  const group = (category, index) => {
    const definition = selected(state.taxonomy.food_categories, category)
    const items = itemsUnder(category)
    const heading = definition?.name || category
    //: A chosen category the vocabulary has no food for is shown saying so, not
    //: hidden. Hiding it would make the group list disagree with step 2's ticks,
    //: and a visitor who ticked five categories and sees four groups has to work
    //: out which one went missing and why. **Folding does not change that**: the card
    //: is drawn, its badge reads `0 selected`, and the sentence is what is inside it.
    const body = items.length
      ? `<div class="simple-choice-list">${items.map(item =>
          `<label class="simple-choice ${ticked(category, item.code) ? 'selected' : ''}"><input id="food-item-${slug(category)}-${slug(item.code)}" type="checkbox" name="food-item" value="${escapeHtml(item.code)}" data-category="${escapeHtml(category)}" ${ticked(category, item.code) ? 'checked' : ''} ${refused(category, item.code) ? 'disabled' : ''}><span><strong>${escapeHtml(item.name)}</strong></span>${ticked(category, item.code) ? `<span class="selected-label" aria-hidden="true">&#10003; ${escapeHtml(t('Selected'))}</span>` : ''}</label>`).join('')}</div>`
      : `<p class="field-hint">${escapeHtml(t('No specific foods are listed for this category. It is counted as %(category)s.', { category: heading }))}</p>`
    //: `.item-group` stays on the `<fieldset>`: it is how three browser tests address a
    //: group and its `<legend>`, and the legend still carries the category's own name —
    //: `.sr-only` now, because the name a sighted reader sees is the card header's.
    //: `.choice-fieldset` is gone, and that is not cosmetic: its `border: 0` would erase
    //: the card's ground, and the step-1 panel it was shared with still has it (which is
    //: what `test_step_navigation.py`'s first-panel margin measurement reads).
    return collapsibleCard({
      step: ITEM_CARD_STEP,
      key: category,
      anchor: `item-${index + 1}`,
      name: heading,
      count,
      status: itemStatus(((state.foodItems || {})[category] || []).length),
      body,
      extraClass: 'item-group',
      dataAttr: `id="item-group-${index + 1}" data-item-group="${escapeHtml(category)}"`,
    })
  }
  //: **The ceiling notice and the clear button are outside every card**, which is #138's
  //: criterion that the `MAX_LEAVES` message stays visible whether the cards are open or
  //: shut: the boxes it explains are inside the cards and go dark there, so a message
  //: folded away with them would leave a visitor looking at nothing.
  const content = `<div class="item-step__content"><p class="eyebrow">${escapeHtml(t('Step %(step)s', { step: 2 }))} &middot; ${escapeHtml(t('Optional'))}</p><h1 id="item-title">${escapeHtml(t('Do you know which foods these were?'))}</h1><p class="section-intro">${escapeHtml(t('Choose the specific foods you measured, or continue without choosing any. A category with no food chosen is counted as that category.'))}</p><p class="choice-count" aria-live="polite">${escapeHtml(t('%(count)s selected', { count: chosen }))}</p>${state.foodCategories.map(group).join('')}${atCeiling ? `<p class="field-hint choice-ceiling" role="status">${escapeHtml(t('You can enter at most %(limit)s food types in one calculation. Untick one, or calculate what you have.', { limit: MAX_LEAVES }))}</p>` : ''}${chosen ? `<button type="button" class="text-button" data-action="clear-items">${escapeHtml(t('Clear all selections'))}</button>` : ''}</div>`
  return `<section class="content-section item-step" aria-labelledby="item-title">${itemFloatingNavigation()}${content}${stepNav({ step: 1, back: 1, backAction: 'back-to-categories' })}</section>`
}

/**
 * **The step 3 card, as a step number, in one place** — `leafProblem`'s rule block,
 * `collapsibleCard`'s card ids and the summary's own `focus-field` all mean this step,
 * and all three used to spell it `2` at their own call site. `ITEM_CARD_STEP` above is
 * the same idea for step 2.5.
 */
const AMOUNT_CARD_STEP = 2

/**
 * **The step 3 fields a message can be attached to, and the one place that names them.**
 *
 * Four readers want the same list: `leafProblem` returns one of these names as the
 * `field` it refused, `leafPanel` draws the box, `focusLeafField` moves focus to it by
 * name, and `amountErrorItems` below prints its label. A second list of field names for
 * one of those readers is the defect `destinationStep`'s own comment records — and the
 * list this replaces was already a second one, and already wrong: it knew `amount` and
 * `wastedValue` and answered **nothing at all** for `totalInput`, `totalValue`,
 * `allocation` or any name added later, so a refusal naming one of those left the page
 * silent.
 *
 * **The label is the label `leafPanel` puts on the box, by key, not a second wording.**
 * A summary reading *Waste value* over a field labelled *Value of the waste* is a
 * summary the visitor has to translate. `amount` is a function of the figures because it
 * is one question asked in two modes — a mass in `#total-waste`, a count in
 * `#unit-count` — which is exactly how `leafPanel` and `focusLeafField` both treat it.
 *
 * **Step 4's `allocation` is deliberately absent.** This is step 3's list, and
 * `leafProblem(AMOUNT_CARD_STEP, …)` cannot return it. A name this map does not carry is
 * handled rather than assumed impossible — see `amountErrorItems`' `unbound`.
 */
const AMOUNT_STEP_FIELD_LABELS = {
  amount: figures => (figures.measureMode === 'container' ? t('How many containers?') : t('Waste amount')),
  totalInput: () => t('Total amount produced'),
  totalValue: () => t('Value of production'),
  wastedValue: () => t('Value of the waste'),
}

/**
 * Every step 3 field the refusal on screen is about, in card order, plus a count of the
 * ones that could not be named.
 *
 * This is the index beside the step title (#133). The detailed message still belongs
 * beside its own input; what this adds is *which cards* — a forked step 3 folds its
 * cards, so without it a visitor is told something is wrong and has to open each card to
 * find out which.
 *
 * **Two halves, because step 3 is refused in two ways.**
 *
 * * **Continue's own rules.** `leafProblem` is the only copy of them (v1.81) and already
 *   answers per leaf, so this walks the leaves and asks it rather than re-deriving
 *   anything. It must not go through `stepProblemAt`, which stops at the **first**
 *   problem because that is all Continue needs: a summary built on it is a one-item list
 *   naming the one card `focusLeafField` has already expanded and focused, which is a
 *   second surface restating a single fact. #133's criterion (b) is that *each* missing
 *   or invalid field is identified, and three blank cards are three faults.
 * * **The server's `VALIDATION_ERROR`.** One item per scalar path `state.fieldErrors`
 *   carries, read through `ENTRY_SCALAR_FIELDS` — the map whose own note says one place
 *   rather than a per-call-site guess — and rooted at each leaf's own request index, the
 *   same arithmetic `leafPanel`'s `scalarError` does.
 *
 * **`unbound` is what the banner needs.** It counts the refusals in play that this list
 * could not put a name and a destination against: a detail naming a saved entry, an
 * `alternative[…]` path or a field this form has no box for (`validationMessage` spells
 * those out in the banner for exactly that reason), and a `leafProblem` field
 * `AMOUNT_STEP_FIELD_LABELS` does not know. Either way the text has nowhere else to go,
 * so `amountStep` keeps the banner. Suppressing it unconditionally loses it.
 */
function amountErrorItems(leaves) {
  const single = leaves.length === 1
  const isApiError = state.errorCode === 'VALIDATION_ERROR'
  const isClientError = !isApiError && Boolean(state.error)
  const items = new Map()
  let unbound = 0
  const add = (leaf, field) => {
    const label = AMOUNT_STEP_FIELD_LABELS[field]
    if (!label) {
      unbound += 1
      return
    }
    const key = leafKey(leaf)
    // Keyed by (leaf, field) so the two halves cannot list one box twice: a money
    // contradiction refused by Continue and a `wasted_value_nzd` the server also named
    // are one box and therefore one item.
    items.set(`${key}\u0000${field}`, {
      leaf: key,
      field,
      food: single ? '' : leafName(leaf),
      fieldName: label(draftLeafFigures(leaf)),
    })
  }

  // `state.errorAt` is the gate rather than `state.error` alone, and it is the same gate
  // `leafPanel`'s `mine()` uses: it says this message came from THIS step's Continue and
  // named a leaf. A `BLOCKED` banner (§9.2) survives a step change by design and carries
  // no `errorAt`, so it must not be turned into a list of blank amount boxes.
  if (isClientError && state.errorAt?.leaf) {
    for (const leaf of leaves) {
      const problem = leafProblem(AMOUNT_CARD_STEP, leaf, single ? null : leafName(leaf))
      if (problem.message) add(leaf, problem.field)
    }
  }

  if (isApiError) {
    const base = savedLeafCount()
    leaves.forEach((leaf, index) => {
      for (const [path, scalar] of Object.entries(ENTRY_SCALAR_FIELDS)) {
        if (state.fieldErrors[`entries[${base + index}].${path}`]) add(leaf, scalar.field)
      }
    })
    unbound += unboundFieldErrors().length
  }
  return { items: [...items.values()], unbound }
}

/**
 * The index itself.
 *
 * **Each item is a `button`, not an `<a href="#id">`, and that is a privacy decision
 * rather than a style one.** The ids on this step are `fieldId`'s —
 * `total-waste--bakery-grains`, a slug of a food category the visitor chose — and a
 * fragment link writes that into the address bar, where §7.2b's own note says the
 * answers must not go: *"a URL is pasted into chats and written into intermediaries'
 * logs."* `?step=` and `#step-3` were refused on exactly that ground, and a food is a
 * more specific answer than a step number. It also pushed a session-history entry
 * `history.js`'s `traverse` ignores, which made the first Back after a click a dead
 * press. So the item navigates the way this project navigates — `data-action`, then
 * `openedCard` and `focusLeafField`, the pair decision 3 of #134 already uses for a
 * refused Continue.
 *
 * **`role="alert"`, with `--error` on the border.** The ground is the Banana tint the
 * tangible-equivalent cards use, which is brand-correct and gives Kale text 13.28:1 on
 * it — but against the page that surface is 1.07:1, which is an information card wearing
 * an assertive live region's semantics. The border carries `--error` (Beetroot) instead,
 * 9.02:1 on the painted ground, so the surface reads as the alert it declares itself to
 * be. It keeps the announcement, because it is the only thing that tells a screen-reader
 * visitor there are three faults rather than the one the focus was moved to. The
 * `aria-labelledby` it used to carry pointed at its own first child, which made the title
 * both the region's name and its content and had it read out twice.
 */
function amountErrorSummary(items) {
  if (!items.length) return ''
  const links = items.map(item => `<li><button type="button" class="amount-validation-summary__link" data-action="focus-field" data-leaf="${keyAttr(item.leaf)}" data-field="${escapeHtml(item.field)}">${item.food ? `<span>${escapeHtml(item.food)}</span><span aria-hidden="true"> &mdash; </span>` : ''}<span>${escapeHtml(item.fieldName)}</span></button></li>`).join('')
  return `<aside class="amount-validation-summary" role="alert"><p>${escapeHtml(t('Check the highlighted fields and try again.'))}</p><ul>${links}</ul></aside>`
}

function amountStep() {
  const leaves = draftLeaves()
  const single = leaves.length === 1
  const isApiError = state.errorCode === 'VALIDATION_ERROR'
  const isClientError = !isApiError && Boolean(state.error)
  const errors = amountErrorItems(leaves)
  const errorSummary = amountErrorSummary(errors.items)
  // **The classification's own `else`.** A `state.error` that belongs to no leaf field on
  // this screen - `BLOCKED` (§9.2), which `clearedError` deliberately keeps across a step
  // change - matched nothing before this line and rendered nothing at all.
  //
  // **The summary suppresses the banner only where it says the same sentence.** With
  // every refusal in play carrying an item of its own, `validationMessage` returns
  // exactly `Check the highlighted fields and try again.`, which is the summary's own
  // title printed a second time. With anything unbound it returns the long form instead,
  // naming details no box on this page can show, and that text has nowhere else to go.
  // Suppressing the banner unconditionally loses it: a 400 whose first detail is
  // `entries[0].total_value_nzd` and whose second is `entries[0].alternative[0].qty_kg`
  // lands here (`errorStep` is the first detail with a locatable step), lists one item,
  // and would drop the second detail from the page entirely.
  const coveredBySummary = errors.items.length > 0 && errors.unbound === 0
  const bannerError = coveredBySummary
    ? null
    : isApiError
      ? state.error
      // **`errors.unbound` is the new clause on the client side, and it closes a hole
      // that predates the summary.** `leafPanel` draws an inline message for `amount`
      // and `wastedValue` only, and the `!state.errorAt` test here is false for every
      // refusal this step produces - so a `leafProblem` rule returning any other field
      // name rendered NOTHING: no inline message, no banner, and no summary item either.
      : (isClientError && (!state.errorAt || errors.unbound) ? state.error : null)
  const combined = leaves.reduce((sum, leaf) => sum + (totalKilograms(draftLeafFigures(leaf)) || 0), 0)
  const combinedText = state.totalUnit === 'tonnes' ? kgToTonnes(combined) : combined
  const heading = single ? t('How much food waste are you measuring?') : t('How much of each did you waste?')
  const intro = single
    ? t('Enter the total amount. You will allocate this total across destinations in the next step.')
    : t('Enter an amount for every food type you chose. You will allocate the combined total across destinations in the next step.')
  // **Both wrappers exist only when there is a summary to put beside the title, so a
  // step 3 with nothing wrong with it renders byte for byte what it rendered before
  // #133.** `.amount-heading-row` is the two-column grid and `.amount-heading-copy` is
  // its first cell; neither has a job without a second cell, and an empty second cell
  // beside a step title is 28px of gap with nothing in it. Emitting the inner wrapper
  // unconditionally would also put a `<div>` into every step-3 render for the sake of
  // the refused ones, which is a diff twenty browser tests walk through for no reason.
  const headingMarkup = `<p class="eyebrow">${escapeHtml(t('Step %(step)s', { step: 3 }))}</p><h1 id="amount-title">${escapeHtml(heading)}</h1>`
  const headingBlock = errorSummary
    ? `<div class="amount-heading-row"><div class="amount-heading-copy">${headingMarkup}</div>${errorSummary}</div>`
    : headingMarkup
  const content = `<div class="amount-step__content">${headingBlock}<p class="section-intro">${escapeHtml(intro)}</p>${bannerError ? `<p class="field-error api-error ${state.errorCode ? `error-${slug(state.errorCode)}` : ''}" role="alert">${escapeHtml(bannerError)}</p>` : ''}<div class="leaf-panel-list">${leaves.map((leaf, index) => leafPanel(leaf, leaves, index)).join('')}</div>${single ? '' : `<p class="combined-total" aria-live="polite"><span>${escapeHtml(t('Combined waste amount'))}</span> <strong data-combined-total>${formatNumber(combinedText, 2)} ${escapeHtml(unitLabel(state.totalUnit))}</strong></p>`}</div>`
  const navigation = single ? '' : stepFloatingNavigation(leaves.map((leaf, index) => ({ name: leafName(leaf), id: `amount-leaf-${index + 1}` })), 'amount-floating-nav')
  return `<section class="content-section ${single ? '' : 'wide'} amount-step" aria-labelledby="amount-title">${navigation}${content}${stepNav({ step: 2, back: backTarget(2) })}</section>`
}

/**
 * Every draft line's `entries[N].current[M].qty_kg` path, keyed by the line's own id.
 *
 * Three things have to agree with what `submitCalculation` actually posted, and none of
 * them follows from the render loop:
 *
 *   * **`requestLines` drops every blank row before sending**, so a row's request index
 *     is its position among the *submitted* lines of its own leaf. That is not its
 *     position in the leaf's `current` as soon as one destination is left empty - which
 *     is the normal case.
 *   * **the draft occupies a RANGE of entry indices**, one per leaf, starting after
 *     every saved chain's leaves. A path rooted at `entries[state.entries.length]` named
 *     a chain and now names whichever leaf happens to sit there.
 *   * §9's paths are rooted at the request body, so a bare `current[i].qty_kg` key can
 *     never match one.
 *
 * Keyed by line id rather than returned as a parallel array because the cells are now
 * rendered leaf by leaf and a flat index across the matrix would have to be re-derived
 * at every call site.
 */
function draftLinePaths() {
  const paths = new Map()
  const base = savedLeafCount()
  draftLeaves().forEach((leaf, leafIndex) => {
    const figures = draftLeafFigures(leaf)
    let sent = 0
    for (const line of normaliseLines(figures.current, figures.totalUnit)) {
      if (Number(line.qtyKg) > 0) paths.set(line.id, `entries[${base + leafIndex}].current[${sent++}].qty_kg`)
    }
  })
  return paths
}

/** Every path the draft's own boxes can carry a server message at. */
const draftFieldPaths = () => [...draftLinePaths().values()]

/**
 * One cell of the allocation: a destination, for one leaf.
 *
 * **The same `.destination-row` element it has always been**, with `data-leaf` so the
 * handlers know which leaf's lines they are editing. It is ordinary block flow inside its
 * own food type's card and it carries its own label, which is what the narrow layout
 * always did and what every width does since #142.
 *
 * **The `--row` / `--col` custom properties are gone with the matrix.** They were the
 * grid's placement and nothing else read them; a row keeps its position from its place in
 * the document now, which is what a list is.
 */
function destinationCell(leaf, line, paths, allocationExcess) {
  const figures = draftLeafFigures(leaf)
  const destination = selected(state.taxonomy.destinations, line.destination)
  const serverError = state.fieldErrors[paths.get(line.id)]
  const invalid = Boolean(serverError) || (line.qtyInput !== '' && Number(line.qtyInput) < 0) || (allocationExcess && state.lastChangedDestination === line.destination)
  const rowUnit = line.unit || figures.totalUnit
  const name = destination?.name || line.destination
  return `<div class="destination-row ${invalid ? 'invalid' : ''}"><label for="destination-${line.id}">${escapeHtml(name)}${destination?.description ? `<small>${escapeHtml(destination.description)}</small>` : ''}</label><div class="amount-with-unit"><input id="destination-${line.id}" data-line-field="amount" data-line-id="${line.id}" data-leaf="${leafAttr(leaf)}" type="number" inputmode="decimal" min="0" step="0.01" value="${escapeHtml(line.qtyInput)}" ${invalid ? 'aria-invalid="true"' : ''} aria-label="${escapeHtml(t('%(destination)s amount in %(unit)s', { destination: name, unit: rowUnitLabel(rowUnit) }))}"><select data-line-field="unit" data-line-id="${line.id}" data-leaf="${leafAttr(leaf)}" aria-label="${escapeHtml(t('Unit'))}">${unitOptionsHtml(rowUnit, containerPresets())}</select></div>${serverError ? `<p class="field-error" role="alert">${escapeHtml(serverError)}</p>` : ''}</div>`
}

/**
 * One leaf's Total / Allocated / Remaining, in that leaf's own unit.
 *
 * **Since #142 it is rendered OUTSIDE its card's fold**, as `collapsibleCard`'s `always`
 * strip, which is the whole of the client's "hang the remaining value on the outside".
 * Nothing about this function changed to make that true and nothing is computed twice:
 * `updateLine` already finds it by `data-summary-leaf` and rewrites Allocated, Remaining
 * and the `invalid` class in place on every keystroke, so a shut card's Remaining is live
 * and its over-allocation is visible without being opened.
 *
 * `--row` / `--col` are gone with the matrix that read them.
 */
function leafSummary(leaf, first) {
  const figures = draftLeafFigures(leaf)
  const total = totalNumber(figures)
  const allocated = allocatedAmount(figures.current, figures.totalUnit)
  const invalid = exceedsTotal(allocated, total) || (figures.current || []).some(line => line.qtyInput !== '' && Number(line.qtyInput) < 0)
  const unit = unitLabel(figures.totalUnit)
  return `<div class="allocation-summary ${invalid ? 'invalid' : ''}" ${first ? 'id="current-summary"' : ''} data-summary-leaf="${leafAttr(leaf)}" aria-live="polite"><div><span>${escapeHtml(t('Total waste'))}</span><strong>${formatNumber(total, 2)} ${escapeHtml(unit)}</strong></div><div><span>${escapeHtml(t('Allocated'))}</span><strong data-summary="allocated">${formatNumber(allocated, 2)} ${escapeHtml(unit)}</strong></div><div><span>${escapeHtml(t('Remaining'))}</span><strong data-summary="remaining">${formatNumber(remainingAmount(total, allocated), 2)} ${escapeHtml(unit)}</strong></div></div>`
}

/**
 * Step 4, which forks too (`design.md` §10).
 *
 * **Every leaf gets its own allocation, not one split divided pro-rata, and that is still
 * true.** The request body carries one `current[]` per entry, so this is the shape the
 * contract expects; a shared split would have to be derived into each entry as
 * `row_kg x leaf_kg / chain_kg`, and three-decimal `qty_kg` rounding can then leave the
 * leaves' lines not summing to the figure the visitor typed - which `improvement.js`'s
 * per-entry mass-conservation check compares. It also means a submission can say "the
 * dairy went to landfill and the bakery went to animal feed", which a shared split cannot
 * express at all. Nothing in #142 touches that: it changes how the per-leaf allocations
 * are DRAWN, not that there is one per leaf.
 *
 * **THE MATRIX IS GONE, AND IT WAS USED BEFORE IT WAS REJECTED (1 October 2026).** This
 * step drew a grid - destinations down the side, food types across the top - above 650px,
 * and per-leaf stacked blocks below it. The client used the wide one in the round-three
 * demonstration and rejected it as hard to use (#142), and asked for the vertical
 * per-card layout step 3 has. So the narrow layout became the only layout and the cards
 * fold.
 *
 * **The arithmetic that scoped the matrix to 650px is kept here because it is still
 * true**, and it is the reason nobody should reintroduce the grid as an improvement: 320px
 * less the gutters is 288px; a destination label needs about 90px; the ~198px left over is
 * 66px per column at three food types and 40px at five, and a cell that narrow cannot show
 * the number typed into it. A matrix was therefore never available at a phone width, which
 * `tests/web/test_horizontal_overflow.py` makes a hard gate by measuring document
 * `scrollWidth` against `clientWidth` at 320 and 390 in a real browser. What the client's
 * verdict added is that the grid was not worth having at the widths where it *did* fit
 * either - so the honest layout is the one that is the same everywhere, and
 * `test_leaf_layout_browser.py` now measures that it does not become a grid when there is
 * room for one.
 *
 * **All thirteen destination rows stay, inside a card.** This step lists every applicable
 * destination and the visitor fills the ones that apply, which is what it did before the
 * fork and before the fold. A "choose which destinations this chain used" pre-step would
 * shorten each card a lot and can be added later if the full list proves too heavy; the
 * fold is the cheaper answer to the same complaint and is what #142 asked for.
 *
 * **A chain with ONE food type draws a card too** (#142's own criterion), unlike step 3,
 * where one leaf renders the panel it always had. The difference is that a step-4 card
 * carries something a lone card still wants: a badge, and the Total / Allocated /
 * Remaining strip in a header position rather than loose above the list.
 * `cardIsFixedOpen` keeps it open and gives it no toggle.
 *
 * **Continue on this step is disabled before it is pressed, and that is deliberate and
 * older than #142.** `updateLine` writes the refusal into `#allocation-error` and disables
 * the button on every keystroke, because the allocation summary beside it is live and a
 * rule that only spoke when the button was pressed would contradict the running totals
 * (`test_amount_limits_browser.py::continue_from_step_four` is where that is written
 * down). So #134's decision 3 - *a refused Continue expands the offending card and focuses
 * the field at fault* - is wired through the one path this step shares with step 3
 * (`stepProblemAt` -> `openedCard` -> `focusLeafField`, which knows this step's
 * `allocation` field) and is the backstop for the routes that do press, not this step's
 * primary answer. What a visitor with a shut, wrong card actually sees is the three things
 * the folding had to leave outside the fold: the badge, the `invalid` summary strip with
 * its Remaining, and `#allocation-error` naming the food type.
 */
function destinationStep() {
  const leaves = draftLeaves()
  const single = leaves.length === 1
  const named = !single
  const paths = draftLinePaths()
  // **The same question `updateLine` asks, asked the same way.** One list of rules for
  // one button: any rule added to one of two lists left the other enabling Continue on a
  // state the other had just refused.
  const canContinue = !validateCurrentStep()
  const excess = leaf => {
    const figures = draftLeafFigures(leaf)
    return exceedsTotal(allocatedAmount(figures.current, figures.totalUnit), totalNumber(figures))
  }
  // A server `VALIDATION_ERROR` names a line by the §9 path `draftLinePaths` builds, and a
  // client-side refusal names a leaf in `state.errorAt`. Either way **a card holding a
  // message about itself is open whatever the open set says**, which is `leafPanel`'s rule
  // on step 3: a collapsed card hiding its own error is the reverse of what the folding is
  // for. Mere over-allocation is NOT this - that is a live state, it is said by the
  // summary strip outside the fold, and a card that could not be shut while a figure was
  // wrong would fight the visitor who was fixing it.
  const isApiError = state.errorCode === 'VALIDATION_ERROR'
  const card = (leaf, index) => {
    const figures = draftLeafFigures(leaf)
    const key = leafKey(leaf)
    const lines = figures.current || []
    const rows = lines.map(line => destinationCell(leaf, line, paths, excess(leaf))).join('')
    const errored = (!isApiError && state.errorAt?.leaf === key)
      || lines.some(line => state.fieldErrors[paths.get(line.id)])
    return collapsibleCard({
      step: 3,
      key,
      anchor: `destination-${index + 1}`,
      name: leafName(leaf),
      count: leaves.length,
      forceOpen: Boolean(errored),
      // The badge asks `leafProblem` through `leafSettled`, which is what Continue is
      // gated on - including the `food` argument, so that it is the same function with the
      // same arguments rather than a different one with the same name.
      status: cardStatus(leafSettled(3, leaf, named ? leafName(leaf) : null)),
      // Outside the fold, which is #142's "hang the remaining value on the outside".
      always: leafSummary(leaf, index === 0),
      body: `<div class="destination-list">${rows}</div>`,
      // `.leaf-panel` for its `margin: 0` inside the list's own gap, and
      // `data-leaf-panel` because `updateCardBadges` finds a card's badge through it on
      // both steps - the attribute means "this leaf's card on the screen in front of
      // you", and only one step is ever on screen.
      extraClass: 'leaf-panel',
      dataAttr: `id="destination-leaf-${index + 1}" data-leaf-panel="${keyAttr(key)}"`,
    })
  }
  const intro = single
    ? t('Enter an amount for every applicable destination. The combined amount cannot exceed your total waste.')
    : t('Enter an amount for every applicable destination, for each food type. No food type may have more allocated than it has.')
  // Keep the current per-food collapsible cards and their live allocation summaries.
  const content = `<div class="destination-step__content"><p class="eyebrow">${escapeHtml(t('Step %(step)s', { step: 4 }))}</p><h1 id="destination-title">${escapeHtml(t('Where did the food waste go?'))}</h1><p class="section-intro">${escapeHtml(intro)}</p>
    <div class="leaf-panel-list">${leaves.map(card).join('')}</div>
    <p class="field-error" id="allocation-error" role="alert">${escapeHtml(state.error || '')}</p></div>`
  const navigation = single ? '' : stepFloatingNavigation(leaves.map((leaf, index) => ({
    name: leafName(leaf), id: `destination-leaf-${index + 1}`,
  })), 'destination-floating-nav')
  return `<section class="content-section wide destination-step" aria-labelledby="destination-title">${navigation}${content}${stepNav({ step: 3, back: backTarget(3), disabled: !canContinue })}</section>`
}

/** One leaf's destination rows, as the visitor typed them and as they reach the wire. */
function reviewLines(figures) {
  const lines = normaliseLines(figures.current, figures.totalUnit).filter(line => Number(line.qtyKg) > 0)
  if (!lines.length) return ''
  return `<dl class="review-destinations">${lines.map(line => {
    const destination = selected(state.taxonomy.destinations, line.destination)
    // Item (6): the unit this row was typed in, not the entry's - the kilograms beside it
    // are what actually reaches the wire, and both are shown so the conversion stays
    // checkable at the one moment a visitor can still compare the two.
    return `<div><dt>${escapeHtml(destination?.name || line.destination)}</dt><dd>${formatNumber(line.qtyInput, 2)} ${escapeHtml(rowUnitLabel(line.unit || figures.totalUnit))} <small>(${formatNumber(line.qtyKg, 3)} kg)</small></dd></div>`
  }).join('')}</dl>`
}

/**
 * Every leaf of one chain, for one of the review step's two blocks.
 *
 * **Two blocks, not one, and that is not cosmetic**: each review section carries its own
 * *Edit* link pointing at the step that answers it (`reviewEdit`), and amounts are
 * answered on step 3 while allocations are answered on step 4. Folding them together
 * would leave one of those two steps unreachable from the review screen.
 */
function reviewLeafBlocks(chain, kind) {
  return entryLeaves(chain).map(leaf => {
    const figures = leafFigures(chain, leaf)
    const body = kind === 'destinations'
      ? reviewLines(figures)
      : `<p><strong>${measuredAs(figures)}</strong> &middot; ${formatNumber(totalKilograms(figures), 3)} kg</p>`
    return `<div class="review-leaf"><h3>${escapeHtml(leafName(leaf))}</h3>${body}</div>`
  }).join('')
}

/** A chain's combined mass, in kilograms - the sum of its leaves. */
const chainKilograms = chain => entryLeaves(chain).reduce((sum, leaf) => sum + (totalKilograms(leafFigures(chain, leaf)) || 0), 0)

/**
 * What step 3 was told, in the words it was told in - **already escaped**, so no call
 * site may escape it again.
 *
 * A container entry says how many of which container, because "139.200 kg" is not what
 * the visitor entered and is not what they can check. The kilograms are printed beside
 * it, never instead of it. `preset.label` is staff-typed and published as written
 * (§7.7.7), so it is escaped and never translated.
 *
 * **It takes a LEAF's figures**, not a chain: `measureMode`, `unitPreset`, `unitCount`,
 * `totalAmount` and `totalUnit` are all per leaf now.
 */
function measuredAs(figures) {
  if (figures.measureMode !== 'container') {
    return `${formatNumber(figures.totalAmount, 2)} ${escapeHtml(unitLabel(figures.totalUnit))}`
  }
  const preset = selected(presetList(), figures.unitPreset)
  return `${formatNumber(figures.unitCount, 2)} &times; ${escapeHtml(preset?.label || figures.unitPreset || '')}`
}

/**
 * The message against one whole **chain**, or `''`.
 *
 * §9's `entries[N]` names a LEAF, and the review step shows chain cards, so the lookup
 * goes through `submissionLeafMap` rather than indexing `state.entries` directly. A
 * banner naming "entry 2" must land on the card the visitor counts as entry 2.
 */
function entryProblem(chainIndex) {
  const errors = state.fieldErrors || {}
  const hit = submissionLeafMap().find(item => item.chainIndex === chainIndex && errors[`entries[${item.index}]`])
  return hit ? errors[`entries[${hit.index}]`] : ''
}

function entryProblemHtml(index) {
  const problem = entryProblem(index)
  return problem ? `<p class="field-error entry-problem" role="alert">${escapeHtml(problem)}</p>` : ''
}

/**
 * One saved chain's card.
 *
 * **One card per chain, not one per leaf**, and the Edit and Remove buttons decide it:
 * the visitor built one chain and can only meaningfully edit or remove one chain.
 * Removing a leaf is unticking a category on step 2, which is what Edit is for. So the
 * card grows a leaf list instead.
 */
function entryCard(entry, index) {
  const sector = selected(state.taxonomy.sectors, entry.sector)
  const leaves = entryLeaves(entry)
  const single = leaves.length === 1
  const label = leaf => {
    const figures = leafFigures(entry, leaf)
    return `<li><span>${escapeHtml(leafName(leaf))}</span><span>${measuredAs(figures)}</span></li>`
  }
  const summary = single
    ? `<p>${measuredAs(leafFigures(entry, leaves[0]))} &middot; ${escapeHtml(leafName(leaves[0]))}</p>`
    : `<p>${escapeHtml(t('%(count)s food types', { count: leaves.length }))} &middot; ${escapeHtml(t('%(amount)s combined', { amount: `${formatNumber(chainKilograms(entry), 2)} kg` }))}</p><ul class="entry-card__leaves">${leaves.map(label).join('')}</ul>`
  return `<article class="saved-entry-card ${entryProblem(index) ? 'has-error' : ''}"><div><span class="eyebrow">${escapeHtml(t('Entry %(number)s', { number: index + 1 }))}</span><h3>${escapeHtml(sector?.name || entry.sector)}</h3>${summary}${entryProblemHtml(index)}</div><div class="card-actions"><button class="text-button" type="button" data-action="edit-entry" data-index="${index}">${escapeHtml(t('Edit'))}<span class="sr-only"> ${escapeHtml(t('entry %(number)s', { number: index + 1 }))}</span></button><button class="text-button danger" type="button" data-action="remove-entry" data-index="${index}">${escapeHtml(t('Remove'))}<span class="sr-only"> ${escapeHtml(t('entry %(number)s', { number: index + 1 }))}</span></button></div></article>`
}

/**
 * One review-step *Edit* link, and the only place the four are defined.
 *
 * **`data-jump="review"` is what makes it a jump rather than a step backwards**, and
 * the attribute exists because the two are otherwise indistinguishable: the review
 * step's own Back emits `data-action="go-step" data-step="3"` and so does the
 * *Waste destinations / Edit* link beside it. A rule of the form "any `go-step` leaving
 * the review step for a lower step is a jump" would therefore make review's own Back
 * write a marker, and the destination step's Back would then bounce straight back to
 * review — the wizard could never be walked out of. `goToStep` reads the attribute
 * instead of guessing, and the four links are emitted from here so a fifth section
 * added to the review step cannot be added without it.
 *
 * `data-action` stays `go-step`: it is the same navigation, and every existing selector
 * (`tests/web/test_step_navigation.py`, `test_step_one_back_navigation_browser.py`) is
 * written against it.
 *
 * @param {number} step Zero-based index of the screen this section is answered on.
 * @returns {string}
 */
const reviewEdit = step => `<button class="text-button" type="button" data-action="go-step" data-step="${step}" data-jump="review">${escapeHtml(t('Edit'))}</button>`

/**
 * "One entry, three food types" - said in the shape the numbers actually are.
 *
 * **The single sentence was ungrammatical in the commonest forked case.** One chain of
 * three categories read "These 1 entries will be calculated as 3 food-type entries.",
 * and one chain is exactly what a visitor who ticks three boxes and never presses *Add
 * another supply-chain entry* has. `t()` has no plural machinery - `i18n.js`'s catalogue
 * is a flat string map - so the two shapes are two keys, chosen here, which is also what
 * gives a translator two separate sentences to get right rather than one to compromise
 * on. `leaves` is at least two wherever this is shown at all (the caller only prints it
 * when `leafTotal > chains`), so there is no third shape to write.
 */
const leafCountNote = (chains, leaves) => (chains === 1
  ? t('This entry will be calculated as %(leaves)s food-type entries.', { leaves })
  : t('These %(chains)s entries will be calculated as %(leaves)s food-type entries.', { chains, leaves }))

/**
 * Every reason the review step's Calculate is off that has nothing to do with the
 * reporting period.
 *
 * Pulled out so that `period.js`'s typing fast path can ask the identical question
 * this render path just asked. That path patches the button by hand rather than
 * re-rendering (§7.3a's documented exception — a `setState` per keystroke destroys
 * the caret), and a fast path that knew only its own half of the condition would
 * re-enable Calculate on a corrected date while a request was still in flight.
 */
const calculateOtherwiseDisabled = () => state.loading || Date.now() < state.rateLimitedUntil || blocked()

function reviewStep() {
  const sector = selected(state.taxonomy.sectors, state.sector)
  const draft = draftChain()
  const leaves = entryLeaves(draft)
  const chains = state.entries.length + 1
  const leafTotal = submissionLeafCount()
  // §7.6.2: the placeholder wording is conditional on `is_mock`, never unconditional. It was
  // hard-coded here, which is correct only while every factor set is mock and inverts the day
  // a real one is published - a screen that tells a user their verified factors have not been
  // supplied. §6.1 puts `factor_set` on the taxonomy response, which is what this step has.
  const mock = state.taxonomy?.factor_set?.is_mock
  const estimateNotice = mock
    ? t('Demonstration only — verified calculation factors have not yet been supplied. Final results will depend on factors supplied and approved by Kai Commitment.')
    : t('Results are estimates, produced from the calculation factors supplied and approved by Kai Commitment.')
  const foodNames = leaves.map(leafName).join(t(', '))
  // The same rule step 2's checkboxes are disabled by, asked about the other door into a
  // twenty-first leaf. Without it a visitor could press Add at the ceiling, walk four
  // screens, and meet the ceiling as a 400 the form had every means to prevent.
  const noRoomForAnother = addEntryRefused()
  return `<section class="content-section wide" aria-labelledby="review-title"><p class="eyebrow">${escapeHtml(t('Step %(step)s', { step: 5 }))}</p><h1 id="review-title">${escapeHtml(t('Review your information'))}</h1><p class="section-intro">${escapeHtml(t('Check this entry, or add another supply-chain entry before viewing the combined results.'))}</p>
    <div class="form-field time-frame-field"><label for="time-frame">${escapeHtml(t('What period do these figures cover?'))} <span class="optional-tag">${escapeHtml(t('(optional)'))}</span></label><p class="field-hint">${escapeHtml(t('Optional — it only labels your figures, it never changes a result.'))}</p><select id="time-frame"><option value="" ${!state.timeFrame ? 'selected' : ''}>${escapeHtml(t('Not stated'))}</option><option value="one_week" ${state.timeFrame === 'one_week' ? 'selected' : ''}>${escapeHtml(t('One week'))}</option><option value="one_month" ${state.timeFrame === 'one_month' ? 'selected' : ''}>${escapeHtml(t('One month'))}</option><option value="one_quarter" ${state.timeFrame === 'one_quarter' ? 'selected' : ''}>${escapeHtml(t('One quarter'))}</option><option value="one_year" ${state.timeFrame === 'one_year' ? 'selected' : ''}>${escapeHtml(t('One year'))}</option><option value="custom" ${state.timeFrame === 'custom' ? 'selected' : ''}>${escapeHtml(t('Custom period'))}</option></select>${PeriodField()}</div>
    ${state.entries.length ? `<section class="saved-entries"><div class="section-heading-row"><h2>${escapeHtml(t('Added entries'))}</h2><span>${state.entries.length}</span></div>${state.entries.map(entryCard).join('')}</section>` : ''}
    <div class="section-heading-row current-entry-heading ${entryProblem(state.entries.length) ? 'has-error' : ''}"><h2>${escapeHtml(t('Current entry %(number)s', { number: state.entries.length + 1 }))}</h2><span>${escapeHtml(t('Ready to calculate'))}</span></div>${entryProblemHtml(state.entries.length)}
    <article class="review-block"><div class="section-heading-row"><h2>${escapeHtml(t('Supply-chain stage'))}</h2>${reviewEdit(0)}</div><p>${escapeHtml(sector?.name || state.sector)}</p></article>
    <article class="review-block"><div class="section-heading-row"><h2>${escapeHtml(t('Food categories'))}</h2>${reviewEdit(1)}</div><p>${escapeHtml(foodNames)}</p></article>
    <article class="review-block"><div class="section-heading-row"><h2>${escapeHtml(t('Waste amount'))}</h2>${reviewEdit(2)}</div>${reviewLeafBlocks(draft, 'amounts')}</article>
    <article class="review-block"><div class="section-heading-row"><h2>${escapeHtml(t('Waste destinations'))}</h2>${reviewEdit(3)}</div>${reviewLeafBlocks(draft, 'destinations')}</article>
    <button class="button button-add add-entry-button" type="button" data-action="add-entry" ${noRoomForAnother ? 'disabled' : ''}>+ ${escapeHtml(t('Add another supply-chain entry'))}</button>
    ${noRoomForAnother ? `<p class="field-hint choice-ceiling" role="status">${escapeHtml(t('You can enter at most %(limit)s food types in one calculation. Untick one, or calculate what you have.', { limit: MAX_LEAVES }))}</p>` : ''}
    ${leafTotal > chains ? `<p class="leaf-count-note">${escapeHtml(leafCountNote(chains, leafTotal))}</p>` : ''}
    <aside class="disclaimer compact" aria-label="${escapeHtml(t('Important information'))}"><span class="info-icon" aria-hidden="true">i</span><div><strong>${escapeHtml(t('Estimate notice'))}</strong><p>${escapeHtml(estimateNotice)}</p></div></aside>
    ${state.error ? `<p class="field-error api-error ${state.errorCode ? `error-${slug(state.errorCode)}` : ''}" role="alert">${escapeHtml(state.error)}</p>` : ''}${stepNav({
      step: 4,
      back: 3,
      label: state.loading ? t('Calculating…') : Date.now() < state.rateLimitedUntil ? t('Try again shortly') : state.entries.length ? t('Calculate results for %(count)s entries', { count: chains }) : t('Calculate impact'),
      disabled: calculateOtherwiseDisabled() || Boolean(periodProblem().message),
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
function amountOnlyValidation(figures, food) {
  if (figures.measureMode === 'container') {
    // **The two-decimal rule applies to the count, which is what the visitor typed.** It
    // is an input rule about typing, not a property of the total: `toKg` returns three
    // decimals because that is what §6.2 accepts, and a total of "139.200" is not a
    // number anybody entered. §7.2 recorded this collision as the decision building this
    // input would require; this is the decision.
    if (!figures.unitCount || Number(figures.unitCount) <= 0) return t('Number of containers must be greater than zero.')
    // `1e5` is a value a number input hands over, it is not written as a decimal, and it
    // has no decimal places — so the two-decimal message was the wrong sentence for it.
    // Ask the two questions separately and each answer is true of what was typed.
    if (!isPlainDecimal(figures.unitCount)) return t('Write the number out in full, using digits only.')
    if (!decimalPattern.test(figures.unitCount)) return t('Enter no more than two decimal places.')
    // A preset whose code the taxonomy no longer holds, or whose `kg_per_unit` will not
    // parse, leaves `containerKg` at '' — and a step that continued on that would carry a
    // zero total into step 4 and refuse every allocation with a message about the
    // allocation. Say what is actually wrong instead.
    //
    // **Ahead of the count bound, because that bound is derived from the same missing
    // number.** `countLimit` answers 0 when there is no usable conversion, so asking the
    // bound first would refuse a perfectly ordinary count with "Enter no more than 0
    // containers." — a sentence about the visitor's typing for a fault in the taxonomy.
    if (!figures.unitPreset || containerTotal(figures) === '') return t('That container is no longer available. Choose another.')
    if (Number(figures.unitCount) > containerLimit(figures)) return t('Enter no more than %(limit)s containers.', { limit: formatNumber(containerLimit(figures), 0) })
    return ''
  }
  // **The food is named only when there is more than one.** A chain with one leaf keeps
  // the sentence it has always had; with three, "Waste amount must be greater than zero."
  // is true of all three and says nothing about which.
  if (!figures.totalAmount) return food ? t('Enter a waste amount for %(food)s.', { food }) : t('Waste amount must be greater than zero.')
  if (Number(figures.totalAmount) <= 0) return food ? t('Waste amount for %(food)s must be greater than zero.', { food }) : t('Waste amount must be greater than zero.')
  if (!isPlainDecimal(figures.totalAmount)) return t('Write the number out in full, using digits only.')
  if (!decimalPattern.test(figures.totalAmount)) return t('Enter no more than two decimal places.')
  // **`null` is over the ceiling, not under it.** `massToKg` answers `null` when there
  // is no finite mass, and `null > MAX` is `false` — so a bare comparison lets the one
  // case the whole guard exists for straight through. It is reachable: 308 nines is the
  // longest run a number input keeps (309 is outside a double's range and the browser
  // blanks it), it is a plain decimal, it passes the two-decimal rule, and *as tonnes*
  // it is `Infinity` kilograms.
  const kilograms = totalKilograms(figures)
  if (kilograms === null || kilograms > MAX_SCENARIO_KG) {
    return t('Enter no more than %(limit)s %(unit)s.', { limit: formatNumber(limitIn(MAX_SCENARIO_KG, figures.totalUnit), 0), unit: unitLabel(figures.totalUnit) })
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
function moneyContradictionValidation(figures) {
  if (figures.totalValueNzd === '' || figures.wastedValueNzd === '') return ''
  const totalCents = moneyCents(figures.totalValueNzd)
  const wastedCents = moneyCents(figures.wastedValueNzd)
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
 *
 * **Decision, recorded rather than acted on:** refusing "produced 0, wasted more than
 * 0" (a legitimate case of this same contradiction — `exceedsTotal(wasteKg, 0)` is true
 * for any positive waste) makes the server's `production_share_percent: "undefined"`
 * state (§4.6, added at v1.51 for exactly a stated zero production total) unreachable
 * from this form, while its copy still ships on the results page, the text export and
 * the PDF. The refusal stays — a zero production total alongside non-zero waste is a
 * genuine contradiction and this check exists to catch exactly that shape of it — but
 * that `undefined` state is not thereby dead: it remains reachable for a submission
 * made before this change (nothing here touches a stored `submission` row) and for a
 * caller that reaches the API directly, bypassing this client-side check entirely
 * (§7.6.1 — this form calculates nothing and refuses nothing the server itself relies
 * on). So it is a defensive state, not dead copy, and this comment is that record —
 * not a contract change: `docs/interfaces.md` is unaffected and untouched.
 */
function massContradictionValidation(figures) {
  if (figures.totalInputKg === '') return ''
  const producedKg = massToKg(figures.totalInputKg, figures.totalUnit)
  const wasteKg = totalKilograms(figures)
  if (producedKg === null || wasteKg === null || !exceedsTotal(wasteKg, producedKg)) return ''
  return t('Waste amount exceeds total amount produced by %(excess)s %(unit)s.', { excess: formatNumber(limitIn(wasteKg - producedKg, figures.totalUnit), 2), unit: unitLabel(figures.totalUnit) })
}

/**
 * **What is wrong with ONE leaf on one step, or `''` — and the only copy of these
 * rules.**
 *
 * Two readers ask it. `stepProblemAt` below walks the leaves and hands back the first
 * problem, which is what Continue is gated on; `leafSettled` beside it asks about one
 * leaf, which is what a collapsible card's completion badge prints (#134). The badge and
 * the button therefore ask the same question, in the same words, of the same figures.
 *
 * **It is one function rather than two lists because this file has already paid for
 * two.** `destinationStep` carries the note: *"One list of rules for one button: any
 * rule added to one of two lists left the other enabling Continue on a state the other
 * had just refused."* A badge computed from a second list is that same defect with a
 * tick instead of a button, and it is the worse half of it — a button that refuses says
 * so at the moment of pressing, whereas a tick is read as a promise before anybody
 * presses anything, and the client's stated use for it is deciding whether to open a
 * card at all.
 *
 * **What a tick therefore means: every required field on this card is filled, and
 * nothing on it is at fault.** The two optional money figures cannot make a card
 * incomplete by being empty — `moneyContradictionValidation` returns `''` the moment
 * either is blank — so the badge never demands them. What it does report is a
 * *contradiction* between two figures the visitor did fill in, which is a fault rather
 * than an absence and which Continue refuses; a tick over that state would be a tick
 * over a card the next press rejects.
 *
 * Returns the field the fault belongs to, never the prose: two leaves produce the
 * byte-identical sentence, so `amountStep` highlights by place (`state.errorAt`) and
 * `focusLeafField` moves focus by place.
 *
 * @param {number} step 2 for the amount step, 3 for the destination step. Any other
 *   step has no per-leaf rules and answers `''`.
 * @param {object} leaf
 * @param {string|null} food The leaf's own name where there is more than one leaf, and
 *   `null` where there is one — which is what decides whether a message names a food.
 * @returns {{message: string, field?: string}}
 */
function leafProblem(step, leaf, food) {
  const figures = draftLeafFigures(leaf)
  if (step === 2) {
    // **Today's three rules.** The first problem wins, and it carries the field it
    // belongs to so that `amountStep` can highlight the right box without comparing
    // prose.
    const amountError = amountOnlyValidation(figures, food)
    if (amountError) return { message: amountError, field: 'amount' }
    const moneyError = moneyContradictionValidation(figures)
    if (moneyError) return { message: moneyError, field: 'wastedValue' }
    const massError = massContradictionValidation(figures)
    if (massError) return { message: massError, field: 'amount' }
  }
  if (step === 3) {
    // **Per leaf: this leaf's allocation sums to no more than this leaf's OWN amount.**
    // Validating against the chain's combined total instead would let one leaf take
    // another's mass and still pass, and the API would then refuse the submission for a
    // mass-conservation failure the form had already been shown.
    const unit = figures.totalUnit
    const lines = figures.current || []
    const fail = message => ({ message, field: 'allocation' })
    if (!lines.some(line => Number(line.qtyInput) > 0)) {
      return fail(food
        ? t('Enter an amount for at least one waste destination for %(food)s.', { food })
        : t('Enter an amount for at least one waste destination.'))
    }
    if (lines.some(line => line.qtyInput !== '' && Number(line.qtyInput) < 0)) return fail(t('Destination amounts must be zero or greater.'))
    if (lines.some(line => line.qtyInput && !isPlainDecimal(line.qtyInput))) return fail(t('Write the number out in full, using digits only.'))
    if (lines.some(line => line.qtyInput && !decimalPattern.test(line.qtyInput))) return fail(t('Enter destination amounts to no more than two decimal places.'))
    // §6.2's per-line bound, restated. Since v1.46 it is the same number as the step-3
    // ceiling, so a visitor who puts all of a legal total into one destination is
    // refused by neither. The finiteness check is asked through the same `null`, where
    // the true answer is that the row is over the limit and the message says which.
    const overLine = lines.some(line => {
      if (line.qtyInput === '') return false
      const kilograms = lineKilograms(line.qtyInput, line.unit || unit)
      return kilograms === null || kilograms > MAX_LINE_KG
    })
    if (overLine) return fail(t('Enter destination amounts of no more than %(limit)s %(unit)s.', { limit: formatNumber(limitIn(MAX_LINE_KG, unit), 0), unit: unitLabel(unit) }))
    const total = totalNumber(figures)
    const sum = allocatedAmount(lines, unit)
    if (exceedsTotal(sum, total)) {
      const excess = (sum - total).toFixed(2)
      const unitName = unit === 'kilograms' ? 'kg' : t('tonnes')
      return fail(food
        ? t('Allocated waste for %(food)s exceeds its amount by %(excess)s %(unit)s.', { food, excess, unit: unitName })
        : t('Allocated waste exceeds total waste by %(excess)s %(unit)s.', { excess, unit: unitName }))
    }
  }
  return { message: '' }
}

/**
 * **The badge's reader of `leafProblem`, and the predicate #133's notice must share.**
 *
 * `true` when nothing on this leaf's card is at fault — see `leafProblem` for exactly
 * what that promises and why the optional money figures cannot withhold it.
 *
 * It takes the step so that steps 3 and 4 get the same chrome from the same rules, and
 * it takes `food` for one reason only: `leafProblem`'s messages name the food when there
 * is more than one leaf, and a predicate that passed a different `food` than Continue
 * does would be reading a different function with the same name.
 */
const leafSettled = (step, leaf, food) => !leafProblem(step, leaf, food).message

/**
 * What is wrong with the draft as far as one step's own rules are concerned, or `''`.
 *
 * **Parameterised on the step rather than reading `state.step`, so that one other
 * caller can ask about a step the visitor is not standing on.** `draftIsComplete`
 * (below) asks all three of them at once, from step 1's duplicate notice as readily as
 * from the review step, to decide whether the draft is something `state.entries` is
 * allowed to hold. Writing that predicate out by hand instead would be a second copy of
 * this list, and the note over `EMPTY_DRAFT` records what a second copy of a key list
 * costs: the one that drifts is the one nobody notices has drifted.
 *
 * None of the four checks `leafProblem` delegates to reads `state.step` —
 * `amountOnlyValidation` reads the measure mode and the amount fields, the two
 * contradiction checks read their own pairs, and the step-3 block reads the leaf's own
 * `current` — so asking about a step from another step answers about the draft, which is
 * the question.
 *
 * **The leaf key is attached here rather than inside `leafProblem`** because it is the
 * answer to "which card", and a per-card reader already knows which card it asked
 * about. `leafErrorAt` is what turns it into `state.errorAt`.
 */
function stepProblemAt(step) {
  if (step === 0 && !state.sector) return { message: t('Select where in the food supply chain the waste occurred.') }
  const leaves = draftLeaves()
  const named = leaves.length > 1
  for (const leaf of leaves) {
    const problem = leafProblem(step, leaf, named ? leafName(leaf) : null)
    if (problem.message) return { ...problem, leaf: leafKey(leaf) }
  }
  return { message: '' }
}

/**
 * What is wrong with the draft as far as one step's own rules are concerned, or `''`.
 *
 * **Parameterised on the step rather than reading `state.step`, so that one other caller
 * can ask about a step the visitor is not standing on.** `draftIsComplete` asks all three
 * at once, from step 1's duplicate notice as readily as from the review step.
 */
const stepProblem = step => stepProblemAt(step).message

/** The same question about the screen the visitor is actually on. */
const validateCurrentStep = () => stepProblem(state.step)

/**
 * The steps that stand between a new chain and the review step, and so the whole of
 * what "complete" means for an entry. Step 2 (food category) is absent because it is
 * optional — `foodStep` is headed so, and a chain that names no food is the leaf
 * `leafDisplayName` calls "Not broken down by type" wherever it is shown.
 */
const GATED_STEPS = [0, 2, 3]

/**
 * Whether the draft, right now, is something `state.entries` may hold.
 *
 * **The saved list is only ever allowed to contain complete entries**, and every
 * consumer of it is written on that promise: `entryCard` prints a sector, an amount and
 * a category for every row, `reviewStep` offers Calculate over all of them, and
 * `submissionPayload` posts every row for the API — which refuses an entry with no
 * sector or no destination lines outright. A row that fails here could only ever be
 * repaired through the same button that created it.
 *
 * Note the return values are being used as a predicate, not as copy: `stepProblem(3)`
 * asked of a draft standing on step 1 produces a real sentence about destinations that
 * nothing shows to anybody.
 */
const draftIsComplete = () => GATED_STEPS.every(step => !stepProblem(step))

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
const clearedError = () => (blocked() ? {} : { error: null, errorAt: null, errorCode: null })

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

// **The three round-two scalar fields, the step whose markup owns each, and the name
// that markup calls it.** `entries[N].total_input_kg` (and its two money neighbours) are
// answered on step 2 (`amountStep`) — a fact about that function's HTML, not something
// derivable from the field name itself — and the box it is answered in is
// `data-leaf-field="totalInput"`, which is equally a fact about that HTML. One map, in
// one place, rather than a per-call-site guess; `entryDestinations`'s and
// `draftFieldPaths`'s own paths cover the one other kind of field this form has a box
// for, `entries[N].current[M].qty_kg`, and that one is derived from `state.current`
// because there is one row per destination and the row is what the path counts.
//
// **`field` is new (v1.87) and it is here rather than beside its one reader** because
// the request path and the control's own name are the same correspondence stated twice
// the moment they live apart: `amountErrorItems` had written the triple out a fourth
// time to get at it, alongside this map, `scalarFieldPaths()` and `leafPanel`'s three
// `scalarError(...)` calls. It was `ENTRY_SCALAR_FIELD_STEP` while the step was all it
// carried.
const ENTRY_SCALAR_FIELDS = {
  total_input_kg: { step: 2, field: 'totalInput' },
  total_value_nzd: { step: 2, field: 'totalValue' },
  wasted_value_nzd: { step: 2, field: 'wastedValue' },
}

/**
 * **The submission's leaves, in request order, each knowing which chain it came from.**
 *
 * §9's `entries[N]` paths are rooted at the request body, and the request body carries
 * leaves - while the review step shows chain cards and the visitor counts chains. Every
 * router below goes through this one map rather than doing arithmetic on chain indices,
 * which is what `index <= state.entries.length` was and what made it wrong the moment a
 * chain could contribute more than one entry.
 *
 * Built from the same `entryLeaves` rule the request body is built from, so the two
 * cannot disagree about which index is which.
 */
function submissionLeafMap() {
  const chains = [...state.entries, draftChain()]
  const out = []
  chains.forEach((chain, chainIndex) => {
    for (const leaf of entryLeaves(chain)) out.push({ index: out.length, chainIndex, chain, leaf })
  })
  return out
}

/** The request indices the draft occupies - a range now, not a single number. */
const draftLeafIndices = () => {
  const base = savedLeafCount()
  return draftLeaves().map((leaf, index) => base + index)
}

// The exact `entries[N].<key>` paths the scalar map above answers to - always the
// *draft's*, because a saved chain's fields sit on a read-only `entryCard` with no box to
// highlight. **One path per draft LEAF per key**, because the draft now occupies a range
// of request indices. Shared by `detailStep`, which routes a rejected visitor, and
// `validationMessage`, which must not also describe in the banner a field already
// highlighted at its own input.
const scalarFieldPaths = () => draftLeafIndices().flatMap(index => Object.keys(ENTRY_SCALAR_FIELDS).map(key => `entries[${index}].${key}`))

/**
 * **The §9 `field` paths this screen has a box for**, as one set.
 *
 * `validationMessage` asks it of the response's `details[]` to decide what the banner
 * still owes, and `unboundFieldErrors` asks it of `state.fieldErrors` to decide the same
 * thing at render time, one step later. Two readers of one definition: a second spelling
 * of "bound" would let the banner and the field index disagree about whether a detail had
 * been shown anywhere, which is precisely the state in which text goes missing.
 */
const boundFieldPaths = () => new Set([...draftFieldPaths().filter(Boolean), ...scalarFieldPaths()])

/**
 * The live `VALIDATION_ERROR`'s fields that no box on this form can display.
 *
 * Read off `state.fieldErrors`, whose keys are `details[].field` verbatim
 * (`fieldErrorMap`), because `details[]` itself is not kept on `state` — only the copy
 * made at the moment of the response. A detail with no `field` keys as `"undefined"`,
 * which is in no bound set and is therefore counted, which is the right answer: it has
 * no box either.
 *
 * `amountStep` is the caller. It is what keeps the banner on screen for a 400 that names
 * both a field this step draws and one it does not — the summary beside the title can
 * only ever index the first kind.
 */
const unboundFieldErrors = () => {
  const bound = boundFieldPaths()
  return Object.keys(state.fieldErrors || {}).filter(field => !bound.has(field))
}

/**
 * The step that owns one API validation detail, or `undefined` when this form has no
 * field to point at - a saved entry, `alternative`, anything §9 might name that never
 * reaches an `<input>` on this page.
 */
function detailStep(detail) {
  const field = detail.field || ''
  if (scalarFieldPaths().includes(field)) {
    const key = /\.(\w+)$/.exec(field)?.[1]
    if (key && ENTRY_SCALAR_FIELDS[key] !== undefined) return ENTRY_SCALAR_FIELDS[key].step
  }
  // A detail naming a *saved* entry has no input to highlight, which is why this
  // function used to answer `undefined` for one and let `submitCalculation` fall
  // through to its step-3 default. That default put a visitor refused for a
  // duplicate entry on the destination step, whose fields were all valid, with
  // nothing on screen belonging to the entry the API had named. The saved entry
  // IS on screen - as its card on the review step - so send them there, and let
  // `entryCard` carry the message against the card itself.
  if (entryIndexOf(detail) !== undefined) return 4
  return draftFieldPaths().includes(field) ? 3 : undefined
}

/**
 * The index of the **chain** a detail names as a whole, or `undefined`.
 *
 * §9's `entries[N]` is a LEAF index; the cards on the review step are chains. This is
 * the translation, and it is the whole reason `submissionLeafMap` exists: the old
 * `index <= state.entries.length` was arithmetic on chain indices and silently named the
 * wrong card the moment one chain carried two leaves.
 *
 * **Anchored at both ends, and that is the point.** `entries[1].qty_kg` names a field
 * *inside* an entry and belongs to whichever step renders that field; a bare `entries[1]`
 * names the entry itself. The draft is deliberately included: `duplicate_entry` flags the
 * *later* of the two colliding entries, and with one saved chain plus a draft that later
 * one IS the draft.
 */
function entryIndexOf(detail) {
  const index = Number(/^entries\[(\d+)\]$/.exec(detail.field || '')?.[1])
  if (!Number.isInteger(index)) return undefined
  return submissionLeafMap()[index]?.chainIndex
}

/**
 * The entry a duplicate repeats, as the visitor's own 1-based **chain** number, plus the
 * food that collides.
 *
 * Recomputed here rather than read out of the API's prose. §9's `message` for
 * `duplicate_entry` names the other entry as `entries[0]` inside an English sentence, and
 * parsing an index back out of that would tie this screen to that wording. The front end
 * holds every entry, so it can find the match itself, on the same key `api/schemas.py`
 * keys on - one dimension deeper now that an entry is a leaf.
 *
 * **The API's key is a triple since v1.58** - `(sector, food_category, food_item)` - and
 * this is a pair, which is correct here and will not stay correct. Nothing on this form
 * can name a food yet: step 2.5 is a later landing, so every leaf this function sees
 * carries a food of `null` and a pair and a triple agree on all of them. The day the form
 * can name one, this key has to gain it or the screen will warn about a duplicate the API
 * accepts - `dairy/cheese` beside `dairy/butter`, which is the pair step 2.5 exists to
 * produce.
 */
function duplicateOf(leafIndex) {
  const map = submissionLeafMap()
  const item = map[leafIndex]
  if (!item) return undefined
  const key = other => savedLeafKey(other.chain.sector, other.leaf)
  const first = map.findIndex(other => key(other) === key(item))
  if (first === -1 || first === leafIndex) return undefined
  return { number: map[first].chainIndex + 1, food: leafName(item.leaf) }
}

/**
 * The banner text for a 400.
 *
 * `destinationRows` shows every detail that names a row of the entry on screen against
 * that row, and `amountStep` now does the same for the three scalar fields above, so the
 * banner only has to point at what is left. A detail that names anything else — a saved
 * entry, an `alternative`, a field this form has no input for — has no box to attach to
 * and would otherwise vanish entirely, so it is spelled out here instead.
 *
 * **Which makes the short form, and only the short form, safe to suppress on screen.**
 * `amountStep`'s field index beside the step title says the same sentence, so printing
 * both is printing it twice; it asks `unboundFieldErrors` — the same `boundFieldPaths`
 * set this function uses, over the copy of the details held on `state` — so the two
 * cannot disagree about which branch was taken.
 */
function validationMessage(error) {
  const bound = boundFieldPaths()
  const unbound = (error.details || []).filter(detail => !bound.has(detail.field))
  if (!unbound.length) return t('Check the highlighted fields and try again.')
  // The envelope's `message` is the API's own English and never varies ("Request
  // validation failed"), so it printed one untranslated sentence in front of
  // details that are themselves translated. `publicError` gives every other error
  // code a `t()` string; VALIDATION_ERROR was the one that leaked.
  return [t('The calculation could not be completed.'), ...unbound.map(describeDetail)].join(' ')
}

// §9's path is rooted at the request body and starts `entries[N]`, where N is the
// submission-order index. The user counts entries from one.
function describeDetail(detail) {
  const entry = /^entries\[(\d+)\]/.exec(detail.field || '')
  // An `issue` this screen understands is phrased here, in the visitor's own
  // language and in their own terms. Falling through to `detail.message` prints
  // the API's English verbatim, which is how a visitor reading the Chinese
  // interface came to be shown "记录 2：has the same sector and food category as
  // entries[0]" - half translated, and naming a path into the request body.
  const known = detail.issue === 'duplicate_entry' ? duplicateEntryMessage(entry) : undefined
  if (known) return known
  const message = detail.message || t('This value could not be accepted.')
  if (!entry) return message
  // **§9's N is a leaf; the visitor counts chains.** Naming the leaf number would name a
  // card that does not exist. The food is named beside the chain number because with a
  // forked chain the chain number alone does not say which of its rows was refused.
  const item = submissionLeafMap()[Number(entry[1])]
  if (!item) return message
  return t('Entry %(number)s (%(food)s): %(message)s', { number: item.chainIndex + 1, food: leafName(item.leaf), message })
}

// Both wordings exist because `duplicateOf` can legitimately come back empty: the
// API refused the pair, so something matched, but an entry edited between the
// request and the response would leave nothing to point at. Naming a wrong entry
// number is worse than naming none.
function duplicateEntryMessage(entry) {
  if (!entry) return undefined
  const leafIndex = Number(entry[1])
  const item = submissionLeafMap()[leafIndex]
  if (!item) return undefined
  const number = item.chainIndex + 1
  const food = leafName(item.leaf)
  const other = duplicateOf(leafIndex)
  return other === undefined
    ? t('Entry %(number)s repeats %(food)s at a supply-chain stage you have already entered. Combine the two, or change one of them.', { number, food })
    : t('Entry %(number)s has %(food)s at the same supply-chain stage as entry %(other)s. Combine the two, or change one of them.', { number, food, other: other.number })
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
  // An issue this screen phrases itself is stored phrased, so that whatever
  // renders it - a destination row, an amount field, an entry card - shows the
  // visitor's language rather than the API's English. Only `duplicate_entry`
  // qualifies today, and it is the one detail whose key is a bare `entries[N]`,
  // so no existing consumer of this map sees a changed value.
  return Object.fromEntries((error.details || []).map(detail => [
    detail.field,
    (detail.issue === 'duplicate_entry' ? duplicateEntryMessage(/^entries\[(\d+)\]/.exec(detail.field || '')) : undefined)
      || detail.message
      || t('This value could not be accepted.'),
  ]))
}

async function submitCalculation() {
  if (Date.now() < state.rateLimitedUntil) return
  setState({ loading: true, error: null, errorAt: null, errorCode: null, fieldErrors: {} })
  try {
    // §6.2: the whole submission travels in one call. One request per entry would let
    // §5.3's token upsert overwrite every entry but the last, cost N× the rate limit,
    // and leave the earlier entries persisted when a later one fails.
    const chains = [...state.entries, draftEntry()]
    // **`entryResultsFrom` pairs by INDEX, and the request carries LEAVES.** Hand it
    // `chains` while the body was built from leaves and every figure on the results page,
    // in the text export and in the PDF is attached to the wrong entry, with no error and
    // no warning anywhere. The two `submissionLeaves` calls are on the same input and are
    // pure, so they cannot disagree.
    const leaves = submissionLeaves(chains)
    // `submissionPayload` is shared with `improvement.js`'s Compare Impact button, which
    // re-sends the whole submission under this same token. Two builders is how the four
    // round-two fields came to be silently dropped by the second call.
    const response = await calculate(submissionPayload(state, chains))
    const token = response.token || state.token
    if (token) sessionStorage.setItem('kaiCalculatorToken', token)
    setState({ result: { ...response, entry_results: entryResultsFrom(leaves, response) }, resultTaxonomy: state.taxonomy, token, loading: false, step: 5, error: null, errorAt: null, errorCode: null, fieldErrors: {}, returnTo: null })
    // **§7.2a's second checkpoint, and it is the response rather than the press.** The
    // click handler already wrote the answers when Calculate was pressed — before this
    // request was sent, so that a `RATE_LIMITED` refusal still leaves them restorable — and
    // that copy says `step: 4`, because this function is `async` and the step only becomes
    // 5 on the line above. So both documents are rewritten here: the answers, to carry the
    // step the visitor is now on, and the result, which is the whole of what this revision
    // adds.
    //
    // **`resultTaxonomy: state.taxonomy` in the same patch as `result`, every time.** Not
    // only on the restore path: one code path means the restored page exercises the same
    // reads as this one, and it closes a live case too — the `catch` below re-fetches the
    // taxonomy on `UNKNOWN_CODE`, so `state.taxonomy` can move underneath a result that is
    // still on screen.
    writeSnapshot(state)
    writeResultSnapshot(state)
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
    // A refusal is pressed on the review step and nowhere else, so a visitor it pushes
    // back to a form step — `UNKNOWN_CODE`'s step 1, `VALIDATION_ERROR`'s step 4, or
    // whichever step `detailStep` names for the field the server objected to — came from
    // review just as surely as one who pressed *Edit*, and Back has to say so. Nothing was
    // moved, so the marker carries no snapshot and backing out asks nothing. `errorStep`
    // is the review step itself for every failure that does not name a field, and that
    // one clears the marker rather than writing a Back that returns to where it already is.
    setState({ loading: false, error: publicError(error), errorAt: null, errorCode: error.code || 'UNKNOWN_ERROR', fieldErrors: fieldErrorMap(error), rateLimitedUntil, step: errorStep, returnTo: errorStep >= 0 && errorStep < 4 ? { from: errorStep, step: 4 } : null })
    // Clearing the deadline without clearing the banner re-enabled Calculate underneath a
    // paragraph still telling the user to wait 60 seconds — the button and the copy saying
    // opposite things, with the copy the more believable of the two. The banner only goes if
    // it is still the rate-limit one: another failure may have replaced it inside the minute,
    // and that message is about something the wait does not fix.
    if (error.code === 'RATE_LIMITED') {
      setTimeout(() => setState(state.errorCode === 'RATE_LIMITED'
        ? { rateLimitedUntil: 0, error: null, errorAt: null, errorCode: null }
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
  const key = leafOf(control)
  patchLeaf(key, { unitCount: control.value })
  state.error = null
  state.errorAt = null
  const total = document.getElementById(control.id.replace(/^unit-count/, 'container-total'))
  if (total) total.textContent = containerTotalText(state.leafFigures[key] || EMPTY_LEAF)
  updateCombinedTotal()
}

/**
 * Step 3's combined figure, on keystroke.
 *
 * The same §7.2 exception `updateContainerCount` above takes, for the same reason: a
 * re-render per keystroke destroys the focused input. It is a sum of figures already on
 * screen and it never reaches the wire - the browser-only arithmetic `allocatedAmount`
 * already performs - but it is the one number on this step that says the fork adds up,
 * and a running total that only moved on Continue would be worse than none.
 */
function updateCombinedTotal() {
  const node = document.querySelector('[data-combined-total]')
  if (!node) return
  const kilograms = draftLeaves().reduce((sum, leaf) => sum + (totalKilograms(draftLeafFigures(leaf)) || 0), 0)
  const amount = state.totalUnit === 'tonnes' ? kgToTonnes(kilograms) : kilograms
  node.textContent = `${formatNumber(amount, 2)} ${unitLabel(state.totalUnit)}`
}

/**
 * Every per-leaf card's completion badge, on keystroke — step 3's and, since #142,
 * step 4's.
 *
 * **The same §7.2 exception `updateCombinedTotal` above takes, and it is not optional
 * here.** Step 3's Continue is never disabled — it validates on the press — so the badge
 * is the only thing on that screen that says, before the press, whether a card would be
 * refused. A badge that moved only on Continue would read as a promise about a card the
 * visitor had just finished typing into, and the press would then disagree with it. On
 * step 4 the button IS disabled before the press, and the badge is then the thing that
 * says *which* card the disabled button is about — the button cannot, and a shut card's
 * own contents cannot either.
 *
 * It asks `leafSettled`, which is `leafProblem`, which is what Continue asks. The markup
 * it rewrites is `cardStatus`'s, so the two states are stated once.
 *
 * **`step` is a parameter and not read off `state`** so that the question is the step the
 * caller is rendering, exactly as `stepProblem` is parameterised: `updateLine` runs on
 * step 4 and step 3's three scalar handlers run on step 3, and neither has to consult
 * anything to know which it is.
 *
 * Found through `data-leaf-panel` rather than by index: the cards are keyed by leaf and
 * an index into a NodeList is one untick away from naming a different food. A leaf with no
 * card on screen — the single-leaf step 3 panel — simply has no badge to rewrite, which is
 * why this walks the leaves rather than the cards.
 */
function updateCardBadges(step = 2) {
  const leaves = draftLeaves()
  if (!leaves.length) return
  const named = leaves.length > 1
  for (const leaf of leaves) {
    const key = leafKey(leaf)
    const panel = document.querySelector(`[data-leaf-panel="${CSS.escape(keyAttr(key))}"]`)
    const badge = panel?.querySelector('[data-card-status]')
    if (!badge) continue
    const status = cardStatus(leafSettled(step, leaf, named ? leafName(leaf) : null))
    badge.dataset.state = status.state
    badge.querySelector('.step-card__mark').textContent = status.mark
    badge.querySelector('[data-card-status-text]').textContent = status.text
  }
}

/**
 * Move focus to the field `stepProblemAt` named, once its card has been opened.
 *
 * **Decision 3 of #134**: pressing Continue over an incomplete or invalid card expands
 * that card and moves focus to the first field at fault. The place comes from
 * `leafProblem`'s own `field`, never from comparing the message's prose — two leaves
 * produce the byte-identical sentence, which is why `state.errorAt` exists at all.
 *
 * **Called after the `setState` that opened the card, and synchronously.**
 * `main.js`'s subscriber runs inside `setState`, so by the time this returns to the
 * caller the new DOM is already in place and has already had focus moved to `<main>` by
 * that subscriber — this is what moves it on to the box. Not inside a
 * `requestAnimationFrame`: a frame does not arrive in a background tab, and a refusal
 * that silently failed to focus anything there would be a refusal nobody could act on.
 *
 * `amount` names two controls because it is one question asked in two modes — a mass in
 * `#total-waste`, a count in `#unit-count` — and `leafProblem` does not care which mode
 * the card is in.
 *
 * **`allocation` is step 4's whole card, so it resolves to the FIRST of that card's
 * thirteen rows.** Its controls are `data-line-field` — one per destination, keyed by line
 * id — rather than the single `data-leaf-field` box the other four names are, so this used
 * to look for a `[data-leaf-field="allocation"]` that no screen has ever drawn and answer
 * `null` for every step-4 refusal. The first row is where a visitor starts reading the
 * list, and `leafProblem` knows the leaf and not which of its rows is the wrong one —
 * "allocated more than you have" is a property of the thirteen together.
 *
 * @returns {Element|null} what it focused, so the caller can tell whether the page has
 *   already been moved to the fault and must not also be scrolled to the top.
 */
function focusLeafField(at) {
  if (!at || !at.leaf) return null
  const leaf = CSS.escape(keyAttr(at.leaf))
  if (at.field === 'allocation') {
    const row = document.querySelector(`[data-line-field="amount"][data-leaf="${leaf}"]`)
    if (row) row.focus()
    return row || null
  }
  const fields = at.field === 'amount' ? ['amount', 'count'] : [at.field]
  for (const field of fields) {
    const control = document.querySelector(`[data-leaf-field="${field}"][data-leaf="${leaf}"]`)
    if (control) {
      control.focus()
      return control
    }
  }
  return null
}

/**
 * One leaf's figures, patched in place.
 *
 * **In place, not through `setState`**, for the keystroke handlers only: `render()`
 * replaces `main.innerHTML`, so a re-render per keystroke destroys the focused input -
 * §7.2's documented exception, the same one `updateLine` and `improvement.js` take.
 * Every `<select>` on this page still goes through `setState`, because a select has no
 * mid-edit caret to lose.
 */
function patchLeaf(key, patch) {
  const held = state.leafFigures[key] || EMPTY_LEAF
  state.leafFigures = { ...state.leafFigures, [key]: { ...EMPTY_LEAF, ...held, ...patch } }
}

/** The same, as a `setState` patch rather than an in-place write. */
const leafPatch = (key, patch) => {
  const held = state.leafFigures[key] || EMPTY_LEAF
  return { leafFigures: { ...state.leafFigures, [key]: { ...EMPTY_LEAF, ...held, ...patch } } }
}

function updateLine(control) {
  const lineId = control.dataset.lineId
  const key = leafOf(control)
  const figures = state.leafFigures[key] || EMPTY_LEAF
  const lines = (figures.current || []).map(line => (line.id === lineId ? { ...line, qtyInput: control.value } : line))
  patchLeaf(key, { current: lines })
  // The server named its failing lines by their position among the lines that were sent
  // (§9), and blanking or filling a row changes which lines would be sent at all. The
  // stored paths stop meaning what they meant, so they go rather than move to a
  // neighbouring row; the highlights they drew are cleared below with the rest.
  state.fieldErrors = {}
  state.lastChangedDestination = lines.find(line => line.id === lineId)?.destination || null
  const unit = figures.totalUnit
  const total = totalNumber(state.leafFigures[key])
  const sum = allocatedAmount(lines, unit)
  const summary = document.querySelector(`[data-summary-leaf="${CSS.escape(keyAttr(key))}"]`)
  const hasNegative = lines.some(line => line.qtyInput !== '' && Number(line.qtyInput) < 0)
  summary?.classList.toggle('invalid', exceedsTotal(sum, total) || hasNegative)
  if (summary) {
    summary.querySelector('[data-summary="allocated"]').textContent = `${sum.toFixed(2)} ${unitLabel(unit)}`
    summary.querySelector('[data-summary="remaining"]').textContent = `${remainingAmount(total, sum).toFixed(2)} ${unitLabel(unit)}`
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
  // **Every card's badge, not only this one's** (#142). An allocation is refused per leaf,
  // but `#allocation-error` holds only the first refusal, so the badge is what says of
  // *each* card whether it is the one the disabled button is about — and a shut card has
  // nothing else to say it with. Step 4, so `leafSettled(3, ...)`: the same `step` the
  // renderer passed `cardStatus`, and the same question Continue is gated on.
  updateCardBadges(3)
}

/**
 * One saved entry, as the draft — the patch and not the `setState`, so that the two
 * things that put an entry back in the draft slot share one definition of what the draft
 * slot *is*.
 *
 * `draftEntry` (`state.js`) and this are exact inverses, key for key. They have to stay
 * that way: `returnTo.draft` is built by the first and restored by the second, so a field
 * added to one and not the other is a field that silently fails to come back.
 *
 * The line ids are re-minted rather than carried over, exactly as this did when it was
 * only `loadEntry`. Nothing keys on a line id across a render — `fieldErrors` uses the
 * server's own paths and `lastChangedDestination` uses a destination code — so no
 * assertion may be written against their identity either.
 */
const entryPatch = entry => ({
  sector: entry.sector,
  foodCategories: [...(entry.foodCategories || [])],
  foodUnspecified: Boolean(entry.foodUnspecified),
  foodItems: Object.fromEntries(Object.entries(entry.foodItems || {}).map(([code, items]) => [code, [...items]])),
  totalUnit: entry.totalUnit || 'kilograms',
  // Pruned to the entry's own leaves on the way in as well as on the way out: a chain
  // written before a category was unticked must not carry that category's figures back
  // into the draft, where re-ticking it would silently restore them.
  leafFigures: Object.fromEntries(entryLeaves(entry).map(leaf => {
    const figures = leafFigures(entry, leaf)
    // The line ids are re-minted, exactly as this did when it was only `loadEntry`.
    // Nothing keys on a line id across a render - `fieldErrors` uses the server's own
    // paths and `lastChangedDestination` uses a destination code - so no assertion may be
    // written against their identity either.
    return [leafKey(leaf), { ...figures, current: figures.current.map(line => ({ ...line, id: randomId() })) }]
  })),
  error: null,
  errorAt: null,
  fieldErrors: {},
  lastChangedDestination: null,
})

function loadEntry(entry) {
  setState({ ...entryPatch(entry), step: 0 })
}

/**
 * An empty draft entry, as a `setState` patch.
 *
 * Named because two things need the same definition of "the visitor has entered nothing
 * for this entry": `clearDraft` below, which writes it, and `movedSnapshot`'s `after`,
 * which has to describe what the draft will look like the instant `add-entry` has
 * cleared it. A second inline copy of the key list is a second thing to keep in step,
 * and the one that drifted would make the confirmation dialog either never fire or
 * always fire.
 */
const EMPTY_DRAFT = { sector: null, foodCategories: [], foodUnspecified: false, foodItems: {}, totalUnit: 'kilograms', leafFigures: {} }

/**
 * Everything about an entry that the visitor typed, and nothing else, as a comparable
 * value.
 *
 * **What it leaves out is the whole of its correctness.** Line `id`s are excluded
 * because they are render bookkeeping, not input: `entryPatch` re-mints them on every
 * load and `normaliseLines` may rebuild the list, so two drafts holding the identical
 * figures routinely carry different ids. Including them would make the comparison below
 * report "changed" for every restored entry, and the dialog would fire on a Back that
 * discards nothing — the exact noise the owner ruled against.
 *
 * Every other key `draftEntry()` carries is in. The fallbacks mirror `entryPatch`'s, so
 * a saved entry and the draft it becomes fingerprint identically; without them an entry
 * written before a field existed would read as "changed" the moment it was opened.
 *
 * `state.timeFrame` and `state.gwpHorizon` are deliberately absent: they belong to the
 * submission rather than to an entry, and the undo does not touch them, so a visitor who
 * changed the period would otherwise be asked about an entry they never touched.
 */
const entryFingerprint = entry => [
  entry.sector || null,
  [...(entry.foodCategories || [])],
  Boolean(entry.foodUnspecified),
  // **Leaf keys in LEAF ORDER, never `Object.keys(leafFigures)`.** A category ticked,
  // filled and unticked leaves a record behind unless something prunes it, and a
  // fingerprint that read the map's own keys would see figures that are not on screen -
  // which makes `backingOutDiscardsWork` fire on a Back that discards nothing, or not
  // fire on one that discards everything, depending on which way the ghost falls.
  entryLeaves(entry).map(leaf => {
    const figures = leafFigures(entry, leaf)
    return [
      leafKey(leaf),
      figures.totalAmount || '',
      figures.totalUnit || 'kilograms',
      figures.measureMode || 'mass',
      figures.unitPreset || null,
      figures.unitCount || '',
      figures.totalInputKg || '',
      figures.totalValueNzd || '',
      figures.wastedValueNzd || '',
      // Line `id`s are excluded because they are render bookkeeping, not input:
      // `entryPatch` re-mints them on every load, so two drafts holding the identical
      // figures routinely carry different ids and the comparison would report "changed"
      // for every restored entry.
      figures.current.map(line => [line.destination, line.qtyInput || '', line.unit || null]),
    ]
  }),
]

/** Two entries holding the identical input, by the fingerprint above. */
const sameContent = (one, other) => JSON.stringify(entryFingerprint(one)) === JSON.stringify(entryFingerprint(other))

/**
 * The same entry with every destination row nobody has filled in removed.
 *
 * **A blank row is not input; it is furniture.** `continue` on the step 3 -> 4 move
 * builds one `createLine` row per destination the sector offers, with the destination
 * fixed by the taxonomy and `qtyInput: ''`. The visitor chose none of them and typed
 * nothing into them, so a draft holding thirteen of those and nothing else has had
 * nothing entered into it — and must fingerprint as such, or `loadingGivesBackTheDraft`
 * reads a walk as far as step 4 and back as work to be confirmed before it is lost.
 *
 * **A row is only dropped when its unit is the entry's own as well.** A visitor who set
 * one row to tonnes and has not yet typed its figure made a choice the load would take
 * away, and `rowUnit` in `destinationRows` reads `line.unit || state.totalUnit`, so
 * "unset" and "set to the entry's unit" are the same row. Anything else stays.
 *
 * This is deliberately *not* folded into `entryFingerprint`. `backingOutDiscardsWork`
 * compares two snapshots of the same shape taken at two moments, where a blank row is on
 * both sides and cancels; here one side is `EMPTY_DRAFT`, which has no rows at all, and
 * the asymmetry is the whole of the question.
 */
const withoutBlankLines = entry => ({
  ...entry,
  leafFigures: Object.fromEntries(entryLeaves(entry).map(leaf => {
    const figures = leafFigures(entry, leaf)
    return [leafKey(leaf), {
      ...figures,
      current: figures.current.filter(line => line.qtyInput !== '' || (line.unit || figures.totalUnit) !== figures.totalUnit),
    }]
  })),
})

/**
 * Whether opening `entry` hands the draft slot back everything it currently holds — so
 * that overwriting the draft with it destroys nothing at all.
 *
 * **This is not a second definition of "complete"; it is a different question.**
 * `draftIsComplete` asks whether the draft is fit to be *kept* as a saved entry.
 * This asks whether there is anything to keep. Two shapes qualify:
 *
 *   * an untouched draft — nothing was entered, so nothing can be lost;
 *   * a draft that is nothing but the `(sector, foodCategory)` pair the opened entry
 *     already carries. **This is the whole of the duplicate notice's ordinary case**:
 *     `draftRepeatsSavedEntry` matches on exactly that pair and nothing else, so an
 *     "Open entry N" pressed after choosing a sector and a category loads an entry
 *     whose first two answers are the two the visitor just gave.
 *
 * The comparison is against *the opened entry's* pair rather than the draft's own,
 * which is the difference between "nothing is lost" and "not much is lost". A sector
 * chosen on the review step's Edit-link excursion and then abandoned by pressing Edit
 * on some other card is a choice the load does not give back, so it is asked about.
 *
 * **Both comparisons are made against `withoutBlankLines`, and without it the second one
 * could not match.** `EMPTY_DRAFT.current` is `[]`, but a draft that has been as far as
 * step 4 holds one `createLine` row per destination — a destination the taxonomy chose
 * and an amount nobody has typed. Reproduced: save one entry, start a second, walk to
 * step 4, walk back to step 3, clear the amount, clear the category, and the duplicate
 * notice's *Open entry 1* asked before discarding a draft that held nothing but the
 * pair the opened entry was about to hand straight back. The identical journey stopped
 * one screen short of step 4 asked nothing, which is the control that names the cause.
 */
/**
 * Figures held for a leaf that is not currently selected.
 *
 * Unticking a category parks its record rather than discarding it, so re-ticking
 * brings the work back. Parked is not safe, though: `entryPatch` prunes to the
 * loaded entry's own leaves, so loading an entry destroys every parked record.
 * Anything that replaces the draft wholesale has to count these.
 */
const parkedFigures = () => {
  const live = new Set(draftLeaves().map(leafKey))
  return Object.entries(state.leafFigures || {})
    .filter(([key]) => !live.has(key))
    .map(([, figures]) => figures)
}

const loadingGivesBackTheDraft = entry => {
  // Parked figures are destroyed by the load and restored by nothing, so their
  // presence alone means the visitor has something to lose.
  if (parkedFigures().some(leafHasFigures)) return false
  const draft = withoutBlankLines(draftEntry())
  // The pair it named is a quadruple now: the opened entry's sector AND its whole food
  // selection, because that selection is what the duplicate notice matched on.
  return sameContent(draft, EMPTY_DRAFT) || sameContent(draft, {
    ...EMPTY_DRAFT,
    sector: entry.sector,
    foodCategories: entry.foodCategories,
    foodUnspecified: entry.foodUnspecified,
    foodItems: entry.foodItems,
  })
}

/** The whole calculator's entry content — the saved list and the draft — as one string. */
const contentSnapshot = (entries, draft) => JSON.stringify([entries.map(entryFingerprint), entryFingerprint(draft)])

/** The same, read off `state` right now. */
const currentSnapshot = () => contentSnapshot(state.entries, draftEntry())

/**
 * What a jump onto step 1 is about to move, captured *before* it moves it.
 *
 * Both callers destroy their own evidence: `add-entry` pushes the draft into `entries`
 * and then empties it, and `edit-entry` replaces an entry (or removes it) and overwrites
 * the draft with it. So this has to be evaluated first, at the call site, and not
 * reconstructed afterwards — see the note on `returnTo` in `state.js`.
 *
 * **What it holds is an undo and never a sole copy.** `edit-entry` used to leave the
 * displaced chain here and nowhere else, which made a marker that three handlers
 * deliberately drop the only thing standing between a visitor and losing it. It now
 * swaps a complete draft onto the saved list, where it is on screen and outlives every
 * one of those drops.
 *
 * @param {number} step The screen the visitor is standing on, and so the one Back returns
 *   them to: 4 from the review step's Add and Edit buttons, 1 from step 2's duplicate
 *   notice.
 * @param {object} moved `kind` (`'add'` | `'edit'`) and `after` — `contentSnapshot` of
 *   the state the jump is about to *install*, which the call site has already computed
 *   and this function cannot see.
 */
const movedSnapshot = (step, moved) => ({ from: 0, step, entries: [...state.entries], draft: draftEntry(), ...moved })

function clearDraft() {
  // `openCards` goes with the draft it describes: the next chain has its own leaves, and
  // a card id left behind from the chain just committed would decide the open state of a
  // card for a food this one may not even name. The stated default is all collapsed, so
  // an empty list is what a fresh chain starts on.
  setState({ ...EMPTY_DRAFT, step: 0, error: null, errorAt: null, fieldErrors: {}, expandedSectors: [], openCards: [], lastChangedDestination: null })
}

/**
 * What a restore had to throw away, as one notice above the step.
 *
 * **Saying so is the whole point of the revalidation** (§7.2a). A restored answer is
 * checked against a freshly fetched taxonomy and anything the current published set no
 * longer offers is dropped - and dropping a category or a destination the visitor chose
 * *silently* is the failure that check exists to prevent, not a tidy-up. So every drop is
 * named here, by the kind of thing it was and by the code the visitor's own answer
 * carried.
 *
 * **The code and not a name, because there is no name to print.** §6.1's response is the
 * only place a `code` becomes words, and the whole premise here is that this code is no
 * longer in it. A retired row's last label is not kept anywhere on this side, and
 * inventing one would be worse than printing the code.
 *
 * **Five existing catalogue keys and one new sentence.** `Sector`, `Food category`,
 * `Food`, `Destination` and `Containers` are already rendered elsewhere in this form, and
 * the separator `', '` is already a key; only the sentence is new. It is a plain quoted
 * literal at the `t()` call, because `tests/web/i18n_keys.py` is a regex and a template
 * literal there ships English in twenty catalogues with nothing failing.
 *
 * **The escape is on the finished sentence, not on the code.** A `code` here came out of
 * the visitor's own `sessionStorage` and can therefore be anything a hand edit put there,
 * so it must not reach the markup raw — and since it is interpolated into the sentence
 * before `escapeHtml` runs on the whole of it, one escape covers both. Escaping the code
 * first would double-escape it the moment the sentence were escaped as well.
 *
 * It is NOT `state.error`: `clearedError` empties that on every step transition, and this
 * has to survive the visitor walking back to the step the dropped answer was on. It is
 * cleared by `calculate` - the point past which there is nothing left to check - and by
 * `resetCalculator`.
 *
 * Markup copied from the duplicate notice above: the same `.disclaimer.compact` aside,
 * with no `aria-live` region, for the reason that one has none. `render()` replaces
 * `main.innerHTML` on every `setState`, so a live region here would re-announce the same
 * sentence on every keystroke that reaches `setState`.
 */
const DROPPED_LABELS = {
  [DROPPED_SECTOR]: () => t('Sector'),
  [DROPPED_FOOD_CATEGORY]: () => t('Food category'),
  [DROPPED_FOOD_ITEM]: () => t('Food'),
  [DROPPED_DESTINATION]: () => t('Destination'),
  [DROPPED_UNIT_PRESET]: () => t('Containers'),
}

function restoreNotice() {
  const dropped = state.restoreDropped || []
  if (!dropped.length) return ''
  // **Not over a restored result** — the same rule `calculate` applies one screen earlier,
  // for a reason that is stronger here. `calculate` clears this list outright so that a
  // notice about a dropped answer does not follow the visitor onto the results page; a
  // restore that lands on step 5 has pruned the form behind that page and has something to
  // report, but reporting it *here* puts "some of the answers you had entered ... have been
  // removed" directly above a set of figures the drop did not touch and cannot touch. A
  // restored result is history: it is re-shown under the factor set that produced it and is
  // never recomputed, so the notice would read as a caveat on numbers it says nothing about.
  //
  // Held rather than cleared, so it is still there when *Edit your data* takes the visitor
  // back into the form that actually lost something. It clears where it always did — on the
  // next `calculate`, and on `resetCalculator`.
  if (state.step === 5) return ''
  const items = dropped
    .map(entry => `${(DROPPED_LABELS[entry.kind] || (() => entry.kind))()} “${entry.code}”`)
    .join(t(', '))
  const sentence = t('Some of the answers you had entered are no longer offered by this calculator and have been removed: %(items)s. Please check your selections before you calculate.', { items })
  return `<aside class="disclaimer compact restore-notice" aria-label="${escapeHtml(t('Important information'))}"><span class="info-icon" aria-hidden="true">i</span><div><p>${escapeHtml(sentence)}</p></div></aside>`
}

export function render(main) {
  main.className = `main-content${state.step === -1 ? ' introduction-main' : ''}`
  if (state.loading && !state.taxonomy) {
    main.innerHTML = `<section class="content-section"><p class="loading-state" role="status">${escapeHtml(t('Loading calculator options…'))}</p></section>`
    bindResultsSectionSpy(main)
    return
  }
  if (!state.taxonomy) {
    // §9.2: `BLOCKED` is the one failure here that a retry can never clear, so the button
    // is withheld rather than disabled — the message is the whole of the response.
    main.innerHTML = `<section class="content-section error-state"><h1>${escapeHtml(t('Calculator unavailable'))}</h1><p>${escapeHtml(state.error || t('The taxonomy could not be loaded.'))}</p>${blocked() ? '' : `<button class="button button-primary" type="button" data-action="retry">${escapeHtml(t('Try again'))}</button>`}</section>`
    bindResultsSectionSpy(main)
    return
  }
  // Step 1 is two panels, not two steps -- see `itemStep`. `foodStage` picks
  // which, and `itemStepOffered()` is re-asked on every render so a stage left
  // at 'items' by an earlier draft cannot strand the visitor on a panel this
  // taxonomy has nothing to put in.
  const foodPanel = () =>
    (state.foodStage === 'items' && itemStepOffered() ? itemStep : foodStep)()
  const screens = [sectorStep, foodPanel, amountStep, destinationStep, reviewStep]
  // The restore notice sits OUTSIDE the step's `<section>`, above it. Inside would break
  // `stepNav`'s "must stay the last child" rule by adding a sibling after it, and it
  // belongs to the page load rather than to the step.
  main.innerHTML = restoreNotice() + (state.step === -1 ? introduction() : state.step === 5 ? renderResults(state) : screens[state.step]())
  // **Called at every exit from this function, including the two above that are not
  // the results page.** The floating nav's scroll-spy watches four `<section>`
  // elements that this line has just destroyed and recreated, so it is re-pointed
  // after every render rather than wired once at start-up; off the results page there
  // is no nav, and the call disconnects instead. One observer object either way --
  // see `bindResultsSectionSpy` in `results.js` for why that has to be true.
  bindResultsSectionSpy(main)
  bindStepSectionNavigation(main)
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

/**
 * Whether backing out of `marker` would throw away something the visitor entered after
 * the jump that wrote it.
 *
 * **The test is: does the calculator's entry content still look exactly as the jump left
 * it?** `marker.after` is a `contentSnapshot` taken of the state the jump installed —
 * the emptied draft for an add, the opened entry for an edit — and this compares it with
 * the same snapshot of now. Equal means every keystroke since the jump is either absent
 * or was undone by hand, so the restore replaces the content with identical content and
 * the visitor loses nothing. Different means the restore overwrites work.
 *
 * It cannot be wrong in either direction, and both directions matter:
 *
 *   * **It cannot miss a loss.** Every key `draftEntry()` carries is in the fingerprint,
 *     as is each destination line's own destination, amount and unit, as is the saved
 *     entry list. The restore writes exactly those and nothing else, so anything the
 *     restore can overwrite is something the fingerprint reads.
 *   * **It cannot invent one.** The only things excluded are line ids — re-minted on
 *     every load and never entered by anyone — and the submission-level `timeFrame` and
 *     `gwpHorizon`, which the restore does not touch. Pressing Add and immediately
 *     pressing Back therefore asks nothing, which is the case that would otherwise train
 *     people to dismiss the dialog unread.
 *
 * A marker with no `draft` (a review *Edit* link, a refused Calculate) restores nothing
 * at all, so it never reaches here.
 */
const backingOutDiscardsWork = marker => currentSnapshot() !== marker.after

/**
 * What the visitor is asked before the undo runs.
 *
 * **Two messages, not one, because the two jumps destroy different things.** Backing out
 * of an add throws away a supply-chain entry that was being started and was never on the
 * list; backing out of an edit throws away alterations to an entry that is on the list
 * and will go back onto it unaltered. "Discard your changes?" would be wrong for the
 * first — there is nothing to change yet.
 *
 * **The edit message names no number, and that is the point.** It used to say "entry
 * %(number)s", `number` being the entry the visitor clicked — right about the button and
 * wrong about the position from the moment `edit-entry` became a swap. The swap puts the
 * displaced draft at the opened entry's index, so after "Edit entry 1" the card numbered
 * 1 is a *different* chain, on screen, under that number, while the entry the question is
 * actually about has left the list for the draft slot and has no number at all. A
 * sentence that points at a card the visitor can see and means another one is worse than
 * a sentence that points at no card: "the entry you opened" is true before the swap,
 * after it, and after the second swap that trades the two back.
 *
 * `window.confirm` rather than a styled dialog, following `start-over` above it: there is
 * no build step and no modal in this front end, the browser's own dialog is the one thing
 * that reliably blocks the navigation until it is answered, and OK/Cancel map cleanly
 * onto a question whose last words are "Go back anyway?".
 */
const discardPrompt = marker => (marker.kind === 'edit'
  ? t('Going back will discard the changes you have made to the entry you opened. Go back anyway?')
  : t('Going back will discard the new supply-chain entry you have started. Go back anyway?'))

/**
 * Every `go-step`, including each step's own Back.
 *
 * Three things happen here, and two of them are about `state.returnTo`:
 *
 * 1. **Backing out of a jump undoes it.** When the marker is about the screen the visitor
 *    is standing on (`from`) and this click is the Back it was written for (`step`), the
 *    entries and the draft go back to what they were before the jump moved them, in the
 *    same `setState` as the step change. Without the undo, returning to the review step
 *    after an `add-entry` shows an empty "Current entry N" — the draft `clearDraft` just
 *    emptied — with a Calculate button offering to submit it. That is the whole reason
 *    this is not a one-line change to `sectorStep`'s `back:`.
 *
 *    **And it asks first, whenever it would discard anything.** The marker survives a
 *    forward walk, so the visitor may have pressed Add, built a second chain across four
 *    screens and walked back — and the undo would then throw all four away. It is not
 *    narrowed to the press that immediately follows the jump, because the same journey
 *    keeps the work if Continue happened to be pressed on the destination step (the
 *    `continue` handler clears the marker on arrival at review) and loses it otherwise,
 *    with nothing on screen saying which case you are in. Declining returns without a
 *    `setState` at all: same step, same draft, same entries, marker still live.
 *
 *    A marker with no `draft` (a review *Edit* link, a refused Calculate) moved nothing,
 *    so it reverts nothing and asks nothing: a sector changed while on step 1 is kept,
 *    exactly as a field edited on steps 2-4 is kept when Back is pressed there.
 *
 * 2. **A review-step *Edit* link opens the same kind of excursion**, with nothing to put
 *    back. `jumped` is the link's own `data-jump="review"` rather than anything inferred
 *    from the step numbers, because review's Back and the *Waste destinations / Edit*
 *    link both emit `data-step="3"` — see `reviewEdit`.
 *
 * Every other `go-step` hands the marker to `markerAfterLeaving`, which keeps an undo —
 * so a visitor who pressed Add, chose a sector and continued to step 2 can still walk
 * Back to step 1 and back out of the add from there — and drops a review-*Edit*
 * excursion whose own step has been walked past.
 *
 * @returns {boolean} False only when the visitor declined the discard, so that the
 *   caller can treat the click as not having happened. Nothing moved, nothing rendered,
 *   and nothing may scroll the page either: see the note on the scroll at the bottom of
 *   `bindCalculator`.
 */
function goToStep(step, jumped = false) {
  const back = state.returnTo
  // **`back.from === state.step` is the test that keeps a marker from firing on a
  // different step's Back**, and it is killed by
  // `test_a_marker_from_the_duplicate_notice_does_not_fire_on_another_steps_back`.
  // The duplicate notice's `edit-entry` writes `{from: 0, step: 1}`; step 3's own Back
  // emits `data-step="1"` too, because step 3's previous screen and this marker's
  // destination are the same number for different reasons. Drop the origin test and
  // walking that entry forward to step 3 and pressing Back restores the pre-edit
  // snapshot over it. The same clause is what stops step 2's Back — identical
  // `data-step="0"` to step 1's — from ping-ponging, and what would stop the results
  // step's `back: 4` (`results.js`) from firing an `add-entry` marker, were
  // `submitCalculation` not already clearing it.
  if (back && back.from === state.step && back.step === step) {
    if (back.draft && backingOutDiscardsWork(back) && !window.confirm(discardPrompt(back))) return false
    setState({ ...(back.draft ? { ...entryPatch(back.draft), entries: back.entries } : {}), step, returnTo: null, ...clearedError() })
    return true
  }
  // **Any arrival at a step opens step 2's FIRST panel.** A jump named "food type"
  // means the category question; leaving `foodStage` at 'items' would answer a
  // different one, and a jump to any other step must not leave the stage set for
  // the next time step 2 is reached.
  setState({ step, foodStage: 'categories', returnTo: jumped ? { from: step, step: state.step } : markerAfterLeaving(), ...clearedError() })
  return true
}

/**
 * A browser Back or Forward press, answered by **the same function the on-screen Back
 * calls** (§7.2b).
 *
 * That identity is the whole of how the two Backs are kept from disagreeing: the jump undo,
 * the "this will discard the entry you started" confirmation, the `foodStage` reset and the
 * spent-marker rule are not reimplemented here, they are `goToStep`'s, and a refusal is
 * passed straight back so `history.js` can put the position where the visitor left it.
 *
 * **The one thing this adds is a clamp, and only `start-over` can reach it.** History
 * entries cannot be deleted, so after "Clear all calculator data" the entries *above* the
 * one the unwind returns to still name steps of a calculation that no longer exists.
 * Forward is a live direction, and a step past the stage question with no sector and no
 * saved entry has nothing to draw — `amountStep` would ask for figures against no food and
 * `reviewStep` would offer Calculate on nothing. So it lands on the introduction, and
 * `history.js` rewrites that entry to say so rather than leaving it to mislead the next
 * press. Step 0 is never clamped: the stage question is answerable from empty, and it is
 * where Forward after pressing Start and Back belongs.
 *
 * @param {number} step The step the entry being traversed to records
 * @returns {boolean} False only if the visitor declined a discard — `goToStep`'s own answer
 */
export function goToStepFromHistory(step) {
  const drawable = step >= 1 && !state.sector && !state.entries.length ? -1 : step
  return goToStep(drawable)
}

export function bindCalculator(main, retryTaxonomy) {
  reloadTaxonomy = retryTaxonomy
  main.addEventListener('click', event => {
    // **The one line step 3's term tooltips need, and it has to be here rather than in
    // `leafPanel`.** A `.term` sits inside `<label for=…>`; the label's default action on
    // click is to forward the activation to its control, so on a phone the tap focuses the
    // input, the span blurs, `:focus-within` stops matching and the panel closes in the
    // same frame it opened - the tooltip is unreachable by touch, and `:hover` is no
    // answer because a touch device has no hover. Refusing the label's default leaves the
    // focus the pointer already put on the span, and the panel stays up until the next tap
    // elsewhere. Nothing is recorded on `state`: `render()` replaces `main.innerHTML` on
    // every `setState`, so a flag there would rebuild the step to show a tooltip.
    // It runs before the `[data-action]` guard below, which returns early for exactly the
    // clicks this needs to see - a label is not an action.
    if (event.target.closest('.term')) event.preventDefault()
    const control = event.target.closest('[data-action]')
    if (!control) return
    const action = control.dataset.action
    if (action === 'focus-card') {
      const id = control.dataset.navTarget
      const card = document.getElementById(id)?.closest('.step-card')
      const toggle = card?.querySelector('.step-card__toggle[data-card-step]')
      if (!toggle) return
      if (toggle.getAttribute('aria-expanded') === 'false') {
        setState({ openCards: openedCard(Number(toggle.dataset.cardStep), decodeURIComponent(toggle.dataset.card || '')) })
      }
      const target = document.getElementById(id)
      const focusTarget = target?.querySelector('.step-card__toggle') || target
      if (focusTarget) focusTarget.focus({ preventScroll: true })
      target?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      markCurrentStepSection(id)
      return
    }
    // **The field a refused Continue has just put the caret in, or `null`** (#134,
    // decision 3). It exists so that the scroll-to-top at the bottom of this listener
    // can stand aside: `focus()` has already brought the box into view, and a smooth
    // `scrollTo({top: 0})` arriving afterwards would animate the page away from the
    // field the visitor was just sent to — the same shape of defect the declined-discard
    // note above records, through a different door.
    let refusedAt = null
    // Step 5's calendar owns four actions and answers whether it took the click,
    // so the chain below is not extended by a component that has its own module.
    if (handlePeriodClick(action, control, event)) return
    if (action === 'start') setState({ step: 0, returnTo: null, ...clearedError() })
    // **A declined discard ends the click**, exactly as `edit-entry`'s own declined
    // question below already did. `goToStep` returning early was not enough on its own:
    // the scroll-to-top at the bottom of this listener runs on the *action name*, so
    // Cancel left the screen, the draft and the saved list untouched and then threw the
    // visitor to the top of the page anyway. Measured at 320x600: scrollY 327 -> 0 on a
    // Back the visitor had just refused.
    if (action === 'go-step' && !goToStep(Number(control.dataset.step), control.dataset.jump === 'review')) return
    if (action === 'toggle-sector') {
      const code = control.dataset.sector
      setState({ expandedSectors: state.expandedSectors.includes(code) ? state.expandedSectors.filter(item => item !== code) : [...state.expandedSectors, code] })
    }
    // **One collapsible card, opened or closed** (#134). Written back to `state`, because
    // `render()` replaces `main.innerHTML` on every `setState` and an open-ness held by
    // the element would be erased by the next keystroke anywhere on the step.
    //
    // `decodeURIComponent` because the attribute holds `keyAttr(key)` - the leaf key's
    // own NUL survives a round trip through the DOM only encoded. The step comes off the
    // element too, so the card the visitor pressed and the id this writes are the same
    // pair by construction rather than by this handler knowing which step it is on.
    //
    // **Not in the scroll-to-top list at the bottom of this listener.** Opening a card
    // is a disclosure, not a navigation; throwing the page to the top would take the
    // card the visitor just pressed out from under their eyes. `main.js`'s subscriber
    // returns focus to the toggle by its `id`, so a keyboard user is left on the control
    // they operated with its `aria-expanded` freshly changed.
    if (action === 'toggle-card') {
      const id = cardId(Number(control.dataset.cardStep), decodeURIComponent(control.dataset.card || ''))
      const open = state.openCards || []
      setState({ openCards: open.includes(id) ? open.filter(one => one !== id) : [...open, id] })
    }
    // **#133's field index beside the step 3 title, and it navigates the way this project
    // navigates.** `openedCard` then `focusLeafField` — the identical pair decision 3 of
    // #134 uses for a refused Continue, and the reason the index emits a `<button>`
    // rather than an `<a href="#total-waste--bakery-grains">`: that id is a slug of a food
    // category the visitor chose, and a fragment link puts it in the address bar, which
    // is the one thing §7.2b's privacy note refuses. See `amountErrorSummary`.
    //
    // **Not in the scroll-to-top list at the foot of this listener**, for `toggle-card`'s
    // reason and one more: `focusLeafField` has already brought the box into view, so a
    // smooth scroll to the top would animate the page away from the field the visitor
    // just asked for.
    //
    // Synchronously after the `setState`, exactly as the `continue` branch is: `main.js`'s
    // subscriber runs inside `setState`, so the opened card is already in the document by
    // the time this line runs. Inside a `requestAnimationFrame` it would never fire in a
    // background tab.
    if (action === 'focus-field') {
      const at = { leaf: leafOf(control), field: control.dataset.field }
      setState({ openCards: openedCard(AMOUNT_CARD_STEP, at.leaf) })
      focusLeafField(at)
    }
    // Clearing the category takes any category-specific container with it, for the same
    // reason choosing a different one does: the preset is no longer on the list step 3
    // would offer, and a conversion nobody can see is a conversion nobody can check.
    // **It clears the selection and prunes the figures the selection carried.** A leaf
    // that is no longer selected must not leave its amounts, its money figures or its
    // destination rows behind: `entryFingerprint` would still read them, the back-out
    // confirmation would fire on a Back that discards nothing, and re-ticking the
    // category would silently restore figures the visitor had cleared.
    // Keeps `leafFigures` for the same reason the untick does: re-ticking a
    // category restores what was typed for it, and `draftEntry()` prunes at the
    // boundary so nothing dead is ever sent.
    if (action === 'clear-food') setState({ foodCategories: [], foodUnspecified: false, foodItems: {}, error: null, errorAt: null })
    // Step 2.5's own clear. It leaves the CATEGORIES alone: they are step 2's
    // answer, and clearing them from this panel would undo a question the visitor
    // is no longer looking at.
    if (action === 'clear-items') setState({ foodItems: {}, error: null, errorAt: null })
    // Step 2.5's Back. A panel change, not a step change: `goToStep` is not called
    // and no `returnTo` marker is touched, because the visitor has not left step 2.
    if (action === 'back-to-categories') setState({ foodStage: 'categories', error: null, errorAt: null })
    if (action === 'continue') {
      const problem = stepProblemAt(state.step)
      const error = problem.message
      // `errorCode` is cleared with it: `amountStep` tells a client-side message about a
      // leaf's own field apart from a server VALIDATION_ERROR naming a different field by
      // whether `errorCode` is still set from that response, and a leftover code from an
      // earlier submit must not survive to mislabel this one. `errorAt` says WHICH leaf
      // and which field, because with N leaves the sentence alone no longer does.
      //
      // **Decision 3 of #134 is these three lines.** A refusal that named a leaf expands
      // that leaf's card and moves focus into the box at fault, because a collapsed card
      // hiding its own error is the reverse of the problem the collapsing exists to fix.
      // The card is added to `state.openCards` rather than only forced open by the
      // message's presence (`leafPanel` does that too): the message is cleared on the
      // next keystroke, and a card that shut itself again the moment the visitor started
      // fixing it would be the same defect two seconds later.
      if (error) {
        const at = leafErrorAt(problem)
        setState({ error, errorAt: at, errorCode: null, ...(at ? { openCards: openedCard(state.step, at.leaf) } : {}) })
        refusedAt = focusLeafField(at)
      }
      // Step 3 -> 4 builds the destination rows, and it hands the marker to
      // `markerAfterLeaving` exactly as the general branch below does: this is the one
      // forward move with a patch of its own, and leaving `returnTo` out of it meant a
      // *Waste amount / Edit* marker walked past its own step and aimed step 3's Back at
      // the review step — the same loop, through the one door this branch owns.
      // **Step 2's Continue has two destinations.** With step 2.5 offered it opens
      // the second panel and the step number does not move; without it, the step
      // advances exactly as it did before the panel existed. `itemStepOffered()`
      // is false in every deployment today, so this branch is inert by data.
      else if (state.step === 1 && state.foodStage !== 'items' && itemStepOffered())
        setState({ foodStage: 'items', error: null, errorAt: null })
      else if (state.step === 2) setState({ step: 3, foodStage: 'categories', error: null, errorAt: null, returnTo: markerAfterLeaving(), ...destinationRowsPatch() })
      // **Arriving at the review step ends any excursion, and this line is load-bearing.**
      // A marker holds the entries and the draft as they were before the jump that wrote
      // it; once the visitor has reached review again they have built something that did
      // not exist then. Without this, `add-entry` → build the new chain → review → Back
      // four times to step 1 → Back would restore the pre-add snapshot *over* the chain
      // just built, which is real data loss introduced by the fix rather than by the bug.
      // `tests/web/test_step_one_back_navigation_browser.py` walks exactly that path.
      //
      // Every other Continue hands the marker to `markerAfterLeaving`, which keeps an
      // undo and discards a spent review-*Edit* excursion — see its own note for the
      // Back loop that keeping one built.
      // **`foodStage` is cleared on the way out, for `goToStep`'s reason and by the
      // same rule.** It says which of step 2's two panels is showing and nothing
      // about the draft, so it must not outlive the step it belongs to. Continue is
      // the OTHER way to arrive at a step, and it was not resetting it: after
      // *Add another entry*, `clearDraft` puts the visitor on step 0 with the stage
      // still reading 'items' from the chain they just finished, and the next
      // Continue landed them on step 2 with it intact. Nothing was visibly wrong
      // until they ticked a category the vocabulary has foods for -- at that moment
      // `itemStepOffered()` turned true, and `foodPanel` swapped the category list
      // for the food panel underneath their hand, mid-tick, with no Continue pressed.
      // `test_the_form_bounds_the_submissions_leaf_count_at_max_entries` is what met
      // it: the fifth tick moved the screen and its sixth checkbox never appeared.
      //
      // Every other route into step 2 already reset it here or in `goToStep`, and
      // all of them -- add, edit, Start -- reach step 2 through this branch.
      else setState({ step: state.step + 1, foodStage: 'categories', error: null, errorAt: null, returnTo: state.step === 3 ? null : markerAfterLeaving() })
    }
    if (action === 'add-entry') {
      // The rule, not the rendering: a `disabled` attribute can be removed with a
      // devtools inspector and the ceiling is the API's, not this button's.
      if (addEntryRefused()) return
      // Before the push and before `clearDraft`, which between them destroy both halves
      // of what Back would need to put back. `clearDraft` names a fixed key list that does
      // not include `returnTo`, so the marker survives it.
      //
      // `after` is what the calculator will hold once this click has finished — the
      // committed list and an empty draft — and it is stated here rather than read back
      // afterwards so that recording it costs no extra render. It is what the Back on
      // step 1 compares against to decide whether it has anything to ask about.
      const entries = [...state.entries, draftEntry()]
      const returnTo = movedSnapshot(4, { kind: 'add', after: contentSnapshot(entries, EMPTY_DRAFT) })
      setState({ entries, returnTo })
      clearDraft()
    }
    // **Opening a saved entry trades places with the draft; it does not overwrite it.**
    //
    // This used to filter the entry out of the list and then `loadEntry` it, and
    // `loadEntry` is a load: `entryPatch` overwrites every key of the draft. Whatever
    // chain was being built went with it. Since the marker was added it survived in
    // `state.returnTo.draft` — but that is a *navigation marker*, deliberately dropped
    // by `continue` on the 3 -> 4 move, by `start` and by `submitCalculation`, so a
    // visitor who walked forward instead of pressing Back lost the only copy, silently.
    // The owner reproduced exactly that, twice: a complete second chain gone with no
    // dialog and nothing on screen to say so. **Live data must not be the marker's to
    // keep.** The marker now carries only the undo.
    //
    // Three cases, and the decision is a property of the draft, never of the door. Both
    // doors can hold either kind: a complete chain reaches step 2's duplicate notice
    // through the review step's *Food category / Edit* link, and an incomplete one
    // reaches the review step through *Waste amount / Edit*, clearing the field and
    // pressing Back — `goToStep` validates nothing.
    if (action === 'edit-entry') {
      const index = Number(control.dataset.index)
      const entry = state.entries[index]
      // 1. A complete draft is a saved entry in everything but position, so it takes the
      //    position: `add-entry` promotes the identical object with no more ceremony than
      //    this. In place at `index` rather than appended, so only the two entries
      //    actually involved change number — `fieldErrors['entries[N]']` and
      //    `entryIndexOf` are both written against those numbers — and so `back.entries`
      //    stays the plain inverse of the move. Appending instead leaves every entry
      //    after `index` renumbered by a click that was about one card;
      //    `test_edit_and_back_cleanup_browser.py` is what tells the two apart, and it
      //    needs two saved entries to do it — with one, both produce the same array.
      const swap = draftIsComplete()
      // 2. An incomplete draft cannot go on the list; it is destroyed, as it always was.
      //    Silently only where the entry being loaded hands all of it straight back,
      //    which is the duplicate notice's ordinary case and the reason that door does
      //    not ask. 3. Otherwise a real figure would go, so the visitor is asked —
      //    before any mutation, so Cancel leaves the screen, the draft and the list
      //    exactly as they stood.
      if (!swap && !loadingGivesBackTheDraft(entry) && !window.confirm(t('Opening entry %(number)s will discard the supply-chain entry you have started here, which is not finished. Open entry %(number)s anyway?', { number: index + 1 }))) return
      // Everything below reads `state.entries` and `draftEntry()` as they are *now*, so
      // it all has to be computed before the `setState` — `movedSnapshot` most of all,
      // whose whole job is to capture what this click is about to move.
      //
      // `state.step` is the whole of what distinguishes this action's two call sites: 4
      // from a saved entry's card on the review step, 1 from step 2's duplicate notice.
      // Read here, before `loadEntry` moves the visitor to step 1.
      //
      // **No entry number is recorded**, because there is no longer one to record. The
      // marker used to carry `number` — the label on the button just pressed — for
      // `discardPrompt` to name. The swap moved the displaced draft into that very
      // position, so the number went on addressing a card and stopped addressing the
      // entry the question was about; see `discardPrompt`.
      const displaced = draftEntry()
      const entries = swap
        ? state.entries.map((saved, entryIndex) => (entryIndex === index ? displaced : saved))
        : state.entries.filter((_, entryIndex) => entryIndex !== index)
      // `after` is the content this click installs, whichever branch installed it — the
      // one value handed to both `contentSnapshot` and `setState`, so the two cannot
      // disagree about what the jump left behind.
      const returnTo = movedSnapshot(state.step, { kind: 'edit', after: contentSnapshot(entries, entry) })
      setState({ entries, returnTo })
      loadEntry(entry)
    }
    if (action === 'remove-entry') {
      const index = Number(control.dataset.index)
      setState({ entries: state.entries.filter((_, entryIndex) => entryIndex !== index), error: null })
    }
    // The notice goes at the moment there is nothing left to act on it: Calculate is the
    // end of the form, and a notice about a dropped answer must not follow the visitor onto
    // the results page.
    if (action === 'calculate') {
      if ((state.restoreDropped || []).length) setState({ restoreDropped: [] })
      submitCalculation()
    }
    if (action === 'start-over' && window.confirm(t('Clear all calculator data and return to the introduction?'))) resetCalculator()
    if (action === 'download-results') downloadResults(state)
    if (action === 'download-pdf') downloadPdf(state)
    // Round four's two steps. Submit arms a five-second window and sends nothing;
    // Undo cancels it. Neither of them talks to the API directly — see
    // `CONTRIBUTE_GRACE_MS` in `results.js` for why the window is spent before the
    // request rather than after it.
    if (action === 'contribute-submit') armContribute(state, publicError)
    if (action === 'contribute-undo') cancelContribute()
    if (action === 'breakdown-tab') setState({ resultBreakdownTab: control.dataset.tab })
    // Through `setState`, not by writing the DOM. `render()` replaces
    // `main.innerHTML` on every state change, so the element's own
    // `data-open` was erased by any unrelated update -- opening the menu and
    // then switching a breakdown tab closed it again.
    //
    // `undefined` means the stylesheet's viewport-dependent default is still
    // in force, and the first toggle has to invert *that*. **There are now two
    // such defaults**: docked in the gutter and open from 1600px up, a closed
    // handle from 1100 to 1599 (`styles.css`, the block over
    // `.results-floating-nav`). So the first press cannot assume "open" any
    // more -- it asks the stylesheet which regime is on screen, through the
    // custom property `resultsNavIsDocked()` reads, and inverts that. Hard-coding
    // `false` here is what made the first press a no-op at every width below
    // 1600 once the narrow band's default became closed.
    if (action === 'toggle-results-nav') {
      setState({ resultsNavOpen: state.resultsNavOpen === undefined ? !resultsNavIsDocked() : !state.resultsNavOpen })
    }
    if (action === 'explore-improvements') openImprovement(state)
    if (action === 'reset-improvement') resetImprovement(state)
    // **Which leaf's donut.** The improvement panel forks, so there is one chart per
    // food and the expanded one has to say which. `null` is closed; an index is open,
    // and index 0 is a real answer, which is why the state is not a boolean.
    if (action === 'expand-improvement-chart') setState({ improvementChartExpanded: Number(control.dataset.leaf || 0) })
    if (action === 'close-improvement-chart') setState({ improvementChartExpanded: null })
    if (action === 'cancel-improvement') setState({ improvementOpen: false, improvementChartExpanded: null, improvementResult: null, improvementError: null })
    // §9's code-to-copy map travels with the call. `improvement.js` cannot import it —
    // this module already imports that one — and without it the improvement panel showed
    // raw backend prose for the codes the main flow words carefully.
    if (action === 'compare-improvement') compareImprovement(state, publicError)
    if (action === 'retry' && !blocked()) retryTaxonomy()
    if (action === 'view-methodology') window.location.href = './methodology.html'
    // **The two checkpoints, and they are the visitor's own** (§7.2a). Every `continue`
    // and `calculate` is a moment they have just committed to something, which is why the
    // snapshot is written here and not from `setState`: `render()` replaces
    // `main.innerHTML` on every state change, and `period.js` deliberately does not
    // `setState` while a date is being typed - so a write per keystroke would have to be
    // fed by a render per keystroke, which is the one thing that module avoids.
    //
    // **After the action's own `setState`, never before.** The step 3 -> 4 move builds the
    // destination rows in its patch; written first, the snapshot would carry the step the
    // visitor is leaving and none of its rows.
    //
    // `calculate` is here rather than inside `submitCalculation` because what is being
    // recorded is the press, not the response: the review step is where `timeFrame` and
    // the period are typed, and a submission that comes back `RATE_LIMITED` must still
    // leave the answers restorable.
    if (action === 'continue' || action === 'calculate') writeSnapshot(state)
    if (!refusedAt && ['start', 'go-step', 'continue', 'add-entry', 'edit-entry', 'calculate', 'start-over', 'retry', 'view-methodology'].includes(action)) {
      window.scrollTo({ top: 0, behavior: 'smooth' })
    }
  })

  main.addEventListener('change', event => {
    const target = event.target
    if (target.name === 'sector') setState({ sector: target.value, error: null, errorAt: null })
    if (target.name === 'food-category') toggleFoodChoice(target)
    if (target.name === 'food-item') toggleFoodItem(target)
    // v1.67: the four presets are templates now, so choosing one fills the
    // interval (`period.js::timeFrameChanged`) and `time_frame` goes on recording
    // which shortcut was pressed. "Not stated" clears it, because the two columns
    // mean "no period was given" by absence.
    if (target.id === 'time-frame') setState(timeFrameChanged(target.value))
    // One control, both modes. `current: []` was already this handler's behaviour and the
    // reason is unchanged and now broader: the destination amounts were entered against a
    // total in a unit that is no longer the one in force.
    //
    // **A container pins `totalUnit` to kilograms**, because that is the unit step 4
    // allocates in and §6.2 compares the two. Without it, a visitor who chose tonnes and
    // then chose a wheelie bin would reach a step 4 whose rows say "tonnes" against a total
    // in kilograms — a thousandfold error on a screen that looks entirely normal, refused
    // by nothing, because both numbers are individually plausible.
    if (target.matches('[data-leaf-field="unit"]')) {
      const preset = target.value.startsWith(PRESET_OPTION) ? target.value.slice(PRESET_OPTION.length) : null
      setState({
        ...leafPatch(leafOf(target), {
          measureMode: preset ? 'container' : 'mass',
          unitPreset: preset,
          totalUnit: preset ? 'kilograms' : target.value,
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
        }),
        error: null,
        errorAt: null,
      })
    }
    // Item ⑥: one row's own unit, changed without touching any other row's — the
    // assertion `test_changing_one_row_s_unit_does_not_change_the_others` exists to catch a
    // single shared value wearing several `<select>`s. A `<select>` has no mid-edit caret to
    // preserve, so this goes through `setState` and a full re-render like every other select
    // on this page, rather than the keystroke-preserving patch `updateLine` uses for typing.
    if (target.matches('[data-line-field="unit"]')) {
      const lineId = target.dataset.lineId
      const key = leafOf(target)
      const held = state.leafFigures[key] || EMPTY_LEAF
      setState({
        ...leafPatch(key, { current: (held.current || []).map(line => (line.id === lineId ? { ...line, unit: target.value } : line)) }),
        error: null,
        errorAt: null,
      })
    }
    // §6.2.2, round four: **the tick sends nothing.** It records the visitor's intent
    // and enables the Submit button beside it, and that is all — the request is
    // `armContribute`'s, five seconds after Submit is pressed. Both directions are
    // handled now, unlike the one-way version this replaces: while the box is a
    // statement of intent rather than the act itself, unticking it is a real gesture
    // with a real meaning (it disables Submit again), and the box is only locked once
    // Submit has actually been pressed.
    //
    // Through `setState` rather than left in the DOM, for this file's usual reason:
    // `render()` replaces `main.innerHTML` on every state change, so a tick held only
    // by the element would be erased by the next unrelated update — a keystroke in the
    // improvement panel silently unticking a consent box.
    if (target.id === 'contribute') setState({ contributeTicked: target.checked, contributeError: null })
    // **The improvement panel's one unit control** (#74). A discrete choice like every
    // other `<select>` on this page, so it goes through `setState` and a full re-render
    // rather than the keystroke-preserving patch `updateImprovementInput` uses — there is
    // no caret in a `<select>` to lose, and a full re-render is exactly what is wanted
    // here: every row of every leaf's card has to be redrawn in the new unit together.
    //
    // `improvement.js` reads `state.improvementMode` — `'percentage'`, `'kilograms'`,
    // `'tonnes'` or `preset:<code>` — to decide what every row displays; the allocation
    // itself, in `improvedAllocations`, stays the percentage it always was (see the note
    // on `updateImprovementInput`), so changing what the panel is SHOWN in can never
    // change what it MEANS.
    //
    // **The companion `[data-improvement-unit-code]` branch is gone**, with
    // `state.improvementRowUnits`: between 2026-09-05 and #74 each row carried a unit of
    // its own and this handler patched one key of that map. The client asked on
    // 17 September for one unit throughout, so there is one key and one control; see the
    // note in `ImprovementScenario`, which keeps both asks with their dates.
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
    const decimalCeiling = { totalValue: 2, wastedValue: 2, totalInput: 3 }[target.dataset.leafField]
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
      ['amount', 'count', 'totalInput', 'totalValue', 'wastedValue'].includes(target.dataset.leafField) ||
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
    // The period's four text boxes, and they deliberately do not go through
    // `setState` — see `period.js`'s header. `calculateOtherwiseDisabled()` is
    // handed over so the button it patches carries the whole condition and not
    // just this field's half of it.
    if (handlePeriodInput(event, calculateOtherwiseDisabled())) return
    // **Keyed on `data-leaf-field`, never on an id.** There are N of each of these boxes
    // now, one per leaf, and the id is only a label target.
    if (target.dataset.leafField === 'amount' && !target.dataset.lineId) {
      patchLeaf(leafOf(target), { totalAmount: target.value })
      state.error = null
      state.errorAt = null
      updateCombinedTotal()
      updateCardBadges()
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
    const scalarKey = { totalInput: 'totalInputKg', totalValue: 'totalValueNzd', wastedValue: 'wastedValueNzd' }[target.dataset.leafField]
    // `updateCardBadges` is called for all three although only `wastedValue` and
    // `totalInput` can currently change a card's state - `totalValue` is one half of the
    // money contradiction, so typing in it changes whether the OTHER box is at fault.
    // Asking for all three is also what keeps the badge reading one rule set rather than
    // this handler's idea of which fields that rule set happens to use today.
    if (scalarKey) { patchLeaf(leafOf(target), { [scalarKey]: target.value }); state.error = null; state.errorAt = null; updateCardBadges() }
    if (target.dataset.leafField === 'count') {
      updateContainerCount(target)
      updateCardBadges()
    }
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

  // **`focusout`, not `blur`.** `blur` does not bubble, so a delegated listener
  // on `main` never hears it — and every other listener in this file is
  // delegated, because `render()` replaces `main.innerHTML` and a listener bound
  // to one of the four boxes would be thrown away with it on the next keystroke
  // anywhere on the step. This is the first `focusout` in `web/js/`.
  //
  // It tidies what is in the box — `1/1/2026` to `01/01/2026`, `8` to `08:00` —
  // and changes nothing that is sent; see `period.js`'s header. Like the `input`
  // handler above it is handed the rest of Calculate's condition, because the
  // rewrite re-asks `periodProblem` and patches the button by hand.
  main.addEventListener('focusout', event => {
    handlePeriodBlur(event, calculateOtherwiseDisabled())
  })

  // An IME composing into one of the period boxes must not have its text
  // reformatted mid-composition. `inputmode="numeric"` makes that unlikely; a
  // physical keyboard with an IME active makes it possible.
  main.addEventListener('compositionstart', handlePeriodComposition)
  main.addEventListener('compositionend', handlePeriodComposition)

  // Step 5's clock face. **Pointer events, so a mouse, a finger and a pen are
  // one code path** — see `period.js::handlePeriodPointer`.
  //
  // Delegated on `main` like everything else here, because `render()` replaces
  // `main.innerHTML` and a listener bound to the `<svg>` would be thrown away
  // with it. That survives a drag only because the dial deliberately does *not*
  // `setState` between `pointerdown` and `pointerup`: the `<svg>` holds the
  // pointer capture, so `pointermove` and `pointerup` are retargeted to it and
  // still bubble here however far outside the circle the pointer has gone.
  //
  // `pointermove` is bound unconditionally rather than added on `pointerdown`
  // and removed on `pointerup`, which would be one listener instead of a
  // permanent one. It is not worth it: the handler's first line is an identity
  // check against the captured pointer id and returns immediately, and a
  // listener added mid-gesture is a listener that leaks if the gesture ends in a
  // way nobody predicted.
  for (const type of ['pointerdown', 'pointermove', 'pointerup', 'pointercancel']) {
    main.addEventListener(type, handlePeriodPointer)
  }

  main.addEventListener('keydown', event => {
    // The calendar's own keyboard: the arrows, Page Up/Down, Home/End, Enter,
    // Space, Esc and the Tab cycle that keeps focus inside an `aria-modal`
    // dialog. It answers whether it took the key.
    if (handlePeriodKeydown(event)) return
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
