"""Step 3's collapsible cards, and the one rule set the badge and Continue share (#134).

**The client's problem was missed fields.** Step 3 is long enough to scroll, and a
card below the fold gets skipped -- so the cards fold, and the shut card has to
answer "did I fill this in?" without being opened. Three decisions came out of the
1 October meeting and are not re-derived here:

1. **Complete means every required field is filled.** The optional money figures do
   not withhold a tick by being empty.
2. **A single card is not collapsed.** One food type is the commonest journey, and
   collapsing it would open the step on an empty screen.
3. **Continue over an incomplete or invalid card expands that card and moves focus
   to the first field at fault.**

**What this file is really defending is that there is ONE rule set.**
``destinationStep`` carries the note that two lists of rules for one button left
each list enabling Continue on a state the other had just refused; a badge computed
from a second list is that defect with a tick instead of a button, and worse,
because a tick is read as a promise *before* anybody presses anything.
``test_the_badge_and_continue_agree_on_a_state_they_could_disagree_about`` is the
measurement, and it is built on the one state where a hand-written "every required
field is filled" list and ``leafProblem`` give different answers: two money figures
that contradict each other. Every required field *is* filled there, so the naive
badge ticks the card -- and Continue refuses it.

**Everything here is measured against the running stack**, which serves a *baked*
copy of ``web/`` (``docker/web.Dockerfile`` does ``COPY web/``), so::

    docker compose -f docker/compose.yaml build web
    docker compose -f docker/compose.yaml up -d --force-recreate web

before any of it measures anything, and ``docker inspect --format '{{.Image}}'
kaicalc-web`` compared against the tag's ``{{.Id}}`` before any of it is trusted.
"""

from __future__ import annotations


import pytest

from tests.web.base_url import CALCULATOR
from tests.web.steps import CONTINUE, expand_step_cards, press_continue


pytestmark = pytest.mark.browser

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to measure a collapsed card; folding is unverified without it",
)

BASE = CALCULATOR

#: `body { min-width: 320px }` is a deliberate floor; 390 is the other width
#: `test_horizontal_overflow.py` measures at, and both are restated here because a
#: collapsed card is a NEW row of content - a name, a chevron and a badge on one
#: line - and a row that fits at 390 and not at 320 is the usual way this breaks.
NARROW = (320, 390)

#: Every card on screen, reduced to what the three decisions are about.
CARDS = """() => [...document.querySelectorAll('.step-card')].map(card => {
  const toggle = card.querySelector('.step-card__toggle');
  const body = card.querySelector('.step-card__body');
  const badge = card.querySelector('[data-card-status]');
  const box = card.getBoundingClientRect();
  return {
    legend: card.querySelector('legend').textContent.trim(),
    legendDrawn: card.querySelector('legend').getBoundingClientRect().width > 2,
    toggleId: toggle.id,
    expanded: toggle.getAttribute('aria-expanded'),
    controls: toggle.getAttribute('aria-controls'),
    bodyId: body.id,
    bodyHidden: body.hasAttribute('hidden'),
    fields: body.querySelectorAll('input, select').length,
    drawnFields: [...body.querySelectorAll('input, select')].filter(
      control => control.offsetParent !== null).length,
    badgeState: badge.dataset.state,
    badgeWords: badge.querySelector('[data-card-status-text]').textContent.trim(),
    headerText: toggle.innerText.replace(/\\s+/g, ' ').trim(),
    height: Math.round(box.height),
  };
})"""

OVERFLOW = """() => ({
  scrollWidth: document.documentElement.scrollWidth,
  clientWidth: document.documentElement.clientWidth,
})"""


@pytest.fixture
def forked(browser):
    """A page on step 3 of a chain with `leaves` food types, cards untouched.

    Deliberately does **not** open the cards: this is the file that measures the
    shut state, so the fixture leaves the step exactly as the visitor meets it.
    """
    contexts = []

    def open_at(leaves=2, width=1278, height=983):
        context = browser.new_context(viewport={"width": width, "height": height}, locale="en-NZ")
        contexts.append(context)
        page = context.new_page()
        try:
            page.goto(BASE + "?lang=en", wait_until="networkidle", timeout=15000)
        except Exception as error:  # pragma: no cover - environment guard
            pytest.skip(f"the front end is not being served at {BASE}: {error}")
        page.wait_for_selector('[data-action="start"]', timeout=10000)
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
        #: `state="attached"`: the fields are in the document and hidden, which is
        #: the whole point of this file, so a visibility wait would time out here.
        page.wait_for_selector('[data-leaf-field="amount"]', state="attached", timeout=10000)
        drawn = page.locator('[data-leaf-field="amount"]').count()
        assert drawn == leaves, (
            f"step 3 rendered {drawn} waste-amount fields for {leaves} leaves"
        )
        return page

    yield open_at
    for context in contexts:
        context.close()


def _ids(page, field):
    return page.evaluate(
        "(field) => [...document.querySelectorAll(`[data-leaf-field=${field}]`)].map(e => e.id)",
        field,
    )


def _accessible_name(page, selector):
    """The accessible NAME a screen reader would read, computed by the browser.

    Not `innerText`, and the difference is the whole assertion: the chevron and the
    tick are `aria-hidden`, so what reaches assistive technology is the food's name
    and the badge's *word*. Reading `innerText` instead would pass on a badge whose
    only state was a colour and a glyph, which is exactly what #134 forbids.
    """
    cdp = page.context.new_cdp_session(page)
    document = cdp.send("DOM.getDocument", {"depth": -1})
    found = cdp.send("DOM.querySelector", {"nodeId": document["root"]["nodeId"], "selector": selector})
    assert found["nodeId"], selector
    tree = cdp.send("Accessibility.getPartialAXTree", {"nodeId": found["nodeId"], "fetchRelatives": False})
    for node in tree["nodes"]:
        if node.get("role", {}).get("value") == "button":
            properties = {p["name"]: p["value"]["value"] for p in node.get("properties", [])}
            return node.get("name", {}).get("value"), properties
    raise AssertionError(f"{selector} is not exposed as a button: {tree['nodes']}")


def _refused(page):
    """Press Continue and say whether the step held the visitor."""
    page.click(CONTINUE)
    page.wait_for_timeout(260)
    return page.locator("#amount-title").count() == 1


# --------------------------------------------------------- the collapsed state


def test_a_forked_chain_draws_one_collapsed_card_per_food_type(forked):
    """The fold itself, and what the shut card is allowed to leave unsaid.

    Three claims, because three different things could be broken: there is one
    card per leaf, every one of them is shut, and a shut card's fields are
    genuinely unreachable rather than merely invisible -- `hidden`, so Tab does
    not walk into them and `offsetParent` is null.

    Mutation: rendering the body without the `hidden` attribute leaves
    `bodyHidden` false and `drawnFields` at 4, and this fails naming the card.
    """
    page = forked(leaves=3)
    cards = page.evaluate(CARDS)
    assert len(cards) == 3, [card["legend"] for card in cards]
    for card in cards:
        assert card["expanded"] == "false", card
        assert card["bodyHidden"], (
            f"{card['legend']}'s body is not hidden, so the step is as long as it was "
            f"and the card below the fold is still missed"
        )
        assert card["fields"] >= 4, card
        assert card["drawnFields"] == 0, (
            f"{card['legend']} is shut and {card['drawnFields']} of its controls are "
            f"still drawn, so Tab walks into a card nobody opened"
        )
        assert card["controls"] == card["bodyId"], card


def test_a_shut_card_says_its_food_type_and_its_state(forked):
    """**Decision 1's whole purpose**: the shut card has to answer "did I fill this
    in?" without being opened, or the fold has only hidden the problem.

    The food's name is read off the header the visitor sees rather than off the
    `<legend>`, which is `.sr-only`: a name that existed only for assistive
    technology would leave a sighted reader a row of identical unlabelled cards.
    """
    page = forked(leaves=2)
    cards = page.evaluate(CARDS)
    for card in cards:
        assert card["legend"], card
        assert card["legend"] in card["headerText"], (
            f"the shut card does not say which food it is about: {card['headerText']!r}"
        )
        assert card["badgeWords"], f"{card['legend']} carries no state in words: {card}"
        assert card["badgeWords"] in card["headerText"], card
        assert card["badgeState"] == "incomplete", (
            f"{card['legend']} is empty and its badge reads {card['badgeState']!r}"
        )


def test_a_single_food_type_is_not_collapsed(browser):
    """**Decision 2.** One leaf draws no card at all, so there is nothing to shut:
    the step opens on the form, not on a closed box.

    Asserted as "no card AND the fields are drawn" rather than as "one card, open".
    Either shape satisfies the decision, but the single-leaf panel is also what
    `test_leaf_figure_migration_browser.py` calls "today's screen byte for byte",
    and a card that happened to render open would still have moved it.
    """
    context = browser.new_context(viewport={"width": 1278, "height": 983}, locale="en-NZ")
    page = context.new_page()
    try:
        page.goto(BASE + "?lang=en", wait_until="networkidle", timeout=15000)
    except Exception as error:  # pragma: no cover - environment guard
        context.close()
        pytest.skip(f"the front end is not being served at {BASE}: {error}")
    try:
        page.wait_for_selector('[data-action="start"]', timeout=10000)
        page.click('[data-action="start"]')
        page.wait_for_selector('input[name="sector"]')
        page.evaluate("document.querySelectorAll('input[name=sector]')[0].click()")
        page.wait_for_timeout(80)
        press_continue(page)
        page.wait_for_selector('input[name="food-category"]')
        #: Nothing ticked is one category-less leaf, which is the commonest journey.
        press_continue(page)
        page.wait_for_selector("#total-waste", timeout=10000)
        assert page.locator(".step-card").count() == 0, (
            "a chain with one food type drew a collapsible card; decision 2 is that a "
            "single card is not collapsed, and the single-leaf panel is not a card"
        )
        assert page.locator("#total-waste").is_visible()
        assert expand_step_cards(page) == 0
    finally:
        context.close()


@pytest.mark.parametrize("width", NARROW)
def test_the_cards_do_not_scroll_sideways_shut_or_open(forked, width):
    """The hard gate, restated for the one screen the fold added a row to.

    `test_horizontal_overflow.py` walks the pages at 320 and 390; it does not fork
    a chain, so the card header -- a name, a chevron and a badge on one line -- is
    content neither of those cases ever draws. Measured shut and then open, because
    the two are different layouts.

    Mutation: `white-space: nowrap` on `.step-card__name` (the name is staff-typed
    and has no length ceiling) pushes `scrollWidth` past `clientWidth` at 320 and
    this fails naming the width.
    """
    page = forked(leaves=3, width=width, height=700)
    shut = page.evaluate(OVERFLOW)
    assert shut["scrollWidth"] <= shut["clientWidth"], (
        f"at {width}px the shut cards scroll sideways: {shut}"
    )
    expand_step_cards(page)
    opened = page.evaluate(OVERFLOW)
    assert opened["scrollWidth"] <= opened["clientWidth"], (
        f"at {width}px the open cards scroll sideways: {opened}"
    )


# ------------------------------------------------------------------- the badge


def test_the_badge_is_not_colour_only(forked):
    """**An acceptance criterion of #134, measured through the browser's own
    accessible-name computation rather than through `innerText`.**

    The tick and the chevron are `aria-hidden`, so what a screen reader is given
    is the food's name, the badge's word, and `aria-expanded`. A badge whose only
    state was `color` and a `✓` would pass an `innerText` assertion and fail a
    reader; this asks the question the reader asks.
    """
    page = forked(leaves=2)
    cards = page.evaluate(CARDS)
    name, properties = _accessible_name(page, f"#{cards[0]['toggleId']}")
    assert cards[0]["legend"] in name, (name, cards[0])
    assert cards[0]["badgeWords"] in name, (
        f"the card's state reaches a screen reader as a colour and a glyph only: {name!r}"
    )
    assert properties.get("expanded") is False, properties
    page.click(f"#{cards[0]['toggleId']}")
    page.wait_for_timeout(200)
    _, reopened = _accessible_name(page, f"#{cards[0]['toggleId']}")
    assert reopened.get("expanded") is True, reopened


def test_the_badge_ticks_once_the_required_field_is_filled_and_asks_for_no_money(forked):
    """**Decision 1, both halves.** A filled waste amount is enough for a tick, and
    the two optional NZ$ figures left empty cannot take it away.

    The second half is the one that matters to the client's stated use for the
    badge -- deciding whether to open a card at all. A tick that meant "and the
    optional figures too" would be a cross on every card almost all the time, and
    the fold would be back to hiding work nobody can see.
    """
    page = forked(leaves=2)
    expand_step_cards(page)
    amounts = _ids(page, "amount")
    assert page.evaluate(CARDS)[0]["badgeState"] == "incomplete"
    page.fill(f"#{amounts[0]}", "400")
    page.wait_for_timeout(160)
    cards = page.evaluate(CARDS)
    assert cards[0]["badgeState"] == "complete", (
        f"the required field is filled and the badge still reads {cards[0]['badgeState']!r}; "
        f"money: {_ids(page, 'totalValue')}"
    )
    assert cards[1]["badgeState"] == "incomplete", cards[1]
    #: And it stays ticked with both money boxes empty, which is what they are.
    assert page.input_value(f"#{_ids(page, 'totalValue')[0]}") == ""
    assert page.input_value(f"#{_ids(page, 'wastedValue')[0]}") == ""


def test_the_badge_and_continue_agree_on_a_state_they_could_disagree_about(forked):
    """**The mandatory measurement: one rule set, two readers.**

    The state is chosen so that a badge computed from a hand-written "every
    required field is filled" list and a Continue gated on ``leafProblem`` give
    *different* answers. Both cards carry a legal waste amount, so every required
    field is filled on both; the first card then says the waste was worth NZ$200
    out of NZ$100 of production, which is the contradiction
    ``moneyContradictionValidation`` refuses. A second list ticks that card.
    Continue refuses it.

    Mutation 1 (the one #134's plan requires): add a rule to ``leafProblem`` --
    ``if (figures.totalAmount === '7') return {message: t('...'), field: 'amount'}``
    -- type 7 into the first card and both move together: the badge turns to
    incomplete and Continue holds the visitor on the step. Neither this test nor
    any other has to be edited for that, which is the property being claimed.

    Mutation 2 (the defect itself): render the badge from
    ``settled: Boolean(figures.totalAmount)`` instead of ``leafSettled`` and this
    test fails at the first assertion, reporting a tick over a card Continue
    refuses.
    """
    page = forked(leaves=2)
    expand_step_cards(page)
    amounts = _ids(page, "amount")
    for field in amounts:
        page.fill(f"#{field}", "400")
        page.wait_for_timeout(60)
    page.fill(f"#{_ids(page, 'totalValue')[0]}", "100")
    page.wait_for_timeout(60)
    page.fill(f"#{_ids(page, 'wastedValue')[0]}", "200")
    page.wait_for_timeout(200)

    cards = page.evaluate(CARDS)
    assert cards[0]["badgeState"] == "incomplete", (
        "every required field on this card is filled, and the two money figures "
        "contradict each other - which Continue refuses. A badge reading "
        f"{cards[0]['badgeState']!r} here is a second list of rules: "
        f"{cards[0]['headerText']!r}"
    )
    assert cards[1]["badgeState"] == "complete", cards[1]

    assert _refused(page), (
        "the badge calls the card incomplete and Continue let the visitor past it, "
        "which is the same disagreement from the other side"
    )
    #: **Re-read after the press, because the badge has TWO writers and the
    #: assertion above only reaches one of them.** `cardStatus` is printed by
    #: `collapsibleCard` at render time and rewritten by `updateCardBadges` on every
    #: keystroke, and the keystroke is the later of the two - so a second list of
    #: rules in the renderer alone is repaired before anything above can see it.
    #: Measured: mutating only `collapsibleCard`'s `settled` left this whole file
    #: green. Continue re-renders, so this read is the renderer's own answer.
    rendered = page.evaluate(CARDS)
    assert rendered[0]["badgeState"] == "incomplete", (
        "the renderer and the keystroke handler disagree about this card: the badge "
        f"read incomplete while typing and {rendered[0]['badgeState']!r} after a "
        f"re-render, so one of the two is not reading `leafSettled`"
    )
    #: And the other direction: fix the contradiction and both answers move.
    page.fill(f"#{_ids(page, 'wastedValue')[0]}", "50")
    page.wait_for_timeout(200)
    assert page.evaluate(CARDS)[0]["badgeState"] == "complete", page.evaluate(CARDS)[0]
    assert not _refused(page), (
        "nothing is at fault on either card and Continue still held the visitor"
    )


# -------------------------------------------------- Continue, and the keyboard


def test_continue_expands_the_offending_card_and_focuses_the_field_at_fault(forked):
    """**Decision 3.** A collapsed card hiding its own error is the reverse of the
    problem the fold exists to fix, so the refusal has to open the card it is about
    and put the caret in the box.

    The focus assertion names the *place* -- the leaf and the field -- rather than
    comparing the message's prose, because two leaves produce the byte-identical
    sentence; that is what `state.errorAt` is for and what `leafProblem` returns
    its `field` for.

    The scroll assertion is the half that is easy to lose: the click handler's
    scroll-to-top runs on the action name, so without `refusedAt` standing it
    aside the page would animate away from the field the refusal had just focused.

    Mutation: dropping `openCards: openedCard(...)` from the refusal patch in the
    `continue` branch leaves the card shut and the focus call finds nothing to
    focus -- `activeElement` comes back as `main-content` and this fails.
    """
    page = forked(leaves=2)
    expand_step_cards(page)
    amounts = _ids(page, "amount")
    page.fill(f"#{amounts[0]}", "400")
    page.wait_for_timeout(100)
    #: Shut both cards again, so the refusal has something to open.
    for card in page.evaluate(CARDS):
        page.click(f"#{card['toggleId']}")
        page.wait_for_timeout(120)
    assert all(card["bodyHidden"] for card in page.evaluate(CARDS)), page.evaluate(CARDS)
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    page.wait_for_timeout(80)

    assert _refused(page)
    cards = page.evaluate(CARDS)
    assert cards[0]["bodyHidden"], (
        "the card that is finished was opened too; the refusal is about one card"
    )
    assert not cards[1]["bodyHidden"], (
        f"the empty card stayed shut over its own error message: {cards[1]}"
    )
    focused = page.evaluate(
        "() => ({id: document.activeElement.id, field: document.activeElement.dataset.leafField,"
        " leaf: document.activeElement.dataset.leaf})"
    )
    assert focused["field"] == "amount", (
        f"Continue did not put the caret in the field at fault: {focused}"
    )
    assert focused["id"] == amounts[1], (focused, amounts)
    assert page.evaluate("Math.round(window.scrollY)") > 0, (
        "the refusal focused the field and then threw the page to the top, which "
        "scrolls the visitor away from the box they were just sent to"
    )


def test_a_card_opens_and_closes_from_the_keyboard_and_says_which_it_is(forked):
    """Keyboard open, keyboard close, and the state announced both times.

    "Announced" is `aria-expanded` on the control that still holds focus: a
    toggle that lost focus across the re-render would change a property on an
    element the reader is no longer on, and nothing would be read out.
    `main.js`'s subscriber restores focus by `id`, which is why the toggle has
    one.

    Space as well as Enter, because they are different code paths in the browser
    for a `<button>` and only one of them is free on a `<div role="button">` --
    the shape this would have taken if the header were not a real button.

    Mutation: dropping the `id` from the toggle in `collapsibleCard` leaves
    `activeElement` at `main-content` after the first press and this fails there.
    """
    page = forked(leaves=2)
    first = page.evaluate(CARDS)[0]["toggleId"]
    page.focus(f"#{first}")
    assert page.evaluate("document.activeElement.id") == first

    page.keyboard.press("Enter")
    page.wait_for_timeout(200)
    opened = page.evaluate(CARDS)[0]
    assert opened["expanded"] == "true" and not opened["bodyHidden"], opened
    assert page.evaluate("document.activeElement.id") == first, (
        "focus left the toggle, so the state change was applied and not announced"
    )

    page.keyboard.press(" ")
    page.wait_for_timeout(200)
    closed = page.evaluate(CARDS)[0]
    assert closed["expanded"] == "false" and closed["bodyHidden"], closed
    assert page.evaluate("document.activeElement.id") == first


# ------------------------------------------- the figures, and the running total


def test_values_survive_collapsing_expanding_and_a_re_render(forked):
    """What is typed stays typed through a shut, an open, and a full re-render.

    The re-render is driven by changing the unit `<select>`, which is the one
    control on this step that deliberately goes through `setState` -- a select has
    no mid-edit caret to lose. That is the keystroke-independent path on which a
    DOM-held `open` attribute would be erased, and it is the reason
    `state.openCards` exists.

    Mutation: rendering `open` from a `<details>`'s own attribute instead of from
    `state.openCards` closes both cards on the select change and this fails at the
    open-ness assertion rather than at the value.
    """
    page = forked(leaves=2)
    #: The FIRST card only, so the second one's shut state is also under test: a
    #: re-render has to leave both answers where the visitor put them, not just the
    #: open one.
    toggle = page.evaluate(CARDS)[0]["toggleId"]
    page.click(f"#{toggle}")
    page.wait_for_timeout(150)
    amounts = _ids(page, "amount")
    page.fill(f"#{amounts[0]}", "123.45")
    page.wait_for_timeout(120)
    page.click(f"#{toggle}")
    page.wait_for_timeout(150)
    assert page.evaluate(CARDS)[0]["bodyHidden"]
    page.click(f"#{toggle}")
    page.wait_for_timeout(150)
    assert page.input_value(f"#{amounts[0]}") == "123.45", (
        "the amount did not survive the card being shut and opened again"
    )

    #: This card's own unit, because the other card's select is inside a `hidden`
    #: body and is not operable - which is itself the first assertion of this file.
    page.select_option(f"#{_ids(page, 'unit')[0]}", "tonnes")
    page.wait_for_timeout(250)
    cards = page.evaluate(CARDS)
    assert not cards[0]["bodyHidden"], (
        f"a full re-render closed a card the visitor had opened: {cards}"
    )
    assert cards[1]["bodyHidden"], (
        f"a full re-render opened a card the visitor never touched: {cards}"
    )
    assert page.input_value(f"#{amounts[0]}") == "123.45"


def test_a_keystroke_does_not_close_a_card(forked):
    """The constraint `state.openCards` was written for, measured at the keystroke.

    Typing into one card must leave every card's open-ness exactly where it was.
    This is the case a native `<details open>` inside `<main>` cannot survive --
    `render()` replaces `main.innerHTML`, so the attribute is rebuilt from
    whatever the renderer believes, and a renderer that believed the DOM would
    close everything on the first character.

    The badge is asserted in the same breath because it moves on the same
    keystroke and through the same rule set: a card the visitor has just finished
    typing into reads complete without a press.
    """
    page = forked(leaves=3)
    opened = page.evaluate(CARDS)[1]["toggleId"]
    page.click(f"#{opened}")
    page.wait_for_timeout(150)
    before = [card["expanded"] for card in page.evaluate(CARDS)]
    assert before == ["false", "true", "false"], before

    amounts = _ids(page, "amount")
    page.fill(f"#{amounts[1]}", "9")
    page.wait_for_timeout(60)
    page.type(f"#{amounts[1]}", "50")
    page.wait_for_timeout(180)
    after = page.evaluate(CARDS)
    assert [card["expanded"] for card in after] == before, (
        f"a keystroke changed which cards are open: {before} -> "
        f"{[card['expanded'] for card in after]}"
    )
    assert page.input_value(f"#{amounts[1]}") == "950"
    assert after[1]["badgeState"] == "complete", after[1]
    assert after[0]["badgeState"] == "incomplete", after[0]


def test_the_combined_total_is_live_whether_the_cards_are_open_or_shut(forked):
    """The one figure on this step that says the fork adds up stays on screen and
    stays current with every card closed.

    It is browser-only arithmetic over figures already entered (`updateCombinedTotal`),
    and it sits outside the cards for exactly this reason: folded under one of them
    it would be a running total of three cards visible from one.

    Mutation: moving `.combined-total` inside `collapsibleCard`'s body leaves it
    `hidden` with the cards shut and this fails at the visibility assertion.
    """
    page = forked(leaves=2)
    expand_step_cards(page)
    amounts = _ids(page, "amount")
    page.fill(f"#{amounts[0]}", "400")
    page.wait_for_timeout(80)
    page.fill(f"#{amounts[1]}", "600")
    page.wait_for_timeout(180)
    assert "1,000.00" in page.inner_text(".combined-total"), page.inner_text(".combined-total")

    for card in page.evaluate(CARDS):
        page.click(f"#{card['toggleId']}")
        page.wait_for_timeout(120)
    assert all(card["bodyHidden"] for card in page.evaluate(CARDS))
    assert page.locator(".combined-total").is_visible(), (
        "the combined total went with the cards; it is the step's own summary and "
        "belongs to the step"
    )
    assert "1,000.00" in page.inner_text(".combined-total"), page.inner_text(".combined-total")
