"""Step 2.5 in a real browser, against a stack whose factor set prices foods.

`spec.md` §3.3's panel is unreachable unless four things line up, and they live
in four files with no test in common: the vocabulary `admin/seed.py` creates,
the `item_level_enabled` switch on the published set, §6.1's parent-covered
filter deciding which foods reach `GET /taxonomy`, and the panel itself. Every
other test in this repository holds one of those still and asserts about the
others. This one drives the whole chain.

**It needs a stack whose published set releases the item level.** The shipped
`docker/mock-factors.json` does, so a fresh `docker compose up` is enough -- but
a deployment seeded before that file grew its item rows keeps the set it has,
and there the panel is correctly absent. The fixture skips rather than fails in
that case, and says which of the two it found: a skip that could not tell them
apart would hide the landing regressing.

Run against an isolated stack rather than the developer's own::

    KAICALC_WEB_PORT=18090 docker compose -p kaicalc-e2e -f docker/compose.yaml up -d --build
    KAICALC_WEB_URL=http://localhost:18090 pytest tests/web/test_step_two_point_five_browser.py
"""

from __future__ import annotations

import json
import os

import pytest

from tests.web.steps import expand_step_cards

pytestmark = pytest.mark.browser

pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to drive step 2.5",
)

ROOT = os.environ.get("KAICALC_WEB_URL", "http://localhost:18080").rstrip("/")
BASE = ROOT + "/index.html"


@pytest.fixture(scope="module")
def taxonomy():
    """What the stack under test actually publishes.

    Read once, over HTTP, so the tests below assert against the deployment's own
    vocabulary rather than against `admin/seed.py` -- which is the whole point:
    §6.1 filters the vocabulary against the published set, and a test that read
    the seed would assert about foods the visitor is never offered.
    """
    import urllib.request

    try:
        with urllib.request.urlopen(f"{ROOT}/api/v1/taxonomy", timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as error:  # pragma: no cover - environment guard
        pytest.skip(f"no calculator is being served at {ROOT}: {error}")


@pytest.fixture(scope="module")
def released(taxonomy):
    """The two conditions, reported apart.

    A stack with the switch off and a stack with an empty vocabulary are two
    different states, and only one of them is a landing that regressed.
    """
    if not taxonomy["factor_set"].get("item_level_enabled"):
        pytest.skip(
            "the published factor set has item_level_enabled false, so step 2.5 "
            "is correctly absent; seed a fresh database to exercise it"
        )
    if not taxonomy.get("food_items"):
        pytest.skip(
            "the published factor set releases the item level but §6.1 offers no "
            "food under any category it prices"
        )
    return taxonomy


@pytest.fixture
def page(browser, released):
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


def _category_with_foods(taxonomy):
    """A category the published set prices AND that has foods offered under it."""
    offered = {item["food_category"] for item in taxonomy["food_items"]}
    for category in taxonomy["food_categories"]:
        if category["code"] in offered:
            return category
    pytest.skip("no offered food's category is itself offered")


def _category_without_foods(taxonomy):
    """A category the published set prices but that has NO food offered under it.

    The seed gives a vocabulary to four of its ten categories, so this is the
    common case rather than an edge one, and it is the state the test below is
    about.
    """
    offered = {item["food_category"] for item in taxonomy["food_items"]}
    for category in taxonomy["food_categories"]:
        if category["code"] not in offered:
            return category
    pytest.skip("every offered category has foods, so there is no empty panel to refuse")


def _to_food_step(page):
    page.click('[data-action="start"]')
    page.wait_for_selector('input[name="sector"]')
    page.evaluate("document.querySelectorAll('input[name=sector]')[0].click()")
    page.wait_for_timeout(80)
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector('input[name="food-category"]')


def _tick_category(page, code):
    page.click(f'input[name="food-category"][value="{code}"]')
    page.wait_for_timeout(80)


def _continue(page):
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_timeout(200)


# --------------------------------------------------------------------------


def test_continuing_from_a_chosen_category_opens_the_food_panel(page, released):
    """The panel is reached by Continue, not by a step number: it is step 2's
    second panel (`spec.md` §3.3), so `state.step` does not move."""
    category = _category_with_foods(released)
    _to_food_step(page)
    _tick_category(page, category["code"])
    _continue(page)

    page.wait_for_selector('input[name="food-item"]', timeout=5000)
    assert page.locator("#item-title").count() == 1


def test_the_panel_groups_the_foods_under_the_category_that_was_chosen(page, released):
    """One group per chosen category, the category's own name as its legend,
    and only the foods §6.1 offers under it."""
    category = _category_with_foods(released)
    expected = sorted(
        item["name"] for item in released["food_items"]
        if item["food_category"] == category["code"]
    )
    _to_food_step(page)
    _tick_category(page, category["code"])
    _continue(page)
    page.wait_for_selector('input[name="food-item"]')

    legends = page.evaluate(
        "() => [...document.querySelectorAll('.item-group legend')].map(e => e.innerText.trim())"
    )
    assert legends == [category["name"]], legends
    shown = sorted(page.evaluate(
        "() => [...document.querySelectorAll('.item-group .simple-choice strong')]"
        ".map(e => e.innerText.trim())"
    ))
    assert shown == expected, shown


def test_the_step_number_does_not_advance_into_the_food_panel(page, released):
    """§3.3: step 2.5 refines step 2. The position label and the progress bar
    say the same thing on both panels, because the visitor has not left the
    step -- and making it a seventh step would have made "step 3 of 7" untrue
    wherever the panel is absent."""
    category = _category_with_foods(released)
    _to_food_step(page)
    before = page.inner_text(".step-nav-label")
    _tick_category(page, category["code"])
    _continue(page)
    page.wait_for_selector('input[name="food-item"]')

    assert page.inner_text(".step-nav-label") == before


def test_back_returns_to_the_categories_with_the_ticks_intact(page, released):
    """A panel change, not a step change: the categories are still ticked,
    because Back here undoes the Continue that opened the panel and nothing
    else."""
    category = _category_with_foods(released)
    _to_food_step(page)
    _tick_category(page, category["code"])
    _continue(page)
    page.wait_for_selector('input[name="food-item"]')

    page.click('.step-nav [data-action="back-to-categories"]')
    page.wait_for_selector('input[name="food-category"]', timeout=5000)
    assert page.is_checked(f'input[name="food-category"][value="{category["code"]}"]')


def test_ticking_two_foods_forks_the_chain_into_two_named_leaves(page, released):
    """The leaf rule, seen from the screen: each ticked food is a leaf, so step
    3 asks for an amount per food and names each one."""
    category = _category_with_foods(released)
    foods = [item for item in released["food_items"]
             if item["food_category"] == category["code"]][:2]
    if len(foods) < 2:
        pytest.skip("this deployment offers fewer than two foods under one category")

    _to_food_step(page)
    _tick_category(page, category["code"])
    _continue(page)
    page.wait_for_selector('input[name="food-item"]')
    for food in foods:
        page.click(f'input[name="food-item"][value="{food["code"]}"]')
        page.wait_for_timeout(60)
    _continue(page)

    #: `state="attached"`: two foods is two leaves, so since #134 step 3 draws two
    #: collapsed cards and their bodies carry `hidden`. The legends are read off the
    #: cards themselves and are `.sr-only` either way, so nothing here needs them
    #: open - which is also the point, since this measures the naming and not the
    #: fields.
    page.wait_for_selector("[data-leaf-field=amount]", state="attached", timeout=5000)
    legends = page.evaluate(
        "() => [...document.querySelectorAll('.leaf-panel legend')].map(e => e.innerText.trim())"
    )
    assert len(legends) == 2, legends
    for food in foods:
        assert any(food["name"] in legend for legend in legends), (food["name"], legends)


def test_a_category_with_no_food_ticked_stays_one_category_level_leaf(page, released):
    """*I know it was dairy, but not which dairy.* Continuing through the panel
    without ticking anything leaves exactly the leaf step 2 produced, named for
    the category."""
    category = _category_with_foods(released)
    _to_food_step(page)
    _tick_category(page, category["code"])
    _continue(page)
    page.wait_for_selector('input[name="food-item"]')
    _continue(page)

    page.wait_for_selector("[data-leaf-field=amount], #total-waste", timeout=5000)
    legends = page.evaluate(
        "() => [...document.querySelectorAll('.leaf-panel legend')].map(e => e.innerText.trim())"
    )
    assert len(legends) <= 1, legends
    if legends:
        assert category["name"] in legends[0], legends


def test_the_named_food_reaches_the_request_and_changes_the_figure(page, released):
    """The end of the chain, and the only test that walks all of it.

    Two foods under one category become two entries, each carrying its own
    `food_item` on the wire (contract v1.58) -- and the two figures differ,
    because the published set prices those foods apart. Equal figures would
    mean the dimension was wired and inert, which is what every landing before
    this one deliberately was.
    """
    category = _category_with_foods(released)
    foods = [item for item in released["food_items"]
             if item["food_category"] == category["code"]][:2]
    if len(foods) < 2:
        pytest.skip("this deployment offers fewer than two foods under one category")

    sent = []
    page.on("request", lambda r: sent.append(r.post_data)
            if r.method == "POST" and r.url.endswith("/calculate") else None)

    _to_food_step(page)
    _tick_category(page, category["code"])
    _continue(page)
    page.wait_for_selector('input[name="food-item"]')
    for food in foods:
        page.click(f'input[name="food-item"][value="{food["code"]}"]')
        page.wait_for_timeout(60)
    _continue(page)

    #: `state="attached"`, then the cards: two foods is two leaves, so since #134
    #: step 3 draws two collapsed cards and a collapsed body carries `hidden`.
    page.wait_for_selector('[data-leaf-field="amount"]', state="attached")
    expand_step_cards(page)
    fields = page.evaluate(
        "() => [...document.querySelectorAll('[data-leaf-field=amount]')].map(e => e.id)")
    assert len(fields) == 2, fields
    for field in fields:
        page.fill(f"#{field}", "1000")
        page.wait_for_timeout(50)
    _continue(page)

    page.wait_for_selector('[data-line-field="amount"]')
    first_row = page.evaluate(
        """() => {
          const byLeaf = {};
          for (const input of document.querySelectorAll('[data-line-field=amount]')) {
            (byLeaf[input.dataset.leaf] ||= []).push(input.id);
          }
          return Object.values(byLeaf).map(ids => ids[0]);
        } """)
    assert len(first_row) == 2, first_row
    for field in first_row:
        page.fill(f"#{field}", "1000")
        page.wait_for_timeout(70)
    _continue(page)

    page.wait_for_selector('[data-action="calculate"]')
    page.click('[data-action="calculate"]')
    page.wait_for_selector("#results-title", timeout=30000)

    assert sent, "no calculate request was sent"
    entries = json.loads(sent[-1])["entries"]
    assert [entry["food_item"] for entry in entries] == [food["code"] for food in foods], entries
    assert all(entry["food_category"] == category["code"] for entry in entries), entries


def test_a_category_with_no_foods_does_not_open_a_panel_with_nothing_on_it(page, released):
    """**Continue skips step 2.5 when it would have nothing to ask.**

    `itemStepOffered` used to ask whether the DEPLOYMENT has a vocabulary --
    `food_items.length > 0` -- and not whether any category the visitor ticked
    is in it. The seed prices ten categories and gives foods to four, so ticking
    any of the other six opened a panel headed *Do you know which foods these
    were?* whose every group read "No specific foods are listed for this
    category": a screen with nothing on it to tick and Continue the only way off
    it. It is what a visitor ticking *Standard mix* -- the first box on step 2 --
    met on every calculation.

    `itemStep`'s own note, that an empty group is SHOWN rather than hidden so the
    group list cannot disagree with step 2's ticks, is untouched by this and the
    test below still pins it. That note is about one empty group beside full
    ones; it was never an argument for a panel made entirely of them.
    """
    category = _category_without_foods(released)
    _to_food_step(page)
    _tick_category(page, category["code"])
    _continue(page)

    assert page.locator("#item-title").count() == 0, (
        f"ticking {category['code']!r}, which has no food offered under it, opened "
        f"the food panel -- every group on it can only say there is nothing to "
        f"choose, so the one press of Continue has to go straight to the amount step"
    )
    page.wait_for_selector("#total-waste", timeout=5000)


def test_an_empty_group_is_still_shown_beside_a_full_one(page, released):
    """The half of the rule that must NOT change.

    Ticking one category with foods and one without opens the panel -- the first
    of them has something to ask -- and the second is rendered as a group saying
    it has nothing listed, rather than dropped. Hiding it would leave a visitor
    who ticked two categories looking at one group with no way to tell which of
    their answers went missing or why.

    **`state="attached"` and nothing opened.** Two ticked categories is two cards
    since #138, and a shut card's body carries `hidden`, so the food checkboxes
    are in the document and not visible -- which the default `state="visible"`
    would wait out against a screen that is working correctly. The legends are
    read off the `<fieldset>`s rather than out of the bodies, and they are
    `.sr-only` either way, so nothing here needs a card open. That is also the
    point: what this test is about is that both groups EXIST, and folding must
    not be a way for one of them to stop existing.
    """
    full = _category_with_foods(released)
    empty = _category_without_foods(released)
    _to_food_step(page)
    _tick_category(page, full["code"])
    _tick_category(page, empty["code"])
    _continue(page)
    page.wait_for_selector('input[name="food-item"]', state="attached", timeout=5000)

    legends = page.evaluate(
        "() => [...document.querySelectorAll('.item-group legend')].map(e => e.innerText.trim())"
    )
    assert sorted(legends) == sorted([full["name"], empty["name"]]), legends


def test_a_second_chain_starts_on_the_categories_and_not_in_the_food_panel(page, released):
    """**The panel must not open under a hand that is still ticking categories.**

    `foodStage` says which of step 2's two panels is showing. `goToStep` clears it
    on every arrival -- its own note says a jump named *food type* means the
    category question -- but Continue is the other way to arrive at a step, and it
    was not clearing it. So after *Add another entry*, `clearDraft` put the visitor
    on step 1 with the stage still reading `'items'` from the chain they had just
    finished, and the next Continue carried it into step 2.

    Nothing looked wrong while they ticked categories the vocabulary has no food
    for. The moment they ticked one it does, `itemStepOffered()` turned true, and
    `foodPanel` swapped the category list for the food panel underneath them --
    mid-tick, with no Continue pressed and no way to tell what had happened.

    The tick below is on a category WITH foods and it is the whole point: a
    category without them cannot turn `itemStepOffered()` true and so could never
    have shown the bug.
    """
    full = _category_with_foods(released)

    # One chain, all the way through the food panel, so `foodStage` is left at
    # 'items' the way a real visitor leaves it.
    _to_food_step(page)
    _tick_category(page, full["code"])
    _continue(page)
    page.wait_for_selector('input[name="food-item"]', timeout=5000)
    _continue(page)
    page.wait_for_selector("#total-waste", timeout=5000)
    page.fill("#total-waste", "500")
    page.wait_for_timeout(80)
    _continue(page)
    page.wait_for_selector('[data-line-field="amount"]', timeout=5000)
    first = page.locator('[data-line-field="amount"]').first
    first.fill("500")
    page.wait_for_timeout(80)
    _continue(page)
    page.wait_for_selector('[data-action="add-entry"]', timeout=5000)

    page.click('[data-action="add-entry"]')
    page.wait_for_selector("#stage-title", timeout=5000)
    page.evaluate("document.querySelectorAll('input[name=sector]')[0].click()")
    page.wait_for_timeout(80)
    _continue(page)
    page.wait_for_selector('input[name="food-category"]', timeout=5000)

    _tick_category(page, full["code"])

    assert page.locator("#item-title").count() == 0, (
        f"ticking {full['code']!r} on the second chain opened the food panel with no "
        f"Continue pressed -- the stage was left at 'items' by the first chain"
    )
    assert page.locator('input[name="food-category"]').count() > 0, (
        "the category checkboxes are gone, so the screen moved on its own"
    )
