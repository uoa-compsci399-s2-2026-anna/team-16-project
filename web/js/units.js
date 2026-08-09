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
