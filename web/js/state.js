// **The one import this module has, and why it is safe.** `state.js` is the module every
// other one may import (see `entryLeaves`), which is only true while it imports nothing
// that imports it back. `i18n.js` imports nothing at all, so `t` closes no cycle - and
// `leafDisplayName` below needs it, because a leaf's name is the one piece of state that
// is also a sentence.
import { t } from './i18n.js'

const storedToken = sessionStorage.getItem('kaiCalculatorToken')

export const state = {
  taxonomy: null,
  token: storedToken,
  sector: null,
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
  // **The fork.** One supply-chain chain may name several food categories, and each
  // one carries its own amount AND its own destination allocation — see
  // `entryLeaves` below, which is the single definition of what a leaf is.
  //
  //   * `foodCategories` — category `code`s, **in ticking order**, which is the order
  //     steps 3 and 4 iterate. `[]` means the optional step was not answered.
  //   * `foodUnspecified` — the explicit "I do not know / not broken down by type"
  //     answer. A separate boolean rather than a `null` member of `foodCategories`:
  //     a `null` inside an array of codes is one `.filter(Boolean)` from silently
  //     disappearing, and `escapeHtml(null)` renders the string `"null"`.
  //   * `foodItems` — reserved for step 2.5 (`design.md` §3), category code -> item
  //     codes. `entryLeaves` already reads it, so the leaf rule does not move when
  //     the item level lands; nothing writes it yet.
  foodCategories: [],
  foodUnspecified: false,
  foodItems: {},
  // Which panel of step 2 is showing: 'categories' or 'items' (step 2.5).
  //
  // A panel rather than a step number, because §3.3's step 2.5 *refines* step 2 --
  // and because it is absent from most deployments. A seventh step would have to
  // renumber twenty hard-coded references and would make "step 3 of 7" untrue
  // whenever `item_level_enabled` is off, which is every deployment today.
  //
  // Reset to 'categories' by anything that lands on step 2 from outside, so a jump
  // back to "food type" asks the category question -- which is the one that name
  // refers to.
  foodStage: 'categories',
  gwpHorizon: 100,
  // **Narrowed by the fork to "the unit the chain's combined figures are stated in".**
  // Every amount the visitor types now belongs to a leaf and is measured in that
  // leaf's own `totalUnit` (inside `leafFigures`), so this is no longer the unit
  // anything is entered in. It is fixed at the 3 -> 4 move to the leaves' common mass
  // unit when they share one and `'kilograms'` otherwise, and nothing re-derives it
  // afterwards — a chain unit that changed under a row already saved with an explicit
  // `unit` would reinterpret that row, which is exactly the hazard the note on
  // `current` below documents.
  totalUnit: 'kilograms',
  // **Every per-leaf figure, keyed by the leaf's own identity.** `leafKey(leaf)` ->
  // `{totalAmount, totalUnit, measureMode, unitPreset, unitCount, totalInputKg,
  // totalValueNzd, wastedValueNzd, current}` — the nine keys that were flat on this
  // object before the fork, one set per leaf.
  //
  // **A keyed map and not an array.** The leaf list is derived from
  // `foodCategories`/`foodItems`, so an array would have to be reindexed on every tick
  // and untick, and unticking the middle category would slide the third category's
  // money figures onto the second. A map keyed by the leaf's own identity cannot do
  // that.
  //
  // **The three scalars fork too, and that is not a style choice.**
  // `engine/calculate.py:369` sums every entry's `total_input_kg` as the
  // production-share denominator, so copying one chain's figure onto every leaf
  // divides the share by N; `:506` derives `value_per_kg` per entry, so it overstates
  // `saving_nzd` by roughly N. Attaching them to leaf 0 alone makes `_across_entries`
  // (`:410`, "Partial coverage is not absence") report "incomplete" forever.
  //
  // **`current` is in here too, because step 4 forks as well** (`design.md` §10). The
  // request body already carries one `current[]` per entry, so a per-leaf allocation
  // is the shape the contract expects; a shared split would have to be *derived* into
  // each entry pro-rata, and three-decimal `qty_kg` rounding can then leave the leaves'
  // lines not summing to the figure the visitor typed.
  leafFigures: {},
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
  //
  // **Since the fork this list lives inside `leafFigures[key].current`, one per leaf**,
  // and the "entry's own total unit" it falls back to is that leaf's. Everything else in
  // this note is unchanged.
  entries: [],
  result: null,
  loading: true,
  error: null,
  // **Where `error` belongs, when it belongs to one leaf's field.** `{leaf, field}` or
  // `null`. Before the fork `amountStep` decided which input a client-side message was
  // about by comparing `state.error` against the string each validator returns; with N
  // leaves two of them produce the byte-identical sentence ("Waste amount must be
  // greater than zero.") and the highlight landed on whichever card was asked first.
  // String identity cannot survive N leaves, so the place is recorded beside the
  // sentence instead of inferred from it. `leaf` is a `leafKey`; `field` is one of
  // `amount`, `wastedValue`, `allocation`.
  errorAt: null,
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
  // **One allocation PER LEAF, in submission order** (`design.md` §10, owner decision
  // 6). It was one submission-wide `{destinationCode: percentString}` map applied to
  // every leaf's own mass, which made *Match the current allocation* stop being an
  // identity the moment a chain forked: 100 kg of dairy sent entirely to landfill and
  // 200 kg of fruit sent entirely to animal feed came back as one 33/67 split applied
  // to both, describing neither. An array and not a map keyed by the leaf's name,
  // because two chains may legitimately carry the same sector and the same food — the
  // duplicate notice warns about it and does not forbid it — so position in the
  // submission is the only identity a leaf has here, and it is the same position
  // `entries[]` and `entry_results[]` use.
  improvedAllocations: [],
  // `null` is closed and an index is the leaf whose donut is expanded. Not a boolean:
  // the panel forks, so there is one chart per food, and leaf 0 is a real answer that a
  // boolean would read as closed.
  improvementChartExpanded: null,
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
export const draftEntry = () => {
  const chain = {
    sector: state.sector,
    foodCategories: [...(state.foodCategories || [])],
    foodUnspecified: Boolean(state.foodUnspecified),
    foodItems: Object.fromEntries((state.foodCategories || []).map(code => [code, [...((state.foodItems || {})[code] || [])]])),
    totalUnit: state.totalUnit,
    leafFigures: {},
  }
  // **Pruned to the leaves this chain actually has**, so figures typed for a category
  // that was later unticked can never be sent, fingerprinted, or silently restored.
  // Without it a ticked-filled-unticked category leaves a ghost in `leafFigures` that
  // `entryFingerprint` still reads, which makes the back-out confirmation fire on a
  // Back that discards nothing — and re-ticking the category brings its old money
  // figures back with no trace of where they came from.
  // Read off `state`, never off `chain`: `chain.leafFigures` is the empty object this
  // loop is filling, so reading from it would hand every leaf a blank record and the
  // draft would reach the wire with nothing in it.
  for (const leaf of entryLeaves(chain)) chain.leafFigures[leafKey(leaf)] = leafFigures(state, leaf)
  return chain
}

/**
 * **The leaf rule, and the only definition of it.**
 *
 * > A chain's leaves are: every ticked food item; plus every selected category with no
 * > item ticked; plus one category-less leaf when the chain names no category at all —
 * > either because nothing is ticked, or because "I do not know" is. The leaves are the
 * > chain's entries. Every element of the request body's `entries[]` is a leaf, and
 * > nothing else is.
 *
 * **It lives here and not in `calculator.js` because four modules must agree about it**
 * — `calculator.js` (steps 2, 3, 4, the review step, the duplicate notice, the
 * validation router), `submission.js` (the request body), `improvement.js`
 * (`submissionEntries`, `improvedLines`, `currentAllocationPercentages`) and
 * `results.js` (labelling a breakdown row) — and this is the only module all four can
 * import without closing a cycle: it imports nothing at all, while `calculator.js`
 * already imports `improvement.js` and `results.js`, which is why `publicError` has to
 * be *passed* into `compareImprovement` rather than imported.
 *
 * It also belongs beside `draftEntry()` because it is the rule that says which of
 * `leafFigures`'s keys are real; `draftEntry()` calls it to prune, and nothing else may
 * decide that question.
 *
 * @param {object} chain
 * @returns {Array<{foodCategory: string|null, foodItem: string|null}>} in selection order
 */
export function entryLeaves(chain) {
  const leaves = []
  for (const category of (chain?.foodCategories || [])) {
    const items = (chain?.foodItems || {})[category] || []
    if (items.length) for (const item of items) leaves.push({ foodCategory: category, foodItem: item })
    else leaves.push({ foodCategory: category, foodItem: null })
  }
  if (!leaves.length || chain?.foodUnspecified) leaves.push({ foodCategory: null, foodItem: null })
  return leaves
}

/**
 * A leaf's identity, as a map key.
 *
 * NUL-joined rather than concatenated, the same convention `draftRepeatsSavedEntry` and
 * `duplicateOf` already use in `calculator.js`: a category code ending in the next
 * field's first characters could otherwise collide with a different pair. The sector is
 * deliberately absent — it is a property of the chain, not of the leaf.
 */
export const leafKey = leaf => `${leaf?.foodCategory || ''}\u0000${leaf?.foodItem || ''}`

/**
 * **A leaf's name, and the only place one is decided.**
 *
 * Step 3's legend, step 4's column head, the review step, the saved-entry card, the
 * results page's stage tab, the text export and the PDF all show a leaf by name, and
 * before this they disagreed in two ways that are both defects:
 *
 * * **Two leaves could render byte-identical.** `results.js` returned
 *   `Standard mix / not specified` both for a NULL `food_category` and for the
 *   `standard_mix` category, so a visitor who ticked *both* the standard mix and
 *   "I do not know" - which §5.4 requires to be two separate answers, and which step 2
 *   offers as two separate boxes - got two indistinguishable rows carrying different
 *   numbers.
 * * **One leaf had four names.** `Not broken down by type` on steps 3, 4 and the
 *   review step, `Food type not provided` on the saved-entry card, `Not provided` in
 *   the text export and `Standard mix / not specified` on the results page.
 *
 * So the name is computed here, once, from the leaf and the taxonomy, and every surface
 * reads it. The standard mix keeps its own taxonomy name, because it is a category the
 * visitor chose; the category-less leaf gets the words §5.4 gives it, because "the
 * visitor did not break their waste down by type" is a different answer and may not
 * borrow another one's words.
 *
 * It lives beside `entryLeaves` for the same reason that does: `calculator.js`,
 * `results.js` and `submission.js` must all agree, and this module is the only one all
 * three can import without closing a cycle. `i18n.js` imports nothing either, so
 * importing `t` here adds no edge to the graph.
 *
 * @param {{foodCategory: string|null, foodItem: string|null}} leaf
 * @param {object} taxonomy §6.1's response
 * @returns {string}
 */
export function leafDisplayName(leaf, taxonomy) {
  const find = (rows, code) => (rows || []).find(row => row.code === code)
  if (leaf?.foodItem) {
    const item = find(taxonomy?.food_items, leaf.foodItem)
    if (item) return item.name
  }
  if (leaf?.foodCategory) {
    const category = find(taxonomy?.food_categories, leaf.foodCategory)
    return category?.name || leaf.foodCategory
  }
  return t('Not broken down by type')
}

/**
 * The nine per-leaf figures, with every key present.
 *
 * Every consumer — `units.js`'s `entryTotal`/`containerKg`, `calculator.js`'s
 * `measuredAs`, `submission.js`'s `entryPayload` — reads these names off whatever object
 * it is handed, so a leaf record keeps exactly the names the chain-level state carried
 * before the fork and nothing downstream had to learn a second vocabulary.
 */
export const EMPTY_LEAF = {
  totalAmount: '',
  totalUnit: 'kilograms',
  measureMode: 'mass',
  unitPreset: null,
  unitCount: '',
  totalInputKg: '',
  totalValueNzd: '',
  wastedValueNzd: '',
  current: [],
}

/** One leaf's figures off a chain, defaulted and deep-copied. */
export function leafFigures(chain, leaf) {
  const held = (chain?.leafFigures || {})[leafKey(leaf)] || {}
  return { ...EMPTY_LEAF, ...held, current: (held.current || []).map(line => ({ ...line })) }
}

/** The same, read off `state` — the draft's own figures for one leaf. */
export const draftLeafFigures = leaf => leafFigures(state, leaf)

/**
 * Pairs the entries the user typed with the per-entry results §6.2 returns, which
 * preserve request order.
 *
 * **What it must be handed is LEAVES, never chains.** Since the fork one chain becomes
 * several `entries[]`, and this pairs purely by index — hand it the chain array while
 * the request carried leaves and every figure on the results page, in the text export
 * and in the PDF is attached to the wrong entry, with no error and no warning anywhere.
 * `submitCalculation` (`calculator.js`) builds the leaf array with the same
 * `submissionLeaves` call the request body was built from, for exactly that reason. Each paired `response` is the shape the rendering modules
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
    foodCategories: [],
    foodUnspecified: false,
    foodStage: 'categories',
    foodItems: {},
    totalUnit: 'kilograms',
    leafFigures: {},
    timeFrame: '',
    entries: [],
    result: null,
    error: null,
    errorAt: null,
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
    improvedAllocations: [],
    improvementChartExpanded: null,
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
