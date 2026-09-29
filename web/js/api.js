import { t } from './i18n.js'
import { API_ORIGIN, NEWS_ORIGIN } from './config.js'

// `API_ORIGIN` is empty in the designed topology, so this is `/api/v1` — a relative path,
// resolved against whatever origin served the page. See config.js for why it is
// configurable at all and for what stops it being pointed anywhere a visitor chooses.
const API_BASE = `${API_ORIGIN}/api/v1`

// WordPress's REST route is fixed by WordPress; only the origin is a deployment fact, and
// it arrives from config.js so that the same value builds the `connect-src` this fetch has
// to satisfy. An empty origin is not a URL and is never requested — `getNewsPosts` returns
// null rather than asking a guessed domain for posts.
const NEWS_PATH = '/wp-json/wp/v2/posts'
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

// §4.6 in miniature, for the one totals-level figure mock mode can compute honestly.
// `production_share_percent` is `current.total_kg ÷ Σ total_input_kg` (§4.6) — two masses
// the visitor typed, the same identity `mockScenario`'s own `mass` metric already
// recomputes above, so this is not "deriving an impact figure" the comment over this
// section forbids: no factor and no formula enters it, on the server or here. The other
// four §4.6 fields (`totals.money`) stay the pre-existing gap `docs/interfaces.md` §7.1
// records — mock mode never had a real per-request money figure to show, because it never
// carried the visitor's own `total_value_nzd`/`wasted_value_nzd` through a computation —
// and this function does not change that.
//
// Same three states `_across_entries` gives the real engine, in the same order: nobody
// typed a production total, some did and some did not, or everybody did and the total
// came to zero (`undefined`, v1.51 — a mock-mode visitor who types 0 kg across the board
// must see the same sentence a real submission would, not "Not supplied").
function mockProductionShare(requestEntries, totalKg) {
  const totals = requestEntries.map(entry => entry.total_input_kg)
  const present = totals.filter(value => value !== null && value !== undefined && value !== '')
  if (present.length === 0) return { value: null, state: 'not_supplied' }
  if (present.length !== totals.length) return { value: null, state: 'incomplete' }
  const sum = present.reduce((total, value) => total + (Number(value) || 0), 0)
  if (sum === 0) return { value: null, state: 'undefined' }
  return { value: ((totalKg / sum) * 100).toFixed(2), state: 'complete' }
}

function mockTotals(template, entries, requestEntries) {
  const current = mockScenarioTotals(entries, 'current', template.current?.equivalences)
  const compared = entries.some(entry => entry.alternative)
  const alternative = compared ? mockScenarioTotals(entries, 'alternative', template.alternative?.equivalences) : null
  const netBenefit = alternative
    ? Object.fromEntries(Object.entries(current.metrics).map(([code, metric]) => [code, (Number(metric.total) - Number(alternative.metrics[code]?.total || 0)).toFixed(10)]))
    : null
  const totalKg = entries.reduce((sum, entry) => sum + (Number(entry.current?.total_kg) || 0), 0)
  const share = mockProductionShare(requestEntries, totalKg)
  return {
    total_kg: totalKg.toFixed(3),
    current,
    alternative,
    net_benefit: netBenefit,
    // `money` stays absent — see the comment above `mockProductionShare`. `data_state`
    // is emitted in full regardless, consistent with what this response actually carries:
    // `production_share_percent`'s own computed state, and `not_supplied` for every money
    // field, because mock mode never supplies one. Before this, the totals object carried
    // no `data_state` key at all, and every card read that as "not supplied" by accident
    // of `results.js`'s fallback branch rather than because mock mode said so.
    production_share_percent: share.value,
    data_state: {
      production_share_percent: share.state,
      total_value_nzd: 'not_supplied',
      wasted_value_nzd: 'not_supplied',
      wasted_share_percent: 'not_supplied',
      saving_nzd: 'not_supplied',
    },
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
    totals: mockTotals(fixture.totals || {}, entries, requestEntries),
    entries,
  }
}

async function mockRequest(path, options) {
  if (path === '/taxonomy') return readFixture('taxonomy.json')
  if (path.startsWith('/factors')) return readFixture('factors.json')
  if (path === '/stats') return readFixture('stats.json')
  if (path === '/calculate' && options.method === 'POST') return mockCalculate(options)
  // §6.2.2 answers 204 with no body always, whether or not a row moved — mock mode
  // reproduces that rather than a shape a real caller would never see.
  if (path === '/contribute' && options.method === 'POST') return null
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

/**
 * `POST /api/v1/export/pdf`. The document, not JSON, so this cannot go through
 * `request()` above: that helper always reads `response.json()`, which throws on a
 * binary body. Everything else about the failure shapes matches it — the same
 * `ApiError`, the same envelope fields read off a JSON error body where the route
 * answers one.
 *
 * @param {object} payload  `submission.js`'s `exportPayload(state, locale)`.
 * @returns {Promise<Blob>}
 */
export async function exportPdf(payload) {
  let response
  try {
    response = await fetch(`${API_BASE}/export/pdf`, {
      method: 'POST',
      headers: { Accept: 'application/pdf', 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
  } catch {
    throw new ApiError('NETWORK_ERROR', t('The calculator service could not be reached. Check your connection and try again.'))
  }
  if (!response.ok) {
    let body = null
    try {
      body = await response.json()
    } catch {
      // The error body is not JSON either; `body` stays null and the fallback
      // message below is what is shown.
    }
    throw new ApiError(
      body?.error?.code || body?.code || 'HTTP_ERROR',
      body?.error?.message || body?.message || t('The request could not be completed.'),
      body?.error?.details || body?.details || [],
      response.status,
    )
  }
  return response.blob()
}

/**
 * §6.2.2's opt-in. `token` is the only field the request carries — the same session
 * token `/calculate` already minted and the front end already holds in `sessionStorage`
 * — and the response is 204 with no body, always: the route gives nothing away about
 * whether the token still names a live submission, so this call cannot be read as a
 * success/failure signal about anything but the request having been made.
 */
export function contribute(token) {
  return request('/contribute', {
    method: 'POST',
    body: JSON.stringify({ token }),
  })
}

export function getFactors(opts = {}) {
  const query = opts.version ? `?version=${encodeURIComponent(opts.version)}` : ''
  return request(`/factors${query}`)
}

/**
 * The client's WordPress posts, or `null` when no news origin is configured.
 *
 * **`null` is not a failure and must not be flattened into an empty list.** It says the
 * deployment has no WordPress, which is the common case; an empty list says the site was
 * asked and had nothing to give. The home page removes its news section for the first and
 * shows a temporarily-unavailable notice for the second.
 */
export async function getNewsPosts(limit = 6) {
  if (!NEWS_ORIGIN) return null

  const numericLimit = Number(limit)
  const integerLimit = Number.isFinite(numericLimit) ? Math.trunc(numericLimit) : 6
  const safeLimit = Math.min(100, Math.max(1, integerLimit))
  const per_page = String(safeLimit)
  const query = new URLSearchParams({ per_page })
  const url = `${NEWS_ORIGIN}${NEWS_PATH}?${query.toString()}&_embed`

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
