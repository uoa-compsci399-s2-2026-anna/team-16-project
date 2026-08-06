const storedToken = sessionStorage.getItem('kaiCalculatorToken')

export const state = {
  taxonomy: null,
  token: storedToken,
  sector: null,
  foodCategory: null,
  gwpHorizon: 100,
  totalAmount: '',
  totalUnit: 'kilograms',
  current: [],
  alternative: [],
  compareAlternative: false,
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
    alternative: [],
    compareAlternative: false,
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
