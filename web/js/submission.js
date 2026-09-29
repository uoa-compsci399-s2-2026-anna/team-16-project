/**
 * The `POST /api/v1/calculate` request body, built in one place for both callers.
 *
 * **This module exists because there are two callers and they disagreed.**
 * `calculator.js`'s Calculate button and `improvement.js`'s Compare Impact button both
 * send a whole submission, both send it under the same `state.token`, and §5.3 upserts on
 * that token — so the second call does not add a row, it *replaces* the first one. When
 * the two builders drift, the visitor's stored submission silently becomes whichever of
 * the two was wrong.
 *
 * Both halves of that were measured against the running stack:
 *
 *   * `compareImprovement` sent no `time_frame`, no `total_input_kg`, no
 *     `total_value_nzd` and no `wasted_value_nzd`, so a submission that held
 *     `one_year / 50000.000 / 120000.00 / 4500.00` held four `NULL`s the moment the
 *     visitor pressed the button on the very next screen. Everything the round-two input
 *     work collects was discarded by one click.
 *   * it converted every destination row with `entry.totalUnit` rather than the row's own
 *     unit, so a 0.5 t row sent as `500.000` by Calculate was re-sent as `0.500` — and a
 *     `preset:` row was not converted at all.
 *
 * Neither could be caught by a test that inspects the outgoing request body of the first
 * call, which is why `tests/web/test_improvement_submission.py` reads the database.
 *
 * Nothing here calculates (§7.6.1). The only arithmetic is the unit conversion, and it is
 * `units.js`'s, called rather than re-typed.
 */

import { entryLeaves, EMPTY_LEAF, leafFigures, leafKey } from './state.js'
import { exactKgString, rowKgString } from './units.js'

/**
 * **Every chain's leaves, in submission order — THE one place a chain becomes several
 * entries.**
 *
 * A chain is what the visitor built and can Edit or Remove as a unit; a leaf is what the
 * API receives. `state.entries` holds chains, `entries[]` on the wire holds leaves, and
 * this function is the only crossing between the two. All three request builders go
 * through it — `submitCalculation`'s Calculate, `improvement.js`'s Compare Impact, and
 * `submissionPayload` itself — so a caller that forgets cannot produce a wrong body.
 *
 * Each leaf comes back already shaped as the flat entry `entryPayload` consumes: the
 * chain's `sector`, the leaf's own nine figures (including its own `current` allocation,
 * because step 4 forks too — `design.md` §10), and its `foodCategory` / `foodItem`.
 *
 * **`state.js` imports nothing, so this import cannot cycle.** It is the only module
 * `calculator.js`, `improvement.js`, `results.js` and this one can all reach.
 *
 * @param {Array<object>} chains
 * @returns {Array<object>} one flat entry per leaf, in request order
 */
export function submissionLeaves(chains) {
  return (chains || []).flatMap(chain => {
    // **A leaf is not a chain, and fanning one out again is silent data loss.** A leaf
    // has no `foodCategories`, so `entryLeaves` answers "one category-less leaf" and the
    // spread finds no `leafFigures` - every entry becomes a blank leaf with `current: []`
    // and the API answers 400 for a submission that was complete. `compareImprovement`
    // did exactly that for one build. It is a programming error, so it is thrown rather
    // than absorbed.
    if (chain && chain.leafKey !== undefined) throw new TypeError('submissionLeaves takes chains, not leaves')
    return entryLeaves(chain).map(leaf => ({
      sector: chain.sector,
      ...(chain.leafFigures ? leafFigures(chain, leaf) : { ...EMPTY_LEAF }),
      foodCategory: leaf.foodCategory,
    // Labels the leaf on `results.js`, the review step and the duplicate notice --
    // and **is sent**, since contract v1.58 gave `EntryPayload` a `food_item`.
    // It was carried and deliberately withheld before that landing, because
    // `EntryPayload` is a Pydantic model with `extra="forbid"` and an unknown key
    // is a 400 rather than an ignored field.
      foodItem: leaf.foodItem,
      leafKey: leafKey(leaf),
    }))
  })
}

/**
 * The `current` scenario's lines, as §6.2 wants them.
 *
 * **Each row converts with its own `unit`**, falling back to the entry's total unit for a
 * row saved before rows could carry one — the unit those figures were actually typed
 * against, so an older entry is never silently reinterpreted.
 *
 * A blank row and a row whose container preset has left the taxonomy both convert to
 * something `Number()` reads as zero, and §6.2 has no use for a zero line, so both are
 * dropped — which is what `buildLines` did before this moved here.
 *
 * @param {object} entry
 * @param {Array} presets  taxonomy.unit_presets
 * @returns {Array<{destination: string, qty_kg: string}>}
 */
export function requestLines(entry, presets) {
  return (entry.current || [])
    .map(line => ({ destination: line.destination, qty_kg: rowKgString(line.qtyInput, line.unit || entry.totalUnit, presets) }))
    .filter(line => Number(line.qty_kg) > 0)
}

/**
 * A mass field the visitor may have left alone.
 *
 * `''` is "not answered" and has to reach the API as `null`, never as `"0.000"` — a zero
 * is the claim that production was actually nil (§6.2: absent is stored as NULL and never
 * as zero).
 *
 * **`exactKgString`, not `kgString`, and that is the same ruling the money fields carry.**
 * `kgString` ends in `.toFixed(3)`, so a typed `1.2345` was sent as `"1.234"` — exactly the
 * silent rewrite `optionalMoneyString` below refuses to perform, applied by the same
 * request body to the field next door. The three-decimal ceiling §6.2 states is enforced
 * where the money ceiling is: at the keystroke, in `calculator.js`.
 */
export const optionalKgString = (value, unit) =>
  (value === '' || value === null || value === undefined ? null : exactKgString(value, unit))

/**
 * A New Zealand dollar figure the visitor may have left alone.
 *
 * Carried through untouched. **Not `Number(value).toFixed(2)`, deliberately**: rounding a
 * figure the visitor typed is a calculation, and §7.6.1 permits exactly one. The
 * two-decimal ceiling is enforced at the keystroke instead, in `calculator.js`.
 */
export const optionalMoneyString = value =>
  (value === '' || value === null || value === undefined ? null : value)

/**
 * One entry of the request body.
 *
 * @param {object} entry
 * @param {Array} presets  taxonomy.unit_presets
 * @param {Array|null} alternative  the improved scenario's lines, or null when there is none
 */
export function entryPayload(entry, presets, alternative = null) {
  return {
    sector: entry.sector,
    food_category: entry.foodCategory || null,
    // The named food within `food_category`, contract v1.58. `|| null` rather than
    // the value as held, for the reason `food_category` beside it uses one: a leaf
    // that names no food carries `null`, and §6.2 says absent and null mean the
    // same thing -- so sending `null` explicitly is the shape that cannot be
    // mistaken for a client that predates the field.
    food_item: entry.foodItem || null,
    current: requestLines(entry, presets),
    alternative,
    total_input_kg: optionalKgString(entry.totalInputKg, entry.totalUnit),
    total_value_nzd: optionalMoneyString(entry.totalValueNzd),
    wasted_value_nzd: optionalMoneyString(entry.wastedValueNzd),
  }
}

/**
 * The reporting period's two instants, as §6.2 wants them (v1.67's fields, sent from v1.68).
 *
 * **The state's own strings, sent as they are held.** `state.periodStart` is already
 * `"2026-09-14T08:10"` — the wire's shape, local wall-clock time carrying no zone — and
 * §6.2 appends the seconds itself. Nothing here builds a `Date`, reads `toISOString()` or
 * takes an epoch millisecond: `toISOString()` converts to UTC, so a shift typed as 08:10
 * in Auckland would be sent as the previous day's 20:10 and printed back to the visitor
 * on their own download; the validator refuses a zone-carrying value for that reason and
 * would not catch this one, because the converted string is perfectly well-formed and
 * simply says a different time. A number would be refused outright.
 *
 * `''` means "no period was given" in the state and `null` means it on the wire (§2.3:
 * absence, never a sentinel), and the two are always both or neither — `period.js`'s
 * `periodValues` empties both the moment the period stops being legal, so there is no
 * keystroke at which this can send half an interval.
 */
const periodPayload = state => ({
  period_start: state.periodStart || null,
  period_end: state.periodEnd || null,
})

/**
 * The whole request body.
 *
 * `time_frame` sits beside `gwp_horizon` rather than inside the entries because it is one
 * period for the whole submission (§6.2) — asked once, on the review step. `period_start`
 * and `period_end` (v1.68) sit beside it for the same reason and are sent by the same
 * builder for the reason this module exists at all: `improvement.js`'s Compare Impact
 * upserts on the same token, so a period sent by Calculate and not by Compare would be
 * two `NULL`s in the row the moment the visitor pressed the button on the next screen —
 * exactly the defect `time_frame` itself was found in.
 *
 * @param {object} state
 * @param {Array<object>} chains  every supply-chain entry, in submission order. They
 *   are CHAINS; this function forks them into leaves itself.
 * @param {(leaf: object, index: number) => Array|null} [alternativeFor]  the improved
 *   scenario for a leaf, and the leaf's own position in the submission; the default is
 *   no alternative, which is what the main Calculate button sends. The **index** is what
 *   `improvement.js` selects that leaf's own allocation with, now that the improvement
 *   panel forks: a leaf's name is not an identity, because two chains may name the same
 *   sector and the same food, while its position in `entries[]` is.
 */
export function submissionPayload(state, chains, alternativeFor = () => null) {
  const presets = state.taxonomy?.unit_presets || []
  // **Chains in, leaves out**, and the fan-out happens here rather than at the three
  // call sites. `alternativeFor` is therefore invoked with a LEAF, which is what
  // `improvement.js`'s `entry => improvedLines(entry, ...)` needs: `improvedLines` takes
  // `sumQtyKg(requestLines(entry, presets))` as its base, and that base must be the
  // leaf's own mass or the alternative will not conserve mass against the leaf's current
  // scenario — which §6.2 rejects for the whole submission.
  const leaves = submissionLeaves(chains)
  return {
    token: state.token || null,
    gwp_horizon: state.gwpHorizon,
    time_frame: state.timeFrame || null,
    ...periodPayload(state),
    entries: leaves.map((leaf, index) => entryPayload(leaf, presets, alternativeFor(leaf, index))),
  }
}

/**
 * The `POST /api/v1/export/pdf` request body (`api/export.py`'s `ExportPayload`).
 *
 * **Built from `state.result.entry_results`, not from `state.entries`.** By the time the
 * results page — and its PDF button — exist, the wizard's own draft has already been
 * folded into the frozen record `entryResultsFrom` (`state.js`) built at Calculate time:
 * each item pairs the entry as submitted with the response it produced. That is the same
 * pairing the plain-text export reads its figures from, so this reads the same entries the
 * visitor is looking at rather than a second copy of the wizard's working state.
 *
 * **No `token`.** `ExportPayload` has no field for one and `extra="forbid"` refuses a
 * request that adds one — the route persists nothing, so there is nothing for a token to
 * name (see `api/export.py`'s module docstring). `submissionPayload` above cannot be reused
 * as-is for exactly this reason.
 *
 * @param {object} state
 * @param {string} locale  the interface language the visitor is reading, so the document
 *   renders in it — `i18n.js`'s `activeLanguage()`, not a value invented here.
 */
export function exportPayload(state, locale) {
  const presets = state.taxonomy?.unit_presets || []
  const entries = (state.result?.entry_results || []).map(item => item.entry)
  return {
    gwp_horizon: state.gwpHorizon,
    time_frame: state.timeFrame || null,
    // The same two fields `submissionPayload` sends, from the same state and through the
    // same builder: `ExportPayload` inherits `PricingOptions` precisely so that a period
    // one route accepted and the other refused cannot happen, and two builders here would
    // put the disagreement back one layer up. The document has to name the period its
    // figures cover, and this route persists nothing, so this is the only way it learns it.
    ...periodPayload(state),
    entries: entries.map(entry => entryPayload(entry, presets)),
    locale,
  }
}
