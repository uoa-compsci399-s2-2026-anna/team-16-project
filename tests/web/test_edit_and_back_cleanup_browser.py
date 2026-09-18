"""The four things review of the ``returnTo`` and swap commits found, driven.

Each of these is a defect in navigation or in a confirmation, not in a
calculation, so each one is asserted on what the visitor can see: the screen
they land on, the question they are asked, the order of the cards on the review
step, and — once — the scroll position of the page under them.

**A. A review *Edit* link's marker outlived the step it was about.**
``state.returnTo`` records that a jump landed the visitor on a screen, so that
that screen's Back returns to the review step the Edit link was pressed on.
Nothing dropped it when the visitor walked *forward* off that screen instead::

    review -> Food category / Edit   marker {from: 1, step: 4}
           -> Continue               step 3, marker carried
           -> Back                   step 2, marker carried
           -> Back                   the REVIEW step

and from there Back walked review -> step 4 -> step 3 -> step 2 -> review
forever, with step 1 and the introduction reachable by no number of presses.
``test_walking_back_out_of_the_wizard_still_reaches_the_introduction``
(``test_step_one_back_navigation_browser.py``) walks Back with no marker live,
so it never saw this.

**B. A confirmation that fired with nothing to lose.** ``edit-entry`` asks
before it throws away a draft that is neither complete nor merely the pair the
opened entry hands back. ``continue`` on the step 3 -> 4 move creates one
destination row per destination — a destination the taxonomy chose and an
amount nobody typed — and those rows made an otherwise untouched draft compare
as changed. The two journeys below differ by one screen and by nothing else,
which is what says the rows are the cause.

**C. The question named a number that had stopped addressing what it meant.**
Covered next door, in ``test_step_one_back_navigation_browser.py``, because
that file owns the question.

**D. Declining scrolled the page.** ``goToStep`` returns without a ``setState``
when the visitor cancels, so the screen, the draft and the saved list were all
correctly untouched — and then the scroll-to-top at the bottom of the click
listener ran anyway, on the action's name. Cancel is "I did not mean to press
that"; it has to put the visitor back exactly where they were, viewport
included.

**E. And the test gap: nothing told an in-place swap from an appending one.**
Every existing case has a single saved entry, where ``[displaced]`` and
``[...rest, displaced]`` are the same array. The last test below uses two.

**Running this**::

    pip install playwright && playwright install chromium
    docker compose -f docker/compose.yaml up -d --build web
    pytest tests/web/test_edit_and_back_cleanup_browser.py

Restart ``kaicalc-api`` between this file and any other browser test file — see
``test_amount_limits_browser.py``'s header for why a sweep exhausts the
taxonomy rate limit.
"""

from __future__ import annotations

import os

import pytest

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to drive the wizard the way the owner drove it",
)

BASE = os.environ.get("KAICALC_WEB_URL", "http://localhost:18080").rstrip("/") + "/index.html"

#: Each screen, and a selector only that screen renders — so a failure names the
#: screen the visitor actually landed on rather than timing out on a selector.
SCREENS = (
    ("the introduction", '[data-action="start"]'),
    ("step 1 (supply-chain stage)", "#stage-title"),
    ("step 2 (food type)", "#food-title"),
    ("step 3 (waste amount)", "#amount-title"),
    ("step 4 (destinations)", "#destination-title"),
    ("step 5 (review)", "#review-title"),
)


@pytest.fixture
def page(browser):
    """A fresh context and a fresh session token per test."""
    context = browser.new_context(viewport={"width": 1278, "height": 983}, locale="en-NZ")
    opened = context.new_page()
    try:
        opened.goto(BASE + "?lang=en", wait_until="networkidle", timeout=15000)
    except Exception as error:  # pragma: no cover - environment guard
        context.close()
        pytest.skip(f"the front end is not being served at {BASE}: {error}")
    opened.wait_for_selector('[data-action="start"]', timeout=10000)
    yield opened
    context.close()


def _screen(page) -> str:
    for name, selector in SCREENS:
        if page.locator(selector).count():
            return name
    return "no recognisable screen"


def _dialogs(page, accept: bool) -> list:
    """Handle every `window.confirm` this page raises, and record its message.

    Registering the handler is not optional: an unhandled dialog blocks the
    renderer and hangs the test, and Playwright's auto-dismiss cannot be told
    apart from "nothing was asked".
    """
    seen: list = []

    def handle(dialog):
        seen.append(dialog.message)
        dialog.accept() if accept else dialog.dismiss()

    page.on("dialog", handle)
    return seen


def _pick_sector(page, index: int) -> str:
    """Select the `index`th supply-chain stage and return its visible name."""
    page.wait_for_selector('input[name="sector"]')
    page.evaluate(f"document.querySelectorAll('input[name=sector]')[{index}].click()")
    page.wait_for_timeout(120)
    return page.evaluate(
        f"document.querySelectorAll('.stage-card .stage-title')[{index}].textContent.trim()"
    )


def _build_chain(page, amount: str):
    """Steps 1 to 5 with the sector already chosen, leaving the visitor on review."""
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector('input[name="food-category"]')
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector("#total-waste")
    page.fill("#total-waste", amount)
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector('[data-line-field="amount"]')
    page.fill('[data-line-field="amount"] >> nth=0', amount)
    page.wait_for_timeout(100)
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector('[data-action="calculate"]')


def _walk_forward_to_review(page):
    """Continue four times from step 1, filling nothing that is already filled."""
    page.wait_for_selector("#stage-title")
    for selector in ("#food-title", "#total-waste", "#destination-title", '[data-action="calculate"]'):
        page.click('.step-nav [data-action="continue"]')
        page.wait_for_selector(selector)


def _back(page):
    page.click('.step-nav [data-action="go-step"]')
    page.wait_for_timeout(250)


def _continue(page):
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_timeout(250)


def _saved_cards(page) -> list:
    return [card.inner_text() for card in page.locator(".saved-entry-card").all()]


def _card_numbers(page) -> list:
    """Each saved card's "Entry N", lower-cased — `.eyebrow` is uppercased in CSS."""
    return [eyebrow.strip().lower() for eyebrow in page.locator(".saved-entry-card .eyebrow").all_inner_texts()]


def _current_entry_heading(page) -> str:
    return page.locator(".current-entry-heading h2").inner_text().strip()


def _review_amounts(page) -> str:
    """The draft's own destination figures, as the review step prints them."""
    return page.locator(".review-destinations").inner_text()


def _draft_stage(page) -> str:
    """The supply-chain stage the review step prints for the draft."""
    return page.locator(".review-block").first.inner_text()


# ---------------------------------------------------------------- A: the marker trap

#: The Edit links whose marker can be walked past, the screen each one opens, and
#: the screen a Back pressed *there* belongs on once the excursion is over.
#:
#: `data-step="3"` is absent because `continue` has always dropped every marker on
#: the step 3 -> 4 move, so that one link was never able to reach this state.
WALKED_PAST = (
    (0, "step 1 (supply-chain stage)", "step 2 (food type)", "the introduction"),
    (1, "step 2 (food type)", "step 3 (waste amount)", "step 1 (supply-chain stage)"),
    (2, "step 3 (waste amount)", "step 4 (destinations)", "step 2 (food type)"),
)


@pytest.mark.parametrize("step,opened,forward,behind", WALKED_PAST)
def test_walking_past_a_review_edit_link_stops_its_back_pointing_at_review(
    page, step, opened, forward, behind
):
    """Defect A. Press an Edit link, walk one screen forward, walk back, press Back.

    The marker is an offer — "you came here from the review step, so Back will
    take you back to it" — and the offer is about *that screen*. Continue is the
    visitor declining it: they are walking the wizard now, and `continue` will
    carry them to the review step on its own. A marker left live past that point
    aims Back forwards instead of backwards, and the four form steps plus the
    review step then form a ring with the introduction outside it.

    Both halves are asserted. The screen the press lands on says the marker is
    gone; walking on out to the introduction says no *other* marker took its
    place, which a fix that merely rewrote `from` would leave broken.
    """
    seen = _dialogs(page, accept=False)
    page.click('[data-action="start"]')
    _pick_sector(page, 0)
    _build_chain(page, "1000")

    page.click(f'.review-block [data-action="go-step"][data-step="{step}"]')
    page.wait_for_timeout(250)
    assert _screen(page) == opened, (
        f"precondition: the Edit link for step {step + 1} opens it, got {_screen(page)}"
    )
    _continue(page)
    assert _screen(page) == forward, (
        f"precondition: Continue walks one screen on from {opened}, got {_screen(page)}"
    )
    _back(page)
    assert _screen(page) == opened, (
        f"precondition: Back returns to {opened}, got {_screen(page)}"
    )

    _back(page)

    assert _screen(page) == behind, (
        f"the visitor walked forward off {opened} and back to it, which ends the Edit "
        f"excursion; Back belongs on {behind}, got {_screen(page)} — a marker that "
        f"outlived its own step is aiming Back at the review step"
    )

    walked = [_screen(page)]
    for _ in range(6):
        if walked[-1] == "the introduction":
            break
        _back(page)
        walked.append(_screen(page))
    assert walked[-1] == "the introduction", (
        f"Back has to keep walking out of the wizard rather than looping through the "
        f"review step; the walk went {walked}"
    )
    assert seen == [], (
        f"an Edit link moves nothing, so none of these presses discards anything and "
        f"none of them may ask; got {seen}"
    )


# ------------------------------------------------- B: a confirmation with nothing to lose


def _second_chain_over_the_duplicate_notice(page, reach_destinations: bool):
    """Stand on step 2's duplicate notice with an empty draft behind the visitor.

    One entry is saved. The visitor pressed Add, chose the same stage, chose a
    category (which clears the notice), typed a figure, optionally continued to
    the destinations screen and back, then cleared the figure and cleared the
    category again. Every field they filled they have emptied by hand, and the
    notice is back because the draft repeats the saved entry's pair.

    `reach_destinations` is the only difference between the two tests below. It
    is what creates `state.current` — one `createLine` row per destination, each
    with an amount nobody has typed.
    """
    page.click('[data-action="start"]')
    _pick_sector(page, 0)
    _build_chain(page, "1000")
    page.click('[data-action="add-entry"]')
    page.wait_for_selector("#stage-title")
    _pick_sector(page, 0)
    _continue(page)
    page.wait_for_selector("#food-title")
    page.evaluate("document.querySelectorAll('input[name=food-category]')[0].click()")
    page.wait_for_timeout(150)
    _continue(page)
    page.wait_for_selector("#total-waste")
    page.fill("#total-waste", "100")
    page.wait_for_timeout(120)
    if reach_destinations:
        _continue(page)
        page.wait_for_selector("#destination-title")
        rows = page.locator('[data-line-field="amount"]').count()
        assert rows > 0, "precondition: the destinations screen creates a row per destination"
        assert page.evaluate(
            "[...document.querySelectorAll('[data-line-field=amount]')].every(i => i.value === '')"
        ), "precondition: not one of those rows has an amount typed into it"
        _back(page)
        page.wait_for_selector("#total-waste")
    page.fill("#total-waste", "")
    page.wait_for_timeout(120)
    _back(page)
    assert _screen(page) == "step 2 (food type)", (
        f"precondition: step 3's Back is step 2, got {_screen(page)}"
    )
    page.click('[data-action="clear-food"]')
    page.wait_for_timeout(200)
    assert page.locator(".duplicate-notice").count() == 1, (
        "precondition: the draft repeats the saved entry's stage and category, so the "
        "notice is showing"
    )


def test_blank_destination_rows_are_not_work_worth_confirming_the_loss_of(page):
    """Defect B, reproduced. The draft holds nothing but rows it was handed.

    Everything this visitor typed, they deleted. What is left in the draft is the
    saved entry's own stage and category — which *Open entry 1* is about to hand
    straight back — and one destination row per destination, created by the walk
    to step 4, holding a destination the visitor did not choose and an amount
    they did not type.

    That is nothing to lose, and the owner's report is that nothing is asked in
    the ordinary version of this journey. It was asked here only because
    `EMPTY_DRAFT.current` is `[]` and this draft's is thirteen blank rows.
    """
    _second_chain_over_the_duplicate_notice(page, reach_destinations=True)
    seen = _dialogs(page, accept=False)

    page.click('.duplicate-notice [data-action="edit-entry"]')
    page.wait_for_timeout(300)

    assert seen == [], (
        f"the draft holds nothing a person entered — only rows the destinations screen "
        f"created — so opening the entry discards nothing and may not ask; got {seen}"
    )
    assert _screen(page) == "step 1 (supply-chain stage)", (
        f"nothing was asked, so the entry opened and the visitor is on step 1; got "
        f"{_screen(page)}"
    )
    _walk_forward_to_review(page)
    assert _saved_cards(page) == [], (
        f"the draft could not be a saved entry, so nothing may have been put on the "
        f"list; got {_saved_cards(page)}"
    )
    assert "1,000.00" in _review_amounts(page), (
        f"the opened entry is the draft: {_review_amounts(page)!r}"
    )


def test_the_same_journey_one_screen_shorter_also_asks_nothing(page):
    """Defect B's control, and the evidence that the rows are the cause.

    Identical to the test above in every press but one: this visitor never
    reaches the destinations screen, so `state.current` stays `[]`. It passed
    before the fix and passes after it. Keeping it is what stops the fix being
    credited to the wrong change — and what would catch a "fix" that made the
    comparison blind to real destination figures instead of to blank rows.
    """
    _second_chain_over_the_duplicate_notice(page, reach_destinations=False)
    seen = _dialogs(page, accept=False)

    page.click('.duplicate-notice [data-action="edit-entry"]')
    page.wait_for_timeout(300)

    assert seen == [], (
        f"an emptied draft over the duplicate notice has never asked and must not start; "
        f"got {seen}"
    )
    assert _screen(page) == "step 1 (supply-chain stage)", (
        f"the entry opened, so the visitor is on step 1; got {_screen(page)}"
    )


def test_a_destination_figure_left_in_the_draft_is_still_asked_about(page):
    """The other direction, which is what makes the test above mean anything.

    Same journey, except one destination row keeps its figure. That figure is
    something a person entered and opening the entry would destroy it, so the
    question has to survive the fix. A comparison that ignored `current`
    wholesale rather than ignoring blank rows would pass the two tests above and
    fail here.
    """
    page.click('[data-action="start"]')
    _pick_sector(page, 0)
    _build_chain(page, "1000")
    page.click('[data-action="add-entry"]')
    page.wait_for_selector("#stage-title")
    _pick_sector(page, 0)
    _continue(page)
    page.wait_for_selector("#food-title")
    page.evaluate("document.querySelectorAll('input[name=food-category]')[0].click()")
    page.wait_for_timeout(150)
    _continue(page)
    page.wait_for_selector("#total-waste")
    page.fill("#total-waste", "100")
    _continue(page)
    page.wait_for_selector("#destination-title")
    page.fill('[data-line-field="amount"] >> nth=0', "55")
    page.wait_for_timeout(120)
    _back(page)
    page.wait_for_selector("#total-waste")
    page.fill("#total-waste", "")
    page.wait_for_timeout(120)
    _back(page)
    page.click('[data-action="clear-food"]')
    page.wait_for_timeout(200)
    seen = _dialogs(page, accept=False)

    page.click('.duplicate-notice [data-action="edit-entry"]')
    page.wait_for_timeout(300)

    assert seen, (
        "one destination row holds 55, which the load would overwrite — that is a "
        "figure a person typed and it has to be asked about"
    )
    assert _screen(page) == "step 2 (food type)", (
        f"declining navigates nowhere, got {_screen(page)}"
    )


# ---------------------------------------------------------------- D: declining and scroll


def test_declining_the_discard_leaves_the_viewport_exactly_where_it_was(page):
    """Defect D. Cancel means "I did not mean to press that", scroll included.

    Driven at 320x600 because that is where it is visible: the page is five
    screens tall, the Back button is at the foot of it, and a visitor who
    scrolled down to reach it and then changed their mind was thrown to the top
    of the page with nothing else changed — no navigation, no re-render, just
    the scroll.

    The page is scrolled to its own foot rather than to an arbitrary offset so
    that Playwright's own scroll-into-view before the click is a no-op: the
    button is already on screen, so the only thing that can move the page is the
    handler.
    """
    page.set_viewport_size({"width": 320, "height": 600})
    page.click('[data-action="start"]')
    _pick_sector(page, 0)
    _build_chain(page, "1000")
    page.click('[data-action="add-entry"]')
    page.wait_for_selector("#stage-title")
    _pick_sector(page, 1)
    _build_chain(page, "2000")
    page.click('.saved-entry-card [data-action="edit-entry"]')
    page.wait_for_selector("#stage-title")
    changed = _pick_sector(page, 2)

    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(600)
    before = page.evaluate("window.scrollY")
    assert before > 0, (
        f"precondition: step 1 at 320x600 has to be taller than the viewport for this "
        f"to be about anything; scrollY is {before}"
    )

    seen = _dialogs(page, accept=False)
    page.click('.step-nav [data-action="go-step"]')
    page.wait_for_timeout(1200)

    assert seen, "the sector was changed after the jump, so backing out has to ask first"
    assert _screen(page) == "step 1 (supply-chain stage)", (
        f"declining navigates nowhere, got {_screen(page)}"
    )
    assert changed in page.locator(".stage-card.selected").inner_text(), (
        f"declining kept the visitor's change; expected {changed!r}"
    )
    assert page.evaluate("window.scrollY") == before, (
        f"declining moved nothing on the page, so it may not move the page either: "
        f"scrollY went {before} -> {page.evaluate('window.scrollY')}"
    )


# ------------------------------------------------------- E: in place, not at the end


def test_the_swap_takes_the_opened_entrys_own_place_in_the_list(page):
    """Defect E, the gap. Two saved entries, and the first one is opened.

    With one saved entry an in-place swap and a swap appended to the end of the
    list produce the same array, which is why every existing test passes against
    either. With two they do not:

        entries [A, B], draft C, *Edit* on A
          in place -> [C, B], draft A      <- what the numbers are written against
          appended -> [B, C], draft A      <- B silently renumbered to entry 1

    `fieldErrors['entries[N]']` and `entryIndexOf` both address entries by
    position, and the visitor addresses them by the number printed on the card,
    so a click about one card must not renumber a different one.
    """
    seen = _dialogs(page, accept=False)
    page.click('[data-action="start"]')
    first = _pick_sector(page, 0)
    _build_chain(page, "1000")
    page.click('[data-action="add-entry"]')
    page.wait_for_selector("#stage-title")
    second = _pick_sector(page, 1)
    _build_chain(page, "2000")
    page.click('[data-action="add-entry"]')
    page.wait_for_selector("#stage-title")
    third = _pick_sector(page, 2)
    _build_chain(page, "3000")

    cards = _saved_cards(page)
    assert len(cards) == 2, f"precondition: two saved entries and a complete draft, got {cards}"
    assert "1,000.00" in cards[0] and first in cards[0], f"precondition: entry 1 is {first!r}: {cards[0]!r}"
    assert "2,000.00" in cards[1] and second in cards[1], f"precondition: entry 2 is {second!r}: {cards[1]!r}"
    assert _current_entry_heading(page) == "Current entry 3", (
        f"precondition: the draft is the third chain, got {_current_entry_heading(page)!r}"
    )

    page.click('.saved-entry-card [data-action="edit-entry"] >> nth=0')
    _walk_forward_to_review(page)

    cards = _saved_cards(page)
    assert len(cards) == 2, (
        f"a swap trades two chains and creates none, so the list is still two long; "
        f"got {len(cards)}: {cards}"
    )
    assert "3,000.00" in cards[0] and third in cards[0], (
        f"the displaced draft ({third!r}, 3,000) belongs in the opened entry's own slot, "
        f"which is entry 1; entry 1 reads {cards[0]!r}"
    )
    assert "2,000.00" in cards[1] and second in cards[1], (
        f"entry 2 was not involved in this click and may not have moved or been "
        f"renumbered; entry 2 reads {cards[1]!r}"
    )
    assert _card_numbers(page) == ["entry 1", "entry 2"], (
        f"the cards are numbered by position: {_card_numbers(page)}"
    )
    assert "1,000.00" in _review_amounts(page), (
        f"the entry that was opened is the draft: {_review_amounts(page)!r}"
    )
    assert first in _draft_stage(page), (
        f"the draft should be the opened chain ({first!r}), got {_draft_stage(page)!r}"
    )
    assert seen == [], f"a complete draft swaps silently; got {seen}"
