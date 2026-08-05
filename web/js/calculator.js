import { calculate } from './api.js'
import { state, setState, resetCalculator } from './state.js'
import { kgString, massToKg, toKg } from './units.js'
import { buttonRow, escapeHtml, formatNumber, slug } from './view.js'
import { renderResults } from './results.js'

const STEPS = ['Supply-chain stage', 'Food type', 'Waste amount', 'Destinations', 'Review', 'Results']
const decimalPattern = /^\d+(\.\d{1,2})?$/

const sorted = items => [...(items || [])].sort((a, b) => Number(a.sort_order || 0) - Number(b.sort_order || 0))
const selected = (items, code) => items.find(item => item.code === code)
const createLine = () => ({ id: crypto.randomUUID(), destination: '', qtyKg: '', qtyInput: '', measurement: 'kilograms', unitPreset: null, unitCount: null })
const allocatedKg = lines => lines.reduce((sum, line) => sum + (Number(line.qtyKg) || 0), 0)
const applicablePresets = () => sorted(state.taxonomy.unit_presets).filter(preset => !preset.food_category || preset.food_category === state.foodCategory)
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
  if (!lines.length) return '<p class="empty-state">No destination rows added yet.</p>'
  const presets = applicablePresets()
  return lines.map((line, index) => {
    const fieldPath = `${kind}[${index}].qty_kg`
    const invalid = Boolean(state.fieldErrors[fieldPath])
    return `<article class="scenario-line ${invalid ? 'invalid' : ''}" data-line-id="${line.id}">
      <div class="scenario-line-grid">
        <div class="form-field"><label for="${kind}-${line.id}-destination">Destination <span class="required">(required)</span></label><select id="${kind}-${line.id}-destination" data-line-field="destination" data-scenario="${kind}" data-line-id="${line.id}"><option value="">Select destination</option>${sorted(state.taxonomy.destinations).map(destination => `<option value="${escapeHtml(destination.code)}" ${line.destination === destination.code ? 'selected' : ''}>${escapeHtml(destination.name)}</option>`).join('')}</select></div>
        <div class="form-field"><label for="${kind}-${line.id}-measurement">Measurement <span class="required">(required)</span></label><select id="${kind}-${line.id}-measurement" data-line-field="measurement" data-scenario="${kind}" data-line-id="${line.id}"><option value="kilograms" ${line.measurement === 'kilograms' ? 'selected' : ''}>kilograms</option><option value="tonnes" ${line.measurement === 'tonnes' ? 'selected' : ''}>tonnes</option>${presets.map(preset => `<option value="preset:${escapeHtml(preset.code)}" ${line.measurement === `preset:${preset.code}` ? 'selected' : ''}>${escapeHtml(preset.label)}</option>`).join('')}</select></div>
        <div class="form-field"><label for="${kind}-${line.id}-amount">${line.measurement.startsWith('preset:') ? 'Number of units' : 'Waste amount'} <span class="required">(required)</span></label><input id="${kind}-${line.id}-amount" data-line-field="amount" data-scenario="${kind}" data-line-id="${line.id}" type="number" inputmode="decimal" min="0" step="any" value="${escapeHtml(line.qtyInput)}" ${invalid ? 'aria-invalid="true"' : ''}><p class="field-hint">${line.qtyKg ? `${escapeHtml(line.qtyKg)} kg will be sent to the API.` : 'Enter a non-negative amount.'}</p></div>
      </div>
      ${invalid ? `<p class="field-error" role="alert">${escapeHtml(state.fieldErrors[fieldPath])}</p>` : ''}
      <button class="text-button danger" type="button" data-action="remove-line" data-scenario="${kind}" data-line-id="${line.id}">Remove destination row</button>
    </article>`
  }).join('')
}

function destinationStep() {
  const total = massToKg(state.totalAmount, state.totalUnit) || 0
  const currentAllocated = allocatedKg(state.current)
  const alternativeAllocated = allocatedKg(state.alternative)
  const summary = (kind, value) => `<div class="allocation-summary ${value > total ? 'invalid' : ''}" id="${kind}-summary" aria-live="polite"><div><span>Total waste</span><strong>${total.toFixed(3)} kg</strong></div><div><span>Allocated</span><strong data-summary="allocated">${value.toFixed(3)} kg</strong></div><div><span>Remaining</span><strong data-summary="remaining">${(total - value).toFixed(3)} kg</strong></div></div>`
  return `<section class="content-section wide" aria-labelledby="destination-title"><p class="eyebrow">Step 4</p><h1 id="destination-title">Where did the food waste go?</h1><p class="section-intro">Allocate the full total for the current scenario. You can also compare an alternative destination scenario.</p>
    <section aria-labelledby="current-title"><h2 id="current-title">Current scenario</h2>${summary('current', currentAllocated)}<div class="scenario-line-list">${scenarioRows('current')}</div><button class="button button-add" type="button" data-action="add-line" data-scenario="current">+ Add destination row</button></section>
    <label class="comparison-toggle"><input id="compare-alternative" type="checkbox" ${state.compareAlternative ? 'checked' : ''}> Compare with an alternative scenario</label>
    ${state.compareAlternative ? `<section aria-labelledby="alternative-title"><h2 id="alternative-title">Alternative scenario</h2><p class="field-hint">Use prevention when the alternative represents food waste avoided.</p>${summary('alternative', alternativeAllocated)}<div class="scenario-line-list">${scenarioRows('alternative')}</div><button class="button button-add" type="button" data-action="add-line" data-scenario="alternative">+ Add destination row</button></section>` : ''}
    <p class="field-error" id="allocation-error" role="alert">${escapeHtml(state.error || '')}</p>${buttonRow(2, 'Continue', false, 'continue')}</section>`
}

function reviewLines(kind) {
  const lines = state[kind].filter(line => Number(line.qtyKg) > 0)
  return `<dl class="review-destinations">${lines.map(line => {
    const destination = selected(state.taxonomy.destinations, line.destination)
    const inputLabel = line.unitPreset ? `${formatNumber(line.unitCount, 2)} × ${escapeHtml(selected(state.taxonomy.unit_presets, line.unitPreset)?.label || line.unitPreset)}` : `${formatNumber(line.qtyInput, 2)} ${escapeHtml(line.measurement)}`
    return `<div><dt>${escapeHtml(destination?.name || line.destination)}<small>${inputLabel}</small></dt><dd>${formatNumber(line.qtyKg, 3)} kg</dd></div>`
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
    ${state.error ? `<p class="field-error api-error ${state.errorCode ? `error-${slug(state.errorCode)}` : ''}" role="alert">${escapeHtml(state.error)}</p>` : ''}${buttonRow(3, state.loading ? 'Calculating…' : Date.now() < state.rateLimitedUntil ? 'Try again shortly' : 'Calculate impact', state.loading || Date.now() < state.rateLimitedUntil, 'calculate')}</section>`
}

function validateCurrentStep() {
  if (state.step === 0 && !state.sector) return 'Select where in the food supply chain the waste occurred.'
  if (state.step === 2) {
    if (!state.totalAmount || Number(state.totalAmount) <= 0) return 'Waste amount must be greater than zero.'
    if (!decimalPattern.test(state.totalAmount)) return 'Enter no more than two decimal places.'
  }
  if (state.step === 3) {
    const total = massToKg(state.totalAmount, state.totalUnit)
    const scenarios = state.compareAlternative ? ['current', 'alternative'] : ['current']
    for (const kind of scenarios) {
      const lines = state[kind]
      if (!lines.length) return `Add at least one destination row to the ${kind} scenario.`
      if (lines.length > 20) return `The ${kind} scenario cannot contain more than 20 destination rows.`
      if (lines.some(line => !line.destination)) return `Select a destination for every ${kind} scenario row.`
      if (new Set(lines.map(line => line.destination)).size !== lines.length) return `Each destination can appear only once in the ${kind} scenario.`
      if (lines.some(line => line.qtyInput === '' || Number(line.qtyInput) < 0 || !Number.isFinite(Number(line.qtyInput)))) return `${kind === 'current' ? 'Current' : 'Alternative'} destination amounts must be valid non-negative numbers.`
      const sum = allocatedKg(lines)
      if (sum > total) return `Allocated ${kind} waste exceeds total waste by ${(sum - total).toFixed(3)} kg.`
      if (Math.abs(sum - total) > 0.0005) return `Allocate the full ${total.toFixed(3)} kg for the ${kind} scenario. ${(total - sum).toFixed(3)} kg remains.`
    }
  }
  return ''
}

function buildLines(kind) {
  return state[kind].filter(line => Number(line.qtyKg) >= 0).map(line => ({ destination: line.destination, qty_kg: line.qtyKg }))
}

let reloadTaxonomy = null

function publicError(error) {
  switch (error.code) {
    case 'VALIDATION_ERROR': return error.message || 'Check the highlighted fields and try again.'
    case 'UNKNOWN_CODE': return 'Calculator options have changed. The latest options are being loaded; please review your selections and try again.'
    case 'RATE_LIMITED': return 'Too many calculations have been requested. Please wait 60 seconds and try again.'
    case 'FORMULA_ERROR': return 'The calculator could not produce a result because its calculation configuration needs attention. Please try again later.'
    case 'NO_PUBLISHED_FACTOR_SET': return 'The calculator is currently under maintenance because no factor set is available.'
    default: return error.message || 'The calculation could not be completed.'
  }
}

function fieldErrorMap(error) {
  if (error.code !== 'VALIDATION_ERROR') return {}
  return Object.fromEntries((error.details || []).filter(detail => detail.field).map(detail => [detail.field, error.message || 'Check this value.']))
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
  if (Date.now() < state.rateLimitedUntil) return
  setState({ loading: true, error: null, errorCode: null, fieldErrors: {} })
  try {
    const result = await calculate(payload)
    if (result.token) sessionStorage.setItem('kaiCalculatorToken', result.token)
    setState({ result, token: result.token || state.token, loading: false, step: 5, error: null, errorCode: null, fieldErrors: {} })
  } catch (error) {
    const rateLimitedUntil = error.code === 'RATE_LIMITED' ? Date.now() + 60000 : state.rateLimitedUntil
    if (error.code === 'UNKNOWN_CODE' && reloadTaxonomy) await reloadTaxonomy({ preserveError: true })
    const errorStep = error.code === 'VALIDATION_ERROR' ? 3 : error.code === 'UNKNOWN_CODE' ? 0 : state.step
    setState({ loading: false, error: publicError(error), errorCode: error.code || 'UNKNOWN_ERROR', fieldErrors: fieldErrorMap(error), rateLimitedUntil, step: errorStep })
    if (error.code === 'RATE_LIMITED') setTimeout(() => setState({ rateLimitedUntil: 0 }), 60000)
  }
}

function convertLine(line) {
  if (line.qtyInput === '' || !Number.isFinite(Number(line.qtyInput))) return { ...line, qtyKg: '' }
  if (line.measurement.startsWith('preset:')) {
    const presetCode = line.measurement.slice(7)
    return { ...line, qtyKg: toKg(line.qtyInput, presetCode, state.taxonomy.unit_presets), unitPreset: presetCode, unitCount: line.qtyInput }
  }
  return { ...line, qtyKg: kgString(line.qtyInput, line.measurement), unitPreset: null, unitCount: null }
}

function updateLine(control) {
  const kind = control.dataset.scenario
  const lineId = control.dataset.lineId
  const field = control.dataset.lineField
  state[kind] = state[kind].map(line => line.id === lineId ? convertLine({ ...line, [field === 'amount' ? 'qtyInput' : field]: control.value }) : line)
  const total = massToKg(state.totalAmount, state.totalUnit) || 0
  const sum = allocatedKg(state[kind])
  const summary = document.getElementById(`${kind}-summary`)
  summary?.classList.toggle('invalid', sum > total)
  if (summary) {
    summary.querySelector('[data-summary="allocated"]').textContent = `${sum.toFixed(3)} kg`
    summary.querySelector('[data-summary="remaining"]').textContent = `${(total - sum).toFixed(3)} kg`
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
  reloadTaxonomy = retryTaxonomy
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
      if (error) setState({ error })
      else if (state.step === 2) setState({ step: 3, error: null, current: state.current.length ? state.current : [createLine()], alternative: state.alternative.length ? state.alternative : [createLine()] })
      else setState({ step: state.step + 1, error: null })
    }
    if (action === 'add-line') {
      const kind = control.dataset.scenario
      if (state[kind].length < 20) setState({ [kind]: [...state[kind], createLine()], error: null })
    }
    if (action === 'remove-line') {
      const kind = control.dataset.scenario
      setState({ [kind]: state[kind].filter(line => line.id !== control.dataset.lineId), error: null, fieldErrors: {} })
    }
    if (action === 'calculate') submitCalculation()
    if (action === 'start-over') resetCalculator()
    if (action === 'retry') retryTaxonomy()
    if (action === 'view-methodology') window.location.href = './methodology.html'
    if (['start', 'go-step', 'continue', 'calculate', 'start-over', 'retry', 'view-methodology'].includes(action)) {
      window.scrollTo({ top: 0, behavior: 'smooth' })
    }
  })

  main.addEventListener('change', event => {
    const target = event.target
    if (target.name === 'sector') setState({ sector: target.value, error: null })
    if (target.name === 'food-category') setState({ foodCategory: target.value })
    if (target.id === 'total-unit') setState({ totalUnit: target.value, error: null, current: [], alternative: [] })
    if (target.id === 'compare-alternative') setState({ compareAlternative: target.checked, alternative: target.checked ? (state.alternative.length ? state.alternative : [createLine()]) : [], error: null })
    if (target.matches('[data-line-field="destination"], [data-line-field="measurement"]')) {
      updateLine(target)
      setState({ [target.dataset.scenario]: state[target.dataset.scenario], error: null, fieldErrors: {} })
    }
  })

  main.addEventListener('input', event => {
    const target = event.target
    if (target.id === 'total-waste') {
      state.totalAmount = target.value
      state.error = null
    }
    if (target.matches('[data-line-field="amount"]')) updateLine(target)
  })
}
