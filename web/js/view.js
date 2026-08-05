export function escapeHtml(value = '') {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;')
}

export function formatNumber(value, precision = 2) {
  const number = Number(value)
  if (!Number.isFinite(number)) return 'Not available'
  return number.toLocaleString('en-NZ', { maximumFractionDigits: precision })
}

export function slug(value) {
  return String(value).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '')
}

export function buttonRow(backStep, label = 'Continue', disabled = false, action = 'continue') {
  return `<div class="navigation-buttons">
    <button class="button button-secondary" type="button" data-action="go-step" data-step="${backStep}">Back</button>
    <button class="button button-primary" type="button" data-action="${action}" ${disabled ? 'disabled' : ''}>${escapeHtml(label)}</button>
  </div>`
}
