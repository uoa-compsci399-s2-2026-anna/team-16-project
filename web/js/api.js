const API_BASE = '/api/v1'

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
