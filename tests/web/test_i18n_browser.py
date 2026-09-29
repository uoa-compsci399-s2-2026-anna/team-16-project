"""Translation, in a browser, on the running stack, with the language varied.

**A test that asserts a string exists does not assert anyone can read it.**
Six defects have reached this repository through green markup tests - 87 field
descriptions asserted present while four were invisible behind a widget's CSS
class, five guidance blocks asserted present while absent from the built image.
`test_i18n_web.py` proves the catalogues are complete; it cannot prove a single
one of them reaches a screen, because the calculator renders from template
literals and a literal that was never wrapped in `t()` looks exactly like one
that was.

So this file drives Chromium against http://localhost:18080, sets
`navigator.languages` and `Accept-Language` to what a real visitor would send,
and asserts **a specific string inside the specific element that carries it**.
"The page contains Chinese characters" would pass against a page with one
translated word in it and is written nowhere here.

Requires the stack rebuilt: `docker compose -f docker/compose.yaml up -d
--build web admin`. Skipped rather than failed when it is not up.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tests.web.steps import press_continue

from tests.web import i18n_keys

pytestmark = pytest.mark.browser

#: `KAICALC_WEB_URL` like every other browser module here, which this one alone
#: did not read. It hard-coded :18080, so it measured the stack on that port
#: whatever the rest of the run was pointed at — and
#: `test_every_catalogue_is_actually_in_the_built_image`, whose whole subject is
#: whether the *current* checkout's catalogues shipped, was reading an image
#: built from some earlier one. A stale image is exactly what that test exists to
#: report, so it did report it; but it reported it about the wrong stack, and it
#: could not be pointed at the right one.
BASE = os.environ.get("KAICALC_WEB_URL", "http://localhost:18080").rstrip("/")

#: Set through `Page.add_init_script` before any of the page's own scripts run,
#: because `web/js/i18n.js` reads `navigator.languages` at module evaluation.
#: Playwright's `locale=` only sets `Accept-Language` and `navigator.language`;
#: it does not set the ordered list, which is the thing under test.
_LANGUAGES_SHIM = """
Object.defineProperty(navigator, 'languages', {{ get: () => {languages} }});
Object.defineProperty(navigator, 'language', {{ get: () => {first} }});
"""


def _stack_is_up() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE}/", timeout=3) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

if not _stack_is_up():  # pragma: no cover - environment guard
    pytest.skip(
        f"the stack is not answering on {BASE}; run "
        "`docker compose -f docker/compose.yaml up -d --build web admin`",
        allow_module_level=True,
    )

#: `browser` itself now comes from `tests/web/conftest.py`, package-scoped
#: and shared across every file in this directory - see that module's
#: docstring for why a per-file fixture corrupted the rest of the run.
def open_page(browser, languages, path="/index.html", query="", stats_fixture=False):
    """A page whose browser claims `languages`, in preference order.

    **The default is `/index.html`, and `/` is the same document again** - nginx
    says `index index.html`, `home.html` is retired. It is named explicitly rather
    than left as `/` so that this file measures the calculator whatever the `index`
    directive says next.

    `stats_fixture` serves `tests/fixtures/stats.json` in place of the live
    statistics response, the same way `test_statistics_browser.py` does. The
    deployed factor set is the owner's to change - it has been a US comparison
    set since 14 August - and a translation test that depends on how many
    buckets survive suppression today is a test that fails for the wrong reason
    tomorrow.
    """
    context = browser.new_context(
        extra_http_headers={"Accept-Language": ",".join(languages)}
    )
    page = context.new_page()
    page.add_init_script(
        _LANGUAGES_SHIM.format(
            languages=json.dumps(languages), first=json.dumps(languages[0])
        )
    )
    if stats_fixture:
        page.route(
            "**/api/v1/stats",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(_STATS_FIXTURE),
            ),
        )
    page.goto(f"{BASE}{path}{query}", wait_until="networkidle")
    return context, page


#: The canonical ten-bucket response, which is also what
#: `test_statistics_browser.py` measures against.
_STATS_FIXTURE = json.loads(
    (Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "stats.json").read_text(
        encoding="utf-8"
    )
)


# ---------------------------------------------------------------------------
# The built image
# ---------------------------------------------------------------------------


def test_every_catalogue_is_actually_in_the_built_image():
    """`web/locales/` is a new runtime directory, which is the exact shape of a
    defect this repository has already paid for: five guidance blocks passed
    every test in the checkout and were absent from the image. Nothing but a
    request to the running container proves a file shipped.

    A miss here does not 404, either - nginx redirects an unknown path to `/`
    (`@not_a_page`), so a missing catalogue arrives as a 200 carrying HTML.
    That is why the body is parsed rather than the status checked.
    """
    with urllib.request.urlopen(f"{BASE}/locales/index.json", timeout=10) as response:
        manifest = json.loads(response.read().decode("utf-8"))
    assert [entry["language"] for entry in manifest["catalogues"]] == (
        i18n_keys.catalogue_languages()
    )
    for entry in manifest["catalogues"]:
        url = f"{BASE}/locales/{entry['language']}.json"
        with urllib.request.urlopen(url, timeout=10) as response:
            catalogue = json.loads(response.read().decode("utf-8"))
        assert catalogue["language"] == entry["language"]
        assert len(catalogue["strings"]) == len(i18n_keys.source_strings())


# ---------------------------------------------------------------------------
# The calculator: negotiated from navigator.languages, with no query string
# ---------------------------------------------------------------------------


def test_the_intro_renders_in_chinese_for_a_chinese_browser(browser):
    """The whole of change 1 on the public side: no picker, no cookie, no
    query string, and the first paint is already in the visitor's language.

    The first screen is the introduction again. It was step one for the week
    `home.html` held `/`; `home.html` is retired, `/` serves this page, and this
    page opens on its own hero.
    """
    context, page = open_page(browser, ["zh-CN", "zh", "en"])
    try:
        assert page.get_attribute("html", "lang") == "zh"
        # The element, not the page. A heading asserted "somewhere in the
        # markup" would pass against a string rendered into a comment.
        assert page.inner_text("h1#page-title") == "食物浪费影响计算器"
        assert page.inner_text('[data-action="start"]') == "启动计算器"
        # Static HTML the browser parsed before any module ran.
        assert "Skip to calculator" not in page.inner_text(".skip-link")
        assert page.inner_text("footer .transparency-notice") != ""
    finally:
        context.close()


def test_en_nz_reaches_english_and_nothing_is_translated(browser):
    """`en-NZ` has no catalogue of its own and must reach the source language
    by truncation rather than by falling off the end of the negotiation."""
    context, page = open_page(browser, ["en-NZ", "en"])
    try:
        assert page.get_attribute("html", "lang") == "en-NZ"
        assert page.inner_text("h1#page-title") == "Food Waste Impact Calculator"
        assert page.locator("#machine-translation-notice").count() == 0
    finally:
        context.close()


def test_only_the_first_language_is_consulted_and_the_rest_is_not_walked(browser):
    """v1.26, in a real browser, with a real `navigator.languages`.

    `sv` has no catalogue, so this visitor reads English — the Korean behind
    it does not get a turn. A browser's second and third entries are
    frequently residue (a preinstalled locale, an input method added once)
    rather than a second language, and English is the floor every reader of
    this tool has.

    **The second half is the test.** The first assertion alone would pass
    against a calculator that had lost the ability to render Korean at all, so
    the same list with Korean at the head is asserted beside it, on the same
    element, against Korean's own catalogue entry.
    """
    context, page = open_page(browser, ["sv-SE", "ko", "en"])
    try:
        assert page.get_attribute("html", "lang") == "en-NZ"
        assert page.inner_text("h1#page-title") == "Food Waste Impact Calculator"
    finally:
        context.close()

    korean = i18n_keys.catalogue("ko")["strings"]["Food Waste Impact Calculator"]
    context, page = open_page(browser, ["ko", "sv-SE", "en"])
    try:
        assert page.get_attribute("html", "lang") == "ko"
        assert page.inner_text("h1#page-title") == korean
    finally:
        context.close()


def test_a_regional_first_tag_still_truncates_to_its_catalogue(browser):
    """`de-AT, xx` reads German. The rule took away the walk between tags, not
    the lookup inside one — and this is the case that tells the two apart: a
    negotiator that had stopped truncating would answer English here and still
    pass every assertion above."""
    german = i18n_keys.catalogue("de")["strings"]["Food Waste Impact Calculator"]
    context, page = open_page(browser, ["de-AT", "xx"])
    try:
        assert page.get_attribute("html", "lang") == "de"
        assert page.inner_text("h1#page-title") == german
    finally:
        context.close()


def test_zh_tw_reaches_traditional_chinese_and_not_simplified(browser):
    """The one case a plain RFC 4647 implementation gets wrong.

    Anchored on a character that differs between the scripts, so a
    Traditional page that had silently fallen back to Simplified fails here.
    """
    context, page = open_page(browser, ["zh-TW", "zh", "en"])
    try:
        assert page.get_attribute("html", "lang") == "zh-Hant"
        # 計算 / 计算 - the same word in each script, in the introduction screen's
        # title. The anchor was 供應鏈 / 供应链 for the week that screen was deleted
        # and step one's heading was the first one on the page.
        heading = page.inner_text("h1#page-title")
        assert "計算" in heading, heading
        assert "计算" not in heading, "zh-TW fell through to Simplified Chinese"
    finally:
        context.close()


def test_zh_hk_also_reaches_traditional(browser):
    context, page = open_page(browser, ["zh-HK", "en"])
    try:
        assert page.get_attribute("html", "lang") == "zh-Hant"
    finally:
        context.close()


def test_fil_reaches_the_tagalog_catalogue(browser):
    """Browsers send `fil` for Filipino; the catalogue is `tl`."""
    context, page = open_page(browser, ["fil", "en"])
    try:
        assert page.get_attribute("html", "lang") == "tl"
    finally:
        context.close()


def test_an_unknown_tag_falls_all_the_way_to_english(browser):
    context, page = open_page(browser, ["xx-YY", "zz"])
    try:
        assert page.get_attribute("html", "lang") == "en-NZ"
        assert page.inner_text("h1#page-title") == "Food Waste Impact Calculator"
    finally:
        context.close()


def test_lang_forces_a_language_over_the_browser_s_own(browser):
    context, page = open_page(browser, ["en-NZ"], query="?lang=th")
    try:
        assert page.get_attribute("html", "lang") == "th"
    finally:
        context.close()


def test_an_unrecognised_lang_is_ignored_and_the_browser_still_decides(browser):
    context, page = open_page(browser, ["zh-CN", "en"], query="?lang=qq")
    try:
        assert page.get_attribute("html", "lang") == "zh"
    finally:
        context.close()


def test_nothing_is_stored_until_a_choice_is_made(browser):
    """**This test replaced its own opposite**, and the bound is what changed.

    It used to assert `context.cookies() == []` outright, so that storing
    anything had to be a deliberate act rather than a quiet one. It was. What
    it asserts now is narrower and more useful: **merely visiting stores
    nothing.** Negotiation still keeps nothing, and the cookie appears only
    when somebody uses the control.

    `localStorage` stays empty in both cases - the choice is a cookie because
    the panel has to read it server-side, and a second copy in `localStorage`
    would be a second thing to keep in step.
    """
    context, page = open_page(browser, ["zh-CN", "en"])
    try:
        assert context.cookies() == [], "visiting the page stored something"
        assert page.evaluate("Object.keys(localStorage)") == []
        assert "lang" not in " ".join(page.evaluate("Object.keys(sessionStorage)"))
    finally:
        context.close()


def _choose(page, value):
    """Operate the control the way a person does, and wait for the result.

    `select_option` fires `change`, which is what the chooser listens for. The
    wait is on the rendered text rather than on a timeout: the handler fetches
    a catalogue, so asserting immediately would race it.
    """
    page.select_option("#language-chooser", value)
    page.wait_for_function(
        "(want) => document.getElementById('language-chooser')?.value === want",
        arg=value,
    )
    # The handler repaints the chooser after the catalogue arrives, so wait for
    # the document to be announced in the language that was asked for rather
    # than for the select alone - the select's value updates synchronously on
    # `change` and would let every assertion below race the fetch.
    page.wait_for_function(
        "(want) => want === 'auto' || document.documentElement.lang.startsWith("
        "want === 'zh-Hant' ? 'zh' : want)",
        arg=value,
    )


def test_the_chooser_is_present_usable_and_at_the_top_inline_end(browser):
    """Present is not usable, and this project has shipped that six times.

    So: in the viewport without scrolling, big enough to touch, actually
    reachable by keyboard, and carrying a real accessible name rather than a
    bare `<select>` a screen reader announces as nothing.
    """
    context, page = open_page(browser, ["en-NZ"])
    try:
        box = page.locator("#language-chooser").bounding_box()
        assert box is not None, "the chooser is not rendered at all"
        assert box["height"] >= 44, f"below the 44px touch target: {box}"
        assert box["y"] < page.evaluate("window.innerHeight"), "below the fold"

        # A real accessible name, from a real <label for>.
        assert page.evaluate(
            """() => document.querySelector('label[for="language-chooser"]')
                 ?.textContent.trim()"""
        ) == "Language"

        # Keyboard reachable: focus it and confirm it took focus.
        page.locator("#language-chooser").focus()
        assert page.evaluate("document.activeElement.id") == "language-chooser"

        # **Last item in the header's own row, and it must cost no height.**
        # A strip of its own above the header cost 57px on every page, which
        # this calculator cannot afford - it deleted an 87px step-indicator band
        # to stop short steps scrolling. So the chooser joins the row after the
        # brand, and the row must not have grown.
        assert page.evaluate(
            """() => {
              const bar = document.querySelector('.language-bar');
              const row = document.querySelector('.header-inner');
              const brand = document.querySelector('.brand');
              return bar.parentElement === row &&
                (brand.compareDocumentPosition(bar) &
                 Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
            }"""
        ), "the chooser is not after the brand in the header row"
        assert page.evaluate(
            "() => Math.round("
            "document.querySelector('.header-inner').getBoundingClientRect().height)"
        ) <= 96, "the header grew to make room for the chooser"
    finally:
        context.close()


# ---------------------------------------------------------------------------
# The chooser as an object somebody can see: the capsule, the globe, the ring
#
# `test_the_chooser_is_present_usable_and_at_the_top_inline_end` above proves
# the control is there, big enough and reachable. None of that says it is
# LEGIBLE as a control, and the whole of this section is about a defect class
# this repository keeps shipping: an element that every geometry assertion
# agrees with and nobody can see. A `<select>` styled to nothing is present; a
# label the same colour as the ground behind it is present; an SVG with no
# stroke has a bounding box.
# ---------------------------------------------------------------------------

#: One read of everything the treatment is made of. Computed styles rather than
#: the stylesheet's text, because what is in `styles.css` and what a browser
#: resolved are different claims and only the second one is the page.
_CAPSULE = """
() => {
  const bar = document.querySelector('.language-bar');
  const select = document.getElementById('language-chooser');
  const globe = document.querySelector('.language-bar__globe');
  const label = document.querySelector('.language-bar__label');
  const barStyle = getComputedStyle(bar);
  const selectStyle = getComputedStyle(select);
  const box = (el) => {
    const b = el.getBoundingClientRect();
    return {x: b.x, y: b.y, w: b.width, h: b.height, left: b.left, right: b.right,
            top: b.top, bottom: b.bottom};
  };
  return {
    shadow: barStyle.boxShadow,
    radius: parseFloat(barStyle.borderRadius),
    ground: barStyle.backgroundColor,
    selectBorder: [selectStyle.borderTopWidth, selectStyle.borderInlineStartWidth],
    selectGround: selectStyle.backgroundColor,
    labelColour: label && getComputedStyle(label).color,
    labelGround: label && getComputedStyle(label).backgroundColor,
    labelDrawn: label ? box(label).w > 1 : false,
    bar: box(bar),
    select: box(select),
    globe: globe && box(globe),
    globeIsSvg: Boolean(globe) && globe.namespaceURI === 'http://www.w3.org/2000/svg',
    globeHidden: globe && globe.getAttribute('aria-hidden'),
    globeChildren: globe ? [...globe.children].map((c) => c.tagName) : null,
    dir: document.documentElement.dir,
  };
}
"""

#: `box-shadow` layers, split without cutting `rgba(0, 50, 35, 0.18)` in half:
#: only a comma followed by the start of a colour or a keyword begins a layer,
#: and the commas inside `rgba(...)` are all followed by a digit.
_LAYER = re.compile(r",(?=\s*(?:rgba|rgb|#|inset|[a-z]))")

#: `rgb(r, g, b)` / `rgba(r, g, b, a)` as a browser reports it.
_RGB = re.compile(r"rgba?\(([^)]*)\)")


def _rgba(value):
    """A computed colour as `(r, g, b, alpha)`, alpha 0..1."""
    match = _RGB.search(value)
    assert match, f"not a colour this helper understands: {value!r}"
    parts = [float(p.strip()) for p in match.group(1).split(",")]
    if len(parts) == 3:
        parts.append(1.0)
    return tuple(parts)


def _contrast(foreground, background):
    """WCAG contrast ratio between two computed colours, both opaque."""

    def channel(value):
        value /= 255
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    def luminance(colour):
        r, g, b, _ = _rgba(colour)
        return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)

    lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


#: Whether the `<select>`'s currently selected text fits inside it, arrow and
#: padding included. A `<select>` clips its closed value silently — no ellipsis,
#: no overflow, no scrollWidth to read — so the only way to know is to measure
#: the text and compare. The probe copies the control's own font rather than
#: assuming one, and is removed before it can be seen.
_VALUE_FITS = """
() => {
  const select = document.getElementById('language-chooser');
  const style = getComputedStyle(select);
  const probe = document.createElement('span');
  probe.style.cssText = 'position:absolute;visibility:hidden;white-space:nowrap;left:-9999px';
  probe.style.font = style.font;
  probe.style.letterSpacing = style.letterSpacing;
  probe.textContent = select.options[select.selectedIndex].textContent.trim();
  document.body.append(probe);
  const text = probe.getBoundingClientRect().width;
  probe.remove();
  const room = select.clientWidth
    - parseFloat(style.paddingInlineStart) - parseFloat(style.paddingInlineEnd);
  // The browser draws its own arrow inside the control. Chromium's is about
  // 16px wide with its spacing; 20 is that with a pixel of margin either side.
  return {text: Math.round(text), room: Math.round(room), arrow: 20,
          value: probe.textContent};
}
"""

#: Breathing room the value has to have beyond "it happens to fit".
#:
#: **A bare fit is not a size, it is a coincidence, and this control has already
#: been one.** The panel's first sizing settled the `<select>` at 146px around a
#: value measuring 145px with its arrow: correct by a pixel, on this machine, in
#: this Chromium, with this font loaded. Deleting the rule that sizes the control
#: left it at 140px against 139px and every assertion stayed green — which is how
#: that mutant survived twice. Twelve pixels is under a character at this size and
#: is the difference between a control that was measured and one that was lucky.
_VALUE_SLACK = 12


def _paints_something(page, selector):
    """Whether hiding this element changes the pixels where it says it is.

    **The one assertion in this file that is not a number read off the DOM.**
    An `<svg>` whose stroke resolved to `none`, or whose paths are outside its
    own `viewBox`, has a bounding box, an `aria-hidden`, three children and
    draws nothing — every structural assertion above passes and the control has
    no icon on it. So the region is photographed with the element visible and
    again with `visibility: hidden`, and the two PNGs have to differ. Chromium
    encodes identical pixels to identical bytes, so no image decoder is needed
    and Pillow — which this project deliberately does not depend on — stays out
    of the suite.

    `visibility` rather than `display`, so nothing around it moves and the two
    photographs are of the same rectangle.
    """
    box = page.locator(selector).bounding_box()
    assert box is not None, f"{selector} is not rendered at all"
    clip = {
        "x": box["x"],
        "y": box["y"],
        "width": max(box["width"], 1),
        "height": max(box["height"], 1),
    }
    drawn = page.screenshot(clip=clip)
    page.eval_on_selector(selector, "el => { el.style.visibility = 'hidden'; }")
    blank = page.screenshot(clip=clip)
    page.eval_on_selector(selector, "el => { el.style.visibility = ''; }")
    return drawn != blank


def test_the_chooser_is_one_raised_surface_and_the_select_has_no_box_of_its_own(browser):
    """The treatment the owner asked for, measured rather than eyeballed.

    A bordered, slightly raised component: a hairline, a lift off the page, a
    radius, and an opaque ground of its own. And the half that is easy to leave
    out — the `<select>` has to GIVE UP its frame when the capsule takes one, or
    the result is a box 2px inside a box, which is the obvious implementation
    and the one that reads as a mistake.

    **The capsule is exactly as tall as the control it holds.** That is the
    height budget of contract §7.7.4 restated as an assertion: this row is 92px
    around a 67px logo and a separate strip for the chooser cost 57px and was
    removed. A 1px `border` would put the capsule at 46px and the wrapped row at
    390px 2px above what it was measured at, so the hairline is an inset shadow.
    """
    context, page = open_page(browser, ["en-NZ"])
    try:
        seen = page.evaluate(_CAPSULE)

        layers = _LAYER.split(seen["shadow"])
        inset = [layer for layer in layers if "inset" in layer]
        lift = [layer for layer in layers if "inset" not in layer]
        assert inset, f"no hairline on the capsule: {seen['shadow']!r}"
        assert lift, f"the capsule is flat — nothing lifts it off the page: {seen['shadow']!r}"
        assert seen["radius"] >= 8, seen

        ground = _rgba(seen["ground"])
        assert ground[3] == 1, f"the capsule has no ground of its own: {seen['ground']!r}"

        assert seen["selectBorder"] == ["0px", "0px"], (
            f"the select still draws its own box inside the capsule: {seen}"
        )
        assert _rgba(seen["selectGround"])[3] == 0, (
            f"the select paints its own ground inside the capsule: {seen}"
        )

        # Everything is inside the capsule, or it is not one component.
        for part in ("select", "globe"):
            piece = seen[part]
            assert piece is not None, f"no {part}"
            assert seen["bar"]["left"] - 1 <= piece["left"], (part, seen)
            assert piece["right"] <= seen["bar"]["right"] + 1, (part, seen)
            assert seen["bar"]["top"] - 1 <= piece["top"], (part, seen)
            assert piece["bottom"] <= seen["bar"]["bottom"] + 1, (part, seen)

        assert round(seen["bar"]["h"]) == 44, (
            "the capsule is taller than the 44px control inside it, which spends "
            f"header height §7.7.4 measured: {seen['bar']}"
        )

        # **No hole at the reading end.** A percentage inside `min()` is
        # indefinite while a flex container is sized from its contents, so
        # `max-width: min(100%, 22ch)` clamped the select's used width and not
        # its max-content contribution: the capsule shrink-wrapped to 396px
        # around a 183px control and 83px of it was empty. Invisible while the
        # select had the only border; a visible hole the moment the box moved
        # outwards. `.language-bar__select` carries an explicit `width` for
        # this, and nothing but a measurement of the gap would notice it going.
        assert seen["bar"]["right"] - seen["select"]["right"] <= 12, (
            "the capsule is wider than what is in it — dead space between the "
            f"value and its inline-end edge: {seen}"
        )

        # And the other end of the same rule: the control is not so narrow that
        # the language it names is clipped. A `<select>` truncates its closed
        # value in silence, so the text is measured against the room it has.
        fit = page.evaluate(_VALUE_FITS)
        assert fit["room"] >= fit["text"] + fit["arrow"] + _VALUE_SLACK, (
            f"the selected language is clipped, or fits only by accident: {fit}"
        )
    finally:
        context.close()


def test_the_globe_is_drawn_in_the_page_and_actually_paints(browser):
    """Inline SVG, because it cannot be anything else.

    `img-src 'self' data:` forbids a third-party origin, there is no icon font
    and there is no build step, so the icon is drawn in `web/js/i18n.js`. This
    asserts it is really SVG, really painted, and really silent — the `<label>`
    already names this control and a second name would have a screen reader say
    "globe, Language".
    """
    context, page = open_page(browser, ["en-NZ"])
    try:
        seen = page.evaluate(_CAPSULE)
        assert seen["globeIsSvg"], "the globe is not an SVG element in the SVG namespace"
        assert seen["globeHidden"] == "true", (
            f"the globe would be announced beside the label: {seen['globeHidden']!r}"
        )
        assert seen["globe"]["w"] >= 14 and seen["globe"]["h"] >= 14, seen["globe"]
        assert seen["globeChildren"] == ["circle", "path", "ellipse"], seen["globeChildren"]

        # Nothing is fetched to draw it: no `<image>`, no `<use>`, no `<img>`.
        assert page.evaluate(
            "() => document.querySelectorAll('.language-bar img, .language-bar image,"
            " .language-bar use').length"
        ) == 0

        assert _paints_something(page, ".language-bar__globe"), (
            "the globe occupies its box and draws nothing in it — a stroke that "
            "resolved to `none` passes every assertion above"
        )

        # The accessible name is still the one word, not two.
        assert page.evaluate(
            """() => document.querySelector('label[for="language-chooser"]')
                 ?.textContent.trim()"""
        ) == "Language"
    finally:
        context.close()


@pytest.mark.parametrize(
    "language,direction", [("en", "ltr"), ("ar", "rtl"), ("ur", "rtl")]
)
def test_the_globe_leads_the_control_in_whichever_direction_the_page_reads(
    browser, language, direction
):
    """The globe is at the capsule's inline-start, so it swaps sides on its own.

    A physical `left` here would put the icon at the reading-END of the control
    in Arabic — after the value it introduces. The whole of `styles.css` was
    converted to logical properties for this and a test refuses a physical
    direction property in it; this is the same rule measured on a rendered page,
    which is the half that catches a `flex-direction` doing the damage instead.

    The `<select>`'s arrow is the browser's own and is drawn at the select's
    inline-end, so the two land on opposite sides of the capsule in both
    directions. That is asserted here too — it is the reason the arrow is left
    alone rather than replaced with a drawn one.
    """
    context, page = open_page(browser, ["en-NZ"], query=f"?lang={language}")
    try:
        seen = page.evaluate(_CAPSULE)
        assert seen["dir"] == direction, seen["dir"]
        if direction == "ltr":
            assert seen["globe"]["right"] <= seen["select"]["left"] + 1, seen
            assert seen["globe"]["left"] < seen["bar"]["left"] + 32, seen
        else:
            assert seen["select"]["right"] <= seen["globe"]["left"] + 1, seen
            assert seen["globe"]["right"] > seen["bar"]["right"] - 32, seen
    finally:
        context.close()


def test_the_capsule_shows_a_focus_ring_when_the_control_is_tabbed_to(browser):
    """Keyboard-operable **and visibly so**.

    The ring is on the capsule rather than on the `<select>`: a rectangle drawn
    round a frameless control inside a frame is not an indicator anybody reads
    as "this is focused". `:focus-within` carries it, which is also why the
    select's own outline can be suppressed — the two are one decision and
    removing either half alone leaves a control with no focus indicator.

    Tabbed to rather than `.focus()`ed, because the claim is about a keyboard.
    """
    context, page = open_page(browser, ["en-NZ"])
    try:
        resting = page.evaluate(
            "() => getComputedStyle(document.querySelector('.language-bar')).outlineStyle"
        )
        assert resting == "none", f"the capsule is ringed before anything is focused: {resting}"

        # Start the walk at the skip link, which is the first thing in the tab
        # order. The calculator moves focus into `<main>` when a step renders and
        # step one has more than eight tabbable controls in it, so a walk started
        # from wherever the render left the caret walks the form rather than the
        # header - and blurring does not move the sequential-focus starting point
        # back, it only clears the ring. Focusing the SKIP LINK is not focusing the
        # control under test: the claim is that Tab reaches the chooser from the top
        # of the tab order, and that is where this puts it.
        page.focus(".skip-link")

        for _ in range(8):
            page.keyboard.press("Tab")
            if page.evaluate("() => document.activeElement?.id") == "language-chooser":
                break
        else:  # pragma: no cover - a chooser no Tab reaches is the defect
            raise AssertionError("eight Tabs from the top of the page never reached the chooser")

        ring = page.evaluate(
            """() => {
              const cs = getComputedStyle(document.querySelector('.language-bar'));
              return {style: cs.outlineStyle, width: parseFloat(cs.outlineWidth),
                      colour: cs.outlineColor};
            }"""
        )
        assert ring["style"] not in ("none", "hidden"), ring
        assert ring["width"] >= 2, ring
        assert _rgba(ring["colour"])[3] > 0, ring
        # A ring the same colour as the ground it is drawn on is not a ring.
        assert _contrast(ring["colour"], "rgb(0, 50, 35)") >= 1.6, ring
    finally:
        context.close()


def test_the_label_is_legible_against_the_ground_it_is_actually_on(browser):
    """The intro header used to tint this label white for a Kale ground.

    The label sits inside a **white** capsule now, on every header, so that rule
    would paint white on white: present in the DOM, still the control's
    accessible name, and invisible to everyone who can see. That is not a
    hypothetical here — an Arabic label collapsed into a 1px column survived a
    green suite on this branch, and it was a screenshot that caught it.

    Asserted on `/`, whose header is the Kale one, because that is the page the
    deleted rule applied to.
    """
    context, page = open_page(browser, ["en-NZ"])
    try:
        seen = page.evaluate(_CAPSULE)
        assert seen["labelDrawn"], "the label is not drawn at this width at all"
        assert _rgba(seen["labelGround"])[3] == 0, (
            "the label paints its own ground, so this test is measuring the wrong "
            f"pair of colours: {seen['labelGround']!r}"
        )
        ratio = _contrast(seen["labelColour"], seen["ground"])
        assert ratio >= 4.5, (
            f"the label is {ratio:.2f}:1 against the capsule it sits on "
            f"({seen['labelColour']} on {seen['ground']})"
        )
    finally:
        context.close()


# ---------------------------------------------------------------------------
# The panel's copy of the same capsule
#
# **Why it is in this file.** The browser harness lives in `tests/web`;
# `tests/admin` drives an httpx client and has no browser at all, and standing
# one up there for a single test would be a second harness to keep. The panel's
# login gate is the one panel screen that needs no session, it carries the same
# partial and the same stylesheet as every screen behind it, and it is the page
# a person who cannot read English needs most. What it cannot cover — that the
# Tabler skin *reaches* `language.css` — is asserted in
# `tests/admin/test_i18n_pages.py`, which can read the panel proper's markup.
# ---------------------------------------------------------------------------


def test_the_panel_chooser_is_the_same_capsule_and_its_button_works(browser):
    """One object holding a globe, a word, a control and its submit button.

    The panel's chooser is a real `<form method="post">` and stays one: it is
    the surface that genuinely works with scripting off. So the button cannot be
    designed away, and it is joined to the field instead — pressing it is
    asserted here, because a button drawn into a capsule at the wrong corner
    radius is still a button and a button that no longer submits is not.
    """
    context = browser.new_context()
    page = context.new_page()
    try:
        page.goto(f"{BASE}/admin/login", wait_until="networkidle")

        seen = page.evaluate(
            """() => {
              const bar = document.querySelector('.language-bar');
              const button = document.querySelector('.language-bar__submit');
              const globe = document.querySelector('.language-bar__globe');
              const select = document.getElementById('language-chooser');
              const cs = getComputedStyle(bar);
              const box = (el) => { const b = el.getBoundingClientRect();
                return {w: b.width, h: b.height, left: b.left, right: b.right,
                        top: b.top, bottom: b.bottom}; };
              return {shadow: cs.boxShadow, radius: parseFloat(cs.borderRadius),
                      ground: cs.backgroundColor, viewport: window.innerWidth,
                      bar: box(bar), button: box(button), globe: globe && box(globe),
                      select: box(select),
                      buttonInk: getComputedStyle(button).color,
                      buttonGround: getComputedStyle(button).backgroundColor};
            }"""
        )

        layers = _LAYER.split(seen["shadow"])
        assert [layer for layer in layers if "inset" in layer], seen["shadow"]
        assert [layer for layer in layers if "inset" not in layer], seen["shadow"]
        assert seen["radius"] >= 8, seen

        # **It floats rather than spanning the screen**, which is the half of the
        # report that read as scaffolding: a full-bleed white strip with a rule
        # under it is chrome the panel grew to hold one small control.
        #
        # Half the viewport, not "narrower than the viewport". Two rules make
        # this capsule the size of its contents — `align-self: flex-start`
        # against Tabler's flex column and `width: 20ch` on the select — and
        # against a 1,278px screen both of them fail into a pill of 1,200-odd
        # pixels holding 320 of control, which is comfortably "narrower than the
        # viewport" and is exactly the shape being fixed. Measured at 395px.
        assert seen["bar"]["w"] <= seen["viewport"] / 2, (
            f"the capsule is a banner rather than a control: {seen}"
        )
        # The button is a segment of the capsule, not a control beside it.
        assert seen["button"]["right"] <= seen["bar"]["right"] + 1, seen
        assert seen["button"]["top"] >= seen["bar"]["top"] - 1, seen
        assert seen["button"]["bottom"] <= seen["bar"]["bottom"] + 1, seen
        assert _contrast(seen["buttonInk"], seen["buttonGround"]) >= 4.5, seen
        # No hole between the value and the button - the same `min(100%, ...)`
        # sizing trap the calculator's capsule was measured with.
        assert seen["button"]["left"] - seen["select"]["right"] <= 12, (
            f"dead space between the control and the button it is joined to: {seen}"
        )
        # **And the value inside it is readable.** `.language-bar__select` is
        # sized in `ch` for exactly this, and a `<select>` clips its closed value
        # in silence - no ellipsis, no overflow, nothing to read off the DOM. The
        # first sizing this capsule was given settled the control at 146px
        # against a 145px value: correct by one pixel, and by accident.
        fit = page.evaluate(_VALUE_FITS)
        assert fit["room"] >= fit["text"] + fit["arrow"] + _VALUE_SLACK, (
            f"the selected language is clipped, or fits only by accident: {fit}"
        )

        assert seen["globe"] is not None and seen["globe"]["w"] >= 14, seen
        assert _paints_something(page, ".language-bar__globe"), (
            "the panel's globe draws nothing inside its own box"
        )

        # Focus ring, from a keyboard.
        for _ in range(8):
            page.keyboard.press("Tab")
            if page.evaluate("() => document.activeElement?.id") == "language-chooser":
                break
        else:  # pragma: no cover
            raise AssertionError("eight Tabs never reached the panel's chooser")
        ring = page.evaluate(
            """() => {
              const cs = getComputedStyle(document.querySelector('.language-bar'));
              return {style: cs.outlineStyle, width: parseFloat(cs.outlineWidth)};
            }"""
        )
        assert ring["style"] not in ("none", "hidden") and ring["width"] >= 2, ring

        # And it still posts. Chinese, then read back what the panel rendered.
        page.select_option("#language-chooser", "zh")
        page.click(".language-bar__submit")
        page.wait_for_load_state("networkidle")
        assert page.get_attribute("html", "lang") == "zh", (
            "the button no longer submits the form it is drawn into"
        )
        assert page.eval_on_selector("#language-chooser", "el => el.value") == "zh"
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 320])
def test_the_panel_chooser_fits_a_phone_and_still_names_its_language(browser, width):
    """The capsule is four things wide and a phone is not.

    **Both widths are asserted for staying on screen, and only 390 for staying
    readable, and the difference is a decision rather than an oversight.** Three
    rules hold this together and each one shows at a different width, which is
    why one viewport could not kill them:

    * the **label stops being drawn** below 720px. With the word still there the
      control was squeezed to 101px at 390 and 31px at 320 — present, sized,
      and not a control anybody reads a language out of.
    * the capsule takes a **ceiling** of the page width less its own margins, and
      the select takes `min-width: 0` so it is the part that gives way. At 390 the
      hidden label already makes it fit; at **320** it does not, and without these
      two the capsule hangs 44px off the screen.

    At 320 the value truncates: it needs 139px and has 117px. That is the
    deliberate half of the trade — a control on screen with a clipped word beats a
    control a reader has to scroll sideways to find, on the one screen a
    locked-out account can still open. Making it fit properly costs a stack of
    narrow-width tweaks to the margin, the button's padding and the gap, at a
    width nothing else on this surface supports: 320 is the *calculator's*
    declared floor (`body { min-width: 320px }`), and `_list_table_css.html`
    records four panel screens already scrolling sideways at 390 for reasons
    that have nothing to do with this control.

    `fit-content` is the sum of a globe, a word, a 20ch control and a button —
    395px — and 390px of screen is 350px once the capsule's own margins are off
    it. Measured hanging 45px off the gate page and 22px off a Tabler page before
    the ceiling existed, which is a control a reader has to scroll sideways to
    reach on the one screen a locked-out account can still open.

    Two rules carry it and both are asserted here, because the first alone is not
    enough: the capsule takes a ceiling, and the label stops being **drawn** below
    720px so that the `<select>` is not what pays for it. With the word still
    there the control was squeezed to 101px at 390 and 31px at 320, which is not
    a control anybody reads a language out of — and its `<label for>` association
    survives, so the accessible name does not change.
    """
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    try:
        page.goto(f"{BASE}/admin/login", wait_until="networkidle")

        seen = page.evaluate(
            """(w) => {
              const bar = document.querySelector('.language-bar');
              const b = bar.getBoundingClientRect();
              return {left: Math.round(b.left), right: Math.round(b.right),
                      width: Math.round(b.width), height: Math.round(b.height),
                      overflow: document.documentElement.scrollWidth
                                - document.documentElement.clientWidth,
                      labelDrawn: document.querySelector('.language-bar__label')
                                    .getBoundingClientRect().width > 2,
                      name: document.querySelector('label[for="language-chooser"]')
                              .textContent.trim()};
            }""",
            width,
        )
        assert seen["overflow"] <= 0, f"{width}px: the page scrolls sideways: {seen}"
        assert seen["left"] >= 0 and seen["right"] <= width, (
            f"{width}px: the capsule hangs off the screen: {seen}"
        )
        assert seen["height"] == 44, seen
        assert not seen["labelDrawn"], (
            f"{width}px: the word is still drawn and the control is paying for it: {seen}"
        )
        assert seen["name"] == "Language", (
            "hiding the word must not take the control's accessible name with it"
        )

        fit = page.evaluate(_VALUE_FITS)
        if width >= 390:
            assert fit["room"] >= fit["text"] + fit["arrow"] + _VALUE_SLACK, (
                f"{width}px: the selected language is clipped, or fits only by"
                f" accident: {fit}"
            )
        else:
            # Asserted as the known state rather than left unmentioned, so that
            # a future narrow-width pass that fixes it has to come here and say
            # so instead of silently satisfying a test nobody wrote.
            assert fit["room"] < fit["text"] + fit["arrow"], (
                f"{width}px: the value now fits — good, and this test's docstring "
                f"and the CSS comment that record it as the trade are stale: {fit}"
            )
    finally:
        context.close()


#: Everything the gate skin has to be measured on, read in one round trip.
#:
#: `--lang-*` are deliberately NOT read here. A custom property's computed value
#: is the token, not what was painted with it, so reading them would assert that
#: the stylesheet says what the stylesheet says. Every value below is a used
#: value off a laid-out element.
_GATE = """
() => {
  const bar = document.querySelector('.language-bar');
  const gate = document.querySelector('.gate');
  const label = document.querySelector('.language-bar__label');
  const select = document.getElementById('language-chooser');
  const button = document.querySelector('.language-bar__submit');
  const cs = getComputedStyle(bar);
  const box = (el) => { const b = el.getBoundingClientRect();
    return {w: b.width, h: b.height, left: b.left, right: b.right,
            top: b.top, bottom: b.bottom}; };
  return {
    pageGround: getComputedStyle(document.body).backgroundColor,
    surface: cs.backgroundColor,
    shadow: cs.boxShadow,
    ink: cs.color,
    labelInk: getComputedStyle(label).color,
    labelGround: getComputedStyle(label).backgroundColor,
    selectInk: getComputedStyle(select).color,
    buttonInk: getComputedStyle(button).color,
    buttonGround: getComputedStyle(button).backgroundColor,
    bar: box(bar),
    gate: box(gate),
    viewportH: document.documentElement.clientHeight,
    viewportW: document.documentElement.clientWidth,
    docHeight: document.documentElement.scrollHeight,
    dir: document.documentElement.dir,
  };
}
"""


def _tab_to_the_chooser(page):
    for _ in range(8):
        page.keyboard.press("Tab")
        if page.evaluate("() => document.activeElement?.id") == "language-chooser":
            return
    raise AssertionError("eight Tabs from the top of the page never reached the chooser")


def test_the_gate_s_kale_runs_under_the_chooser_rather_than_stopping_below_it(browser):
    """The report, in two measurements.

    `brand.css` painted `.gate` and left `body` white, and the chooser is a
    SIBLING of `.gate` — it has to be, because `brand/base.html` owns it and
    every page's `{% block shell %}` replaces what is under it. So the page's
    own ground above the shell was white: a 72px full-bleed strip, a hard edge,
    then the deep green with the card floating in it. A patch stuck onto a page.

    **The first assertion is photographic and that is the point.** A computed
    `backgroundColor` on `<body>` is a property read; it would pass against a
    Kale body with a white element still spanning the top of it, which is the
    defect one refactor away. So a square of the page BESIDE the capsule and a
    square from the middle of the field are photographed and their bytes
    compared — Chromium encodes identical pixels identically, which is the same
    property `_paints_something` is built on. One ground or two.

    The second is the 72px the strip also cost in height. `.gate` asks for
    `100vh` and the chooser sits above it, so the login page scrolled by exactly
    the height of the chooser for as long as the two were stacked. Asserted as
    "the shell ends at the fold", not as "the document does not scroll" — a page
    whose card is taller than the viewport is allowed to scroll, and every gate
    page but login has a taller card.
    """
    context = browser.new_context(viewport={"width": 1278, "height": 983})
    page = context.new_page()
    try:
        page.goto(f"{BASE}/admin/login", wait_until="networkidle")
        seen = page.evaluate(_GATE)

        # Beside the capsule, at its own vertical middle, at the far inline-end
        # of the row it is on — the strip the white band occupied.
        beside = {"x": seen["viewportW"] - 140, "y": seen["bar"]["top"],
                  "width": 60, "height": 40}
        # Well inside the shell, clear of the card.
        inside = {"x": seen["viewportW"] - 140, "y": seen["viewportH"] / 2,
                  "width": 60, "height": 40}
        assert page.screenshot(clip=beside) == page.screenshot(clip=inside), (
            "the ground the chooser floats on is not the ground the card sits "
            "on — the page is two colours stacked, which is the band being fixed"
        )

        # And the shell no longer asks for a second viewport under the chooser.
        assert seen["gate"]["bottom"] <= seen["viewportH"] + 1, (
            "the shell still claims a full 100vh below the chooser, so the page "
            f"scrolls by the height of the chooser and nothing else: {seen}"
        )
        assert seen["gate"]["top"] >= seen["bar"]["bottom"], (
            f"the shell is drawn over the chooser rather than below it: {seen}"
        )
    finally:
        context.close()


def test_the_capsule_is_legible_on_the_kale_ground_and_is_not_a_hole_in_it(browser):
    """Every colour in this control was correct on Tabler's grey and wrong here.

    A white capsule on Kale is a hole punched in the field; a Kale shadow on
    Kale is a shadow nobody can see; a Kale-filled button on a Kale-family
    capsule is a button nobody can find. All three shipped, because the capsule
    was built against the one ground the browser module could reach and the gate
    pages were the other one.

    So the four pairs that decide whether it can be read are measured **on the
    page that actually renders them**, not against the tokens the stylesheet
    declares. And the pair nobody thinks to assert is the last one: a surface
    identical to the field it floats on satisfies every contrast rule above it
    and is not an object at all.
    """
    context = browser.new_context(viewport={"width": 1278, "height": 983})
    page = context.new_page()
    try:
        page.goto(f"{BASE}/admin/login", wait_until="networkidle")
        seen = page.evaluate(_GATE)

        assert _rgba(seen["surface"])[3] == 1, (
            f"the capsule has no ground of its own on Kale: {seen['surface']!r}"
        )
        assert _rgba(seen["labelGround"])[3] == 0, (
            "the label paints its own ground, so this is measuring the wrong "
            f"pair of colours: {seen['labelGround']!r}"
        )
        for part, ink in (("label", seen["labelInk"]),
                          ("value", seen["selectInk"])):
            ratio = _contrast(ink, seen["surface"])
            assert ratio >= 4.5, (
                f"the {part} is {ratio:.2f}:1 against the capsule it sits on "
                f"({ink} on {seen['surface']})"
            )
        ratio = _contrast(seen["buttonInk"], seen["buttonGround"])
        assert ratio >= 4.5, f"the button reads {ratio:.2f}:1 on the Kale ground: {seen}"

        # **The object has to be an object.** Nothing above notices a capsule
        # painted the exact colour of the field: the text would still be
        # legible, the shadow would still have its layers, and the control would
        # have vanished into the ground.
        assert seen["surface"] != seen["pageGround"], (
            "the capsule is painted the same colour as the field it floats on: "
            f"{seen['surface']!r}"
        )
        assert _paints_something(page, ".language-bar"), (
            "hiding the whole capsule changes no pixels — it is the field"
        )

        # It is lit as well as seated. On a dark ground the cast shadow is the
        # half that cannot be seen, so an inset layer is what raises it.
        layers = _LAYER.split(seen["shadow"])
        assert [layer for layer in layers if "inset" in layer], seen["shadow"]
        assert [layer for layer in layers if "inset" not in layer], seen["shadow"]
    finally:
        context.close()


def test_the_focus_ring_is_visible_against_the_ground_it_is_drawn_on(browser):
    """A focus ring that disappears is worse than an ugly one.

    The ring carries `outline-offset`, so it is drawn on the PAGE's ground and
    not on the capsule's — which is why it is measured against `<body>` here and
    why the offset is asserted rather than assumed. Blueberry was the colour on
    both grounds and measures **2.2:1 on Kale**, under the 3:1 a non-text
    indicator needs; it is Banana on this ground and stays Blueberry on the
    light one, which is the whole reason the skin is a set of tokens.

    The inner half is asserted too. The ring is two lines — an outline outside
    and the capsule's own hairline taken to full contrast inside it — so that
    losing either still leaves an indicator. A hairline left at its resting
    translucency is exactly the half that would go unnoticed, so its **alpha**
    is what is read: 0.28 at rest, 1 when focused.

    Tabbed to rather than `.focus()`ed, because the claim is about a keyboard.
    """
    context = browser.new_context(viewport={"width": 1278, "height": 983})
    page = context.new_page()
    try:
        page.goto(f"{BASE}/admin/login", wait_until="networkidle")
        resting = page.evaluate(
            """() => {
              const cs = getComputedStyle(document.querySelector('.language-bar'));
              return {style: cs.outlineStyle, shadow: cs.boxShadow};
            }"""
        )
        assert resting["style"] == "none", resting
        resting_inset = [
            layer for layer in _LAYER.split(resting["shadow"]) if "inset" in layer
        ][0]
        assert _rgba(resting_inset)[3] < 1, (
            f"the hairline is already opaque at rest, so focus cannot say anything "
            f"by making it so: {resting_inset!r}"
        )

        _tab_to_the_chooser(page)
        ring = page.evaluate(
            """() => {
              const cs = getComputedStyle(document.querySelector('.language-bar'));
              return {style: cs.outlineStyle, width: parseFloat(cs.outlineWidth),
                      offset: parseFloat(cs.outlineOffset), colour: cs.outlineColor,
                      shadow: cs.boxShadow,
                      ground: getComputedStyle(document.body).backgroundColor};
            }"""
        )
        assert ring["style"] not in ("none", "hidden"), ring
        assert ring["width"] >= 2, ring
        assert ring["offset"] > 0, (
            "the ring has no offset, so it is drawn on the capsule and the "
            f"contrast asserted below is against the wrong ground: {ring}"
        )
        ratio = _contrast(ring["colour"], ring["ground"])
        assert ratio >= 3, (
            f"the focus ring is {ratio:.2f}:1 against the Kale field it is drawn "
            f"on ({ring['colour']} on {ring['ground']})"
        )
        focused_inset = [
            layer for layer in _LAYER.split(ring["shadow"]) if "inset" in layer
        ][0]
        assert _rgba(focused_inset)[3] == 1, (
            f"the ring's inner line stayed at its resting translucency: {focused_inset!r}"
        )
    finally:
        context.close()


def test_the_same_capsule_off_the_kale_ground_falls_back_to_the_light_skin(browser):
    """**The panel proper must not have been broken to fix the gate.**

    Every colour in this component became a token in order to give the Kale
    ground a second skin, and a typo in one default would restyle every screen a
    signed-in person uses — which is the exact defect `language.css` was created
    to close, pointed the other way.

    Reaching a Tabler page means driving the whole login gauntlet, which this
    module deliberately does not do (see the section header above). What it can
    do is take away the one thing the skin is derived from. The ground is
    selected as `body:has(> .gate)`, so removing that class from the shell —
    the real page, the real stylesheet, the real element, one class less —
    is precisely the condition every panel screen is in, and the component has
    to change back.

    The Tabler ground is grey rather than white, so what is asserted is the
    component's own colours and not a contrast against a ground this page does
    not have.
    """
    context = browser.new_context(viewport={"width": 1278, "height": 983})
    page = context.new_page()
    try:
        page.goto(f"{BASE}/admin/login", wait_until="networkidle")
        kale = page.evaluate(_GATE)

        page.eval_on_selector(".gate", "el => el.classList.remove('gate')")
        light = page.evaluate(
            """() => {
              const bar = document.querySelector('.language-bar');
              const label = document.querySelector('.language-bar__label');
              const button = document.querySelector('.language-bar__submit');
              const cs = getComputedStyle(bar);
              return {surface: cs.backgroundColor, labelInk: getComputedStyle(label).color,
                      buttonInk: getComputedStyle(button).color,
                      buttonGround: getComputedStyle(button).backgroundColor};
            }"""
        )

        assert light["surface"] != kale["surface"], (
            "the capsule wears the Kale skin on a page with no Kale on it, so "
            f"every panel screen wears it too: {light}"
        )
        assert _rgba(light["surface"]) == (255.0, 255.0, 255.0, 1.0), (
            f"the light skin's capsule is no longer white: {light['surface']!r}"
        )
        for part, ink in (("label", light["labelInk"]),
                          ("button", light["buttonInk"])):
            ground = light["surface"] if part == "label" else light["buttonGround"]
            ratio = _contrast(ink, ground)
            assert ratio >= 4.5, (
                f"the light skin's {part} reads {ratio:.2f}:1 ({ink} on {ground})"
            )
        assert light["buttonGround"] != light["surface"], (
            f"the light skin's button has lost its own ground: {light}"
        )
    finally:
        context.close()


def test_the_island_moves_to_the_other_side_when_the_page_reads_right_to_left(browser):
    """The capsule's position on the page, not the order of things inside it.

    `test_the_globe_leads_the_control_in_whichever_direction_the_page_reads`
    covers the inside on the calculator, which ships Arabic and Urdu. **The
    panel ships English and Chinese**, so no catalogue here can produce an RTL
    page — and `dir` is emitted from the catalogue anyway (§7.7.8), so the first
    RTL catalogue added to `admin/locales/` is what would set it. Setting it
    directly is therefore what that catalogue would do and nothing more.

    Asserted because the fix moved the ground under this control: an island
    pinned physically to the left of a mirrored page lands at the reading-END of
    it, which is the same defect the whole stylesheet was written in logical
    properties to avoid.
    """
    context = browser.new_context(viewport={"width": 1278, "height": 983})
    page = context.new_page()
    try:
        page.goto(f"{BASE}/admin/login", wait_until="networkidle")
        ltr = page.evaluate(_GATE)
        assert ltr["bar"]["left"] < ltr["viewportW"] / 2, ltr["bar"]

        page.evaluate("() => { document.documentElement.dir = 'rtl'; }")
        rtl = page.evaluate(_GATE)
        assert rtl["dir"] == "rtl"
        assert rtl["bar"]["right"] > rtl["viewportW"] / 2, (
            f"the island stayed at the physical left of a mirrored page: {rtl['bar']}"
        )
        # Mirrored, not merely moved: the same gap from the reading edge.
        assert abs(
            (rtl["viewportW"] - rtl["bar"]["right"]) - ltr["bar"]["left"]
        ) <= 1, (rtl["bar"], ltr["bar"])
        # And the ground goes with it — the field is one colour in both.
        assert rtl["pageGround"] == ltr["pageGround"], (rtl, ltr)
    finally:
        context.close()


def test_choosing_a_language_survives_a_reload_and_another_page(browser):
    """The whole point of the feature, driven the way a person drives it.

    Select, reload, navigate to a second page - and only then assert. A test
    that asserted straight after the click would prove the DOM updated, not
    that anything was remembered.
    """
    context, page = open_page(browser, ["en-NZ"])
    try:
        _choose(page, "zh")
        assert page.inner_text("h1#page-title") == "食物浪费影响计算器"

        stored = [c for c in context.cookies() if c["name"] == "kaicalc_lang"]
        assert len(stored) == 1 and stored[0]["value"] == "zh"
        assert stored[0]["path"] == "/", "the panel could not read it at any other path"
        assert stored[0]["httpOnly"] is False, "web/js/i18n.js has to read it"

        # **The chooser translates itself, not only the page around it.**
        # `i18n_keys.py` finds these three strings by reading the constants they
        # are declared as, so a chooser that stopped passing them through `t()`
        # would keep every catalogue complete and every file-level test green -
        # a mutation that did exactly that survived the whole suite until this
        # assertion existed. Only a rendered page catches it.
        assert page.eval_on_selector(
            'label[for="language-chooser"]', "el => el.textContent.trim()"
        ) == "语言"
        assert page.eval_on_selector(
            '#language-chooser option[value="auto"]', "el => el.textContent.trim()"
        ) == "跟随系统"
        assert "机器翻译" in page.eval_on_selector(
            '#language-chooser option[value="th"]', "el => el.textContent"
        )

        page.reload(wait_until="networkidle")
        assert page.inner_text("h1#page-title") == "食物浪费影响计算器"
        assert page.eval_on_selector("#language-chooser", "el => el.value") == "zh"

        page.goto(f"{BASE}/methodology.html", wait_until="networkidle")
        assert page.eval_on_selector("#language-chooser", "el => el.value") == "zh"
        assert "透明" in page.inner_text("body") or "方法" in page.inner_text("body")
    finally:
        context.close()


def test_follow_the_system_reverts_and_is_stored_rather_than_deleted(browser):
    """Going back is a write, not a deletion, and the page follows the browser again.

    The `value == "auto"` assertion is the one that kills the tempting
    implementation: clearing the cookie would also make the page revert, and
    would then be indistinguishable from never having chosen - and a cookie
    deletion that misses on path or domain silently leaves the old value.
    """
    context, page = open_page(browser, ["zh-CN", "en"])
    try:
        _choose(page, "en")
        assert page.inner_text("h1#page-title") == "Food Waste Impact Calculator"

        _choose(page, "auto")
        # Back to the browser's own language, which is Chinese here.
        assert page.inner_text("h1#page-title") == "食物浪费影响计算器"

        stored = [c for c in context.cookies() if c["name"] == "kaicalc_lang"]
        assert len(stored) == 1, "follow-the-system deleted the cookie"
        assert stored[0]["value"] == "auto"
    finally:
        context.close()


def test_lang_does_not_write_the_choice(browser):
    """A shared support link must not re-language the recipient for good.

    This is what keeps `?lang=` and the chooser from being confused for one
    another: one renders a page, the other remembers.
    """
    context, page = open_page(browser, ["en-NZ"], query="?lang=th")
    try:
        assert page.get_attribute("html", "lang") == "th"
        assert [c for c in context.cookies() if c["name"] == "kaicalc_lang"] == []
    finally:
        context.close()


def test_a_stored_choice_beats_the_browser_and_the_notice_follows(browser):
    """Chinese browser, English chosen - the case the chooser was asked for.

    The notice assertions are the second half: switching into a machine
    translated language must raise it, and switching out must remove it. A
    notice that outlived the language it warned about would be a false
    statement about a reviewed page.
    """
    context, page = open_page(browser, ["zh-CN", "en"])
    try:
        assert page.locator("#machine-translation-notice").count() == 0

        _choose(page, "th")
        assert page.locator("#machine-translation-notice").count() == 1
        assert page.evaluate(
            "document.body.firstElementChild.id"
        ) == "machine-translation-notice", "the chooser displaced the notice"

        _choose(page, "en")
        assert page.inner_text("h1#page-title") == "Food Waste Impact Calculator"
        assert page.locator("#machine-translation-notice").count() == 0, (
            "the notice outlived the language it was warning about"
        )
    finally:
        context.close()


def test_switching_twice_does_not_strand_the_page_in_the_first_language(browser):
    """The defect `applyToDocument`'s key-pinning exists to prevent.

    `data-i18n` with no value means "my own text is the key". After one switch
    that text is Chinese, so a second switch looks the Chinese up as a key,
    finds nothing, and leaves the page stuck. Two switches and a return to
    English is the shortest sequence that catches it; the footer is asserted
    because it is static HTML translated by `applyToDocument` rather than
    re-rendered by the wizard.
    """
    context, page = open_page(browser, ["en-NZ"])
    try:
        english = page.inner_text("footer")
        _choose(page, "zh")
        chinese = page.inner_text("footer")
        assert chinese != english

        _choose(page, "ja")
        japanese = page.inner_text("footer")
        assert japanese not in (english, chinese), "stranded in the first language"

        _choose(page, "en")
        assert page.inner_text("footer") == english, "cannot get back to English"
    finally:
        context.close()


def test_the_machine_translated_options_are_marked_before_anyone_picks(browser):
    """The warning belongs on the option too, not only after the choice.

    Chinese and English carry no mark - English is hand-written and Chinese is
    reviewed by its users - and asserting that is what stops the marker being
    applied to everything.
    """
    context, page = open_page(browser, ["en-NZ"])
    try:
        labels = page.evaluate(
            "() => Object.fromEntries(Array.from("
            "document.querySelectorAll('#language-chooser option')"
            ").map(o => [o.value, o.textContent]))"
        )
        assert "machine translated" in labels["th"]
        assert "machine translated" in labels["ar"]
        assert "machine translated" not in labels["zh"]
        assert "machine translated" not in labels["en"]
        # The endonym itself is never translated away.
        assert "中文" in labels["zh"] and labels["en"] == "English"
        assert labels["auto"] == "Follow the system"
    finally:
        context.close()


#: 938 is the owner's own laptop and sits inside the 561-999px band, where
#: the header row carries THREE items since v1.47 - brand, clear action or
#: navigation, and chooser - having carried two before it. That band had no
#: width under test at all, so the row going one item wider was measured
#: nowhere.
@pytest.mark.parametrize("width", [390, 938, 1280])
def test_the_chooser_is_usable_at_every_width(browser, width):
    """390px is a phone. A control that overflows there is not a control.

    The horizontal-overflow assertion is the one that matters: the language bar
    is a new row at the top of every page and is the most likely thing to widen
    the document.
    """
    context = browser.new_context(
        viewport={"width": width, "height": 800},
        extra_http_headers={"Accept-Language": "en-NZ"},
    )
    try:
        page = context.new_page()
        page.goto(f"{BASE}/index.html", wait_until="networkidle")
        box = page.locator("#language-chooser").bounding_box()
        assert box is not None and box["height"] >= 44, f"{width}px: {box}"
        assert box["x"] >= 0 and box["x"] + box["width"] <= width + 1, (
            f"{width}px: the chooser is off screen: {box}"
        )
        assert not page.evaluate(
            "document.documentElement.scrollWidth > "
            "document.documentElement.clientWidth + 1"
        ), f"{width}px: the page scrolls sideways"
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1280])
def test_the_chooser_mirrors_in_a_right_to_left_page(browser, width):
    """The chooser remains at reading-end when Arabic mirrors the header.

    **Measured, not trusted.** `dir="rtl"` on its own proves nothing - the
    whole point of the logical-property conversion is that the bar moves. So
    the chooser's distance from each edge is measured in both directions and
    the two must swap: inline-end in English is the right edge, and in Arabic
    it is the left one.
    """
    def measure(languages):
        context = browser.new_context(
            viewport={"width": width, "height": 800},
            extra_http_headers={"Accept-Language": ",".join(languages)},
        )
        try:
            page = context.new_page()
            page.add_init_script(
                _LANGUAGES_SHIM.format(
                    languages=json.dumps(languages), first=json.dumps(languages[0])
                )
            )
            page.goto(f"{BASE}/index.html", wait_until="networkidle")
            # Measured against the BAR's own edges rather than the viewport's.
            # The bar is full width, so a viewport measurement says the same
            # thing, but only by coincidence - the claim being tested is that
            # the content sits at the row's reading-end edge.
            return page.evaluate(
                """() => {
                  // The ROW, not the bar. The bar now hugs its own contents
                  // inside the header row, so measuring the gap against the bar
                  // would read zero in both directions and prove nothing.
                  const bar = document.querySelector('.header-inner');
                  const el = document.getElementById('language-chooser');
                  const label = document.querySelector('.language-bar__label');
                  const b = bar.getBoundingClientRect();
                  const s = el.getBoundingClientRect();
                  const l = label.getBoundingClientRect();
                  return {
                    dir: document.documentElement.dir,
                    // Gap between the bar's edge and the first thing in it.
                    gapLeft: Math.round(Math.min(s.left, l.left) - b.left),
                    gapRight: Math.round(b.right - Math.max(s.right, l.right)),
                    // The label is visually hidden below 720px, so its box is
                    // 1px and off-flow; compare it only where it is drawn.
                    labelLeftOfSelect: l.width > 2 ? l.left < s.left : null,
                    overflows:
                      document.documentElement.scrollWidth >
                      document.documentElement.clientWidth + 1,
                  };
                }"""
            )
        finally:
            context.close()

    rtl = measure(["ar"])
    ltr = measure(["en-NZ"])

    assert ltr["dir"] == "ltr" and rtl["dir"] == "rtl"
    # The content hugs the reading-end edge, which is the right in English and
    # the left in Arabic. Asserted as a swap rather than against a constant, so
    # that changing the bar's padding does not require editing this test.
    assert ltr["gapRight"] < ltr["gapLeft"], f"{width}px: not at the end in ltr"
    assert rtl["gapLeft"] < rtl["gapRight"], f"{width}px: not mirrored in rtl"
    assert ltr["gapRight"] == rtl["gapLeft"], (
        f"{width}px: the two directions are not mirror images: {ltr} vs {rtl}"
    )
    # The label leads the control in reading order in both directions, which is
    # the half that `dir` alone would not give us. Skipped where the label is
    # visually hidden, which is a real state and not a failure.
    if ltr["labelLeftOfSelect"] is not None:
        assert ltr["labelLeftOfSelect"] and not rtl["labelLeftOfSelect"]
    assert not rtl["overflows"], f"{width}px: the right-to-left page scrolls sideways"


def test_the_calculator_says_it_needs_scripting_rather_than_offering_a_dead_control(
    browser,
):
    """With JavaScript off there is no chooser at all, and a note saying why.

    The note is about the calculator, not about the language control: this page
    is ES modules end to end and renders nothing without scripting, so a
    chooser that needed JavaScript added no degradation. What would have been
    wrong is a `<select>` sitting in the static HTML doing nothing.
    """
    context = browser.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(f"{BASE}/index.html", wait_until="domcontentloaded")
        assert page.locator("#language-chooser").count() == 0, (
            "a language control is present with scripting off and cannot work"
        )
        notice = page.locator(".noscript-notice")
        assert notice.count() == 1 and notice.is_visible()
        assert "JavaScript" in notice.inner_text()
    finally:
        context.close()


# ---------------------------------------------------------------------------
# The notice, and what it says about which promise is being made
# ---------------------------------------------------------------------------


def test_a_machine_translated_page_says_so_at_the_top_in_both_languages(browser):
    """The notice cannot live on a switcher any more, so it lives on the page.

    Asserted at the top of `<body>` and in two languages: the sentence a
    machine-translated page most has to get right went through the same
    machine as the rest of the file, so the English half is not decoration.
    """
    context, page = open_page(browser, ["th", "en"])
    try:
        notice = page.locator("#machine-translation-notice")
        assert notice.count() == 1
        assert notice.is_visible()
        # The first element of the body, before the header - not a footnote.
        assert page.evaluate(
            "document.body.firstElementChild.id"
        ) == "machine-translation-notice"
        english = notice.locator(".machine-translation-notice-en")
        assert "machine translated" in english.inner_text()
        assert english.get_attribute("lang") == "en"
        # And the Thai half is not the English one repeated.
        assert notice.inner_text().replace(english.inner_text(), "").strip()
    finally:
        context.close()


def test_chinese_carries_no_notice_because_it_has_readers(browser):
    context, page = open_page(browser, ["zh-CN", "en"])
    try:
        assert page.locator("#machine-translation-notice").count() == 0
    finally:
        context.close()


# ---------------------------------------------------------------------------
# Right to left
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("language", ["ar", "ur"])
def test_arabic_and_urdu_render_right_to_left(browser, language):
    context, page = open_page(browser, [language, "en"])
    try:
        assert page.get_attribute("html", "dir") == "rtl"
        assert page.get_attribute("html", "lang") == language
    finally:
        context.close()


def _mirror_measurements(page):
    return page.evaluate(
        """() => {
        const item = document.querySelector('.check-list li');
        const label = document.querySelector('.prototype-label');
        const bullet = getComputedStyle(item, '::before');
        return {
          dir: document.documentElement.dir,
          itemPadLeft: getComputedStyle(item).paddingLeft,
          itemPadRight: getComputedStyle(item).paddingRight,
          bulletLeft: parseFloat(bullet.left),
          bulletRight: parseFloat(bullet.right),
          labelBorderLeft: getComputedStyle(label).borderLeftWidth,
          labelBorderRight: getComputedStyle(label).borderRightWidth,
          overflows: document.body.scrollWidth > window.innerWidth + 1,
        };
      }"""
    )


def test_the_right_to_left_layout_is_actually_mirrored(browser):
    """`dir="rtl"` on its own proves nothing.

    A page whose stylesheet is full of physical `margin-left` rules carries
    the attribute and lays out exactly as it did before - which is the
    half-mirrored page this project decided was worse than not shipping
    Arabic and Urdu at all. So this is measured rather than asserted: the
    check-list's indent, the tick drawn before each item, and the accent
    border beside the wordmark all have to change sides, and the page must
    not gain a horizontal scrollbar doing it.

    **On the calculator, because that is where the check-list is again.** "What you
    will need" spent a week on `home.html`, which is retired; it is back on the
    introduction screen, which is also where `.prototype-label` lives. The label is
    `display: none` under `.intro-header` and that is deliberate on both sides: it is
    still in the DOM, so its logical border still resolves, and the wordmark beside
    it is what says the same thing to a reader.
    """
    context, page = open_page(browser, ["ar"])
    try:
        rtl = _mirror_measurements(page)
    finally:
        context.close()

    context, page = open_page(browser, ["en-NZ"])
    try:
        ltr = _mirror_measurements(page)
    finally:
        context.close()

    assert rtl["dir"] == "rtl" and ltr["dir"] == "ltr"
    assert ltr["itemPadLeft"] == rtl["itemPadRight"] != "0px"
    assert rtl["itemPadLeft"] == ltr["itemPadRight"] == "0px"
    assert ltr["bulletLeft"] == 0 and ltr["bulletRight"] > 0
    assert rtl["bulletRight"] == 0 and rtl["bulletLeft"] > 0
    assert ltr["labelBorderLeft"] == rtl["labelBorderRight"] != "0px"
    assert not rtl["overflows"], "the right-to-left page scrolls sideways"


# ---------------------------------------------------------------------------
# Past the first screen: the steps, the form, and what stays English
# ---------------------------------------------------------------------------


def test_the_wizard_is_translated_past_the_first_screen(browser):
    """The intro is one function of six. A page that translates its hero and
    reverts on step 1 is what a test of the landing screen alone would miss."""
    context, page = open_page(browser, ["zh-CN", "en"])
    try:
        page.click('[data-action="start"]')
        page.wait_for_selector("#stage-title")
        assert page.inner_text("#stage-title") == "这些浪费发生在食物供应链的哪个环节？"
        # The step bar, which composes its label from a placeholder.
        assert page.inner_text(".step-nav-label") == "第 1 步，共 6 步"
        assert page.inner_text(".step-nav-name") == "供应链环节"
        assert page.inner_text('.step-nav [data-action="continue"]') == "继续"
    finally:
        context.close()


def test_the_taxonomy_stays_in_the_language_staff_typed_it(browser):
    """The honest half of the result, asserted rather than left to be noticed.

    Destination names, food categories and sector names are staff-typed rows
    (O-8 rule 1), so a Chinese page lists them in English. This test is what
    makes that a decision the repository holds rather than a gap somebody
    finds.
    """
    context, page = open_page(browser, ["zh-CN", "en"])
    try:
        page.click('[data-action="start"]')
        page.wait_for_selector(".stage-card")
        names = page.locator(".stage-title").all_inner_texts()
        assert names, "no sectors rendered - is the API up?"
        assert any(name.isascii() for name in names), names
        # while the chrome around them is not English
        assert page.inner_text(".stage-fieldset legend").startswith("供应链")
    finally:
        context.close()


def test_a_validation_message_is_translated(browser):
    """Error copy is the text somebody reads when they are already stuck."""
    context, page = open_page(browser, ["zh-CN", "en"])
    try:
        page.click('[data-action="start"]')
        page.wait_for_selector("#stage-title")
        press_continue(page)
        page.wait_for_selector(".field-error")
        assert page.inner_text(".field-error") == "请选择浪费发生在食物供应链的哪个环节。"
    finally:
        context.close()


# ---------------------------------------------------------------------------
# The panel, which negotiates on the server
# ---------------------------------------------------------------------------


def _admin_login(browser, accept_language=None, query=""):
    """The panel, through a real browser.

    `urllib` cannot be used here and the reason is E-8: `admin/protection.py`
    refuses a headless client outright, so a raw request to /admin/login
    answers 403 whatever language it asks for. That refusal is itself worth
    knowing about - it is the response most likely to be cached, and it
    carries `Vary` because the middleware that sets it is the outermost one.
    """
    context = browser.new_context(
        extra_http_headers=(
            {"Accept-Language": accept_language} if accept_language else {}
        )
    )
    page = context.new_page()
    response = page.goto(
        f"{BASE}/admin/login{query}", wait_until="domcontentloaded"
    )
    return context, page, response


def test_the_panel_negotiates_from_accept_language_and_says_it_varies(browser):
    """Verified against the RUNNING stack rather than against the application.

    A response can carry `Vary` out of FastAPI and still reach a visitor
    without it if something upstream rewrote the headers, and nginx proxies
    this route. This is the check that would notice.
    """
    context, page, response = _admin_login(browser, "zh-CN,zh;q=0.9,en;q=0.8")
    try:
        assert response.status == 200
        assert "accept-language" in response.headers.get("vary", "").lower()
        assert page.get_attribute("html", "lang") == "zh"
        assert page.inner_text('button[type="submit"], .gate__card button') != ""
        assert context.cookies() == []
    finally:
        context.close()

    context, page, response = _admin_login(browser)
    try:
        assert "accept-language" in response.headers.get("vary", "").lower()
        assert page.get_attribute("html", "lang") == "en-NZ"
    finally:
        context.close()


def test_the_panel_ranks_the_header_by_quality(browser):
    """`zh;q=0.8, en;q=0.9` is a request for English, in that written order."""
    context, page, _ = _admin_login(browser, "zh;q=0.8, en;q=0.9")
    try:
        assert page.get_attribute("html", "lang") == "en-NZ"
    finally:
        context.close()


def test_a_wildcard_at_the_head_is_no_preference_and_renders_english(browser):
    """`*` says "anything", which under v1.26 is a statement that the visitor
    has expressed no preference — and no preference is English.

    Paired with the same wildcard *behind* a real preference, which decides
    nothing: `*` is ranked like any other entry rather than dropped, precisely
    so that it can hold the one slot that matters when it is at the head.
    """
    context, page, _ = _admin_login(browser, "*, zh;q=0.9")
    try:
        assert page.get_attribute("html", "lang") == "en-NZ"
    finally:
        context.close()

    context, page, _ = _admin_login(browser, "zh, *;q=0.9")
    try:
        assert page.get_attribute("html", "lang") == "zh"
    finally:
        context.close()


def test_the_panel_consults_only_the_highest_priority_tag(browser):
    """`fr-CA, fr, zh` reaches **English**, not Chinese (v1.26).

    The same rule as the calculator, on the surface that reads
    `Accept-Language` instead of `navigator.languages`, because two surfaces
    that answered one visitor differently would be the defect.

    Asserted in a pair: the second request is the same header with a supported
    tag at its head, and it still reaches Chinese. Without it this file would
    pass against a panel whose catalogue had stopped loading.
    """
    context, page, _ = _admin_login(browser, "fr-CA,fr;q=0.9,zh;q=0.5")
    try:
        assert page.get_attribute("html", "lang") == "en-NZ"
    finally:
        context.close()

    context, page, _ = _admin_login(browser, "zh-CN,fr;q=0.9,en;q=0.5")
    try:
        assert page.get_attribute("html", "lang") == "zh"
    finally:
        context.close()


def test_the_panel_stores_nothing_and_ignores_an_unrecognised_lang(browser):
    context, page, _ = _admin_login(browser, "en-NZ", query="?lang=zh")
    try:
        assert page.get_attribute("html", "lang") == "zh"
        assert [c for c in context.cookies() if "lang" in c["name"]] == []
    finally:
        context.close()

    context, page, _ = _admin_login(browser, "zh-CN", query="?lang=qq")
    try:
        assert page.get_attribute("html", "lang") == "zh"
    finally:
        context.close()


def test_vary_survives_the_proxy_on_a_response_no_browser_rendered():
    """`Vary` is set by the OUTERMOST middleware, so it reaches responses the
    inner ones short-circuit - and it has to survive nginx, which proxies this
    route.

    A bare urllib request is what `admin/protection.py` calls a headless
    client, so this may come back 403 rather than 200. Either is fine and the
    assertion is the same: the response a shared cache is most likely to hold
    is the one that must not be servable to the next visitor in the wrong
    language.
    """
    request = urllib.request.Request(
        f"{BASE}/admin/login", headers={"Accept-Language": "zh-CN"}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            headers, status = response.headers, response.status
    except urllib.error.HTTPError as refusal:
        headers, status = refusal.headers, refusal.code
    assert status in (200, 403), status
    assert "accept-language" in headers.get("Vary", "").lower(), dict(headers)


def test_the_static_origin_does_not_claim_to_vary():
    """Stated as a test because it looks like an omission and is a decision:
    `Vary` on a near-unique header would fragment a shared cache across every
    asset for a page that does not negotiate at all."""
    with urllib.request.urlopen(f"{BASE}/css/styles.css", timeout=10) as response:
        assert "accept-language" not in response.headers.get("Vary", "").lower()


# ---------------------------------------------------------------------------
# Every catalogue, on a real page
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("language", i18n_keys.catalogue_languages())
def test_every_catalogue_reaches_the_page_it_was_written_for(browser, language):
    """Twenty languages, each asserted against its OWN catalogue entry, inside
    the element that carries it.

    The expectation is read from `web/locales/<lang>.json` by Python and
    compared with what Chromium rendered after fetching the same file over
    HTTP and running `t()` on it - two independent paths to the same string,
    which is what makes this an end-to-end assertion rather than a tautology.

    Parametrised over the manifest rather than over a list here, so a
    twenty-first language is covered by adding its file.
    """
    catalogue = i18n_keys.catalogue(language)
    context, page = open_page(browser, [language, "en"])
    try:
        assert page.get_attribute("html", "lang") == language
        assert page.get_attribute("html", "dir") == catalogue.get("dir", "ltr")
        assert (
            page.inner_text("h1#page-title")
            == catalogue["strings"]["Food Waste Impact Calculator"]
        )
        assert (
            page.inner_text('[data-action="start"]')
            == catalogue["strings"]["Start calculator"]
        )
        # Static HTML, translated by `applyToDocument` rather than by a render.
        assert (
            page.inner_text(".skip-link") == catalogue["strings"]["Skip to calculator"]
        )
        # An attribute, which a text-only walk would miss.
        assert (
            page.get_attribute("#home-button", "aria-label")
            == catalogue["strings"][
                "Clear calculator data and return to the introduction"
            ]
        )
        # `<title>`, which is neither text nor attribute.
        assert page.title() == catalogue["strings"][
            "Food Waste Impact Calculator | Kai Commitment"
        ]
        # And the notice says what the catalogue says about itself.
        expected = 1 if catalogue["machine_translated"] else 0
        assert page.locator("#machine-translation-notice").count() == expected
    finally:
        context.close()


# ---------------------------------------------------------------------------
# The panel's chooser, driven with scripting switched OFF
# ---------------------------------------------------------------------------


def _gate(browser, scripting, width=1280, accept_language="en-NZ"):
    """The panel's login page, which is where the chooser can be driven
    without credentials.

    Deliberately the gate rather than a signed-in screen. It is the page a
    person who does not read English needs most, it is the one page a
    locked-out account can still reach, and reaching it costs no password -
    a wrong one charges the shared login throttle and locks an account for
    fifteen minutes, so no test here may guess at one.

    `urllib` cannot be used against this path at all: admin/protection.py
    refuses headless clients, which is why this goes through a real browser.
    """
    context = browser.new_context(
        viewport={"width": width, "height": 800},
        java_script_enabled=scripting,
        extra_http_headers={"Accept-Language": accept_language},
    )
    page = context.new_page()
    page.goto(f"{BASE}/admin/login", wait_until="domcontentloaded")
    return context, page


@pytest.mark.parametrize("width", [390, 1280])
def test_the_panels_chooser_works_with_scripting_off(browser, width):
    """**The whole reason the panel's chooser is a form.**

    Driven with `java_script_enabled=False`, so nothing here can be carried by
    a change handler: the visitor picks an option, presses a real button, and
    the server answers. Then a reload and a second page, because a control that
    only appears to work until you navigate is the defect this is guarding.

    Run at 390px as well as desktop - a control that overflows a phone is not a
    control - and finished by returning to "Follow the system" and confirming it
    reverts rather than sticking.
    """
    context, page = _gate(browser, scripting=False, width=width)
    try:
        chooser = page.locator("#language-chooser")
        assert chooser.count() == 1, "no chooser on the gate page"
        box = chooser.bounding_box()
        assert box["height"] >= 44, f"{width}px: below the touch target: {box}"
        assert box["x"] >= 0 and box["x"] + box["width"] <= width + 1, (
            f"{width}px: the chooser is off screen: {box}"
        )
        assert page.locator("form.language-bar button[type=submit]").count() == 1

        # English to begin with, because that is what the browser asked for.
        assert "Sign in" in page.content() or "Continue" in page.content()

        # Pick Chinese and submit. No script is involved in either step.
        page.select_option("#language-chooser", "zh")
        page.click("form.language-bar button[type=submit]")
        page.wait_for_load_state("domcontentloaded")
        assert "登录" in page.content(), (
            f"{width}px: the panel did not change language without scripting"
        )

        stored = [c for c in context.cookies() if c["name"] == "kaicalc_lang"]
        assert len(stored) == 1 and stored[0]["value"] == "zh"
        assert stored[0]["path"] == "/", "the calculator could not read it"

        # It survives a reload...
        page.reload(wait_until="domcontentloaded")
        assert "登录" in page.content()
        # The chooser shows the choice it is honouring, not the first option.
        assert 'value="zh" lang="zh" selected' in page.content()

        # ...and a different page of the panel. /admin/security is behind the
        # session, so its refusal is what a signed-out visitor gets - and the
        # refusal has to be readable too.
        page.goto(f"{BASE}/admin/security", wait_until="domcontentloaded")
        assert "登录" in page.content(), (
            f"{width}px: a second page of the panel lost the language"
        )

        # Back to following the system, which is a write and not a deletion.
        page.goto(f"{BASE}/admin/login", wait_until="domcontentloaded")
        page.select_option("#language-chooser", "auto")
        page.click("form.language-bar button[type=submit]")
        page.wait_for_load_state("domcontentloaded")
        assert "登录" not in page.content(), "follow-the-system did not revert"
        reverted = [c for c in context.cookies() if c["name"] == "kaicalc_lang"]
        assert len(reverted) == 1 and reverted[0]["value"] == "auto", (
            "follow-the-system deleted the cookie instead of writing it"
        )
    finally:
        context.close()


def test_the_panel_names_a_language_it_cannot_render(browser):
    """Tamil chosen on the calculator, then the panel opened.

    The cookie is set through the calculator's own origin - the same cookie,
    which is the point - and the panel is then asked for. It must render
    English, name தமிழ், offer no Tamil option, and **leave the cookie alone**.
    """
    context = browser.new_context(extra_http_headers={"Accept-Language": "en-NZ"})
    try:
        page = context.new_page()
        page.goto(f"{BASE}/index.html", wait_until="networkidle")
        _choose(page, "ta")
        assert [c["value"] for c in context.cookies() if c["name"] == "kaicalc_lang"] == ["ta"]

        page.goto(f"{BASE}/admin/login", wait_until="domcontentloaded")
        body = page.content()
        assert "தமிழ்" in body, "the panel did not name the language it lacks"
        assert 'value="ta"' not in body, "a dead Tamil entry is in the list"
        assert "登录" not in body, "the panel should be English here"

        after = [c["value"] for c in context.cookies() if c["name"] == "kaicalc_lang"]
        assert after == ["ta"], (
            f"the panel rewrote the shared cookie to {after}; the calculator's "
            "language has been destroyed from an unrelated screen"
        )
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1280])
def test_choosing_a_right_to_left_language_survives_and_reverts(browser, width):
    """The owner's acceptance run, on the calculator, in Arabic, at both widths.

    Every other test here either chooses a language OR checks the mirroring.
    This does both in one session, because the interesting failures live in the
    join: a chooser that mirrors on a page the browser negotiated into Arabic
    but not on one the visitor chose into Arabic, or a stored right-to-left
    choice that survives a reload and loses `dir` on the second page.

    Ends by returning to "Follow the system" and confirming the page comes back
    to English on an English browser - a revert that leaves `dir="rtl"` behind
    is the failure this last third exists for.
    """
    context = browser.new_context(
        viewport={"width": width, "height": 800},
        extra_http_headers={"Accept-Language": "en-NZ"},
    )
    try:
        page = context.new_page()
        # The shim is not optional here. `extra_http_headers` does not touch
        # `navigator.languages`, and this machine's own locale is Chinese - so
        # without it the revert at the end follows the real browser to Chinese
        # and the test fails on correct behaviour. `open_page` exists for this
        # reason; it is inlined because this test needs its own viewport.
        page.add_init_script(
            _LANGUAGES_SHIM.format(
                languages=json.dumps(["en-NZ"]), first=json.dumps("en-NZ")
            )
        )
        page.goto(f"{BASE}/index.html", wait_until="networkidle")
        assert page.get_attribute("html", "dir") == "ltr"
        assert page.get_attribute("html", "lang") == "en-NZ"

        _choose(page, "ar")
        assert page.get_attribute("html", "dir") == "rtl"
        assert page.get_attribute("html", "lang") == "ar"

        # Survives a reload.
        page.reload(wait_until="networkidle")
        assert page.get_attribute("html", "dir") == "rtl"
        assert page.eval_on_selector("#language-chooser", "el => el.value") == "ar"

        # Survives navigation to another page.
        page.goto(f"{BASE}/methodology.html", wait_until="networkidle")
        assert page.get_attribute("html", "dir") == "rtl"
        assert page.eval_on_selector("#language-chooser", "el => el.value") == "ar"

        # The control is still reachable and still at the reading-END edge
        # (v1.47), which in a mirrored page is the physical LEFT - a mirrored
        # page that puts its own language control off screen is the trap this
        # whole parametrisation is for, and `onScreen` below is what guards it
        # whichever edge the control sits at.
        geometry = page.evaluate(
            """(w) => {
              const el = document.getElementById('language-chooser');
              const b = el.getBoundingClientRect();
              return {onScreen: b.left >= 0 && b.right <= w + 1,
                      height: Math.round(b.height),
                      fromRight: Math.round(w - b.right),
                      fromLeft: Math.round(b.left)};
            }""",
            width,
        )
        assert geometry["onScreen"], f"{width}px: off screen in rtl: {geometry}"
        assert geometry["height"] >= 44, geometry
        assert geometry["fromLeft"] < geometry["fromRight"], (
            f"{width}px: not at the reading-end edge in rtl: {geometry}"
        )
        # STILL not asserted here, and now for the opposite reason. The
        # sideways scroll this used to record is fixed - see
        # tests/web/test_horizontal_overflow.py, which owns the measurement
        # for both pages in both directions. It is not pulled back in here:
        # this is a chooser test, and a chooser test that fails because a
        # factor set's notes grew a long URL teaches the next reader to delete
        # the assertion.
        #
        # The record it replaced was half wrong and the correction is worth
        # keeping. It read "in Arabic at 390px"; measured, the page overflowed
        # by 485px in Arabic and 480px in English, so it was never an RTL
        # defect. Nor were the published-formulas table and the definition list
        # both at fault - the table sits in an `overflow-x: auto` container and
        # was always clipped. One `<dd>` holding a 124-character URL was the
        # whole of it.

        # And back again.
        _choose(page, "auto")
        assert page.get_attribute("html", "dir") == "ltr", (
            "reverting left the document in right-to-left"
        )
        assert page.get_attribute("html", "lang") == "en-NZ"
        stored = [c for c in context.cookies() if c["name"] == "kaicalc_lang"]
        assert len(stored) == 1 and stored[0]["value"] == "auto"
    finally:
        context.close()


# ---------------------------------------------------------------------------
# The three content pages: statistics, home and documentation
#
# They shipped in English at v1.29 and were translated at v1.30. Everything
# below is asserted against `web/locales/<lang>.json` read by Python, inside the
# element that carries it, in two languages - one of them right-to-left.
# ---------------------------------------------------------------------------

#: page path -> (selector, catalogue key) for a heading only that page renders.
#: `/home.html` is NOT here. It is retired - nothing links to it - so it is not a
#: content page, and asserting its navigation would assert a route that no longer
#: exists. Its strings are still translated in every catalogue, which
#: `tests/web/test_i18n_web.py` covers from the file rather than from the route.
CONTENT_PAGES = {
    "/stats.html": ("h1#stats-title", "Statistics from this tool"),
    "/methodology.html": (
        "h1#documentation-title",
        "Methodology and published factors",
    ),
}

#: Three links, not four: `Home` went with the retired page.
_NAV_HREF = {
    "Calculator": "index.html",
    "Statistics": "stats.html",
    "Documentation": "methodology.html",
}

_PAGE_TITLE = {
    "/stats.html": "Statistics | Kai Commitment Food Waste Impact Calculator",
    "/methodology.html": "Documentation | Kai Commitment Food Waste Impact Calculator",
}

#: The footer notice every public page carries. **Reworded in stage three's fix
#: round**: it renders in `index.html`'s footer, which is the calculator, so a
#: visitor met it on the results page a few centimetres above the contribute
#: control - reading that their calculation was already in the aggregate
#: statistics and then being asked to opt in to exactly that. `tests/web/
#: test_consent_copy.py` holds the rule; this constant is the rendered half.
#: **Reworded again in v1.59**, to name the food item `submission_entry.
#: food_item_id` has held since v1.58 (contract §7.3c). This constant is a
#: hand-copy of a sentence that lives in four pages, and it drifted the moment
#: the source moved -- caught only because the catalogue lookup below raises
#: `KeyError` on a key no catalogue carries any more, which is the one thing
#: standing between a retyped constant and a test that silently stops
#: describing the page it is named for.
_TRANSPARENCY = (
    "This calculator stores the sector, food category, the specific food where "
    "you name one, and the quantities entered. They join the public statistics "
    "only if you choose to offer them, and nothing that identifies you or your "
    "business is stored."
)


@pytest.mark.parametrize("path,heading", sorted(CONTENT_PAGES.items()))
@pytest.mark.parametrize("language", ["zh", "ar"])
def test_the_content_pages_translate_their_own_prose(browser, path, heading, language):
    """One string only this page has, plus the chrome every page shares.

    Two languages, because a single one passes against a page hard-coded in it,
    and Arabic because it is also the direction check. Every expectation is read
    out of the catalogue file by Python and compared with what Chromium rendered
    after fetching the same file over HTTP, so this cannot agree with itself.
    """
    selector, key = heading
    catalogue = i18n_keys.catalogue(language)
    strings = catalogue["strings"]
    context, page = open_page(browser, [language, "en"], path=path)
    try:
        assert page.get_attribute("html", "lang") == language
        assert page.get_attribute("html", "dir") == catalogue.get("dir", "ltr")
        assert page.inner_text(selector) == strings[key]
        # The four navigation links, which the key extractor lost until v1.30
        # because each sits inside an element that is itself marked.
        for label, href in _NAV_HREF.items():
            assert page.inner_text('.public-nav a[href$="%s"]' % href) == strings[
                label
            ], "%s: the %s link did not translate" % (path, label)
        # The footer sentence, which is a `<span>` beside a link precisely so
        # that translating it cannot delete the link.
        assert page.inner_text(".transparency-notice span") == strings[_TRANSPARENCY]
        assert page.inner_text(".transparency-notice a") == strings["What we record"]
        # `<title>`, which is neither text nor attribute.
        assert page.title() == strings[_PAGE_TITLE[path]]
    finally:
        context.close()


@pytest.mark.parametrize("language", ["zh", "ar"])
def test_the_statistics_summary_is_translated_around_its_figure(browser, language):
    """The one string on these pages that shipped English after the first pass.

    It reads "Across 1,247 calculations contributed to this tool.", and the
    figure sits in a `<strong>` inside the sentence. That is built by splitting
    the translation on its placeholder, and the first version passed the
    **key** to the helper that splits - so the literal was an argument to that
    helper rather than to `t()`, `tests/web/i18n_keys.py` never extracted it,
    no catalogue was required to carry it, and the headline of the statistics
    page rendered in English on an Arabic screen with the whole suite green.
    It was found by looking at a screenshot.

    So it is asserted here, in two languages, against the catalogue's own entry
    with the placeholder filled the way the page fills it - and the figure is
    checked to be still inside its `<strong>`, because a fix that translated the
    sentence by dropping the emphasis would be a different regression.
    """
    strings = i18n_keys.catalogue(language)["strings"]
    expected = strings["Across %(count)s calculations contributed to this tool."].replace(
        "%(count)s", "1,247"
    )
    context, page = open_page(
        browser, [language, "en"], path="/stats.html", stats_fixture=True
    )
    try:
        page.wait_for_selector(".stats-calculation-total")
        assert page.inner_text(".stats-calculation-total") == expected
        assert page.inner_text(".stats-calculation-total strong") == "1,247", (
            "the figure is no longer emphasised inside the sentence"
        )
        # The two sentences under it, which are ordinary `t()` calls and would
        # not have caught the defect above on their own.
        assert page.inner_text(".stats-breakdown-note >> nth=0") == strings[
            "Share of destination entries across calculations contributed to this tool."
        ]
    finally:
        context.close()


@pytest.mark.parametrize("path", sorted(CONTENT_PAGES))
def test_the_notice_and_the_chooser_reach_every_content_page(browser, path):
    """The gap this batch was written to close.

    A Thai session got English statistics, home and documentation pages with
    nothing on them saying so and no way to change it, while the calculator one
    link away had both. So: the notice is the first element in the body, it is
    written in Thai and again in English, and the chooser is on the page, big
    enough to touch and inside the viewport.
    """
    strings = i18n_keys.catalogue("th")["strings"]
    notice = strings[
        "This interface was machine translated and has not been reviewed by a "
        "speaker of this language. The figures are unaffected; the wording may "
        "be wrong."
    ]
    context, page = open_page(browser, ["th"], path=path)
    try:
        assert (
            page.evaluate("() => document.body.firstElementChild?.id")
            == "machine-translation-notice"
        ), "%s: the notice is not the first element in the body" % path
        assert notice in page.inner_text("#machine-translation-notice"), (
            "%s: the notice is not in Thai" % path
        )
        # And in English underneath, because the sentence warning you about a
        # machine pass went through the same machine as the rest of the file.
        assert "machine translated" in page.inner_text(
            "#machine-translation-notice .machine-translation-notice-en"
        )

        box = page.locator("#language-chooser").bounding_box()
        assert box is not None, "%s: no chooser" % path
        assert box["height"] >= 44, "%s: below the 44px touch target: %s" % (path, box)
        assert box["y"] < page.evaluate("window.innerHeight"), (
            "%s: the chooser is below the fold" % path
        )
        assert (
            page.evaluate(
                "() => document.querySelector('label[for=\"language-chooser\"]')"
                "?.textContent.trim()"
            )
            == strings["Language"]
        )
    finally:
        context.close()


def test_chinese_carries_no_notice_on_the_content_pages_either(browser):
    """Chinese has readers on this project, so it is not warned about.

    The negative is asserted on these three pages as well, or "the notice
    reaches every page" would be satisfied by a page that showed it
    unconditionally.
    """
    for path in CONTENT_PAGES:
        context, page = open_page(browser, ["zh"], path=path)
        try:
            assert page.locator("#machine-translation-notice").count() == 0, path
            assert page.locator("#language-chooser").count() == 1, path
        finally:
            context.close()


# ---------------------------------------------------------------------------
# The charts, which `applyToDocument()` cannot reach
# ---------------------------------------------------------------------------

_CHART_STATE = """
() => [...document.querySelectorAll('.stats-chart-region canvas')].map((canvas) => {
  const chart = window.Chart.getChart(canvas);
  return {
    id: chart?.id,
    title: chart?.titleBlock?.options?.text,
    titleHeight: chart?.titleBlock?.height,
    legendShown: chart?.options?.plugins?.legend?.display !== false,
    legend: (chart?.legend?.legendItems || []).map((item) => item.text),
    xTicks: (chart?.scales?.x?.ticks || []).map((tick) => tick.label),
    aria: canvas.getAttribute('aria-label'),
  };
})
"""

_CHART_TITLES = (
    "Destinations entered (share)",
    "Sectors selected (share)",
    "Food categories selected (share)",
)

_BREAKDOWN_TITLES = (
    "Destinations entered",
    "Sectors selected",
    "Food categories selected",
)

_CANVAS_LABEL = (
    "%(title)s, charted using the API-provided share for every published "
    "bucket. The full values follow in a text list."
)


def _wait_for_charts(page):
    page.wait_for_function(
        """() => [...document.querySelectorAll('.stats-chart-region canvas')]
             .filter((canvas) => window.Chart.getChart(canvas)).length === 3"""
    )


def test_the_charts_are_rebuilt_in_the_new_language(browser):
    """**The second translation path, driven and measured rather than mocked.**

    A Chart.js title and a canvas `aria-label` are arguments to a constructor.
    Nothing `applyToDocument()` does can reach them - the title is painted onto
    a bitmap - so changing language has to destroy each chart and build it
    again, and "a function was called" is not evidence that it did. What is
    asserted here:

    * the title read back off the live chart's **laid-out title block**, whose
      measured height is what makes it a drawn title rather than a stored
      string, equals the Chinese catalogue's own entry;
    * every `Chart` instance id changed, so these are new charts;
    * the canvases the first charts were drawn on are **destroyed** - the
      registry no longer knows them - rather than left behind the new ones;
    * and the same titles again in Arabic, so this cannot pass against one
      hard-coded language.
    """
    zh = i18n_keys.catalogue("zh")["strings"]
    ar = i18n_keys.catalogue("ar")["strings"]
    context, page = open_page(
        browser, ["en-NZ"], path="/stats.html", stats_fixture=True
    )
    try:
        _wait_for_charts(page)
        english = page.evaluate(_CHART_STATE)
        assert [chart["title"] for chart in english] == list(_CHART_TITLES), english
        assert all(chart["titleHeight"] > 0 for chart in english), english

        # A handle on the canvases the first charts were drawn on, so their
        # destruction can be asserted after they have left the document.
        first_canvases = page.evaluate_handle(
            "() => [...document.querySelectorAll('.stats-chart-region canvas')]"
        )

        _choose(page, "zh")
        _wait_for_charts(page)
        chinese = page.evaluate(_CHART_STATE)
        assert [chart["title"] for chart in chinese] == [
            zh[title] for title in _CHART_TITLES
        ], chinese
        assert all(chart["titleHeight"] > 0 for chart in chinese), chinese
        assert [chart["aria"] for chart in chinese] == [
            zh[_CANVAS_LABEL].replace("%(title)s", zh[title])
            for title in _BREAKDOWN_TITLES
        ], chinese

        assert all(
            new["id"] != old["id"] for new, old in zip(chinese, english)
        ), "the charts were not rebuilt: %s -> %s" % (english, chinese)
        assert page.evaluate(
            "(canvases) => canvases.every("
            "(canvas) => window.Chart.getChart(canvas) === undefined)",
            first_canvases,
        ), "the first charts were never destroyed"

        _choose(page, "ar")
        _wait_for_charts(page)
        arabic = page.evaluate(_CHART_STATE)
        assert [chart["title"] for chart in arabic] == [
            ar[title] for title in _CHART_TITLES
        ], arabic
    finally:
        context.close()


def _taxonomy(charts):
    """Every place a chart puts an API `label` on screen.

    **The drawn legend only, plus the category axis.** A bar chart here sets
    `legend.display: false`, and Chart.js still computes `legendItems` for it -
    holding the *dataset* label, which is this page's own translated chart
    title and is supposed to change. Comparing those would fail on correct
    behaviour and teach the next reader to delete the assertion. What carries a
    staff-typed name is the doughnut's visible legend and the bar's x-axis
    ticks.

    **All three breakdowns are doughnuts as of the shares-as-shares change**,
    so `xTicks` is `[]` for every chart here today - there is no bar on this
    page to carry one. It stays in the tuple rather than being dropped: a
    future breakdown of impact *values* (which can be negative, so it would
    need `renderBar`, not `renderDonut` - see the note above `BREAKDOWNS`)
    would put a category axis back on this page, and this comparison should
    hold it to the same rule without anyone having to remember to re-add it.
    """
    return [
        (chart["legend"] if chart["legendShown"] else [], chart["xTicks"])
        for chart in charts
    ]

def test_the_bucket_labels_stay_in_the_language_staff_typed_them(browser):
    """Section 7.7.7, on the surface where breaking it would be easiest.

    Every label in a legend and in the text list is `label` from the statistics
    response - a destination, sector or food-category name a staff member typed
    into the panel, published exactly as written. They must be **identical** in
    English and in Chinese: a legend that changed with the language would mean
    the front end had started translating the client's taxonomy.
    """
    context, page = open_page(
        browser, ["en-NZ"], path="/stats.html", stats_fixture=True
    )
    try:
        _wait_for_charts(page)
        english = page.evaluate(_CHART_STATE)
        listed = page.eval_on_selector_all(
            ".stats-breakdown-list li strong", "nodes => nodes.map(n => n.textContent)"
        )
        assert any(chart["legendShown"] and chart["legend"] for chart in english), (
            "no chart drew a legend, so this measures nothing"
        )
        # Not `assert any(chart["xTicks"] ...)`: all three breakdowns are
        # doughnuts today (see `_taxonomy`'s note), so no chart on this page
        # has a category axis to draw one, and `_taxonomy` compares `[]` to
        # `[]` for that half until a bar returns to this page.
        assert listed, "the text list is empty, so this measures nothing"

        _choose(page, "zh")
        _wait_for_charts(page)
        chinese = page.evaluate(_CHART_STATE)
        assert _taxonomy(chinese) == _taxonomy(english), (
            "a taxonomy label changed with the language; the taxonomy is not ours "
            "to translate"
        )
        assert (
            page.eval_on_selector_all(
                ".stats-breakdown-list li strong",
                "nodes => nodes.map(n => n.textContent)",
            )
            == listed
        )

        # And the prose around them did change, or the assertion above would
        # pass just as well against a page that ignored the language entirely.
        assert page.inner_text("h1#stats-title") == (
            i18n_keys.catalogue("zh")["strings"]["Statistics from this tool"]
        )
    finally:
        context.close()


def test_the_figures_take_no_locale_aware_separator(browser):
    """Section 7.7.7: figures are not localised, on either surface.

    **Read in Arabic, and asserted positively rather than as an absence.** The
    obvious form of this test - "no Eastern Arabic numeral appears" - was
    written first and passed against a deliberately locale-aware build, because
    Chromium's default numbering system for a bare `ar` is `latn`: a mutated
    `toLocaleString(document.documentElement.lang, ...)` produced
    `'0.0‎%‎'`, Western digits wrapped in left-to-right marks. An
    absence check cannot see that, so this one states what the label must be:
    exactly the `en-NZ` rendering, with nothing around it.

    The masses are checked the other way round again - against the strings the
    service actually sent, trailing zero included - because those cross the wire
    as decimals and are printed rather than formatted (section 1.2).

    **No bar chart draws a y axis on this page any more** - all three
    breakdowns are doughnuts as of the shares-as-shares change - so the figure
    to check for locale-aware formatting is read from a doughnut tooltip
    instead. Every doughnut on this page shares the one `sharePercent`
    formatter with the text list (`stats.js::createChart`'s `formatValue`), so
    a doughnut's tooltip is exactly as good a witness to a mis-localised digit
    as the old bar's axis tick was.
    """
    en_nz_percent = re.compile(r"[0-9]+(\.[0-9]+)?%")
    context, page = open_page(browser, ["ar"], path="/stats.html", stats_fixture=True)
    try:
        _wait_for_charts(page)
        assert page.get_attribute("html", "dir") == "rtl", (
            "this is not the right-to-left rendering, so it measures nothing"
        )
        tooltips = page.evaluate(
            """() => [...document.querySelectorAll('.stats-chart-region canvas')]
                 .map((canvas) => window.Chart.getChart(canvas))
                 .filter((chart) => chart && chart.config.type === 'doughnut')
                 .map((chart) => chart.options.plugins.tooltip.callbacks.label({
                   label: 'Example', parsed: 0.379,
                 }))"""
        )
        assert tooltips, "no doughnut drew a tooltip, so this measures nothing"
        for tooltip in tooltips:
            match = en_nz_percent.search(tooltip)
            assert match and match.group(0) == "37.9%", (
                "the tooltip is being formatted for the active locale: %r" % tooltip
            )

        listed = page.inner_text(".stats-breakdown-list")
        shares = en_nz_percent.findall(listed)
        assert shares, "the text list states no share, so nothing is being compared"
        for bucket in _STATS_FIXTURE["by_destination"]:
            assert "%s kg" % bucket["total_kg"] in listed, (
                "the mass %r was reformatted rather than printed as sent"
                % bucket["total_kg"]
            )
    finally:
        context.close()


# ---------------------------------------------------------------------------
# The language machinery must not be contingent on any one section of a page
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("language", ["zh", "ar"])
def test_a_home_page_with_no_news_section_still_translates(browser, language):
    """The rule, separated from the accident of ordering that used to satisfy it.

    **This is the one test that still drives `home.html`, and that is on purpose.**
    The page is retired: nothing links to it, `/` serves the calculator, and it is
    kept only because the client has not decided about the news feed. A retired page
    with no test on it is a page that quietly stops working, so this one keeps its
    language machinery honest at the URL it is still served from. If `home.html` is
    ever deleted, this test goes with it.

    ``web/js/home.js`` once installed the chooser, set ``<html lang>`` and ran the
    ``data-i18n`` walk *inside* ``if (document.querySelector('#news-feed'))``. On the
    shipped markup that always passed — the feed element is parsed into the document
    and it is ``loadNews`` that removes it afterwards — so no test on the shipped page
    could tell the two arrangements apart, and an unconfigured deployment kept its
    chooser by luck rather than by design.

    So the page is served here **without a news section at all**, by rewriting the
    response body on the way through. That is not a hypothetical document: it is what
    the module would face the first time anybody edits the news block out of
    ``home.html`` for a deployment that has no WordPress, and the failure it produces
    is silent and total — an English page announced as ``en-NZ`` with no control to
    change it.

    Restore the guard around those three calls and this fails; nothing else in the
    suite does.
    """
    catalogue = i18n_keys.catalogue(language)
    strings = catalogue["strings"]
    source = (Path(__file__).resolve().parents[2] / "web" / "home.html").read_text(
        encoding="utf-8"
    )
    stripped = re.sub(
        r'<section class="home-news".*?</section>', "", source, flags=re.S
    )
    assert "home-news" not in stripped and stripped != source, (
        "the news section could not be removed from home.html, so this test would "
        "measure the ordinary page and prove nothing"
    )

    context = browser.new_context(
        extra_http_headers={"Accept-Language": "%s,en" % language}
    )
    page = context.new_page()
    page.add_init_script(
        _LANGUAGES_SHIM.format(
            languages=json.dumps([language, "en"]), first=json.dumps(language)
        )
    )
    page.route(
        "**/home.html",
        lambda route: route.fulfill(
            status=200, content_type="text/html; charset=utf-8", body=stripped
        ),
    )
    try:
        page.goto("%s/home.html" % BASE, wait_until="networkidle")
        page.wait_for_timeout(400)
        assert page.locator("#news-feed").count() == 0, (
            "the rewrite did not take; this is the ordinary page"
        )
        assert page.get_attribute("html", "lang") == language
        assert page.get_attribute("html", "dir") == catalogue.get("dir", "ltr")
        assert page.inner_text("h1#home-title") == strings[
            "Turn food waste information into action"
        ], "a home page with no news section was left in English"
        chooser = page.locator("#language-chooser")
        assert chooser.count() == 1 and chooser.is_visible(), (
            "a home page with no news section has no language chooser, so a reader "
            "who cannot read it has no way out"
        )
    finally:
        context.close()


# ---------------------------------------------------------------------------
# The header's own arrangement, which is what this change is for
#
# Everything above measures the CHOOSER. Nothing measured the brand, and the
# brand moving to the reading-start edge is the whole point of the header
# change - so a rewrite that put the logo anywhere at all would have passed
# every geometry test in this file.


def _sized_page(browser, languages, width, height=700):
    """A page at a fixed viewport, claiming `languages`.

    `open_page` above takes no size, and these two measurements are about what
    happens when the header runs out of room - which is a question that only
    exists at a width.
    """
    context = browser.new_context(
        viewport={"width": width, "height": height},
        extra_http_headers={"Accept-Language": ",".join(languages)},
    )
    page = context.new_page()
    page.add_init_script(
        _LANGUAGES_SHIM.format(
            languages=json.dumps(languages), first=json.dumps(languages[0])
        )
    )
    page.goto(f"{BASE}/index.html", wait_until="networkidle")
    return context, page


def _boxes(page, *selectors):
    return [page.locator(selector).first.bounding_box() for selector in selectors]


def test_the_brand_sits_at_the_reading_start_edge(browser):
    """**The assertion this change exists to make, and the one nothing made.**

    Every other geometry test in this file is about the chooser: where it sits,
    how tall it is, whether it mirrors. The brand was never measured, so a
    header rewrite could have left the logo anywhere - centred, at the reading
    end, stacked under the chooser - with the whole file still green.

    Measured against the row's own edges rather than the viewport's, the same
    way the mirroring test does it, so that changing the header's padding does
    not require editing this.
    """
    for languages, direction in ((["en-NZ"], "ltr"), (["ar"], "rtl")):
        context, page = _sized_page(browser, languages, 1280)
        try:
            seen = page.evaluate(
                """() => {
                  const row = document.querySelector('.public-header-inner');
                  const brand = document.querySelector('.brand');
                  const r = row.getBoundingClientRect();
                  const b = brand.getBoundingClientRect();
                  return {
                    dir: document.documentElement.dir,
                    gapLeft: Math.round(b.left - r.left),
                    gapRight: Math.round(r.right - b.right),
                  };
                }"""
            )
            assert seen["dir"] == direction
            if direction == "ltr":
                assert seen["gapLeft"] < seen["gapRight"], (
                    f"the brand is not at the reading-start edge in {direction}: {seen}"
                )
            else:
                #: Arabic mirrors, so reading-start is the physical right. A
                #: rule written with `left` rather than `inset-inline-start`
                #: passes the English case and fails here, which is the whole
                #: reason both directions are driven.
                assert seen["gapRight"] < seen["gapLeft"], (
                    f"the brand did not mirror in {direction}: {seen}"
                )
        finally:
            context.close()


def test_the_clear_action_never_lands_on_top_of_the_logo(browser):
    """**Two grid items naming one cell are stacked, not reflowed.**

    At 560px and below `.public-header-inner` is a single column, so
    `grid-column: 1; grid-row: 2` on both the brand and the clear action puts
    them in the same cell - and `justify-self: start` and `end` then slide them
    toward each other until they meet. At 320px the track is about 280px, the
    wordmark is 174px and Spanish's "Borrar todos los datos" is around 165px:
    they overlap, and the button is later in the DOM so it wins the hit test.
    Tapping the right-hand end of the logo opens "clear all data?".

    Spanish because it is the longest of the labels the fixture languages
    carry; the defect is proportional to that width, and English alone leaves
    enough room to hide it.

    **Driven on a wizard step with data, which is the part that makes this a
    real test.** `calculator.js` sets `clearButton.hidden = !hasData()`, so on
    the intro screen the button is `display: none` and not a grid item at all -
    every existing browser test in this file stays on that screen, which is why
    a defect in three placement rules could ship with the suite green.
    """
    context, page = _sized_page(browser, ["es"], 320)
    try:
        page.click('[data-action="start"]')
        page.wait_for_selector('input[name="sector"]')
        page.evaluate("document.querySelector('input[name=sector]').click()")
        page.wait_for_timeout(80)

        clear = page.locator("#clear-button")
        assert clear.is_visible(), (
            "the clear action is still hidden - `hasData()` did not become true, "
            "so this test would measure nothing"
        )

        brand, button = _boxes(page, ".brand", "#clear-button")
        assert brand and button

        overlap_x = min(brand["x"] + brand["width"], button["x"] + button["width"]) - max(
            brand["x"], button["x"]
        )
        overlap_y = min(brand["y"] + brand["height"], button["y"] + button["height"]) - max(
            brand["y"], button["y"]
        )
        assert overlap_x <= 0 or overlap_y <= 0, (
            f"the clear action overlaps the logo by {overlap_x:.0f}x{overlap_y:.0f}px "
            f"- brand {brand}, button {button}"
        )
    finally:
        context.close()
