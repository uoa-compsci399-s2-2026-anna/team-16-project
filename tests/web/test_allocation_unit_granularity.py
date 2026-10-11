"""Step 4's allocation rules compared hundredths of the DISPLAY unit, not of a
kilogram — so a tonnes leaf hid up to five kilograms (contract v1.111).

**This is a residual defect of v1.105, not a regression from it.** That revision
replaced a floating-point epsilon with `Math.round(kg * 100)` and §0.1's own
entry states the rule as *"compare integer hundredths of **a kilogram**"*. The
three predicates do exactly that. What they were handed did not: `allocatedAmount`
sums its lines in kilograms and then converts the sum **back** to the leaf's
display unit before returning it, and `totalNumber` is the typed total in that
same unit. On a kilograms leaf the two spellings coincide, which is why every
figure v1.105 measured — 2.01, 0.07, 6.667, 1234.5, 9999999.99 — passed. On a
**tonnes** leaf the comparison granularity was 0.01 tonne, which is **10 kg**.

Every destination row carries its own unit select (`destinationRows` calls
`unitOptionsHtml(rowUnit, containerPresets())`), so a kilograms row under a
tonnes total is an ordinary thing to enter rather than a contrivance.

**Measured on `origin/main`** (step 3 unit *tonnes*, total `0.01`; step 4 one
destination row, unit *kilograms*):

    row 5.01 kg   ->  step 4, no error, strip calm, "Remaining 0.00 tonnes"
    row 14.99 kg  ->  step 4, no error, strip calm, "Remaining 0.00 tonnes"
    row 9.99 kg   ->  step 4, no error, strip calm, "Remaining 0.00 tonnes"

Four point nine nine kilograms unplaced under a `Complete` badge with Continue
enabled — the v1.99 defect intact, three orders of magnitude up — and the
unallocated remainder is not held back for later: `requestLines` sends the
destination rows, so every metric would have been computed on half the mass.

## The four kilograms cases below are v1.105's CONTROL, not four more defects

`test_an_allocation_that_misses_its_total_is_refused` is parametrised over both
units on purpose, and **the `kilograms` parameters pass on `origin/main`.** They
are here to measure that this change moves only the mixed-unit case and leaves
v1.105's own rule exactly where it was; a reader meeting a green row must not
read it as a defect that was fixed. Every parameter id carrying `CONTROL` says
so, and the `tonnes` rows are the regression half. Measured split of this
module's fifteen cases against `origin/main`: **7 passed, 8 failed**, and the
eight failures are exactly the eight `tonnes` assertions.

## What also changed, deliberately and visibly

The derived figures in the allocation strip now print to
`unitDisplayPrecision(unit)` — `units.js`'s own house rule, five places for
tonnes, whose docstring gives the reason: *"Five places keeps the same 0.01 kg
underneath a tonnes figure that a visitor did not type to begin with."* That
0.01 kg is the resolution the rules compare at, so *Allocated*, *Remaining* and
the shortfall named in the refusal are stated to it. **`Total waste` keeps two
places**, because it is the figure the visitor typed rather than one the form
derived. v1.105 change #3 promised that *"an over-allocation reads `-0.01`"*; in
kilograms it still does, and in tonnes the same 0.01 kg now reads `-0.00001`.
`test_the_strip_states_derived_figures_to_the_unit_s_own_precision` is what
pins both halves of that.

## Why this runs the real module under Node rather than greping it

The claim is about which quantity reaches a comparison, which no amount of
source reading answers. `calculator.js` exports `render()` and `bindCalculator`,
so one Node process can draw the real step-4 markup for a leaf it has set up,
read the strip back, press the real Continue handler and report where the form
went — the same seam `test_step_three_copy_truth.py` uses, for the same reason.

**Two independent writers of one rule.** `leafSummary` draws the strip on a
re-render and `updateLine` rewrites it on a keystroke, and `CLAUDE.md` and the
v1.99 / v1.109 entries record that the pair has drifted three times. This module
covers the re-render writer. `updateLine`'s half needs a live keystroke and is
covered in `test_card_folding_browser.py`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WEB_JS = ROOT / "web" / "js"
TAXONOMY = ROOT / "tests" / "fixtures" / "taxonomy.json"

node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node executes web/js/; the allocation rules are unverified without it",
)

#: `calculator.js` reaches `window`, `document`, `sessionStorage`, `CSS` and
#: `requestAnimationFrame` at module load and inside `render()`. Every stub here
#: is inert: `bindResultsSectionSpy` takes its `!sections.length` return because
#: `main.querySelector` is absent, and `bindEquivalenceOverlay` needs
#: `document.addEventListener` to exist and does nothing with it off the results
#: page. Nothing under test touches any of them.
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
const { bindCalculator, render } = await import(jsDir + 'calculator.js')
const taxonomy = JSON.parse(readFileSync(taxonomyPath, 'utf8'))
const key = leafKey({ foodCategory: 'dairy' })
setState({
  taxonomy, loading: false, step: 3, sector: 'processing', foodCategories: ['dairy'],
  foodItems: {}, foodUnspecified: false, entries: [], token: null, result: null,
  error: null, errorAt: null, fieldErrors: {}, returnTo: null, totalUnit: input.unit,
  leafFigures: { [key]: {
    measureMode: 'mass', totalAmount: input.total, totalUnit: input.unit,
    unitPreset: null, unitCount: '', totalInputKg: '', totalValueNzd: '',
    wastedValueNzd: '',
    current: [{ id: 'one', destination: 'landfill', qtyInput: input.amount, unit: input.rowUnit }],
  } },
})
// The real step-4 markup, drawn by the real renderer into a stub `main` whose
// `innerHTML` is just a string.
const main = { className: '', innerHTML: '' }
render(main)
// The one request this form can make. It stays empty in every case here, which is
// part of the assertion: a refused allocation must not reach the API.
let requests = []
globalThis.fetch = async (url, options) => {
  requests.push(JSON.parse(options.body))
  return { ok: true, json: async () => ({ token: 'token', totals: {}, entries: [{}] }) }
}
const handlers = {}
bindCalculator({ addEventListener: (type, fn) => { handlers[type] = fn } }, () => {})
const control = { dataset: { action: 'continue' } }
handlers.click({ target: { closest: selector => (selector === '[data-action]' ? control : null) } })
console.log(JSON.stringify({ markup: main.innerHTML, step: state.step, error: state.error, requests }))
"""


@pytest.fixture(scope="module")
def harness(tmp_path_factory) -> Path:
    script = tmp_path_factory.mktemp("allocation-granularity") / "harness.mjs"
    script.write_text(HARNESS, encoding="utf-8")
    return script


def _press_continue(harness: Path, *, unit: str, total: str, amount: str, row_unit: str) -> dict:
    """Draw step 4 for one leaf and press Continue, in one Node process."""
    completed = subprocess.run(
        [
            shutil.which("node"),
            str(harness),
            WEB_JS.as_uri() + "/",
            str(TAXONOMY),
            json.dumps({"unit": unit, "total": total, "amount": amount, "rowUnit": row_unit}),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert completed.returncode == 0, (
        f"node could not run the allocation step:\n{completed.stdout}\n{completed.stderr}"
    )
    return json.loads(completed.stdout)


def _strip(markup: str) -> dict:
    """The allocation strip's class list and its three figures.

    Read out of the emitted markup rather than recomputed: `leafSummary` writes
    `data-summary="allocated"` and `data-summary="remaining"` precisely so that
    `updateLine` can find the same two nodes, and *Total waste* is the only
    `<strong>` in the strip with no `data-summary` of its own.
    """
    opening = re.search(r'<div class="allocation-summary ([^"]*)"', markup)
    assert opening, "step 4 drew no allocation strip"
    total = re.search(r"Total waste</span><strong>([^<]*)<", markup)
    allocated = re.search(r'data-summary="allocated">([^<]*)<', markup)
    remaining = re.search(r'data-summary="remaining">([^<]*)<', markup)
    assert total and allocated and remaining, "the strip is missing one of its three figures"
    return {
        "classes": opening.group(1).split(),
        "total": total.group(1),
        "allocated": allocated.group(1),
        "remaining": remaining.group(1),
    }


# -------------------------------------------------------------------------------------
# The gate
# -------------------------------------------------------------------------------------

#: `(unit, total, row amount, row unit, the figure the refusal must name)`.
#:
#: **`kilograms-*` are v1.105's control and pass on `origin/main`;
#: `tonnes-*` are the regression and fail on it.** The pairs are the same
#: physical masses: a 0.01 t total is 10 kg, and 5.01 kg short of it is 4.99 kg
#: either way. Stating them in both units is what makes the comparison unit the
#: subject of the test rather than the arithmetic.
MISSES_ITS_TOTAL = [
    pytest.param("kilograms", "10", "5.01", "kilograms", "4.99", id="kilograms-short-CONTROL"),
    pytest.param("kilograms", "10", "14.99", "kilograms", "4.99", id="kilograms-over-CONTROL"),
    pytest.param("tonnes", "0.01", "5.01", "kilograms", "0.00499", id="tonnes-short-4.99kg"),
    pytest.param("tonnes", "0.01", "14.99", "kilograms", "0.00499", id="tonnes-over-4.99kg"),
    pytest.param("tonnes", "1", "995", "kilograms", "0.00500", id="tonnes-short-5kg-at-1t"),
    pytest.param("tonnes", "0.01", "9.99", "kilograms", "0.00001", id="tonnes-short-one-hundredth-kg"),
    pytest.param("tonnes", "0.01", "10.01", "kilograms", "0.00001", id="tonnes-over-one-hundredth-kg"),
]


@node
@pytest.mark.parametrize("unit,total,amount,row_unit,missing", MISSES_ITS_TOTAL)
def test_an_allocation_that_misses_its_total_is_refused(harness, unit, total, amount, row_unit, missing):
    """Continue holds, the strip is marked, nothing is sent, and the message
    names the mass that is out of place.

    Four separate assertions because three of them are satisfiable without the
    fourth: a rule that refused the allocation while still printing a calm strip
    would pass the first alone, and one that printed the shortfall while letting
    Continue through would pass the last. v1.105's entry made the same split for
    the same reason.
    """
    result = _press_continue(harness, unit=unit, total=total, amount=amount, row_unit=row_unit)
    assert result["step"] == 3, "Continue went through on an allocation that misses its total"
    assert result["requests"] == [], "a refused allocation reached the API"
    assert missing in (result["error"] or ""), (
        f"the refusal does not name the {missing} that is out of place: {result['error']!r}"
    )
    assert "invalid" in _strip(result["markup"])["classes"], (
        "the strip stayed calm under a refused allocation - which is what a shut card shows"
    )


#: The same masses, met exactly. **All four pass on `origin/main`**: they are
#: here to measure that this change refuses nothing it should accept, which is
#: the half a one-directional test cannot see. `1 t` entered as 1 tonne and as
#: 1000 kg are the same row through two different selects.
MEETS_ITS_TOTAL = [
    pytest.param("kilograms", "10", "10", "kilograms", id="kilograms-exact-CONTROL"),
    pytest.param("tonnes", "0.01", "10.00", "kilograms", id="tonnes-total-kilograms-row-CONTROL"),
    pytest.param("tonnes", "1", "1", "tonnes", id="tonnes-total-tonnes-row-CONTROL"),
    pytest.param("tonnes", "1", "1000", "kilograms", id="tonnes-total-kilograms-row-at-1t-CONTROL"),
]


@node
@pytest.mark.parametrize("unit,total,amount,row_unit", MEETS_ITS_TOTAL)
def test_an_allocation_that_meets_its_total_exactly_continues(harness, unit, total, amount, row_unit):
    result = _press_continue(harness, unit=unit, total=total, amount=amount, row_unit=row_unit)
    assert result["error"] is None, f"a complete allocation was refused: {result['error']!r}"
    assert result["step"] == 4, "Continue did not move on from a complete allocation"
    assert "invalid" not in _strip(result["markup"])["classes"]


# -------------------------------------------------------------------------------------
# The strip
# -------------------------------------------------------------------------------------

#: `(unit, total, row amount, row unit, total as printed, allocated, remaining)`.
#:
#: **Equalities, not substrings.** The whole subject is how many decimal places
#: each of the three figures carries, and `"0.005" in "0.00500"` is true - a
#: substring assertion here would pass against the very precision it is meant to
#: pin. The kilograms row is the control: two places throughout, unchanged.
STRIP_FIGURES = [
    pytest.param(
        "kilograms", "10", "5.01", "kilograms",
        "10.00 kilograms", "5.01 kilograms", "4.99 kilograms",
        id="kilograms-two-places-throughout-CONTROL",
    ),
    pytest.param(
        "tonnes", "1", "995", "kilograms",
        "1.00 tonnes", "0.99500 tonnes", "0.00500 tonnes",
        id="tonnes-typed-at-two-derived-at-five",
    ),
    pytest.param(
        "tonnes", "0.01", "10.01", "kilograms",
        "0.01 tonnes", "0.01001 tonnes", "-0.00001 tonnes",
        id="tonnes-over-allocation-reads-negative",
    ),
]


@node
@pytest.mark.parametrize("unit,total,amount,row_unit,total_text,allocated,remaining", STRIP_FIGURES)
def test_the_strip_states_derived_figures_to_the_unit_s_own_precision(
    harness, unit, total, amount, row_unit, total_text, allocated, remaining
):
    """*Total waste* is the figure the visitor typed and keeps two places; the two
    the form derived carry `unitDisplayPrecision(unit)`, which is five for tonnes.

    The negative case is v1.105 change #3 restated for a tonnes leaf: *Remaining*
    is the exact hundredths difference and an over-allocation reads below zero, so
    the three numbers on the line still add up.
    """
    strip = _strip(_press_continue(harness, unit=unit, total=total, amount=amount, row_unit=row_unit)["markup"])
    assert strip["total"] == total_text
    assert strip["allocated"] == allocated
    assert strip["remaining"] == remaining


@node
def test_the_two_figures_the_rules_compare_are_not_the_two_the_strip_prints(harness):
    """The 0.01 kg boundary, stated as a property of the pair rather than of one
    screen: **0.01 kg either side of a 10 kg total is refused while the total
    itself is accepted**, on a leaf whose every figure is labelled in tonnes.

    This is the one assertion that cannot be satisfied by a rounder tolerance,
    a smaller epsilon or a different number of decimal places. It is the whole of
    what v1.105 promised and what a tonnes leaf did not have.
    """
    short = _press_continue(harness, unit="tonnes", total="0.01", amount="9.99", row_unit="kilograms")
    exact = _press_continue(harness, unit="tonnes", total="0.01", amount="10.00", row_unit="kilograms")
    over = _press_continue(harness, unit="tonnes", total="0.01", amount="10.01", row_unit="kilograms")
    assert [short["step"], exact["step"], over["step"]] == [3, 4, 3]
    assert exact["error"] is None
    assert short["error"] and over["error"]
    assert _strip(exact["markup"])["remaining"] == "0.00000 tonnes"
