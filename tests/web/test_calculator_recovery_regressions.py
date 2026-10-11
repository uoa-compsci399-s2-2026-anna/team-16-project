"""Exercise submission and recovery through the real JavaScript event handlers."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(shutil.which('node') is None, reason='Node executes the browser modules')

HARNESS = r"""
import { readFileSync } from 'node:fs'
const root = process.argv[2]
const input = JSON.parse(process.argv[3])
globalThis.window = { location: { search: '' }, scrollTo() {} }
globalThis.document = { getElementById: () => null, querySelector: () => null, querySelectorAll: () => [] }
globalThis.CSS = { escape: value => value }
globalThis.requestAnimationFrame = () => {}
const stored = new Map()
globalThis.sessionStorage = { getItem: key => stored.get(key) || null, setItem: (key, value) => stored.set(key, value), removeItem: key => stored.delete(key) }
const { state, setState, leafKey } = await import(new URL('state.js', root))
const { bindCalculator } = await import(new URL('calculator.js', root))
const { compareImprovement } = await import(new URL('improvement.js', root))
const { pruneAnswers } = await import(new URL('snapshot.js', root))
const taxonomy = JSON.parse(readFileSync(new URL('../../tests/fixtures/taxonomy.json', root)))
const key = leafKey({ foodCategory: 'dairy' })
const chain = {
  sector: 'processing', foodCategories: ['dairy'], foodItems: {}, foodUnspecified: false,
  leafFigures: { [key]: {
    measureMode: 'mass', totalAmount: '10', totalUnit: 'kilograms', unitPreset: null,
    unitCount: '', totalInputKg: '', totalValueNzd: '', wastedValueNzd: '',
    current: [{ id: 'one', destination: 'landfill', qtyInput: '10', unit: 'kilograms' }],
  } },
}
setState({ ...chain, taxonomy, loading: false, step: 4, entries: [], token: null,
  result: null, error: null, errorAt: null, fieldErrors: {}, returnTo: null,
  improvementOpen: true, improvedAllocations: [{ compost: 100 }] })
const handlers = {}
bindCalculator({ addEventListener: (type, fn) => { handlers[type] = fn } }, () => {})
const press = action => {
  const control = { dataset: { action } }
  handlers.click({ target: { closest: selector => selector === '[data-action]' ? control : null } })
}
let requests = []
globalThis.fetch = async (url, options) => {
  requests.push(JSON.parse(options.body))
  return input.apiFailure
    ? { ok: false, status: 500, json: async () => ({ error: { code: 'HTTP_ERROR', message: 'Service failed' } }) }
    : { ok: true, json: async () => ({ token: 'accepted-token', totals: {}, entries: [{}] }) }
}
let dropped = []
if (input.probe === 'allocation') {
  const figures = state.leafFigures[key]
  figures.totalUnit = input.unit
  figures.totalAmount = input.unit === 'tonnes' ? '0.01' : '10'
  figures.current[0].qtyInput = input.amount
  setState({ step: 3 })
  press('continue')
} else if (input.probe === 'storage') {
  if (input.getter) Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, get() { throw new Error('blocked') } })
  else if (!input.available) globalThis.sessionStorage.setItem = () => { throw new Error('quota exceeded') }
  if (input.compare) await compareImprovement(state)
  else { press('calculate'); await new Promise(resolve => setTimeout(resolve, 0)) }
} else if (input.probe === 'retired') {
  chain.leafFigures[key].current = [
    { id: 'retired', destination: 'landfill', qtyInput: '2', unit: 'preset:retired_bin' },
    { id: 'valid', destination: 'compost', qtyInput: '5', unit: 'kilograms' },
  ]
  const answers = input.saved
    ? { ...state, entries: [structuredClone(chain)] }
    : { ...state, ...chain }
  const restored = pruneAnswers(answers, taxonomy)
  dropped = restored.dropped
  setState({ ...restored.patch, restoreDropped: dropped })
  if (input.compare) await compareImprovement(state)
  else press('calculate')
  await new Promise(resolve => setTimeout(resolve, 0))
}
console.log(JSON.stringify({ step: state.step, error: state.error, fieldErrors: state.fieldErrors,
  result: state.result, token: state.token, storedToken: stored.get('kaiCalculatorToken') || null, improvementError: state.improvementError,
  improvementResult: state.improvementResult, dropped, requests,
  current: input.saved ? state.entries[0].leafFigures[key].current : state.leafFigures[key].current }))
"""


def probe(tmp_path, **input):
    script = tmp_path / 'probe.mjs'
    script.write_text(HARNESS)
    result = subprocess.run(
        [shutil.which('node'), str(script), (ROOT / 'web/js').as_uri() + '/', json.dumps(input)],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('unit', ['kilograms', 'tonnes'])
@pytest.mark.parametrize('amount', ['5.01', '14.99'])
def test_mixed_units_cannot_hide_a_mass_difference(tmp_path, unit, amount):
    result = probe(tmp_path, probe='allocation', unit=unit, amount=amount)
    assert result['step'] == 3
    assert result['error']
    if unit == 'tonnes':
        assert '0.00499' in result['error']


@pytest.mark.parametrize('unit', ['kilograms', 'tonnes'])
def test_equal_mass_in_mixed_units_can_continue(tmp_path, unit):
    result = probe(tmp_path, probe='allocation', unit=unit, amount='10.00')
    assert result['step'] == 4
    assert result['error'] is None


@pytest.mark.parametrize('compare', [False, True])
@pytest.mark.parametrize('getter', [False, True])
def test_successful_response_survives_unavailable_storage(tmp_path, compare, getter):
    result = probe(tmp_path, probe='storage', compare=compare, getter=getter)
    assert len(result['requests']) == 1
    assert result['token'] == 'accepted-token'
    if compare:
        assert result['improvementResult'] is not None
        assert result['improvementError'] is None
    else:
        assert result['step'] == 5
        assert result['result'] is not None
        assert result['error'] is None


@pytest.mark.parametrize('saved', [False, True])
def test_retired_row_unit_is_reported_and_must_be_corrected(tmp_path, saved):
    result = probe(tmp_path, probe='retired', saved=saved)
    assert {'kind': 'unit_preset', 'code': 'retired_bin'} in result['dropped']
    assert result['current'][0]['qtyInput'] == ''
    assert result['current'][0]['unit'] == 'kilograms'
    assert result['current'][1]['qtyInput'] == '5'
    assert result['requests'] == []
    assert result['error']
    if saved:
        assert result['step'] == 4
        assert result['fieldErrors']['entries[0]']
    else:
        assert result['step'] == 3


@pytest.mark.parametrize('saved', [False, True])
def test_comparison_cannot_persist_a_pruned_current_mass(tmp_path, saved):
    result = probe(tmp_path, probe='retired', saved=saved, compare=True)
    assert result['requests'] == []
    assert result['improvementResult'] is None
    assert result['improvementError']


@pytest.mark.parametrize('compare', [False, True])
def test_successful_response_still_remembers_token_when_storage_works(tmp_path, compare):
    result = probe(tmp_path, probe='storage', compare=compare, available=True)
    assert result['token'] == result['storedToken'] == 'accepted-token'


@pytest.mark.parametrize('compare', [False, True])
def test_api_failure_is_not_hidden_by_storage_recovery(tmp_path, compare):
    result = probe(tmp_path, probe='storage', compare=compare, apiFailure=True)
    assert result['token'] is None
    if compare:
        assert result['improvementResult'] is None
        assert result['improvementError']
    else:
        assert result['result'] is None
        assert result['error']
