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

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
RESULTS_JS = ROOT / "web" / "js" / "results.js"

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
