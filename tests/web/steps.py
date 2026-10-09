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


#: Read in one round trip when Continue will not take a press, because the
#: thirty seconds Playwright spends waiting produce only "element is not
#: enabled" while the page has been displaying the reason the whole time.
#: `CONTINUE` is interpolated rather than written out again, so a change to the
#: selector cannot leave this looking at a different button.
#: A RAW string: the `\s` in the regex is a Python escape otherwise, and
#: Python 3.12 warns about it on every import of this module.
_WHY_REFUSED = r"""
() => {
  const button = document.querySelector(%r);
  if (!button || !button.disabled) return null;
  const invalid = [...document.querySelectorAll('.invalid, [aria-invalid="true"]')]
    .map(element => (element.className || element.tagName) + ': '
         + (element.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 160));
  const heading = document.querySelector('main h2, main h1');
  return {step: heading ? heading.textContent.trim() : '(no heading)', invalid};
}
""" % CONTINUE


def _refuse_a_disabled_continue(page) -> None:
    """Fail at once, naming the step and its complaint, rather than in 30s.

    **What this replaces.** `page.click` waits for the control to become
    enabled and then raises `TimeoutError: Page.click: Timeout 30000ms
    exceeded` with a call log whose entire content is `element is not enabled`.
    Thirty seconds per case, naming neither the step nor the reason -- while
    `.allocation-summary.invalid` has been reading `Total waste 1,000.00
    kilograms Allocated 500.00 kilograms Remaining 500.00 kilograms` the whole
    time.

    Measured: v1.99 made step 4 refuse an allocation short of the total, four
    `test_period_*` helpers filled half of it, and the result was **92 failures
    taking 50 minutes** -- one batch spending two thirds of the browser job's
    whole budget waiting for a button that was never going to enable. CI never
    reported it, having been killed three batches earlier.

    **It cannot break a test that passes today.** The sentence this replaces in
    `press_continue` said a refused press "goes through here unchanged: nothing
    moves" -- true while Continue was merely ineffective, false since v1.99
    made it `disabled`, because `page.click` then throws. Every caller that
    reaches here with Continue disabled is therefore already failing, and the
    only change is how fast and how legibly. A test that wants to observe the
    refusal reads `el.disabled` directly, as `test_amount_limits_browser.py`
    does, and never comes through here.
    """
    why = page.evaluate(_WHY_REFUSED)
    if why is None:
        return
    complaint = "; ".join(why["invalid"]) or "nothing on the page is marked invalid"
    raise AssertionError(
        f"Continue is disabled on the step headed {why['step']!r}, so this "
        f"press cannot land. The page's own complaint: {complaint}. A helper "
        f"walking the form has to satisfy the step's rule, and the usual cause "
        f"is a step 4 allocation short of the total, which v1.99 made a "
        f"refusal. Pressing anyway costs 30 seconds and reports only 'element "
        f"is not enabled'."
    )


def press_continue(page, *, settle: int = 150) -> bool:
    """Press the step bar's Continue, and press it again if the food panel answered.

    Returns whether the panel was there, so a caller that cares can assert on
    it. Almost none do: they want to be on the next step.

    A press refused because **Continue is disabled** fails here at once, naming
    the step and the page's own complaint -- see `_refuse_a_disabled_continue`
    for the 92 cases and 50 minutes that bought. A press refused by a
    validation error that leaves Continue enabled still goes through unchanged:
    nothing moves, the panel is not showing, and the helper returns having
    pressed once.
    """
    _refuse_a_disabled_continue(page)
    page.click(CONTINUE)
    page.wait_for_timeout(settle)
    if not food_panel_is_showing(page):
        return False
    _refuse_a_disabled_continue(page)
    page.click(CONTINUE)
    page.wait_for_timeout(settle)
    return True
