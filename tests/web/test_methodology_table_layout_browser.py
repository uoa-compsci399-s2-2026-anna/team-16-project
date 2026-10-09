"""Published methodology tables stay readable at the widths and languages we ship.

Issue #129: on the documentation page the table's cells were breaking **inside**
words — one or two characters a line, a column of stacked glyphs rather than text.
`.methodology-page td` carried ``overflow-wrap: anywhere``, which is the one value
that also shrinks a cell's min-content contribution to a single character, so a
shrink-to-fit table column could collapse to roughly one glyph wide and the text
would stack down it. The fix is one declaration: ``break-word``, which breaks a word
only when it cannot fit on a line of its own.

**What this file measures, and why each of the three is here.**

``§7.6`` rule 8 prescribes ``anywhere`` for a long value, and is right about the case
it was written for — a value in a flex row beside its own label. A table cell is both
label and value, and a shrink-to-fit context, so a reader following rule 8 into a
table re-creates #129. The rule now says so; this file is the measurement behind it.

Every assertion below is a **line-box** count, taken as the number of distinct
rounded ``top`` values over ``range.getClientRects()`` — the instrument
``test_horizontal_overflow.py`` already uses, and the only kind that can see this
defect at all. A ``textContent`` or ``count()`` assertion passes over a column of
stacked glyphs without noticing, and so does every "is it clipped" assertion: at no
point was anything clipped. ``anywhere`` squeezes rather than clips, and
``scrollWidth == clientWidth`` on every cell throughout.

**The body-cell density assertion is the one with teeth, and it is here because the
first version of this file did not have it.** That version asserted only that
headings fit on one line — which ``white-space: nowrap`` on ``thead th`` guarantees
by itself, with nothing at all to say about ``td``. Measured against a revert of
``overflow-wrap: break-word`` with the ``nowrap`` left in place: headings still one
line, every clipping assertion still green, and ``kg CO2e/kg`` broken across **four
lines over ten characters**. The load-bearing declaration was unmeasured.
"""

from __future__ import annotations

import urllib.error
import urllib.request

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright is required to measure rendered table layout")

from tests.web.base_url import ORIGIN


#: 768 joins the three this file shipped with, because §7.6 rule 3 says the baseline
#: is not a floor — "check the band between the breakpoints" — and the band is where
#: the previous round's regression lived. `test_horizontal_overflow.py` uses the same
#: four.
WIDTHS = (320, 390, 768, 1278)

#: `ur` joins `ar`: the repository's own RTL assertions run `("ar", "ur", "en")`, and
#: neither script offers a mid-word break opportunity without `anywhere`, which is
#: exactly the condition this file is about.
LANGUAGES = ("en", "de", "ar", "ur", "ja", "zh", "fr", "th")

#: Long enough that it cannot fit any column at any of these widths, so a token this
#: long is one that WOULD have stacked before the fix. The same figure
#: `test_horizontal_overflow.py` uses, and checked rather than assumed — see
#: `test_the_table_still_carries_a_token_long_enough_to_stack`.
UNBREAKABLE_MIN = 60

#: A cell of three characters or more must average at least this many characters a
#: line. **Measured rather than chosen.** With the fix the worst cell on the page is
#: 8 characters over 2 lines — 4.0 — and under `anywhere` the same page put `1.2345`
#: on 5 lines, 1.2. Three sits between them with margin on both sides, and is also
#: the point below which a column stops reading as text.
MIN_CHARS_A_LINE = 3


def _stack_is_up() -> bool:
    try:
        with urllib.request.urlopen(f"{ORIGIN}/methodology.html", timeout=3) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


if not _stack_is_up():  # pragma: no cover - environment guard
    pytest.skip(
        f"the stack is not answering on {ORIGIN}; run "
        "`docker compose -f docker/compose.yaml up -d --build web admin`",
        allow_module_level=True,
    )


#: One round trip. Line boxes are counted as distinct rounded `top` values rather
#: than as `getClientRects().length`, because a range can report more rects than it
#: occupies lines and this assertion must not fail for that reason.
_MEASURE = r"""
() => {
  const lineBoxes = node => {
    const range = document.createRange();
    range.selectNodeContents(node);
    return new Set([...range.getClientRects()].map(rect => Math.round(rect.top))).size;
  };
  const textNodes = element =>
    [...element.childNodes].filter(n => n.nodeType === 3 && n.textContent.trim());

  const headings = [];
  for (const heading of document.querySelectorAll('.methodology-page thead th')) {
    for (const node of textNodes(heading)) {
      const text = node.textContent.trim();
      headings.push({text, words: text.split(/\s+/).length, lines: lineBoxes(node)});
    }
  }

  const cells = [];
  let longest = '';
  for (const cell of document.querySelectorAll('.methodology-page td')) {
    for (const node of textNodes(cell)) {
      const text = node.textContent.trim();
      for (const word of text.split(/\s+/)) if (word.length > longest.length) longest = word;
      if (text.length >= 3) cells.push({text: text.slice(0, 40), chars: text.length, lines: lineBoxes(node)});
    }
  }

  const boxes = [...document.querySelectorAll('.methodology-page th, .methodology-page td')];
  return {
    pageScroll: document.documentElement.scrollWidth,
    pageClient: document.documentElement.clientWidth,
    longest: longest.length,
    longestSample: longest.slice(0, 60),
    headings,
    cells,
    regions: [...document.querySelectorAll('.methodology-page .table-scroll')].map(region => ({
      scroll: region.scrollWidth,
      client: region.clientWidth,
      labelled: region.getAttribute('aria-label'),
    })),
    boxes: boxes.map(box => ({
      clipped: box.scrollWidth > box.clientWidth + 1,
      overflow: getComputedStyle(box).overflow,
      textOverflow: getComputedStyle(box).textOverflow,
    })),
  };
}
"""


def _measure(browser, width, language):
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    try:
        page.goto(f"{ORIGIN}/methodology.html?lang={language}", wait_until="networkidle")
        page.wait_for_selector(".methodology-page .table-scroll table")
        return page.evaluate(_MEASURE)
    finally:
        context.close()


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("language", LANGUAGES)
def test_methodology_tables_keep_headings_and_cells_readable(browser, width, language):
    """#129, measured as line boxes: no word is broken that did not have to be.

    Three claims, and the second is the one the fix is actually about.
    """
    seen = _measure(browser, width, language)

    assert seen["pageScroll"] <= max(seen["pageClient"], 320), (
        f"the document itself scrolls sideways at {width}px in {language}: "
        f"{seen['pageScroll']} against {seen['pageClient']}"
    )

    #: **Line boxes <= words, not <= 1.** A heading is allowed to wrap BETWEEN its
    #: words — that is ordinary text — and is not allowed to break inside one. The
    #: first version of this file asserted one line, which `white-space: nowrap` on
    #: `thead th` guarantees on its own and which would therefore have gone on
    #: passing with the `td` fix reverted. Measured: dropping the `nowrap` makes only
    #: `Food category` wrap, to 2 lines over 2 words, at every width.
    stacked = [h for h in seen["headings"] if h["lines"] > h["words"]]
    assert not stacked, (
        f"at {width}px in {language}, {len(stacked)} heading(s) break inside a word "
        f"rather than between words: {stacked[:3]}"
    )

    #: **The body-cell density claim, which is the whole of #129.** `overflow-wrap:
    #: anywhere` shrinks a cell's min-content contribution to one character, so a
    #: shrink-to-fit column collapses and the text stacks down it. Nothing else here
    #: can see that: the page does not overflow, nothing is clipped, and the headings
    #: are held by `nowrap`.
    thin = [
        c for c in seen["cells"]
        if c["lines"] and c["chars"] / c["lines"] < MIN_CHARS_A_LINE
    ]
    worst = [(c["text"], "%dch/%dln" % (c["chars"], c["lines"])) for c in thin[:3]]
    assert not thin, (
        f"at {width}px in {language}, {len(thin)} cell(s) average under "
        f"{MIN_CHARS_A_LINE} characters a line — a column of stacked glyphs rather "
        f"than text, which is #129: {worst}"
    )

    assert all(not box["clipped"] for box in seen["boxes"]), (
        f"a cell is clipped at {width}px in {language}; the fix must FIT the text, "
        f"not hide it"
    )
    assert all(box["overflow"] not in {"hidden", "clip"} for box in seen["boxes"])
    assert all(box["textOverflow"] != "ellipsis" for box in seen["boxes"])

    if width <= 390:
        #: **Strict `>`, because `scrollWidth` is never smaller than `clientWidth`.**
        #: The `>=` this file shipped with cannot fail: measured on `main` at 1278px
        #: the region is `{'scroll': 1276, 'client': 1276}` and `>=` passes with
        #: nothing to scroll. At 320px the real table is 760 against a 240px region,
        #: so the strict form is the real claim — the table scrolls INSIDE its own
        #: labelled region rather than widening the document.
        for region in seen["regions"]:
            assert region["scroll"] > region["client"], (
                f"at {width}px the table region does not scroll "
                f"({region['scroll']} against {region['client']}), so either the "
                f"table has shrunk to fit — which is the defect — or this is no "
                f"longer the narrow layout"
            )
            assert region["labelled"], (
                "a scrollable region with no accessible name is a keyboard trap "
                "nobody can identify"
            )


def test_the_table_still_carries_a_token_long_enough_to_stack(browser):
    """**The precondition, without which every assertion above proves nothing.**

    #129 came from data, not from markup: the published factor set's notes cite a
    URL that no column can fit. Publish a set whose notes are three short words and
    the measurements above all pass on a page that was never capable of stacking —
    green for the wrong reason, and green straight through a revert of the fix.

    §7.6 rule 8's own last sentence asks for this, and the repository already does it
    one file over (`test_the_factor_notes_still_carry_the_token_that_caused_the_
    overflow`). A failure here is not a layout regression: it means the published
    factor set changed and this file is no longer testing what it claims.

    Measured against a fresh stack, whose published set is `docker/mock-factors.json`:
    the longest unbreakable token is 128 characters, the ReFED conversion-factors URL.
    """
    seen = _measure(browser, 390, "en")
    assert seen["longest"] >= UNBREAKABLE_MIN, (
        f"the longest unbreakable token in the methodology tables is now "
        f"{seen['longest']} characters ({seen['longestSample']!r}), under the "
        f"{UNBREAKABLE_MIN} this file needs to exercise #129. The published factor "
        f"set has changed; re-point this test at data that still reproduces it."
    )
