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
from tests.web import i18n_keys  # noqa: E402
from tests.web.base_url import ORIGIN

BASE = ORIGIN
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

    def open_page(width, height, dpr=1.0, *, language="en", payload=None):
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
        page.add_init_script(_LANGUAGES_SHIM.format(
            languages=json.dumps([language]), first=json.dumps(language)
        ))
        requests = []
        page.on("request", lambda request: requests.append(request.url)
                if request.url.endswith("/api/v1/stats") else None)
        page.stats_requests = requests
        page.route(
            "**/api/v1/stats",
            lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps(STATS if payload is None else payload)
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

    # One assertion over the whole set, not one per chart: since all three
    # breakdowns default to Pies, all three are legended, and the fixture only
    # gives one of them (`by_destination`) ten buckets - `by_sector` and
    # `by_food_category` are five and seven. Requiring *every* legended chart
    # to individually reach ten would fail on a fixture shape that has nothing
    # to do with the defect this test guards against. What must not regress is
    # that the ten-bucket case is exercised *somewhere* in the page.
    assert any(chart["buckets"] >= 10 for chart in legended), (
        f"no legended chart reached ten buckets ({[c['buckets'] for c in legended]}), so "
        "the ten-bucket case that produced the defect is not being exercised"
    )

    for chart in legended:
        assert chart["entries"] == chart["buckets"], (
            f"{chart['type']}: {chart['entries']} legend entries for {chart['buckets']} "
            "buckets - a legend that is not drawing every bucket cannot be checked for "
            "one drawn outside the canvas"
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

    The page now defaults to three Pies, so this test selects Bar and Line to
    make both cartesian axes observable. Their tooltips and the remaining Pie
    tooltip must agree with the text list's percentage unit.
    """
    page = stats_page(1278, 983, 1.25)
    page.locator(".stats-chart-controls select").nth(0).select_option("bar")
    page.locator(".stats-chart-controls select").nth(1).select_option("line")
    axes = page.evaluate(
        """() => Object.values(Chart.instances)
             .filter((chart) => ['bar', 'line'].includes(chart.config.type))
             .map((chart) => ({
               ticks: chart.scales.y.ticks.map((tick) => tick.label),
               tooltip: chart.options.plugins.tooltip.callbacks.label({
                 label: 'Example', parsed: {y: 0.379},
               }),
             }))"""
    )
    assert len(axes) == 2, "both selected cartesian charts must draw an axis"
    for axis in axes:
        assert axis["ticks"], "the y axis drew no ticks"
        assert all("%" in str(label) for label in axis["ticks"]), (
            f"the y axis reads a raw fraction for a share: {axis['ticks']}"
        )
        assert "37.9%" in axis["tooltip"], (
            f"the axis is a percentage and the tooltip is not: {axis['tooltip']!r}"
        )

    pies = page.evaluate(
        """() => Object.values(Chart.instances)
             .filter((chart) => chart.config.type === 'pie')
             .map((chart) => chart.options.plugins.tooltip.callbacks.label({
               label: 'Example', parsed: 0.379,
             }))"""
    )
    assert len(pies) == 1, "the remaining Pie must draw a tooltip"
    for tooltip in pies:
        assert "37.9%" in tooltip, (
            f"the text list states a percentage share and a doughnut's tooltip is not: {tooltip!r}"
        )

    listed = page.inner_text("#stats-breakdown-content")
    assert "% share" in listed, "the text list no longer states a share, so nothing is being compared"
    assert "37.9% share" in listed


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


#: Mirrors `test_i18n_browser.py::open_page` for this one call. Not imported from
#: there: that module skips its own collection (`pytest.skip(allow_module_level=True)`)
#: when the stack is down or Playwright is missing, and importing it here would let
#: that skip escape as an import error instead of this file's own stack-up guard above.
_LANGUAGES_SHIM = """
Object.defineProperty(navigator, 'languages', {{ get: () => {languages} }});
Object.defineProperty(navigator, 'language', {{ get: () => {first} }});
"""


def open_page(browser, languages, path="/stats.html", query="", stats_fixture=False):
    context = browser.new_context(extra_http_headers={"Accept-Language": ",".join(languages)})
    page = context.new_page()
    page.add_init_script(
        _LANGUAGES_SHIM.format(languages=json.dumps(languages), first=json.dumps(languages[0]))
    )
    if stats_fixture:
        page.route(
            "**/api/v1/stats",
            lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps(STATS)
            ),
        )
    page.goto(f"{BASE}{path}{query}", wait_until="networkidle")
    return context, page


def test_the_statistics_page_no_longer_claims_every_calculation(browser):
    """**Stage one made the old sentence untrue.**

    The page said "across the calculations run in this tool". Since v1.48 the
    aggregate counts only the calculations whose visitors offered them, so that
    phrasing overstated its own sample - and this is the page whose entire
    design problem is not overclaiming.

    The replacement is the more honest sentence, not a smaller one: a
    self-selected sample was always the situation, and now the page says so.
    """
    context, page = open_page(browser, ["en-NZ"], path="/stats.html", stats_fixture=True)
    try:
        text = page.locator("main").inner_text().lower()
        assert "contribut" in text or "chose to share" in text, (
            "the page does not say the figures come from calculations people offered"
        )
    finally:
        context.close()


def test_every_statistics_breakdown_is_drawn_as_a_share(browser):
    """**Item ⑫, and it is two words in a config - but the reason it is safe
    is worth writing down.**

    The current Statistics default, `renderPie`, can only express a part of a
    whole. This project's charts must render negative values, because
    `factor_downstream` may be negative (an offset) - which is why `renderBar`
    carries `allowNegative`. A negative
    slice does not exist.

    These three buckets are safe because they are COUNTS of what visitors
    selected - destination entries, supply-chain points, food categories -
    and a count cannot go below zero. The server also merges any bucket below
    the suppression threshold into `other`, so the parts do sum to the whole.

    That reasoning does not extend to a future chart of impact VALUES, and
    the note in `stats.js` says so.
    """
    context, page = open_page(browser, ["en-NZ"], path="/stats.html",
                              stats_fixture=True)
    try:
        charted = page.evaluate(
            """() => Object.values(Chart.instances || {}).map(c => ({
                type: c.config.type,
                title: c.options?.plugins?.title?.text,
                data: c.data.datasets[0].data,
            }))"""
        )
        assert charted, "no charts were drawn"
        kinds = [chart["type"] for chart in charted]
        assert kinds == ["pie", "pie", "pie"], (
            f"not every breakdown is a share chart: {kinds}"
        )

        # The chart *type* alone does not prove the right field was drawn: a
        # donut of counts and a donut of shares are the same slices in the
        # same proportions on screen, so a `valueKey: 'share'` silently swapped
        # for `'count'` would still pass everything above and would not be
        # caught by looking at the page either. Compare what Chart.js was
        # actually handed against the fixture's own `share`, per breakdown.
        title_to_key = {
            'Destinations entered (share)': 'by_destination',
            'Sectors selected (share)': 'by_sector',
            'Food categories selected (share)': 'by_food_category',
        }
        titles = {chart['title'] for chart in charted}
        assert titles == set(title_to_key), (
            f"expected one chart per breakdown ({sorted(title_to_key)}), drew: {sorted(titles)}"
        )
        for chart in charted:
            key = title_to_key[chart['title']]
            expected_shares = [float(row['share']) for row in STATS[key]]
            assert chart['data'] == pytest.approx(expected_shares), (
                f"{chart['title']}: chart.data.datasets[0].data is {chart['data']}, "
                f"the fixture's own `share` is {expected_shares} - the chart is not "
                "drawing the share field"
            )
    finally:
        context.close()


LINE_NOTE = "Categories follow the service's count-ranked order, with any combined Other bucket shown last. This compares categories, not a time trend."
BREAKDOWN_KEYS = ("by_destination", "by_sector", "by_food_category")
CHART_DATA = """() => [...document.querySelectorAll('.stats-chart-region canvas')].map(canvas => {
  const chart = Chart.getChart(canvas);
  return {id: chart.id, type: chart.config.type, labels: [...chart.data.labels],
          data: [...chart.data.datasets[0].data]};
})"""


def test_empty_error_and_recovery_keep_chart_choices_without_orphans(stats_page):
    page = stats_page(1278, 983)
    result = page.evaluate("""async stats => {
      const module = await import('/js/stats.js');
      const { ApiError } = await import('/js/api.js');
      const selections = () => [...document.querySelectorAll('.stats-breakdown')].map(
        section => section.querySelector('select')?.value ?? null);
      const ids = () => [...document.querySelectorAll('.stats-chart-region canvas')].map(
        canvas => Chart.getChart(canvas)?.id);
      document.querySelectorAll('.stats-chart-controls select')[0].value = 'bar';
      document.querySelectorAll('.stats-chart-controls select')[0].dispatchEvent(new Event('change'));
      document.querySelectorAll('.stats-chart-controls select')[1].value = 'line';
      document.querySelectorAll('.stats-chart-controls select')[1].dispatchEvent(new Event('change'));
      const empty = structuredClone(stats);
      empty.by_sector = [];
      module.renderStats(empty);
      const duringEmpty = {selections: selections(), active: Object.keys(Chart.instances).length,
        sectorCanvas: !!document.querySelectorAll('.stats-breakdown')[1].querySelector('canvas')};
      module.renderStatsError(new ApiError('TEST', 'Temporary failure'));
      const duringError = {active: Object.keys(Chart.instances).length,
        controls: document.querySelectorAll('.stats-chart-controls select').length,
        canvases: document.querySelectorAll('.stats-chart-region canvas').length};
      await module.loadStats({getStats: async () => stats});
      const recovered = {selections: selections(), active: Object.keys(Chart.instances).length,
        ids: ids()};
      module.renderStats(stats);
      const repeated = {selections: selections(), active: Object.keys(Chart.instances).length,
        ids: ids()};
      const select = document.querySelector('.stats-chart-controls select');
      select.value = 'pie';
      select.dispatchEvent(new Event('change'));
      const afterChange = {active: Object.keys(Chart.instances).length, ids: ids()};
      return {duringEmpty, duringError, recovered, repeated, afterChange};
    }""", STATS)
    assert result["duringEmpty"] == {"selections": ["bar", None, "pie"], "active": 2,
                                     "sectorCanvas": False}
    assert result["duringError"] == {"active": 0, "controls": 0, "canvases": 0}
    assert result["recovered"]["selections"] == ["bar", "line", "pie"]
    assert result["recovered"]["active"] == result["repeated"]["active"] == 3
    assert len(set(result["repeated"]["ids"])) == 3
    assert result["afterChange"]["active"] == 3
    assert result["afterChange"]["ids"][0] == result["repeated"]["ids"][0] + 3
    assert result["afterChange"]["ids"][1:] == result["repeated"]["ids"][1:]
    assert len(page.stats_requests) == 1


def test_stale_stats_success_and_failure_do_not_replace_the_latest_view(stats_page):
    page = stats_page(1278, 983)
    result = page.evaluate("""async stats => {
      const module = await import('/js/stats.js');
      const { ApiError } = await import('/js/api.js');
      const target = document.querySelector('#stats-breakdown-content');
      const deferred = () => {let resolve, reject; const promise = new Promise((yes, no) => {
        resolve = yes; reject = no;
      }); return {promise, resolve, reject};};
      let calls = 0;
      const getDeferred = pending => () => { calls += 1; return pending.promise; };
      const snapshot = () => ({busy: target.getAttribute('aria-busy'),
        summary: document.querySelector('#stats-summary').textContent,
        ids: [...document.querySelectorAll('.stats-chart-region canvas')].map(c => Chart.getChart(c).id)});
      const older = deferred(), newer = deferred();
      const oldCall = module.loadStats({getStats: getDeferred(older)});
      const busyAfterOld = target.getAttribute('aria-busy');
      const newCall = module.loadStats({getStats: getDeferred(newer)});
      const busyAfterNew = target.getAttribute('aria-busy');
      newer.resolve({...stats, total_calculations: 222});
      await newCall;
      const latestSuccess = snapshot();
      older.resolve({...stats, total_calculations: 111});
      const staleSuccessResult = await oldCall;
      const afterStaleSuccess = snapshot();
      const olderFailure = deferred(), newerSuccess = deferred();
      const oldFailureCall = module.loadStats({getStats: getDeferred(olderFailure)});
      const busyAfterOldFailure = target.getAttribute('aria-busy');
      const newSuccessCall = module.loadStats({getStats: getDeferred(newerSuccess)});
      newerSuccess.resolve({...stats, total_calculations: 333});
      await newSuccessCall;
      const latestAfterFailureRace = snapshot();
      olderFailure.reject(new ApiError('TEST', 'Obsolete failure'));
      const staleFailureResult = await oldFailureCall;
      const afterStaleFailure = snapshot();
      let unexpectedError = null;
      try {
        await module.loadStats({getStats: async () => { throw new RangeError('Renderer error'); }});
      } catch (error) {
        unexpectedError = {name: error.name, message: error.message};
      }
      return {busyAfterOld, busyAfterNew, busyAfterOldFailure, latestSuccess,
        staleSuccessResult, afterStaleSuccess, latestAfterFailureRace,
        staleFailureResult, afterStaleFailure, unexpectedError, calls,
        afterUnexpectedError: snapshot()};
    }""", STATS)
    assert result["busyAfterOld"] == result["busyAfterNew"] == result["busyAfterOldFailure"] == "true"
    assert result["staleSuccessResult"] is None and result["staleFailureResult"] is None
    assert result["latestSuccess"] == result["afterStaleSuccess"]
    assert result["latestAfterFailureRace"] == result["afterStaleFailure"]
    assert result["afterStaleFailure"] == result["afterUnexpectedError"]
    assert result["unexpectedError"] == {"name": "RangeError", "message": "Renderer error"}
    assert result["calls"] == 4
    assert "222" in result["latestSuccess"]["summary"]
    assert "333" in result["latestAfterFailureRace"]["summary"]
    assert result["latestSuccess"]["busy"] == result["latestAfterFailureRace"]["busy"] == "false"
    assert len(page.stats_requests) == 1


def test_detached_selector_cannot_recreate_a_destroyed_chart(stats_page):
    page = stats_page(1278, 983)
    result = page.evaluate("""async stats => {
      const module = await import('/js/stats.js');
      const { ApiError } = await import('/js/api.js');
      const ids = () => Object.keys(Chart.instances).map(Number).sort((a, b) => a - b);
      const staleChange = select => {
        const before = ids();
        select.value = 'bar';
        select.dispatchEvent(new Event('change'));
        return {before, after: ids()};
      };
      const oldRerender = document.querySelector('.stats-chart-controls select');
      module.rerenderInActiveLanguage();
      const afterRerender = staleChange(oldRerender);
      const oldError = document.querySelector('.stats-chart-controls select');
      module.renderStatsError(new ApiError('TEST', 'Temporary failure'));
      const afterError = staleChange(oldError);
      module.renderStats(stats);
      const oldDestroy = document.querySelector('.stats-chart-controls select');
      module.destroyCharts();
      const afterDestroy = staleChange(oldDestroy);
      module.renderStats(stats);
      const oldPagehide = document.querySelector('.stats-chart-controls select');
      window.dispatchEvent(new Event('pagehide'));
      const afterPagehide = staleChange(oldPagehide);
      module.renderStats(stats);
      const current = document.querySelector('.stats-chart-controls select');
      const normalBefore = ids();
      current.value = 'bar';
      current.dispatchEvent(new Event('change'));
      return {afterRerender, afterError, afterDestroy, afterPagehide,
        normalBefore, normalAfter: ids(), currentValue: current.value};
    }""", STATS)
    for name in ("afterRerender", "afterError", "afterDestroy", "afterPagehide"):
        assert result[name]["after"] == result[name]["before"], name
    assert result["currentValue"] == "bar"
    assert len(result["normalBefore"]) == len(result["normalAfter"]) == 3
    assert len(set(result["normalBefore"]) & set(result["normalAfter"])) == 2
    assert len(page.stats_requests) == 1


def test_each_breakdown_selects_a_chart_without_refetching_or_replacing_its_list(stats_page):
    page = stats_page(1278, 983)
    before = page.evaluate(CHART_DATA)
    assert [chart["type"] for chart in before] == ["pie"] * 3
    for chart, key in zip(before, BREAKDOWN_KEYS):
        assert chart["labels"] == [row["label"] for row in STATS[key]]
        assert chart["data"] == pytest.approx([float(row["share"]) for row in STATS[key]])
    handles = page.evaluate_handle("""() => ({
        canvases: [...document.querySelectorAll('.stats-chart-region canvas')],
        lists: [...document.querySelectorAll('.stats-breakdown-list')],
        charts: [...document.querySelectorAll('.stats-chart-region canvas')].map(c => Chart.getChart(c))
    })""")
    selects = page.locator(".stats-chart-controls select")
    assert selects.count() == 3
    for index, kind in ((0, "bar"), (1, "line")):
        previous = page.evaluate(CHART_DATA)
        selects.nth(index).select_option(kind)
        after = page.evaluate(CHART_DATA)
        assert [(a["id"] != b["id"]) for a, b in zip(after, previous)] == [i == index for i in range(3)]
        assert [(c["labels"], c["data"]) for c in after] == [(c["labels"], c["data"]) for c in before]
        assert page.evaluate("""saved => saved.lists.every((list, i) => list.isConnected &&
            list === document.querySelectorAll('.stats-breakdown-list')[i]) &&
            saved.canvases.every((canvas, i) => canvas.isConnected &&
            canvas === document.querySelectorAll('.stats-chart-region canvas')[i])""", handles)
    assert page.evaluate("saved => saved.charts[0].canvas === null && saved.charts[1].canvas === null && saved.charts[2].canvas !== null", handles)
    assert selects.evaluate_all("nodes => nodes.map(n => n.value)") == ["bar", "line", "pie"]
    assert len(page.stats_requests) == 1


def test_line_explains_count_ranked_categories_not_time(stats_page):
    payload = json.loads(json.dumps(STATS))
    payload["by_destination"] = [
        {"code": "a", "label": "A", "count": 20, "share": "0.2", "total_kg": "2.000"},
        {"code": "b", "label": "B", "count": 10, "share": "0.1", "total_kg": "1.000"},
        {"code": "other", "label": "Other", "count": 70, "share": "0.7", "total_kg": "7.000"},
    ]
    page = stats_page(390, 700, payload=payload)
    section = page.locator(".stats-breakdown").first
    section.locator("select").select_option("line")
    assert LINE_NOTE in section.locator(".stats-breakdown-note").inner_text()
    assert section.locator(".stats-breakdown-note").is_visible()
    note_id = section.locator(".stats-breakdown-note").get_attribute("id")
    assert note_id and section.locator("select").get_attribute("aria-describedby") == note_id
    assert section.locator("canvas").get_attribute("aria-describedby") == note_id
    chart = page.evaluate(CHART_DATA)[0]
    assert chart["labels"] == ["A", "B", "Other"]
    assert chart["data"] == [0.2, 0.1, 0.7]
    assert section.locator("canvas").evaluate("canvas => Chart.getChart(canvas).scales.x.type") == "category"
    section.locator("select").select_option("pie")
    assert LINE_NOTE not in section.locator(".stats-breakdown-note").inner_text()


@pytest.mark.parametrize("width,height,dpr", VIEWPORTS)
@pytest.mark.parametrize("language", ["en", "zh", "ar"])
def test_chart_selector_is_labelled_keyboard_usable_and_fits_the_viewport(stats_page, width, height, dpr, language):
    page = stats_page(width, height, dpr, language=language)
    assert page.locator("html").get_attribute("dir") == ("rtl" if language == "ar" else "ltr")
    charts = page.evaluate(LEGENDS)
    assert len(charts) == 3
    for chart in charts:
        assert chart["type"] == "pie" and chart["entries"] == chart["buckets"] > 0
        assert max(chart["bottoms"]) <= chart["canvasHeight"]
        assert max(chart["rights"]) <= chart["canvasWidth"] + 1
    for index in range(3):
        section = page.locator(".stats-breakdown").nth(index)
        control = section.locator("select")
        label = section.locator(".stats-chart-controls label")
        assert label.is_visible() and label.inner_text().strip()
        assert label.get_attribute("for") == control.get_attribute("id")
        assert control.bounding_box()["height"] >= 54
        assert control.evaluate("node => getComputedStyle(node).boxSizing") == "border-box"
        assert control.bounding_box()["width"] == section.locator(".stats-chart-controls").bounding_box()["width"]
        control.focus()
        control.press("ArrowDown")
        control.press("Enter")
        assert control.input_value() == "bar"
        assert control.evaluate("node => document.activeElement === node")
        control.press("ArrowDown")
        control.press("Enter")
        assert control.input_value() == "line"
        assert control.evaluate("node => document.activeElement === node")
        note = section.locator(".stats-breakdown-note")
        assert note.is_visible()
        assert control.get_attribute("aria-describedby") == note.get_attribute("id")
        assert section.locator("canvas").get_attribute("aria-describedby") == note.get_attribute("id")
        expected = LINE_NOTE if language == "en" else i18n_keys.catalogue(language)["strings"][LINE_NOTE]
        assert expected in note.inner_text()
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    long_label = STATS["by_food_category"][1]["label"]
    assert long_label in page.locator(".stats-breakdown-list").nth(2).inner_text()
    assert long_label in page.locator("canvas").nth(2).evaluate("""(canvas, label) => Chart.getChart(canvas).options.plugins.tooltip.callbacks.label({label, parsed: {y: 0.1312}})""", long_label)


def test_render_bar_draws_a_negative_value_below_the_axis(browser):
    """Statistics defaults to `renderPie`, whose slices cannot express negative
    values. The page's Bar option calls `renderBar` with `allowNegative: false`
    because these breakdowns are non-negative counts, so the negative-axis
    branch this test exercises is not reached from `stats.js`. `renderDonut`
    remains a compatible adapter with no Statistics caller. Negative Bar support
    is retained capability rather than dead code: §7.3a's "charts
    must render negative values" still stands, because `factor_downstream` may
    be negative (an offset) - `animal_feed` is `-0.15` in
    `tests/fixtures/factors.json` - and this is D's own library for whatever
    chart draws that figure next.

    So the coverage has to be a direct unit test on `renderBar` itself, called
    the way a future caller would, rather than anything read off `stats.html`.
    A mutation replacing `allowNegative ? Math.min(0, ...finiteValues) : 0`
    with a bare `0` left every one of the 142 tests in this file's siblings
    green; this is the test that must fail against it.
    """
    context = browser.new_context(bypass_csp=True)
    page = context.new_page()
    try:
        page.goto(f"{BASE}/stats.html", wait_until="domcontentloaded")
        measured = page.evaluate(
            """async () => {
              const { renderBar } = await import('/js/charts.js')
              const rows = [
                { label: 'Landfill', value: 12 },
                { label: 'Animal feed', value: -5 },
              ]
              const canvas = document.createElement('canvas')
              canvas.width = 400
              canvas.height = 300
              document.body.appendChild(canvas)

              const defaulted = renderBar(canvas, rows)
              const withNegative = {
                data: [...defaulted.data.datasets[0].data],
                suggestedMin: defaulted.options.scales.y.suggestedMin,
              }
              defaulted.destroy()

              const suppressed = renderBar(canvas, rows, { allowNegative: false })
              const withoutNegative = suppressed.options.scales.y.suggestedMin
              suppressed.destroy()

              return { withNegative, withoutNegative }
            }"""
        )
    finally:
        context.close()

    # The value itself is drawn verbatim - never clipped, never made positive -
    # which is `renderBar`'s own documented contract and not merely this
    # option's concern, but a mutation that broke it would land here too.
    assert measured["withNegative"]["data"] == [12, -5], (
        f"a negative row was not drawn as negative: {measured['withNegative']['data']}"
    )
    # The mutation under test: `allowNegative` (true by default - `opts.allowNegative
    # !== false`) must lower the axis floor below the data's own minimum, not leave
    # it pinned at zero.
    assert measured["withNegative"]["suggestedMin"] < 0, (
        "a negative value did not lower the y-axis floor below zero: "
        f"{measured['withNegative']['suggestedMin']!r}"
    )
    # The opt-out this option exists to provide: explicitly `false` must still pin
    # the floor at zero even though the data is negative, or `allowNegative` does
    # nothing in either direction.
    assert measured["withoutNegative"] == 0, (
        f"allowNegative: false did not hold the axis floor at zero: {measured['withoutNegative']!r}"
    )
