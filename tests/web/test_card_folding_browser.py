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

import os

import pytest

from tests.web.steps import CONTINUE, expand_step_cards, press_continue


pytestmark = pytest.mark.browser

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to measure a collapsed card; folding is unverified without it",
)

ROOT = os.environ.get("KAICALC_WEB_URL", "http://localhost:18080").rstrip("/")
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
    three, the colour assertion included.
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
        assert badge["mark"] != "✕", badge


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
