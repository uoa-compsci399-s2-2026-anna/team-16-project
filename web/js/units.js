export function toKg(count, presetCode, presets) {
  const preset = presets.find(item => item.code === presetCode)
  if (!preset) throw new Error(`Unknown unit preset: ${presetCode}`)
  const kilograms = Number(count) * Number(preset.kg_per_unit)
  if (!Number.isFinite(kilograms)) throw new Error('Container count must be a valid number.')
  return kilograms.toFixed(3)
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
