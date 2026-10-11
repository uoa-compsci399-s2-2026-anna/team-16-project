"""A retired `unit_preset` made a destination row vanish off the wire, and half
the mass went with it (contract v1.112).

## The defect

`unit_preset` has **no `factor_set_id`** (`admin/taxonomy_models.py`): it is a
global table like `food_item`. It carries `active: Boolean`, staff edit it
through sqladmin's generic CRUD, and `get_taxonomy` filters
`.where(UnitPreset.active.is_(True))` (`db/repository.py`). **So one unticked
checkbox in the admin panel removes a preset code from §6.1's response at the
next page load, with no factor-set publish involved.** O-6 says plainly that
this table is expected to be edited: the `0.29 kg/L` behind the ten seeded
presets is a published overseas figure standing in for a New Zealand
measurement nobody has taken.

`rowKgString` honours §6.1 correctly and answers `null` for a row whose own
unit the taxonomy no longer carries. `requestLines` then asked
`Number(line.qty_kg) > 0` — and `Number(null)` is `0`. **That conflation was
the defect:** `null` ("this preset no longer exists") was indistinguishable
from `''` ("the visitor left this row blank") and from a legitimate `0`. The
`null` was produced correctly and then thrown away.

**Measured on `origin/main`** (a 10 kg leaf; `2 × preset:retired_bin` →
landfill, `5 kg` → compost; restored through `pruneAnswers`, then Calculate):

    dropped:   []                 step: 5    error: null
    requests:  1
    entries[0].current: [ {"destination": "compost", "qty_kg": "5.000"} ]

Half the mass gone, no notice of any kind, and a result on screen computed on
it — and because one calculation is one submission (§2.3), that halved figure
lands in the public statistics. Compare Impact does the same thing and upserts
over the same token (§5.3): on `origin/main` its request carried
`current: 5.000 kg` with `alternative: 5.000 kg`, and the Compare button was
enabled.

`step` is in `ANSWER_KEYS` and `restorableStep` keeps a stored 5 when there is
a result to redraw, so §7.2a's own note at `snapshot.js` is what makes this
reachable: *"`restoredPatch` clamps the step back to the review screen, and
Calculate is one press"* — the step-3 gate never re-runs.

## The three layers, and why each is needed

Each was measured rather than assumed, and no one of them covers the others.

1. **`submission.js` stops conflating, and `submissionPayload` refuses.** The
   `null` is no longer thrown away: `unconvertibleRows` is the half that used to
   be discarded, and the one builder both buttons go through throws
   `UnconvertibleRowError` rather than quietly sending a shorter list. This is
   the layer the loss lived at, and it is the only one that holds for a row the
   prune has **not** seen — a taxonomy reloaded mid-session (`UNKNOWN_CODE`)
   does not re-run `pruneAnswers`, because `pendingRestore` is consumed by the
   first load.
2. **`pruneAnswers` empties the row and reports it.** This is the half that
   tells the visitor: the count goes with the code, the destination stays, and
   `DROPPED_UNIT_PRESET` puts "Containers" in the restore notice. Its own cases
   live in `test_snapshot.py`, where the prune's other rules are.
3. **`submitCalculation` re-runs the allocation gate over every chain.**
   Necessary precisely *because* of (2): once the row is emptied it is a
   legitimately blank row, so (1) has nothing to object to — the submission is
   simply 5 kg short of a 10 kg total, which is the v1.99 rule, which only the
   step-3 gate states. A saved chain's Continue is not on the restore path.

The improvement panel is guarded in `improvementValidation` rather than at its
own submit, so Compare Impact is **disabled** rather than failing on press, and
with step 3's existing sentence rather than a new catalogue key.

## Controls in this module

`test_an_intact_container_row_still_reaches_the_wire_whole` and
`test_a_blank_row_and_a_zero_are_not_an_unconvertible_row` **pass on
`origin/main`.** They are not regression tests: the first is the half that
matters more here, because a fix that refused or emptied every `preset:` row
would satisfy every other assertion in this file while destroying the figures
of every visitor who measured in containers. Measured split of this module's
eleven cases against `origin/main`: **4 passed, 7 failed** — the four are the
four controls.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WEB_JS = ROOT / "web" / "js"
TAXONOMY = ROOT / "tests" / "fixtures" / "taxonomy.json"

node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node executes web/js/; the submission rules are unverified without it",
)

#: A code `tests/fixtures/taxonomy.json` does **not** carry, which is the whole
#: of what "retired" means to the front end: §6.1's response is the only place a
#: `kg_per_unit` exists, so a code absent from it cannot be converted at all.
RETIRED = "preset:retired_bin"

#: A code it does carry, at `6.6700` kg each — so four of them are `26.680` kg,
#: which is what the control below reads on the wire. Taken from the fixture
#: rather than hand-written, because §10 makes the fixtures the executable form
#: of the contract.
LIVE = "preset:food_scraps_bin_23l"

#: The reproduction: a 10 kg leaf, two bins of a retired container to landfill
#: and 5 kg to compost. The bins are the mass that disappeared.
RETIRED_ROWS = [
    {"destination": "landfill", "qtyInput": "2", "unit": RETIRED},
    {"destination": "compost", "qtyInput": "5", "unit": "kilograms"},
]

#: The same shape with a container the taxonomy still offers: 4 × 6.67 kg plus
#: 3.32 kg is 30.00 kg exactly, so it satisfies the step-3 gate and must go
#: through untouched.
LIVE_ROWS = [
    {"destination": "landfill", "qtyInput": "4", "unit": LIVE},
    {"destination": "compost", "qtyInput": "3.32", "unit": "kilograms"},
]

HARNESS = r"""
import { readFileSync } from 'node:fs'
const [, , jsDir, taxonomyPath, inputJson] = process.argv
const input = JSON.parse(inputJson)
globalThis.window = { location: { search: '' }, scrollTo() {} }
globalThis.document = {
  getElementById: () => null, querySelector: () => null, querySelectorAll: () => [],
  addEventListener() {}, removeEventListener() {},
}
globalThis.CSS = { escape: value => value }
globalThis.requestAnimationFrame = () => {}
const stored = new Map()
globalThis.sessionStorage = {
  getItem: key => stored.get(key) || null,
  setItem: (key, value) => stored.set(key, value),
  removeItem: key => stored.delete(key),
}
const { state, setState, leafKey } = await import(jsDir + 'state.js')
const { bindCalculator } = await import(jsDir + 'calculator.js')
const { ImprovementScenario, compareImprovement, improvementValidation } = await import(jsDir + 'improvement.js')
const { pruneAnswers } = await import(jsDir + 'snapshot.js')
const submission = await import(jsDir + 'submission.js')
const taxonomy = JSON.parse(readFileSync(taxonomyPath, 'utf8'))
const key = leafKey({ foodCategory: 'dairy' })

const rows = (input.rows || []).map((row, index) => ({ id: `row-${index}`, ...row }))
const chain = {
  sector: 'processing', foodCategories: ['dairy'], foodItems: {}, foodUnspecified: false,
  totalUnit: 'kilograms',
  leafFigures: { [key]: {
    measureMode: 'mass', totalAmount: input.total || '10', totalUnit: 'kilograms',
    unitPreset: null, unitCount: '', totalInputKg: '', totalValueNzd: '', wastedValueNzd: '',
    current: rows,
  } },
}

// The one request the calculator can make. Its contents are the assertion: a row
// that cannot be converted must not simply be missing from `current[]`.
let requests = []
globalThis.fetch = async (url, options) => {
  requests.push(JSON.parse(options.body))
  return { ok: true, json: async () => ({ token: 'accepted', totals: {}, entries: [{}] }) }
}

let dropped = []
let thrown = null
let unconvertible = null
let lines = null
let compareDisabled = null
let compareMatches = 0
let inlineError = null

if (input.probe === 'builder') {
  // The request builder on its own: no prune and no gate, which is the layer the
  // silent loss lived at and the only one that holds for a row the prune never saw.
  setState({ taxonomy, loading: false, step: 4, entries: [], token: null, result: null,
    gwpHorizon: 100, timeFrame: null, periodStart: '', periodEnd: '', ...chain })
  const presets = taxonomy.unit_presets
  const leaf = { ...chain.leafFigures[key], sector: 'processing', foodCategory: 'dairy', totalUnit: 'kilograms' }
  // Guarded so that this probe reports rather than crashes when run against a tree
  // that has no such export -- which is how the `origin/main` split below was measured.
  unconvertible = typeof submission.unconvertibleRows === 'function'
    ? submission.unconvertibleRows(leaf, presets).map(row => ({ destination: row.destination, unit: row.unit }))
    : null
  lines = submission.requestLines(leaf, presets)
  try {
    submission.submissionPayload(state, [{ ...chain }])
  } catch (error) {
    thrown = { name: error.name, message: error.message, code: error.code ?? null, rows: error.rows ?? null }
  }
} else {
  // A clean second chain, so that `saved` means what the review screen actually means:
  // this chain is on the list and another complete chain is in the draft. A different
  // food category, because §6.2 refuses two entries naming the same sector and food.
  const draftKey = leafKey({ foodCategory: 'fruit' })
  const secondChain = {
    sector: 'processing', foodCategories: ['fruit'], foodItems: {}, foodUnspecified: false,
    totalUnit: 'kilograms',
    leafFigures: { [draftKey]: {
      measureMode: 'mass', totalAmount: '2', totalUnit: 'kilograms', unitPreset: null,
      unitCount: '', totalInputKg: '', totalValueNzd: '', wastedValueNzd: '',
      current: [{ id: 'clean', destination: 'compost', qtyInput: '2', unit: 'kilograms' }],
    } },
  }
  const answers = input.saved
    ? { ...state, entries: [structuredClone(chain)], ...structuredClone(secondChain) }
    : { ...state, ...structuredClone(chain) }
  setState({ taxonomy, loading: false, step: input.step ?? 4, entries: [], token: null, result: null,
    error: null, errorAt: null, fieldErrors: {}, returnTo: null,
    gwpHorizon: 100, timeFrame: null, periodStart: '', periodEnd: '',
    improvementOpen: true, improvedAllocations: [{ compost: 100 }] })
  const restored = pruneAnswers(answers, taxonomy)
  dropped = restored.dropped
  setState({ sector: 'processing', ...restored.patch, restoreDropped: dropped })
  const handlers = {}
  bindCalculator({ addEventListener: (type, fn) => { handlers[type] = fn } }, () => {})
  if (input.compare) {
    inlineError = improvementValidation(state)
    const html = ImprovementScenario(state)
    const hits = [...html.matchAll(/<button[^>]*data-action="compare-improvement"[^>]*>/g)]
    // Reported separately from `compareDisabled`, because `.every()` over an empty
    // list is `true`: a selector that stopped matching would otherwise read as "the
    // button is disabled" and pass. The test asserts this is non-zero.
    compareMatches = hits.length
    compareDisabled = hits.length > 0 && hits.every(hit => /\bdisabled\b/.test(hit[0]))
    await compareImprovement(state)
  } else {
    const control = { dataset: { action: 'calculate' } }
    handlers.click({ target: { closest: s => (s === '[data-action]' ? control : null) } })
    await new Promise(resolve => setTimeout(resolve, 0))
  }
}

console.log(JSON.stringify({
  step: state.step, error: state.error, fieldErrors: state.fieldErrors, result: state.result,
  token: state.token, improvementError: state.improvementError, improvementResult: state.improvementResult,
  dropped, requests, thrown, unconvertible, lines, compareDisabled, compareMatches, inlineError,
  current: input.saved ? state.entries[0]?.leafFigures[key]?.current : state.leafFigures[key]?.current,
}))
"""


@pytest.fixture(scope="module")
def harness(tmp_path_factory) -> Path:
    script = tmp_path_factory.mktemp("retired-unit-preset") / "harness.mjs"
    script.write_text(HARNESS, encoding="utf-8")
    return script


def _probe(harness: Path, **payload) -> dict:
    completed = subprocess.run(
        [shutil.which("node"), str(harness), WEB_JS.as_uri() + "/", str(TAXONOMY), json.dumps(payload)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert completed.returncode == 0, (
        f"node could not run the submission probe:\n{completed.stdout}\n{completed.stderr}"
    )
    return json.loads(completed.stdout)


# -------------------------------------------------------------------------------------
# Layer 1 — the request builder, which is where the loss happened
# -------------------------------------------------------------------------------------


@node
def test_the_request_builder_refuses_rather_than_sending_a_shorter_list(harness):
    """No prune and no gate: `submissionPayload` on a chain holding the retired
    row, which is the layer below every screen.

    The three assertions are three different claims. `unconvertibleRows` says the
    `null` is no longer discarded; the throw says the builder refuses rather than
    returning something shorter than what it was given; and `requests == []` says
    nothing left the browser. A fix that only reported the row, or only threw on a
    path nothing reaches, would satisfy one of the three.
    """
    result = _probe(harness, probe="builder", rows=RETIRED_ROWS)
    assert result["unconvertible"] == [{"destination": "landfill", "unit": RETIRED}], (
        f"the unconvertible row is still being thrown away: {result['unconvertible']!r}"
    )
    assert result["thrown"], "submissionPayload built a request from a chain it cannot convert"
    assert result["thrown"]["name"] == "UnconvertibleRowError", result["thrown"]
    assert result["thrown"]["rows"] == [
        {"entry": 0, "destination": "landfill", "unit": RETIRED}
    ], result["thrown"]
    assert result["requests"] == [], "a request was sent for a submission that cannot be converted"


@node
def test_the_refusal_needs_no_catalogue_key(harness):
    """**No new user-facing string, checked rather than intended.**

    §7.7's three traps are why: `tests/web/i18n_keys.py` reads the source and
    cannot see a string it was not written to see, `api.i18n.Catalogue.gettext`
    **raises** where the browser's `t()` falls back — so one key missing from any
    of the twenty catalogues makes every non-English PDF answer 500 — and a new
    CJK character prints as a box until the subsets are re-cut.

    So `UnconvertibleRowError` carries no message at all. `calculator.js`'s
    `publicError` answers a message-less error with its own existing
    `t('The calculation could not be completed.')`, and no `code` means
    `submitCalculation`'s `catch` leaves the visitor on the screen they pressed
    from rather than moving them to the step some §9 code names.
    """
    thrown = _probe(harness, probe="builder", rows=RETIRED_ROWS)["thrown"]
    assert thrown["message"] == "", (
        f"the error carries its own prose, which has to exist in all twenty "
        f"catalogues: {thrown['message']!r}"
    )
    assert thrown["code"] is None, thrown


@node
def test_an_intact_container_row_still_reaches_the_wire_whole(harness):
    """**CONTROL — passes on `origin/main`, and it is the half that matters most.**

    A fix that refused, emptied or dropped every `preset:` row would satisfy every
    other assertion in this file while destroying the figures of every visitor who
    measured in containers. Four 23 L food-scraps bins at `6.6700` kg each is
    `26.680` kg, converted by `units.js` and sent as a string (§7.4's decimal
    rule), beside the 3.32 kg typed as a weight.
    """
    result = _probe(harness, probe="builder", total="30", rows=LIVE_ROWS)
    assert result["unconvertible"] in ([], None), result["unconvertible"]
    assert result["thrown"] is None, result["thrown"]
    assert result["lines"] == [
        {"destination": "landfill", "qty_kg": "26.680"},
        {"destination": "compost", "qty_kg": "3.320"},
    ], result["lines"]


@node
def test_a_blank_row_and_a_zero_are_not_an_unconvertible_row(harness):
    """**CONTROL — passes on `origin/main`.** The three cases `requestLines` used
    to collapse into one `Number()`, told apart.

    A blank row is not a row holding zero and is skipped; a legitimate `0` is
    skipped too, because §6.2 has no use for a zero line; and neither of them is
    an unconvertible row, so neither may refuse the submission. Without this the
    fix above could have been "throw whenever a row is not positive", which would
    refuse every form a visitor has not filled completely.
    """
    result = _probe(harness, probe="builder", rows=[
        {"destination": "landfill", "qtyInput": "", "unit": "kilograms"},
        {"destination": "animal_feed", "qtyInput": "0", "unit": "kilograms"},
        {"destination": "compost", "qtyInput": "10", "unit": "kilograms"},
    ])
    assert result["unconvertible"] in ([], None), result["unconvertible"]
    assert result["thrown"] is None, result["thrown"]
    assert result["lines"] == [{"destination": "compost", "qty_kg": "10.000"}], result["lines"]


# -------------------------------------------------------------------------------------
# Layers 2 and 3 — the restore reports it, and Calculate refuses
# -------------------------------------------------------------------------------------


@node
@pytest.mark.parametrize("saved", [False, True], ids=["draft-chain", "saved-chain"])
def test_the_retired_row_is_reported_and_its_count_is_cleared(harness, saved):
    """The restore says what it took. The count goes with the code — a container
    count cannot be reinterpreted as a weight, and the `kg_per_unit` that told
    them apart is exactly what has gone — and the *destination* stays, so step 4
    draws a row with a blank box the visitor can answer.

    The second row is the control within the case: a prune that emptied the wrong
    row, or every row, would pass the first three assertions.
    """
    result = _probe(harness, probe="restore", saved=saved, rows=RETIRED_ROWS)
    assert {"kind": "unit_preset", "code": "retired_bin"} in result["dropped"], (
        f"the visitor is told nothing about the container that was retired: {result['dropped']}"
    )
    assert result["current"][0]["qtyInput"] == "", result["current"]
    assert result["current"][0]["unit"] == "kilograms", result["current"]
    assert result["current"][0]["destination"] == "landfill", result["current"]
    assert result["current"][1]["qtyInput"] == "5", result["current"]


@node
@pytest.mark.parametrize("saved", [False, True], ids=["draft-chain", "saved-chain"])
def test_a_restored_review_cannot_calculate_on_the_reduced_mass(harness, saved):
    """**The measurement at the top of this file, inverted.** Nothing is sent, no
    result is put on screen, and the refusal names the mass that is out of place.

    Where it is said differs by where the chain lives, and that is the point of
    the parametrisation rather than decoration. The draft has form screens of its
    own, so the visitor is taken to the allocation step. A saved chain has no
    screen of its own — it is a card on the review step — so the message goes into
    `state.fieldErrors` under §9's `entries[N]` path, which is what `entryProblem`
    reads to print it on that card. A fix that only handled the draft would pass
    the `draft-chain` parameter and leave the restore path it was written for
    unfixed, because a restore brings back a **list**.
    """
    result = _probe(harness, probe="restore", saved=saved, rows=RETIRED_ROWS)
    assert result["requests"] == [], (
        f"the submission went out on the reduced mass: {result['requests']}"
    )
    assert result["result"] is None, "a result was put on screen for a mass nobody entered"
    assert "5.00" in (result["error"] or ""), (
        f"the refusal does not name the 5 kg that is out of place: {result['error']!r}"
    )
    if saved:
        assert result["step"] == 4, "the visitor was moved off the review screen"
        assert result["fieldErrors"].get("entries[0]"), (
            f"nothing marks the saved entry's own card: {result['fieldErrors']}"
        )
    else:
        assert result["step"] == 3, "the visitor was not taken to the step that owns the fault"


@node
def test_a_complete_submission_is_still_sent_whole(harness):
    """**CONTROL.** The gate re-run before every Calculate must refuse nothing it
    did not refuse at Continue — §7.6.1: a client-side guard may refuse earlier
    and more kindly than the API, and never something the API would take.

    One saved chain measured in a live container, plus a complete draft chain, so
    the walk over `[...state.entries, draftEntry()]` is exercised on both halves
    and both reach the wire.
    """
    result = _probe(harness, probe="restore", saved=True, total="30", rows=LIVE_ROWS)
    assert result["error"] is None, f"a complete submission was refused: {result['error']!r}"
    assert result["step"] == 5
    assert len(result["requests"]) == 1, result["requests"]
    assert [entry["current"] for entry in result["requests"][0]["entries"]] == [
        [{"destination": "landfill", "qty_kg": "26.680"}, {"destination": "compost", "qty_kg": "3.320"}],
        [{"destination": "compost", "qty_kg": "2.000"}],
    ], result["requests"][0]["entries"]


# -------------------------------------------------------------------------------------
# The improvement panel, which redistributes the same mass
# -------------------------------------------------------------------------------------


@node
def test_the_comparison_is_refused_rather_than_computed_on_the_reduced_mass(harness):
    """`leafAllocatableKg` is `sumQtyKg(requestLines(...))`, so the panel's
    sliders, its *Total mass* line and the whole alternative scenario were on the
    reduced mass too — and Compare Impact upserts over the same token (§5.3), so
    pressing it replaced the visitor's own submission with the halved one.

    **Reachable**: `restorableStep` keeps a stored 5 when there is a result to
    redraw, so Explore Improvements is one press from a pruned form.

    Refused through `improvementValidation`, which means the Compare button is
    rendered `disabled` rather than failing when pressed, and with step 3's
    existing sentence rather than a new catalogue key. `compareMatches` is
    asserted non-zero because `.every()` over an empty list is `true`: a selector
    that stopped matching would otherwise read as "disabled" and pass.
    """
    result = _probe(harness, probe="restore", saved=False, compare=True, step=5, rows=RETIRED_ROWS)
    assert result["requests"] == [], (
        f"the comparison persisted the reduced mass: {result['requests']}"
    )
    assert result["improvementResult"] is None
    assert "5.00" in (result["inlineError"] or ""), (
        f"the panel does not say what is out of place: {result['inlineError']!r}"
    )
    assert result["compareMatches"] >= 1, "the Compare Impact button was not found at all"
    assert result["compareDisabled"] is True, "Compare Impact is live over a reduced mass"


@node
def test_the_comparison_still_runs_on_an_intact_form(harness):
    """**CONTROL — passes on `origin/main`.** The new rule is one-directional and
    carries `MASS_TOLERANCE_KG`, the tolerance this module already derived for
    this comparison, so an ordinary panel is untouched: the button is live and the
    request goes out with both scenarios at 30 kg.
    """
    result = _probe(harness, probe="restore", saved=False, compare=True, step=5, total="30", rows=LIVE_ROWS)
    assert result["inlineError"] == "", f"an intact panel is refused: {result['inlineError']!r}"
    assert result["compareMatches"] >= 1
    assert result["compareDisabled"] is False
    assert len(result["requests"]) == 1, result["requests"]
    entry = result["requests"][0]["entries"][0]
    assert entry["current"] == [
        {"destination": "landfill", "qty_kg": "26.680"},
        {"destination": "compost", "qty_kg": "3.320"},
    ], entry
    assert entry["alternative"] == [{"destination": "compost", "qty_kg": "30.000"}], entry
