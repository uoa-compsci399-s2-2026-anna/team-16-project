/**
 * One history entry per step, so Back and Forward move *inside* the calculator.
 * Contract §7.2b.
 *
 * ## The defect this closes
 *
 * `pushState`, `replaceState` and `popstate` appeared **zero times** in `web/js/`.
 * `location.pathname` is `/` from the introduction to the results and which screen is up
 * is `state.step`, in memory, so the whole wizard was one history entry: pressing Back on
 * step 4 did not go to step 3, it left the calculator altogether and landed on whatever
 * the visitor was looking at before they arrived. §7.2a gave the answers back across that
 * press; it could not stop the press leaving.
 *
 * ## One URL, and the step in `history.state` rather than in it
 *
 * **The step is in `history.state` and the URL never changes.** `pushState` is called with
 * no URL argument, so every entry is `/index.html` exactly as the page was served.
 *
 * A `?step=` or `#step-3` was considered and refused, because a URL is *for* being copied
 * and this one could not be honoured:
 *
 *   * **A deep link cannot deliver the screen it names.** Step 3 asks how much waste there
 *     was *for the categories the visitor chose*; step 4 allocates *their* total. None of
 *     that is in the URL, and putting it there is the one thing §7.2a's own privacy note
 *     rules out — a URL is pasted into chats and written into intermediaries' logs, and the
 *     answers are what this project takes care not to spread. So a pasted `/?step=3` would
 *     have to be clamped to the introduction, which makes the parameter a lie in exactly
 *     the case a URL exists for.
 *   * **`history.state` has the right scope and the right lifetime.** It is per entry, per
 *     tab, invisible, uncopyable, and it survives a reload and a document discard — which
 *     is what makes the restore below work. It is the same lifetime as the `sessionStorage`
 *     documents holding the answers, so the two cannot disagree about how long they last.
 *
 * Refreshing is identical either way: `history.state` survives F5, so a refresh comes back
 * to the same step from the same two sources as a Back press.
 *
 * ## History is a projection of `state.step`; the control is the source of truth
 *
 * Two answers to "where does Back go" existed already — `backTarget` (`calculator.js`),
 * which reads `state.returnTo` so that a review-step *Edit* returns to the review step
 * rather than stepping back one — and a browser Back is a third. They are kept from
 * disagreeing by construction rather than by being compared:
 *
 *   1. **Nothing but a step change writes history.** The push happens in one subscriber,
 *      off `state.step`, so `render()` running on every `setState` — every keystroke, every
 *      tick, every breakdown tab — cannot produce an entry. A re-render is not a
 *      navigation, and neither is the one step change the *page* performs rather than the
 *      visitor: see `withoutAnEntry`.
 *   2. **Every entry therefore holds the step the control moved to**, and its predecessor
 *      holds the step the control moved *from*. `backTarget` is *also* "the step the
 *      visitor came from": `state.returnTo.step` records where they stood when they
 *      jumped, and `step - 1` is where a plain walk came from. So the previous entry and
 *      `backTarget(state.step)` are the same number — not checked to be, but the same fact
 *      recorded twice.
 *   3. **A traversal runs the control's own function.** `popstate` calls the same
 *      `goToStep` the on-screen Back calls, so the jump undo, the discard confirmation and
 *      the `foodStage` reset all happen identically whichever Back was pressed. A visitor
 *      who declines the confirmation has the history position put back (`traverse` below),
 *      because a `popstate` has already moved it and cannot be cancelled.
 *
 * `popstate` is the only thing that writes `state.step` from history, and the subscriber is
 * the only thing that writes history from `state.step`. One direction each way.
 *
 * ## The cost, stated rather than discovered
 *
 * **A visitor who walked to the results page now needs seven Back presses to leave the
 * calculator** — results, review, destinations, amount, food type, stage, introduction,
 * out. That is the ordinary price of an entry per step and it is worth paying: the presses
 * it adds are ones that land on a screen the visitor recognises, and the press it fixes was
 * the one that threw a finished calculation away. Anybody who wants *out* has the three
 * header links, which are one click from anywhere.
 *
 * ## `start-over` unwinds rather than replacing
 *
 * "Clear all calculator data and return to the introduction?" must not leave six entries
 * behind Back, each naming a step of a calculation that no longer exists. Entries cannot be
 * deleted, so `unwindStepHistory` **travels back to the entry the calculator opened in**
 * (`kaiIndex`, a counter this module keeps inside `history.state` so it survives the
 * document being discarded) and rewrites it as the introduction. Back from there leaves the
 * site, which is what it did before the visitor started.
 *
 * Forward is still a live direction after that, so the entries above are not left to
 * mislead either: a traversal to a step the calculator cannot draw — anything past the
 * stage question with no sector and no saved entry — is answered with the introduction and
 * the entry is **rewritten** to say so (`goToStepFromHistory` in `calculator.js`, and
 * `recordStepChange` below is what rewrites it).
 *
 * ## Inert until installed, and out of service if the browser refuses
 *
 * **Nothing here touches `history` at module load**, and nothing *changes* it until
 * `installStepHistory` has run: `unwindStepHistory` returns at once and `withoutAnEntry`
 * simply runs what it is given. `state.js` calls the first of those from `resetCalculator`,
 * and `tests/web/test_snapshot.py` runs that function under Node with no `history` object at
 * all — a page that never installed a step history has none to unwind. (`stepFromHistory`
 * is the one export that does read, before the install by design, and it answers `null`
 * where there is nothing to read.)
 *
 * **And a browser that refuses to write history must leave a working calculator**, which is
 * `state.js`'s rule for `sessionStorage` applied here. Every write goes through `attempt`,
 * which takes this module out of service rather than letting a `SecurityError` out of
 * `main.js`'s module evaluation — see its own note for what that costs and why it is not
 * optional.
 */

/** The step this entry shows. */
const STEP_KEY = 'kaiStep'
/**
 * How far this entry is from the one the calculator opened in.
 *
 * Two things need it and neither can be derived: `unwindStepHistory` has to know how many
 * entries to travel back over, and `traverse` has to know which *direction* a refused
 * traversal came from in order to put the position back — the step numbers cannot say,
 * because Back from step 0 after *Add another entry* goes forward in number to the review
 * step. It lives in `history.state` rather than in a module variable so that it survives
 * the document being discarded and re-parsed, which is precisely the case the restore is
 * about.
 */
const INDEX_KEY = 'kaiIndex'

let installed = false
let readStep = () => -1
let navigate = () => true

/** The step the entry the visitor is standing on records, and that entry's index. */
let recordedStep = null
let recordedIndex = 0

/**
 * A traversal is being applied, so the step change it causes must not push a second entry
 * for the entry it came from.
 */
let applying = false

/**
 * `popstate`s this module asked for, and what to do when each lands.
 *
 * `history.go` is asynchronous and reports nothing, so a position repair and the
 * `start-over` unwind are both "call `go`, then finish the job in the `popstate` it
 * produces". A queue rather than a boolean so that two of them in flight cannot be
 * mistaken for one.
 *
 * **What it cannot tell apart is a visitor's own press arriving inside that window**, which
 * is a few milliseconds wide and would be a Back pressed during a dialog's dismissal. That
 * press is swallowed, and both callbacks re-read `recordedIndex` off whatever entry the
 * browser actually landed on — so the worst case is one press that does nothing, with the
 * count still true of the entry it is standing on rather than drifting from it.
 */
const swallow = []

const asObject = value => (value && typeof value === 'object' ? value : {})
const integerOr = (value, fallback) => (Number.isInteger(value) ? value : fallback)

/** This entry's own `history.state`, or `{}` — including where there is no `history`. */
function entryState() {
  try {
    return asObject(window.history.state)
  } catch {
    return {}
  }
}

/**
 * The step the entry the page arrived on records, or `null`.
 *
 * **Read by `main.js` before the first render, and it outranks the snapshot's own `step`.**
 * Both are about the same tab and they agree in the ordinary case. They part in exactly one
 * journey, and that journey is the whole reason this is read at all: the visitor pressed
 * Back *inside* the calculator and then left. The snapshot is written at `continue` and
 * `calculate` only, so it still names the furthest step reached; this names the entry the
 * browser actually returned to, which is the screen they were last looking at.
 *
 * **It is not needed for the presses that follow a restore, and that was measured rather
 * than assumed.** The obvious argument — that every entry behind the restored one belongs
 * to a discarded document, so traversing to it re-parses the page and would restore the
 * snapshot's step over the entry's — is **wrong**. Those entries were created by
 * `pushState` from the document the restored entry reloaded, so the reload gives all of them
 * that same new document and traversing between them is an ordinary same-document
 * `popstate`. Measured in Chromium by writing a mark onto `window` before the press and
 * finding it still there after: the heap survives, twice. So Back after a restore is driven
 * by `traverse` below exactly as Back before one is, and the mutation that takes this line
 * out leaves `test_back_after_a_restore_keeps_walking_back_through_the_steps` green — see
 * that test's own note.
 *
 * Used only in place of the snapshot's `step`, never in place of the restore: with no
 * answers to draw there is nothing for a step to show, so a page that restores nothing
 * opens at the introduction whatever the entry says.
 */
export function stepFromHistory() {
  const value = entryState()[STEP_KEY]
  return Number.isInteger(value) ? value : null
}

/**
 * Every call that *changes* the session history, and the one place that may refuse.
 *
 * **A browser that will not let us write history must leave a working calculator**, which is
 * `state.js`'s rule for `sessionStorage` applied to the other browser API this feature
 * depends on. `pushState` throws where the document is sandboxed, and a page that is
 * throttled for calling it too often gets a `SecurityError` too. Unguarded, the very first
 * call — `installStepHistory`, which runs at module evaluation time — would abort `main.js`
 * and the calculator would not render at all: a wizard lost to a history entry.
 *
 * So a failure takes this module out of service and nothing else with it. `installed` goes
 * false, no entry is pushed again, and the step bar's own Back is untouched by any of it.
 */
function attempt(write) {
  if (!installed) return false
  try {
    write()
    return true
  } catch {
    installed = false
    return false
  }
}

/** Rewrite the entry the visitor is standing on. Adds no entry, so Back is unchanged. */
function replaceEntry(step) {
  const held = entryState()
  recordedStep = step
  recordedIndex = integerOr(held[INDEX_KEY], recordedIndex)
  return attempt(() => window.history.replaceState({ ...held, [STEP_KEY]: step, [INDEX_KEY]: recordedIndex }, ''))
}

/** Add an entry for a step the visitor has just moved to. */
function pushEntry(step) {
  recordedStep = step
  recordedIndex += 1
  return attempt(() => window.history.pushState({ [STEP_KEY]: step, [INDEX_KEY]: recordedIndex }, ''))
}

/**
 * The one place a step change becomes a history entry.
 *
 * **The first load replaces and does not push** — `installStepHistory` below — or every
 * visitor collects a duplicate entry before they have done anything, and the first Back of
 * the session does nothing visible.
 *
 * A step change that a traversal *caused* must not push either: the entry it moved to is
 * already the right one. It is rewritten instead where the two differ, which is how a
 * traversal to a step the calculator cannot draw stops claiming otherwise.
 */
function recordStepChange() {
  const now = readStep()
  if (now === recordedStep) return
  if (applying) {
    replaceEntry(now)
    return
  }
  pushEntry(now)
}

/** A Back or Forward press: the same navigation the on-screen control performs. */
function traverse(event) {
  // Out of service (`attempt`): the entries that exist are the browser's business and
  // this module no longer claims to know what they mean.
  if (!installed) return
  if (swallow.length) {
    swallow.shift()()
    return
  }
  const held = asObject(event.state)
  const target = held[STEP_KEY]
  // An entry this module never wrote — another page's, or one from before this
  // deployment. Nothing here knows what it means, so nothing here touches it.
  if (!Number.isInteger(target)) return
  const index = integerOr(held[INDEX_KEY], 0)
  const from = recordedIndex
  recordedStep = target
  recordedIndex = index
  applying = true
  let moved
  try {
    moved = navigate(target)
  } finally {
    applying = false
  }
  if (moved !== false) return
  // **The visitor declined the discard, so nothing moved — but the browser has already
  // moved the position and a `popstate` cannot be cancelled.** Travel back to the entry
  // they are still standing on, and swallow the `popstate` that lands, so that the refusal
  // costs them their place in the history as little as it costs them their draft.
  recordedStep = readStep()
  recordedIndex = from
  const delta = from - index
  // `go(0)` is a reload, which would throw away the very draft the refusal protected.
  if (delta === 0) return
  swallow.push(() => {
    recordedIndex = integerOr(entryState()[INDEX_KEY], from)
    recordedStep = readStep()
  })
  if (!attempt(() => window.history.go(delta))) swallow.pop()
}

/**
 * Run a step change that is **not** a navigation, so the entry is rewritten rather than
 * added to.
 *
 * One caller: the `setState` in `main.js` that lands a freshly fetched taxonomy. §7.2a's
 * revalidation can move the visitor — a restored draft whose sector a publish has retired is
 * sent back to step 0, which is the screen that asks the question they now have to answer
 * again — and **that is the page load moving them, not a press of theirs.** Pushed, Back
 * would offer the step the dropped answer was on, which is the one screen the prune has just
 * made unanswerable.
 *
 * It wraps the whole `setState` rather than being conditional on a prune having happened,
 * because the rule is about the *cause* and not the outcome: a taxonomy arriving is not a
 * navigation whether or not it moves anybody.
 */
export function withoutAnEntry(change) {
  if (!installed) return change()
  applying = true
  try {
    return change()
  } finally {
    applying = false
  }
}

/**
 * Claim the entry the page arrived on and start recording step changes.
 *
 * @param {object} wiring
 * @param {() => number} wiring.step Reads `state.step`.
 * @param {(step: number) => boolean} wiring.navigate Performs a traversal, answering false
 *   if the visitor declined it. This is the on-screen Back's own function.
 * @param {(fn: () => void) => void} wiring.subscribe `state.js`'s subscribe.
 */
export function installStepHistory({ step, navigate: performNavigation, subscribe }) {
  readStep = step
  navigate = performNavigation
  installed = true
  // **`replaceState`, never `pushState`.** See `recordStepChange`. And if this first call is
  // refused, nothing else is wired at all: see `attempt`.
  if (!replaceEntry(step())) return
  subscribe(recordStepChange)
  window.addEventListener('popstate', traverse)
}

/**
 * Return to the entry the calculator opened in, and call it the introduction.
 *
 * Called by `resetCalculator` (`state.js`) **before** its own `setState`, so that the step
 * this module records is already `-1` when that patch arrives and the reset itself pushes
 * nothing.
 */
export function unwindStepHistory() {
  if (!installed) return
  const depth = recordedIndex
  if (!replaceEntry(-1) || depth <= 0) return
  swallow.push(() => replaceEntry(-1))
  if (!attempt(() => window.history.go(-depth))) swallow.pop()
}
