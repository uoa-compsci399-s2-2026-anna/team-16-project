"""Step 2 as a multi-select, and the four rules that hang off it.

A supply-chain chain may now name several food categories. Each is a **leaf**:
its own amount, its own optional figures and its own destination allocation. The
rules measured here are the ones that produce a wrong *number* rather than a
wrong pixel:

* **Ghost figures.** ``leafFigures`` is keyed by leaf, so a record survives an
  untick unless something prunes it - and it is then fingerprinted, makes the
  back-out confirmation fire on a Back that discards nothing, and is silently
  restored on a re-tick.
* **``MAX_ENTRIES`` counts leaves.** ``api/schemas.py`` bounds the submission at
  twenty *entries*, and an entry is a leaf. Four chains of five categories is
  exactly the ceiling; a fifth is a 400 the form could have prevented.
* **Validation is per leaf.** Each leaf's allocation sums to no more than that
  leaf's **own** amount. Validated against the chain's combined total instead,
  one leaf could take another's mass and still pass, and the API would refuse
  the submission for a mass-conservation failure the form had already been
  shown.
* **The error names a place, not a sentence.** Two leaves produce the
  byte-identical "Waste amount must be greater than zero.", so the highlight
  cannot be decided by comparing prose.

Requires the stack: ``docker compose -f docker/compose.yaml up -d --build web``.
"""

from __future__ import annotations

import os

import pytest

from tests.web.steps import expand_step_cards, press_continue


pytestmark = pytest.mark.browser

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to drive the multi-select",
)

BASE = os.environ.get("KAICALC_WEB_URL", "http://localhost:18080").rstrip("/") + "/index.html"

#: `api/schemas.py`'s `MAX_ENTRIES`, restated here for the same reason
#: `calculator.js` restates it: a client-side ceiling may refuse earlier and more
#: kindly than the API, and must never refuse something the API would take.
MAX_LEAVES = 20


@pytest.fixture
def page(browser):
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


def _to_food_step(page, sector=0):
    page.click('[data-action="start"]')
    page.wait_for_selector('input[name="sector"]')
    page.evaluate(f"document.querySelectorAll('input[name=sector]')[{sector}].click()")
    page.wait_for_timeout(80)
    press_continue(page)
    page.wait_for_selector('input[name="food-category"]')


def _tick(page, *indices):
    boxes = page.locator('input[name="food-category"]')
    for index in indices:
        boxes.nth(index).click()
        page.wait_for_timeout(60)


def _amount_step(page):
    """Wait for step 3 and open every card on it.

    **`state="attached"`, then the cards.** Since #134 a forked chain draws its
    cards collapsed and a collapsed card's body carries `hidden`, so the amount
    fields are in the document and not visible - which the default
    ``state="visible"`` waits out against a screen that is working correctly, and
    which ``page.fill`` refuses outright. Every test below either types into
    those fields or measures them, so it opens the cards through the control a
    visitor would press. On a one-leaf chain there is no card to open and
    ``expand_step_cards`` finds nothing (decision 2 of #134: a lone card is never
    shut), so this is the same wait it always was.
    """
    page.wait_for_selector('[data-leaf-field="amount"]', state="attached")
    expand_step_cards(page)


def _amount_fields(page):
    return page.evaluate(
        "() => [...document.querySelectorAll('[data-leaf-field=amount]')].map(e => e.id)"
    )


def _first_rows(page):
    return page.evaluate(
        """() => {
          const byLeaf = {};
          for (const input of document.querySelectorAll('[data-line-field=amount]')) {
            (byLeaf[input.dataset.leaf] ||= []).push(input.id);
          }
          return Object.values(byLeaf).map(ids => ids[0]);
        }"""
    )


# ----------------------------------------------------------------- the control


def test_step_two_offers_checkboxes_and_counts_what_is_ticked(page):
    """The control itself, and the running count the leaves' length depends on."""
    _to_food_step(page)
    types = page.evaluate(
        "() => [...document.querySelectorAll('input[name=food-category]')].map(e => e.type)"
    )
    assert set(types) == {"checkbox"}, (
        f"step 2 still offers {sorted(set(types))} controls, so only one food type can "
        f"ever be chosen"
    )
    assert "0" in page.inner_text(".choice-count"), page.inner_text(".choice-count")
    _tick(page, 0, 2)
    assert "2" in page.inner_text(".choice-count"), (
        f"two categories are ticked and the count reads {page.inner_text('.choice-count')!r}"
    )


def test_i_do_not_know_is_a_peer_option_that_combines_with_a_category(page):
    """§5.4 forbids conflating "the user did not break their waste down by type"
    with the standard-mix category. They are two ticks, and a chain may hold both -
    "300 kg dairy and 200 kg I cannot identify" is two rows the database already
    permits under `COALESCE(food_category_id, 0)`."""
    _to_food_step(page)
    _tick(page, 0)
    unspecified = page.locator("input#food-category-unspecified")
    assert unspecified.count() == 1, (
        "step 2 offers no explicit \"I do not know\" answer, so continuing with "
        "nothing ticked stays indistinguishable from an omission"
    )
    unspecified.click()
    page.wait_for_timeout(80)
    press_continue(page)
    _amount_step(page)
    legends = page.locator(".leaf-panel legend").all_inner_texts()
    assert len(legends) == 2, f"a category plus \"I do not know\" is two leaves, got {legends}"
    assert "Not broken down by type" in legends[-1], (
        f"the unspecified leaf borrows another answer's words: {legends}"
    )


# ----------------------------------------------------------------- ghost figures


def test_unticking_a_category_parks_its_figures_and_re_ticking_brings_them_back(page):
    """An untick PARKS the record; it does not discard it.

    **This test asserted the opposite and was wrong.** The first fix for the
    figure-migration defect pruned `leafFigures` on the untick, and that overshot.
    The defect was a record reaching a DIFFERENT leaf - 400 kg typed under one
    category reappearing under the next one - and `leafFigures` is keyed by leaf,
    so a kept record can only ever come back to the food it was typed for.
    `test_leaf_figure_migration_browser.py` is what holds those two apart, and it
    stays green under this behaviour.

    Pruning on the untick instead destroyed work silently, for a food the visitor
    had named themselves, with no question asked - the exact class of defect the
    discard confirmation exists to prevent. A checkbox is the cheapest thing on
    the screen to press by accident.

    **Parked is not safe, and that is a separate rule.** `entryPatch` prunes to
    the loaded entry's own leaves, so loading an entry destroys every parked
    record; `loadingGivesBackTheDraft` counts them for that reason, and
    `test_edit_and_back_cleanup_browser.py` has the case. Nothing parked ever
    reaches a request body either - `draftEntry()` prunes at that boundary.
    """
    _to_food_step(page)
    _tick(page, 0, 1)
    press_continue(page)
    _amount_step(page)
    fields = _amount_fields(page)
    assert len(fields) == 2, fields
    page.fill(f"#{fields[0]}", "400")
    page.fill(f"#{fields[1]}", "800")
    page.wait_for_timeout(80)
    page.click('.step-nav [data-action="go-step"]')
    page.wait_for_selector('input[name="food-category"]')
    _tick(page, 1)  # untick
    _tick(page, 1)  # and back on
    press_continue(page)
    _amount_step(page)
    values = page.evaluate(
        "() => [...document.querySelectorAll('[data-leaf-field=amount]')].map(e => [e.id, e.value])"
    )
    revived = [row for row in values if row[1] == "800"]
    assert revived, (
        f"unticking a category and ticking it again lost the amount typed against "
        f"it. The visitor named that food themselves and nothing asked before it "
        f"went: {values}"
    )
    kept = [row for row in values if row[1] == "400"]
    assert kept, (
        f"the category that was never unticked lost its own amount: {values}"
    )


def test_clearing_the_only_selection_takes_its_figures_with_it(page):
    """**Nothing migrates between leaves, including when there is only one.**

    This assertion was the other way round for one round: a chain with one leaf was
    held to be "the same chain, only an optional label changed", so the figures
    carried. The owner reproduced what that rule actually does - tick the standard
    mix, type 400 kg, press Back, untick it and tick *Fruit*, and step 3 offers
    400 kg under Fruit - and ruled the carry out. Clearing is the same move with
    the category-less leaf on the far side of it, and §5.4 is explicit that "not
    broken down by type" is its own answer rather than a missing label, so the
    figures belong to the leaf that was cleared away and go with it.

    See `tests/web/test_leaf_figure_migration_browser.py` for the whole rule and
    for the half that must *not* change.
    """
    _to_food_step(page)
    _tick(page, 0)
    press_continue(page)
    page.wait_for_selector("#total-waste")
    page.fill("#total-waste", "640")
    page.wait_for_timeout(80)
    page.click('.step-nav [data-action="go-step"]')
    page.wait_for_selector('[data-action="clear-food"]')
    page.click('[data-action="clear-food"]')
    page.wait_for_timeout(150)
    press_continue(page)
    page.wait_for_selector("#total-waste")
    assert page.input_value("#total-waste") == "", (
        f"clearing the food selection carried the measurement taken under it onto "
        f"the category-less leaf: {page.input_value('#total-waste')!r}"
    )


# ----------------------------------------------------------------- the ceiling


def test_the_form_bounds_the_submissions_leaf_count_at_max_entries(page):
    """`MAX_ENTRIES` counts leaves, so step 2 has to bound the **whole submission**,
    not this chain. Reached by building one chain of every category the taxonomy
    offers and then ticking into a second until the form refuses.

    Left unbounded, the twenty-first leaf is a 400 the visitor meets after two more
    screens of typing.
    """
    _to_food_step(page)
    boxes = page.locator('input[name="food-category"]')
    offered = boxes.count()
    assert offered >= 3, f"the taxonomy offers {offered} food choices; too few to test"
    _tick(page, *range(offered))
    first_leaves = offered
    press_continue(page)
    _amount_step(page)
    for index, field in enumerate(_amount_fields(page)):
        page.fill(f"#{field}", str(100 + index))
        page.wait_for_timeout(35)
    press_continue(page)
    page.wait_for_selector('[data-line-field="amount"]')
    rows = _first_rows(page)
    for index, field in enumerate(rows):
        page.fill(f"#{field}", str(100 + index))
        page.wait_for_timeout(45)
    press_continue(page)
    page.wait_for_selector('[data-action="add-entry"]')
    page.click('[data-action="add-entry"]')
    _to_food_step_again = page.wait_for_selector("#stage-title")
    page.evaluate("document.querySelectorAll('input[name=sector]')[1].click()")
    page.wait_for_timeout(80)
    press_continue(page)
    page.wait_for_selector('input[name="food-category"]')

    room = MAX_LEAVES - first_leaves
    assert 0 < room < offered, (
        f"the taxonomy offers {offered} choices and the first chain used {first_leaves}, "
        f"which leaves {room} - this test needs the ceiling to fall inside one screen"
    )
    _tick(page, *range(room))
    disabled = page.evaluate(
        """() => [...document.querySelectorAll('input[name=food-category]')]
             .map(e => ({checked: e.checked, disabled: e.disabled}))"""
    )
    ticked = [row for row in disabled if row["checked"]]
    open_boxes = [row for row in disabled if not row["checked"] and not row["disabled"]]
    assert len(ticked) == room, f"expected {room} ticks on the second chain, got {len(ticked)}"
    assert not open_boxes, (
        f"the submission is at {MAX_LEAVES} leaves and {len(open_boxes)} boxes are still "
        f"tickable, so the form will let the visitor build a request the API answers 400 for"
    )
    assert page.locator(".choice-ceiling").count() == 1, (
        "the boxes went dead with nothing on screen saying why"
    )
    assert str(MAX_LEAVES) in page.inner_text(".choice-ceiling"), page.inner_text(".choice-ceiling")


# ----------------------------------------------------------------- per-leaf rules


def test_an_allocation_is_measured_against_its_own_leafs_amount(page):
    """**Today's rule, N times.** 150 kg allocated against a leaf that holds 100 is
    refused even though the chain holds 300 between its two leaves.

    Mutation: validating the allocation against the chain's combined total accepts
    this, and the API then refuses the whole submission for a per-entry
    mass-conservation failure the form had every figure needed to catch.
    """
    _to_food_step(page)
    _tick(page, 0, 1)
    press_continue(page)
    _amount_step(page)
    fields = _amount_fields(page)
    page.fill(f"#{fields[0]}", "100")
    page.fill(f"#{fields[1]}", "200")
    page.wait_for_timeout(80)
    press_continue(page)
    page.wait_for_selector('[data-line-field="amount"]')
    rows = _first_rows(page)
    # The second leaf is allocated in full first, so the only thing left to be wrong
    # is the first leaf's own over-allocation - a message about the OTHER leaf having
    # nothing allocated would otherwise satisfy the assertion below for the wrong reason.
    page.fill(f"#{rows[1]}", "200")
    page.wait_for_timeout(120)
    page.fill(f"#{rows[0]}", "150")
    page.wait_for_timeout(150)
    message = page.inner_text("#allocation-error").strip()
    assert message, (
        "150 kg was allocated against a leaf holding 100 kg and the form said nothing; "
        "the combined total is 300, which is what a chain-level check would compare to"
    )
    assert page.get_attribute('.step-nav [data-action="continue"]', "disabled") is not None, (
        f"Continue is still enabled over an over-allocated leaf: {message!r}"
    )
    page.fill(f"#{rows[0]}", "100")
    page.wait_for_timeout(150)
    assert page.inner_text("#allocation-error").strip() == "", (
        f"100 kg against a 100 kg leaf is a full allocation and must be accepted: "
        f"{page.inner_text('#allocation-error')!r}"
    )


def test_the_amount_error_lands_on_the_leaf_it_is_about(page):
    """`state.error` stopped identifying a field the moment two leaves could produce
    the byte-identical sentence. The message carries its place (`state.errorAt`), and
    this measures where the highlight actually landed.

    Mutation: attributing by string identity - `state.error === amountOnlyValidation()`
    - marks whichever card is asked first, which here is the one that is filled in.
    """
    _to_food_step(page)
    _tick(page, 0, 1)
    press_continue(page)
    _amount_step(page)
    fields = _amount_fields(page)
    page.fill(f"#{fields[0]}", "500")
    page.wait_for_timeout(80)
    press_continue(page)
    page.wait_for_timeout(250)
    marked = page.evaluate(
        """() => [...document.querySelectorAll('.leaf-panel')].map(panel => ({
             legend: panel.querySelector('legend').textContent.trim(),
             errors: panel.querySelectorAll('.field-error').length,
           }))"""
    )
    assert len(marked) == 2, marked
    assert marked[0]["errors"] == 0, (
        f"the leaf that holds 500 kg is marked as the fault: {marked}"
    )
    assert marked[1]["errors"] == 1, (
        f"the empty leaf carries no message, so the visitor is told something is wrong "
        f"and not where: {marked}"
    )
    said = page.locator(".leaf-panel").nth(1).locator(".field-error").inner_text()
    assert marked[1]["legend"] in said, (
        f"the message does not name the food it is about: {said!r} against "
        f"{marked[1]['legend']!r}"
    )
    summary = page.locator(".amount-validation-summary")
    assert summary.get_attribute("role") == "alert"
    link = summary.locator("a")
    assert link.count() == 1, summary.inner_text()
    assert marked[1]["legend"] in link.inner_text(), link.inner_text()
    assert "Waste amount" in link.inner_text(), link.inner_text()
    assert link.get_attribute("href") == f"#{fields[1]}"
    assert page.input_value(f"#{fields[0]}") == "500", (
        "showing the field-specific error discarded the valid amount in the other card"
    )
    presentation = page.evaluate(
        """() => {
          const title = document.querySelector('#amount-title').getBoundingClientRect();
          const summary = document.querySelector('.amount-validation-summary');
          const box = summary.getBoundingClientRect();
          return {
            besideTitle: box.left > title.left && box.top < title.bottom && box.bottom > title.top,
            background: getComputedStyle(summary).backgroundColor,
          };
        }"""
    )
    assert presentation["besideTitle"], presentation
    assert presentation["background"] == "rgba(255, 215, 110, 0.2)", presentation


@pytest.mark.parametrize("width", [320, 390])
def test_the_amount_error_summary_stacks_without_sideways_scroll(page, width):
    """The title and its summary share a row only when that row has room for both."""
    page.set_viewport_size({"width": width, "height": 700})
    _to_food_step(page)
    _tick(page, 0, 1)
    press_continue(page)
    _amount_step(page)
    press_continue(page)
    page.wait_for_selector(".amount-validation-summary")
    layout = page.evaluate(
        """() => {
          const title = document.querySelector('#amount-title').getBoundingClientRect();
          const summary = document.querySelector('.amount-validation-summary').getBoundingClientRect();
          return {
            titleBottom: title.bottom,
            summaryTop: summary.top,
            scrollWidth: document.documentElement.scrollWidth,
            clientWidth: document.documentElement.clientWidth,
          };
        }"""
    )
    assert layout["summaryTop"] >= layout["titleBottom"], layout
    assert layout["scrollWidth"] <= layout["clientWidth"], layout


def test_two_leaves_producing_the_identical_sentence_mark_only_the_one_at_fault(page):
    """**The case string identity cannot survive, and the reason `state.errorAt`
    exists.**

    The test above is satisfied by prose comparison, because the "greater than zero"
    message names its food once there is more than one leaf. Several of this step's
    messages do not and cannot - "Write the number out in full, using digits only."
    is about the characters typed, not about the food - so two leaves that make the
    same mistake produce the byte-identical sentence.

    `stepProblem` returns the first problem, so exactly one card is at fault. Deciding
    that by `state.error === amountOnlyValidation(...)` marks **both**, and the
    visitor is shown two faults where there is one, on a screen where fixing either
    one clears neither message.
    """
    _to_food_step(page)
    _tick(page, 0, 1)
    press_continue(page)
    _amount_step(page)
    fields = _amount_fields(page)
    for field in fields:
        page.fill(f"#{field}", "1e5")
        page.wait_for_timeout(60)
    press_continue(page)
    page.wait_for_timeout(250)
    marked = page.evaluate(
        """() => [...document.querySelectorAll('.leaf-panel')].map(panel => ({
             legend: panel.querySelector('legend').textContent.trim(),
             errors: [...panel.querySelectorAll('.field-error')].map(e => e.textContent.trim()),
           }))"""
    )
    assert len(marked) == 2, marked
    faulted = [row for row in marked if row["errors"]]
    assert len(faulted) == 1, (
        f"two leaves made the same mistake and produced the same sentence, so the "
        f"highlight is being decided by comparing prose: {marked}"
    )
    assert faulted[0]["legend"] == marked[0]["legend"], (
        f"the first leaf is the one `stepProblem` stopped at, and the mark landed "
        f"elsewhere: {marked}"
    )
    assert "digits only" in faulted[0]["errors"][0], faulted
