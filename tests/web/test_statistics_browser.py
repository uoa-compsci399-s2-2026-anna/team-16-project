"""The statistics page's charts, measured in a browser rather than read.

**Why this file exists at all.** Defects 1 and 2 in this page were the "green
markup test, broken page" pattern this repository keeps hitting: the doughnut's
tenth legend entry was drawn at `top: 324` on a 320px canvas — outside it,
invisible, with no scrollbar to reach it — and the tenth bucket repeated the
first bucket's colour. Every static assertion about `charts.js` passed
throughout. The only thing that settles either question is measuring what the
runtime actually laid out.

**The clipped bucket was `Other (sample too small)`, every time**, because it is
always last. §6.4 devotes a paragraph to `other` precisely because on a young
data set it can be the largest row in the breakdown, and launch day is the day
the page is most likely to be screenshotted.

**The fixture is fulfilled in the browser rather than read from the API.**
`tests/fixtures/stats.json` is the canonical ten-bucket response and is not
served by nginx; more to the point, the running stack currently has eleven
calculations in it and would produce two buckets, which is a page on which
every assertion below passes and none of them means anything.

Requires the stack: `docker compose -f docker/compose.yaml up -d --build web`.
Skipped, never failed, when it is not up.

**Mutation hook.** `KAICALC_MUTATION_CSS` is injected after load, the same hook
`test_step_navigation.py` uses, so each rule these measurements depend on can be
knocked out and the test watched to fail.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.browser

pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to measure where a legend entry is drawn; it is unverified without it",
)

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # pragma: no cover - import path guard
    sys.path.insert(0, str(ROOT))

from tests.support import red_line  # noqa: E402

BASE = os.environ.get("KAICALC_WEB_URL", "http://localhost:18080")
STATS = json.loads((ROOT / "tests" / "fixtures" / "stats.json").read_text(encoding="utf-8"))
MUTATION_CSS = os.environ.get("KAICALC_MUTATION_CSS", "")

#: The owner's two real viewports, the phone, and the 320px floor. The defect
#: was measured at 390; 320 is kept because a legend that fits at 390 and not at
#: 320 is the same defect one width down.
VIEWPORTS = [
    pytest.param(1278, 983, 1.25, id="1278x983"),
    pytest.param(938, 898, 1.5, id="938x898"),
    pytest.param(390, 700, 3.0, id="390x700"),
    pytest.param(320, 700, 2.0, id="320x700"),
]

#: Every chart instance's legend geometry, in the canvas's own coordinate space.
#: `legendHitBoxes` is what Chart.js hit-tests against, so it is also what a
#: reader can click; measuring the drawn text instead would measure something
#: nobody can press.
LEGENDS = """
() => Object.values(Chart.instances).map((chart) => {
  const boxes = (chart.legend && chart.legend.legendHitBoxes) || [];
  const rect = chart.canvas.getBoundingClientRect();
  return {
    type: chart.config.type,
    legendShown: chart.options?.plugins?.legend?.display !== false,
    buckets: chart.data.labels.length,
    entries: boxes.length,
    canvasHeight: Math.round(rect.height),
    canvasWidth: Math.round(rect.width),
    bottoms: boxes.map((box) => Math.round(box.top + box.height)),
    rights: boxes.map((box) => Math.round(box.left + box.width)),
    colours: chart.data.datasets[0].backgroundColor,
  };
})
"""


def _stack_is_up() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE}/stats.html", timeout=3) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):  # pragma: no cover - environment guard
        return False


if not _stack_is_up():  # pragma: no cover - environment guard
    pytest.skip(
        f"the stack is not answering on {BASE}; run "
        "`docker compose -f docker/compose.yaml up -d --build web`",
        allow_module_level=True,
    )


#: `browser` itself now comes from `tests/web/conftest.py`, package-scoped
#: and shared across every file in this directory - see that module's
#: docstring for why a per-file fixture corrupted the rest of the run.
@pytest.fixture
def stats_page(browser):
    """The statistics page at a viewport, rendered from the canonical fixture."""
    contexts = []

    def open_page(width, height, dpr=1.0):
        # `bypass_csp` for the mutation hook only - see the note in
        # test_step_navigation.py. `test_csp.py` measures the policy itself
        # with no bypass, which is where a directive that breaks a page fails.
        context = browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=dpr,
            locale="en-NZ",
            bypass_csp=True,
        )
        contexts.append(context)
        page = context.new_page()
        page.route(
            "**/api/v1/stats",
            lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps(STATS)
            ),
        )
        page.goto(f"{BASE}/stats.html", wait_until="networkidle", timeout=20000)
        if MUTATION_CSS:
            page.add_style_tag(content=MUTATION_CSS)
        # Three breakdowns, so three chart instances. Waiting on `networkidle`
        # alone would measure a page whose charts have not been laid out, and a
        # legend with no entries is inside every canvas there has ever been.
        page.wait_for_function(
            "() => window.Chart && Object.keys(Chart.instances).length >= 3", timeout=20000
        )
        page.wait_for_timeout(400)
        return page

    yield open_page
    for context in contexts:
        context.close()


@pytest.mark.parametrize("width, height, dpr", VIEWPORTS)
def test_every_legend_entry_is_drawn_inside_its_canvas(stats_page, width, height, dpr):
    """**The one assertion that would have caught defect 1.**

    Measured before the fix at 390px: `legendHitBoxes[9] = {top: 324}` on a
    320px canvas. The entry existed, was in the DOM's accessibility tree by way
    of the equivalent text list, and was drawn nine pixels past the bottom edge
    of the surface it was drawn on.

    The count assertion beside it is what stops this passing vacuously. A legend
    switched off, or one that laid out no entries, satisfies "no entry is
    outside the canvas" perfectly, and that is the shape this project's defect
    list is full of.
    """
    page = stats_page(width, height, dpr)
    charts = page.evaluate(LEGENDS)
    assert len(charts) == 3, f"expected three charts, measured {len(charts)}"

    legended = [chart for chart in charts if chart["legendShown"]]
    assert legended, "no chart on this page draws a legend, so this measures nothing"

    for chart in legended:
        assert chart["entries"] == chart["buckets"], (
            f"{chart['type']}: {chart['entries']} legend entries for {chart['buckets']} "
            "buckets - a legend that is not drawing every bucket cannot be checked for "
            "one drawn outside the canvas"
        )
        assert chart["buckets"] >= 10, (
            f"{chart['type']}: only {chart['buckets']} buckets, so the ten-bucket case "
            "that produced the defect is not being exercised"
        )
        for index, bottom in enumerate(chart["bottoms"]):
            assert bottom <= chart["canvasHeight"], (
                f"{chart['type']} at {width}x{height}: legend entry {index} of "
                f"{chart['entries']} has its bottom at {bottom}px on a "
                f"{chart['canvasHeight']}px canvas - drawn outside it, with no scrollbar. "
                f"The last entry is always `other`, which §6.4 says can be the largest "
                f"bucket in the breakdown."
            )
        for index, right in enumerate(chart["rights"]):
            assert right <= chart["canvasWidth"] + 1, (
                f"{chart['type']} at {width}x{height}: legend entry {index} runs to "
                f"{right}px on a {chart['canvasWidth']}px canvas"
            )


@pytest.mark.parametrize("width, height, dpr", VIEWPORTS)
def test_no_two_buckets_are_drawn_in_the_same_colour(stats_page, width, height, dpr):
    """Defect 2, measured on the rendered dataset rather than on the palette.

    The palette's own length and distinctness are asserted statically in
    `tests/test_d_statistics_content.py`. This is the other half: that the
    module actually hands one colour per bucket rather than indexing modulo a
    palette that is too short. Measured before the fix, `Landfill` and
    `Other (sample too small)` were both `#005f73` in this doughnut.
    """
    page = stats_page(width, height, dpr)
    for chart in page.evaluate(LEGENDS):
        colours = chart["colours"]
        if not isinstance(colours, list):
            continue  # A single-series bar carries one colour by design.
        assert len(colours) == chart["buckets"]
        duplicates = sorted({colour for colour in colours if colours.count(colour) > 1})
        assert not duplicates, (
            f"{chart['type']} at {width}x{height}: {duplicates} is used for more than one "
            f"bucket across {chart['buckets']} buckets"
        )


@pytest.mark.parametrize("width, height, dpr", VIEWPORTS)
def test_the_statistics_page_does_not_scroll_sideways(stats_page, width, height, dpr):
    """D got this right and a chart is the easiest way to lose it: a canvas is a
    replaced element with an intrinsic size, and the plugin that grows the chart
    region for the legend changes that region's box on every update."""
    page = stats_page(width, height, dpr)
    measured = page.evaluate(
        "() => ({scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth})"
    )
    assert measured["scroll"] <= measured["client"], (
        f"{width}x{height}: the page scrolls sideways by "
        f"{measured['scroll'] - measured['client']}px"
    )


def test_the_rendered_page_never_makes_new_zealand_the_subject(stats_page):
    """§6.4's copy constraint over `innerText`, which is the text a reader sees.

    The static form of this is in `tests/test_d_statistics_content.py` and reads
    the page's own source. This one reads what the browser rendered, so it also
    covers the copy `stats.js` composes from the API response — the bucket
    labels, the headline and the suppression sentence — none of which exists as
    a literal anywhere.
    """
    page = stats_page(1278, 983, 1.25)
    rendered = page.inner_text("body")
    assert "self-selected" in rendered, "the page did not render its own copy"
    offenders = red_line.violations(rendered)
    assert not offenders, f"the rendered statistics page makes New Zealand its subject: {offenders}"


def test_the_axis_and_the_list_state_the_same_number_in_the_same_unit(stats_page):
    """Defect 4. The bar chart's y axis read `0`, `0.05` … `0.40` immediately
    above a text list reading `37.9% share`: one number, two units, one card.

    Both the ticks and the tooltip are asserted, because formatting one and not
    the other only moves the contradiction into the hover. And the tick labels
    are read off the rendered scale rather than off the formatter, so a
    callback that is configured but never reached fails here.
    """
    page = stats_page(1278, 983, 1.25)
    axes = page.evaluate(
        """() => Object.values(Chart.instances)
             .filter((chart) => chart.config.type === 'bar')
             .map((chart) => ({
               ticks: chart.scales.y.ticks.map((tick) => tick.label),
               tooltip: chart.options.plugins.tooltip.callbacks.label({
                 label: 'Example', parsed: {y: 0.379},
               }),
             }))"""
    )
    assert axes, "no bar chart was rendered, so the axis cannot be checked"
    for axis in axes:
        assert axis["ticks"], "the y axis drew no ticks"
        assert all("%" in str(label) for label in axis["ticks"]), (
            f"the y axis reads a raw fraction for a share: {axis['ticks']}"
        )
        assert "%" in axis["tooltip"], (
            f"the axis is a percentage and the tooltip is not: {axis['tooltip']!r}"
        )

    listed = page.inner_text("#stats-breakdown-content")
    assert "% share" in listed, "the text list no longer states a share, so nothing is being compared"


def test_a_mass_is_printed_as_the_service_sent_it(stats_page):
    """Defect 6. §7.7.7 rules that decimals take no locale-aware separator on
    either surface, and this was the only place on the public front end still
    applying one.

    Two failures in one call. `toLocaleString('en-NZ', {maximumFractionDigits:
    3})` grouped — `521952.691` rendered as `521,952.691 kg` — and it round
    tripped through `Number`, dropping the significant trailing zero the service
    published, so `139132.500` came out as `139,132.5`. Both are asserted,
    because fixing the separator alone leaves the precision wrong and the page
    still looks right.
    """
    page = stats_page(1278, 983, 1.25)
    rendered = page.inner_text("#stats-breakdown-content")

    masses = re.findall(r"(\S+) kg", rendered)
    assert masses, "no mass was rendered, so nothing is being checked"
    grouped = [mass for mass in masses if "," in mass]
    assert not grouped, (
        f"a decimal was given a locale-aware separator: {grouped}. §7.7.7 forbids it."
    )

    # Every `total_kg` in the fixture carries exactly three decimal places, and
    # two of them end in a zero that `Number` would have dropped.
    sent = {row["total_kg"] for rows in
            (STATS["by_destination"], STATS["by_sector"], STATS["by_food_category"])
            for row in rows}
    trailing_zero = sorted(value for value in sent if value.endswith("0"))
    assert trailing_zero, "the fixture no longer exercises a trailing zero"
    for value in trailing_zero:
        assert f"{value} kg" in rendered, (
            f"{value} was not printed as sent; a round trip through Number would render it "
            f"as {float(value)!r}"
        )


def test_the_page_carries_no_placeholder_data_banner(stats_page):
    """**Do not add one.** §7.6.2 requires the mock-data warning to be
    *conditional* on `is_mock`, and `GET /api/v1/stats` carries no `is_mock` and
    no factor-derived number - it reports counts, shares and masses that people
    typed in. An unconditional banner here is exactly the failure §7.6.2 names:
    a disclaimer that goes on disclaiming real data once real factors are
    published.

    This is asserted because the banner is the obvious thing for the next person
    to "fix", the factor set is mock today, and every other public surface does
    carry one.
    """
    page = stats_page(1278, 983, 1.25)
    rendered = page.inner_text("body").lower()
    assert "placeholder data" not in rendered
    assert not page.query_selector(".disclaimer"), (
        "the statistics page must carry no mock-data banner: its figures are not derived "
        "from any factor set"
    )
