/**
 * The visitor's own answers, kept across a page load. Contract §7.2a.
 *
 * ## The defect this closes
 *
 * The whole calculator is **one URL with one history entry**: `location.pathname` is
 * `/` from the introduction to the results, and which screen is up is `state.step`, in
 * memory. `pushState`, `replaceState` and `popstate` appear nowhere in `web/js/`. So
 * following the header's *Documentation* or *Statistics* link and pressing Back
 * re-parses the page — measured by registering a `pageshow` listener before leaving and
 * finding it gone on return, so the JS heap is genuinely new — and the visitor landed on
 * the introduction screen with everything they had typed gone. `nginx` serves
 * `Cache-Control: no-cache` rather than `no-store`, so the response header is not what
 * disables the back-forward cache and changing it would not have helped.
 *
 * Nothing but the token was persisted: `state.js` read exactly one stored value,
 * `sessionStorage.getItem('kaiCalculatorToken')`. This module adds the second, and the
 * second is the answers.
 *
 * ## `sessionStorage`, one key, one version number
 *
 * **`sessionStorage` and not `localStorage`**, which is the same scope the token already
 * uses: per tab, and gone when the tab closes. `localStorage` survives a tab close and is
 * shared between every tab of the origin, which is wider than anything here needs — a
 * visitor who closes the tab has finished, and the next person to open one on that
 * machine must not be handed somebody else's figures.
 *
 * **One key holding one JSON object, stamped with `SNAPSHOT_VERSION`.** A deployment ships
 * new state keys and renames old ones; a snapshot written by an older deployment and
 * read field-by-field by a newer one is a form half-filled from a shape nobody checked.
 * The version is compared first and a mismatch **discards the whole snapshot silently** —
 * silently because the visitor did nothing wrong and there is nothing for them to act on,
 * unlike the revalidation below, which is about answers they chose.
 *
 * ## Nothing transient, and it is a whitelist for that reason
 *
 * `ANSWER_KEYS` names what is written. It is deliberately a whitelist rather than a copy
 * of `state` minus a forbidden list: the forbidden list would have to be edited every
 * time somebody adds a spinner flag, and forgetting is a restored open dialog or a
 * restored error about a request that finished before the visitor left. A whitelist fails
 * the safe way — a new key is simply not stored until somebody decides it should be.
 *
 * So `loading`, `error`, `errorAt`, `errorCode`, `fieldErrors`, `rateLimitedUntil`,
 * `pdfExporting`, `pdfError`, `periodPicker` and `periodClock` are absent by
 * construction. `returnTo` is absent too and that is a decision rather than an omission:
 * it holds a snapshot of entries and a draft *as they were before a jump*, and restoring
 * one would let Back reinstate entries that never went through the revalidation below —
 * the one door this module must not leave open.
 *
 * **`result`, `taxonomy` and the contribute flags are also absent, and that is this
 * package's boundary rather than an oversight.** A restored *result* is a record of a
 * calculation that already happened and has to be rendered from the taxonomy that
 * produced it, which is a second use of the taxonomy pointing the opposite way from
 * §6.1's "fetched fresh, every load". That distinction is worth writing down properly and
 * is not written down yet, so this module stores answers only and `restoredPatch` clamps
 * a stored `step` of 5 back to the review step: a results screen with `result: null`
 * renders nothing at all.
 *
 * ## Decimals survive untouched
 *
 * §1.2 puts decimals on the wire as **strings** because JavaScript's `Number` is a
 * double, and every quantity in `leafFigures` is the raw string the visitor typed.
 * `JSON.stringify`/`JSON.parse` preserve a JSON string exactly, so the round trip is
 * lossless **as long as nothing in it calls `Number`** — and nothing here does, at any
 * depth. A figure that came back as a float would be this contract's oldest rule broken
 * by a cache, which is why `tests/web/test_snapshot.py` asserts the type and the
 * trailing zeros rather than the value.
 *
 * ## Storage may refuse, and the form does not depend on it
 *
 * A private window, blocked site data or a full quota makes every call below throw. Every
 * one is wrapped, a failed write is dropped, and a failed read is "no snapshot". **This
 * feature must never become a precondition for the form working**: a visitor whose
 * browser refuses storage gets the calculator exactly as it behaved before this module
 * existed.
 *
 * This module imports nothing, which is what lets `state.js` import it — `state.js` is
 * the module every other one may import, and that is only true while what it imports
 * imports nothing back.
 *
 * @module snapshot
 */

/** The one `sessionStorage` key, beside `kaiCalculatorToken`. */
export const SNAPSHOT_KEY = 'kaiCalculatorAnswers'

/**
 * The schema number. **Raise it whenever the meaning of anything in `ANSWER_KEYS`
 * changes** — a key renamed, a value space narrowed, a nested shape altered. A raise
 * discards every snapshot written before it, which costs one visitor one restore and
 * is the cheap half of the trade.
 */
export const SNAPSHOT_VERSION = 1

/**
 * Exactly what is written, and nothing else.
 *
 * `leafFigures` and `totalUnit` are here although neither is named in the work
 * package's own list, and both have to be: `leafFigures` **is** the draft's "per-leaf
 * figures", and `state.totalUnit` is the unit the chain's combined figures are stated in
 * (fixed at the 3 → 4 move and never re-derived). Restore step 4 without it and a chain
 * whose rows were typed in tonnes is laid out against a total in kilograms — a
 * thousandfold error on a screen that looks entirely normal, which is the same hazard
 * the note over `totalUnit` in `state.js` documents.
 *
 * `foodStage` is here so a visitor who left from step 2.5 comes back to step 2.5 rather
 * than to the category list they had already answered. It is safe to restore because
 * `render()` re-asks `itemStepOffered()` on every render, so a stage left at `'items'`
 * cannot strand anybody on a panel this taxonomy has nothing to put in.
 */
export const ANSWER_KEYS = [
  'sector',
  'gwpHorizon',
  'foodCategories',
  'foodUnspecified',
  'foodItems',
  'foodStage',
  'totalUnit',
  'leafFigures',
  'entries',
  'timeFrame',
  'periodStart',
  'periodEnd',
  'periodFields',
  'step',
]

/** The five kinds of thing a restored answer can name, and so the five labels the notice
 *  prints. Each label is an existing catalogue key — see `DROPPED_LABELS` and
 *  `restoreNotice` in `calculator.js`, which is where they meet `t()`; this module renders
 *  nothing and imports no `t`. */
export const DROPPED_SECTOR = 'sector'
export const DROPPED_FOOD_CATEGORY = 'food_category'
export const DROPPED_FOOD_ITEM = 'food_item'
export const DROPPED_DESTINATION = 'destination'
export const DROPPED_UNIT_PRESET = 'unit_preset'

const isObject = value => Boolean(value) && typeof value === 'object' && !Array.isArray(value)
const codeSet = rows => new Set((Array.isArray(rows) ? rows : []).map(row => row?.code))

/**
 * Write the answers. Never throws, and never reports a failure to the caller.
 *
 * Called at the visitor's own checkpoints — every `continue` and `calculate` — rather
 * than on every `setState`, because `render()` replaces `main.innerHTML` on every state
 * change and `period.js` deliberately does not `setState` while a date is being typed.
 * A write per keystroke would have to be fed by a `setState` per keystroke, which is the
 * one thing that module is written to avoid.
 *
 * @param {object} state The live state object; only `ANSWER_KEYS` are read off it.
 */
export function writeSnapshot(state) {
  try {
    const answers = {}
    for (const key of ANSWER_KEYS) answers[key] = state[key]
    // `JSON.stringify` and nothing else. Every decimal below is already a string and
    // stays one; no traversal here parses, formats or rounds anything.
    sessionStorage.setItem(SNAPSHOT_KEY, JSON.stringify({ version: SNAPSHOT_VERSION, answers }))
  } catch {
    // Private window, blocked site data, or the quota. The calculator goes on working
    // without a snapshot, which is the whole of the fallback.
  }
}

/** Remove the snapshot. Paired with the token's own removal in `resetCalculator`. */
export function clearSnapshot() {
  try {
    sessionStorage.removeItem(SNAPSHOT_KEY)
  } catch {
    // As above: a browser that will not let us remove it will not have let us write it.
  }
}

/**
 * The stored answers, or `null`.
 *
 * `null` for every reason: nothing stored, storage unavailable, unparseable JSON, a
 * version this deployment does not write, or a top level that is not the shape this
 * module writes. **A wrong version is discarded whole and silently** — reading half of
 * a shape nobody checked is exactly what the version number exists to prevent.
 *
 * @returns {object|null} The answers object, with only recognised keys present.
 */
export function readSnapshot() {
  let raw = null
  try {
    raw = sessionStorage.getItem(SNAPSHOT_KEY)
  } catch {
    return null
  }
  if (!raw) return null
  let parsed = null
  try {
    parsed = JSON.parse(raw)
  } catch {
    return null
  }
  if (!isObject(parsed) || parsed.version !== SNAPSHOT_VERSION || !isObject(parsed.answers)) return null
  const answers = {}
  for (const key of ANSWER_KEYS) if (key in parsed.answers) answers[key] = parsed.answers[key]
  return Object.keys(answers).length ? answers : null
}

/**
 * The stored answers as a `setState` patch, with each value's own shape checked.
 *
 * **Shape-checked rather than trusted.** The version number rules out another
 * deployment's schema; it does not rule out a hand-edited value, and one wrong type here
 * is a `TypeError` inside `render()`, which is a blank page. Anything of the wrong shape
 * is simply left out of the patch, so `state.js`'s own initial value stands.
 *
 * **`step` is clamped, and 5 is clamped to 4.** The results screen renders from
 * `state.result`, which this package does not store, so a restored step 5 would render
 * a results page with no result in it. The review step is where that visitor left the
 * form, and Calculate is one press away.
 *
 * @param {object} answers `readSnapshot()`'s return
 * @returns {object} a patch for `setState`
 */
export function restoredPatch(answers) {
  const patch = {}
  const string = key => {
    if (typeof answers[key] === 'string') patch[key] = answers[key]
  }
  if (typeof answers.sector === 'string' || answers.sector === null) patch.sector = answers.sector
  if (answers.gwpHorizon === 20 || answers.gwpHorizon === 100) patch.gwpHorizon = answers.gwpHorizon
  if (Array.isArray(answers.foodCategories)) patch.foodCategories = answers.foodCategories.filter(code => typeof code === 'string')
  if (typeof answers.foodUnspecified === 'boolean') patch.foodUnspecified = answers.foodUnspecified
  if (isObject(answers.foodItems)) patch.foodItems = answers.foodItems
  if (answers.foodStage === 'categories' || answers.foodStage === 'items') patch.foodStage = answers.foodStage
  if (answers.totalUnit === 'kilograms' || answers.totalUnit === 'tonnes') patch.totalUnit = answers.totalUnit
  if (isObject(answers.leafFigures)) patch.leafFigures = answers.leafFigures
  if (Array.isArray(answers.entries)) patch.entries = answers.entries.filter(isObject)
  string('timeFrame')
  string('periodStart')
  string('periodEnd')
  if (isObject(answers.periodFields)) {
    const fields = {}
    for (const field of ['startDate', 'startTime', 'endDate', 'endTime']) {
      fields[field] = typeof answers.periodFields[field] === 'string' ? answers.periodFields[field] : ''
    }
    patch.periodFields = fields
  }
  if (Number.isInteger(answers.step) && answers.step >= -1 && answers.step <= 5) patch.step = Math.min(answers.step, 4)
  return patch
}

/**
 * Check the restored answers against a **freshly fetched** taxonomy and drop what it no
 * longer has.
 *
 * ## Why this exists at all
 *
 * §6.1 rules that the taxonomy is fetched once per page load and cached across loads
 * nowhere, because a taxonomy fetched before a publish is *"a form offering codes the
 * current set does not price"*. A restored answer naming a destination a publish has
 * since retired is **that same hazard arriving by another door**: the form would be
 * holding a code the current set cannot price, and the server would refuse the
 * submission with `UNKNOWN_CODE` at the end of the flow if anything refused it at all.
 *
 * So the order is fixed and `main.js` implements it in this order: restore the answers,
 * fetch the taxonomy fresh, check the answers against it, drop what no longer exists and
 * **say so on screen**. Silently dropping a category or a destination the visitor chose
 * is the failure this step exists to prevent, so every drop is reported and the caller
 * renders the list.
 *
 * ## What is checked, and against which list
 *
 * Five kinds of code can reach here, and each is checked against the list the form would
 * have offered it from:
 *
 *   * `sector` against `sectors`.
 *   * `foodCategories` against `food_categories`.
 *   * `foodItems` against `food_items`, **and against the parent the taxonomy now gives
 *     that item**: step 2.5 groups foods under their category, so an item re-parented by
 *     a publish would be restored under a heading it no longer belongs to.
 *   * every `current[].destination` against the destinations the form offers, which is
 *     the destinations **minus the prevention ones** — `entryDestinations` in
 *     `calculator.js` is that same filter, and §6.2 refuses a flagged destination in a
 *     *current* scenario outright, so a row naming one could never be submitted.
 *   * `unitPreset` against `unit_presets`. A container whose preset is gone falls back to
 *     a mass measurement, because the count alone means nothing without the `kg_per_unit`
 *     that converted it.
 *
 * ## What happens to what is left
 *
 *   * **A dropped category or item takes its parked figures with it.** `state.js`
 *     deliberately *parks* the figures of an unticked category so re-ticking restores
 *     them, and `draftEntry()` prunes at the boundary. A code the vocabulary no longer
 *     has can never be re-ticked, so its record is unreachable forever and is removed
 *     rather than parked.
 *   * **A committed entry whose sector is gone is dropped whole.** `sector` is the one
 *     answer an entry cannot be without — `submission_entry.sector_id` is NOT NULL — so
 *     an entry with a null sector is an entry that can only ever be refused. Its loss is
 *     reported like any other.
 *   * **A draft whose sector is gone sends the visitor back to step 0**, which is the
 *     screen that asks the question they now have to answer again.
 *
 * Pure: it reads the two arguments and returns a patch. No storage, no `t()`, no DOM.
 *
 * @param {object} answers The restored answers, as they now sit on `state`
 * @param {object} taxonomy §6.1's response, fetched on this page load
 * @returns {{patch: object, dropped: Array<{kind: string, code: string}>}}
 */
export function pruneAnswers(answers, taxonomy) {
  const sectors = codeSet(taxonomy?.sectors)
  const categories = codeSet(taxonomy?.food_categories)
  const presets = codeSet(taxonomy?.unit_presets)
  const offeredDestinations = codeSet((taxonomy?.destinations || []).filter(row => !row?.is_prevention))
  const itemParent = new Map((taxonomy?.food_items || []).map(row => [row?.code, row?.food_category]))

  const dropped = []
  const drop = (kind, code) => {
    if (!dropped.some(entry => entry.kind === kind && entry.code === code)) dropped.push({ kind, code })
  }

  /** One chain's codes, pruned. Used for the draft and for every committed entry, because
   *  a committed entry is the draft's own shape and the two must not diverge here. */
  const pruneChain = chain => {
    const keptCategories = (chain.foodCategories || []).filter(code => {
      if (categories.has(code)) return true
      drop(DROPPED_FOOD_CATEGORY, code)
      return false
    })
    const keptItems = {}
    for (const category of keptCategories) {
      keptItems[category] = ((chain.foodItems || {})[category] || []).filter(code => {
        if (itemParent.get(code) === category) return true
        drop(DROPPED_FOOD_ITEM, code)
        return false
      })
    }
    // The leaf keys that survive, in `leafKey`'s own NUL-joined form. Written out here
    // rather than imported from `state.js`, which imports this module: one line of
    // duplication against a cycle, and `tests/web/test_snapshot.py` pins the two together.
    const live = new Set()
    for (const category of keptCategories) {
      const items = keptItems[category]
      if (items.length) for (const item of items) live.add(`${category}\u0000${item}`)
      else live.add(`${category}\u0000`)
    }
    // **The category-less leaf is always live**, because unticking every category brings
    // it back (`entryLeaves`) — so its record is reachable and is parked, not pruned. That
    // is the difference this loop turns on: a retired code can never be re-ticked, and a
    // record only that code could reach is unreachable forever.
    live.add('\u0000')
    const keptFigures = {}
    for (const [key, figures] of Object.entries(chain.leafFigures || {})) {
      if (!live.has(key)) continue
      const preset = figures?.unitPreset
      const presetGone = preset && !presets.has(preset)
      if (presetGone) drop(DROPPED_UNIT_PRESET, preset)
      keptFigures[key] = {
        ...figures,
        ...(presetGone ? { unitPreset: null, measureMode: 'mass' } : {}),
        current: (figures?.current || []).filter(line => {
          if (offeredDestinations.has(line?.destination)) return true
          drop(DROPPED_DESTINATION, line?.destination)
          return false
        }),
      }
    }
    return { ...chain, foodCategories: keptCategories, foodItems: keptItems, leafFigures: keptFigures }
  }

  const draft = pruneChain({
    foodCategories: answers.foodCategories,
    foodItems: answers.foodItems,
    leafFigures: answers.leafFigures,
  })
  const sectorGone = Boolean(answers.sector) && !sectors.has(answers.sector)
  if (sectorGone) drop(DROPPED_SECTOR, answers.sector)

  const entries = []
  for (const entry of answers.entries || []) {
    if (entry?.sector && !sectors.has(entry.sector)) {
      drop(DROPPED_SECTOR, entry.sector)
      continue
    }
    entries.push(pruneChain(entry))
  }

  const patch = {
    foodCategories: draft.foodCategories,
    foodItems: draft.foodItems,
    leafFigures: draft.leafFigures,
    entries,
  }
  if (sectorGone) {
    patch.sector = null
    // Step 0 asks for the sector, so that is where a visitor who no longer has one has to
    // stand. Clamped rather than left alone: steps 3 and 4 are laid out per leaf and read
    // fine without a sector, so nothing downstream would have told them.
    patch.step = 0
  }
  return { patch, dropped }
}
