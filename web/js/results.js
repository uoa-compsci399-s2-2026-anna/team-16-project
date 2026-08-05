import { escapeHtml, formatNumber } from './view.js'

function metricCards(scenario, taxonomy, label) {
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
      <p class="result-label">${escapeHtml(label)} total food waste</p>
      <p class="result-value">${formatNumber(scenario?.total_kg, 3)} kg</p>
      <p class="result-note">${formatNumber(Number(scenario?.total_kg || 0) / 1000, 3)} tonnes</p>
    </article>${cards}`
}

function equivalences(result) {
  const sections = [['Current', result.current], ['Alternative', result.alternative]].filter(([, scenario]) => scenario)
  if (!sections.some(([, scenario]) => scenario.equivalences?.length)) return '<p class="empty-state">Available once approved conversion factors are supplied.</p>'
  return sections.map(([label, scenario]) => `<section class="equivalence-scenario"><h3>${label} scenario</h3><div class="equivalent-grid">${(scenario.equivalences || []).map(row => `<article class="placeholder-panel"><strong>${escapeHtml(row.label)}</strong><span>${formatNumber(row.value, 2)}</span></article>`).join('') || '<p class="empty-state">No equivalences returned.</p>'}</div></section>`).join('')
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
      const netValue = Number(result.net_benefit[code])
      const valueClass = netValue > 0 ? 'value-positive' : netValue < 0 ? 'value-negative' : 'value-zero'
      const valueLabel = netValue > 0 ? 'positive' : netValue < 0 ? 'negative' : 'zero'
      return `<tr><th scope="row">${escapeHtml(definition?.name || code)}</th><td>${formatNumber(currentMetric.total, precision)} ${escapeHtml(unit)}</td><td>${formatNumber(alternativeMetric?.total, precision)} ${escapeHtml(unit)}</td><td class="${valueClass}" aria-label="${valueLabel} net benefit: ${formatNumber(result.net_benefit[code], precision)} ${escapeHtml(unit)}">${formatNumber(result.net_benefit[code], precision)} ${escapeHtml(unit)}</td></tr>`
    }).join('')}</tbody>
  </table></div>`
}

function destinationBreakdown(result, taxonomy) {
  const scenarioSections = [['Current', result.current], ['Alternative', result.alternative]].filter(([, scenario]) => scenario)
  const hasRows = scenarioSections.some(([, scenario]) => Object.values(scenario.metrics || {}).some(metric => metric.by_destination?.length))
  if (!hasRows) return '<p class="empty-state">A destination breakdown was not returned by the calculation service.</p>'
  return scenarioSections.map(([scenarioLabel, scenario]) => {
    const metricsWithRows = Object.entries(scenario.metrics || {}).filter(([, metric]) => metric.by_destination?.length)
    return `<section class="breakdown-scenario"><h3>${scenarioLabel} scenario</h3>${metricsWithRows.map(([code, metric]) => {
    const definition = (taxonomy.metrics || []).find(item => item.code === code)
    const precision = Number(metric.display_precision ?? definition?.display_precision ?? 2)
    const unit = metric.unit || definition?.display_unit || definition?.unit || ''
    return `<div class="table-scroll" tabindex="0"><table><caption>${escapeHtml(scenarioLabel)} scenario — ${escapeHtml(definition?.name || code)} by destination</caption><thead><tr><th scope="col">Destination</th><th scope="col">Waste amount</th><th scope="col">Metric contribution</th><th scope="col">Upstream / kg</th><th scope="col">Downstream / kg</th></tr></thead><tbody>${(metric.by_destination || []).map(row => {
      const destination = (taxonomy.destinations || []).find(item => item.code === row.destination)
      const contribution = Number(row.value)
      const contributionClass = contribution > 0 ? 'value-positive' : contribution < 0 ? 'value-negative' : 'value-zero'
      return `<tr><th scope="row">${escapeHtml(destination?.name || row.destination)}</th><td>${formatNumber(row.qty_kg, 3)} kg</td><td class="${contributionClass}">${formatNumber(row.value, precision)} ${escapeHtml(unit)}</td><td>${formatNumber(row.upstream, precision)}</td><td>${formatNumber(row.downstream, precision)}</td></tr>`
    }).join('')}</tbody></table></div>`
    }).join('') || '<p class="empty-state">No destination-level metric rows were returned for this scenario.</p>'}</section>`
  }).join('')
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
    <section class="results-section" aria-labelledby="summary-title"><div class="result-section-heading"><span class="section-number">01</span><div><h2 id="summary-title">Impact summary</h2><p>Values returned by the calculation service.</p></div></div><h3>Current scenario</h3><div class="results-grid">${metricCards(result.current, taxonomy, 'Current')}</div>${result.alternative ? `<h3 class="scenario-result-heading">Alternative scenario</h3><div class="results-grid">${metricCards(result.alternative, taxonomy, 'Alternative')}</div>` : ''}</section>
    <section class="results-section" aria-labelledby="equivalents-title"><div class="result-section-heading"><span class="section-number">02</span><div><h2 id="equivalents-title">Tangible equivalents</h2><p>Equivalent values are supplied by the published factor set.</p></div></div>${equivalences(result)}</section>
    <section class="results-section" aria-labelledby="comparison-title"><div class="result-section-heading"><span class="section-number">03</span><div><h2 id="comparison-title">Scenario comparison</h2><p>Net benefit is current impact minus alternative impact.</p></div></div>${comparison(result, taxonomy)}</section>
    <section class="results-section" aria-labelledby="breakdown-title"><div class="result-section-heading"><span class="section-number">04</span><div><h2 id="breakdown-title">Breakdown by destination</h2><p>Metric contributions returned for each scenario and destination.</p></div></div>${destinationBreakdown(result, taxonomy)}</section>
    <section class="results-section methodology-section" aria-labelledby="method-title"><h2 id="method-title">Methodology &amp; limitations</h2><p>Results are estimates produced by the currently published factor set. Factors and formulas are maintained by Kai Commitment.</p><p><strong>Factor version:</strong> ${escapeHtml(result.factor_set?.version_label || 'Not supplied')}</p><button class="text-button" type="button" data-action="view-methodology">View methodology</button></section>
    <div class="result-actions"><button class="button button-secondary" type="button" data-action="go-step" data-step="4">Edit your data</button><button class="button button-primary" type="button" data-action="start-over">Start a new calculation</button></div>
  </section>`
}
