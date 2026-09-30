/**
 * The visitor's own answers, and the result they already saw, kept across a page load.
 * Contract §7.2a.
 *
 * ## The defect this closes
 *
 * The whole calculator was **one URL with one history entry**: `location.pathname` is
 * `/` from the introduction to the results, and which screen is up is `state.step`, in
 * memory. `pushState`, `replaceState` and `popstate` appeared nowhere in `web/js/` —
 * **which was a second defect, and `history.js` closes it (§7.2b): the step now has an
 * entry of its own, and Back moves within the calculator.** This module and that one
 * compose and neither depends on the other. So
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
 * ## Two keys, because the result is not an answer
 *
 * The result got a **second key** rather than a second section of the first one, and the
 * reason is the write cadence. `writeSnapshot` runs at every `continue`; the result and
 * the taxonomy beside it are measured at 32 KB against the running stack and change
 * exactly once per calculation. One document would mean carrying 32 KB through every
 * Continue press to avoid erasing it, or erasing it and losing the restore. Two keys
 * write what has actually changed, and a quota failure on the large one cannot take the
 * small one with it.
 *
 * They share **one version number**, so there is one integer to raise, and
 * `clearSnapshot` removes both — `resetCalculator` calls it once and needs to know
 * nothing about how many keys there are.
 *
 * **The result is only ever read alongside its answers.** `step` lives in the answers
 * document, so the results screen is reachable only through it; a result key found on its
 * own is a hand edit or an eviction and is ignored rather than restored into a state
 * nothing would render.
 *
 * ## A restored result is history, and history has its own taxonomy
 *
 * `submission` stamps `factor_set_id` so historical results stay reproducible, and every
 * §6.2 response carries its own `factor_set`. **So a restored result is re-shown under
 * the set that produced it and is never recomputed**, and a publish that lands while the
 * visitor is away takes effect on their *next* calculation. That is what makes the
 * taxonomy snapshot necessary: `results.js` reads the taxonomy to name metrics,
 * destinations, sectors and foods, and those are the names the calculation *used*.
 *
 * **This is a second use of the taxonomy pointing the opposite way from §6.1, and the two
 * are kept apart by name rather than by convention.** §6.1's rule is about the *form*:
 * fetched fresh on every load, cached across loads nowhere, because a stale one is "a form
 * offering codes the current set does not price". `state.taxonomy` is that one and stays
 * that one. The restored result reads `state.resultTaxonomy` through
 * `taxonomyForResult()` (`state.js`), and every site in `results.js` that renders the
 * calculation says which of the two it means. The improvement panel is a **form** — its
 * sliders offer destinations to allocate to and Compare Impact submits them — so it keeps
 * reading `state.taxonomy`, and a destination a publish retired is absent from it.
 *
 * ## One contribute flag of six, and why the other five are wrong to keep
 *
 * `RESULT_KEYS` carries `contributed` and nothing else about the control. A visitor who
 * ticked, submitted, left for the methodology page and came back must not be invited to
 * contribute again — the server dedupes on the token so no second row is written, but the
 * interface would be telling them it had forgotten. `contributeBlock` (`results.js`)
 * derives the whole of the done state from that one flag: the box reads back ticked and
 * disabled, Submit is not rendered, and `.contribute-status` speaks.
 *
 * The five that are not stored:
 *
 *   * **`contributing`** — a request in flight. The page load ended it, and whether it
 *     reached the server is unknowable from here. Restored `true` it is a control
 *     disabled forever with nothing left to finish it; restored `false` the visitor may
 *     press again, and §5.3's token upsert means a second press writes no second row. So
 *     `false` is both the honest answer and the safe one.
 *   * **`contributeArmedUntil`** — the five-second grace window. **The `setTimeout` that
 *     fires the request lives in `results.js` module scope and does not survive a page
 *     load**, so a restored deadline is a picture of a send that will never happen:
 *     `contributeWindowIsOpen` tests `> 0` and not `> Date.now()`, so a deadline that
 *     *expired* while the visitor was away would render a full countdown bar, an Undo
 *     button and a locked checkbox over a request nothing is going to make. A deadline
 *     still in the future is the same defect with a shorter fuse. Neither is stored, so
 *     the answer to "what happens to a countdown that expired while they were away" is
 *     that it cannot be restored at all — nothing was sent, and the control says so by
 *     being back where it was before the press.
 *   * **`contributeTicked`** — forced on by `contributed` where it matters, and on its own
 *     it is a control position rather than a decision. Left out for the same reason the
 *     answers whitelist leaves out a spinner.
 *   * **`contributeCelebrating`** — a one-shot animation tied to the transition into
 *     `contributed`, cleared by its own timeout. Replaying it on a page load would be the
 *     defect its own note in `state.js` exists to prevent.
 *   * **`contributeError`** — an error about a request that is over.
 *
 * `token` is not here either: it has rules of its own and keeps its own key. §6.2's
 * *response* does echo it, and `result` is stored as the response came, so a second copy
 * of it is in this document — the same value, in the same `sessionStorage`, for the same
 * lifetime, removed by the same `clearSnapshot`. No new information and no second
 * identifier.
 *
 * **The load-bearing guard for those five is `readResultSnapshot`'s own destructuring,
 * not this whitelist**, and that was measured rather than assumed: adding
 * `contributeArmedUntil` to `RESULT_KEYS` — and then also writing the document while a
 * grace window was open — left `tests/web/test_session_restore_browser.py::
 * test_an_armed_countdown_does_not_come_back` green both times, because the read names
 * the three keys it hands back and ignores anything else it finds. Only the third edit,
 * spreading the stored section into the patch, put the countdown back on the screen. So
 * **a key added here does nothing until it is added there too**, which is the safe
 * direction, and it is why the whitelist is worth keeping even though it is the belt
 * rather than the braces.
 *
 * **The improvement panel is not stored.** `improvementOpen`, `improvedAllocations` and
 * `improvementResult` are absent, so a restored results page has the panel closed. That
 * is a decision and not an omission: Compare Impact is a *new* submission under the
 * currently published set, so its editor and its comparison belong to the load that ran
 * them, and restoring a comparison computed under one set beside allocations offered by
 * another is the one thing this distinction exists to prevent.
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

/** The answers key, beside `kaiCalculatorToken`. */
export const SNAPSHOT_KEY = 'kaiCalculatorAnswers'

/**
 * The result key. Separate from the answers for the reason in the note above: the answers
 * are rewritten at every `continue` and this document changes once per calculation.
 */
export const RESULT_KEY = 'kaiCalculatorResult'

/**
 * The schema number, **shared by both keys** so there is one integer to raise and no way
 * for the two documents to disagree about which deployment wrote them.
 *
 * **Raise it whenever the meaning of anything in `ANSWER_KEYS` or `RESULT_KEYS` changes**
 * — a key renamed, a value space narrowed, a nested shape altered. A raise discards every
 * snapshot written before it, which costs one visitor one restore and is the cheap half of
 * the trade.
 *
 * **Storing the result did not raise it, and that is a decision.** `step` gained one
 * reachable value in practice — a stored 5 is now the results screen rather than something
 * clamped to the review step — but the old document degrades correctly rather than being
 * half-read: a v1 snapshot written before this revision has no result key, `readResultSnapshot`
 * answers `null`, and `restoredPatch` clamps the 5 exactly as it did before. The criterion
 * for a raise is that an older document could be *misread*, not that a newer one says more,
 * and raising it here would have thrown away every in-flight visitor's restore for nothing.
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

/**
 * Exactly what the result document carries, and nothing else.
 *
 * Three keys, each earning its place:
 *
 *   * **`result`** — the §6.2 response with `entry_results` beside it (`entryResultsFrom`,
 *     §7.2). Every figure in it is a string, and this module is why they are still strings
 *     when they come back.
 *   * **`resultTaxonomy`** — §6.1's response **as it was when the calculation ran**, stored
 *     whole rather than reduced to the rows the result names. A reduction would be a
 *     transformation, and what this key promises is the taxonomy that produced the result,
 *     not a summary of it. It is 9.3 KB measured against the running stack.
 *   * **`contributed`** — see the note above for why this one of six, and why the other
 *     five would each be a lie about something the page load ended.
 *
 * A whitelist for `ANSWER_KEYS`' own reason: a forbidden list would have to be edited every
 * time somebody adds a flag to the results page, and `pdfExporting`, `pdfError`,
 * `resultBreakdownTab`, `resultsNavOpen` and the six improvement keys are all absent by
 * construction rather than by anybody remembering.
 */
export const RESULT_KEYS = ['result', 'resultTaxonomy', 'contributed']

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
 * **Every nested read inside `pruneAnswers` goes through one of these two.**
 *
 * The version number rules out another deployment's schema; it does not rule out a hand
 * edit, and `pruneAnswers` runs inside `loadTaxonomy`'s own `try`. So `.filter` on a
 * string there is not a broken restore — it is caught as a *taxonomy* failure and the
 * visitor is shown "Calculator unavailable", which is a working calculator reporting
 * itself broken because of something in their own browser. These two make a wrong type
 * read as an empty one, which is what the visitor would have had anyway.
 */
const asList = value => (Array.isArray(value) ? value : [])
const asMap = value => (isObject(value) ? value : {})

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

/**
 * Write the result the visitor is looking at, the taxonomy that produced it, and their
 * contribute choice. Never throws, and never reports a failure to the caller.
 *
 * **Called where the displayed result changes, and nowhere else** — twice:
 *
 *   * `submitCalculation` (`calculator.js`), after the success `setState`, which is the
 *     one moment `state.result` and `state.resultTaxonomy` become the thing on screen.
 *     The answers snapshot is rewritten in the same breath, because `step` has just become
 *     5 and the copy written by the Calculate press itself still says 4.
 *   * `contributeCalculation` (`results.js`), on success, which is the only other moment
 *     anything in `RESULT_KEYS` changes. Without it a visitor who contributed and then
 *     left would come back to a page offering to contribute again — the row is already
 *     flagged, so nothing would be double-counted, but the interface would be saying it
 *     had forgotten what they did.
 *
 * A *failed* contribute writes nothing, and does not need to: `contributed` is still
 * false, which is what is already stored.
 *
 * @param {object} state The live state object; only `RESULT_KEYS` are read off it.
 */
export function writeResultSnapshot(state) {
  try {
    const calculation = {}
    for (const key of RESULT_KEYS) calculation[key] = state[key]
    // `JSON.stringify` and nothing else, for `writeSnapshot`'s reason: every metric total,
    // every `qty_kg` and every money figure inside `result` is a string (§1.2) and stays
    // one. No traversal here parses, formats or rounds anything at any depth.
    sessionStorage.setItem(RESULT_KEY, JSON.stringify({ version: SNAPSHOT_VERSION, calculation }))
  } catch {
    // Private window, blocked site data, or the quota — and this is the larger of the two
    // documents, so it is the one a quota refuses first. The results page is on screen
    // either way; only coming back to it is lost.
  }
}

/**
 * Remove both snapshots. Paired with the token's own removal in `resetCalculator`.
 *
 * **Both, from one call.** `resetCalculator` asks for "Clear all calculator data" and must
 * not have to know how many keys that is; a second key added and not removed here is
 * exactly how that question becomes a false statement.
 */
export function clearSnapshot() {
  try {
    sessionStorage.removeItem(SNAPSHOT_KEY)
    sessionStorage.removeItem(RESULT_KEY)
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
 * The stored result as a `setState` patch, or `null`.
 *
 * `null` for every reason `readSnapshot` answers `null` for, and for three more of its own,
 * each of which is a document that would render a broken results page rather than no page:
 *
 *   * **`result.entry_results` is not a non-empty array.** `renderResults` returns
 *     "Results unavailable" on an empty one, which is a dead end with the visitor's answers
 *     still in the form behind it — so an unusable result is answered `null` here instead,
 *     `restoredPatch` clamps the step back to the review screen, and Calculate is one press
 *     away.
 *   * **`result.totals` or `result.factor_set` is not an object.** `totals` is where every
 *     headline figure comes from and `factor_set.is_mock` is what raises the mandatory
 *     placeholder banner; a result carrying neither is not the response §6.2 defines.
 *   * **`resultTaxonomy` is not an object.** `taxonomyForResult` hands it straight to
 *     `findByCode(taxonomy.destinations, …)`, and `null.destinations` is a `TypeError`
 *     inside `render()`, which is a blank page.
 *
 * **Read and validated here, rather than trusted because the version matched.** The version
 * rules out another deployment's schema; it does not rule out a hand edit, and this document
 * is the larger and more inviting of the two.
 *
 * Beyond those three the contents are **not** walked. Every name on the results page goes
 * through `findByCode`, which is `(items || []).find(…)`, so a missing or malformed list
 * degrades to the code rather than throwing — which is the same fallback §5.2 already
 * requires for a row retired after a submission named it.
 *
 * @returns {{result: object, resultTaxonomy: object, contributed: boolean}|null}
 */
export function readResultSnapshot() {
  let raw = null
  try {
    raw = sessionStorage.getItem(RESULT_KEY)
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
  if (!isObject(parsed) || parsed.version !== SNAPSHOT_VERSION || !isObject(parsed.calculation)) return null
  const { result, resultTaxonomy, contributed } = parsed.calculation
  if (!isObject(result) || !Array.isArray(result.entry_results) || !result.entry_results.length) return null
  if (!result.entry_results.every(isObject)) return null
  if (!isObject(result.totals) || !isObject(result.factor_set)) return null
  if (!isObject(resultTaxonomy)) return null
  // `contributed` is coerced rather than shape-checked out: the durable half of the
  // contribute control is a yes or a no, and anything that is not an explicit `true` is a
  // no. A missing flag is the ordinary case for a visitor who never touched the box.
  return { result, resultTaxonomy, contributed: contributed === true }
}

/**
 * A stored step number, or `null` where there is nothing usable in it.
 *
 * **Two sources restore a step and they must clamp identically** (§7.2b): the answers
 * document, through `restoredPatch` below, and the step the *history entry* the page arrived
 * on records, which `main.js` prefers because it names the screen the visitor was last
 * looking at rather than the furthest one a checkpoint saw. Written once here so the two
 * cannot disagree about what a 5 means or about what is out of range.
 *
 * **5 clamps to 4 only when there is no result to render.** The results screen renders from
 * `state.result`, so a step 5 restored without one would paint "Results unavailable" over
 * answers that are perfectly intact; with one it stands, and the visitor comes back to the
 * figures they were looking at.
 *
 * @param {*} step Whatever was stored — any type
 * @param {object|null} [result] `readResultSnapshot()`'s return, or `state.result`
 * @returns {number|null}
 */
export function restorableStep(step, result = null) {
  if (!Number.isInteger(step) || step < -1 || step > 5) return null
  return step === 5 && !result ? 4 : step
}

/**
 * The stored answers as a `setState` patch, with each value's own shape checked.
 *
 * **Shape-checked rather than trusted.** The version number rules out another
 * deployment's schema; it does not rule out a hand-edited value, and one wrong type here
 * is a `TypeError` inside `render()`, which is a blank page. Anything of the wrong shape
 * is simply left out of the patch, so `state.js`'s own initial value stands.
 *
 * **`step` is clamped, and 5 is clamped to 4 only when there is no result to render.**
 * The results screen renders from `state.result`, so a step 5 restored without one would
 * paint "Results unavailable" over answers that are perfectly intact. `result` is
 * `readResultSnapshot()`'s return — it has already refused every document that would not
 * render — so a truthy one means step 5 stands and the visitor comes back to the figures
 * they were looking at, and a `null` one means the review step they calculated from, with
 * Calculate one press away.
 *
 * @param {object} answers `readSnapshot()`'s return
 * @param {object|null} [result] `readResultSnapshot()`'s return
 * @returns {object} a patch for `setState`
 */
export function restoredPatch(answers, result = null) {
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
  const step = restorableStep(answers.step, result)
  if (step !== null) patch.step = step
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
 *   * **A RETIRED category or item takes its figures with it; an unticked one does not.**
 *     `state.js` deliberately *parks* the figures of an unticked category so that
 *     re-ticking restores them, and `draftEntry()` prunes at the request boundary — so
 *     this function keys on what the taxonomy has, never on what is currently ticked. A
 *     code the vocabulary no longer has can never be re-ticked, so its record is
 *     unreachable forever and is removed; a code that is merely unticked is parked
 *     exactly as it is in memory, and survives the page load with everything else.
 *     Getting this the wrong way round loses money figures a visitor typed, silently,
 *     for a category that is still priced.
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
    const keptCategories = asList(chain.foodCategories).filter(code => {
      if (categories.has(code)) return true
      drop(DROPPED_FOOD_CATEGORY, code)
      return false
    })
    const keptItems = {}
    for (const category of keptCategories) {
      keptItems[category] = asList(asMap(chain.foodItems)[category]).filter(code => {
        if (itemParent.get(code) === category) return true
        drop(DROPPED_FOOD_ITEM, code)
        return false
      })
    }
    // **A figures record is dropped when the TAXONOMY no longer has its leaf, never
    // because the visitor has unticked it.** That distinction is the whole of this
    // loop and getting it the other way round loses work.
    //
    // §7.2 *parks* the figures of an unticked category so that re-ticking hands them
    // back, and `draftEntry()` prunes at the request boundary so nothing parked is ever
    // sent. An earlier version of this function kept only the records belonging to the
    // chain's **live leaves** — which meant a visitor who ticked two categories, filled
    // both, unticked one and pressed Continue lost the unticked one's money figures at
    // the next page load, silently, with the category still perfectly well priced. The
    // prune is not the place that decides what is in use.
    //
    // A key is `category` NUL `item` (`leafKey`, §7.2). Written out here rather than
    // imported from `state.js`, which imports this module: one line of duplication
    // against a cycle, and `tests/web/test_snapshot.py` pins the two together. The
    // category-less record's key is a bare NUL and passes both tests, which is correct —
    // unticking every category brings that leaf back (`entryLeaves`).
    const keptFigures = {}
    for (const [key, figures] of Object.entries(asMap(chain.leafFigures))) {
      const [category, item] = String(key).split('\u0000')
      if (category && !categories.has(category)) continue
      if (item && itemParent.get(item) !== category) continue
      const preset = figures?.unitPreset
      const presetGone = preset && !presets.has(preset)
      if (presetGone) drop(DROPPED_UNIT_PRESET, preset)
      keptFigures[key] = {
        ...asMap(figures),
        ...(presetGone ? { unitPreset: null, measureMode: 'mass' } : {}),
        current: asList(figures?.current).filter(line => {
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
  for (const entry of asList(answers.entries)) {
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
