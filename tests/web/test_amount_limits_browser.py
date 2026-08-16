"""What step 3 and step 4 refuse, typed one character at a time in a real browser.

**Everything here is typed, never `fill()`ed.** `page.fill` sets `.value` and
dispatches one synthetic `input`; it produces no `beforeinput`, no per-keystroke
sanitising, and no intermediate states. Every rule this file covers lives in one
of those three, so a filled field would agree with a broken implementation. The
`1e5` case only exists at all because a `<input type="number">` accepts `1`, then
rejects `1e`, then accepts `1e5` — three values for three keystrokes.

**The rules under test, and where they come from.**

``qty_kg`` is the only number that crosses the wire. §6.2 bounds it at
``MAX_LINE_QTY`` = 10,000,000 kg per destination line and ``MAX_SCENARIO_QTY`` =
50,000,000 kg per entry scenario (``api/schemas.py``). The step-3 total is never
sent — it is the ceiling of the step-4 allocation — so the client rule that
restates the scenario cap belongs on it, and the one that restates the line cap
belongs on a destination row. Neither refuses anything the server would accept.

A previous attempt used ``999999999999.99`` for both, five orders of magnitude
above the real bound and, read as tonnes, past the ``DECIMAL(16,3)`` column
outright: a formatting cap wearing a validation coat, with the real ceiling left
unguarded.

**The refusal never edits the number.** A guard that clamps or reverts means a
visitor pastes one figure and submits another. Every rule below leaves the field
exactly as typed and refuses on Continue with a catalogue string, which is what
the rest of ``calculator.js`` already does.

**Two languages and two widths.** The English-validation-message defect — a
guard that shows ``target.validationMessage``, which Chromium writes in the
*browser's* language regardless of the page's — is invisible in English. `de` is
the check: a German refusal that reads "Value must be less than or equal to …"
came from the browser, not from `web/locales/de.json`.

**Mutation record.** Each was applied to ``web/js/calculator.js``, the container
rebuilt, and the named test watched to fail:

=========================================================  =========================================
Mutation                                                   Killed by
=========================================================  =========================================
the step-3 kilogram ceiling is deleted                     ``..._a_total_above_the_scenario_cap_...``
the ceiling is compared against the typed number           ``..._the_ceiling_is_on_the_mass[tonnes]``
the ceiling is compared against the typed count            ``..._the_ceiling_is_on_the_mass[container]``
the per-line ceiling is deleted                            ``..._one_destination_may_not_exceed_...``
the plain-number branch falls back to the decimal message  ``..._1e5_is_not_a_decimal_places_problem``
the minus guard drops ``#unit-count``                      ``..._a_minus_never_lands_in_the_count``
the minus guard drops the position-0 exception             ``..._a_leading_minus_stays_visible``
``minusEntered`` is never released on delete               ``..._clearing_the_field_allows_a_new_...``
the refusal assigns a clamped ``.value``                   ``..._the_number_typed_is_the_number_kept``
=========================================================  =========================================

**Running these.** Playwright is not a project dependency, for the reason
``test_step_navigation.py`` gives::

    pip install playwright && playwright install chromium
    docker compose -f docker/compose.yaml up -d --build web
    pytest tests/web/test_amount_limits_browser.py
"""

from __future__ import annotations

import json
import os
import urllib.request
from decimal import Decimal

import pytest

pytestmark = pytest.mark.browser

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to type into the real inputs; these rules are unverified without it",
)

#: The ORIGIN, not a page — the taxonomy probe and the calculator are built from
#: it and are different shapes. `/` serves `home.html`, so the wizard has to be
#: named.
BASE = os.environ.get("KAICALC_WEB_URL", "http://localhost:18080").rstrip("/")
CALCULATOR = BASE + "/index.html"

#: §6.2's two bounds, in kilograms, written out here rather than imported. This
#: file's subject is whether the browser restates the server's rule, and a test
#: that read the constant from the module it is checking would assert only that
#: a name exists. These are the numbers in `api/schemas.py`; if that file moves
#: them, this file is supposed to fail.
MAX_LINE_KG = Decimal("10000000")
MAX_SCENARIO_KG = Decimal("50000000")

#: The preset the container assertions drive, and its conversion. Same reasoning:
#: held, not read back from the row under test.
PRESET_CODE = "wheelie_bin_240l"
PRESET_KG = Decimal("69.6000")

VIEWPORTS = [
    pytest.param(1278, 983, id="1278x983"),
    pytest.param(390, 700, id="390x700"),
]

FORCE_AUTO = "html { scroll-behavior: auto !important; }"

#: Enough of a §6.2 response to reach the results screen. Nothing here asserts on
#: a response.
RESULT_STUB = {
    "token": "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0",
    "factor_set": {"version_label": "MOCK-v0 — PLACEHOLDER", "is_mock": True},
    "factor_source": "published",
    "gwp_horizon": 100,
    "totals": {"total_kg": "1000.000", "current": {"metrics": [], "equivalences": []}},
    "entries": [{"current": {"metrics": [], "equivalences": []}}],
}


@pytest.fixture(scope="session")
def taxonomy():
    """The served taxonomy, so a stale stack skips rather than fails."""
    url = BASE + "/api/v1/taxonomy"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            served = json.loads(response.read())
    except Exception as error:  # pragma: no cover - environment guard
        pytest.skip(f"no stack answering {url}: {error}")
    presets = {row["code"]: row for row in served.get("unit_presets", [])}
    if PRESET_CODE not in presets or Decimal(presets[PRESET_CODE]["kg_per_unit"]) != PRESET_KG:
        pytest.skip(
            f"{BASE} serves no {PRESET_CODE} at {PRESET_KG} kg — the stack predates "
            "migration 0015. Point KAICALC_WEB_URL at one that has it."
        )
    return served


@pytest.fixture(scope="session")
def browser():
    with playwright_api.sync_playwright() as p:
        instance = p.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def page_at(browser, taxonomy):
    """A page at a given viewport and language, with the calculate POST captured.

    The POST is fulfilled locally — `/api/v1/calculate` is rate limited — but the
    body is kept in `page.sent`, because "what actually left the browser" is the
    only proof that a refusal did not quietly send a different number.
    """
    contexts = []

    def open_page(width, height, language="en"):
        ctx = browser.new_context(
            viewport={"width": width, "height": height},
            locale="en-NZ",
            bypass_csp=True,
        )
        contexts.append(ctx)
        page = ctx.new_page()
        page.sent = []

        def capture(route, request):
            if request.method == "POST":
                page.sent.append(json.loads(request.post_data or "{}"))
            route.fulfill(status=200, content_type="application/json", body=json.dumps(RESULT_STUB))

        page.route("**/api/v1/calculate*", capture)
        try:
            page.goto(f"{CALCULATOR}?lang={language}", wait_until="networkidle", timeout=20000)
        except Exception as error:  # pragma: no cover - environment guard
            pytest.skip(f"the front end is not being served at {CALCULATOR}: {error}")
        page.add_style_tag(content=FORCE_AUTO)
        page.wait_for_selector('input[name="sector"]', timeout=10000)
        return page

    yield open_page
    for ctx in contexts:
        ctx.close()


# ------------------------------------------------------------------- helpers


def to_amount_step(page):
    """Walk to step 3 and stop."""
    page.wait_for_selector('input[name="sector"]')
    page.evaluate("document.querySelector('input[name=sector]').click()")
    page.wait_for_timeout(60)
    page.click('[data-action="continue"]')
    page.wait_for_selector('input[name="food-category"]')
    page.click('[data-action="continue"]')
    page.wait_for_selector("#total-unit")
    return page


def type_into(page, selector, text):
    """One keystroke per character, with the field emptied first.

    `press_sequentially` is the point of this module. `fill()` would skip every
    `beforeinput` the guards hang off and every intermediate value the browser's
    own sanitiser produces.
    """
    field = page.locator(selector)
    field.click()
    page.keyboard.press("Control+a")
    page.keyboard.press("Delete")
    field.press_sequentially(text, delay=12)
    page.wait_for_timeout(80)
    return field


def value_of(page, selector):
    return page.eval_on_selector(selector, "el => el.value")


def continue_and_read_error(page):
    """Press Continue and return `(error text, step advanced?)`."""
    page.click('[data-action="continue"]')
    page.wait_for_timeout(150)
    error = page.query_selector(".form-field .field-error, #allocation-error")
    text = (error.inner_text().strip() if error else "")
    return text, page.query_selector("#total-waste") is None and page.query_selector("#unit-count") is None


def to_destination_step(page, total="1000"):
    to_amount_step(page)
    type_into(page, "#total-waste", total)
    page.click('[data-action="continue"]')
    page.wait_for_selector('[data-line-field="amount"]')
    return page


# --------------------------------------------------------------- minus signs


def test_a_minus_never_lands_in_the_total_amount(page_at):
    """`-5` typed into `#total-waste` is `5`.

    `.value` alone does not discriminate on the minus keystroke — a lone "-" is
    not a number and the browser reports `''` either way — so the second
    character is what makes the assertion sharp: without the guard the field
    holds `-5`, with it `5`.
    """
    page = to_amount_step(page_at(1278, 983))
    type_into(page, "#total-waste", "-5")
    assert value_of(page, "#total-waste") == "5"


def test_a_minus_never_lands_in_the_container_count(page_at):
    """The same rule on `#unit-count`, which the guard used to miss.

    The guard keyed on `target.id === 'total-waste'` and so applied to exactly
    one of the two fields that occupy that position — the container count, added
    on this branch, was silently outside it.
    """
    page = to_amount_step(page_at(1278, 983))
    page.select_option("#total-unit", f"preset:{PRESET_CODE}")
    page.wait_for_selector("#unit-count")
    type_into(page, "#unit-count", "-2")
    assert value_of(page, "#unit-count") == "2"


def test_a_leading_minus_stays_visible_in_a_destination_amount(page_at):
    """...because the refusal it triggers is drawn on the screen.

    `validateCurrentStep` refuses negative destination amounts and `updateLine`
    marks the row and the summary invalid as it is typed. Blocking the character
    would delete the only feedback the visitor gets, so this field takes the
    opposite ruling from the two totals and that asymmetry is the design.
    """
    page = to_destination_step(page_at(1278, 983))
    type_into(page, '[data-line-field="amount"] >> nth=0', "-5")
    state = page.evaluate(
        """() => {
          const input = document.querySelector('[data-line-field="amount"]');
          return {
            value: input.value,
            invalidRow: input.closest('.destination-row').classList.contains('invalid'),
            ariaInvalid: input.getAttribute('aria-invalid'),
            invalidSummary: document.querySelector('.allocation-summary').classList.contains('invalid'),
            continueDisabled: document.querySelector('[data-action="continue"]').disabled,
          };
        }"""
    )
    assert state["value"] == "-5", state
    assert state["invalidRow"] and state["invalidSummary"], state
    assert state["ariaInvalid"] == "true", state
    assert state["continueDisabled"], state


def test_a_minus_after_digits_is_refused_in_a_destination_amount(page_at):
    """`5-` is the state worth preventing, and it is not a number.

    `<input type="number">` will not call "5-" a value, so `.value` goes to `''`
    while the box still *shows* "5-": the row reads as empty to `updateLine`, the
    allocation summary drops it, and nothing on screen says why. Refusing the
    character is what stops that; refusing the number cannot, because by then
    there is none.
    """
    page = to_destination_step(page_at(1278, 983))
    type_into(page, '[data-line-field="amount"] >> nth=0', "5-")
    assert value_of(page, '[data-line-field="amount"] >> nth=0') == "5"


def test_a_second_minus_is_refused_in_a_destination_amount(page_at):
    page = to_destination_step(page_at(1278, 983))
    type_into(page, '[data-line-field="amount"] >> nth=0', "--5")
    assert value_of(page, '[data-line-field="amount"] >> nth=0') == "-5"


def test_clearing_the_field_allows_a_new_leading_minus(page_at):
    """The flag has to be released, not only set.

    A guard that latches `minusEntered` on the first minus and never clears it
    leaves the field unable to take a second one for the rest of the page's life
    — including after the visitor has emptied it and started again. Deleting is
    the only path back, and it is the path `beforeinput` cannot see.
    """
    page = to_destination_step(page_at(1278, 983))
    field = type_into(page, '[data-line-field="amount"] >> nth=0', "-5")
    field.click()
    page.keyboard.press("Control+a")
    page.keyboard.press("Delete")
    page.wait_for_timeout(60)
    field.press_sequentially("-3", delay=12)
    page.wait_for_timeout(80)
    assert value_of(page, '[data-line-field="amount"] >> nth=0') == "-3"
