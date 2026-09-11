"""The results export actually carries results (contract §7.3a, §7.6.2).

**What this file exists to stop happening again.** `downloadResults` produced
`food-waste-impact-results.txt` containing the total mass, each entry, its
destinations and quantities, the factor set version and the placeholder
warning — and **not one output figure**. No greenhouse gas, no methane, no
water, no cost. A results export with no results is the file somebody attaches
to an email, and nothing in the repository asserted otherwise, because nothing
in the repository ran a line of `web/js/`.

**So this is the first test that executes the front end.** It runs the real
module under Node rather than reading its source with a regular expression: a
test that greps `results.js` for the string `Impact summary` asserts that a
heading was typed, not that a figure reaches the file, and this project has
already lost five defects to exactly that substitution. The assertions below
name figures — `4,449.0 kg CO2e` — which can only appear if the value was read
from the response, formatted at the metric's own precision and labelled with
the unit that travelled with it.

**`buildResultsReport` is the seam.** `downloadResults` keeps the two lines
that touch `Blob`, `document` and `URL`; everything worth asserting on is the
string, and the string is returnable. The three browser globals Node does not
have are stubbed here (`window.location.search` for `api.js`, `sessionStorage`
for `state.js`) because `results.js` imports `improvement.js`, which imports
both — nothing under test reads either.

The state is assembled from `tests/fixtures/*.json`, which §10 makes the
executable form of the contract, through the same pairing `state.js`'s
`entryResultsFrom` performs. A hand-written state would let this file drift
from the shape the API actually returns.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

pytest.importorskip(
    "playwright.sync_api",
    reason="the export is asserted by driving a real browser, not by reading source",
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
RESULTS_JS = ROOT / "web" / "js" / "results.js"
IMPROVEMENT_JS = ROOT / "web" / "js" / "improvement.js"

#: Node is not a dependency of this project and never becomes one — `web/` has no
#: build step and `docs/architecture.md` §3 rules Node out of the stack. It is
#: only ever the *runner* here, the way a browser is, and it is preinstalled on
#: every GitHub-hosted runner, so this skip does not fire in CI. If it ever does,
#: the front end is untested rather than passing: that is what the reason says.
node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is required to execute web/js/results.js; the export is unverified without it",
)

HARNESS = """
// The three globals `improvement.js` -> {api.js, state.js} read at module load.
// Nothing under test uses any of them.
globalThis.window = { location: { search: '' } }
globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} }

import { readFileSync, writeFileSync } from 'node:fs'
const { buildResultsReport } = await import(process.argv[2])
const state = JSON.parse(readFileSync(process.argv[3], 'utf8'))
writeFileSync(process.argv[4], buildResultsReport(state), 'utf8')
"""


#: **The agreement harness (v1.50 review, item 1).** `HARNESS` above proves
#: `results.js`'s own text export; this proves it *alongside*
#: `improvement.js`'s HTML comparison, from the **one** state object, in the
#: **one** Node process — so a fix that only reaches one of `savingLines` and
#: `comparisonSaving` shows up here as a disagreement rather than as two green
#: test files that each checked their own surface in isolation and never
#: compared notes. `improvement.js`'s `ComparisonResults` is exported for the
#: screen already (`web/js/results.js` renders it into the results page); this
#: calls it directly rather than through a browser; nothing here needs a DOM.
HARNESS_BOTH = """
globalThis.window = { location: { search: '' } }
globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} }

import { readFileSync, writeFileSync } from 'node:fs'
const { buildResultsReport } = await import(process.argv[2])
const { ComparisonResults } = await import(process.argv[3])
const state = JSON.parse(readFileSync(process.argv[4], 'utf8'))
const out = {
  report: buildResultsReport(state),
  html: ComparisonResults(state),
}
writeFileSync(process.argv[5], JSON.stringify(out), 'utf8')
"""


def both_surfaces_for(tmp_path: Path, state: dict) -> dict:
    """`{"report": <text export>, "html": <the comparison screen's markup>}`,
    both built from the same `state` in the same Node process — see
    `HARNESS_BOTH`."""
    harness = tmp_path / "harness_both.mjs"
    harness.write_text(HARNESS_BOTH, encoding="utf-8")
    state_file = tmp_path / "state_both.json"
    state_file.write_text(json.dumps(state), encoding="utf-8")
    out = tmp_path / "both.json"
    completed = subprocess.run(
        [
            shutil.which("node"),
            str(harness),
            RESULTS_JS.as_uri(),
            IMPROVEMENT_JS.as_uri(),
            str(state_file),
            str(out),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert completed.returncode == 0, (
        f"node could not build both surfaces:\n{completed.stdout}\n{completed.stderr}"
    )
    return json.loads(out.read_text(encoding="utf-8"))


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _entry_results(entries: list[dict], response: dict) -> list[dict]:
    """`state.js`'s `entryResultsFrom`, in Python. §7.2."""
    return [
        {
            "entry": entry,
            "response": {
                **(response.get("entries") or [{}] * (index + 1))[index],
                "factor_set": response.get("factor_set"),
                "factor_source": response.get("factor_source"),
                "gwp_horizon": response.get("gwp_horizon"),
            },
        }
        for index, entry in enumerate(entries)
    ]


#: The two entries of `calculate_response.json`, as `calculator.js`'s `draftEntry`
#: builds them: what the user typed, in the unit they typed it in.
DRAFT_ENTRIES = [
    {
        "sector": "processing",
        "foodCategory": "dairy",
        "totalAmount": "1500",
        "totalUnit": "kilograms",
        "current": [
            {"id": "a", "destination": "landfill", "qtyInput": "1200"},
            {"id": "b", "destination": "animal_feed", "qtyInput": "300"},
            {"id": "c", "destination": "compost", "qtyInput": ""},
        ],
    },
    {
        "sector": "primary_production",
        "foodCategory": "vegetables",
        "totalAmount": "800",
        "totalUnit": "kilograms",
        "current": [{"id": "d", "destination": "not_harvested", "qtyInput": "800"}],
    },
]


def build_state(*, is_mock: bool = True, with_comparison: bool = False) -> dict:
    response = _fixture("calculate_response.json")
    response["factor_set"] = dict(response["factor_set"], is_mock=is_mock)
    state = {
        "taxonomy": _fixture("taxonomy.json"),
        "result": {**response, "entry_results": _entry_results(DRAFT_ENTRIES, response)},
        "improvementResult": response if with_comparison else None,
    }
    return state


def build_state_with_share(*, state: str, value: str | None) -> dict:
    """`build_state`, with `totals.data_state.production_share_percent` and
    `totals.production_share_percent` set explicitly - the fixture's own default
    (`incomplete`, `null`) is exercised by `report` above, so the other two states
    need their own response rather than a hand-edited copy per test."""
    response = _fixture("calculate_response.json")
    response["totals"] = dict(response["totals"])
    response["totals"]["data_state"] = dict(
        response["totals"]["data_state"], production_share_percent=state
    )
    response["totals"]["production_share_percent"] = value
    return {
        "taxonomy": _fixture("taxonomy.json"),
        "result": {**response, "entry_results": _entry_results(DRAFT_ENTRIES, response)},
        "improvementResult": None,
    }


#: **v1.50 review, item 3.** Neither `calculate_response.json` (`complete` in
#: all four money fields, `incomplete` share) nor `calculate_response_single.
#: json` (`not_supplied` throughout) ever gives a money field `incomplete` or
#: the share `complete` — the exact state §4.5's rewrite exists for. This
#: fixture does, and it is not hand-typed: `docs/interfaces.md` §10 and
#: `tests/api/test_fixture_consistency.py` both record it as
#: `engine.calculate.calculate`'s own output for a two-entry request in which
#: entry 2 supplies a production total but no money figures.
def build_state_partial_coverage() -> dict:
    """`build_state`'s shape, fed by `calculate_response_partial_coverage.
    json` on both `result` and `improvementResult` — the same doubling
    `build_state(with_comparison=True)` already does for the canonical
    fixture, needed here because `savingLines` reads `improvementResult` and
    `moneyLines` / `productionShareText` read `result`, and this fixture is
    the one response in the tree where all three are worth reading at once."""
    response = _fixture("calculate_response_partial_coverage.json")
    return {
        "taxonomy": _fixture("taxonomy.json"),
        "result": {**response, "entry_results": _entry_results(DRAFT_ENTRIES, response)},
        "improvementResult": response,
    }


#: **v1.51.** Neither fixture above ever gives `production_share_percent` or
#: `wasted_share_percent` the fourth state: every entry answered a production
#: total, a total value and a wasted value of zero, so both ratios are
#: `complete`-coverage and still undefined. Not hand-typed — the same
#: discipline `build_state_partial_coverage` above documents —
#: `tests/api/test_fixture_consistency.py::test_the_zero_totals_pair_is_what_
#: its_name_promises` pins the same fixture's shape from the API side.
def build_state_zero_totals() -> dict:
    response = _fixture("calculate_response_zero_totals.json")
    return {
        "taxonomy": _fixture("taxonomy.json"),
        "result": {**response, "entry_results": _entry_results(DRAFT_ENTRIES, response)},
        "improvementResult": None,
    }


def report_for(tmp_path: Path, state: dict) -> str:
    harness = tmp_path / "harness.mjs"
    harness.write_text(HARNESS, encoding="utf-8")
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps(state), encoding="utf-8")
    out = tmp_path / "report.txt"
    completed = subprocess.run(
        [
            shutil.which("node"),
            str(harness),
            RESULTS_JS.as_uri(),
            str(state_file),
            str(out),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert completed.returncode == 0, (
        f"node could not build the report:\n{completed.stdout}\n{completed.stderr}"
    )
    return out.read_text(encoding="utf-8")


@pytest.fixture
def report(tmp_path):
    return report_for(tmp_path, build_state())


# ------------------------------------------------------- the figures themselves

#: `(metric name, the exact line the fixture's own totals must produce)`.
#:
#: The figures are `totals.current.metrics[*].total` formatted at that metric's
#: `display_precision` and labelled with the `unit` travelling beside it — 4449
#: at precision 1 is `4,449.0`, and en-NZ groups thousands with a comma. Reading
#: `display_unit` instead would print `kg CO₂e` here and `1,787,600 L` would be
#: unaffected, so `co2e` is the row that can tell the two columns apart.
TOTAL_LINES = [
    ("co2e", "  - Greenhouse gases: 4,449.0 kg CO2e"),
    ("ch4", "  - Methane: 32.4 kg CH4"),
    ("water", "  - Water: 1,787,600 L"),
    ("cost", "  - Cost: 72 NZD"),
]


@node
@pytest.mark.parametrize("code,line", TOTAL_LINES, ids=[code for code, _ in TOTAL_LINES])
def test_every_metric_total_reaches_the_export(report, code, line):
    """Anchored to the whole line, on purpose.

    A substring match for `4,449.0` passes on `4,449.0` appearing anywhere,
    including inside a longer number and including without its unit — and "with
    their units" is half of what the export was missing.
    """
    assert re.search(rf"^{re.escape(line)}$", report, re.M), (
        f"{code}'s total is not in the export as `{line}`:\n{report}"
    )


@node
def test_the_export_names_no_metric_of_its_own(report):
    """§7.6.5: adding a metric costs one row and one formula, not an edit here.

    Every metric the response carries is printed, so the count of impact lines
    is the count of metrics the response carries — `mass` excepted, for the
    reason `summaryCards` excepts it, and that exception is asserted directly
    below rather than assumed.
    """
    lines = report.split("Impact summary\n", 1)[1].split("\n\nTangible equivalents", 1)[0]
    assert len(lines.strip().splitlines()) == len(TOTAL_LINES)


@node
def test_mass_is_the_headline_and_not_an_impact_line(report):
    """`mass` is on screen as the primary card and the "Waste amount" column."""
    assert re.search(r"^Total food waste: 2,300\.00 kg$", report, re.M)
    assert re.search(r"^Total food waste: 2\.300 tonnes$", report, re.M)
    assert not re.search(r"^  - Mass: ", report, re.M)


@node
def test_the_equivalence_is_copied_as_the_engine_worded_it(report):
    """§3: `label` is `label_template` already interpolated. Nothing rewords it."""
    assert re.search(r"^  - Equivalent to driving 18,597 km$", report, re.M)


@node
def test_the_equivalence_name_reaches_the_text_export(report):
    """The final review's other divergence (§1b): the page and the PDF each
    print a third fact beside `Total` and `Per unit` -- the equivalence's own
    `name` -- and the text export printed only the first two. `18596.82`
    rounds the same way the engine's own label interpolation does (whole
    number, half rounds up), so `18,597` here is not a coincidence with the
    `Equivalent to driving 18,597 km` line above -- it is the same figure,
    named rather than folded into a sentence."""
    assert re.search(r"^      Kilometres driven: 18,597$", report, re.M)


@node
def test_each_destination_carries_its_own_computed_figures(report):
    """The destination breakdown table, in text.

    3,468 against landfill and 525 against animal feed are `by_destination[].value`
    for `co2e` in the first entry — per-line figures, not the entry total, so a
    report that printed the entry total against every destination fails here.
    """
    assert re.search(
        r"^  - Landfill \(1,200\.000 kg\): .*Greenhouse gases: 3,468\.0 kg CO2e", report, re.M
    )
    assert re.search(
        r"^  - Animal feed \(300\.000 kg\): .*Greenhouse gases: 525\.0 kg CO2e", report, re.M
    )


@node
def test_a_per_entry_total_is_the_entrys_own(report):
    """3,993 is entry 1's `co2e` total; 456 is entry 2's. Neither is 4,449."""
    assert re.search(r"^  - Greenhouse gases: 3,993\.0 kg CO2e$", report, re.M)
    assert re.search(r"^  - Greenhouse gases: 456\.0 kg CO2e$", report, re.M)


@node
def test_the_input_the_user_typed_survives(report):
    """The export was not wrong about the inputs; it must not lose them either."""
    assert re.search(r"^Entry 1: Processing and manufacturing$", report, re.M)
    assert re.search(r"^  - Landfill: 1200\.00 kilograms$", report, re.M)
    assert re.search(r"^Factor version: MOCK-v0 — PLACEHOLDER$", report, re.M)


@node
def test_the_export_does_not_ask_for_a_figure_the_visitor_already_gave(report):
    """**Finding 2, in the file this time.**

    `#total-input` is collected on step 2 and persisted as `total_input_kg`, and
    the export's closing line used to say the figure was unavailable "because
    total food handled data is required", a few lines under a money block built
    from the very value that sentence asked for.

    The engine now returns `production_share_percent`, and `report`'s fixture
    supplies it for one entry and not the other - `incomplete`, not `not
    available` - so the file no longer blames its reader for a figure it never
    asked them to type twice.
    """
    assert "data is required" not in report, report
    assert re.search(
        r"^Percentage waste: Data incomplete\. Some entries stated a production total "
        r"and some did not, so a share of waste cannot be shown\.$",
        report,
        re.M,
    ), report


# ------------------------------------- the percentage card's three states (§4.6)
#
# `productionShareText` backs both `summaryCards` (the on-screen card) and
# `buildResultsReport` (this export) - the same function, so proving its three
# branches here proves the card's wording too. Each test asserts what tells its
# state apart from the *other two*, not just that its own sentence is present: a
# card that always printed the same words would pass a test that only checked
# the state it was pointed at.


@node
def test_the_export_states_the_percentage_when_every_entry_gave_one(tmp_path):
    """`complete`: the number itself, and neither other state's wording."""
    report = report_for(tmp_path, build_state_with_share(state="complete", value="50.00"))
    assert re.search(r"^Percentage waste: 50\.00%$", report, re.M), report
    assert "Data incomplete" not in report
    assert "You did not say" not in report


@node
def test_the_export_says_incomplete_when_some_entries_answered_and_some_did_not(tmp_path):
    """`incomplete`: named as incomplete - not silence, and not "not supplied",
    which would claim nobody said anything when some entries did."""
    report = report_for(tmp_path, build_state_with_share(state="incomplete", value=None))
    assert re.search(
        r"^Percentage waste: Data incomplete\. Some entries stated a production total "
        r"and some did not, so a share of waste cannot be shown\.$",
        report,
        re.M,
    ), report
    assert "You did not say how much food this covered" not in report
    assert not re.search(r"^Percentage waste: \d", report, re.M)


@node
def test_the_export_says_not_supplied_when_nobody_answered(tmp_path):
    """`not_supplied`: worded about what the visitor typed, not about what the
    calculator reports - and not "incomplete", which would imply somebody did
    answer part of it."""
    report = report_for(tmp_path, build_state_with_share(state="not_supplied", value=None))
    assert re.search(
        r"^Percentage waste: Not supplied\. You did not say how much food this covered, "
        r"so a share of waste cannot be shown\.$",
        report,
        re.M,
    ), report
    assert "Data incomplete" not in report
    assert not re.search(r"^Percentage waste: \d", report, re.M)


@node
def test_the_export_says_undefined_when_every_entry_answered_zero(tmp_path):
    """v1.51's fourth state: every entry answered `total_input_kg` — as
    zero — so the coverage is `complete`, and the ratio built from what they
    answered is still undefined. Distinct from `not_supplied` (the visitor
    said none, not nothing) and from `incomplete` (nobody left a gap)."""
    report = report_for(tmp_path, build_state_with_share(state="undefined", value=None))
    assert re.search(
        r"^Percentage waste: Undefined\. You said this covered 0 kg in total, "
        r"so a share of waste cannot be shown\.$",
        report,
        re.M,
    ), report
    assert "Data incomplete" not in report
    assert "You did not say how much food this covered" not in report
    assert not re.search(r"^Percentage waste: \d", report, re.M)


@node
def test_the_export_says_undefined_for_a_money_share_of_a_zero_total(tmp_path):
    """The money block's own fourth state, off the real fixture rather than a
    hand-built one: both `total_value_nzd` and `wasted_value_nzd` print as
    real `NZ$0.00` figures — `complete`, not withheld — and only the ratio
    built from them says it cannot be calculated."""
    report = report_for(tmp_path, build_state_zero_totals())
    assert re.search(r"^\s*- Total value of food handled: NZ\$0\.00$", report, re.M), report
    assert re.search(r"^\s*- Value of food wasted: NZ\$0\.00$", report, re.M), report
    assert re.search(
        r"^\s*- Share of value wasted: The total value was zero, so this "
        r"cannot be calculated\.$",
        report,
        re.M,
    ), report
    assert "Not every entry supplied this figure" not in report


# --------------------------------------------------------- the placeholder rule

NOTICE = "Demonstration only — verified calculation factors have not yet been supplied."


@node
def test_the_placeholder_notice_is_on_a_mock_export(report):
    assert re.search(rf"^{re.escape(NOTICE)}$", report, re.M)


@node
def test_the_placeholder_notice_is_absent_from_a_real_export(tmp_path):
    """§7.6.2 conditions the notice on `is_mock`, in both directions.

    A client-facing report that disclaims real data is the more damaging half
    of the same bug, and the figures must still be there without it.
    """
    report = report_for(tmp_path, build_state(is_mock=False))
    assert NOTICE not in report
    assert re.search(r"^  - Greenhouse gases: 4,449\.0 kg CO2e$", report, re.M)


@node
def test_the_equivalence_disclaimer_is_absent_from_a_real_export(tmp_path):
    """The equivalence caveat's own negative case (L52). Gated on `is_mock`
    the same way `NOTICE` is above, but until now only
    `tests/api/test_pdf_render.py::test_a_real_factor_set_carries_no_warning`
    proved the gate holds - the page and this export were only ever asserted
    with `is_mock` true. The basis note itself is not gated and must still
    print; only the placeholder sentence goes."""
    report = report_for(tmp_path, build_state(is_mock=False))
    assert "The conversion factor comes from the client" not in report
    assert "placeholder factors" not in report
    assert "PLACEHOLDER. Open item O-3" in report


# ------------------------------------------------- the unit each row was measured in

#: One entry whose three destination rows were each measured differently: the
#: entry's own tonnes, an explicit kilograms, and a container preset. `qtyInput`
#: is what the visitor typed and `unit` is what they typed it in - the shape
#: `state.current` has carried since a row could differ from its neighbour.
MIXED_UNIT_ENTRY = [
    {
        "sector": "processing",
        "foodCategory": "dairy",
        "totalAmount": "1.5",
        "totalUnit": "tonnes",
        "current": [
            {"id": "a", "destination": "landfill", "qtyInput": "0.5", "unit": "tonnes"},
            {"id": "b", "destination": "animal_feed", "qtyInput": "500", "unit": "kilograms"},
            {"id": "c", "destination": "compost", "qtyInput": "0.25", "unit": "preset:food_scraps_bin_23l"},
        ],
    }
]


def mixed_unit_report(tmp_path: Path) -> str:
    response = _fixture("calculate_response.json")
    state = {
        "taxonomy": _fixture("taxonomy.json"),
        "result": {**response, "entry_results": _entry_results(MIXED_UNIT_ENTRY, response)},
        "improvementResult": None,
    }
    return report_for(tmp_path, state)


@node
def test_a_row_is_labelled_with_its_own_unit_not_the_entrys(tmp_path):
    """The export said `${qtyInput} ${entry.totalUnit}`, and a row carries its own
    unit - so half a tonne, sent to the API as `500.000`, was written into the
    downloaded file as `0.50 kilograms`.

    `results.js`'s own note says this report exists to be attached to an email
    and believed. A figure under a label that is not its own is the one kind of
    error that file cannot afford, and it is a thousandfold one here.

    The kilograms ride along for any row not already in them: `0.50 tonnes` is
    what the visitor said and `(500.000 kg)` is what was calculated from it, and
    a reader holding only this file needs both to check one against the other.
    """
    report = mixed_unit_report(tmp_path)
    assert re.search(r"^  - Landfill: 0\.50 tonnes \(500\.000 kg\)$", report, re.M), report
    assert re.search(r"^  - Animal feed: 500\.00 kilograms$", report, re.M), report
    #: The row that says it plainest: nothing in the file may call half a tonne
    #: half a kilogram, in either unit's name.
    assert not re.search(r"^  - Landfill: 0\.50 kilograms$", report, re.M), report


@node
def test_a_container_row_names_the_container_and_its_kilograms(tmp_path):
    """A `preset:` row is the case `massToKg` cannot express at all: it applies no
    preset, so a quarter of a 23 L bin printed as `0.25 kilograms` rather than
    the 1.668 kg `toKg` sends. The container's `label` is staff-typed and is
    published exactly as written (§7.7.7), never translated.
    """
    report = mixed_unit_report(tmp_path)
    assert re.search(
        r"^  - Composting \(aerobic digestion\): 0\.25 × 23 L kerbside food scraps bin \(full\) \(1\.668 kg\)$",
        report,
        re.M,
    ), report


# ---------------------------------------------------------------- the comparison


@node
def test_no_comparison_section_when_none_was_run(report):
    assert "Improved scenario" not in report


@node
def test_the_comparison_reads_net_benefit_rather_than_subtracting(tmp_path):
    """§7.6.1: `1,560.0` is `totals.net_benefit.co2e`, not `4,449 − 2,889` done here.

    The two agree in this fixture, which is the point of asserting the label as
    well: the engine's figure arrives with the engine's sign convention, and a
    positive `net_benefit` is a saving.
    """
    report = report_for(tmp_path, build_state(with_comparison=True))
    assert re.search(
        r"^  - Greenhouse gases: 4,449\.0 kg CO2e → 2,889\.0 kg CO2e \(1,560\.0 kg CO2e saved\)$",
        report,
        re.M,
    )
    assert not re.search(r"^  - Mass: ", report, re.M)


FILENAME_HARNESS = """
globalThis.window = { location: { search: '' } }
globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} }

import { writeFileSync } from 'node:fs'
const { exportFilename } = await import(process.argv[2])
// Local-time constructor on purpose: the stamp is meant to read as the clock
// the person pressing the button is looking at, so a UTC fixture would assert
// the wrong thing on any machine that is not on UTC.
const fixed = new Date(2026, 7, 12, 9, 4, 5)
writeFileSync(process.argv[3], exportFilename(fixed), 'utf8')
"""


def _filename_for(tmp_path: Path) -> str:
    harness = tmp_path / "filename.mjs"
    harness.write_text(FILENAME_HARNESS, encoding="utf-8")
    out = tmp_path / "name.txt"
    completed = subprocess.run(
        [shutil.which("node"), str(harness), RESULTS_JS.as_uri(), str(out)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert completed.returncode == 0, (
        f"node could not build the file name:\n{completed.stdout}\n{completed.stderr}"
    )
    return out.read_text(encoding="utf-8")


@node
def test_the_export_file_name_carries_a_zero_padded_timestamp(tmp_path):
    """August is `08` and 9:04:05 is `090405`, so a directory sorts chronologically.

    Unpadded parts would put `2026-8-12` after `2026-10-01` in a file listing,
    which is the one property the stamp exists to provide. The date is fixed in
    the harness rather than read from the clock - `exportFilename` takes `now`
    for the same reason `engine.calculate` takes no clock.
    """
    assert _filename_for(tmp_path) == "food-waste-impact-results-2026-08-12-090405.txt"


def test_the_download_no_longer_hard_codes_one_name():
    """The stamp is worthless if `downloadResults` keeps its own literal.

    This asserts the absence of the old constant rather than the presence of the
    call, because a file that contains both would pass a presence check while
    every export still landed on the same name.
    """
    source = RESULTS_JS.read_text(encoding="utf-8")
    assert "'food-waste-impact-results.txt'" not in source
    assert re.search(r"^\s*link\.download = exportFilename\(\)$", source, re.M)


# ------------------------------------------------- the destination-first pivot (browser)
#
# **Item ③.** `buildResultsReport`'s harness above proves the plain-text export;
# `breakdowns()`'s new destination-first tree is markup and CSS, which only a real DOM
# can prove was built at all and only a real viewport can prove reads as three levels
# rather than the one section-per-entry list it replaced. So this half runs in a real
# browser rather than under Node, the same way `test_step_navigation.py` and
# `test_amount_limits_browser.py` do, and for the same reason.
#
# Requires the stack rebuilt after any change under `web/`:
#     docker compose -f docker/compose.yaml up -d --build web

#: The origin, not a page - `/index.html` is named explicitly so this measures the
#: calculator whatever the `index` directive does next, the same reasoning
#: `test_step_navigation.py` gives for its own `BASE`.
CALCULATOR_URL = os.environ.get("KAICALC_WEB_URL", "http://localhost:18080").rstrip("/") + "/index.html"

FORCE_AUTO_SCROLL = "html { scroll-behavior: auto !important; }"


@pytest.fixture
def page_at(browser):
    """A page with the calculate POST fulfilled from a given contract-shaped response.

    Mirrors `test_step_navigation.py`'s fixture of the same name: `/api/v1/calculate`
    carrying `X-Dry-Run` is staff-only and answers 401, so the results view is reached
    by fulfilling the POST in-browser rather than by driving the real API. Unlike that
    fixture, the body is a parameter rather than one fixed file, because this file's two
    tests need two different shapes of it - one where a destination is shared between
    two entries (there is no such shape in `tests/fixtures/*.json` today) and, for the
    second test, the same shape read back to check the page against it.
    """
    contexts = []

    def open_page(response, width=390, height=900, reduced_motion=None):
        ctx = browser.new_context(
            viewport={"width": width, "height": height},
            locale="en-NZ",
            bypass_csp=True,
            reduced_motion=reduced_motion,
        )
        contexts.append(ctx)
        page = ctx.new_page()
        page.route(
            "**/api/v1/calculate*",
            lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(response)),
        )
        try:
            page.goto(f"{CALCULATOR_URL}?lang=en", wait_until="networkidle", timeout=15000)
        except Exception as error:  # pragma: no cover - environment guard
            pytest.skip(f"the front end is not being served at {CALCULATOR_URL}: {error}")
        page.add_style_tag(content=FORCE_AUTO_SCROLL)
        page.wait_for_selector('[data-action="start"]', timeout=10000)
        return page

    yield open_page
    for ctx in contexts:
        ctx.close()


def _submit_two_entries(page, time_frame=None):
    """Drive the wizard through two entries and press Calculate.

    What is typed does not matter. `sectorName` and `stageFoodLabel` in `results.js`
    both read `response.sector` / `response.food_category` ahead of what the entry
    itself carries, so the two entries only have to *exist* - for `entryResultsFrom`'s
    index pairing (`state.js`) to carry both of the fulfilled response's entries onto
    the results page. What labels them on screen is the fulfilled body, not this walk.

    `time_frame`, when given, is chosen on the review step's `#time-frame` select
    before the final Calculate click - the one place `state.timeFrame` (item ⑦) is
    ever set, since it is never part of the fulfilled response.
    """
    page.click('[data-action="start"]')
    page.wait_for_selector('input[name="sector"]')
    for first_entry in (True, False):
        page.evaluate("document.querySelector('input[name=sector]').click()")
        page.wait_for_timeout(60)
        page.click('[data-action="continue"]')
        page.wait_for_selector('input[name="food-category"]')
        page.click('[data-action="continue"]')
        page.wait_for_selector("#total-waste")
        page.fill("#total-waste", "1000")
        page.click('[data-action="continue"]')
        page.wait_for_selector('[data-line-field="amount"]')
        page.fill('[data-line-field="amount"] >> nth=0', "1000")
        page.wait_for_timeout(60)
        page.click('[data-action="continue"]')
        page.wait_for_selector('[data-action="calculate"]')
        if first_entry:
            page.click('[data-action="add-entry"]')
            page.wait_for_selector('input[name="sector"]')
    if time_frame:
        page.select_option("#time-frame", time_frame)
    page.click('[data-action="calculate"]')
    page.wait_for_selector(".results-page", timeout=15000)


def _metric(unit, precision, total, rows):
    """One `MetricResult`, wire-shaped: `(destination, qty_kg, upstream, downstream, value)`
    tuples become `by_destination` rows, the field names §6.2 and the fixtures use."""
    return {
        "unit": unit,
        "display_precision": precision,
        "total": total,
        "by_destination": [
            {"destination": destination, "qty_kg": qty_kg, "upstream": upstream, "downstream": downstream, "value": value}
            for destination, qty_kg, upstream, downstream, value in rows
        ],
    }


#: Two entries, different sectors and different food categories, both wasting to
#: `landfill` - the one shape neither `calculate_response.json` nor
#: `calculate_response_single.json` carries. In `calculate_response.json` no
#: destination is shared by two entries at all; rendered per entry (the shape this
#: task replaces) or per destination (the new one), a fixture without that overlap
#: produces the *same* single section either way, so nothing already in the
#: repository can tell the transpose from the original.
#:
#: `totals`' landfill `qty_kg` is deliberately **not** 700 + 500. If it were, a
#: `.destination-group__total` built by summing the two rows below in JavaScript
#: would read right by coincidence, and the test that exists to prove §7.6.1 would
#: pass for the wrong reason - the exact trap its own docstring names. 1,300.000 is
#: the figure the engine is asserted to have returned, and it is the only one either
#: test below may show; 1,200.000, the sum of the two rows, must not appear.
SHARED_DESTINATION_RESPONSE = {
    "token": "11111111-2222-3333-4444-555555555555",
    "factor_set": {"version_label": "MOCK-v0 — PLACEHOLDER", "is_mock": True},
    "factor_source": "published",
    "gwp_horizon": 100,
    "totals": {
        "total_kg": "1200.000",
        "current": {
            "metrics": {
                "co2e": _metric("kg CO2e", 1, "1325.0000000000", [
                    ("landfill", "1300.000", "0.0000000000", "0.0000000000", "1325.0000000000"),
                ]),
                "mass": _metric("kg", 1, "1200.0000000000", [
                    ("landfill", "1300.000", "0.0000000000", "0.0000000000", "1300.0000000000"),
                ]),
            },
            "equivalences": [],
        },
        "alternative": None,
        "net_benefit": None,
        "money": None,
    },
    "entries": [
        {
            "sector": "processing",
            "food_category": "dairy",
            "current": {
                "total_kg": "700.000",
                "metrics": {
                    "co2e": _metric("kg CO2e", 1, "770.0000000000", [
                        ("landfill", "700.000", "1.0000000000", "0.1000000000", "770.0000000000"),
                    ]),
                    "mass": _metric("kg", 1, "700.0000000000", [
                        ("landfill", "700.000", "0.0000000000", "0.0000000000", "700.0000000000"),
                    ]),
                },
                "equivalences": [],
            },
            "alternative": None,
            "net_benefit": None,
        },
        {
            "sector": "primary_production",
            "food_category": "vegetables",
            "current": {
                "total_kg": "500.000",
                "metrics": {
                    "co2e": _metric("kg CO2e", 1, "550.0000000000", [
                        ("landfill", "500.000", "1.3000000000", "-0.2000000000", "550.0000000000"),
                    ]),
                    "mass": _metric("kg", 1, "500.0000000000", [
                        ("landfill", "500.000", "0.0000000000", "0.0000000000", "500.0000000000"),
                    ]),
                },
                "equivalences": [],
            },
            "alternative": None,
            "net_benefit": None,
        },
    ],
}


def _results_page_with_two_entries_sharing_a_destination(page_at):
    """The page, on the destination tab, built from `SHARED_DESTINATION_RESPONSE`."""
    page = page_at(SHARED_DESTINATION_RESPONSE)
    _submit_two_entries(page)
    page.click("#breakdown-tab-destination")
    page.wait_for_selector(".destination-group", timeout=10000)
    return page


def _results_page_and_its_response(page_at):
    """The same page, paired with the response it was built from - for asserting the
    page against the response rather than against a number this file derives."""
    page = _results_page_with_two_entries_sharing_a_destination(page_at)
    return page, SHARED_DESTINATION_RESPONSE


@pytest.mark.browser
def test_the_destination_breakdown_is_grouped_by_destination_first(page_at):
    """**Item ③, and it is the transpose of what is there.**

    Today `breakdowns()` pushed one section per ENTRY and listed that entry's
    destinations inside it. The client asked for the other axis:

        1. Animal feed
           a) from the farm    -> dairy, vegetables, mixed
           b) from a restaurant
        2. Landfill
           a) ...

    Every figure for it is already in the response. Each entry carries its own
    `sector_code` and `food_category`, and its metrics carry a per-destination
    breakdown; stage one added the cross-entry roll-up that gives the top level a
    total nobody has to add up.
    """
    page = _results_page_with_two_entries_sharing_a_destination(page_at)

    groups = page.locator(".destination-group")
    assert groups.count() >= 1, "nothing is grouped by destination"

    first = groups.nth(0)
    heading = first.locator(".destination-group__name").inner_text()
    assert heading, "a destination group with no destination name"

    #: The stages under it, and the food categories under those. Three levels, which
    #: is what "destination, then stage, then food" asks for.
    stages = first.locator(".destination-group__stage")
    assert stages.count() >= 2, (
        "the two entries sharing this destination did not both appear under it"
    )
    assert stages.nth(0).locator(".destination-group__food").count() >= 1


@pytest.mark.browser
def test_a_destination_group_s_total_comes_from_the_response(page_at):
    """**§7.6.1, and this is the one place it is tempting to break.**

    The leaves are per-entry figures and the group heading is their sum - so adding
    them up in JavaScript would produce the right number *for real data* and be
    forbidden anyway. `totals.current.metrics[*].by_destination` is the engine's own
    roll-up (§3 rule 2, v1.48), and the page must render it rather than derive it.

    `SHARED_DESTINATION_RESPONSE` is built so the two claims are distinguishable: its
    landfill `qty_kg` at the totals level (1,300.000) is not the sum of the two
    entries' own rows (700 + 500 = 1,200.000). Comparing the rendered heading against
    the response the page was given - rather than against a sum computed in this
    file - is the only way to tell a read from a re-computation that happens to agree,
    and here the two do not even agree by coincidence.
    """
    page, response = _results_page_and_its_response(page_at)

    rolled = {
        row["destination"]: row["qty_kg"]
        for row in response["totals"]["current"]["metrics"]["mass"]["by_destination"]
    }
    assert rolled, "the totals-level roll-up is missing from the response"

    for code, qty_kg in rolled.items():
        group = page.locator(f'.destination-group[data-destination="{code}"]')
        assert group.count() == 1, f"no group for {code}"
        shown = group.locator(".destination-group__total").inner_text()
        #: The response's own string, formatted for display - not a number this page
        #: arrived at by adding the two entries' rows together.
        assert f"{float(qty_kg):,.3f}" in shown, (
            f"{code}: page shows {shown!r}, response says {qty_kg}"
        )


@pytest.mark.browser
def test_a_destination_group_s_metric_figures_come_from_the_response(page_at):
    """**§7.6.1 again, one column to the right - and this is the half that had no
    test.**

    The assertion above reads `.destination-group__total`, the kilogram figure.
    Every other figure in the header - greenhouse gases, methane, water, cost -
    goes through the same roll-up and had nothing asserting it, so summing the
    leaves in JavaScript produced a page showing **1,320.0 kg CO2e against the
    response's 1,325.0** and all thirty-six tests stayed green.

    The two claims are distinguishable in `SHARED_DESTINATION_RESPONSE` on
    purpose: the totals-level `co2e` value for landfill (1,325.0) is deliberately
    not 770.0 + 550.0, so a figure arrived at by adding the two entries' rows is
    not merely forbidden, it is a different number on screen. Both are asserted -
    the response's figure present, the leaf sum absent - because a header
    carrying both would satisfy either check alone.
    """
    page, response = _results_page_and_its_response(page_at)

    checked = 0
    for code, metric in response["totals"]["current"]["metrics"].items():
        if code == "mass":
            #: `mass` is the kilogram figure the assertion above already covers,
            #: and `destinationRowFigures` holds it out for that reason.
            continue
        precision = int(metric["display_precision"])
        #: What the browser would get by adding the entries' own rows together -
        #: the forbidden number, computed here so it can be asserted *absent*.
        leaves = {}
        for entry in response["entries"]:
            for row in entry["current"]["metrics"][code]["by_destination"]:
                leaves[row["destination"]] = leaves.get(row["destination"], 0) + float(row["value"])
        for row in metric["by_destination"]:
            destination = row["destination"]
            group = page.locator(f'.destination-group[data-destination="{destination}"]')
            assert group.count() == 1, f"no group for {destination}"
            shown = group.locator(".destination-group__figures").inner_text()
            assert f"{float(row['value']):,.{precision}f}" in shown, (
                f"{destination}/{code}: page shows {shown!r}, response says {row['value']}"
            )
            leaf_sum = f"{leaves.get(destination, 0):,.{precision}f}"
            if leaf_sum != f"{float(row['value']):,.{precision}f}":
                assert leaf_sum not in shown, (
                    f"{destination}/{code}: the header shows {leaf_sum}, which is the "
                    "entries' rows added up in the browser rather than the engine's "
                    f"own roll-up of {row['value']}"
                )
            checked += 1
    assert checked, "no non-mass metric was checked, so this proves nothing"


@pytest.mark.browser
def test_a_group_fed_by_one_entry_prints_its_figures_once(page_at):
    """**Finding 7, and it is the commonest journey rather than an edge case.**

    §3 rule 2 builds a destination's group total by rolling the entries' own
    `by_destination` rows up, so a group used by exactly one entry has a total
    that *is* that entry's row - identical at every metric, by construction. The
    tree printed both, under a note saying the rows "add up to the total shown
    rather than repeat it". With one entry, which is what the calculator opens
    on, every group was such a group: the page contradicted itself everywhere.

    `calculate_response.json` has two entries and **no destination shared between
    them**, so every group here has exactly one contributing entry - the same
    shape a single-entry calculation produces, reached through the fixture this
    file already drives.

    The stage and its food category still render. Losing them would answer one of
    the client's three questions instead of three; what is dropped is the repeat,
    not the level.
    """
    page = page_at(_fixture("calculate_response.json"))
    _submit_two_entries(page)
    page.click("#breakdown-tab-destination")
    page.wait_for_selector(".destination-group", timeout=10000)

    groups = page.locator(".destination-group")
    assert groups.count() >= 1
    single = 0
    for index in range(groups.count()):
        group = groups.nth(index)
        stages = group.locator(".destination-group__stage")
        if stages.count() != 1:
            continue
        single += 1
        assert group.locator(".destination-group__total").inner_text().strip(), (
            "the group lost the engine's own total"
        )
        assert group.locator(".destination-group__stage-figures").count() == 0, (
            "the only contributing entry repeats the group's metric figures"
        )
        assert group.locator(".destination-group__stage-row span").count() == 0, (
            "the only contributing entry repeats the group's kilogram figure"
        )
        assert stages.locator(".destination-group__food").count() >= 1, (
            "suppressing the repeat also removed the food category, which is one "
            "of the three questions this tree answers"
        )
        assert stages.inner_text().strip(), "the stage lost its name as well"
    assert single, "no single-entry group in a fixture built to have only those"

    assert page.locator(".breakdown-note").count() == 0, (
        "the note says the rows add up to the total rather than repeat it, on a "
        "page where no group has more than one row"
    )


@pytest.mark.browser
def test_a_group_fed_by_two_entries_still_shows_both_and_says_why(page_at):
    """The affirmative half of the test above, and the reason it is not a licence
    to delete the level.

    Where a destination really is shared, the per-entry rows carry figures that
    are *not* the group's - 700 kg and 500 kg under a rolled-up 1,300 kg here -
    and the note explaining that relationship is printed exactly where the
    relationship exists.
    """
    page = _results_page_with_two_entries_sharing_a_destination(page_at)

    shared = page.locator('.destination-group[data-destination="landfill"]')
    assert shared.locator(".destination-group__stage").count() == 2
    assert shared.locator(".destination-group__stage-figures").count() == 2, (
        "two entries share this destination and their own figures are hidden"
    )
    note = page.locator(".breakdown-note")
    assert note.count() == 1, "the note is missing where the rows really do differ"
    assert "repeat" in note.inner_text().lower()


# ------------------------------------------------------ the money, and the period
#
# Task 2. `totals.money` (§4.5) is figures the *visitor typed*, not a metric and not
# derived here - the section and the export line print exactly what the response
# carries. `state.timeFrame` (item ⑦) is a label the visitor chose on the review
# step; it is never part of the fulfilled response, so `_submit_two_entries` selects
# it in-browser via `#time-frame` on the way to Calculate.


def _money_response(*, total=None, wasted=None, share=None):
    """A deep copy of `calculate_response.json` - the fixture §10 names as the one
    that carries a populated `totals.money` - with that block replaced by exactly
    the three figures given. A figure left as `None` here stays absent on the wire,
    the same as a visitor who left that field blank (§4.5: every field is `null`
    unless everything it derives from was supplied).

    **`saving_nzd` is held at `None` and is not a parameter**, because this fixture
    fulfils the *Calculate* POST, and Calculate sends `alternative: null` for every
    entry - so §4.5 makes the saving `None` on every response this helper can
    honestly stand for. Two tests used to pass a value here and assert the row on
    screen; the shape they built could not come out of the API, which is how the
    row stayed dead in the product with both of them green. The saving is proved
    against the real stack in `tests/web/test_improvement_saving_browser.py`.
    """
    response = copy.deepcopy(_fixture("calculate_response.json"))
    response["totals"]["money"] = {
        "total_value_nzd": total,
        "wasted_value_nzd": wasted,
        "wasted_share_percent": share,
        "saving_nzd": None,
    }
    return response


def _results_page_with_money(page_at, *, total=None, wasted=None, share=None,
                              time_frame=None):
    """The results page, fulfilled from a response carrying exactly the given
    `totals.money` figures - and, when `time_frame` is given, with that period
    chosen on the way through the wizard."""
    page = page_at(_money_response(total=total, wasted=wasted, share=share))
    _submit_two_entries(page, time_frame=time_frame)
    return page


def _results_page_without_money(page_at):
    """The common case: `totals.money` is `null`, the same shape
    `calculate_response_single.json` carries on the wire (§4.5's absent case)."""
    response = copy.deepcopy(_fixture("calculate_response.json"))
    response["totals"]["money"] = None
    page = page_at(response)
    _submit_two_entries(page)
    return page


def _results_page_with_time_frame(page_at, time_frame):
    """A visitor who chose a reporting period and supplied no money figure at all -
    item ⑦ does not depend on item ⑤, so the money block stays `null` here."""
    response = copy.deepcopy(_fixture("calculate_response.json"))
    response["totals"]["money"] = None
    page = page_at(response)
    _submit_two_entries(page, time_frame=time_frame)
    return page


@pytest.mark.browser
def test_the_money_figures_appear_when_the_visitor_supplied_them(page_at):
    """Item ⑤'s display half. Every figure is read from `totals.money`, which
    the engine derived - the share is not computed here, and neither is the
    saving.

    The share given (`61.11`) is deliberately **not** what `wasted / total *
    100` would produce from the totals below (that division is `3.75`) - a
    page that recomputed the share in JavaScript instead of reading
    `wasted_share_percent` would show `3.75` here and fail this assertion,
    the same strengthening Task 1's mutation step applies to its own figures.
    """
    page = _results_page_with_money(page_at, total="120000.00",
                                     wasted="4500.00", share="61.11")

    section = page.locator(".money-summary")
    assert section.count() == 1, "no money section on a result that has money"
    text = section.inner_text()
    assert "61.11" in text, "the share of value wasted is not the response's own figure"
    assert "4,500" in text
    assert "NZ$" in text or "NZD" in text, "the currency is not named"


@pytest.mark.browser
def test_the_money_section_is_absent_when_nobody_supplied_a_value(page_at):
    """The common case, and the affirmative half of the test above: a section
    that always renders would satisfy it while showing "—" to every visitor
    who typed nothing."""
    page = _results_page_without_money(page_at)

    assert page.locator(".money-summary").count() == 0


@pytest.mark.browser
def test_an_absent_money_figure_renders_as_nothing_at_all(page_at):
    """**Absent is not zero - and it is not "Not available" either.**

    §4.5 makes each field `null` unless everything it derives from was supplied,
    and a computed zero would read as "this food was worth nothing" rather than
    "nobody said". `hasValue` is the whole of that rule in `results.js`, and it
    had no test: loosening it to `value !== undefined` puts `null` through `nzd`,
    which hands `formatNumber` a NaN, which prints the interface's own
    "Not available" - so the page read "Value of food not wasted at all:
    NZ$Not available" with all thirty-six tests green.

    So this counts the rows as well as reading them. One figure supplied means
    one row; a block that renders every row whatever the response says would
    satisfy a substring check on the figure that *was* supplied.
    """
    page = _results_page_with_money(page_at, total="120000.00")

    summary = page.locator(".money-summary")
    assert summary.count() == 1, "the money block is missing from a priced response"
    text = summary.inner_text()
    assert "120,000.00" in text, f"the one supplied figure is not shown: {text!r}"
    assert summary.locator(".money-row").count() == 1, (
        "two money fields were null and the block printed a row for them anyway: "
        f"{text!r}"
    )
    remainder = text.replace("120,000.00", "")
    for placeholder in ("Not available", "NZ$0.00", "0.00%", "\u2014"):
        assert placeholder not in remainder, (
            f"an absent money figure rendered as {placeholder!r}: {text!r}"
        )


@pytest.mark.browser
def test_the_saving_is_not_advertised_by_a_calculation_that_has_none(page_at):
    """The other half of the row's move (finding 1).

    Calculate sends `alternative: null`, so §4.5 leaves `saving_nzd` `None` on
    every response this block is fed by. The label must not appear here at all -
    not as a row, and not as a caveat with nothing above it. Where it *does*
    appear is beside the comparison, which
    `tests/web/test_improvement_saving_browser.py` drives against the real API.
    """
    page = _results_page_with_money(page_at, total="120000.00",
                                     wasted="4500.00", share="3.75")

    text = page.locator(".money-summary").inner_text().lower()
    assert "not wasted at all" not in text
    assert "assumes an even value per kilogram" not in text


@pytest.mark.browser
def test_a_share_over_100_percent_renders_as_a_number(page_at):
    """§4.5: the share is deliberately unclamped - a visitor who typed a wasted
    value above the total value sees a figure over 100%, not a value silently
    reshaped, and not a bar or fill that would look like a rendering fault. This
    section draws no bar for the share at all, so a figure over 100% is simply
    the number the response gave."""
    page = _results_page_with_money(page_at, total="1000.00", wasted="1425.00",
                                     share="142.50")

    text = page.locator(".money-summary").inner_text()
    assert "142.5" in text


@pytest.mark.browser
def test_the_mock_warning_still_shows_beside_the_money_section(page_at):
    """§7.6.2: the placeholder-data warning is mandatory and non-dismissible on
    every results view. Adding the money section must not push it off the top of
    the page or behind a disclosure - it is asserted visible here on a page that
    also carries `.money-summary`, and it must not be sitting inside a `<details>`
    (a `<details>` renders and stays queryable while collapsed, so the safeguard
    is that no ancestor of it is one)."""
    page = _results_page_with_money(page_at, total="120000.00", wasted="4500.00",
                                     share="3.75")

    warning = page.locator(".disclaimer")
    assert warning.count() == 1
    assert warning.is_visible()
    assert warning.locator("xpath=ancestor::details").count() == 0


@pytest.mark.browser
def test_the_period_is_shown_when_it_was_stated(page_at):
    """Item ⑦'s display half. A label, not a computation - nothing on this
    page is scaled by it."""
    page = _results_page_with_time_frame(page_at, "one_month")

    assert "month" in page.locator(".results-period").inner_text().lower()


@pytest.mark.browser
def test_the_period_is_absent_when_it_was_not_stated(page_at):
    """The affirmative half of the test above: `''` means the visitor did not
    say, and it must render nothing - never the raw empty string, and never a
    phrase implying "not stated" is itself a period."""
    page = _results_page_with_money(page_at, total="120000.00", wasted="4500.00",
                                     share="3.75")

    assert page.locator(".results-period").count() == 0


# --------------------------------- the percentage card's three states, on screen
#
# The `@node` tests above (`test_the_export_states_the_percentage_when_every_entry_
# gave_one` and its two siblings) prove `productionShareText` by calling
# `buildResultsReport`, which is the text export's own call site - not
# `summaryCards`', the card a visitor actually looks at. The two call the shared
# helper independently (`results.js` lines ~236 and ~656), so a fault planted at
# `summaryCards`' own call site - the branch forced unconditionally, the two
# non-complete states swapped, or the card's number replaced with a constant -
# changes nothing the export tests above can see. These render the real page
# through `page_at` and read the card `summaryCards` actually built.


def _share_response(*, state, value=None):
    """A deep copy of `calculate_response.json` with `totals.production_share_
    percent` and its `data_state` entry set explicitly - the on-screen
    counterpart of `build_state_with_share` above, which drives the same three
    states through the text export instead."""
    response = copy.deepcopy(_fixture("calculate_response.json"))
    response["totals"]["data_state"] = dict(
        response["totals"]["data_state"], production_share_percent=state
    )
    response["totals"]["production_share_percent"] = value
    return response


def _results_page_with_share(page_at, *, state, value=None):
    page = page_at(_share_response(state=state, value=value))
    _submit_two_entries(page)
    return page


def _share_card(page):
    """The one `.result-card` `summaryCards` labels "Percentage waste", among
    the several it renders for a two-entry submission."""
    card = page.locator('.result-card:has-text("Percentage waste")')
    assert card.count() == 1, "no Percentage waste card on the results page"
    return card


@pytest.mark.browser
def test_the_percentage_card_shows_the_number_when_every_entry_gave_one(page_at):
    """`complete`: the card prints the percentage itself, and neither other
    state's wording - kills a mutation that hardcodes the card's value (a
    constant would not read `50.00` back) and a mutation that renders the
    `complete` branch unconditionally regardless of `data_state` (it would
    still say `50.00%` here, but fails the `incomplete` and `not_supplied`
    tests below instead)."""
    page = _results_page_with_share(page_at, state="complete", value="50.00")
    text = _share_card(page).inner_text()
    assert "50.00%" in text, f"the card does not show the supplied percentage: {text!r}"
    assert "Data incomplete" not in text
    assert "Not supplied" not in text


@pytest.mark.browser
def test_the_percentage_card_says_incomplete_when_some_entries_answered_and_some_did_not(page_at):
    """`incomplete`: named as incomplete, not silence and not "not supplied" -
    which would claim nobody said anything when some entries did. Kills a
    mutation that swaps the `incomplete` and `not_supplied` wording at the
    card's own call site (this response's state is `incomplete`; the swap
    would print the `not_supplied` sentence here instead) and a mutation that
    renders the `complete` branch unconditionally (it would print
    `Not available%` here, not this sentence)."""
    page = _results_page_with_share(page_at, state="incomplete")
    text = _share_card(page).inner_text()
    assert "Data incomplete" in text, text
    assert (
        "Some entries stated a production total and some did not, so a share "
        "of waste cannot be shown."
    ) in text, text
    assert "Not supplied" not in text
    assert "You did not say" not in text
    assert not re.search(r"\d+\.\d+%", text), f"a number leaked into an incomplete card: {text!r}"


@pytest.mark.browser
def test_the_percentage_card_says_not_supplied_when_nobody_answered(page_at):
    """`not_supplied`: worded about what the visitor typed, not "incomplete" -
    which would imply somebody did answer part of it. Kills the same
    call-site swap as the test above, from the other direction (this
    response's state is `not_supplied`; the swap would print the `incomplete`
    sentence here instead)."""
    page = _results_page_with_share(page_at, state="not_supplied")
    text = _share_card(page).inner_text()
    assert "Not supplied" in text, text
    assert "You did not say how much food this covered, so a share of waste cannot be shown." in text, text
    assert "Data incomplete" not in text
    assert not re.search(r"\d+\.\d+%", text), f"a number leaked into a not_supplied card: {text!r}"


@pytest.mark.browser
def test_the_percentage_card_says_undefined_when_every_entry_answered_zero(page_at):
    """v1.51's fourth state, on screen: every entry answered `total_input_kg`
    as zero, so `data_state` is `complete` and `production_share_percent`
    is still `null`. Before v1.51 this state did not exist and the card fell
    through to `not_supplied`'s branch, telling a visitor who typed zero on
    every row that they had typed nothing. Kills a mutation that collapses
    `undefined` into either of the other two non-complete branches."""
    page = _results_page_with_share(page_at, state="undefined")
    text = _share_card(page).inner_text()
    assert "Undefined" in text, text
    assert "You said this covered 0 kg in total, so a share of waste cannot be shown." in text, text
    assert "Not supplied" not in text
    assert "You did not say" not in text
    assert "Data incomplete" not in text
    assert not re.search(r"\d+\.\d+%", text), f"a number leaked into an undefined card: {text!r}"


@pytest.mark.browser
def test_the_money_block_shows_the_incomplete_note_on_screen(page_at):
    """The on-screen counterpart of `test_the_export_says_incomplete_for_a_
    money_figure_partial_coverage_gave_no_number` below: that test only proves
    `moneyFieldText`, never `moneySummary`'s own call site, so a card that
    ignored `data_state` and simply hid every `null` field (the pre-§4.6
    behaviour) would still pass every export test while showing a visitor an
    incomplete row exactly as if nobody had touched it at all.

    One field complete (a number, its own row), one `incomplete` (the shared
    note, its own row), one `not_supplied` (no row) - the same three-way split
    the export test below carries, read from the screen instead of the file.
    """
    response = _money_response(total="120000.00", wasted=None, share=None)
    response["totals"]["data_state"] = dict(
        response["totals"]["data_state"],
        wasted_value_nzd="incomplete",
        wasted_share_percent="not_supplied",
    )
    page = page_at(response)
    _submit_two_entries(page)

    summary = page.locator(".money-summary")
    assert summary.count() == 1, "no money section on a response carrying a total"
    text = summary.inner_text()
    assert "120,000.00" in text, f"the supplied figure is not shown: {text!r}"
    assert "Not every entry supplied this figure, so it cannot be totalled." in text, (
        f"the incomplete note is missing from the money block: {text!r}"
    )
    assert summary.locator(".money-row").count() == 2, (
        "expected one complete row and one incomplete-note row, and no row at "
        f"all for the not_supplied field: {text!r}"
    )


@pytest.mark.browser
def test_the_money_block_shows_the_undefined_note_on_screen(page_at):
    """v1.51's fourth state, on screen. Both sums answered as real zeros
    (`complete`, not withheld) print their own rows as `NZ$0.00`; the ratio
    built from them prints its own "cannot be calculated" sentence, which
    must not be the `incomplete` sentence above — a different claim about a
    different kind of gap."""
    response = _money_response(total="0.00", wasted="0.00", share=None)
    response["totals"]["data_state"] = dict(
        response["totals"]["data_state"],
        total_value_nzd="complete",
        wasted_value_nzd="complete",
        wasted_share_percent="undefined",
    )
    page = page_at(response)
    _submit_two_entries(page)

    summary = page.locator(".money-summary")
    assert summary.count() == 1, "no money section on a response carrying real zeros"
    text = summary.inner_text()
    assert "NZ$0.00" in text, f"the two zero sums are not shown: {text!r}"
    assert "The total value was zero, so this cannot be calculated." in text, (
        f"the undefined note is missing from the money block: {text!r}"
    )
    assert "Not every entry supplied this figure" not in text, text
    assert summary.locator(".money-row").count() == 3, (
        f"expected two complete rows and one undefined-note row: {text!r}"
    )


@node
def test_the_export_carries_the_money_and_the_period(tmp_path):
    """§7.3a: the file named "results" carries the results. The money figures and
    the period are on the page (the tests above); they belong in the file for the
    same reason the impact figures do.

    Driven through `buildResultsReport` directly, the way every other export
    assertion in this file is - `report_for`'s Node harness is what proves the
    string a browser download would produce, and it does so without a real
    download's platform-specific blob/`<a download>` handling, which this
    project's browser tests do not exercise anywhere else either.

    **Three figures here, not four.** `saving_nzd` was asserted in this state
    until stage three's whole-branch review, and this state is one the API cannot
    produce: §4.5 makes the saving `None` unless an entry carries an alternative,
    and the Calculate button that fills `state.result` never sends one. The
    saving's own test is below, driven from `state.improvementResult`.
    """
    #: `wasted_share_percent` (`61.11`) deliberately disagrees with what
    #: `4500 / 120000 * 100` would give (`3.75`), for the reason the browser
    #: test above gives: the export must print the response's own figure.
    state = build_state()
    state["timeFrame"] = "one_month"
    state["result"]["totals"]["money"] = {
        "total_value_nzd": "120000.00",
        "wasted_value_nzd": "4500.00",
        "wasted_share_percent": "61.11",
        "saving_nzd": None,
    }
    report = report_for(tmp_path, state)
    assert "61.11" in report and "120,000" in report and "month" in report.lower()
    assert "not wasted at all" not in report.lower(), (
        "the export advertises a saving on a calculation that carries none"
    )


@node
def test_the_saving_reaches_the_export_from_the_comparison(tmp_path):
    """**Finding 1, in the file.** The saving is written under the comparison's
    own heading, from `state.improvementResult` - the only response that can
    carry one - and never from `state.result`.

    `build_state(with_comparison=True)` puts the fixture on `improvementResult`,
    so the figure asserted here (`4,000.00`) is `tests/fixtures/calculate_
    response.json`'s own `totals.money.saving_nzd`, not a number written here.
    """
    state = build_state(with_comparison=True)
    report = report_for(tmp_path, state)
    improved = report.split("Improved scenario (Current", 1)
    assert len(improved) == 2, f"no comparison section in the export: {report}"
    assert re.search(
        r"^  - Value of food not wasted at all: NZ\$4,000\.00$", improved[1], re.M
    ), improved[1]
    #: The caveat travels with the figure, in the file as on the screen: the rate
    #: is nominal (§4.5), and a report written to be attached to an email and
    #: believed is the last place to drop the sentence that says so.
    assert "assumes an even value per kilogram" in improved[1]


@node
def test_no_saving_is_written_when_no_comparison_was_run(tmp_path):
    """The affirmative half: absent is not zero, in the export too.

    A `savingLines` that printed `NZ$0.00` - or the `NZ$Not available` a `hasValue`
    loosened to `value !== undefined` produces - would satisfy the test above and
    still tell every visitor who never pressed Compare Impact that they saved
    nothing, when what happened is that nobody asked.
    """
    report = report_for(tmp_path, build_state())
    assert "not wasted at all" not in report.lower()
    assert "NZ$Not available" not in report


@node
def test_the_export_omits_the_money_section_when_the_block_is_null(tmp_path):
    """The mutation-resistant half of the export test above: a report that
    always prints "The money" heading with "—" figures would pass the test
    above and still mislead every visitor who supplied nothing."""
    state = build_state()
    state["result"]["totals"]["money"] = None
    report = report_for(tmp_path, state)
    assert not re.search(r"^The money$", report, re.M)


@node
def test_the_export_says_incomplete_for_a_money_figure_partial_coverage_gave_no_number(tmp_path):
    """§4.6 extends the same three-state rule to `totals.money`: a field the
    engine made `null` because coverage was partial says so in words, distinct
    from `complete` (a number, on the row above) and from a field nobody
    touched at all (`not_supplied` - no row at all, on the row below). One test
    carrying all three is what proves the block tells them apart rather than
    printing the same thing, or nothing, regardless of which one it is."""
    state = build_state()
    state["result"]["totals"]["money"] = {
        "total_value_nzd": "120000.00",
        "wasted_value_nzd": None,
        "wasted_share_percent": None,
        "saving_nzd": None,
    }
    state["result"]["totals"]["data_state"] = dict(
        state["result"]["totals"]["data_state"],
        wasted_value_nzd="incomplete",
        wasted_share_percent="not_supplied",
    )
    report = report_for(tmp_path, state)
    assert re.search(r"^  - Total value of food handled: NZ\$120,000\.00$", report, re.M), report
    assert re.search(
        r"^  - Value of food wasted: Not every entry supplied this figure, so it "
        r"cannot be totalled\.$",
        report,
        re.M,
    ), report
    assert not re.search(r"^  - Share of value wasted: ", report, re.M), (
        "a field nobody touched at all printed a row: " + report
    )


# -------------------------------------------- the three surfaces agree (v1.50 review)
#
# The review that closed this task found `saving_nzd` on different terms in the
# text export and the comparison screen than in the PDF: a bare `hasValue`
# printed *nothing* for an `incomplete` submission on both browser surfaces,
# while `api/pdf_render.py::_money_rows` printed the shared "Not every entry
# supplied this figure, so it cannot be totalled." sentence — directly against
# v1.50's own change-log item 4, "the three surfaces cannot disagree about one
# submission." The tests below assert the two browser surfaces against *each
# other*, from one fixture-derived state, in one Node process — not each
# against its own expectation, which is what let the disagreement through in
# the first place.


@node
def test_the_saving_says_incomplete_in_the_export_when_the_comparison_did_not_price_every_entry(
    tmp_path,
):
    """`calculate_response_partial_coverage.json`'s `saving_nzd` is
    `incomplete` (one entry priced, one did not, both carry an alternative) —
    the state a bare `hasValue` used to render as nothing at all in the text
    export, silently disagreeing with the PDF beside it."""
    report = report_for(tmp_path, build_state_partial_coverage())
    improved = report.split("Improved scenario (Current", 1)
    assert len(improved) == 2, f"no comparison section in the export: {report}"
    assert re.search(
        r"^  - Value of food not wasted at all: Not every entry supplied this "
        r"figure, so it cannot be totalled\.$",
        improved[1],
        re.M,
    ), improved[1]
    # The nominal-rate caveat is a claim about a *figure*; the row above carries
    # a sentence instead, so the caveat must not ride along with it here the
    # way it does beside an actual number (`test_the_saving_reaches_the_export_
    # from_the_comparison` above).
    assert "assumes an even value per kilogram" not in improved[1]


@node
def test_the_text_export_and_the_comparison_screen_agree_about_an_incomplete_saving(
    tmp_path,
):
    """**The agreement test.** Built from `calculate_response_partial_
    coverage.json` through `HARNESS_BOTH`, so the text export
    (`buildResultsReport`, `web/js/results.js`) and the comparison screen's own
    markup (`ComparisonResults`, `web/js/improvement.js`) are two outputs of
    the *same* Node process reading the *same* state — not two test files each
    checking their own surface against a hand-typed expectation, which is
    exactly what let the two surfaces drift apart from each other undetected.

    **Mutation target.** Revert either `savingLines` (`results.js`) or
    `comparisonSaving` (`improvement.js`) to a bare `hasValue` check and this
    fails: the reverted surface prints nothing for `saving_nzd`, the other
    still prints the sentence, and the assertion below - which requires the
    *same* sentence in *both* outputs - catches whichever one went quiet
    without needing to know in advance which surface regressed.
    """
    both = both_surfaces_for(tmp_path, build_state_partial_coverage())
    sentence = "Not every entry supplied this figure, so it cannot be totalled."
    assert sentence in both["report"], (
        f"the text export does not carry the incomplete sentence: {both['report']!r}"
    )
    assert sentence in both["html"], (
        f"the comparison screen does not carry the incomplete sentence: {both['html']!r}"
    )
    # And neither surface is silent about the field instead - the specific
    # failure mode a bare `hasValue` produced on both of them at once.
    assert "not wasted at all" in both["report"].lower()
    assert "not wasted at all" in both["html"].lower()


# ------------------------------------------------------------- the contribute control
#
# Task 3. §6.2.2's opt-in, reached through the ordinary wizard so the token exercised
# is the one the results page actually holds - `_results_page` below is `_submit_two_
# entries` against the default fixture response, the same walk every other browser
# test in this file already drives.


def _results_page(page_at, *, contribute_calls=None, reduced_motion=None):
    """The results page, reached with `calculate_response.json` - the fixture that
    carries a real `token` (§6.2), which is what the control this section tests
    actually sends.

    `contribute_calls`, when given, is a list this appends every `/contribute`
    request body to, and the route is installed before the wizard is driven at
    all - so it catches a call made on page load or at any other point before a
    test installs its own, later route. Playwright runs the most-recently-added
    matching route first and only falls through to an earlier one if that
    handler calls `route.fallback()`, which this one never does - so a route a
    test adds afterwards, to capture the on-press request specifically, takes
    over cleanly without this counter also swallowing it.

    `reduced_motion`, when given, is forwarded to `page_at`'s own context - see
    `test_the_flower_blooms_only_when_motion_is_allowed` below for the one
    place this actually varies.
    """
    page = page_at(_fixture("calculate_response.json"), reduced_motion=reduced_motion)
    if contribute_calls is not None:
        page.route(
            "**/api/v1/contribute",
            lambda route: (contribute_calls.append(route.request.post_data_json), route.fulfill(status=204)),
        )
    _submit_two_entries(page)
    return page


@pytest.mark.browser
def test_the_results_page_offers_to_contribute_and_does_not_assume(page_at):
    """**Item ⑬ reverses a decision §2.3 wrote down deliberately**: "one
    calculation is one submission... there is no consent checkbox and no
    separate contribute button". The client asked for exactly the thing that
    sentence excluded.

    So the control starts UNCHECKED. A pre-ticked box is not consent, and a
    default of opted-in would make stage one's `is_public_contributed`
    decorative.
    """
    page = _results_page(page_at)

    control = page.locator("#contribute")
    assert control.count() == 1, "the results page does not offer to contribute"
    assert control.is_checked() is False, (
        "the contribution control is pre-ticked, which is not a choice"
    )

    label = page.locator('label[for="contribute"]').inner_text()
    assert label.strip(), "the control has no label"


@pytest.mark.browser
def test_ticking_it_posts_the_token(page_at):
    """The request the button exists to make - and, first, the half that is easy
    to lose: that nothing is sent before the box is pressed. A control that
    calls `contribute()` on page load would still make this request eventually
    and could still pass a version of this test that only checked the request's
    shape once it arrived; the count below is what actually distinguishes "sent
    on the press" from "sent regardless, and the press did nothing new"."""
    early = []
    page = _results_page(page_at, contribute_calls=early)
    assert not early, "a request to /contribute was made before the box was ever pressed"

    sent = {}
    page.route(
        "**/api/v1/contribute",
        lambda route: (sent.update(route.request.post_data_json), route.fulfill(status=204)),
    )

    page.locator("#contribute").check()
    page.wait_for_timeout(400)

    assert sent.get("token"), "no token was sent, so no row can be found to update"


@pytest.mark.browser
def test_the_page_says_what_contributing_means_before_it_is_clicked(page_at):
    """The public statistics are the client's own page, and what goes into
    them is a decision a visitor should be able to make informed. The sentence
    beside the control says what is contributed - an anonymous calculation -
    and what is not."""
    page = _results_page(page_at)

    text = page.locator(".contribute-block").inner_text().lower()
    assert "anonymous" in text or "no personal" in text or "nothing that identifies" in text


@pytest.mark.browser
def test_the_tick_does_not_visibly_undo_itself_while_the_request_is_in_flight(page_at):
    """**Fix round 1, must-fix 1.** `contributeBlock` keyed `checked` on `done` and
    `disabled` on `pending || done`, so for the whole time the request was in flight
    the box read back unchecked *and* disabled - a visitor ticks a consent box and
    watches it come back empty. The route below is held open rather than fulfilled,
    so this measures the box mid-flight rather than after an answer has arrived.
    """
    page = _results_page(page_at)
    held = {}
    page.route("**/api/v1/contribute", lambda route: held.setdefault("route", route))

    page.locator("#contribute").check()
    page.wait_for_timeout(300)

    control = page.locator("#contribute")
    assert control.is_checked() is True, (
        "the box is unticked while the request is still in flight"
    )
    assert control.is_disabled() is True, (
        "the box is not yet locked while the request is still in flight"
    )

    held["route"].fulfill(status=204)


@pytest.mark.browser
def test_the_confirmation_is_present_tense_and_names_no_past_success(page_at):
    """**Fix round 1, must-fix 2.** §6.2.2 answers `204` identically whether the
    token named a live submission or was unknown / already expired - "nothing is
    written and nothing is said" is the contract's own wording. "...has been added"
    asserts a past event this page cannot actually confirm; the replacement states
    the present, true-on-every-press fact instead, which is also true again after a
    recalculation without needing to know whether this was the first press or not.
    """
    page = _results_page(page_at)
    page.route("**/api/v1/contribute", lambda route: route.fulfill(status=204))

    page.locator("#contribute").check()
    page.wait_for_timeout(400)

    status = page.locator(".contribute-status").inner_text().lower()
    assert "has been added" not in status, (
        "the confirmation still claims a past success the front end cannot verify"
    )
    assert "are in" in status or "is in" in status or "public statistics" in status


@pytest.mark.browser
def test_the_control_is_described_for_a_visitor_who_cannot_see_the_sentence(page_at):
    """**Fix round 1, fix 4.** A visitor tabbing to `#contribute` without sight
    hears its label and nothing else unless the control is described by the
    sentence that says what contributing means - the whole point of item 13's
    "read before you click" requirement, for someone who cannot read the page
    layout to find that sentence unaided."""
    page = _results_page(page_at)

    control = page.locator("#contribute")
    described_by = control.get_attribute("aria-describedby")
    assert described_by, "the control has no aria-describedby at all"

    described_text = page.locator(f"#{described_by}").inner_text().lower()
    assert "anonymous" in described_text, (
        "aria-describedby does not point at the sentence explaining the choice"
    )


# ------------------------------------------------------- the invitation (Task 4)
#
# The client's ask was a long rounded button rather than a tick, "a bit cuter",
# and perhaps a small flower animation on press. The three tests above this
# banner - unticked by default, described before the click, an explicit
# aria-describedby - all still pass unchanged against whatever markup this
# section builds, because the id `#contribute`, the `label[for="contribute"]`
# association and `.contribute-block`'s own text never move. What follows is
# new ground: an explicit `aria-checked` alongside the control's native
# semantics, a checked-state mark that does not rely on colour alone, and the
# flower itself, gated on `prefers-reduced-motion`.


@pytest.mark.browser
def test_the_control_reports_an_explicit_aria_checked_state(page_at):
    """The brief's own wording: "keep a real checkbox or switch role, and
    `aria-checked`". A native `input[type=checkbox]` already exposes its
    checked state to the accessibility tree through the `checked` property
    alone, so this is not asserting the control is *readable* - the existing
    `test_the_results_page_offers_to_contribute_and_does_not_assume` already
    covers that with `is_checked()`. It asserts the explicit attribute the
    brief calls for is present *and tracks the same state*, which a control
    that set it once at render time and never updated it would fail.
    """
    page = _results_page(page_at)
    page.route("**/api/v1/contribute", lambda route: route.fulfill(status=204))

    control = page.locator("#contribute")
    assert control.get_attribute("aria-checked") == "false", (
        "the control has no aria-checked attribute, or it is not false before the press"
    )

    control.check()
    page.wait_for_timeout(400)

    assert control.get_attribute("aria-checked") == "true", (
        "aria-checked did not move to true once the control was ticked"
    )


@pytest.mark.browser
def test_the_checked_state_is_marked_by_more_than_colour(page_at):
    """"A button that looks the same pressed and unpressed is worse than the
    checkbox it replaced" - so this measures a *non-colour* property of the
    control's own state mark before and after the press, the same way
    `test_site_drawer.py` measures its chevron's rotation rather than trusting
    a colour token to have changed. A mutation that left only a background
    colour switching between the two states passes every other test in this
    file and fails this one.
    """
    page = _results_page(page_at)
    page.route("**/api/v1/contribute", lambda route: route.fulfill(status=204))

    read_mark = (
        "() => { const mark = document.querySelector('.contribute-toggle__mark');"
        " const after = getComputedStyle(mark, '::after');"
        " return after.content + '|' + after.borderStyle + '|' + mark.className; }"
    )
    before = page.evaluate(read_mark)

    page.locator("#contribute").check()
    page.wait_for_timeout(400)

    after = page.evaluate(read_mark)
    assert before != after, (
        "the control's state mark reads identically before and after the "
        f"press ({before!r}); only colour would then distinguish the two states"
    )


@pytest.mark.browser
def test_the_flower_blooms_only_when_motion_is_allowed(page_at):
    """The client's own ask - "perhaps a small flower animation... when it is
    pressed" - and the one non-negotiable beside it: it must not run under
    `prefers-reduced-motion: reduce`, and the control's own state change must
    be complete and visible without it.

    Both halves live in one test rather than two. A suite that only asserted
    the reduced-motion half would pass equally against an implementation that
    never grew a flower at all - asserting the element DOES appear under
    ordinary motion first is what makes the reduced-motion assertion below
    mean "suppressed" rather than "never built".
    """
    ordinary = _results_page(page_at, reduced_motion="no-preference")
    ordinary.route("**/api/v1/contribute", lambda route: route.fulfill(status=204))
    ordinary.locator("#contribute").check()
    ordinary.wait_for_timeout(300)
    assert ordinary.locator(".contribute-flower").count() >= 1, (
        "no flower ever appears, even with motion allowed - the reduced-motion "
        "assertion below would prove nothing"
    )

    reduced = _results_page(page_at, reduced_motion="reduce")
    reduced.route("**/api/v1/contribute", lambda route: route.fulfill(status=204))
    reduced.locator("#contribute").check()
    reduced.wait_for_timeout(300)
    assert reduced.locator(".contribute-flower").count() == 0, (
        "the flower animation element is present in the DOM under "
        "prefers-reduced-motion: reduce"
    )

    # The state change itself does not depend on the animation having played.
    assert reduced.locator("#contribute").is_checked() is True
    assert reduced.locator(".contribute-status").count() == 1, (
        "the contributed state is not fully conveyed without the animation"
    )


@pytest.mark.browser
def test_the_flower_does_not_bloom_over_a_failed_contribute(page_at):
    """The brief: the flower plays on the transition INTO the contributed state
    and never on the way out. Nothing above drives a failed `/contribute` at
    all, so nothing constrained which branch of `contributeCalculation`
    (`results.js`) is allowed to set `contributeCelebrating` - a version that
    set it in the `catch` too, alongside dropping the `done &&` half of
    `celebrate`'s guard, left every other test in this file green: a failed
    press still leaves `checked=False`, so `test_the_results_page_offers_to_
    contribute_and_does_not_assume`'s "starts unticked" reads exactly the same
    whether or not a flower bloomed on the way there.

    A failed contribute must snap the control back to its unticked, unpressed
    state; a flower blooming over that is celebrating a consent that was never
    recorded.
    """
    page = _results_page(page_at)
    page.route("**/api/v1/contribute", lambda route: route.fulfill(status=500))

    # `.click()` rather than `.check()`: the box settles back to unticked once the
    # failure lands, so `.check()`'s own "ends up checked" postcondition would retry
    # the click forever and time out - the failure path is exactly what this test
    # means to drive.
    page.locator("#contribute").click()
    page.wait_for_timeout(400)

    assert page.locator("#contribute").is_checked() is False, (
        "the control still reads ticked after the contribute request failed"
    )
    assert page.locator(".contribute-flower").count() == 0, (
        "the flower bloomed over a contribute that failed and snapped back to unticked"
    )


@pytest.mark.browser
def test_the_control_shows_a_focus_ring_when_tabbed_to(page_at):
    """**Required fix.** `#contribute` is `opacity: 0` (see `styles.css`'s own
    note over `.contribute-control input[type="checkbox"]`) so the ring the
    top-of-file `:focus-visible { outline }` rule draws on the input itself is
    real but painted on nothing anybody can see - the parent's full-opacity
    20px native checkbox had a visible ring; this pill did not. The fix draws
    it on the sibling pill instead, keyed off the input's own `:focus-visible`
    state (`.contribute-control input:focus-visible + .contribute-toggle`).

    `.focus()` does not exercise this: Chromium only turns `:focus-visible` on
    for a genuine keyboard walk, not a script calling `.focus()` on an element
    directly (confirmed against this exact page before writing this test), so
    the walk below is a real `Tab` from the skip link - the same construction
    `test_the_capsule_shows_a_focus_ring_when_the_control_is_tabbed_to`
    (`test_i18n_browser.py`) uses for the language chooser. The assertion is a
    screenshot difference rather than a check that some CSS rule exists,
    because a rule that exists but targets the wrong element, or draws an
    outline `opacity: 0` still swallows, would satisfy the latter and fail a
    real keyboard visitor exactly as before this fix.
    """
    page = _results_page(page_at)

    resting = page.locator(".contribute-control").screenshot()

    page.focus(".skip-link")
    reached = False
    for _ in range(40):
        page.keyboard.press("Tab")
        if page.evaluate("() => document.activeElement?.id") == "contribute":
            reached = True
            break
    assert reached, "forty Tabs from the top of the page never reached #contribute"
    assert page.evaluate(
        "() => document.querySelector('#contribute').matches(':focus-visible')"
    ) is True, "the control was reached but Chromium does not consider it focus-visible"

    focused = page.locator(".contribute-control").screenshot()
    assert focused != resting, (
        "tabbing to the contribute control paints no different pixels - there is no "
        "visible focus indicator on the one control that records a consent"
    )


#: 320, 390, 700, 938 and 1278 - the plan's own five widths - checked in German,
#: the longest of the twenty catalogues shipped. Mirrors
#: `DOWNLOAD_LAYOUT_WIDTHS`/`test_neither_download_button_overflows_in_german`
#: below, applied to the one other piece of layout this task touches.
CONTRIBUTE_LAYOUT_WIDTHS = (320, 390, 700, 938, 1278)


@pytest.mark.browser
@pytest.mark.parametrize("width", CONTRIBUTE_LAYOUT_WIDTHS)
def test_the_contribute_button_does_not_overflow_in_german(browser, width):
    """A "long rounded button" is exactly the shape that breaks first at a
    narrow width - German is this catalogue set's longest language and the one
    that builds unbreakable compounds (`test_horizontal_overflow.py`'s own
    reasoning), so its label is what a real button has to accommodate rather
    than English's shorter one.
    """
    context = browser.new_context(
        viewport={"width": width, "height": 900},
        locale="de",
        extra_http_headers={"Accept-Language": "de,en;q=0.5"},
        bypass_csp=True,
    )
    try:
        page = context.new_page()
        page.route(
            "**/api/v1/calculate*",
            lambda route: route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps(_fixture("calculate_response.json")),
            ),
        )
        try:
            page.goto(CALCULATOR_URL, wait_until="networkidle", timeout=15000)
        except Exception as error:  # pragma: no cover - environment guard
            pytest.skip(f"the front end is not being served at {CALCULATOR_URL}: {error}")
        page.add_style_tag(content=FORCE_AUTO_SCROLL)
        page.wait_for_selector('[data-action="start"]', timeout=10000)
        _submit_two_entries(page)

        de = page.evaluate(
            "() => ({scroll: document.documentElement.scrollWidth, "
            "client: document.documentElement.clientWidth})"
        )
        assert de["scroll"] <= max(de["client"], 320), (
            f"the page scrolls sideways at {width}px in German: "
            f"scrollWidth={de['scroll']} clientWidth={de['client']}"
        )

        toggle = page.locator(".contribute-toggle")
        box = toggle.bounding_box()
        assert box is not None, f"the contribute button has no box at {width}px"
        assert box["x"] + box["width"] <= de["client"] + 1, (
            f"the contribute button overflows its own viewport at {width}px in German: {box}"
        )
    finally:
        context.close()


# ---------------------------------------------------------------- the PDF export
#
# Task 5. `POST /api/v1/export/pdf` already works (`api/export.py`, `api/router.py`); this
# is the button that reaches it, beside the plain-text download rather than replacing it.
# Both tests assert the outgoing request rather than only a download having fired - a
# button pointed at the wrong path, or one that drops the locale, would still trigger a
# download and still pass a check that stopped at "something downloaded".

PDF_BYTES = b"%PDF-1.4\n%mock pdf body\n"


def _fulfil_pdf(route, calls):
    calls.append(
        {
            "method": route.request.method,
            "url": route.request.url,
            "body": route.request.post_data_json,
        }
    )
    route.fulfill(status=200, content_type="application/pdf", body=PDF_BYTES)


@pytest.mark.browser
def test_the_pdf_button_posts_the_export_route_carrying_the_locale(page_at):
    """The request the button exists to make.

    `page_at` opens with `?lang=en` (see its own docstring), so `en` is the tag
    `activeLanguage()` holds when the click happens - the export endpoint has no other way
    to learn which language the visitor is reading (`api/export.py`'s own docstring).
    """
    page = page_at(_fixture("calculate_response.json"))
    calls = []
    page.route("**/api/v1/export/pdf", lambda route: _fulfil_pdf(route, calls))
    _submit_two_entries(page)

    button = page.locator('[data-action="download-pdf"]')
    assert button.count() == 1, "no PDF button on the results page"
    assert button.is_visible(), "the PDF button is present but not visible"

    with page.expect_download() as download_info:
        button.click()
    download = download_info.value

    assert len(calls) == 1, f"expected exactly one request to /export/pdf, got {calls}"
    call = calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/api/v1/export/pdf"), call["url"]

    body = call["body"] or {}
    assert body.get("locale") == "en", f"the visitor's locale did not reach the request: {body}"
    assert body.get("entries"), f"the request carries no entries: {body}"
    #: §2.3/`api/export.py`: the export route persists nothing and `ExportPayload` has no
    #: field for a token at all - a request that carried one would be a 422, not a document.
    assert "token" not in body, f"the export request carries a token it has no use for: {body}"

    assert download.suggested_filename.endswith(".pdf"), download.suggested_filename


@pytest.mark.browser
def test_both_downloads_are_offered_and_each_produces_its_own_format(page_at):
    """Both formats, one screen - and the regression this file exists to hold shut.

    PR #46 did not add a PDF button beside the text one; it **replaced the text
    download's implementation**, turning the `text/plain` blob into a hand-rolled PDF.
    Merging that branch reintroduces the removal, and a check that stopped at "two
    buttons are present" would pass against two buttons emitting the same document.

    So each button is driven separately and its *output* is asserted:

    - the text button downloads a `.txt` whose bytes are `buildResultsReport`'s report,
      built in the browser - and makes **no** request to the export route;
    - the PDF button posts to `/api/v1/export/pdf` and downloads what the server answered.

    Both are asserted enabled, not merely visible: `downloadPdf` disables its own button
    while a request is in flight, and a permanently disabled control is present, visible
    and useless.
    """
    page = page_at(_fixture("calculate_response.json"))
    calls = []
    page.route("**/api/v1/export/pdf", lambda route: _fulfil_pdf(route, calls))
    _submit_two_entries(page)

    text_button = page.locator('[data-action="download-results"]')
    pdf_button = page.locator('[data-action="download-pdf"]')
    assert text_button.count() == 1, "the plain-text download is gone from the results page"
    assert pdf_button.count() == 1, "no PDF button on the results page"
    assert text_button.is_visible() and text_button.is_enabled()
    assert pdf_button.is_visible() and pdf_button.is_enabled()

    with page.expect_download() as text_info:
        text_button.click()
    text_download = text_info.value
    assert text_download.suggested_filename.endswith(".txt"), text_download.suggested_filename
    text_bytes = Path(text_download.path()).read_bytes()
    #: The report itself, not a PDF wearing a `.txt` name - the figures reach the file and
    #: the mock-data notice travels with them (§7.6.2).
    assert not text_bytes.startswith(b"%PDF"), "the text button produced a PDF"
    text = text_bytes.decode("utf-8")
    assert "Impact summary" in text, text[:400]
    assert "4,449.0 kg CO2e" in text, text[:400]
    assert NOTICE in text, text[:400]
    #: Built in the browser from state already held; the export route is the PDF's alone.
    assert calls == [], f"the text download called the PDF export route: {calls}"

    with page.expect_download() as pdf_info:
        pdf_button.click()
    pdf_download = pdf_info.value
    assert pdf_download.suggested_filename.endswith(".pdf"), pdf_download.suggested_filename
    assert Path(pdf_download.path()).read_bytes() == PDF_BYTES
    assert len(calls) == 1, f"expected one request to /export/pdf, got {calls}"
    assert calls[0]["method"] == "POST"

    #: Two names, so a visitor who takes both does not overwrite one with the other.
    assert text_download.suggested_filename != pdf_download.suggested_filename


# ------------------------------------------------- the two buttons, and the PDF's own name
#
# Task 3, the plan of 2026-08-31. Two client complaints landing in one place: the text
# download was the step-nav's own primary action and the PDF a secondary afterthought in
# `.result-actions`, so the pair did not read as a pair; and the PDF's file name was the
# fixed `kai-commitment-impact-calculator.pdf`, so a second download became `... (1).pdf`.


@node
def test_the_shared_helper_stamps_a_pdf_name_from_the_same_clock_as_the_text_export(tmp_path):
    """`exportFilename` is the one place a timestamp is turned into a file name -
    `docs`'s own reasoning for the text export's stamp - so the PDF has to be
    stamped by a call to the *same* function, not a second implementation of the
    same date arithmetic.

    Two calls at the same fixed instant, one for each extension, carry the same
    date-and-time stamp; only the extension (and, for the PDF, an added
    identifier - see the distinctness tests below) differs. The text call is the
    exact call `downloadResults` already made before this task, so this also
    guards the existing convention against a change made in passing while the
    PDF gains its own.
    """
    harness = tmp_path / "harness.mjs"
    harness.write_text(
        """
        globalThis.window = { location: { search: '' } }
        globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} }
        import { writeFileSync } from 'node:fs'
        const { exportFilename } = await import(process.argv[2])
        const fixed = new Date(2026, 7, 12, 9, 4, 5)
        const names = {
          text: exportFilename(fixed),
          pdfOne: exportFilename(fixed, { ext: 'pdf', unique: 'aaaaaaaa' }),
          pdfTwo: exportFilename(fixed, { ext: 'pdf', unique: 'bbbbbbbb' }),
        }
        writeFileSync(process.argv[3], JSON.stringify(names), 'utf8')
        """,
        encoding="utf-8",
    )
    out = tmp_path / "names.json"
    completed = subprocess.run(
        [shutil.which("node"), str(harness), RESULTS_JS.as_uri(), str(out)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert completed.returncode == 0, (
        f"node could not build the file names:\n{completed.stdout}\n{completed.stderr}"
    )
    names = json.loads(out.read_text(encoding="utf-8"))

    #: The existing text convention, unchanged by the PDF gaining one.
    assert names["text"] == "food-waste-impact-results-2026-08-12-090405.txt", names
    #: The PDF carries the identical stamp, plus the identifier that tells two
    #: PDFs taken in the same second apart.
    assert names["pdfOne"] == "food-waste-impact-results-2026-08-12-090405-aaaaaaaa.pdf", names
    assert names["pdfTwo"] == "food-waste-impact-results-2026-08-12-090405-bbbbbbbb.pdf", names


def test_the_pdf_no_longer_hard_codes_one_file_name():
    """The regression this task exists to close: a constant name is what turned
    every second PDF download into `... (1).pdf`.

    Both downloads' `link.download` are asserted set from `exportFilename` -
    counting the call sites rather than grepping for either one alone, because a
    file that kept the old constant *beside* a new call would satisfy a
    presence check while the PDF still had a fixed name available to fall back
    to.
    """
    source = RESULTS_JS.read_text(encoding="utf-8")
    assert "kai-commitment-impact-calculator.pdf" not in source, (
        "the PDF still carries a constant file name somewhere in the file"
    )
    calls = re.findall(r"link\.download\s*=\s*exportFilename\(", source)
    assert len(calls) == 2, (
        f"expected the text and PDF downloads to both set `link.download` from "
        f"`exportFilename`, found {len(calls)} such call(s)"
    )


#: `food-waste-impact-results-2026-08-30-213033`, less its extension - the shape both
#: downloads must share, the PDF with an identifier appended before its own extension.
STAMP_PATTERN = r"food-waste-impact-results-\d{4}-\d{2}-\d{2}-\d{6}"


@pytest.mark.browser
def test_the_two_downloads_sit_together_as_one_choice(page_at):
    """**Complaint 1.** The text download used to be the step-nav's own primary
    action, reachable nowhere near the PDF button in `.result-actions` - a
    visitor reading the page saw one action and, below it, an afterthought, and
    could not find the text export among the sentence-shaped step navigation at
    all. The two now sit in one container as two buttons of equal visual
    weight, so the page reads as a choice of format rather than an action plus
    an extra.
    """
    page = page_at(_fixture("calculate_response.json"))
    calls = []
    page.route("**/api/v1/export/pdf", lambda route: _fulfil_pdf(route, calls))
    _submit_two_entries(page)

    text_button = page.locator('[data-action="download-results"]')
    pdf_button = page.locator('[data-action="download-pdf"]')
    assert text_button.count() == 1, "the text download is missing from the results page"
    assert pdf_button.count() == 1, "the PDF download is missing from the results page"

    #: The step-nav keeps only its back action - the primary slot the text
    #: download used to occupy is gone, not merely relabelled.
    assert page.locator('.step-nav [data-action="download-results"]').count() == 0, (
        "the text download is still living inside the step navigation"
    )
    assert page.locator('.step-nav [data-action="go-step"]').count() == 1, (
        "the step-nav's own back action moved when it was not supposed to"
    )

    #: One container holds both. A single `page.evaluate` reads both elements out of the
    #: live DOM in one call, rather than comparing two locators' handles across separate
    #: round trips - which is not the same node identity check it looks like.
    same_parent = page.evaluate(
        """() => {
            const text = document.querySelector('[data-action="download-results"]');
            const pdf = document.querySelector('[data-action="download-pdf"]');
            return !!text && !!pdf && text.parentElement === pdf.parentElement;
        }"""
    )
    assert same_parent, "the two downloads do not share a parent element"

    #: Equal weight - the same button styling, not one primary and one secondary.
    text_classes = set((text_button.get_attribute("class") or "").split())
    pdf_classes = set((pdf_button.get_attribute("class") or "").split())
    assert text_classes == pdf_classes, (
        f"the two downloads are not styled as equals: {text_classes} vs {pdf_classes}"
    )
    assert "button-secondary" not in text_classes or "button-primary" not in pdf_classes, (
        "one button reads as primary and the other secondary"
    )


@pytest.mark.browser
def test_two_pdf_downloads_in_the_same_frozen_second_still_get_distinct_names(page_at):
    """**Complaint 2, made deliberately non-flaky.** `exportFilename`'s stamp has
    one-second resolution, so two downloads taken in quick succession would
    only prove distinctness by luck - passing when the clicks happen to straddle
    a second boundary and failing, or worse, silently agreeing, when they land
    inside the same one. That is exactly the "timestamp alone" version of this
    fix the client did not ask for: they asked for a timestamp *and* something
    unique per file.

    So the browser's own clock is frozen to one instant before either click.
    Both downloads are therefore built from the identical timestamp; if the
    file name depended on the timestamp alone the two names would be identical
    outright, which is what the assertion below actually tests for - not "two
    downloads happened to differ" but "two downloads forced onto the same
    second still differ", which only a genuinely unique identifier can produce.
    """
    page = page_at(_fixture("calculate_response.json"))
    calls = []
    page.route("**/api/v1/export/pdf", lambda route: _fulfil_pdf(route, calls))
    _submit_two_entries(page)

    page.clock.set_fixed_time(datetime(2026, 8, 30, 21, 30, 33))

    pdf_button = page.locator('[data-action="download-pdf"]')

    with page.expect_download() as first_info:
        pdf_button.click()
    first_name = first_info.value.suggested_filename

    #: `downloadPdf` disables the button for the length of the request; the
    #: second click has to wait for it, exactly as a visitor's second press
    #: would have to.
    page.wait_for_selector('[data-action="download-pdf"]:not([disabled])', timeout=10000)

    with page.expect_download() as second_info:
        pdf_button.click()
    second_name = second_info.value.suggested_filename

    assert len(calls) == 2, f"expected two requests to /export/pdf, got {calls}"

    first_stamp = re.match(STAMP_PATTERN, first_name)
    second_stamp = re.match(STAMP_PATTERN, second_name)
    assert first_stamp and second_stamp, (first_name, second_name)
    assert first_stamp.group() == second_stamp.group(), (
        "the clock was not actually frozen for both downloads - the test proves "
        f"nothing about the same-second case: {first_name!r} vs {second_name!r}"
    )

    assert first_name != second_name, (
        "two PDF downloads taken in the same frozen second produced the same "
        f"file name: {first_name!r}"
    )


@pytest.mark.browser
def test_the_pdf_filename_follows_the_same_convention_as_the_text_export(page_at):
    """The stamp itself - not merely that the two names differ from each other,
    but that the PDF's name is built the way the text export's already was:
    the same sortable date-and-time prefix, with the PDF's own identifier and
    extension after it rather than before or in place of the stamp."""
    page = page_at(_fixture("calculate_response.json"))
    calls = []
    page.route("**/api/v1/export/pdf", lambda route: _fulfil_pdf(route, calls))
    _submit_two_entries(page)

    with page.expect_download() as text_info:
        page.locator('[data-action="download-results"]').click()
    text_name = text_info.value.suggested_filename

    with page.expect_download() as pdf_info:
        page.locator('[data-action="download-pdf"]').click()
    pdf_name = pdf_info.value.suggested_filename

    assert re.fullmatch(STAMP_PATTERN + r"\.txt", text_name), text_name
    assert re.fullmatch(STAMP_PATTERN + r"-[0-9a-z]{6,10}\.pdf", pdf_name), pdf_name


#: 320, 390, 700, 938 and 1278 - the plan's own five widths - checked in German, the
#: longest of the twenty catalogues shipped, per `tests/web/test_horizontal_overflow.py`'s
#: own reasoning for including it.
DOWNLOAD_LAYOUT_WIDTHS = (320, 390, 700, 938, 1278)


@pytest.mark.browser
@pytest.mark.parametrize("width", DOWNLOAD_LAYOUT_WIDTHS)
def test_neither_download_button_overflows_in_german(browser, width):
    """Measured, not reasoned about - `test_horizontal_overflow.py`'s own rule,
    applied to the one change this task makes to the page's layout. German is
    checked because it is this catalogue set's longest language and the one
    that builds unbreakable compounds; a button pair that wraps rather than
    overflows at a narrow width is a pass, the same allowance
    `test_horizontal_overflow.py` makes for `.result-actions` already wrapping.
    """
    context = browser.new_context(
        viewport={"width": width, "height": 900},
        locale="de",
        extra_http_headers={"Accept-Language": "de,en;q=0.5"},
        bypass_csp=True,
    )
    try:
        page = context.new_page()
        page.route(
            "**/api/v1/calculate*",
            lambda route: route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps(_fixture("calculate_response.json")),
            ),
        )
        try:
            page.goto(CALCULATOR_URL, wait_until="networkidle", timeout=15000)
        except Exception as error:  # pragma: no cover - environment guard
            pytest.skip(f"the front end is not being served at {CALCULATOR_URL}: {error}")
        page.add_style_tag(content=FORCE_AUTO_SCROLL)
        page.wait_for_selector('[data-action="start"]', timeout=10000)
        _submit_two_entries(page)

        de = page.evaluate(
            "() => ({scroll: document.documentElement.scrollWidth, "
            "client: document.documentElement.clientWidth})"
        )
        #: The same 320px floor `test_horizontal_overflow.py` applies: below that,
        #: the interface stops reflowing by design and the page is 320px wide on
        #: purpose rather than by defect.
        assert de["scroll"] <= max(de["client"], 320), (
            f"the page scrolls sideways at {width}px in German: "
            f"scrollWidth={de['scroll']} clientWidth={de['client']}"
        )

        for action in ("download-results", "download-pdf"):
            button = page.locator(f'[data-action="{action}"]')
            box = button.bounding_box()
            assert box is not None, f"{action} has no box at {width}px"
            assert box["x"] + box["width"] <= de["client"] + 1, (
                f"{action} overflows its own viewport at {width}px in German: {box}"
            )
    finally:
        context.close()


# --------------------------------------------------------------- the equivalence disclosure
#
# Task 6. Every equivalence carries a question mark that opens onto how it was worked
# out (contract v1.52, Task 3). Browser tests never reach the real engine - `page_at`
# fulfils `/api/v1/calculate` in-browser - so the factor and basis text asserted below
# come from the response THIS FILE builds, modelled on the client's own "vehicles_year"
# conversion (Task 4), not from `tests/fixtures/*.json`'s `km_driven` row, which is still
# on the old `4.1800000000` factor.


def _equivalence_response(*, source_note, is_mock: bool = True):
    """A deep copy of `calculate_response.json` with its one equivalence replaced.

    `co2e`'s total in that fixture is `4449.0000000000` at one decimal place
    (`kg CO2e`), so `equivalenceBasis` has a real metric to read `source_metric`
    against. The factor itself - `0.0004149378`, displayed as `0.000414938` - and
    the source note are copied verbatim from the client's document via
    `data/upstream-factors-draft/build_upstream_factors_draft.py`'s `vehicles_year`
    row, not retyped from memory.

    `is_mock` defaults to the fixture's own `True` - the disclaimer beside the
    basis is gated on it (`equivalenceBasis`, `web/js/results.js`), the same
    gate `_EQUIVALENCE_DISCLAIMER` enforces in the PDF, so a caller can flip
    it to prove the negative case without hand-building a whole response.
    """
    response = copy.deepcopy(_fixture("calculate_response.json"))
    response["factor_set"] = dict(response["factor_set"], is_mock=is_mock)
    response["totals"]["current"]["equivalences"] = [
        {
            "code": "vehicles_year",
            "name": "Passenger vehicles for a year",
            "label": "Equivalent to running 1 passenger vehicles for a year",
            "value": "1.0000000000",
            "value_per_unit": "0.0004149378",
            "value_per_unit_display": "0.000414938",
            "source_metric": "co2e",
            "source_note": source_note,
        }
    ]
    return response


#: Copied verbatim from `data/upstream-factors-draft/upstream_factors_draft.json`'s
#: `vehicles_year` row - not retyped, so a transcription slip cannot make this test
#: pass against text the client never approved.
_VEHICLE_SOURCE_NOTE = (
    "Client, Data sources for impact calculator (2026-08-29): "
    "\"Passenger vehicles on the road: GHG emissions (t CO2e) / "
    "2.41 (t CO2e/passenger vehicle/year)\". Applied per kilogram, "
    "so the divisor here is 2,410."
)


@pytest.fixture
def results_page(page_at):
    """The results page, reached with one equivalence that carries a recorded basis."""
    page = page_at(_equivalence_response(source_note=_VEHICLE_SOURCE_NOTE))
    _submit_two_entries(page)
    return page


@pytest.fixture
def results_page_without_basis(page_at):
    """O-3 is open and `source_note` is nullable - the same equivalence, unrecorded."""
    page = page_at(_equivalence_response(source_note=None))
    _submit_two_entries(page)
    return page


@pytest.fixture
def results_page_on_a_real_factor_set(page_at):
    """The negative case for the equivalence disclaimer - L52. Only the PDF
    (`tests/api/test_pdf_render.py::test_a_real_factor_set_carries_no_
    warning`) had a test proving the gate holds in the direction that matters
    most: a real, published factor set once described itself as
    "placeholder" and the mistake shipped. The page and the text export were
    asserted only with `is_mock` true."""
    page = page_at(_equivalence_response(source_note=_VEHICLE_SOURCE_NOTE, is_mock=False))
    _submit_two_entries(page)
    return page


@pytest.mark.browser
def test_each_equivalence_offers_an_explanation_that_starts_closed(results_page):
    """A long explanation beside every figure is what the client asked to be
    spared; the disclosure is closed until asked for."""
    rows = results_page.locator(".equivalent-grid article")
    assert rows.count() > 0
    for i in range(rows.count()):
        details = rows.nth(i).locator("details.equivalent-basis")
        assert details.count() == 1
        assert details.get_attribute("open") is None


@pytest.mark.browser
def test_the_explanation_shows_the_total_the_factor_and_the_basis(results_page):
    """Asserts what a person can SEE, not that the text is in the DOM. A closed
    <details> still contains its text -- that is exactly how a folded warning
    passed tests/admin/test_guidance.py once already."""
    first = results_page.locator(".equivalent-grid article").first
    body = first.locator(".equivalent-basis__body")
    assert not body.is_visible()
    first.locator("details.equivalent-basis > summary").click()
    assert body.is_visible()
    assert "0.000414938" in body.inner_text()
    assert "Client, Data sources for impact calculator" in body.inner_text()


@pytest.mark.browser
def test_the_last_row_shows_the_figure_and_does_not_repeat_the_whole_sentence(results_page):
    """The final review's own finding: this row used to render `row.name` /
    `= row.label` -- `Passenger vehicles for a year = Equivalent to running 1
    passenger vehicles for a year` -- restating the whole heading sentence
    where a reader following the arithmetic (`Total` x `Per unit` = ?) expects
    the answer. It now shows just the figure the equivalence rounds to,
    `formatNumber(row.value, 0)` -- `1`, for this fixture's `vehicles_year`
    row (`_equivalence_response`'s `"value": "1.0000000000"`) -- and the
    sentence itself is not repeated a second time in this row."""
    first = results_page.locator(".equivalent-grid article").first
    first.locator("details.equivalent-basis > summary").click()
    rows = first.locator(".equivalent-basis__body dl > div")
    last_row = rows.nth(rows.count() - 1)
    assert last_row.locator("dt").inner_text() == "Passenger vehicles for a year"
    assert last_row.locator("dd").inner_text() == "= 1"


@pytest.mark.browser
def test_a_real_factor_set_carries_no_equivalence_warning(results_page_on_a_real_factor_set):
    """The negative case, on the page: the factor and the basis still show,
    but the sentence that says the total comes from placeholder factors does
    not, because the factor set is not one."""
    first = results_page_on_a_real_factor_set.locator(".equivalent-grid article").first
    first.locator("details.equivalent-basis > summary").click()
    body = first.locator(".equivalent-basis__body").inner_text()
    assert "0.000414938" in body
    assert "Client, Data sources for impact calculator" in body
    assert "placeholder factors" not in body


@pytest.mark.browser
def test_an_equivalence_with_no_basis_says_so_rather_than_opening_onto_nothing(
    results_page_without_basis,
):
    """O-3 is open and source_note is nullable. A question mark that opens onto
    nothing is worse than no question mark."""
    first = results_page_without_basis.locator(".equivalent-grid article").first
    first.locator("details.equivalent-basis > summary").click()
    body = first.locator(".equivalent-basis__body")
    assert body.is_visible()
    assert "0.000414938" in body.inner_text()
    assert "not recorded" in body.inner_text().lower()


def _extract_pdf_text(path) -> str:
    """The same normalisation `tests/support/pdf.py::extract_text` applies,
    read off a file the browser downloaded rather than in-process bytes -
    this suite drives a real browser against the running container, so there
    is no in-process PDF to hand the shared helper directly."""
    from tests.support.pdf import extract_text

    return extract_text(Path(path).read_bytes())


def _submit_two_entries_of_different_sectors(page):
    """The same wizard walk `_submit_two_entries` does, except the two entries
    choose different sectors.

    Only this test needs it: it is the one test in this file whose PDF
    download reaches the real `/export/pdf` route rather than a routed stub
    (see the test's own docstring), and that route re-runs the calculation
    for real, where `POST /api/v1/calculate`'s own `duplicate_entry` check
    refuses two entries sharing one `(sector, food_category)` pair -
    `_submit_two_entries`'s default first radio button, chosen twice, is
    exactly that pair, and the mocked `/calculate` route the other tests in
    this file use never enforces it.
    """
    page.click('[data-action="start"]')
    page.wait_for_selector('input[name="sector"]')
    sectors = page.locator('input[name="sector"]').element_handles()
    codes = [handle.get_attribute("value") for handle in sectors[:2]]
    assert len(codes) == 2 and codes[0] != codes[1], (
        f"need two distinct sectors to submit two entries without tripping "
        f"the real API's duplicate_entry check, got {codes}"
    )
    for index, code in enumerate(codes):
        page.check(f'input[name="sector"][value="{code}"]')
        page.wait_for_timeout(60)
        page.click('[data-action="continue"]')
        page.wait_for_selector('input[name="food-category"]')
        page.click('[data-action="continue"]')
        page.wait_for_selector("#total-waste")
        page.fill("#total-waste", "1000")
        page.click('[data-action="continue"]')
        page.wait_for_selector('[data-line-field="amount"]')
        page.fill('[data-line-field="amount"] >> nth=0', "1000")
        page.wait_for_timeout(60)
        page.click('[data-action="continue"]')
        page.wait_for_selector('[data-action="calculate"]')
        if index == 0:
            page.click('[data-action="add-entry"]')
            page.wait_for_selector('input[name="sector"]')
    page.click('[data-action="calculate"]')
    page.wait_for_selector(".results-page", timeout=15000)


@pytest.mark.browser
def test_the_page_the_text_export_and_the_pdf_tell_the_same_story_about_an_equivalence(page_at):
    """One submission, three surfaces, one set of facts. Asserting each surface
    on its own is how saving_nzd shipped a disagreement: the page printed
    nothing where the PDF printed a sentence, and every test was green.

    The page and the text export are read straight off the mocked `/calculate`
    response, the same as every other test in this file - but the PDF button
    posts to the real `/export/pdf` route (see `page_at`'s own docstring: the
    results view is reached by fulfilling `/calculate` in-browser, not by
    driving the real API), which re-runs the calculation against whatever
    factor set is actually published. The point of the test is that all three
    surfaces agree regardless of which one had to go back to the server for
    its numbers.
    """
    page = page_at(_equivalence_response(source_note=_VEHICLE_SOURCE_NOTE))
    _submit_two_entries_of_different_sectors(page)

    first = page.locator(".equivalent-grid article").first
    first.locator("details.equivalent-basis > summary").click()
    on_screen = first.locator(".equivalent-basis__body").inner_text()

    with page.expect_download() as text_download:
        page.click("[data-action='download-results']")
    text = text_download.value.path().read_text(encoding="utf-8")

    with page.expect_download() as pdf_download:
        page.click("[data-action='download-pdf']")
    pdf = _extract_pdf_text(pdf_download.value.path())

    factor = "0.000414938"
    basis = "Client, Data sources for impact calculator"
    disclaimer = "The conversion factor comes from the client. The total it is applied to comes from placeholder factors."
    # `row.name` (§1b): the page and the PDF both print it; the text export
    # did not until this fix. Fixed by the equivalence's own definition
    # (published set), not by the entries submitted, so it is safe to assert
    # as one literal string on all three surfaces the same way `factor` and
    # `basis` already are.
    name = "Passenger vehicles for a year"
    # The `Total` line (§1a): `api/pdf_render.py` printed `source.total` raw
    # -- `4449.0000000000 kg CO2e` -- where the page and the text export both
    # format it through the metric's own `display_precision`. Unlike `factor`
    # and `basis`, this figure is the co2e metric's rolled-up total for
    # *these two entries*: the page and the text export read it off the
    # mocked `/calculate` response, but the PDF button reaches the real
    # `/export/pdf` route, which recomputes it against the published factor
    # set (`api/export.py`'s own module docstring: "the endpoint recalculates
    # instead of trusting the client") -- so the three surfaces are not
    # guaranteed to print the *same number*, only the same *shape*: grouped
    # thousands, a bounded number of fraction digits, never the engine's raw
    # ten-digit `Decimal`. That shape is exactly what the f-string this fix
    # replaces would fail to produce.
    total_line = re.compile(r"([\d,]+\.\d+) kg CO2e")
    for surface, content in (("screen", on_screen), ("text", text), ("pdf", pdf)):
        assert factor in content, f"{surface} is missing the conversion factor"
        assert basis in content, f"{surface} is missing the basis"
        assert disclaimer in content, f"{surface} is missing the disclaimer"
        assert name in content, f"{surface} is missing the equivalence's name"
        match = total_line.search(content)
        assert match, f"{surface} is missing a formatted total for co2e"
        fraction_digits = len(match.group(1).split(".")[1])
        assert fraction_digits <= 3, (
            f"{surface}'s total {match.group(1)!r} is not display-formatted "
            f"-- {fraction_digits} fraction digits is the engine's own scale, "
            f"not a metric's display_precision"
        )


#: The plan's own five widths, checked in both an RTL and an LTR language - the same
#: reason `test_horizontal_overflow.py` checks Arabic and German rather than English
#: alone: a physical `left`/`right` property reads correctly in one direction and
#: overflows, or sits on the wrong side, in the other.
EQUIVALENT_BASIS_WIDTHS = (320, 390, 700, 938, 1278)


@pytest.mark.browser
@pytest.mark.parametrize("width", EQUIVALENT_BASIS_WIDTHS)
@pytest.mark.parametrize("language", ("de", "ar"))
def test_the_equivalence_explanation_does_not_overflow(browser, language, width):
    """Measured, not reasoned about - `test_horizontal_overflow.py`'s own rule.
    Checked with the disclosure open: the open body is the box `test_horizontal_
    overflow.py`'s own docstring warns an absolutely-positioned version of this
    exact affordance once overflowed by about 27px at 320px."""
    context = browser.new_context(
        viewport={"width": width, "height": 900},
        locale=language,
        extra_http_headers={"Accept-Language": f"{language},en;q=0.5"},
        bypass_csp=True,
    )
    try:
        page = context.new_page()
        page.route(
            "**/api/v1/calculate*",
            lambda route: route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps(_equivalence_response(source_note=_VEHICLE_SOURCE_NOTE)),
            ),
        )
        try:
            page.goto(CALCULATOR_URL, wait_until="networkidle", timeout=15000)
        except Exception as error:  # pragma: no cover - environment guard
            pytest.skip(f"the front end is not being served at {CALCULATOR_URL}: {error}")
        page.add_style_tag(content=FORCE_AUTO_SCROLL)
        page.wait_for_selector('[data-action="start"]', timeout=10000)
        _submit_two_entries(page)

        summary = page.locator(".equivalent-grid article").first.locator(
            "details.equivalent-basis > summary"
        )
        summary.click()
        page.wait_for_selector(".equivalent-basis__body")

        measured = page.evaluate(
            "() => ({scroll: document.documentElement.scrollWidth, "
            "client: document.documentElement.clientWidth})"
        )
        assert measured["scroll"] <= max(measured["client"], 320), (
            f"the results page scrolls sideways at {width}px in {language} with the "
            f"explanation open: scrollWidth={measured['scroll']} "
            f"clientWidth={measured['client']}"
        )

        body_box = page.locator(".equivalent-basis__body").first.bounding_box()
        assert body_box is not None
        assert body_box["x"] >= -1, (
            f"the open explanation starts off-screen at {width}px in {language}: {body_box}"
        )
        assert body_box["x"] + body_box["width"] <= measured["client"] + 1, (
            f"the open explanation overflows its own viewport at {width}px in {language}: {body_box}"
        )

        #: The summary sits at its row's own INLINE END: in English/German (LTR)
        #: that is nearer the article's right edge than its left, and in Arabic
        #: (RTL) the reverse - `text-align: end`, a logical property, is what
        #: makes both true without a direction-specific rule. Compared as
        #: "nearer one edge than the other" rather than against a fixed pixel
        #: gap, so the article's own padding does not have to be hard-coded here.
        article_box = page.locator(".equivalent-grid article").first.bounding_box()
        summary_box = summary.bounding_box()
        assert article_box is not None and summary_box is not None
        gap_from_start = summary_box["x"] - article_box["x"]
        gap_from_end = (article_box["x"] + article_box["width"]) - (summary_box["x"] + summary_box["width"])
        near_end = gap_from_end < gap_from_start if language != "ar" else gap_from_start < gap_from_end
        assert near_end, (
            f"the summary is not at the article's inline end at {width}px in "
            f"{language}: {gap_from_start}px from the start, {gap_from_end}px from "
            f"the end ({summary_box} in {article_box})"
        )
    finally:
        context.close()
