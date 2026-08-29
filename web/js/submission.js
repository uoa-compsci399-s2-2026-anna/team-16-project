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

import { exactKgString, rowKgString } from './units.js'

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
    current: requestLines(entry, presets),
    alternative,
    total_input_kg: optionalKgString(entry.totalInputKg, entry.totalUnit),
    total_value_nzd: optionalMoneyString(entry.totalValueNzd),
    wasted_value_nzd: optionalMoneyString(entry.wastedValueNzd),
  }
}

/**
 * The whole request body.
 *
 * `time_frame` sits beside `gwp_horizon` rather than inside the entries because it is one
 * period for the whole submission (§6.2) — asked once, on the review step.
 *
 * @param {object} state
 * @param {Array<object>} entries  every entry, in submission order
 * @param {(entry: object) => Array|null} [alternativeFor]  the improved scenario for an
 *   entry; the default is no alternative, which is what the main Calculate button sends.
 */
export function submissionPayload(state, entries, alternativeFor = () => null) {
  const presets = state.taxonomy?.unit_presets || []
  return {
    token: state.token || null,
    gwp_horizon: state.gwpHorizon,
    time_frame: state.timeFrame || null,
    entries: entries.map(entry => entryPayload(entry, presets, alternativeFor(entry))),
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
    entries: entries.map(entry => entryPayload(entry, presets)),
    locale,
  }
}
