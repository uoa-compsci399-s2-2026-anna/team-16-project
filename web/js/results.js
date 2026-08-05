import { escapeHtml, formatNumber } from './view.js'

function metricCards(result, taxonomy) {
  const scenario = result.current
  const metricDefinitions = taxonomy.metrics || []
  const metricEntries = Object.entries(scenario?.metrics || {})
  const cards = metricEntries.map(([code, metric]) => {
    const definition = metricDefinitions.find(item => item.code === code)
    const precision = Number(metric.display_precision ?? definition?.display_precision ?? 2)
    const unit = metric.unit || definition?.display_unit || definition?.unit || ''
    return `<article class="result-card">
      <p class="result-label">${escapeHtml(definition?.name || code)}</p>
      <p class="result-value">${formatNumber(metric.total, precision)} ${escapeHtml(unit)}</p>
    </article>`
  }).join('')

  return `<article class="result-card primary-result">
      <p class="result-label">Total food waste</p>
      <p class="result-value">${formatNumber(scenario?.total_kg, 3)} kg</p>
      <p class="result-note">${formatNumber(Number(scenario?.total_kg || 0) / 1000, 3)} tonnes</p>
    </article>${cards}<article class="result-card">
      <p class="result-label">Percentage waste</p>
      <p class="result-value">Not available</p>
      <p class="result-note">Total food handled data is required.</p>
    </article>`
}

function equivalences(result) {
  const rows = result.current?.equivalences || []
  if (!rows.length) return '<p class="empty-state">Available once approved conversion factors are supplied.</p>'
  return `<div class="equivalent-grid">${rows.map(row => `<article class="placeholder-panel"><strong>${escapeHtml(row.label)}</strong></article>`).join('')}</div>`
}

function comparison(result, taxonomy) {
  if (!result.alternative || !result.net_benefit) return '<p class="empty-state">No alternative scenario was submitted.</p>'
  return `<div class="table-scroll" tabindex="0"><table>
    <caption>Current and alternative scenario results</caption>
    <thead><tr><th scope="col">Metric</th><th scope="col">Current</th><th scope="col">Alternative</th><th scope="col">Net benefit</th></tr></thead>
    <tbody>${Object.entries(result.current.metrics || {}).map(([code, currentMetric]) => {
      const definition = (taxonomy.metrics || []).find(item => item.code === code)
      const alternativeMetric = result.alternative.metrics?.[code]
      const precision = Number(currentMetric.display_precision ?? definition?.display_precision ?? 2)
      const unit = currentMetric.unit || definition?.display_unit || ''
      return `<tr><th scope="row">${escapeHtml(definition?.name || code)}</th><td>${formatNumber(currentMetric.total, precision)} ${escapeHtml(unit)}</td><td>${formatNumber(alternativeMetric?.total, precision)} ${escapeHtml(unit)}</td><td>${formatNumber(result.net_benefit[code], precision)} ${escapeHtml(unit)}</td></tr>`
    }).join('')}</tbody>
  </table></div>`
}

function destinationBreakdown(result, taxonomy) {
  const firstMetric = Object.values(result.current?.metrics || {})[0]
  const rows = firstMetric?.by_destination || []
  if (!rows.length) return '<p class="empty-state">A destination breakdown was not returned by the calculation service.</p>'
  const total = Number(result.current.total_kg) || 0
  return `<div class="bar-list" aria-hidden="true">${rows.map(row => {
    const destination = (taxonomy.destinations || []).find(item => item.code === row.destination)
    const percentage = total ? Number(row.qty_kg) / total * 100 : 0
    return `<div class="bar-row"><div><strong>${escapeHtml(destination?.name || row.destination)}</strong><span>${percentage.toFixed(1)}%</span></div><div class="bar-track"><span style="width:${Math.min(100, percentage)}%"></span></div></div>`
  }).join('')}</div><div class="table-scroll" tabindex="0"><table><caption>Waste by destination</caption><thead><tr><th scope="col">Destination</th><th scope="col">Waste amount</th><th scope="col">Percentage</th></tr></thead><tbody>${rows.map(row => {
    const destination = (taxonomy.destinations || []).find(item => item.code === row.destination)
    const percentage = total ? Number(row.qty_kg) / total * 100 : 0
    return `<tr><th scope="row">${escapeHtml(destination?.name || row.destination)}</th><td>${formatNumber(row.qty_kg, 3)} kg</td><td>${percentage.toFixed(1)}%</td></tr>`
  }).join('')}</tbody></table></div>`
}

export function renderResults(state) {
  const { result, taxonomy } = state
  if (!result) return '<section class="content-section"><h1>Results unavailable</h1><p>No calculation result has been returned.</p></section>'
  const mockWarning = result.factor_set?.is_mock
    ? `<aside class="disclaimer" role="status"><span class="info-icon" aria-hidden="true">i</span><div><strong>Placeholder data</strong><p>This result uses mock factors (${escapeHtml(result.factor_set.version_label)}). It must not be treated as a verified impact result.</p></div></aside>`
    : ''

  return `<section class="content-section wide results-page" aria-labelledby="results-title">
    <p class="eyebrow">Calculation complete</p><h1 id="results-title">Your food waste impact</h1>
    ${mockWarning}
    <section class="results-section" aria-labelledby="summary-title"><div class="result-section-heading"><span class="section-number">01</span><div><h2 id="summary-title">Impact summary</h2><p>Values returned by the calculation service.</p></div></div><div class="results-grid">${metricCards(result, taxonomy)}</div></section>
    <section class="results-section" aria-labelledby="equivalents-title"><div class="result-section-heading"><span class="section-number">02</span><div><h2 id="equivalents-title">Tangible equivalents</h2><p>Equivalent values are supplied by the published factor set.</p></div></div>${equivalences(result)}</section>
    <section class="results-section" aria-labelledby="comparison-title"><div class="result-section-heading"><span class="section-number">03</span><div><h2 id="comparison-title">Scenario comparison</h2><p>Net benefit is current impact minus alternative impact.</p></div></div>${comparison(result, taxonomy)}</section>
    <section class="results-section" aria-labelledby="breakdown-title"><div class="result-section-heading"><span class="section-number">04</span><div><h2 id="breakdown-title">Breakdown by destination</h2><p>Waste quantities from the current scenario.</p></div></div>${destinationBreakdown(result, taxonomy)}</section>
    <section class="results-section methodology-section" aria-labelledby="method-title"><h2 id="method-title">Methodology &amp; limitations</h2><p>Results are estimates produced by the currently published factor set. Factors and formulas are maintained by Kai Commitment.</p><p><strong>Factor version:</strong> ${escapeHtml(result.factor_set?.version_label || 'Not supplied')}</p><button class="text-button" type="button" data-action="view-methodology">View methodology</button></section>
    <div class="result-actions"><button class="button button-secondary" type="button" data-action="go-step" data-step="4">Edit your data</button><button class="button button-primary" type="button" data-action="start-over">Start a new calculation</button></div>
  </section>`
}
