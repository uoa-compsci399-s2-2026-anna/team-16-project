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

**And the visitor is now told, which v1.114 left open.** With the write refused the
calculator keeps working and loses only the reload-restore -- the degradation
`writeSnapshot` has always had -- and for one revision nothing said so. v1.115 adds the
sentence and the detection behind it, and the cases from `v1.115` onwards below are that
half. Two things about it are worth knowing before reading them: the detection is a
**write** probe and not a read, because a browser can answer every `getItem` and still
refuse a `setItem`; and the control -- a browser that allows storage being told nothing --
is the only assertion separating a notice from a decoration.
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
// `refuse-answers` and `refuse-result` are the **quota**, and they are the shapes the
// module-load probe cannot see: one character goes in, so the probe is told yes, and the
// document that is actually refused is the few hundred bytes of answers or the 32 KB of
// result. They are separated by key because the two writes happen on different screens
// and the notice's placement rule means only one of them is visible where it is made.
//
// `refuse-cleanup` is the narrow case that keeps the probe honest: the write lands and the
// `removeItem` that takes the sentinel back out is what throws. Nothing was refused, so
// nothing may be reported — which is only true while the two calls have a `try` each.
const SNAPSHOT_KEY = 'kaiCalculatorAnswers'
const RESULT_KEY = 'kaiCalculatorResult'
const PROBE_KEY = 'kaiCalculatorStorageProbe'
if (input.storage === 'refuse-access') {
  Object.defineProperty(globalThis, 'sessionStorage', { get() { throw denied() } })
} else if (input.storage === 'refuse-write') {
  globalThis.sessionStorage = { ...working, setItem: () => { throw denied() } }
} else if (input.storage === 'refuse-answers') {
  globalThis.sessionStorage = { ...working,
    setItem: (key, value) => { if (key === SNAPSHOT_KEY) throw denied(); stored.set(key, value) } }
} else if (input.storage === 'refuse-result') {
  globalThis.sessionStorage = { ...working,
    setItem: (key, value) => { if (key === RESULT_KEY) throw denied(); stored.set(key, value) } }
} else if (input.storage === 'refuse-cleanup') {
  globalThis.sessionStorage = { ...working,
    removeItem: key => { if (key === PROBE_KEY) throw denied(); stored.delete(key) } }
} else {
  globalThis.sessionStorage = working
}

globalThis.window = { location: { search: '' }, scrollTo() {}, addEventListener() {} }
globalThis.document = {
  getElementById: () => null, querySelector: () => null, querySelectorAll: () => [],
  addEventListener() {}, removeEventListener() {},
}
globalThis.CSS = { escape: value => value }
globalThis.requestAnimationFrame = () => {}

// **Does the module graph load at all?** `state.js` reads the token at module scope, so a
// refusal there would be the whole calculator not starting -- which is the premise this
// file would otherwise be assuming rather than testing.
let loaded = true
let loadError = null
let state, setState, leafKey, storeToken, bindCalculator, render, compareImprovement
let storageRefusesToPersist, persistenceHasBeenRefused
try {
  ;({ state, setState, leafKey, storeToken } = await import(new URL('state.js', root)))
  ;({ bindCalculator, render } = await import(new URL('calculator.js', root)))
  ;({ compareImprovement } = await import(new URL('improvement.js', root)))
  ;({ storageRefusesToPersist, persistenceHasBeenRefused } = await import(new URL('snapshot.js', root)))
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
const press = (action, dataset = {}) => handlers.click({
  target: { closest: selector => (selector === '[data-action]' ? { dataset: { action, ...dataset } } : null) },
})
const flush = () => new Promise(resolve => setTimeout(resolve, 0))

// **`render()` against a stand-in for `<main>`, which is all it writes to.** The two hooks
// it calls on every exit are inert here on purpose and not by luck:
// `bindStepSectionNavigation` returns at its first line because `querySelector` answers
// null, and `bindResultsSectionSpy` takes its `!sections.length` branch for the same
// reason. So what comes back is the markup and nothing else.
const main = { className: '', innerHTML: '' }
const draw = () => { render(main); return main.innerHTML }

let threwOnFalsy = null
let stepAfterCalculate = null
const html = {}
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
} else if (input.probe === 'notice') {
  // One render per form step, plus the two screens the notice must stay off.
  for (const step of [-1, 0, 1, 2, 3, 4]) {
    setState({ step })
    html[step] = draw()
  }
} else if (input.probe === 'noticeAfterRefusedWrite') {
  // The quota path. The probe said yes, so nothing is reported until a real write is
  // refused -- and the press is the real call site (`continue`/`calculate` are what call
  // `writeSnapshot`), not a hand call to the module.
  setState({ step: 4 })
  html.before = draw()
  press('calculate')
  await flush()
  await flush()
  stepAfterCalculate = state.step
  // Back into the form the way the results page offers it: *Edit your data* is
  // `data-action="go-step"` with `data-step="4"` (`stepNav` in `view.js`).
  html.atResults = draw()
  press('go-step', { step: '4' })
  html.backInForm = draw()
}

console.log(JSON.stringify({
  loaded, loadError,
  step: state.step, hasResult: state.result !== null,
  error: state.error, errorCode: state.errorCode, token: state.token,
  improvementResult: state.improvementResult !== null, improvementError: state.improvementError,
  storedToken: stored.get('kaiCalculatorToken') || null,
  storedKeys: [...stored.keys()].sort(),
  calls, sentTokens, threwOnFalsy,
  storageRefusesToPersist, persistenceRefused: persistenceHasBeenRefused(), stepAfterCalculate,
  html,
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

# --------------------------------------------------------------------------------------
# v1.115: and now the visitor is told.
#
# Everything above is about a refusal costing nothing it did not have to. This half is
# about the one cost it does carry -- the reload-restore -- being said out loud, which is
# what v1.114's own entry recorded as still open.
# --------------------------------------------------------------------------------------

#: A fragment of the sentence, spelled once. Long enough that nothing else on any screen
#: could match it, short enough to survive a comma being moved.
NOTICE_TEXT = 'not letting this page store data'
FORM_STEPS = ['0', '1', '2', '3', '4']


@pytest.mark.parametrize('storage', REFUSALS)
def test_a_browser_that_refuses_to_persist_is_told_so_on_every_form_step(tmp_path, storage):
    """The notice itself, which is the whole of this revision.

    `writeSnapshot` has dropped a refused write since v1.73 and the calculator has gone on
    working, which is right. What nobody did was *say so*, so a visitor part-way through a
    multi-entry submission who reloaded lost all of it with no way to know their own
    browser was the reason.
    """
    seen = probe(tmp_path, storage=storage, probe='notice')
    assert seen['storageRefusesToPersist'] is True
    for step in FORM_STEPS:
        assert NOTICE_TEXT in seen['html'][step], f'step {step} says nothing'
        assert 'storage-notice' in seen['html'][step]


def test_a_browser_that_allows_storage_is_told_nothing(tmp_path):
    """**The control, and the one that matters most here.**

    A notice rendered unconditionally would satisfy every assertion above and be a
    permanent caution on a calculator that is working perfectly. This is the only thing
    that separates a notice from a decoration, and the mutation `notice-always` is in the
    matrix for exactly that reason.
    """
    seen = probe(tmp_path, storage='working', probe='notice')
    assert seen['storageRefusesToPersist'] is False
    assert seen['persistenceRefused'] is False
    for step in FORM_STEPS:
        assert NOTICE_TEXT not in seen['html'][step], f'step {step} warns about nothing'
        assert 'storage-notice' not in seen['html'][step]


@pytest.mark.parametrize('storage', REFUSALS)
def test_the_notice_stays_off_the_introduction(tmp_path, storage):
    """Step -1 has nothing entered to lose, and is the full-bleed hero `introduction-main`
    restyles. A caution there is a caution about nothing, in the one place on the site
    whose layout is not built to carry a disclaimer."""
    seen = probe(tmp_path, storage=storage, probe='notice')
    assert NOTICE_TEXT not in seen['html']['-1']


def test_the_probe_takes_its_own_sentinel_back_out(tmp_path):
    """The probe is a write, so it leaves a trace unless it cleans up after itself.

    A browser that allows storage must be left exactly as it was found - in particular
    `readSnapshot` and `state.js`'s module-load read must not find a fourth key beside the
    three keys §7.2a defines.
    """
    seen = probe(tmp_path, storage='working', probe='falsyToken')
    assert seen['storedKeys'] == [], 'the probe left a key behind'


def test_a_refused_cleanup_is_not_a_refused_write(tmp_path):
    """**Why `setItem` and `removeItem` have a `try` each rather than sharing one.**

    Here the write lands and only the sentinel's removal throws. Nothing was refused, so
    there is nothing to tell the visitor; one shared `try` would report a working browser
    as a blocked one and put a standing caution in front of somebody in no danger.
    """
    seen = probe(tmp_path, storage='refuse-cleanup', probe='notice')
    assert seen['storageRefusesToPersist'] is False, 'a refused cleanup read as a refused write'
    for step in FORM_STEPS:
        assert NOTICE_TEXT not in seen['html'][step]


def test_a_quota_the_probe_cannot_see_is_still_reported(tmp_path):
    """**The half the module-load probe is blind to, measured rather than assumed.**

    The probe stores one character, so a quota that is merely exhausted tells it yes and
    then refuses the real document. `writeSnapshot`'s catch is the only witness, and this
    walks the real call site: Calculate is one of the two actions that write the snapshot.

    `before` is the proof that the probe really did pass - without it this case would pass
    on a build where the probe reported every browser blocked.
    """
    seen = probe(tmp_path, storage='refuse-answers', probe='noticeAfterRefusedWrite')
    assert seen['storageRefusesToPersist'] is False
    assert NOTICE_TEXT not in seen['html']['before'], 'warned before any write was refused'
    assert seen['persistenceRefused'] is True, 'a refused answers write went unrecorded'
    assert NOTICE_TEXT in seen['html']['backInForm']


def test_a_refused_result_write_is_reported_once_the_visitor_edits_their_data(tmp_path):
    """**That the second catch is reachable at all, which is the question #187 taught.**

    `writeResultSnapshot` runs on the Calculate that moves the visitor to step 5, and the
    notice is suppressed on step 5 - so a flag set there would be unobservable unless
    something takes the visitor back into the form. *Edit your data* does, in one press,
    and this is that press. A guard whose effect cannot be reached is the defect v1.113
    shipped and had to remove, and it is not being added back here.
    """
    seen = probe(tmp_path, storage='refuse-result', probe='noticeAfterRefusedWrite')
    assert seen['storageRefusesToPersist'] is False
    assert seen['stepAfterCalculate'] == 5, 'the calculation did not reach the results page'
    assert NOTICE_TEXT not in seen['html']['atResults'], 'a caution about answers sat over the figures'
    assert seen['persistenceRefused'] is True, 'a refused result write went unrecorded'
    assert NOTICE_TEXT in seen['html']['backInForm'], 'the flag was set and never shown'


@pytest.mark.parametrize('storage', REFUSALS)
def test_the_notice_is_the_calculators_own_sentence_and_not_the_browsers(tmp_path, storage):
    """§9.2 again, one revision later.

    v1.114 stopped a `DOMException`'s `message` reaching the interface through
    `publicError`; this is the same assertion about the screen that now deliberately
    *does* mention storage, so the one place the subject comes up is still a sentence
    written for end users in twenty languages.
    """
    seen = probe(tmp_path, storage=storage, probe='notice')
    assert DENIED_TEXT not in json.dumps(seen)
    assert 'setItem' not in json.dumps(seen['html'])


@pytest.mark.parametrize('storage', REFUSALS)
def test_the_notice_does_not_claim_the_calculation_is_broken(tmp_path, storage):
    """The reassurance is load-bearing, not padding.

    The defect v1.114 closed was a working calculation reported as a failure. A notice
    saying only *your browser is blocking storage* would re-create that impression in
    prose, so the sentence ends by saying the calculation works - and Calculate is still
    offered rather than withheld, `blocked()` being what withholds it.
    """
    seen = probe(tmp_path, storage=storage, probe='notice')
    assert 'works normally' in seen['html']['4']
    assert 'data-action="calculate"' in seen['html']['4']
