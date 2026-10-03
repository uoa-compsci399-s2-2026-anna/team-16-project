"""Issue #135: Step 4 indexes the food types chosen before allocation."""

from __future__ import annotations

import pytest

from tests.web.base_url import ORIGIN

pytestmark = pytest.mark.browser
pytest.importorskip("playwright.sync_api")

@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1278, "height": 800}, locale="en-NZ")
    opened = context.new_page()
    opened.goto(ORIGIN + "/index.html?lang=en", wait_until="networkidle")
    yield opened
    context.close()


def _reach_destinations(page, two_foods):
    page.click('[data-action="start"]')
    page.locator('input[name="sector"]').first.click()
    page.click('.step-nav [data-action="continue"]')
    page.locator('input[name="food-category"][value="vegetables"]').click()
    if two_foods:
        page.locator('input[name="food-category"][value="dairy"]').click()
    page.click('.step-nav [data-action="continue"]')
    if page.locator('#item-title').count():
        for card in page.locator('.item-group').all():
            toggle = card.locator('.step-card__toggle[aria-expanded]')
            if toggle.count() and toggle.get_attribute('aria-expanded') == 'false':
                toggle.click()
        page.locator('input[name="food-item"][value="potatoes"]').click()
        if two_foods:
            page.locator('input[name="food-item"][value="milk"]').click()
        page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector('#amount-title')
    if two_foods:
        for card in page.locator('.leaf-panel').all():
            toggle = card.locator('.step-card__toggle[aria-expanded]')
            if toggle.get_attribute('aria-expanded') == 'false':
                toggle.click()
            card.locator('input[data-leaf-field="amount"]').fill('1')
    else:
        page.locator('input[data-leaf-field="amount"]').fill('1')
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector('#destination-title')


@pytest.mark.parametrize("two_foods", [False, True])
def test_step_four_navigation_lists_and_opens_selected_foods(page, two_foods):
    _reach_destinations(page, two_foods)
    nav = page.locator('.destination-floating-nav')
    links = nav.locator('button')
    expected = ["Potatoes", "Milk"] if two_foods else ["Potatoes"]
    if not two_foods:
        assert nav.count() == 0
        assert page.locator('.destination-step .step-card').count() == 1
        return
    assert nav.is_visible()
    assert links.all_inner_texts() == expected
    page.wait_for_function("() => window.scrollY === 0")
    page.wait_for_function("() => document.querySelector('.destination-floating-nav button')?.getAttribute('aria-current') === 'location'")
    assert links.first.get_attribute('aria-current') == 'location'
    assert page.locator('[id^="destination-leaf-"]').count() == len(expected)
    assert page.locator('.destination-step .step-card').count() == 2
    assert page.evaluate(
        "document.querySelector('.step-floating-nav').compareDocumentPosition(document.querySelector('.destination-step__content')) & Node.DOCUMENT_POSITION_FOLLOWING"
    )
    for link in links.all():
        assert page.locator('#' + link.get_attribute('data-nav-target')).count() == 1

    target = '#' + links.last.get_attribute('data-nav-target')
    toggle = page.locator(target + ' .step-card__toggle[aria-expanded]')
    if toggle.get_attribute('aria-expanded') == 'true':
        toggle.click()
    assert toggle.get_attribute('aria-expanded') == 'false'
    history_length = page.evaluate('history.length')
    links.last.click()
    page.wait_for_function(
        "expected => document.querySelector('.destination-floating-nav button[aria-current="
        "\"location\"]').textContent === expected",
        arg=expected[-1],
    )
    assert page.locator(target).is_visible()
    assert toggle.get_attribute('aria-expanded') == 'true'
    assert page.evaluate('document.activeElement.id') == toggle.get_attribute('id')
    assert page.evaluate('location.hash') == ''
    assert page.evaluate('history.length') == history_length
    links.first.click()
    assert page.locator('.destination-floating-nav button[aria-current="location"]').inner_text() == expected[0]
    assert page.evaluate(
        "document.documentElement.scrollWidth === document.documentElement.clientWidth"
    )
    page.set_viewport_size({"width": 768, "height": 800})
    assert nav.is_visible()
    assert page.evaluate(
        "document.documentElement.scrollWidth === document.documentElement.clientWidth"
    )
    page.set_viewport_size({"width": 767, "height": 800})
    assert not nav.is_visible()
