"""A measured mass never moves onto a food nobody named.

``prunedLeafFigures`` carried a whole leaf record - amount, unit, measure mode,
unit preset, container count and the three optional scalars - whenever one leaf
became one other leaf. The stated ground was that step 2 has always kept the
measurement when only an optional *label* changed; but a label that changes from
``standard_mix`` to ``fruit`` is not a label, it is a different food, and the
carry attributed a mass the visitor measured for one food to another they had
just ticked for the first time.

**The owner reproduced this twice.** Tick a category, type an amount on step 3,
press Back, untick it and tick a different one: step 3 shows the amount under the
new food, with no dialog, and the request body carries it.

The rule these tests hold to: **a figure belongs to the leaf it was typed
against, and to no other leaf, ever.** Nothing carries. When the leaf's identity
is unchanged the record is kept by ``prunedLeafFigures``'s own ``keep`` set and no
carry is involved at all, which is why "carry only when the identity is
unchanged" and "do not carry" are the same rule - and this file measures both
halves, so removing the carry cannot be mistaken for dropping figures a leaf
still owns.

Requires the stack: ``docker compose -f docker/compose.yaml up -d --build web``.
"""

from __future__ import annotations

import json

import pytest

from tests.web.base_url import CALCULATOR
from tests.web.steps import expand_step_cards, press_continue


pytestmark = pytest.mark.browser

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to walk the owner's own journey",
)

BASE = CALCULATOR

#: Step 2's checkbox order is the taxonomy's `sort_order`: `standard_mix` first,
#: `fruit` second. Named by index rather than by code because the boxes are what
#: the visitor clicks and the codes are asserted on separately below.
STANDARD_MIX = 0
FRUIT = 1


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
        page.wait_for_timeout(70)


def _code_of(page, index):
    return page.evaluate(
        f"() => document.querySelectorAll('input[name=food-category]')[{index}].value"
    )


def _leaf_amounts(page):
    """Every step-3 card, as ``{leaf, name, amount, count, unit}``.

    **One leaf renders today's screen byte for byte** - no card, no legend - so the
    leaf is identified by the ``data-leaf`` attribute its own controls carry
    (``leafKey``, percent-encoded) rather than by a heading that only exists once
    there are two of them. ``name`` is the legend when the card has one and ``''``
    when it does not.
    """
    return page.evaluate(
        """() => {
          const panels = [...document.querySelectorAll('.leaf-panel')];
          const roots = panels.length ? panels : [...document.querySelectorAll('.amount-grid')];
          return roots.map(panel => {
            const amount = panel.querySelector('[data-leaf-field=amount]');
            const count = panel.querySelector('[data-leaf-field=count]');
            const unit = panel.querySelector('[data-leaf-field=unit]');
            return {
              leaf: decodeURIComponent((amount || count || unit)?.dataset.leaf || ''),
              name: panel.querySelector('legend')?.textContent?.trim() || '',
              amount: amount?.value ?? '',
              count: count?.value ?? '',
              unit: unit?.value ?? '',
            };
          });
        }"""
    )


# ------------------------------------------------------- the owner's own journey


def test_swapping_the_only_category_does_not_move_the_amount_onto_the_new_food(page):
    """**Reproduced by the owner, twice.** ``standard_mix`` then 400 then Back then
    untick then tick ``fruit``: step 3 shows 400 under *Fruit*.

    Mutation: restoring the 1 to 1 carry in ``prunedLeafFigures`` makes this fail
    printing the amount it found and the food it found it under.
    """
    _to_food_step(page)
    _tick(page, STANDARD_MIX)
    press_continue(page)
    page.wait_for_selector("#total-waste")
    page.fill("#total-waste", "400")
    page.wait_for_timeout(100)

    page.click('.step-nav [data-action="go-step"]')
    page.wait_for_selector('input[name="food-category"]')
    _tick(page, STANDARD_MIX)  # untick it
    fruit = _code_of(page, FRUIT)
    _tick(page, FRUIT)  # and name a different food
    press_continue(page)
    page.wait_for_selector("#total-waste")

    found = _leaf_amounts(page)
    assert len(found) == 1, found
    leaf = found[0]
    assert leaf["leaf"].startswith(fruit), (
        f"the leaf on screen is not the food that was ticked: {found}"
    )
    assert leaf["amount"] == "", (
        f"swapping the food category moved a measured mass onto a food nobody named: "
        f"step 3 shows {leaf['amount']!r} kg under {fruit!r}. A visitor who swaps a "
        f"category has told the calculator nothing about the new one."
    )


def test_the_swapped_amount_does_not_reach_the_request_body(page):
    """The screen is the visible half; this is the half that produces a wrong
    number. The request must carry no mass at all for a food whose box was ticked
    after the measurement was taken.
    """
    bodies: list[str] = []
    page.on(
        "request",
        lambda request: bodies.append(request.post_data)
        if request.method == "POST" and request.url.endswith("/api/v1/calculate")
        else None,
    )
    _to_food_step(page)
    _tick(page, STANDARD_MIX)
    press_continue(page)
    page.wait_for_selector("#total-waste")
    page.fill("#total-waste", "400")
    page.wait_for_timeout(100)
    press_continue(page)
    page.wait_for_selector('[data-line-field="amount"]')
    first_row = page.evaluate(
        "() => document.querySelectorAll('[data-line-field=amount]')[0].id"
    )
    page.fill(f"#{first_row}", "400")
    page.wait_for_timeout(120)

    # Back to step 2 and swap the food. Everything typed above belongs to
    # `standard_mix` and to nothing else.
    page.click('.step-nav [data-action="go-step"]')
    page.wait_for_selector("#total-waste")
    page.click('.step-nav [data-action="go-step"]')
    page.wait_for_selector('input[name="food-category"]')
    _tick(page, STANDARD_MIX)
    fruit = _code_of(page, FRUIT)
    _tick(page, FRUIT)

    # Type the new food's own figures and calculate.
    press_continue(page)
    page.wait_for_selector("#total-waste")
    assert page.input_value("#total-waste") == "", (
        f"step 3 offered {page.input_value('#total-waste')!r} for a food that was "
        f"ticked after the measurement was taken"
    )
    page.fill("#total-waste", "50")
    page.wait_for_timeout(100)
    press_continue(page)
    page.wait_for_selector('[data-line-field="amount"]')
    row = page.evaluate("() => document.querySelectorAll('[data-line-field=amount]')[0].id")
    page.fill(f"#{row}", "50")
    page.wait_for_timeout(120)
    press_continue(page)
    page.wait_for_selector('[data-action="calculate"]')
    page.click('.step-nav [data-action="calculate"]')
    page.wait_for_selector(".results-page", timeout=25000)

    assert bodies, "nothing was posted to /api/v1/calculate"
    entries = json.loads(bodies[-1])["entries"]
    assert len(entries) == 1, entries
    entry = entries[0]
    assert entry["food_category"] == fruit, entry
    carried = sum(float(line["qty_kg"]) for line in entry["current"])
    assert abs(carried - 50.0) < 0.001, (
        f"the request body attributes {carried} kg to {fruit!r}; only the 50 kg typed "
        f"against it may reach the wire, never the 400 kg measured for another food: "
        f"{entry}"
    )


def test_swapping_the_category_does_not_carry_a_unit_preset_with_it(page):
    """**Defect two, and the hazard ``presetSurvives``/``presetPatch`` guarded.**
    Those were deleted on the stated ground that a leaf's category cannot change,
    and then a rule was written that changes it. A container count of three
    240-litre wheelie bins is a measurement of one food's waste; it says nothing
    about the next food the visitor ticks.
    """
    _to_food_step(page)
    _tick(page, STANDARD_MIX)
    press_continue(page)
    page.wait_for_selector("#total-unit")
    presets = page.evaluate(
        """() => [...document.querySelectorAll('#total-unit option')]
             .map(o => o.value).filter(v => v.startsWith('preset:'))"""
    )
    assert presets, "step 3 offers no container presets, so this rule cannot be measured"
    preset = presets[-1]
    page.select_option("#total-unit", preset)
    page.wait_for_timeout(120)
    page.fill("#unit-count", "3")
    page.wait_for_timeout(120)
    measured = page.inner_text(".amount-grid")

    page.click('.step-nav [data-action="go-step"]')
    page.wait_for_selector('input[name="food-category"]')
    _tick(page, STANDARD_MIX)
    fruit = _code_of(page, FRUIT)
    _tick(page, FRUIT)
    press_continue(page)
    page.wait_for_selector("#total-unit")

    found = _leaf_amounts(page)
    assert len(found) == 1, found
    leaf = found[0]
    assert leaf["leaf"].startswith(fruit), (
        f"the leaf on screen is not the food that was ticked: {found}"
    )
    assert not leaf["unit"].startswith("preset:"), (
        f"the container preset {leaf['unit']!r} survived a swap from the standard mix "
        f"onto {fruit!r}; it was measured for a different food. Earlier screen: "
        f"{measured.splitlines()[:4]}"
    )
    assert leaf["count"] == "", (
        f"the container count {leaf['count']!r} survived a swap onto {fruit!r}"
    )


# ------------------------------------------------ the half that must NOT change


def test_a_leaf_whose_identity_is_unchanged_keeps_every_figure(page):
    """The other half of the same rule, and the reason removing the carry is safe:
    a leaf that is still the same leaf keeps its record through
    ``prunedLeafFigures``'s own ``keep`` set, with no carry involved. Ticking a
    *second* category must not disturb the first one's figures.

    Mutation: pruning on identity rather than on the leaf key - or clearing
    ``leafFigures`` wholesale on every tick - fails here naming what was lost.
    """
    _to_food_step(page)
    _tick(page, STANDARD_MIX)
    press_continue(page)
    page.wait_for_selector("#total-waste")
    page.fill("#total-waste", "640")
    page.wait_for_timeout(100)
    page.click('.step-nav [data-action="go-step"]')
    page.wait_for_selector('input[name="food-category"]')
    fruit = _code_of(page, FRUIT)
    _tick(page, FRUIT)  # a second food, alongside the first
    press_continue(page)
    page.wait_for_selector('[data-leaf-field="amount"]', state="attached")
    expand_step_cards(page)

    found = _leaf_amounts(page)
    assert len(found) == 2, found
    kept = [row for row in found if row["amount"] == "640"]
    assert len(kept) == 1, (
        f"the leaf that was never unticked lost the amount typed against it, or it "
        f"was copied onto the new one: {found}"
    )
    assert not kept[0]["leaf"].startswith(fruit), (
        f"the 640 kg is now attributed to {kept[0]['leaf']!r}: {found}"
    )
