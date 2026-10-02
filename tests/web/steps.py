"""Driving the calculator's step bar from a browser test, where one step is optional.

**Step 2 is two panels and only sometimes both.** `spec.md` §3.3's step 2.5 is
shown when the published factor set releases the item level, offers a food
vocabulary, and the visitor has ticked at least one category (`itemStepOffered`
in `web/js/calculator.js`). Every one of those is a property of the deployment
under test, not of the test.

Before the item level shipped, one Continue took every test from step 2 to
step 3, and a hundred and twenty-four presses across twenty files were written
that way. On a stack whose set releases the item level -- which a fresh
`docker compose up` now produces, because `docker/mock-factors.json` carries
the switch -- that press lands on the food panel instead, and the waits after
it time out against a screen that is working correctly.

**What is centralised here is pressing Continue, not step 2's press.** An
earlier attempt tried to rewrite only the presses that were on step 2, by
matching a click followed by a wait for step 3's first field; the wait shapes
vary enough (`#total-waste`, `[data-line-field]`, `.destination-row`, a bare
timeout) that it found a quarter of them, and "which step is this press on" is
not a question answerable from the two lines around the call. Pressing again
costs nothing on any other step, because the food panel is only ever on screen
after step 2 -- so the helper can be the one way every test presses Continue,
and then no site can be missed.

**Continuing through the panel without ticking a food changes nothing**, which
is what makes the extra press safe rather than a behaviour the tests have to
model: `entryLeaves` gives a chosen category with no food ticked exactly the
leaf it gave before step 2.5 existed. A test that wants to exercise the panel
ticks a food itself -- `test_step_two_point_five_browser.py` is that test, and
it drives the panel directly rather than through this helper.
"""

from __future__ import annotations

#: The food panel's own heading. Asked for instead of a food checkbox because a
#: chosen category the vocabulary has no food for is rendered as a group saying
#: so rather than hidden (`itemStep`), so a panel can legitimately carry zero
#: checkboxes -- which is what a detector keyed on `input[name="food-item"]`
#: read as "the panel is not here", leaving fifty tests pressing Continue once
#: and then waiting out thirty seconds on step 3's field.
ITEM_PANEL = "#item-title"

#: `view.js::stepNav` is the only thing that renders the continue action, and it
#: always wraps it in `.step-nav`, so exactly one of these is on screen at a
#: time and the scoped and unscoped selectors the tests used are the same button.
CONTINUE = '.step-nav [data-action="continue"]'


def food_panel_is_showing(page) -> bool:
    """Whether step 2.5 is on screen right now.

    Asked of the DOM rather than of the taxonomy, deliberately: a test that
    read `item_level_enabled` would still be guessing at the other two
    conditions, and the question it actually needs answered is what the visitor
    is looking at.
    """
    return page.locator(ITEM_PANEL).count() > 0


#: The collapsible card's header button (#134), and the attribute that says
#: whether it is shut. Rendered out of `state.openCards`, so what is on screen
#: is the whole truth about which cards are open.
CARD_TOGGLE = ".step-card__toggle"
SHUT_CARD = '.step-card__toggle[aria-expanded="false"]'


def expand_step_cards(page, *, settle: int = 120, ceiling: int = 40) -> int:
    """Open every shut collapsible card on the step on screen, and say how many.

    **Why this exists rather than each test reaching into `state`.** Since #134
    a step that draws more than one card draws them collapsed, and a collapsed
    card's body is `hidden` -- so its inputs are not visible, not focusable and
    not fillable, and `page.fill` and a default `wait_for_selector` both wait
    out their timeout against a screen that is working correctly. Opening them
    through the control a visitor would press keeps that one fact in one place;
    writing `state.openCards` from a test would measure the renderer against a
    state no press can produce.

    **The single card is not collapsible at all** (decision 2 of #134: a lone
    card is never shut), so on a one-leaf chain this finds nothing and returns
    0. Callers do not have to know which case they are in.

    Re-queried after every press because the press re-renders: `render()`
    replaces `main.innerHTML` on every `setState`, so a NodeList captured before
    the first click names elements that are no longer in the document.

    `ceiling` is a loop guard rather than a bound on anything real -- `MAX_LEAVES`
    is twenty - and it exists so that a toggle that silently fails to change
    `aria-expanded` fails as an assertion naming the card rather than as a hung
    test.
    """
    opened = 0
    while page.locator(SHUT_CARD).count():
        page.locator(SHUT_CARD).first.click()
        page.wait_for_timeout(settle)
        opened += 1
        assert opened <= ceiling, (
            f"pressed {opened} card toggles and {page.locator(SHUT_CARD).count()} are "
            "still shut: a toggle is not writing its own aria-expanded"
        )
    return opened


def press_continue(page, *, settle: int = 150) -> bool:
    """Press the step bar's Continue, and press it again if the food panel answered.

    Returns whether the panel was there, so a caller that cares can assert on
    it. Almost none do: they want to be on the next step.

    A press that the page refuses -- Continue disabled, or a validation error
    holding the visitor on the step -- goes through here unchanged: nothing
    moves, the panel is not showing, and the helper returns having pressed once.
    """
    page.click(CONTINUE)
    page.wait_for_timeout(settle)
    if not food_panel_is_showing(page):
        return False
    page.click(CONTINUE)
    page.wait_for_timeout(settle)
    return True
