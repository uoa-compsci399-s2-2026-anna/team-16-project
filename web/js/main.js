import { getTaxonomy } from './api.js'
import { state, setState, subscribe, resetCalculator } from './state.js'
import { bindCalculator, render, renderChrome } from './calculator.js'
import { applyDocumentLanguage, applyToDocument, installLanguageChooser, t } from './i18n.js'
import { pruneAnswers, readSnapshot, restoredPatch } from './snapshot.js'
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
    setState({
      taxonomy,
      loading: false,
      ...(revalidated ? { ...revalidated.patch, restoreDropped: revalidated.dropped } : {}),
      ...(preserveError ? {} : { error: null, errorCode: null }),
    })
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
  setState(restoredPatch(snapshot))
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
