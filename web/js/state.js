const storedToken = sessionStorage.getItem('kaiCalculatorToken')

export const state = {
  taxonomy: null,
  token: storedToken,
  sector: null,
  foodCategory: null,
  gwpHorizon: 100,
  // Whether the visitor has toggled the results page's section nav, and
  // `undefined` while they have not -- which is the usual case and means "let
  // the stylesheet decide". The default is viewport-dependent (open where
  // there is a gutter to put it in, absent where there is not) and CSS is
  // what knows the viewport; a boolean default here would have to guess at
  // render time and would be wrong on the first paint after a resize.
  //
  // It lives on `state` rather than on the element because `render()` does
  // `main.innerHTML = ...` on every `setState`: the DOM copy was wiped by any
  // unrelated change, so opening the menu and then switching a breakdown tab
  // closed it.
  resultsNavOpen: undefined,
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
  // **How the visitor got to the step they are on, when they did not walk there** —
  // `null`, or `{from, step, entries?, draft?, kind?, after?}`. Written by the
  // navigation that performs the jump, read only by that step's own Back, cleared on the
  // next arrival at the review step and by `resetCalculator` below.
  //
  // It cannot be derived. `state.entries.length` cannot tell an add from an edit from a
  // walk backwards, and by the time step 1 renders the evidence is already gone:
  // `add-entry` has pushed the draft into `entries` and emptied it, and `edit-entry` has
  // removed an entry and overwritten the draft with it. So the jump records what it is
  // about to move, at the one moment that is still knowable.
  //
  //   * `from` — the screen index this marker is about. It is what keeps the marker from
  //     firing on a *different* step's Back: the food step's Back emits the same
  //     `data-action="go-step" data-step="0"` that step 1's own Back does, and a marker
  //     keyed on the target rather than on the origin would ping-pong between the two
  //     with the introduction unreachable.
  //   * `step` — where Back goes. It is `4` for a jump made from the review step and `1`
  //     for one made from step 2's duplicate notice; it records where the visitor stood,
  //     never a constant.
  //   * `kind` / `after` — what the Back this marker belongs to has to ask
  //     before it discards anything. `kind` is `'add'` or `'edit'` and picks between two
  //     messages, because a chain being started and alterations to a saved entry are
  //     different losses. A third field, `number`, recorded the entry number the visitor
  //     clicked so that an edit's question could name it; it is gone, because
  //     `edit-entry` now puts the displaced draft at that same index and the number
  //     therefore addresses a card the question is not about — see `discardPrompt` in
  //     `calculator.js`. `after` is a fingerprint of the
  //     entries and the draft **as the jump left them**, and it is the whole of the test
  //     for whether the undo would throw anything away: equal to now means every
  //     keystroke since is absent and the restore is a no-op, so nothing is asked. It is
  //     a separate field from `entries`/`draft` above because those record the state
  //     *before* the jump and this one records the state *after* it — the dialog fires on
  //     the difference between `after` and now, never on the difference between before
  //     and after, which is the jump itself and is exactly what Back exists to reverse.
  //     Present only alongside `draft`; a marker that moved nothing never asks.
  //   * `entries` / `draft` — present only when the navigation itself *moved* something,
  //     so that backing out can put it back. A snapshot rather than a per-caller inverse,
  //     because `edit-entry` already has two call sites and the displaced draft cannot be
  //     reconstructed from anything that survives.
  //
  //     **They are an undo, never the only copy of anything.** `edit-entry` once left the
  //     chain it displaced here and nowhere else, so walking forward — where `continue`
  //     drops the marker on the 3 -> 4 move, and `start` and `submitCalculation` drop it
  //     too — destroyed a complete supply-chain entry with nothing shown. It now trades a
  //     complete draft onto the saved list instead, where the visitor can see it and no
  //     navigation can drop it. Anything added here has to hold that line: a marker may
  //     be discarded at any moment, so what only it remembers is what the visitor loses.
  //
  //     `entries` is a shallow copy; the entry
  //     objects inside it are shared, which is safe only while nothing mutates an entry in
  //     place (today `calculator.js` replaces the array every time).
  //
  // **One slot, so the undo is one deep.** A jump made while a marker is live overwrites
  // it. Nothing is lost when that happens — the outer jump's own mutation stays applied —
  // but the outer undo is gone, and the visitor is back to walking out of the wizard.
  //
  // **A marker holding an undo survives a forward walk, and the confirmation is why that
  // is safe.** A visitor may press Add, build a whole second chain across four screens
  // and walk Back to step 1 with the marker still live. Backing out there is a real
  // discard, so `goToStep` (`calculator.js`) asks before it runs — and only when `after`
  // says there is something to discard, so pressing Add and immediately pressing Back is
  // silent.
  //
  // **A marker holding no undo does not survive one**, and must not: a review *Edit*
  // link's marker moved nothing, so it exists only to point one step's Back at the
  // review step. Left live once that step had been walked past it aimed Back *forward*,
  // and step 1 and the introduction became unreachable by any number of Back presses.
  // `markerAfterLeaving` (`calculator.js`) is where the two are told apart, on `draft`.
  returnTo: null,
  expandedSectors: [],
  resultBreakdownTab: 'stage',
  lastChangedDestination: null,
  improvementOpen: false,
  improvedAllocations: {},
  improvementChartExpanded: false,
  // Item ⑧'s toggle. `improvedAllocations` stays percentages in every mode — see the note
  // on `updateImprovementInput` in `improvement.js` — so this only ever decides which unit
  // the sliders and boxes *display*, never what they store. `'percentage'` or `'unit'`;
  // renamed from `'kilograms'` when the second mode grew a per-row unit choice instead of
  // being kilograms specifically.
  improvementMode: 'percentage',
  // Item ⑧'s per-row unit, in unit mode only: destination `code` -> `'kilograms'`,
  // `'tonnes'`, or `preset:<unit_preset.code>` — the same value space a step-4 row's own
  // unit uses. A destination with no entry here falls back to kilograms (`rowUnitFor` in
  // `improvement.js`), so this only ever needs a key once a visitor changes a row away
  // from the default. Nothing here changes what `improvedAllocations` means; it decides
  // only how one row's own share is *displayed*.
  improvementRowUnits: {},
  improvementResult: null,
  improvementLoading: false,
  improvementError: null,
  // §6.2.2's opt-in. `contributed` is the visitor's own choice and starts false on
  // every fresh calculation — `resetCalculator` below returns here — and deliberately
  // is NOT reset by a recalculation on the same token: see the note over
  // `contributeBlock` in `results.js` for why that is correct rather than an oversight.
  contributing: false,
  contributed: false,
  contributeError: null,
  // Task 4's flower, and nothing else. `contributed` above is the durable choice and
  // survives every re-render for as long as it holds; this is a one-shot flourish tied
  // to the exact transition into it. `render()` (`calculator.js`) replaces `<main>`
  // wholesale on every `setState` (`main.js`), so a flower keyed on `contributed` alone
  // would re-bloom on every unrelated re-render for as long as the box stayed ticked -
  // opening the improvement panel, say. `contributeCalculation` (`results.js`) sets this
  // true on success and clears it itself once the animation's own duration has passed;
  // see that function for why a timeout-cleared flag rather than a CSS one-shot.
  contributeCelebrating: false,
  // The PDF button (`results.js`'s `downloadPdf`). Unlike `contributed` above this never
  // needs to survive past its own request: there is nothing to remember about a document
  // once it has downloaded, only whether one is in flight right now.
  pdfExporting: false,
  pdfError: null,
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
    // Cleared with everything else, and it has to be: a marker that outlived Clear would
    // let Back resurrect entries the visitor had just deleted.
    returnTo: null,
    expandedSectors: [],
    resultBreakdownTab: 'stage',
    lastChangedDestination: null,
    improvementOpen: false,
    improvedAllocations: {},
    improvementChartExpanded: false,
    improvementMode: 'percentage',
    improvementRowUnits: {},
    improvementResult: null,
    improvementLoading: false,
    improvementError: null,
    // A genuinely new calculation, on a token that does not exist yet — unlike a
    // recalculation on the same token, there is no earlier consent for this one to
    // carry forward, so it is the one place `contributed` is cleared.
    contributing: false,
    contributed: false,
    contributeError: null,
    contributeCelebrating: false,
    pdfExporting: false,
    pdfError: null,
  })
}
