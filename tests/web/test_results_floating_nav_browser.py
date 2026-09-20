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
  };
}
"""

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


def _open(browser, width, height=900, lang="en", init_script=None):
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


@pytest.mark.parametrize("width,opens", [(1280, True), (1600, False)])
def test_the_first_press_of_the_handle_inverts_whichever_default_is_on_screen(browser, width, opens):
    """**One control, two defaults, and the first press has to know which.**

    `state.resultsNavOpen` is `undefined` until the visitor touches the handle -
    that is what lets the stylesheet decide - so the first press has nothing on
    `state` to invert. It used to assume the default was "open" and set `false`,
    which is correct docked and a dead button everywhere else: press, and the
    already-closed panel is closed again.

    Mutation: `state.resultsNavOpen === undefined ? false : ...` back in
    `calculator.js` leaves the 1280 case pressing a button that does nothing.
    """
    context, page = _open(browser, width)
    try:
        _to_results(page)
        before = page.evaluate(GEOMETRY)
        assert before["panelVisible"] is not opens, "the fixture is not in the state this test is about"
        page.click(".results-floating-nav__handle")
        page.wait_for_timeout(300)
        after = page.evaluate(GEOMETRY)
    finally:
        context.close()

    assert after["panelVisible"] is opens, (
        f"at {width}px the panel was {'closed' if not before['panelVisible'] else 'open'} and the "
        f"first press left it {'open' if after['panelVisible'] else 'closed'}: the press has to "
        f"invert what is on screen, not a default that only holds in one regime"
    )
    #: And now the markup says so, in both places, agreeing with each other.
    assert after["dataOpen"] == ("true" if opens else "false"), after["dataOpen"]
    assert after["ariaExpanded"] == ("true" if opens else "false"), after["ariaExpanded"]


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
        assert mark["background"] == "rgb(40, 200, 130)", f"the current item is {mark['background']}, not Pea"
        assert mark["colour"] == "rgb(0, 50, 35)", f"the current item's text is {mark['colour']}, not Kale"


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
