"""Where the money saved by an improved scenario is shown.

**This file exists because the row was dead in the product and two tests said
otherwise.** §4.5's `saving_nzd` is `None` "when no entry carries an
alternative", and the Calculate button sends `alternative: null` for every
entry — `submission.js`'s `submissionPayload` defaults `alternativeFor` to
`() => null`, which is exactly what the main flow wants. So
`state.result.totals.money.saving_nzd` is `null` on every calculation a visitor
can run. The response that *does* carry a saving is Compare Impact's, and it
lands on `state.improvementResult`.

The money block read `state.result` only, so "Value of food not wasted at all",
its caveat sentence and their twenty catalogue entries could not be reached by
anybody. The two tests covering them fulfilled the calculate POST from a
hand-built body carrying both `alternative: null` at the request edge and a
populated `saving_nzd` at the response edge — a shape the engine cannot
produce. **A test built from a hand-written response is what hid this**, so
nothing here fulfils a route: both calls reach the real API, and every figure
asserted on is read back out of the response the engine actually returned.

**Running these**::

    pip install playwright && playwright install chromium
    docker compose -f docker/compose.yaml up -d
    pytest tests/web/test_improvement_saving_browser.py

Every prerequisite skips rather than fails: an unreachable stack means this is
unverified, not broken.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import pytest

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to drive Compare Impact against the real API",
)

BASE = os.environ.get("KAICALC_WEB_URL", "http://localhost:18080/index.html")

#: One entry, one tonne of landfill, priced. The rate §4.5 derives is
#: `wasted_value_nzd / entry current mass` = 4500 / 1000 = $4.50 per kilogram,
#: and half the mass is moved to `prevention` below, so `diverted_i` is 500 kg
#: and the engine's saving is $2,250.00. **The test does not assert that
#: number**: it reads `saving_nzd` back out of the response and compares the
#: page against it, so a wrong engine figure fails `test_golden.py` rather than
#: silently teaching this file the wrong answer.
WASTE_KG = "1000"
TOTAL_VALUE = "120000"
WASTED_VALUE = "4500"

#: The share of the entry's mass the improved scenario prevents. §4.5 excludes
#: prevention mass from `diverted_i` on *both* sides of the subtraction, so a
#: landfill-to-composting diversion correctly reports `0.00` and only a move to
#: a prevention destination produces a figure a rendered zero can be told from.
PREVENTED_SHARE = "50"

#: The origin the taxonomy is read from, so the two destination codes below are
#: **derived from the published factor set rather than named here**. The stack
#: currently publishes the ReFED benchmark set, whose destinations are
#: `refed_landfill` / `refed_prevention`; the NZ set spells them `landfill` /
#: `prevention`. A test naming either pair passes on one deployment and times
#: out on the other looking exactly like a layout failure.
TAXONOMY = os.environ.get(
    "KAICALC_API_URL", "http://localhost:18080"
).rstrip("/") + "/api/v1/taxonomy"


@pytest.fixture(scope="module")
def destinations():
    """`(non-prevention destination, prevention destination)` from the published set.

    `is_prevention` is a field of the taxonomy response (§6.1), which is the
    same question `FactorBundle.is_prevention_destination()` answers engine-side
    — and asking it beats comparing against the literal `"prevention"`, for
    v1.22's reason: the ReFED vocabulary spells its own prevention row
    `refed_prevention`, and a literal missed it once already.
    """
    try:
        with urllib.request.urlopen(TAXONOMY, timeout=15) as answer:
            payload = json.load(answer)
    except (urllib.error.URLError, TimeoutError) as error:  # pragma: no cover - guard
        pytest.skip(f"the taxonomy is not being served at {TAXONOMY}: {error}")
    rows = payload.get("destinations") or []
    waste = next((row for row in rows if not row["is_prevention"]), None)
    prevention = next((row for row in rows if row["is_prevention"]), None)
    assert waste and prevention, f"the published set has no such pair: {rows}"
    return waste, prevention


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        instance = p.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    """A fresh context, so every run mints its own session token.

    No route interception at all - see the module docstring. The two responses
    are read with `expect_response` around the click that causes each, which is
    the sync API's own way of holding one: an `on("response")` listener has to
    call `response.json()` from inside the event loop's own callback, and the
    first run of this file caught neither of the two.
    """
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


def calculate(page, waste, *, wasted_value: str | None) -> dict:
    """Drive the whole wizard to a real result.

    `wasted_value` of `None` is the visitor who priced nothing, which §4.5 turns
    into a `saving_nzd` of `None` however good the improvement is — "an entry
    that supplied no value contributes nothing rather than borrowing a
    neighbour's rate".
    """
    page.click('[data-action="start"]')
    page.wait_for_selector('input[name="sector"]')
    page.evaluate("document.querySelector('input[name=sector]').click()")
    page.wait_for_timeout(80)
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector('input[name="food-category"]')
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector("#total-waste")

    page.fill("#total-waste", WASTE_KG)
    page.fill("#total-value", TOTAL_VALUE)
    if wasted_value is not None:
        page.fill("#wasted-value", wasted_value)
    page.click('.step-nav [data-action="continue"]')

    #: All of it to one non-prevention destination. Step 4 renders one fixed row per
    #: destination and the row carries the destination's own name in its
    #: `aria-label` (§7.3), so the row is found by name rather than by position.
    #: A published set's names are staff-typed and are never translated (§7.7.7),
    #: which is why matching on one is safe here.
    page.wait_for_selector('[data-line-field="amount"]')
    page.fill(f'input[aria-label^="{waste["name"]} amount"]', WASTE_KG)
    page.wait_for_timeout(120)
    page.click('.step-nav [data-action="continue"]')
    page.wait_for_selector('[data-action="calculate"]')
    with page.expect_response(_is_calculation, timeout=20000) as answer:
        page.click('.step-nav [data-action="calculate"]')
    page.wait_for_selector(".results-page", timeout=20000)
    return answer.value.json()


def compare(page, waste, prevention) -> dict:
    """Open the improvement panel, move half the mass to prevention, compare."""
    page.click('[data-action="explore-improvements"]')
    page.wait_for_selector('[data-action="compare-improvement"]')
    allocation = {waste["code"]: PREVENTED_SHARE, prevention["code"]: PREVENTED_SHARE}
    for code, percentage in allocation.items():
        field = page.locator(f'.percentage-input [data-improvement-code="{code}"]')
        assert field.count() == 1, f"no improvement control for {code}"
        field.fill(percentage)
        page.wait_for_timeout(80)
    button = page.locator('[data-action="compare-improvement"]')
    inline = page.locator("#improvement-inline-error")
    assert button.is_enabled(), (
        "Compare Impact is disabled on an allocation totalling 100%: "
        f"{inline.inner_text() if inline.count() else ''}"
    )
    with page.expect_response(_is_calculation, timeout=20000) as answer:
        button.click()
    page.wait_for_selector("#comparison-results", timeout=20000)
    return answer.value.json()


def _is_calculation(response) -> bool:
    return "/api/v1/calculate" in response.url and response.status == 200


def _money(response: dict) -> dict:
    return (response.get("totals") or {}).get("money") or {}


def test_calculate_alone_cannot_produce_a_saving(page, destinations):
    """**The measurement the whole fix rests on**, asserted rather than assumed.

    §4.5: `saving_nzd` is `None` when no entry carries an alternative, and the
    Calculate button never sends one. So a money block that reads only
    `state.result` can never show the saving to anybody, whatever the visitor
    types.
    """
    waste, _ = destinations
    answer = calculate(page, waste, wasted_value=WASTED_VALUE)

    money = _money(answer)
    assert money.get("wasted_value_nzd") is not None, (
        "the priced entry did not reach the engine, so this proves nothing"
    )
    assert money.get("saving_nzd") is None, (
        "the main Calculate response now carries a saving; the row's home may "
        f"need revisiting: {json.dumps(money)}"
    )

    summary = page.locator(".money-summary")
    assert summary.count() == 1, "the money block is missing from a priced calculation"
    text = summary.inner_text().lower()
    assert "not wasted at all" not in text, (
        "the main money block advertises a saving on a response that has none"
    )


def test_the_saving_is_shown_beside_the_comparison_that_produced_it(page, destinations):
    """**Finding 1.** The saving belongs to the alternative scenario, so it is
    rendered inside the comparison output — beside the figures it describes.

    Every number here is read back out of the second `/api/v1/calculate`
    response. The engine computed it from what this journey typed; nothing in
    this file re-derives it, and nothing fulfilled the route.
    """
    waste, prevention = destinations
    calculate(page, waste, wasted_value=WASTED_VALUE)
    comparison = compare(page, waste, prevention)

    saving = _money(comparison).get("saving_nzd")
    assert saving is not None, (
        "Compare Impact returned no saving, so the improved scenario diverted "
        f"nothing: {json.dumps(_money(comparison))}"
    )
    assert float(saving) > 0, f"the improvement produced no saving to show: {saving}"

    block = page.locator("#comparison-results .comparison-saving")
    assert block.count() == 1, (
        "the saving is not rendered beside the comparison that produced it"
    )
    shown = block.inner_text()
    #: The response's own figure, formatted for display - en-NZ groups thousands
    #: with a comma and money carries two places (§4.5).
    assert f"{float(saving):,.2f}" in shown, (
        f"the comparison shows {shown!r}, the response says {saving}"
    )
    assert "NZ$" in shown, "the currency is not named"
    assert "not wasted at all" in shown.lower(), (
        "the saving is not named for what it measures - only mass that stopped "
        "being wasted contributes to it (§4.5)"
    )
    assert "assum" in shown.lower(), (
        "the caveat did not travel with the figure; the rate is nominal, not "
        "a measured price (§4.5)"
    )


def test_the_main_money_block_still_carries_the_three_typed_figures(page, destinations):
    """Moving the saving must not move the rest of §4.5 with it.

    The other three are properties of the *current* scenario alone — what the
    visitor said their food was worth — so they stay where they were, on screen
    before any comparison is run and still there after one.
    """
    waste, prevention = destinations
    calculate(page, waste, wasted_value=WASTED_VALUE)
    money = _money(compare(page, waste, prevention))

    summary = page.locator(".money-summary").inner_text()
    for field in ("total_value_nzd", "wasted_value_nzd"):
        assert f"{float(money[field]):,.2f}" in summary, (
            f"{field} is not in the money block: {summary!r}"
        )
    assert "not wasted at all" not in summary.lower(), (
        "the saving is being printed twice - once in each block"
    )


def test_an_unpriced_entry_shows_no_saving_at_all(page, destinations):
    """**Absent is not zero, on the comparison screen too.**

    §4.5: an entry that supplied no `wasted_value_nzd` contributes nothing to
    the saving rather than borrowing a rate, so `saving_nzd` is `null` on this
    journey even though the improvement is identical to the one above. A `null`
    must render as nothing — not `NZ$0.00`, not a dash, and not the
    `NZ$Not available` that a `hasValue` loosened to `value !== undefined`
    prints.
    """
    waste, prevention = destinations
    calculate(page, waste, wasted_value=None)
    money = _money(compare(page, waste, prevention))

    assert money.get("saving_nzd") is None, (
        f"an entry with no wasted value produced a saving: {json.dumps(money)}"
    )

    assert page.locator("#comparison-results .comparison-saving").count() == 0, (
        "a saving block was rendered for a calculation that has no saving"
    )
    comparison = page.locator("#comparison-results").inner_text().lower()
    assert "not wasted at all" not in comparison
    assert "nz$not available" not in comparison.replace(" ", "")
