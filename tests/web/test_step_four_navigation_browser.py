"""Issue #135: Step 4 indexes the food types chosen before allocation."""

from __future__ import annotations

import os

import pytest


pytestmark = pytest.mark.browser
pytest.importorskip("playwright.sync_api")

BASE = os.environ.get("KAICALC_WEB_URL", "http://localhost:18080").rstrip("/")
@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1278, "height": 800}, locale="en-NZ")
    opened = context.new_page()
    opened.goto(BASE + "/index.html?lang=en", wait_until="networkidle")
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
    links = nav.locator('a')
    expected = ["Potatoes", "Milk"] if two_foods else ["Potatoes"]
    assert nav.is_visible()
    assert links.all_inner_texts() == expected
    assert page.locator('[id^="destination-leaf-"]').count() == len(expected)
    assert page.locator('.allocation-matrix').count() == 0
    assert page.locator('.destination-step .step-card').count() == (2 if two_foods else 1)
    for link in links.all():
        assert page.locator(link.get_attribute('href')).count() == 1

    target = links.last.get_attribute('href')
    if two_foods:
        toggle = page.locator(target + ' .step-card__toggle[aria-expanded]')
        if toggle.get_attribute('aria-expanded') == 'true':
            toggle.click()
        assert toggle.get_attribute('aria-expanded') == 'false'
    links.last.click()
    page.wait_for_function(
        "expected => document.querySelector('.destination-floating-nav a[aria-current="
        "\"location\"]').textContent === expected",
        arg=expected[-1],
    )
    assert page.locator(target).is_visible()
    if two_foods:
        assert page.locator(target).locator(
            '.step-card__toggle[aria-expanded]'
        ).get_attribute('aria-expanded') == 'true'
    assert page.evaluate(
        "document.documentElement.scrollWidth === document.documentElement.clientWidth"
    )
