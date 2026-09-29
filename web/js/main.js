import { getTaxonomy } from './api.js'
import { state, setState, subscribe, resetCalculator } from './state.js'
import { bindCalculator, goToStepFromHistory, render, renderChrome } from './calculator.js'
import { installStepHistory, stepFromHistory, withoutAnEntry } from './history.js'
import { applyDocumentLanguage, applyToDocument, installLanguageChooser, t } from './i18n.js'
import { pruneAnswers, readResultSnapshot, readSnapshot, restorableStep, restoredPatch } from './snapshot.js'
// The site drawer's `Escape` handler and `aria-expanded`. Side-effect import: the
// drawer is a `<details>` in the markup and works without this; see web/js/drawer.js.
import './drawer.js'

const main = document.getElementById('main-content')
const homeButton = document.getElementById('home-button')
const clearButton = document.getElementById('clear-button')

/**
 * The restored answers, waiting for a taxonomy to be checked against - or `null`.
 *
 * **The order is the whole of the safety argument** (§7.2a, and §6.1's own rule arriving
 * by another door): restore the answers, fetch the taxonomy *fresh*, check the answers
 * against it, drop what no longer exists and say so. A restored answer naming a
 * destination a publish has since retired is a form holding a code the current set does
 * not price, which is exactly what §6.1 forbids caching a taxonomy to avoid.
 *
 * It is a module-level latch rather than an argument because the check has to happen on
 * whichever fetch *succeeds*: a first load that fails leaves the visitor on the retry
 * screen with their answers still on `state`, and the Try again button's fetch is then the
 * one that has to do the pruning.
 *
 * **Nothing unvalidated is ever on screen.** `render()` shows the loading state for as
 * long as `state.loading && !state.taxonomy`, which is every moment between the restore
 * below and the prune inside `loadTaxonomy` - so the form is first painted from answers
 * that have already been checked.
 */
let pendingRestore = null

async function loadTaxonomy({ preserveError = false } = {}) {
  setState({ loading: true, ...(preserveError ? {} : { error: null, errorCode: null }) })
  try {
    const taxonomy = await getTaxonomy()
    // One `setState`, so the taxonomy and the answers it has been checked against arrive
    // together. Two would paint the form once from the unpruned answers.
    const revalidated = pendingRestore ? pruneAnswers(state, taxonomy) : null
    pendingRestore = null
    // **A taxonomy arriving is not a navigation** (§7.2b). The revalidation can move the
    // visitor - a restored draft whose sector this response no longer has is sent back to
    // step 0 - and that is this load moving them rather than a press of theirs. Pushed, Back
    // would offer the step the dropped answer was on, which is the one screen the prune has
    // just made unanswerable; rewritten, the entry says what is on it.
    withoutAnEntry(() => setState({
      taxonomy,
      loading: false,
      ...(revalidated ? { ...revalidated.patch, restoreDropped: revalidated.dropped } : {}),
      ...(preserveError ? {} : { error: null, errorCode: null }),
    }))
  } catch (error) {
    setState({ taxonomy: null, loading: false, error: error.message, errorCode: error.code || 'NETWORK_ERROR' })
  }
}

// **The restore, before anything renders and before `subscribe` is even installed.**
// Nothing is notified, so this costs no render; the first `render()` below is the one that
// draws it - as the loading state, because `state.loading` is true and the taxonomy has
// not arrived, which is what keeps unvalidated answers off the screen entirely.
const snapshot = readSnapshot()
if (snapshot) {
  pendingRestore = snapshot
  // **The result is read only when its answers are there, and it is read here rather than
  // inside `restoredPatch` because the step depends on it** (§7.2a). `step` lives in the
  // answers document, so the results screen is reachable only through it: a result key
  // found on its own is a hand edit or a partial eviction, and restoring it would put a
  // calculation on `state` that nothing renders. `restoredPatch` is handed the validated
  // result so that a stored step of 5 stands when there is something to draw and falls back
  // to the review step when there is not.
  //
  // **The gate is documentation rather than behaviour, and that was measured.** Restoring
  // the result unconditionally was tried with the answers key deleted and the result key
  // intact at 19,536 bytes: `main-content`'s `innerText` and `innerHTML` came back
  // byte-identical, because `step` lives only in the answers document and `state.step`
  // therefore stays at -1 — the introduction screen, which renders nothing off
  // `state.result`. So this is a mutation-equivalent guard, kept because the relationship
  // it states ("the result is reachable only through its answers") is the thing a future
  // reader needs and cannot infer from the two keys being independent.
  //
  // **It is NOT pruned against the fresh taxonomy, and that is the point of the whole
  // package.** The answers are - `pendingRestore` above is what asks for that - because the
  // form must not offer a code the current set does not price (§6.1). The result is history:
  // it carries its own `factor_set`, it is rendered from its own `resultTaxonomy`, and a
  // publish that landed while the visitor was away takes effect on their next calculation
  // rather than rewriting one they have already seen.
  const calculation = readResultSnapshot()
  setState({ ...restoredPatch(snapshot, calculation), ...(calculation || {}) })
  // **The entry the browser returned to outranks the snapshot's own `step`** (§7.2b).
  // They are about the same tab and they agree in the ordinary case - the last thing the
  // visitor did before leaving was a Continue, and both name that step. They part in one
  // journey, which is the whole reason this line exists: the visitor pressed Back *inside*
  // the calculator and then left. The snapshot is written at `continue` and `calculate`
  // only, so it still names the furthest step reached; the entry names the one they were
  // actually looking at.
  //
  // It is **not** what keeps Back working after a restore, and that was measured: the
  // entries behind this one were pushed from the document this load replaced, so they are
  // handed this document too and traversing to them is a same-document `popstate` rather
  // than another re-parse. See `stepFromHistory` in `history.js` for the measurement.
  //
  // **Only in place of the step, never in place of the restore**: with no answers there is
  // nothing for a step to show, so the whole of this block is inside `if (snapshot)`.
  const entryStep = restorableStep(stepFromHistory(), state.result)
  if (entryStep !== null) setState({ step: entryStep })
}

// render() replaces main.innerHTML wholesale, so every re-render detaches whatever the
// user had focused. Moving focus to <main> is right on a step transition and wrong on
// every other setState: arrow-keying a radio group fires change -> setState -> re-render,
// and the focus call then throws the keyboard user out of the group. On a same-step
// re-render, put focus back on the element that had it.
let focusedStep = null

subscribe(() => {
  const activeId = main.contains(document.activeElement) ? document.activeElement.id : null
  const stepChanged = state.step !== focusedStep
  focusedStep = state.step
  renderChrome()
  render(main)
  if (stepChanged) main.focus({ preventScroll: true })
  else if (activeId !== null) (document.getElementById(activeId) || main).focus({ preventScroll: true })
})

// **One history entry per step** (§7.2b), installed after the restore above so that the
// entry the page arrived on is claimed with the step actually on screen, and `replaceState`
// rather than `pushState` so nobody collects a duplicate entry before doing anything.
//
// `goToStepFromHistory` is the same `goToStep` the step bar's own Back calls, which is how
// the two Backs are kept from disagreeing rather than compared; `() => state.step` is the
// only thing history is written from, so a `render()` caused by a keystroke cannot produce
// an entry.
installStepHistory({ step: () => state.step, navigate: goToStepFromHistory, subscribe })

bindCalculator(main, loadTaxonomy)
homeButton.addEventListener('click', () => resetCalculator())
clearButton.addEventListener('click', () => {
  if (window.confirm(t('Clear all calculator data and return to the introduction?'))) resetCalculator()
})

// The static HTML the browser parsed before any of this ran - the header, the skip
// link, the footer, the transparency notice - plus `<html lang>`, `<html dir>` and
// the machine-translation notice. Done once, before the first render, so nothing
// below re-translates a string it already translated.
applyDocumentLanguage()
applyToDocument()

// The chooser is built here rather than shipped in `index.html` so that it
// cannot exist as a control that is present and does nothing: with scripting
// off none of this runs, and the calculator does not render at all. Changing
// language re-renders in place — `state` is held in memory, so a reload would
// throw away every entry somebody had typed.
installLanguageChooser(() => {
  renderChrome()
  render(main)
})

renderChrome()
render(main)
loadTaxonomy()
