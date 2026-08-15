// A plain decimal literal: optional sign, digits, optional fractional digits. No
// exponent — `1e3` is not something a user types into a number input, and admitting
// it here would mean admitting `Infinity` and `NaN` by the same door.
const DECIMAL_LITERAL = /^[+-]?\d+(\.\d+)?$/

/**
 * A decimal string as an exact integer and the power of ten it is scaled by.
 *
 * `'6.6700'` becomes `{digits: 66700n, scale: 4}`. Nothing is rounded and nothing
 * passes through a double, which is the entire reason this exists.
 *
 * @param {string|number} value
 * @returns {{digits: bigint, scale: number}|null} null when the input is not a
 *   plain decimal literal
 */
function decimalParts(value) {
  const text = String(value).trim()
  if (!DECIMAL_LITERAL.test(text)) return null
  const negative = text.startsWith('-')
  const [whole, fraction = ''] = text.replace(/^[+-]/, '').split('.')
  const digits = BigInt((whole || '0') + fraction)
  return { digits: negative ? -digits : digits, scale: fraction.length }
}

/**
 * `{digits, scale}` printed at exactly `places` decimals, rounded half away from
 * zero — the rounding a person doing this on paper performs, and the one
 * `Decimal.quantize(ROUND_HALF_UP)` performs on the Python side.
 */
function formatParts({ digits, scale }, places) {
  const negative = digits < 0n
  let magnitude = negative ? -digits : digits
  if (scale > places) {
    const divisor = 10n ** BigInt(scale - places)
    const quotient = magnitude / divisor
    // `remainder * 2 >= divisor` is "the dropped tail is at least a half", asked
    // without ever forming the half — which at these scales is not representable
    // as an integer division.
    magnitude = (magnitude % divisor) * 2n >= divisor ? quotient + 1n : quotient
  } else {
    magnitude *= 10n ** BigInt(places - scale)
  }
  const text = magnitude.toString().padStart(places + 1, '0')
  const point = text.length - places
  const body = places ? `${text.slice(0, point)}.${text.slice(point)}` : text
  return negative && magnitude !== 0n ? `-${body}` : body
}

/**
 * Convert a container count to kilograms.
 *
 * **This multiplication is done in decimal, not in double.** Both operands are
 * decimals — the count is what the visitor typed and `kg_per_unit` is a
 * `DECIMAL(12,4)` that §1.2 puts on the wire as a string precisely so that
 * JavaScript's `Number` never sees it. `Number(count) * Number(preset.kg_per_unit)`
 * followed by `.toFixed(3)` was the previous body and it is wrong at the third
 * decimal place, not the fifteenth: a quarter of the seeded 23 L food scraps bin
 * is `0.25 × 6.6700 = 1.6675 kg` exactly, and the double nearest `6.67` is a
 * shade *below* it, so `toFixed(3)` reads the exact tie as a value under the half
 * and answers `"1.667"`. The correct answer is `"1.668"`, and `tests/web/
 * test_unit_presets.py` asserts that number for that reason.
 *
 * @param {number|string} count  Number of containers, as the visitor typed it
 * @param {string} presetCode    unit_preset code
 * @param {Array}  presets       taxonomy.unit_presets
 * @returns {string}             Kilograms as a string with 3 decimal places,
 *                               ready to send to the API (§6.2 refuses a fourth)
 * @throws {Error}               presetCode does not exist, or either operand is
 *                               not a plain decimal literal
 */
export function toKg(count, presetCode, presets) {
  const preset = presets.find(item => item.code === presetCode)
  if (!preset) throw new Error(`Unknown unit preset: ${presetCode}`)
  const left = decimalParts(count)
  if (!left) throw new Error('Container count must be a valid number.')
  const right = decimalParts(preset.kg_per_unit)
  if (!right) throw new Error(`Unit preset ${presetCode} has no usable conversion.`)
  return formatParts(
    { digits: left.digits * right.digits, scale: left.scale + right.scale },
    3,
  )
}

/**
 * `toKg` made total: the kilograms a container entry describes, or `''`.
 *
 * **It has to be total, and it has to be here.** `toKg` throws on a preset code it cannot
 * find, which is reachable — §6.1 says a consumer must not assume the taxonomy is stable
 * across a publish, and a page holds a selection made before one — and both callers run
 * this inside a render, where a throw blanks the screen.
 *
 * Whether the count is *typeable* is a separate question and is not asked here: `1.` and
 * `1.2345` both simply fail to convert. The two-decimal input rule lives in
 * `calculator.js` beside the same rule for the mass field.
 *
 * @param {{unitPreset: string|null, unitCount: string}} entry
 * @param {Array} presets  taxonomy.unit_presets
 * @returns {string}  Kilograms at 3 decimal places, or '' when there is no total yet
 */
export function containerKg(entry, presets) {
  if (!entry?.unitPreset) return ''
  try {
    return toKg(entry.unitCount, entry.unitPreset, presets || [])
  } catch {
    return ''
  }
}

/**
 * The one place the two step-3 measurement modes are reconciled: an entry's total as
 * `{amount, unit}`, in the unit its destination rows are entered in.
 *
 * **A container entry always answers kilograms.** A container estimates the *total*, and
 * the total is a mass: step 4 splits it across destinations, §6.2's mass-conservation rule
 * compares the two, and both have to be in one unit — "0.37 wheelie bins to landfill" is
 * not something anyone can enter or check. So a container entry reaches the API as exactly
 * the `qty_kg` a visitor who had typed the kilograms would have sent, which is the property
 * that makes the container input safe to add at all.
 *
 * **In `units.js` rather than in `calculator.js` because it has two consumers.** The
 * results export prints each entry's waste amount too, and it read `entry.totalAmount`
 * directly — which is `''` for a container entry, so the downloaded report would have said
 * "0.00 kilograms" for an entry whose form showed 139.200 kg. `results.js` cannot import
 * `calculator.js` (that module imports this one), and a second copy of the rule is how the
 * two would come to disagree — §7.3's whole reason for existing.
 *
 * @param {object} entry   A draft or saved entry
 * @param {Array} presets  taxonomy.unit_presets
 * @returns {{amount: string, unit: 'kilograms'|'tonnes'}}
 */
export function entryTotal(entry, presets) {
  return entry?.measureMode === 'container'
    ? { amount: containerKg(entry, presets), unit: 'kilograms' }
    : { amount: entry?.totalAmount ?? '', unit: entry?.totalUnit || 'kilograms' }
}

export function massToKg(amount, unit) {
  const numericAmount = Number(amount)
  if (!Number.isFinite(numericAmount)) return null
  return unit === 'tonnes' ? numericAmount * 1000 : numericAmount
}

export function kgString(amount, unit) {
  const kilograms = massToKg(amount, unit)
  return kilograms === null ? null : kilograms.toFixed(3)
}

// The one arithmetic §7.6.1 permits on a figure the API supplied, and §7.3 requires it to
// live here: `results.js` printed `totals.total_kg / 1000` inline at two sites, which is a
// unit conversion outside `units.js` — the exception stated in terms of a module that was
// not the one doing it. Nothing else may divide an API figure.
export function kgToTonnes(kilograms) {
  // `Number(null)` and `Number('')` are both 0, so a plain finite check would print
  // "0.000 tonnes" beside the "Not available" the same absent figure produces one line
  // above. Absent has to stay absent, which is what formatNumber() does with NaN.
  if (kilograms === null || kilograms === undefined || kilograms === '') return Number.NaN
  const numeric = Number(kilograms)
  return Number.isFinite(numeric) ? numeric / 1000 : Number.NaN
}
