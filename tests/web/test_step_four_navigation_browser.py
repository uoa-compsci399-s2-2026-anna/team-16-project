"""Issue #135: Step 4 indexes every destination in the real browser."""

from __future__ import annotations

import os

import pytest


pytestmark = pytest.mark.browser
pytest.importorskip("playwright.sync_api")

BASE = os.environ.get("KAICALC_WEB_URL", "http://localhost:18080").rstrip("/")
DESTINATIONS = [
    "Food redistribution",
    "Upcycling to other food products",
    "Animal feed",
    "Composting (aerobic digestion)",
    "Anaerobic digestion",
    "Land application",
    "Not harvested or ploughed in",
    "Processing into non-food items",
    "Other recovery, including biodiesel",
    "Combustion",
    "Landfill",
    "Refuse or discard",
    "Sewer or wastewater",
]


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
        page.locator('input[name="food-item"][value="potatoes"]').click()
        if two_foods:
            page.locator('input[name="food-item"][value="milk"]').click()
        page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector('#amount-title')
    if two_foods:
        for card in page.locator('.leaf-panel').all():
            card.locator('.step-card__toggle').click()
            card.locator('input[data-leaf-field="amount"]').fill('1')
    else:
        page.locator('input[data-leaf-field="amount"]').fill('1')
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector('#destination-title')


@pytest.mark.parametrize("two_foods", [False, True])
def test_step_four_navigation_lists_and_reaches_all_destinations(page, two_foods):
    _reach_destinations(page, two_foods)
    nav = page.locator('.destination-floating-nav')
    links = nav.locator('a')
    assert nav.is_visible()
    assert links.all_inner_texts() == DESTINATIONS
    assert page.locator('[id^="destination-section-"]').count() == len(DESTINATIONS)
    assert page.locator('.allocation-matrix').count() == int(two_foods)
    for link in links.all():
        assert page.locator(link.get_attribute('href')).count() == 1

    links.last.click()
    page.wait_for_function(
        "() => document.querySelector('.destination-floating-nav a[aria-current="
        "\"location\"]').textContent === 'Sewer or wastewater'"
    )
    assert page.locator('#destination-section-sewer').is_visible()
    assert page.evaluate(
        "document.documentElement.scrollWidth === document.documentElement.clientWidth"
    )
