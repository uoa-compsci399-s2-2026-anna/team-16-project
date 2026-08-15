"""The navigation drawer, driven in a browser rather than read out of the markup.

**A test that asserts an element exists does not assert anyone can press it.** This
project's defect list includes a dialog whose submit button was invisible, a scrollbar
drawn 4,500px below the fold, an Arabic label collapsed into a 1px column while every
overflow assertion was green, and an 83px hole between a frame and its control. A
drawer is exactly the shape of component those failures take: the panel is in the
document whether it is open or not, so ``locator.count()`` and even ``is_visible()``
answer the wrong question — a closed ``<details>`` keeps a box, and its contents are
skipped for painting and hit-testing rather than removed.

So the assertions below are about **hit-testing and geometry**: what
``elementFromPoint`` returns at the panel's own centre, how wide the handle's pressable
box is, where the panel sits relative to the reading edge, and what a real click does
with scripting switched off.

Requires the stack: ``docker compose -f docker/compose.yaml up -d --build web``.
Skipped, never failed, when it is not up.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.browser

BASE = os.environ.get("KAICALC_WEB_URL", "http://localhost:18080").rstrip("/")

#: All four public pages. The drawer is one markup block repeated, and "repeated" is a
#: claim about four files that only four measurements can hold.
PAGES = ("/", "/index.html", "/stats.html", "/methodology.html")

#: The three that must work with scripting off. `index.html` is ES modules end to end
#: and renders nothing without JavaScript, so it is exempt from that one requirement
#: and from that one only.
STATIC_PAGES = ("/", "/stats.html", "/methodology.html")

_LANGUAGES = """
Object.defineProperty(navigator, 'languages', {{ get: () => {languages} }});
Object.defineProperty(navigator, 'language', {{ get: () => {first} }});
"""


def _stack_is_up() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE}/", timeout=3) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):  # pragma: no cover - environment guard
        return False


pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

if not _stack_is_up():  # pragma: no cover - environment guard
    pytest.skip(
        f"the stack is not answering on {BASE}; run "
        "`docker compose -f docker/compose.yaml up -d --build web`",
        allow_module_level=True,
    )

from playwright.sync_api import sync_playwright  # noqa: E402


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


def _open_page(browser, path, width=1278, height=983, language="en", **context_options):
    context = browser.new_context(
        viewport={"width": width, "height": height},
        extra_http_headers={"Accept-Language": f"{language},en"},
        **context_options,
    )
    page = context.new_page()
    if context_options.get("java_script_enabled", True):
        page.add_init_script(
            _LANGUAGES.format(
                languages=json.dumps([language, "en"]), first=json.dumps(language)
            )
        )
    page.goto(f"{BASE}{path}", wait_until="networkidle", timeout=20000)
    page.wait_for_timeout(500)
    return context, page


#: What is at the centre of the panel, by hit-test. Returns the drawer link's text when
#: the panel is really on screen and reachable, and something else when it is not.
_AT_PANEL_CENTRE = """
() => {
  const panel = document.querySelector('.site-drawer__panel');
  const box = panel.getBoundingClientRect();
  const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
  return {
    tag: hit ? hit.tagName : null,
    insideDrawer: Boolean(hit && hit.closest('#site-drawer')),
    text: hit ? (hit.textContent || '').trim().slice(0, 40) : null,
  };
}
"""


@pytest.mark.parametrize("path", PAGES)
def test_the_drawer_is_on_every_public_page_and_opens(browser, path):
    """One markup block, four pages, and the panel has to become *reachable*.

    The closed assertion is the half that would otherwise rot. A closed `<details>`
    still has a box the size of its contents, so `count()`, `bounding_box()` and even
    `is_visible()` can all answer yes about a panel nobody can touch — which is the
    exact shape of defect this repository keeps finding. `elementFromPoint` is asked
    instead, because it is the browser's own answer to "what would a click land on".
    """
    context, page = _open_page(browser, path)
    try:
        drawer = page.locator("#site-drawer")
        assert drawer.count() == 1, f"{path}: no drawer"

        closed = page.evaluate(_AT_PANEL_CENTRE)
        assert not closed["insideDrawer"], (
            f"{path}: the closed drawer is still taking clicks at the panel's centre "
            f"({closed}); it is on top of the page rather than folded away"
        )

        page.click(".site-drawer__handle")
        page.wait_for_timeout(400)
        opened = page.evaluate(_AT_PANEL_CENTRE)
        assert opened["insideDrawer"], (
            f"{path}: the open panel is not what a click at its own centre would reach "
            f"({opened})"
        )
        hrefs = page.eval_on_selector_all(
            ".site-drawer__nav a", "els => els.map(el => el.getAttribute('href'))"
        )
        assert hrefs == [
            "./home.html",
            "./index.html",
            "./stats.html",
            "./methodology.html",
        ], f"{path}: {hrefs}"
    finally:
        context.close()


@pytest.mark.parametrize("width,height", [(1278, 983), (938, 898), (390, 700), (320, 700)])
def test_opening_the_drawer_moves_nothing_on_the_page(browser, width, height):
    """**Overlay, never compress**, and it is measured rather than asserted from CSS.

    Horizontal space is the binding constraint on this interface: the owner's laptop is
    a 938px CSS viewport and one destination row has a 544px floor, so a drawer taking
    240px out of the document would break a step this team measured and fixed a week
    ago. A `position: fixed` rule can be read off the stylesheet; whether the document
    moved can only be measured, and the measurement is what a reader experiences.

    320px is included because that is where a panel wide enough to matter would push
    the page sideways first, and a horizontal scrollbar is the visible form of exactly
    this failure.
    """
    context, page = _open_page(browser, "/index.html", width=width, height=height)
    try:
        before = page.evaluate(
            """() => {
                 const main = document.querySelector('#main-content').getBoundingClientRect();
                 return {x: Math.round(main.x), w: Math.round(main.width),
                         scroll: document.documentElement.scrollWidth,
                         client: document.documentElement.clientWidth};
               }"""
        )
        page.click(".site-drawer__handle")
        page.wait_for_timeout(450)
        after = page.evaluate(
            """() => {
                 const main = document.querySelector('#main-content').getBoundingClientRect();
                 return {x: Math.round(main.x), w: Math.round(main.width),
                         scroll: document.documentElement.scrollWidth,
                         client: document.documentElement.clientWidth};
               }"""
        )
        assert after == before, (
            f"{width}x{height}: opening the drawer moved the page. before={before} "
            f"after={after}. It must overlay, not compress."
        )
        assert after["scroll"] <= after["client"] + 1, (
            f"{width}x{height}: the open drawer made the page scroll sideways: {after}"
        )
    finally:
        context.close()


def test_the_handle_is_a_forty_four_pixel_target_at_the_narrowest_width(browser):
    """Visually a triangle; pressable as a box.

    A CSS triangle's hit area is its bounding box or its painted shape depending on how
    it is drawn - borders give the box, `clip-path` gives the shape - and at 390px that
    difference is whether a thumb lands on the control or beside it. The corner is
    probed as well as the centre, because a `clip-path` implementation passes a
    centre-only check and fails a real thumb.
    """
    context, page = _open_page(browser, "/index.html", width=390, height=700)
    try:
        box = page.locator(".site-drawer__handle").bounding_box()
        assert box is not None, "the handle has no box at all"
        assert box["width"] >= 44 and box["height"] >= 44, box
        assert box["x"] >= 0 and box["x"] + box["width"] <= 390 + 1, (
            f"the handle is off the screen edge: {box}"
        )

        # **All four corners of the box, and the far two are the discriminating pair.**
        # The apex is at the box's inline-end edge, half way down, so the two corners on
        # that edge are the ones a painted-shape implementation misses by the widest
        # margin - a probe near the base would be inside a `clip-path` triangle too and
        # would pass against the very thing this is written to refuse. Verified by
        # mutation: `clip-path: polygon(0 0, 100% 50%, 0 100%)` fails here and passes a
        # base-corner check.
        corners = page.evaluate(
            """(box) => {
                 const inset = 3;
                 const points = [
                   ['near-top', box.x + inset, box.y + inset],
                   ['near-bottom', box.x + inset, box.y + box.height - inset],
                   ['far-top', box.x + box.width - inset, box.y + inset],
                   ['far-bottom', box.x + box.width - inset, box.y + box.height - inset],
                 ];
                 const missed = [];
                 for (const [name, x, y] of points) {
                   const hit = document.elementFromPoint(x, y);
                   if (!(hit && hit.closest('.site-drawer__handle'))) missed.push(name);
                 }
                 return missed;
               }""",
            box,
        )
        assert corners == [], (
            "the handle does not take a press at %s, so its hit area is the painted "
            "triangle rather than its box - which is the 390px failure" % corners
        )
    finally:
        context.close()


def test_the_handle_reports_its_state_and_escape_closes_it(browser):
    """`aria-expanded`, `Escape`, and where focus goes afterwards.

    The direction a triangle points is the only visible statement of this control's
    state, and a shape says nothing to a reader who is not looking at it. `Escape` is
    the second half: closing a drawer and leaving focus on a link that is no longer
    reachable strands a keyboard user in the document body. There is one accessibility
    regression on record here from a focus call placed where it fired on every state
    change, so the resting case is asserted too - focus must NOT be dragged to the
    handle by an ordinary open.
    """
    context, page = _open_page(browser, "/stats.html")
    try:
        handle = page.locator(".site-drawer__handle")
        assert handle.get_attribute("aria-expanded") == "false"

        page.focus(".site-drawer__handle")
        page.keyboard.press("Enter")
        page.wait_for_timeout(300)
        assert page.locator("#site-drawer").evaluate("el => el.open") is True
        assert handle.get_attribute("aria-expanded") == "true"

        # Into the panel, the way a keyboard user gets there.
        page.keyboard.press("Tab")
        assert page.evaluate(
            "() => Boolean(document.activeElement.closest('.site-drawer__nav'))"
        ), "Tab from the handle did not reach the panel's links"

        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        assert page.locator("#site-drawer").evaluate("el => el.open") is False
        assert handle.get_attribute("aria-expanded") == "false"
        assert page.evaluate(
            "() => document.activeElement === document.querySelector('.site-drawer__handle')"
        ), (
            "Escape closed the drawer and left focus inside it; the keyboard user is "
            "now on an element nobody can see"
        )
    finally:
        context.close()


@pytest.mark.parametrize("path", STATIC_PAGES)
def test_the_drawer_navigates_with_scripting_switched_off(browser, path):
    """The reason it is a `<details>` and not a button with a class toggle.

    `home.html`, `stats.html` and `methodology.html` are static content and must not
    lose their navigation when scripting is off; the calculator renders nothing without
    it and is exempt. This drives a real click through Playwright's actionability
    checks - which include the browser's own hit test - so a panel that were present
    but unreachable would fail here rather than pass on a `count()`.
    """
    context, page = _open_page(browser, path, java_script_enabled=False)
    try:
        page.click(".site-drawer__handle")
        page.wait_for_timeout(200)
        page.click('.site-drawer__nav a[href$="methodology.html"]')
        page.wait_for_load_state("domcontentloaded")
        assert page.url.endswith("/methodology.html"), (
            f"{path}: the drawer did not navigate with scripting off: {page.url}"
        )
    finally:
        context.close()


def test_the_drawer_mirrors_in_a_right_to_left_page(browser):
    """`dir="rtl"` proves nothing on its own; the drawer has to change edges.

    The handle's drop shadow is measured too. It is the one physical value in the
    component - a shadow offset cannot be written logically - so it is the one that
    silently stays put when everything around it mirrors, and a shadow falling the
    wrong way is the half-mirrored page this project decided against shipping.
    """
    def measure(language):
        context, page = _open_page(browser, "/stats.html", width=938, height=898,
                                   language=language)
        try:
            page.click(".site-drawer__handle")
            page.wait_for_timeout(400)
            return page.evaluate(
                """() => {
                     const panel = document.querySelector('.site-drawer__panel').getBoundingClientRect();
                     const handle = document.querySelector('.site-drawer__handle').getBoundingClientRect();
                     return {
                       dir: document.documentElement.dir,
                       panelStart: Math.round(panel.x),
                       panelEnd: Math.round(panel.right),
                       handleStart: Math.round(handle.x),
                       shadow: getComputedStyle(
                         document.querySelector('.site-drawer__handle')).filter,
                       viewport: window.innerWidth,
                       overflows: document.documentElement.scrollWidth >
                                  document.documentElement.clientWidth + 1,
                     };
                   }"""
            )
        finally:
            context.close()

    ltr = measure("en")
    rtl = measure("ar")

    assert ltr["dir"] == "ltr" and rtl["dir"] == "rtl"
    assert ltr["panelStart"] == 0, ltr
    assert rtl["panelEnd"] == rtl["viewport"], rtl
    # The handle is outboard of the panel in each direction: after it in English,
    # before it in Arabic.
    assert ltr["handleStart"] >= ltr["panelEnd"] - 4, ltr
    assert rtl["handleStart"] <= rtl["panelStart"], rtl
    assert not rtl["overflows"], "the mirrored drawer made the page scroll sideways"

    def offset(filter_value):
        """The shadow's x offset, in px.

        Read with a regex rather than by splitting on the first token, because
        `getComputedStyle` re-serialises the function with the colour FIRST -
        `drop-shadow(rgba(0, 50, 35, 0.22) 3px 3px 0px)` - and the shorthand this is
        written from puts it last.
        """
        assert "drop-shadow" in filter_value, filter_value
        lengths = re.findall(r"(-?[\d.]+)px", filter_value)
        assert len(lengths) >= 3, filter_value
        return float(lengths[0])

    assert offset(ltr["shadow"]) > 0, ltr["shadow"]
    assert offset(rtl["shadow"]) == -offset(ltr["shadow"]), (
        "the handle's shadow falls the same way in both directions: %s vs %s"
        % (ltr["shadow"], rtl["shadow"])
    )


def test_the_drawer_does_not_animate_under_reduced_motion(browser):
    """A panel springing in from the edge of the screen is the motion vestibular
    sensitivity is worst with, and it is decoration on a control that works without it.

    Asserted as the computed `animation-name`, and against the *presence* of an
    animation in the ordinary case - an assertion that only checked for `none` would
    pass against a drawer that had never animated at all, which is a rule nothing
    holds.
    """
    def animation(reduced):
        context, page = _open_page(
            browser, "/methodology.html", reduced_motion="reduce" if reduced else "no-preference"
        )
        try:
            page.click(".site-drawer__handle")
            page.wait_for_timeout(150)
            return page.evaluate(
                "() => getComputedStyle(document.querySelector('.site-drawer__panel'))"
                ".animationName"
            )
        finally:
            context.close()

    assert animation(reduced=False) != "none", (
        "the drawer does not animate at all, so the reduced-motion rule below asserts "
        "nothing"
    )
    assert animation(reduced=True) == "none"


def test_the_open_drawer_clears_the_sticky_step_bar(browser):
    """The bottom edge belongs to `.step-nav`.

    `position: sticky; bottom: 0` on the calculator's Back/Next bar is what put every
    step's primary action above the fold at 1278x983, 938x898 and 390x700. A drawer
    anchored to the bottom on a narrow screen would sit on top of it, so this is where
    that decision is written down as a measurement: at the narrowest supported width,
    with the drawer open, the two must not overlap and the primary action must still
    take a click.
    """
    context, page = _open_page(browser, "/index.html", width=390, height=700)
    try:
        page.click(".site-drawer__handle")
        page.wait_for_timeout(400)
        boxes = page.evaluate(
            """() => {
                 const r = (sel) => {
                   const el = document.querySelector(sel);
                   const b = el.getBoundingClientRect();
                   return {top: Math.round(b.top), bottom: Math.round(b.bottom),
                           left: Math.round(b.left), right: Math.round(b.right)};
                 };
                 return {drawer: r('#site-drawer'), bar: r('.step-nav')};
               }"""
        )
        drawer, bar = boxes["drawer"], boxes["bar"]
        overlaps = (
            drawer["top"] < bar["bottom"]
            and bar["top"] < drawer["bottom"]
            and drawer["left"] < bar["right"]
            and bar["left"] < drawer["right"]
        )
        assert not overlaps, (
            f"the open drawer sits on the sticky step bar at 390x700: {boxes}"
        )

        reachable = page.evaluate(
            """() => {
                 const b = document.querySelector('.step-nav [data-action="continue"]')
                   .getBoundingClientRect();
                 const hit = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2);
                 return Boolean(hit && hit.closest('[data-action="continue"]'));
               }"""
        )
        assert reachable, "the open drawer is covering the step's primary action"
    finally:
        context.close()
