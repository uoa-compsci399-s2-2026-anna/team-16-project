"""Leave the calculator, press Back, and find the calculation still there.

**The defect, reproduced before anything was written.** The whole calculator is
one URL with one history entry: ``location.pathname`` is ``/`` from the
introduction to the results, and which screen is up is ``state.step``, in memory.
So following the header's *Documentation* link and pressing Back landed the
visitor on the introduction screen with everything they had typed gone::

    on step 3:      {step heading: 'How much food waste are you measuring?'}
    after leaving:  {path: '/methodology.html'}
    after Back:     {step heading: 'Food Waste Impact Calculator'}

Back **re-parses the page**; it is not restored from the back-forward cache.
Measured by registering a ``pageshow`` listener before leaving and finding it
gone afterwards, so the JS heap is new -- and ``docker/nginx.conf`` serves
``Cache-Control: no-cache`` rather than ``no-store``, so the response header is
not what disables bfcache. Which is why the fix is stored answers rather than a
header change, and why **this file drives a real browser**: a restored page is
measured, not reasoned about.

What is measured here, and why each case needs a browser:

* **Leaving from a middle step and coming back.** The whole point. Asserted on
  the heading on screen and on the values in the inputs, not on storage.
* **Closing the tab and opening a new one restores nothing.** ``sessionStorage``
  is per top-level browsing context, and that claim is about the browser rather
  than about our code -- so it is asserted against a browser.
* **A snapshot written by another schema version is discarded silently**: the
  form opens at the introduction with no notice and no broken screen.
* **A publish between two page loads.** The second load's taxonomy is served
  without a row the first load's answers name, and the visitor is told what went.
  This is §6.1's own hazard arriving by another door and is the one real risk in
  the whole feature.
* **A decimal survives the round trip as a string**, read out of the box the
  visitor typed it into after a real reload.
* **Clear wipes it**, or the button is a false statement.

``tests/web/test_snapshot.py`` is the other half: it can put a hand-written
snapshot into storage and assert on the bytes, which no browser walk can.

Requires the stack, and the image rebuilt -- ``web/`` is baked in by
``docker/web.Dockerfile``::

    docker compose -f docker/compose.yaml build web
    docker compose -f docker/compose.yaml up -d --force-recreate web
    pytest tests/web/test_session_restore_browser.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.web.steps import press_continue


pytestmark = pytest.mark.browser

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to press Back in a real browser",
)

ORIGIN = os.environ.get("KAICALC_WEB_URL", "http://localhost:18080").rstrip("/")
BASE = ORIGIN + "/index.html?lang=en"
#: The page the results view's own link goes to (`calculator.js`'s
#: `view-methodology`), and one of the three header links. Leaving by a real
#: navigation rather than by `page.reload()` is the point: a reload is not the
#: defect anybody reported.
AWAY = ORIGIN + "/methodology.html?lang=en"

SNAPSHOT_KEY = "kaiCalculatorAnswers"

ROOT = Path(__file__).resolve().parents[2]
#: The contract fixture the results view is reached from. Fulfilled in the
#: browser rather than calculated for real, for `test_step_navigation.py`'s
#: reason and one more of its own: a real POST writes a `submission` row, and a
#: test that pollutes the public statistics to look at a screen is the wrong
#: trade.
CALCULATE_FIXTURE = json.loads(
    (ROOT / "tests" / "fixtures" / "calculate_response_single.json").read_text(encoding="utf-8")
)

#: The notice's own element. `calculator.js::restoreNotice`.
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


def _snapshot(page):
    raw = page.evaluate(f"() => sessionStorage.getItem({SNAPSHOT_KEY!r})")
    return None if raw is None else json.loads(raw)


def _to_amount_step(page, *, amount="1200.50"):
    """Walk to step 3 and type one amount.

    `press_continue` rather than a bare click: the published set has
    `item_level_enabled: true`, so step 2's Continue opens step 2.5 and a single
    press does not reach step 3 (`tests/web/steps.py`).

    **The amount typed here is not in a snapshot yet**, and that is the mechanism
    rather than an oversight: the snapshot is written at the visitor's own
    checkpoints, which are `continue` and `calculate`.
    `test_only_what_a_checkpoint_saw_is_restored` is what pins that boundary;
    every other test presses Continue before it leaves.
    """
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
    field = page.locator('[data-leaf-field="amount"]').first
    field.fill(amount)
    page.wait_for_timeout(80)
    return field


def _to_destination_step(page, *, amount="1200.50"):
    """The same walk, carried one Continue further so that step 3's answer is
    inside a checkpoint. Step 4 is where a visitor has the most to lose."""
    _to_amount_step(page, amount=amount)
    press_continue(page)
    page.wait_for_selector('[data-line-field="amount"]', timeout=10000)


def _to_review_step(page, *, amount="1200.50", allocated="1000.00"):
    """One Continue further again, so that step 4's rows are inside a checkpoint
    too. A partial allocation is accepted by the form, which is why this does not
    have to fill the total."""
    _to_destination_step(page, amount=amount)
    page.locator('[data-line-field="amount"]').first.fill(allocated)
    page.wait_for_timeout(200)
    press_continue(page)
    page.wait_for_selector("#time-frame", timeout=10000)


def _fulfil_calculate(page):
    """Answer `POST /calculate` from the contract fixture."""
    page.route(
        "**/api/v1/calculate*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(CALCULATE_FIXTURE),
        ),
    )


def _leave_the_calculator(page):
    """Follow the site drawer's *Documentation* link, as a visitor does.

    A real click on the real link rather than a `goto`: the link is one of the
    four ways out of the calculator the defect was reported against, and the
    drawer has to be opened first because the three links live inside a
    `<details>` (`web/index.html`).
    """
    page.click(".site-drawer__handle")
    page.wait_for_timeout(120)
    page.click('.site-drawer__nav a[href="./methodology.html"]')
    page.wait_for_load_state("load")
    assert "methodology.html" in page.url, page.url


def _leave_and_come_back(page):
    """The measured defect's own route: away by the link, then Back.

    `wait_until="load"` on the way back rather than `networkidle`: the taxonomy
    fetch is what the restore waits on, and it is asked for after `load`.
    """
    _leave_the_calculator(page)
    page.go_back(wait_until="load", timeout=15000)
    page.wait_for_selector("main h1", timeout=10000)
    page.wait_for_timeout(400)


# ------------------------------------------------------- the defect itself


def test_leaving_from_a_middle_step_and_pressing_back_comes_back_to_that_step(page):
    """The reported defect, at the step where it costs the most typing.

    Asserted on the heading and on the field, because either alone can be right
    while the page is wrong: the step can be restored with the boxes empty, and a
    box can hold a value on a screen the visitor cannot see.
    """
    _to_destination_step(page)
    before = _heading(page)
    assert before == "Where did the food waste go?", before

    _leave_and_come_back(page)

    assert _heading(page) == before, (
        f"Back landed on {_heading(page)!r} rather than on the step the visitor left "
        f"({before!r}) - the calculation is gone, which is the whole defect"
    )
    # Step 3's amount, read off step 3 after walking back to it.
    page.click('.step-nav [data-action="go-step"]')
    page.wait_for_selector('[data-leaf-field="amount"]', timeout=10000)
    assert page.locator('[data-leaf-field="amount"]').first.input_value() == "1200.50"
    # Step 2's tick, read off step 2.
    page.click('.step-nav [data-action="go-step"]')
    page.wait_for_selector('input[name="food-category"]', timeout=10000)
    ticked = page.evaluate(
        "() => [...document.querySelectorAll('input[name=food-category]')].filter(b => b.checked).length"
    )
    assert ticked == 1, f"the food category the visitor ticked did not survive Back ({ticked} ticked)"
    # And step 0's sector.
    page.click('.step-nav [data-action="go-step"]')
    page.wait_for_selector('input[name="sector"]', timeout=10000)
    chosen = page.evaluate(
        "() => [...document.querySelectorAll('input[name=sector]')].filter(b => b.checked).length"
    )
    assert chosen == 1, "the supply-chain stage did not survive Back"


def test_only_what_a_checkpoint_saw_is_restored(page):
    """**The boundary, measured and pinned rather than discovered later.**

    The snapshot is written at the visitor's own checkpoints -- every `continue`
    and `calculate` -- and not on every keystroke, because a write per keystroke
    would have to be fed by a `setState` per keystroke and `render()` replaces
    `main.innerHTML` on every one of those (`period.js` avoids exactly that).

    The consequence is real and is not hidden: an amount typed on step 3 and not
    yet continued past is **not** restored. The step is, and everything up to the
    last Continue is. This test exists so that the boundary is on record: if it is
    ever moved, it fails here and the change is deliberate rather than noticed by
    a visitor.
    """
    _to_amount_step(page, amount="777.00")
    heading = _heading(page)

    _leave_and_come_back(page)

    assert _heading(page) == heading, _heading(page)
    assert page.locator('[data-leaf-field="amount"]').first.input_value() == "", (
        "an amount typed after the last checkpoint came back, so the write is no longer "
        "at `continue` and `calculate` - which is a behaviour change to look at, not a "
        "test to update"
    )


def test_the_destination_rows_and_their_units_come_back(page):
    """Step 4 is where the most is at stake and where a restore can be subtly
    wrong: a row carries its own unit, and `state.totalUnit` is the unit the
    chain's combined figures are stated in. Restore the rows without it and a
    chain typed in tonnes is laid out against a total in kilograms -- a
    thousandfold error on a screen that looks entirely normal.
    """
    _to_amount_step(page, amount="5.00")
    page.evaluate(
        """() => {
          const select = document.querySelector('[data-leaf-field=unit]');
          select.value = 'tonnes';
          select.dispatchEvent(new Event('change', { bubbles: true }));
        }"""
    )
    page.wait_for_timeout(150)
    page.locator('[data-leaf-field="amount"]').first.fill("5.00")
    page.wait_for_timeout(120)
    press_continue(page)
    page.wait_for_selector('[data-line-field="amount"]', timeout=10000)
    page.locator('[data-line-field="amount"]').first.fill("2.25")
    page.wait_for_timeout(200)
    # Continue to the review step, which is the checkpoint that records step 4.
    press_continue(page)
    page.wait_for_selector("#time-frame", timeout=10000)

    _leave_and_come_back(page)

    assert _heading(page) == "Review your information", _heading(page)
    # Back to step 4, and read the row off its own screen.
    page.click('.step-nav [data-action="go-step"]')
    page.wait_for_selector('[data-line-field="amount"]', timeout=10000)
    assert page.locator('[data-line-field="amount"]').first.input_value() == "2.25"
    units = page.evaluate(
        "() => [...document.querySelectorAll('[data-line-field=unit]')].map(s => s.value)"
    )
    assert units and units[0] == "tonnes", (
        f"the row came back in {units[:1]} - the unit the figure was typed against was lost, "
        "which reinterprets the number"
    )
    # And the chain's own unit with it: the running total this step is measured
    # against is stated in `state.totalUnit`, not in the row's unit.
    assert "tonnes" in page.inner_text(".content-section"), (
        "step 4 came back measuring the allocation in a different unit from the one the "
        "total was typed in"
    )


def test_the_review_step_comes_back_with_its_period(page):
    """`timeFrame` and the period boxes are typed on the review step, which is why
    `calculate` is a checkpoint of its own: without it the last screen's answers
    would be the only ones no snapshot ever held.
    """
    _fulfil_calculate(page)
    _to_review_step(page, amount="10.00", allocated="10.00")
    page.select_option("#time-frame", "one_week")
    page.wait_for_timeout(250)
    assert page.eval_on_selector("#time-frame", "element => element.value") == "one_week"

    # Calculate is the second checkpoint, and it is the one that records the review
    # step's own answers: `timeFrame` and the period are typed here and nowhere else.
    page.click('[data-action="calculate"]')
    page.wait_for_selector('[data-action="start-over"]', timeout=10000)
    stored = _snapshot(page)
    assert stored["answers"]["timeFrame"] == "one_week", stored["answers"]["timeFrame"]
    # **The step the calculate checkpoint records is 4, not 5, and that was measured.**
    # `writeSnapshot` runs from the click handler; `submitCalculation`'s move to step 5
    # happens after its `await`. So nothing in the calculator writes a snapshot holding
    # step 5 today, and `restoredPatch`'s clamp of 5 -> 4 is defence for the revision
    # that stores the result rather than a live path -- which is why a mutation removing
    # that clamp survives this file and is killed by `tests/web/test_snapshot.py`
    # instead. Asserted here so the claim is checked rather than remembered.
    assert stored["answers"]["step"] == 4, stored["answers"]["step"]

    # **The reported route, end to end**: the Documentation link, clicked from the
    # results page, and Back.
    _leave_the_calculator(page)
    page.go_back(wait_until="load", timeout=15000)
    page.wait_for_selector("#time-frame", timeout=10000)

    # The review step, not the introduction - and NOT the results page: WP1 stores
    # the answers and not the computed result, so the visitor comes back to the
    # screen they calculated from with Calculate one press away.
    assert _heading(page) == "Review your information", _heading(page)
    assert page.eval_on_selector("#time-frame", "element => element.value") == "one_week"
    assert page.locator('[data-action="calculate"]').count() == 1


# -------------------------------------------------- a new tab restores nothing


def test_closing_the_tab_and_opening_a_new_one_restores_nothing(context, page):
    """`sessionStorage` is per top-level browsing context, which is the property
    that made it the right scope: a visitor who closes the tab has finished, and
    the next person to open one on that machine must not be handed their figures.

    Asserted against a browser because the claim is about the browser. A new tab
    in the same context is the same profile, the same cookies and the same
    `localStorage` - and must be a different `sessionStorage`.
    """
    _to_amount_step(page)
    assert _snapshot(page) is not None, "nothing was stored, so this proves nothing"
    page.close()

    fresh = context.new_page()
    fresh.goto(BASE, wait_until="networkidle", timeout=15000)
    fresh.wait_for_selector('[data-action="start"]', timeout=10000)
    fresh.wait_for_timeout(300)
    assert _snapshot(fresh) is None, "a new tab can read the closed tab's snapshot"
    assert fresh.locator('[data-action="start"]').count() == 1, (
        "a new tab did not open on the introduction screen"
    )
    assert fresh.locator(NOTICE).count() == 0
    fresh.close()


# ------------------------------------------------- another deployment's snapshot


def test_a_snapshot_from_another_schema_version_is_discarded_silently(page):
    """A deployment ships new state keys; a snapshot written before it is a shape
    nobody checked. It is dropped whole -- and **silently**, because the visitor
    did nothing wrong and there is nothing for them to act on, unlike a dropped
    answer.

    The form has to open at the introduction and be usable, not merely not crash:
    the assertion walks one step forward afterwards.
    """
    _to_amount_step(page)
    stored = _snapshot(page)
    assert stored["version"] == 1, stored["version"]
    page.evaluate(
        f"""() => {{
          const held = JSON.parse(sessionStorage.getItem({SNAPSHOT_KEY!r}));
          held.version = 99;
          sessionStorage.setItem({SNAPSHOT_KEY!r}, JSON.stringify(held));
        }}"""
    )
    _leave_and_come_back(page)

    assert page.locator('[data-action="start"]').count() == 1, (
        f"a snapshot from schema 99 was read: the page opened on {_heading(page)!r}"
    )
    assert page.locator(NOTICE).count() == 0, (
        "a version mismatch is not the visitor's business and must not put a notice on "
        "the screen"
    )
    page.click('[data-action="start"]')
    page.wait_for_selector('input[name="sector"]', timeout=5000)


def test_a_snapshot_that_is_not_json_leaves_the_calculator_working(page):
    """Storage holds a string, and a string can be anything. The form must open."""
    page.evaluate(f"() => sessionStorage.setItem({SNAPSHOT_KEY!r}, 'not json at all')")
    page.reload(wait_until="load", timeout=15000)
    page.wait_for_selector('[data-action="start"]', timeout=10000)
    page.click('[data-action="start"]')
    page.wait_for_selector('input[name="sector"]', timeout=5000)


# ----------------------------------------- a publish between two page loads


#: The second load's taxonomy, with `landfill` and the second food category
#: removed - a publish that retired both. Built from the live response so that
#: everything else about it is exactly what the deployment serves.
_RETIRE = """
(body) => {
  const taxonomy = JSON.parse(body);
  taxonomy.destinations = taxonomy.destinations.filter(row => row.code !== 'landfill');
  return JSON.stringify(taxonomy);
}
"""


def _retire_on_next_load(page, *, destination=None, category=None):
    """Serve the next `GET /taxonomy` without one row, as a publish would.

    Fulfilled from the **real** response rather than from a fixture, so the form
    is checked against a taxonomy that differs from the live one in exactly one
    row and in nothing else.
    """

    def handler(route):
        response = route.fetch()
        taxonomy = response.json()
        if destination:
            taxonomy["destinations"] = [row for row in taxonomy["destinations"] if row["code"] != destination]
        if category:
            taxonomy["food_categories"] = [
                row for row in taxonomy["food_categories"] if row["code"] != category
            ]
            taxonomy["food_items"] = [
                row for row in taxonomy["food_items"] if row["food_category"] != category
            ]
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(taxonomy),
        )

    page.route("**/api/v1/taxonomy*", handler)


def test_a_destination_the_publish_retired_is_dropped_and_the_visitor_is_told(page):
    """§6.1's hazard, arriving by the other door.

    §6.1 forbids caching a taxonomy across page loads because a stale one is "a
    form offering codes the current set does not price". A restored answer naming
    a destination a publish has since retired is that same form. So the order is
    fixed -- restore, fetch the taxonomy **fresh**, check, drop, say so -- and
    this measures the last two: the row is gone from the form and the visitor
    reads why.
    """
    _to_amount_step(page, amount="10.00")
    press_continue(page)
    page.wait_for_selector('[data-line-field="amount"]')
    rows_before = page.evaluate(
        "() => [...document.querySelectorAll('[data-line-field=amount]')].length"
    )
    landfill = page.evaluate(
        """() => {
          const labels = [...document.querySelectorAll('.destination-row label')];
          const row = labels.find(l => /landfill/i.test(l.textContent));
          if (!row) return null;
          const input = row.parentElement.querySelector('[data-line-field=amount]');
          input.value = '10.00';
          input.dispatchEvent(new Event('input', { bubbles: true }));
          return input.id;
        }"""
    )
    assert landfill, "this deployment's taxonomy has no landfill row, so the case is unreachable"
    page.wait_for_timeout(200)

    _retire_on_next_load(page, destination="landfill")
    _leave_and_come_back(page)

    assert page.locator(NOTICE).count() == 1, (
        "a destination the visitor allocated waste to was dropped with nothing on the "
        "screen saying so, which is the failure the revalidation exists to prevent"
    )
    notice = page.locator(NOTICE).inner_text()
    assert "landfill" in notice, notice
    assert "Destination" in notice, notice
    rows_after = page.evaluate(
        "() => [...document.querySelectorAll('.destination-row label')].map(l => l.textContent)"
    )
    assert not any("landfill" in text.lower() for text in rows_after), rows_after
    assert len(rows_after) == rows_before - 1, (len(rows_after), rows_before)


def test_a_food_category_the_publish_retired_is_dropped_and_named(page):
    """The other half, and the more expensive loss: a category carries the
    amount, the money figures and a whole destination allocation."""
    _to_amount_step(page, amount="42.00")
    chosen = page.evaluate(
        """() => {
          const box = [...document.querySelectorAll('input[name=food-category]')][1];
          return box ? box.value : null;
        }"""
    )
    # Step 3 has replaced step 2, so the code is read back off the snapshot instead.
    chosen = _snapshot(page)["answers"]["foodCategories"][0]
    assert chosen, "no category was ticked, so this proves nothing"

    _retire_on_next_load(page, category=chosen)
    _leave_and_come_back(page)

    assert page.locator(NOTICE).count() == 1, "a retired category was dropped silently"
    notice = page.locator(NOTICE).inner_text()
    assert chosen in notice, notice
    assert "Food category" in notice, notice


def test_nothing_is_said_when_the_taxonomy_still_prices_everything(page):
    """The control, and it is not optional: every assertion above names something
    it expects to be gone, so a revalidation that dropped *everything* would pass
    all of them. This is what says the notice appears for a reason."""
    _to_destination_step(page)
    _leave_and_come_back(page)
    assert page.locator(NOTICE).count() == 0, "a notice appeared with nothing dropped"
    page.click('.step-nav [data-action="go-step"]')
    page.wait_for_selector('[data-leaf-field="amount"]', timeout=10000)
    assert page.locator('[data-leaf-field="amount"]').first.input_value() == "1200.50"


# ------------------------------------------------------ the decimal, in a browser


def test_a_quantity_comes_back_as_the_string_it_was_typed_as(page):
    """§1.2's oldest rule, measured after a real reload rather than in a harness.

    `1200.50` is chosen for its trailing zeros: `Number('1200.50')` is
    `1200.5`, so one `Number()` anywhere in the write, the read or the render
    shows up here as one character missing from the box the visitor typed into.
    """
    _to_destination_step(page, amount="1200.50")
    stored = _snapshot(page)
    figures = stored["answers"]["leafFigures"]
    held = next(iter(figures.values()))
    assert isinstance(held["totalAmount"], str), held
    assert held["totalAmount"] == "1200.50", held
    # And in the stored text it is quoted, which is what stops JSON.parse handing
    # back a double.
    raw = page.evaluate(f"() => sessionStorage.getItem({SNAPSHOT_KEY!r})")
    assert '"1200.50"' in raw.replace(" ", ""), raw

    _leave_and_come_back(page)
    page.click('.step-nav [data-action="go-step"]')
    page.wait_for_selector('[data-leaf-field="amount"]', timeout=10000)
    assert page.locator('[data-leaf-field="amount"]').first.input_value() == "1200.50"


# ------------------------------------------ the notice prints a code as text


def test_a_code_from_storage_reaches_the_notice_as_text_and_not_as_markup(page):
    """The notice names the code the visitor's answer carried, and that answer came
    out of **their own** `sessionStorage` -- so it can hold whatever a hand edit,
    or anything else with access to this origin, put there.

    `restoreNotice` escapes the finished sentence, so the interpolated code is
    escaped with it. Measured rather than reasoned about: the snapshot is edited to
    name a sector that is an `<img onerror>`, the page is reloaded, and the notice
    is read as text while the DOM is asked whether an element appeared and the
    handler ran.
    """
    _to_amount_step(page)
    payload = '<img src=x onerror="window.__injected = true">'
    page.evaluate(
        """(payload) => {
          const held = JSON.parse(sessionStorage.getItem('kaiCalculatorAnswers'));
          held.answers.sector = payload;
          sessionStorage.setItem('kaiCalculatorAnswers', JSON.stringify(held));
        }""",
        payload,
    )
    page.reload(wait_until="load", timeout=15000)
    page.wait_for_selector(NOTICE, timeout=10000)
    notice = page.locator(NOTICE).inner_text()
    assert "<img" in notice, notice
    assert page.evaluate("() => window.__injected === true") is False, (
        "a code out of the visitor's own storage was written into the page as markup, and "
        "its handler ran"
    )
    assert page.evaluate(f"() => document.querySelector({NOTICE!r}).querySelectorAll('img').length") == 0


# --------------------------------------------------------------- clearing it


def test_clear_all_calculator_data_removes_the_snapshot(page):
    """"Clear all calculator data and return to the introduction?" is what the
    header's button asks. A snapshot that outlived it would put every one of those
    answers back on the next page load, which makes the question a false
    statement."""
    _to_amount_step(page)
    assert _snapshot(page) is not None
    page.once("dialog", lambda dialog: dialog.accept())
    page.click("#clear-button")
    page.wait_for_timeout(250)
    assert _snapshot(page) is None, "Clear left the snapshot in storage"
    assert page.locator('[data-action="start"]').count() == 1

    # And it stays cleared across the load that would otherwise have restored it.
    page.goto(AWAY, wait_until="load", timeout=15000)
    page.go_back(wait_until="load", timeout=15000)
    page.wait_for_selector("main h1", timeout=10000)
    page.wait_for_timeout(300)
    assert page.locator('[data-action="start"]').count() == 1, _heading(page)


def test_start_a_new_calculation_on_the_results_page_removes_the_snapshot(page):
    """The second door to the same question, and the one a visitor reaches at the
    end rather than the beginning. *Start a new calculation* and the header's Clear
    are one code path -- `resetCalculator` -- and that is the point of asserting
    both: the day they stop being one, one of these fails."""
    _fulfil_calculate(page)
    _to_review_step(page, amount="10.00", allocated="10.00")
    page.click('[data-action="calculate"]')
    page.wait_for_selector('[data-action="start-over"]', timeout=10000)
    assert _snapshot(page) is not None
    page.once("dialog", lambda dialog: dialog.accept())
    page.click('[data-action="start-over"]')
    page.wait_for_timeout(300)
    assert _snapshot(page) is None, "Start a new calculation left the snapshot in storage"
    assert page.locator('[data-action="start"]').count() == 1


# ------------------------------------------------- nothing transient is restored


def test_no_error_and_no_open_dialog_survives_the_page_load(page):
    """A restored error about a request that finished long ago, and a restored
    open calendar, are both wrong. The snapshot carries neither by construction
    (`ANSWER_KEYS` is a whitelist), and this is what says so from the screen.
    """
    _to_amount_step(page, amount="")
    # Provoke a real client-side error: Continue on an empty amount.
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_timeout(200)
    assert page.locator(".field-error").count() > 0, "no error was raised, so this proves nothing"

    _leave_and_come_back(page)
    assert page.locator(".field-error").count() == 0, (
        "an error message about a Continue pressed before the page was reloaded came "
        "back with the answers"
    )
    assert page.evaluate(f"() => JSON.parse(sessionStorage.getItem({SNAPSHOT_KEY!r})).answers.error") is None
