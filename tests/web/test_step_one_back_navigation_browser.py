"""Back on step 1 returns the visitor where they came from, and undoes the jump
that took them there.

**The defect, as the repository owner hit it.** On the review step (step 5 on
screen) they pressed "Add another supply-chain entry", which took them to step
1. They changed their mind and pressed Back — and landed on the
*introduction*, not on the review step they had just left.

`web/js/calculator.js`'s `sectorStep` rendered `stepNav({ step: 0, back: -1 })`
unconditionally, so step 1's Back has exactly one destination whatever brought
the visitor there. Nothing is lost by it — `go-step` and `start` only set
`step`, so the entries and the draft survive and "Start calculator" returns to
step 1 with everything intact. It is a navigation dead end, not data loss.

**Why the one-line fix is wrong, and why this file asserts content rather than
a heading.** Making the back target the review step whenever `state.entries`
is non-empty returns the visitor to a review step built from a draft that
`add-entry` has *already emptied*: an empty "Current entry 2" and a Calculate
button offering to submit it. Backing out of an add has to undo the add. So
every assertion below that lands on the review step also asserts what that
step is showing — how many saved cards, which entry number the current block
carries, and the draft's own destination figures — because a test that only
waits for `#review-title` passes against the known-wrong fix.

**Every arrival at step 1 is covered, not only the add.** The interface has
six of them and they want three different answers:

===  ===============================================  ==========================
Row  Arrival                                          Back goes to
===  ===============================================  ==========================
A    "Start calculator" on the introduction           the introduction
B    "Add another supply-chain entry" on review       review, add undone
C    "Edit" on a saved entry card on review           review, edit undone
D    "Open entry N" in step 2's duplicate notice      step 2, edit undone
E    "Edit" beside Supply-chain stage on review       review, nothing undone
F    a refused Calculate routed to step 1             review, nothing undone
G    walking Back from review down to step 1          the introduction
===  ===============================================  ==========================

Row G is the deliberate limitation and the sharpest risk in the fix: the
marker that records the arrival is cleared the moment the visitor reaches the
review step again, and without that clearing a *stale* marker would fire hours
later and restore a pre-add snapshot over a chain built since. That is data
loss introduced by the fix, so it has its own test
(`test_a_chain_built_after_an_add_is_never_restored_away`) which walks the full
six-click path and then proves the entries survived.

**Driven against the real API**, like
`test_duplicate_entry_routing_browser.py`, except in row F: `UNKNOWN_CODE` is
the one rejection that routes a visitor to step 1 and it cannot be provoked
through the form, whose radios only ever offer codes the published set holds.
That one test fulfils the POST itself, the way
`test_step_navigation.py`'s `BLOCKED` test does, because its subject is where
the front end goes afterwards rather than what the server said.

**Running this**::

    pip install playwright && playwright install chromium
    docker compose -f docker/compose.yaml up -d --build web
    pytest tests/web/test_step_one_back_navigation_browser.py

Restart ``kaicalc-api`` between this file and any other browser test file — see
``test_amount_limits_browser.py``'s header for why a sweep exhausts the
taxonomy rate limit.
"""

from __future__ import annotations

import json
import os

import pytest

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to drive the wizard the way the owner drove it",
)

BASE = os.environ.get("KAICALC_WEB_URL", "http://localhost:18080").rstrip("/") + "/index.html"

#: Each screen, and a selector only that screen renders. Used by `_screen` below so a
#: failure names the screen the visitor actually landed on. Asserting "the review step
#: is visible" and nothing else produces a red state that says only which selector was
#: missing; naming the screen that IS on the page is the difference between a legible
#: failure and a 15-second selector timeout.
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
    """The screen on the page right now, by name."""
    for name, selector in SCREENS:
        if page.locator(selector).count():
            return name
    return "no recognisable screen"


def _pick_sector(page, index: int) -> str:
    """Select the `index`th supply-chain stage and return its visible name.

    The name is read off the card rather than hard-coded: the sector list is
    seeded data and a test that names "Primary production" breaks the day the
    seed is re-ordered, without anything being wrong.
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


def _back(page):
    page.click('.step-nav [data-action="go-step"]')
    page.wait_for_timeout(250)


def _saved_cards(page) -> int:
    return page.locator(".saved-entry-card").count()


def _current_entry_heading(page) -> str:
    return page.locator(".current-entry-heading h2").inner_text().strip()


def _review_amounts(page) -> str:
    """The draft's own destination figures, as the review step prints them."""
    return page.locator(".review-destinations").inner_text()


def test_the_first_visit_still_backs_out_to_the_introduction(page):
    """Row A, and the behaviour that must not change. A visitor who pressed
    "Start calculator" has an introduction behind them and nothing else; this
    is the path `test_step_navigation.py`'s `walk` drives on every step."""
    page.click('[data-action="start"]')
    page.wait_for_selector("#stage-title")
    _back(page)
    assert _screen(page) == "the introduction", (
        f"the only screen behind a first-run step 1 is the introduction, got {_screen(page)}"
    )


def test_backing_out_of_an_add_returns_to_review_with_the_add_undone(page):
    """Row B — the owner's own report, and the reason it is not a one-liner.

    `add-entry` pushes the draft into `state.entries` and then clears the
    draft, so a Back that merely changed `step` would show a review step with
    an empty "Current entry 2" and a Calculate button offering to submit it.
    The three assertions after the screen check are what separate the fix from
    that wrong answer.
    """
    page.click('[data-action="start"]')
    _pick_sector(page, 0)
    _build_chain(page, "1000")
    page.click('[data-action="add-entry"]')
    page.wait_for_selector("#stage-title")

    _back(page)

    assert _screen(page) == "step 5 (review)", (
        f"Back out of an add belongs on the review step it was pressed from, got {_screen(page)}"
    )
    assert _saved_cards(page) == 0, (
        f"the add has to be undone, not merely navigated away from: {_saved_cards(page)} "
        "saved entry card(s) remain, so the chain was committed and the visitor is "
        "looking at a review of two entries they only ever built one of"
    )
    assert _current_entry_heading(page) == "Current entry 1", (
        f"the draft the add emptied has to come back: heading reads "
        f"{_current_entry_heading(page)!r}"
    )
    assert "1,000.00" in _review_amounts(page), (
        f"the current entry is empty — the review step is showing a draft `clearDraft` "
        f"wiped: {_review_amounts(page)!r}"
    )
    assert page.locator('[data-action="calculate"]').inner_text().strip() == "Calculate impact", (
        "with the add undone there is one entry, so the button is the single-entry one; "
        f"got {page.locator('[data-action=calculate]').inner_text().strip()!r}"
    )


def test_backing_out_of_an_edit_from_the_review_list_restores_both(page):
    """Row C. `edit-entry` removes the entry from the list *and* overwrites the
    draft with it, so backing out has to put both back — the entry at its own
    position in the list, and the chain that was showing as "Current entry 2"
    back in the draft slot."""
    page.click('[data-action="start"]')
    _pick_sector(page, 0)
    _build_chain(page, "1000")
    page.click('[data-action="add-entry"]')
    _pick_sector(page, 1)
    _build_chain(page, "2000")
    assert _saved_cards(page) == 1, "precondition: one saved chain and one draft"

    page.click('.saved-entry-card [data-action="edit-entry"]')
    page.wait_for_selector("#stage-title")
    _back(page)

    assert _screen(page) == "step 5 (review)", (
        f"Edit was pressed on the review step, so Back belongs there, got {_screen(page)}"
    )
    assert _saved_cards(page) == 1, (
        f"the entry `edit-entry` removed has to go back on the list, got {_saved_cards(page)} card(s)"
    )
    assert _current_entry_heading(page) == "Current entry 2", (
        f"the draft `loadEntry` overwrote has to come back, heading reads "
        f"{_current_entry_heading(page)!r}"
    )
    assert "2,000.00" in _review_amounts(page), (
        f"the restored draft is the 2,000 chain that was on screen when Edit was "
        f"pressed, not the entry that was opened: {_review_amounts(page)!r}"
    )


def test_backing_out_of_an_edit_from_the_duplicate_notice_returns_to_step_two(page):
    """Row D. The commit below this one put an `edit-entry` button in step 2's
    duplicate notice, so `edit-entry` is reachable from two screens now and
    Back has to return to whichever one it was pressed on — not to a constant.

    The half-built draft the notice displaced (a sector, and no category yet)
    comes back with it, which is what makes the notice render again.
    """
    page.click('[data-action="start"]')
    _pick_sector(page, 0)
    _build_chain(page, "1000")
    page.click('[data-action="add-entry"]')
    _pick_sector(page, 0)
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector(".duplicate-notice")

    page.click('.duplicate-notice [data-action="edit-entry"]')
    page.wait_for_selector("#stage-title")
    _back(page)

    assert _screen(page) == "step 2 (food type)", (
        f"the notice was pressed on step 2, so Back belongs on step 2 — not on the "
        f"review step and not on the introduction, got {_screen(page)}"
    )
    assert page.locator(".saved-entry-card").count() == 0, "step 2 shows no cards"
    assert page.locator(".duplicate-notice").is_visible(), (
        "the notice reads the sector and the saved entry that were both restored; if "
        "it is gone, one of the two was not put back"
    )


def test_backing_out_of_a_review_edit_link_returns_to_review(page):
    """Row E — the same dead end through a door with no add behind it. The
    review step's "Supply-chain stage / Edit" link is a plain `go-step` to
    step 1, and today Back from there also lands on the introduction.

    Nothing was moved, so nothing is undone: a sector changed while on step 1
    is kept, exactly as a field edited on steps 2-4 is kept when Back is
    pressed there.
    """
    page.click('[data-action="start"]')
    _pick_sector(page, 0)
    _build_chain(page, "1000")

    page.click('.review-block [data-action="go-step"][data-step="0"]')
    page.wait_for_selector("#stage-title")
    second = _pick_sector(page, 1)
    _back(page)

    assert _screen(page) == "step 5 (review)", (
        f"Edit was pressed on the review step, so Back belongs there, got {_screen(page)}"
    )
    assert second in page.locator(".review-block").first.inner_text(), (
        "a navigation that moved nothing must revert nothing: the sector chosen on "
        f"step 1 should still be {second!r}"
    )


def test_a_refused_calculate_routed_to_step_one_can_get_back_to_review(page):
    """Row F. `submitCalculation` sends an `UNKNOWN_CODE` refusal to step 1 —
    the visitor's selections are what the message asks them to review — and a
    visitor sent there by a refusal came from the review step just as surely
    as one who pressed Edit.

    The POST is fulfilled here rather than provoked: the radios only ever
    offer codes the published set holds, so the form cannot produce this
    rejection. The subject is where the front end goes afterwards.
    """
    page.click('[data-action="start"]')
    _pick_sector(page, 0)
    _build_chain(page, "1000")
    page.route(
        "**/api/v1/calculate",
        lambda route: route.fulfill(
            status=400,
            content_type="application/json",
            body=json.dumps(
                {
                    "error": {
                        "code": "UNKNOWN_CODE",
                        "message": "sector 'gone' is not in the published factor set",
                        "details": None,
                    }
                }
            ),
        ),
    )
    page.click('[data-action="calculate"]')
    page.wait_for_selector("#stage-title", timeout=15000)
    assert _screen(page) == "step 1 (supply-chain stage)", (
        f"precondition: an UNKNOWN_CODE refusal routes to step 1, got {_screen(page)}"
    )

    _back(page)
    assert _screen(page) == "step 5 (review)", (
        f"a visitor pushed to step 1 by a refusal came from the review step; sending "
        f"them to the introduction strands the chain they were refused on, got {_screen(page)}"
    )


def test_walking_back_out_of_the_wizard_still_reaches_the_introduction(page):
    """Row G, the stated limitation. Step by step backwards from review is a
    linear walk, and the screen before step 1 on that walk is the
    introduction. This is asserted rather than merely tolerated so that a
    later change making Back universally "return to review" is caught here
    instead of leaving the introduction unreachable."""
    page.click('[data-action="start"]')
    _pick_sector(page, 0)
    _build_chain(page, "1000")

    for expected in (
        "step 4 (destinations)",
        "step 3 (waste amount)",
        "step 2 (food type)",
        "step 1 (supply-chain stage)",
        "the introduction",
    ):
        _back(page)
        assert _screen(page) == expected, f"expected {expected}, got {_screen(page)}"


def test_a_chain_built_after_an_add_is_never_restored_away(page):
    """The data-loss risk the fix itself introduces, driven end to end.

    A marker recording "you got here by pressing Add" holds the entries and
    the draft as they were *before* the add. If it survives past the moment
    the visitor reaches the review step again, this six-click path fires it
    over a chain that did not exist when it was written, and the second chain
    is destroyed:

        add → build chain 2 → review → Back ×4 to step 1 → Back

    So the marker is cleared on arrival at review, and Back at the end of that
    walk is row G's plain walk out to the introduction. Both halves are
    asserted, because a test that only checks the screen passes against a fix
    that cleared nothing.
    """
    page.click('[data-action="start"]')
    first = _pick_sector(page, 0)
    _build_chain(page, "1000")
    page.click('[data-action="add-entry"]')
    page.wait_for_selector("#stage-title")
    _pick_sector(page, 1)
    _build_chain(page, "2000")
    assert _saved_cards(page) == 1, "precondition: chain 1 saved, chain 2 in the draft"

    for _ in range(5):
        _back(page)
    assert _screen(page) == "the introduction", (
        f"five Backs from review is a linear walk out of the wizard, got {_screen(page)}"
    )

    page.click('[data-action="start"]')
    page.wait_for_selector("#stage-title")
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector("#food-title")
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector("#total-waste")
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector("#destination-title")
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector("#review-title")

    assert _saved_cards(page) == 1, (
        f"a stale marker restored a pre-add snapshot over the work done since: "
        f"{_saved_cards(page)} saved card(s)"
    )
    assert first in page.locator(".saved-entry-card").first.inner_text(), (
        "the saved chain is the first one, unchanged"
    )
    assert _current_entry_heading(page) == "Current entry 2", (
        f"chain 2 is still the draft, got {_current_entry_heading(page)!r}"
    )
    assert "2,000.00" in _review_amounts(page), (
        f"chain 2's own figures were destroyed by a restore: {_review_amounts(page)!r}"
    )


# ---------------------------------------------------------------------------
# The confirmation, and the other three review Edit links.
#
# Everything above this line was written against the first version of the fix.
# Three defects were then found in it, and the two that are behavioural are
# covered here.
#
# **Defect 1 - the undo fired silently after an unbounded forward walk.** The
# marker is carried forward on every `continue` except the one that arrives at
# review, so a visitor who pressed Add, built a whole second chain through the
# destination step and then walked Back to step 1 still had a live marker - and
# pressing Back there threw four screens of input away without a word. The same
# journey *kept* the work if the visitor happened to press Continue on the
# destination step, and there was no way to see which case you were in. The
# owner's ruling: confirm before discarding. The undo keeps its semantics and
# stops being silent.
#
# **Defect 2 - the dead end survived on three of the four review Edit links.**
# Only "Supply-chain stage / Edit" wrote a marker. "Food category / Edit" went
# to step 2, whose Back was hard-coded to step 1, whose Back then had no marker:
# two presses and the visitor is on the introduction, which is the original
# complaint.
#
# `window.confirm` blocks the renderer, so every test below that can reach a
# dialog registers a handler *before* the click. `_dialogs` records the message
# as well as handling it, which is what lets a test assert that no dialog
# appeared - an assertion that reads `[] == [...]` rather than a selector
# timeout.
# ---------------------------------------------------------------------------


def _dialogs(page, accept: bool) -> list:
    """Handle every `window.confirm` this page raises, and record its message.

    Returns the list, which fills as the test runs. Registering the handler is
    not optional: Playwright auto-dismisses an unhandled dialog, so a test
    without one cannot tell "no confirmation was asked" from "a confirmation
    was asked and silently declined", and those are the two answers this whole
    section is about.
    """
    seen: list = []

    def handle(dialog):
        seen.append(dialog.message)
        dialog.accept() if accept else dialog.dismiss()

    page.on("dialog", handle)
    return seen


def _build_to_destinations(page, amount: str):
    """Steps 1 to 4 with the sector already chosen, stopping *on* the
    destination step - Continue is never pressed there.

    That is the owner's journey and it is the half `_build_chain` cannot
    express: the marker is cleared by the `continue` that arrives at review, so
    a chain built all the way to review is precisely the path where the defect
    does not reproduce.
    """
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector('input[name="food-category"]')
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector("#total-waste")
    page.fill("#total-waste", amount)
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector('[data-line-field="amount"]')
    page.fill('[data-line-field="amount"] >> nth=0', amount)
    page.wait_for_timeout(100)


def _walk_forward_to_review(page):
    """Continue until the review step, from wherever the visitor is standing."""
    for selector in ("#food-title", "#total-waste", "#destination-title", "#review-title"):
        page.click('.step-nav [data-action="continue"]')
        page.wait_for_selector(selector, timeout=10000)


def _owners_journey(page, accept: bool) -> list:
    """The journey the owner drove in a real browser, up to the press that
    decides: review -> Add -> a whole second chain through the destination step
    -> Back, Back, Back to step 1 -> Back.

    Returns the recorded dialog messages.
    """
    page.click('[data-action="start"]')
    _pick_sector(page, 0)
    _build_chain(page, "1000")
    page.click('[data-action="add-entry"]')
    page.wait_for_selector("#stage-title")
    _pick_sector(page, 1)
    _build_to_destinations(page, "777")
    assert _screen(page) == "step 4 (destinations)", (
        f"precondition: the second chain stops on the destination step, got {_screen(page)}"
    )

    seen = _dialogs(page, accept)
    for expected in ("step 3 (waste amount)", "step 2 (food type)", "step 1 (supply-chain stage)"):
        _back(page)
        assert _screen(page) == expected, f"precondition: expected {expected}, got {_screen(page)}"
    _back(page)
    return seen


def test_the_owners_journey_asks_before_discarding_four_screens_of_input(page):
    """Defect 1, accepting. The dialog has to exist, and it has to be about the
    new entry rather than about "changes"."""
    seen = _owners_journey(page, accept=True)

    assert seen, (
        "no confirmation was shown: four screens of input (a sector, 777 kg and a "
        "destination allocation) were discarded silently, which is the defect"
    )
    assert len(seen) == 1, f"one press of Back, one question; got {seen}"
    assert "discard" in seen[0].lower(), (
        f"the question has to say what it will do before it does it, got {seen[0]!r}"
    )
    assert "entry" in seen[0].lower(), (
        f"the question has to name what is at risk, got {seen[0]!r}"
    )
    assert _screen(page) == "step 5 (review)", (
        f"accepting means going back, so the visitor lands on review, got {_screen(page)}"
    )
    assert _saved_cards(page) == 0, (
        f"accepting means the add is undone, got {_saved_cards(page)} saved card(s)"
    )
    assert _current_entry_heading(page) == "Current entry 1", (
        f"the pre-add draft comes back, heading reads {_current_entry_heading(page)!r}"
    )
    assert "1,000.00" in _review_amounts(page), (
        f"the restored draft is the 1,000 chain, got {_review_amounts(page)!r}"
    )


def test_the_owners_journey_declining_keeps_every_screen_of_input(page):
    """Defect 1, declining - the half that proves the dialog is a choice rather
    than a notification.

    Cancel must leave the visitor exactly where they were with everything
    intact, so this asserts the screen *and* then walks forward to review and
    reads the numbers off it. A test that only checked the screen would pass
    against a fix that discarded the work and merely stayed on step 1.
    """
    seen = _owners_journey(page, accept=False)

    assert seen, "declining cannot be tested if nothing was ever asked"
    assert _screen(page) == "step 1 (supply-chain stage)", (
        f"declining leaves the visitor exactly where they were, got {_screen(page)}"
    )
    assert page.locator('input[name="sector"]:checked').count() == 1, (
        "the sector chosen after the add is part of what declining keeps"
    )

    _walk_forward_to_review(page)
    assert _saved_cards(page) == 1, (
        f"the first chain was committed by the add and declining does not un-commit it: "
        f"{_saved_cards(page)} saved card(s)"
    )
    assert _current_entry_heading(page) == "Current entry 2", (
        f"the second chain is still the draft, got {_current_entry_heading(page)!r}"
    )
    assert "777.00" in _review_amounts(page), (
        f"declining threw the second chain away anyway: {_review_amounts(page)!r}"
    )


def test_an_add_backed_out_of_immediately_asks_nothing(page):
    """The false-positive half of the test that decides whether to ask.

    Press Add, press Back: the draft is the empty one `clearDraft` just wrote,
    so the undo discards nothing and a dialog here would be noise that trains
    people to dismiss dialogs without reading. The undo still happens.
    """
    page.click('[data-action="start"]')
    _pick_sector(page, 0)
    _build_chain(page, "1000")
    page.click('[data-action="add-entry"]')
    page.wait_for_selector("#stage-title")

    seen = _dialogs(page, accept=False)
    _back(page)

    assert seen == [], (
        f"nothing had been entered since the add, so there was nothing to discard and "
        f"nothing to ask about; got {seen}"
    )
    assert _screen(page) == "step 5 (review)", (
        f"the undo still runs when it discards nothing, got {_screen(page)}"
    )
    assert _saved_cards(page) == 0 and _current_entry_heading(page) == "Current entry 1", (
        f"{_saved_cards(page)} saved card(s), heading {_current_entry_heading(page)!r}"
    )


def test_an_edit_backed_out_of_immediately_asks_nothing(page):
    """The same, through the other door. Opening a saved entry and pressing Back
    without touching anything changes nothing, so it asks nothing."""
    page.click('[data-action="start"]')
    _pick_sector(page, 0)
    _build_chain(page, "1000")
    page.click('[data-action="add-entry"]')
    _pick_sector(page, 1)
    _build_chain(page, "2000")

    page.click('.saved-entry-card [data-action="edit-entry"]')
    page.wait_for_selector("#stage-title")
    seen = _dialogs(page, accept=False)
    _back(page)

    assert seen == [], f"the opened entry was not changed, so nothing is discarded; got {seen}"
    assert _screen(page) == "step 5 (review)", f"got {_screen(page)}"
    assert _saved_cards(page) == 1, f"got {_saved_cards(page)} saved card(s)"
    assert _current_entry_heading(page) == "Current entry 2", (
        f"got {_current_entry_heading(page)!r}"
    )


def test_an_edit_changed_before_backing_out_asks_about_the_entry_that_was_opened(page):
    """Defect 1's second message. An add discards *a chain being started*; an
    edit discards *changes to a saved entry*. They are different losses and the
    question says which.

    **It says which without naming a number, and this test used to require the
    opposite.** It asserted "entry 1", which was the number on the button the
    visitor pressed and, from the commit that made `edit-entry` a swap, no
    longer the number of anything the question was about: the swap puts the
    displaced chain into the opened entry's slot, so the card numbered 1 is a
    *different* chain — visible, on the review step, under that number — while
    the entry being asked about has left the list for the draft slot and has no
    number at all. The assertions below are the same three claims with the
    third corrected: it is about an edit, it is about the entry that was opened,
    and it must not point at a card that is now somebody else.

    Declining here also has to be safe, so this test dismisses and then proves
    the change survived.
    """
    page.click('[data-action="start"]')
    opened = _pick_sector(page, 0)
    _build_chain(page, "1000")
    page.click('[data-action="add-entry"]')
    displaced = _pick_sector(page, 1)
    _build_chain(page, "2000")

    page.click('.saved-entry-card [data-action="edit-entry"]')
    page.wait_for_selector("#stage-title")
    changed = _pick_sector(page, 2)
    seen = _dialogs(page, accept=False)
    _back(page)

    assert seen, "a changed entry backed out of discards the change, so it has to ask"
    assert "change" in seen[0].lower(), (
        f"an edit discards changes, not a new entry; got {seen[0]!r}"
    )
    assert "the entry you opened" in seen[0].lower(), (
        f"the question is about the entry the visitor opened ({opened!r}), which the "
        f"swap has moved off the list; got {seen[0]!r}"
    )
    assert "entry 1" not in seen[0].lower(), (
        f"entry 1 is now the chain this click displaced ({displaced!r}), so naming that "
        f"number points the visitor at the one card the question is not about; got "
        f"{seen[0]!r}"
    )
    assert _screen(page) == "step 1 (supply-chain stage)", (
        f"declining leaves the visitor on step 1, got {_screen(page)}"
    )
    assert changed in page.locator(".stage-card.selected").inner_text(), (
        f"declining kept the visitor here but dropped their change; expected {changed!r}"
    )


REVIEW_EDIT_LINKS = (
    (0, "step 1 (supply-chain stage)"),
    (1, "step 2 (food type)"),
    (2, "step 3 (waste amount)"),
    (3, "step 4 (destinations)"),
)


@pytest.mark.parametrize("step,landing", REVIEW_EDIT_LINKS)
def test_every_review_edit_link_backs_out_to_the_review_step(page, step, landing):
    """Defect 2. All four Edit links on the review step are the same promise -
    "go and change this one thing" - and all four have to come back to the
    review step they were pressed on.

    Only `data-step="0"` did. `data-step="1"` landed on step 2, whose Back was
    hard-coded to step 1, whose Back then had no marker: two presses and the
    visitor is on the introduction with the wizard behind them. Same for the
    other two.

    Nothing is moved by an Edit link, so nothing is undone and nothing is asked:
    the figure typed on the way through has to still be there at the end.
    """
    page.click('[data-action="start"]')
    _pick_sector(page, 0)
    _build_chain(page, "1000")

    seen = _dialogs(page, accept=False)
    page.click(f'.review-block [data-action="go-step"][data-step="{step}"]')
    page.wait_for_timeout(250)
    assert _screen(page) == landing, (
        f"precondition: the Edit link for step {step + 1} opens it, got {_screen(page)}"
    )

    _back(page)

    assert _screen(page) == "step 5 (review)", (
        f"Edit for step {step + 1} was pressed on the review step, so Back belongs "
        f"there - got {_screen(page)}"
    )
    assert seen == [], (
        f"an Edit link moves nothing, so backing out of one discards nothing and asks "
        f"nothing; got {seen}"
    )
    assert "1,000.00" in _review_amounts(page), (
        f"backing out of an Edit link must revert nothing: {_review_amounts(page)!r}"
    )


def test_a_marker_from_the_duplicate_notice_does_not_fire_on_another_steps_back(page):
    """The origin test inside the undo guard, driven - and it is reachable now
    that steps 2 to 4 consult the marker too.

    `edit-entry` pressed in step 2's duplicate notice writes a marker whose Back
    goes to step 2: `{from: 0, step: 1}`. Walk *forward* to step 3 instead of
    pressing Back, and step 3's own Back emits `data-step="1"` as well - the
    marker's `step` and this click's target are the same number, because the
    marker is about step 1 and the click is about step 3. Only
    `from === state.step` tells the two apart. Without it the undo fires on a
    Back it was never written for: the opened entry goes back onto the saved
    list and the half-built draft the notice displaced goes back into the draft
    slot, on a press that should have moved one screen and changed nothing.

    Asserted through the duplicate notice because that is exactly what the two
    states differ by. The notice renders when a *saved* entry repeats the
    draft's stage and category; after a correct Back the opened entry is the
    draft and the saved list is empty, so there is nothing for it to collide
    with. Its presence here is the wrong restore, visible.
    """
    page.click('[data-action="start"]')
    _pick_sector(page, 0)
    _build_chain(page, "1000")
    page.click('[data-action="add-entry"]')
    _pick_sector(page, 0)
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector(".duplicate-notice")

    page.click('.duplicate-notice [data-action="edit-entry"]')
    page.wait_for_selector("#stage-title")
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector("#food-title")
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector("#total-waste")
    assert _screen(page) == "step 3 (waste amount)", (
        f"precondition: the opened entry was walked forward to step 3, got {_screen(page)}"
    )

    seen = _dialogs(page, accept=False)
    _back(page)

    assert _screen(page) == "step 2 (food type)", (
        f"step 3's Back is one screen back, got {_screen(page)}"
    )
    assert page.locator(".duplicate-notice").count() == 0, (
        "a marker written for step 1's Back fired on step 3's: the entry that was "
        "opened is back on the saved list and the displaced draft is back in the draft "
        "slot, which is why the duplicate notice is on screen again"
    )
    assert seen == [], f"nothing was backed out of, so nothing is asked; got {seen}"
