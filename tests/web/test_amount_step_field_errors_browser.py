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

**One test below is the exception, and its own docstring says why.**
``test_a_detail_with_no_box_on_this_screen_keeps_its_banner`` fulfils the route,
because the response it needs — one detail bound to a field on screen and one naming
an ``alternative[…]`` path — is §9-legal and emittable by the real API, while no
control on any screen can make the form *send* a request that produces it. What is
measured there is this module's rendering rule, not the server's refusal, so the
response is the fixture and the renderer is the subject.

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

import json

import pytest

from tests.web.base_url import CALCULATOR
from tests.web.steps import press_continue


#: **This file reaches Playwright and must say so.** It is one of the sixteen
#: `tests/web/` files issue #149 found driving Chromium with no marker, so
#: `-m "not browser"` selected it and ran it against the live stack. The import
#: guard below skips when Playwright is absent; it does nothing about a marker
#: filter, which is what the marker is for.
# The `browser` marker is applied by `conftest.py`, by location: every module
# here is a browser suite unless it is named in its `NOT_A_BROWSER_SUITE`.

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to drive the real 400 this file asserts against",
)

BASE = CALCULATOR


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
    press_continue(page)
    page.wait_for_selector('input[name="food-category"]')
    press_continue(page)
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
    press_continue(page)
    page.wait_for_selector('[data-line-field="amount"]')

    page.fill('[data-line-field="amount"] >> nth=0', "1000")
    page.wait_for_timeout(120)
    press_continue(page)
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

    # **The banner is gone, and asserted gone rather than tolerated.** The one field the
    # API named is the one field highlighted, so `validationMessage` returns exactly
    # `Check the highlighted fields and try again.` - which is the summary's own title.
    # This assertion used to read `if banner is not None: assert "highlighted" in ...`,
    # which passed while the banner carried that sentence and passed again, vacuously,
    # once nothing rendered it: tolerant before, never executed after. The rule the
    # summary actually implements is *suppressed only where it would have been the short
    # form*, and that is a statement about absence, so it is asserted as one.
    assert page.query_selector(".field-error.api-error") is None, (
        "the banner repeats the summary's own title beside a summary that already says it: "
        + page.inner_text(".field-error.api-error")
    )

    summary = page.locator(".amount-validation-summary")
    assert summary.count() == 1, "the field error has no summary beside the Step 3 title"
    assert summary.get_attribute("role") == "alert"
    link = summary.locator(".amount-validation-summary__link")
    assert link.count() == 1, summary.inner_text()
    # **Equality, not containment.** `in` cannot tell `Value of production` from
    # `Dairy - Value of production`, so `food: single ? '' : leafName(leaf)` -> `food:
    # leafName(leaf)` survived it: the single-leaf item rendered "Dairy - Value of
    # production" and the substring assertion still held. One food type means the panel
    # names the field and nothing else, because there is no other card to distinguish it
    # from.
    assert link.inner_text().strip() == "Value of production", link.inner_text()
    # **No fragment, anywhere.** `fieldId` is `total-value--<food slug>` the moment a
    # second food type is chosen, and an `<a href="#...">` writes that into the address
    # bar - which §7.2b's privacy note refuses for `?step=` and refuses harder for a food
    # the visitor named. The item is a `<button>`, so there is no `href` to leak and no
    # history entry `history.js`'s `traverse` cannot read.
    assert link.get_attribute("href") is None, "the item is a link and puts an id in the URL"
    before = page.url
    link.click()
    page.wait_for_timeout(150)
    assert page.url == before, f"clicking the item changed the URL: {before!r} -> {page.url!r}"
    assert page.evaluate("() => document.activeElement.id") == "total-value", (
        "the item did not put the caret in the field it names"
    )


def test_a_detail_with_no_box_on_this_screen_keeps_its_banner(page):
    """**One bound detail and one unbound detail, and the banner must survive.**

    `validationMessage` has two branches. Where every detail is bound to a field on
    screen it returns `Check the highlighted fields and try again.`, and suppressing
    *that* beside a summary saying the same sentence is de-duplication. Where **any**
    detail is unbound it returns `The calculation could not be completed.` plus a
    `describeDetail` for each - and its own docstring says why: a detail naming a saved
    entry, an `alternative[...]` path or a field this form has no input for *"has no box
    to attach to and would otherwise vanish entirely"*.

    `errorStep` is the first detail with a locatable step, so a 400 whose first detail is
    `entries[0].total_value_nzd` and whose second is `entries[0].alternative[0].qty_kg`
    lands the visitor on step 3, puts one item in the summary, and - with the banner
    suppressed on the presence of a summary rather than on what the banner would have
    said - drops the second detail from the page altogether. Measured by rendering both
    sides with that state: the unbound sentence was present on `main` and absent with the
    unconditional suppression.

    **Why this one test intercepts the route when the rest of the file refuses to.**
    The shape is §9-legal and the real API can emit it, but the *form* cannot be driven
    into producing it: there is no control on any screen that sends an `alternative[]`
    line with the main submission. The subject under test is not what the server refuses,
    it is what this module renders when handed two details of different kinds, so the
    response is the fixture and the renderer is the thing measured.
    """
    envelope = {
        "error": {
            "code": "VALIDATION_ERROR",
            "message": "Request validation failed",
            "details": [
                {
                    "field": "entries[0].total_value_nzd",
                    "issue": "decimal_places",
                    "message": "Enter at most two decimal places.",
                },
                {
                    "field": "entries[0].alternative[0].qty_kg",
                    "issue": "greater_than_equal",
                    "message": "Destination amounts must be zero or greater.",
                },
            ],
        }
    }
    page.route(
        "**/api/v1/calculate*",
        lambda route: route.fulfill(
            status=400,
            content_type="application/json",
            body=json.dumps(envelope),
        ),
    )
    page = to_review_and_calculate(page)

    assert page.query_selector("#amount-title") is not None, "the 400 did not land on step 3"
    summary = page.locator(".amount-validation-summary")
    assert summary.count() == 1, "the bound detail has no item in the summary"
    assert summary.locator(".amount-validation-summary__link").count() == 1, summary.inner_text()

    banner = page.query_selector(".field-error.api-error")
    assert banner is not None, (
        "the banner was suppressed on the presence of a summary, so the detail with no "
        "box on this screen is now on no surface at all"
    )
    said = banner.inner_text()
    assert "could not be completed" in said, said
    assert "zero or greater" in said, said
    # And the short form is NOT what is printed: if it were, the long branch never ran
    # and this test would be asserting the banner's existence for the wrong reason.
    assert "highlighted" not in said.lower(), said
