const storedToken = sessionStorage.getItem('kaiCalculatorToken')

export const state = {
  taxonomy: null,
  token: storedToken,
  sector: null,
  foodCategory: null,
  gwpHorizon: 100,
  totalAmount: '',
  totalUnit: 'kilograms',
  // §7.2's key list, and nothing beyond it. `alternative: []` and `compareAlternative: false`
  // stood here and in `resetCalculator` below, were assigned `[]` / never assigned by two
  // functions in `calculator.js`, and were read by nothing: the alternative scenario is built
  // from `improvedAllocations` by `improvement.js`, which never looks at either. A state key
  // that is initialised and reset but never populated reads as a feature under construction.
  current: [],
  entries: [],
  result: null,
  loading: true,
  error: null,
  errorCode: null,
  fieldErrors: {},
  rateLimitedUntil: 0,
  step: -1,
  expandedSectors: [],
  resultBreakdownTab: 'stage',
  lastChangedDestination: null,
  improvementOpen: false,
  improvedAllocations: {},
  improvementResult: null,
  improvementLoading: false,
  improvementError: null,
}

/**
 * Pairs the entries the user typed with the per-entry results §6.2 returns, which
 * preserve request order. Each paired `response` is the shape the rendering modules
 * already consume — one entry's `current` / `alternative` / `net_benefit`, plus the
 * submission-level `factor_set` they read `is_mock` and `version_label` from.
 *
 * Temporary adapter: `state.result` also carries the whole response, so once the
 * rendering modules read `totals` and `entries` directly this helper goes away.
 *
 * @param {Array<object>} entries Draft entries, in the order they were sent
 * @param {object} response The §6.2 response
 * @returns {Array<{entry: object, response: object}>}
 */
export function entryResultsFrom(entries, response) {
  return entries.map((entry, index) => ({
    entry,
    response: {
      ...(response.entries?.[index] || {}),
      factor_set: response.factor_set,
      factor_source: response.factor_source,
      gwp_horizon: response.gwp_horizon,
    },
  }))
}

const subscribers = new Set()

export function setState(patch) {
  Object.assign(state, patch)
  subscribers.forEach(subscriber => subscriber(state))
}

export function subscribe(fn) {
  subscribers.add(fn)
  return () => subscribers.delete(fn)
}

export function resetCalculator() {
  sessionStorage.removeItem('kaiCalculatorToken')
  setState({
    token: null,
    sector: null,
    foodCategory: null,
    totalAmount: '',
    totalUnit: 'kilograms',
    current: [],
    entries: [],
    result: null,
    error: null,
    errorCode: null,
    fieldErrors: {},
    rateLimitedUntil: 0,
    step: -1,
    expandedSectors: [],
    resultBreakdownTab: 'stage',
    lastChangedDestination: null,
    improvementOpen: false,
    improvedAllocations: {},
    improvementResult: null,
    improvementLoading: false,
    improvementError: null,
  })
}
