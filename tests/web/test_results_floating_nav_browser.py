"""The results page's floating section nav: where its box is, and where the reader is.

**The defect this file was written for is that the panel covered the page.** The
nav is anchored just outside the content column, but the panel hangs off the
handle's *inline-start* edge, so it opened backwards -- over the text -- and it
opened by default at every width it was drawn at. Measured on the running stack
before the fix, English, with the column at 32-1032 at a 1100px window:

    window   column right   panel            on the text
    1100     1032           788 - 1040       244 of 252px
    1280     1122           878 - 1130       244 of 252px
    1440     1202           958 - 1210       244 of 252px
    1600     1282           1038 - 1290      244 of 252px
    1920     1442           1198 - 1450      244 of 252px

Arabic mirrored it exactly (panel 214-466 against a column starting at 222), and
none of it needed a single interaction: that was the state a visitor was handed.

**So this measures boxes, and never class names.** Every class involved was
present and correct for the whole life of the defect; a test that asserted
`.results-floating-nav__panel` existed, or that the nav carried `data-open`,
would have been green throughout. What was wrong was two rectangles overlapping,
so two rectangles are what is compared -- at five widths, in both writing
directions, in the state a visitor gets without touching anything.

**Three more defects landed here afterwards, and all three are geometry too.**

*Press the second item and the third lights up.* A jump leaves its target at
`top: 24` (`scroll-margin-top`, measured at all four sections), the reading band
is 162-360 at a 900px viewport, and *Tangible equivalents* is 287px tall -- so it
rested at 24-311 with the next section creeping to 347, 13px inside the band, and
the rule "take the last section in the band" handed the mark to the successor. The
rule is the first section in the band now, which is the one occupying the band's
top edge, and a press additionally *pins* its own section until the reader scrolls:
`html { scroll-behavior: smooth }` means the observer spends several hundred
milliseconds answering honestly about a page still in transit.

*The current item was a filled Pea block.* Pea measures 2.04:1 on the panel's
frosted ground and 2.17:1 on the white actually painted behind it, so "the item's
text in the accent" is not available at all; the mark is Kale at full weight
against a list pushed back to `--muted`, lifted by a Pea-tinted shadow, with the
accent on a rule in a green dark enough to carry it. The ratios are asserted, not
the hex values.

*The docked nav had a handle.* It is permanent in that regime, so there was nothing
for the handle to expand -- and it was in the tab order. `display: none`, measured
by `focus()` refusing it rather than by a class name.

**The scroll-spy is asserted through the observer's own output**, not through the
observer. `render()` (`web/js/calculator.js`) replaces `main.innerHTML` on every
`setState`, so the four sections the `IntersectionObserver` watches are destroyed
and rebuilt constantly -- a keystroke in the improvement panel is a full rebuild.
The two failure modes are opposite and neither shows up in a screenshot: an
observer wired once is left holding detached nodes and silently stops reporting,
and an observer created per render is one live observer per keystroke. Both are
measured below, the first by scrolling *after* a dozen re-renders and the second
by counting constructions from a page init script.

**Everything here is measured against the running stack**, and the stack serves a
*baked* copy of ``web/`` (``docker/web.Dockerfile`` does ``COPY web/``, not a bind
mount), so an edit to the front end means::

    docker compose -f docker/compose.yaml build web
    docker compose -f docker/compose.yaml up -d web

before any of this measures anything. Skipping that measures the previous image.

Requires Playwright and the stack::

    pip install playwright && playwright install chromium
    docker compose -f docker/compose.yaml up -d
    pytest tests/web/test_results_floating_nav_browser.py
"""

from __future__ import annotations

import os
import re

import pytest

from tests.web.steps import press_continue


pytestmark = pytest.mark.browser

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to measure a rendered layout; the floating nav is unverified without it",
)

BASE = os.environ.get("KAICALC_WEB_URL", "http://localhost:18080").rstrip("/") + "/index.html"

#: The widths the owner measured the defect at, kept as the widths it is fixed at.
WIDTHS = (1100, 1280, 1440, 1600, 1920)

#: `styles.css` docks the panel in the gutter from here up and leaves it a closed
#: handle below. **Stated here as a number on purpose**: the stylesheet derives it
#: (24px of clearance + a 252px panel + 16px before the viewport edge = 292px of
#: gutter, and the gutter is `clientWidth / 2 - 490`, so `clientWidth >= 1564`,
#: plus a 17px scrollbar because a media query is matched against the window
#: width), and if this file re-derived it the two derivations could agree with
#: each other while both disagreeing with what the browser draws. The assertions
#: below are about the rectangles either side of it, not about the number.
DOCKED_FROM = 1600

#: `html { scroll-behavior: smooth }` otherwise returns mid-animation geometry from
#: a `getBoundingClientRect` taken straight after a scroll - the same
#: instrumentation `test_step_navigation.py` and `test_step_three_zones_browser.py`
#: inject, for the same reason.
FORCE_AUTO = "html { scroll-behavior: auto !important; }"

#: One round trip, every rectangle the assertions below ask about. `visible` is
#: the computed state and not a class: `visibility: hidden` is what takes the
#: closed panel out of the page AND out of the tab order, so it is the thing that
#: decides whether the panel is covering anything.
GEOMETRY = """
() => {
  const box = el => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height};
  };
  const nav = document.querySelector('.results-floating-nav');
  const panel = document.querySelector('.results-floating-nav__panel');
  const handle = document.querySelector('.results-floating-nav__handle');
  const panelStyle = panel ? getComputedStyle(panel) : null;
  return {
    clientWidth: document.documentElement.clientWidth,
    innerWidth: window.innerWidth,
    dir: document.documentElement.dir || 'ltr',
    column: box(document.querySelector('.results-page')),
    nav: box(nav),
    panel: box(panel),
    handle: box(handle),
    navDisplay: nav ? getComputedStyle(nav).display : null,
    panelVisible: panelStyle ? panelStyle.visibility === 'visible' && panelStyle.opacity !== '0' : false,
    panelBackground: panelStyle ? panelStyle.backgroundColor : null,
    panelColor: panelStyle ? panelStyle.color : null,
    dataOpen: nav ? nav.getAttribute('data-open') : null,
    ariaExpanded: handle ? handle.getAttribute('aria-expanded') : null,
    handleDisplay: handle ? getComputedStyle(handle).display : null,
  };
}
"""

#: Can the handle be reached at all? `display: none` is what takes it out of the
#: accessibility tree and the tab order together, and the honest question to ask of
#: the tab order is whether focus will land on the element -- `focus()` is refused by
#: a `display: none` button and accepted by every other way of hiding one, which is
#: why this is asked rather than the computed `display` alone.
FOCUS_HANDLE = """
() => {
  const handle = document.querySelector('.results-floating-nav__handle');
  if (!handle) return {present: false, focused: false};
  handle.focus();
  return {present: true, focused: document.activeElement === handle};
}
"""

#: Put a section's top on the line a *click* would leave it on. `24` is
#: `scroll-margin-top` (`styles.css`) and `SECTION_REST_TOP` (`results.js`), and it
#: is the position the tie-break is decided at -- see
#: `test_the_band_marks_the_section_at_its_top_edge_not_the_one_creeping_in_below`.
SCROLL_TO_REST = """
id => {
  const target = document.getElementById(id);
  window.scrollTo({top: target.getBoundingClientRect().top + window.scrollY - 24, behavior: 'instant'});
}
"""

#: The reading band `results.js` decides the mark inside, in this file's viewport.
#: `SECTION_BAND` is the `rootMargin` `-18% 0px -60% 0px`, and every case in this
#: file is measured at 900px tall, so the band is 162-360. **Stated as two numbers
#: for the same reason `DOCKED_FROM` is**: re-deriving the percentages here would
#: let this file and `results.js` agree with each other while both disagreed with
#: what the browser observes. They are one pair of names rather than four inline
#: literals because a band spelled in several places is the kind of duplicate this
#: file has already been bitten by.
BAND_TOP = 162
BAND_BOTTOM = 360

#: Every section's box, so an assertion about which one the spy picked can print the
#: geometry that made that the right or the wrong answer.
#:
#: **Read out of the nav's own link list, not named here.** It used to carry the four
#: ids as literals, which was a second copy of `RESULTS_NAV_SECTIONS` -- the thing
#: that list's docstring exists to prevent ("the order is the whole of this nav's
#: correctness"). A fifth entry added there and not here is not a failure, it is a
#: silent loss of coverage: in `fills_the_band`, the last of the hard-coded four has
#: `successor is None`, so `assert successor is None or successor_top > BAND_BOTTOM`
#: passes **vacuously** about a successor the nav does in fact claim.
#:
#: Reading the page instead means the one list is still the one list. What stops this
#: from being circular -- a nav that drops a section agreeing with itself -- is
#: `test_the_results_sections_are_in_the_order_the_floating_nav_claims` in
#: `test_results_export.py`, which names the four and fails on a fifth or a fourth
#: missing. That is the right place for the roster: it is a contract assertion and it
#: needs no browser. Here, what matters is that the geometry measured is the geometry
#: of whatever the nav actually offers.
#:
#: A link whose target is not on the page returns `null` for its box rather than
#: throwing, so the caller can say *which* id went missing.
SECTION_BOXES = """
() => [...document.querySelectorAll('.results-floating-nav__links a[href^="#"]')]
  .map(a => a.getAttribute('href').slice(1))
  .map(id => {
    const el = document.getElementById(id);
    if (!el) return {id, top: null, bottom: null, height: null};
    const r = el.getBoundingClientRect();
    return {id, top: Math.round(r.top), bottom: Math.round(r.bottom), height: Math.round(r.height)};
  })
"""


def _luminance(colour):
    """WCAG relative luminance of an `rgb(r, g, b)` string or a `#rrggbb` literal."""
    if colour.startswith("#"):
        parts = [int(colour[i:i + 2], 16) for i in (1, 3, 5)]
    else:
        parts = [int(part) for part in re.findall(r"\d+", colour)[:3]]
    channels = []
    for part in parts:
        value = part / 255.0
        channels.append(value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast(first, second):
    """The WCAG 2.x ratio between two colours, in the range 1.0 - 21.0."""
    high, low = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)

#: Which link carries the reader's position, and how many claim to.
MARK = """
() => {
  const links = [...document.querySelectorAll('.results-floating-nav__links a')];
  const marked = links.filter(a => a.getAttribute('aria-current') !== null);
  return {
    href: marked.length === 1 ? marked[0].getAttribute('href') : null,
    values: marked.map(a => a.getAttribute('aria-current')),
    count: marked.length,
    background: marked.length === 1 ? getComputedStyle(marked[0]).backgroundColor : null,
    colour: marked.length === 1 ? getComputedStyle(marked[0]).color : null,
    order: links.map(a => a.getAttribute('href')),
  };
}
"""

#: Counts every `IntersectionObserver` the page ever constructs, installed before
#: any of `web/js/` runs. The leak this guards is invisible from the DOM: a
#: hundred live observers and one live observer render the same page.
COUNT_OBSERVERS = """
(() => {
  const Real = window.IntersectionObserver;
  window.__observersMade = 0;
  window.__observersDisconnected = 0;
  window.IntersectionObserver = class extends Real {
    constructor(...args) { super(...args); window.__observersMade += 1; }
    disconnect(...args) { window.__observersDisconnected += 1; return super.disconnect(...args); }
  };
})()
"""

SCROLL_TO = """
id => {
  const target = document.getElementById(id);
  window.scrollTo({top: target.getBoundingClientRect().top + window.scrollY - 40, behavior: 'instant'});
}
"""


@pytest.fixture(scope="module")
def browser(_playwright):
    """**Scrollbars drawn, which is the second thing this file had to get right.**

    The package fixture in `conftest.py` launches Chromium with Playwright's
    default `--hide-scrollbars`, so `document.documentElement.clientWidth` equals
    the viewport width and every gutter measures 15px wider than any visitor's.
    That is not a detail here: the gutter this nav lives in is `clientWidth / 2 -
    490`, so at a 1100px window it is **52.5px with a scrollbar and 60px without**,
    and the handle is 52px. Measured, and measured because a mutation caught it:
    putting the old `calc(480px + 18px)` offset back leaves the handle at
    1040-1092 against a client width of 1085 - off the page - and every assertion
    in this file passed anyway, because the browser it ran in had no scrollbar and
    reported 1100.

    Two Chromium instances launched from the *same* `_playwright` driver coexist
    fine; two `sync_playwright()` contexts on one thread do not. This is the same
    exception, built the same way, as `test_horizontal_overflow.py`'s.
    """
    instance = _playwright.chromium.launch(ignore_default_args=["--hide-scrollbars"])
    yield instance
    instance.close()


def _overlap(first, second):
    """How many pixels of `first` lie inside `second`, horizontally.

    Physical left/right rather than logical start/end, deliberately: overlap is
    the same question in both writing directions, and comparing raw rectangles is
    what makes the Arabic case the *same* assertion rather than a mirrored one.
    """
    return max(0.0, min(first["right"], second["right"]) - max(first["left"], second["left"]))


def _open(browser, width, height=900, lang="en", init_script=None, force_auto_scroll=True):
    #: `bypass_csp` is instrumentation and nothing more, exactly as it is in
    #: `test_step_three_zones_browser.py`: `style-src 'self'` correctly refuses the
    #: `<style>` element `add_style_tag` creates, and `FORCE_AUTO` is the only
    #: thing being injected through it. The policy itself is enforced against a
    #: real browser with no bypass in `tests/web/test_csp.py`.
    context = browser.new_context(
        viewport={"width": width, "height": height}, locale="en-NZ", bypass_csp=True)
    if init_script:
        context.add_init_script(init_script)
    page = context.new_page()
    try:
        page.goto(f"{BASE}?lang={lang}", wait_until="networkidle", timeout=20000)
    except Exception as error:  # pragma: no cover - environment guard
        context.close()
        pytest.skip(f"the front end is not being served at {BASE}: {error}")
    page.wait_for_selector('[data-action="start"]', timeout=10000)
    #: One test wants the smooth scroll left ON, because what it measures is the
    #: mark's behaviour *during* the travel a press starts. Everything else wants it
    #: off, for the reason `FORCE_AUTO` gives.
    if force_auto_scroll:
        page.add_style_tag(content=FORCE_AUTO)
    return context, page


def _to_results(page):
    """One entry, one food, one destination, calculated: the shortest walk to step 5."""
    page.click('[data-action="start"]')
    page.wait_for_selector('input[name="sector"]')
    page.evaluate("document.querySelectorAll('input[name=sector]')[0].click()")
    page.wait_for_timeout(80)
    press_continue(page)
    page.wait_for_selector('input[name="food-category"]')
    page.locator('input[name="food-category"]').nth(0).click()
    page.wait_for_timeout(60)
    press_continue(page)
    page.wait_for_selector('[data-leaf-field="amount"]')
    for field in page.evaluate("() => [...document.querySelectorAll('[data-leaf-field=amount]')].map(e => e.id)"):
        page.fill(f"#{field}", "1000")
        page.wait_for_timeout(40)
    press_continue(page)
    page.wait_for_selector('[data-line-field="amount"]')
    first_row = page.evaluate(
        """() => {
          const byLeaf = {};
          for (const input of document.querySelectorAll('[data-line-field=amount]')) {
            (byLeaf[input.dataset.leaf] ||= []).push(input.id);
          }
          return Object.values(byLeaf).map(ids => ids[0]);
        }""")
    for field in first_row:
        page.fill(f"#{field}", "1000")
        page.wait_for_timeout(60)
    press_continue(page)
    page.wait_for_selector('[data-action="calculate"]')
    page.click('[data-action="calculate"]')
    page.wait_for_selector("#results-title", timeout=30000)
    page.wait_for_timeout(250)


@pytest.fixture(scope="module")
def results(browser):
    """One walk to step 5, shared by the tests that only read geometry.

    Resizing the viewport is what moves this nav between its two regimes, and
    resizing is also the stronger measurement: the page is NOT re-rendered, so
    anything that responds is responding in CSS, which is where this nav's
    regimes are decided.
    """
    context, page = _open(browser, WIDTHS[-1])
    _to_results(page)
    yield page
    context.close()


def _at(page, width, height=900):
    page.set_viewport_size({"width": width, "height": height})
    page.wait_for_timeout(200)
    return page.evaluate(GEOMETRY)


@pytest.mark.parametrize("width", WIDTHS)
def test_the_nav_never_covers_the_results_column_in_the_state_a_visitor_is_given(results, width):
    """**The defect, stated as the two rectangles that were on top of each other.**

    Nothing is clicked here: this is the page as it arrives. Whatever is drawn -
    the handle always, the panel where the stylesheet docks it - has to be clear
    of the column, and inside the viewport, at every width the nav exists at.

    Mutation: putting `margin-inline-start: calc(480px + 18px)` back and letting
    the panel open by default (`:not([data-open="false"])` with no media query)
    reproduces the measurements in this file's docstring and fails at all five.
    """
    measured = _at(results, width)
    assert measured["navDisplay"] != "none", f"the floating nav is not drawn at {width}px at all"
    column, handle, panel = measured["column"], measured["handle"], measured["panel"]

    assert _overlap(handle, column) == 0, (
        f"at {width}px the handle covers the column: handle "
        f"{handle['left']:.1f}-{handle['right']:.1f} against a column of "
        f"{column['left']:.1f}-{column['right']:.1f}"
    )
    if measured["panelVisible"]:
        assert _overlap(panel, column) == 0, (
            f"at {width}px the panel is open by default and covers "
            f"{_overlap(panel, column):.0f}px of the column: panel "
            f"{panel['left']:.1f}-{panel['right']:.1f} against a column of "
            f"{column['left']:.1f}-{column['right']:.1f}. A visitor who has touched "
            f"nothing is reading text with a menu on top of it"
        )

    #: And it is on the page rather than half of it under the scrollbar. `nav` is
    #: the whole floating box, so this covers the docked panel and the bare handle
    #: with one measurement.
    nav = measured["nav"]
    assert nav["left"] >= 0 and nav["right"] <= measured["clientWidth"], (
        f"at {width}px the nav runs outside the viewport: {nav['left']:.1f}-{nav['right']:.1f} "
        f"against a client width of {measured['clientWidth']}. A `position: fixed` box "
        f"does not move `scrollWidth`, so nothing else in the suite can see this"
    )


@pytest.mark.parametrize("width", WIDTHS)
def test_the_panel_is_open_where_it_fits_beside_the_column_and_closed_where_it_would_not(results, width):
    """**Two regimes, and the default state is different in each.**

    Docked: the list is *there*, in the gutter, because there is room for it to be
    there. Undocked: a handle, because the only way to show a 252px panel in a
    142px gutter is to put it on the text. Which regime is on screen is the
    stylesheet's decision and nothing here re-derives it -- the assertion is that
    an open panel is a panel with somewhere to be.

    Mutation: deleting the `@media (min-width: 1600px)` block leaves every width
    undocked, and 1600 and 1920 fail on a panel that is hidden where it fits.
    Moving that block *above* the panel's own rules (where `@media` adds no
    specificity and source order decides) leaves the docked panel `position:
    absolute`, hung off the handle at 1254-1506 with the nav starting at 1306,
    and the previous test fails at 1600 and 1920 on the 28px of column that
    puts back under an open panel.
    """
    measured = _at(results, width)
    column, panel = measured["column"], measured["panel"]
    gutter = measured["clientWidth"] / 2 - 490

    if width >= DOCKED_FROM:
        assert measured["panelVisible"], (
            f"at {width}px there is {gutter:.0f}px of gutter, which is room for the "
            f"252px panel, and it is closed anyway. The list is meant to be there "
            f"where it can be"
        )
        #: Both clearances, because a panel jammed against either edge is a panel
        #: that looks like an accident. 24px from the column, 16px from the
        #: viewport: the two numbers the breakpoint was derived from.
        from_column = min(abs(panel["left"] - column["right"]), abs(column["left"] - panel["right"]))
        from_edge = min(panel["left"], measured["clientWidth"] - panel["right"])
        assert from_column >= 16, f"at {width}px the docked panel is {from_column:.1f}px from the column"
        assert from_edge >= 16, f"at {width}px the docked panel is {from_edge:.1f}px from the viewport edge"
        #: Light ground, Kale text (CLAUDE.md's brand rule). The translucent white
        #: is the frosted glass; it is readable because `backdrop-filter` is behind
        #: it, and `@supports not (...)` hands a browser without one an opaque
        #: ground instead of Kale text over whatever paragraph is behind the panel.
        assert measured["panelColor"] == "rgb(0, 50, 35)", (
            f"the panel's text is {measured['panelColor']}, not Kale. It sits on a light "
            f"ground now, and a light ground takes Kale text"
        )
        assert measured["panelBackground"] == "rgba(255, 255, 255, 0.72)", (
            f"the panel's ground is {measured['panelBackground']}: the frosted glass is "
            f"a translucent light ground, not the Kale card this used to be"
        )
    else:
        assert not measured["panelVisible"], (
            f"at {width}px the gutter is {gutter:.0f}px and the panel is 252px, so an "
            f"open panel here is an open panel on the text -- and this is the state "
            f"of the page before the visitor has touched anything"
        )

    #: In neither regime does the markup claim a state. `data-open` and
    #: `aria-expanded` are written only once the visitor has toggled, because the
    #: default is viewport-dependent and CSS is what knows the viewport.
    assert measured["dataOpen"] is None, measured["dataOpen"]
    assert measured["ariaExpanded"] is None, measured["ariaExpanded"]


@pytest.mark.parametrize("width", [1100, 1280, 1440])
def test_the_first_press_of_the_handle_opens_the_list_it_is_the_only_way_into(browser, width):
    """**The handle now belongs to one regime, and in that regime the default is
    closed - so the first press opens.**

    `state.resultsNavOpen` is `undefined` until the visitor touches the handle, and
    the first press has nothing on `state` to invert. It used to assume the default
    was "open" and set `false`, which was a dead button here: press, and the
    already-closed panel is closed again. It then asked `resultsNavIsDocked()` which
    of two defaults was on screen. There is only one default a press can be made
    against now, because the docked regime draws no handle at all (the test below),
    but the expression is unchanged and the behaviour it has to produce is this.

    Mutation: `state.resultsNavOpen === undefined ? false : ...` back in
    `calculator.js` leaves every width here pressing a button that does nothing.
    """
    context, page = _open(browser, width)
    try:
        _to_results(page)
        before = page.evaluate(GEOMETRY)
        assert not before["panelVisible"], "the fixture is not in the closed regime this test is about"
        page.click(".results-floating-nav__handle")
        page.wait_for_timeout(300)
        after = page.evaluate(GEOMETRY)
    finally:
        context.close()

    assert after["panelVisible"], (
        f"at {width}px the panel was closed and the first press left it closed. This is "
        f"the only regime with a handle in it, and the only thing its first press can "
        f"sensibly do is show the list"
    )
    #: And now the markup says so, in both places, agreeing with each other.
    assert after["dataOpen"] == "true", after["dataOpen"]
    assert after["ariaExpanded"] == "true", after["ariaExpanded"]


#: Where focus actually is. The `id` is what `main.js` restores focus *by*, so an
#: empty one is the whole of the defect below; the tag name is what says where it
#: ended up instead.
FOCUS_NOW = """
() => {
  const node = document.activeElement
  return {id: node ? node.id : null, tag: node ? node.tagName : null}
}
"""

#: The one id-less focusable control on this page that is reliably clear of the open
#: panel. The panel is 252px wide in the gutter and vertically centred -- at 1280 it
#: covers x 878-1130 of a 131-1131 column -- and `<summary>` is a block, so Playwright
#: clicks its centre at x ~631, well inside the column and well clear of the panel.
#: **Id-less is the point**: `main.js` restores focus by `id`, and for a control
#: without one `document.activeElement.id` is `''`, `getElementById('')` is `null`,
#: and `main.focus()` wins. The improvement panel's number box and all three action
#: buttons are in the same position, and clicking into the number box is how the
#: regression was found.
ID_LESS_CONTROL = "#results-methodology summary"


def test_clicking_outside_the_open_nav_closes_it_and_does_not_take_the_focus(browser):
    """**Click-away, and the re-render it must not cost.**

    The overlay covers the text in this regime, so a click on the page is the
    reader saying they are done with the menu, and it closes. That much is the
    feature. The three things around it are why this test is longer than it:

    **A click inside the nav keeps it open.** This is the `closest('.results-
    floating-nav')` guard, and it is asserted here rather than left to read
    correctly, because the panel's own padding is the only place a click can land
    that is inside the nav and not on a control. Mutation: delete
    `!event.target?.closest?.('.results-floating-nav') &&` from the document click
    listener in `results.js`. It fails here, on the click inside -- and it also
    fails `test_the_first_press_of_the_handle_opens_the_list_it_is_the_only_way_
    into`, because the handle's own click would then close what it had just
    opened. The guard was unasserted while *that* test was the only thing holding
    it, and the test about the dismissal is the one that should report it.

    **Closing must not move the reader's focus**, which is what makes this a
    dismissal rather than a re-render. `setState` rebuilds `main.innerHTML` and
    `main.js` then restores focus by `id`; `document.activeElement.id` is `''` for
    an id-less control, `document.getElementById('')` is `null`, and `main.focus()`
    takes it. The results page is full of id-less focusable controls -- the
    improvement panel's number box, the methodology `<summary>`, all three action
    buttons -- so a `setState` here means that opening the menu and then clicking
    into the number box throws the reader to the top of the page. Before the
    dismissal existed, those clicks caused no `setState` at all. Mutation:
    `setState({ resultsNavOpen: false })` in place of `closeResultsNav()` fails the
    focus assertion with `main-content/MAIN`.

    **The docked regime is untouched.** From 1600px up the panel *is* the nav and
    `data-open` must stay absent, because its absence is what lets the stylesheet
    decide and the stylesheet's decision there is "open". Mutation: dropping the
    `resultsNavIsDocked()` / `resultsNavPanelIsOpen` guards from `closeResultsNav`
    stamps `data-open="false"` on a nav nobody touched, and this fails on it while
    `panelVisible` stays true -- which is exactly how that defect would reach a
    visitor, invisibly, until their window got smaller.
    """
    context, page = _open(browser, 1280)
    try:
        _to_results(page)

        #: **The docked regime first, while nothing has been pressed**, because
        #: what is asserted there is the *untouched* state and the narrow phases
        #: below leave `data-open` behind them. A resize is not a re-render, so
        #: this is the same page in its other regime.
        docked_before = _at(page, 1600)
        page.click(ID_LESS_CONTROL)
        page.wait_for_timeout(250)
        docked_after = page.evaluate(GEOMETRY)

        _at(page, 1280)
        page.click(".results-floating-nav__handle")
        page.wait_for_timeout(250)
        opened = page.evaluate(GEOMETRY)

        #: Inside the nav, on the panel's own 8px of padding rather than on a link
        #: (a link takes the first branch of the listener and never reaches the
        #: guard) and rather than on the handle (which would legitimately toggle).
        #:
        #: `mouse.click` at a coordinate rather than `page.click` on the selector,
        #: so that a mutation which closes the panel early fails on an *assertion*
        #: instead of on a 30-second actionability timeout: the panel keeps its box
        #: when it is hidden, so the coordinate stays valid and the click simply
        #: lands on the column underneath -- which is the outside case, and reported
        #: as one.
        at = page.evaluate(
            """() => {
                 const r = document.querySelector('.results-floating-nav__panel')
                   .getBoundingClientRect()
                 return {x: r.left + 4, y: r.top + 4}
               }""")
        page.mouse.click(at["x"], at["y"])
        page.wait_for_timeout(250)
        after_inside = page.evaluate(GEOMETRY)

        page.click(ID_LESS_CONTROL)
        page.wait_for_timeout(250)
        after_outside = page.evaluate(GEOMETRY)
        focus = page.evaluate(FOCUS_NOW)
    finally:
        context.close()

    assert docked_before["panelVisible"] and docked_before["dataOpen"] is None, docked_before
    assert docked_after["panelVisible"], (
        "a click on the page closed the DOCKED panel. Docked, the panel is not open: it "
        "IS the nav, and there is no handle left to bring it back"
    )
    assert docked_after["dataOpen"] is None, (
        f"the dismissal stamped data-open={docked_after['dataOpen']!r} on a docked nav "
        f"nobody had touched. Its absence is what lets the stylesheet decide, and the "
        f"docked default is open -- so this collapses the nav the moment the window shrinks"
    )

    assert opened["panelVisible"], "the handle did not open the panel, so there is nothing to dismiss"
    assert after_inside["panelVisible"], (
        f"a click on the panel's own padding closed it: {after_inside['dataOpen']}. The "
        f"listener's `closest('.results-floating-nav')` guard is what keeps the nav's own "
        f"clicks out of the dismissal, and without it the handle closes what it just opened"
    )
    assert not after_outside["panelVisible"], after_outside
    assert after_outside["dataOpen"] == "false", after_outside["dataOpen"]
    assert after_outside["ariaExpanded"] == "false", after_outside["ariaExpanded"]
    assert focus == {"id": "", "tag": "SUMMARY"}, (
        f"after the click that closed the nav, focus is on {focus}. It belongs on the "
        f"control the reader clicked. This control has no `id`, so a `setState` here sends "
        f"focus to `<main>` -- the dismissal writes the DOM instead, for exactly that reason"
    )


def test_escape_closes_the_overlay_and_hands_focus_back_to_the_handle(browser):
    """**The keyboard's way out, which did not exist** (`grep Escape web/js/*.js`
    found `drawer.js` and `period.js` and nothing for this nav).

    A pointer user has click-away. A keyboard user had only the handle -- and
    `:focus-within` means they can be *inside* the list with the handle behind
    them, so "press the handle again" is not a way out so much as a way back.

    **Both open states are exercised, because they are different states.** Pressing
    the handle writes `data-open="true"`; tabbing to it writes nothing at all and
    the panel is open purely on `:focus-within`, which is the only reason a
    `visibility: hidden` list is reachable from a keyboard. Escape has to close
    both, and in the second case it is what *first* puts `data-open` on the
    element.

    **Focus goes to the handle, and the panel stays shut with it there.** That is
    the `:not([data-open="false"])` guard on the `:focus-within` rule doing its
    job: without it, the focus that performs the close reopens the panel, which is
    the defect a mouse cannot reproduce. `closeResultsNav` writes the attribute
    before it calls `focus()` for that reason.

    Mutations: `closeResultsNav()` without the `focusHandle` argument leaves focus
    on the link and fails the second assertion pair; removing the `:not([data-
    open="false"])` guard from the stylesheet leaves `panelVisible` true in both
    phases.
    """
    context, page = _open(browser, 1280)
    try:
        _to_results(page)

        page.click(".results-floating-nav__handle")
        page.wait_for_timeout(250)
        pressed_open = page.evaluate(GEOMETRY)
        page.keyboard.press("Escape")
        page.wait_for_timeout(250)
        pressed_closed = page.evaluate(GEOMETRY)
        pressed_focus = page.evaluate(FOCUS_NOW)

        #: Phase two: open on `:focus-within` alone, with focus down in the list
        #: rather than on the handle, and `data-open` back to absent.
        page.evaluate("""() => {
          const nav = document.querySelector('.results-floating-nav')
          nav.removeAttribute('data-open')
          nav.querySelector('.results-floating-nav__handle').removeAttribute('aria-expanded')
        }""")
        page.wait_for_timeout(250)
        page.focus(".results-floating-nav__handle")
        page.wait_for_timeout(250)
        #: The links are `visibility: hidden` until the panel is open, and a hidden
        #: link is not in the tab order -- so this Tab has to come after the panel
        #: has actually opened, or it steps straight out of the nav and takes
        #: `:focus-within` with it.
        assert page.evaluate(GEOMETRY)["panelVisible"], "focusing the handle did not reopen the panel"
        page.keyboard.press("Tab")
        page.wait_for_timeout(250)
        in_the_list = page.evaluate(GEOMETRY)
        reached = page.evaluate("() => document.activeElement.getAttribute('href')")
        page.keyboard.press("Escape")
        page.wait_for_timeout(250)
        list_closed = page.evaluate(GEOMETRY)
        list_focus = page.evaluate(FOCUS_NOW)
    finally:
        context.close()

    assert pressed_open["panelVisible"], "the handle did not open the panel"
    assert not pressed_closed["panelVisible"], (
        f"Escape left the panel open: {pressed_closed}. A pointer user can click away; "
        f"this is the keyboard's equivalent and the nav had none"
    )
    assert pressed_closed["dataOpen"] == "false", pressed_closed["dataOpen"]
    assert pressed_closed["ariaExpanded"] == "false", pressed_closed["ariaExpanded"]
    assert pressed_focus["id"] == "results-floating-nav-handle", (
        f"after Escape focus is on {pressed_focus}. It belongs on the handle, which is "
        f"where the handle's own close path puts it and the reason that button has an id"
    )

    assert in_the_list["panelVisible"] and in_the_list["dataOpen"] is None, in_the_list
    assert reached == "#impact-summary", f"Tab from the handle reached {reached}"
    assert not list_closed["panelVisible"], (
        f"Escape from inside the list left the panel open: {list_closed}. Focus is on the "
        f"handle now, so `:focus-within` still matches -- the `:not([data-open=\"false\"])` "
        f"guard is what has to hold here, and `data-open` is written before the focus moves"
    )
    assert list_closed["dataOpen"] == "false", list_closed["dataOpen"]
    assert list_focus["id"] == "results-floating-nav-handle", list_focus


def test_the_navs_two_actions_download_and_start_over_for_real(browser):
    """**#126's criteria 2 and 3, asserted as the issue words them.**

    *"Download produces the expected results file/output"* and *"Start a new
    calculator clearly begins a fresh calculation"* are not satisfied by a link
    that scrolls to a button, which is what these two entries were. They are
    buttons that press the action row's own buttons, so what is asserted here is
    that pressing them in the panel does what pressing them in the row does: a
    file arrives, and the confirm-then-reset path runs. **That is the whole point
    of measuring it in a browser**: the forwarding is a `querySelector` and a
    synthesised `.click()`, and the only way to know the event reaches
    `calculator.js`'s delegated listener on `main` is to watch the action happen.

    The `window.confirm` is accepted rather than suppressed, and its message is
    read, because "clearly begins a fresh calculation" includes being asked
    first -- the action is destructive and the dialog is the fourth criterion
    ("does not accidentally overwrite the current results") being met.

    **The panel surviving its own download is asserted, and it is the one thing
    here that was found rather than written.** `downloadResults` appends an
    `<a download>` to `document.body` and calls `.click()` on it; that event
    bubbles to the document listener with a target outside the nav, so the first
    run of this test closed the panel on the download and could not reach the
    second button at all ("element is not visible", 30s). The listener now takes
    `event.isTrusted` as well -- a dismissal is a reader's gesture, and any
    programmatic `.click()` on the page would otherwise perform one.

    Mutations: changing either button's `data-nav-action` to anything else leaves
    the forwarding `querySelector` with nothing to find -- it is `?.click()`, so it
    fails silently -- and this test reports it, the download timing out or no dialog
    being raised and `[data-action="start"]` never appearing. Rendering the two as
    `<a href="#...">` again fails for the same reason, which is the point: the
    anchors could not have passed this test. Dropping `event.isTrusted` fails the
    panel-survives-its-own-download assertion.
    """
    context, page = _open(browser, 1280)
    asked = []
    try:
        _to_results(page)
        page.click(".results-floating-nav__handle")
        page.wait_for_timeout(250)
        assert page.evaluate(GEOMETRY)["panelVisible"], "the panel did not open"

        with page.expect_download(timeout=8000) as download:
            page.click('.results-floating-nav__actions [data-nav-action="download-results"]')
        filename = download.value.suggested_filename
        body = download.value.path().read_text(encoding="utf-8")

        #: Pressing an action inside the panel is a click inside the nav, so the
        #: dismissal leaves it alone and the second action is still reachable.
        #: Asserted here rather than after the `finally`, so that the mutation which
        #: breaks it reports this sentence instead of a 30-second timeout waiting
        #: for the second button to become visible. The wait is not optional:
        #: `transition: visibility 140ms` keeps the computed `visibility` at
        #: `visible` for the whole of the closing transition, so a reading taken
        #: straight after the click says "open" about a panel on its way out.
        page.wait_for_timeout(250)
        assert page.evaluate(GEOMETRY)["panelVisible"], (
            "the panel closed on its own download, and its second action is now "
            "unreachable. `downloadResults` appends a synthesised `<a download>` to "
            "`document.body` and clicks it; that event bubbles to the document listener "
            "with a target outside the nav, so `event.isTrusted` is what keeps the "
            "dismissal to gestures the reader actually made"
        )

        page.once("dialog", lambda dialog: (asked.append(dialog.message), dialog.accept()))
        page.click('.results-floating-nav__actions [data-nav-action="start-over"]')
        page.wait_for_selector('[data-action="start"]', timeout=15000)
        after = page.evaluate(
            "() => ({intro: !!document.querySelector('[data-action=\\\"start\\\"]'),"
            "        results: !!document.querySelector('#results-title'),"
            "        nav: !!document.querySelector('.results-floating-nav')})")
    finally:
        context.close()

    assert filename.startswith("food-waste-impact-results-") and filename.endswith(".txt"), filename
    assert "Food Waste Impact" in body or "Impact" in body, body[:200]

    assert asked and "Clear all calculator data" in asked[0], (
        f"pressing *Start a new calculation* in the nav raised {asked!r}. A destructive "
        f"action has to ask, and #126's fourth criterion is that starting a new "
        f"calculation does not accidentally overwrite the current results"
    )
    assert after["intro"] and not after["results"] and not after["nav"], (
        f"after the confirm the page is {after}: *Start a new calculation* has to actually "
        f"start one, which is #126's third criterion and is not something an anchor could do"
    )


@pytest.mark.parametrize("width", [w for w in WIDTHS if w >= DOCKED_FROM])
def test_the_docked_nav_is_simply_there_and_has_no_handle_to_press(browser, width):
    """**Docked, the panel is not "open": it IS the nav.**

    The list is permanent in this regime, so a control that expands and collapses
    it has nothing to do - and it was drawn anyway, 52px of orange disc above a
    list it could not affect, reachable by Tab, carrying an `aria-expanded` about
    a panel whose state it no longer decided.

    **`display: none` and not a class name is what is measured**, in two ways that
    fail differently: the computed `display`, and whether `focus()` can put the
    document's focus on the button. The second is the one about the tab order, and
    it is the one `visibility: hidden` or a zero-size box would not satisfy - both
    of those leave a button that a *reader* cannot see and a keyboard visitor still
    has to Tab through.

    The comparison at 1280 in the same test is what stops this passing against a
    nav with no handle anywhere: it asserts the handle is focusable where it is
    supposed to exist.

    Mutations, both measured: deleting `.results-floating-nav__handle
    { display: none }` from the `min-width: 1600px` block fails both docked widths
    on a handle that still takes focus. Replacing it with `opacity: 0;
    pointer-events: none` - a handle no reader can see - fails on the same
    assertion, which is the one this test exists for: that is a 52px button still
    sitting in a keyboard visitor's path.
    """
    context, page = _open(browser, width)
    try:
        _to_results(page)
        measured = _at(page, width)
        docked_focus = page.evaluate(FOCUS_HANDLE)
        #: The control for the assertion above, on the same page: a resize is not a
        #: re-render, so the button below is the same button, in the regime that
        #: still has a use for it.
        _at(page, 1280)
        narrow_focus = page.evaluate(FOCUS_HANDLE)
    finally:
        context.close()

    assert measured["panelVisible"], "the docked list is not on screen, so there is nothing to be permanent"
    #: The tab order first, because it is the assertion with the most ways to be
    #: wrong: `opacity: 0` hides the handle from a reader and leaves it here.
    assert docked_focus["focused"] is False, (
        f"at {width}px `focus()` still lands on the handle, so it is in the tab order: a "
        f"keyboard visitor Tabs onto a control that is not on screen and cannot change "
        f"anything. `opacity: 0` and a clipped box both leave it here; `display: none` "
        f"is what removes it"
    )
    assert measured["handle"]["width"] == 0 and measured["handle"]["height"] == 0, (
        f"at {width}px the handle still occupies {measured['handle']['width']:.0f}x"
        f"{measured['handle']['height']:.0f}px of the gutter"
    )
    assert measured["handleDisplay"] == "none", (
        f"at {width}px the handle is still drawn (`display: {measured['handleDisplay']}`). The "
        f"list is permanent here, so the handle is a control with nothing to expand"
    )
    assert narrow_focus["focused"] is True, (
        "the handle cannot be focused at 1280px either, so the assertion above is about a "
        "handle that does not exist anywhere rather than about the docked regime"
    )


def test_a_nav_the_visitor_closed_while_narrow_still_docks_open_when_the_window_grows(browser):
    """**The other half of removing the handle**, and the reason the docked panel
    carries no `:not([data-open="false"])` guard any more.

    `data-open="false"` can only be written by a press, a press can only happen in
    the narrow regime, and the attribute survives a resize into the docked one -
    nothing re-renders on a resize. With the guard in place that visitor arrived in
    the docked regime with no list and no handle to bring one back: a nav that is
    not there at all.

    Mutation: putting `.results-floating-nav:not([data-open="false"])` back in front
    of the docked panel rule fails here with `panelVisible` false at 1920 - and
    every other test in this file stays green, because none of them close the panel
    before they measure the docked one.
    """
    context, page = _open(browser, 1280)
    try:
        _to_results(page)
        page.click(".results-floating-nav__handle")
        page.wait_for_timeout(250)
        page.click(".results-floating-nav__handle")
        page.wait_for_timeout(250)
        closed = page.evaluate(GEOMETRY)
        docked = _at(page, 1920)
    finally:
        context.close()

    assert closed["dataOpen"] == "false", (
        f"the two presses did not leave the nav explicitly closed ({closed['dataOpen']}), so "
        f"the resize below is not carrying the state this test is about"
    )
    assert docked["dataOpen"] == "false", (
        "the resize cleared `data-open`, so nothing here proves the docked rules ignore it"
    )
    assert docked["panelVisible"], (
        "a visitor who closed the overlay at 1280 and widened their window to 1920 was docked "
        "into a nav with neither a list nor a handle to bring one back"
    )
    assert docked["handleDisplay"] == "none", docked["handleDisplay"]


def test_opening_the_undocked_nav_is_the_one_way_the_panel_ends_up_over_the_text(browser):
    """The overlay regime, stated as itself so that it is a decision and not a bug.

    Below the docking breakpoint there is nowhere for a 252px panel to go, so a
    visitor who presses the handle is shown it over the page. That is allowed
    *because they pressed it*; what is not allowed is the page arriving that way,
    which the first test in this file measures.
    """
    context, page = _open(browser, 1280)
    try:
        _to_results(page)
        page.click(".results-floating-nav__handle")
        page.wait_for_timeout(300)
        measured = page.evaluate(GEOMETRY)
    finally:
        context.close()

    assert measured["panelVisible"]
    assert _overlap(measured["panel"], measured["column"]) > 0, (
        "the undocked panel opened somewhere other than over the column. That may be "
        "an improvement, but it is not what this regime was designed to do, and the "
        "gutter it would need does not exist at 1280px"
    )


def test_the_link_for_the_section_in_view_is_marked_as_the_reader_scrolls(browser):
    """**Wiki-style: the list says where the reader is.**

    `aria-current="location"` is the value for a nav item marking a position
    *within* the current page. `"page"` is a different claim - it is what the site
    drawer's own marker means - and a screen reader announces the two the same
    way, so the value is asserted and not just its presence.

    Mutation: dropping the `markCurrentSection` call from the observer's callback
    leaves the mark on `#impact-summary` for the whole scroll and every section
    below the first fails here.
    """
    context, page = _open(browser, 1600)
    try:
        _to_results(page)
        first = page.evaluate(MARK)
        assert first["href"] == "#impact-summary", (
            f"the nav arrives with no section marked ({first}). At the top of the page "
            f"the reader is at the start of it, and an unmarked nav reads as a broken one"
        )
        seen = {}
        for target in ("tangible-equivalents", "breakdown-section", "improvement-section", "impact-summary"):
            page.evaluate(SCROLL_TO, target)
            page.wait_for_timeout(400)
            seen[target] = page.evaluate(MARK)
    finally:
        context.close()

    for target, mark in seen.items():
        assert mark["href"] == f"#{target}", (
            f"scrolled to #{target}, and the nav marks {mark['href']}: the highlight is "
            f"not following the reader"
        )
        assert mark["count"] == 1, f"{mark['count']} links claim the reader's position at once: {mark}"
        assert mark["values"] == ["location"], (
            f"the mark on #{target} is aria-current={mark['values']}. `location` is the "
            f"value for a position within a page; `page` claims this link IS the current page"
        )
        #: Pea ground, Kale text - the brand's pairing for a light ground, and the
        #: reason the panel's own text is Kale rather than the white it used to be.
        #: What the mark *looks* like is
        #: `test_the_current_item_is_lifted_and_the_others_are_pushed_back` below,
        #: with the ratios. All this one needs is that the marked link is drawn
        #: differently from an unmarked one at all.
        assert mark["colour"] == "rgb(0, 50, 35)", f"the current item's text is {mark['colour']}, not Kale"


def _band_cases(page):
    """Which section *shares* the reading band with its successor, and which *fills* it.

    **Asked of the page, because that is what the answer is a property of.** This
    replaces two pixel constants - `("tangible-equivalents", 287)` and
    `("impact-summary", 762)` - that named the heights the two cases happened to
    have on the day they were written. The second one expired: a sixth metric row
    (`land`) landed in the published set and *Impact summary* grew to 953px, so a
    test about which link gets marked failed about geometry. A height is a
    measurement with a date on it; which of the two tie-break cases a section is
    is a relationship between two rectangles, and that is what is read here.

    Every section is put at its own *click* rest position, because that is the
    position the tie is decided at, and classified by what is then in the band:

    * **shares** - the next section's top has crept into the band, so two sections
      are inside it and the tie-break is what picks between them. This is the
      defect's own case.
    * **fills** - the section covers the band's bottom edge on its own, and its
      successor is clear of it, so no tie-break is involved at all. This is why
      three of the four links always appeared to work.

    The two are mutually exclusive by construction: a section whose bottom reaches
    `BAND_BOTTOM` puts its successor at least one inter-section gap past it.
    Returned in page order, so a caller taking `[0]` gets the same case run after
    run rather than whichever the dict happened to yield.
    """
    boxes_now = page.evaluate(SECTION_BOXES)
    #: Every target the nav offers has to be on the page, said here rather than left
    #: to `SCROLL_TO_REST` throwing a `TypeError` about `null` four frames away.
    missing = [entry["id"] for entry in boxes_now if entry["top"] is None]
    assert not missing, (
        f"the floating nav links to {missing}, which is not on the rendered results page. "
        f"Every entry in `RESULTS_NAV_SECTIONS` is both a link and an observed element, so "
        f"a target that is not there is a link that goes nowhere and a spy watching nothing"
    )
    assert len(boxes_now) >= 4, (
        f"the nav offers {len(boxes_now)} section links, and every measurement in this "
        f"file was taken against the four it is documented to index: {boxes_now}"
    )
    order = [entry["id"] for entry in boxes_now]
    shares, fills = [], []
    for index, section in enumerate(order):
        page.evaluate(SCROLL_TO_REST, section)
        page.wait_for_timeout(250)
        boxes = {entry["id"]: entry for entry in page.evaluate(SECTION_BOXES)}
        successor = order[index + 1] if index + 1 < len(order) else None
        successor_top = boxes[successor]["top"] if successor else None
        if successor is not None and BAND_TOP <= successor_top <= BAND_BOTTOM:
            shares.append((section, boxes))
        elif boxes[section]["bottom"] >= BAND_BOTTOM:
            fills.append((section, boxes))
    return {"shares_the_band": shares, "fills_the_band": fills}


@pytest.mark.parametrize("case", ["shares_the_band", "fills_the_band"])
def test_a_press_on_a_link_marks_that_link_and_not_the_one_after_it(browser, case):
    """**The reported defect: press the second item, and the third lights up.**

    Measured on the running stack at 1600x900 before the fix, with the old rule in
    force. `scroll-margin-top: 24px` and no sticky element at the top of this page,
    so a press leaves its target's border box at exactly `top: 24` - all four were
    measured doing it (scrollY 427 / 1225 / 1548 / 1986). *Tangible equivalents* is
    287px tall, so it came to rest at 24-311; the 36px gap after it put *Breakdown
    by category* at 347, and the reading band ends at 360. Two sections in the band,
    the rule took the last, and the mark landed one link past the press.

    **A short section and a tall one**, because the two are not the same case: a
    section that fills the band on its own leaves no tie to break, which is why
    three of the four links always appeared to work. **That reasoning is unchanged;
    only the way the two are chosen is.** The pair used to be named as heights, and
    the tall one's height expired the moment the published set priced a sixth metric
    - see `_band_cases`, which selects them by measuring which section shares the
    band and which covers it.

    The rest position is asserted as well as the mark. If a jump stops landing at
    24 - a sticky header added above `<main>`, or a `scroll-margin-top` changed
    without this file - the geometry every number here was derived from has moved,
    and this says so rather than failing somewhere less legible later.

    Mutation: taking the LAST section in band again (`next = id` with no `break` in
    `onSectionsCrossed`) leaves **both** cases green here, and that is correct rather
    than a weak test: the pin answers the press before the observer is consulted at
    all, which is the whole reason the request asked for two separate things. What
    fails this is the two together - the old tie-break *and* `pinSection` made a
    no-op - and it fails the sharing case with the mark on `#breakdown-section` while
    the filling one stays green. `test_the_band_marks_the_section_at_its_top_edge_not_
    the_one_creeping_in_below` is what holds the tie-break on its own, and
    `test_the_mark_is_on_the_pressed_link_before_the_page_has_finished_moving` the
    pin. This one is the defect as the owner reported it: press the second item, and
    the third lights up.
    """
    context, page = _open(browser, 1600)
    try:
        _to_results(page)
        cases = _band_cases(page)
        assert cases[case], (
            f"no section on this page {case.replace('_', ' ')} at its rest position, so "
            f"this case is not on the page any more and a green result would mean nothing. "
            f"Measured at rest: {{'shares': {[s for s, _ in cases['shares_the_band']]}, "
            f"'fills': {[s for s, _ in cases['fills_the_band']]}}}"
        )
        section, at_rest = cases[case][0]
        page.evaluate("() => window.scrollTo({top: 0, behavior: 'instant'})")
        page.wait_for_timeout(200)
        page.click(f'.results-floating-nav__links a[href="#{section}"]')
        page.wait_for_timeout(450)
        mark = page.evaluate(MARK)
        boxes = page.evaluate(SECTION_BOXES)
    finally:
        context.close()

    order = [entry["id"] for entry in boxes]
    by_id = {entry["id"]: entry for entry in boxes}
    box = by_id[section]
    assert box["top"] == 24, (
        f"a press on #{section} left it at top {box['top']}, not the 24px "
        f"`scroll-margin-top` every measurement in this test was taken against"
    )
    #: The case is restated here, against the page the press was actually measured
    #: on rather than against the selection pass, and as the relationship it is
    #: rather than as the height it happens to be. A page that stopped offering
    #: this case would otherwise be reported as a marking defect.
    index = order.index(section)
    successor = order[index + 1] if index + 1 < len(order) else None
    successor_top = by_id[successor]["top"] if successor else None
    geometry = [(entry["id"], entry["top"], entry["bottom"]) for entry in boxes]
    if case == "shares_the_band":
        assert successor is not None and BAND_TOP <= successor_top <= BAND_BOTTOM, (
            f"#{section} was selected as the section that SHARES the {BAND_TOP}-{BAND_BOTTOM} "
            f"band, and after the press its successor {successor} sits at {successor_top}: "
            f"nothing is in the band with it, so there is no tie for the rule to get wrong "
            f"and this case has stopped covering the defect. Sections: {geometry}"
        )
    else:
        assert box["bottom"] >= BAND_BOTTOM, (
            f"#{section} was selected as the section that FILLS the {BAND_TOP}-{BAND_BOTTOM} "
            f"band and reaches only {box['bottom']}. Sections: {geometry}"
        )
        assert successor is None or successor_top > BAND_BOTTOM, (
            f"#{section} was selected as the section that FILLS the band alone, and "
            f"{successor} is inside it at {successor_top}. Sections: {geometry}"
        )
    assert mark["count"] == 1, f"{mark['count']} links claim the reader's position: {mark}"
    assert mark["href"] == f"#{section}", (
        f"pressed #{section} and the nav marks {mark['href']}. Sections: {geometry}"
    )
    assert at_rest[section]["height"] == box["height"], (
        f"#{section} measured {at_rest[section]['height']}px during the selection pass and "
        f"{box['height']}px after the press: the page is not stable between the two, so the "
        f"case selected is not the case asserted"
    )


def test_the_mark_is_on_the_pressed_link_before_the_page_has_finished_moving(browser):
    """**A press is an instruction, and the answer to it is not an inference.**

    This is the one test that leaves `html { scroll-behavior: smooth }` switched on,
    because what it measures only exists while the page is travelling. From the top
    of the page, a press on the last link starts several hundred milliseconds of
    scrolling - measured: scrollY 0, 45, 285, 1212, 1816 at 0, 60, 120, 200 and
    400ms - during which the observer's honest answer is wherever the page happens
    to be, which is still *Impact summary*.

    So the pin is not decoration over a tie-break that now works. It is what makes
    the press answer the press.

    Mutation: making `pinSection` a no-op (`return` on its first line) leaves the
    mark on `#impact-summary` at 0ms and 60ms here, and leaves every other test in
    this file green - the tie-break alone gets the *settled* answer right.
    """
    context, page = _open(browser, 1600, force_auto_scroll=False)
    try:
        _to_results(page)
        page.evaluate("() => window.scrollTo(0, 0)")
        page.wait_for_timeout(400)
        before = page.evaluate(MARK)
        page.click('.results-floating-nav__links a[href="#improvement-section"]')
        at_once = page.evaluate(MARK)
        travelled_at_once = page.evaluate("() => Math.round(window.scrollY)")
        page.wait_for_timeout(60)
        early = page.evaluate(MARK)
        travelled_early = page.evaluate("() => Math.round(window.scrollY)")
        page.wait_for_timeout(600)
        settled = page.evaluate(MARK)
    finally:
        context.close()

    assert before["href"] == "#impact-summary", (
        f"the page did not start at the top ({before['href']}), so the press below is not "
        f"the long travel this test is about"
    )
    assert travelled_early < 900, (
        f"the page had already moved {travelled_early}px 60ms after the press, so the smooth "
        f"scroll is not running and this test measures nothing. `FORCE_AUTO` leaking in?"
    )
    assert at_once["href"] == "#improvement-section", (
        f"the instant the link was pressed the nav marked {at_once['href']} with the page "
        f"still at scrollY {travelled_at_once}: the mark is waiting for the page to arrive "
        f"instead of answering the press"
    )
    assert early["href"] == "#improvement-section", (
        f"60ms after the press, with the page at scrollY {travelled_early} of ~1986, the nav "
        f"marks {early['href']}"
    )
    assert settled["href"] == "#improvement-section", settled


def test_the_readers_own_scroll_takes_the_pin_back_off(browser):
    """**Pinned until the reader scrolls of their own accord - and then not.**

    A pin that outlived the press would be worse than the defect it fixes: the mark
    would sit on whatever was last pressed for the rest of the session. `wheel` is
    a real gesture and is dispatched by `page.mouse.wheel`, which is why it is used
    here rather than `window.scrollTo` - a programmatic scroll is exactly what the
    pin is meant to ignore while the browser performs the jump.

    Measured: pinned to *Impact summary* at the top, then 1300px of wheel leaves
    *Breakdown by category* at -155 to 247, which is the section across the band's
    162px top edge, and the mark follows.

    Mutation: dropping the `wheel` listener alone leaves this **green**, and the
    reason is measured rather than guessed - a wheel produces `scroll` events too,
    the pinned section had already arrived at its rest position, and the `scroll`
    path released the pin on the same gesture. The mutation was incomplete, not the
    test weak. Removing all four release paths (`wheel`, `touchmove`, the scrolling
    keys and the settled-`scroll` branch) fails here with the mark still on
    `#impact-summary` after 1300px of scrolling. `bindNavGestures` records what the
    gesture listeners cover that the scroll path does not, and why it could not be
    reproduced through Playwright.
    """
    context, page = _open(browser, 1600)
    try:
        _to_results(page)
        page.click('.results-floating-nav__links a[href="#impact-summary"]')
        page.wait_for_timeout(400)
        pinned = page.evaluate(MARK)
        page.mouse.move(700, 500)
        page.mouse.wheel(0, 1300)
        page.wait_for_timeout(500)
        released = page.evaluate(MARK)
        boxes = page.evaluate(SECTION_BOXES)
    finally:
        context.close()

    assert pinned["href"] == "#impact-summary", pinned
    assert released["href"] != "#impact-summary", (
        "the mark is still on the section that was pressed after the reader scrolled 1300px "
        "away from it: the pin never comes off, so the nav now reports the last button "
        "pressed rather than where the reader is"
    )
    in_band = [b["id"] for b in boxes if b["top"] <= BAND_TOP <= b["bottom"]]
    assert released["href"] == f"#{in_band[0]}", (
        f"after the scroll the nav marks {released['href']}, and the section across the band's "
        f"top edge is #{in_band[0]}. Sections: {[(b['id'], b['top'], b['bottom']) for b in boxes]}"
    )


def test_the_band_marks_the_section_at_its_top_edge_not_the_one_creeping_in_below(browser):
    """**The tie-break itself, with no press involved, so the pin cannot mask it.**

    Two sections are inside the reading band whenever a boundary falls in it, and
    which of them is "where the reader is" is the whole question. The rule was the
    *last* in page order - described as "the one the reader has most recently come
    to", which is the right description of a section whose top has just appeared at
    the bottom of the band and the wrong one for the reader, who is still looking at
    the section above it.

    The page is put at each section's *click* rest position by `window.scrollTo`,
    because that is the position where the tie actually occurs: at 1600x900 the band
    is 162-360, and *Tangible equivalents* resting at 24-311 leaves 13px of
    *Breakdown by category* (347-749) inside it. `SCROLL_TO`'s own -40px offset,
    which the older test above uses, puts *Breakdown* at 363 - three pixels clear of
    the band - which is exactly why that test was green throughout the defect.

    Mutation: `next = id` with no `break` (the last-in-band rule) fails on
    `tangible-equivalents` with the mark on `#breakdown-section`, and passes on the
    other three, whose successors are out of the band at their rest positions.
    """
    context, page = _open(browser, 1600)
    try:
        _to_results(page)
        seen = {}
        for target in ("tangible-equivalents", "breakdown-section", "improvement-section", "impact-summary"):
            page.evaluate(SCROLL_TO_REST, target)
            page.wait_for_timeout(400)
            seen[target] = (page.evaluate(MARK), page.evaluate(SECTION_BOXES))
    finally:
        context.close()

    for target, (mark, boxes) in seen.items():
        below = [b for b in boxes if BAND_TOP <= b["top"] <= BAND_BOTTOM]
        assert mark["href"] == f"#{target}", (
            f"with #{target} resting at the top of the reading band the nav marks "
            f"{mark['href']}. Sections: {[(b['id'], b['top'], b['bottom']) for b in boxes]}"
            + (f"; #{below[0]['id']} has crept {BAND_BOTTOM - below[0]['top']}px into the bottom of "
               f"the band and taken the mark with it" if below else "")
        )
    #: And the tie is really there to be got wrong. Without this the test could pass
    #: against a page whose sections never share the band at all.
    _, tangible_boxes = seen["tangible-equivalents"]
    successor = next(b for b in tangible_boxes if b["id"] == "breakdown-section")
    assert BAND_TOP <= successor["top"] <= BAND_BOTTOM, (
        f"#breakdown-section is at {successor['top']}, outside the {BAND_TOP}-{BAND_BOTTOM} band, so nothing "
        f"here is a tie and the assertion above would hold under either rule"
    )


def test_the_current_item_is_lifted_and_the_others_are_pushed_back(browser):
    """**An elevation and a colour, and the colour cannot be the accent.**

    The current item was a filled Pea block. The obvious replacement - the item's
    own text in Pea - is unavailable, and the numbers are the reason. Measured
    against the panel's frosted ground:

        Pea    #28c882    2.04:1 on #f2faf6, 2.17:1 on white
        Kale   #003223   13.36:1 on #f2faf6, 14.18:1 on white
        muted  #465f56    6.52:1 on #f2faf6, 6.93:1 on white

    Body text needs 4.5:1. So the mark is carried by Kale at full weight against a
    list pushed back to `--muted` at regular weight, lifted by a Pea-tinted shadow,
    with the accent itself on a 3px inline-start rule in `--green-rule` #147d52 -
    Pea composited 50% over Kale, 4.84:1 on #f2faf6, past the 3:1 WCAG 1.4.11 asks
    of a non-text state indicator.

    **Both grounds are asserted.** The panel is `rgba(255, 255, 255, 0.72)` over the
    body, and the pixel actually painted behind these links was measured at
    `#ffffff`; #f2faf6 is the darker ground the request quoted, and a ratio is
    asserted against both so that neither a lighter nor a slightly tinted backdrop
    can carry this below the bar.

    Mutation: `color: var(--kai-pea)` on the current item fails on the ratio, at
    2.04:1 against a 4.5 floor, with the measured number in the message. Dropping the
    `box-shadow` fails on the elevation; dropping `--muted` back to Kale fails on the
    two items being drawn identically apart from weight.
    """
    context, page = _open(browser, 1600)
    try:
        _to_results(page)
        drawn = page.evaluate(
            """() => {
              const links = [...document.querySelectorAll('.results-floating-nav__links a')];
              const read = el => { const s = getComputedStyle(el); return {
                colour: s.color, background: s.backgroundColor, weight: s.fontWeight,
                shadow: s.boxShadow, rule: s.borderInlineStartColor,
                ruleWidth: s.borderInlineStartWidth}; };
              return {
                current: read(links.find(a => a.getAttribute('aria-current'))),
                other: read(links.find(a => !a.getAttribute('aria-current'))),
                panel: getComputedStyle(document.querySelector('.results-floating-nav__panel')).backgroundColor,
              };
            }""")
    finally:
        context.close()

    current, other = drawn["current"], drawn["other"]
    for ground in ("#ffffff", "#f2faf6"):
        assert _contrast(current["colour"], ground) >= 4.5, (
            f"the current item's text is {current['colour']}, which measures "
            f"{_contrast(current['colour'], ground):.2f}:1 on {ground} - under the 4.5:1 body "
            f"text needs. Pea is 2.04:1 here, which is why the accent is not on the glyphs"
        )
        assert _contrast(other["colour"], ground) >= 4.5, (
            f"the items pushed back are {other['colour']}, {_contrast(other['colour'], ground):.2f}:1 "
            f"on {ground}. 'Pushed back' is a recession in emphasis, not in legibility"
        )
        assert _contrast(current["rule"], ground) >= 3.0, (
            f"the accent rule is {current['rule']}, {_contrast(current['rule'], ground):.2f}:1 on "
            f"{ground} - under the 3:1 WCAG 1.4.11 asks of a non-text state indicator. Pea "
            f"itself is 2.04:1, which is the whole reason --green-rule exists"
        )

    #: The current item has to be MORE readable than the list it stands out from,
    #: which is the assertion a readable accent green would still have failed:
    #: --green-rule is 4.84:1 and --muted is 6.52:1, so accent glyphs would have
    #: made the reader's own section the faintest line in the panel.
    assert _contrast(current["colour"], "#f2faf6") > _contrast(other["colour"], "#f2faf6"), (
        f"the current item ({_contrast(current['colour'], '#f2faf6'):.2f}:1) is no more readable "
        f"than the items it is meant to stand out from ({_contrast(other['colour'], '#f2faf6'):.2f}:1)"
    )
    assert current["weight"] == "700" and other["weight"] == "400", (
        f"weights are current={current['weight']} other={other['weight']}: the mark is meant to "
        f"be carried by weight as well as colour, and 400/700 are the only two faces this "
        f"stylesheet has files for"
    )
    #: The elevation, and the accent inside it. A shadow carries no contrast
    #: requirement because it carries no information the rule and the weight do not,
    #: but it is what makes this read as lifted rather than as a repaint.
    assert "40, 200, 130" in current["shadow"], (
        f"the current item's shadow is {current['shadow']}: the accent was supposed to be "
        f"carried in it"
    )
    assert other["shadow"] == "none", f"an unmarked item is lifted too ({other['shadow']})"
    assert current["ruleWidth"] == other["ruleWidth"], (
        f"the rule is {current['ruleWidth']} on the current item and {other['ruleWidth']} on the "
        f"others, so the mark moving down the list shifts every label sideways"
    )
    assert other["rule"] in ("rgba(0, 0, 0, 0)", "transparent"), (
        f"the unmarked items carry a visible rule ({other['rule']}), so the accent no longer "
        f"distinguishes anything"
    )
    #: And it is not a painted chip in the accent any more, which is the thing the
    #: request asked to be rid of.
    assert current["background"] != "rgb(40, 200, 130)", (
        "the current item is still a filled Pea block"
    )


def test_the_spy_survives_the_page_being_rebuilt_and_never_makes_a_second_observer(browser):
    """**The trap: `render()` replaces `main.innerHTML` on every `setState`.**

    Both ways of getting this wrong leave a page that looks right in a screenshot.
    Wire the observer once and it ends up holding four detached `<section>`
    elements, reporting nothing for the rest of the session. Wire it per render
    and there is a live observer per keystroke, each holding its own detached
    four. So the two halves are measured separately: the mark still follows a
    scroll *after* a dozen re-renders, and exactly one observer was ever
    constructed to make that true.

    A breakdown tab is the cheapest re-render on this page - `setState` and no
    request - and it is the same `main.innerHTML` replacement a keystroke in the
    improvement panel performs.

    Mutation: `new IntersectionObserver(...)` on every bind instead of
    `disconnect()` + re-observe takes the count from 1 to 13. Hoisting the bind
    out of `render()` and calling it once leaves the count at 1 and the scroll
    after the rebuilds marks `#impact-summary` -- which is why one assertion
    could not have covered both.
    """
    context, page = _open(browser, 1600, init_script=COUNT_OBSERVERS)
    try:
        _to_results(page)
        made_at_first = page.evaluate("() => window.__observersMade")
        tabs = page.locator('[data-action="breakdown-tab"]')
        rebuilds = tabs.count()
        assert rebuilds, "the results page rendered no breakdown tabs, so nothing here re-renders"
        for index in range(12):
            tabs.nth(index % rebuilds).click()
            page.wait_for_timeout(50)
        page.evaluate(SCROLL_TO, "improvement-section")
        page.wait_for_timeout(400)
        after = page.evaluate(MARK)
        counted = page.evaluate("() => [window.__observersMade, window.__observersDisconnected]")
    finally:
        context.close()

    assert after["href"] == "#improvement-section", (
        "after twelve re-renders the highlight stopped following the reader: the "
        f"observer is watching sections that have left the document ({after})"
    )
    assert counted[0] == 1, (
        f"{counted[0]} IntersectionObservers were constructed across thirteen renders "
        f"({made_at_first} of them before the first re-render). One observer, "
        f"re-pointed, is the whole of the contract -- the rest are live observers "
        f"holding detached nodes that nothing can reach to disconnect"
    )
    assert counted[1] >= 12, (
        f"only {counted[1]} disconnects for twelve rebuilds: the observer is keeping "
        f"the elements each render threw away"
    )


def test_the_list_can_be_opened_and_closed_from_the_keyboard(browser):
    """**A closed default is only honest if the closed panel can be reached.**

    `visibility: hidden` takes the four links out of the tab order, so the
    stylesheet opens the panel on `:focus-within` as well: the handle takes focus,
    the list appears, Tab goes into it. That rule carries
    `:not([data-open="false"])` for the other half of the same problem -- the
    handle keeps focus after a press, so without the guard the visitor's own close
    would be undone by the focus that performed it, and the button would look
    broken in exactly the way a mouse cannot reproduce.

    The handle's `id` is what keeps focus on it across the re-render its press
    causes (`main.js` restores focus by id, and `document.activeElement.id` was
    `''`). The handle is reached by class here rather than by that id, so the
    mutation is reportable: removing the id sends focus to `<main>` on the press,
    and the Tab below then reaches `None` instead of the first link.
    """
    context, page = _open(browser, 1280)
    try:
        _to_results(page)
        arrived = page.evaluate(GEOMETRY)
        page.focus(".results-floating-nav__handle")
        page.wait_for_timeout(250)
        focused = page.evaluate(GEOMETRY)
        page.keyboard.press("Enter")
        page.wait_for_timeout(300)
        opened = page.evaluate(GEOMETRY)
        page.keyboard.press("Tab")
        page.wait_for_timeout(150)
        reached = page.evaluate(
            "() => document.activeElement ? document.activeElement.getAttribute('href') : null")
        page.focus(".results-floating-nav__handle")
        page.keyboard.press("Enter")
        page.wait_for_timeout(300)
        closed = page.evaluate(GEOMETRY)
        still_focused = page.evaluate("() => document.activeElement.id")
    finally:
        context.close()

    assert not arrived["panelVisible"], "the fixture is not in the closed regime this test is about"
    assert focused["panelVisible"], (
        "focusing the handle did not open the list, so its links are `visibility: hidden` "
        "and there is no way into them from a keyboard at all"
    )
    assert opened["ariaExpanded"] == "true", opened["ariaExpanded"]
    assert reached == "#impact-summary", f"Tab from the open handle reached {reached}, not the first link"
    assert not closed["panelVisible"], (
        "the handle still has focus after the press that closed the panel, and "
        "`:focus-within` reopened it: the visitor's own close is undone by the focus "
        "that performed it"
    )
    assert closed["ariaExpanded"] == "false", closed["ariaExpanded"]
    assert still_focused == "results-floating-nav-handle", (
        f"focus went to {still_focused!r} after the press. The re-render destroys the "
        f"button, and `main.js` can only put focus back on an element with an id"
    )


def test_a_visitor_who_asked_for_no_motion_gets_none_of_this_nav_s(browser):
    """`prefers-reduced-motion`, on the one element in this nav that moves.

    The panel fades and slides 8px on its way in. Both are switched off rather
    than shortened, and the slide is switched off in *both* states so that
    turning the transition off does not leave the closed panel parked 8px away
    from where the open one sits.

    Mutation: deleting the `@media (prefers-reduced-motion: reduce)` rule leaves
    `transitionDuration` at 0.14s and the closed `translate` at `8px -50%`, and
    both assertions below fail.
    """
    context = browser.new_context(
        viewport={"width": 1280, "height": 900}, locale="en-NZ", bypass_csp=True,
        reduced_motion="reduce")
    page = context.new_page()
    try:
        page.goto(f"{BASE}?lang=en", wait_until="networkidle", timeout=20000)
        page.wait_for_selector('[data-action="start"]', timeout=10000)
        _to_results(page)
        motion = page.evaluate(
            """() => {
              const panel = document.querySelector('.results-floating-nav__panel');
              const closed = getComputedStyle(panel);
              return {durations: closed.transitionDuration, closedTranslate: closed.translate};
            }""")
        page.click(".results-floating-nav__handle")
        page.wait_for_timeout(250)
        opened = page.evaluate(
            "() => getComputedStyle(document.querySelector('.results-floating-nav__panel')).translate")
    finally:
        context.close()

    assert set(motion["durations"].replace(" ", "").split(",")) == {"0s"}, (
        f"the panel still transitions over {motion['durations']} for a visitor who asked "
        f"for no motion"
    )
    assert motion["closedTranslate"] == opened, (
        f"the panel still slides {motion['closedTranslate']} -> {opened} under reduced "
        f"motion: the fade was switched off and the movement was left on"
    )


def test_the_nav_mirrors_into_arabic_from_dir_alone(browser):
    """**RTL is layout, not a `dir` attribute** (`test_i18n_web.py`'s rule, measured).

    The pair that positions this nav is `inset-inline-start: 50%` plus a logical
    margin, so the gutter it lands in is the one on the reader's *end* side: the
    right in English, the left in Arabic. The column is not symmetric about the
    centre -- `.results-page`'s over-constrained `margin-inline: -80px` resolves
    on the inline-end side, putting its far edge at `C/2 - 490` in Arabic and
    `C/2 + 490` in English -- so the same declaration has to land on both, and
    that is what is measured here rather than assumed.

    Mutation: `inset-inline-start` written as the physical `left` keeps every
    English measurement in this file green and fails here with the nav on the
    wrong side of the page, on top of the column. `test_i18n_web.py`'s stylesheet
    scan does not catch it either: it forbids `margin`/`padding`/`border-left`,
    not `left`.
    """
    context, page = _open(browser, 1600, lang="ar")
    try:
        _to_results(page)
        docked = page.evaluate(GEOMETRY)
        narrow = _at(page, 1280)
        page.click(".results-floating-nav__handle")
        page.wait_for_timeout(300)
        opened = page.evaluate(GEOMETRY)
    finally:
        context.close()

    assert docked["dir"] == "rtl", "the page did not render right-to-left, so nothing below is an RTL measurement"
    assert docked["panelVisible"], "the docked default did not survive the mirror"
    assert docked["panel"]["right"] < docked["column"]["left"], (
        f"in Arabic the docked panel is at {docked['panel']['left']:.0f}-{docked['panel']['right']:.0f} "
        f"and the column at {docked['column']['left']:.0f}-{docked['column']['right']:.0f}: it has to sit "
        f"in the gutter on the reading-end side, which is the LEFT here"
    )
    assert _overlap(docked["panel"], docked["column"]) == 0
    assert docked["panel"]["left"] >= 16, "the mirrored panel is jammed against the viewport edge"

    assert not narrow["panelVisible"], "the closed default did not survive the mirror"
    assert _overlap(narrow["handle"], narrow["column"]) == 0, (
        f"in Arabic at 1280px the handle covers the column: "
        f"{narrow['handle']['left']:.0f}-{narrow['handle']['right']:.0f} against "
        f"{narrow['column']['left']:.0f}-{narrow['column']['right']:.0f}"
    )
    assert narrow["handle"]["right"] <= narrow["column"]["left"], "the handle mirrored to the wrong side"
    assert opened["panelVisible"] and _overlap(opened["panel"], opened["column"]) > 0, (
        "the mirrored overlay opened away from the page instead of over it"
    )
