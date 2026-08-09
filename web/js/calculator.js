import { calculate } from './api.js'
import { state, setState, resetCalculator, entryResultsFrom } from './state.js'
import { massToKg } from './units.js'
import { buttonRow, escapeHtml, formatNumber, slug } from './view.js'
import { downloadResults, renderResults } from './results.js'
import { compareImprovement, openImprovement, resetImprovement, updateImprovementInput } from './improvement.js'

const STEPS = ['Supply-chain stage', 'Food type', 'Waste amount', 'Destinations', 'Review', 'Results']
const decimalPattern = /^\d+(\.\d{1,2})?$/

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
const entryDestinations = () => sorted(state.taxonomy.destinations).filter(destination => destination.code !== 'prevention')
const createLine = (destination, qtyInput = '') => ({ id: randomId(), destination, qtyInput })
const amountToKg = amount => massToKg(amount, state.totalUnit)
const normaliseLines = lines => lines.map(line => ({ ...line, qtyKg: line.qtyInput === '' ? '' : amountToKg(line.qtyInput).toFixed(3) }))
const allocatedAmount = lines => lines.reduce((sum, line) => sum + (Number(line.qtyInput) || 0), 0)
const hasData = () => Boolean(state.entries.length || state.sector || state.foodCategory || state.totalAmount || state.current.some(line => line.qtyInput !== '') || state.result)
const draftEntry = () => ({ sector: state.sector, foodCategory: state.foodCategory, totalAmount: state.totalAmount, totalUnit: state.totalUnit, current: state.current.map(line => ({ ...line })) })

function introduction() {
  return `<section class="hero" aria-labelledby="page-title">
    <div class="hero-copy"><p class="eyebrow">For New Zealand food businesses</p><h1 id="page-title">Food Waste Impact Calculator</h1><p class="lead">Turn your food waste measurements into a clearer view of their potential environmental and financial impact.</p><button class="button button-primary button-large" type="button" data-action="start">Start calculator</button><p class="privacy-note">Your entries are submitted anonymously when you calculate results.</p></div>
    <div class="hero-food-pattern" aria-hidden="true"><svg class="food-arch-mask" viewBox="0 0 1500 190" preserveAspectRatio="none"><defs><mask id="food-arch-cutouts"><rect width="1500" height="190" fill="white" />${[150, 450, 750, 1050, 1350].flatMap(centre => [`<ellipse cx="${centre}" cy="190" rx="205" ry="166" fill="none" stroke="black" stroke-width="32"/>`, `<ellipse cx="${centre}" cy="190" rx="151" ry="120" fill="none" stroke="black" stroke-width="28"/>`]).join('')}${[300, 600, 900, 1200].map(x => `<path d="M ${x} 72 L ${x + 36} 126 L ${x} 181 L ${x - 36} 126 Z" fill="black"/>`).join('')}</mask></defs><rect width="1500" height="190" fill="currentColor" mask="url(#food-arch-cutouts)"/></svg></div>
    <div class="hero-support-grid"><div class="needs-panel"><h2>What you will need</h2><ul class="check-list"><li>Where the waste occurred in the food supply chain</li><li>The food category, if known</li><li>The total waste amount in kilograms or tonnes</li><li>How that total was distributed across waste destinations</li></ul></div></div>
  </section>`
}

function sectorStep() {
  const sectors = sorted(state.taxonomy.sectors)
  return `<section class="content-section" aria-labelledby="stage-title"><p class="eyebrow">Step 1</p><h1 id="stage-title">Where in the food supply chain did this waste occur?</h1><p class="section-intro" id="supply-chain-support">Choose the stage that best describes where the food waste was generated.</p>
    <fieldset class="stage-fieldset" aria-describedby="supply-chain-support"><legend>Supply-chain stage <span class="required">(required)</span></legend><div class="stage-card-list">${sectors.map(sector => {
      const isSelected = state.sector === sector.code
      const expanded = state.expandedSectors.includes(sector.code)
      const id = `sector-${slug(sector.code)}`
      return `<div class="stage-card ${isSelected ? 'selected' : ''}"><label class="stage-select" for="${id}"><input id="${id}" name="sector" type="radio" value="${escapeHtml(sector.code)}" ${isSelected ? 'checked' : ''}><span class="stage-copy"><span class="stage-title">${escapeHtml(sector.name)}</span><span class="stage-description">${escapeHtml(sector.description || '')}</span></span>${isSelected ? '<span class="selected-label" aria-hidden="true">✓ Selected</span>' : ''}</label><button class="details-button" type="button" data-action="toggle-sector" data-sector="${escapeHtml(sector.code)}" aria-expanded="${expanded}" aria-controls="${id}-details" aria-label="${expanded ? 'Hide' : 'Show'} details for ${escapeHtml(sector.name)}">Details <span class="chevron ${expanded ? 'expanded' : ''}" aria-hidden="true">⌄</span></button><div class="stage-details" id="${id}-details" ${expanded ? '' : 'hidden'}><p>${escapeHtml(sector.details || sector.description || 'Additional details have not been supplied.')}</p></div></div>`
    }).join('')}</div>${state.error ? `<p class="field-error" role="alert">${escapeHtml(state.error)}</p>` : ''}</fieldset>${buttonRow(-1)}</section>`
}

function foodStep() {
  const categories = sorted(state.taxonomy.food_categories)
  return `<section class="content-section" aria-labelledby="food-title"><p class="eyebrow">Step 2 · Optional</p><h1 id="food-title">What type of food waste are you measuring?</h1><p class="section-intro">Choose one category if you know it, or continue without selecting an option.</p><fieldset class="choice-fieldset"><legend class="sr-only">Food type</legend><div class="simple-choice-list">${categories.map(category => {
    const isSelected = state.foodCategory === category.code
    return `<label class="simple-choice ${isSelected ? 'selected' : ''}"><input id="food-category-${slug(category.code)}" type="radio" name="food-category" value="${escapeHtml(category.code)}" ${isSelected ? 'checked' : ''}><span><strong>${escapeHtml(category.name)}</strong>${category.is_standard_mix ? '<small>Recommended if you do not separate food waste by category</small>' : ''}</span>${isSelected ? '<span class="selected-label" aria-hidden="true">✓ Selected</span>' : ''}</label>`
  }).join('')}</div></fieldset>${state.foodCategory ? '<button type="button" class="text-button" data-action="clear-food">Clear optional selection</button>' : ''}${buttonRow(0)}</section>`
}

function amountStep() {
  return `<section class="content-section" aria-labelledby="amount-title"><p class="eyebrow">Step 3</p><h1 id="amount-title">How much food waste are you measuring?</h1><p class="section-intro">Enter the total amount. You will allocate this total across destinations in the next step.</p><div class="form-panel amount-grid"><div class="form-field ${state.error ? 'has-error' : ''}"><label for="total-waste">Waste amount <span class="required">(required)</span></label><p class="field-hint">Use up to two decimal places.</p><input id="total-waste" type="number" inputmode="decimal" min="0" step="0.01" value="${escapeHtml(state.totalAmount)}" ${state.error ? 'aria-invalid="true" aria-describedby="amount-error"' : ''}>${state.error ? `<p class="field-error" id="amount-error" role="alert">${escapeHtml(state.error)}</p>` : ''}</div><div class="form-field"><label for="total-unit">Unit <span class="required">(required)</span></label><p class="field-hint">Choose the measurement unit.</p><select id="total-unit"><option value="kilograms" ${state.totalUnit === 'kilograms' ? 'selected' : ''}>kilograms</option><option value="tonnes" ${state.totalUnit === 'tonnes' ? 'selected' : ''}>tonnes</option></select></div></div>${buttonRow(1)}</section>`
}

/**
 * The `entries[N].current[M].qty_kg` path §9 will use to name each row of the draft entry,
 * aligned with `state.current` so the render loop can look up its own row, and `null` for
 * a row the request never carried.
 *
 * Two things have to agree with what `submitCalculation` actually posted, and neither
 * follows from the render loop:
 *
 *   * **`buildLines` drops every blank row before sending**, so a row's request index is
 *     its position among the *submitted* lines. That is not its position in
 *     `state.current` as soon as one destination is left empty — which is the normal
 *     case, and a mismatch that predates the contract fork.
 *   * **the draft entry travels last**, at `entries[state.entries.length]`. §9's paths are
 *     rooted at the request body, so a bare `current[i].qty_kg` key can never match one.
 *
 * Both failures are silent: no error, no console warning, just the generic banner.
 */
function draftFieldPaths() {
  const entryIndex = state.entries.length
  let sent = 0
  return normaliseLines(state.current).map(line => (Number(line.qtyKg) > 0 ? `entries[${entryIndex}].current[${sent++}].qty_kg` : null))
}

function destinationRows() {
  const allocationExcess = exceedsTotal(allocatedAmount(state.current), Number(state.totalAmount || 0))
  const paths = draftFieldPaths()
  return state.current.map((line, index) => {
    const destination = selected(state.taxonomy.destinations, line.destination)
    const serverError = paths[index] ? state.fieldErrors[paths[index]] : null
    const invalid = Boolean(serverError) || (line.qtyInput !== '' && Number(line.qtyInput) < 0) || (allocationExcess && state.lastChangedDestination === line.destination)
    return `<div class="destination-row ${invalid ? 'invalid' : ''}"><label for="destination-${line.id}">${escapeHtml(destination?.name || line.destination)}${destination?.description ? `<small>${escapeHtml(destination.description)}</small>` : ''}</label><div class="amount-with-unit"><input id="destination-${line.id}" data-line-field="amount" data-line-id="${line.id}" type="number" inputmode="decimal" min="0" step="0.01" value="${escapeHtml(line.qtyInput)}" ${invalid ? 'aria-invalid="true"' : ''} aria-label="${escapeHtml(destination?.name || line.destination)} amount in ${escapeHtml(state.totalUnit)}"><span>${escapeHtml(state.totalUnit)}</span></div>${serverError ? `<p class="field-error" role="alert">${escapeHtml(serverError)}</p>` : ''}</div>`
  }).join('')
}

function destinationStep() {
  const total = Number(state.totalAmount) || 0
  const allocated = allocatedAmount(state.current)
  const summaryInvalid = exceedsTotal(allocated, total) || state.current.some(line => line.qtyInput !== '' && Number(line.qtyInput) < 0)
  const canContinue = allocated > 0 && !summaryInvalid && !state.current.some(line => line.qtyInput && !decimalPattern.test(line.qtyInput))
  return `<section class="content-section wide" aria-labelledby="destination-title"><p class="eyebrow">Step 4</p><h1 id="destination-title">Where did the food waste go?</h1><p class="section-intro">Enter an amount for every applicable destination. The combined amount cannot exceed your total waste.</p>
    <div class="allocation-summary ${summaryInvalid ? 'invalid' : ''}" id="current-summary" aria-live="polite"><div><span>Total waste</span><strong>${formatNumber(total, 2)} ${escapeHtml(state.totalUnit)}</strong></div><div><span>Allocated</span><strong data-summary="allocated">${formatNumber(allocated, 2)} ${escapeHtml(state.totalUnit)}</strong></div><div><span>Remaining</span><strong data-summary="remaining">${formatNumber(remainingAmount(total, allocated), 2)} ${escapeHtml(state.totalUnit)}</strong></div></div>
    <div class="destination-list">${destinationRows()}</div>
    <p class="field-error" id="allocation-error" role="alert">${escapeHtml(state.error || '')}</p>${buttonRow(2, 'Continue', !canContinue, 'continue')}</section>`
}

function reviewLines(entry) {
  const lines = normaliseEntryLines(entry).filter(line => Number(line.qtyKg) > 0)
  return `<dl class="review-destinations">${lines.map(line => {
    const destination = selected(state.taxonomy.destinations, line.destination)
    return `<div><dt>${escapeHtml(destination?.name || line.destination)}</dt><dd>${formatNumber(line.qtyInput, 2)} ${escapeHtml(entry.totalUnit)} <small>(${formatNumber(line.qtyKg, 3)} kg)</small></dd></div>`
  }).join('')}</dl>`
}

function normaliseEntryLines(entry) {
  return entry.current.map(line => ({ ...line, qtyKg: line.qtyInput === '' ? '' : massToKg(line.qtyInput, entry.totalUnit).toFixed(3) }))
}

function entryCard(entry, index) {
  const sector = selected(state.taxonomy.sectors, entry.sector)
  const food = selected(state.taxonomy.food_categories, entry.foodCategory)
  return `<article class="saved-entry-card"><div><span class="eyebrow">Entry ${index + 1}</span><h3>${escapeHtml(sector?.name || entry.sector)}</h3><p>${formatNumber(entry.totalAmount, 2)} ${escapeHtml(entry.totalUnit)} · ${escapeHtml(food?.name || 'Food type not provided')}</p></div><div class="card-actions"><button class="text-button" type="button" data-action="edit-entry" data-index="${index}">Edit<span class="sr-only"> entry ${index + 1}</span></button><button class="text-button danger" type="button" data-action="remove-entry" data-index="${index}">Remove<span class="sr-only"> entry ${index + 1}</span></button></div></article>`
}

function reviewStep() {
  const sector = selected(state.taxonomy.sectors, state.sector)
  const food = selected(state.taxonomy.food_categories, state.foodCategory)
  const totalKg = massToKg(state.totalAmount, state.totalUnit)
  return `<section class="content-section wide" aria-labelledby="review-title"><p class="eyebrow">Step 5</p><h1 id="review-title">Review your information</h1><p class="section-intro">Check this entry, or add another supply-chain entry before viewing the combined results.</p>
    ${state.entries.length ? `<section class="saved-entries"><div class="section-heading-row"><h2>Added entries</h2><span>${state.entries.length}</span></div>${state.entries.map(entryCard).join('')}</section>` : ''}
    <div class="section-heading-row current-entry-heading"><h2>Current entry ${state.entries.length + 1}</h2><span>Ready to calculate</span></div>
    <article class="review-block"><div class="section-heading-row"><h2>Supply-chain stage</h2><button class="text-button" type="button" data-action="go-step" data-step="0">Edit</button></div><p>${escapeHtml(sector?.name || state.sector)}</p></article>
    <article class="review-block"><div class="section-heading-row"><h2>Food category</h2><button class="text-button" type="button" data-action="go-step" data-step="1">Edit</button></div><p>${escapeHtml(food?.name || 'Standard mix / not specified')}</p></article>
    <article class="review-block"><div class="section-heading-row"><h2>Waste amount</h2><button class="text-button" type="button" data-action="go-step" data-step="2">Edit</button></div><p><strong>${formatNumber(state.totalAmount, 2)} ${escapeHtml(state.totalUnit)}</strong> · ${formatNumber(totalKg, 3)} kg</p></article>
    <article class="review-block"><div class="section-heading-row"><h2>Waste destinations</h2><button class="text-button" type="button" data-action="go-step" data-step="3">Edit</button></div>${reviewLines(draftEntry())}</article>
    <button class="button button-add add-entry-button" type="button" data-action="add-entry">+ Add another supply-chain entry</button>
    <aside class="disclaimer compact" aria-label="Important information"><span class="info-icon" aria-hidden="true">i</span><div><strong>Estimate notice</strong><p>Demonstration only — verified calculation factors have not yet been supplied. Final results will depend on factors supplied and approved by Kai Commitment.</p></div></aside>
    ${state.error ? `<p class="field-error api-error ${state.errorCode ? `error-${slug(state.errorCode)}` : ''}" role="alert">${escapeHtml(state.error)}</p>` : ''}${buttonRow(3, state.loading ? 'Calculating…' : Date.now() < state.rateLimitedUntil ? 'Try again shortly' : state.entries.length ? `Calculate results for ${state.entries.length + 1} entries` : 'Calculate impact', state.loading || Date.now() < state.rateLimitedUntil || blocked(), 'calculate')}</section>`
}

function validateCurrentStep() {
  if (state.step === 0 && !state.sector) return 'Select where in the food supply chain the waste occurred.'
  if (state.step === 2) {
    if (!state.totalAmount || Number(state.totalAmount) <= 0) return 'Waste amount must be greater than zero.'
    if (!decimalPattern.test(state.totalAmount)) return 'Enter no more than two decimal places.'
  }
  if (state.step === 3) {
    const total = Number(state.totalAmount)
    const lines = state.current
    if (!lines.some(line => Number(line.qtyInput) > 0)) return 'Enter an amount for at least one waste destination.'
    if (lines.some(line => line.qtyInput !== '' && (Number(line.qtyInput) < 0 || !Number.isFinite(Number(line.qtyInput))))) return 'Destination amounts must be zero or greater.'
    if (lines.some(line => line.qtyInput && !decimalPattern.test(line.qtyInput))) return 'Enter destination amounts to no more than two decimal places.'
    const sum = allocatedAmount(lines)
    if (exceedsTotal(sum, total)) return `Allocated waste exceeds total waste by ${(sum - total).toFixed(2)} ${state.totalUnit === 'kilograms' ? 'kg' : 'tonnes'}.`
  }
  return ''
}

function buildLines(entry) {
  return normaliseEntryLines(entry).filter(line => Number(line.qtyKg) > 0).map(line => ({ destination: line.destination, qty_kg: line.qtyKg }))
}

let reloadTaxonomy = null

// §9.2: a `BLOCKED` caller will never be served, so every retry affordance has to go —
// a "Try again" button that cannot ever succeed is worse than none. This is the opposite
// of `RATE_LIMITED`, which keeps its 60-second re-enable.
const blocked = () => state.errorCode === 'BLOCKED'

// `BLOCKED` is terminal, so a step change must not wipe the banner: that would leave the
// user looking at a permanently disabled Calculate button with no text saying why.
const clearedError = () => (blocked() ? {} : { error: null, errorCode: null })

function publicError(error) {
  switch (error.code) {
    case 'VALIDATION_ERROR': return validationMessage(error)
    case 'UNKNOWN_CODE': return 'Calculator options have changed. The latest options are being loaded; please review your selections and try again.'
    case 'RATE_LIMITED': return 'Too many calculations have been requested. Please wait 60 seconds and try again.'
    case 'FORMULA_ERROR': return 'The calculator could not produce a result because its calculation configuration needs attention. Please try again later.'
    // §9: `ENGINE_UNAVAILABLE` means the calculator cannot run at all right now rather
    // than that this request was bad, which is `NO_PUBLISHED_FACTOR_SET`'s situation and
    // takes the same copy.
    case 'ENGINE_UNAVAILABLE':
    case 'NO_PUBLISHED_FACTOR_SET': return 'The calculator is currently under maintenance because no factor set is available.'
    // §9.2: `message` never varies, is written for end users, and is the whole of what a
    // refused caller is owed. Do not add copy suggesting a retry.
    case 'BLOCKED': return error.message || 'This request was refused. If you believe this is an error, contact the Kai Commitment team.'
    default: return error.message || 'The calculation could not be completed.'
  }
}

/**
 * The banner text for a 400.
 *
 * `destinationRows` shows every detail that names a row of the entry on screen against
 * that row, so the banner only has to point at them. A detail that names anything else —
 * a saved entry, an `alternative`, a field this form has no input for — has no box to
 * attach to and would otherwise vanish entirely, so it is spelled out here instead.
 */
function validationMessage(error) {
  const bound = new Set(draftFieldPaths().filter(Boolean))
  const unbound = (error.details || []).filter(detail => !bound.has(detail.field))
  if (!unbound.length) return 'Check the highlighted fields and try again.'
  return [error.message || 'The calculation could not be completed.', ...unbound.map(describeDetail)].join(' ')
}

// §9's path is rooted at the request body and starts `entries[N]`, where N is the
// submission-order index. The user counts entries from one.
function describeDetail(detail) {
  const entry = /^entries\[(\d+)\]/.exec(detail.field || '')
  const message = detail.message || 'This value could not be accepted.'
  return entry ? `Entry ${Number(entry[1]) + 1}: ${message}` : message
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
  return Object.fromEntries((error.details || []).map(detail => [detail.field, detail.message || 'This value could not be accepted.']))
}

async function submitCalculation() {
  if (Date.now() < state.rateLimitedUntil) return
  setState({ loading: true, error: null, errorCode: null, fieldErrors: {} })
  try {
    // §6.2: the whole submission travels in one call. One request per entry would let
    // §5.3's token upsert overwrite every entry but the last, cost N× the rate limit,
    // and leave the earlier entries persisted when a later one fails.
    const entries = [...state.entries, draftEntry()]
    const response = await calculate({
      token: state.token || null,
      gwp_horizon: state.gwpHorizon,
      entries: entries.map(entry => ({
        sector: entry.sector,
        food_category: entry.foodCategory || null,
        current: buildLines(entry),
        alternative: null,
      })),
    })
    const token = response.token || state.token
    if (token) sessionStorage.setItem('kaiCalculatorToken', token)
    setState({ result: { ...response, entry_results: entryResultsFrom(entries, response) }, token, loading: false, step: 5, error: null, errorCode: null, fieldErrors: {} })
  } catch (error) {
    const rateLimitedUntil = error.code === 'RATE_LIMITED' ? Date.now() + 60000 : state.rateLimitedUntil
    if (error.code === 'UNKNOWN_CODE' && reloadTaxonomy) await reloadTaxonomy({ preserveError: true })
    const errorStep = error.code === 'VALIDATION_ERROR' ? 3 : error.code === 'UNKNOWN_CODE' ? 0 : state.step
    setState({ loading: false, error: publicError(error), errorCode: error.code || 'UNKNOWN_ERROR', fieldErrors: fieldErrorMap(error), rateLimitedUntil, step: errorStep })
    if (error.code === 'RATE_LIMITED') setTimeout(() => setState({ rateLimitedUntil: 0 }), 60000)
  }
}

function updateLine(control) {
  const lineId = control.dataset.lineId
  state.current = state.current.map(line => line.id === lineId ? { ...line, qtyInput: control.value } : line)
  // The server named its failing lines by their position among the lines that were sent
  // (§9), and blanking or filling a row changes which lines would be sent at all. The
  // stored paths stop meaning what they meant, so they go rather than move to a
  // neighbouring row; the highlights they drew are cleared below with the rest.
  state.fieldErrors = {}
  state.lastChangedDestination = state.current.find(line => line.id === lineId)?.destination || null
  const total = Number(state.totalAmount) || 0
  const sum = allocatedAmount(state.current)
  const summary = document.getElementById('current-summary')
  const hasNegative = state.current.some(line => line.qtyInput !== '' && Number(line.qtyInput) < 0)
  summary?.classList.toggle('invalid', exceedsTotal(sum, total) || hasNegative)
  if (summary) {
    summary.querySelector('[data-summary="allocated"]').textContent = `${sum.toFixed(2)} ${state.totalUnit}`
    summary.querySelector('[data-summary="remaining"]').textContent = `${remainingAmount(total, sum).toFixed(2)} ${state.totalUnit}`
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
}

function loadEntry(entry) {
  setState({ sector: entry.sector, foodCategory: entry.foodCategory, totalAmount: entry.totalAmount, totalUnit: entry.totalUnit, current: entry.current.map(line => ({ ...line, id: randomId() })), alternative: [], step: 0, error: null, fieldErrors: {}, lastChangedDestination: null })
}

function clearDraft() {
  setState({ sector: null, foodCategory: null, totalAmount: '', totalUnit: 'kilograms', current: [], alternative: [], step: 0, error: null, fieldErrors: {}, expandedSectors: [], lastChangedDestination: null })
}

export function render(main) {
  main.className = `main-content${state.step === -1 ? ' introduction-main' : ''}`
  if (state.loading && !state.taxonomy) {
    main.innerHTML = '<section class="content-section"><p class="loading-state" role="status">Loading calculator options…</p></section>'
    return
  }
  if (!state.taxonomy) {
    // §9.2: `BLOCKED` is the one failure here that a retry can never clear, so the button
    // is withheld rather than disabled — the message is the whole of the response.
    main.innerHTML = `<section class="content-section error-state"><h1>Calculator unavailable</h1><p>${escapeHtml(state.error || 'The taxonomy could not be loaded.')}</p>${blocked() ? '' : '<button class="button button-primary" type="button" data-action="retry">Try again</button>'}</section>`
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
    if (action === 'start') setState({ step: 0, ...clearedError() })
    if (action === 'go-step') setState({ step: Number(control.dataset.step), ...clearedError() })
    if (action === 'toggle-sector') {
      const code = control.dataset.sector
      setState({ expandedSectors: state.expandedSectors.includes(code) ? state.expandedSectors.filter(item => item !== code) : [...state.expandedSectors, code] })
    }
    if (action === 'clear-food') setState({ foodCategory: null })
    if (action === 'continue') {
      const error = validateCurrentStep()
      if (error) setState({ error })
      else if (state.step === 2) setState({ step: 3, error: null, current: state.current.length ? normaliseLines(state.current) : entryDestinations().map(destination => createLine(destination.code)) })
      else setState({ step: state.step + 1, error: null })
    }
    if (action === 'add-entry') {
      setState({ entries: [...state.entries, draftEntry()] })
      clearDraft()
    }
    if (action === 'edit-entry') {
      const index = Number(control.dataset.index)
      const entry = state.entries[index]
      setState({ entries: state.entries.filter((_, entryIndex) => entryIndex !== index) })
      loadEntry(entry)
    }
    if (action === 'remove-entry') {
      const index = Number(control.dataset.index)
      setState({ entries: state.entries.filter((_, entryIndex) => entryIndex !== index), error: null })
    }
    if (action === 'calculate') submitCalculation()
    if (action === 'start-over' && window.confirm('Clear all calculator data and return to the introduction?')) resetCalculator()
    if (action === 'download-results') downloadResults(state)
    if (action === 'breakdown-tab') setState({ resultBreakdownTab: control.dataset.tab })
    if (action === 'explore-improvements') openImprovement(state)
    if (action === 'reset-improvement') resetImprovement(state)
    if (action === 'cancel-improvement') setState({ improvementOpen: false, improvementResult: null, improvementError: null })
    if (action === 'compare-improvement') compareImprovement(state)
    if (action === 'retry' && !blocked()) retryTaxonomy()
    if (action === 'view-methodology') window.location.href = './methodology.html'
    if (['start', 'go-step', 'continue', 'add-entry', 'edit-entry', 'calculate', 'start-over', 'retry', 'view-methodology'].includes(action)) {
      window.scrollTo({ top: 0, behavior: 'smooth' })
    }
  })

  main.addEventListener('change', event => {
    const target = event.target
    if (target.name === 'sector') setState({ sector: target.value, error: null })
    if (target.name === 'food-category') setState({ foodCategory: target.value })
    if (target.id === 'total-unit') setState({ totalUnit: target.value, error: null, current: [] })
  })

  main.addEventListener('input', event => {
    const target = event.target
    if (target.id === 'total-waste') {
      state.totalAmount = target.value
      state.error = null
    }
    if (target.matches('[data-line-field="amount"]')) updateLine(target)
    if (target.matches('[data-improvement-code]')) updateImprovementInput(target, state)
  })

  main.addEventListener('keydown', event => {
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
