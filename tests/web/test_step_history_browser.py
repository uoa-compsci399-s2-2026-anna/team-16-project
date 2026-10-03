"""One history entry per step, so Back and Forward move *inside* the calculator.

**The defect, reproduced before anything was written.** ``pushState``,
``replaceState`` and ``popstate`` appeared zero times in ``web/js/``.
``location.pathname`` is ``/`` from the introduction to the results and which
screen is up is ``state.step``, in memory, so the whole wizard was one history
entry: pressing Back on step 4 did not go to step 3, it left the calculator
altogether and landed on whatever the visitor had been looking at before they
arrived. §7.2a gave the answers back across that press; nothing stopped the
press leaving.

Every assertion here is read off a real browser after a real Back or Forward
press, because none of this can be reasoned about from the source:

* **Back from a middle step lands on the previous step, with its answers**, and
  **Forward returns**. The two together, because either alone can pass while the
  page is wrong: a step can be restored with the boxes empty, and a box can hold
  a value on a screen nobody can see.
* **Back from the results page lands on the review step** rather than leaving.
* **Back out of the introduction leaves the calculator** — which is also the
  measurement for *the first load replaces and does not push*. Had the arrival
  entry been pushed, that press would land back on the introduction and the
  first Back of every visitor's session would do nothing visible.
* **A re-render is not a navigation.** ``render()`` runs on every ``setState``
  and most of those are typing, ticking and tab changes; ``history.length`` is
  measured across a burst of them and must not move.
* **The step bar's Back and the browser's Back are the same navigation.**
  Asserted three ways: on a review *Edit* excursion, where ``state.returnTo``
  points Back at the review step and the browser's Back has to agree; on the
  confirmation dialog, which must be the same sentence with the same words; and
  on the *undo*, which is the part a bare ``setState({step})`` would silently
  skip.
* **A declined discard costs nothing**, including the history position. A
  ``popstate`` cannot be cancelled, so the position has to be travelled back.
* **A restored load's history walks back through the steps the visitor
  visited**, one screen per press, each with its own answers — and the restore
  itself adds no entry.
* **``start-over`` cannot be Backed into.** Entries cannot be deleted, so the
  unwind travels back to the entry the calculator opened in; the entries above it
  are answered with the introduction rather than with a step of a calculation
  that no longer exists.
* **A revalidation that moves the visitor rewrites its entry**, because §7.2a's
  check sending a restored draft back to step 0 is the page load moving them and
  not a press of theirs.
* **The URL never changes.** The step is in ``history.state``, not in the URL —
  see ``web/js/history.js`` for why a deep link was refused.
* **A browser that refuses to write history still has a calculator.** The first
  write happens at module evaluation time, so unguarded it takes the whole page
  with it -- the same hazard ``sessionStorage`` is already wrapped against.

The walk is modelled on ``test_session_restore_browser.py``'s, which is the file
that already drives this form correctly: ``press_continue`` rather than a bare
click, because the published set has ``item_level_enabled: true`` and step 2's
Continue opens step 2.5 (``tests/web/steps.py``).

Requires the stack, and the image rebuilt -- ``web/`` is baked in by
``docker/web.Dockerfile``::

    docker compose -f docker/compose.yaml build web
    docker compose -f docker/compose.yaml up -d --force-recreate web
    pytest tests/web/test_step_history_browser.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.web.base_url import ORIGIN
from tests.web.steps import press_continue


# The `browser` marker is applied by `conftest.py`, by location: every module
# here is a browser suite unless it is named in its `NOT_A_BROWSER_SUITE`.

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to press Back in a real browser",
)

ORIGIN = ORIGIN
BASE = ORIGIN + "/index.html?lang=en"

ROOT = Path(__file__).resolve().parents[2]
#: The contract fixture the results view is reached from, fulfilled in the browser
#: rather than calculated for real: a real POST writes a `submission` row, and a
#: test that pollutes the public statistics to look at a screen is the wrong trade.
CALCULATE_FIXTURE = json.loads(
    (ROOT / "tests" / "fixtures" / "calculate_response_single.json").read_text(encoding="utf-8")
)

#: `web/js/history.js` owns exactly these two keys inside `history.state`.
STEP_KEY = "kaiStep"
INDEX_KEY = "kaiIndex"

STAGE = "Where in the food supply chain did this waste occur?"
FOOD = "What types of food waste are you measuring?"
AMOUNT = "How much food waste are you measuring?"
DESTINATIONS = "Where did the food waste go?"
REVIEW = "Review your information"
RESULTS = "Your estimated impact"
INTRODUCTION = "Food Waste Impact Calculator"

#: The restore notice's own element. `calculator.js::restoreNotice`.
NOTICE = ".restore-notice"


@pytest.fixture
def context(browser):
    made = browser.new_context(viewport={"width": 1278, "height": 983}, locale="en-NZ")
    yield made
    made.close()


@pytest.fixture
def page(context):
    opened = context.new_page()
    try:
        opened.goto(BASE, wait_until="networkidle", timeout=15000)
    except Exception as error:  # pragma: no cover - environment guard
        pytest.skip(f"the front end is not being served at {ORIGIN}: {error}")
    opened.wait_for_selector('[data-action="start"]', timeout=10000)
    return opened


def _heading(page) -> str:
    return page.locator("main h1").first.inner_text().strip()


def _entry_state(page):
    return page.evaluate("() => history.state")


def _recorded_step(page):
    held = _entry_state(page) or {}
    return held.get(STEP_KEY)


def _back(page, *, settle: int = 400):
    """Press Back and let the step settle.

    A traversal between two entries of the *same* document fires `popstate` and no
    load event, so there is nothing to wait for; a traversal to an entry whose
    document has been discarded re-parses the page and waits on the taxonomy
    fetch. The settle covers both.
    """
    page.go_back()
    page.wait_for_timeout(settle)


def _forward(page, *, settle: int = 400):
    page.go_forward()
    page.wait_for_timeout(settle)


def _fulfil_calculate(page):
    page.route(
        "**/api/v1/calculate*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(CALCULATE_FIXTURE),
        ),
    )


def _to_amount_step(page, *, amount="1200.50"):
    """Walk to step 3 and type one amount."""
    page.click('[data-action="start"]')
    page.wait_for_selector('input[name="sector"]')
    page.evaluate("document.querySelectorAll('input[name=sector]')[1].click()")
    page.wait_for_timeout(80)
    press_continue(page)
    page.wait_for_selector('input[name="food-category"]')
    page.locator('input[name="food-category"]').nth(1).click()
    page.wait_for_timeout(80)
    press_continue(page)
    page.wait_for_selector('[data-leaf-field="amount"]')
    page.locator('[data-leaf-field="amount"]').first.fill(amount)
    page.wait_for_timeout(80)


def _to_destination_step(page, *, amount="1200.50"):
    _to_amount_step(page, amount=amount)
    press_continue(page)
    page.wait_for_selector('[data-line-field="amount"]', timeout=10000)


def _to_review_step(page, *, amount="1200.50", allocated="1000.00"):
    _to_destination_step(page, amount=amount)
    page.locator('[data-line-field="amount"]').first.fill(allocated)
    page.wait_for_timeout(200)
    press_continue(page)
    page.wait_for_selector("#time-frame", timeout=10000)


def _to_the_results_page(page):
    _fulfil_calculate(page)
    _to_review_step(page)
    page.click('[data-action="calculate"]')
    page.wait_for_selector('[data-action="start-over"]', timeout=10000)
    assert _heading(page) == RESULTS, _heading(page)


def _leave_the_calculator(page):
    """Follow the site drawer's *Documentation* link, as a visitor does."""
    page.click(".site-drawer__handle")
    page.wait_for_timeout(120)
    page.click('.site-drawer__nav a[href="./methodology.html"]')
    page.wait_for_load_state("load")
    assert "methodology.html" in page.url, page.url


# --------------------------------------------------- Back and Forward inside


def test_back_from_a_middle_step_lands_on_the_previous_step_with_its_answers(page):
    """The defect itself, at the step where it costs the most typing.

    Before this work, this press left the calculator: step 4's Back went to
    whatever the visitor was looking at before they arrived.
    """
    _to_destination_step(page)
    assert _heading(page) == DESTINATIONS, _heading(page)

    _back(page)

    # Asked before the heading, and it is the defect's own shape: with no entry of its
    # own the calculator has nothing behind it, so this press left the document and there
    # is no heading to read. Without this line the failure is a thirty-second wait for a
    # selector on a page that is gone.
    assert "index.html" in page.url, (
        f"Back from the destinations step left the calculator for {page.url!r} - the step "
        f"has no history entry of its own, which is the whole defect"
    )
    assert _heading(page) == AMOUNT, (
        f"Back from the destinations step landed on {_heading(page)!r} rather than on "
        f"the amount step"
    )
    assert page.locator('[data-leaf-field="amount"]').first.input_value() == "1200.50", (
        "the step came back without the figure typed on it"
    )
    assert _recorded_step(page) == 2, _entry_state(page)


def test_forward_returns_to_the_step_back_came_from(page):
    """Back is only half a history; Forward is the other half."""
    _to_destination_step(page)
    _back(page)
    assert _heading(page) == AMOUNT, _heading(page)

    _forward(page)

    assert _heading(page) == DESTINATIONS, (
        f"Forward landed on {_heading(page)!r} rather than back on the destinations step"
    )
    assert page.locator('[data-line-field="amount"]').count() > 0, (
        "the destinations step came back without its allocation rows"
    )
    assert _recorded_step(page) == 3, _entry_state(page)


def test_back_from_the_results_page_lands_on_the_review_step(page):
    """The press the defect was reported against, at the screen it cost most.

    The figures are the end of a walk through six screens, and Back threw the
    whole calculation away.
    """
    _to_the_results_page(page)
    assert _recorded_step(page) == 5, _entry_state(page)

    _back(page)

    assert "index.html" in page.url, (
        f"Back from the results page left the calculator for {page.url!r}, throwing away a "
        f"calculation the visitor had walked six screens for"
    )
    assert _heading(page) == REVIEW, (
        f"Back from the results page landed on {_heading(page)!r} rather than on the "
        f"review step"
    )
    assert _recorded_step(page) == 4, _entry_state(page)


def test_back_out_of_the_introduction_leaves_the_calculator(page):
    """**And this is the measurement for "the first load replaces".**

    The entry the page arrives on is written with `replaceState`. Push it instead
    and there are two entries for the introduction, so this press lands on the
    introduction again and the first Back of every visitor's session does nothing
    visible. There is nothing to see on the screen in that case, which is why the
    assertion is that the document is gone.
    """
    assert _heading(page) == INTRODUCTION, _heading(page)
    # Read before the press, and asserted after it: the behavioural claim is the headline
    # here and must be the assertion that fails, rather than being shadowed by a
    # bookkeeping check that happens to notice the same mutation first.
    arrival = _entry_state(page)

    _back(page)

    assert "index.html" not in page.url, (
        f"Back out of the introduction stayed on {page.url!r} - the first load pushed an "
        f"entry it should have replaced"
    )
    assert arrival[INDEX_KEY] == 0, (
        f"the arrival entry is not the calculator's first: {arrival}"
    )


def test_the_whole_walk_out_is_one_press_per_screen(page):
    """**The stated cost of an entry per step, pinned rather than discovered.**

    A visitor who walked to the results needs seven presses to leave: results,
    review, destinations, amount, food type, stage, introduction, out. That is
    the ordinary price of this pattern and it is worth paying - the presses it
    adds each land on a screen the visitor recognises, and the press it fixes
    threw a finished calculation away. If it ever changes, it changes here and
    deliberately.
    """
    _to_the_results_page(page)
    walked = []
    for _ in range(7):
        _back(page)
        if "index.html" not in page.url:
            break
        walked.append(_heading(page))
    assert walked == [REVIEW, DESTINATIONS, AMOUNT, FOOD, STAGE, INTRODUCTION], walked
    assert "index.html" not in page.url, (
        f"seven presses did not leave the calculator; still on {page.url!r}"
    )


def test_the_step_is_not_in_the_url(page):
    """One URL, and the step in `history.state`.

    A `?step=` would be a link that is copied and cannot be honoured: the screen
    it names is about answers no URL carries, and putting those in a URL is what
    §7.2a's privacy position rules out. So the URL is the same string on every
    screen, and this is what says so.
    """
    seen = {page.url}
    _to_the_results_page(page)
    seen.add(page.url)
    for _ in range(3):
        _back(page)
        seen.add(page.url)
    assert seen == {BASE}, f"the URL moved across the walk: {sorted(seen)}"


# ------------------------------------------------ a re-render is not a step


def test_typing_and_ticking_add_no_history_entries(page):
    """**`render()` runs on every `setState`, and most of those are not navigation.**

    Push per render and a visitor who typed a four-digit amount needs four Back
    presses to undo one screen. The entry count is measured across a burst of
    ordinary editing: four keystrokes into the amount, a unit change, and a
    breakdown tab on the results page.
    """
    _to_amount_step(page)
    before = page.evaluate("() => history.length")
    field = page.locator('[data-leaf-field="amount"]').first
    for value in ("1", "12", "123", "1234"):
        field.fill(value)
        page.wait_for_timeout(60)
    page.locator('[data-leaf-field="unit"]').first.select_option("tonnes")
    page.wait_for_timeout(150)
    after = page.evaluate("() => history.length")
    assert after == before, (
        f"ordinary editing added {after - before} history entries - a re-render was "
        f"treated as a navigation"
    )


def test_a_results_page_breakdown_tab_adds_no_history_entry(page):
    """The same rule on the screen with the most non-navigating controls."""
    _to_the_results_page(page)
    before = page.evaluate("() => history.length")
    tabs = page.locator('[data-action="breakdown-tab"]')
    if tabs.count() < 2:  # pragma: no cover - depends on the published set
        pytest.skip("the published set renders fewer than two breakdown tabs")
    tabs.nth(1).click()
    page.wait_for_timeout(200)
    tabs.nth(0).click()
    page.wait_for_timeout(200)
    after = page.evaluate("() => history.length")
    assert after == before, f"switching a breakdown tab added {after - before} entries"


# ------------------------------ the step bar's Back and the browser's are one


def test_the_browser_back_obeys_return_to_exactly_as_the_step_bar_does(page):
    """**Two answers to "where does Back go", asserted to be the same answer.**

    A review-step *Edit* link jumps to the step it names and writes
    `state.returnTo`, so that step's Back returns to the *review* step rather
    than stepping back one (`backTarget`, `calculator.js`). The browser's Back
    has to agree, and it does by construction: the entry behind the one the jump
    pushed is the review step, because that is where the visitor stood.

    Asserted on the control's own `data-step` and on where the press lands, so a
    disagreement fails here rather than being reported by a visitor.
    """
    _fulfil_calculate(page)
    _to_review_step(page)
    page.locator('[data-action="go-step"][data-jump="review"]').first.click()
    page.wait_for_timeout(400)
    assert _heading(page) == STAGE, _heading(page)

    offered = page.evaluate(
        "() => document.querySelector('.step-nav [data-action=\"go-step\"]').dataset.step"
    )
    assert offered == "4", (
        f"the step bar's Back offers step {offered} rather than the review step, so this "
        f"test is no longer measuring the disagreement it was written for"
    )

    _back(page)

    assert _heading(page) == REVIEW, (
        f"the browser's Back landed on {_heading(page)!r} while the step bar's Back "
        f"offered step {offered} - the two disagree about where Back goes"
    )
    assert _recorded_step(page) == 4, _entry_state(page)


def test_the_browser_back_runs_the_same_undo_the_step_bar_runs(page):
    """**The part a bare `setState({step})` would silently skip.**

    *Add another entry* promotes the draft onto the saved list and empties it, and
    the Back that follows is an *undo*: `goToStep` puts the entries and the draft
    back. Routing `popstate` straight into `state.step` would leave the promoted
    entry on the list with an empty "Current entry 2" beside it, which is the
    defect `goToStep`'s first branch exists to prevent.

    The same press must also ask first, in the same words.
    """
    _fulfil_calculate(page)
    _to_review_step(page)
    assert page.locator(".saved-entry-card").count() == 0, "nothing is saved yet"
    page.click('[data-action="add-entry"]')
    page.wait_for_selector('input[name="sector"]', timeout=10000)
    page.evaluate("document.querySelectorAll('input[name=sector]')[2].click()")
    page.wait_for_timeout(150)

    asked = []
    page.once("dialog", lambda dialog: (asked.append(dialog.message), dialog.accept()))
    _back(page, settle=700)

    assert asked == [
        "Going back will discard the new supply-chain entry you have started. Go back anyway?"
    ], f"the browser's Back asked {asked!r} rather than the step bar's own question"
    assert _heading(page) == REVIEW, _heading(page)
    assert page.locator(".saved-entry-card").count() == 0, (
        "backing out of Add left the promoted entry on the saved list - the browser's "
        "Back did not run the undo the step bar's Back runs"
    )


def test_a_declined_discard_costs_the_visitor_nothing_including_their_place(page):
    """A `popstate` has already moved the position and cannot be cancelled.

    So a refusal has to travel the position back. What is measured is the screen,
    the draft, the entry the history records, and that Forward is not left as the
    only way back to where the visitor already is.
    """
    _fulfil_calculate(page)
    _to_review_step(page)
    page.click('[data-action="add-entry"]')
    page.wait_for_selector('input[name="sector"]', timeout=10000)
    page.evaluate("document.querySelectorAll('input[name=sector]')[2].click()")
    page.wait_for_timeout(150)
    standing = _entry_state(page)

    page.once("dialog", lambda dialog: dialog.dismiss())
    _back(page, settle=800)

    assert _heading(page) == STAGE, (
        f"a declined Back moved the visitor to {_heading(page)!r} anyway"
    )
    ticked = page.evaluate(
        "() => [...document.querySelectorAll('input[name=sector]')].findIndex(b => b.checked)"
    )
    assert ticked == 2, f"the draft's sector did not survive a declined Back ({ticked})"
    assert _entry_state(page) == standing, (
        f"the history position was left where the refused press put it: "
        f"{_entry_state(page)} rather than {standing}"
    )


# --------------------------------------------------- composing with §7.2a


def test_back_after_a_restore_keeps_walking_back_through_the_steps(page):
    """**The restore and the history entries, composed.**

    Leaving for the documentation page and pressing Back re-parses this document
    and §7.2a puts the answers back. What is measured here is that the presses
    *after* that one still walk the wizard, one screen at a time, each with its
    own answers - and that the restore adds no entry of its own, so the visitor
    does not collect a duplicate for the screen they were already on.

    **Those further presses are same-document `popstate`s, not re-parses**, and
    that was measured rather than assumed: the entries behind this one were pushed
    from the document this load replaced, so the reload hands them the new document
    too. A mark written onto `window` before the press is still there after it,
    twice. The consequence for mutation testing is on record in
    `web/js/history.js`: taking `main.js`'s "the entry outranks the snapshot"
    line out leaves this test green, because on this journey the two agree.
    `test_a_back_inside_the_calculator_before_leaving_is_where_the_visitor_returns`
    is the test that kills it, and it is the one journey where they disagree.
    """
    _to_destination_step(page)
    standing = _entry_state(page)

    _leave_the_calculator(page)
    # Measured from the documentation page rather than from the calculator, because
    # `history.length` counts the whole session: leaving added the methodology entry
    # itself, and the question here is only whether the load that *returns* adds one
    # more on top of it.
    away = page.evaluate("() => history.length")
    page.go_back(wait_until="load", timeout=15000)
    page.wait_for_selector("main h1", timeout=10000)
    page.wait_for_timeout(600)

    assert _heading(page) == DESTINATIONS, _heading(page)
    assert page.evaluate("() => history.length") == away, (
        "the restored load pushed an entry of its own, so Back now walks the visitor "
        "through a screen they never left"
    )
    assert _entry_state(page) == standing, (
        f"the restored load rewrote its entry: {_entry_state(page)} rather than {standing}"
    )

    _back(page, settle=800)
    assert _heading(page) == AMOUNT, (
        f"Back after a restore landed on {_heading(page)!r} rather than on the previous "
        f"step - the step was taken from the snapshot rather than from the entry"
    )
    assert page.locator('[data-leaf-field="amount"]').first.input_value() == "1200.50", (
        "the step behind the restored one came back without its answer"
    )

    _back(page, settle=800)
    assert _heading(page) == FOOD, _heading(page)
    ticked = page.evaluate(
        "() => [...document.querySelectorAll('input[name=food-category]')].filter(b => b.checked).length"
    )
    assert ticked == 1, f"the food category did not survive the second Back ({ticked} ticked)"


def test_a_back_inside_the_calculator_before_leaving_is_where_the_visitor_returns(page):
    """The one case where the entry and the snapshot disagree, and the entry wins.

    The snapshot is written at `continue` and `calculate` only, so after a Back
    inside the calculator it still names the furthest step reached. The entry
    names the screen the visitor was actually looking at when they left, which is
    the one they expect to come back to.
    """
    _to_destination_step(page)
    _back(page)
    assert _heading(page) == AMOUNT, _heading(page)

    _leave_the_calculator(page)
    page.go_back(wait_until="load", timeout=15000)
    page.wait_for_selector("main h1", timeout=10000)
    page.wait_for_timeout(600)

    stored = page.evaluate(
        "() => JSON.parse(sessionStorage.getItem('kaiCalculatorAnswers')).answers.step"
    )
    assert stored == 3, f"this test needs the snapshot to disagree; it says {stored}"
    assert _heading(page) == AMOUNT, (
        f"returned to {_heading(page)!r} - the furthest step a checkpoint saw, rather than "
        f"the screen the visitor left"
    )


def test_a_revalidation_that_moves_the_visitor_rewrites_its_entry(page):
    """**The one step change that is the page's and not the visitor's.**

    §7.2a revalidates restored answers against a freshly fetched taxonomy, and a
    draft whose sector a publish has retired is sent back to step 0 - the screen
    that asks the question they now have to answer again. That is the *load*
    moving them. Pushed as an entry, Back would offer the step the dropped answer
    was on, which is the one screen the prune has just made unanswerable; so the
    entry is rewritten instead and says what is on it.

    Driven by serving the second load a taxonomy without the sector the visitor
    chose, which is what a publish that retires a supply-chain stage looks like
    from here.
    """
    _to_destination_step(page)
    chosen = page.evaluate(
        """() => {
             const back = document.querySelector('.step-nav [data-action="go-step"]')
             return back ? back.dataset.step : null
           }"""
    )
    assert chosen == "2", f"the walk did not end on the destinations step ({chosen})"
    standing = _entry_state(page)

    _leave_the_calculator(page)
    sector = page.evaluate(
        "() => JSON.parse(sessionStorage.getItem('kaiCalculatorAnswers')).answers.sector"
    )
    assert isinstance(sector, str) and sector, sector

    def retire_the_sector(route):
        taxonomy = route.fetch().json()
        taxonomy["sectors"] = [row for row in taxonomy["sectors"] if row["code"] != sector]
        route.fulfill(status=200, content_type="application/json", body=json.dumps(taxonomy))

    page.route("**/api/v1/taxonomy*", retire_the_sector)
    page.go_back(wait_until="load", timeout=15000)
    page.wait_for_selector("main h1", timeout=10000)
    page.wait_for_timeout(700)

    assert _heading(page) == STAGE, (
        f"the revalidation did not land the visitor on the stage question: {_heading(page)!r}"
    )
    assert page.locator(NOTICE).count() == 1, "the drop was not reported on screen"
    assert _entry_state(page) == {STEP_KEY: 0, INDEX_KEY: standing[INDEX_KEY]}, (
        f"the revalidation pushed an entry instead of rewriting the one it was on: "
        f"{_entry_state(page)} against {standing}"
    )


# ------------------------------------------------------------- start over


def test_start_over_cannot_be_backed_into(page):
    """"Clear all calculator data and return to the introduction?" must mean it.

    Six entries sit behind Back by the time a visitor reaches the results.
    Entries cannot be deleted, so the unwind travels back to the entry the
    calculator opened in - and Back from *there* leaves the site, which is what
    it did before the visitor started.
    """
    _to_the_results_page(page)
    page.once("dialog", lambda dialog: dialog.accept())
    page.click('[data-action="start-over"]')
    page.wait_for_timeout(900)

    assert _heading(page) == INTRODUCTION, _heading(page)
    # Read before the press and asserted after it, so that the press is what fails.
    cleared = _entry_state(page)

    _back(page, settle=600)
    assert "index.html" not in page.url, (
        f"Back after start-over stayed on {page.url!r} - there are still entries of the "
        f"cleared calculation behind it"
    )
    assert cleared == {STEP_KEY: -1, INDEX_KEY: 0}, (
        f"start-over left the visitor at {cleared} rather than on the entry the calculator "
        f"opened in"
    )


def test_a_second_clear_unwinds_from_where_the_first_one_left_off(page):
    """Clearing twice in one tab, which is what says the unwind keeps its count.

    The unwind travels back by the number of entries the calculator has pushed, and
    that count lives in `history.state` so it survives a reload. If the entry the
    unwind lands on is not re-stamped, the count keeps rising across the clear and
    the *next* unwind travels further back than there are entries - the browser
    clamps, and "return to the introduction" walks the visitor off the site
    entirely. Measured here rather than reasoned about, because the browser's
    clamping is what makes the symptom.
    """
    _to_the_results_page(page)
    page.once("dialog", lambda dialog: dialog.accept())
    page.click('[data-action="start-over"]')
    page.wait_for_timeout(900)
    assert _entry_state(page) == {STEP_KEY: -1, INDEX_KEY: 0}, _entry_state(page)

    # In again, two screens deep, then clear from the header this time.
    page.click('[data-action="start"]')
    page.wait_for_selector('input[name="sector"]', timeout=10000)
    page.evaluate("document.querySelectorAll('input[name=sector]')[1].click()")
    page.wait_for_timeout(80)
    press_continue(page)
    page.wait_for_selector('input[name="food-category"]', timeout=10000)
    assert _entry_state(page) == {STEP_KEY: 1, INDEX_KEY: 2}, (
        f"the second walk did not restart the count: {_entry_state(page)}"
    )

    page.once("dialog", lambda dialog: dialog.accept())
    page.click("#clear-button")
    page.wait_for_timeout(900)

    assert "index.html" in page.url, (
        f"the second clear walked off the site to {page.url!r} - it unwound further than "
        f"the calculator had pushed"
    )
    assert _heading(page) == INTRODUCTION, _heading(page)
    assert _entry_state(page) == {STEP_KEY: -1, INDEX_KEY: 0}, _entry_state(page)


def test_a_browser_that_refuses_to_write_history_is_not_a_broken_calculator(context):
    """The other browser API this feature leans on, guarded like `sessionStorage`.

    §7.2a wraps every storage call because a private window throws on all of them;
    `pushState` throws where the document is sandboxed, and a page throttled for
    calling it too often gets a `SecurityError` as well. The first call runs at
    module evaluation time inside `main.js`, so unguarded it would abort the module
    and the calculator would not render at all - a whole wizard lost to a history
    entry.

    Both writes are made to throw here, before any of the page's own scripts run,
    and the form still has to walk. There is no history to move within in that
    browser; there is a calculator.
    """
    broken = context.new_page()
    broken.add_init_script(
        """
        history.pushState = () => { throw new Error('history is blocked') }
        history.replaceState = () => { throw new Error('history is blocked') }
        """
    )
    try:
        broken.goto(BASE, wait_until="networkidle", timeout=15000)
    except Exception as error:  # pragma: no cover - environment guard
        pytest.skip(f"the front end is not being served at {ORIGIN}: {error}")
    # Named, rather than left as a bare selector timeout: the whole failure mode here is
    # that `main.js` aborts at module evaluation and the page renders nothing at all, so
    # the message has to say that instead of "waiting for [data-action=start]".
    try:
        broken.wait_for_selector('[data-action="start"]', timeout=10000)
    except playwright_api.Error:
        painted = broken.evaluate("() => document.getElementById('main-content').innerText")
        pytest.fail(
            "the calculator did not render at all in a browser that refuses to write "
            f"history - main-content holds {painted!r}"
        )
    assert _heading(broken) == INTRODUCTION, _heading(broken)

    broken.click('[data-action="start"]')
    broken.wait_for_selector('input[name="sector"]', timeout=10000)
    broken.evaluate("document.querySelectorAll('input[name=sector]')[1].click()")
    broken.wait_for_timeout(80)
    press_continue(broken)
    broken.wait_for_selector('input[name="food-category"]', timeout=10000)
    assert _heading(broken) == FOOD, _heading(broken)

    # And the step bar's own Back is untouched by any of it.
    broken.click('.step-nav [data-action="go-step"]')
    broken.wait_for_timeout(300)
    assert _heading(broken) == STAGE, _heading(broken)
    broken.close()


def test_forward_after_start_over_does_not_reopen_a_cleared_step(page):
    """Forward is a live direction too, and those entries still name steps.

    They cannot be deleted, so a traversal to a step the calculator cannot draw -
    anything past the stage question with no sector and no saved entry - is
    answered with the introduction, and the entry is rewritten to say so. Step 0
    is never clamped: the stage question is answerable from empty.
    """
    _to_the_results_page(page)
    page.once("dialog", lambda dialog: dialog.accept())
    page.click('[data-action="start-over"]')
    page.wait_for_timeout(900)

    _forward(page, settle=500)
    assert _heading(page) == STAGE, (
        f"Forward after start-over landed on {_heading(page)!r} rather than on the stage "
        f"question, which is the one step that is answerable from empty"
    )

    seen = []
    for _ in range(3):
        _forward(page, settle=500)
        seen.append((_heading(page), _recorded_step(page)))
    assert seen == [(INTRODUCTION, -1)] * 3, (
        f"Forward walked back into the cleared calculation: {seen}"
    )
