// A plain **non-negative** decimal literal: digits, optional fractional digits.
//
// No sign, and the omission is load-bearing rather than tidy. Neither operand of a
// container conversion is ever legitimately negative — a count of containers cannot be,
// and `unit_preset.kg_per_unit` carries a `kg_per_unit >= 0` CHECK constraint (§2.1)
// written for precisely this multiplication, whose model docstring says so: "one negative
// row turns 'three buckets' into a negative mass and feeds a negative quantity into every
// metric downstream of it". Admitting `-` here reproduces that from the other operand,
// where no database constraint can reach it. `-2` is a string a `<input type="number">`
// will hand over quite happily.
//
// No exponent either: `1e3` is not something a number input produces, and admitting it
// would mean admitting `Infinity` and `NaN` by the same door.
const DECIMAL_LITERAL = /^\d+(\.\d+)?$/

/**
 * Whether a value is written the way every amount field on this site accepts.
 *
 * **Exported so there is one copy of the rule.** `calculator.js` needs to tell a value
 * that is merely too precise (`1.234`) from one that is not written as a plain decimal at
 * all (`1e5`, which a `<input type="number">` produces quite happily and which carries no
 * decimal places to complain about) — and a second regular expression over there is how
 * the two would come to disagree about, say, whether an exponent is a number. The
 * two-decimal *typing* rule is a separate and stricter thing and stays in `calculator.js`
 * beside the field hints that state it.
 *
 * @param {string|number} value
 * @returns {boolean}
 */
export const isPlainDecimal = value => DECIMAL_LITERAL.test(String(value).trim())

/**
 * A non-negative decimal string as an exact integer and the power of ten it is scaled by.
 *
 * `'6.6700'` becomes `{digits: 66700n, scale: 4}`. Nothing is rounded and nothing
 * passes through a double, which is the entire reason this exists.
 *
 * @param {string|number} value
 * @returns {{digits: bigint, scale: number}|null} null when the input is not a
 *   plain non-negative decimal literal
 */
function decimalParts(value) {
  const text = String(value).trim()
  if (!DECIMAL_LITERAL.test(text)) return null
  const [whole, fraction = ''] = text.split('.')
  return { digits: BigInt((whole || '0') + fraction), scale: fraction.length }
}

/**
 * `{digits, scale}` printed at exactly `places` decimals, rounded half up — the rounding
 * a person doing this on paper performs, and the one `Decimal.quantize(ROUND_HALF_UP)`
 * performs on the Python side. Python's own `round()` is banker's rounding and is not it.
 */
function formatParts({ digits, scale }, places) {
  let magnitude = digits
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
  return places ? `${text.slice(0, point)}.${text.slice(point)}` : text
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
 *                               not a plain non-negative decimal literal
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

/**
 * The largest container count whose mass stays inside `maxKg`.
 *
 * **A kilogram ceiling has to be restated in the unit of the field it guards.** §6.2's
 * bounds are on kilograms, and a container entry's field holds a *count*; a guard that
 * compared the typed number against a kilogram limit would be checking two containers
 * against ten million and passing every count a visitor could type, whatever the preset
 * weighed. Dividing here turns the one ceiling into the one number the message can name
 * and the check can use — the same number for both, so they cannot disagree at the
 * boundary the way a separate check and a separate message would.
 *
 * `Number(kg_per_unit)` is a double, which §7.6 allows for display and not for a
 * calculation. This is neither: it is an input bound, it is floored, and it never reaches
 * the wire. `toKg` still does the multiplication that does, exactly, in decimal. The floor
 * is what keeps the double's error on the safe side — it can only make the bound one
 * container tighter, never one looser.
 *
 * @param {string|null} presetCode
 * @param {Array} presets  taxonomy.unit_presets
 * @param {number} maxKg   the kilogram ceiling to translate
 * @returns {number}  a whole count, or 0 when there is no usable conversion
 */
export function countLimit(presetCode, presets, maxKg) {
  const preset = (presets || []).find(item => item.code === presetCode)
  const perUnit = preset ? Number(preset.kg_per_unit) : 0
  if (!Number.isFinite(perUnit) || perUnit <= 0) return 0
  return Math.floor(maxKg / perUnit)
}

/**
 * A mass in kilograms, or `null` when there is no finite mass to give.
 *
 * **The check is on what comes out, not only on what went in**, and the difference is
 * one multiplication wide: a finite number of tonnes past about 1.8e305 is an infinite
 * number of kilograms. This function used to test the input, return the product, and so
 * hand back `Infinity` from an input it had just certified finite — and `Infinity` is the
 * one value that passes a ceiling check written as `mass > limit ? refuse : accept`
 * without being either. A mutation that removed `calculator.js`'s `=== null` branch
 * survived every test because of it, which is how this was found.
 */
export function massToKg(amount, unit) {
  const numericAmount = Number(amount)
  if (!Number.isFinite(numericAmount)) return null
  const kilograms = unit === 'tonnes' ? numericAmount * 1000 : numericAmount
  return Number.isFinite(kilograms) ? kilograms : null
}

export function kgString(amount, unit) {
  const kilograms = massToKg(amount, unit)
  return kilograms === null ? null : kilograms.toFixed(3)
}

/**
 * The same conversion as `kgString`, but it never rewrites the visitor's figure.
 *
 * `kgString` ends in `.toFixed(3)`, and `toFixed` rounds: a typed `1.2345` left as
 * `"1.234"`, a figure nobody wrote down. §7.6.1 permits the front end exactly one
 * calculation — a unit conversion — and rounding is a second one, which is the ruling
 * `optionalMoneyString` already carries for the two money fields. The two field families
 * were applying opposite rules to the same kind of mistake.
 *
 * Kilograms are therefore sent **verbatim**, and tonnes are shifted three decimal places
 * **exactly**, in the `BigInt` decimal arithmetic `toKg` already uses rather than through a
 * double. A tonne figure gains three places of headroom by the conversion, so nothing a
 * `<input type="number">` can hold is rounded on that path either; the three-decimal ceiling
 * §6.2 states is enforced where the money ceiling is, at the keystroke.
 *
 * Anything that is not a plain non-negative decimal literal — `1e5`, `.5`, `-3` — falls back
 * to `kgString`, which is the behaviour that was here before and is still what the caller's
 * `null` handling expects.
 *
 * @param {string|number} amount
 * @param {'kilograms'|'tonnes'|string} unit
 * @returns {string|null}
 */
export function exactKgString(amount, unit) {
  const parts = decimalParts(amount)
  if (!parts) return kgString(amount, unit)
  if (unit !== 'tonnes') return String(amount).trim()
  // ×1000 with the scale untouched is the exact product; printing it at three places
  // fewer is the shift, and `formatParts` never has a non-zero tail to round away here
  // because 1000 carries the three places it is asked to drop.
  return formatParts({ digits: parts.digits * 1000n, scale: parts.scale }, Math.max(0, parts.scale - 3))
}

/**
 * The `#total-unit` value space's container prefix, and the two readers of it.
 *
 * A destination row's `unit` is `'kilograms'`, `'tonnes'` or `preset:<unit_preset.code>`
 * (§7.2's note on `state.current`), and **three modules now have to read that value**:
 * `calculator.js` converts it for the wire, `improvement.js` converts it again for the
 * comparison request, and `results.js` names it in the downloaded report. It lives here
 * because the prefix and the conversion behind it are one rule — the whole reason §7.3
 * puts the front end's only arithmetic in this module.
 */
export const PRESET_UNIT = 'preset:'
export const isPresetUnit = unit => typeof unit === 'string' && unit.startsWith(PRESET_UNIT)
export const presetUnitCode = unit => String(unit).slice(PRESET_UNIT.length)

/**
 * One destination row's kilograms, converted with **the row's own unit**.
 *
 * `massToKg` for a weight, `toKg` for a container. This was a private helper in
 * `calculator.js`, and while it was, `improvement.js` converted the same rows with
 * `entry.totalUnit` instead — so a 0.5 t row that the calculator sent as `500.000` was
 * re-sent as `0.500` under the same session token, and §5.3's upsert replaced 500 kg with
 * half a kilogram. A `preset:` row was worse: `massToKg` applies no preset at all, so a
 * quarter of a 23 L bin went as `0.250` rather than `1.668`.
 *
 * `''` for a row the visitor has not filled — a blank row is not a row holding zero — and
 * `null` for a preset the taxonomy no longer carries (§6.1: a consumer must not assume the
 * taxonomy survives a publish, and both callers run this inside a render, where a throw
 * blanks the screen).
 *
 * @param {string|number} qtyInput  the row's amount, as the visitor typed it
 * @param {string} unit             the row's own unit, or the entry's for a row that predates them
 * @param {Array} presets           taxonomy.unit_presets
 * @returns {string|null}           kilograms at three decimal places, `''`, or `null`
 */
export function rowKgString(qtyInput, unit, presets) {
  if (qtyInput === '' || qtyInput === null || qtyInput === undefined) return ''
  if (isPresetUnit(unit)) {
    try {
      return toKg(qtyInput, presetUnitCode(unit), presets || [])
    } catch {
      return null
    }
  }
  return kgString(qtyInput, unit)
}

/**
 * A percentage share of a total mass, as kilograms — **display only**.
 *
 * The improvement panel's kilogram mode shows this instead of the percentage
 * `state.improvedAllocations` actually stores; nothing this returns reaches the wire.
 * `improvement.js`'s `improvedLines` still derives every `qty_kg` it sends from the
 * stored percentage, the one calculation §7.6.1 permits here, so a rounded kilogram
 * figure on screen can never be the number the request carries — only the percentage
 * behind it can be that, and this function does not touch it.
 *
 * @param {number|string} percentage
 * @param {number} totalKg
 * @returns {number} `NaN` when either operand is not a finite number, so a caller that
 *   forgets to guard prints "Not available" rather than "NaN%"
 */
export function percentageToKg(percentage, totalKg) {
  const pct = Number(percentage)
  return Number.isFinite(pct) && Number.isFinite(totalKg) ? (totalKg * pct) / 100 : Number.NaN
}

/**
 * The inverse of `percentageToKg`: the kilogram figure a visitor typed, as a percentage
 * of `totalKg`.
 *
 * **This is what a keystroke in kilogram mode stores.** `state.improvedAllocations`
 * stays a percentage in every mode — see the note on `updateImprovementInput` — so a
 * kilogram entry is converted once, here, on the way in, and the exactly-100 rule in
 * `improvementValidation` is never asked to compare a mass against a tolerance sized for
 * a percentage point.
 *
 * @param {number|string} kg
 * @param {number} totalKg
 * @returns {number} `0` when there is no positive total to divide by — an entry with
 *   nothing in it yet allocates nothing, rather than dividing by zero into `Infinity`
 */
export function kgToPercentage(kg, totalKg) {
  const mass = Number(kg)
  if (!Number.isFinite(mass) || !Number.isFinite(totalKg) || totalKg <= 0) return 0
  return (mass / totalKg) * 100
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
