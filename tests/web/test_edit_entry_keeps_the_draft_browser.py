"""Opening a saved entry must not destroy the chain the visitor is building.

**The defect, as the repository owner reproduced it twice in a real browser.**
Build a chain, press "Add another supply-chain entry", build a second one, and
stand on the review step with one saved card and a complete "Current entry 2".
Press *Edit* on the saved card. Then walk *forward* — Continue four times —
back to the review step.

Before this commit the second chain was gone. Not hidden, not recoverable:
gone, with no dialog and nothing on screen to say so.

    savedCards=1 ['Primary production'], Current entry 2 = 2,000.00
    -> savedCards=0 [],                  Current entry 1 = 1,000.00

**Why it happened.** `edit-entry` (`web/js/calculator.js`) filtered the opened
entry out of `state.entries` and then called `loadEntry`, which is a *load*:
`entryPatch` overwrites every key of the draft. The chain that was in the draft
slot survived only inside `state.returnTo.draft` — a navigation marker, which
`continue` drops on the step 3 -> 4 move, `start` drops and
`submitCalculation` drops. Walking forward discarded the only copy. The
pre-existing bug is that live data was being kept in a structure designed to be
dropped.

**Why the forward path is what this file drives.** Every assertion in
`test_step_one_back_navigation_browser.py` presses Back after `edit-entry`, so
the marker is still alive and the chain still exists. Nothing walked forward,
which is why an eighteen-test file covering this exact handler was green
against a handler that lost a whole supply-chain entry.

**The two doors, and why the tests below are not symmetrical.** `edit-entry`
has two call sites and the draft it displaces is complete at only one of them:

  * the review list's *Edit* button, where the draft has passed steps 1, 3 and
    4's gates to be there at all — it is a saved entry in everything but
    position, so it takes the saved entry's slot and nothing is lost;
  * step 2's duplicate notice, "Open entry N", where the visitor usually has a
    sector and a category and nothing else — and where the entry being opened
    carries that same pair by construction, so the load hands back everything
    the draft held and there is still nothing to lose.

Between them sits a third case with no silent answer: a draft that holds real
figures but is not finished, which cannot go on the saved list (`entryCard`
renders a sector, an amount and a category for every row, and
`submissionPayload` sends every row for the API to refuse) and cannot be thrown
away quietly either. That one asks, and both answers are driven below.

**Running this**::

    pip install playwright && playwright install chromium
    docker compose -f docker/compose.yaml up -d --build web
    pytest tests/web/test_edit_entry_keeps_the_draft_browser.py

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
    apart from "nothing was asked". Both answers matter here, so both are
    driven.
    """
    seen: list = []

    def handle(dialog):
        seen.append(dialog.message)
        dialog.accept() if accept else dialog.dismiss()

    page.on("dialog", handle)
    return seen


def _pick_sector(page, index: int) -> str:
    """Select the `index`th supply-chain stage and return its visible name.

    Read off the card rather than hard-coded, so a re-ordered seed does not
    fail a test with nothing wrong in it.
    """
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


def _walk_forward_to_review(page, amount: str | None = None):
    """Continue four times from step 1, filling nothing that is already filled.

    This is the owner's own forward walk: the entry that `edit-entry` loaded is
    complete, so every gate passes and the four presses need no typing. `amount`
    is for the case where the loaded chain has to be re-typed, which no test
    below needs but which keeps the helper honest about what it assumes.
    """
    page.wait_for_selector("#stage-title")
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector("#food-title")
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector("#total-waste")
    if amount is not None:
        page.fill("#total-waste", amount)
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector("#destination-title")
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector('[data-action="calculate"]')


def _back(page):
    page.click('.step-nav [data-action="go-step"]')
    page.wait_for_timeout(250)


def _saved_cards(page) -> int:
    return page.locator(".saved-entry-card").count()


def _saved_card_text(page) -> str:
    return page.locator(".saved-entry-card").inner_text()


def _current_entry_heading(page) -> str:
    return page.locator(".current-entry-heading h2").inner_text().strip()


def _review_amounts(page) -> str:
    """The draft's own destination figures, as the review step prints them."""
    return page.locator(".review-destinations").inner_text()


def test_the_owners_journey_keeps_the_chain_that_was_being_built(page):
    """The reproduction, exactly as reported, asserted on the numbers.

    E1 (1,000) saved, E2 (2,000) complete in the draft, *Edit* pressed on the E1
    card, then Continue four times rather than Back. Before the fix the walk
    forward dropped the marker holding E2 and E2 ceased to exist.

    The assertions name both chains, because "a saved card is present" is true
    of the broken behaviour too the moment a second entry is added — it is
    *which* chain is where that the defect got wrong.
    """
    seen = _dialogs(page, accept=False)
    page.click('[data-action="start"]')
    first = _pick_sector(page, 0)
    _build_chain(page, "1000")
    page.click('[data-action="add-entry"]')
    second = _pick_sector(page, 1)
    _build_chain(page, "2000")
    assert _saved_cards(page) == 1, "precondition: one saved chain and one complete draft"
    assert _current_entry_heading(page) == "Current entry 2", "precondition: the draft is the second chain"

    page.click('.saved-entry-card [data-action="edit-entry"]')
    _walk_forward_to_review(page)

    assert _screen(page) == "step 5 (review)", f"four Continues reach review, got {_screen(page)}"
    assert seen == [], (
        f"a complete chain takes the opened entry's place on the list, so nothing is "
        f"discarded and nothing may be asked; got {seen}"
    )
    assert _saved_cards(page) == 1, (
        f"the 2,000 chain that was in the draft slot when Edit was pressed has to still "
        f"exist: {_saved_cards(page)} saved card(s) on the review step"
    )
    assert second in _saved_card_text(page), (
        f"the saved card should now hold the displaced chain ({second!r}), got "
        f"{_saved_card_text(page)!r}"
    )
    assert "2,000.00" in _saved_card_text(page), (
        f"the displaced chain's own figure is missing from the list, so the chain was "
        f"not preserved: {_saved_card_text(page)!r}"
    )
    assert _current_entry_heading(page) == "Current entry 2", (
        f"the opened entry is the draft and there is still one saved entry beside it; "
        f"heading reads {_current_entry_heading(page)!r}"
    )
    assert "1,000.00" in _review_amounts(page), (
        f"the entry that was opened for editing has to be the one in the draft slot: "
        f"{_review_amounts(page)!r}"
    )
    assert first in page.locator(".review-block").first.inner_text(), (
        f"the draft should be the opened chain ({first!r}), got "
        f"{page.locator('.review-block').first.inner_text()!r}"
    )


def test_the_swap_survives_a_second_edit(page):
    """The exchange is reversible, and reversing it loses nothing either.

    Press *Edit* on the card a swap just put there and the two chains trade back.
    This is the property that makes the swap safe to do silently: it moves work,
    it never consumes it, however many times it is pressed.
    """
    seen = _dialogs(page, accept=False)
    page.click('[data-action="start"]')
    _pick_sector(page, 0)
    _build_chain(page, "1000")
    page.click('[data-action="add-entry"]')
    _pick_sector(page, 1)
    _build_chain(page, "2000")

    page.click('.saved-entry-card [data-action="edit-entry"]')
    _walk_forward_to_review(page)
    assert _saved_cards(page) == 1, (
        f"precondition: the first press must leave a card to press again — the displaced "
        f"chain is gone, so there is nothing to trade back: {_saved_cards(page)} card(s)"
    )
    page.click('.saved-entry-card [data-action="edit-entry"]')
    _walk_forward_to_review(page)

    assert seen == [], f"neither press discards anything, so neither may ask; got {seen}"
    assert _saved_cards(page) == 1, f"got {_saved_cards(page)} saved card(s)"
    assert "1,000.00" in _saved_card_text(page), (
        f"the two chains trade back, so the 1,000 chain is on the list again: "
        f"{_saved_card_text(page)!r}"
    )
    assert "2,000.00" in _review_amounts(page), (
        f"and the 2,000 chain is the draft again: {_review_amounts(page)!r}"
    )


def test_the_duplicate_notice_still_opens_its_entry_silently(page):
    """The notice door, in the state it was built for — and the list stays clean.

    The visitor pressed Add, chose a sector, and is on step 2 looking at "You
    have already entered this combination". Their draft is that sector and
    nothing else, and the entry the notice names carries the same pair: opening
    it hands back every figure the draft held, so there is nothing to ask about
    and nothing to preserve.

    The second half is the important half. A swap here would put a row with no
    amount and no destinations onto a list whose every consumer assumes a
    sendable entry — `entryCard` would print "0.00 kilograms" and
    `submissionPayload` would post it for the API to refuse. Walking forward and
    counting the cards is what catches that.
    """
    seen = _dialogs(page, accept=False)
    page.click('[data-action="start"]')
    _pick_sector(page, 0)
    _build_chain(page, "1000")
    page.click('[data-action="add-entry"]')
    _pick_sector(page, 0)
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector(".duplicate-notice")

    page.click('.duplicate-notice [data-action="edit-entry"]')
    _walk_forward_to_review(page)

    assert seen == [], (
        f"the opened entry gives the draft's sector straight back, so nothing is lost "
        f"and nothing may be asked; got {seen}"
    )
    assert _saved_cards(page) == 0, (
        f"the draft held nothing that could be a saved entry, so nothing may have been "
        f"put on the list; got {_saved_cards(page)} card(s): {_saved_card_text(page)!r}"
    )
    assert _current_entry_heading(page) == "Current entry 1", (
        f"one chain exists and it is the one that was opened; heading reads "
        f"{_current_entry_heading(page)!r}"
    )
    assert "1,000.00" in _review_amounts(page), (
        f"the opened entry is the draft: {_review_amounts(page)!r}"
    )


def _notice_over_a_part_built_chain(page) -> str:
    """Stand on step 2's duplicate notice with a figure typed and no destinations.

    Add -> sector -> Continue -> Continue -> type 777 -> Back. Step 3's Back is
    step 2, `state.current` is still empty because only `continue` from step 3
    populates it, and the notice is on screen because the sector repeats the
    saved entry's pair. The draft is therefore neither complete nor empty —
    the one case with no silent answer.
    """
    page.click('[data-action="start"]')
    _pick_sector(page, 0)
    _build_chain(page, "1000")
    page.click('[data-action="add-entry"]')
    name = _pick_sector(page, 0)
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector("#food-title")
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector("#total-waste")
    page.fill("#total-waste", "777")
    _back(page)
    assert _screen(page) == "step 2 (food type)", (
        f"precondition: step 3's Back is step 2, got {_screen(page)}"
    )
    assert page.locator(".duplicate-notice").count() == 1, (
        "precondition: the sector repeats the saved entry, so the notice is showing"
    )
    return name


def test_a_part_built_chain_is_never_discarded_without_asking(page):
    """The third case, declined — and declining has to be completely safe.

    A draft holding 777 kg cannot go on the saved list (no destinations, so
    `entryCard` would show an amount against nothing and the API would refuse
    the submission) and must not be thrown away in silence either. So the
    visitor is asked, and Cancel leaves everything exactly where it stood: same
    step, same figure, same saved entry.
    """
    _notice_over_a_part_built_chain(page)
    seen = _dialogs(page, accept=False)
    page.click('.duplicate-notice [data-action="edit-entry"]')
    page.wait_for_timeout(250)

    assert seen, "opening the entry would destroy a typed figure, so it has to ask first"
    assert "1" in seen[0], f"the question names the entry the button named, got {seen[0]!r}"
    assert _screen(page) == "step 2 (food type)", (
        f"declining moves nothing and navigates nowhere, got {_screen(page)}"
    )
    assert page.locator(".duplicate-notice").count() == 1, (
        "declining must leave the saved entry on the list — the notice reads it"
    )
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector("#total-waste")
    assert page.input_value("#total-waste") == "777", (
        f"declining kept the visitor here but dropped the figure the dialog was about; "
        f"got {page.input_value('#total-waste')!r}"
    )


def test_a_part_built_chain_is_discarded_when_the_visitor_accepts(page):
    """The same dialog, accepted. The visitor asked for the entry; they get it.

    Accepting is today's behaviour — the part-built draft goes, the opened entry
    becomes the draft — and it is correct *because it was asked for*. What the
    fix removes is the silence, not the discard.
    """
    _notice_over_a_part_built_chain(page)
    seen = _dialogs(page, accept=True)
    page.click('.duplicate-notice [data-action="edit-entry"]')
    _walk_forward_to_review(page)

    assert len(seen) == 1, f"asked exactly once, got {seen}"
    assert _saved_cards(page) == 0, (
        f"the opened entry left the list to become the draft, got {_saved_cards(page)} card(s)"
    )
    assert _current_entry_heading(page) == "Current entry 1", (
        f"got {_current_entry_heading(page)!r}"
    )
    assert "1,000.00" in _review_amounts(page), (
        f"the entry the visitor asked for is the draft: {_review_amounts(page)!r}"
    )


def test_the_review_step_can_hold_an_unfinished_draft_and_still_asks(page):
    """The review door is *not* always complete, which is why the gate reads the
    draft rather than the button that was pressed.

    `docs/interfaces.md` says the review step is only reachable with a complete
    draft, and the ordinary way in makes that true. But the *Waste amount / Edit*
    link is a jump, and its Back runs no validation at all: clear the field,
    press Back, and the review step renders with a blank amount. A rule of the
    form "pressed on review, therefore swap" would put that blank row onto the
    saved list. A rule that reads the draft asks instead — the chain still holds
    its destination figures, and they would really be destroyed.
    """
    page.click('[data-action="start"]')
    _pick_sector(page, 0)
    _build_chain(page, "1000")
    page.click('[data-action="add-entry"]')
    _pick_sector(page, 1)
    _build_chain(page, "2000")

    page.click('.review-block [data-action="go-step"][data-step="2"]')
    page.wait_for_selector("#total-waste")
    page.fill("#total-waste", "")
    page.wait_for_timeout(120)
    _back(page)
    assert _screen(page) == "step 5 (review)", (
        f"precondition: the Edit link's Back returns to review unvalidated, got {_screen(page)}"
    )

    seen = _dialogs(page, accept=False)
    page.click('.saved-entry-card [data-action="edit-entry"]')
    page.wait_for_timeout(250)

    assert seen, (
        "the draft has no amount, so it cannot be kept as a saved entry, and it still "
        "holds destination figures that opening the card would destroy — it has to ask"
    )
    assert _screen(page) == "step 5 (review)", (
        f"declining navigates nowhere, got {_screen(page)}"
    )
    assert _saved_cards(page) == 1, (
        f"declining moves nothing, got {_saved_cards(page)} saved card(s)"
    )
    assert "2,000.00" in _review_amounts(page), (
        f"declining kept the draft's destination figures: {_review_amounts(page)!r}"
    )
