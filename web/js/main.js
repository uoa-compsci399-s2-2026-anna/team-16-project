import { getTaxonomy } from './api.js'
import { state, setState, subscribe, resetCalculator } from './state.js'
import { bindCalculator, render, renderChrome } from './calculator.js'
import { applyDocumentLanguage, applyToDocument, installLanguageChooser, t } from './i18n.js'

const main = document.getElementById('main-content')
const homeButton = document.getElementById('home-button')
const clearButton = document.getElementById('clear-button')

async function loadTaxonomy({ preserveError = false } = {}) {
  setState({ loading: true, ...(preserveError ? {} : { error: null, errorCode: null }) })
  try {
    const taxonomy = await getTaxonomy()
    setState({ taxonomy, loading: false, ...(preserveError ? {} : { error: null, errorCode: null }) })
  } catch (error) {
    setState({ taxonomy: null, loading: false, error: error.message, errorCode: error.code || 'NETWORK_ERROR' })
  }
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
