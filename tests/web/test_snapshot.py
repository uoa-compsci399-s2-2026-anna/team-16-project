"""`web/js/snapshot.js`: what is written, what is refused, and what is dropped.

The visitor's answers -- and, since v1.75, the result they already saw -- are
kept in `sessionStorage` so that leaving the calculator for the Documentation or
Statistics page and pressing Back resumes the calculation instead of restarting
it. Two keys, `kaiCalculatorAnswers` and `kaiCalculatorResult`, under one shared
version number. Properties of that mechanism worth a test of their own, none of
them observable from a browser test that walks the form:

* **A decimal survives the round trip as a string.** §1.2 puts decimals on the
  wire as strings because JavaScript's ``Number`` is a double, and a quantity
  that came back from storage as a float would be this contract's oldest rule
  broken by a cache. Asserted on the type, on the trailing zeros, and on the
  bytes actually stored.
* **Only the answers are written.** The module keeps a whitelist rather than a
  forbidden list, and this is what says the whitelist is the one the work
  package specified -- a restored open dialog or a restored error about a
  request that finished long ago are both wrong, and both would arrive by a key
  nobody remembered to exclude.
* **A snapshot from another schema version is discarded whole**, silently.
* **A revalidation against a freshly fetched taxonomy drops what the taxonomy
  no longer has, and reports every drop.** Silently dropping a category or a
  destination the visitor chose is the failure that check exists to prevent, so
  the report is the assertion.
* **The result document carries the result, the taxonomy that produced it and
  one contribute flag of six** -- and a result document that would not render is
  refused rather than half-restored, because "Results unavailable" over answers
  that are perfectly intact is a worse page than no restore at all.
* **The two uses of the taxonomy are told apart by one function.** The form is
  offered from the freshly fetched one (section 6.1) and a restored result is
  rendered from the stored one, because a calculation named its rows out of that
  vocabulary. `taxonomyForResult` is where that decision lives, so it is
  asserted on rather than grepped for.
* **A Continue does not erase the stored result.** That is what the second key
  buys, and it is the reason there are two.

Run under Node for the reason ``test_leaf_rule.py`` gives at length: a test
that greps the source asserts that a name was typed, not that a value came
back. Node is the runner only and does not enter the stack
(``docs/architecture.md`` §3) -- there is still no build step and no
``package.json``.

The browser half is ``test_session_restore_browser.py``, which measures what is
on the screen after a real Back press. Neither file replaces the other: this
one can put a hand-written snapshot in storage and that one cannot, and that
one can prove a visitor sees their answers and this one cannot.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web" / "js"

node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is required to execute web/js/snapshot.js",
)

#: One harness, several probes, so each test below asks exactly one question and
#: fails for exactly one reason.
#:
#: **The `sessionStorage` stub is the point of the harness.** It is the only way
#: to put a snapshot written by another schema version into storage, and the only
#: way to make every call throw -- which is what a private window or blocked site
#: data does, and which must leave the calculator working.
HARNESS = """
globalThis.window = { location: { search: '' } }

let failing = false
const store = new Map()
globalThis.sessionStorage = {
  getItem(key) {
    if (failing) throw new Error('site data is blocked')
    return store.has(key) ? store.get(key) : null
  },
  setItem(key, value) {
    if (failing) throw new Error('site data is blocked')
    store.set(key, String(value))
  },
  removeItem(key) {
    if (failing) throw new Error('site data is blocked')
    store.delete(key)
  },
}

import { readFileSync, writeFileSync } from 'node:fs'
const snapshot = await import(process.argv[2])
const { leafKey, resetCalculator, state, taxonomyForResult } = await import(process.argv[3])
// A sibling of `state.js`, imported off its own URL so that `probe()` keeps the
// signature every test above passes. `submission.js` is where the PDF request body
// is built, and it has to convert a container count with the RESULT's `kg_per_unit`.
const submission = await import(new URL('./submission.js', process.argv[3]).href)
const input = JSON.parse(readFileSync(process.argv[4], 'utf8'))

const probes = {
  // Write a state, read it back, and hand back BOTH the parsed answers and the
  // exact bytes stored - the bytes are what say a decimal is still a string.
  roundTrip() {
    snapshot.writeSnapshot(input.state)
    return { raw: store.get(snapshot.SNAPSHOT_KEY), answers: snapshot.readSnapshot() }
  },
  // The keys actually written, and the whole stored document as text so a
  // forbidden value can be searched for wherever it might have hidden.
  written() {
    snapshot.writeSnapshot(input.state)
    const raw = store.get(snapshot.SNAPSHOT_KEY)
    return { keys: Object.keys(JSON.parse(raw).answers), raw, expected: snapshot.ANSWER_KEYS }
  },
  // A snapshot this deployment did not write.
  foreign() {
    store.set(snapshot.SNAPSHOT_KEY, JSON.stringify(input.stored))
    return { answers: snapshot.readSnapshot() }
  },
  // Whatever `input.raw` is, byte for byte.
  rawStored() {
    store.set(snapshot.SNAPSHOT_KEY, input.raw)
    return { answers: snapshot.readSnapshot() }
  },
  // Every call throws. Nothing here may.
  blocked() {
    failing = true
    snapshot.writeSnapshot(input.state)
    const read = snapshot.readSnapshot()
    snapshot.clearSnapshot()
    failing = false
    return { read, threw: false, stored: store.has(snapshot.SNAPSHOT_KEY) }
  },
  // The patch a restore turns into.
  patch() {
    return { patch: snapshot.restoredPatch(input.answers) }
  },
  // The revalidation.
  prune() {
    return snapshot.pruneAnswers(input.answers, input.taxonomy)
  },
  // `pruneAnswers` writes the NUL-joined leaf key out by hand rather than
  // importing `state.js`, which imports it. This is what pins the two together.
  leafKeys() {
    return { keys: input.leaves.map(leaf => leafKey(leaf)) }
  },
  // Clear has to take BOTH snapshots with the token, or the Clear button is a
  // false statement.
  reset() {
    snapshot.writeSnapshot(input.state)
    snapshot.writeResultSnapshot(input.state)
    store.set('kaiCalculatorToken', 'a-token')
    const before = store.has(snapshot.SNAPSHOT_KEY)
    const resultBefore = store.has(snapshot.RESULT_KEY)
    resetCalculator()
    return {
      before,
      resultBefore,
      snapshotAfter: store.has(snapshot.SNAPSHOT_KEY),
      resultAfter: store.has(snapshot.RESULT_KEY),
      tokenAfter: store.has('kaiCalculatorToken'),
      step: state.step,
      resultTaxonomyAfter: state.resultTaxonomy,
    }
  },

  // ---------------------------------------------------------------- WP2's own

  // The result document, written and read back, with the exact bytes so that a
  // metric total can be checked for still being a string.
  resultRoundTrip() {
    snapshot.writeResultSnapshot(input.state)
    return { raw: store.get(snapshot.RESULT_KEY), calculation: snapshot.readResultSnapshot() }
  },
  // The keys actually written to the result document, and the whole of it as text.
  resultWritten() {
    snapshot.writeResultSnapshot(input.state)
    const raw = store.get(snapshot.RESULT_KEY)
    return { keys: Object.keys(JSON.parse(raw).calculation), raw, expected: snapshot.RESULT_KEYS }
  },
  // A result document this deployment did not write.
  resultForeign() {
    store.set(snapshot.RESULT_KEY, JSON.stringify(input.stored))
    return { calculation: snapshot.readResultSnapshot() }
  },
  // Whatever `input.raw` is, byte for byte.
  resultRawStored() {
    store.set(snapshot.RESULT_KEY, input.raw)
    return { calculation: snapshot.readResultSnapshot() }
  },
  // Every call throws. Nothing here may.
  resultBlocked() {
    failing = true
    snapshot.writeResultSnapshot(input.state)
    const read = snapshot.readResultSnapshot()
    failing = false
    return { read, stored: store.has(snapshot.RESULT_KEY) }
  },
  // The step the answers restore to, given a result beside them or not.
  patchWithResult() {
    return { patch: snapshot.restoredPatch(input.answers, input.result || null) }
  },
  // Two keys, and the cheap one is rewritten at every Continue. This is what says
  // the expensive one is not erased by that.
  continueAfterCalculate() {
    snapshot.writeResultSnapshot(input.state)
    snapshot.writeSnapshot({ ...input.state, step: 3 })
    return {
      calculation: snapshot.readResultSnapshot(),
      answers: snapshot.readSnapshot(),
      keys: [...store.keys()].sort(),
    }
  },
  // The PDF request body, built from `state.result`'s own entries.
  exportPayload() {
    return { payload: submission.exportPayload(input.state, 'en') }
  },
  // `taxonomyForResult` is the one place the two uses of the taxonomy are told
  // apart, so it is asserted on rather than grepped for.
  taxonomyChoice() {
    return {
      restored: taxonomyForResult({ taxonomy: { mark: 'fresh' }, resultTaxonomy: { mark: 'stored' } }).mark,
      live: taxonomyForResult({ taxonomy: { mark: 'fresh' }, resultTaxonomy: null }).mark,
    }
  },
}

writeFileSync(process.argv[5], JSON.stringify(probes[input.probe]()), 'utf8')
"""


def probe(tmp_path: Path, name: str, **payload) -> dict:
    harness = tmp_path / "harness.mjs"
    harness.write_text(HARNESS, encoding="utf-8")
    source = tmp_path / "input.json"
    source.write_text(json.dumps({"probe": name, **payload}), encoding="utf-8")
    out = tmp_path / "out.json"
    completed = subprocess.run(
        [
            shutil.which("node"),
            str(harness),
            (WEB / "snapshot.js").as_uri(),
            (WEB / "state.js").as_uri(),
            str(source),
            str(out),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"
    return json.loads(out.read_text(encoding="utf-8"))


def _leaf(**figures) -> dict:
    base = {
        "totalAmount": "",
        "totalUnit": "kilograms",
        "measureMode": "mass",
        "unitPreset": None,
        "unitCount": "",
        "totalInputKg": "",
        "totalValueNzd": "",
        "wastedValueNzd": "",
        "current": [],
    }
    base.update(figures)
    return base


def _key(category: str | None = None, item: str | None = None) -> str:
    return f"{category or ''}\x00{item or ''}"


#: A taxonomy that covers everything `_state()` names, so a prune against it is a
#: no-op and every drop below is caused by the row the test removed.
TAXONOMY = {
    "sectors": [{"code": "processing"}, {"code": "wholesale_retail"}],
    "food_categories": [{"code": "fruit"}, {"code": "dairy"}],
    "food_items": [
        {"code": "apples", "food_category": "fruit"},
        {"code": "cheese", "food_category": "dairy"},
    ],
    "destinations": [
        {"code": "prevention", "is_prevention": True},
        {"code": "landfill"},
        {"code": "compost"},
    ],
    "unit_presets": [{"code": "wheelie_bin_120l"}],
}


#: A §6.2 response as `submitCalculation` leaves it on `state.result` -- the response
#: plus the `entry_results` pairing `entryResultsFrom` adds (§7.2). Every figure in it
#: is a **string**, which is the whole reason it is written out in full here rather
#: than stubbed: `1234.560` and `1200.500` both lose a character to a single `Number()`
#: anywhere in the write or the read, and `240.00` loses two.
RESULT = {
    "token": "a-token",
    "factor_set": {"code": "fs-mock-1", "version_label": "mock-1", "is_mock": True},
    "factor_source": "mock",
    "gwp_horizon": 100,
    "totals": {
        "total_kg": "1240.500",
        "current": {
            "total_kg": "1240.500",
            "metrics": {
                "co2e": {
                    "total": "1234.560",
                    "unit": "kg CO2e",
                    "display_precision": 2,
                    "by_destination": [
                        {"destination": "landfill", "qty_kg": "1200.500", "value": "1200.100"}
                    ],
                }
            },
        },
        "money": {"wasted_value_nzd": "240.00"},
    },
    "entry_results": [
        {
            "entry": {
                "sector": "processing",
                "foodCategory": "fruit",
                "foodItem": "apples",
                "totalAmount": "1200.500",
                "totalUnit": "kilograms",
                "current": [
                    {"id": "row-1", "destination": "landfill", "qtyInput": "1200.500", "unit": "kilograms"}
                ],
            },
            "response": {
                "sector": "processing",
                "food_category": "fruit",
                "food_item": "apples",
                "item_basis": "category",
                "current": {
                    "total_kg": "1200.500",
                    "metrics": {"co2e": {"total": "1200.100", "unit": "kg CO2e", "display_precision": 2, "by_destination": []}},
                },
                "factor_set": {"code": "fs-mock-1", "version_label": "mock-1", "is_mock": True},
            },
        }
    ],
}


def _state(**overrides) -> dict:
    """A state carrying one filled draft, one committed entry, and every
    transient key the module must refuse to write."""
    base = {
        "sector": "processing",
        "gwpHorizon": 100,
        "foodCategories": ["fruit"],
        "foodUnspecified": False,
        "foodItems": {"fruit": ["apples"]},
        "foodStage": "categories",
        "totalUnit": "kilograms",
        "leafFigures": {
            _key("fruit", "apples"): _leaf(
                totalAmount="1200.500",
                wastedValueNzd="0.10",
                current=[{"id": "row-1", "destination": "landfill", "qtyInput": "1200.500", "unit": "kilograms"}],
            )
        },
        "entries": [
            {
                "sector": "wholesale_retail",
                "foodCategories": ["dairy"],
                "foodUnspecified": False,
                "foodItems": {"dairy": []},
                "totalUnit": "kilograms",
                "leafFigures": {_key("dairy"): _leaf(totalAmount="40.000")},
            }
        ],
        "timeFrame": "week",
        "periodStart": "2026-09-01T08:10",
        "periodEnd": "2026-09-01T16:20",
        "periodFields": {
            "startDate": "2026-09-01",
            "startTime": "08:10",
            "endDate": "2026-09-01",
            "endTime": "16:20",
        },
        "step": 4,
        # Everything below is transient and must not be written.
        "loading": True,
        "error": "Waste amount must be greater than zero.",
        "errorAt": {"leaf": _key("fruit", "apples"), "field": "amount"},
        "errorCode": "VALIDATION_ERROR",
        "fieldErrors": {"entries[0]": "Nope"},
        "rateLimitedUntil": 1234567890,
        "pdfExporting": True,
        "pdfError": "The document could not be produced.",
        "periodPicker": {"field": "startDate", "cursor": "2026-09-01", "openerId": "x"},
        "periodClock": {"field": "startTime", "stage": "hours", "mode": "dial"},
        # The result document's own keys (v1.75). They must not reach the ANSWERS
        # document -- a 32 KB result rewritten at every Continue is what the second
        # key exists to avoid -- so every test below that looks at the answers
        # asserts their absence, and the WP2 tests further down assert their
        # presence in the other one.
        "result": RESULT,
        "resultTaxonomy": TAXONOMY,
        "contributed": True,
        # The five contribute flags that are NOT stored, one of each kind: a request
        # in flight, a tick on its own, a grace window, a one-shot animation and an
        # error about a request that is over.
        "contributing": True,
        "contributeTicked": True,
        "contributeArmedUntil": 1234567890,
        "contributeCelebrating": True,
        "contributeError": "The calculator service could not be reached.",
        # Never stored anywhere: `taxonomy` is the FORM's, fetched fresh on every
        # load (§6.1), and the token has rules and a key of its own.
        "taxonomy": TAXONOMY,
        "token": "a-token",
        # The results page's own furniture, and the improvement panel's.
        "resultBreakdownTab": "destination",
        "resultsNavOpen": True,
        "improvementOpen": True,
        "improvedAllocations": [{"landfill": "50"}],
        "improvementResult": {"totals": {}},
    }
    base.update(overrides)
    return base


# ------------------------------------------------------- the decimal round trip


@node
def test_a_decimal_survives_the_round_trip_as_the_string_it_was_typed_as(tmp_path):
    """§1.2's oldest rule, asserted against the bytes in storage.

    `1200.500` is chosen for its trailing zeros: `Number('1200.500')` is
    `1200.5`, so a single `Number()` anywhere in the write, the read or a
    "normalising" traversal between them shows up here as three characters
    missing. `0.10` is the same trap one decimal place along, on a money field.

    Three assertions, because each catches a different way of losing it: the
    stored text (a JSON *number* rather than a *string*), the parsed type (a
    revive step that coerced it), and the exact characters (a round trip that
    kept a string but reformatted it).
    """
    result = probe(tmp_path, "roundTrip", state=_state())
    raw, answers = result["raw"], result["answers"]

    # In the stored document it is quoted. `1200.500` unquoted is a JSON number
    # and would come back as 1200.5.
    assert '"totalAmount":"1200.500"' in raw.replace(" ", ""), raw
    assert '"1200.500"' in raw and "1200.5," not in raw.replace(" ", ""), raw

    figures = answers["leafFigures"][_key("fruit", "apples")]
    assert isinstance(figures["totalAmount"], str), type(figures["totalAmount"])
    assert figures["totalAmount"] == "1200.500"
    assert isinstance(figures["wastedValueNzd"], str)
    assert figures["wastedValueNzd"] == "0.10"
    # And at the depth the destination rows live at, which is one level further
    # in than anything above.
    assert figures["current"][0]["qtyInput"] == "1200.500"
    assert isinstance(figures["current"][0]["qtyInput"], str)
    # A committed entry's own figures are a second copy of the same shape.
    assert answers["entries"][0]["leafFigures"][_key("dairy")]["totalAmount"] == "40.000"


# --------------------------------------------------------- what is written


@node
def test_exactly_the_answer_keys_are_written_and_nothing_transient(tmp_path):
    """The whitelist, asserted as a set rather than as a subset.

    A subset check would pass on a snapshot that wrote `fieldErrors` as well.
    The forbidden names are then searched for in the stored *text*, because a
    transient value could also arrive nested inside an answer -- `errorAt`
    carries a leaf key, and a naive deep copy of `state` would bring it.
    """
    result = probe(tmp_path, "written", state=_state())
    assert sorted(result["keys"]) == sorted(result["expected"]), result["keys"]
    assert sorted(result["expected"]) == sorted(
        [
            "sector",
            "gwpHorizon",
            "foodCategories",
            "foodUnspecified",
            "foodItems",
            "foodStage",
            "totalUnit",
            "leafFigures",
            "entries",
            "timeFrame",
            "periodStart",
            "periodEnd",
            "periodFields",
            "step",
        ]
    ), result["expected"]
    forbidden = [
        "loading",
        "error",
        "errorAt",
        "errorCode",
        "fieldErrors",
        "rateLimitedUntil",
        "pdfExporting",
        "pdfError",
        "periodPicker",
        "periodClock",
        # The result document's own keys, which live under `kaiCalculatorResult` and
        # must not also be here: `writeSnapshot` runs at every `continue`, and a
        # 32 KB result carried through each of those is exactly what the second key
        # exists to avoid.
        "result",
        "resultTaxonomy",
        "contributed",
        # `taxonomy` is the FORM's and is never stored at all (§6.1).
        "taxonomy",
        # The five contribute flags that are stored nowhere.
        "contributing",
        "contributeTicked",
        "contributeArmedUntil",
        "contributeCelebrating",
        "contributeError",
        # The results page's furniture and the improvement panel's.
        "resultBreakdownTab",
        "resultsNavOpen",
        "improvementOpen",
        "improvedAllocations",
        "improvementResult",
        # And the token, which has rules of its own and keeps its own key.
        "token",
    ]
    for key in forbidden:
        assert f'"{key}"' not in result["raw"], (
            f"{key!r} reached the snapshot. A restored open dialog, a restored "
            "error about a request that is over, or a second copy of the token "
            "are each a defect this whitelist exists to make impossible."
        )
    # The one value that would prove a transient leaked in nested rather than at
    # the top level: `pdfError`'s own sentence.
    assert "could not be produced" not in result["raw"]


# ------------------------------------------------------------ the version gate


@node
@pytest.mark.parametrize("version", [0, 2, 99, "1", None])
def test_a_snapshot_from_another_schema_version_is_discarded_whole(tmp_path, version):
    """Not half-read, and not repaired.

    A deployment ships new state keys and renames old ones. Reading a shape
    nobody checked field by field is the defect the version number exists to
    prevent, so a mismatch answers "there is no snapshot" -- including for the
    string `"1"`, because `===` is what the module compares with and a loose
    check here would accept a document written by something that is not this
    module at all.
    """
    stored = {"version": version, "answers": {"sector": "processing", "step": 3}}
    assert probe(tmp_path, "foreign", stored=stored)["answers"] is None


@node
def test_the_current_version_is_read(tmp_path):
    """The other half of the gate. Without this, a version check that rejected
    *everything* would pass every test above and restore nothing, ever."""
    stored = {"version": 1, "answers": {"sector": "processing", "step": 3}}
    answers = probe(tmp_path, "foreign", stored=stored)["answers"]
    assert answers == {"sector": "processing", "step": 3}


@node
@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not json at all",
        "[]",
        '"a string"',
        "null",
        '{"version":1}',
        '{"version":1,"answers":null}',
        '{"version":1,"answers":[]}',
        '{"version":1,"answers":{}}',
        '{"version":1,"answers":{"somethingElse":1}}',
    ],
)
def test_a_snapshot_that_is_not_this_module_s_shape_reads_as_nothing(tmp_path, raw):
    """Storage is a string, and a string can be anything.

    Each of these is a real shape somebody could find there -- an empty value, a
    hand edit, another tool's key collision, a document whose answers hold
    nothing this deployment recognises. None of them may reach `setState`, where
    one wrong type is a `TypeError` inside `render()` and a blank page.
    """
    assert probe(tmp_path, "rawStored", raw=raw)["answers"] is None


# -------------------------------------------------- storage that refuses to work


@node
def test_the_form_works_when_storage_throws_on_every_call(tmp_path):
    """A private window, or site data blocked.

    `sessionStorage` then throws on access rather than returning null, and this
    runs at module load and at every checkpoint. **This feature must never be a
    precondition for the form working**, so the harness makes all three calls
    throw and asserts the module swallows each one: the probe reaching its
    `writeFileSync` at all is the assertion, and `read is None` is what says the
    failure was handled rather than turned into a half-restore.
    """
    result = probe(tmp_path, "blocked", state=_state())
    assert result["read"] is None
    assert result["stored"] is False


# ----------------------------------------------------------- the restored patch


@node
def test_the_results_step_is_restored_as_the_review_step_when_there_is_no_result(tmp_path):
    """A results screen with no result renders "Results unavailable", which is a
    dead end with the visitor's answers intact in the form behind it.

    So the clamp stands for every document that has no usable result beside it --
    a snapshot written before v1.75, a result key evicted on its own, or one that
    failed `readResultSnapshot`'s shape check. The visitor comes back to the review
    step they calculated from, with Calculate one press away.
    """
    patch = probe(tmp_path, "patch", answers={"step": 5})["patch"]
    assert patch["step"] == 4


@node
@pytest.mark.parametrize("step, expected", [(-1, -1), (0, 0), (3, 3), (4, 4), (5, 4)])
def test_every_legal_step_is_restored_as_itself_except_the_results(tmp_path, step, expected):
    patch = probe(tmp_path, "patch", answers={"step": step})["patch"]
    assert patch["step"] == expected


@node
@pytest.mark.parametrize("step", [-2, 6, 99, "4", 3.5, None])
def test_a_step_outside_the_wizard_is_not_restored_at_all(tmp_path, step):
    """`screens[state.step]()` is an index into a five-element array, so a step
    of 6 is `undefined()` -- a `TypeError` inside `render()`, which is a blank
    page. Left out of the patch, `state.js`'s own -1 stands and the visitor gets
    the introduction, which is what they would have had without this feature."""
    assert "step" not in probe(tmp_path, "patch", answers={"step": step})["patch"]


@node
@pytest.mark.parametrize(
    "answers",
    [
        {"foodCategories": "fruit"},
        {"foodItems": ["apples"]},
        {"leafFigures": []},
        {"entries": {}},
        {"periodFields": "2026-09-01"},
        {"totalUnit": "stones"},
        {"gwpHorizon": 50},
        {"foodStage": "somewhere"},
        {"timeFrame": 7},
    ],
)
def test_a_value_of_the_wrong_shape_is_left_out_rather_than_restored(tmp_path, answers):
    """The version number rules out another deployment's schema; it does not
    rule out a hand edit. One wrong type here is a `TypeError` inside
    `render()`, so the shape of every value is checked and anything that fails
    simply does not join the patch."""
    patch = probe(tmp_path, "patch", answers=answers)["patch"]
    for key in answers:
        assert key not in patch, patch


@node
def test_the_period_fields_are_restored_with_all_four_boxes_present(tmp_path):
    """`periodFields` is read as `periodFields.startDate` and three siblings
    with no guard, so a stored object missing one box would put `undefined`
    into an input's `value` -- the string "undefined", in a date box."""
    patch = probe(tmp_path, "patch", answers={"periodFields": {"startDate": "2026-09-01"}})["patch"]
    assert patch["periodFields"] == {
        "startDate": "2026-09-01",
        "startTime": "",
        "endDate": "",
        "endTime": "",
    }


# ----------------------------------------------------------- the revalidation


@node
def test_a_taxonomy_that_still_has_everything_drops_nothing(tmp_path):
    """The control. Without it, a prune that dropped every code would pass every
    assertion below -- each of those names a code it expects to be gone."""
    result = probe(tmp_path, "prune", answers=_state(), taxonomy=TAXONOMY)
    assert result["dropped"] == []
    assert result["patch"]["foodCategories"] == ["fruit"]
    assert result["patch"]["foodItems"] == {"fruit": ["apples"]}
    assert result["patch"]["entries"][0]["sector"] == "wholesale_retail"
    figures = result["patch"]["leafFigures"][_key("fruit", "apples")]
    assert [line["destination"] for line in figures["current"]] == ["landfill"]
    # And the figures come through the prune with their decimals intact: this is
    # the same round trip as the storage one, through a different function.
    assert figures["totalAmount"] == "1200.500"
    assert figures["current"][0]["qtyInput"] == "1200.500"


@node
def test_a_retired_food_category_is_dropped_with_its_figures_and_reported(tmp_path):
    """The category the visitor ticked is gone from the published set.

    Two things have to happen: the code leaves `foodCategories`, and the figures
    it carried leave `leafFigures`. `state.js` deliberately *parks* the figures
    of an unticked category so that re-ticking restores them -- but a code the
    vocabulary no longer has can never be re-ticked, so a parked record for it
    is unreachable forever.

    And it is **reported**: silently dropping a category the visitor chose is
    the failure this whole step exists to prevent.
    """
    taxonomy = {**TAXONOMY, "food_categories": [{"code": "dairy"}]}
    result = probe(tmp_path, "prune", answers=_state(), taxonomy=taxonomy)
    assert result["patch"]["foodCategories"] == []
    assert _key("fruit", "apples") not in result["patch"]["leafFigures"]
    assert {"kind": "food_category", "code": "fruit"} in result["dropped"]


@node
def test_a_retired_food_is_dropped_and_its_category_is_kept(tmp_path):
    """The narrower case, and the one a category-level check would miss: the
    category is still priced and the specific food is not. The leaf falls back to
    the category, which is exactly what `entryLeaves` gives a chosen category
    with no food ticked."""
    taxonomy = {**TAXONOMY, "food_items": [{"code": "cheese", "food_category": "dairy"}]}
    result = probe(tmp_path, "prune", answers=_state(), taxonomy=taxonomy)
    assert result["patch"]["foodCategories"] == ["fruit"]
    assert result["patch"]["foodItems"] == {"fruit": []}
    assert {"kind": "food_item", "code": "apples"} in result["dropped"]
    # The figures typed under that food go with it: nothing can reach them again.
    assert _key("fruit", "apples") not in result["patch"]["leafFigures"]


@node
def test_a_food_the_taxonomy_has_re_parented_is_dropped_too(tmp_path):
    """Step 2.5 groups foods under their category, so a food whose parent moved
    would be restored as a tick under a heading it no longer belongs to -- and
    §6.2 prices it against the category the *server* has for it, not the one the
    form drew it under. Membership alone is not the question."""
    taxonomy = {
        **TAXONOMY,
        "food_items": [{"code": "apples", "food_category": "dairy"}],
    }
    result = probe(tmp_path, "prune", answers=_state(), taxonomy=taxonomy)
    assert result["patch"]["foodItems"] == {"fruit": []}
    assert {"kind": "food_item", "code": "apples"} in result["dropped"]


@node
def test_a_retired_destination_row_is_dropped_and_reported(tmp_path):
    """§6.1's own words for the hazard: a form offering codes the current set
    does not price. A destination row is where that lands after a restore, and
    the row goes rather than the whole allocation."""
    taxonomy = {**TAXONOMY, "destinations": [{"code": "compost"}]}
    result = probe(tmp_path, "prune", answers=_state(), taxonomy=taxonomy)
    figures = result["patch"]["leafFigures"][_key("fruit", "apples")]
    assert figures["current"] == []
    assert {"kind": "destination", "code": "landfill"} in result["dropped"]


@node
def test_a_prevention_destination_is_not_an_offered_one(tmp_path):
    """The form's own list is the destinations MINUS the prevention ones
    (`entryDestinations`), and §6.2 refuses a flagged destination in a *current*
    scenario outright. So a restored row naming one could never be submitted,
    whatever the taxonomy says about it existing."""
    answers = _state()
    answers["leafFigures"][_key("fruit", "apples")]["current"] = [
        {"id": "row-1", "destination": "prevention", "qtyInput": "5.00", "unit": "kilograms"}
    ]
    result = probe(tmp_path, "prune", answers=answers, taxonomy=TAXONOMY)
    assert result["patch"]["leafFigures"][_key("fruit", "apples")]["current"] == []
    assert {"kind": "destination", "code": "prevention"} in result["dropped"]


@node
def test_a_retired_container_falls_back_to_a_weight_and_is_reported(tmp_path):
    """A count of containers means nothing without the `kg_per_unit` that
    converted it, and that number lives only in the taxonomy. So the preset is
    cleared and the leaf goes back to measuring a mass, rather than keeping a
    count nothing can turn into kilograms."""
    answers = _state()
    answers["leafFigures"][_key("fruit", "apples")].update(
        measureMode="container", unitPreset="crate_30l_full", unitCount="12"
    )
    result = probe(tmp_path, "prune", answers=answers, taxonomy=TAXONOMY)
    figures = result["patch"]["leafFigures"][_key("fruit", "apples")]
    assert figures["unitPreset"] is None
    assert figures["measureMode"] == "mass"
    assert {"kind": "unit_preset", "code": "crate_30l_full"} in result["dropped"]


@node
def test_a_draft_whose_sector_is_gone_is_sent_back_to_step_zero(tmp_path):
    """The sector is the one answer an entry cannot be without
    (`submission_entry.sector_id` is NOT NULL), and steps 3 and 4 are laid out
    per leaf and read perfectly well without one -- so nothing downstream would
    have told the visitor. Step 0 is the screen that asks the question."""
    taxonomy = {**TAXONOMY, "sectors": [{"code": "wholesale_retail"}]}
    result = probe(tmp_path, "prune", answers=_state(), taxonomy=taxonomy)
    assert result["patch"]["sector"] is None
    assert result["patch"]["step"] == 0
    assert {"kind": "sector", "code": "processing"} in result["dropped"]


@node
def test_a_committed_entry_whose_sector_is_gone_is_dropped_whole_and_reported(tmp_path):
    """It can only ever be refused, so keeping it would put a card on the review
    step whose only possible future is a `VALIDATION_ERROR` at the end of the
    flow. Reported like every other drop."""
    taxonomy = {**TAXONOMY, "sectors": [{"code": "processing"}]}
    result = probe(tmp_path, "prune", answers=_state(), taxonomy=taxonomy)
    assert result["patch"]["entries"] == []
    assert {"kind": "sector", "code": "wholesale_retail"} in result["dropped"]
    # The DRAFT's own sector is still priced, so the patch does not mention it and
    # `state.js`'s restored value stands. `step` is absent for the same reason: a
    # visitor whose own sector survived has no question to be sent back to.
    assert "sector" not in result["patch"], result["patch"]
    assert "step" not in result["patch"], result["patch"]


@node
def test_a_committed_entry_is_pruned_by_the_same_rule_as_the_draft(tmp_path):
    """A committed entry is the draft's own shape, and the two must not diverge
    here: a retired category dropped from the draft and left in a saved entry is
    the same `UNKNOWN_CODE` arriving from the other half of the submission."""
    taxonomy = {**TAXONOMY, "food_categories": [{"code": "fruit"}]}
    result = probe(tmp_path, "prune", answers=_state(), taxonomy=taxonomy)
    entry = result["patch"]["entries"][0]
    assert entry["foodCategories"] == []
    assert _key("dairy") not in entry["leafFigures"]
    assert {"kind": "food_category", "code": "dairy"} in result["dropped"]


@node
def test_the_category_less_record_is_kept_because_it_can_always_come_back(tmp_path):
    """The one record the prune must NOT remove. Unticking every category brings
    the category-less leaf back (`entryLeaves`), so its figures are reachable and
    are parked exactly as `state.js` parks an unticked category's. The prune
    removes what is unreachable forever, not what is merely not in use."""
    answers = _state()
    answers["leafFigures"][_key()] = _leaf(totalAmount="7.000")
    taxonomy = {**TAXONOMY, "food_categories": [{"code": "dairy"}]}
    result = probe(tmp_path, "prune", answers=answers, taxonomy=taxonomy)
    assert result["patch"]["leafFigures"][_key()]["totalAmount"] == "7.000"


@node
def test_a_category_the_visitor_merely_unticked_keeps_its_figures(tmp_path):
    """**The prune removes what the TAXONOMY lost, never what the visitor unticked.**

    §7.2 parks the figures of an unticked category so that re-ticking hands them
    back, and `draftEntry()` prunes at the request boundary so nothing parked is
    ever sent. An earlier version of this function kept only the records belonging
    to the chain's *live* leaves, and the consequence was a real loss: tick two
    categories, fill both, untick one, press Continue, and the unticked one's money
    figures were gone at the next page load -- silently, with the category still
    perfectly well priced, and with nothing in the notice because nothing had been
    retired.

    So the record for a category that is still in the taxonomy survives whether or
    not it is ticked, and the page load is no longer the thing that decides.
    """
    answers = _state(foodCategories=["fruit"])
    # `dairy` was ticked, filled and unticked. The figures are parked.
    answers["leafFigures"][_key("dairy")] = _leaf(totalAmount="400.000", wastedValueNzd="55.50")
    result = probe(tmp_path, "prune", answers=answers, taxonomy=TAXONOMY)
    parked = result["patch"]["leafFigures"].get(_key("dairy"))
    assert parked is not None, (
        "the figures of a category the visitor unticked were destroyed by the page load, "
        "although the taxonomy still prices it and re-ticking would hand them back in "
        "memory"
    )
    assert parked["totalAmount"] == "400.000"
    assert parked["wastedValueNzd"] == "55.50"
    assert result["dropped"] == [], result["dropped"]


@node
def test_each_dropped_code_is_reported_once_however_many_times_it_appears(tmp_path):
    """Two entries sending waste to the same retired destination is one thing to
    tell the visitor, not two. A notice that read "Destination "landfill",
    Destination "landfill"" would look like a bug in the message rather than a
    statement about their answers."""
    answers = _state()
    answers["entries"].append(
        {
            "sector": "processing",
            "foodCategories": ["fruit"],
            "foodUnspecified": False,
            "foodItems": {"fruit": []},
            "totalUnit": "kilograms",
            "leafFigures": {
                _key("fruit"): _leaf(
                    current=[{"id": "r", "destination": "landfill", "qtyInput": "1.00", "unit": "kilograms"}]
                )
            },
        }
    )
    taxonomy = {**TAXONOMY, "destinations": [{"code": "compost"}]}
    result = probe(tmp_path, "prune", answers=answers, taxonomy=taxonomy)
    assert result["dropped"].count({"kind": "destination", "code": "landfill"}) == 1
    assert len(result["dropped"]) == 1, result["dropped"]


@node
def test_an_empty_taxonomy_drops_everything_rather_than_keeping_it(tmp_path):
    """The failure direction that matters. A prune written to be forgiving --
    "no rows means I cannot tell, so keep it" -- restores a whole form of codes
    the current set cannot price, which is precisely §6.1's hazard. Absence is
    evidence here."""
    result = probe(tmp_path, "prune", answers=_state(), taxonomy={})
    assert result["patch"]["foodCategories"] == []
    assert result["patch"]["entries"] == []
    assert result["patch"]["sector"] is None
    assert result["patch"]["leafFigures"] == {}
    # **The food is not reported beside its own category, and that is deliberate.**
    # A retired category takes its foods with it; naming both would tell the visitor
    # about a tick inside a box that is itself gone. The category is the loss.
    kinds = sorted({entry["kind"] for entry in result["dropped"]})
    assert kinds == ["food_category", "sector"], result["dropped"]


@node
@pytest.mark.parametrize(
    "answers",
    [
        {"foodCategories": "fruit"},
        {"foodItems": {"fruit": "apples"}},
        {"foodItems": "fruit"},
        {"leafFigures": {"fruit\x00apples": "40.000"}},
        {"leafFigures": {"fruit\x00apples": {"current": "landfill"}}},
        {"entries": [{"sector": "processing", "foodItems": {"dairy": 7}}]},
        {"entries": "processing"},
    ],
)
def test_a_hand_edited_snapshot_does_not_turn_into_a_broken_calculator(tmp_path, answers):
    """The failure mode this guards is worse than a lost restore.

    `pruneAnswers` runs inside `loadTaxonomy`'s own `try`, so a `.filter` on a
    string there is caught as a **taxonomy** failure and the visitor is shown
    "Calculator unavailable" -- a working calculator reporting itself broken
    because of something in their own browser, with a Try again button that can
    never clear it. So every nested read goes through `asList`/`asMap` and a wrong
    type reads as an empty one.

    The version gate cannot cover this: these are documents at the current
    version with a wrong value inside.
    """
    full = _state()
    full.update(answers)
    result = probe(tmp_path, "prune", answers=full, taxonomy=TAXONOMY)
    assert isinstance(result["patch"], dict), result
    assert isinstance(result["dropped"], list), result


# ------------------------------------------------- the leaf key is not re-derived


@node
def test_the_leaf_key_the_prune_uses_is_the_one_state_js_defines(tmp_path):
    """`pruneAnswers` writes the NUL-joined key out by hand, because `state.js`
    imports `snapshot.js` and importing back would close a cycle. One line of
    duplication is the trade, and this is what stops it drifting: a `leafKey`
    that changed shape would leave the prune deleting every figure record on the
    next publish, silently, because nothing it computed would match a key.
    """
    leaves = [
        {"foodCategory": "fruit", "foodItem": "apples"},
        {"foodCategory": "fruit", "foodItem": None},
        {"foodCategory": None, "foodItem": None},
    ]
    keys = probe(tmp_path, "leafKeys", leaves=leaves)["keys"]
    assert keys == [_key("fruit", "apples"), _key("fruit"), _key()], keys
    # And the prune reads each of them back: it splits the key on the same NUL to ask
    # whether the taxonomy still has that category and that food. All three survive a
    # taxonomy that has everything, whichever of them is currently ticked -- the prune
    # does not decide what is in use.
    answers = _state(foodCategories=["fruit"], foodItems={"fruit": ["apples"]}, leafFigures={})
    for key in keys:
        answers["leafFigures"][key] = _leaf(totalAmount="1.000")
    result = probe(tmp_path, "prune", answers=answers, taxonomy=TAXONOMY)
    assert sorted(result["patch"]["leafFigures"]) == sorted(keys), result["patch"]["leafFigures"]
    # And a key naming a retired food is the one that goes, which is what says the split
    # is being read rather than ignored.
    taxonomy = {**TAXONOMY, "food_items": [{"code": "cheese", "food_category": "dairy"}]}
    result = probe(tmp_path, "prune", answers=answers, taxonomy=taxonomy)
    assert _key("fruit", "apples") not in result["patch"]["leafFigures"]
    assert _key("fruit") in result["patch"]["leafFigures"]


# -------------------------------------------- the code reaches the notice as text


@node
def test_a_code_from_storage_is_carried_as_data_and_not_as_markup(tmp_path):
    """The notice prints the `code` the visitor's own answer carried, and that
    answer came out of *their* `sessionStorage` -- so it can hold whatever a hand
    edit put there.

    This module's half of the guarantee is that the code travels as data: it is
    reported verbatim in `dropped`, with no markup assembled here. `restoreNotice`
    in `calculator.js` is what escapes it, and it escapes the finished sentence, so
    one escape covers the interpolated code. Asserted here rather than in a browser
    because the shape of `dropped` is this module's contract.
    """
    hostile = '<img src=x onerror="alert(1)">'
    result = probe(tmp_path, "prune", answers=_state(sector=hostile), taxonomy=TAXONOMY)
    assert {"kind": "sector", "code": hostile} in result["dropped"], result["dropped"]
    assert result["patch"]["sector"] is None


# ----------------------------------------------------------------- clearing it


@node
def test_clearing_the_calculator_removes_the_snapshot_with_the_token(tmp_path):
    """Both doors ask "Clear all calculator data and return to the
    introduction?" -- the header's Clear button and the results page's *Start
    over*. A snapshot that outlived either would put every one of those answers
    back on the next page load, which makes both a false statement.
    """
    result = probe(tmp_path, "reset", state=_state())
    assert result["before"] is True, "the snapshot was never written; the test proves nothing"
    assert result["snapshotAfter"] is False
    assert result["tokenAfter"] is False
    assert result["step"] == -1


@node
def test_clearing_the_calculator_removes_the_result_snapshot_too(tmp_path):
    """The second key, and the one that would be forgotten.

    "Clear all calculator data" is one call to `clearSnapshot`, and a key added to
    this module and not removed there is exactly how that question becomes a false
    statement -- with the more revealing half left behind, because the result
    document holds the figures rather than the answers. `resultTaxonomy` is cleared
    off `state` in the same patch, so nothing is left pointing at a vocabulary for a
    calculation that no longer exists.
    """
    result = probe(tmp_path, "reset", state=_state())
    assert result["resultBefore"] is True, "the result snapshot was never written; the test proves nothing"
    assert result["resultAfter"] is False, (
        "Clear left the result in storage, so the next page load would put the figures "
        "back on a calculator the visitor had just emptied"
    )
    assert result["resultTaxonomyAfter"] is None


# ------------------------------------------------ the result, and its taxonomy


@node
def test_the_result_document_carries_exactly_the_result_keys(tmp_path):
    """Asserted as a set, and then every excluded name searched for in the text.

    The five contribute flags are the interesting half. `contributed` is the
    visitor's durable choice and has to ride along, or a visitor who ticked,
    submitted and came back is invited to contribute again -- `contributeBlock`
    derives the whole done state from that one flag. The other five would each be a
    statement about something the page load ended:

    * `contributing` -- a request in flight, which cannot be resumed and whose fate
      is unknowable from here.
    * `contributeArmedUntil` -- the grace window, whose `setTimeout` lives in
      `results.js` module scope and died with the page.
    * `contributeTicked` -- a control position rather than a decision.
    * `contributeCelebrating` -- a one-shot animation.
    * `contributeError` -- an error about a request that is over.
    """
    result = probe(tmp_path, "resultWritten", state=_state())
    assert sorted(result["keys"]) == sorted(result["expected"]), result["keys"]
    assert sorted(result["expected"]) == sorted(["result", "resultTaxonomy", "contributed"]), result["expected"]
    forbidden = [
        "contributing",
        "contributeTicked",
        "contributeArmedUntil",
        "contributeCelebrating",
        "contributeError",
        # The form's own taxonomy, which is fetched fresh on every load and stored
        # nowhere. `resultTaxonomy` is a different key and a different promise.
        "taxonomy",
        "improvementOpen",
        "improvedAllocations",
        "improvementResult",
        "resultBreakdownTab",
        "resultsNavOpen",
        "loading",
        "periodPicker",
    ]
    for key in forbidden:
        assert '"%s":' % key not in result["raw"], (
            f"{key!r} reached the result snapshot. A restored spinner, a restored "
            "countdown over a request nothing will make, or a restored comparison "
            "computed under a factor set the allocations no longer match are each a "
            "defect this whitelist exists to make impossible."
        )
    # The fixture's `contributeError` sentence: the one value that would prove a
    # transient leaked in nested rather than at the top level.
    assert "could not be reached" not in result["raw"], result["raw"]
    # **`token` is NOT on that list, and its presence is a property of the response
    # rather than of this whitelist.** §6.2 echoes the session token in its own body
    # and `result` is stored as the response came, so a second copy of it is in this
    # document. It is the same value, in the same `sessionStorage`, for the same
    # lifetime, removed by the same `clearSnapshot`/`resetCalculator` pair as
    # `kaiCalculatorToken` itself -- no new information and no second identifier. The
    # answers document is the one that must not carry it, and
    # `test_exactly_the_answer_keys_are_written_and_nothing_transient` asserts that.
    assert '"token":"a-token"' in result["raw"].replace(" ", "")


@node
def test_a_metric_total_survives_the_result_round_trip_as_a_string(tmp_path):
    """Section 1.2's oldest rule, on the other document.

    `1234.560` and `1200.500` each lose a character to one `Number()` anywhere in
    the write or the read, and `240.00` loses two. Asserted on the stored bytes, on
    the parsed types and on the exact characters -- at the top level, inside
    `by_destination`, inside `entry_results[].entry` and inside
    `entry_results[].response`, because a "normalising" traversal would reach some
    depths and not others.
    """
    result = probe(tmp_path, "resultRoundTrip", state=_state())
    raw, calculation = result["raw"], result["calculation"]
    assert '"total":"1234.560"' in raw.replace(" ", ""), raw
    assert '"wasted_value_nzd":"240.00"' in raw.replace(" ", ""), raw
    assert "1234.56," not in raw.replace(" ", ""), raw

    metric = calculation["result"]["totals"]["current"]["metrics"]["co2e"]
    assert isinstance(metric["total"], str) and metric["total"] == "1234.560", metric
    assert metric["by_destination"][0]["qty_kg"] == "1200.500"
    assert calculation["result"]["totals"]["money"]["wasted_value_nzd"] == "240.00"
    paired = calculation["result"]["entry_results"][0]
    assert paired["entry"]["current"][0]["qtyInput"] == "1200.500"
    assert paired["response"]["current"]["metrics"]["co2e"]["total"] == "1200.100"


@node
def test_the_stored_taxonomy_is_the_whole_response_and_keeps_its_own_numbers(tmp_path):
    """Stored whole rather than reduced to the rows the result names.

    A reduction would be a transformation, and what this key promises is the
    taxonomy that produced the result rather than a summary of it -- 9.3 KB measured
    against the running stack, against about 5 MB of quota. Any `kg_per_unit` or
    `display_precision` in it is a decimal and is held to the same rule as the
    figures above.
    """
    presets = [{"code": "wheelie_bin_120l", "kg_per_unit": "12.500"}]
    calculation = probe(
        tmp_path,
        "resultRoundTrip",
        state=_state(resultTaxonomy={**TAXONOMY, "unit_presets": presets}),
    )["calculation"]
    stored = calculation["resultTaxonomy"]
    assert sorted(stored) == sorted(TAXONOMY), stored
    assert stored["destinations"] == TAXONOMY["destinations"]
    assert stored["unit_presets"][0]["kg_per_unit"] == "12.500", stored["unit_presets"]


@node
@pytest.mark.parametrize("version", [0, 2, 99, "1", None])
def test_a_result_from_another_schema_version_is_discarded_whole(tmp_path, version):
    """One version number for two keys, and it gates both.

    A result document written by a deployment whose `result` shape differed is not
    half-read: it is refused, `restoredPatch` clamps the step to the review screen,
    and the visitor gets the form they can still calculate from.
    """
    stored = {
        "version": version,
        "calculation": {"result": RESULT, "resultTaxonomy": TAXONOMY, "contributed": False},
    }
    assert probe(tmp_path, "resultForeign", stored=stored)["calculation"] is None


@node
def test_the_current_version_of_the_result_is_read(tmp_path):
    """The other half of the gate. Without it, a check that refused *everything*
    would satisfy every assertion above and restore no result, ever."""
    stored = {
        "version": 1,
        "calculation": {"result": RESULT, "resultTaxonomy": TAXONOMY, "contributed": True},
    }
    calculation = probe(tmp_path, "resultForeign", stored=stored)["calculation"]
    assert calculation is not None
    assert calculation["contributed"] is True
    assert calculation["resultTaxonomy"] == TAXONOMY


#: Documents somebody could find in storage after a hand edit or a partial
#: eviction, each of which would produce a worse page than no restore at all.
REFUSED_RESULTS = [
    "",
    "not json at all",
    "[]",
    "null",
    '{"version":1}',
    '{"version":1,"calculation":null}',
    '{"version":1,"calculation":{}}',
    # A result that is not a result.
    '{"version":1,"calculation":{"result":"a string","resultTaxonomy":{}}}',
    # `entry_results` is what `renderResults` renders from, and an empty one is the
    # "Results unavailable" dead end.
    '{"version":1,"calculation":{"result":{"entry_results":[],"totals":{},"factor_set":{}},"resultTaxonomy":{}}}',
    '{"version":1,"calculation":{"result":{"entry_results":{},"totals":{},"factor_set":{}},"resultTaxonomy":{}}}',
    '{"version":1,"calculation":{"result":{"entry_results":["x"],"totals":{},"factor_set":{}},"resultTaxonomy":{}}}',
    # `totals` is where every headline figure comes from.
    '{"version":1,"calculation":{"result":{"entry_results":[{}],"factor_set":{}},"resultTaxonomy":{}}}',
    # `factor_set.is_mock` is what raises the mandatory placeholder banner.
    '{"version":1,"calculation":{"result":{"entry_results":[{}],"totals":{}},"resultTaxonomy":{}}}',
    # And the taxonomy, which `findByCode(taxonomy.destinations, ...)` reads straight
    # off: `null.destinations` is a TypeError inside render(), which is a blank page.
    '{"version":1,"calculation":{"result":{"entry_results":[{}],"totals":{},"factor_set":{}}}}',
    '{"version":1,"calculation":{"result":{"entry_results":[{}],"totals":{},"factor_set":{}},"resultTaxonomy":"fruit"}}',
]

#: The minimal document that IS accepted, and the control for the list above.
ACCEPTED_RESULT = (
    '{"version":1,"calculation":{"result":{"entry_results":[{}],"totals":{},'
    '"factor_set":{}},"resultTaxonomy":{}}}'
)


@node
@pytest.mark.parametrize("raw", REFUSED_RESULTS)
def test_a_result_document_that_would_not_render_is_refused(tmp_path, raw):
    """Storage is a string, and a string can be anything.

    Each of these would produce a worse page than no restore at all -- "Results
    unavailable" over answers that are perfectly intact, or a `TypeError` inside
    `render()`. Refusing here is what makes `restoredPatch` clamp the step back to
    the review screen, where the answers are and Calculate is one press away.
    """
    assert probe(tmp_path, "resultRawStored", raw=raw)["calculation"] is None


@node
def test_a_minimal_result_document_is_accepted(tmp_path):
    """The control for the list above: without it, a check that refused every
    document would pass all fifteen of those and restore nothing, ever."""
    assert probe(tmp_path, "resultRawStored", raw=ACCEPTED_RESULT)["calculation"] is not None


@node
@pytest.mark.parametrize(
    "stored, expected",
    [(True, True), (False, False), (None, False), ("yes", False), (1, False)],
)
def test_contributed_is_restored_only_on_an_explicit_true(tmp_path, stored, expected):
    """The durable half of the consent control, and the direction of the default
    matters: a visitor who never touched the box has answered no, and a document
    saying anything other than `true` must not come back as a yes."""
    document = {
        "version": 1,
        "calculation": {"result": RESULT, "resultTaxonomy": TAXONOMY, "contributed": stored},
    }
    calculation = probe(tmp_path, "resultForeign", stored=document)["calculation"]
    assert calculation["contributed"] is expected


@node
def test_a_stored_results_step_stands_when_there_is_a_result_to_render(tmp_path):
    """The whole of WP2's change to the restored patch.

    Back from the results page has to land on the results page. The clamp is kept
    for the case where there is nothing to draw, and both directions are asserted
    here because a clamp that never fired and a clamp that always fired would each
    pass only one of them.
    """
    calculation = {"result": RESULT, "resultTaxonomy": TAXONOMY, "contributed": False}
    assert probe(tmp_path, "patchWithResult", answers={"step": 5}, result=calculation)["patch"]["step"] == 5
    assert probe(tmp_path, "patchWithResult", answers={"step": 5}, result=None)["patch"]["step"] == 4
    # A step the visitor actually left from is never *raised* to the results screen
    # because a result happens to be stored beside it: walking back from the results
    # page to step 3 and pressing Continue writes 3, and 3 is where they return.
    assert probe(tmp_path, "patchWithResult", answers={"step": 3}, result=calculation)["patch"]["step"] == 3


@node
def test_a_continue_after_a_calculation_does_not_erase_the_result(tmp_path):
    """Two keys, and this is the property they buy.

    `writeSnapshot` runs at every `continue` and `writeResultSnapshot` once per
    calculation. One document holding both would mean either carrying 32 KB through
    every Continue press or erasing the result on the next one -- so the visitor who
    calculates, presses *Edit your data*, changes an amount, presses Continue and
    then leaves would come back to a form with no result behind it.
    """
    result = probe(tmp_path, "continueAfterCalculate", state=_state())
    assert result["keys"] == ["kaiCalculatorAnswers", "kaiCalculatorResult"], result["keys"]
    assert result["answers"]["step"] == 3, result["answers"]["step"]
    assert result["calculation"] is not None, "the Continue erased the stored result"
    assert result["calculation"]["result"]["totals"]["total_kg"] == "1240.500"


@node
def test_the_result_snapshot_is_not_a_precondition_for_anything(tmp_path):
    """A private window, or site data blocked.

    This is the larger of the two documents, so it is the one a quota refuses first.
    The probe reaching its `writeFileSync` at all is the assertion, and
    `read is None` is what says the failure was handled rather than turned into a
    half-restore.
    """
    result = probe(tmp_path, "resultBlocked", state=_state())
    assert result["read"] is None
    assert result["stored"] is False


@node
def test_the_pdf_payload_converts_a_container_with_the_result_s_own_kg_per_unit(tmp_path):
    """`POST /export/pdf` recomputes every figure server-side, so the front end's job
    is to send the same *masses* the screen showed -- and a container's mass is a count
    times a `kg_per_unit` that lives only in the taxonomy.

    A publish that changed that number between the calculation and the download would
    otherwise have the document state a mass the visitor never saw: eight crates at
    12.5 kg is the 100.000 kg on screen, and the same eight crates at 20 kg is
    160.000 kg in a PDF nothing else in the flow would disagree with. During a live
    page load the two taxonomies are the same object, which is exactly why this needs a
    test rather than a walk -- the divergence only exists after a restore.
    """
    result = {
        **RESULT,
        "entry_results": [
            {
                "entry": {
                    "sector": "processing",
                    "foodCategory": "fruit",
                    "foodItem": None,
                    "measureMode": "count",
                    "unitPreset": "wheelie_bin_120l",
                    "unitCount": "8",
                    "totalAmount": "",
                    "totalUnit": "kilograms",
                    # One destination row stated in containers rather than in mass, which
                    # is the shape `rowKgString` converts with the presets.
                    "current": [
                        {
                            "id": "row-1",
                            "destination": "landfill",
                            "qtyInput": "8",
                            "unit": "preset:wheelie_bin_120l",
                        }
                    ],
                },
                "response": RESULT["entry_results"][0]["response"],
            }
        ],
    }
    preset = lambda kg: [{"code": "wheelie_bin_120l", "kg_per_unit": kg}]
    payload = probe(
        tmp_path,
        "exportPayload",
        state=_state(
            result=result,
            # The form's own taxonomy, re-fetched after a publish that changed the
            # preset. `exportPayload` must not read this one.
            taxonomy={**TAXONOMY, "unit_presets": preset("20.000")},
            resultTaxonomy={**TAXONOMY, "unit_presets": preset("12.500")},
        ),
    )["payload"]
    line = payload["entries"][0]["current"][0]
    assert line["qty_kg"] == "100.000", (
        "the PDF was asked for a mass the results page never showed: the count was "
        f"converted with the freshly fetched kg_per_unit rather than the result's own -- {line}"
    )


@node
def test_the_two_uses_of_the_taxonomy_are_told_apart_by_one_function(tmp_path):
    """`taxonomyForResult` (`state.js`) is where the distinction lives.

    Two uses, pointing in opposite directions: the form is offered from
    `state.taxonomy`, fetched fresh on every load because a stale one is "a form
    offering codes the current set does not price" (section 6.1); a restored result
    is rendered from `state.resultTaxonomy`, because a calculation named its rows out
    of *that* vocabulary and `submission.factor_set_id` exists so it stays
    reproducible.

    The fallback is load-bearing rather than defensive dressing: `results.js` does
    `findByCode(taxonomy.destinations, ...)` on the return, and `null.destinations`
    is a `TypeError` inside `render()`.
    """
    result = probe(tmp_path, "taxonomyChoice")
    assert result["restored"] == "stored", "a restored result was renamed out of the current taxonomy"
    assert result["live"] == "fresh", "the fallback is gone, so a state with no stored taxonomy renders nothing"
