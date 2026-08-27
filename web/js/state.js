const storedToken = sessionStorage.getItem('kaiCalculatorToken')

export const state = {
  taxonomy: null,
  token: storedToken,
  sector: null,
  foodCategory: null,
  gwpHorizon: 100,
  totalAmount: '',
  totalUnit: 'kilograms',
  // §7.3's container input. `measureMode` says which of the two step-3 fields is
  // authoritative; `unitPreset` is a `taxonomy.unit_presets` code and `unitCount` is the
  // raw string the visitor typed, held raw for the same reason `qtyInput` is — nothing
  // rounds until `units.js` converts it for the API.
  //
  // **`measureMode: 'container'` implies `totalUnit: 'kilograms'`, and `calculator.js`
  // maintains that.** `totalUnit` is the unit step 4 allocates in, and a destination row
  // reading "0.37 wheelie bins" is not a thing anyone can enter; containers estimate the
  // total and the total is then a mass.
  measureMode: 'mass',
  unitPreset: null,
  unitCount: '',
  // Item ④: the site's production total for the period, in `totalUnit` - the
  // same unit the waste amount above it is in, never a second unit of its
  // own. Optional and carried raw, like `totalAmount`; §6.2's engine never
  // reads it, it travels only so the results page can state waste as a share
  // of production.
  totalInputKg: '',
  // Item ⑤: what the stage's production was worth, and what the wasted portion
  // was worth, both in New Zealand dollars. Statistics only - §6.2's engine
  // never reads either, and cost price versus retail price is the client's own
  // question, not this calculator's. Optional and carried raw, like
  // `totalInputKg` above; an empty string must reach the API as absent, never
  // as zero, so nothing here ever defaults to `'0'`.
  totalValueNzd: '',
  wastedValueNzd: '',
  // Item ⑦: the span the whole submission's figures cover - one value for
  // every entry, not one per supply-chain stage, so it lives beside the
  // review of the whole submission rather than inside the per-entry loop.
  // `''` means the visitor did not choose and must reach the API as absent,
  // never as a guess and never as a default period; the four non-empty
  // values are exactly what §6.2 accepts as `time_frame`, and the engine is
  // never given it.
  timeFrame: '',
  // §7.2's key list, and nothing beyond it. `alternative: []` and `compareAlternative: false`
  // stood here and in `resetCalculator` below, were assigned `[]` / never assigned by two
  // functions in `calculator.js`, and were read by nothing: the alternative scenario is built
  // from `improvedAllocations` by `improvement.js`, which never looks at either. A state key
  // that is initialised and reset but never populated reads as a feature under construction.
  //
  // Each line is `{id, destination, qtyInput, unit}`. `unit` is the one field this list did
  // not have before item ⑥: a destination row used to borrow `state.totalUnit` for every row
  // of an entry, and a site that knows its landfill figure in tonnes and its animal-feed
  // figure in crates could not say so. `unit` holds exactly the value `#total-unit` (or a
  // row's own select) would carry — `'kilograms'`, `'tonnes'`, or `preset:<unit_preset.code>`
  // — and `calculator.js` converts each row with *its own* `unit`, never the entry's.
  //
  // **A line built before this field existed has no `unit`, and every reader falls back to
  // the entry's own total unit rather than the global default.** That is `state.totalUnit`
  // for the draft in `state.current`, and `entry.totalUnit` for a saved entry in
  // `state.entries` — the unit that row's figures were actually typed against — so a visitor
  // whose entry predates this change is never silently reinterpreted into a different unit.
  current: [],
  entries: [],
  result: null,
  loading: true,
  error: null,
  errorCode: null,
  fieldErrors: {},
  rateLimitedUntil: 0,
  // **-1 is the introduction screen, and it is the state this page opens in.** For one
  // week this was `0` - `/` served `home.html` and the calculator opened on the first
  // question. `home.html` is retired and `/` serves this page again, so the landing
  // screen is `introduction()` in `web/js/calculator.js`, and `resetCalculator` below
  // returns here. Anything that renders by index into `screens` must therefore guard
  // -1 first; `render()` does.
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
 * The entry being typed, as a saved entry.
 *
 * **One definition, because two modules build submissions.** `calculator.js` held this
 * privately and `improvement.js` had its own five-field copy of it, and the copy was
 * missing exactly the fields round two added — so pressing Compare Impact re-sent the
 * submission with `total_input_kg`, `total_value_nzd` and `wasted_value_nzd` absent, and
 * §5.3's token upsert wrote the absence over the figures the visitor had entered. A copy
 * of a shape is where the next field will go missing too, so there is no longer a copy.
 *
 * Every key here is something the visitor entered; nothing derived and nothing from the
 * API. `current` is copied row by row so a later edit of the draft cannot reach into an
 * entry already added to the list.
 *
 * @returns {object}
 */
export const draftEntry = () => ({
  sector: state.sector,
  foodCategory: state.foodCategory,
  totalAmount: state.totalAmount,
  totalUnit: state.totalUnit,
  measureMode: state.measureMode,
  unitPreset: state.unitPreset,
  unitCount: state.unitCount,
  totalInputKg: state.totalInputKg,
  totalValueNzd: state.totalValueNzd,
  wastedValueNzd: state.wastedValueNzd,
  current: state.current.map(line => ({ ...line })),
})

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
    measureMode: 'mass',
    unitPreset: null,
    unitCount: '',
    totalInputKg: '',
    totalValueNzd: '',
    wastedValueNzd: '',
    timeFrame: '',
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
