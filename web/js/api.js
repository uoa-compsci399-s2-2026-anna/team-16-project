const API_BASE = '/api/v1'
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
    throw new ApiError('NETWORK_ERROR', 'The calculator service could not be reached. Check your connection and try again.')
  }

  let body = null
  try {
    body = await response.json()
  } catch {
    if (!response.ok) throw new ApiError('HTTP_ERROR', 'The calculator service returned an unexpected response.', [], response.status)
  }

  if (!response.ok) {
    throw new ApiError(
      body?.error?.code || body?.code || 'HTTP_ERROR',
      body?.error?.message || body?.message || 'The request could not be completed.',
      body?.error?.details || body?.details || [],
      response.status,
    )
  }
  return body
}

async function readFixture(path) {
  const response = await fetch(`/tests/fixtures/${path}`)
  if (!response.ok) throw new ApiError('MOCK_FIXTURE_ERROR', `Mock fixture ${path} could not be loaded.`, [], response.status)
  return response.json()
}

async function mockRequest(path, options) {
  if (path === '/taxonomy') return readFixture('taxonomy.json')
  if (path.startsWith('/factors')) return readFixture('factors.json')
  if (path === '/stats') return readFixture('stats.json')
  if (path === '/calculate' && options.method === 'POST') {
    if (MOCK_ERROR) {
      const fixtureName = MOCK_ERROR.toLowerCase()
      const body = await readFixture(`errors/${fixtureName}.json`)
      throw new ApiError(body.error.code, body.error.message, body.error.details, body.error.code === 'RATE_LIMITED' ? 429 : 400)
    }
    const fixture = await readFixture('calculate_response.json')
    const payload = JSON.parse(options.body || '{}')
    const currentRows = payload.current || []
    const currentTotal = currentRows.reduce((sum, row) => sum + (Number(row.qty_kg) || 0), 0).toFixed(3)
    const result = structuredClone(fixture)
    result.current.total_kg = currentTotal
    if (result.current.metrics?.mass) {
      result.current.metrics.mass.total = Number(currentTotal).toFixed(10)
      result.current.metrics.mass.by_destination = currentRows.map(row => ({
        destination: row.destination,
        qty_kg: row.qty_kg,
        upstream: '1.0000000000',
        downstream: '0.0000000000',
        value: Number(row.qty_kg).toFixed(10),
      }))
    }
    if (!payload.alternative) {
      result.alternative = null
      result.net_benefit = null
    } else {
      const alternativeRows = payload.alternative || []
      const alternativeTotal = alternativeRows.reduce((sum, row) => sum + (Number(row.qty_kg) || 0), 0).toFixed(3)
      result.alternative.total_kg = alternativeTotal
      if (result.alternative.metrics?.mass) {
        result.alternative.metrics.mass.total = Number(alternativeTotal).toFixed(10)
        result.alternative.metrics.mass.by_destination = alternativeRows.map(row => ({ destination: row.destination, qty_kg: row.qty_kg, upstream: '1.0000000000', downstream: '0.0000000000', value: Number(row.qty_kg).toFixed(10) }))
      }
    }
    return { ...result, token: result.token || 'mock-session-token' }
  }
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
