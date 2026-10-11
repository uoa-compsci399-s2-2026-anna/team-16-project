"""A response to a calculation the visitor abandoned must not write to the one on screen.

Contract v1.113. These run the **real** modules — `state.js`, `calculator.js`,
`improvement.js`, `results.js` — under Node against `tests/fixtures/taxonomy.json`, with
`fetch` and `sessionStorage` replaced by doubles the test drives. Nothing is stubbed that
the defect lives in: the click arrives through `bindCalculator`'s own dispatcher, and the
reset arrives through `resetCalculator`, which is the one function all three doors call.

**Why a Node harness and not a browser.** The defect is a window between a request and its
response, and the test has to *hold* that window open while it presses something else.
Playwright can throttle a route, but it cannot make the gap deterministic, and the three
clocks involved (`setTimeout`, the fetch, the re-render) would make the result flaky at
exactly the moments it matters. What is lost by staying out of the browser is the
`disabled` attribute's effect on a real pointer, and
`test_pending_comparison_locks_its_inputs_and_submit` says plainly which half of that it
can and cannot see.

**The three doors into `resetCalculator`, because only one of them is a `data-action`.**
`results.js:2345` renders *Start over* on the results page. `main.js:140` binds the
header's *Clear all data*, which `renderChrome` hides only when `hasData()` is false — so
it is on screen at step 4 the moment the visitor has typed anything. `main.js:139` binds
the brand button beside it, with no confirmation at all. Pressing Calculate and then that
header Clear is the reported sequence, and `resetCalculator()` is what it reaches; these
probes call it directly for that reason rather than simulating a control that belongs to
another module's markup.
"""
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
globalThis.sessionStorage = {
  getItem: key => stored.get(key) || null,
  setItem: (key, value) => stored.set(key, value),
  removeItem: key => stored.delete(key),
}
const { state, setState, leafKey, resetCalculator } = await import(new URL('state.js', root))
const { bindCalculator } = await import(new URL('calculator.js', root))
const { compareImprovement, updateImprovementInput, ImprovementScenario } = await import(new URL('improvement.js', root))
const { contributeCalculation } = await import(new URL('results.js', root))
const taxonomy = JSON.parse(readFileSync(new URL('../../tests/fixtures/taxonomy.json', root)))
const key = leafKey({ foodCategory: 'dairy' })

// One complete, valid chain: 10 kg, all of it to landfill. Step 4 with nothing left to
// correct, so every refusal below is the one being tested and not a leftover field error.
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
  improvementOpen: true, improvedAllocations: [{ compost: 100 }] })

// `reloadTaxonomy` is counted, and optionally held open: the `catch` block awaits it, so a
// Clear can arrive either BEFORE it (which must skip it entirely) or DURING it.
let reloads = 0
let releaseReload = null
const reloadTaxonomy = () => {
  reloads += 1
  if (!input.holdReload) return Promise.resolve()
  return new Promise(resolve => { releaseReload = resolve })
}
const handlers = {}
bindCalculator({ addEventListener: (type, fn) => { handlers[type] = fn } }, reloadTaxonomy)
const press = action => {
  const control = { dataset: { action } }
  handlers.click({ target: { closest: selector => selector === '[data-action]' ? control : null } })
}
const flush = () => new Promise(resolve => setTimeout(resolve, 0))

// §6.5's minute, held rather than waited for. `submitCalculation` schedules exactly one
// 60,000 ms timer -- the deferred clear of `rateLimitedUntil` -- so capturing that delay
// and firing it by hand is the whole of the fake clock this needs, and `flush` above keeps
// using the real one.
const minuteTimers = []
const realSetTimeout = globalThis.setTimeout
globalThis.setTimeout = (fn, delay, ...rest) => {
  if (delay === 60000) return minuteTimers.push(fn)
  return realSetTimeout(fn, delay, ...rest)
}

const requests = []
const deferred = []
let settle = null
globalThis.fetch = (url, options) => {
  requests.push(JSON.parse(options.body))
  return new Promise((resolve, reject) => {
    const entry = { resolve, reject }
    deferred.push(entry)
    settle = () => (input.failure
      ? reject(new Error('the response the visitor is no longer waiting for'))
      : resolve({ ok: true, json: async () => ({ token: 'late-token', totals: {}, entries: [{}] }) }))
  })
}
// §9's `UNKNOWN_CODE` is the one failure the `catch` block awaits something for.
const settleUnknownCode = () => deferred[0].resolve({
  ok: false, status: 422,
  json: async () => ({ error: { code: 'UNKNOWN_CODE', message: 'A code in the request is not published.', details: [] } }),
})
const settleRateLimited = index => deferred[index].resolve({
  ok: false, status: 429,
  json: async () => ({ error: { code: 'RATE_LIMITED', message: 'Too many requests.', details: [] } }),
})

let locked = null
let inputsDisabled = null
let inputsSeen = null
let loadingAfterReset = null
let rateLimitedWhileWaiting = null
let errorWhileWaiting = null

if (input.probe === 'asyncReset') {
  setState({ token: 'old-token' })
  if (input.kind === 'calculate') press('calculate')
  else if (input.kind === 'compare') compareImprovement(state)
  else contributeCalculation(state)
  await flush()
  resetCalculator()
  if (input.restart) setState({ ...chain(), token: 'new-token', step: 0, result: null })
  settle()
  await flush()
} else if (input.probe === 'abandonedUnknownCode') {
  press('calculate')
  await flush()
  resetCalculator()
  settleUnknownCode()
  await flush()
} else if (input.probe === 'clearDuringReload') {
  press('calculate')
  await flush()
  settleUnknownCode()
  await flush()
  // Execution is now parked inside the awaited `reloadTaxonomy`.
  resetCalculator()
  releaseReload()
  await flush()
} else if (input.probe === 'rateLimitRetimer') {
  // Rate-limited once, cleared, rate-limited again -- then the FIRST minute's timer fires
  // while the second minute is still running.
  press('calculate')
  await flush()
  settleRateLimited(0)
  await flush()
  resetCalculator()
  setState({ ...chain(), step: 4 })
  press('calculate')
  await flush()
  settleRateLimited(1)
  await flush()
  rateLimitedWhileWaiting = state.rateLimitedUntil > Date.now()
  errorWhileWaiting = state.error
  minuteTimers[0]()
  await flush()
} else if (input.probe === 'stuckLoading') {
  press('calculate')
  await flush()
  resetCalculator()
  loadingAfterReset = state.loading
  // The visitor starts again and walks back to the review step. The response never comes.
  setState({ ...chain(), step: 4 })
} else if (input.probe === 'compareCancel') {
  const pending = compareImprovement(state)
  await flush()
  press('cancel-improvement')
  if (input.reopen) {
    press('explore-improvements')
    setState({ improvedAllocations: [{ compost: 100 }] })
    compareImprovement(state)
    await flush()
  }
  if (input.failure) deferred[0].reject(new Error('the cancelled comparison failed'))
  else deferred[0].resolve({ ok: true, json: async () => ({ token: 'cancelled-token', totals: {} }) })
  await pending
  await flush()
  locked = state.improvementLoading
  if (input.reopen) {
    deferred[1].resolve({ ok: true, json: async () => ({ token: 'reopened-token', totals: {} }) })
    await flush()
  }
} else if (input.probe === 'compareBusy') {
  const pending = compareImprovement(state)
  await flush()
  const html = ImprovementScenario(state)
  const matches = [...html.matchAll(/<input\b[^>]*data-improvement-code[^>]*>/g)]
  inputsSeen = matches.length
  inputsDisabled = matches.length > 0 && matches.every(hit => /\bdisabled\b/.test(hit[0]))
  // **The rendered Compare button, not a `document.querySelector` double.** The attribute
  // is written by `ImprovementScenario`, and `updateImprovementInput`'s own
  // `state.improvementLoading` guard returns before the line that patches a live button --
  // so a probe watching that line watches dead code while a comparison is in flight, and
  // reports whatever it was initialised to.
  const compare = html.match(/<button[^>]*data-action="compare-improvement"[^>]*>/)
  locked = compare === null ? null : /\bdisabled\b/.test(compare[0])
  for (const [code, value] of [['compost', '50'], ['landfill', '50']]) {
    updateImprovementInput({ dataset: { improvementCode: code }, type: 'number', value }, state)
  }
  const duplicate = compareImprovement(state)
  for (const entry of deferred) entry.resolve({ ok: true, json: async () => ({ token: 'late-token', totals: {} }) })
  await Promise.all([pending, duplicate])
  await flush()
}

console.log(JSON.stringify({
  step: state.step, error: state.error, errorCode: state.errorCode, result: state.result,
  token: state.token, storedToken: stored.get('kaiCalculatorToken') || null,
  storedKeys: [...stored.keys()], loading: state.loading, loadingAfterReset,
  improvementResult: state.improvementResult, improvementError: state.improvementError,
  improvementLoading: state.improvementLoading, improvementOpen: state.improvementOpen,
  improvedAllocations: state.improvedAllocations,
  contributing: state.contributing, contributed: state.contributed,
  contributeError: state.contributeError,
  requests: requests.length, reloads, locked, inputsDisabled, inputsSeen,
  rateLimitedUntilFuture: state.rateLimitedUntil > Date.now(),
  rateLimitedWhileWaiting, errorWhileWaiting, minuteTimers: minuteTimers.length,
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


@pytest.mark.parametrize('restart', [False, True])
@pytest.mark.parametrize('failure', [False, True])
@pytest.mark.parametrize('kind', ['calculate', 'compare', 'contribute'])
def test_a_late_response_cannot_restore_a_cleared_calculation(tmp_path, kind, failure, restart):
    """The reported sequence: press the button, press the header's Clear, confirm, wait.

    **Measured on `6ef309a`, `kind='calculate'`, `failure=False`:** `step` went from -1 to
    **5**, `token` came back as the response's own, and `writeSnapshot` /
    `writeResultSnapshot` re-created all three storage keys `clearSnapshot` had removed a
    moment earlier — `kaiCalculatorToken` among them, which is §5.3's dedup key, so the
    next calculation inside the hour would have upserted onto the row the visitor had
    just abandoned. `resetCalculator`'s own note says the snapshot has to go with the
    token "or the Clear button is a false statement"; it was one.

    `failure=True` is not a duplicate of it. On the same build a late **network** error put
    *The calculator service could not be reached* on the introduction screen, and a late
    `UNKNOWN_CODE` moved the visitor to **step 0** under *Calculator options have changed* —
    for a request they had abandoned. Different symptom, same cause, and the error path
    writes no storage, so `storedKeys` alone would not have caught it.

    `restart=True` is the harsher half and the reason the guard compares a token as well as
    a generation: the visitor clears, starts again, and the first call's answer lands on the
    second calculation. `kind='contribute'` is where that matters most — a 204 arriving then
    marked a result nobody had offered as already contributed.
    """
    result = probe(tmp_path, probe='asyncReset', kind=kind, failure=failure, restart=restart)
    assert result['requests'] == 1
    assert result['step'] == (0 if restart else -1)
    assert result['token'] == ('new-token' if restart else None)
    assert result['result'] is None
    assert result['error'] is None
    assert result['errorCode'] is None
    assert result['improvementResult'] is None
    assert result['improvementError'] is None
    assert result['contributeError'] is None
    assert not result['contributed']
    assert not result['loading']
    assert not result['improvementLoading']
    assert not result['contributing']
    #: Nothing the abandoned call touched may be left in storage. On the fixed build the
    #: `restart=True` case has not pressed Calculate again, so this is empty either way.
    assert result['storedKeys'] == [], result['storedKeys']


def test_an_abandoned_unknown_code_does_not_refetch_the_taxonomy(tmp_path):
    """**This is the assertion that gives the first of the two `catch` guards a reason.**

    `submitCalculation`'s `catch` block checks the generation twice, with
    `await reloadTaxonomy(...)` between them, and a reviewer who removes either one alone
    sees every other test in this file stay green. Measured, on the fixed build with one
    guard removed at a time:

    ======================  ==================  =======  ============
    build                   `reloadTaxonomy`    `step`   `errorCode`
    ======================  ==================  =======  ============
    `6ef309a`               1                   0        UNKNOWN_CODE
    fixed                   **0**               -1       ``None``
    first guard removed     **1**               -1       ``None``
    second guard removed    0                   -1       ``None``
    both removed            1                   0        UNKNOWN_CODE
    ======================  ==================  =======  ============

    So the pair is killed only together, and the *first* guard's own effect is visible in
    exactly one column: whether a second `GET /taxonomy` goes out for a request nobody is
    waiting for. §6.5 allows a caller 600 an hour and answers 429 after that, so a refetch
    nobody asked for is not free. Without this case the honest report on that line is
    "no test requires it", which is how a useful guard gets deleted.
    """
    result = probe(tmp_path, probe='abandonedUnknownCode')
    assert result['requests'] == 1
    assert result['reloads'] == 0, (
        'a calculation the visitor cleared refetched the taxonomy; the first generation '
        'check in the catch block is what skips that work'
    )
    assert result['step'] == -1
    assert result['error'] is None
    assert result['errorCode'] is None


def test_a_clear_during_the_taxonomy_refetch_is_still_honoured(tmp_path):
    """And the second guard, which is the one the `await` above it makes necessary.

    Here the Clear arrives **while** `reloadTaxonomy` is in flight, so the first check has
    already passed and only the second can refuse. Measured: the reload is entered (so this
    really is the window described), and with the second guard removed `step` becomes
    **0** under `UNKNOWN_CODE` while the visitor is on the introduction screen.

    `reloads == 1` is asserted rather than assumed: if the harness stopped reaching the
    reload, this case would pass on the first guard alone and prove nothing about the
    second.
    """
    result = probe(tmp_path, probe='clearDuringReload', holdReload=True)
    assert result['reloads'] == 1, 'the probe never entered the window this case exists for'
    assert result['step'] == -1
    assert result['error'] is None
    assert result['errorCode'] is None
    assert result['result'] is None


def test_clear_frees_the_calculate_button_a_pending_request_had_disabled(tmp_path):
    """A third defect in the same window, and it needs no response at all to show.

    `calculateOtherwiseDisabled()` is `state.loading || …`, and `resetCalculator` did not
    touch `loading`. So a Clear pressed while a calculation was in flight left the flag set
    with nothing on the way to clear it. **Measured on `6ef309a`:** `loading` true after
    Calculate, **still true after Clear**, and still true once the whole form had been
    entered again — a Calculate button disabled and reading *Calculating…* for the rest of
    the session, for a request that never came back.

    The fix is `loading: !state.taxonomy` rather than `false`, because the field carries two
    meanings and a Clear before the first `GET /taxonomy` answers must leave the
    introduction screen waiting. Here the taxonomy is loaded, so the expected value is
    `False`.
    """
    result = probe(tmp_path, probe='stuckLoading')
    assert result['requests'] == 1
    assert result['loadingAfterReset'] is False, (
        'Clear left `loading` set, which disables Calculate through '
        '`calculateOtherwiseDisabled()` with nothing left to clear it'
    )
    assert result['loading'] is False
    assert result['step'] == 4


@pytest.mark.parametrize('reopen', [False, True])
@pytest.mark.parametrize('failure', [False, True])
def test_a_cancelled_comparison_cannot_replace_a_reopened_panel(tmp_path, failure, reopen):
    """Cancel has to disown the request, not just shut the panel.

    The dispatcher's old `setState({ improvementOpen: false, … })` cleared the screen and
    left the call in flight. Measured on `6ef309a` with `reopen=False`: the cancelled
    comparison still wrote `improvementResult` and `token`, so a comparison sat in the state
    under a closed panel. With `reopen=True` it was worse than untidy — the older answer
    replaced the newer one, because both wrote the same two fields and the slower one won.

    `reopen=True` is where `comparisonRequest` earns its place beside
    `calculatorGeneration`: no reset has happened, so the generation is unchanged and only
    the per-comparison counter can tell the two apart.
    """
    result = probe(tmp_path, probe='compareCancel', failure=failure, reopen=reopen)
    assert result['improvementError'] is None
    assert not result['improvementLoading']
    if reopen:
        assert result['improvementResult'] is not None
        assert result['improvementResult']['token'] == 'reopened-token', (
            'the cancelled comparison overwrote the one the visitor is waiting for'
        )
        assert result['token'] == 'reopened-token'
        assert result['improvementOpen']
    else:
        assert result['improvementResult'] is None
        assert result['token'] is None
        assert not result['improvementOpen']


def test_pending_comparison_locks_its_inputs_and_submit(tmp_path):
    """One comparison in flight, one request on the wire, and the panel inert meanwhile.

    On `6ef309a` the allocation could be edited while the comparison was running and
    *Compare Impact* could be pressed again, so **two** requests went out for one panel and
    the figures on screen described neither of them.

    **What this case can and cannot see.** `inputsDisabled` reads the attribute
    `DestinationAllocationRow` wrote into a string, which is not the same claim as "a
    pointer cannot reach the control" — `updateImprovementInput` patches the live DOM in
    place without re-rendering, which is why that function carries its own
    `state.improvementLoading` guard and why `improvedAllocations` is asserted below:
    together they say the *allocation did not move*, which is the thing that matters.
    `inputsSeen` is asserted non-empty because `Array.prototype.every` is `true` on an empty
    list, and a markup change that stopped emitting `data-improvement-code` would otherwise
    make `inputsDisabled` pass for ever.

    **`locked` reads the rendered button, and that is a correction rather than a
    preference.** The obvious probe — hand `document.querySelector` a `{ disabled: … }`
    double and watch `updateImprovementInput` set it — watches a line that cannot run,
    because that function's own `state.improvementLoading` guard returns above it while a
    comparison is in flight. A double initialised to `true` therefore reports `true` for a
    build that does nothing at all; measured here, initialising it to `false` leaves it
    `false` on the fixed build. `ImprovementScenario` is what writes the attribute, so that
    is what this reads.
    """
    result = probe(tmp_path, probe='compareBusy')
    assert result['requests'] == 1, 'a second comparison went out for one panel'
    assert result['inputsSeen'] > 0, 'the allocation inputs were not found, so the next assertion is vacuous'
    assert result['inputsDisabled']
    assert result['locked'] is not None, 'Compare Impact was not found in the rendered panel'
    assert result['locked'], 'Compare Impact was left pressable while its own request was in flight'
    assert result['improvedAllocations'] == [{'compost': 100}], (
        'the allocation moved under a comparison that had already been sent'
    )
    assert result['improvementResult'] is not None


def test_a_stale_rate_limit_timer_cannot_lift_the_current_one(tmp_path):
    """§6.5's minute is deferred by a `setTimeout`, and a minute is a long time.

    `submitCalculation` schedules the clear of `rateLimitedUntil` sixty seconds out. The
    visitor has every opportunity to press Clear and start again inside that minute, and on
    a second 429 a **second** timer is scheduled with its own minute to run.

    The first timer then fires while the second window is still open. Unguarded it takes the
    `errorCode === 'RATE_LIMITED'` branch — which is true again, for the new failure — and
    clears `rateLimitedUntil` and the message together: **the current block is lifted early
    and the sentence explaining it disappears**, roughly half a minute before the API will
    actually answer anything.

    Measured: with the generation check removed from that `setTimeout`, this case fails on
    both assertions below and nothing else in this file notices, which is why it exists.
    The minute is held rather than waited for — the harness captures callbacks scheduled at
    exactly 60,000 ms, and `submitCalculation` schedules no other timer at that delay.
    """
    result = probe(tmp_path, probe='rateLimitRetimer')
    assert result['requests'] == 2
    assert result['minuteTimers'] == 2, result['minuteTimers']
    #: The preconditions, so neither assertion below can pass by the window never opening.
    assert result['rateLimitedWhileWaiting'] is True
    assert result['errorWhileWaiting']
    assert result['rateLimitedUntilFuture'] is True, (
        "the first failure's timer lifted the second failure's rate-limit window"
    )
    assert result['errorCode'] == 'RATE_LIMITED'
    assert result['error'], 'the message explaining the current block was cleared by a stale timer'
