"""No page scrolls sideways, measured rather than reasoned about.

**`scrollWidth` against `clientWidth`, in a real browser, at a real width.**
A stylesheet can be read and reasoned about all day and still produce a page
that scrolls; this repository has twice shipped layout defects that every
markup test passed - a table whose scrollbar was drawn 4,500px below the fold,
and a dialog whose submit button was invisible. The only thing that settles a
layout question is measuring the rendered box.

**THIS SUITE RUNS WITH REAL SCROLLBARS, AND FOR TWO YEARS IT DID NOT.** Playwright
launches headless Chromium with `--hide-scrollbars`. With that flag the vertical
scrollbar is not drawn and takes no width, so `documentElement.clientWidth` equals
`window.innerWidth` exactly - measured here as 390 against 390. On a desktop
browser that draws a classic scrollbar the same page reports 375 against 390, and
the 15px between them is where the single most common cause of horizontal overflow
lives: `100vw`, `50vw`, `100vmax` and every expression built on them count the
scrollbar, while the `100%` they are subtracted from does not. The full-bleed
idiom `margin-inline: calc(50% - 50vw)` is that arithmetic exactly, it shipped to
`main` on the home page, and every assertion in this file measured **zero** against
it. A test that reads zero because it cannot see is worse than no test: it is a
green light over the defect.

So the fixture below passes `ignore_default_args=["--hide-scrollbars"]`, and
`test_the_browser_is_actually_drawing_a_scrollbar` is what stops that silently
lapsing again - if a Playwright upgrade, a flag rename or a headless-mode change
puts the scrollbar back into hiding, that test fails by name instead of this whole
file quietly going blind a second time.

**Widths and languages both grew with it**, because the flag was not the only thing
this file could not see. It ran at 320 and 390 in Arabic and English: two phone
widths, at which a real browser uses an *overlay* scrollbar anyway, and two
languages that happen not to produce long unbreakable words. Neither of the two
overflows found in the merge that prompted this was reachable from there - one was
`#stats-title { white-space: nowrap }` at 768px in German, one was a `50vw` bleed at
every width on a desktop. 768 and 1278 are now measured because that is where a
classic scrollbar actually is, and German is measured because it is the language in
this catalogue set that builds unbreakable compounds.

**The defect this file was written for, and the correction it records.** It was
reported as "the methodology page scrolls sideways in Arabic at 390px". The
Arabic half is wrong: measured at 390px the page overflowed by 485px in Arabic
and by 480px in English, which is the same defect seen twice and not an RTL
defect at all. What actually overflowed was a `<dd>` in the factor-set summary
holding the published ReFED set's notes, which cite a 124-character source URL.
A URL contains no space, so its min-content width is its full width, and a flex
item's default `min-width: auto` means "never shrink below min-content" - one
token widened a 310px list to 768px and pushed the document to 875px.

So the assertions below are run in an RTL language AND in English, because a
fix that only held in one of them would not be a fix.

Requires the stack rebuilt: `docker compose -f docker/compose.yaml up -d
--build web admin`. Skipped rather than failed when it is not up.
"""

from __future__ import annotations

import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.browser

BASE = "http://localhost:18080"

#: The width in the report. A 390px viewport is an iPhone 12/13/14, which is
#: the single most common phone width in New Zealand traffic; 320px is kept
#: alongside it as the narrowest width anything still ships at, because a fix
#: that only holds at the width it was reported at is a fix for one phone.
#:
#: **768 and 1278 are here because that is where a scrollbar is.** A phone draws
#: an overlay scrollbar that takes no width; a desktop browser draws a classic
#: one that does, and a `100vw` bleed overflows by its width at EVERY viewport
#: size rather than only at narrow ones. Measuring only phone widths meant the
#: two widths where the flag in the fixture below makes a difference to a real
#: reader were the two widths nothing measured. 768 is also where
#: `#stats-title { white-space: nowrap }` was measured overflowing by 76px in
#: German - a tablet, and not a narrow-phone edge case.
WIDTHS = (320, 390, 768, 1278)

#: One RTL language and one LTR, for the reason in the module docstring: the
#: defect was reported as Arabic-only and was not.
#:
#: **German is the third because word length is its own failure mode.** Arabic and
#: English both break where this interface expects them to; German builds
#: compounds - "Lebensmittelabfall-Auswirkungsrechner" and the like - that have no
#: break opportunity in them at all, so they set an element's min-content width to
#: their own full width and push the document sideways from the inside. Two of the
#: three overflows this file now catches were German-only and every English and
#: Arabic assertion beside them was green.
LANGUAGES = ("ar", "de", "en")

#: `body { min-width: 320px }` in `web/css/styles.css` is a deliberate floor: below
#: 320px of content this interface stops reflowing and lets the reader scroll,
#: which is a supported outcome rather than a defect. At a 320px VIEWPORT with a
#: real 15px scrollbar there are only 305px of content, so the floor is in force
#: and the page is 320px wide by design.
#:
#: So the assertion is against `max(clientWidth, 320)` rather than `clientWidth`.
#: This relaxes exactly one case - the 320px viewport - and relaxes it by exactly
#: the amount the stylesheet declares. It does not weaken anything else: a `50vw`
#: bleed at 320px measured 328 against this 320 and still fails, and at every
#: other width `max()` returns `clientWidth` untouched.
MIN_CONTENT_WIDTH = 320

#: Each page mapped to an element that exists only once that page has actually
#: rendered. Both pages draw from JavaScript, so `networkidle` alone would
#: measure an empty `<main>` - a page with nothing in it never overflows, and
#: every assertion here would pass on one.
PATHS = {
    # The factor-set summary: fetched from the API after load, and the element
    # the defect was in.
    "/methodology.html": ".review-destinations dd",
    # The calculator. `/` serves it again, and `/index.html` is named rather than
    # `/` so this measures the file whatever the `index` directive says next.
    #
    # **The marker is step one's heading, not the introduction screen's**, even
    # though the introduction is what the page now opens on. `#stage-title` appears
    # only once the taxonomy has resolved, so waiting on it measures the widest
    # thing this page renders - forty food categories and eleven destination rows -
    # rather than a hero that is one heading and a button. `walk()` in
    # `tests/web/test_step_navigation.py` measures the introduction screen for
    # overflow at every breakpoint, including 320px.
    "/index.html": "#main-content #stage-title",
    # The two content pages joined this measurement at v1.30, when the language
    # chooser was added to their header rows. That row already carried a brand
    # lockup and a header navigation, so it is the third block in a row that
    # was measured as not fitting three at 938px - it wraps, and a wrap is
    # exactly the thing that stops being a wrap and starts being an overflow at
    # 320px in a language whose words are longer.
    #
    # `aria-busy="false"` rather than a content element: both pages set it when
    # their fetch has resolved, in the success case and the failure case alike,
    # so this waits for a page that has finished rendering without depending on
    # the WordPress feed being reachable or on how many buckets survive
    # suppression today.
    "/stats.html": "#stats-breakdown-content[aria-busy='false']",
    # Retired - nothing links to it - but still served, and still measured: a page
    # kept for the client's pending decision on the news feed is a page that must
    # still be readable when they make it.
    "/home.html": "#news-feed[aria-busy='false']",
}

#: Long enough that it cannot fit a 390px column at any sane font size, so a
#: token this long is one that WOULD have overflowed before the fix. Checked
#: rather than assumed - see `test_the_factor_notes_still_carry...` below.
_UNBREAKABLE_MIN = 60

_OVERFLOW = """
() => {
  const de = document.documentElement;
  return {scroll: de.scrollWidth, client: de.clientWidth, inner: window.innerWidth};
}
"""

#: The longest run of non-whitespace anywhere in the factor-set summary. This
#: is the thing that caused the overflow, so it is the thing whose presence
#: makes the measurement meaningful.
_LONGEST_TOKEN = """
() => {
  let longest = '';
  for (const dd of document.querySelectorAll('.review-destinations dd')) {
    for (const token of (dd.textContent || '').trim().split(/\\s+/)) {
      if (token.length > longest.length) longest = token;
    }
  }
  return longest;
}
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

@pytest.fixture(scope="module")
def browser(_playwright):
    """Headless Chromium **with its scrollbars drawn**. See the module docstring.

    `--hide-scrollbars` is one of Playwright's default arguments, and removing it
    is the whole difference between `clientWidth == innerWidth` (a page that can
    never be seen to overflow by a viewport-unit rule) and `clientWidth ==
    innerWidth - 15` (a desktop browser). Nothing else about the launch changes.

    Built on `_playwright` (`tests/web/conftest.py`'s package-scoped driver)
    rather than a second `sync_playwright()` of its own: two Chromium
    instances launched from the same driver coexist fine, but two
    `sync_playwright()` contexts on one thread is the exact conflict that
    fixture exists to close - see its docstring.
    """
    instance = _playwright.chromium.launch(
        ignore_default_args=["--hide-scrollbars"]
    )
    yield instance
    instance.close()


def _open(browser, path, language, width):
    context = browser.new_context(
        viewport={"width": width, "height": 780},
        locale=language,
        extra_http_headers={"Accept-Language": f"{language},en;q=0.5"},
    )
    page = context.new_page()
    page.goto(f"{BASE}{path}", wait_until="networkidle")
    # The calculator opens on its introduction screen again, and the marker below
    # is step one's heading, so this file has to press the button a visitor presses.
    # Measuring the introduction instead would measure one heading and a button
    # where the wizard's widest screens are what this test exists for; the
    # introduction is measured for overflow at every breakpoint by
    # `tests/web/test_step_navigation.py::test_no_horizontal_overflow_at_any_breakpoint`.
    if path == "/index.html":
        page.click('[data-action="start"]')
    # Both pages render from JavaScript, so this waits for the page's own
    # marker element. Without it the measurement can be of an empty `<main>`,
    # which never overflows and would pass every assertion here for the wrong
    # reason.
    page.wait_for_selector(PATHS[path], timeout=15_000)
    return context, page


@pytest.mark.parametrize("path", sorted(PATHS))
@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("width", WIDTHS)
def test_no_page_scrolls_sideways(browser, path, language, width):
    """`scrollWidth` against the content width: the whole claim, measured.

    Before the fix this failed at 390px on `/methodology.html` with 875
    against 390 in Arabic and 870 against 390 in English. The calculator was
    already clean at both widths in both languages, which is what located the
    defect in the factor-set summary rather than in the shared layout.

    With the scrollbar drawn and German added, this went on to catch two more:
    `margin-inline: calc(50% - 50vw)` on the home page's arch band, 8px at every
    width on every browser with a classic scrollbar, and an unbreakable German
    compound in the home hero's `<h1>` - 110px at 390px, 87px at 320px - which
    had been there through every previous green run of this file.

    The comparison is against `max(clientWidth, MIN_CONTENT_WIDTH)`; the constant's
    own note says why, and why that does not blunt it.
    """
    context, page = _open(browser, path, language, width)
    try:
        measured = page.evaluate(_OVERFLOW)
    finally:
        context.close()

    allowed = max(measured["client"], MIN_CONTENT_WIDTH)
    assert measured["scroll"] <= allowed, (
        f"{path} scrolls sideways at {width}px in {language}: "
        f"scrollWidth {measured['scroll']} against a content width of "
        f"{allowed} (clientWidth {measured['client']}, innerWidth "
        f"{measured['inner']}, floor {MIN_CONTENT_WIDTH})"
    )


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("width", WIDTHS)
def test_the_introduction_screen_does_not_scroll_sideways(browser, language, width):
    """**The screen `/` opens on, which the parametrisation above walks straight past.**

    `_open` presses "Start calculator" on `/index.html`, deliberately: the widest thing
    that page renders is step one's forty food categories, not a hero. The cost of that
    is that the first screen a visitor sees - the one at the bare address - was measured
    by nothing in this file, and `test_step_navigation.py`'s breakpoint walk, which does
    measure it, runs in English with the scrollbar hidden.

    Between the two, the introduction overflowed by 43px at 320px in German and no test
    said so: `.hero-copy` is a column flex box with `align-items: center`, so the
    heading is sized to its own min-content, and "Lebensmittelabfällen" set 343px inside
    a 280px column. Found by hand at a browser, which is not a repeatable process, so it
    is written down here.
    """
    context = browser.new_context(
        viewport={"width": width, "height": 780},
        locale=language,
        extra_http_headers={"Accept-Language": f"{language},en;q=0.5"},
    )
    page = context.new_page()
    try:
        page.goto(f"{BASE}/index.html", wait_until="networkidle")
        # The introduction's own marker, and NOT `[data-action="start"]` clicked: this
        # is the one measurement in this file that must stay on this screen.
        page.wait_for_selector(".hero-copy h1", timeout=15_000)
        measured = page.evaluate(_OVERFLOW)
    finally:
        context.close()

    allowed = max(measured["client"], MIN_CONTENT_WIDTH)
    assert measured["scroll"] <= allowed, (
        f"the introduction screen scrolls sideways at {width}px in {language}: "
        f"scrollWidth {measured['scroll']} against a content width of {allowed} "
        f"(clientWidth {measured['client']}, innerWidth {measured['inner']})"
    )


def test_the_browser_is_actually_drawing_a_scrollbar(browser):
    """**The precondition for everything above, and the reason this file was blind.**

    Every assertion in this module is `scrollWidth` against `clientWidth`. Under
    `--hide-scrollbars` those two are equal by construction on any page that does
    not overflow *and* on every page whose only overflow is a viewport-unit rule,
    because the rule's `100vw` and the element's `100%` then agree. The suite reads
    zero and passes, and it passed against a `50vw` bleed that overflowed a real
    browser by 8px on every page it was on.

    So the flag is asserted, not assumed. `clientWidth` must be strictly narrower
    than `innerWidth` on a page tall enough to scroll: that gap IS the scrollbar,
    and its absence means this file has stopped measuring what it says it measures
    - whether through a Playwright default changing, a launch argument being
    tidied away, or a headless mode that draws overlay scrollbars.

    A failure here is not a layout regression. It means every other test in this
    file has quietly become a tautology.
    """
    context, page = _open(browser, "/methodology.html", "en", 390)
    try:
        measured = page.evaluate(_OVERFLOW)
    finally:
        context.close()

    assert measured["client"] < measured["inner"], (
        f"the browser is not drawing a scrollbar: clientWidth "
        f"{measured['client']} equals innerWidth {measured['inner']}, so every "
        f"`scrollWidth == clientWidth` assertion in this file is measuring a "
        f"viewport that has no scrollbar in it. Check that the `browser` fixture "
        f"still passes ignore_default_args=['--hide-scrollbars']."
    )


def test_the_factor_notes_still_carry_the_token_that_caused_the_overflow(browser):
    """**The precondition, without which the test above proves nothing.**

    The overflow came from data, not from markup: the published factor set's
    notes cite a 124-character URL. Publish a set whose notes are three short
    words and every assertion above passes on a page that was never capable of
    overflowing - the test would go green for the wrong reason and stay green
    through a revert of the fix.

    So this asserts the page still contains a token long enough to have caused
    it. A failure here is not a layout regression; it means the published
    factor set changed and this file is no longer testing what it claims.
    """
    context, page = _open(browser, "/methodology.html", "en", 390)
    try:
        longest = page.evaluate(_LONGEST_TOKEN)
    finally:
        context.close()

    assert len(longest) >= _UNBREAKABLE_MIN, (
        f"the longest unbreakable token in the factor-set summary is now "
        f"{len(longest)} characters ({longest!r}), under the {_UNBREAKABLE_MIN} "
        f"this file needs to exercise the overflow. The published factor set "
        f"has changed; re-point this test at data that still reproduces it."
    )


@pytest.mark.parametrize("language", ("ar", "ur", "en"))
def test_the_row_labels_are_not_broken_mid_word_to_make_room(browser, language):
    """**The regression the first attempt at the fix caused, caught by looking.**

    Letting the `<dd>` break anywhere is right - it holds a URL from the
    database. Letting the `<dt>` do it is not: it holds a translated label, and
    the `<dd>` beside it will take every pixel it is allowed to. The first fix
    applied both properties to both elements and every overflow assertion above
    passed; the Arabic label `ملاحظات` had collapsed to a 1px column 760px
    tall, one letter per line.

    So this counts line boxes rather than measuring a width, which is
    language-neutral and needs no threshold: a label that fits on one line has
    exactly one client rect. Run in both RTL languages and in English, because
    the failure was invisible in English - no label here is a single unbroken
    word long enough to be squeezed.
    """
    context, page = _open(browser, "/methodology.html", language, 390)
    try:
        lines = page.evaluate(
            """
            () => {
              const out = [];
              for (const dt of document.querySelectorAll('.review-destinations dt')) {
                const node = [...dt.childNodes].find(n => n.nodeType === 3 && n.textContent.trim());
                if (!node) continue;
                const range = document.createRange();
                range.selectNodeContents(node);
                out.push({text: node.textContent.trim(), lines: range.getClientRects().length});
              }
              return out;
            }
            """
        )
    finally:
        context.close()

    assert lines, "no row labels were found to check"
    broken = [row for row in lines if row["lines"] > 1]
    assert not broken, (
        f"row labels are being broken across lines to make room for the value "
        f"beside them, in {language}: {broken}"
    )


def test_the_long_token_wraps_instead_of_widening_its_row(browser):
    """The mechanism, not just the outcome, anchored on the element itself.

    `test_no_page_scrolls_sideways` would also pass if somebody hid the
    summary, clipped it with `overflow: hidden`, or truncated the notes
    server-side - all of which lose the text rather than fit it. This asserts
    the `<dd>` is inside its list and taller than one line, which is what
    wrapping looks like and what hiding or clipping does not.
    """
    context, page = _open(browser, "/methodology.html", "en", 390)
    try:
        box = page.evaluate(
            """
            () => {
              let longest = null, best = 0;
              for (const dd of document.querySelectorAll('.review-destinations dd')) {
                for (const token of (dd.textContent || '').trim().split(/\\s+/)) {
                  if (token.length > best) { best = token.length; longest = dd; }
                }
              }
              const r = longest.getBoundingClientRect();
              const list = longest.closest('.review-destinations').getBoundingClientRect();
              const cs = getComputedStyle(longest);
              return {
                width: Math.round(r.width), listWidth: Math.round(list.width),
                height: Math.round(r.height),
                lineHeight: Math.round(parseFloat(cs.lineHeight) || 16),
                overflowX: cs.overflowX,
              };
            }
            """
        )
    finally:
        context.close()

    assert box["width"] <= box["listWidth"], (
        f"the notes cell is {box['width']}px inside a {box['listWidth']}px "
        f"list, so it is still widening its row rather than wrapping"
    )
    # Wrapped, not clipped: several lines tall, and not hidden behind an
    # `overflow` that would simply cut the URL off.
    assert box["height"] > box["lineHeight"] * 2, (
        f"the notes cell is {box['height']}px tall at a {box['lineHeight']}px "
        f"line height, which is not a wrapped 124-character URL - it has "
        f"probably been clipped or truncated instead"
    )
    assert box["overflowX"] == "visible", (
        "the notes cell now clips horizontally, which hides text rather than "
        "fitting it"
    )
