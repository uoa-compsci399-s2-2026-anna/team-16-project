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

import base64
import copy
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pytest

from tests.web.steps import press_continue


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


#: The results page itself, as markup. `renderResults` is what the browser
#: calls; `buildResultsReport` below is its text export. Both are asserted
#: because contract §7.3c requires the screen and the file to carry the same
#: sentence, and a test of one of them proves nothing about the other.
HARNESS_SCREEN = """
globalThis.window = { location: { search: '' } }
globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} }

import { readFileSync, writeFileSync } from 'node:fs'
const { renderResults } = await import(process.argv[2])
const state = JSON.parse(readFileSync(process.argv[3], 'utf8'))
writeFileSync(process.argv[4], renderResults(state), 'utf8')
"""


def screen_for(tmp_path: Path, state: dict) -> str:
    """`renderResults(state)`, the markup the results page is."""
    harness = tmp_path / "harness_screen.mjs"
    harness.write_text(HARNESS_SCREEN, encoding="utf-8")
    state_file = tmp_path / "state_screen.json"
    state_file.write_text(json.dumps(state), encoding="utf-8")
    out = tmp_path / "screen.html"
    completed = subprocess.run(
        [shutil.which("node"), str(harness), RESULTS_JS.as_uri(),
         str(state_file), str(out)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert completed.returncode == 0, (
        "node could not render the results page:\n"
        f"{completed.stdout}\n{completed.stderr}"
    )
    return out.read_text(encoding="utf-8")


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


#: The same harness shape, for `renderResults` instead of
#: `buildResultsReport`. **Added because a mutation survived**: making
#: `equivalenceBasis` fall back to `source_note` left every runnable test in
#: the tree green, and the only thing that would have caught it was a
#: `@pytest.mark.browser` case against the running container -- which cannot
#: see a change to `web/js/` until the image is rebuilt (`docker/web.Dockerfile`
#: COPYs `web/`), and rebuilding is not something a test may do to a deployment
#: somebody is using.
#:
#: `renderResults` is a pure string builder: it reads `state`, calls `t()` and
#: returns markup. So the markup can be asserted here, off the file, the same
#: way the text export already is. This is not a replacement for the browser
#: cases -- they measure layout, visibility and the accessible name, which a
#: string cannot -- it is the half that can be run on a checkout.
MARKUP_HARNESS = """
// The three globals `improvement.js` -> {api.js, state.js} read at module load.
globalThis.window = { location: { search: '' } }
globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} }

import { readFileSync, writeFileSync } from 'node:fs'
const { renderResults } = await import(process.argv[2])
const state = JSON.parse(readFileSync(process.argv[3], 'utf8'))
writeFileSync(process.argv[4], renderResults(state), 'utf8')
"""


def markup_for(tmp_path: Path, state: dict) -> str:
    harness = tmp_path / "markup.mjs"
    harness.write_text(MARKUP_HARNESS, encoding="utf-8")
    state_file = tmp_path / "markup-state.json"
    state_file.write_text(json.dumps(state), encoding="utf-8")
    out = tmp_path / "markup.html"
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
        f"node could not render the results page:\n{completed.stdout}\n"
        f"{completed.stderr}"
    )
    return out.read_text(encoding="utf-8")


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


def build_state_with_item_basis(*bases: str) -> dict:
    """`build_state`, with each entry given a food and a v1.59 `item_basis`.

    The taxonomy fixture ships `"food_items": []` -- every deployment does,
    because `admin/seed.py` seeds no food -- so a vocabulary is added here
    rather than invented in the fixture, where it would put codes in front of
    C and D that no database holds (contract v1.58's note on `taxonomy.json`).
    """
    state = build_state()
    state["taxonomy"] = dict(
        state["taxonomy"],
        food_items=[
            {"code": "cheese", "name": "Cheese", "food_category": "dairy",
             "sort_order": 1},
            {"code": "carrots", "name": "Carrots", "food_category": "vegetables",
             "sort_order": 2},
        ],
    )
    foods = ("cheese", "carrots")
    for index, basis in enumerate(bases):
        item = state["result"]["entry_results"][index]
        item["response"] = dict(item["response"], item_basis=basis,
                                food_item=foods[index])
    return state


def test_the_results_sections_are_in_the_order_the_floating_nav_claims(tmp_path):
    """**The nav is a list of jumps, so a wrong order is a wrong jump.**

    It read summary / improvements / equivalents / breakdown while the
    improvement panel was rendered below the downloads -- three sections further
    down than its second slot claimed. Its one link that existed to save a
    scroll was the one that jumped past everything else on the page.

    Asserted against the rendered page rather than against the two source lists,
    because reading both and comparing them would pass whenever they were
    wrong in the same way, which is exactly how they came to disagree.
    """
    screen = screen_for(tmp_path, build_state())

    markers = {
        "#impact-summary": 'id="impact-summary"',
        "#tangible-equivalents": 'id="tangible-equivalents"',
        "#breakdown-section": 'id="breakdown-section"',
        "#improvement-section": 'id="improvement-section"',
    }
    on_page = {}
    for href, marker in markers.items():
        at = screen.find(marker)
        assert at != -1, f"{href} is not on the rendered results page at all"
        on_page[href] = at

    #: The nav's own hrefs, in the order it writes them.
    import re as _re

    nav = _re.search(r'class="results-floating-nav__links">(.*?)</ul>', screen, _re.S)
    assert nav, "the floating nav rendered no link list"
    claimed = _re.findall(r'href="(#[a-z-]+)"', nav.group(1))
    assert len(claimed) == len(markers), f"the nav lists {claimed}, not the four sections"

    actual = sorted(claimed, key=lambda href: on_page[href])
    assert claimed == actual, (
        f"the floating nav lists the sections as {claimed} but the page renders "
        f"them as {actual}, so at least one link jumps somewhere the reader did "
        f"not expect -- a nav in the wrong order is worse than no nav, because a "
        f"reader who scrolls finds the sections in the order they are in"
    )

    #: The move this test was written for, stated as itself so a later reshuffle
    #: that kept the two lists agreeing but put the panel back under the
    #: downloads still fails here.
    assert on_page["#improvement-section"] < screen.find('id="results-methodology"'), (
        "the improvement panel renders below Methodology & Limitations again. It "
        "was moved above both that and the download actions because a visitor "
        "who has just read their result is being offered the next thing to do, "
        "not a footnote"
    )


def test_the_navs_two_actions_are_controls_and_not_two_more_jumps(tmp_path):
    """**#126 asked for *Download* and *Start a new calculator* in this nav, and a
    jump to them is not what it asked for.**

    They arrived as two `href="#..."` entries in the link list, onto
    `.result-actions` -- `display: flex`, with its column override at
    `max-width: 480px` while the nav is not drawn below 1100px. So the two entries
    were two names for one destination, and #126's criteria 2 and 3 ("Download
    produces the expected results file" and "Start a new calculator clearly begins a
    fresh calculation") were answered by scrolling the reader to a button and
    stopping there.

    They are buttons now, each a remote control for the action row's own button:
    `results.js` forwards the press to `.result-actions [data-action="..."]`, whose
    click reaches the one delegated listener in `calculator.js` exactly as the row's
    own press does. Five separable things are asserted, and each fails on its own:

    * the panel holds exactly the two actions, as `<button>`s;
    * neither of them is a link -- the thing this test exists to stop coming back is
      an `href` in this group;
    * each names the `data-action` of a control that is actually on the page, since
      the forwarding is a `querySelector` and a typo in it is a silent no-op;
    * the section link list is untouched by them, because a section index with
      actions in it is what made the pin unreleasable;
    * the labels are the action row's own strings, which is what keeps this at zero
      new catalogue entries -- and legitimate, because the two controls that share a
      name now do the same thing.

    **`data-nav-action`, not `data-action`**, and that is asserted too, below.
    `[data-action="download-results"]` is how this whole suite names *the* download
    button -- eighteen selectors in five files -- so a second element answering to it
    made nine of them resolve to this hidden panel instead. Measured: this file went
    from 135 passed to 9 failed on actionability timeouts.
    """
    screen = screen_for(tmp_path, build_state())

    import re as _re

    group = _re.search(r'<div class="results-floating-nav__actions" role="group">(.*?)</div>',
                       screen, _re.S)
    assert group, (
        "the floating nav renders no action group, so #126's two actions are reachable "
        "only by scrolling to the bottom of the page again"
    )
    body = group.group(1)

    assert 'href' not in body, (
        f"the nav's action group contains a link: {body!r}. These are actions, and as "
        f"`#` anchors they were two entries on one flex row -- both jumping to the same "
        f"place, and neither able to release the `aria-current` mark a press puts on it, "
        f"because the row never reaches the 24px rest line a release latches on"
    )
    offered = _re.findall(r'data-nav-action="([a-z-]+)"', body)
    assert offered == ["start-over", "download-results"], (
        f"the nav's actions are {offered}; each has to name the `data-action` of the "
        f"action row's own button, because the press is forwarded to it by "
        f"`querySelector` -- and `data-nav-action` is the attribute, because a second "
        f"element answering to `data-action` makes this panel's hidden copy the one "
        f"every unscoped selector in the suite finds"
    )
    assert 'data-action=' not in body, (
        f"the nav's action group carries a `data-action`: {body!r}. Eighteen selectors "
        f"across five test files name the real controls that way, and this panel is "
        f"hidden -- so they resolve here and wait thirty seconds for a button that will "
        f"never become visible"
    )
    assert body.count("<button") == 2, f"the action group holds {body.count('<button')} buttons: {body!r}"

    #: The forwarding target has to exist, **inside `.result-actions`**, which is the
    #: selector `results.js` forwards through. `querySelector(...)?.click()` is a
    #: silent no-op against a typo, and the non-browser render is the cheapest place
    #: to see that both ends of the pair are spelled the same way.
    row = _re.search(r'<div class="result-actions">(.*?)\Z', screen, _re.S)
    assert row, "the results page renders no `.result-actions` row for the nav to forward to"
    in_the_row = _re.findall(r'data-action="([a-z-]+)"', row.group(1))
    for action in offered:
        assert action in in_the_row, (
            f"the nav forwards to `.result-actions [data-action=\"{action}\"]` and the row "
            f"offers {in_the_row}. `querySelector(...)?.click()` fails silently, so a nav "
            f"item pointed at a control that is not there simply does nothing"
        )

    #: The strings, so that a redesign which coins new labels here is reported --
    #: a new string reaching the PDF raises 500 on every non-English download until
    #: all twenty catalogues carry it (`api.i18n.Catalogue.gettext`).
    for label in ("Start a new calculation", "Download results"):
        assert f">{label}</button>" in body, (
            f"the nav's action group does not print {label!r}. These are the action row's "
            f"own strings on purpose: reusing them is why this change needed nothing in "
            f"twenty catalogues, and it is honest because both doors run one action"
        )

    #: And the section index is still only sections. This is the assertion that
    #: fails if the two entries are put back into `RESULTS_NAV_SECTIONS` as well.
    links = _re.search(r'class="results-floating-nav__links">(.*?)</ul>', screen, _re.S)
    assert links, "the floating nav rendered no link list"
    assert _re.findall(r'href="(#[a-z-]+)"', links.group(1)) == [
        "#impact-summary", "#tangible-equivalents", "#breakdown-section", "#improvement-section",
    ], _re.findall(r'href="(#[a-z-]+)"', links.group(1))


def test_the_results_page_renders_every_section_it_composes(tmp_path):
    """**The improvement panel has now vanished in a merge twice.**

    Both times the shape was the same: moving a section is a removal plus an
    insertion, the removal merged cleanly on its own, and the insertion went
    with the discarded side. `ImprovementScenario` stayed imported, so nothing
    complained; it was simply called zero times, and `renderResults` went on
    returning a page that looked complete. The first time, ninety-six tests
    stayed green. The second time it took out every browser test that reaches
    the panel -- but only after the merge was already on `main`.

    Counting call sites is what found it by hand and is not what this asserts:
    a call inside a branch nobody takes would satisfy that. This renders the
    page and looks for what each composed section puts on screen, so a section
    that stops being called fails here whatever the source looks like.
    """
    screen = screen_for(tmp_path, build_state())

    for marker, section in (
        ('id="impact-summary"', "the impact summary"),
        ('data-action="explore-improvements"', "the improvement panel"),
        ('id="tangible-equivalents"', "the tangible equivalents"),
        ('id="breakdown-section"', "the breakdown"),
        ('id="results-methodology"', "the methodology block"),
        ('data-action="download-results"', "the download actions"),
    ):
        assert marker in screen, (
            f"{section} is not on the rendered results page -- `renderResults` "
            f"composes it, so it has stopped being called ({marker!r})"
        )


def test_the_screen_and_the_file_carry_the_same_disclosure(tmp_path):
    """Contract §7.3c: one sentence, three surfaces, one field.

    The screen and the text export are built here from **one** state in two
    calls, so a fix that reached only one of them shows up as a disagreement
    rather than as two green tests that each checked their own surface and
    never compared notes. (The third surface is the PDF; `tests/api/
    test_pdf_render.py` pins its two constants against the keys the front end
    renders, which is the same claim across a process boundary.)
    """
    state = build_state_with_item_basis("category")

    screen = screen_for(tmp_path, state)
    report = report_for(tmp_path, state)

    sentence = "Cheese is priced at the Dairy average"
    assert sentence in screen, "the results page does not disclose the fallback"
    assert sentence in report, "the text export does not disclose the fallback"
    assert "Food category average" in screen and "Food category average" in report


def test_the_screen_keeps_the_disclosure_and_the_placeholder_banner_apart(tmp_path):
    """They are two different caveats and the page shows both.

    A real factor set can still price a food only at its category, and a
    placeholder set can price a food individually -- so folding the disclosure
    into the `is_mock` banner would tie a caveat about *coverage* to a flag
    about *provenance*, and the day the client supplies real factors the
    fallback sentence would disappear with the banner.
    """
    state = build_state_with_item_basis("category")
    screen = screen_for(tmp_path, state)

    assert "Placeholder data" in screen
    assert "Cheese is priced at the Dairy average" in screen
    assert screen.count("aside class=\"disclaimer\"") == 2


def test_a_food_priced_at_its_category_is_disclosed_in_the_export(tmp_path):
    """Contract v1.59. The export is the copy most likely to be forwarded to
    somebody who was not in the room, so a figure that is not as specific as
    the question it answers has to say so on the file as well as on screen."""
    report = report_for(tmp_path, build_state_with_item_basis("category"))

    assert "Food category average" in report
    assert "Cheese is priced at the Dairy average" in report


def test_the_export_names_every_food_that_fell_back_and_only_those(tmp_path):
    """One line per food, and an entry that was priced at its own food does
    not get one. Asserted together, because a test that only checked the
    disclosed food would pass against a version that disclosed every entry."""
    report = report_for(tmp_path, build_state_with_item_basis("category", "item"))

    assert "Cheese is priced at the Dairy average" in report
    assert "Carrots" not in report


def test_a_mixed_entry_is_not_disclosed_in_the_export(tmp_path):
    """`mixed` is the ordinary state, not an alarm: `prevention` factors are
    stored as category-level rows (contract §2.2, the shape that closes O-7),
    so every entry that moves mass to prevention has a category-priced line
    however well the set prices its food. A caveat that fires on nearly every
    submission is read as furniture."""
    report = report_for(tmp_path, build_state_with_item_basis("mixed", "mixed"))

    assert "Food category average" not in report
    assert "is priced at the" not in report


def test_an_entry_that_named_no_food_is_not_disclosed_in_the_export(report):
    """`not_applicable`, which is every entry today -- the fixture carries
    `"item_basis": "not_applicable"` on both entries. Nothing was asked, so
    there is nothing to say, and a caveat here would be about a choice the
    visitor never made."""
    assert "Food category average" not in report


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
    #: `land` joined the fixture at contract v1.78, when the response fixtures
    #: caught up with the set the owner had published -- `m2` from the figure's
    #: own `unit`, which is the column that reaches this surface, and `11,174.3`
    #: is 11174.3429467200 at `display_precision` 1.
    ("land", "  - Land use: 11,174.3 m2"),
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


def test_total_lines_names_every_metric_the_fixture_carries_but_mass():
    """`TOTAL_LINES` is a **count** as well as a list of exact lines, so it has to
    be complete or the test above stops meaning what it says.

    This is the guard the list did not have. `land` reached
    `calculate_response.json` at contract v1.78 and
    `test_the_export_names_no_metric_of_its_own` failed on `5 == 4` -- which was
    the right answer to the wrong question: the export was correct and the list
    was short. A list of anchored strings cannot be derived from the fixture
    without becoming a tautology (the point of `4,449.0 kg CO2e` is that somebody
    read it), so what is derived instead is its **membership**: adding a metric
    to the fixture now costs one line here, written and checked by a person,
    rather than a count that silently stops covering it.

    `mass` is excepted for the reason `summaryCards` excepts it, and
    `test_mass_is_the_headline_and_not_an_impact_line` is what holds that.
    """
    carried = [
        code
        for code in _fixture("calculate_response.json")["totals"]["current"]["metrics"]
        if code != "mass"
    ]

    assert [code for code, _ in TOTAL_LINES] == carried, (
        f"TOTAL_LINES names {[code for code, _ in TOTAL_LINES]} and "
        f"calculate_response.json's totals carry {carried} (mass excepted). The count "
        f"assertion above is only as complete as this list, so a metric missing from it "
        f"is a metric whose printed line, unit and precision nothing checks"
    )


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
@pytest.mark.parametrize("is_mock", (True, False))
def test_the_equivalence_caveat_is_in_no_export_at_all_now(tmp_path, is_mock):
    """**v1.80 (#127) turned a gate into a deletion, and this is the test that
    was a gate.**

    It used to prove the per-equivalence caveat was conditional on `is_mock`,
    because an unconditional version of it once made a real published set
    describe itself as placeholder data. The client has now asked for the
    sentence to go entirely, so the property is no longer "absent when the set
    is real" but "absent either way" - and it is parametrised over both states
    rather than asserted on the real one, because a sentence that came back
    only under `is_mock` would be exactly the old defect wearing the fix.

    **The page-level notice is a separate obligation and is not weakened**:
    `test_the_placeholder_notice_is_on_a_mock_export` above still requires it
    on a mock export, and the test beside that one still requires its absence
    on a real one. That pair is §7.6 rule 2; this one never was.
    """
    report = report_for(tmp_path, build_state(is_mock=is_mock))
    assert "The conversion factor comes from the client" not in report
    assert ("placeholder factors" in report) is False
    assert "Basis:" not in report


@node
def test_the_export_prints_the_sentence_and_not_the_provenance(report):
    """The positive half of #127 on the surface that gets forwarded.

    `calculate_response.json`'s one equivalence carries both fields, and they
    are nothing like each other on purpose, so this cannot pass against a
    renderer that printed the wrong one. `source_note` is the four-sentence
    audit paragraph; it belongs to `GET /factors` and the methodology page and
    must not be in a visitor's download.
    """
    assert (
        "The same greenhouse gases as driving an average light petrol car "
        "this far, at a placeholder 0.24 kg CO2e a kilometre." in report
    )
    assert "Open item O-3" not in report
    assert "Roughly one kilometre of an average light petrol vehicle" not in report


@node
def test_an_equivalence_with_no_sentence_prints_nothing_and_never_the_note(tmp_path):
    """**The anti-fallback assertion on the text export.**

    `description` is nullable and `null` means print nothing (§6.2, v1.80).
    The one wrong answer is `source_note`, and this state is the only one in
    which a fallback is observable at all - so the equivalence keeps its
    basis, loses its sentence, and the report must carry neither the note nor
    a label for it. Driven by stripping the field from the response rather
    than by building a response by hand, so the row is otherwise identical to
    the one the test above reads.
    """
    state = build_state()
    for scope in (state["result"]["totals"]["current"],):
        for row in scope["equivalences"]:
            row["description"] = None
    for pair in state["result"]["entry_results"]:
        for key in ("current", "alternative"):
            scenario = (pair.get("response") or {}).get(key)
            for row in (scenario or {}).get("equivalences", []):
                row["description"] = None

    report = report_for(tmp_path, state)

    assert "Open item O-3" not in report, (
        "the text export fell back to source_note for an equivalence with no "
        "description, which is the long version #127 removed"
    )
    assert "Basis:" not in report
    #: The premise: the row still HAS a note, so the assertion above is about
    #: a fallback that was available and not taken.
    assert any(
        row.get("source_note")
        for row in state["result"]["totals"]["current"]["equivalences"]
    ), "the fixture row carries no source_note, so nothing could have fallen back"
    #: And the card is still drawn - the label and the figures survive.
    assert "Equivalent to driving 18,597 km" in report


# --------------------------------- the disclosure's markup, without a browser


@node
def test_the_disclosure_holds_the_sentence_and_not_the_provenance(tmp_path):
    """v1.80 (#127), asserted on the markup `renderResults` produces.

    The browser cases below measure what a person can SEE -- that the panel
    starts closed, opens, and does not overflow. This measures what is in it,
    which is the part that can be checked on a checkout rather than against an
    image somebody would have to rebuild.
    """
    markup = markup_for(tmp_path, build_state())

    assert "equivalent-basis__body" in markup, (
        "the disclosure was not rendered at all, so this test is measuring "
        "nothing"
    )
    assert (
        "The same greenhouse gases as driving an average light petrol car "
        "this far, at a placeholder 0.24 kg CO2e a kilometre." in markup
    )
    assert "Open item O-3" not in markup, (
        "the provenance paragraph is back in the equivalence disclosure"
    )
    assert "Basis:" not in markup


@node
@pytest.mark.parametrize("is_mock", (True, False))
def test_the_disclosure_carries_no_per_equivalence_caveat_either_way(tmp_path, is_mock):
    """The caveat is gone from the panel on every factor set (v1.80, #127).

    Parametrised over both states rather than asserted on the real one,
    because a sentence that reappeared only under `is_mock` would be precisely
    the defect `test_a_real_factor_set_carries_no_warning` was written for.
    The page-level banner is a different thing and is asserted below.
    """
    markup = markup_for(tmp_path, build_state(is_mock=is_mock))
    assert "The conversion factor comes from the client" not in markup
    assert "comes from placeholder factors" not in markup


@node
@pytest.mark.parametrize("is_mock, expected", ((True, True), (False, False)))
def test_the_page_level_placeholder_banner_is_exactly_as_it_was(
    tmp_path, is_mock, expected
):
    """**The one thing in #127 that could not be traded for brevity**, pinned
    on the markup so that removing a per-card sentence cannot quietly weaken
    it.

    §7.6 rule 2: mandatory and non-dismissible while `is_mock`, and
    conditional rather than unconditional -- an export that always disclaims
    becomes an export that disclaims real data the day real factors are
    published. Both directions, and three properties of the banner itself:
    it is an `aside.disclaimer`, it carries `role="status"`, and it has no
    dismiss control of any kind.
    """
    markup = markup_for(tmp_path, build_state(is_mock=is_mock))
    banner = 'class="disclaimer" role="status"'
    assert (banner in markup) is expected, (
        f"is_mock={is_mock} and the placeholder banner "
        f"{'is missing' if expected else 'is drawn anyway'}"
    )
    if expected:
        assert "Placeholder data" in markup
        assert NOTICE in markup
        #: Non-dismissible: nothing in the banner closes it. Asserted on the
        #: whole document because a dismiss control added anywhere would be
        #: the same defect.
        for control in ('data-action="dismiss"', "aria-label=\"Close\"",
                        'class="disclaimer-dismiss"'):
            assert control not in markup, (
                f"the placeholder banner gained a dismiss control: {control}"
            )


@node
def test_a_disclosure_with_no_sentence_falls_back_to_nothing(tmp_path):
    """**The anti-fallback assertion on the page's own markup.**

    The row keeps its `source_note` and loses its `description`, which is the
    only state where a fallback is observable. **This test exists because its
    mutation survived**: rewriting `equivalenceBasis` to
    `row.description || row.source_note` left every runnable test in the tree
    green, because the only case that would have caught it drives a browser
    against an image this work may not rebuild.

    The disclosure still opens onto something -- the three figures -- so an
    empty sentence is not an empty panel.
    """
    state = build_state()
    for row in state["result"]["totals"]["current"]["equivalences"]:
        row["description"] = None
    for pair in state["result"]["entry_results"]:
        for key in ("current", "alternative"):
            scenario = (pair.get("response") or {}).get(key)
            for row in (scenario or {}).get("equivalences", []):
                row["description"] = None

    markup = markup_for(tmp_path, state)

    assert "equivalent-basis__body" in markup
    assert "equivalent-basis__note" not in markup, (
        "an equivalence with no description still drew a note paragraph"
    )
    assert "Open item O-3" not in markup, (
        "the disclosure fell back to source_note, which is the long version "
        "#127 removed"
    )
    assert "not recorded" not in markup.lower()
    #: The premise, so the assertions above are about a fallback that was
    #: available and not taken.
    assert any(
        row.get("source_note")
        for row in state["result"]["totals"]["current"]["equivalences"]
    ), "the fixture row carries no source_note, so nothing could have fallen back"


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
        press_continue(page)
        page.wait_for_selector('input[name="food-category"]')
        press_continue(page)
        page.wait_for_selector("#total-waste")
        page.fill("#total-waste", "1000")
        press_continue(page)
        page.wait_for_selector('[data-line-field="amount"]')
        page.fill('[data-line-field="amount"] >> nth=0', "1000")
        page.wait_for_timeout(60)
        press_continue(page)
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


def _results_page(page_at, *, contribute_calls=None, reduced_motion=None, width=None):
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

    `width` likewise. The default 390 is where every other test here belongs -
    `.contribute-control` wraps at that width and the tick and the action are on
    two lines - but `test_submit_undo_and_the_flower_take_one_position_in_turn`
    is about where they sit *in a row*, so it asks for a viewport that has one.
    """
    kwargs = {} if width is None else {"width": width}
    page = page_at(_fixture("calculate_response.json"), reduced_motion=reduced_motion, **kwargs)
    if contribute_calls is not None:
        page.route(
            "**/api/v1/contribute",
            lambda route: (contribute_calls.append(route.request.post_data_json), route.fulfill(status=204)),
        )
    _submit_two_entries(page)
    return page


#: Round four's grace window, in milliseconds. Kept in step with
#: `CONTRIBUTE_GRACE_MS` (`web/js/results.js`) and `--contribute-grace`
#: (`web/css/styles.css`) - the timer, and the picture of the timer.
CONTRIBUTE_GRACE_MS = 5000

#: Headroom over the window before a test reads the result of it. Generous on
#: purpose: an assertion that a request HAS arrived is allowed to wait; the
#: assertions that a request has NOT arrived are the ones that must be tight,
#: and those are written against a deliberately short wait instead.
_GRACE_SLACK_MS = 1200


def _press_submit(page):
    """The second of the two steps. `#contribute-action` is one id worn by two
    buttons in turn - Submit before the press, Undo during the window - because
    `main.js` restores focus by id after a re-render (see `contributeBlock`'s
    own note), so this is also what a keyboard visitor stays on."""
    page.locator("#contribute-action").click()


def _contribute(page, slack=_GRACE_SLACK_MS):
    """Tick, press Submit, and let the five seconds run out - the whole of what
    it now takes to send a `/contribute`. Every test below that needs the
    contributed state goes through this rather than through `.check()` alone,
    which is exactly the difference round four introduced."""
    page.locator("#contribute").check()
    _press_submit(page)
    page.wait_for_timeout(CONTRIBUTE_GRACE_MS + slack)


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
def test_the_tick_alone_sends_nothing_and_submit_plus_the_window_sends_the_token(page_at):
    """**Round four, and the test that defines it.** The control is two steps: a
    tick on the left and a separate Submit button on the right, and pressing
    Submit only *arms* a five-second window in which the visitor can still take
    it back. So there are three distinct moments here and all three are checked
    in order, because each one alone would pass against a wrong implementation:

    * before anything is touched - the case that catches a `contribute()` on
      page load, which would eventually make this request anyway and satisfy any
      assertion written only about the request's shape;
    * after the tick and nothing else - the case that catches the one-step
      control this replaces, which sent on `change`;
    * after Submit and the whole window - the case that catches a window that
      never fires, i.e. a consent the visitor gave and the server never heard.

    The middle assertion is the new one, and it is checked against a wait longer
    than any plausible request latency rather than immediately, so "not sent
    yet" cannot be mistaken for "not sent at all".
    """
    early = []
    page = _results_page(page_at, contribute_calls=early)
    assert not early, "a request to /contribute was made before the box was ever pressed"

    sent = []
    page.route(
        "**/api/v1/contribute",
        lambda route: (sent.append(route.request.post_data_json), route.fulfill(status=204)),
    )

    page.locator("#contribute").check()
    page.wait_for_timeout(1200)
    assert not sent, (
        "ticking the box on its own sent a /contribute - the tick is a statement "
        "of intent and the Submit button beside it is what sends"
    )

    _press_submit(page)
    page.wait_for_timeout(CONTRIBUTE_GRACE_MS + _GRACE_SLACK_MS)

    assert len(sent) == 1, f"Submit plus its five seconds sent {len(sent)} requests, not one"
    assert sent[0].get("token"), "no token was sent, so no row can be found to update"


@pytest.mark.browser
def test_submit_is_disabled_until_the_box_is_ticked(page_at):
    """"The tick alone must do nothing - no request. Submit is `disabled` until
    the box is ticked." The second half is what makes the first half a *control*
    rather than a trap: an enabled Submit over an unticked box either sends a
    consent nobody gave or does nothing at all when pressed, and both are worse
    than refusing the press in the markup.

    Unticking again is asserted too, because a `disabled` attribute written once
    at first render and then only ever removed would pass the two lines above it
    and would leave Submit live over a box the visitor had just cleared.
    """
    page = _results_page(page_at)

    action = page.locator("#contribute-action")
    assert action.count() == 1, "the results page has no separate Submit button"
    assert action.is_disabled() is True, (
        "Submit is pressable before the visitor has ticked anything"
    )

    page.locator("#contribute").check()
    page.wait_for_timeout(200)
    assert action.is_disabled() is False, "Submit is still disabled over a ticked box"

    page.locator("#contribute").uncheck()
    page.wait_for_timeout(200)
    assert action.is_disabled() is True, (
        "Submit stayed pressable after the box was unticked again"
    )


@pytest.mark.browser
def test_undo_cancels_the_send_and_leaves_the_box_ticked(page_at):
    """**The five seconds, and what they are for.** `POST /contribute` is one-way
    by construction - `set_public_contribution` only ever sets the flag TRUE and
    §6.2.2 has no path that clears it - so the window is spent *before* the
    request rather than after it: Undo cancels a `setTimeout`, and nothing needs
    unsending because nothing was sent.

    Two things are asserted, and the second is the one an implementation is
    likely to get wrong. No request must ever arrive - checked well past the end
    of the window the press armed, so a timer that was left running would have
    fired by then. And the box must still be **ticked**, and pressable again:
    the visitor's answer to "would you like to contribute" was yes, and only
    their decision to send it now was undone. Clearing the tick would make Undo
    a second, unasked-for reversal.
    """
    sent = []
    page = _results_page(page_at, contribute_calls=sent)

    page.locator("#contribute").check()
    _press_submit(page)
    page.wait_for_timeout(600)
    assert page.locator(".contribute-countdown").count() == 1, (
        "pressing Submit opened no visible window, so there is nothing to undo"
    )

    _press_submit(page)  # the same slot, now carrying Undo
    page.wait_for_timeout(CONTRIBUTE_GRACE_MS + _GRACE_SLACK_MS)

    assert not sent, "Undo did not stop the send; /contribute was called anyway"
    assert page.locator(".contribute-countdown").count() == 0, (
        "the countdown is still on screen after Undo"
    )

    control = page.locator("#contribute")
    assert control.is_checked() is True, (
        "Undo unticked the box as well - it cancels the send, not the choice"
    )
    assert control.is_disabled() is False, "the box is still locked after Undo"
    assert page.locator("#contribute-action").is_disabled() is False, (
        "Submit cannot be pressed again after an Undo, so the window was a trapdoor"
    )

    # And it really can be pressed again, on a fresh window.
    _press_submit(page)
    page.wait_for_timeout(CONTRIBUTE_GRACE_MS + _GRACE_SLACK_MS)
    assert len(sent) == 1, f"the second Submit sent {len(sent)} requests, not one"


@pytest.mark.browser
def test_a_re_render_inside_the_window_does_not_grant_a_fresh_five_seconds(page_at):
    """**Trap 1, measured.** `render()` (`calculator.js`) replaces
    `main.innerHTML` on every `setState`, and the visitor is quite likely to be
    typing in the improvement panel while the window runs. Two things could
    reset and both are checked here, because each fails independently:

    * the **timer**, if its handle lived anywhere inside `<main>`. It does not -
      it is module scope in `results.js`, the same place `calculator.js` holds
      `reloadTaxonomy` - and the assertion is that the request still arrives on
      the original deadline. A re-render at ~2.5s into a 5s window would push it
      to ~7.5s under an implementation that re-armed; the bound below is 6.5s,
      which the honest one clears and the re-arming one cannot.
    * the **picture of the timer**, which is a CSS animation, and a CSS
      animation *does* restart when its element is recreated. `results.js`
      writes a negative `animation-delay` from the absolute deadline on
      `state.contributeArmedUntil` so the rebuilt bar resumes where the old one
      was. Asserted on the bar's measured width rather than on the delay alone:
      a delay attribute that is written but lands on an element with no
      animation, or with the wrong duration, would satisfy the attribute check
      and still show a visitor a full bar.
    """
    arrived = []
    page = _results_page(page_at)
    page.route(
        "**/api/v1/contribute",
        lambda route: (arrived.append(time.monotonic()), route.fulfill(status=204)),
    )

    page.locator("#contribute").check()
    page.wait_for_timeout(200)
    armed_at = time.monotonic()
    _press_submit(page)
    page.wait_for_timeout(250)

    width = "() => document.querySelector('.contribute-countdown__bar').getBoundingClientRect().width"
    full = page.evaluate(width)
    assert full > 0, "the countdown bar has no width at the start of the window"

    # ~2.5s in, force the re-render: a breakdown tab is `setState` and a full
    # rebuild of the results page, the same thing a keystroke in the improvement
    # panel does, and it needs no typing to be unambiguous.
    page.wait_for_timeout(2300)
    page.click('[data-action="breakdown-tab"] >> nth=1')
    page.wait_for_timeout(150)

    resumed = page.evaluate(width)
    assert resumed < full * 0.75, (
        "the countdown bar refilled after a re-render - it is showing the visitor "
        f"a window they no longer have ({resumed:.1f}px of {full:.1f}px, about "
        "2.5s into five)"
    )

    page.wait_for_timeout(CONTRIBUTE_GRACE_MS)
    assert arrived, "the request never arrived at all, so nothing here was measured"
    elapsed = arrived[0] - armed_at
    assert elapsed < 6.5, (
        "the re-render restarted the five-second window: the request went "
        f"{elapsed:.2f}s after Submit was pressed, not about five"
    )


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

    **Round four moved where "in flight" begins.** The request no longer leaves on
    the `change` event, so reaching the in-flight state means ticking, pressing
    Submit and letting the grace window run out first - and the rule this test was
    written for now has to hold across a longer stretch than it did: from the press,
    through the five seconds, and on through the request. All three are checked,
    because `pending` and `armed` are separate flags in `contributeBlock` and a
    version that locked only one of them would leave the box readable as unticked
    for whichever half it forgot.
    """
    page = _results_page(page_at)
    held = {}
    page.route("**/api/v1/contribute", lambda route: held.setdefault("route", route))

    page.locator("#contribute").check()
    _press_submit(page)
    page.wait_for_timeout(600)

    control = page.locator("#contribute")
    assert control.is_checked() is True, "the box is unticked while its window is open"
    assert control.is_disabled() is True, (
        "the box is still editable while the send it armed is counting down"
    )

    page.wait_for_timeout(CONTRIBUTE_GRACE_MS + _GRACE_SLACK_MS)
    assert "route" in held, "the window closed and no request was made"

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

    _contribute(page)

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
    _contribute(ordinary, slack=300)
    assert ordinary.locator(".contribute-flower").count() >= 1, (
        "no flower ever appears, even with motion allowed - the reduced-motion "
        "assertion below would prove nothing"
    )

    reduced = _results_page(page_at, reduced_motion="reduce")
    reduced.route("**/api/v1/contribute", lambda route: route.fulfill(status=204))
    _contribute(reduced, slack=300)
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
def test_reduced_motion_still_shows_the_window_running_down(page_at):
    """**The one animation in `styles.css` that reduced motion does not simply
    switch off, and the reasoning is the opposite of the flower's.** The flower
    is a flourish over a decision already made, so suppressing it costs nothing.
    The countdown is a five-second deadline the visitor is being invited to beat:
    removing it would leave someone who asked for no motion with an Undo button
    and no way to tell how much of their window was left, which is worse for
    them than the movement was. So the bar stays and its timing function becomes
    `steps(5)` - it jumps once a second instead of sliding.

    Both halves are asserted, for `test_the_flower_blooms_only_when_motion_is_
    allowed`'s reason in reverse: that the bar is still animating at all, and
    that it is animating in discrete steps rather than continuously. A stylesheet
    that dropped the whole `@media` block would fail the second; one that set
    `animation: none` would fail the first. And the sentence above the control
    must say "five seconds" in words, so how long they have never depends on
    seeing the bar at all.
    """
    page = _results_page(page_at, reduced_motion="reduce")
    page.route("**/api/v1/contribute", lambda route: route.fulfill(status=204))

    page.locator("#contribute").check()
    _press_submit(page)
    page.wait_for_timeout(400)

    assert page.locator(".contribute-countdown").count() == 1, (
        "there is no countdown at all under prefers-reduced-motion, so a visitor "
        "who asked for no animation is given a deadline and no sight of it"
    )
    bar = page.evaluate(
        "() => { const style = getComputedStyle(document.querySelector('.contribute-countdown__bar'));"
        " return {name: style.animationName, timing: style.animationTimingFunction,"
        " duration: style.animationDuration}; }"
    )
    assert bar["name"] not in ("none", ""), (
        f"the countdown bar does not animate under reduced motion: {bar}"
    )
    assert bar["duration"] == "5s", f"the bar does not run for the window's own five seconds: {bar}"
    assert "steps(5" in bar["timing"], (
        "the countdown slides continuously under prefers-reduced-motion rather "
        f"than stepping: {bar}"
    )

    said = page.locator(".contribute-block").inner_text().lower()
    assert "five seconds" in said, (
        "nothing on the page says how long the window is, so the bar is the only "
        "source of a fact a visitor may not be able to watch"
    )


@pytest.mark.browser
def test_a_recalculation_does_not_ask_again_or_offer_submit_again(page_at):
    """**Consent survives a recalculation, and round four must not reopen it.**
    `upsert_submission` revises the same row on the same token, and the flag this
    control set is not touched by an update - so a visitor who contributes, goes
    back to edit their data and recalculates is looking at the same contributed
    row with new figures on it. Showing them a fresh, unticked Submit button
    would be asking a question the server has already been told the answer to,
    and pressing it would be a second `/contribute` for a row already flagged.

    The Submit button specifically is what this asserts, because it is the new
    surface: the tick's own survival was already covered, and an implementation
    that reset only `contributeTicked` on the return trip would leave the box
    correctly ticked and still hand back a live Submit beside it.
    """
    sent = []
    page = _results_page(page_at, contribute_calls=sent)
    _contribute(page)
    assert len(sent) == 1, f"the first contribution sent {len(sent)} requests"
    assert page.locator(".contribute-status").count() == 1, "nothing was contributed to begin with"

    # Back to the review step and calculate again - the same token, revised.
    page.click('.results-page .step-nav [data-action="go-step"]')
    page.wait_for_selector('[data-action="calculate"]')
    page.click('.step-nav [data-action="calculate"]')
    page.wait_for_selector(".results-page", timeout=15000)
    page.wait_for_timeout(300)

    assert page.locator(".contribute-status").count() == 1, (
        "the done state was lost on a recalculation, so the visitor is asked again "
        "about a row that is already contributed"
    )
    assert page.locator("#contribute-action").count() == 0, (
        "a recalculation put the Submit button back over an already-contributed row"
    )
    control = page.locator("#contribute")
    assert control.is_checked() is True and control.is_disabled() is True, (
        "the tick came back editable after a recalculation"
    )
    assert len(sent) == 1, "the recalculation sent a second /contribute on its own"


#: Every box the position assertions below ask about, in one round trip. `null` for
#: an absent element is the answer, not an error: the whole point of the slot is that
#: it holds one occupant at a time and is sometimes empty.
_CONTRIBUTE_BOXES = """
() => {
  const box = selector => {
    const element = document.querySelector(selector);
    if (!element) return null;
    const r = element.getBoundingClientRect();
    return {left: Math.round(r.left), right: Math.round(r.right), top: Math.round(r.top),
            bottom: Math.round(r.bottom), width: Math.round(r.width), height: Math.round(r.height)};
  };
  const action = document.querySelector('#contribute-action');
  return {
    choice: box('.contribute-choice'), slot: box('.contribute-slot'),
    action: box('#contribute-action'), flower: box('.contribute-flower'),
    control: box('.contribute-control'),
    label: action ? action.textContent.trim() : null,
  };
}
"""


@pytest.mark.browser
def test_submit_undo_and_the_flower_take_one_position_in_turn(page_at):
    """**The action and the flower share one place, and that place is beside the
    tick.**

    Round four put the tick on the left and threw the action to the far right with
    `.contribute-action { margin-inline-start: auto }`, while the flower bloomed
    back at the tick's inline-end - so the button the visitor pressed and the thing
    that answered the press were at opposite edges of a 947px row with nothing
    joining them. `.contribute-slot` is the one position, immediately after the
    tick, and Submit, then Undo, then the flower occupy it in turn.

    **Boxes, not class names.** Every class here was present and correct while the
    two were at opposite ends of the row; what was wrong was two rectangles 340px
    apart. So three rectangles are compared, at the three moments, and the
    assertion is that they are the same rectangle.

    The handover is exact by construction rather than by timing: `done` empties the
    action and `contributeCelebrating` renders the flower, and
    `contributeCalculation` sets both in one `setState`, so the render that removes
    Undo is the render that plants the flower. That is what the third measurement
    below asserts - a flower present *and* `#contribute-action` gone, in the same
    observation.

    Mutations, both measured: restoring `margin-inline-start: auto` on the action
    puts Submit 393px after the tick in a 947px row and fails the first assertion.
    Putting the flower back where it was - `position: absolute` off
    `.contribute-choice`, rendered inside the tick's wrapper - blooms it at 622 where
    Undo stood at 630, and fails the handover assertion by those 8px. The second one
    matters: the flower was *already* beside the tick, so a test that only moved the
    button would have been satisfied by the two being roughly near each other.
    """
    page = _results_page(page_at, width=1280)
    page.route("**/api/v1/contribute", lambda route: route.fulfill(status=204))
    page.locator(".contribute-block").scroll_into_view_if_needed()
    page.wait_for_timeout(200)

    submit = page.evaluate(_CONTRIBUTE_BOXES)
    assert submit["label"] == "Submit", f"the control is not in its resting state ({submit['label']})"
    #: Beside the tick means beside the tick: one `.contribute-control` gap (14px)
    #: and no more. The failure this replaces measured 393.
    gap = submit["action"]["left"] - submit["choice"]["right"]
    assert 0 <= gap <= 24, (
        f"Submit sits {gap}px after the tick, in a row {submit['control']['width']}px wide. The "
        f"action is supposed to be brought to the flower's position, not left at the far edge"
    )

    page.locator("#contribute").check()
    _press_submit(page)
    page.wait_for_timeout(400)
    undo = page.evaluate(_CONTRIBUTE_BOXES)
    assert undo["label"] == "Undo", f"the grace window did not open ({undo['label']})"
    assert undo["action"]["left"] == submit["action"]["left"], (
        f"Undo appeared at {undo['action']['left']} where Submit was at "
        f"{submit['action']['left']}: the two are meant to be one slot, so the second must not "
        f"move under the press that produced it"
    )
    assert undo["action"]["top"] == submit["action"]["top"], (
        f"Undo is at {undo['action']['top']} and Submit was at {submit['action']['top']}"
    )

    page.wait_for_timeout(CONTRIBUTE_GRACE_MS + 300)
    bloom = page.evaluate(_CONTRIBUTE_BOXES)
    assert bloom["action"] is None, (
        "Undo is still on screen once the request has gone, so there is no handover to measure"
    )
    assert bloom["flower"] is not None, (
        "no flower at the moment Undo disappeared. It is gated on `contributeCelebrating` and on "
        "motion being allowed, and this context allows motion"
    )
    assert bloom["flower"]["left"] == undo["action"]["left"], (
        f"the flower blooms at {bloom['flower']['left']} and Undo was at "
        f"{undo['action']['left']}: the sequence is meant to happen in one position"
    )
    assert undo["action"]["top"] <= bloom["flower"]["top"] <= undo["action"]["bottom"], (
        f"the flower is at {bloom['flower']['top']}-{bloom['flower']['bottom']} and Undo occupied "
        f"{undo['action']['top']}-{undo['action']['bottom']}"
    )
    #: The row must not jump as the slot's occupant changes size - 96px of Submit,
    #: 83px of Undo, 44px of flower and then nothing at all.
    for moment, measured in (("Undo", undo), ("the bloom", bloom)):
        assert measured["control"]["height"] == submit["control"]["height"], (
            f"the contribute row changed height at {moment} "
            f"({submit['control']['height']} -> {measured['control']['height']}px), so the block "
            f"twitches under the visitor at every step of a sequence they are watching"
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
    # means to drive. Round four: the click only ticks, so Submit and its five
    # seconds are what actually reach the failing route.
    page.locator("#contribute").click()
    page.wait_for_timeout(200)
    _press_submit(page)
    page.wait_for_timeout(CONTRIBUTE_GRACE_MS + _GRACE_SLACK_MS)

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


def _equivalence_response(*, source_note, description=None, is_mock: bool = True):
    """A deep copy of `calculate_response.json` with its one equivalence replaced.

    `co2e`'s total in that fixture is `4449.0000000000` at one decimal place
    (`kg CO2e`), so `equivalenceBasis` has a real metric to read `source_metric`
    against. The factor itself - `0.0004149378`, displayed as `0.000414938` - and
    the source note are copied verbatim from the client's document via
    `data/upstream-factors-draft/build_upstream_factors_draft.py`'s `vehicles_year`
    row, not retyped from memory.

    `description` (v1.80, #127) is the sentence the disclosure now prints, and
    it defaults to `None` - the empty-field state - so that a caller has to
    name it to get one. `source_note` stays a REQUIRED argument although no
    surface prints it any more: every caller here passes a real one, which is
    what makes the empty-`description` cases evidence that nothing fell back
    to it rather than evidence that there was nothing to fall back to.

    `is_mock` defaults to the fixture's own `True`. It no longer gates
    anything inside the disclosure - the per-equivalence caveat is gone at
    v1.80 - and is kept because `results.js` still reads it for the
    PAGE-LEVEL placeholder banner, which is the obligation (§7.6 rule 2) and
    which `results_page_on_a_real_factor_set` exercises from the other side.
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
            "description": description,
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

#: Copied verbatim from the same row's `description` (v1.80, #127). **Nothing
#: like the note above, by design**: the two fields make different claims, and
#: a test whose two strings overlapped could not tell a correct read from a
#: fallback.
_VEHICLE_DESCRIPTION = (
    "The same greenhouse gases an average passenger vehicle puts out in a "
    "year of driving, counted as a number of vehicles."
)


@pytest.fixture
def results_page(page_at):
    """The results page, reached with one equivalence that carries both its
    sentence and its basis - so every assertion that the basis is NOT shown is
    made against a row that has one."""
    page = page_at(_equivalence_response(
        source_note=_VEHICLE_SOURCE_NOTE, description=_VEHICLE_DESCRIPTION,
    ))
    _submit_two_entries(page)
    return page


@pytest.fixture
def results_page_without_a_sentence(page_at):
    """v1.80, #127: `description` is nullable and `null` means print nothing.

    The row keeps its `source_note`, which is the point: this is the only
    state in which a fallback to the long version is observable, so the
    fixture is built so that one was available and must not have been taken.
    """
    page = page_at(_equivalence_response(
        source_note=_VEHICLE_SOURCE_NOTE, description=None,
    ))
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
    page = page_at(_equivalence_response(
        source_note=_VEHICLE_SOURCE_NOTE, description=_VEHICLE_DESCRIPTION,
        is_mock=False,
    ))
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
def test_the_explanation_shows_the_total_the_factor_and_the_sentence(results_page):
    """Asserts what a person can SEE, not that the text is in the DOM. A closed
    <details> still contains its text -- that is exactly how a folded warning
    passed tests/admin/test_guidance.py once already.

    v1.80 (#127): the third thing it shows is `description`, one sentence, and
    it is explicitly NOT `source_note` - the fixture row carries both, so this
    distinguishes the two rather than merely finding prose.
    """
    first = results_page.locator(".equivalent-grid article").first
    body = first.locator(".equivalent-basis__body")
    assert not body.is_visible()
    first.locator("details.equivalent-basis > summary").click()
    assert body.is_visible()
    text = body.inner_text()
    assert "0.000414938" in text
    assert _VEHICLE_DESCRIPTION in text
    assert "Client, Data sources for impact calculator" not in text, (
        "the provenance paragraph is back on the results page"
    )
    assert "Basis:" not in text


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
    """The negative case, on the page: the factor and the sentence still show,
    and the caveat that says the total comes from placeholder factors does not.

    **This test outlived the sentence it was written about** (v1.80, #127). The
    caveat is now absent on every factor set, so the assertion it makes about a
    real one is weaker than it was - and it is kept, because the mutation it
    guards against has happened in this repository: an unconditional version of
    that sentence made a real published set describe itself as placeholder data.
    Anything that reintroduces it fails here first. Its mock-side twin is
    `test_the_equivalence_caveat_is_in_no_export_at_all_now`, which asserts the
    stronger "absent either way" on the text export.
    """
    first = results_page_on_a_real_factor_set.locator(".equivalent-grid article").first
    first.locator("details.equivalent-basis > summary").click()
    body = first.locator(".equivalent-basis__body").inner_text()
    assert "0.000414938" in body
    assert _VEHICLE_DESCRIPTION in body
    assert "placeholder factors" not in body


@pytest.mark.browser
def test_an_equivalence_with_no_sentence_opens_onto_its_figures_and_not_the_note(
    results_page_without_a_sentence,
):
    """**The anti-fallback assertion, in a browser** (v1.80, #127).

    This test used to require the opposite: that a row with no `source_note`
    said *"The basis for this conversion is not recorded yet."* rather than
    opening onto nothing. The panel's content changed, so what it must not open
    onto changed with it. `description` is empty and `source_note` is NOT, so
    the only way the note can appear is a fallback - and the fallback is what
    #127 removed. The disclosure is not empty either: the three figures are
    still there, which is why the `?` still earns its place.
    """
    first = results_page_without_a_sentence.locator(".equivalent-grid article").first
    first.locator("details.equivalent-basis > summary").click()
    body = first.locator(".equivalent-basis__body")
    assert body.is_visible()
    text = body.inner_text()
    assert "0.000414938" in text
    assert "Client, Data sources for impact calculator" not in text, (
        "an equivalence with no description fell back to its source_note"
    )
    assert "not recorded" not in text.lower()
    assert "Basis:" not in text


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
        press_continue(page)
        page.wait_for_selector('input[name="food-category"]')
        press_continue(page)
        page.wait_for_selector("#total-waste")
        page.fill("#total-waste", "1000")
        press_continue(page)
        page.wait_for_selector('[data-line-field="amount"]')
        page.fill('[data-line-field="amount"] >> nth=0', "1000")
        page.wait_for_timeout(60)
        press_continue(page)
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
    page = page_at(_equivalence_response(
        source_note=_VEHICLE_SOURCE_NOTE, description=_VEHICLE_DESCRIPTION,
    ))
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
    #: v1.80 (#127). The sentence is what all three surfaces must now carry,
    #: and the provenance paragraph is what none of them may. The PDF reaches
    #: the real `/export/pdf` route, so its `description` is the PUBLISHED
    #: row's and not this response's - which is why the two
    #: must-not-appear strings below are asserted on all three surfaces and
    #: the sentence itself is asserted per surface a few lines down.
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
        assert basis not in content, (
            f"{surface} prints the provenance paragraph #127 removed"
        )
        assert disclaimer not in content, (
            f"{surface} prints the per-equivalence caveat #127 removed"
        )
        assert "Basis:" not in content, f"{surface} still labels a basis"
        assert name in content, f"{surface} is missing the equivalence's name"
    #: The screen and the text export read the mocked response, so they carry
    #: THIS row's sentence. The PDF recomputes against the published set, so it
    #: carries that set's - checked as "a sentence, from the published row"
    #: rather than as this literal, for the same reason the Total line below is
    #: checked by shape.
    for surface, content in (("screen", on_screen), ("text", text)):
        assert _VEHICLE_DESCRIPTION in content, (
            f"{surface} is missing the equivalence's own sentence"
        )
        match = total_line.search(content)
        assert match, f"{surface} is missing a formatted total for co2e"
        fraction_digits = len(match.group(1).split(".")[1])
        assert fraction_digits <= 3, (
            f"{surface}'s total {match.group(1)!r} is not display-formatted "
            f"-- {fraction_digits} fraction digits is the engine's own scale, "
            f"not a metric's display_precision"
        )

    # The sixth divergence, found by the scoped re-review that closed the
    # fifth: fixing the page's stuttering last row and leaving the PDF's
    # turned a wart both surfaces shared into a fresh disagreement between
    # them. The last row's content is the equivalence's own figure -- not
    # `row.label` printed a second time.
    #
    # The label sentence itself cannot be pinned to one literal string here,
    # for the same reason the Total line above cannot: the page and the text
    # export interpolate it from the mocked `value` ("1"), but the PDF's
    # route recomputes against the real entries and the real published
    # `vehicles_year` factor, so its own `value` -- and the whole numeral
    # inside the sentence -- is whatever that real division comes to. A
    # regex extracts each surface's own sentence rather than assuming it.
    label_pattern = re.compile(r"Equivalent to running [\d,.]+ passenger vehicles for a year")
    # `on_screen` is `.equivalent-basis__body`'s own text, and the card's
    # `<h3>` heading sits outside that element -- so the label sentence has
    # no reason to appear inside it at all once fixed. It used to, because
    # the dd literally contained `row.label`.
    assert not label_pattern.search(on_screen), (
        "screen repeats the label sentence inside the disclosure body"
    )
    # The text export and the PDF both print the label once, as their own
    # heading line; a second occurrence there is exactly the stutter.
    for surface, content in (("text", text), ("pdf", pdf)):
        match = label_pattern.search(content)
        assert match, f"{surface} is missing the equivalence's own label sentence"
        occurrences = content.count(match.group(0))
        assert occurrences == 1, (
            f"{surface} prints {match.group(0)!r} {occurrences} times -- it "
            f"should be the heading only, not repeated as the figure row too"
        )
    # And the figure row itself: `name` followed by `=` (pdf, screen) or `:`
    # (text) and a NUMBER, never the word "Equivalent" -- which is what the
    # stutter would put there instead.
    figure_pattern = re.compile(rf"{re.escape(name)}\s*[:=]\s*([\d,.]+)\b")
    for surface, content in (("screen", on_screen), ("text", text), ("pdf", pdf)):
        match = figure_pattern.search(content)
        assert match, f"{surface} is missing the equivalence's own figure beside its name"


#: The plan's own five widths, checked in both an RTL and an LTR language - the same
#: reason `test_horizontal_overflow.py` checks Arabic and German rather than English
#: alone: a physical `left`/`right` property reads correctly in one direction and
#: overflows, or sits on the wrong side, in the other.
EQUIVALENT_BASIS_WIDTHS = (320, 390, 700, 938, 1278)


@pytest.mark.browser
def test_the_equivalence_icons_share_one_baseline_where_the_cards_are_a_row(browser):
    """Above the breakpoint the three cards stretch to the tallest, so a "?" laid
    out after its own text sits at whatever height that text ended -- measured at
    700px as 21, 165 and 93px from each card's bottom, three icons on three
    different lines. They are pinned to the card's foot instead.

    **This test exists because the change that did it had nothing to detect its
    removal.** The assertion beside it checks the icon's INLINE placement and is
    load-bearing -- flipping `text-align: end` to `start` fails eight of its ten
    parametrisations -- but nothing looked at the vertical, so reverting the two
    lines that do this left the suite green.

    Asserted at 700px only: below 650px the grid is a single column, every card
    sizes to its own content, and there is no misalignment to fix. Asserting a
    shared baseline there would pin a coincidence.
    """
    #: Three equivalences whose **labels** differ in length, and the labels are
    #: what matters: the panel's own prose sits inside a collapsed `<details>`,
    #: so varying it changes no card's height and the test passes whatever the
    #: stylesheet does. Measured that the wrong way round first -- three
    #: different notes, three identical heights, green under mutation. (v1.80
    #: swapped which field holds that prose and changed nothing about this: a
    #: collapsed box contributes no height whatever is in it.)
    response = _equivalence_response(
        source_note=_VEHICLE_SOURCE_NOTE, description=_VEHICLE_DESCRIPTION,
    )
    template = response["totals"]["current"]["equivalences"][0]
    response["totals"]["current"]["equivalences"] = [
        dict(template, code="short", name="Short", label="One short line"),
        dict(template, code="medium", name="Medium",
             label="A label of a middling length that takes up about two lines here"),
        dict(template, code="long", name="Long",
             label="A deliberately long label that wraps onto several lines so that this "
                   "card is taller than both of the others beside it in the same row"),
    ]

    context = browser.new_context(viewport={"width": 700, "height": 900}, locale="en-NZ")
    try:
        page = context.new_page()
        page.route(
            "**/api/v1/calculate*",
            lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps(response),
            ),
        )
        try:
            page.goto(CALCULATOR_URL, wait_until="networkidle", timeout=15000)
        except Exception as error:  # pragma: no cover - environment guard
            pytest.skip(f"the front end is not being served at {CALCULATOR_URL}: {error}")
        page.wait_for_selector('[data-action="start"]', timeout=10000)
        _submit_two_entries(page)
        page.wait_for_selector(".equivalent-grid article")

        gaps = page.evaluate(
            """() => [...document.querySelectorAll('.equivalent-grid article')].map(card => {
              const basis = card.querySelector('.equivalent-basis')
              if (!basis) return null
              return Math.round(card.getBoundingClientRect().bottom
                                - basis.getBoundingClientRect().bottom)
            })"""
        )
        assert len(gaps) >= 2 and None not in gaps, gaps
        assert len(set(gaps)) == 1, (
            "the equivalence explanations do not sit at the same height in their "
            f"cards, so they are following their own text rather than the card: {gaps}"
        )
    finally:
        context.close()


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
                body=json.dumps(_equivalence_response(
                    source_note=_VEHICLE_SOURCE_NOTE,
                    description=_VEHICLE_DESCRIPTION,
                )),
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


# --------------------------------------- the expanded panel's surface (#143)
#
# **The client's words were about the expanded state, not the content** — 展开
# 之后太难看, at the 1 October meeting. v1.80 (#127) shortened what the panel
# holds; this is the surface it holds it on. The panel was an opaque `#fff`
# rectangle inside a Banana-tinted card: the heaviest thing either surface could
# have done, maximum contrast against the one ground it sits on, drawn at the
# moment a reader asked a small question. It now takes the shared frosted-glass
# token (`--glass-ground` / `--glass-blur` / `--glass-edge` in
# `web/css/styles.css`), the treatment the results page's floating section nav
# has carried since v1.66.
#
# **Four properties are measured here and none of them is a class name.** A
# translucent panel's contrast ratio is not what the stylesheet says it is — the
# ground it composites onto decides, and `saturate(1.4)` in the blur moves it
# again — so the ground is read off a PIXEL the compositor painted, and the
# ratio is computed from that. The fallback is exercised the only way a browser
# that has `backdrop-filter` can exercise it: the token's own default is forced
# on, the filter is taken off the element, and the card behind it is painted
# Beetroot. An opaque ground does not move; a merely-lighter one does.

#: 320 is the floor `test_horizontal_overflow.py` measures at and the width the
#: panel is narrowest at; 1278 is the three-column grid, where the card is a
#: flex column and the disclosure is pushed to its foot by an auto margin. The
#: two are different layouts for the same panel, and a surface that is legible
#: in one is not thereby legible in the other — `saturate(1.4)` composites over
#: whatever the card is actually painted, and the card is painted the same at
#: both, which is the thing being established rather than assumed.
GLASS_WIDTHS = (320, 1278)

#: Decode a PNG the browser produced, in the browser, and hand back one pixel.
#: **Pillow is not a dependency of this project** (`requirements.txt` says so
#: beside `qrcode`), and a test that skips when it is absent is a test that
#: measures nothing on a machine that never had it — rule 2 of the round this
#: landed in. Chromium already has a PNG decoder and a canvas, so the screenshot
#: goes back in as a data URL and `getImageData` answers. The image is same-origin
#: data, so the canvas is not tainted.
PAINTED_PIXEL = """
([b64, x, y]) => new Promise(resolve => {
  const img = new Image()
  img.onload = () => {
    const canvas = document.createElement('canvas')
    canvas.width = img.width
    canvas.height = img.height
    const context = canvas.getContext('2d')
    context.drawImage(img, 0, 0)
    const data = context.getImageData(x, y, 1, 1).data
    resolve([data[0], data[1], data[2], data[3]])
  }
  img.src = 'data:image/png;base64,' + b64
})
"""


def _painted(page, locator, x, y):
    """The colour actually painted at (x, y) of `locator`'s own box."""
    shot = base64.b64encode(locator.screenshot()).decode()
    return tuple(page.evaluate(PAINTED_PIXEL, [shot, x, y]))


#: WCAG 2.x relative luminance and ratio. **A fourth copy in this suite**, with
#: the other three in `test_results_floating_nav_browser.py`,
#: `test_i18n_browser.py` and `test_d_statistics_content.py`. Kept local rather
#: than imported from one of those: importing a sibling test module runs its
#: module-level `importorskip` and its fixtures into this one's collection, and
#: six lines of arithmetic out of a published formula is the cheaper duplicate.
def _relative_luminance(colour):
    channels = []
    for part in colour[:3]:
        value = part / 255.0
        channels.append(value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast(first, second):
    high, low = sorted((_relative_luminance(first), _relative_luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _rgb(colour):
    """`rgb(r, g, b)` / `rgba(r, g, b, a)` to a 3-tuple of integers."""
    return tuple(int(float(part)) for part in re.findall(r"[\d.]+", colour)[:3])


#: The token, read out of the live stylesheet rather than retyped here: the rule
#: that declares `backdrop-filter`, the `@supports` condition that upgrades its
#: ground, and the default that condition upgrades FROM. A test that hard-coded
#: `rgba(255, 255, 255, 0.72)` would still pass after the stylesheet stopped
#: saying it.
READ_THE_TOKEN = """
() => {
  const found = {consumers: null, condition: null, fallback: null, upgraded: null}
  for (const sheet of document.styleSheets) {
    let rules
    try { rules = sheet.cssRules } catch (error) { continue }
    for (const rule of rules) {
      if (rule.type === CSSRule.STYLE_RULE
          && rule.style.getPropertyValue('backdrop-filter')
          && !found.consumers) {
        found.consumers = rule.selectorText
      }
      if (rule.type === CSSRule.STYLE_RULE && rule.selectorText === ':root'
          && rule.style.getPropertyValue('--glass-ground')) {
        found.fallback = rule.style.getPropertyValue('--glass-ground').trim()
      }
      if (rule.type === CSSRule.SUPPORTS_RULE && /backdrop-filter/.test(rule.conditionText)) {
        found.condition = rule.conditionText
        for (const inner of rule.cssRules) {
          const value = inner.style && inner.style.getPropertyValue('--glass-ground')
          if (value) found.upgraded = value.trim()
        }
      }
    }
  }
  return found
}
"""


def _open_the_first_explanation(page):
    page.wait_for_selector(".equivalent-grid article")
    page.locator("details.equivalent-basis > summary").first.click()
    page.wait_for_selector(".equivalent-basis__body")
    return page.locator(".equivalent-basis__body").first


@pytest.mark.browser
@pytest.mark.parametrize("width", GLASS_WIDTHS)
def test_the_open_explanation_is_a_translucent_light_layer(page_at, width):
    """**Measured from the pixel, not from the declaration** (#143).

    What #143 asks for is that the panel read as a light layer over the card
    rather than a block dropped into it, in the brand's light-ground/Kale-text
    pairing. Three things make that true and all three are asserted here:

      * the ground is translucent, so the card's own colour reaches through it —
        the painted pixel is NOT `#ffffff`, which is exactly what it was before;
      * it is nevertheless LIGHTER than the card it sits on, so it reads as above
        the card and not as a hole in it;
      * the text on it is Kale, and both the figures and the staff sentence clear
        4.5:1 against the ground that was painted rather than the one declared.

    Measured on the running stack: the card composites to `rgb(255, 247, 226)`
    (Banana at 0.2 over white) and the panel to **`rgb(255, 253, 245)`** — Kale
    at **13.93:1** and `--muted` at **6.80:1**. The arithmetic alone predicts
    `(255, 253, 247)`: `saturate(1.4)` in the blur pulls the backdrop's own tint
    up, which is the whole reason this reads a pixel instead of compositing two
    `rgba()` values in Python.
    """
    page = page_at(_equivalence_response(
        source_note=_VEHICLE_SOURCE_NOTE, description=_VEHICLE_DESCRIPTION,
    ), width=width)
    _submit_two_entries(page)
    page.wait_for_selector(".equivalent-grid article")

    card = page.locator(".equivalent-grid article").first
    closed_box = card.bounding_box()
    assert closed_box is not None
    #: x=10 is inside the card's own 20px inline padding and y is inside its
    #: block padding, so this is the card's ground and not a glyph.
    card_ground = _painted(page, card, 10, round(closed_box["height"]) - 10)

    body = _open_the_first_explanation(page)
    drawn = page.evaluate(
        """() => {
          const body = document.querySelector('.equivalent-basis__body')
          const note = document.querySelector('.equivalent-basis__note')
          const style = getComputedStyle(body)
          return {
            background: style.backgroundColor,
            colour: style.color,
            backdrop: style.backdropFilter || style.webkitBackdropFilter || 'none',
            noteColour: note ? getComputedStyle(note).color : null,
          }
        }"""
    )
    #: 6, 6 clears the 1px hairline and sits in the panel's own padding
    #: (12px block, 14px inline), ahead of the first `<dt>`.
    ground = _painted(page, body, 6, 6)

    assert drawn["backdrop"] != "none", (
        f"the panel declares no backdrop-filter at {width}px: {drawn['backdrop']}. "
        f"Without one a translucent ground is just transparency"
    )
    assert drawn["colour"] == "rgb(0, 50, 35)", (
        f"the panel's text is {drawn['colour']}, not Kale. It sits on a light ground, "
        f"and the brand pairs a light ground with Kale text"
    )
    assert len(re.findall(r"[\d.]+", drawn["background"])) == 4, (
        f"the panel's declared ground is {drawn['background']}, which carries no alpha: "
        f"an opaque ground is the block #143 asked to be rid of"
    )
    assert ground[:3] != (255, 255, 255), (
        f"the panel painted {ground} at {width}px - pure white, so nothing of the card "
        f"reads through it and it is still the opaque rectangle it was"
    )
    assert sum(ground[:3]) > sum(card_ground[:3]), (
        f"the panel painted {ground} over a card painted {card_ground} at {width}px: it is "
        f"no lighter than the card, so it reads as a hole in the card rather than a layer "
        f"above it"
    )
    for name, colour in (("the figures", drawn["colour"]), ("the sentence", drawn["noteColour"])):
        if colour is None:
            continue
        ratio = _contrast(_rgb(colour), ground)
        assert ratio >= 4.5, (
            f"{name} measure {ratio:.2f}:1 ({colour} on the painted ground {ground}) at "
            f"{width}px - under the 4.5:1 body text needs. A translucent panel's ratio is "
            f"decided by what it composites onto, not by what the stylesheet declares"
        )


@pytest.mark.browser
def test_the_question_mark_is_a_native_disclosure_with_a_name(page_at):
    """**The restyle is paint, and these are the two things paint must not cost**
    (#143): the keyboard a native `<details>` gives for nothing, and a name for a
    control whose only glyph is `?`.

    Both are asked of the platform rather than of the markup. The name is read out
    of Chromium's accessibility tree, not off the `aria-label` attribute — an
    attribute present in the DOM is not the same claim as a name the platform
    computes, and `?` is what a screen reader reaches for when there is none.
    The keyboard is Enter on the focused summary, twice, which is also the only
    route a keyboard visitor has into this panel at all.
    """
    page = page_at(_equivalence_response(
        source_note=_VEHICLE_SOURCE_NOTE, description=_VEHICLE_DESCRIPTION,
    ))
    _submit_two_entries(page)
    page.wait_for_selector(".equivalent-grid article")

    shape = page.evaluate(
        """() => {
             const details = document.querySelector('.equivalent-basis')
             const summary = details.firstElementChild
             return {details: details.tagName, summary: summary.tagName,
                     glyph: summary.textContent.trim()}
           }"""
    )
    assert shape["details"] == "DETAILS" and shape["summary"] == "SUMMARY", (
        f"the disclosure is {shape}: a div pair gets no keyboard and no open state for free"
    )
    assert shape["glyph"] == "?", shape

    summary = page.locator("details.equivalent-basis > summary").first
    #: Chromium's own accessible-name computation, over CDP. `page.accessibility`
    #: was removed from Playwright, and the alternative - reading `aria-label`
    #: back - asserts that an attribute was typed rather than that a name was
    #: computed from it.
    session = page.context.new_cdp_session(page)
    session.send("DOM.enable")
    session.send("Accessibility.enable")
    root = session.send("DOM.getDocument")["root"]["nodeId"]
    node = session.send("DOM.querySelector", {
        "nodeId": root, "selector": "details.equivalent-basis > summary"})["nodeId"]
    assert node, "the summary is not in the document"
    tree = session.send("Accessibility.getPartialAXTree", {
        "nodeId": node, "fetchRelatives": False})["nodes"]
    assert tree, "the summary is not in the accessibility tree at all"
    name = (tree[0].get("name", {}).get("value") or "").strip()
    assert len(name) > 1 and name != "?", (
        f"the summary's accessible name is {name!r} - a screen reader announces the glyph, "
        f"which says nothing about what opens (the node was {tree[0]})"
    )

    body = page.locator(".equivalent-basis__body").first
    summary.focus()
    page.keyboard.press("Enter")
    page.wait_for_selector(".equivalent-basis__body")
    assert body.is_visible(), "Enter on the focused summary did not open the panel"
    page.keyboard.press("Enter")
    assert not body.is_visible(), "Enter on the focused summary did not close the panel again"


@pytest.mark.browser
def test_the_glass_falls_back_to_an_opaque_ground_and_not_a_lighter_one(page_at):
    """**The fallback is the half nobody looks at, so it is the half measured.**

    Without `backdrop-filter` a translucent white is simply transparent, and the
    panel's text would be read against whatever is behind it. The token therefore
    declares `--glass-ground` **opaque** and upgrades it inside `@supports`,
    which is the inverse of the `@supports not (...)` spelling the floating nav
    carried alone: inverted, there is no second selector list for a new consumer
    to be left out of, and a browser with no `@supports` at all gets the opaque
    ground rather than the translucent one.

    **Chromium cannot evaluate the prefixed half of that condition.** Measured:
    `CSS.supports('(-webkit-backdrop-filter: blur(1px))')` is **false** in the
    Chromium this suite drives, while the unprefixed property is true and is what
    paints. So "a browser with only the prefixed spelling stays on the glass
    path" cannot be evaluated directly here; what is asserted instead is the
    condition's own shape, read off the live rule — a disjunction, not negated,
    with the prefixed spelling as one of its terms — plus a disjunction of the
    same shape with a false term and a true one standing in, evaluated by the
    browser itself. Safari shipped `-webkit-backdrop-filter` alone for years and
    treating that as unsupported would hand a working browser the fallback.

    The opacity itself is measured rather than parsed: the fallback ground is
    forced on, the filter is taken off the element, and the card behind it is
    painted Beetroot. Measured, the panel paints `rgb(255, 255, 255)` either way
    — Kale at **14.18:1**, `--muted` at **6.93:1** — and a ground that let
    Beetroot through would have moved.
    """
    page = page_at(_equivalence_response(
        source_note=_VEHICLE_SOURCE_NOTE, description=_VEHICLE_DESCRIPTION,
    ), width=1278)
    _submit_two_entries(page)
    body = _open_the_first_explanation(page)

    token = page.evaluate(READ_THE_TOKEN)
    condition = token["condition"]
    assert condition, "no @supports rule mentioning backdrop-filter is in the stylesheet"
    assert token["fallback"], (
        "no :root rule outside the @supports block declares --glass-ground, so there is "
        "no fallback ground at all"
    )
    assert token["upgraded"], (
        f"the @supports block {condition} upgrades no --glass-ground, so the token's "
        f"ground is not what it gates"
    )
    assert "not" not in condition.lower(), (
        f"the condition is {condition}: negated, the fallback is what a browser WITH "
        f"backdrop-filter would be handed"
    )
    assert " or " in condition, (
        f"the condition is {condition}, which is not a disjunction - one spelling of the "
        f"property decides it, and Safari shipped only the prefixed one for years"
    )
    assert "-webkit-backdrop-filter" in condition, (
        f"the condition is {condition} and does not mention the prefixed spelling at all"
    )
    #: The condition's shape, evaluated rather than read: one false term and one
    #: true term joined the way the live rule joins its two, and the browser
    #: answers true. The prefixed term is one of the live rule's two terms
    #: (asserted above), so a browser for which only it is true takes this path.
    shape = page.evaluate(
        """condition => ({
             asWritten: CSS.supports(condition),
             prefixedSpellingAlone: CSS.supports('(-webkit-backdrop-filter: blur(1px))'),
             eitherTermSuffices: CSS.supports('((kai-not-a-property: 1px) or (color: red))'),
           })""",
        condition,
    )
    assert shape["asWritten"], (
        f"this browser does not satisfy {condition}, so everything the test above measured "
        f"was the fallback and not the glass"
    )
    assert shape["eitherTermSuffices"], (
        "a disjunction with one false term and one true term did not evaluate true in this "
        "browser, so nothing can be concluded about the shape of the live condition"
    )
    assert shape["prefixedSpellingAlone"] is False, (
        "this Chromium now reports support for -webkit-backdrop-filter. The docstring's "
        "reason for standing a term in rather than evaluating the real one is stale: "
        "evaluate the real one"
    )

    opaque = page.evaluate(
        """fallback => {
             document.documentElement.style.setProperty('--glass-ground', fallback)
             const body = document.querySelector('.equivalent-basis__body')
             body.style.backdropFilter = 'none'
             body.style.webkitBackdropFilter = 'none'
             return getComputedStyle(body).backgroundColor
           }""",
        token["fallback"],
    )
    on_the_card = _painted(page, body, 6, 6)
    page.evaluate(
        """() => { document.querySelector('.equivalent-grid article').style.background = '#87005a' }"""
    )
    on_beetroot = _painted(page, body, 6, 6)

    assert on_the_card == on_beetroot, (
        f"with the fallback ground in force the panel paints {on_the_card} over the card and "
        f"{on_beetroot} over Beetroot: the fallback is translucent, so it is merely lighter "
        f"and the text is read against whatever is behind it ({opaque})"
    )
    for name, colour in (("the figures", "rgb(0, 50, 35)"), ("the sentence", "rgb(70, 95, 86)")):
        ratio = _contrast(_rgb(colour), on_the_card)
        assert ratio >= 4.5, (
            f"on the fallback ground {on_the_card}, {name} measure {ratio:.2f}:1 - under "
            f"the 4.5:1 body text needs"
        )


@pytest.mark.browser
def test_the_glass_is_a_containing_block_for_nothing_positioned(page_at):
    """**`backdrop-filter` creates a containing block**, and the stylesheet near
    `.step-nav` already carries what that broke before.

    The consumers are read out of the token's own selector list rather than named
    here, so a selector added to it is covered the day it is added — on this page.
    Today that is the equivalence panel and the floating section nav; the step
    cards (#134's chrome, #138, #142) are not on the results page and are the
    reason the hazard is written down: step 3's term tooltips are
    `position: absolute` inside a card body, and `position: absolute` inside a
    glass surface resolves against the glass rather than against whatever it was
    written for.

    The panel holds a `<dl>` and a `<p>` and must keep holding nothing
    positioned. The floating nav's own `position: absolute` panel is a consumer
    rather than a descendant of one, which is why the nav is measured by
    `test_results_floating_nav_browser.py` and only its contents are asked about
    here.
    """
    page = page_at(_equivalence_response(
        source_note=_VEHICLE_SOURCE_NOTE, description=_VEHICLE_DESCRIPTION,
    ), width=1278)
    _submit_two_entries(page)
    _open_the_first_explanation(page)

    token = page.evaluate(READ_THE_TOKEN)
    assert token["consumers"], "no rule in the stylesheet declares backdrop-filter"
    positioned = page.evaluate(
        """selector => {
             const surfaces = [...document.querySelectorAll(selector)]
             return {
               surfaces: surfaces.length,
               offenders: surfaces.flatMap(surface =>
                 [...surface.querySelectorAll('*')]
                   .filter(node => getComputedStyle(node).position !== 'static')
                   .map(node => `${selector} > ${node.tagName.toLowerCase()}`
                                + `.${node.className} is ${getComputedStyle(node).position}`)),
             }
           }""",
        token["consumers"],
    )
    assert positioned["surfaces"] >= 2, (
        f"only {positioned['surfaces']} of the token's consumers ({token['consumers']}) are "
        f"on this page, so this measured less than it claims to"
    )
    assert positioned["offenders"] == [], (
        f"a glass surface is the containing block for {positioned['offenders']}: "
        f"backdrop-filter makes it one, and that element's offsets now resolve against the "
        f"glass instead of against whatever they were written for"
    )


@pytest.mark.browser
@pytest.mark.parametrize("width", GLASS_WIDTHS)
def test_opening_the_explanation_leaves_its_own_card_where_it_was(page_at, width):
    """**Opening must not shift the card layout under the reader's eye** (#143).

    What the restyle owes is that it moved no box: the panel keeps the margin,
    the padding, the 1px hairline and the in-flow block it already had, and only
    the paint changed. So the opened card's own top is unchanged, the panel stays
    inside that card's box, and a card sharing the opened card's grid row keeps
    its top too.

    **Two movements are real, pre-existing, and deliberately not asserted
    against.** Measured on three cards: at 1278 the three sit in one grid row, so
    opening the first grows the row and the auto margin that pushed every `?` to
    its card's foot gives up its slack — the opened card's own summary rises
    1960 → 1840 and its two neighbours' fall 1960 → 2048. At 320 the grid is one
    column and the cards below the opened one move down, 2811 → 3067. Both are
    what an in-flow `<details>` in a stretch-aligned grid row does; neither is
    the paint, and a test that forbade them would be a test against the
    disclosure existing.
    """
    response = _equivalence_response(
        source_note=_VEHICLE_SOURCE_NOTE, description=_VEHICLE_DESCRIPTION,
    )
    template = response["totals"]["current"]["equivalences"][0]
    response["totals"]["current"]["equivalences"] = [
        dict(template, code="short", name="Short", label="One short line"),
        dict(template, code="medium", name="Medium",
             label="A label of a middling length that takes up about two lines here"),
        dict(template, code="long", name="Long",
             label="A deliberately long label that wraps onto several lines so that this "
                   "card is taller than both of the others beside it in the same row"),
    ]
    page = page_at(response, width=width)
    _submit_two_entries(page)
    page.wait_for_selector(".equivalent-grid article")

    geometry = """
    () => [...document.querySelectorAll('.equivalent-grid article')].map(card => {
      const box = card.getBoundingClientRect()
      const body = card.querySelector('.equivalent-basis__body')
      return {
        top: Math.round(box.top + window.scrollY),
        bottom: Math.round(box.bottom + window.scrollY),
        bodyBottom: body
          ? Math.round(body.getBoundingClientRect().bottom + window.scrollY) : null,
        bodyPosition: body ? getComputedStyle(body).position : null,
      }
    })
    """
    before = page.evaluate(geometry)
    assert len(before) == 3, before
    _open_the_first_explanation(page)
    after = page.evaluate(geometry)

    assert after[0]["top"] == before[0]["top"], (
        f"opening the explanation moved its own card's top from {before[0]['top']} to "
        f"{after[0]['top']} at {width}px"
    )
    assert after[0]["bodyPosition"] == "static", (
        f"the open panel is {after[0]['bodyPosition']} at {width}px. An out-of-flow panel is "
        f"how the admin panel's help marker once overflowed a 320px viewport by 27px"
    )
    assert after[0]["bodyBottom"] is not None
    assert after[0]["bodyBottom"] <= after[0]["bottom"] + 1, (
        f"the open panel ends at {after[0]['bodyBottom']} and its card at "
        f"{after[0]['bottom']} at {width}px: it is hanging out of the card it belongs to"
    )
    row = [index for index, card in enumerate(before) if card["top"] == before[0]["top"]]
    for index in row:
        assert after[index]["top"] == before[index]["top"], (
            f"card {index} shares the opened card's grid row and its top moved from "
            f"{before[index]['top']} to {after[index]['top']} at {width}px"
        )


# ------------------------------------------------- the reporting period (v1.68)
#
# Item ⑦ gained an interval at v1.67 and a sentence that can say it at v1.68.
# Everything below is driven through `buildResultsReport` and `renderResults`
# from one state in one Node process, because §7.3c requires the screen and the
# file to carry the SAME sentence and a test of one of them proves nothing about
# the other — the fault this whole file was written against.


def _state_with_period(time_frame, start="", end=""):
    """`build_state`'s shape with §6.2's three period fields set.

    The two instants are in the wire's own shape, `YYYY-MM-DDTHH:MM`, because
    that is what `state.periodStart` holds — a hand-written `Date` here would
    be testing a state the calculator cannot reach.
    """
    state = build_state()
    state["timeFrame"] = time_frame
    state["periodStart"] = start
    state["periodEnd"] = end
    return state


#: The client's own example: a shift, 08:10 to 16:20.
SHIFT = ("2026-09-14T08:10", "2026-09-14T16:20")


@node
def test_the_export_prints_a_hand_picked_period_as_its_two_instants(tmp_path):
    """The shift, in the downloaded file.

    **`dd/mm/yyyy`, not `2026-09-14T08:10`.** The wire shape is what the API
    stores; what a person opens has to read the way the box they typed it into
    read, which is `en-NZ` — the pin `web/js/stats.js` and `web/js/home.js`
    carry and the reason written beside them is O-4.
    """
    report = report_for(tmp_path, _state_with_period("custom", *SHIFT))
    assert "These figures cover: 14/09/2026 08:10 – 14/09/2026 16:20" in report
    assert "2026-09-14T08:10" not in report, (
        "the file printed the wire shape rather than the period as a person reads it"
    )
    assert "custom" not in report.lower(), (
        "the file printed the vocabulary word instead of the dates"
    )


@node
def test_the_export_prints_a_preset_and_the_interval_it_filled(tmp_path):
    """v1.67's normal case. Both halves, for the reason the line exists: the
    phrase records which shortcut was pressed and the interval records what it
    filled, and the client's first question of this column needs both to be
    answerable."""
    report = report_for(tmp_path, _state_with_period("one_week", *SHIFT))
    assert (
        "These figures cover: One week · 14/09/2026 08:10 – 14/09/2026 16:20" in report
    )


@node
def test_the_export_still_prints_a_preset_that_filled_nothing(tmp_path):
    """Every submission made before v1.67 is this shape and must go on reading
    as what it always read as — with no dangling separator where the dates
    would have been."""
    report = report_for(tmp_path, _state_with_period("one_month"))
    assert "These figures cover: One month" in report
    assert "These figures cover: One month ·" not in report


@node
def test_the_export_prints_no_period_line_when_none_was_stated(tmp_path):
    """"Not stated" renders nothing, the same way an unstated money figure
    does — never the label with nothing after it."""
    assert "These figures cover" not in report_for(tmp_path, _state_with_period(""))


@node
def test_the_screen_and_the_file_carry_the_identical_period_sentence(tmp_path):
    """§7.3c, and the reason `periodLine` and `resultsPeriod` share one
    function rather than each formatting the period themselves.

    A visitor reads the line on the results page and then opens the file they
    downloaded from it. Two sentences about one fact is two sentences the day
    one of them is reworded — and this is asserted across all three shapes,
    because a helper that agreed on the simple case and diverged on the preset
    one would pass a single-state check.
    """
    for time_frame, start, end in (
        ("custom", *SHIFT),
        ("one_week", *SHIFT),
        ("one_year", "", ""),
    ):
        state = _state_with_period(time_frame, start, end)
        report = report_for(tmp_path, state)
        screen = screen_for(tmp_path, state)
        sentence = next(
            line for line in report.splitlines() if line.startswith("These figures cover")
        )
        assert sentence in screen, (
            f"{time_frame}: the page and the file word the period differently.\n"
            f"file: {sentence!r}"
        )
