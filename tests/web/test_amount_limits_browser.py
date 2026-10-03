"""What step 3 and step 4 refuse, typed one character at a time in a real browser.

**Everything here is typed, never `fill()`ed.** `page.fill` sets `.value` and
dispatches one synthetic `input`; it produces no `beforeinput`, no per-keystroke
sanitising, and no intermediate states. Every rule this file covers lives in one
of those three, so a filled field would agree with a broken implementation. The
`1e5` case only exists at all because a `<input type="number">` accepts `1`, then
rejects `1e`, then accepts `1e5` — three values for three keystrokes.

**The rules under test, and where they come from.**

``qty_kg`` is the only number that crosses the wire. §6.2 bounds it at
``MAX_LINE_QTY`` per destination line and ``MAX_SCENARIO_QTY`` per entry
scenario (``api/schemas.py``), and since v1.46 both are 50,000,000 kg. The
step-3 total is never sent — it is the ceiling of the step-4 allocation — so
the client rule that restates the scenario cap belongs on it, and the one that
restates the line cap belongs on a destination row. Neither refuses anything the
server would accept.

**That last sentence stayed true through a real defect, which is the point.**
The line cap was 10,000,000 kg — a fifth of the scenario cap — so a legal
50,000 t total sent to a single destination was refused at step 4 with a limit
the visitor had not crossed at step 3. The browser was *right*: the API refused
it too. The rule being restated was the wrong rule, and "the client agrees with
the server" cannot detect that. What could have is an assertion that the
permitted case is permitted, and every ceiling test in this file asserted only
a refusal. ``test_one_destination_may_carry_an_entire_legal_scenario`` is the
missing half, and it fails against v1.45 on both sides of the wire.

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

**Mutation record.** Each was applied to ``web/js/calculator.js`` or
``web/js/units.js``, ``docker cp``'d into the running web container, and the
named test watched to fail. All fifteen are killed:

============================================================  ===========================================================
Mutation                                                      Killed by
============================================================  ===========================================================
the step-3 ceiling is deleted                                 ``..._ceiling_is_on_the_mass...`` (both units)
the ceiling compares the typed number, not the mass           ``..._ceiling_is_on_the_mass...[tonnes]``
the non-finite branch is dropped from the ceiling             ``..._largest_number_the_field_will_hold...[infinite]``
``containerLimit`` ignores the preset                         ``..._container_ceiling_is_the_mass_expressed...``
the per-line ceiling is deleted                               ``..._one_destination_may_not_exceed...`` (both units)
the LINE cap is put on the total instead                      ``..._a_scenario_spread_across_destinations_is_not_refused``
``MAX_LINE_KG`` goes back to ``10000000`` (v1.46)             ``..._one_destination_may_carry_an_entire_legal...`` (both units)
the per-line rule is asked AFTER the allocation rule (v1.46)  ``..._one_destination_may_not_exceed...`` (both units)
the plain-number branch is deleted                            ``..._1e5_is_not_a_decimal_places_problem``
the refusal ships an English literal                          ``..._refusal_is_in_the_pages_language_and_not_the_browsers``
``aria-invalid`` is written as an empty string                ``..._refused_field_is_announced_as_invalid``
the guard clamps ``.value`` in place                          ``..._the_number_typed_is_the_number_kept``
``canContinue`` goes back to restating three rules inline     ``..._returning_to_step_four_does_not_re_enable_continue...``
the minus guard drops ``#unit-count``                         ``..._a_minus_never_lands_in_the_container_count``
the minus guard drops the position-0 exception                ``..._a_leading_minus_stays_visible...``
``minusEntered`` is never released on delete                  ``..._clearing_the_field_allows_a_new_leading_minus``
the availability check moves back below the count bound       ``..._a_container_with_no_usable_conversion_says_so``
============================================================  ===========================================================

**One survived first time, and it was the test that was wrong.** Dropping
``kilograms === null ||`` from the step-3 ceiling changed nothing, because
``massToKg`` checked only its *input* for finiteness and then multiplied: a
finite number of tonnes past about 1.8e305 came back as ``Infinity``, which is
neither ``null`` nor under a ``>`` comparison, so the ceiling fired for the
wrong reason and the branch this file claimed to cover had never run. ``units.js``
now checks the product, the branch is reachable, and the mutation dies. That is
the shape this project keeps finding: an assertion that passes, against a line
that is never executed.

**Running these.** Playwright is not a project dependency, for the reason
``test_step_navigation.py`` gives::

    pip install playwright && playwright install chromium
    docker compose -f docker/compose.yaml up -d --build web
    pytest tests/web/test_amount_limits_browser.py
"""

from __future__ import annotations

import json
import urllib.request
from decimal import Decimal

import pytest

from tests.web.base_url import ORIGIN
from tests.web.steps import press_continue

# The `browser` marker is applied by `conftest.py`, by location: every module
# here is a browser suite unless it is named in its `NOT_A_BROWSER_SUITE`.

pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is required to type into the real inputs; these rules are unverified without it",
)

#: The ORIGIN, not a page — the taxonomy probe and the calculator are built from
#: it and are different shapes. `/` serves this same file, and it is named anyway
#: so the constant does not move when the `index` directive does.
BASE = ORIGIN
CALCULATOR = BASE + "/index.html"

#: §6.2's two bounds, in kilograms, written out here rather than imported. This
#: file's subject is whether the browser restates the server's rule, and a test
#: that read the constant from the module it is checking would assert only that
#: a name exists. These are the numbers in `api/schemas.py`; if that file moves
#: them, this file is supposed to fail.
#:
#: **They are equal as of v1.46, and they are still two names.** The per-line cap
#: was 10,000,000 kg — a fifth of the scenario cap — which meant a step-3 total
#: at its own ceiling could only be allocated across five or more destinations.
#: "All of it goes to animal feed" was refused at step 4 with a message about a
#: limit the visitor had not exceeded at step 3. Collapsing the two names here
#: would hide the next divergence rather than catch it.
MAX_LINE_KG = Decimal("50000000")
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


#: `browser` itself now comes from `tests/web/conftest.py`, package-scoped
#: and shared across every file in this directory - see that module's
#: docstring for why a per-file `session`-scoped fixture corrupted the rest
#: of the run.
@pytest.fixture
def page_at(browser, taxonomy):
    """A page at a given viewport and language, with the calculate POST captured.

    The POST is fulfilled locally — `/api/v1/calculate` is rate limited — but the
    body is kept in `page.sent`, because "what actually left the browser" is the
    only proof that a refusal did not quietly send a different number.
    """
    contexts = []

    def open_page(width, height, language="en", extra_preset=None):
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
        if extra_preset is not None:
            # **Every seeded preset is too light to reach the kilogram ceiling.**
            # The heaviest is 319 kg, so 50,000,000 kg is 156,739 of them and the
            # 10,000-container plausibility bound is always the smaller of the
            # two — which means the real taxonomy cannot tell a guard that
            # converts from one that does not. `kg_per_unit` is staff-editable,
            # so a heavy container is a state the deployed system can reach; this
            # serves one, and nothing else about the taxonomy is touched.
            served = dict(taxonomy)
            served["unit_presets"] = [*taxonomy["unit_presets"], extra_preset]
            page.route(
                "**/api/v1/taxonomy*",
                lambda route: route.fulfill(
                    status=200, content_type="application/json", body=json.dumps(served)
                ),
            )
        try:
            page.goto(f"{CALCULATOR}?lang={language}", wait_until="networkidle", timeout=20000)
        except Exception as error:  # pragma: no cover - environment guard
            pytest.skip(f"the front end is not being served at {CALCULATOR}: {error}")
        page.add_style_tag(content=FORCE_AUTO)
        # The calculator opens on its introduction screen again - `home.html` is
        # retired and `/` serves this page - so the wizard is one click away.
        page.click('[data-action="start"]')
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
    press_continue(page)
    page.wait_for_selector('input[name="food-category"]')
    press_continue(page)
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


def continue_from_step_three(page):
    """Press Continue on step 3 and report what happened.

    `advanced` is read from the heading, not from the button: a refusal that
    silently advanced anyway and a refusal that showed no message are different
    defects and both have to be visible here.
    """
    press_continue(page)
    page.wait_for_timeout(200)
    error = page.query_selector("#amount-error")
    return {
        "error": error.inner_text().strip() if error else "",
        "advanced": page.query_selector("#amount-title") is None,
        "ariaInvalid": page.eval_on_selector(
            "#total-waste, #unit-count", "el => el.getAttribute('aria-invalid')"
        ) if page.query_selector("#total-waste, #unit-count") else None,
    }


def continue_from_step_four(page):
    """Step 4 refuses *before* Continue, so this reads rather than clicks.

    `updateLine` writes the message into `#allocation-error` and disables the
    button on every keystroke — the allocation summary is live, so a rule that
    only spoke when the button was pressed would contradict the running totals
    beside it. The button is only pressed when it is enabled, which is what makes
    `advanced` meaningful.
    """
    page.wait_for_timeout(120)
    error = page.eval_on_selector("#allocation-error", "el => el.textContent.trim()")
    disabled = page.eval_on_selector('[data-action="continue"]', "el => el.disabled")
    if not disabled:
        press_continue(page)
        page.wait_for_timeout(200)
    return {
        "error": error,
        "disabled": disabled,
        "advanced": page.query_selector("#destination-title") is None,
    }


def to_destination_step(page, total="1000"):
    to_amount_step(page)
    type_into(page, "#total-waste", total)
    press_continue(page)
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


# ------------------------------------------------------------------ the ceiling


#: The two ways a mass can be typed, and the mass each one comes to.
#:
#: **The tonnes row is the discriminating one.** 50,001 is five orders of
#: magnitude below any plausible cap on a typed number and 50,000,001 kg is one
#: kilogram over the scenario ceiling, so a guard that compares what was typed
#: instead of what it converts to accepts it and a guard that converts refuses
#: it. The kilograms row cannot tell those two apart; on its own it would pass
#: against the wrong implementation.
CEILING_CASES = [
    pytest.param("kilograms", "50000000", "50000001", "50,000,000 kilograms", id="kilograms"),
    pytest.param("tonnes", "50000", "50001", "50,000 tonnes", id="tonnes"),
]


@pytest.mark.parametrize("unit,largest_allowed,first_refused,named", CEILING_CASES)
def test_the_ceiling_is_on_the_mass_not_on_the_typed_number(
    page_at, unit, largest_allowed, first_refused, named
):
    """Section 6.2's 50,000,000 kg scenario cap, restated in the unit of the field.

    The step-3 total is never sent - `buildLines` sends the destination rows -
    so the bound that belongs on it is the one on the scenario those rows make
    up, and the largest total that can be allocated is `MAX_SCENARIO_QTY`.

    Both ends are asserted. A ceiling test that only shows the refusal is
    satisfied by an implementation that refuses everything.
    """
    page = to_amount_step(page_at(1278, 983))
    page.select_option("#total-unit", unit)
    page.wait_for_selector("#total-waste")

    type_into(page, "#total-waste", first_refused)
    refused = continue_from_step_three(page)
    assert not refused["advanced"], refused
    assert refused["error"] == f"Enter no more than {named}.", refused

    type_into(page, "#total-waste", largest_allowed)
    allowed = continue_from_step_three(page)
    assert allowed["advanced"], allowed


def test_the_container_ceiling_is_the_mass_expressed_in_containers(page_at):
    """A count field guarded by a kilogram rule has to be told in containers.

    Six thousand kilograms a container puts 8,333 of them at 49,998,000 kg and
    8,334 at 50,004,000 kg, so the count bound is 8,333 - below the 10,000
    plausibility bound, which is what makes this case discriminate. A guard that
    compared the typed count against 50,000,000 would accept 8,334, and every
    other count anybody could type; one that only knew about 10,000 would accept
    it too, and both would carry 50,004,000 kg into step 4.

    The number in the message is the number in the check, by construction:
    `containerLimit()` is evaluated once for both, so they cannot drift apart at
    the boundary the way a separate kilogram check and a separate sentence would.
    """
    heavy = {
        "code": "test_skip_bin",
        "label": "Test 6 t skip",
        "kg_per_unit": "6000.0000",
        "food_category": None,
    }
    page = to_amount_step(page_at(1278, 983, extra_preset=heavy))
    page.select_option("#total-unit", "preset:test_skip_bin")
    page.wait_for_selector("#unit-count")

    type_into(page, "#unit-count", "8334")
    refused = continue_from_step_three(page)
    assert not refused["advanced"], refused
    assert refused["error"] == "Enter no more than 8,333 containers.", refused

    type_into(page, "#unit-count", "8333")
    assert continue_from_step_three(page)["advanced"]


def test_the_plausibility_bound_still_holds_for_an_ordinary_container(page_at):
    """The 10,000 bound is the smaller of the two for every seeded preset.

    69.6 kg a wheelie bin makes the kilogram ceiling 718,390 bins, so folding it
    in must not have loosened the bound a visitor actually meets.
    """
    page = to_amount_step(page_at(1278, 983))
    page.select_option("#total-unit", f"preset:{PRESET_CODE}")
    page.wait_for_selector("#unit-count")
    type_into(page, "#unit-count", "10001")
    refused = continue_from_step_three(page)
    assert not refused["advanced"], refused
    assert refused["error"] == "Enter no more than 10,000 containers.", refused


@pytest.mark.parametrize(
    "unit,named",
    [
        pytest.param("kilograms", "50,000,000 kilograms", id="finite"),
        pytest.param("tonnes", "50,000 tonnes", id="infinite"),
    ],
)
def test_the_largest_number_the_field_will_hold_is_over_the_ceiling(page_at, unit, named):
    """`Infinity` must not fall through a `>` comparison as false.

    308 nines is the longest run Chromium keeps in a `type="number"` field: 309
    is outside a double's range, so the browser's own sanitiser blanks it and
    there is nothing left for a guard to catch. 308 is the interesting case
    precisely because of what happens next - as kilograms it is finite and
    simply enormous, and *multiplied by a thousand for tonnes* it is `Infinity`,
    at which point `massToKg` answers `null` and a bare `kilograms > MAX` is
    `false`. Same keystrokes, two branches, and the second is the one that
    silently admits the overflow the whole guard exists for.
    """
    page = to_amount_step(page_at(1278, 983))
    page.select_option("#total-unit", unit)
    page.wait_for_selector("#total-waste")
    type_into(page, "#total-waste", "9" * 308)
    assert len(value_of(page, "#total-waste")) == 308, "the browser did not keep the number"
    refused = continue_from_step_three(page)
    assert not refused["advanced"], refused
    assert refused["error"] == f"Enter no more than {named}.", refused


def test_a_container_with_no_usable_conversion_says_so(page_at):
    """The taxonomy's fault must not be reported as the visitor's.

    `countLimit` answers 0 when a preset has no usable `kg_per_unit` - it is
    derived from the same missing number - so asking the count bound before the
    availability check refuses an ordinary "5" with **"Enter no more than 0
    containers."**, a sentence about typing for a fault in the data. §6.1 says a
    consumer must not assume the taxonomy is stable across a publish, and this
    is that state made concrete: a preset on the select whose conversion will
    not parse.
    """
    broken = {
        "code": "test_broken_bin",
        "label": "Test unusable bin",
        "kg_per_unit": "n/a",
        "food_category": None,
    }
    page = to_amount_step(page_at(1278, 983, extra_preset=broken))
    page.select_option("#total-unit", "preset:test_broken_bin")
    page.wait_for_selector("#unit-count")
    type_into(page, "#unit-count", "5")
    refused = continue_from_step_three(page)
    assert not refused["advanced"], refused
    assert refused["error"] == "That container is no longer available. Choose another.", refused


# --------------------------------------------------------- the per-line ceiling


@pytest.mark.parametrize(
    "unit,total,line,named",
    [
        pytest.param("kilograms", "50000000", "50000001", "50,000,000 kilograms", id="kilograms"),
        pytest.param("tonnes", "50000", "50001", "50,000 tonnes", id="tonnes"),
    ],
)
def test_one_destination_may_not_exceed_the_per_line_ceiling(page_at, unit, total, line, named):
    """Section 6.2's `MAX_LINE_QTY`, restated where the number carrying it is typed.

    Still its own rule after v1.46 made it equal to the scenario cap, and still
    reachable: `validateCurrentStep` asks it *before* the allocation-exceeds-
    total rule, so a row one kilogram over the ceiling is answered by the
    ceiling and not by the allocation. That order is what this parametrisation
    pins - if the two rules swapped, both rows here would get "Allocated waste
    exceeds total waste by ..." instead.

    The tonnes row is again the discriminating one: 50,001 is a small number and
    50,001,000 kg is over the bound, so a guard comparing the typed figure
    passes it and a guard comparing the mass does not.
    """
    page = to_amount_step(page_at(1278, 983))
    page.select_option("#total-unit", unit)
    page.wait_for_selector("#total-waste")
    type_into(page, "#total-waste", total)
    press_continue(page)
    page.wait_for_selector('[data-line-field="amount"]')

    type_into(page, '[data-line-field="amount"] >> nth=0', line)
    refused = continue_from_step_four(page)
    assert not refused["advanced"], refused
    assert refused["error"] == f"Enter destination amounts of no more than {named}.", refused


def test_a_scenario_spread_across_destinations_is_not_refused(page_at):
    """**The client must not refuse what the API would take.**

    Thirty million kilograms in three destinations is three legal lines and one
    legal scenario, and `POST /api/v1/calculate` accepts it. Putting the
    *per-line* cap on the total instead - the tempting simplification, and the
    shape of the constant this replaced - refuses it at step 3, which would be
    this project's own definition of a defect.

    Asserted on the request body, because that is the only place the claim is
    settled: the numbers that left the browser are the numbers the server was
    asked to accept.
    """
    page = to_amount_step(page_at(1278, 983))
    type_into(page, "#total-waste", "30000000")
    assert continue_from_step_three(page)["advanced"]
    page.wait_for_selector('[data-line-field="amount"]')
    for index in range(3):
        type_into(page, f'[data-line-field="amount"] >> nth={index}', "10000000")
    assert continue_from_step_four(page)["advanced"]
    page.click('[data-action="calculate"]')
    page.wait_for_selector(".results-page", timeout=15000)

    assert page.sent, "no request was sent"
    lines = page.sent[0]["entries"][0]["current"]
    assert [line["qty_kg"] for line in lines] == ["10000000.000"] * 3, page.sent[0]


@pytest.mark.parametrize(
    "unit,total,named_total",
    [
        pytest.param("kilograms", "50000000", "50000000.000", id="kilograms"),
        pytest.param("tonnes", "50000", "50000000.000", id="tonnes"),
    ],
)
def test_one_destination_may_carry_an_entire_legal_scenario(page_at, unit, total, named_total):
    """**The reported defect, at the screen the message appeared on.**

    Step 3: 50,000 tonnes, which step 3 accepts. Step 4: all of it to animal
    feed, which step 4 refused with "Enter destination amounts of no more than
    10,000 tonnes." A site that only landfills, or only digests, has no second
    destination to split across, and nothing in the model asks a scenario to be
    divided - so "at least five destinations" was a rule the ratio between two
    unexplained constants had invented.

    Driven at exactly the scenario ceiling in both units, because anything
    below 10,000 t passes against the old bound too and asserts nothing. The
    tonnes row is the one that also proves the *conversion* is on the right side
    of the comparison: 50,000 typed is 50,000,000 kg sent.

    Asserted on the request body. "Not refused" is a claim about the screen;
    "the whole figure left the browser in one line" is the claim that matters,
    and only `page.sent` settles it.
    """
    page = to_amount_step(page_at(1278, 983))
    page.select_option("#total-unit", unit)
    page.wait_for_selector("#total-waste")
    type_into(page, "#total-waste", total)
    assert continue_from_step_three(page)["advanced"]
    page.wait_for_selector('[data-line-field="amount"]')

    type_into(page, '[data-line-field="amount"] >> nth=0', total)
    allocated = continue_from_step_four(page)
    assert allocated["error"] == "", allocated
    assert allocated["advanced"], allocated

    page.click('[data-action="calculate"]')
    page.wait_for_selector(".results-page", timeout=15000)
    assert page.sent, "no request was sent"
    lines = page.sent[0]["entries"][0]["current"]
    assert len(lines) == 1, page.sent[0]
    assert lines[0]["qty_kg"] == named_total, page.sent[0]


# --------------------------------------------------- the number is never edited


def test_the_number_typed_is_the_number_kept(page_at):
    """A refusal leaves the field alone. It does not clamp and does not revert.

    Clamping to a maximum and reverting to a previous value are the same defect
    wearing different clothes: the visitor enters one figure, the page shows
    another, and if they press on it is the page's figure that is submitted.
    Every other rule in `calculator.js` refuses on Continue and keeps what was
    typed; so does this one.
    """
    page = to_amount_step(page_at(1278, 983))
    typed = "99999999999"
    type_into(page, "#total-waste", typed)
    assert value_of(page, "#total-waste") == typed
    refused = continue_from_step_three(page)
    assert not refused["advanced"], refused
    assert value_of(page, "#total-waste") == typed, "the refusal rewrote the visitor's number"
    assert page.sent == [], page.sent


def test_the_refused_field_is_announced_as_invalid(page_at):
    """`aria-invalid="true"`, not `aria-invalid=""`.

    An empty string is how `toggleAttribute` writes a flag, and ARIA reads an
    empty `aria-invalid` as **false** - a field a screen reader never announces
    as wrong, under a message the same step's server-error path marks `"true"`.
    The association is asserted with it, because a message with no link to the
    box is a message a screen-reader user meets on its own.
    """
    page = to_amount_step(page_at(1278, 983))
    type_into(page, "#total-waste", "50000001")
    refused = continue_from_step_three(page)
    assert refused["ariaInvalid"] == "true", refused
    described = page.eval_on_selector("#total-waste", "el => el.getAttribute('aria-describedby')")
    assert described == "amount-error", described
    assert page.query_selector("#amount-error").get_attribute("role") == "alert"


def test_returning_to_step_four_does_not_re_enable_continue_on_a_refused_line(page_at):
    """The render path and the keystroke path must agree about one button.

    `updateLine` disables Continue from `validateCurrentStep()` on every
    keystroke; `destinationStep` decides its state when the screen is drawn. The
    render path used to restate three of those rules inline and so knew nothing
    about the others - and this walk reaches it: Back to step 3 and Continue
    again re-renders step 4 over the line that is still there. With two lists,
    the button comes back **enabled** on a state the other list refuses, and the
    only thing between the visitor and a 400 is the message they have already
    read once.
    """
    page = to_amount_step(page_at(1278, 983))
    # At the ceiling, with the line one kilogram past it. Until v1.46 this read
    # 30,000,000 and 10,000,001; raising `MAX_LINE_QTY` made that pair legal and
    # the test failed, correctly - it needs a state step 4 actually refuses, and
    # the per-line ceiling is the rule it is meant to walk back onto.
    type_into(page, "#total-waste", "50000000")
    press_continue(page)
    page.wait_for_selector('[data-line-field="amount"]')
    type_into(page, '[data-line-field="amount"] >> nth=0', "50000001")
    first = continue_from_step_four(page)
    assert first["disabled"], first

    page.click('[data-action="go-step"][data-step="2"]')
    page.wait_for_selector("#total-waste")
    press_continue(page)
    page.wait_for_selector('[data-line-field="amount"]')
    redrawn = page.evaluate(
        """() => ({
             value: document.querySelector('[data-line-field="amount"]').value,
             disabled: document.querySelector('[data-action="continue"]').disabled,
           })"""
    )
    assert redrawn["value"] == "50000001", redrawn
    assert redrawn["disabled"], redrawn


# ------------------------------------------------- decimals, and what is not one


def test_1e5_is_not_a_decimal_places_problem(page_at):
    """`1e5` has no decimal places, so it cannot be told it has too many.

    `<input type="number">` accepts it - this walks `1`, `1e` (which the browser
    reports as `''`), `1e5` - and the one refusal the field had covered both
    "too precise" and "not written as a decimal" with a sentence that is only
    true of the first. Two questions, two answers, each true of what was typed.
    """
    page = to_amount_step(page_at(1278, 983))
    type_into(page, "#total-waste", "1e5")
    assert value_of(page, "#total-waste") == "1e5", "the browser did not keep 1e5"
    refused = continue_from_step_three(page)
    assert not refused["advanced"], refused
    assert refused["error"] == "Write the number out in full, using digits only.", refused


def test_three_decimal_places_still_gets_the_decimal_message(page_at):
    """The other half of the pair, so the split is a split and not a rename."""
    page = to_amount_step(page_at(1278, 983))
    type_into(page, "#total-waste", "1.234")
    refused = continue_from_step_three(page)
    assert refused["error"] == "Enter no more than two decimal places.", refused


# ------------------------------------------------------- language, and geometry


#: `web/locales/de.json`. Held here rather than read from the catalogue: a test
#: that looked the string up in the file the page loads would pass on any string
#: at all, including Chromium's own.
GERMAN_CEILING = "Geben Sie höchstens 50,000,000 Kilogramm ein."
GERMAN_PLAIN = "Schreiben Sie die Zahl vollständig aus, nur mit Ziffern."


def test_the_refusal_is_in_the_pages_language_and_not_the_browsers(page_at):
    """The defect that can only be seen in a non-English locale.

    A guard that reports `target.validationMessage` ships whatever Chromium
    wrote - in the *browser's* language, from a string table the project does
    not own - inside a page that has been translated to the visitor's. In
    English the two are indistinguishable, which is why this test is in German.

    The grouping is the other half: the figure keeps `formatNumber`'s pinned
    `en-NZ` separators in every language, so German's own `50.000.000` would
    mean the number had been formatted somewhere this project does not control.
    """
    page = to_amount_step(page_at(1278, 983, language="de"))
    type_into(page, "#total-waste", "50000001")
    refused = continue_from_step_three(page)
    assert not refused["advanced"], refused
    assert refused["error"] == GERMAN_CEILING, refused
    assert "Value must be" not in refused["error"], refused
    assert "Wert muss" not in refused["error"], refused

    type_into(page, "#total-waste", "1e5")
    assert continue_from_step_three(page)["error"] == GERMAN_PLAIN


@pytest.mark.parametrize("width,height", VIEWPORTS)
@pytest.mark.parametrize("language", ["en", "de"])
def test_the_refusal_is_on_the_screen_at_both_widths(page_at, width, height, language):
    """A message nobody can see refuses nothing.

    390x700 is the width where the sticky navigation bar can sit on top of the
    thing it is refusing, and a longer language is the case where the message
    wraps into space that was measured in English.
    """
    page = to_amount_step(page_at(width, height, language=language))
    type_into(page, "#total-waste", "50000001")
    continue_from_step_three(page)
    seen = page.evaluate(
        """() => {
          const el = document.querySelector('#amount-error');
          if (!el) return { found: false };
          el.scrollIntoView({ block: 'center' });
          const box = el.getBoundingClientRect();
          const at = document.elementFromPoint(box.left + 4, box.top + box.height / 2);
          return {
            found: true,
            width: Math.round(box.width),
            height: Math.round(box.height),
            text: el.textContent.trim(),
            covered: !(el === at || el.contains(at)),
          };
        }"""
    )
    assert seen["found"] and seen["width"] > 0 and seen["height"] > 0, seen
    assert not seen["covered"], seen
    assert seen["text"], seen
