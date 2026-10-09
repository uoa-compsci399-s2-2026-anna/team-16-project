"""#134's collapsible card on its other two consumers: step 2.5 (#138) and step 4 (#142).

`test_step_card_collapse_browser.py` measures the chrome on step 3, where it was
built. This file measures what the two later consumers add to it, and nothing it
already proves is repeated here:

* **Step 2.5's badge asks a different question.** The step is optional as a whole,
  so "no food chosen" is a legitimate answer and the badge counts rather than
  judges. There is no state of step 2.5 that can be wrong, so there is nothing for
  a cross to mean -- and a cross over a card the visitor deliberately left empty is
  the defect #138 names.
* **Both of them draw a card at a count of one**, which step 3 never does
  (`leafPanel` returns the plain single-leaf panel first). So this file is the first
  measurement of `cardIsFixedOpen`, and of the markup decision that follows from it:
  a card that can never be shut is given no toggle, because a chevron that changes
  nothing when pressed is worse than no chevron.
* **Step 4 hangs a figure OUTSIDE the fold.** Remaining has to be readable, with its
  unit, off a shut card, and it has to move as the visitor types -- which is the one
  thing a `hidden` body cannot do for itself.

**Measured in a browser and never reasoned about from the markup.** A `hidden`
attribute, an `aria-expanded` and an `offsetParent` are three different claims and
only the third is what a visitor meets.

Requires the stack, with the published set releasing the item level::

    docker compose -f docker/compose.yaml build web
    docker compose -f docker/compose.yaml up -d --force-recreate web
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.web.base_url import ORIGIN
from tests.web.steps import CONTINUE, expand_step_cards, press_continue


# The `browser` marker is applied by `conftest.py`, by location: every module
# here is a browser suite unless it is named in its `NOT_A_BROWSER_SUITE`.

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to measure a collapsed card; folding is unverified without it",
)

ROOT = ORIGIN
BASE = ROOT + "/index.html"

#: `body { min-width: 320px }` is the floor; 390 is the other width
#: `test_horizontal_overflow.py` measures at. Both are restated because a card
#: header is a new row of content -- a staff-typed name, a chevron and a badge on
#: one line -- and a row that fits at 390 and not at 320 is the usual way this
#: breaks.
NARROW = (320, 390)

#: `api/schemas.py`'s `MAX_ENTRIES`, restated for the reason `calculator.js` and
#: `test_leaf_multiselect_browser.py` both restate it: a client-side ceiling may
#: refuse earlier and more kindly than the API and must never refuse something the
#: API would take.
MAX_LEAVES = 20

#: Every card on screen, reduced to what this file asks about. Deliberately the
#: same shape as `test_step_card_collapse_browser.py`'s probe so the two files'
#: failures read alike, plus the two things only these consumers have: whether the
#: header is a real control, and what sits outside the fold.
CARDS = """() => [...document.querySelectorAll('.step-card')].map(card => {
  const header = card.querySelector('.step-card__toggle');
  const body = card.querySelector('.step-card__body');
  const badge = card.querySelector('[data-card-status]');
  const summary = card.querySelector(':scope > .allocation-summary');
  return {
    legend: card.querySelector('legend').textContent.trim(),
    header: header.tagName,
    headerIsButton: header.tagName === 'BUTTON',
    expanded: header.getAttribute('aria-expanded'),
    toggleId: header.id,
    bodyHidden: body.hasAttribute('hidden'),
    fields: body.querySelectorAll('input, select').length,
    drawnFields: [...body.querySelectorAll('input, select')].filter(
      control => control.offsetParent !== null).length,
    badgeState: badge.dataset.state,
    badgeWords: badge.querySelector('[data-card-status-text]').textContent.trim(),
    headerText: header.innerText.replace(/\\s+/g, ' ').trim(),
    summaryOutsideFold: Boolean(summary),
    summaryDrawn: Boolean(summary) && summary.offsetParent !== null,
    summaryText: summary ? summary.innerText.replace(/\\s+/g, ' ').trim() : null,
    summaryInvalid: Boolean(summary) && summary.classList.contains('invalid'),
  };
})"""

OVERFLOW = """() => ({
  scrollWidth: document.documentElement.scrollWidth,
  clientWidth: document.documentElement.clientWidth,
})"""


@pytest.fixture(scope="module")
def taxonomy():
    """What the stack under test actually publishes.

    Step 2.5 is unreachable unless the published set releases the item level and
    offers a food under a category it prices, and both are properties of the
    deployment rather than of this file. Read over HTTP for the reason
    `test_step_two_point_five_browser.py` gives: a test that read `admin/seed.py`
    would assert about foods the visitor is never offered.
    """
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(f"{ROOT}/api/v1/taxonomy", timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as error:  # pragma: no cover - environment guard
        pytest.skip(f"no calculator is being served at {ROOT}: {error}")


@pytest.fixture(scope="module")
def item_level(taxonomy):
    """The two conditions step 2.5 needs, reported apart.

    A skip is not a pass: a set with the switch off and a set with no vocabulary
    are different states, and only one of them is a landing that regressed.
    """
    if not taxonomy["factor_set"].get("item_level_enabled"):
        pytest.skip(
            "the published factor set has item_level_enabled false, so step 2.5 is "
            "correctly absent; seed a fresh database to exercise it"
        )
    if not taxonomy.get("food_items"):
        pytest.skip(
            "the published set releases the item level but §6.1 offers no food under "
            "any category it prices"
        )
    return taxonomy


def _categories_with_foods(taxonomy, how_many):
    """`how_many` categories the published set prices AND offers foods under."""
    offered = {item["food_category"] for item in taxonomy["food_items"]}
    found = [c for c in taxonomy["food_categories"] if c["code"] in offered]
    if len(found) < how_many:
        pytest.skip(f"only {len(found)} offered categories have foods; {how_many} needed")
    return found[:how_many]


@pytest.fixture
def opened(browser):
    contexts = []

    def open_at(width=1278, height=983):
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
        page.click(CONTINUE)
        page.wait_for_selector('input[name="food-category"]')
        return page

    yield open_at
    for context in contexts:
        context.close()


def _to_item_panel(page, categories):
    """Tick each of `categories` on step 2 and press Continue into step 2.5."""
    for category in categories:
        page.click(f'input[name="food-category"][value="{category["code"]}"]')
        page.wait_for_timeout(60)
    page.click(CONTINUE)
    page.wait_for_timeout(220)
    assert page.locator("#item-title").count() == 1, (
        "Continue did not open step 2.5 for "
        f"{[c['code'] for c in categories]}: the panel is not on screen"
    )
    page.wait_for_selector('input[name="food-item"]', state="attached", timeout=5000)


# ------------------------------------------------------------------- step 2.5


def test_each_chosen_category_is_a_card_shut_by_default(opened, item_level):
    """#138's first criterion. Three ticked categories is three cards, all shut,
    and the foods inside them are in the document and not drawn.

    `drawnFields` rather than the `hidden` attribute: `hidden` is a claim about the
    markup and `offsetParent` is the browser's own answer about what a visitor can
    reach.

    Mutation: `cardIsOpen` returning `true` unconditionally fails this on
    `aria-expanded`, which is the first of the three to be asserted; the dump shows
    `bodyHidden` false and every field drawn beside it, so all three claims move
    together and none of them is carrying the test on its own.
    """
    categories = _categories_with_foods(item_level, 3)
    page = opened()
    _to_item_panel(page, categories)
    cards = page.evaluate(CARDS)

    assert len(cards) == 3, f"three ticked categories drew {len(cards)} cards: {cards}"
    assert [card["legend"] for card in cards] == [c["name"] for c in categories], cards
    assert all(card["expanded"] == "false" for card in cards), cards
    assert all(card["bodyHidden"] for card in cards), cards
    assert all(card["fields"] > 0 for card in cards), cards
    assert all(card["drawnFields"] == 0 for card in cards), (
        f"a shut card's food checkboxes are still drawn: {cards}"
    )


def test_a_shut_card_says_how_many_foods_are_inside_it(opened, item_level):
    """#138's count criterion, and the reason it is a criterion rather than a
    nicety: collapsing only satisfies the issue's own body -- *make the categories
    clearly visible and expandable* -- if the collapsed state says what is inside.

    Measured on the header's `innerText`, which is what a sighted reader sees and
    (the chevron and the mark being `aria-hidden`) what the toggle's accessible
    name is.

    The second card is never opened, which is the point: its count has to be
    readable off a card nobody has touched, and it must not move when a food is
    ticked in the first.
    """
    categories = _categories_with_foods(item_level, 2)
    page = opened()
    _to_item_panel(page, categories)
    shut = page.evaluate(CARDS)
    assert [card["badgeWords"] for card in shut] == ["0 selected", "0 selected"], shut
    assert all("0 selected" in card["headerText"] for card in shut), shut

    # One food in the first card. Its own body has to be opened to reach the box --
    # a shut body is `hidden` and its checkbox is not clickable, which is the whole
    # of what folding does.
    page.locator('.step-card__toggle[aria-expanded="false"]').first.click()
    page.wait_for_timeout(160)
    page.locator(f'[data-item-group="{categories[0]["code"]}"] input[name="food-item"]').first.click()
    page.wait_for_timeout(180)
    cards = page.evaluate(CARDS)
    assert cards[0]["badgeWords"] == "1 selected", cards
    assert "1 selected" in cards[0]["headerText"], cards
    assert cards[0]["badgeState"] == "complete", cards
    assert cards[1]["badgeWords"] == "0 selected", (
        f"ticking a food in the first card changed the second card's count: {cards}"
    )
    assert cards[0]["bodyHidden"] is False, (
        f"the card whose checkbox was pressed shut itself: {cards}"
    )
    assert cards[1]["bodyHidden"] is True, cards


def test_an_unanswered_category_is_never_marked_wrong(opened, item_level):
    """**The one difference between step 2.5's badge and step 3's** (#138).

    Step 2.5 is optional as a whole: `entryLeaves` gives a chosen category with no
    food ticked exactly the leaf it gave before the panel existed, so an empty card
    is an answer and not a fault. The badge therefore distinguishes *answered* from
    *not looked at* -- the count -- and never *invalid*.

    Measured as three separate claims, because the easy way to get this wrong is to
    get one of them right: the state is not step 3's `incomplete`, the words are not
    step 3's, and the colour is not the error colour. The third is what a sighted
    reader would see even if the first two were fixed.

    Mutation: passing `cardStatus(false)` instead of `itemStatus(0)` fails all
    four — state, words, colour and the mark. Re-checked at #181, which emptied
    `cardStatus`'s marks and briefly left the fourth unable to fail; see the note
    beside it.
    """
    categories = _categories_with_foods(item_level, 2)
    page = opened()
    _to_item_panel(page, categories)
    measured = page.evaluate(
        """() => [...document.querySelectorAll('.step-card [data-card-status]')].map(badge => ({
          state: badge.dataset.state,
          words: badge.querySelector('[data-card-status-text]').textContent.trim(),
          mark: badge.querySelector('.step-card__mark').textContent.trim(),
          colour: getComputedStyle(badge).color,
          error: getComputedStyle(document.documentElement).getPropertyValue('--error').trim(),
          ink: getComputedStyle(badge.closest('.step-card__toggle')).color,
        }))"""
    )
    assert measured, "no badge was drawn on step 2.5"
    for badge in measured:
        assert badge["state"] == "neutral", (
            f"an unanswered step-2.5 card reads {badge['state']!r}: {badge}"
        )
        assert badge["words"] == "0 selected", badge
        assert "Incomplete" not in badge["words"], badge
        assert badge["colour"] == badge["ink"], (
            f"an unanswered step-2.5 card's badge is coloured away from the header's "
            f"own ink, so it reads as a verdict: {badge}"
        )
        #: **`== "—"`, not `!= "✕"`** (#181). The cross was `cardStatus`'s
        #: incomplete mark, so `!= "✕"` killed the mutation this docstring names
        #: — until #181 removed both of `cardStatus`'s glyphs, after which
        #: `cardStatus(false).mark` is `''` and the assertion could no longer
        #: fail. Asserting step 2.5's OWN mark restores it: `itemStatus(0)`
        #: gives `'—'` and `cardStatus(false)` gives `''`, so the swap is caught
        #: again, and by a claim about what this badge IS rather than about what
        #: it is not.
        assert badge["mark"] == "—", badge


def test_a_card_opens_and_closes_from_the_keyboard_and_says_which_it_is(opened, item_level):
    """#138's keyboard criterion, measured on step 2.5's own markup.

    The chrome is step 3's, and `test_step_card_collapse_browser.py` measures it
    there; it is re-measured here because this consumer has a second header path
    (`cardIsFixedOpen`'s `<div>`) and a mistake in the branch would ship a step whose
    cards cannot be opened without a mouse.

    `Enter` and `Space` are the browser's own activation of a `<button>` and nothing
    in `calculator.js` handles a key, so what is really asserted is that the header
    *is* a button, that it holds focus, and that focus comes back to it across the
    re-render the toggle causes -- which is what makes the `aria-expanded` change
    announced rather than merely applied.
    """
    categories = _categories_with_foods(item_level, 2)
    page = opened()
    _to_item_panel(page, categories)
    toggles = page.locator(".step-card__toggle")
    assert toggles.count() == 2, toggles.count()
    assert page.evaluate(
        "() => [...document.querySelectorAll('.step-card__toggle')].every("
        "el => el.tagName === 'BUTTON' && el.tabIndex >= 0)"
    ), "a step-2.5 card header is not a keyboard-reachable button"

    toggles.first.focus()
    assert page.evaluate("() => document.activeElement.dataset.action") == "toggle-card"
    page.keyboard.press("Enter")
    page.wait_for_timeout(180)
    after = page.evaluate(CARDS)
    assert after[0]["expanded"] == "true" and after[0]["bodyHidden"] is False, after
    assert after[1]["expanded"] == "false", after
    assert page.evaluate("() => document.activeElement.getAttribute('aria-expanded')") == "true", (
        "focus did not come back to the toggle across the re-render, so the state "
        "change was applied and not announced"
    )

    page.keyboard.press(" ")
    page.wait_for_timeout(180)
    shut = page.evaluate(CARDS)
    assert shut[0]["expanded"] == "false" and shut[0]["bodyHidden"] is True, shut


def test_one_chosen_category_is_a_card_that_is_open_and_has_no_toggle(opened, item_level):
    """**`cardIsFixedOpen`'s first test, and step 2.5 is its first consumer.**

    #134 left the `count <= 1` clause in `cardIsOpen` unexercised -- `leafPanel`
    returns the plain single-leaf panel before any card is built -- with a note
    saying whichever of #138 and #142 landed first would put a test under it. This
    is that test on step 2.5.

    Three claims, and they are three because `count <= 1` has two consequences and
    only one of them is openness: the card is drawn, its body is open and reachable,
    and its header is **not a button**. A lone card can never be shut, so a chevron
    on it would be a control that changes nothing, sitting in the tab order and
    carrying an `aria-expanded="true"` that never becomes `false`.

    Mutation: `cardIsFixedOpen` returning `false` leaves the card drawn with a
    toggle and a `hidden` body, and this fails on `bodyHidden` and on
    `headerIsButton` together.
    """
    categories = _categories_with_foods(item_level, 1)
    page = opened()
    _to_item_panel(page, categories)
    cards = page.evaluate(CARDS)

    assert len(cards) == 1, cards
    assert cards[0]["bodyHidden"] is False, (
        f"the only card on step 2.5 is collapsed, so the step opens on an empty "
        f"screen: {cards}"
    )
    assert cards[0]["drawnFields"] > 0, cards
    assert cards[0]["headerIsButton"] is False, (
        f"a card that cannot be shut carries a toggle: {cards}"
    )
    assert cards[0]["expanded"] is None, cards
    assert expand_step_cards(page) == 0, (
        "a lone card was reported as shut by the helper every other suite uses"
    )


def test_the_ceiling_message_is_visible_whether_the_cards_are_open_or_shut(opened, item_level):
    """#138's `MAX_LEAVES` criterion.

    The boxes the message explains are *inside* the cards and go dark with them, so
    a message folded away alongside them would leave a visitor looking at nothing
    while every remaining checkbox refused to tick. It lives outside every card, and
    this measures that it is drawn with the cards shut.

    **Whether the ceiling is reachable at all is read off the taxonomy, not guessed
    at from the screen.** `MAX_LEAVES` is twenty and one seeded category offers at
    most seven foods, so it takes ticks across several cards; a first draft ticked
    until the message appeared and *skipped* if it had not after a fixed number of
    presses, which made a swallowed click indistinguishable from a deployment too
    small to reach the ceiling. It skipped once and passed once on the same build,
    which is the measurement that said so. Now the vocabulary says whether the state
    exists, and failing to reach a state that exists is a failure.
    """
    categories = _categories_with_foods(item_level, 5)
    offered = sum(
        1 for item in item_level["food_items"]
        if item["food_category"] in {c["code"] for c in categories}
    )
    if offered <= MAX_LEAVES:
        pytest.skip(
            f"five categories offer {offered} foods between them and the ceiling is "
            f"{MAX_LEAVES} leaves, so this deployment cannot reach it"
        )
    page = opened()
    _to_item_panel(page, categories)
    expand_step_cards(page)
    pressed = 0
    while page.locator(".choice-ceiling").count() == 0 and pressed < offered + 4:
        live = page.locator('input[name="food-item"]:not(:checked):not(:disabled)')
        if live.count() == 0:
            break
        live.first.click()
        page.wait_for_timeout(90)
        pressed += 1
    assert page.locator(".choice-ceiling").count() == 1, (
        f"{pressed} food boxes were ticked across five cards offering {offered} foods "
        f"between them, which is past the {MAX_LEAVES}-leaf ceiling, and no ceiling "
        f"message was drawn"
    )

    # Shut every card again through the control a visitor would press.
    for index in range(page.locator('.step-card__toggle[aria-expanded="true"]').count() + 2):
        shut_me = page.locator('.step-card__toggle[aria-expanded="true"]')
        if shut_me.count() == 0:
            break
        shut_me.first.click()
        page.wait_for_timeout(110)
    assert page.locator('.step-card__toggle[aria-expanded="true"]').count() == 0, (
        "some card would not shut"
    )
    ceiling = page.locator(".choice-ceiling")
    assert ceiling.count() == 1, "the ceiling message vanished when the cards were shut"
    assert ceiling.first.is_visible(), (
        "the ceiling message is in the document and not drawn with the cards shut"
    )


@pytest.mark.parametrize("width", NARROW)
def test_step_two_point_five_does_not_scroll_sideways_shut_or_open(opened, item_level, width):
    """The hard gate, on the screen the hard gate cannot reach.

    Both states, because the card header is the content that is new -- a staff-typed
    category name, a chevron and a badge on one flex line -- and a header that fits
    at 390 and not at 320 is the usual way this breaks.
    """
    categories = _categories_with_foods(item_level, 3)
    page = opened(width=width, height=700)
    _to_item_panel(page, categories)
    shut = page.evaluate(OVERFLOW)
    expand_step_cards(page)
    openned = page.evaluate(OVERFLOW)

    for state, measured in (("shut", shut), ("open", openned)):
        assert measured["scrollWidth"] <= max(measured["clientWidth"], 320), (
            f"step 2.5 scrolls sideways at {width}px with the cards {state}: {measured}"
        )


# --------------------------------------------------------------------- step 4


def _to_step_four(page, leaves, amount=100):
    """A chain of `leaves` food types, each with `amount`, standing on step 4.

    Step 3's cards are opened through `expand_step_cards` for the reason that helper
    exists; step 4's are left exactly as the visitor meets them, because the shut
    state is what this file measures.
    """
    boxes = page.locator('input[name="food-category"]')
    offered = boxes.count()
    assert offered > leaves, f"step 2 offers {offered} choices, too few to tick {leaves}"
    for index in range(leaves):
        boxes.nth(index).click()
        page.wait_for_timeout(55)
    press_continue(page)
    page.wait_for_selector('[data-leaf-field="amount"], #total-waste', state="attached")
    expand_step_cards(page)
    fields = page.evaluate(
        "() => [...document.querySelectorAll('[data-leaf-field=amount]')].map(e => e.id)")
    assert len(fields) == leaves, f"step 3 drew {len(fields)} amount fields for {leaves}: {fields}"
    for index, field in enumerate(fields):
        page.fill(f"#{field}", str(amount * (index + 1)))
        page.wait_for_timeout(40)
    press_continue(page)
    page.wait_for_selector('[data-line-field="amount"]', state="attached")


#: Every card's badge and the one button, read in a single breath.
#:
#: **They are not the same arity, and that is the point.** `leafSettled` answers about
#: ONE leaf and `stepProblemAt` walks every leaf and stops at the first problem, so the
#: badge is per card and Continue is the conjunction over them. A first draft of the
#: agreement test asserted a biconditional between one card's badge and the button and
#: failed on the honest state where the first card is finished and the second is not --
#: which was the test being wrong, not the code.
VERDICT = """() => {
  const cards = [...document.querySelectorAll('.step-card')];
  const first = cards[0];
  const summary = first.querySelector(':scope > .allocation-summary');
  return {
    badges: cards.map(card => card.querySelector('[data-card-status]').dataset.state),
    words: cards.map(card =>
      card.querySelector('[data-card-status-text]').textContent.trim()),
    continueDisabled: document.querySelector('[data-action="continue"]').disabled,
    error: document.getElementById('allocation-error').textContent.trim(),
    badge: first.querySelector('[data-card-status]').dataset.state,
    // Guarded rather than dereferenced: a summary that has been moved INSIDE the fold
    // is the defect two of these tests are about, and a probe that threw on it would
    // report a TypeError instead of naming what moved.
    summaryOutsideFold: Boolean(summary),
    remaining: summary ? summary.querySelector('[data-summary="remaining"]').textContent.trim() : null,
    summaryInvalid: Boolean(summary) && summary.classList.contains('invalid'),
    summaryDrawn: Boolean(summary) && summary.offsetParent !== null,
    shut: first.querySelector('.step-card__body').hasAttribute('hidden'),
  };
}"""


def test_step_four_is_one_shut_card_per_food_type_with_its_remaining_outside_the_fold(opened):
    """#142's shape, in one measurement: no matrix, one card per food type, every
    card shut, and every card's Remaining readable without opening anything.

    `summaryOutsideFold` is `:scope > .allocation-summary` -- the strip is a child of
    the `<fieldset>` and not of the `hidden` body, which is the structural half of the
    claim -- and `summaryDrawn` is `offsetParent`, which is the browser's own answer
    about whether a visitor can read it. Both, because a strip can be outside the
    fold and still be invisible for some other reason.
    """
    page = opened()
    _to_step_four(page, 3)
    cards = page.evaluate(CARDS)

    assert page.evaluate("() => document.querySelectorAll('.allocation-matrix').length") == 0, (
        "step 4 still draws the allocation matrix the client rejected"
    )
    assert len(cards) == 3, f"three food types drew {len(cards)} cards: {cards}"
    assert all(card["expanded"] == "false" for card in cards), cards
    assert all(card["bodyHidden"] for card in cards), cards
    assert all(card["drawnFields"] == 0 for card in cards), cards
    assert all(card["summaryOutsideFold"] for card in cards), (
        f"a food type's Total/Allocated/Remaining strip is inside the fold: {cards}"
    )
    assert all(card["summaryDrawn"] for card in cards), (
        f"a shut card's Remaining is in the document and not drawn: {cards}"
    )
    for index, card in enumerate(cards):
        expected = f"{(index + 1) * 100}.00 kilograms"
        assert f"Remaining {expected}" in card["summaryText"], (
            f"card {index} does not show its own Remaining with its unit: {card}"
        )


def test_the_cards_are_a_vertical_list_at_every_width(opened):
    """**No grid at any width** (#142). Measured as geometry rather than from a class
    name: every card starts where the one above it ended and all of them share a left
    edge. A grid of one column per food type would put them side by side, which is
    exactly what the client rejected, and a `display: grid` that happened to resolve
    to one column would still pass a class-name assertion.

    1600x900 is the widest case and the one the matrix was drawn for; 650 is the
    breakpoint it used to engage at.
    """
    for width in (1600, 650):
        page = opened(width=width, height=900)
        _to_step_four(page, 3)
        boxes = page.evaluate(
            """() => [...document.querySelectorAll('.step-card')].map(card => {
              const box = card.getBoundingClientRect();
              return {left: Math.round(box.left), right: Math.round(box.right),
                      top: Math.round(box.top), bottom: Math.round(box.bottom)};
            })"""
        )
        assert len(boxes) == 3, (width, boxes)
        assert len({box["left"] for box in boxes}) == 1, (
            f"at {width}px the cards do not share a left edge, so they are laid out "
            f"across rather than down: {boxes}"
        )
        for above, below in zip(boxes, boxes[1:]):
            assert below["top"] >= above["bottom"], (
                f"at {width}px a card starts before the one above it has ended, so the "
                f"layout is a grid and not a list: {boxes}"
            )


def test_remaining_stays_right_when_the_card_it_belongs_to_is_shut(opened):
    """**The figure #142 asks for, through both of the paths that write it.**

    `updateLine` rewrites the strip in place on a keystroke -- §7.2's exception, taken
    because a re-render per keystroke destroys the focused input -- and `leafSummary`
    renders it again from `state` on the next re-render. Shutting the card is a
    re-render, so this walks both: type into an open card and read the strip without
    a re-render, then shut the card and read the same figure out of fresh markup.

    A figure that was only correct on one of the two paths is the defect this is
    for: it would be right while the visitor typed and wrong the moment they folded
    the card away, which is precisely when they are relying on it.
    """
    page = opened()
    _to_step_four(page, 2)
    page.locator('.step-card__toggle[aria-expanded="false"]').first.click()
    page.wait_for_timeout(180)
    first_row = page.evaluate(
        "() => document.querySelector('.step-card [data-line-field=amount]').id")
    page.fill(f"#{first_row}", "40")
    page.wait_for_timeout(200)

    typed = page.evaluate(VERDICT)
    assert typed["summaryOutsideFold"], (
        f"the Total/Allocated/Remaining strip is not a child of the card, so folding "
        f"the card takes it with it: {typed}"
    )
    assert typed["remaining"] == "60.00 kilograms", (
        f"the strip did not follow the keystroke: {typed}"
    )
    assert typed["shut"] is False, typed

    page.locator('.step-card__toggle[aria-expanded="true"]').first.click()
    page.wait_for_timeout(200)
    folded = page.evaluate(VERDICT)
    assert folded["shut"] is True, folded
    assert folded["summaryDrawn"] is True, (
        f"folding the card took its Remaining with it: {folded}"
    )
    assert folded["remaining"] == "60.00 kilograms", (
        f"the shut card's Remaining disagrees with what was typed into it: {folded}"
    )


def test_over_allocation_is_visible_from_a_shut_card(opened):
    """#142: over-allocation keeps its current treatment and is visible from the
    collapsed state.

    The treatment is the summary turning `invalid` -- Beetroot border and ground --
    and it has to survive the card being folded away, because the card being folded
    away is how a visitor stops seeing the rows. The badge and the disabled Continue
    are measured beside it: three things say it, and only the first two are readable
    without hunting for the button.
    """
    page = opened()
    _to_step_four(page, 2)
    page.locator('.step-card__toggle[aria-expanded="false"]').first.click()
    page.wait_for_timeout(180)
    first_row = page.evaluate(
        "() => document.querySelector('.step-card [data-line-field=amount]').id")
    page.fill(f"#{first_row}", "150")  # against a leaf holding 100
    page.wait_for_timeout(220)
    over = page.evaluate(VERDICT)
    assert over["summaryOutsideFold"], (
        f"the strip is not a child of the card, so a shut card cannot show it: {over}"
    )
    assert over["summaryInvalid"] is True, f"150 kg against a 100 kg leaf is not marked: {over}"
    assert over["badge"] == "incomplete", over
    assert over["continueDisabled"] is True, over

    page.locator('.step-card__toggle[aria-expanded="true"]').first.click()
    page.wait_for_timeout(200)
    folded = page.evaluate(VERDICT)
    assert folded["shut"] is True, folded
    assert folded["summaryInvalid"] is True, (
        f"folding an over-allocated card hides that it is over-allocated: {folded}"
    )
    assert folded["summaryDrawn"] is True, folded
    assert folded["badge"] == "incomplete", folded
    assert folded["continueDisabled"] is True, folded


@pytest.mark.parametrize("total", ("2.00", "10.00", "1.00", "100.00"))
def test_one_hundredth_over_is_refused_at_every_scale(opened, total):
    """Contract v1.105. **The owner's report: 2.00 kilograms accepting 2.01.**

    `exceedsTotal` was `allocated - total > ALLOCATION_EPSILON` with the epsilon at
    0.01, in doubles. Two things were wrong with that and the second is the one this
    test is shaped around.

    The tolerance was **one whole unit in the last place a visitor can type** — the
    destination input is `step="0.01"` — so it swallowed not the floating-point dust
    its note described but the smallest real error there is, and `>` made it
    inclusive. The screen said *Total 2.00*, *Allocated 2.01*, *Remaining 0.00*, under
    a badge reading *Complete*, with Continue enabled. And because the step-3 total
    never crosses the wire, the calculator then computed on 2.01.

    **Which "+0.01" it swallowed was decided by the dust rather than by the figures:**

        1.00 -> 1.01     0.010000000000000009   refused
        2.00 -> 2.01     0.009999999999999787   ACCEPTED
       10.00 -> 10.01    0.009999999999999787   ACCEPTED
      100.00 -> 100.01   0.010000000000005116   refused

    So the four totals here are not arbitrary and are not padding: **two of them the
    old rule refused and two it accepted, for the identical logical gap.** A test at
    one scale would have passed against the defect at the other two. That is the
    property under test — one gap, one verdict, whatever the magnitude — and it is why
    the parameters are spelled out rather than generated.

    One leaf, not two: with a second unallocated leaf on the page Continue is disabled
    by v1.99's own rule and this test's `continueDisabled` would pass without the card
    under measurement contributing anything.

    It finishes by allocating the total exactly, so it cannot be satisfied by a rule
    that refuses everything.
    """
    over = f"{Decimal(total) + Decimal('0.01'):.2f}"
    page = opened()
    _to_step_four(page, 1, amount=total)
    #: No toggle to click: `cardIsFixedOpen` holds a lone card open, so a one-leaf
    #: chain has no `.step-card__toggle` at all. The two tests below use two leaves
    #: because folding is what they measure; this one measures the verdict, and the
    #: second leaf would disable Continue on its own.
    first_row = page.evaluate(
        "() => document.querySelector('.step-card [data-line-field=amount]').id")

    page.fill(f"#{first_row}", over)
    page.wait_for_timeout(220)
    verdict = page.evaluate(VERDICT)
    assert verdict["badge"] == "incomplete", (
        f"{over} against a {total} leaf wears a {verdict['badge']!r} badge: {verdict}"
    )
    assert verdict["summaryInvalid"] is True, (
        f"the allocation strip is not marked for {over} against {total}: {verdict}"
    )
    assert verdict["continueDisabled"] is True, (
        f"Continue is live with {over} allocated against {total}: {verdict}"
    )
    #: **The figure beside the badge lied too, and it is asserted separately.** The old
    #: `remainingAmount` snapped anything inside the tolerance to exactly 0, so the
    #: strip read `Total 2.00  Allocated 2.01  Remaining 0.00` — three numbers that do
    #: not add up, on one line. A rule that refuses the allocation while still printing
    #: `0.00` would pass the three assertions above.
    assert verdict["remaining"].startswith("-0.01"), (
        f"Remaining reads {verdict['remaining']!r} for an allocation 0.01 over its "
        f"total, so the strip still says the books balance: {verdict}"
    )

    page.fill(f"#{first_row}", total)
    page.wait_for_timeout(220)
    settled = page.evaluate(VERDICT)
    assert settled["badge"] == "complete", (
        f"allocating exactly {total} is still refused, so the rule refuses "
        f"everything: {settled}"
    )
    assert settled["summaryInvalid"] is False, settled
    assert settled["continueDisabled"] is False, settled
    assert settled["remaining"].startswith("0.00"), settled


def test_under_allocation_is_refused_and_visible_from_a_shut_card(opened):
    """Contract v1.99. **Short of the total is as wrong as over it.**

    Until v1.99 step 4's rules bounded the allocation from above and said nothing
    below it, and §7.3a recorded that as deliberate. The owner's screenshot is what
    it looked like: a card holding 3.00 of 4.00 kilograms, `Remaining 1.00`, wearing
    a **`Complete`** badge, with Continue enabled.

    What made it worse than an interface slip is that **the step-3 total never
    crosses the wire** -- `buildLines` sends the destination rows, so the entry's
    waste IS their sum. The unallocated kilogram was not held back for later and not
    refused by the API; every metric, the cost, the equivalences and
    `production_share_percent` were computed on three quarters of what the visitor
    had typed, with a tick saying it was accounted for.

    Written as the mirror of `test_over_allocation_is_visible_from_a_shut_card`,
    against the same three readers, because v1.81's rule is that the badge, the strip
    and the button all read one rule set -- and all three were wrong together here,
    which is exactly why nothing reported it.

    **It finishes by allocating the rest**, so the test cannot be satisfied by a rule
    that refuses everything: the same three readers have to come back clean on the
    same card without a reload.
    """
    page = opened()
    _to_step_four(page, 2)
    page.locator('.step-card__toggle[aria-expanded="false"]').first.click()
    page.wait_for_timeout(180)
    first_row = page.evaluate(
        "() => document.querySelector('.step-card [data-line-field=amount]').id")
    page.fill(f"#{first_row}", "60")  # against a leaf holding 100
    page.wait_for_timeout(220)
    short = page.evaluate(VERDICT)
    assert short["summaryInvalid"] is True, (
        f"60 kg placed against a 100 kg leaf is not marked, so a visitor who folds "
        f"this card away is told nothing: {short}"
    )
    assert short["badge"] == "incomplete", (
        f"the card calls itself complete with 40 kg unplaced -- the badge the owner "
        f"photographed: {short}"
    )
    assert short["continueDisabled"] is True, (
        f"Continue is enabled on an under-allocated card, so the submission carries "
        f"60 kg of a 100 kg entry and every figure is computed on the smaller mass: "
        f"{short}"
    )

    page.locator('.step-card__toggle[aria-expanded="true"]').first.click()
    page.wait_for_timeout(200)
    folded = page.evaluate(VERDICT)
    assert folded["shut"] is True, folded
    assert folded["summaryDrawn"] is True, folded
    assert folded["summaryInvalid"] is True, (
        f"folding an under-allocated card hides that it is under-allocated: {folded}"
    )
    assert folded["badge"] == "incomplete", folded
    assert folded["continueDisabled"] is True, folded

    #: And the other direction: placing the rest clears all three readers. Without
    #: this the whole test passes against a rule that never lets anybody through.
    #:
    #: **Every card, not just this one.** `continueDisabled` is the step's reader, not
    #: the card's, and this walk builds two leaves — the first run of this block
    #: filled only the first and reported `continueDisabled: True` with the message
    #: `Enter an amount for at least one waste destination for Fruit.`, which was the
    #: test being wrong rather than the rule. Filling both is also the stronger claim:
    #: a chain whose every card matches its own amount is one the step lets through.
    #: **Open everything first, then read the ids, then fill — in that order.**
    #: `expand_step_cards` rather than a loop over `.all()`, because a toggle press is
    #: a `setState` and `render()` replaces `main.innerHTML`: a list of handles taken
    #: before the first click points into a detached tree and waits out its timeout
    #: against a screen that is working correctly. And the ids are read *after* that
    #: render rather than before it — read first, they named rows that no longer
    #: existed and the second card silently kept all 100 kg unplaced.
    expand_step_cards(page)
    page.wait_for_timeout(200)
    #: **Each card's own amount, not a constant.** This walk gives the two leaves
    #: different totals — 100 kg and 200 kg — so filling 100 into both left Fruit
    #: 100 short and the first version of this block read that correct refusal as a
    #: failure. The row and the figure it has to match are taken from the same card.
    cards = page.evaluate("""() => [...document.querySelectorAll('.step-card')].map(card => {
      const row = card.querySelector('[data-line-field=amount]');
      const total = card.querySelector('[data-summary=remaining]')
        ?.closest('.allocation-summary')?.querySelector('strong')?.textContent?.trim();
      return {row: row ? row.id : null, total: total ? total.split(' ')[0] : null};
    }).filter(c => c.row && c.total)""")
    assert len(cards) == 2, f"this walk is meant to build two leaves, not {len(cards)}"
    assert len({c["total"] for c in cards}) == 2, (
        f"the two leaves are meant to carry different totals, which is what makes "
        f"'each card against its own amount' a real claim: {cards}"
    )
    for card in cards:
        page.fill(f"#{card['row']}", card["total"])
        page.wait_for_timeout(180)
    settled = page.evaluate(VERDICT)
    assert settled["summaryInvalid"] is False, settled
    assert settled["badges"] == ["complete", "complete"], (
        f"a card whose allocation matches its amount is still not complete: {settled}"
    )
    assert settled["continueDisabled"] is False, (
        f"every card matches its own amount and the step still refuses Continue: {settled}"
    )


def test_one_food_type_is_a_card_that_is_open_and_has_no_toggle(opened):
    """`cardIsFixedOpen` on step 4, which is #142's own criterion: *collapsed by
    default unless it is the only card*.

    The sibling on step 2.5 measures the same clause on a different screen, and both
    are kept: the clause lives in the chrome and either consumer could stop asking it
    without the other noticing.

    Mutation: `cardIsFixedOpen` returning `false` leaves the single card shut with a
    toggle, and this fails on `bodyHidden` and `headerIsButton` together.
    """
    page = opened()
    _to_step_four(page, 1)
    cards = page.evaluate(CARDS)

    assert len(cards) == 1, cards
    assert cards[0]["bodyHidden"] is False, (
        f"the only food type's card is collapsed, so step 4 opens on an empty screen: {cards}"
    )
    assert cards[0]["drawnFields"] > 0, cards
    assert cards[0]["headerIsButton"] is False, (
        f"a card that cannot be shut carries a toggle: {cards}"
    )
    assert cards[0]["expanded"] is None, cards
    assert cards[0]["summaryOutsideFold"] and cards[0]["summaryDrawn"], cards


def test_every_badge_and_continue_read_one_rule_set(opened):
    """**One rule set, two readers -- the whole of it, on step 4.**

    `leafProblem` is the only copy of the per-leaf rules. `stepProblemAt` walks them
    for Continue and stops at the first problem; `leafSettled` asks them about one
    leaf, which is what a card's badge prints. Nothing else encodes a rule, and
    `destinationStep`'s own comment records what happened the last time something did:
    *any rule added to one of two lists left the other enabling Continue on a state the
    other had just refused.* A badge computed from a second list is that defect with a
    tick instead of a button, and worse, because a tick is read as a promise before
    anybody presses anything.

    The two readers have different arities, so the agreement is not a biconditional
    between one badge and the button: **Continue is live exactly when EVERY badge is
    complete.** That is asserted at four states, together with each state's concrete
    verdict, and the two assertions catch different things:

    * the equivalence catches a SECOND list -- a tick over a card the button refuses,
      or a live button over a card the badge calls incomplete;
    * the concrete verdicts catch a rule that moved BOTH readers, which the
      equivalence cannot see because it still holds.

    The third state is the one a first draft of this test got wrong and the one that
    matters most: the first card finished, the second not. The badge says the first
    card is done, Continue says the step is not, and both are right.

    Mutation: a rule added to `leafProblem`'s step-3 block -- refusing an allocation
    whose rows sum to exactly the leaf's own total -- moves the badge and the button
    together and fails the concrete verdict for *both allocated in full*, with the
    dump showing both halves moved as one. Neither reader is edited to make that
    happen, which is the property under test.
    """
    page = opened()
    _to_step_four(page, 2)
    rows = {}
    for index in (0, 1):
        page.locator('.step-card__toggle[aria-expanded="false"]').first.click()
        page.wait_for_timeout(180)
        rows[index] = page.evaluate(
            f"""() => [...document.querySelectorAll('.step-card')][{index}]
                 .querySelector('[data-line-field=amount]').id"""
        )

    seen = {}
    seen["nothing allocated"] = page.evaluate(VERDICT)
    page.fill(f"#{rows[0]}", "100")
    page.wait_for_timeout(220)
    seen["the first card allocated in full"] = page.evaluate(VERDICT)
    page.fill(f"#{rows[1]}", "200")
    page.wait_for_timeout(220)
    seen["both allocated in full"] = page.evaluate(VERDICT)
    page.fill(f"#{rows[0]}", "150")
    page.wait_for_timeout(220)
    seen["the first card over-allocated"] = page.evaluate(VERDICT)

    for state, verdict in seen.items():
        every = all(badge == "complete" for badge in verdict["badges"])
        assert every is (not verdict["continueDisabled"]), (
            f"with {state} the badges read {verdict['badges']} and Continue is "
            f"{'disabled' if verdict['continueDisabled'] else 'live'}: the badge and "
            f"the button are reading different rules"
        )
    assert seen["nothing allocated"]["badges"] == ["incomplete", "incomplete"], seen
    assert seen["the first card allocated in full"]["badges"] == ["complete", "incomplete"], (
        f"a finished card and an untouched one must not read alike: {seen}"
    )
    assert seen["both allocated in full"]["badges"] == ["complete", "complete"], seen
    assert seen["both allocated in full"]["continueDisabled"] is False, seen
    assert seen["the first card over-allocated"]["badges"] == ["incomplete", "complete"], (
        f"over-allocating the first card moved the second card's badge: {seen}"
    )


@pytest.mark.parametrize("width", NARROW)
def test_step_four_does_not_scroll_sideways_shut_or_open(opened, width):
    """The hard gate on the screen the hard gate cannot reach, in both states.

    `test_leaf_layout_browser.py` measures this with the cards as the visitor meets
    them and at two leaf counts; this one is the open state, which the other does not
    reach, and it is the state the thirteen rows are actually in while being filled.
    """
    page = opened(width=width, height=700)
    _to_step_four(page, 3)
    shut = page.evaluate(OVERFLOW)
    expand_step_cards(page)
    openned = page.evaluate(OVERFLOW)

    for state, measured in (("shut", shut), ("open", openned)):
        assert measured["scrollWidth"] <= max(measured["clientWidth"], 320), (
            f"step 4 scrolls sideways at {width}px with the cards {state}: {measured}"
        )
