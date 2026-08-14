import { t } from './i18n.js'

const API_BASE = '/api/v1'
const NEWS_API = 'https://kaicommitment.org.nz/wp-json/wp/v2/posts'
const searchParams = new URLSearchParams(window.location.search)
const MOCK_MODE = searchParams.get('mock') === '1'
const MOCK_ERROR = searchParams.get('mockError')

export class ApiError extends Error {
  constructor(code, message, details = [], status = 0) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.details = details
    this.status = status
  }
}

async function request(path, options = {}) {
  if (MOCK_MODE) return mockRequest(path, options)
  let response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: {
        Accept: 'application/json',
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        ...options.headers,
      },
    })
  } catch {
    throw new ApiError('NETWORK_ERROR', t('The calculator service could not be reached. Check your connection and try again.'))
  }

  let body = null
  try {
    body = await response.json()
  } catch {
    if (!response.ok) throw new ApiError('HTTP_ERROR', t('The calculator service returned an unexpected response.'), [], response.status)
  }

  if (!response.ok) {
    throw new ApiError(
      body?.error?.code || body?.code || 'HTTP_ERROR',
      body?.error?.message || body?.message || t('The request could not be completed.'),
      body?.error?.details || body?.details || [],
      response.status,
    )
  }
  return body
}

// Resolved against this module's own URL rather than the site root, so mock mode keeps
// working whichever directory is served: `web/js/api.js` -> `<repo>/tests/fixtures/`.
const FIXTURE_BASE = new URL('../../tests/fixtures/', import.meta.url)

async function readFixture(path) {
  const response = await fetch(new URL(path, FIXTURE_BASE))
  if (!response.ok) throw new ApiError('MOCK_FIXTURE_ERROR', `Mock fixture ${path} could not be loaded.`, [], response.status)
  return response.json()
}

// -------------------------------------------------------------------------------------
// Mock mode stands in for the server. Every line below runs only when `?mock=1` is set,
// and the shaping it does is the server's work, not the client's — no module outside
// `mockRequest` may derive an impact figure. §6.2's contract is what it reproduces:
// one request carrying `entries[]`, one response carrying engine-computed `totals`
// alongside a per-entry result in request order.
// -------------------------------------------------------------------------------------

const sumQty = rows => rows.reduce((sum, row) => sum + (Number(row.qty_kg) || 0), 0)

// The fixture's factors are placeholders; only `mass` is re-derived from the request,
// because mass is an identity (value === qty_kg) and not a formula. Every other metric
// keeps the fixture's figures — inventing them here would put a formula in the browser.
function mockScenario(template, rows) {
  if (!template) return null
  const totalKg = sumQty(rows)
  const metrics = {}
  for (const [code, metric] of Object.entries(template.metrics || {})) {
    metrics[code] = code === 'mass'
      ? {
          ...metric,
          total: totalKg.toFixed(10),
          by_destination: rows.map(row => ({
            destination: row.destination,
            qty_kg: row.qty_kg,
            upstream: '0.0000000000',
            downstream: '0.0000000000',
            value: (Number(row.qty_kg) || 0).toFixed(10),
          })),
        }
      : { ...metric }
  }
  return { ...template, total_kg: totalKg.toFixed(3), metrics }
}

function mockEntry(template, requestEntry) {
  const alternativeRows = requestEntry.alternative
  return {
    sector: requestEntry.sector,
    food_category: requestEntry.food_category ?? null,
    current: mockScenario(template.current, requestEntry.current || []),
    alternative: alternativeRows ? mockScenario(template.alternative || template.current, alternativeRows) : null,
    net_benefit: alternativeRows ? template.net_benefit ?? null : null,
  }
}

function mockScenarioTotals(entries, key, equivalences) {
  const totals = {}
  for (const entry of entries) {
    // §6.2: an entry carrying no `alternative` contributes zero to `net_benefit` rather
    // than being excluded, so its current figures stand in on the alternative side.
    const scenario = entry[key] || entry.current
    for (const [code, metric] of Object.entries(scenario?.metrics || {})) {
      if (!totals[code]) totals[code] = { unit: metric.unit, display_precision: metric.display_precision, total: 0 }
      totals[code].total += Number(metric.total) || 0
    }
  }
  // §6.2: `totals.<scenario>.metrics[code]` carries no `by_destination`.
  return {
    metrics: Object.fromEntries(Object.entries(totals).map(([code, metric]) => [code, { ...metric, total: metric.total.toFixed(10) }])),
    equivalences: equivalences || [],
  }
}

function mockTotals(template, entries) {
  const current = mockScenarioTotals(entries, 'current', template.current?.equivalences)
  const compared = entries.some(entry => entry.alternative)
  const alternative = compared ? mockScenarioTotals(entries, 'alternative', template.alternative?.equivalences) : null
  const netBenefit = alternative
    ? Object.fromEntries(Object.entries(current.metrics).map(([code, metric]) => [code, (Number(metric.total) - Number(alternative.metrics[code]?.total || 0)).toFixed(10)]))
    : null
  return {
    total_kg: entries.reduce((sum, entry) => sum + (Number(entry.current?.total_kg) || 0), 0).toFixed(3),
    current,
    alternative,
    net_benefit: netBenefit,
  }
}

async function mockCalculate(options) {
  if (MOCK_ERROR) {
    const body = await readFixture(`errors/${MOCK_ERROR.toLowerCase()}.json`)
    throw new ApiError(body.error.code, body.error.message, body.error.details, body.error.code === 'RATE_LIMITED' ? 429 : 400)
  }
  const fixture = structuredClone(await readFixture('calculate_response.json'))
  const payload = JSON.parse(options.body || '{}')
  const requestEntries = payload.entries || []
  const templates = fixture.entries || []
  const entries = requestEntries.map((requestEntry, index) => mockEntry(templates[index % templates.length] || templates[0], requestEntry))
  return {
    factor_set: fixture.factor_set,
    factor_source: fixture.factor_source || 'published',
    gwp_horizon: payload.gwp_horizon ?? fixture.gwp_horizon,
    token: payload.token || fixture.token || 'mock-session-token',
    totals: mockTotals(fixture.totals || {}, entries),
    entries,
  }
}

async function mockRequest(path, options) {
  if (path === '/taxonomy') return readFixture('taxonomy.json')
  if (path.startsWith('/factors')) return readFixture('factors.json')
  if (path === '/stats') return readFixture('stats.json')
  if (path === '/calculate' && options.method === 'POST') return mockCalculate(options)
  throw new ApiError('MOCK_FIXTURE_ERROR', `No mock fixture is mapped for ${path}.`)
}

export function getTaxonomy() {
  return request('/taxonomy')
}

export function calculate(payload, opts = {}) {
  return request('/calculate', {
    method: 'POST',
    headers: opts.dryRun ? { 'X-Dry-Run': 'true' } : {},
    body: JSON.stringify(payload),
  })
}

export function getStats() {
  return request('/stats')
}

export function getFactors(opts = {}) {
  const query = opts.version ? `?version=${encodeURIComponent(opts.version)}` : ''
  return request(`/factors${query}`)
}

export async function getNewsPosts(limit = 6) {
  const numericLimit = Number(limit)
  const integerLimit = Number.isFinite(numericLimit) ? Math.trunc(numericLimit) : 6
  const safeLimit = Math.min(100, Math.max(1, integerLimit))
  const per_page = String(safeLimit)
  const query = new URLSearchParams({ per_page })
  const url = `${NEWS_API}?${query.toString()}&_embed`

  let response
  try {
    response = await fetch(url, { headers: { Accept: 'application/json' } })
  } catch {
    throw new ApiError('NETWORK_ERROR', 'The news service could not be reached. Check your connection and try again.')
  }

  let body
  try {
    body = await response.json()
  } catch {
    throw new ApiError('HTTP_ERROR', 'The news service returned an unexpected response.', [], response.status)
  }

  if (!response.ok) {
    throw new ApiError(
      body?.code || 'HTTP_ERROR',
      body?.message || 'The news request could not be completed.',
      Array.isArray(body?.data) ? body.data : [],
      response.status,
    )
  }
  return body
}
