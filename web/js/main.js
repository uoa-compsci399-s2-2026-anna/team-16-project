import { getTaxonomy } from './api.js'
import { state, setState, subscribe, resetCalculator } from './state.js'
import { bindCalculator, render, renderChrome } from './calculator.js'

const main = document.getElementById('main-content')
const homeButton = document.getElementById('home-button')
const clearButton = document.getElementById('clear-button')

async function loadTaxonomy() {
  setState({ loading: true, error: null })
  try {
    const taxonomy = await getTaxonomy()
    setState({ taxonomy, loading: false })
  } catch (error) {
    setState({ taxonomy: null, loading: false, error: error.message })
  }
}

subscribe(() => {
  renderChrome()
  render(main)
  main.focus({ preventScroll: true })
})

bindCalculator(main, loadTaxonomy)
homeButton.addEventListener('click', () => resetCalculator())
clearButton.addEventListener('click', () => {
  if (window.confirm('Clear all calculator data and return to the introduction?')) resetCalculator()
})

renderChrome()
render(main)
loadTaxonomy()
