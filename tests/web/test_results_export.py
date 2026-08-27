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
import os
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


@pytest.fixture(scope="module")
def browser():
    playwright_api = pytest.importorskip(
        "playwright.sync_api",
        reason="playwright is required to render the destination-first tree; it is unverified without it",
    )
    with playwright_api.sync_playwright() as p:
        instance = p.chromium.launch()
        yield instance
        instance.close()


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

    def open_page(response, width=390, height=900):
        ctx = browser.new_context(viewport={"width": width, "height": height}, locale="en-NZ", bypass_csp=True)
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


def _submit_two_entries(page):
    """Drive the wizard through two entries and press Calculate.

    What is typed does not matter. `sectorName` and `stageFoodLabel` in `results.js`
    both read `response.sector` / `response.food_category` ahead of what the entry
    itself carries, so the two entries only have to *exist* - for `entryResultsFrom`'s
    index pairing (`state.js`) to carry both of the fulfilled response's entries onto
    the results page. What labels them on screen is the fulfilled body, not this walk.
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
