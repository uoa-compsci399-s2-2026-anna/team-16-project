"""`web/js/snapshot.js`: what is written, what is refused, and what is dropped.

The visitor's answers are kept in `sessionStorage` so that leaving the
calculator for the Documentation or Statistics page and pressing Back resumes
the form instead of restarting it. Four properties of that mechanism are worth
a test of their own, and none of them is observable from a browser test that
walks the form:

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
const { leafKey, resetCalculator, state } = await import(process.argv[3])
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
  // Clear has to take the snapshot with the token, or the Clear button is a
  // false statement.
  reset() {
    snapshot.writeSnapshot(input.state)
    store.set('kaiCalculatorToken', 'a-token')
    const before = store.has(snapshot.SNAPSHOT_KEY)
    resetCalculator()
    return {
      before,
      snapshotAfter: store.has(snapshot.SNAPSHOT_KEY),
      tokenAfter: store.has('kaiCalculatorToken'),
      step: state.step,
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
        # WP2's, and so not this module's.
        "result": {"totals": []},
        "taxonomy": TAXONOMY,
        "contributed": True,
        "contributeTicked": True,
        "contributeArmedUntil": 1234567890,
        "token": "a-token",
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
        # WP2's three, which this package deliberately does not carry.
        "result",
        "taxonomy",
        "contributed",
        "contributeTicked",
        "contributeArmedUntil",
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
def test_the_results_step_is_restored_as_the_review_step(tmp_path):
    """WP1 stores no result, and a results screen with no result renders nothing.

    The visitor who left from the results page comes back to the review step
    they calculated from, with Calculate one press away. WP2 is what changes
    this, and it will have to change this line.
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
