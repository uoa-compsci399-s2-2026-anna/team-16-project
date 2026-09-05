"""A rejected amount-step field must land the visitor back on the amount step.

**The defect.** Stage two added three optional numeric fields to the amount step —
`total_input_kg`, `total_value_nzd`, `wasted_value_nzd` — and `calculator.js` always
sent a rejected `VALIDATION_ERROR` to step 3 (the destination step) and rendered
`state.fieldErrors` only against destination rows. A field the API named on step 2 had
no box to highlight there and no way to be seen on the screen the visitor was thrown to
instead: a generic banner, and nothing pointing at what was actually wrong.

**Reachable without a server that misbehaves.** Stage two's keystroke guard on these
three fields refuses a third money decimal as it is typed, but the guard only inspects
`event.data.length === 1` (by its own documented design — that is what keeps it a
keystroke guard and not a bulk-entry one), so a paste walks straight past it. The API
still enforces `decimal_places=2` on `total_value_nzd` (`api/schemas.py`), so the value
that gets past the browser is rejected by the real backend with a genuine 400.

**Driven against the real API, not a stub.** No route interception of
``POST /api/v1/calculate`` — see `test_improvement_submission.py`'s fixture for the
same reasoning applied to a different defect: a test that answers its own POST proves
only that the front end can parse a response it wrote itself.

**The paste itself.** `field.press_sequentially` types one keystroke at a time and
*is* the keystroke guard's own path — it would prove nothing about a paste. Real
clipboard access is unavailable to a sandboxed headless Chromium without OS-level
permissions, so `paste_into` below does not send `Control+V`. It reproduces the one
thing the guard actually cares about: the guard lives on `beforeinput` and only ever
inspects a single-character `event.data`, and a genuine paste never satisfies that
either, because it hands the whole string to `beforeinput` in one event. Setting
`.value` through the native property setter and firing the `input` event
`calculator.js` listens on lands the field in the same end state a real paste would,
without needing a clipboard the test runner may not have.

**Running this**::

    pip install playwright && playwright install chromium
    docker compose -f docker/compose.yaml up -d --build web
    pytest tests/web/test_amount_step_field_errors_browser.py

Restart ``kaicalc-api`` between this file and any other browser test file — see
``test_amount_limits_browser.py``'s header for why a sweep exhausts the taxonomy rate
limit.
"""

from __future__ import annotations

import os

import pytest

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to drive the real 400 this file asserts against",
)

BASE = os.environ.get("KAICALC_WEB_URL", "http://localhost:18080").rstrip("/") + "/index.html"


@pytest.fixture
def page(browser):
    """A fresh context and a fresh session token, and no route interception at all —
    the whole point is what the real API says about the real value pasted in."""
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


def paste_into(page, selector, text):
    """Land ``text`` in a field the way a paste does, past the keystroke guard.

    See the module docstring for why this, and not ``Control+V``, is the equivalent
    of a real paste as far as `calculator.js`'s guard is concerned.
    """
    page.evaluate(
        """([selector, value]) => {
             const el = document.querySelector(selector);
             const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
             setter.call(el, value);
             el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertFromPaste', data: value }));
           }""",
        [selector, text],
    )


def to_review_and_calculate(page):
    """Walk the wizard to step 5 with a pasted, over-precise `#total-value`, and press
    Calculate — a real request the real API is expected to refuse.

    The destination row is filled to the total so step 4 does not refuse first; the
    defect under test is what happens to a *step-2* rejection, and a step-4 refusal
    reaching the same banner first would prove nothing about it.
    """
    page.click('[data-action="start"]')
    page.wait_for_selector('input[name="sector"]')
    page.evaluate("document.querySelector('input[name=sector]').click()")
    page.wait_for_timeout(80)
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector('input[name="food-category"]')
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector("#total-waste")

    page.fill("#total-waste", "1000")
    # Typed, not pasted: three decimal places on a field with no ceiling of its own.
    # Only `#total-value` is driven past the guard — this is the field under test.
    #
    # **At least the waste amount, not less.** A production total smaller than
    # `#total-waste` trips the item-①-round-two guard in `validateCurrentStep`
    # (waste cannot exceed production) and refuses to advance past step 2 for a
    # different reason than the one this test is driving at.
    page.fill("#total-input", "1500")
    paste_into(page, "#total-value", "1.234")
    page.wait_for_timeout(80)
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector('[data-line-field="amount"]')

    page.fill('[data-line-field="amount"] >> nth=0', "1000")
    page.wait_for_timeout(120)
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector('[data-action="calculate"]')

    page.click('.step-nav [data-action="calculate"]')
    page.wait_for_timeout(600)
    return page


def test_a_pasted_over_precise_money_value_lands_back_on_the_amount_step(page):
    """The visitor is sent to step 2, not step 3, and the field itself is marked.

    Both halves are asserted, deliberately: a fix that only routes to the right step
    but still shows nothing against `#total-value`, or one that marks the field but
    still leaves the visitor on step 4, is only half of this defect closed.
    """
    page = to_review_and_calculate(page)

    assert page.query_selector("#amount-title") is not None, (
        "the rejection did not land on the amount step"
    )
    assert page.query_selector("#destination-title") is None, (
        "the rejection landed on the destination step instead — the pre-fix behaviour"
    )

    error_text = page.eval_on_selector(
        "#total-value-error", "el => el.textContent.trim()"
    )
    assert error_text, "no per-field error was rendered against #total-value"

    field_state = page.evaluate(
        """() => {
             const field = document.querySelector('#total-value');
             return {
               ariaInvalid: field.getAttribute('aria-invalid'),
               describedBy: field.getAttribute('aria-describedby'),
               role: document.querySelector('#total-value-error').getAttribute('role'),
             };
           }"""
    )
    assert field_state["ariaInvalid"] == "true", field_state
    assert field_state["describedBy"] == "total-value-error", field_state
    assert field_state["role"] == "alert", field_state

    # The one field the API named is the one field highlighted, so the generic banner
    # collapses to the short "check the highlighted fields" form rather than repeating
    # the same detail a second time in prose next to nothing.
    banner = page.query_selector(".field-error.api-error")
    if banner is not None:
        assert "highlighted" in banner.inner_text().lower(), banner.inner_text()
