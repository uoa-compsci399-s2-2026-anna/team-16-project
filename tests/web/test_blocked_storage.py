"""A browser that refuses to store the dedup token must not lose the calculation.

Contract v1.114. These run the **real** modules -- `state.js`, `calculator.js`,
`improvement.js` -- under Node against `tests/fixtures/taxonomy.json`, with `fetch` and
`sessionStorage` replaced by doubles the test drives.

**The two refusals are different and both are measured.** `refuse-write` is a storage
object whose `setItem` throws while reads go on working, which is the quota and what
Safari's private mode has historically done. `refuse-access` makes touching the
`sessionStorage` property throw at all, which is what the HTML specification prescribes
for a user agent "configured to not allow the page to persist data" -- the setting Chrome
and Firefox expose as blocking site data. Both reach the same two lines, and every case
below runs under each.

**What makes this worth a file of its own is the order of two statements.** The token was
written *before* the `setState` that puts the result on screen, and inside the `try` whose
`catch` means *the calculation failed*. §2.3 and §5.3 make one call one submission, so by
the time the write threw the row was already in the database: the visitor was told a
calculation had failed that had in fact succeeded and been stored. Then -- because
`state.token` was never set either -- their retry carried no token, so §5.3's upsert had
nothing to match and inserted a **second** row. Every press was another submission feeding
the public statistics of §5.4.

**The controls are the half that matters most.** A change that simply deleted both writes
would satisfy every refusal case in this file.
`test_the_token_is_still_written_when_the_browser_allows_it` and its Compare Impact twin
are what separate a fix from a deletion, and the mutation `store-nothing` is in the matrix
for exactly that reason.

**Out of scope, deliberately.** With the write refused the visitor keeps working and loses
only the reload-restore -- the same degradation `writeSnapshot` has always had for the
answers -- and **nothing tells them so**. Saying it needs a string in all twenty
catalogues, or `api.i18n.Catalogue.gettext` answers 500 to every non-English PDF; that is
its own change and not this one.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(shutil.which('node') is None, reason='Node executes the browser modules')

REFUSALS = ['refuse-write', 'refuse-access']
DENIED_TEXT = "Failed to execute 'setItem' on 'Storage'"

HARNESS = r"""
import { readFileSync } from 'node:fs'
const root = process.argv[2]
const input = JSON.parse(process.argv[3])

// What a browser actually raises: a DOMException with a `message` and no `code` in §9's
// sense. Chrome's own wording for a document that is not allowed to persist data.
const denied = () => {
  const error = new Error("Failed to execute 'setItem' on 'Storage': Access is denied for this document.")
  error.name = 'SecurityError'
  return error
}
const stored = new Map()
const working = {
  getItem: key => stored.get(key) || null,
  setItem: (key, value) => stored.set(key, value),
  removeItem: key => stored.delete(key),
}
if (input.storage === 'refuse-access') {
  Object.defineProperty(globalThis, 'sessionStorage', { get() { throw denied() } })
} else if (input.storage === 'refuse-write') {
  globalThis.sessionStorage = { ...working, setItem: () => { throw denied() } }
} else {
  globalThis.sessionStorage = working
}

globalThis.window = { location: { search: '' }, scrollTo() {} }
globalThis.document = { getElementById: () => null, querySelector: () => null, querySelectorAll: () => [] }
globalThis.CSS = { escape: value => value }
globalThis.requestAnimationFrame = () => {}

// **Does the module graph load at all?** `state.js` reads the token at module scope, so a
// refusal there would be the whole calculator not starting -- which is the premise this
// file would otherwise be assuming rather than testing.
let loaded = true
let loadError = null
let state, setState, leafKey, storeToken, bindCalculator, compareImprovement
try {
  ;({ state, setState, leafKey, storeToken } = await import(new URL('state.js', root)))
  ;({ bindCalculator } = await import(new URL('calculator.js', root)))
  ;({ compareImprovement } = await import(new URL('improvement.js', root)))
} catch (error) {
  loaded = false
  loadError = `${error.name}: ${error.message}`
}
if (!loaded) {
  console.log(JSON.stringify({ loaded, loadError }))
  process.exit(0)
}

const taxonomy = JSON.parse(readFileSync(new URL('../../tests/fixtures/taxonomy.json', root)))
const key = leafKey({ foodCategory: 'dairy' })

// One complete, valid chain: 10 kg, all of it to landfill. Step 4 with nothing left to
// correct, so any refusal below is the one being tested and not a leftover field error.
const chain = () => ({
  sector: 'processing', foodCategories: ['dairy'], foodItems: {}, foodUnspecified: false,
  leafFigures: { [key]: {
    measureMode: 'mass', totalAmount: '10', totalUnit: 'kilograms', unitPreset: null,
    unitCount: '', totalInputKg: '', totalValueNzd: '', wastedValueNzd: '',
    current: [{ id: 'one', destination: 'landfill', qtyInput: '10', unit: 'kilograms' }],
  } },
})
setState({ ...chain(), taxonomy, loading: false, step: 4, entries: [], token: null,
  result: null, error: null, errorAt: null, errorCode: null, fieldErrors: {}, returnTo: null,
  improvementOpen: true, improvementResult: null, improvementError: null,
  improvedAllocations: [{ compost: 100 }] })

// The API succeeds every time. §5.3 persists on arrival, so each resolved call below is a
// row that already exists by the time the front end touches storage.
let calls = 0
const sentTokens = []
globalThis.fetch = (url, options) => {
  calls += 1
  const body = JSON.parse(options.body)
  if (!('token' in body)) throw new Error('the request body has no `token` field: this probe is reading the wrong name')
  sentTokens.push(body.token)
  const minted = `server-token-${calls}`
  return Promise.resolve({ ok: true, json: async () => ({ token: minted, totals: {}, entries: [{}] }) })
}

const handlers = {}
bindCalculator({ addEventListener: (type, fn) => { handlers[type] = fn } }, async () => {})
const press = action => handlers.click({
  target: { closest: selector => (selector === '[data-action]' ? { dataset: { action } } : null) },
})
const flush = () => new Promise(resolve => setTimeout(resolve, 0))

let threwOnFalsy = null
if (input.probe === 'calculate') {
  press('calculate')
  await flush()
  await flush()
} else if (input.probe === 'retry') {
  press('calculate')
  await flush()
  await flush()
  press('calculate')
  await flush()
  await flush()
} else if (input.probe === 'compare') {
  await compareImprovement(state)
  await flush()
} else if (input.probe === 'falsyToken') {
  // `storeToken` is handed a falsy value on every path where the server minted nothing.
  try {
    storeToken(null)
    storeToken(undefined)
    storeToken('')
    threwOnFalsy = false
  } catch {
    threwOnFalsy = true
  }
}

console.log(JSON.stringify({
  loaded, loadError,
  step: state.step, hasResult: state.result !== null,
  error: state.error, errorCode: state.errorCode, token: state.token,
  improvementResult: state.improvementResult !== null, improvementError: state.improvementError,
  storedToken: stored.get('kaiCalculatorToken') || null,
  storedKeys: [...stored.keys()].sort(),
  calls, sentTokens, threwOnFalsy,
}))
"""


def probe(tmp_path, **payload):
    script = tmp_path / 'harness.mjs'
    script.write_text(HARNESS, encoding='utf-8')
    root = (ROOT / 'web' / 'js').as_uri() + '/'
    finished = subprocess.run(
        ['node', str(script), root, json.dumps(payload)],
        capture_output=True, text=True, encoding='utf-8', cwd=ROOT, timeout=120,
    )
    assert finished.returncode == 0, finished.stderr
    return json.loads(finished.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize('storage', REFUSALS)
def test_the_calculator_starts_when_the_browser_refuses_site_data(tmp_path, storage):
    """The premise, measured rather than assumed.

    `state.js` reads the token at module scope and says in its own comment that an
    exception there "is the whole calculator not starting". If that read ever lost its
    `try`, every other case in this file would fail for that reason instead of its own, so
    the premise gets an assertion of its own.
    """
    seen = probe(tmp_path, storage=storage, probe='falsyToken')
    assert seen['loaded'] is True, seen['loadError']


@pytest.mark.parametrize('storage', REFUSALS)
def test_a_refused_token_write_does_not_discard_a_calculation_that_succeeded(tmp_path, storage):
    """The defect. The API answered 200 and §5.3 had already stored the row."""
    seen = probe(tmp_path, storage=storage, probe='calculate')
    assert seen['calls'] == 1
    assert seen['step'] == 5, 'a storage refusal left the visitor on the review step'
    assert seen['hasResult'] is True
    assert seen['error'] is None
    assert seen['errorCode'] is None
    assert seen['token'] == 'server-token-1', 'the live token must be kept even when it cannot be persisted'


@pytest.mark.parametrize('storage', REFUSALS)
def test_the_browsers_own_words_are_never_shown_to_the_visitor(tmp_path, storage):
    """§9.2: `message` is written for end users.

    `publicError`'s `default` arm is `error.message || t('The calculation could not be
    completed.')`, and a `DOMException` **has** a `message` -- so the fallback copy never
    got a chance and the visitor read *Access is denied for this document* about a
    calculation of theirs that had succeeded.
    """
    seen = probe(tmp_path, storage=storage, probe='calculate')
    assert seen['error'] is None
    assert DENIED_TEXT not in json.dumps(seen), "the browser's exception text reached the interface"


@pytest.mark.parametrize('storage', REFUSALS)
def test_a_refused_token_write_does_not_turn_a_retry_into_a_second_submission(tmp_path, storage):
    """The public-statistics consequence, and why this is not merely cosmetic.

    §5.3 keys the stored row on the token. A visitor told their calculation failed presses
    Calculate again; if the first response's token went down with the write, the second
    request carries `null`, the upsert has nothing to match, and §5.4 aggregates two rows
    where one calculation happened.
    """
    seen = probe(tmp_path, storage=storage, probe='retry')
    assert seen['calls'] == 2
    assert seen['sentTokens'] == [None, 'server-token-1'], (
        "the second request must carry the first response's token so §5.3 upserts the same row")
    assert seen['step'] == 5
    assert seen['error'] is None


@pytest.mark.parametrize('storage', REFUSALS)
def test_compare_impact_survives_the_same_refusal(tmp_path, storage):
    """The second call site. `improvement.js` had the identical two lines in the identical
    order, and §5.3 has Compare Impact upsert over Calculate's own row."""
    seen = probe(tmp_path, storage=storage, probe='compare')
    assert seen['improvementResult'] is True
    assert seen['improvementError'] is None
    assert seen['token'] == 'server-token-1'
    assert DENIED_TEXT not in json.dumps(seen)


def test_the_token_is_still_written_when_the_browser_allows_it(tmp_path):
    """**The control.** Deleting both writes outright would pass every refusal case above;
    only this says the token is still persisted where it can be, which is the whole of what
    the restore across a reload depends on."""
    seen = probe(tmp_path, storage='working', probe='calculate')
    assert seen['step'] == 5
    assert seen['storedToken'] == 'server-token-1'
    assert 'kaiCalculatorToken' in seen['storedKeys']


def test_compare_impact_still_writes_the_token_when_the_browser_allows_it(tmp_path):
    """The same control, one call site over."""
    seen = probe(tmp_path, storage='working', probe='compare')
    assert seen['improvementResult'] is True
    assert seen['storedToken'] == 'server-token-1'


def test_a_falsy_token_is_a_silent_no_op_rather_than_a_stored_string(tmp_path):
    """`storeToken(null)` happens on every path where the server minted nothing.

    Without its own guard the key would hold the four characters `null`, which
    `state.js`'s module-scope read would then hand back as a live token.
    """
    seen = probe(tmp_path, storage='working', probe='falsyToken')
    assert seen['threwOnFalsy'] is False
    assert seen['storedKeys'] == []
    assert seen['storedToken'] is None
