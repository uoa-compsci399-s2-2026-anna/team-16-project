"""The step navigation bar is *reachable*, not merely present.

A test that asserts an element exists does not assert anyone can reach it, and
this repository has six defects on record that reached the owner through green
markup tests — a dialog whose submit button was invisible, a table whose
scrollbar was drawn 4,500px below the fold. So every assertion here is a
geometry measured in a real browser against the running stack.

**What is being defended.** The client's first look at the calculator was that
advancing requires scrolling at every step. Measured at 1278x983 before the
change, the primary action sat +2961px past the fold on the food-type step,
+796 on destinations, +630 on review and +1230 on results; at 938x898,
+3046 / +899 / +715 / +1342; at 390x700, five of the seven screens failed.
`stepNav` (`web/js/view.js`) pins that row with `position: sticky; bottom: 0`.
The goal is not zero scrolling — the food-type step is 40 categories long and
always will be — it is that **advancing never requires scrolling**.

**`html { scroll-behavior: smooth }` is forced to `auto` before every
measurement.** `scrollTo(0, 0)` followed immediately by `getBoundingClientRect`
otherwise returns mid-animation values; that made one earlier measurement pass
optimistic by up to 800px.

**Running these.** Playwright is not a project dependency — it is not in
`requirements.txt` and not in the `dev` extra, because it needs a browser
download that a marker installing this package should not be made to take. The
whole module skips without it, the same way `test_entry_destinations.py` skips
without Node::

    pip install playwright && playwright install chromium
    docker compose -f docker/compose.yaml up -d
    pytest tests/web/test_step_navigation.py

**Mutation hook.** `KAICALC_MUTATION_CSS` is injected as a stylesheet after
load. It exists so each rule these tests depend on can be knocked out and the
test watched to fail — the project's standard, because a rule no test kills is
a rule no test is really checking. See the table in the module docstring of the
commit that added this file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to measure where the primary action lands; the bar is unverified without it",
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "calculate_response_single.json").read_text(encoding="utf-8"))
BASE = os.environ.get("KAICALC_WEB_URL", "http://localhost:18080/")


def _english(url: str) -> str:
    """`?lang=en`, the one-request override, so this file measures English.

    Belt and braces beside `locale="en-NZ"`: the override is what a support
    request or a screenshot uses, and it is the guarantee that does not depend
    on Chromium honouring a context locale in `navigator.languages`.
    """
    return url + ("&" if "?" in url else "?") + "lang=en"
MUTATION_CSS = os.environ.get("KAICALC_MUTATION_CSS", "")

#: The owner's two machines, both high-resolution panels at the OS default
#: scaling, plus a phone. The device scale factors are theirs; the CSS viewport
#: is what the arithmetic is done in.
VIEWPORTS = [
    pytest.param(1278, 983, 1.25, id="1278x983"),
    pytest.param(938, 898, 1.5, id="938x898"),
    pytest.param(390, 700, 3.0, id="390x700"),
]

FORCE_AUTO = "html { scroll-behavior: auto !important; }"

#: Distance in CSS px from the bottom of the element to the bottom of the
#: viewport at scroll top. Negative means reachable without scrolling.
PAST_FOLD = """
(sel) => {
  window.scrollTo(0, 0);
  const el = document.querySelector(sel);
  if (!el) return null;
  return Math.round(el.getBoundingClientRect().bottom - window.innerHeight);
}
"""


@pytest.fixture(scope="session")
def browser():
    with playwright_api.sync_playwright() as p:
        instance = p.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def page_at(browser):
    """A page at a given viewport, with the calculate POST fulfilled locally.

    `/api/v1/calculate` carrying `X-Dry-Run` is staff-only and answers 401, so
    the results view is reached by fulfilling the POST in-browser from the
    contract fixture — the same way both measurement passes reached it.
    """
    contexts = []

    def open_page(width, height, dpr=1.0):
        # `locale="en-NZ"` and the `?lang=en` below both pin the language, and
        # both are needed. Since v1.25 the calculator negotiates from
        # `navigator.languages`, so a machine whose browser prefers another
        # language renders this page in it and every English string asserted
        # below stops matching - which is exactly what happened on the
        # repository owner's machine the first time this ran after the
        # twenty catalogues landed. This file measures LAYOUT against the
        # English copy it was calibrated on; the language itself is
        # tests/web/test_i18n_browser.py's subject.
        ctx = browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=dpr,
            locale="en-NZ",
        )
        contexts.append(ctx)
        page = ctx.new_page()
        page.route(
            "**/api/v1/calculate*",
            lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(FIXTURE)),
        )
        try:
            page.goto(_english(BASE), wait_until="networkidle", timeout=15000)
        except Exception as error:  # pragma: no cover - environment guard
            pytest.skip(f"the front end is not being served at {BASE}: {error}")
        page.add_style_tag(content=FORCE_AUTO)
        if MUTATION_CSS:
            page.add_style_tag(content=MUTATION_CSS)
        page.wait_for_selector('[data-action="start"]', timeout=10000)
        return page

    yield open_page
    for ctx in contexts:
        ctx.close()


def walk(page):
    """Drive the wizard as a visitor would, yielding the step index *on arrival*
    at each screen — intro, 0..4, then results (5).

    A generator rather than a `go_to(step)` because the wizard is a sequence:
    re-walking it once per screen measures the same seven screens seven times
    over, and the visitor only walks it once.

    Each screen is yielded before it is interacted with, because that is the
    state the visitor lands in and the moment they look for the next action.
    """
    yield -1
    page.click('[data-action="start"]')
    page.wait_for_selector('input[name="sector"]')
    yield 0
    page.evaluate("document.querySelector('input[name=sector]').click()")
    page.wait_for_timeout(60)
    page.click('[data-action="continue"]')
    page.wait_for_selector('input[name="food-category"]')
    yield 1
    page.click('[data-action="continue"]')
    page.wait_for_selector("#total-waste")
    yield 2
    page.fill("#total-waste", "1000")
    page.click('[data-action="continue"]')
    page.wait_for_selector('[data-line-field="amount"]')
    yield 3
    page.fill('[data-line-field="amount"] >> nth=0', "1000")
    page.wait_for_timeout(60)
    page.click('[data-action="continue"]')
    page.wait_for_selector('[data-action="calculate"]')
    yield 4
    page.click('[data-action="calculate"]')
    page.wait_for_selector(".results-page", timeout=15000)
    page.wait_for_timeout(200)
    yield 5


def advance_to(page, step):
    """The single-screen form of `walk`, for the tests that measure one step."""
    for arrived in walk(page):
        if arrived == step:
            return page
    raise AssertionError(f"step {step} was never reached")


#: The screen, and the selector for the action that advances it. The intro has
#: no bar — it is a full-bleed hero whose own CTA measures -447 / -362 / -273 —
#: and the results screen's advancing action is the download.
PRIMARY = {
    -1: '[data-action="start"]',
    0: '.step-nav [data-action="continue"]',
    1: '.step-nav [data-action="continue"]',
    2: '.step-nav [data-action="continue"]',
    3: '.step-nav [data-action="continue"]',
    4: '.step-nav [data-action="calculate"]',
    5: '.step-nav [data-action="download-results"]',
}


@pytest.mark.parametrize("width,height,dpr", VIEWPORTS)
def test_the_primary_action_of_every_step_is_reachable_without_scrolling(page_at, width, height, dpr):
    """The whole point. Every failure is collected rather than raised on the
    first, so one run names every screen that regressed instead of the earliest
    one."""
    page = page_at(width, height, dpr)
    failures = []
    for step in walk(page):
        page.wait_for_timeout(100)
        past = page.evaluate(PAST_FOLD, PRIMARY[step])
        if past is None:
            failures.append(f"step {step}: {PRIMARY[step]} is not on the page at all")
        elif past > 0:
            failures.append(f"step {step}: primary action is {past}px past the fold")
    assert not failures, "; ".join(failures)


@pytest.mark.parametrize("width,height,dpr", VIEWPORTS)
def test_the_back_action_of_every_step_is_reachable_without_scrolling(page_at, width, height, dpr):
    """Back rides in the same bar, so it is the same guarantee — but it is
    asserted separately because a grid that overflows puts one of the two out
    of reach without touching the other."""
    page = page_at(width, height, dpr)
    failures = []
    for step in walk(page):
        if step == -1:
            continue
        page.wait_for_timeout(100)
        past = page.evaluate(PAST_FOLD, '.step-nav [data-action="go-step"]')
        if past is None:
            failures.append(f"step {step} has no Back action in the bar")
        elif past > 0:
            failures.append(f"step {step}: Back is {past}px past the fold")
    assert not failures, "; ".join(failures)


def test_the_bar_is_sticky_and_not_fixed(page_at):
    """A short step must leave the bar where the content ends. `fixed` would
    park it at the bottom of every screen including this one, which reads like
    a cookie banner; `sticky` only pins when the section would push it off."""
    page = advance_to(page_at(1278, 983, 1.25), 2)
    measured = page.evaluate(
        """() => {
          window.scrollTo(0, 0);
          const el = document.querySelector('.step-nav');
          const rect = el.getBoundingClientRect();
          return {position: getComputedStyle(el).position,
                  gap: Math.round(window.innerHeight - rect.bottom)};
        }"""
    )
    assert measured["position"] == "sticky", measured
    assert measured["gap"] > 100, f"the bar is parked at the bottom of a short step: {measured}"


def test_the_bar_unpins_above_the_footer_at_full_scroll(page_at):
    """Why no page-level padding is owed to the bar: at the end of the section
    it returns to its natural place, so nothing is permanently underneath it."""
    page = advance_to(page_at(1278, 983, 1.25), 3)
    measured = page.evaluate(
        """() => {
          window.scrollTo(0, document.documentElement.scrollHeight);
          const bar = document.querySelector('.step-nav').getBoundingClientRect();
          const footer = document.querySelector('footer').getBoundingClientRect();
          return {gap: Math.round(window.innerHeight - bar.bottom),
                  barBottom: Math.round(bar.bottom), footerTop: Math.round(footer.top)};
        }"""
    )
    assert measured["gap"] > 0, f"the bar is still pinned at full scroll: {measured}"
    assert measured["barBottom"] <= measured["footerTop"], measured


def test_the_allocation_summary_still_sticks(page_at):
    """The only other sticky in the flow. A new scroll container or any
    transformed ancestor kills it silently, and the destination step is the one
    screen with a sticky at both edges."""
    page = advance_to(page_at(1278, 983, 1.25), 3)
    measured = page.evaluate(
        """() => {
          const el = document.querySelector('.allocation-summary');
          window.scrollTo(0, 0);
          const natural = Math.round(el.getBoundingClientRect().top);
          const tops = [];
          for (const y of [natural + 200, natural + 500, 900]) {
            window.scrollTo(0, y);
            tops.push(Math.round(el.getBoundingClientRect().top));
          }
          return {position: getComputedStyle(el).position, natural, tops};
        }"""
    )
    assert measured["position"] == "sticky", measured
    assert all(abs(top - 10) <= 1 for top in measured["tops"]), measured


def test_every_focused_control_is_scrolled_clear_of_the_bar(page_at):
    """`scroll-margin-bottom`. Sequential focus navigation scrolls a control
    flush to the viewport edge, which is underneath a bar pinned there — a
    keyboard user typing into an input they cannot see.

    **Every** row, not the last one. Focusing the last row scrolls the document
    to its end, where the bar has already un-pinned, so that row clears the bar
    with the rule deleted and proves nothing: written that way this test
    survived its own mutation. Row 5 of 11 at 1278x983 is the case that
    discriminates — +16px of clearance with the rule, -69px without it.
    """
    page = advance_to(page_at(1278, 983, 1.25), 3)
    measured = page.evaluate(
        """() => {
          const inputs = [...document.querySelectorAll('[data-line-field="amount"]')];
          const rows = [];
          for (let i = 0; i < inputs.length; i++) {
            window.scrollTo(0, 0);
            inputs[i].focus();
            const rect = inputs[i].getBoundingClientRect();
            const bar = document.querySelector('.step-nav').getBoundingClientRect();
            rows.push({row: i, clear: Math.round(bar.top - rect.bottom), top: Math.round(rect.top)});
          }
          return rows;
        }"""
    )
    assert measured, "the destination step rendered no rows to focus"
    hidden = [row for row in measured if row["clear"] < 0 or row["top"] < 0]
    assert not hidden, f"focusing these rows put them behind the bar: {hidden}"


def test_the_bar_is_last_in_the_tab_order(page_at):
    """Tab must reach the form before the navigation, which is a DOM-order
    property of the bar being the last child of its section. This project
    already has one accessibility regression on record from a focus call in the
    wrong place, so it is asserted by actually pressing Tab."""
    page = advance_to(page_at(1278, 983, 1.25), 3)
    page.fill('[data-line-field="amount"] >> nth=0', "1000")
    page.wait_for_timeout(80)
    page.evaluate("window.scrollTo(0, 0); document.getElementById('main-content').focus({preventScroll: true})")
    order = []
    for _ in range(80):
        page.keyboard.press("Tab")
        stop = page.evaluate(
            """() => {
              const active = document.activeElement;
              if (!active || active === document.body || !active.closest('#main-content')) return null;
              const style = getComputedStyle(active);
              return {inBar: !!active.closest('.step-nav'),
                      label: (active.textContent || active.id || '').trim().slice(0, 24),
                      focusVisible: active.matches(':focus-visible'),
                      outline: style.outlineStyle,
                      bottom: Math.round(active.getBoundingClientRect().bottom),
                      viewport: window.innerHeight};
            }"""
        )
        if stop is None:
            break
        order.append(stop)
    in_bar = [index for index, stop in enumerate(order) if stop["inBar"]]
    assert in_bar, "Tab never reached the bar"
    assert in_bar == list(range(len(order) - len(in_bar), len(order))), (
        f"the bar is not last in the Tab order: {[stop['label'] for stop in order]}"
    )
    for stop in order:
        if stop["inBar"]:
            assert stop["focusVisible"] and stop["outline"] == "solid", stop
            assert stop["bottom"] <= stop["viewport"], stop


@pytest.mark.parametrize("width,height,dpr", VIEWPORTS + [pytest.param(960, 900, 1.0, id="960x900"),
                                                          pytest.param(1020, 900, 1.0, id="1020x900")])
def test_no_horizontal_overflow_at_any_breakpoint(page_at, width, height, dpr):
    """960 and 1020 are the `.wide` and `.results-page` bleed gates, and the
    arithmetic beside them in `styles.css` is what stops a section leaving the
    left edge. The bar spans those sections, so it is measured at both."""
    page = page_at(width, height, dpr)
    failures = []
    for step in walk(page):
        page.wait_for_timeout(100)
        measured = page.evaluate(
            "() => ({scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth})"
        )
        if measured["scroll"] > measured["client"]:
            failures.append(f"step {step} overflows by {measured['scroll'] - measured['client']}px")
    assert not failures, "; ".join(failures)


@pytest.mark.parametrize("width", [320, 390])
def test_the_longest_primary_label_does_not_overflow_the_narrowest_viewport(page_at, width):
    """The primary label is variable, and its longest form is the one the layout
    has to survive: "Calculate results for 2 entries" on the review step after a
    second supply-chain entry is added. The bar's middle track is
    `minmax(0, 1fr)` and its buttons do not carry `white-space: nowrap` for
    exactly this reason — an unwrappable `auto` grid track cannot shrink below
    its own text.

    **320 is the width that discriminates**, and it is a supported one:
    `body { min-width: 320px }`. Measured only at 390 both rules survive
    deletion, because "Calculate results for 2 entries" happens to fit there
    unwrapped; at 320 the same mutation puts 43px off the right edge and the
    bar's own `scrollWidth` 64px past its box.
    """
    page = advance_to(page_at(width, 700, 3.0), 4)
    page.click('[data-action="add-entry"]')
    page.wait_for_selector('input[name="sector"]')
    page.evaluate("document.querySelector('input[name=sector]').click()")
    page.wait_for_timeout(60)
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector('input[name="food-category"]')
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector("#total-waste")
    page.fill("#total-waste", "500")
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector('[data-line-field="amount"]')
    page.fill('[data-line-field="amount"] >> nth=0', "500")
    page.wait_for_timeout(60)
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector('[data-action="calculate"]')
    page.wait_for_timeout(120)
    measured = page.evaluate(
        """() => {
          const bar = document.querySelector('.step-nav');
          const primary = bar.querySelector('.button-primary');
          return {label: primary.textContent.trim(),
                  scroll: document.documentElement.scrollWidth,
                  client: document.documentElement.clientWidth,
                  barScroll: bar.scrollWidth, barClient: bar.clientWidth,
                  past: Math.round(primary.getBoundingClientRect().bottom - window.innerHeight)};
        }"""
    )
    assert "entries" in measured["label"], f"the long label was never rendered: {measured}"
    assert measured["scroll"] <= measured["client"], measured
    assert measured["barScroll"] <= measured["barClient"], f"the bar itself overflows: {measured}"
    assert measured["past"] <= 0, measured


def test_a_short_step_is_not_floored_by_a_stale_min_height(page_at):
    """`.main-content` carried `min-height: calc(100vh - 220px)`, arithmetic
    over a header, a step-indicator band and a footer. The band moved into the
    bar; left alone, the constant would have floored every short step 87px
    taller than its content and gone on producing a scrollbar with nothing
    below the fold to scroll to. A previous pass found short steps at 1920
    measuring exactly the floor, so shrinking their content changed nothing."""
    page = advance_to(page_at(1278, 983, 1.25), 2)
    measured = page.evaluate(
        "() => ({doc: Math.round(document.documentElement.scrollHeight), viewport: window.innerHeight})"
    )
    assert measured["doc"] <= measured["viewport"], (
        f"a short step still scrolls: document {measured['doc']}px in a {measured['viewport']}px viewport"
    )


def test_the_step_position_moved_into_the_bar_and_left_no_band_behind(page_at):
    """The 87px. The bar has to *say* which step this is, or the band was
    deleted rather than folded in — and a second progress element anywhere on
    the page spends the space again."""
    page = advance_to(page_at(1278, 983, 1.25), 2)
    measured = page.evaluate(
        """() => ({
          label: document.querySelector('.step-nav .step-nav-label')?.textContent?.trim() || null,
          name: document.querySelector('.step-nav .step-nav-name')?.textContent?.trim() || null,
          bands: document.querySelectorAll('#step-indicator, .step-indicator, .step-mobile').length,
          mainTop: Math.round(document.getElementById('main-content').getBoundingClientRect().top),
        })"""
    )
    assert measured["label"] == "Step 3 of 6", measured
    assert measured["name"] == "Waste amount", measured
    assert measured["bands"] == 0, f"a progress band is still costing height at the top: {measured}"
