"""No page scrolls sideways, measured rather than reasoned about.

**`scrollWidth` against `clientWidth`, in a real browser, at a real width.**
A stylesheet can be read and reasoned about all day and still produce a page
that scrolls; this repository has twice shipped layout defects that every
markup test passed - a table whose scrollbar was drawn 4,500px below the fold,
and a dialog whose submit button was invisible. The only thing that settles a
layout question is measuring the rendered box.

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
WIDTHS = (320, 390)

#: One RTL language and one LTR, for the reason in the module docstring: the
#: defect was reported as Arabic-only and was not.
LANGUAGES = ("ar", "en")

#: Each page mapped to an element that exists only once that page has actually
#: rendered. Both pages draw from JavaScript, so `networkidle` alone would
#: measure an empty `<main>` - a page with nothing in it never overflows, and
#: every assertion here would pass on one.
PATHS = {
    # The factor-set summary: fetched from the API after load, and the element
    # the defect was in.
    "/methodology.html": ".review-destinations dd",
    # The calculator opens on its hero, before any step is entered.
    "/": "#main-content .hero h1",
    # The two content pages joined this measurement at v1.30, when the language
    # chooser was added to their header rows. That row already carried a brand
    # lockup and a four-link navigation, so it is the third block in a row that
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
    "/home.html": "#news-feed[aria-busy='false']",
}

#: Long enough that it cannot fit a 390px column at any sane font size, so a
#: token this long is one that WOULD have overflowed before the fix. Checked
#: rather than assumed - see `test_the_factor_notes_still_carry...` below.
_UNBREAKABLE_MIN = 60

_OVERFLOW = """
() => {
  const de = document.documentElement;
  return {scroll: de.scrollWidth, client: de.clientWidth};
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

from playwright.sync_api import sync_playwright  # noqa: E402


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
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
    """`scrollWidth == clientWidth`: the whole claim, measured.

    Before the fix this failed at 390px on `/methodology.html` with 875
    against 390 in Arabic and 870 against 390 in English. The calculator was
    already clean at both widths in both languages, which is what located the
    defect in the factor-set summary rather than in the shared layout.
    """
    context, page = _open(browser, path, language, width)
    try:
        measured = page.evaluate(_OVERFLOW)
    finally:
        context.close()

    assert measured["scroll"] == measured["client"], (
        f"{path} scrolls sideways at {width}px in {language}: "
        f"scrollWidth {measured['scroll']} against clientWidth "
        f"{measured['client']}"
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
