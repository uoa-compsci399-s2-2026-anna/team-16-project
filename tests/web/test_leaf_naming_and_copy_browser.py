"""One name per leaf, one ceiling rule, and copy that is English.

Four defects share this file because they share a journey: a chain that names
several foods, walked to the end.

* **Two leaves could render byte-identical.** ``results.js`` returned
  ``Standard mix / not specified`` for a NULL ``food_category`` *and* for the
  ``standard_mix`` category. Step 2 offers those as two separate boxes - §5.4
  requires it - so a visitor who ticks both got two indistinguishable rows
  carrying different numbers. And the same leaf had four names across step 3,
  the saved-entry card, the review step and the exports.
* **``t('List separator')`` rendered the literal words.** ``i18n.js`` says it
  outright: English is not a catalogue file, so an English key IS the English
  output. The review step read
  ``DairyList separatorBakery and grainsList separatorFruit``.
* **"These 1 entries will be calculated as 3 food-type entries."** read verbatim
  off the review step, for one chain naming three foods - the commonest forked
  case.
* **The leaf ceiling had two doors and one guard.** ``MAX_ENTRIES`` counts
  leaves; step 2 asked whether the submission was *already* at twenty rather than
  whether the tick would take it past, and *Add another supply-chain entry* asked
  nothing at all.

Requires the stack: ``docker compose -f docker/compose.yaml up -d --build web``.
"""

from __future__ import annotations

import os

import pytest

from tests.web.steps import press_continue


pytestmark = pytest.mark.browser

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to read the rendered copy",
)

BASE = os.environ.get("KAICALC_WEB_URL", "http://localhost:18080").rstrip("/") + "/index.html"

#: `api/schemas.py`'s `MAX_ENTRIES`, restated here for the reason `calculator.js`
#: restates it: a client-side ceiling may refuse earlier and more kindly than the
#: API, and must never refuse something the API would take.
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


def _sector(page, index):
    page.wait_for_selector('input[name="sector"]')
    page.evaluate(f"document.querySelectorAll('input[name=sector]')[{index}].click()")
    page.wait_for_timeout(70)
    press_continue(page)
    page.wait_for_selector('input[name="food-category"]')


def _tick(page, *indices):
    boxes = page.locator('input[name="food-category"]')
    for index in indices:
        boxes.nth(index).click()
        page.wait_for_timeout(55)


def _fill_amounts_and_rows(page, amount="100"):
    """Step 3 then step 4, filling every leaf's amount and its first destination."""
    press_continue(page)
    page.wait_for_selector('[data-leaf-field="amount"], #total-waste')
    fields = page.evaluate(
        "() => [...document.querySelectorAll('[data-leaf-field=amount]')].map(e => e.id)"
    )
    for field in fields:
        page.fill(f"#{field}", amount)
        page.wait_for_timeout(25)
    press_continue(page)
    page.wait_for_selector('[data-line-field="amount"]')
    rows = page.evaluate(
        """() => {
          const byLeaf = {};
          for (const input of document.querySelectorAll('[data-line-field=amount]')) {
            (byLeaf[input.dataset.leaf] ||= []).push(input.id);
          }
          return Object.values(byLeaf).map(ids => ids[0]);
        }"""
    )
    for field in rows:
        page.fill(f"#{field}", amount)
        page.wait_for_timeout(30)
    press_continue(page)
    page.wait_for_selector('[data-action="calculate"]')


# ------------------------------------------------------------- one name per leaf


def test_two_leaves_of_one_chain_never_render_the_same_text(page):
    """**The standard mix and "I do not know" are two answers, and §5.4 forbids
    conflating them.** Ticking both is one chain with two leaves, two masses and
    two sets of figures; if they print alike, the results page shows two rows a
    reader cannot tell apart.

    Mutation: returning one shared string for both - the
    `Standard mix / not specified` this replaced - fails here naming the text the
    two rows share.
    """
    page.click('[data-action="start"]')
    _sector(page, 0)
    _tick(page, 0)  # the standard mix, which is its own category with its own code
    page.locator("input#food-category-unspecified").click()
    page.wait_for_timeout(80)
    _fill_amounts_and_rows(page)

    review = page.evaluate(
        "() => [...document.querySelectorAll('.review-leaf h3')].map(e => e.textContent.trim())"
    )
    assert len(review) == 2 * 2, (
        f"a chain naming the standard mix AND \"I do not know\" has two leaves in "
        f"each of the review step's two blocks: {review}"
    )
    amounts, destinations = review[:2], review[2:]
    assert len(set(amounts)) == 2, (
        f"the review step's two leaves carry the same name: {amounts}"
    )
    assert amounts == destinations, (
        f"the review step names the same two leaves differently in its two blocks: "
        f"{amounts} vs {destinations}"
    )

    page.click('.step-nav [data-action="calculate"]')
    page.wait_for_selector(".results-page", timeout=25000)
    page.click('[data-action="breakdown-tab"][data-tab="stage"]')
    page.wait_for_timeout(200)
    labels = page.evaluate(
        """() => [...document.querySelectorAll('#breakdown-panel-stage tbody tr th[scope=row]')]
             .map(cell => cell.textContent.trim())"""
    )
    assert len(labels) == 2, f"the stage tab shows {len(labels)} rows, expected 2: {labels}"
    repeated = [name for name in set(labels) if labels.count(name) > 1]
    assert not repeated, (
        f"the results page prints {repeated} more than once, so two leaves carrying "
        f"different figures cannot be told apart: {labels}"
    )
    for name in amounts:
        assert any(name in label for label in labels), (
            f"the leaf the form called {name!r} is called something else on the "
            f"results page: {labels}"
        )


# --------------------------------------------------------------------- the copy


def test_the_review_step_never_prints_the_words_list_separator(page):
    """`i18n.js:293`: English is not a catalogue file, so an English key is the
    English output - and `t('List separator')` is a description of a string, not
    a string. English is the client-facing surface.
    """
    page.click('[data-action="start"]')
    _sector(page, 0)
    _tick(page, 1, 2, 3)
    _fill_amounts_and_rows(page)
    text = page.inner_text(".content-section")
    assert "List separator" not in text, (
        f"the review step renders the catalogue key rather than the separator: "
        f"{[line for line in text.splitlines() if 'List separator' in line]}"
    )
    foods = page.inner_text(".review-block:has(h2) p")
    assert ", " in page.evaluate(
        """() => [...document.querySelectorAll('.review-block')]
             .map(block => block.textContent)
             .find(text => text.includes('Food categories')) || ''"""
    ), f"the food list is not separated at all: {foods!r}"


def test_the_leaf_count_note_is_grammatical_for_one_chain(page):
    """One chain of three foods is the commonest forked case, and it read
    "These 1 entries will be calculated as 3 food-type entries."
    """
    page.click('[data-action="start"]')
    _sector(page, 0)
    _tick(page, 1, 2, 3)
    _fill_amounts_and_rows(page)
    note = page.inner_text(".leaf-count-note")
    assert "These 1 entries" not in note, f"the review step reads {note!r}"
    assert note.strip() == "This entry will be calculated as 3 food-type entries.", (
        f"the one-chain leaf-count note reads {note!r}"
    )


# ------------------------------------------------------------------- the ceiling


def test_the_ceiling_refuses_the_tick_that_would_exceed_it_and_not_the_one_that_would_not(page):
    """**The guard was wrong at both ends.**

    Step 2 asked `submissionLeafCount() >= MAX_LEAVES` - whether the submission
    is at twenty *now* - and ticking the first category on an empty draft replaces
    that draft's category-less leaf with a named one and adds nothing. At exactly
    nineteen saved leaves plus an empty draft the old guard therefore refused a
    move the API would have taken. And *Add another supply-chain entry* had no
    ceiling check at all, so a visitor could open a twenty-first leaf and meet the
    ceiling as a 400 four screens later.

    Built to nineteen saved leaves: ten food types in one chain, nine in another.
    """
    page.click('[data-action="start"]')
    _sector(page, 0)
    offered = page.locator('input[name="food-category"]').count() - 1  # less "I do not know"
    assert offered >= 10, f"the taxonomy offers {offered} food categories; too few"
    _tick(page, *range(10))
    _fill_amounts_and_rows(page)
    page.click('[data-action="add-entry"]')
    page.wait_for_selector("#stage-title")
    _sector(page, 1)
    _tick(page, *range(9))
    _fill_amounts_and_rows(page)

    # Nineteen saved leaves once this chain is committed, and an empty draft after it.
    page.click('[data-action="add-entry"]')
    page.wait_for_selector("#stage-title")
    _sector(page, 2)
    state = page.evaluate(
        """() => [...document.querySelectorAll('input[name=food-category]')]
             .map(e => ({value: e.value, checked: e.checked, disabled: e.disabled}))"""
    )
    open_boxes = [row for row in state if not row["disabled"]]
    assert open_boxes, (
        "at nineteen saved leaves the empty draft already carries its own "
        "category-less leaf, so naming a food adds nothing and every box was "
        "refused for a move the API would have taken"
    )
    _tick(page, 0)
    after = page.evaluate(
        """() => [...document.querySelectorAll('input[name=food-category]')]
             .map(e => ({checked: e.checked, disabled: e.disabled}))"""
    )
    assert after[0]["checked"], f"the tick was refused at nineteen saved leaves: {after}"
    still_open = [row for row in after if not row["checked"] and not row["disabled"]]
    assert not still_open, (
        f"the submission is at {MAX_LEAVES} leaves and {len(still_open)} boxes are "
        f"still tickable, so the form will let the visitor build a request the API "
        f"answers 400 for"
    )

    # And the other door.
    _fill_amounts_and_rows(page)
    add = page.locator('[data-action="add-entry"]')
    assert add.is_disabled(), (
        "the submission is at the leaf ceiling and *Add another supply-chain entry* "
        "is still live; a new chain's own empty draft is a twenty-first leaf"
    )
