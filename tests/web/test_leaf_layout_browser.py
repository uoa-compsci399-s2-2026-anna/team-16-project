"""Step 4's layout: one vertical card per food type, at every width (#142).

**This file used to measure a matrix, and the properties it measured survive the
matrix being withdrawn.** Step 4 drew a grid above 650px -- destinations down the
side, food types across the top -- and per-leaf stacked blocks below it, because
320px less the gutters is 288px, a destination label needs about 90px, and the
~198px left over is 66px per column at three food types and 40px at five. The client
used the wide one on 1 October 2026 and rejected it as hard to use, so the stacked
layout is now the only layout. See `destinationStep`'s docstring, which keeps the
arithmetic: it is still the reason nobody should put the grid back.

What the tests here assert is therefore unchanged in substance and rewritten in
mechanism:

* the page never scrolls sideways, at 320 and 390 and through the band where the
  destination row stops being one column;
* each food type gets its OWN allocation -- its own full list of destination rows,
  inside its own card -- rather than one allocation split pro-rata across them;
* all thirteen destination rows stay;
* a cell can always show the number typed into it;
* a chain of one food type renders as a list, not as a grid of one column.

Two assertions are new, and they are the ones that would let the matrix come back
unnoticed: the layout is a vertical stack **at 1600px**, where there is more room for
a grid than there has ever been, and there is no `.allocation-matrix` in the document
at any width.

**Measured, never asserted from the markup.** A layout question is settled by
``documentElement.scrollWidth`` against ``window.innerWidth`` in a real browser and by
element geometry, which is the rule this repository already keeps: a stylesheet can be
read and reasoned about all day and still produce a page that scrolls.

The browser here is the package fixture, whose Chromium hides scrollbars, so
``clientWidth`` equals ``innerWidth`` and the comparison is exactly the one the brief
states. The scrollbar-drawing variant lives in ``test_horizontal_overflow.py`` and
stays there -- it is about viewport-unit rules on whole pages, and nothing on this
screen uses one.

Requires the stack: ``docker compose -f docker/compose.yaml up -d --build web``.
"""

from __future__ import annotations

import pytest

from tests.web.base_url import CALCULATOR
from tests.web.steps import expand_step_cards, press_continue


# The `browser` marker is applied by `conftest.py`, by location: every module
# here is a browser suite unless it is named in its `NOT_A_BROWSER_SUITE`.

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to measure a rendered layout",
)

BASE = CALCULATOR

#: `body { min-width: 320px }` is a deliberate floor: below 320px of content this
#: interface stops reflowing and lets the reader scroll. Same constant, same
#: reasoning and same one relaxed case as `test_horizontal_overflow.py`.
MIN_CONTENT_WIDTH = 320

#: The width the destination row stops being a single column at (`@media (max-width:
#: 480px)` in `styles.css`), and the width its two-column minimum -- 220 + 24 + 260 =
#: 504px -- finally fits in again. **The band between them is where #142's cards broke
#: the page and nothing would have said so.** A `<fieldset>`'s initial
#: `min-inline-size` is `min-content`, so unlike the `<div class="leaf-group">` the
#: rows used to sit in it refuses to shrink: at 500px the card was forced to 588px and
#: the document scrolled to 608. `test_horizontal_overflow.py` measures 320 and 390
#: only, where the row is one column and the band is invisible, so the band is
#: measured here.
TWO_COLUMN_FROM = 481
TWO_COLUMN_FITS_AT = 620

_OVERFLOW = """
() => ({
  scroll: document.documentElement.scrollWidth,
  client: document.documentElement.clientWidth,
  inner: window.innerWidth,
  body: document.body.scrollWidth,
})
"""

#: The step-4 layout, as geometry and as counts. Nothing here reads a class to decide
#: what the layout IS -- `.allocation-matrix` is counted only to assert it is absent.
LAYOUT = """() => {
  const cards = [...document.querySelectorAll('.step-card')];
  const rowsByLeaf = {};
  for (const input of document.querySelectorAll('[data-line-field=amount]')) {
    rowsByLeaf[input.dataset.leaf] = (rowsByLeaf[input.dataset.leaf] || 0) + 1;
  }
  const label = document.querySelector('.destination-row > label');
  return {
    cards: cards.length,
    matrices: document.querySelectorAll('.allocation-matrix').length,
    rowLabels: document.querySelectorAll('.matrix-row-label').length,
    columnHeads: document.querySelectorAll('.matrix-column-head').length,
    cellLabel: label ? getComputedStyle(label).display : null,
    perLeaf: Object.values(rowsByLeaf),
    leaves: Object.keys(rowsByLeaf).length,
    rowsInsideACard: [...document.querySelectorAll('.destination-row')]
      .filter(row => row.closest('.step-card')).length,
    rowsTotal: document.querySelectorAll('.destination-row').length,
    boxes: cards.map(card => {
      const box = card.getBoundingClientRect();
      return {left: Math.round(box.left), right: Math.round(box.right),
              top: Math.round(box.top), bottom: Math.round(box.bottom)};
    }),
  };
}"""


def _open(browser, width, height=700):
    context = browser.new_context(viewport={"width": width, "height": height}, locale="en-NZ")
    page = context.new_page()
    try:
        page.goto(BASE + "?lang=en", wait_until="networkidle", timeout=15000)
    except Exception as error:  # pragma: no cover - environment guard
        context.close()
        pytest.skip(f"the front end is not being served at {BASE}: {error}")
    page.wait_for_selector('[data-action="start"]', timeout=10000)
    return context, page


def _to_step_four(page, leaves, *, expand=True):
    """One chain of `leaves` food types, each with an amount, standing on step 4.

    `expand` opens step 4's cards as well as step 3's. **The open state is the wider
    of the two and is what the overflow measurements want**: a shut card is a header
    and a summary strip, and the thirteen two-column destination rows -- the thing
    that actually has a width -- are only laid out once the body stops being `hidden`.
    Tests that are about the shut state pass `expand=False`.
    """
    page.click('[data-action="start"]')
    page.wait_for_selector('input[name="sector"]')
    page.evaluate("document.querySelectorAll('input[name=sector]')[0].click()")
    page.wait_for_timeout(80)
    press_continue(page)
    page.wait_for_selector('input[name="food-category"]')
    boxes = page.locator('input[name="food-category"]')
    offered = boxes.count()
    assert offered > leaves, f"step 2 offers {offered} choices, too few to tick {leaves}"
    for index in range(leaves):
        boxes.nth(index).click()
        page.wait_for_timeout(50)
    press_continue(page)
    #: `state="attached"`, then the cards. Since #134 a forked chain draws its step-3
    #: cards collapsed and a collapsed card's body carries `hidden`, so the amount
    #: fields are in the document and not visible - which the default
    #: `state="visible"` waits out against a screen that is working correctly, and
    #: which `page.fill` refuses. Opened through the control a visitor would press.
    page.wait_for_selector('[data-leaf-field="amount"]', state="attached")
    expand_step_cards(page)
    ids = page.evaluate("() => [...document.querySelectorAll('[data-leaf-field=amount]')].map(e => e.id)")
    assert len(ids) == leaves, f"step 3 rendered {len(ids)} amount fields for {leaves} leaves: {ids}"
    for index, field in enumerate(ids):
        page.fill(f"#{field}", str((index + 1) * 100))
        page.wait_for_timeout(40)
    press_continue(page)
    #: Step 4's own cards fold since #142, for the same reason and through the same
    #: chrome, so the same two lines apply here.
    page.wait_for_selector('[data-line-field="amount"]', state="attached")
    if expand:
        expand_step_cards(page)


@pytest.mark.parametrize("leaves", (2, 5))
@pytest.mark.parametrize("width", (320, 390))
def test_step_four_does_not_scroll_sideways_at_a_phone_width(browser, width, leaves):
    """**The hard gate, on the screen the hard gate cannot reach.**

    Mutation: dropping the `@media (min-width: 650px)` around the matrix rules -
    letting the grid render at every width - is what this measured before #142 and is
    no longer expressible, because there is no grid. Its replacement is
    `min-inline-size: 0` on `collapsibleCard`'s `<fieldset>`: removing it lets the
    card refuse to shrink below its rows' 504px minimum, and this fails at both
    widths and both leaf counts.
    """
    context, page = _open(browser, width)
    try:
        _to_step_four(page, leaves)
        measured = page.evaluate(_OVERFLOW)
    finally:
        context.close()
    allowed = max(measured["client"], MIN_CONTENT_WIDTH)
    assert measured["scroll"] <= allowed, (
        f"step 4 scrolls sideways at {width}px with {leaves} leaves: scrollWidth "
        f"{measured['scroll']} against a content width of {allowed} (clientWidth "
        f"{measured['client']}, innerWidth {measured['inner']}, body "
        f"{measured['body']}, floor {MIN_CONTENT_WIDTH})"
    )


@pytest.mark.parametrize("width", (TWO_COLUMN_FROM, 540, TWO_COLUMN_FITS_AT - 1))
def test_step_four_does_not_scroll_sideways_where_the_row_is_two_columns_and_does_not_fit(browser, width):
    """**The band between the two widths anything else measures**, and the one #142's
    cards actually broke.

    The destination row is a single column up to 480px and a two-column grid above it
    whose minimum is 504px, which does not fit inside a card until about 620px. A
    `<div>` shrinks and lets `.destination-list`'s `overflow: hidden` clip; a
    `<fieldset>`'s initial `min-inline-size` is `min-content`, so it refuses to, and
    the whole document is pushed sideways instead. Measured before the fix: 608px of
    scroll against a 500px viewport, at every width in this band.

    Mutation: removing `min-inline-size: 0` from `collapsibleCard`'s `<fieldset>`
    fails this at all three widths and leaves the 320/390 test above green, which is
    exactly why this one exists.
    """
    context, page = _open(browser, width, 900)
    try:
        _to_step_four(page, 2)
        measured = page.evaluate(_OVERFLOW)
    finally:
        context.close()
    assert measured["scroll"] <= max(measured["client"], MIN_CONTENT_WIDTH), (
        f"step 4 scrolls sideways at {width}px, which is inside the band where a "
        f"destination row is two columns and does not fit in a card: {measured}"
    )


@pytest.mark.parametrize("leaves", (2, 5))
@pytest.mark.parametrize("width", (320, 390))
def test_step_three_does_not_scroll_sideways_at_a_phone_width(browser, width, leaves):
    """The card stack is today's panel N times, so it should cost nothing
    horizontally - which is a prediction until it is measured.

    **Measured with every card OPEN**, which is the wider of the two layouts and
    the one this has always measured; the shut state is measured in
    `test_step_card_collapse_browser.py`, where the card header - a staff-typed
    food name, a chevron and a badge on one line - is the content that is new."""
    context, page = _open(browser, width)
    try:
        _to_step_four(page, leaves)
        page.click('.step-nav [data-action="go-step"]')
        page.wait_for_selector('[data-leaf-field="amount"]', state="attached")
        expand_step_cards(page)
        measured = page.evaluate(_OVERFLOW)
    finally:
        context.close()
    allowed = max(measured["client"], MIN_CONTENT_WIDTH)
    assert measured["scroll"] <= allowed, (
        f"step 3 scrolls sideways at {width}px with {leaves} leaves: scrollWidth "
        f"{measured['scroll']} against a content width of {allowed} "
        f"(clientWidth {measured['client']}, innerWidth {measured['inner']})"
    )


@pytest.mark.parametrize("width", (320, 390, 649, 1278, 1600))
def test_step_four_is_a_vertical_card_per_food_type_at_every_width(browser, width):
    """**The replacement for the two tests that measured the matrix and the blocks
    apart**, and it is one test because there is now one layout.

    This is the same measurement the old `..._is_per_leaf_blocks_and_not_a_matrix`
    made below the breakpoint - each food type has its own named group, each cell
    carries its own label, and the matrix's shared side labels are not drawn - asked
    at the widths the *other* old test asserted a grid at. 1600px is new and is the
    one that would catch the grid coming back: it is more room than the matrix ever
    had.

    The vertical stack is geometry, not a class name: every card shares a left edge
    and each one starts at or below where the one above it ended. A `display: grid`
    that happened to resolve to a single column would satisfy a class-name assertion
    and fail this one the moment it did not.
    """
    context, page = _open(browser, width, 900)
    try:
        _to_step_four(page, 3)
        measured = page.evaluate(LAYOUT)
    finally:
        context.close()

    assert measured["matrices"] == 0, (
        f"at {width}px step 4 still draws an `.allocation-matrix`, which the client "
        f"used and rejected on 1 October 2026: {measured}"
    )
    assert measured["cards"] == 3, f"at {width}px three food types drew {measured['cards']} cards"
    assert measured["rowLabels"] == 0 and measured["columnHeads"] == 0, (
        f"at {width}px the matrix's shared side labels or column heads are still "
        f"drawn, so every destination name is on the screen more than once: {measured}"
    )
    assert measured["cellLabel"] not in (None, "none"), (
        f"at {width}px a destination cell has no label of its own, and the shared side "
        f"labels that stood in for one are gone: {measured}"
    )
    assert len({box["left"] for box in measured["boxes"]}) == 1, (
        f"at {width}px the cards do not share a left edge, so they are laid out across "
        f"rather than down: {measured['boxes']}"
    )
    for above, below in zip(measured["boxes"], measured["boxes"][1:]):
        assert below["top"] >= above["bottom"], (
            f"at {width}px a card starts before the one above it has ended, so the "
            f"layout is a grid and not a list: {measured['boxes']}"
        )


def test_all_thirteen_destination_rows_stay_inside_each_card(browser):
    """**Two properties the matrix's version of this test carried, and both still
    hold.**

    *Every applicable destination is offered*: no "choose which destinations you used"
    pre-step, so step 4 lists them all and the visitor fills the ones that apply,
    which is what it did before the fork and before the fold.

    *Every food type gets its OWN allocation*: the leaves are offered the same list
    and each leaf's rows are its own, which is what makes a per-leaf allocation
    expressible at all. A pro-rata split of one shared allocation would show one list
    of rows for the chain, and `perLeaf` would have one entry rather than three.

    The one thing that changed is where the rows live: inside their own food type's
    card, which the old assertion about side-label counts stood in for.
    """
    context, page = _open(browser, 1278, 983)
    try:
        _to_step_four(page, 3)
        measured = page.evaluate(LAYOUT)
    finally:
        context.close()

    assert measured["leaves"] == 3, (
        f"three food types produced {measured['leaves']} allocations, so the step is "
        f"not allocating per leaf: {measured}"
    )
    counts = set(measured["perLeaf"])
    assert len(counts) == 1, (
        f"the leaves are offered different destination lists: {measured['perLeaf']}"
    )
    assert measured["perLeaf"][0] >= 10, (
        f"only {measured['perLeaf'][0]} destinations are offered per leaf; the step "
        f"lists every applicable destination and the seeded taxonomy has thirteen"
    )
    assert measured["rowsTotal"] == measured["rowsInsideACard"] > 0, (
        f"{measured['rowsTotal'] - measured['rowsInsideACard']} destination rows are "
        f"outside any card: {measured}"
    )


def test_a_chain_of_one_food_type_renders_as_a_list(browser):
    """**A matrix of one column is a list**, which the old file asserted by leaving
    the single-leaf chain out of the grid entirely. It is still a list, and it is now
    a list inside the one card #142 asks for -- open, because `cardIsFixedOpen` says a
    lone card is never shut.

    `test_card_folding_browser.py` measures the card's chrome at a count of one; what
    is measured here is the thing underneath it, which is that the thirteen rows are
    a `.destination-list` in document order and not a grid of any shape.
    """
    context, page = _open(browser, 1278, 983)
    try:
        _to_step_four(page, 1)
        measured = page.evaluate(LAYOUT)
        listed = page.evaluate(
            """() => {
              const list = document.querySelector('.destination-list');
              return {
                inACard: Boolean(list && list.closest('.step-card')),
                display: list ? getComputedStyle(list).display : null,
                rows: list ? list.querySelectorAll(':scope > .destination-row').length : 0,
                stacked: (() => {
                  const rows = [...document.querySelectorAll('.destination-row')]
                    .map(r => r.getBoundingClientRect());
                  return rows.every((box, index) =>
                    index === 0 || Math.round(box.top) >= Math.round(rows[index - 1].bottom) - 1);
                })(),
              };
            }"""
        )
    finally:
        context.close()

    assert measured["cards"] == 1 and measured["matrices"] == 0, measured
    assert listed["inACard"], f"the single food type's rows are not inside a card: {listed}"
    assert listed["display"] == "block", (
        f"the one-leaf allocation is laid out as {listed['display']!r}; a chain of one "
        f"food type is a list: {listed}"
    )
    assert listed["rows"] >= 10, listed
    assert listed["stacked"], (
        f"the destination rows are not one above another, so the list is some other "
        f"shape: {listed}"
    )


#: What one destination amount box has to be able to show. Five significant figures
#: and two decimal places is an ordinary allocation in kilograms - 1,234.56 kg is a
#: fortnight of a mid-sized site - and the step's own `step="0.01"` says two decimals
#: are expected. A box that cannot show this is a box whose own value is unreadable.
CELL_FIGURE = "1234.56"


@pytest.mark.parametrize("leaves", (2, 5))
@pytest.mark.parametrize("width", (390, 650, 1278))
def test_a_number_typed_into_a_destination_box_is_never_clipped(browser, width, leaves):
    """**A box must fit its own content, and the page must not scroll to let it.**

    This measured the matrix's columns before #142: at 650px with five leaves they
    resolved to 67.22px against an input content box of 65px, so an allocation typed
    into one was cut off and the visitor could not read back the number they had just
    entered. The grid is gone and the arithmetic with it, but the property is not
    about a grid - it is about a box - and the fix for the grid was a column minimum
    that pushed the document sideways instead, so **both horns are still measured
    together**: a change that stops the clipping by widening something is caught by
    the page assertion, and one that stops the page scrolling by narrowing something
    is caught by the box assertion.

    **The width list is shorter than it was, and that is a consequence rather than a
    saving.** It used to be the five widths the grid engaged at, chosen because the
    grid's column arithmetic changed across them. A destination row is now a single
    column up to 480px and a two-column block above it, so there are three cases and
    not five: narrow, the old breakpoint, and the widest. The leaf count no longer
    enters the arithmetic at all - the cards are stacked - and it is kept because a
    layout that started depending on it again would be the matrix returning.

    `scrollWidth > clientWidth` on an `<input>` is the browser's own statement that
    its value does not fit in it. Nothing here reads the stylesheet.
    """
    context, page = _open(browser, width, 900)
    try:
        _to_step_four(page, leaves)
        ids = page.evaluate(
            """() => [...document.querySelectorAll('[data-line-field=amount]')]
                 .map(e => e.id).slice(0, 6)"""
        )
        for field in ids:
            page.fill(f"#{field}", CELL_FIGURE)
            page.wait_for_timeout(30)
        measured = page.evaluate(
            """() => {
              const rows = [...document.querySelectorAll('[data-line-field=amount]')]
                .filter(input => input.value !== '')
                .map(input => ({
                  id: input.id,
                  value: input.value,
                  scroll: input.scrollWidth,
                  client: input.clientWidth,
                  width: Math.round(input.getBoundingClientRect().width * 100) / 100,
                }));
              return {
                cards: document.querySelectorAll('.step-card').length,
                rows,
                page: {
                  scroll: document.documentElement.scrollWidth,
                  client: document.documentElement.clientWidth,
                },
              };
            }"""
        )
    finally:
        context.close()
    assert measured["rows"], "no destination amount was filled, so nothing was measured"
    assert measured["page"]["scroll"] <= max(measured["page"]["client"], MIN_CONTENT_WIDTH), (
        f"at {width}px with {leaves} leaves the page scrolls sideways once the boxes "
        f"hold {CELL_FIGURE}: {measured['page']}"
    )
    clipped = [row for row in measured["rows"] if row["scroll"] > row["client"]]
    assert not clipped, (
        f"at {width}px with {leaves} leaves {len(clipped)} destination box(es) cannot "
        f"show the number typed into them: {clipped[:3]}"
    )


@pytest.mark.parametrize("leaves", (6, 8))
def test_a_leaf_count_the_container_cannot_hold_never_pushes_the_page_sideways(browser, leaves):
    """The other end of the same rule the clipping test measures.

    `MAX_ENTRIES` permits twenty leaves in one submission, so eight is reachable, and
    eight is what the matrix could never fit: 84 + 8x14 + 8x104 = 1028px against a
    container that stopped at `.wide`'s 1000px. The whole reason that arithmetic
    existed was the horizontal direction, and the stacked cards remove it -- eight
    food types is eight cards one under another and costs nothing across. This is the
    measurement that says so rather than the assumption that it must.
    """
    context, page = _open(browser, 1278, 983)
    try:
        _to_step_four(page, leaves)
        ids = page.evaluate(
            """() => [...document.querySelectorAll('[data-line-field=amount]')]
                 .map(e => e.id).slice(0, 4)"""
        )
        for field in ids:
            page.fill(f"#{field}", CELL_FIGURE)
            page.wait_for_timeout(30)
        measured = page.evaluate(_OVERFLOW)
        cells = page.evaluate(
            """() => [...document.querySelectorAll('[data-line-field=amount]')]
                 .filter(input => input.value !== '')
                 .map(input => ({id: input.id, scroll: input.scrollWidth, client: input.clientWidth}))"""
        )
        cards = page.evaluate("() => document.querySelectorAll('.step-card').length")
    finally:
        context.close()
    assert cards == leaves, f"{leaves} food types drew {cards} cards"
    assert measured["scroll"] <= max(measured["client"], MIN_CONTENT_WIDTH), (
        f"step 4 scrolls sideways at 1278px with {leaves} leaves: {measured}"
    )
    clipped = [cell for cell in cells if cell["scroll"] > cell["client"]]
    assert not clipped, (
        f"at 1278px with {leaves} leaves {len(clipped)} box(es) cannot show their own "
        f"number: {clipped[:3]}"
    )


@pytest.mark.parametrize("leaves", (1, 3, 5))
def test_advancing_never_needs_a_sideways_scroll_at_a_tablet_width(browser, leaves):
    """768px is where a real browser draws a classic scrollbar, and where the matrix
    used to be at its most cramped: the breakpoint was 650, so the grid engaged with
    the least room it would ever have. There is no grid now, and the width is kept
    because the scrollbar is what makes this viewport different from the others."""
    context, page = _open(browser, 768, 900)
    try:
        _to_step_four(page, leaves)
        measured = page.evaluate(_OVERFLOW)
    finally:
        context.close()
    assert measured["scroll"] <= max(measured["client"], MIN_CONTENT_WIDTH), (
        f"step 4 scrolls sideways at 768px with {leaves} leaves: {measured}"
    )
