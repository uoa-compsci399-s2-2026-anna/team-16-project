"""Step 4's matrix, and the narrow layout that is not one.

**The matrix cannot survive 320px, and that is arithmetic rather than
preference.** 320px less the gutters is 288px; a destination label needs about
90px, leaving ~198px shared between the leaf columns - 66px each at three leaves
and 40px at five. So the owner's choice is the matrix **above the existing 650px
breakpoint and per-leaf stacked blocks below it**: the same data, one media
query, two layouts, one DOM.

``tests/web/test_horizontal_overflow.py`` is the hard gate on that and measures
every *page* at 320 and 390. It cannot reach this screen - it stops at step one -
so the same measurement is made here, on step 4, with two leaves and with five.

**Measured, never asserted from the markup.** A layout question is settled by
``documentElement.scrollWidth`` against ``window.innerWidth`` in a real browser,
which is the rule the repository already keeps: a stylesheet can be read and
reasoned about all day and still produce a page that scrolls.

The browser here is the package fixture, whose Chromium hides scrollbars, so
``clientWidth`` equals ``innerWidth`` and the comparison is exactly the one the
brief states. The scrollbar-drawing variant lives in
``test_horizontal_overflow.py`` and stays there - it is about viewport-unit rules
on whole pages, and nothing on this screen uses one.

Requires the stack: ``docker compose -f docker/compose.yaml up -d --build web``.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.browser

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to measure a rendered layout",
)

BASE = os.environ.get("KAICALC_WEB_URL", "http://localhost:18080").rstrip("/") + "/index.html"

#: `body { min-width: 320px }` is a deliberate floor: below 320px of content this
#: interface stops reflowing and lets the reader scroll. Same constant, same
#: reasoning and same one relaxed case as `test_horizontal_overflow.py`.
MIN_CONTENT_WIDTH = 320

#: The breakpoint the amount step already splits at, reused rather than invented -
#: one media query for both screens.
BREAKPOINT = 650

_OVERFLOW = """
() => ({
  scroll: document.documentElement.scrollWidth,
  client: document.documentElement.clientWidth,
  inner: window.innerWidth,
  body: document.body.scrollWidth,
})
"""


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


def _to_step_four(page, leaves):
    """One chain of `leaves` food types, each with an amount, standing on step 4."""
    page.click('[data-action="start"]')
    page.wait_for_selector('input[name="sector"]')
    page.evaluate("document.querySelectorAll('input[name=sector]')[0].click()")
    page.wait_for_timeout(80)
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector('input[name="food-category"]')
    boxes = page.locator('input[name="food-category"]')
    offered = boxes.count()
    assert offered > leaves, f"step 2 offers {offered} choices, too few to tick {leaves}"
    for index in range(leaves):
        boxes.nth(index).click()
        page.wait_for_timeout(50)
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector('[data-leaf-field="amount"]')
    ids = page.evaluate("() => [...document.querySelectorAll('[data-leaf-field=amount]')].map(e => e.id)")
    assert len(ids) == leaves, f"step 3 rendered {len(ids)} amount fields for {leaves} leaves: {ids}"
    for index, field in enumerate(ids):
        page.fill(f"#{field}", str((index + 1) * 100))
        page.wait_for_timeout(40)
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector('[data-line-field="amount"]')


@pytest.mark.parametrize("leaves", (2, 5))
@pytest.mark.parametrize("width", (320, 390))
def test_step_four_does_not_scroll_sideways_at_a_phone_width(browser, width, leaves):
    """**The hard gate, on the screen the hard gate cannot reach.**

    Mutation: dropping the `@media (min-width: 650px)` around the matrix rules -
    letting the grid render at every width - reproduces the layout the owner was
    shown and this fails at both widths and both leaf counts.
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


@pytest.mark.parametrize("leaves", (2, 5))
@pytest.mark.parametrize("width", (320, 390))
def test_step_three_does_not_scroll_sideways_at_a_phone_width(browser, width, leaves):
    """The card stack is today's panel N times, so it should cost nothing
    horizontally - which is a prediction until it is measured."""
    context, page = _open(browser, width)
    try:
        _to_step_four(page, leaves)
        page.click('.step-nav [data-action="go-step"]')
        page.wait_for_selector('[data-leaf-field="amount"]')
        measured = page.evaluate(_OVERFLOW)
    finally:
        context.close()
    allowed = max(measured["client"], MIN_CONTENT_WIDTH)
    assert measured["scroll"] <= allowed, (
        f"step 3 scrolls sideways at {width}px with {leaves} leaves: scrollWidth "
        f"{measured['scroll']} against a content width of {allowed} "
        f"(clientWidth {measured['client']}, innerWidth {measured['inner']})"
    )


@pytest.mark.parametrize("width", (320, 390, BREAKPOINT - 1))
def test_below_the_breakpoint_step_four_is_per_leaf_blocks_and_not_a_matrix(browser, width):
    """**The narrow layout is the same data in the per-leaf block shape**, and the
    difference is one media query, so it is measured on the computed style rather
    than inferred from a class name.

    Below the breakpoint: the grid is not in force, each leaf group carries its own
    visible heading, and each cell carries its own destination label. Those three
    together are the block layout; any one of them alone could be true of a broken
    matrix.
    """
    context, page = _open(browser, width)
    try:
        _to_step_four(page, 3)
        measured = page.evaluate(
            """() => {
              const matrix = document.querySelector('.allocation-matrix');
              const heading = document.querySelector('.leaf-group__heading');
              const label = document.querySelector('.destination-row > label');
              return {
                display: matrix ? getComputedStyle(matrix).display : null,
                heading: heading ? getComputedStyle(heading).display : null,
                label: label ? getComputedStyle(label).display : null,
                rowLabel: getComputedStyle(document.querySelector('.matrix-row-label')).display,
              };
            }"""
        )
    finally:
        context.close()
    assert measured["display"] == "block", (
        f"the step-4 matrix is laid out as `{measured['display']}` at {width}px; "
        f"at this width it must be per-leaf blocks, not a grid"
    )
    assert measured["heading"] != "none", (
        f"the per-leaf blocks have no heading at {width}px ({measured}), so the rows "
        f"do not say which food they belong to"
    )
    assert measured["label"] != "none", (
        f"a destination cell has no label of its own at {width}px ({measured}), and "
        f"the shared side labels are the matrix's furniture"
    )
    assert measured["rowLabel"] == "none", (
        f"the matrix's shared side labels are still drawn at {width}px ({measured}), "
        f"so every destination name is on the screen twice"
    )


@pytest.mark.parametrize("leaves", (2, 5))
def test_above_the_breakpoint_step_four_is_a_matrix_of_one_column_per_leaf(browser, leaves):
    """Destinations down the side, leaves across the top. Measured as a grid with
    one label column plus one column per leaf, and with the per-cell labels stood
    down in favour of the shared side label."""
    context, page = _open(browser, 1278, 983)
    try:
        _to_step_four(page, leaves)
        measured = page.evaluate(
            """() => {
              const matrix = document.querySelector('.allocation-matrix');
              const style = getComputedStyle(matrix);
              return {
                display: style.display,
                columns: style.gridTemplateColumns.split(' ').length,
                width: Math.round(matrix.getBoundingClientRect().width),
                heading: getComputedStyle(document.querySelector('.leaf-group__heading')).display,
                label: getComputedStyle(document.querySelector('.destination-row > label')).display,
                rowLabels: document.querySelectorAll('.matrix-row-label').length,
                columnHeads: document.querySelectorAll('.matrix-column-head').length,
              };
            }"""
        )
    finally:
        context.close()
    assert measured["display"] == "grid", f"step 4 is not a grid at 1278px: {measured}"
    assert measured["columns"] == leaves + 1, (
        f"the matrix has {measured['columns']} columns for {leaves} leaves; it needs "
        f"one label column plus one per leaf: {measured}"
    )
    assert measured["columnHeads"] == leaves, measured
    assert measured["heading"] == "none" and measured["label"] == "none", (
        f"the block layout's own furniture is still drawn inside the matrix: {measured}"
    )
    assert measured["rowLabels"] > 1, (
        f"the matrix draws {measured['rowLabels']} destination labels down the side: "
        f"{measured}"
    )


def test_all_thirteen_destination_rows_stay_in_the_matrix(browser):
    """No "choose which destinations you used" pre-step: step 4 lists every
    applicable destination and the visitor fills the ones that apply, which is what
    it did before the fork. So the matrix is that same list, once per leaf."""
    context, page = _open(browser, 1278, 983)
    try:
        _to_step_four(page, 3)
        measured = page.evaluate(
            """() => {
              const byLeaf = {};
              for (const input of document.querySelectorAll('[data-line-field=amount]')) {
                byLeaf[input.dataset.leaf] = (byLeaf[input.dataset.leaf] || 0) + 1;
              }
              return {perLeaf: Object.values(byLeaf), labels: document.querySelectorAll('.matrix-row-label').length};
            }"""
        )
    finally:
        context.close()
    counts = set(measured["perLeaf"])
    assert len(counts) == 1, (
        f"the leaves are offered different destination lists: {measured['perLeaf']}"
    )
    assert measured["labels"] == measured["perLeaf"][0], (
        f"there are {measured['labels']} side labels for {measured['perLeaf'][0]} rows "
        f"per leaf, so a row and its label disagree"
    )
    assert measured["perLeaf"][0] >= 10, (
        f"only {measured['perLeaf'][0]} destinations are offered per leaf; the step "
        f"lists every applicable destination and the seeded taxonomy has thirteen"
    )


#: What one cell has to be able to show. Five significant figures and two decimal
#: places is an ordinary allocation in kilograms - 1,234.56 kg is a fortnight of a
#: mid-sized site - and the step's own `step="0.01"` says two decimals are expected.
#: A cell that cannot show this is a cell whose own value is unreadable in it.
CELL_FIGURE = "1234.56"


@pytest.mark.parametrize("leaves", (2, 3, 4, 5))
@pytest.mark.parametrize("width", (BREAKPOINT, 700, 768, 900, 1278))
def test_a_number_typed_into_a_cell_is_never_clipped(browser, width, leaves):
    """**A cell must fit its own content, at every width the matrix engages at.**

    Measured at 650px with five leaves: the columns resolved to 67.22px and the
    input's content box to 65px, so an allocation typed into it was cut off - the
    visitor could not read back the number they had just entered. There are only two
    honest answers, and the stylesheet may take either: the matrix does not engage at
    that width and leaf count, or the cell fits. This asserts the property rather
    than the mechanism, so either answer passes and neither can be lost silently.

    **Both horns are measured, because fixing one by breaking the other is the easy
    mistake.** Giving the columns a real minimum stops the clipping and makes the
    grid overflow its container instead - the page scrolls sideways, which
    `test_horizontal_overflow.py` forbids everywhere it can reach and cannot reach
    here. So the document's own width is asserted beside the cells': a mutation that
    removes the stand-down rule and leaves the minimum in place is caught by the
    first assertion, and one that removes the minimum and leaves the stand-down in
    place is caught by the second.

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
              const matrix = document.querySelector('.allocation-matrix');
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
                display: matrix ? getComputedStyle(matrix).display : 'none',
                columns: matrix ? getComputedStyle(matrix).gridTemplateColumns : '',
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
        f"at {width}px with {leaves} leaves the step-4 layout is "
        f"{measured['display']!r} with columns {measured['columns']!r} and the page "
        f"scrolls sideways: {measured['page']}"
    )
    clipped = [row for row in measured["rows"] if row["scroll"] > row["client"]]
    assert not clipped, (
        f"at {width}px with {leaves} leaves the step-4 layout is "
        f"{measured['display']!r} with columns {measured['columns']!r}, and "
        f"{len(clipped)} cell(s) cannot show the number typed into them: "
        f"{clipped[:3]}"
    )


@pytest.mark.parametrize("leaves", (6, 8))
def test_a_leaf_count_the_container_cannot_hold_never_pushes_the_page_sideways(browser, leaves):
    """The other end of the same rule the cell-clipping test measures.

    `MAX_ENTRIES` permits twenty leaves in one submission, so eight columns is
    reachable, and eight columns of number inputs need 84 + 8x14 + 8x104 = 1028px
    against a container that stops at `.wide`'s 1000px. A column minimum the
    container cannot honour would push the document sideways rather than shrink -
    which `test_horizontal_overflow.py` forbids on every page - so the stand-down
    rule has to be what keeps the matrix off, and this is the measurement that says
    it does.
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
    finally:
        context.close()
    assert measured["scroll"] <= max(measured["client"], MIN_CONTENT_WIDTH), (
        f"step 4 scrolls sideways at 1278px with {leaves} leaves: {measured}"
    )
    clipped = [cell for cell in cells if cell["scroll"] > cell["client"]]
    assert not clipped, (
        f"at 1278px with {leaves} leaves {len(clipped)} cell(s) cannot show their own "
        f"number: {clipped[:3]}"
    )


@pytest.mark.parametrize("leaves", (1, 3, 5))
def test_advancing_never_needs_a_sideways_scroll_at_a_tablet_width(browser, leaves):
    """768px is where a real browser draws a classic scrollbar and where the matrix
    is at its most cramped - the breakpoint is 650, so the grid is in force with the
    least room it will ever have."""
    context, page = _open(browser, 768, 900)
    try:
        _to_step_four(page, leaves)
        measured = page.evaluate(_OVERFLOW)
    finally:
        context.close()
    assert measured["scroll"] <= max(measured["client"], MIN_CONTENT_WIDTH), (
        f"step 4 scrolls sideways at 768px with {leaves} leaves: {measured}"
    )
