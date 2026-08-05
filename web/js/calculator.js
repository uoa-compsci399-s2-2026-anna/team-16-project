import { calculate } from './api.js'
import { state, setState, resetCalculator } from './state.js'
import { kgString, massToKg } from './units.js'
import { buttonRow, escapeHtml, formatNumber, slug } from './view.js'
import { renderResults } from './results.js'

const STEPS = ['Supply-chain stage', 'Food type', 'Waste amount', 'Destinations', 'Review', 'Results']
const decimalPattern = /^\d+(\.\d{1,2})?$/

const sorted = items => [...(items || [])].sort((a, b) => Number(a.sort_order || 0) - Number(b.sort_order || 0))
const selected = (items, code) => items.find(item => item.code === code)
const lineValue = (lines, destination) => lines.find(line => line.destination === destination)?.qtyInput || ''
const allocated = lines => lines.reduce((sum, line) => sum + (Number(line.qtyInput) || 0), 0)
const hasData = () => Boolean(state.sector || state.foodCategory || state.totalAmount || state.current.length || state.alternative.length || state.result)

function introduction() {
  return `<section class="hero" aria-labelledby="page-title">
    <div class="hero-copy"><p class="eyebrow">For New Zealand food businesses</p><h1 id="page-title">Food Waste Impact Calculator</h1><p class="lead">Turn your food waste measurements into a clearer view of their potential environmental and financial impact.</p><button class="button button-primary button-large" type="button" data-action="start">Start calculator</button><p class="privacy-note">Your entries are submitted anonymously when you calculate results.</p></div>
    <div class="hero-food-pattern" aria-hidden="true"><svg class="food-arch-mask" viewBox="0 0 1500 190" preserveAspectRatio="none"><defs><mask id="food-arch-cutouts"><rect width="1500" height="190" fill="white" />${[150, 450, 750, 1050, 1350].flatMap(centre => [`<ellipse cx="${centre}" cy="190" rx="205" ry="166" fill="none" stroke="black" stroke-width="32"/>`, `<ellipse cx="${centre}" cy="190" rx="151" ry="120" fill="none" stroke="black" stroke-width="28"/>`]).join('')}${[300, 600, 900, 1200].map(x => `<path d="M ${x} 72 L ${x + 36} 126 L ${x} 181 L ${x - 36} 126 Z" fill="black"/>`).join('')}</mask></defs><rect width="1500" height="190" fill="currentColor" mask="url(#food-arch-cutouts)"/></svg></div>
    <div class="hero-support-grid"><div class="needs-panel"><h2>What you will need</h2><ul class="check-list"><li>Where the waste occurred in the food supply chain</li><li>The food category, if known</li><li>The total waste amount in kilograms or tonnes</li><li>Current and optional alternative waste destinations</li></ul></div></div>
  </section>`
}

function sectorStep() {
  const sectors = sorted(state.taxonomy.sectors)
  return `<section class="content-section" aria-labelledby="stage-title"><p class="eyebrow">Step 1</p><h1 id="stage-title">Where in the food supply chain did this waste occur?</h1><p class="section-intro" id="supply-chain-support">Choose the stage that best describes where the food waste was generated.</p>
    <fieldset class="stage-fieldset" aria-describedby="supply-chain-support"><legend>Supply-chain stage <span class="required">(required)</span></legend><div class="stage-card-list">${sectors.map(sector => {
      const isSelected = state.sector === sector.code
      const expanded = state.expandedSectors.includes(sector.code)
      const id = `sector-${slug(sector.code)}`
      return `<div class="stage-card ${isSelected ? 'selected' : ''}"><label class="stage-select" for="${id}"><input id="${id}" name="sector" type="radio" value="${escapeHtml(sector.code)}" ${isSelected ? 'checked' : ''}><span class="stage-copy"><span class="stage-title">${escapeHtml(sector.name)}</span><span class="stage-description">${escapeHtml(sector.description || '')}</span></span>${isSelected ? '<span class="selected-label" aria-hidden="true">✓ Selected</span>' : ''}</label><button class="details-button" type="button" data-action="toggle-sector" data-sector="${escapeHtml(sector.code)}" aria-expanded="${expanded}" aria-controls="${id}-details">Details <span class="chevron ${expanded ? 'expanded' : ''}" aria-hidden="true">⌄</span></button><div class="stage-details" id="${id}-details" ${expanded ? '' : 'hidden'}><p>${escapeHtml(sector.description || 'Additional details have not been supplied.')}</p></div></div>`
    }).join('')}</div>${state.error ? `<p class="field-error" role="alert">${escapeHtml(state.error)}</p>` : ''}</fieldset>${buttonRow(-1)}</section>`
}

function foodStep() {
  const categories = sorted(state.taxonomy.food_categories)
  return `<section class="content-section" aria-labelledby="food-title"><p class="eyebrow">Step 2 · Optional</p><h1 id="food-title">What type of food waste are you measuring?</h1><p class="section-intro">Choose a category if known. Leaving this blank uses the standard mix defined by the published taxonomy.</p><fieldset class="choice-fieldset"><legend class="sr-only">Food category</legend><div class="simple-choice-list">${categories.map(category => {
    const isSelected = state.foodCategory === category.code
    return `<label class="simple-choice ${isSelected ? 'selected' : ''}"><input type="radio" name="food-category" value="${escapeHtml(category.code)}" ${isSelected ? 'checked' : ''}><span><strong>${escapeHtml(category.name)}</strong>${category.is_standard_mix ? '<small>Recommended when composition is unknown</small>' : ''}</span>${isSelected ? '<span class="selected-label" aria-hidden="true">✓ Selected</span>' : ''}</label>`
  }).join('')}</div></fieldset>${state.foodCategory ? '<button type="button" class="text-button" data-action="clear-food">Clear optional selection</button>' : ''}${buttonRow(0)}</section>`
}

function amountStep() {
  return `<section class="content-section" aria-labelledby="amount-title"><p class="eyebrow">Step 3</p><h1 id="amount-title">How much food waste are you measuring?</h1><p class="section-intro">Enter the scenario total. Destination quantities will be converted to kilograms before submission.</p><div class="form-panel amount-grid"><div class="form-field ${state.error ? 'has-error' : ''}"><label for="total-waste">Waste amount <span class="required">(required)</span></label><p class="field-hint">Use up to two decimal places.</p><input id="total-waste" type="number" inputmode="decimal" min="0" step="0.01" value="${escapeHtml(state.totalAmount)}" ${state.error ? 'aria-invalid="true" aria-describedby="amount-error"' : ''}>${state.error ? `<p class="field-error" id="amount-error" role="alert">${escapeHtml(state.error)}</p>` : ''}</div><div class="form-field"><label for="total-unit">Unit <span class="required">(required)</span></label><p class="field-hint">Choose the measurement unit.</p><select id="total-unit"><option value="kilograms" ${state.totalUnit === 'kilograms' ? 'selected' : ''}>kilograms</option><option value="tonnes" ${state.totalUnit === 'tonnes' ? 'selected' : ''}>tonnes</option></select></div></div>${buttonRow(1)}</section>`
}

function scenarioRows(kind) {
  const lines = state[kind]
  return sorted(state.taxonomy.destinations).map(destination => `<div class="destination-row"><label for="${kind}-${slug(destination.code)}">${escapeHtml(destination.name)}${destination.description ? `<small>${escapeHtml(destination.description)}</small>` : ''}</label><div class="amount-with-unit"><input id="${kind}-${slug(destination.code)}" data-scenario="${kind}" data-destination="${escapeHtml(destination.code)}" type="number" inputmode="decimal" min="0" step="0.01" value="${escapeHtml(lineValue(lines, destination.code))}" aria-label="${escapeHtml(destination.name)} amount for ${kind} scenario"><span>${escapeHtml(state.totalUnit)}</span></div></div>`).join('')
}

function destinationStep() {
  const total = Number(state.totalAmount) || 0
  const currentAllocated = allocated(state.current)
  const alternativeAllocated = allocated(state.alternative)
  const summary = (kind, value) => `<div class="allocation-summary" id="${kind}-summary" aria-live="polite"><div><span>Total waste</span><strong>${total.toFixed(2)} ${escapeHtml(state.totalUnit)}</strong></div><div><span>Allocated</span><strong data-summary="allocated">${value.toFixed(2)} ${escapeHtml(state.totalUnit)}</strong></div><div><span>Remaining</span><strong data-summary="remaining">${(total - value).toFixed(2)} ${escapeHtml(state.totalUnit)}</strong></div></div>`
  return `<section class="content-section wide" aria-labelledby="destination-title"><p class="eyebrow">Step 4</p><h1 id="destination-title">Where did the food waste go?</h1><p class="section-intro">Allocate the full total for the current scenario. You can also compare an alternative destination scenario.</p>
    <section aria-labelledby="current-title"><h2 id="current-title">Current scenario</h2>${summary('current', currentAllocated)}<div class="destination-list">${scenarioRows('current')}</div></section>
    <label class="comparison-toggle"><input id="compare-alternative" type="checkbox" ${state.compareAlternative ? 'checked' : ''}> Compare with an alternative scenario</label>
    ${state.compareAlternative ? `<section aria-labelledby="alternative-title"><h2 id="alternative-title">Alternative scenario</h2><p class="field-hint">Use prevention when the alternative represents food waste avoided.</p>${summary('alternative', alternativeAllocated)}<div class="destination-list">${scenarioRows('alternative')}</div></section>` : ''}
    <p class="field-error" id="allocation-error" role="alert">${escapeHtml(state.error || '')}</p>${buttonRow(2, 'Continue', false, 'continue')}</section>`
}

function reviewLines(kind) {
  const lines = state[kind].filter(line => Number(line.qtyInput) > 0)
  return `<dl class="review-destinations">${lines.map(line => {
    const destination = selected(state.taxonomy.destinations, line.destination)
    return `<div><dt>${escapeHtml(destination?.name || line.destination)}</dt><dd>${formatNumber(line.qtyInput, 2)} ${escapeHtml(state.totalUnit)}</dd></div>`
  }).join('')}</dl>`
}

function reviewStep() {
  const sector = selected(state.taxonomy.sectors, state.sector)
  const food = selected(state.taxonomy.food_categories, state.foodCategory)
  const totalKg = massToKg(state.totalAmount, state.totalUnit)
  return `<section class="content-section wide" aria-labelledby="review-title"><p class="eyebrow">Step 5</p><h1 id="review-title">Review your information</h1><p class="section-intro">Check the codes and quantities that will be submitted anonymously for calculation.</p>
    <article class="review-block"><div class="section-heading-row"><h2>Supply-chain stage</h2><button class="text-button" type="button" data-action="go-step" data-step="0">Edit</button></div><p>${escapeHtml(sector?.name || state.sector)}</p></article>
    <article class="review-block"><div class="section-heading-row"><h2>Food category</h2><button class="text-button" type="button" data-action="go-step" data-step="1">Edit</button></div><p>${escapeHtml(food?.name || 'Standard mix / not specified')}</p></article>
    <article class="review-block"><div class="section-heading-row"><h2>Waste amount</h2><button class="text-button" type="button" data-action="go-step" data-step="2">Edit</button></div><p><strong>${formatNumber(state.totalAmount, 2)} ${escapeHtml(state.totalUnit)}</strong> · ${formatNumber(totalKg, 3)} kg</p></article>
    <article class="review-block"><div class="section-heading-row"><h2>Current scenario</h2><button class="text-button" type="button" data-action="go-step" data-step="3">Edit</button></div>${reviewLines('current')}</article>
    ${state.compareAlternative ? `<article class="review-block"><div class="section-heading-row"><h2>Alternative scenario</h2><button class="text-button" type="button" data-action="go-step" data-step="3">Edit</button></div>${reviewLines('alternative')}</article>` : ''}
    <aside class="disclaimer compact"><span class="info-icon" aria-hidden="true">i</span><div><strong>Anonymous submission</strong><p>One calculation equals one anonymous submission. No identifying information about you or your business is requested.</p></div></aside>
    ${state.error ? `<p class="field-error" role="alert">${escapeHtml(state.error)}</p>` : ''}${buttonRow(3, state.loading ? 'Calculating…' : 'Calculate impact', state.loading, 'calculate')}</section>`
}

function validateCurrentStep() {
  if (state.step === 0 && !state.sector) return 'Select where in the food supply chain the waste occurred.'
  if (state.step === 2) {
    if (!state.totalAmount || Number(state.totalAmount) <= 0) return 'Waste amount must be greater than zero.'
    if (!decimalPattern.test(state.totalAmount)) return 'Enter no more than two decimal places.'
  }
  if (state.step === 3) {
    const total = Number(state.totalAmount)
    const scenarios = state.compareAlternative ? ['current', 'alternative'] : ['current']
    for (const kind of scenarios) {
      const values = state[kind].map(line => line.qtyInput).filter(value => value !== '')
      if (values.some(value => Number(value) < 0)) return `${kind === 'current' ? 'Current' : 'Alternative'} destination amounts must be zero or greater.`
      if (values.some(value => !decimalPattern.test(value))) return `Enter ${kind} destination amounts to no more than two decimal places.`
      const sum = allocated(state[kind])
      if (sum > total) return `Allocated ${kind} waste exceeds total waste by ${(sum - total).toFixed(2)} ${state.totalUnit}.`
      if (Math.abs(sum - total) > 0.000001) return `Allocate the full ${total.toFixed(2)} ${state.totalUnit} for the ${kind} scenario. ${(total - sum).toFixed(2)} ${state.totalUnit} remains.`
    }
  }
  return ''
}

function buildLines(kind) {
  return state[kind].filter(line => Number(line.qtyInput) > 0).map(line => ({ destination: line.destination, qty_kg: line.qtyKg }))
}

async function submitCalculation() {
  const payload = {
    ...(state.token ? { token: state.token } : {}),
    sector: state.sector,
    food_category: state.foodCategory || null,
    gwp_horizon: state.gwpHorizon,
    current: buildLines('current'),
    alternative: state.compareAlternative ? buildLines('alternative') : null,
  }
  setState({ loading: true, error: null })
  try {
    const result = await calculate(payload)
    if (result.token) sessionStorage.setItem('kaiCalculatorToken', result.token)
    setState({ result, token: result.token || state.token, loading: false, step: 5 })
  } catch (error) {
    setState({ loading: false, error: error.message || 'The calculation could not be completed.' })
  }
}

function updateDestination(input) {
  const kind = input.dataset.scenario
  const destination = input.dataset.destination
  const existing = state[kind].filter(line => line.destination !== destination)
  state[kind] = input.value === '' ? existing : [...existing, { destination, qtyInput: input.value, qtyKg: kgString(input.value, state.totalUnit) }]
  const total = Number(state.totalAmount) || 0
  const sum = allocated(state[kind])
  const summary = document.getElementById(`${kind}-summary`)
  summary?.classList.toggle('invalid', sum > total)
  if (summary) {
    summary.querySelector('[data-summary="allocated"]').textContent = `${sum.toFixed(2)} ${state.totalUnit}`
    summary.querySelector('[data-summary="remaining"]').textContent = `${(total - sum).toFixed(2)} ${state.totalUnit}`
  }
  const error = validateCurrentStep()
  document.getElementById('allocation-error').textContent = error
}

export function render(main) {
  main.className = `main-content${state.step === -1 ? ' introduction-main' : ''}`
  if (state.loading && !state.taxonomy) {
    main.innerHTML = '<section class="content-section"><p class="loading-state" role="status">Loading calculator options…</p></section>'
    return
  }
  if (!state.taxonomy) {
    main.innerHTML = `<section class="content-section error-state"><h1>Calculator unavailable</h1><p>${escapeHtml(state.error || 'The taxonomy could not be loaded.')}</p><button class="button button-primary" type="button" data-action="retry">Try again</button></section>`
    return
  }
  const screens = [sectorStep, foodStep, amountStep, destinationStep, reviewStep]
  main.innerHTML = state.step === -1 ? introduction() : state.step === 5 ? renderResults(state) : screens[state.step]()
}

export function renderChrome() {
  const header = document.getElementById('site-header')
  const logo = document.getElementById('brand-logo')
  const indicator = document.getElementById('step-indicator')
  const clearButton = document.getElementById('clear-button')
  const intro = state.step === -1
  header.classList.toggle('intro-header', intro)
  logo.src = intro ? './assets/kai-commitment-logo-white.webp' : './assets/kai-commitment-logo.png'
  clearButton.hidden = !hasData()
  indicator.hidden = intro
  if (!intro) indicator.innerHTML = `<p class="step-mobile">Step ${state.step + 1} of ${STEPS.length}: <strong>${escapeHtml(STEPS[state.step])}</strong></p><ol>${STEPS.map((label, index) => `<li class="${index < state.step ? 'complete' : index === state.step ? 'current' : 'upcoming'}" ${index === state.step ? 'aria-current="step"' : ''}><span class="step-number" aria-hidden="true">${index < state.step ? '✓' : index + 1}</span><span>${escapeHtml(label)}</span></li>`).join('')}</ol>`
}

export function bindCalculator(main, retryTaxonomy) {
  main.addEventListener('click', event => {
    const control = event.target.closest('[data-action]')
    if (!control) return
    const action = control.dataset.action
    if (action === 'start') setState({ step: 0, error: null })
    if (action === 'go-step') setState({ step: Number(control.dataset.step), error: null })
    if (action === 'toggle-sector') {
      const code = control.dataset.sector
      setState({ expandedSectors: state.expandedSectors.includes(code) ? state.expandedSectors.filter(item => item !== code) : [...state.expandedSectors, code] })
    }
    if (action === 'clear-food') setState({ foodCategory: null })
    if (action === 'continue') {
      const error = validateCurrentStep()
      setState(error ? { error } : { step: state.step + 1, error: null })
    }
    if (action === 'calculate') submitCalculation()
    if (action === 'start-over') resetCalculator()
    if (action === 'retry') retryTaxonomy()
    if (action === 'view-methodology') window.location.href = './methodology.html'
    window.scrollTo({ top: 0, behavior: 'smooth' })
  })

  main.addEventListener('change', event => {
    const target = event.target
    if (target.name === 'sector') setState({ sector: target.value, error: null })
    if (target.name === 'food-category') setState({ foodCategory: target.value })
    if (target.id === 'total-unit') setState({ totalUnit: target.value, error: null, current: [], alternative: [] })
    if (target.id === 'compare-alternative') setState({ compareAlternative: target.checked, alternative: target.checked ? state.alternative : [], error: null })
  })

  main.addEventListener('input', event => {
    const target = event.target
    if (target.id === 'total-waste') {
      state.totalAmount = target.value
      state.error = null
    }
    if (target.matches('[data-scenario][data-destination]')) updateDestination(target)
  })
}
