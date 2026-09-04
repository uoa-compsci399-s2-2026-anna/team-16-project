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

from api.schemas import CalculatePayload

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to measure where the primary action lands; the bar is unverified without it",
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "calculate_response_single.json").read_text(encoding="utf-8"))
#: `/index.html` rather than `/`, and they are now the same document: nginx says
#: `index index.html` again. Named explicitly so this file measures the calculator
#: whatever the `index` directive says next.
BASE = os.environ.get("KAICALC_WEB_URL", "http://localhost:18080/index.html")


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
        # **`bypass_csp` is what lets this file measure at all, and it is a
        # statement about the harness rather than about the page.** D's
        # `location /` Content-Security-Policy sets `style-src 'self'`, which
        # is correct — the front end loads exactly one stylesheet from this
        # origin — and a `<style>` element created by `add_style_tag` is
        # precisely what that directive refuses. Both injections below are
        # instrumentation: `FORCE_AUTO` stops `scroll-behavior: smooth`
        # returning mid-animation geometry, and `KAICALC_MUTATION_CSS` is the
        # hook that lets each rule this file depends on be knocked out and the
        # test watched to fail. Without the bypass every test in this module
        # errors on the injection and measures nothing.
        #
        # **The CSP is not thereby untested.** It is asserted as a header by
        # `tests/test_d_statistics_content.py`, and enforced in a real browser
        # — with no bypass — by
        # `tests/web/test_csp.py::test_no_public_page_violates_its_own_policy`,
        # which is the test that would catch a directive that breaks the page.
        ctx = browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=dpr,
            locale="en-NZ",
            bypass_csp=True,
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

    **The intro screen is back and is walked again.** It is step -1: a hero with its
    own "Start calculator" button, which every visitor meets because nginx serves
    `index.html` at `/`. It was deleted for a week while `home.html` held that
    address; `home.html` is retired, so the screen and this yield came back with it —
    seven screens to measure rather than six.

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
#: no bar — it is a full-bleed hero whose own CTA measured -447 / -362 / -273 on
#: the pass that put this table here — and the results screen's advancing action
#: is the download.
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
        # The intro screen has no step bar at all - it is a full-bleed hero - so
        # there is no Back to measure on it.
        if step == -1:
            continue
        # Step one HAS a Back again, and it goes to the intro. It lost it for the
        # week the intro was deleted; asserted rather than merely walked, because
        # `go-step` with `back: null` renders no button and the loop below would
        # then report "step 0 has no Back action" without saying why.
        if step == 0:
            assert page.query_selector('.step-nav [data-action="go-step"]') is not None, (
                "step one has no Back button; it should return to the introduction"
            )
        page.wait_for_timeout(100)
        past = page.evaluate(PAST_FOLD, '.step-nav [data-action="go-step"]')
        if past is None:
            failures.append(f"step {step} has no Back action in the bar")
        elif past > 0:
            failures.append(f"step {step}: Back is {past}px past the fold")
    assert not failures, "; ".join(failures)


def test_the_bar_is_sticky_and_not_fixed(page_at):
    """`position` alone does not prove `sticky` over `fixed` - both report a
    `position` string, and a single gap measurement at one scroll offset is
    only ever one sample of a number this test has no business treating as a
    constant. What actually tells the two apart is motion: a `fixed` bar's
    distance from the fold cannot change, because `position: fixed` takes it
    out of the document being scrolled entirely, however tall that document
    grows. A `sticky`, in-flow bar's distance from the fold *grows* as the
    page scrolls, because the bar rides up with the content beneath it like
    any other in-flow element, right up until it is asked to pin.

    This step had zero scroll at all the day this test was written, which is
    why the old version read a single number at the top and called it a day.
    Task 2 gave the step two more required fields and, with them, its first
    real scroll - which is exactly the condition this test needs to say
    anything about `fixed` versus `sticky` at all, and exactly the condition
    the single-sample version could not survive.
    """
    page = advance_to(page_at(1278, 983, 1.25), 2)
    scrollable = page.evaluate("document.documentElement.scrollHeight - window.innerHeight")
    assert scrollable > 40, (
        f"this step does not scroll enough to tell sticky from fixed apart: {scrollable}px"
    )

    def gap_at(scroll_top):
        return page.evaluate(
            """(top) => {
              window.scrollTo(0, top);
              const el = document.querySelector('.step-nav');
              const rect = el.getBoundingClientRect();
              return {position: getComputedStyle(el).position,
                      gap: Math.round(window.innerHeight - rect.bottom)};
            }""",
            scroll_top,
        )

    samples = [gap_at(round(scrollable * fraction)) for fraction in (0, 0.4, 0.85)]
    assert all(sample["position"] == "sticky" for sample in samples), samples
    gaps = [sample["gap"] for sample in samples]
    assert gaps[0] < gaps[1] < gaps[2], (
        f"the gap did not grow while scrolling - a fixed bar reads this way too: {gaps}"
    )


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


def test_the_allocation_summary_is_opaque_in_both_states(page_at):
    """...and being sticky, that is the only thing stopping rows showing through.

    The summary was `rgba(223, 248, 237, 0.68)`, and the destination rows scroll
    *underneath* it: labels and input borders were legible through the one panel
    on the screen whose whole job is to be readable while the list moves. The
    sibling test above proves it sticks; this one proves it covers.

    **Two assertions, because the fix had two halves.** Chromium serialises a
    fully opaque background as `rgb(...)` and anything less as `rgba(...)`, so
    the first is exactly "alpha is 1". The second pins the composite: the
    replacements are the old translucent colours resolved over `--kai-white`
    (#fff), which is the ground the summary sits on — 223/248/237 at 0.68 is
    233.24, 250.24, 242.76, and 255/80/50 at 0.07 is 255, 242.75, 240.65. So the
    panel is byte-identical to what it looked like before and only stops being
    see-through. An opaque background of some *other* colour would satisfy the
    first assertion and would be a redesign nobody asked for.

    Mutation: `KAICALC_MUTATION_CSS` carrying the two original `rgba(...)` rules
    fails both states.
    """
    page = advance_to(page_at(1278, 983, 1.25), 3)
    normal = page.evaluate(
        "() => getComputedStyle(document.querySelector('.allocation-summary')).backgroundColor"
    )
    page.fill('[data-line-field="amount"] >> nth=0', "1001")
    page.wait_for_timeout(80)
    invalid = page.evaluate(
        "() => getComputedStyle(document.querySelector('.allocation-summary.invalid')).backgroundColor"
    )
    measured = {"normal": normal, "invalid": invalid}
    assert not normal.startswith("rgba("), measured
    assert not invalid.startswith("rgba("), measured
    assert normal.replace(" ", "") == "rgb(233,250,243)", measured
    assert invalid.replace(" ", "") == "rgb(255,243,241)", measured


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
    """`.main-content` once carried `min-height: calc(100vh - 220px)`,
    arithmetic over a header, a step-indicator band and a footer. That
    constant floored every step to the same height regardless of how much it
    actually rendered - so a step 87px shorter than the floor still produced
    a scrollbar with nothing below the fold to scroll to.

    **A document height that happens to equal the viewport, on the one step
    that happened to be this short, is not proof the floor is gone** - it is
    one coincidental sample, on one step, at one viewport, and it is exactly
    what broke the moment that step legitimately grew (Task 2's two money
    fields). Two things are asserted instead, neither of them that constant:

    1. the document shrinks at all when content is removed - a stale floor
       pinned to a fixed value would not move;
    2. once essentially everything the step rendered is gone, the document
       settles *exactly* at the viewport, not a few pixels above it. That is
       `body { min-height: 100vh }` - the one floor this project still
       promises, and the direction it guarantees is the opposite one: the
       document is never *shorter* than the viewport, never that it is
       floored *above* its own content. A few pixels of drift on an
       otherwise-empty step is this defect's own signature, at this
       viewport's own scale.
    """
    page = advance_to(page_at(1278, 983, 1.25), 2)
    viewport = page.evaluate("window.innerHeight")

    # First: the document tracks content at all. `.section-intro` is small
    # enough that what remains is still comfortably taller than the viewport,
    # so `body { min-height: 100vh }`'s own, legitimate floor cannot be the
    # thing making this pass.
    before = page.evaluate("document.documentElement.scrollHeight")
    removed = page.evaluate(
        """() => {
          const el = document.querySelector('.section-intro');
          const height = Math.round(el.getBoundingClientRect().height);
          el.remove();
          return height;
        }"""
    )
    after_one_block = page.evaluate("document.documentElement.scrollHeight")
    assert after_one_block < before, (
        f"removing a {removed}px block left the document unchanged - {before} -> {after_one_block} - a stale floor"
    )

    # Second, and this is the part that actually distinguishes the two
    # floors: strip essentially everything the step rendered and see where
    # the document settles. Without a stale floor it settles exactly at the
    # viewport - the legitimate one. With the historical
    # `calc(100vh - 220px)` constant back on `.main-content` it settles a few
    # pixels above it instead, because that arithmetic no longer matches the
    # header/footer chrome it was written against once the step-indicator
    # band moved into the bar - the same drift that produced the original
    # 87px defect, just measured at this viewport's own scale.
    stripped = page.evaluate(
        """() => {
          document.querySelector('.amount-grid')?.remove();
          return document.documentElement.scrollHeight;
        }"""
    )
    assert stripped <= viewport + 5, (
        f"stripping the step's own content still leaves a {stripped}px document in a {viewport}px viewport - "
        "a stale floor is holding it up above its own content"
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


def _improvement_panel(page_at):
    """A page at step 5 (results) with the improvement panel open.

    Shared by every test in this module that needs the destination-allocation
    sliders — factored out rather than repeated so the wizard walk that reaches
    them is written once.
    """
    page = advance_to(page_at(1278, 983, 1.25), 5)
    page.click('[data-action="explore-improvements"]')
    return page


def test_the_improvement_percentage_refuses_a_minus_without_rewriting_the_number(page_at):
    """A negative share of a destination is not a thing, so the minus is refused
    outright — unlike a destination amount, which permits a leading minus
    precisely so that `validateCurrentStep`'s refusal has something to point at.

    The second half is the affirmative one and it is the reason this test is not
    just `assert "-" not in value`. A clamp would also produce a field with no
    minus in it, while destroying the number the visitor typed — that is exactly
    what `updateImprovementInput`'s `Math.min(100, Math.max(0, …))` did before
    PR #27 removed it, turning a typed `0.05` into `5`. The guard must block the
    minus and touch nothing else.
    """
    page = _improvement_panel(page_at)
    number = page.locator('.percentage-input input[type="number"]').first

    number.press("ControlOrMeta+A")
    number.press("Backspace")
    number.press_sequentially("--1")
    assert number.input_value() == "1"

    number.press("ControlOrMeta+A")
    number.press("Backspace")
    number.press_sequentially("205")
    assert number.input_value() == "205", (
        "the guard rewrote the visitor's number; it may only refuse the minus"
    )

    # Driving the slider needs a value away from its ceiling, and the reason is
    # itself worth stating: typing `205` above mirrored straight into this
    # sibling `input[type=range]`, whose `max="100"` made the browser clamp it —
    # which is how we know `updateImprovementInput`'s mirroring is live. From
    # 100 an ArrowUp has nowhere to go, so the field is reset first.
    number.press("ControlOrMeta+A")
    number.press("Backspace")
    number.press_sequentially("10")

    slider = page.locator('input[type="range"][data-improvement-code]').first
    before = slider.input_value()
    slider.press("ArrowUp")
    assert slider.input_value() != before, "the slider stopped responding"
    assert number.input_value() == slider.input_value()

    # The same invariant in the direction that actually breaks it, and the reason
    # the range carries `step="any"` rather than any fixed figure.
    # `<input type="range">` snaps *anything* assigned to `.value` to a multiple of
    # its own step; `<input type="number">` does not. So a range stepped more
    # coarsely than the box beside it leaves one allocation showing as two numbers
    # — a box reading 39.55 next to a slider sitting on 40 — and the visitor has no
    # way to tell which of the two the Compare button is about to send.
    #
    # Stage four is what made the two agree, and it did it by removing the range's
    # native step rather than by matching it to the box's: `updateImprovementInput`
    # rounds a *drag* to `rangeStep`'s own coarseness itself, so a drag still lands
    # on a nameable number while an exact figure mirrored in from the box arrives
    # unrounded. 39.55 is representable at the box's step and at no coarser one, so
    # this fails the moment a native step comes back onto the range — checked by
    # putting `step="5"` back and watching it report `box 39.55, slider 40`.
    number.press("ControlOrMeta+A")
    number.press("Backspace")
    number.press_sequentially("39.55")
    assert number.input_value() == "39.55"
    assert slider.input_value() == number.input_value(), (
        "the slider and the number box are showing different numbers for one "
        f"allocation: box {number.input_value()}, slider {slider.input_value()}"
    )


def test_the_kilogram_guard_refuses_an_allocation_the_percentage_guard_allows(page_at):
    """`improvementValidation` guards the allocation twice, and the second guard
    is not a belt-and-braces afterthought — it is the one that fires at ordinary
    scales.

    60 + 40.005 totals 100.005%, and |100.005 - 100| is 0.005, *inside* the
    0.01-percentage-point tolerance. The percentage guard passes it. On this
    journey's 1,000 kg the same allocation describes 1,000.05 kg of waste, which
    is 0.05 kg heavier than the current scenario and five times
    `MASS_TOLERANCE_KG`, so Compare Impact is refused by the kilogram guard
    alone.

    That refusal is the domain rule, not a preference: both scenarios have to
    move the same mass, or a net benefit can be inflated by quietly assuming
    less waste in the alternative. A percentage tolerance cannot express it,
    because 0.01 percentage points is a different number of kilograms at every
    tonnage.
    """
    page = advance_to(page_at(1278, 983, 1.25), 5)
    page.click('[data-action="explore-improvements"]')
    boxes = page.locator('.percentage-input input[type="number"]')
    assert boxes.count() >= 2, "this needs two destinations to split an allocation across"

    # The panel opens seeded from the current scenario, so every row is cleared
    # before the two under test are set; otherwise the seeded 100% is still in
    # the total and the percentage guard would refuse it too, for the wrong
    # reason.
    for index in range(boxes.count()):
        boxes.nth(index).fill("0")
    boxes.nth(0).fill("60")
    boxes.nth(1).fill("40.005")

    total = sum(float(boxes.nth(i).input_value() or 0) for i in range(boxes.count()))
    assert abs(total - 100) <= 0.01, (
        f"the premise of this test has moved: {total} is no longer inside the "
        "percentage tolerance, so it no longer isolates the kilogram guard"
    )
    compare = page.locator('[data-action="compare-improvement"]')
    assert compare.is_disabled(), (
        "Compare Impact was offered on an allocation 0.05 kg heavier than the "
        "current scenario; the kilogram guard is not firing"
    )
    assert page.locator("#improvement-inline-error").is_visible()

    # The positive control, on the same two rows: the refusal is about the
    # 0.005, not about the panel refusing everything.
    boxes.nth(1).fill("40")
    assert compare.is_enabled(), "an exact 60/40 split was refused"


def test_the_improvement_donut_draws_the_share_the_slider_holds(page_at):
    """The donut is display only (§7.6.1): every slice is an angle turned from a
    percentage the panel already shows as a number, never a second derivation of
    it. So the callout beside a slice has to read back the figure typed into the
    box, and it has to keep doing so on the keystroke path — which patches the
    DOM instead of re-rendering, precisely so the caret survives mid-number, and
    would therefore leave a stale chart if `updateImprovementInput` did not redraw
    it by hand.
    """
    page = advance_to(page_at(1278, 983, 1.25), 5)
    page.click('[data-action="explore-improvements"]')
    assert page.locator(".improvement-pie-chart").count() == 1

    number = page.locator('.percentage-input input[type="number"]').first
    number.press("ControlOrMeta+A")
    number.press("Backspace")
    number.press_sequentially("42.5")
    #: `all_inner_texts` is `innerText`, which an SVG element does not have; the
    #: callouts came back as `[None, None]` before this read `textContent`.
    labels = page.locator(".improvement-pie-label text").all_text_contents()
    assert "42.5%" in labels, (
        f"the donut did not redraw to the typed allocation; callouts were {labels}"
    )

    # Enlarging it is a second copy of the same chart, not a second chart: the
    # dialog reads the same state, so it cannot show a different allocation.
    page.click('[data-action="expand-improvement-chart"]')
    assert page.locator(".improvement-chart-modal .improvement-pie-chart").count() == 1
    page.click('[data-action="close-improvement-chart"]')
    assert page.locator(".improvement-chart-modal").count() == 0


def test_step_three_asks_what_the_stage_put_through(page_at):
    """Item ④. Without it the results page can never state waste as a share
    of production, which is the figure the client asked for - and the reason
    the old percentage was removed rather than fixed: `results.js` carries a
    note saying it was "a number the engine never produced".

    Optional, and the label says so. A visitor who does not know their
    production total still gets every other figure, so this must not become a
    fourth required field on a step that already has two.
    """
    #: `walk()`'s numeric yields are the screen sequence (0 sector, 1 food, 2
    #: amount, 3 destination, ...), one behind the UI's own 1-based "Step 3"
    #: label on the amount screen this field lives on - `2` is the amount
    #: screen; `3` is the destination-allocation screen one step later, whose
    #: `#total-waste` this file's other tests fill, never re-fill.
    page = advance_to(page_at(1278, 983, 1.25), 2)

    field = page.locator("#total-input")
    assert field.count() == 1, "step 3 has no production-total field"
    label = page.locator('label[for="total-input"]').inner_text()
    assert "optional" in label.lower(), (
        f"the field does not say it is optional: {label!r}"
    )

    #: Above the fold on the smallest viewport this project supports. Step 3
    #: already carries an amount, a unit and a container hint; a fourth
    #: control that pushes Continue off the screen is the defect
    #: `test_the_primary_action_of_every_step_is_reachable_without_scrolling`
    #: exists for.
    box = field.bounding_box()
    assert box["y"] < page.evaluate("window.innerHeight")


def test_step_three_controls_share_a_baseline_when_copy_wraps(page_at):
    """The three primary controls are one row even when their copy is not.

    At this width ``Waste amount`` has a one-line label and hint while the
    production-total label and the unit hint wrap. Normal document flow put
    the three controls on three different baselines, which is the misalignment
    the client and professor reported. Shared grid rows let the copy take the
    space it needs while keeping the controls together.

    The container case is measured separately because it adds live feedback
    below the amount input. A layout that aligns only the initial three-child
    fields moves that control again as soon as a visitor chooses a bin.

    Mutation: set these fields back to ``display: block`` and both measurements
    spread by more than one line-height.
    """
    page = advance_to(page_at(1278, 983, 1.25), 2)

    def control_tops():
        return page.locator("#total-waste, #unit-count, #total-unit, #total-input").evaluate_all(
            "controls => controls.map(control => Math.round(control.getBoundingClientRect().top))"
        )

    initial = control_tops()
    assert max(initial) - min(initial) <= 1, (
        f"step 3 controls do not share a baseline: {initial}"
    )

    preset = page.locator("#total-unit option").evaluate_all(
        "options => options.map(option => option.value).find(value => value.startsWith('preset:'))"
    )
    assert preset, "step 3 offers no container preset"
    page.select_option("#total-unit", preset)
    page.wait_for_selector("#unit-count")

    with_container_feedback = control_tops()
    assert max(with_container_feedback) - min(with_container_feedback) <= 1, (
        "container feedback moved the amount control off the shared baseline: "
        f"{with_container_feedback}"
    )


def test_the_production_total_names_its_unit_and_is_cleared_when_the_unit_changes(page_at):
    """`#total-input` is a mass in `state.totalUnit`, and `#total-unit` is the
    control that says which unit that is - so before this, changing the select
    silently reinterpreted whatever was already in the box. Both directions were
    measured on the running stack: 50000 typed against kilograms left as
    `"50000000.000"` once tonnes was chosen, and 50 typed against tonnes left as
    `"50.000"` once a container preset pinned `totalUnit` back to kilograms.

    **Two halves, and neither is sufficient alone.** The field is cleared, the
    same way `state.current` already is and for the same reason - the figure was
    entered against a unit that is no longer in force. And it *names* its unit,
    because a box that says only "Total amount produced" gives a visitor nothing
    to check the number against; `#total-waste` at least sits beside the select
    the visitor just used and is read back on the review step, and this field
    appears on neither screen again.

    Clearing rather than converting is deliberate: converting it would be the
    front end doing arithmetic on the visitor's behalf, on a figure they can no
    longer see, and the destination rows beside it are cleared rather than
    converted already.
    """
    page = advance_to(page_at(1278, 983, 1.25), 2)
    field = page.locator("#total-input")
    label = page.locator('label[for="total-input"]')

    assert "kilograms" in label.inner_text(), (
        f"the field does not name the unit it is read in: {label.inner_text()!r}"
    )

    field.fill("50000")
    page.select_option("#total-unit", "tonnes")
    page.wait_for_timeout(120)
    assert page.locator("#total-input").input_value() == "", (
        "50000 entered against kilograms survived the switch to tonnes, where it "
        "means a thousand times as much"
    )
    assert "tonnes" in page.locator('label[for="total-input"]').inner_text(), (
        "the label still names the old unit after the select changed"
    )

    # The other direction, and the one no arithmetic could have rescued: a
    # container pins `totalUnit` back to kilograms, so a figure entered in tonnes
    # would have been read as kilograms with nothing on screen having moved.
    page.fill("#total-input", "50")
    preset = page.locator("#total-unit option").evaluate_all(
        "options => options.map(o => o.value).filter(v => v.startsWith('preset:'))"
    )
    assert preset, "step 3 offers no container preset, so this half cannot be measured"
    page.select_option("#total-unit", preset[0])
    page.wait_for_timeout(120)
    assert page.locator("#total-input").input_value() == "", (
        "50 entered against tonnes survived the switch to a container, which pins "
        "the unit to kilograms"
    )
    assert "kilograms" in page.locator('label[for="total-input"]').inner_text()


def test_the_production_total_is_optional_and_continue_still_works(page_at):
    """The affirmative half. A test that only checks the field exists is
    satisfied by a field that blocks the form."""
    page = advance_to(page_at(1278, 983, 1.25), 2)

    page.fill("#total-waste", "1200")
    #: #total-input deliberately left empty
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector(".destination-row", timeout=5000)

    assert page.locator(".destination-row").count() > 0, (
        "an empty production total blocked the step it is optional on"
    )


def test_step_three_asks_for_the_two_money_figures(page_at):
    """Item ⑤. Both optional, both in New Zealand dollars, and both
    STATISTICS ONLY - the client's ruling on open item O-2 is that the value
    of the food does not enter the main formula and that cost price versus
    retail price is their own client's question.

    The currency is in the label rather than in a symbol beside the box: a
    bare `$` is ambiguous across the twenty languages this ships in, and the
    figure is only ever NZD.
    """
    #: `2`, not the UI's own "Step 3" label - see the comment on
    #: `test_step_three_asks_what_the_stage_put_through` above, which is the
    #: same amount screen these two fields join.
    page = advance_to(page_at(1278, 983, 1.25), 2)

    for field_id in ("total-value", "wasted-value"):
        field = page.locator(f"#{field_id}")
        assert field.count() == 1, f"no #{field_id}"
        # `count() == 1` is satisfied by a field that is `display: none` or
        # `disabled` just as readily as by one a visitor can actually use -
        # Task 1's own reviewer flagged exactly this gap. A hidden or
        # non-editable field never reaches `beforeinput`, never reaches
        # `state`, and never reaches the wire; the field has to be usable,
        # not merely present, and typing into it and reading the value back
        # is the only check that tells the difference.
        assert field.is_visible(), f"{field_id} exists but is not visible"
        assert field.is_editable(), f"{field_id} exists but cannot be typed into"
        field.fill("42")
        assert field.input_value() == "42", f"{field_id} did not keep a typed value"

        label = page.locator(f'label[for="{field_id}"]').inner_text()
        assert "optional" in label.lower(), f"{field_id} is not marked optional"
        assert "NZ$" in label or "NZD" in label, (
            f"{field_id} does not say which currency: {label!r}"
        )


@pytest.mark.parametrize("field_id", ["total-input", "total-value", "wasted-value"])
def test_a_negative_money_figure_is_refused_as_it_is_typed(page_at, field_id):
    """The same guard `#total-waste` and `#unit-count` already have.

    A negative value would reach stage one's `ge=0` and come back a 400 for
    the whole submission, after the visitor had left the screen the figure was
    on. The minus is refused at `beforeinput`, which is where the other two
    refuse it.

    All three of step 3's optional figures, for the reason the decimal test
    above gives: the guard names them one by one, so a test that asks about one
    of them says nothing about the others.
    """
    page = advance_to(page_at(1278, 983, 1.25), 2)
    field = page.locator(f"#{field_id}")

    field.press_sequentially("-500")

    assert field.input_value() == "500", (
        f"a minus reached #{field_id}: {field.input_value()!r}"
    )


def test_the_review_step_asks_what_period_the_figures_cover(page_at):
    """Item ⑦, and it belongs on step 5 rather than step 3.

    The client asked for it "在计算第六步出结果之前" - before the results. It
    is a statement about the whole submission, not about one supply-chain
    stage, so it sits with the review of everything rather than inside the
    per-entry loop where a visitor with three entries would be asked three
    times.

    The values are the four §6.2 accepts. A fifth would be refused by the API
    after the visitor pressed Calculate.
    """
    #: `4`, not the UI's own "Step 5" label - `walk()`'s numeric yields are
    #: one behind the 1-based on-screen label; `4` is the review screen,
    #: confirmed by what it waits for: `[data-action="calculate"]`.
    page = advance_to(page_at(1278, 983, 1.25), 4)

    select = page.locator("#time-frame")
    assert select.count() == 1, "the review step has no period selector"
    # `count() == 1` alone is satisfied by a hidden or disabled selector just
    # as readily as by one a visitor can actually use.
    assert select.is_visible(), "#time-frame exists but is not visible"
    assert select.is_enabled(), "#time-frame exists but cannot be used"

    values = select.locator("option").evaluate_all(
        "options => options.map(o => o.value)"
    )
    assert values == ["", "one_week", "one_month", "one_quarter", "one_year"], (
        f"the period vocabulary does not match what the API accepts: {values}"
    )

    # The field scales nothing - no figure is annualised, divided or multiplied
    # by it - and a visitor who picks "one week" has no way to know that from
    # the label alone. The natural assumption runs the other way, so the hint
    # carries the fact the label cannot.
    hint = page.locator(".time-frame-field .field-hint")
    assert hint.count() == 1, "the period selector has no explanatory hint"
    assert hint.is_visible(), "the period hint exists but is not visible"
    assert "result" in hint.inner_text().lower(), (
        "the period hint does not say it leaves the results unchanged"
    )


def test_the_period_is_optional_and_calculate_still_works(page_at):
    """Optional, like the other three. The empty option is first and
    selected, and leaving it there must not block the button."""
    page = advance_to(page_at(1278, 983, 1.25), 4)

    assert page.locator("#time-frame").input_value() == ""
    assert page.locator('.step-nav [data-action="calculate"]').is_enabled()


def test_the_four_new_values_reach_the_request_body(page_at):
    """**The assertion that the fields are wired to something.**

    Every test in Tasks 1-3 proves a control exists and holds a value. None
    of them proves the value leaves the browser, and a field bound to state
    that `submitCalculation` never reads is the most likely way this ships
    half-done - it looks right on every screen.

    The POST is intercepted rather than allowed through, so this measures
    what the front end sends rather than what the API tolerates.
    """
    page = page_at(1278, 983, 1.25)
    sent = {}
    page.route(
        "**/api/v1/calculate",
        lambda route: (sent.update(route.request.post_data_json), route.abort()),
    )

    #: `#total-input`, `#total-value` and `#wasted-value` live on the amount step
    #: (`walk()`'s `2`), not the review step (`4`) where `#time-frame` and the
    #: Calculate button are - so `walk()` is driven directly, rather than through
    #: `advance_to`, to fill each set of fields on the screen that actually carries it.
    for arrived in walk(page):
        if arrived == 2:
            #: **Tonnes, and that is the whole point of this line.** Filled while the
            #: entry unit was kilograms, the conversion on `total_input_kg` is the
            #: identity - so deleting it outright left this test green while claiming in
            #: its own comment to assert the conversion happened. 50 tonnes is 50,000 kg
            #: and no other reading of the field produces that number. The unit is
            #: selected before the field is filled because changing it clears the field.
            page.select_option("#total-unit", "tonnes")
            page.wait_for_timeout(80)
            page.fill("#total-waste", "1")
            page.fill("#total-input", "50")
            page.fill("#total-value", "120000")
            page.fill("#wasted-value", "4500")
        elif arrived == 4:
            page.select_option("#time-frame", "one_month")
            page.click('.step-nav [data-action="calculate"]')
            page.wait_for_timeout(400)
            break

    assert sent, "no request was made"
    assert sent["time_frame"] == "one_month"
    entry = sent["entries"][0]
    #: 50 tonnes is 50,000 kg. §1.2: decimals travel as strings because
    #: JavaScript's Number is a double.
    #:
    #: **Exactly `"50000"`, with no invented decimal places.** The send path
    #: ended in `.toFixed(3)`, which also *rounded* - a typed `1.2345` became
    #: `"1.234"`, the very rewrite the round-one fix refused to perform on the
    #: money fields two lines below. The two families now apply one rule.
    assert entry["total_input_kg"] == "50000"
    #: The two money fields are **not** reformatted - Fix round 1 found
    #: `Number(value).toFixed(2)` silently padding (and, for a third typed
    #: decimal, rounding) a figure nobody asked to have rewritten. "120000"
    #: and "4500" are exactly what was typed, and that is what must arrive.
    assert entry["total_value_nzd"] == "120000"
    assert entry["wasted_value_nzd"] == "4500"

    #: The front-end half of the agreement stage one's `test_evaluator.py`
    #: runs on the engine side: two independent pictures of one contract,
    #: and nothing but a test stops them drifting. `route.abort()` above
    #: proves the front end sends *a* shape; running the real Pydantic model
    #: over the captured body is what proves it sends *the* shape - cheaply,
    #: in CI, with no container and no network call.
    CalculatePayload.model_validate(sent)


def test_an_untouched_field_is_sent_as_null_rather_than_zero(page_at):
    """`None` and `0` are different claims, and stage one's schema keeps them
    apart. A front end that sent `"0"` for an empty box would make every
    visitor claim they produced nothing and wasted nothing."""
    page = page_at(1278, 983, 1.25)
    sent = {}
    page.route(
        "**/api/v1/calculate",
        lambda route: (sent.update(route.request.post_data_json), route.abort()),
    )

    page = advance_to(page, 4)
    page.click('.step-nav [data-action="calculate"]')
    page.wait_for_timeout(400)

    entry = sent["entries"][0]
    assert entry["total_input_kg"] is None
    assert entry["total_value_nzd"] is None
    assert entry["wasted_value_nzd"] is None
    assert sent["time_frame"] is None

    #: The absent shape agrees with §6.2 too - `null` on all four optional
    #: fields is what `CalculatePayload` accepts, not merely what this test
    #: asserts about it.
    CalculatePayload.model_validate(sent)


@pytest.mark.parametrize("field_id", ["total-value", "wasted-value"])
def test_a_third_decimal_in_a_money_field_is_refused_as_it_is_typed(page_at, field_id):
    """The same keystroke-level refusal `#wasted-value` already gives a
    minus sign, aimed at the decimal point instead of the sign.

    Fix round 1: `submitCalculation` used to call `Number(value).toFixed(2)`,
    so a visitor who typed "12.345" silently sent "12.35" - a figure they
    never wrote down. Rounding what already arrived is not an option §7.6.1
    allows (it is a calculation, and the front end's only permitted one is a
    unit conversion), so the third decimal has to be refused as it is typed,
    the way the minus already is - and then whatever is left must reach the
    wire completely unrounded, which `test_the_four_new_values_reach_the_
    request_body` is what checks.

    **Both money fields, because one was not enough.** Measured only against
    `#wasted-value`, a mutation narrowing the guard to that single id survived
    the whole file - and there is nothing about the guard that makes the two
    fields move together except a test that asks them both.
    """
    page = advance_to(page_at(1278, 983, 1.25), 2)
    field = page.locator(f"#{field_id}")

    field.press_sequentially("12.345")

    assert field.input_value() == "12.34", (
        f"a third decimal place reached #{field_id}: {field.input_value()!r}"
    )


def test_a_fourth_decimal_in_the_production_total_is_refused_as_it_is_typed(page_at):
    """`total_input_kg` is `DECIMAL(16,3)` (§6.2), so its ceiling is three rather
    than the money fields' two - and until now it had no ceiling at all while its
    send path silently rounded, which is the two families applying opposite rules
    to the same mistake.
    """
    page = advance_to(page_at(1278, 983, 1.25), 2)
    field = page.locator("#total-input")

    field.press_sequentially("1.2345")

    assert field.input_value() == "1.234", (
        f"a fourth decimal place reached the production total: {field.input_value()!r}"
    )
    #: And three are still typeable. A guard that refused the third as well would
    #: pass the assertion above while quietly imposing the money ceiling here.
    assert len(field.input_value().split(".")[1]) == 3
    #: The control has to declare the same granularity the guard enforces. It
    #: said `step="0.01"` - the money fields' - so the browser called the third
    #: decimal place invalid on a field whose contract column is DECIMAL(16,3),
    #: and the spinner stepped in hundredths of a kilogram.
    assert field.get_attribute("step") == "0.001", (
        "the production total declares a granularity its own guard does not enforce: "
        f"{field.get_attribute('step')!r}"
    )


def test_the_production_total_is_not_rounded_on_its_way_to_the_wire(page_at):
    """What the visitor typed is what is sent, in kilograms and in tonnes alike.

    `page.fill` hands the whole string over in one `beforeinput`, which the
    keystroke guard above deliberately does not count against the ceiling (see
    the note beside it: counting every digit of a six-digit fill refused an
    ordinary whole-number entry). That makes `fill` the way to ask what the send
    path does with a figure the guard never saw - and the answer used to be
    `.toFixed(3)`, which rounded `1.2345` to `1.234`.

    **`CalculatePayload.model_validate` is deliberately not run on the kilogram
    body.** `1.2345` kg is four decimal places, so §6.2 refuses it - and that is
    the honest outcome the round-one money fix chose over rewriting the figure:
    the ceiling is enforced at the keystroke, and anything that gets past it goes
    to the server as written rather than being quietly made acceptable. The
    tonnes body is valid and is checked.
    """

    def sent_body(unit, typed):
        page = page_at(1278, 983, 1.25)
        body = {}
        page.route(
            "**/api/v1/calculate",
            lambda route: (body.update(route.request.post_data_json), route.abort()),
        )
        for arrived in walk(page):
            if arrived == 2:
                page.select_option("#total-unit", unit)
                page.wait_for_timeout(80)
                page.fill("#total-waste", "1000" if unit == "kilograms" else "1")
                page.fill("#total-input", typed)
            elif arrived == 4:
                page.click('.step-nav [data-action="calculate"]')
                page.wait_for_timeout(400)
                break
        assert body, "no request was made"
        return body

    kilograms = sent_body("kilograms", "1.2345")
    assert kilograms["entries"][0]["total_input_kg"] == "1.2345", (
        "the production total was rounded on its way to the wire"
    )

    tonnes = sent_body("tonnes", "1.2345")
    #: 1.2345 t is 1234.5 kg exactly - the conversion gains three decimal places,
    #: so nothing is rounded here either, and the result is inside §6.2's three.
    assert tonnes["entries"][0]["total_input_kg"] == "1234.5"
    CalculatePayload.model_validate(tonnes)


def test_a_slider_cannot_be_dragged_past_what_is_left(page_at):
    """**Item ⑨, and the rule it works within does not change.**

    `improvementValidation` requires the allocation to total exactly 100% -
    that is what keeps both scenarios moving the same mass, so net benefit
    cannot be inflated by assuming less waste in the alternative. The client
    confirmed it stands. What is wrong is only that a slider will happily go
    past the remaining headroom and leave the visitor to notice.

    So each slider's own `max` is its current value plus whatever is
    unallocated. Pulling the first to 100 leaves the second unable to move
    above 0.
    """
    page = _improvement_panel(page_at)
    sliders = page.locator('input[type="range"][data-improvement-code]')
    assert sliders.count() >= 2, "need two destinations to test headroom"

    #: Everything to the first destination.
    sliders.nth(0).evaluate("el => { el.value = '100'; el.dispatchEvent(new Event('input', {bubbles: true})) }")
    page.wait_for_timeout(80)

    assert sliders.nth(1).get_attribute("max") == "0", (
        "the second slider still offers headroom that does not exist"
    )


def test_the_sliders_start_at_zero_and_the_total_says_so(page_at):
    """The client asked for "所有滑块默认都是 0".

    The panel currently seeds each destination with its CURRENT share, which
    is a reasonable starting point and is not what was asked for: a visitor
    modelling an improvement is choosing a new allocation, and starting from
    the old one hides which numbers they have actually decided.
    """
    page = _improvement_panel(page_at)

    values = page.locator('input[type="range"][data-improvement-code]').evaluate_all(
        "els => els.map(el => el.value)"
    )
    assert set(values) == {"0"}, f"sliders did not start at zero: {values}"
    assert "0.00" in page.locator("#improvement-total-value").inner_text()


def test_the_compare_button_is_still_gated_on_exactly_one_hundred(page_at):
    """**The affirmative half, and the rule this task must not break.**

    A slider that cannot overshoot could be built by clamping the total to
    100 and enabling the button - which would let 99.99% through and quietly
    change what the alternative scenario means. The gate stays.
    """
    page = _improvement_panel(page_at)
    sliders = page.locator('input[type="range"][data-improvement-code]')

    sliders.nth(0).evaluate("el => { el.value = '60'; el.dispatchEvent(new Event('input', {bubbles: true})) }")
    page.wait_for_timeout(80)

    assert page.locator('[data-action="compare-improvement"]').is_disabled(), (
        "Compare is enabled at 60% - the exactly-100 rule has been weakened"
    )

    sliders.nth(1).evaluate("el => { el.value = '40'; el.dispatchEvent(new Event('input', {bubbles: true})) }")
    page.wait_for_timeout(80)

    assert page.locator('[data-action="compare-improvement"]').is_enabled()


def test_the_improvement_panel_can_be_driven_in_kilograms(page_at):
    """Item ⑧. A toggle, and kilograms are the quantity the panel already
    works in underneath: `improvedLines` computes
    `totalKg * percentage / 100` before it sends anything.

    So this is a display and entry mode, not a second calculation - which is
    also why the toggle cannot change what is sent.
    """
    page = _improvement_panel(page_at)

    toggle = page.locator("#improvement-mode")
    assert toggle.count() == 1, "no percentage/kilograms toggle"

    page.select_option("#improvement-mode", "kilograms")
    page.wait_for_timeout(80)

    unit = page.locator(".percentage-input span").nth(0).inner_text()
    assert "kg" in unit.lower(), f"the unit beside the box did not change: {unit!r}"


def test_the_accessible_names_switch_to_kilograms_too(page_at):
    """The follow-up the coordinator raised on fix round 1.

    An `aria-label` is the only message a screen-reader visitor gets for a
    control - there is no visible text to fall back on. The range and the
    number box both carried `aria-label="Improved <destination> percentage"`
    / `"... percentage value"` unconditionally, so a visitor typing kilograms
    was told, on the one channel they could hear it, that the field wanted a
    percentage. Same defect as the validation message fixed alongside it,
    one layer further from what a sighted visitor notices.
    """
    page = _improvement_panel(page_at)

    page.select_option("#improvement-mode", "kilograms")
    page.wait_for_timeout(80)

    range_label = page.locator('input[type="range"][data-improvement-code]').nth(0).get_attribute("aria-label")
    box_label = page.locator('.percentage-input input[type="number"]').nth(0).get_attribute("aria-label")

    for label, name in ((range_label, "range"), (box_label, "number box")):
        assert label is not None, f"the {name} lost its accessible name entirely"
        assert "percentage" not in label.lower(), (
            f"the {name}'s accessible name still says percentage in kilogram mode: {label!r}"
        )
        assert "kilogram" in label.lower(), (
            f"the {name}'s accessible name does not name kilograms in kilogram mode: {label!r}"
        )


def test_switching_mode_preserves_the_allocation(page_at):
    """**The assertion that makes this a view and not a reset.**

    A visitor who has allocated 60/40 and switches to kilograms must see the
    same allocation expressed differently - not two empty boxes. Rebuilding
    the panel on toggle is the obvious implementation and it silently throws
    away their work.
    """
    page = _improvement_panel(page_at)
    sliders = page.locator('input[type="range"][data-improvement-code]')

    sliders.nth(0).evaluate("el => { el.value = '60'; el.dispatchEvent(new Event('input', {bubbles: true})) }")
    sliders.nth(1).evaluate("el => { el.value = '40'; el.dispatchEvent(new Event('input', {bubbles: true})) }")
    page.wait_for_timeout(80)

    total_kg = float(page.locator("#improvement-total-kg").inner_text().replace(",", ""))

    page.select_option("#improvement-mode", "kilograms")
    page.wait_for_timeout(80)

    boxes = page.locator('.percentage-input input[type="number"]').evaluate_all(
        "els => els.map(el => Number(el.value))"
    )
    assert abs(boxes[0] - total_kg * 0.6) < 0.01, (
        f"60% did not become 60% of the mass: {boxes[0]} against {total_kg}"
    )


def test_the_request_is_unchanged_by_the_mode(page_at):
    """The mode is a way of typing, and the wire never learns which was used.

    Asserted by driving the same allocation twice and comparing the bodies:
    a mode that changed what is sent would be a second calculation path, and
    the two would drift.
    """
    page = _improvement_panel(page_at)
    bodies = []
    page.route(
        "**/api/v1/calculate",
        lambda route: (bodies.append(route.request.post_data_json), route.abort()),
    )

    sliders = page.locator('input[type="range"][data-improvement-code]')
    sliders.nth(0).evaluate("el => { el.value = '60'; el.dispatchEvent(new Event('input', {bubbles: true})) }")
    sliders.nth(1).evaluate("el => { el.value = '40'; el.dispatchEvent(new Event('input', {bubbles: true})) }")
    page.wait_for_timeout(80)
    page.click('[data-action="compare-improvement"]')
    page.wait_for_timeout(400)

    page.select_option("#improvement-mode", "kilograms")
    page.wait_for_timeout(80)
    page.click('[data-action="compare-improvement"]')
    page.wait_for_timeout(400)

    assert len(bodies) == 2
    assert bodies[0]["entries"][0]["alternative"] == bodies[1]["entries"][0]["alternative"]


def test_a_kilogram_split_that_loses_a_digit_still_totals_exactly_100(page_at):
    """**The case the 60/40 split above cannot exercise.**

    60% and 40% of the 1,000 kg fixture entry are `600.000` and `400.000` -
    already exact at two decimal places, so a percentage that is rounded
    before it is stored survives that split by luck rather than by
    correctness. This uses four destinations whose kilogram figures were
    chosen so each one's *own* percentage share lands past the second decimal
    place - `10.005`, `20.015`, `30.025`, `39.955` of a 1,000 kg total - while
    the four kilogram figures themselves (`100.05 + 200.15 + 300.25 +
    399.55`) still sum to exactly `1000.00`.

    A conversion that rounds each destination's percentage to two places
    *before* storing it - rather than keeping the exact value and rounding
    only where it is displayed - drifts the total by two hundredths of a
    percentage point in the same direction on every one of the four, which
    is comfortably past `improvementValidation`'s own 0.01 tolerance.
    """
    page = _improvement_panel(page_at)
    page.select_option("#improvement-mode", "kilograms")
    page.wait_for_timeout(80)

    boxes = page.locator('.percentage-input input[type="number"]')
    assert boxes.count() >= 4, "need four destinations to test this split"
    for index, kilograms in enumerate(["100.05", "200.15", "300.25", "399.55"]):
        boxes.nth(index).fill(kilograms)
        page.wait_for_timeout(40)

    total = page.locator("#improvement-total-value").inner_text().strip()
    assert total == "100.00%", (
        f"a kilogram split that sums exactly to the entry's mass read back as {total!r}"
    )
    classes = page.locator(".improvement-total").get_attribute("class")
    assert "invalid" not in classes, f"a valid allocation was flagged invalid: {classes!r}"
    assert page.locator('[data-action="compare-improvement"]').is_enabled()

    # **The seam with the kilogram/percentage toggle itself.** `sliderMax` returns a
    # PERCENTAGE ceiling (`maxPercent`, at most 100 on any entry); `updateImprovementInput`
    # has to convert that through `displayKg` before it lands on a kilogram slider's `max`
    # attribute, or every kilogram slider is capped at a number sized for percentage points
    # - `100` kg on this 1,000 kg entry - long before its real headroom. The first
    # destination's own share here is ~100.05 kg, comfortably past that percentage-sized
    # ceiling, so a `max` at or below 100 proves the conversion was skipped.
    first_max = float(page.locator('input[type="range"][data-improvement-code]').nth(0).get_attribute("max"))
    assert first_max > 100, (
        f"the kilogram slider's ceiling is percentage-sized ({first_max!r}); "
        "updateImprovementInput must read it back through displayKg"
    )


#: Item 11: the client's report was "the gap between cards differs between
#: steps 1 and 2" - the sector step and the food-type step, the only two
#: screens shaped alike enough to compare side by side (a `<p class="section-intro">`,
#: then one fieldset, then the step bar, with nothing else on the screen). Measuring
#: `.content-section` itself found it identical on every step already - 0 margin, 0
#: padding, every screen - so the divergence the client saw was never in the section
#: the two `.content-section wide` steps share with the four plain ones; it was in
#: the first thing inside it. `.stage-fieldset` opened with `margin: 34px 0 0`;
#: `.choice-fieldset` opened with `margin: 30px 0 18px` - the same role, 4px apart,
#: on the one pair of steps where a visitor can see both in a row.
CONTENT_SECTION_BOX = """
() => {
  const section = document.querySelector('.content-section');
  if (!section) return null;
  const cs = getComputedStyle(section);
  return {
    marginBlockStart: cs.marginBlockStart,
    marginBlockEnd: cs.marginBlockEnd,
    paddingBlockStart: cs.paddingBlockStart,
    paddingBlockEnd: cs.paddingBlockEnd,
    rowGap: cs.rowGap,
  };
}
"""

FIRST_PANEL_MARGIN_BLOCK_START = """
(selector) => {
  const el = document.querySelector(selector);
  return el ? getComputedStyle(el).marginBlockStart : null;
}
"""


@pytest.mark.parametrize("width,height,dpr", [VIEWPORTS[0], VIEWPORTS[2]])
def test_content_section_outer_spacing_is_equal_on_every_step(page_at, width, height, dpr):
    """`.content-section`'s own margin-block, padding-block and row-gap, walked across
    all seven screens at 1278 and 390 - the two widths the item 11 measurement pass
    used - and asserted identical.

    This already reads the same box on every screen (0 margin, 0 padding, `normal`
    gap): the outer container was never the divergence. It is asserted here anyway,
    so a future change that gives one step its own `.content-section` padding - the
    obvious place to reach for a "quick" per-step spacing fix - fails a test instead
    of surfacing in the next demonstration.
    """
    page = page_at(width, height, dpr)
    boxes = {}
    for step in walk(page):
        page.wait_for_timeout(60)
        box = page.evaluate(CONTENT_SECTION_BOX)
        if box is not None:
            boxes[step] = box
    assert len(boxes) >= 5, f"too few steps rendered a .content-section to compare: {boxes}"
    first_step, first_box = next(iter(boxes.items()))
    mismatched = {step: box for step, box in boxes.items() if box != first_box}
    assert not mismatched, (
        f".content-section's own spacing is not equal across steps: step {first_step} "
        f"measured {first_box}, but {mismatched} differ from it"
    )


@pytest.mark.parametrize("width,height,dpr", [VIEWPORTS[0], VIEWPORTS[2]])
def test_the_gap_above_the_first_panel_matches_between_the_sector_and_food_type_steps(page_at, width, height, dpr):
    """The actual item 11 defect, measured directly: the sector step's `.stage-fieldset`
    and the food-type step's `.choice-fieldset` sit in the identical position - directly
    after the intro paragraph, directly before the step bar - and are the only two panels
    in the wizard alike enough for a visitor to notice one sitting closer than the other.

    Before the fix this failed at both viewports with `34px` against `30px`. The fix
    changed `.choice-fieldset`'s `margin-top` to match `.stage-fieldset`'s rather than
    giving either one a new override; `.choice-fieldset`'s bottom margin is untouched,
    because it alone clears an optional "Clear optional selection" button `.stage-fieldset`
    never renders.
    """
    page = page_at(width, height, dpr)
    margins = {}
    for step in walk(page):
        if step == 0:
            margins[0] = page.evaluate(FIRST_PANEL_MARGIN_BLOCK_START, ".stage-fieldset")
        elif step == 1:
            margins[1] = page.evaluate(FIRST_PANEL_MARGIN_BLOCK_START, ".choice-fieldset")
            break
    assert margins.get(0) and margins.get(1), f"could not measure both panels: {margins}"
    assert margins[0] == margins[1], (
        f"the gap above the first panel differs between the sector step ({margins[0]}) "
        f"and the food-type step ({margins[1]})"
    )
